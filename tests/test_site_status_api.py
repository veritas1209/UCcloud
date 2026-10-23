"""Site checks require a team-owned VM, session, CSRF token, and Funnel URL."""

import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi.testclient import TestClient


temporary = tempfile.TemporaryDirectory(prefix="uccloud-site-status-api-")
os.environ["PORTAL_DB"] = str(Path(temporary.name) / "portal.db")
os.environ["PORTAL_ADMIN_PASSWORD"] = "test-admin-password"

from backend import app as portal  # noqa: E402
from backend.database import db  # noqa: E402
from backend.security import token_hash  # noqa: E402


server_id = "c6e69a7b-41bf-4bd4-bbac-1eed7aa26205"
portal.team_connection = lambda slug: slug


def owned(slug, asked_id, project_id):
    if (slug, asked_id, project_id) != ("team-1", server_id, "project-1"):
        raise ValueError("Instance not found")
    return object()


portal._owned_server = owned
portal.check_public_site = lambda url: {
    "url": url, "state": "online", "http_status": 200, "message": "접속 가능",
}

with TestClient(portal.app, base_url="http://localhost") as client:
    with db() as conn:
        for team in (1, 2):
            conn.execute("INSERT INTO teams(id,name,slug,openstack_project_id,network_id) VALUES(?,?,?,?,?)",
                         (team, f"team-{team}", f"team-{team}", f"project-{team}", f"network-{team}"))
            conn.execute("INSERT INTO users(id,username,email,password_hash,status,team_id) VALUES(?,?,?,?,?,?)",
                         (team + 10, f"student-{team}", f"student-{team}@example.test", "x", "active", team))
            conn.execute("INSERT INTO sessions(token_hash,user_id,csrf_token,expires_at) VALUES(?,?,?,?)",
                         (token_hash(f"session-{team}"), team + 10, f"csrf-{team}",
                          (datetime.now(timezone.utc) + timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")))

    path = f"/api/instances/{server_id}/site"
    body = {"url": "https://team.tail123.ts.net"}
    assert client.get(path).status_code == 401
    assert client.post(path + "/check", json=body).status_code == 401
    client.cookies.set(portal.COOKIE_NAME, "session-1")
    assert client.post(path + "/check", json=body).status_code == 403
    assert client.post(path + "/check", json={"url": "https://127.0.0.1"},
                       headers={"X-CSRF-Token": "csrf-1"}).status_code == 422
    response = client.post(path + "/check", json=body, headers={"X-CSRF-Token": "csrf-1"})
    assert response.status_code == 200, response.text
    assert client.get(path).json()["site"]["state"] == "online"
    client.cookies.set(portal.COOKIE_NAME, "session-2")
    assert client.get(path).status_code == 404
    assert client.post(path + "/check", json=body,
                       headers={"X-CSRF-Token": "csrf-2"}).status_code == 404

print("Site status API isolation checks passed")
