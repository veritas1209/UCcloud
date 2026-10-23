"""No production database is touched by this test."""

import importlib.util
import os
import sqlite3
import stat
import tempfile
from contextlib import closing
from pathlib import Path


script = Path(__file__).resolve().parent.parent / "ops" / "backup_portal_db.py"
spec = importlib.util.spec_from_file_location("backup_portal_db", script)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

with tempfile.TemporaryDirectory(prefix="uccloud-backup-test-") as workspace:
    root = Path(workspace)
    source = root / "portal.db"
    backups = root / "backups"
    with sqlite3.connect(source) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        for table in module.TABLES:
            conn.execute(f"CREATE TABLE {table}(id INTEGER PRIMARY KEY, value TEXT)")
        conn.execute("INSERT INTO teams(value) VALUES('original')")
        conn.execute("INSERT INTO users(value) VALUES('student')")
    os.umask(0o077)
    filename, counts = module.create_backup(source, backups)
    assert counts["teams"] == counts["users"] == 1
    assert stat.S_IMODE(filename.stat().st_mode) == 0o600
    assert stat.S_IMODE(backups.stat().st_mode) == 0o700
    with sqlite3.connect(source) as conn:
        conn.execute("UPDATE teams SET value='changed'")
        conn.execute("INSERT INTO users(value) VALUES('later')")
    with closing(module.read_only(filename)) as conn:
        assert conn.execute("SELECT value FROM teams").fetchone()[0] == "original"
    assert module.restore_check(filename, backups) == counts
    assert not list(backups.glob(".restore-*")), list(backups.iterdir())
    assert not list(backups.glob(".portal-*")), list(backups.iterdir())

print("SQLite backup and restore checks passed")
