"""Controller-only disposable VM login and first-boot check. Inspect before run."""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import os
import secrets
import signal
import time
from contextlib import contextmanager

import asyncssh
from fastapi import FastAPI
from openstack.exceptions import NotFoundException
from starlette.testclient import TestClient

from backend import terminal
from backend.database import audit, db
from backend.openstack_service import (
    create_server, delete_server, list_catalog, quota, team_connection,
)
from backend.security import hash_password

TEAM_SLUG = "hajin"
TEAM_PROJECT_ID = "ccac6ab29ec243aba503965495a8ac05"


def team_row():
    with db() as conn:
        row = conn.execute("SELECT * FROM teams WHERE slug=?", (TEAM_SLUG,)).fetchone()
        assert row and row["openstack_project_id"] == TEAM_PROJECT_ID
        assert row["network_id"] and row["cidr"] == "10.30.23.0/24"
        return dict(row)


def capacity(team):
    catalog = list_catalog(TEAM_SLUG)
    flavor = next(item for item in catalog["flavors"] if item["name"] == "small")
    assert catalog["images"]
    usage = quota(team["openstack_project_id"])
    available = {key: usage[key]["limit"] - usage[key]["in_use"] for key in ("instances", "cores", "ram")}
    servers = list(team_connection(TEAM_SLUG).compute.servers(details=True))
    print(f"Existing team VMs: {len(servers)}; free quota: {available['instances']} VM, "
          f"{available['cores']} vCPU, {available['ram']} MB")
    if (available["instances"] < 1 or available["cores"] < flavor["vcpus"]
            or available["ram"] < flavor["ram"]):
        raise RuntimeError("Not enough team quota for a disposable test VM")
    return catalog["images"][0], flavor


async def wait_for_guest_ssh(address: str, username: str, password: str) -> str:
    deadline = time.monotonic() + 240
    marker = "UCLOUD-GUEST-LOGIN-" + secrets.token_hex(8)
    last_error = "guest SSH is not ready"
    while time.monotonic() < deadline:
        try:
            async with asyncssh.connect(
                address, username=username, password=password, client_keys=[],
                known_hosts=None, connect_timeout=6, login_timeout=10,
            ) as connection:
                key = connection.get_server_host_key().export_public_key("openssh").decode().strip()
                result = await connection.run(f"printf '%s\\n' {marker}", check=True)
                assert marker in result.stdout
                return key
        except (OSError, asyncssh.Error) as exc:
            last_error = type(exc).__name__
            await asyncio.sleep(6)
    raise RuntimeError(f"Guest SSH login did not become ready: {last_error}")


async def check_guest_tailscale(address: str, host_key: str, password: str) -> None:
    async with asyncssh.connect(
        address, username="student", password=password, client_keys=[],
        known_hosts=f"{address} {host_key}\n".encode(), connect_timeout=10, login_timeout=12,
    ) as connection:
        first_boot = await connection.run("cloud-init status --wait", check=False, timeout=420)
        if first_boot.exit_status != 0:
            raise RuntimeError(f"VM first boot failed: {first_boot.stdout[-500:]} {first_boot.stderr[-500:]}")
        # The guest installer runs in a background service and can outlive cloud-init.
        deadline = time.monotonic() + 300
        while time.monotonic() < deadline:
            client = await connection.run("tailscale version", check=False, timeout=20)
            daemon = await connection.run("systemctl is-active tailscaled", check=False, timeout=20)
            if client.exit_status == 0 and daemon.exit_status == 0 and daemon.stdout.strip() == "active":
                print("VM first boot completed; Tailscale client and daemon are ready")
                return
            await asyncio.sleep(8)
        installer = await connection.run(
            "systemctl show uccloud-tailscale-install.service -p ActiveState -p SubState -p Result -p NRestarts",
            check=False, timeout=20,
        )
        raise RuntimeError(f"Tailscale installation did not finish: {installer.stdout[-500:]}")


