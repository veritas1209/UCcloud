"""Browser SSH target isolation and connection-limit checks (no live VM)."""

import asyncio
import json
import os
import tempfile
from pathlib import Path
from types import SimpleNamespace

temporary = tempfile.TemporaryDirectory(prefix="portal-terminal-test-")
os.environ["PORTAL_DB"] = str(Path(temporary.name) / "portal.db")

from backend.database import audit, db, init_db  # noqa: E402
from backend import terminal  # noqa: E402

init_db()
user = {
    "team_slug": "example", "openstack_project_id": "project-a",
    "network_id": "network-a", "cidr": "10.30.23.0/24",
}
server = SimpleNamespace(status="ACTIVE")
port = SimpleNamespace(project_id="project-a", fixed_ips=[{"ip_address": "10.30.23.177"}])
network = SimpleNamespace(ports=lambda **kwargs: [port])
terminal.team_connection = lambda slug: SimpleNamespace(network=network)
terminal._owned_server = lambda conn, server_id, project_id: server
assert terminal._ssh_target(user, "vm-a") == "10.30.23.177"

port.project_id = "project-b"
try:
    terminal._ssh_target(user, "vm-a")
    raise AssertionError("Cross-project Neutron port was accepted")
except ValueError:
    pass

with db() as conn:
    audit(conn, None, "instance.create", "vm-recent")
    conn.execute(
        "INSERT INTO audit_log(user_id,action,target,created_at) VALUES(NULL,'instance.start','vm-old',datetime('now','-10 minutes'))"
    )
assert terminal._recent_vm_action("vm-recent")
assert not terminal._recent_vm_action("vm-old")
port.project_id = "project-a"
port.fixed_ips = [{"ip_address": "192.0.2.22"}]
try:
    terminal._ssh_target(user, "vm-a")
    raise AssertionError("Off-subnet SSH target was accepted")
except ValueError:
    pass
port.fixed_ips = [{"ip_address": "10.30.23.177"}]
server.status = "SHUTOFF"
try:
    terminal._ssh_target(user, "vm-a")
    raise AssertionError("Stopped VM was accepted")
except ValueError:
    pass

assert terminal._reserve_slot(7)
assert terminal._reserve_slot(7)
assert terminal._reserve_slot(7)
assert not terminal._reserve_slot(7)
for _ in range(3):
    terminal._release_slot(7)
assert terminal._reserve_slot(7)
terminal._release_slot(7)

terminal._pin_host_key("vm-a", "ssh-ed25519 AAAATEST")
assert b"ssh-ed25519 AAAATEST" in terminal._known_hosts("vm-a", "10.30.23.177")
try:
    terminal._pin_host_key("vm-a", "ssh-ed25519 DIFFERENT")
    raise AssertionError("Changed SSH host key was accepted")
except ValueError:
    pass


class ClosingSocket:
    def __init__(self):
        self.close_codes = []
        self.messages = []

    async def close(self, code):
        self.close_codes.append(code)

    async def send_json(self, message):
        self.messages.append(message)


async def check_boot_retry():
    original_connect = terminal.asyncssh.connect
    attempts = 0

    async def eventually_ready(*args, **kwargs):
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise OSError("SSH is booting")
        return object()

    terminal.asyncssh.connect = eventually_ready
    socket = ClosingSocket()
    try:
        await terminal._connect_ssh(socket, "10.30.23.177", "student", "test-password", None,
                                    recent=True, retry_window=0.1, retry_interval=0.001)
        assert attempts == 3 and socket.messages == [{"type": "waiting"}]

        async def unavailable(*args, **kwargs):
            nonlocal attempts
            attempts += 1
            raise OSError("SSH unavailable")

        terminal.asyncssh.connect = unavailable
        attempts = 0
        socket = ClosingSocket()
        try:
            await terminal._connect_ssh(socket, "10.30.23.177", "student", "test-password", None,
                                        recent=False, retry_window=0.1, retry_interval=0.001)
            raise AssertionError("Old VM unexpectedly retried")
        except ValueError:
            pass
        assert attempts == 1 and socket.messages == []

        async def wrong_password(*args, **kwargs):
            nonlocal attempts
            attempts += 1
            raise terminal.asyncssh.PermissionDenied("Wrong password")

        terminal.asyncssh.connect = wrong_password
        attempts = 0
        socket = ClosingSocket()
        try:
            await terminal._connect_ssh(socket, "10.30.23.177", "student", "wrong-password", None,
                                        recent=True, retry_window=0.1, retry_interval=0.001)
            raise AssertionError("Wrong password unexpectedly retried")
        except ValueError as exc:
            assert str(exc) == ("VM 접속 ID 또는 비밀번호가 올바르지 않습니다. "
                                "VM을 방금 만들었다면 1~2분 뒤 다시 시도해 주세요.")
        assert attempts == 1 and socket.messages == []

        attempts = 0
        try:
            await terminal._connect_ssh(socket, "10.30.23.177", "student", "wrong-password", None,
                                        recent=False, retry_window=0.1, retry_interval=0.001)
            raise AssertionError("Old VM wrong password unexpectedly succeeded")
        except ValueError as exc:
            assert str(exc) == "VM 접속 ID 또는 비밀번호가 올바르지 않습니다."
        assert attempts == 1
    finally:
        terminal.asyncssh.connect = original_connect


asyncio.run(check_boot_retry())


async def check_session_watcher():
    original_session = terminal._session
    socket = ClosingSocket()
    states = iter([
        {"status": "active", "openstack_project_id": "project-a"},
        None,
    ])
    terminal._session = lambda token: next(states)
    try:
        await terminal._watch_session(socket, "session-token", "project-a", interval=0.001)
    finally:
        terminal._session = original_session
    assert socket.close_codes == [4401], "Expired login did not close browser SSH"


asyncio.run(check_session_watcher())

valid_first = json.dumps({"username": "student", "password": "test-password", "cols": 80, "rows": 24})
assert terminal._first_credentials(valid_first) == ("student", "test-password", 80, 24)
for malformed in ("[]", "null", "not-json", " " * (terminal.FIRST_MESSAGE_LIMIT + 1),
                  json.dumps({"username": "student", "password": "test-password", "cols": True})):
    try:
        terminal._first_credentials(malformed)
        raise AssertionError("Malformed first WebSocket message was accepted")
    except ValueError as exc:
        assert str(exc) == "VM 접속 ID와 비밀번호를 확인해 주세요."


class InputSocket:
    def __init__(self, raw):
        self.raw = raw

    async def receive_text(self):
        return self.raw


async def check_malformed_input():
    for raw in ("[]", "not-json", json.dumps({"type": "resize", "cols": True, "rows": 24})):
        try:
            await terminal._receive_input(InputSocket(raw), None)
            raise AssertionError("Malformed SSH input message was accepted")
        except ValueError as exc:
            assert str(exc) in ("요청을 확인해 주세요.", "화면 크기를 확인해 주세요.")


asyncio.run(check_malformed_input())
public_unit = (Path(__file__).resolve().parents[1] / "ops" / "cloud-portal-public.service").read_text()
assert "--ws websockets --ws-max-size 65536 --ws-max-queue 8" in public_unit

temporary.cleanup()
print("Browser SSH security checks passed")
