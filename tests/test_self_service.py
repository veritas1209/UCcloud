"""Isolated API checks; no OpenStack or production database access."""

import base64
import json
import os
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient


temporary = tempfile.TemporaryDirectory(prefix="portal-self-service-")
os.environ["PORTAL_DB"] = str(Path(temporary.name) / "portal.db")
os.environ["PORTAL_ADMIN_PASSWORD"] = "Admin-test-passphrase-2026!"

from backend import app as portal  # noqa: E402
from backend import openstack_service  # noqa: E402


def fake_project(name, slug, team_id):
    return dict(project_id=f"project-{team_id}", network_id=f"network-{team_id}",
                subnet_id=f"subnet-{team_id}", router_id="router-1", cidr=f"10.30.{20 + team_id}.0/24")


portal.ensure_team_project = fake_project
captured = {}


def fake_create(*args):
    captured["args"] = args
    return {"id": "d58be315-054e-49fb-bb25-14a5544adf39"}


portal.create_server = fake_create
portal.get_console_url = lambda *args: "http://172.141.0.231:6080/vnc_auto.html?token=qa"


def login(client, username, password):
    response = client.post("/api/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200, response.text
    return response.json()["csrf_token"]


password = "abcdefgh"
with TestClient(portal.app, base_url="http://localhost") as client:
    first = client.post("/api/auth/register", json={
        "username": "owner", "email": "owner@example.com", "team_name": "Class Lab", "password": password,
    })
    assert first.status_code == 201 and first.json()["ready"] is True, first.text
    assert login(client, "owner", password)
    owner_csrf = login(client, "OWNER@example.com", password)
    wrong_email = client.post("/api/auth/login", json={"username": "owner@example.com", "password": "wrong-pass"})
    assert wrong_email.status_code == 401 and wrong_email.json()["detail"] == "Invalid username or password"
    owner = client.get("/api/me").json()
    assert owner["status"] == "active" and owner["team_owner"] is True

    original_hash_password = portal.hash_password
    hash_calls = []

    def count_hash(value):
        hash_calls.append(value)
        return original_hash_password(value)

    portal.hash_password = count_hash
    try:
        existing = client.post("/api/auth/register", json={
            "username": "member", "email": "member@example.com", "team_name": "class lab", "password": password,
        })
        missing = client.post("/api/auth/register", json={
            "username": "member", "email": "member@example.com", "team_name": "Unknown Team",
            "password": password, "join_existing": True,
        })
    finally:
        portal.hash_password = original_hash_password
    assert existing.status_code == 409 and existing.json()["detail"]["code"] == "team_exists"
    assert missing.status_code == 409 and missing.json()["detail"] == "팀을 찾을 수 없습니다. 다시 확인하세요."
    assert not hash_calls, "team confirmation should not hash the password"
    join = client.post("/api/auth/register", json={
        "username": "member", "email": "member@example.com", "team_name": "Class Lab",
        "password": password, "join_existing": True,
    })
    assert join.status_code == 201 and join.json()["ready"] is False, join.text
    member_csrf = login(client, "member", password)
    assert client.get("/api/me").json()["status"] == "pending"
    assert client.get("/api/team/requests").status_code == 403

    owner_csrf = login(client, "owner", password)
    requests = client.get("/api/team/requests")
    assert requests.status_code == 200 and len(requests.json()["requests"]) == 1
    member_id = requests.json()["requests"][0]["id"]
    approved = client.post(f"/api/team/requests/{member_id}/approve", headers={"X-CSRF-Token": owner_csrf})
    assert approved.status_code == 200, approved.text
    assert client.post(f"/api/team/requests/{member_id}/approve", headers={"X-CSRF-Token": owner_csrf}).status_code == 409

    member_csrf = login(client, "member", password)
    assert client.get("/api/me").json()["status"] == "active"
    assert client.get("/api/admin/capacity").status_code == 403
    assert client.get("/api/admin/registration-code").status_code == 403
    assert client.post("/api/instances", json={}, headers={"X-CSRF-Token": member_csrf}).status_code == 422
    created = client.post("/api/instances", json={
        "name": "test-vm", "image_id": "d58be315-054e-49fb-bb25-14a5544adf39",
        "flavor_id": "small", "vm_username": "student", "vm_password": "abcdefgh",
        "request_id": "c6e69a7b-41bf-4bd4-bbac-1eed7aa26205",
    }, headers={"X-CSRF-Token": member_csrf})
    assert created.status_code == 202 and captured["args"][-2] == "c6e69a7b-41bf-4bd4-bbac-1eed7aa26205" and captured["args"][-1] == [], created.text
    portal.create_server = lambda *args: (_ for _ in ()).throw(openstack_service.InstanceQuotaError("이 팀은 VM을 더 만들 수 없습니다."))
    full = client.post("/api/instances", json={
        "name": "another-vm", "image_id": "d58be315-054e-49fb-bb25-14a5544adf39",
        "flavor_id": "small", "vm_username": "student", "vm_password": "abcdefgh",
    }, headers={"X-CSRF-Token": member_csrf})
    assert full.status_code == 409 and "VM을 더 만들 수 없습니다" in full.json()["detail"]
    console = client.post("/api/instances/d58be315-054e-49fb-bb25-14a5544adf39/console",
                          headers={"X-CSRF-Token": member_csrf})
    assert console.status_code == 200 and console.json()["url"] == "http://127.0.0.1:6080/vnc_auto.html?token=qa"
    def invalid_start(*args):
        raise openstack_service.InstanceStateError("VM이 이미 켜져 있습니다. '접속'을 눌러 주세요.")
    portal.server_action = invalid_start
    start = client.post("/api/instances/d58be315-054e-49fb-bb25-14a5544adf39/action",
                        json={"action": "start"}, headers={"X-CSRF-Token": member_csrf})
    assert start.status_code == 409 and "이미 켜져" in start.json()["detail"]

    login(client, "admin", "Admin-test-passphrase-2026!")
    events = client.get("/api/admin/events")
    assert events.status_code == 200 and any(item["action"] == "team.join.approve" for item in events.json()["events"])
    portal.cluster_capacity = lambda: {"online": 4, "total": 4, "memory_free_mb": 110000}
    assert client.get("/api/admin/capacity").json() == {"online": 4, "total": 4, "memory_free_mb": 110000}
    credential = Path(temporary.name) / "registration-code"
    credential.write_text("2026UCCS-KKS-TEST\n", encoding="utf-8")
    os.environ["CREDENTIALS_DIRECTORY"] = temporary.name
    portal.REGISTRATION_CODE = ""
    code = client.get("/api/admin/registration-code")
    assert code.status_code == 200 and code.json() == {"registration_code": "2026UCCS-KKS-TEST"}
    assert code.headers["cache-control"] == "no-store"
    credential.write_text("bad code\n", encoding="utf-8")
    assert client.get("/api/admin/registration-code").json() == {"registration_code": ""}
    os.environ.pop("CREDENTIALS_DIRECTORY")
    assert client.post(f"/api/admin/users/{member_id}/approve").status_code == 404

config = base64.b64decode(openstack_service._vm_user_data("student", "abcdefgh")).decode()
assert config.startswith("#cloud-config\n")
assert "abcdefgh" not in config
cloud_config = json.loads(config.split("\n", 1)[1])
assert cloud_config["users"][1]["hashed_passwd"].startswith("$6$")
assert cloud_config["ssh_pwauth"] is True
files = {item["path"]: item for item in cloud_config["write_files"]}
installer = files["/usr/local/sbin/uccloud-install-tailscale"]
unit = files["/etc/systemd/system/uccloud-tailscale-install.service"]
assert installer["permissions"] == "0755"
assert "pkgs.tailscale.com/stable/ubuntu/noble" in installer["content"]
assert "tailscale up" not in installer["content"]
assert "Restart=on-failure" in unit["content"]
assert "RestartSec=120" in unit["content"]
assert cloud_config["runcmd"][-1] == ["systemctl", "start", "--no-block", "uccloud-tailscale-install.service"]
print("Self-service API checks passed")
temporary.cleanup()