async def check_background_website(address: str, host_key: str, password: str) -> None:
    """Verify a student website survives closing the SSH session that started it."""
    options = dict(username="student", password=password, client_keys=[],
                   known_hosts=f"{address} {host_key}\n".encode(), connect_timeout=10, login_timeout=12)
    port = 18779
    async with asyncssh.connect(address, **options) as connection:
        started = await connection.run(
            f"nohup bash -lc 'python3 -m http.server {port} --bind 127.0.0.1 --directory /tmp' "
            "> /tmp/uccloud-qa-web.log 2>&1 </dev/null & echo $!", check=True, timeout=20,
        )
        pid = int(started.stdout.strip().splitlines()[-1])
    try:
        for _ in range(12):
            await asyncio.sleep(2)
            async with asyncssh.connect(address, **options) as connection:
                result = await connection.run(
                    "python3 -c 'import urllib.request; print(urllib.request.urlopen(\"http://127.0.0.1:18779\", timeout=3).status)'",
                    check=False, timeout=10,
                )
                if result.exit_status == 0 and result.stdout.strip() == "200":
                    print("Website kept serving after the starting SSH session closed")
                    return
        raise RuntimeError("Background website stopped after SSH disconnected")
    finally:
        async with asyncssh.connect(address, **options) as connection:
            await connection.run(f"kill {pid}", check=False, timeout=10)


async def check_persistent_website(address: str, host_key: str, password: str,
                                   server_id: str, command: str) -> None:
    """Verify the guide's user service survives logout and a VM reboot."""
    expected = "python3 -m http.server 18781 --bind 127.0.0.1"
    sudo_line = 'sudo loginctl enable-linger "$USER"'
    if expected not in command or command.count(sudo_line) != 1 or len(command) > 8000:
        raise ValueError("Unexpected generated website command")
    options = dict(username="student", password=password, client_keys=[],
                   known_hosts=f"{address} {host_key}\n".encode(),
                   connect_timeout=10, login_timeout=12)

    async def verify_site() -> None:
        async with asyncssh.connect(address, **options) as connection:
            service = await connection.run("systemctl --user is-active uccloud-web.service",
                                           check=True, timeout=20)
            assert service.stdout.strip() == "active"
            response = await connection.run(
                "python3 -c 'import urllib.request; print(urllib.request.urlopen(\"http://127.0.0.1:18781\", timeout=3).status)'",
                check=True, timeout=20,
            )
            assert response.stdout.strip() == "200"

    async with asyncssh.connect(address, **options) as connection:
        first_boot = await connection.run("cloud-init status --wait", check=True, timeout=420)
        assert first_boot.exit_status == 0
        linger = await connection.run("sudo -S loginctl enable-linger student",
                                      input=password + "\n", check=True, timeout=30)
        assert linger.exit_status == 0
        setup = await connection.run(
            "mkdir -p ~/uccloud-qa-site && cd ~/uccloud-qa-site && "
            + command.replace(sudo_line, ": # QA enabled linger separately"),
            check=True, timeout=90,
        )
        assert setup.exit_status == 0
        old_boot = (await connection.run("cat /proc/sys/kernel/random/boot_id",
                                         check=True, timeout=10)).stdout.strip()
    await verify_site()
    print("Website stayed up after the setup SSH session closed")
    cloud = team_connection(TEAM_SLUG)
    cloud.compute.reboot_server(cloud.compute.get_server(server_id), reboot_type="SOFT")
    deadline = time.monotonic() + 300
    while time.monotonic() < deadline:
        try:
            async with asyncssh.connect(address, **options) as connection:
                boot = (await connection.run("cat /proc/sys/kernel/random/boot_id",
                                             check=True, timeout=10)).stdout.strip()
                if boot != old_boot:
                    break
        except (OSError, asyncssh.Error):
            pass
        await asyncio.sleep(5)
    else:
        raise RuntimeError("Disposable VM did not reboot")
    for _ in range(20):
        try:
            await verify_site()
            print("Website restarted automatically after VM reboot")
            return
        except (OSError, asyncssh.Error, AssertionError):
            await asyncio.sleep(5)
    raise RuntimeError("Website did not recover after VM reboot")


