"""Disposable 20 GB VM smoke test. Run only on the UCcloud controller as root."""

from __future__ import annotations

import os
import secrets
import time
from uuid import uuid4

from backend.database import db
from backend.openstack_service import create_server, delete_server, list_catalog, team_connection
from backend.vm_backups import list_backups, process_one, queue_backup, queue_delete_backup, queue_restore


if os.geteuid() != 0 or os.getenv("UCCLOUD_LIVE_BACKUP_TEST") != "yes":
    raise SystemExit("Set UCCLOUD_LIVE_BACKUP_TEST=yes and run as root on the controller")

with db() as connection:
    team = connection.execute(
        "SELECT t.id,t.slug,t.openstack_project_id,t.network_id,u.id AS user_id "
        "FROM teams t JOIN users u ON u.team_id=t.id "
        "WHERE u.status='active' AND t.openstack_project_id IS NOT NULL ORDER BY t.id,u.id LIMIT 1"
    ).fetchone()
if not team:
    raise SystemExit("No active test team")

catalog = list_catalog(team["slug"])
image = next(item for item in catalog["images"] if item["name"] == "ubuntu-24.04")
flavor = next(item for item in catalog["flavors"] if item["name"] == "small")
server_id = os.getenv("UCCLOUD_SMOKE_SERVER_ID", "")
if server_id:
    print(f"REUSE_SMOKE_SERVER {server_id}", flush=True)
else:
    name = f"backup-smoke-{str(uuid4())[:8]}"
    created = create_server(team["slug"], team["openstack_project_id"], "smoke",
                            team["network_id"], name, image["id"], flavor["id"],
                            "smoke", secrets.token_urlsafe(24))
    server_id = created["id"]
    print(f"SMOKE_SERVER {server_id} {name}", flush=True)
compute = team_connection(team["slug"]).compute


def wait_status(desired: str, seconds: int = 600):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        server = compute.get_server(server_id)
        if server.status == desired:
            return server
        if server.status == "ERROR":
            raise RuntimeError("Disposable VM entered ERROR")
        time.sleep(5)
    raise TimeoutError(f"Disposable VM did not reach {desired}")


try:
    wait_status("ACTIVE")
    compute.stop_server(server_id)
    wait_status("SHUTOFF")
    for previous in list_backups(team["id"], server_id):
        if previous["state"] == "failed":
            queue_delete_backup(team["id"], team["user_id"], team["openstack_project_id"], server_id, previous["id"])
            assert process_one()
    backup_id = queue_backup(team["id"], team["user_id"], team["slug"], team["openstack_project_id"], server_id)["id"]
    print(f"SMOKE_BACKUP {backup_id}", flush=True)
    assert process_one()
    backup = next(item for item in list_backups(team["id"], server_id) if item["id"] == backup_id)
    print(f"BACKUP_RESULT {backup['state']} {backup['error'] or ''}", flush=True)
    if backup["state"] != "ready":
        raise RuntimeError("Backup did not become ready")
    queue_restore(team["id"], team["user_id"], team["slug"], team["openstack_project_id"], server_id, backup_id)
    assert process_one()
    backup = next(item for item in list_backups(team["id"], server_id) if item["id"] == backup_id)
    print(f"RESTORE_RESULT {backup['restore_state']} {backup['error'] or ''}", flush=True)
    if backup["restore_state"] != "idle":
        raise RuntimeError("Restore did not finish")
    compute.start_server(server_id)
    wait_status("ACTIVE")
    queue_delete_backup(team["id"], team["user_id"], team["openstack_project_id"], server_id, backup_id)
    assert process_one()
    assert not list_backups(team["id"], server_id)
    delete_server(team["slug"], team["openstack_project_id"], server_id)
    print("VM_BACKUP_SMOKE_OK", flush=True)
except Exception:
    print(f"SMOKE_NEEDS_REVIEW server={server_id}", flush=True)
    raise
