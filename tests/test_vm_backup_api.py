"""Student backup endpoints must enforce sessions, CSRF, and team ownership."""

import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient


temporary = tempfile.TemporaryDirectory(prefix="uccloud-vm-backup-api-")
os.environ["PORTAL_DB"] = str(Path(temporary.name) / "portal.db")
os.environ["PORTAL_ADMIN_PASSWORD"] = "test-admin-password"
os.environ["PORTAL_VM_BACKUPS_ENABLED"] = "true"

from backend import app as portal  # noqa: E402
from backend import vm_backups  # noqa: E402
from backend.database import db  # noqa: E402
from backend.security import token_hash  # noqa: E402


vm_backups._source_server = lambda slug, project_id, server_id: (SimpleNamespace(name="vm", status="SHUTOFF"), "comsv-01")
server_id = "c6e69a7b-41bf-4bd4-bbac-1eed7aa26205"

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

    create_path = f"/api/instances/{server_id}/backups"
    assert client.post(create_path).status_code == 401
    client.cookies.set(portal.COOKIE_NAME, "session-1")
    assert client.post(create_path).status_code == 403
    created = client.post(create_path, headers={"X-CSRF-Token": "csrf-1"})
    assert created.status_code == 202, created.text
    backup_id = created.json()["id"]
    assert len(client.get("/api/backups").json()["backups"]) == 1

    client.cookies.set(portal.COOKIE_NAME, "session-2")
    assert client.get("/api/backups").json()["backups"] == []
    restore = client.post(f"{create_path}/{backup_id}/restore", headers={"X-CSRF-Token": "csrf-2"})
    assert restore.status_code == 404
    delete = client.delete(f"{create_path}/{backup_id}", headers={"X-CSRF-Token": "csrf-2"})
    assert delete.status_code == 404

print("VM backup API isolation checks passed")