@contextmanager
def no_database():
    yield None


def check_browser_gateway(team, server_id: str, password: str, host_key: str) -> None:
    origin = "https://uccloud-qa.invalid"
    session = {
        "id": -1000, "status": "active", "csrf_token": "qa-token",
        "team_slug": TEAM_SLUG, "openstack_project_id": TEAM_PROJECT_ID,
        "network_id": team["network_id"], "cidr": team["cidr"],
    }
    terminal.BROWSER_ORIGIN = origin
    terminal._session = lambda token: session if token == "qa-session" else None
    terminal._allow_attempt = lambda user_id, vm_id: True
    terminal._clear_attempts = lambda user_id, vm_id: None
    terminal._recent_vm_action = lambda vm_id: False
    terminal._known_hosts = lambda vm_id, address: f"{address} {host_key}\n".encode()

    def verify_host_key(vm_id, key):
        assert vm_id == server_id and key == host_key

    terminal._pin_host_key = verify_host_key
    terminal.db = no_database
    terminal.audit = lambda conn, user_id, action, target: None
    app = FastAPI()
    app.include_router(terminal.router)
    prefix = "UCLOUD-BROWSER-SSH-"
    suffix = secrets.token_hex(8)
    marker = prefix + suffix
    with TestClient(app, base_url=origin) as client:
        with client.websocket_connect(
            f"/api/instances/{server_id}/terminal",
            subprotocols=["uccloud", "csrf.qa-token"],
            headers={"origin": origin, "cookie": "school_cloud_session=qa-session"},
        ) as websocket:
            websocket.send_json({"username": "student", "password": password, "cols": 80, "rows": 24})
            first = websocket.receive_json()
            assert first.get("type") == "ready", first
            websocket.send_json({"type": "input", "data": f"printf '%s%s\\n' {prefix} {suffix}\n"})
            output = ""
            while marker not in output:
                frame = websocket.receive()
                if frame["type"] != "websocket.send":
                    raise RuntimeError("Browser SSH closed before command output")
                chunk = frame.get("bytes")
                if chunk:
                    output += chunk.decode("utf-8", errors="replace")
            websocket.send_json({"type": "input", "data": "exit\n"})
    print("Browser SSH authenticated and returned command output")


def cleanup(name: str, server_id: str | None) -> str | None:
    cloud = team_connection(TEAM_SLUG)
    if not server_id:
        for _ in range(6):
            matches = [item for item in cloud.compute.servers(details=True)
                       if item.name == name and item.project_id == TEAM_PROJECT_ID]
            if len(matches) > 1:
                raise RuntimeError("Multiple disposable VMs matched the cleanup name")
            if matches:
                server_id = matches[0].id
                break
            time.sleep(5)
    if not server_id:
        return None
    try:
        delete_server(TEAM_SLUG, TEAM_PROJECT_ID, server_id)
    except NotFoundException:
        print("Disposable test VM already absent")
        return server_id
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        try:
            if cloud.compute.get_server(server_id) is None:
                print("Disposable test VM deleted")
                return server_id
        except NotFoundException:
            print("Disposable test VM deleted")
            return server_id
        time.sleep(4)
    raise RuntimeError(f"Disposable test VM deletion still pending: {server_id}")


