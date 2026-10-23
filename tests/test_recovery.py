"""Isolated recovery API/security checks. Never contacts Gmail or the real cloud."""
import os
import tempfile
import time
from pathlib import Path
from unittest.mock import patch
from fastapi.testclient import TestClient

temporary = tempfile.TemporaryDirectory(prefix="uccloud-recovery-")
os.environ["PORTAL_DB"] = str(Path(temporary.name) / "test.db")
os.environ["PORTAL_ADMIN_PASSWORD"] = "test-admin-password"
os.environ["CREDENTIALS_DIRECTORY"] = temporary.name
(Path(temporary.name) / "smtp-password").write_text("test-only-app-password")
from backend import app as portal, recovery
from backend.database import db
from backend.security import hash_password, token_hash, new_token, verify_password

sent = []
def fake_send(email, token):
    sent.append((email, token))
    return True

with TestClient(portal.app, base_url="http://localhost") as client, patch.object(recovery, "send_reset", fake_send):
    with db() as conn:
        conn.execute("INSERT INTO users(username,email,password_hash,status) VALUES(?,?,?,?)",
                     ("student", "student@example.com", hash_password("oldpassword"), "active"))
        user_id = conn.execute("SELECT id FROM users WHERE username='student'").fetchone()[0]
    assert recovery.configured()
    page = client.get("/reset-password")
    assert page.status_code == 200 and page.headers["cache-control"] == "no-store"
    assert page.headers["referrer-policy"] == "no-referrer"
    assert client.post("/api/auth/find-id", json=dict(email="STUDENT@example.com")).json() == {"username": "student"}
    assert client.post("/api/auth/find-id", json=dict(email="absent@example.com")).json() == {"username": None}
    assert client.post("/api/auth/find-id", json=dict(email="not-email")).status_code == 422
    assert client.post("/api/auth/login", json=dict(username="student", password="oldpassword")).status_code == 200
    known = client.post("/api/auth/forgot-password", json=dict(email="student@example.com"))
    unknown = client.post("/api/auth/forgot-password", json=dict(email="absent@example.com"))
    assert known.status_code == unknown.status_code == 202 and known.json() == unknown.json()
    assert len(sent) == 1 and sent[0][0] == "student@example.com"
    token = sent[0][1]
    with db() as conn:
        row = conn.execute("SELECT * FROM password_resets").fetchone()
        assert row["token_hash"] == token_hash(token) and row["token_hash"] != token
        assert 1790 < row["expires_at"] - time.time() <= 1800
        assert conn.execute("SELECT COUNT(*) FROM sessions WHERE user_id=?", (user_id,)).fetchone()[0] > 0
    assert client.post("/api/auth/reset-password", json=dict(token=token, password="short")).status_code == 422
    expired = new_token()
    with db() as conn:
        conn.execute("INSERT INTO password_resets VALUES(?,?,?)", (token_hash(expired), user_id, int(time.time()) - 1))
    assert client.post("/api/auth/reset-password", json=dict(token=expired, password="newpassword")).status_code == 400
    assert client.post("/api/auth/reset-password", json=dict(token=token, password="newpassword")).status_code == 200
    assert client.get("/api/me").status_code == 401
    assert client.post("/api/auth/reset-password", json=dict(token=token, password="anotherpassword")).status_code == 400
    with db() as conn:
        assert conn.execute("SELECT COUNT(*) FROM password_resets WHERE user_id=?", (user_id,)).fetchone()[0] == 0
        value = conn.execute("SELECT password_hash FROM users WHERE id=?", (user_id,)).fetchone()[0]
        assert value.startswith("bcrypt-sha256$$2") and verify_password("newpassword", value) and not verify_password("oldpassword", value)
    assert client.post("/api/auth/login", json=dict(username="student", password="oldpassword")).status_code == 401
    assert client.post("/api/auth/login", json=dict(username="student", password="newpassword")).status_code == 200
    with patch.object(recovery, "send_reset", return_value=False):
        assert client.post("/api/auth/forgot-password", json=dict(email="student@example.com")).status_code == 202
    with db() as conn:
        assert conn.execute("SELECT COUNT(*) FROM password_resets").fetchone()[0] == 0
        assert conn.execute("SELECT 1 FROM audit_log WHERE action='auth.reset.mail_failed'").fetchone()
    assert client.post("/api/auth/forgot-password", json=dict(email="student@example.com")).status_code == 202
    assert client.post("/api/auth/forgot-password", json=dict(email="student@example.com")).status_code == 429
    with patch.object(recovery, "smtp_password", return_value=""):
        assert client.post("/api/auth/forgot-password", json=dict(email="student@example.com")).status_code == 503

with patch.object(recovery.smtplib, "SMTP_SSL") as smtp:
    assert recovery.send_reset("student@example.com", "test-token")
    assert smtp.call_args.args == ("smtp.gmail.com", 465)
    message = smtp.return_value.__enter__.return_value.send_message.call_args.args[0]
    assert "fluction.official@gmail.com" in message["From"]
    assert "/reset-password#token=test-token" in message.get_content()
    assert "test-only-app-password" not in message.get_content()
print("ID lookup, reset email, expiry, single use, session revocation, throttling and TLS checks passed")
temporary.cleanup()
