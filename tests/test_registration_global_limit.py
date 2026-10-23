"""Distributed signup attempts cannot create unlimited accounts or exhaust a valid class by typos."""

import os
import sqlite3
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from fastapi.testclient import TestClient


temporary = tempfile.TemporaryDirectory(prefix="portal-global-registration-")
os.environ["PORTAL_DB"] = str(Path(temporary.name) / "portal.db")
os.environ["PORTAL_ADMIN_PASSWORD"] = "Admin-test-passphrase-2026!"
os.environ["PORTAL_REGISTRATION_CODE"] = "2026UCCS-KKS-AB12"
os.environ["PORTAL_REGISTRATIONS_PER_HOUR"] = "2"

from backend import app as portal  # noqa: E402


portal.ensure_team_project = lambda name, slug, index: {
    "project_id": f"project-{index}", "network_id": f"network-{index}",
    "subnet_id": f"subnet-{index}", "router_id": "router-1", "cidr": "10.30.21.0/24",
}


def registration(number: int, code: str = "2026UCCS-KKS-AB12", **changes) -> dict:
    body = {
        "username": f"student{number}", "email": f"student{number}@example.com",
        "team_name": f"Team {number}", "password": "abcdefgh", "registration_code": code,
    }
    body.update(changes)
    return body


with TestClient(portal.app, base_url="http://localhost", client=("198.51.100.10", 50000)) as first:
    assert first.post("/api/auth/register", json=registration(1, "wrong")).status_code == 403
    assert first.post("/api/auth/register", json=registration(1)).status_code == 201
    assert first.post("/api/auth/register", json=registration(1, team_name="Another Team")).status_code == 409
    assert first.post("/api/auth/register", json=registration(4, team_name="Team 1")).status_code == 409

with TestClient(portal.app, base_url="http://localhost", client=("198.51.100.11", 50000)) as second:
    assert second.post("/api/auth/register", json=registration(2)).status_code == 201
    original_hash_password = portal.hash_password
    portal.hash_password = lambda value: (_ for _ in ()).throw(AssertionError("bcrypt ran after global cap"))
    try:
        response = second.post("/api/auth/register", json=registration(3))
        assert response.status_code == 429, response.text
    finally:
        portal.hash_password = original_hash_password

with sqlite3.connect(os.environ["PORTAL_DB"]) as conn:
    assert conn.execute("SELECT count FROM throttle WHERE key='register-global-success'").fetchone()[0] == 2
    assert conn.execute("SELECT count(*) FROM users WHERE role='member'").fetchone()[0] == 2
    assert conn.execute("SELECT count(*) FROM audit_log WHERE action='auth.register.rate_limited'").fetchone()[0] == 1

# Both requests can pass the read-only precheck; the write transaction must still admit only one.
with sqlite3.connect(os.environ["PORTAL_DB"]) as conn:
    conn.execute("DELETE FROM throttle WHERE key='register-global-success'")
portal.REGISTRATIONS_PER_HOUR = 1
barrier = threading.Barrier(2)
original_hash_password = portal.hash_password


def waiting_hash(value):
    barrier.wait(timeout=10)
    return original_hash_password(value)


def simultaneous_registration(number: int) -> int:
    with TestClient(portal.app, base_url="http://localhost", client=(f"198.51.100.{number}", 50000)) as client:
        return client.post("/api/auth/register", json=registration(number)).status_code


portal.hash_password = waiting_hash
try:
    with ThreadPoolExecutor(max_workers=2) as workers:
        statuses = list(workers.map(simultaneous_registration, [5, 6]))
finally:
    portal.hash_password = original_hash_password
assert sorted(statuses) == [201, 429], statuses
with sqlite3.connect(os.environ["PORTAL_DB"]) as conn:
    assert conn.execute("SELECT count FROM throttle WHERE key='register-global-success'").fetchone()[0] == 1
    assert conn.execute("SELECT count(*) FROM users WHERE role='member'").fetchone()[0] == 3
    assert conn.execute("SELECT count(*) FROM audit_log WHERE action='auth.register.rate_limited'").fetchone()[0] == 2

portal.REGISTRATIONS_PER_HOUR = 5
with portal.db() as conn:
    conn.execute("BEGIN IMMEDIATE")
    conn.execute("DELETE FROM throttle WHERE key='register-global-success'")
    for _ in range(5):
        portal._registration_capacity(conn, increment=True)
with sqlite3.connect(os.environ["PORTAL_DB"]) as conn:
    assert conn.execute("SELECT count(*) FROM audit_log WHERE action='auth.register.rate_warning'").fetchone()[0] == 1
    assert conn.execute("SELECT count(*) FROM audit_log WHERE action='auth.register.rate_limited'").fetchone()[0] == 3
with TestClient(portal.app, base_url="http://localhost", client=("198.51.100.20", 50000)) as operator:
    login = operator.post("/api/auth/login", json={
        "username": "admin", "password": "Admin-test-passphrase-2026!",
    })
    assert login.status_code == 200, login.text
    events = operator.get("/api/admin/events")
    assert events.status_code == 200, events.text
    actions = [entry["action"] for entry in events.json()["events"]]
    assert "auth.register.rate_warning" in actions
    assert "auth.register.rate_limited" in actions

print("Global registration limit, race protection, and operator alerts passed")
temporary.cleanup()