def run(team, image, flavor):
    name = "portal-qa-browser-ssh-" + secrets.token_hex(4)
    password = secrets.token_urlsafe(24)
    server_id = None
    try:
        created = create_server(
            TEAM_SLUG, TEAM_PROJECT_ID, "portal-qa", team["network_id"], name,
            image["id"], flavor["id"], "student", password, None,
        )
        server_id = created["id"]
        cloud = team_connection(TEAM_SLUG)
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            server = cloud.compute.get_server(server_id)
            if server.status == "ACTIVE":
                break
            if server.status == "ERROR":
                raise RuntimeError("Disposable test VM entered ERROR")
            time.sleep(5)
        else:
            raise RuntimeError("Disposable test VM did not become ACTIVE")
        user = {
            "team_slug": TEAM_SLUG, "openstack_project_id": TEAM_PROJECT_ID,
            "network_id": team["network_id"], "cidr": team["cidr"],
        }
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            try:
                address = terminal._ssh_target(user, server_id)
                break
            except ValueError:
                time.sleep(5)
        else:
            raise RuntimeError("Disposable test VM did not receive an IP")
        host_key = asyncio.run(wait_for_guest_ssh(address, "student", password))
        print("Guest SSH authenticated with newly created VM credentials")
        asyncio.run(check_guest_tailscale(address, host_key, password))
        asyncio.run(check_background_website(address, host_key, password))
        check_browser_gateway(team, server_id, password, host_key)
    finally:
        cleanup(name, server_id)


def run_persistent_website(team, image, flavor, command: str) -> None:
    name = "portal-qa-persistent-web-" + secrets.token_hex(4)
    password = secrets.token_urlsafe(24)
    server_id = None
    try:
        created = create_server(
            TEAM_SLUG, TEAM_PROJECT_ID, "portal-qa", team["network_id"], name,
            image["id"], flavor["id"], "student", password, None,
        )
        server_id = created["id"]
        cloud = team_connection(TEAM_SLUG)
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            server = cloud.compute.get_server(server_id)
            if server.status == "ACTIVE":
                break
            if server.status == "ERROR":
                raise RuntimeError("Disposable test VM entered ERROR")
            time.sleep(5)
        else:
            raise RuntimeError("Disposable test VM did not become ACTIVE")
        user = {"team_slug": TEAM_SLUG, "openstack_project_id": TEAM_PROJECT_ID,
                "network_id": team["network_id"], "cidr": team["cidr"]}
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            try:
                address = terminal._ssh_target(user, server_id)
                break
            except ValueError:
                time.sleep(5)
        else:
            raise RuntimeError("Disposable test VM did not receive an IP")
        host_key = asyncio.run(wait_for_guest_ssh(address, "student", password))
        asyncio.run(check_persistent_website(address, host_key, password, server_id, command))
    finally:
        cleanup(name, server_id)


def run_authenticated_browser(team, image, flavor):
    """Keep disposable credentials alive briefly for a real public-browser check."""
    name = "portal-qa-browser-e2e-" + secrets.token_hex(4)
    username = "ucqa" + secrets.token_hex(4)
    password = secrets.token_urlsafe(24)
    done_path = "/run/uccloud-browser-qa-" + secrets.token_hex(8) + ".done"
    server_id = None
    user_id = None
    try:
        created = create_server(
            TEAM_SLUG, TEAM_PROJECT_ID, username, team["network_id"], name,
            image["id"], flavor["id"], "student", password, None,
        )
        server_id = created["id"]
        cloud = team_connection(TEAM_SLUG)
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            server = cloud.compute.get_server(server_id)
            if server.status == "ACTIVE":
                break
            if server.status == "ERROR":
                raise RuntimeError("Disposable test VM entered ERROR")
            time.sleep(5)
        else:
            raise RuntimeError("Disposable test VM did not become ACTIVE")
        target_user = {
            "team_slug": TEAM_SLUG, "openstack_project_id": TEAM_PROJECT_ID,
            "network_id": team["network_id"], "cidr": team["cidr"],
        }
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            try:
                address = terminal._ssh_target(target_user, server_id)
                break
            except ValueError:
                time.sleep(5)
        else:
            raise RuntimeError("Disposable test VM did not receive an IP")
        asyncio.run(wait_for_guest_ssh(address, "student", password))
        with db() as conn:
            cursor = conn.execute(
                """INSERT INTO users(username,email,password_hash,role,status,requested_team,team_id,team_owner)
                   VALUES(?,?,?,'member','active',?,?,0)""",
                (username, f"{username}@example.invalid", hash_password(password), team["name"], team["id"]),
            )
            user_id = cursor.lastrowid
            audit(conn, user_id, "qa.browser.created", server_id)
        print("QA_READY " + json.dumps({"username": username, "password": password,
                                        "vm_id": server_id, "done_path": done_path}), flush=True)
        deadline = time.monotonic() + 600
        while time.monotonic() < deadline:
            if os.path.exists(done_path):
                break
            time.sleep(2)
        else:
            print("QA browser check timed out; cleaning up", flush=True)
    finally:
        if os.path.exists(done_path):
            os.unlink(done_path)
        try:
            if user_id is not None:
                with db() as conn:
                    row = conn.execute("SELECT username FROM users WHERE id=?", (user_id,)).fetchone()
                    if not row or row["username"] != username or not username.startswith("ucqa"):
                        raise RuntimeError("Disposable QA user identity changed; refusing cleanup")
                    conn.execute("UPDATE audit_log SET user_id=NULL WHERE user_id=?", (user_id,))
                    conn.execute("DELETE FROM users WHERE id=?", (user_id,))
                    audit(conn, None, "qa.browser.cleaned", server_id or "")
        finally:
            try:
                cleanup(name, server_id)
            finally:
                if server_id:
                    with db() as conn:
                        conn.execute("DELETE FROM instance_ssh_host_keys WHERE server_id=?", (server_id,))
        print("QA browser resources cleaned", flush=True)


