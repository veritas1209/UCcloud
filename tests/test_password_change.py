"""Account password change, session revocation, CSRF, and stale-login race."""

import os
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient


temporary = tempfile.TemporaryDirectory(prefix="portal-password-change-")
os.environ["PORTAL_DB"] = str(Path(temporary.name) / "portal.db")
os.environ["PORTAL_ADMIN_PASSWORD"] = "isolated-admin-password"

from backend import app as portal  # noqa: E402


portal.AUTH_MIN_SECONDS = 0
old_password = "old password 한글"
new_password = "new password 한글"
headers = lambda csrf: {"X-CSRF-Token": csrf}

with TestClient(portal.app, base_url="http://localhost") as first, \
     TestClient(portal.app, base_url="http://localhost") as second:
    with portal.db() as conn:
        conn.execute(
            "INSERT INTO users(username,email,password_hash,role,status) VALUES(?,?,?,?,?)",
            ("student", "student@example.invalid", portal.hash_password(old_password), "member", "active"),
        )

    def login(client, password):
        return client.post("/api/auth/login", json={"username": "student", "password": password})

    first_login = login(first, old_password)
    second_login = login(second, old_password)
    assert first_login.status_code == second_login.status_code == 200
    csrf = first_login.json()["csrf_token"]
    body = {"current_password": old_password, "new_password": new_password}

    no_csrf = first.post("/api/profile/password", json=body)
    assert no_csrf.status_code == 403
    wrong = first.post("/api/profile/password", json={**body, "current_password": "incorrect"}, headers=headers(csrf))
    assert wrong.status_code == 401 and wrong.json()["detail"] == "Invalid current password"
    too_short = first.post("/api/profile/password", json={**body, "new_password": "short"}, headers=headers(csrf))
    assert too_short.status_code == 422
    unchanged = first.post("/api/profile/password", json={**body, "new_password": old_password}, headers=headers(csrf))
    assert unchanged.status_code == 422
    changed = first.post("/api/profile/password", json=body, headers=headers(csrf))
    assert changed.status_code == 200 and changed.json()["ok"] is True, changed.text
    assert first.get("/api/me").status_code == 200
    assert second.get("/api/me").status_code == 401
    assert login(second, old_password).status_code == 401
    assert login(second, new_password).status_code == 200

    with portal.db() as conn:
        record = conn.execute("SELECT password_hash FROM users WHERE username='student'").fetchone()
        assert record["password_hash"].startswith("bcrypt-sha256$")
        logged = conn.execute("SELECT action,target,detail FROM audit_log WHERE action LIKE 'profile.password.%'").fetchall()
        assert {row["action"] for row in logged} == {"profile.password.failure", "profile.password.update"}
        assert all(old_password not in str(tuple(row)) and new_password not in str(tuple(row)) for row in logged)

    for _ in range(8):
        attempted = first.post("/api/profile/password", json={**body, "current_password": "incorrect"}, headers=headers(csrf))
        assert attempted.status_code == 401
    blocked = first.post("/api/profile/password", json={"current_password": new_password,
                                                            "new_password": "fourth password"}, headers=headers(csrf))
    assert blocked.status_code == 429, blocked.text

    # A password rotation after bcrypt verification but before session insertion
    # must not create a fresh session using the old password.
    real_verify = portal.verify_password
    raced = False

    def rotate_during_verify(password, encoded):
        global raced
        valid = real_verify(password, encoded)
        if valid and password == new_password and not raced:
            raced = True
            with portal.db() as conn:
                conn.execute("UPDATE users SET password_hash=? WHERE username='student'",
                             (portal.hash_password("third password"),))
        return valid

    portal.verify_password = rotate_during_verify
    try:
        raced_login = login(TestClient(portal.app, base_url="http://localhost"), new_password)
    finally:
        portal.verify_password = real_verify
    assert raced and raced_login.status_code == 401, raced_login.text

print("Password change, session revocation, and login-race checks passed")
temporary.cleanup()
