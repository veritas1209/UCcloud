"""Off-host backup access, placement, and queue checks without OpenStack calls."""

import tempfile
from pathlib import Path
from types import SimpleNamespace

from backend import database
from backend import vm_backups as backups


with tempfile.TemporaryDirectory() as folder:
    database.DB_PATH = Path(folder) / "portal.db"
    database.init_db()
    with database.db() as conn:
        conn.execute("INSERT INTO teams(id,name,slug) VALUES(1,'team-a','team-a')")
        conn.execute("INSERT INTO teams(id,name,slug) VALUES(2,'team-b','team-b')")
        conn.execute("INSERT INTO users(id,username,email,password_hash,status,team_id) VALUES(1,'a','a@example.test','x','active',1)")
        conn.execute("INSERT INTO users(id,username,email,password_hash,status,team_id) VALUES(2,'b','b@example.test','x','active',2)")

    seen = []
    def source(slug, project, server_id):
        seen.append((slug, project, server_id))
        return SimpleNamespace(name="my-vm", status="SHUTOFF"), "comsv-01"
    backups._source_server = source

    queued = backups.queue_backup(1, 1, "team-a", "project-a", "server-a")
    assert queued["state"] == "queued"
    assert seen == [("team-a", "project-a", "server-a")]
    assert backups.backup_busy(1, "server-a")
    try:
        backups.queue_backup(1, 1, "team-a", "project-a", "server-a")
        raise AssertionError("duplicate backup accepted")
    except backups.BackupConflict:
        pass
    assert backups.list_team_backups(2) == []

    with database.db() as conn:
        conn.execute("UPDATE vm_backups SET state='ready',target_host='comsv-04',sha256=?,size_bytes=100,disk_format='qcow2' WHERE id=?", ("a" * 64, queued["id"]))
    try:
        backups.queue_restore(2, 2, "team-b", "project-b", "server-a", queued["id"])
        raise AssertionError("cross-team restore accepted")
    except ValueError:
        pass
    restored = backups.queue_restore(1, 1, "team-a", "project-a", "server-a", queued["id"])
    assert restored["restore_state"] == "queued"
    try:
        backups.queue_delete_backup(1, 1, "project-a", "server-a", queued["id"])
        raise AssertionError("deleting during restore accepted")
    except backups.BackupConflict:
        pass
    with database.db() as conn:
        conn.execute("UPDATE vm_backups SET restore_state='idle' WHERE id=?", (queued["id"],))
    backups.queue_delete_backup(1, 1, "project-a", "server-a", queued["id"])
    assert backups.list_team_backups(1)[0]["state"] == "deleting"

    usage = {host: (20 * backups.GIB, 500 * backups.GIB) for host in backups.NODES}
    usage["comsv-04"] = (5 * backups.GIB, 500 * backups.GIB)
    backups._remote_usage = lambda host: usage[host]
    assert backups._target_host("comsv-01", 20 * backups.GIB) == "comsv-04"
    usage["comsv-04"] = (195 * backups.GIB, 500 * backups.GIB)
    assert backups._target_host("comsv-01", 20 * backups.GIB) in {"comsv-05", "user-ProLiant-DL20-Gen11"}

print("VM backup queue and placement checks passed")