def run_browser_creation(team):
    """Provide a disposable account; the real public UI creates and checks its VM."""
    name = "portal-qa-ui-" + secrets.token_hex(6)
    username = "ucqa" + secrets.token_hex(4)
    password = secrets.token_urlsafe(24)
    done_path = "/run/uccloud-browser-qa-" + secrets.token_hex(8) + ".done"
    user_id = None
    try:
        with db() as conn:
            cursor = conn.execute(
                """INSERT INTO users(username,email,password_hash,role,status,requested_team,team_id,team_owner)
                   VALUES(?,?,?,'member','active',?,?,0)""",
                (username, f"{username}@example.invalid", hash_password(password), team["name"], team["id"]),
            )
            user_id = cursor.lastrowid
            audit(conn, user_id, "qa.browser.create.start", name)
        print("QA_CREATE_READY " + json.dumps({"username": username, "password": password,
                                         "vm_name": name, "done_path": done_path}), flush=True)
        deadline = time.monotonic() + 900
        while time.monotonic() < deadline:
            if os.path.exists(done_path):
                break
            time.sleep(2)
        else:
            print("QA creation check timed out; cleaning up", flush=True)
    finally:
        if os.path.exists(done_path):
            os.unlink(done_path)
        if user_id is not None:
            with db() as conn:
                row = conn.execute("SELECT username FROM users WHERE id=?", (user_id,)).fetchone()
                if not row or row["username"] != username or not username.startswith("ucqa"):
                    raise RuntimeError("Disposable QA user identity changed; refusing cleanup")
                conn.execute("UPDATE audit_log SET user_id=NULL WHERE user_id=?", (user_id,))
                conn.execute("DELETE FROM users WHERE id=?", (user_id,))
                audit(conn, None, "qa.browser.create.cleaned", name)
        server_id = cleanup(name, None)
        if server_id:
            with db() as conn:
                conn.execute("DELETE FROM instance_ssh_host_keys WHERE server_id=?", (server_id,))
        print("QA browser creation resources cleaned", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["inspect", "run", "browser", "create-browser", "persistent-web"])
    parser.add_argument("--command-base64")
    args = parser.parse_args()
    signal.signal(signal.SIGTERM, lambda signum, frame: (_ for _ in ()).throw(KeyboardInterrupt()))
    team = team_row()
    image, flavor = capacity(team)
    if args.mode == "run":
        run(team, image, flavor)
    elif args.mode == "persistent-web":
        if not args.command_base64:
            parser.error("persistent-web requires --command-base64")
        run_persistent_website(team, image, flavor,
                               base64.b64decode(args.command_base64, validate=True).decode())
    elif args.mode == "browser":
        run_authenticated_browser(team, image, flavor)
    elif args.mode == "create-browser":
        run_browser_creation(team)
