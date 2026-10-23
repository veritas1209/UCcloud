"""One visitor's incorrect codes must not consume another visitor's retry budget."""

import os
import sqlite3
import tempfile
import threading
from pathlib import Path

from fastapi.testclient import TestClient


temporary = tempfile.TemporaryDirectory(prefix="portal-registration-throttle-")
os.environ["PORTAL_DB"] = str(Path(temporary.name) / "portal.db")
os.environ["PORTAL_ADMIN_PASSWORD"] = "Admin-test-passphrase-2026!"
os.environ["PORTAL_REGISTRATION_CODE"] = "2026UCCS-KKS-AB12"

from backend import app as portal  # noqa: E402


portal.ensure_team_project = lambda name, slug, index: {
    "project_id": f"project-{index}", "network_id": f"network-{index}",
    "subnet_id": f"subnet-{index}", "router_id": "router-1", "cidr": "10.30.21.0/24",
}


def registration(number: int, code: str) -> dict:
    return {
        "username": f"student{number}", "email": f"student{number}@example.com",
        "team_name": f"Team {number}", "password": "abcdefgh",
        "registration_code": code,
    }


with TestClient(portal.app, base_url="http://localhost", client=("198.51.100.10", 50000)) as first:
    for number in range(30):
        result = first.post("/api/auth/register", json=registration(number, "wrong"))
        assert result.status_code == 403, (number, result.text)
    exhausted = first.post("/api/auth/register", json=registration(30, "wrong"))
    assert exhausted.status_code == 429, exhausted.text

    with TestClient(portal.app, base_url="http://localhost", client=("198.51.100.11", 50000)) as second:
        other = second.post("/api/auth/register", json=registration(31, "wrong"))
        assert other.status_code == 403, other.text

    correct = first.post("/api/auth/register", json=registration(32, "2026UCCS-KKS-AB12"))
    assert correct.status_code == 201 and correct.json()["ready"], correct.text

    # A slow bcrypt hash must not hold the database's global write lock.
    hash_started = threading.Event()
    release_hash = threading.Event()
    original_hash_password = portal.hash_password
    result = {}

    def slow_hash(password):
        hash_started.set()
        assert release_hash.wait(5), "registration hash was not released"
        return original_hash_password(password)

    def register_while_hashing():
        result["response"] = first.post("/api/auth/register", json=registration(33, "2026UCCS-KKS-AB12"))

    portal.hash_password = slow_hash
    worker = threading.Thread(target=register_while_hashing)
    try:
        worker.start()
        assert hash_started.wait(5), "registration did not reach bcrypt"
        with sqlite3.connect(os.environ["PORTAL_DB"], timeout=0.5) as probe:
            probe.execute("BEGIN IMMEDIATE")
    finally:
        release_hash.set()
        worker.join(timeout=10)
        portal.hash_password = original_hash_password
    assert not worker.is_alive(), "registration did not finish"
    assert result["response"].status_code == 201, result["response"].text

print("Registration throttle isolation checks passed")
temporary.cleanup()
