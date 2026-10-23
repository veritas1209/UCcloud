"""Online SQLite backup with a disposable restore check; never copy a live WAL file."""

from __future__ import annotations

import argparse
import json
import os
import secrets
import sqlite3
import stat
import tempfile
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path


SOURCE = Path("/var/lib/cloud-portal/portal.db")
DIRECTORY = Path("/var/backups/uccloud-portal")
TABLES = ("teams", "users", "sessions", "audit_log", "instance_ssh_host_keys", "vm_backups", "vm_sites")


def private_directory(path: Path) -> None:
    path.mkdir(mode=0o700, exist_ok=True)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
        raise RuntimeError(f"Backup directory must be private and owned by the runner: {path}")


def read_only(path: Path) -> sqlite3.Connection:
    if not stat.S_ISREG(path.lstat().st_mode):
        raise RuntimeError(f"Not a regular SQLite file: {path}")
    return sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=30)


def check_database(conn: sqlite3.Connection) -> dict[str, int]:
    result = [row[0] for row in conn.execute("PRAGMA integrity_check")]
    if result != ["ok"] or conn.execute("PRAGMA foreign_key_check").fetchone():
        raise RuntimeError("SQLite integrity or foreign-key check failed")
    return {table: conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0] for table in TABLES}


def standalone_snapshot(conn: sqlite3.Connection) -> None:
    """Move any copied WAL pages into the DB before renaming the file."""
    conn.commit()
    if conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal":
        checkpoint = conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
        if checkpoint[0] != 0:
            raise RuntimeError("SQLite backup checkpoint was busy")
    if conn.execute("PRAGMA journal_mode=DELETE").fetchone()[0] != "delete":
        raise RuntimeError("SQLite backup is not a standalone file")


def restore_check(backup: Path, directory: Path) -> dict[str, int]:
    """Restore into a separate temporary database and compare critical row counts."""
    private_directory(directory)
    handle = tempfile.NamedTemporaryFile(prefix=".restore-", suffix=".sqlite3", dir=directory, delete=False)
    restored = Path(handle.name)
    handle.close()
    try:
        with closing(read_only(backup)) as source, closing(sqlite3.connect(restored)) as target:
            source_stats = check_database(source)
            source.backup(target)
            standalone_snapshot(target)
            target_stats = check_database(target)
        if source_stats != target_stats:
            raise RuntimeError("Restored database row counts differ from the backup")
        return target_stats
    finally:
        restored.unlink(missing_ok=True)
        for suffix in ("-wal", "-shm"):
            Path(str(restored) + suffix).unlink(missing_ok=True)


def create_backup(source: Path = SOURCE, directory: Path = DIRECTORY) -> tuple[Path, dict[str, int]]:
    private_directory(directory)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    final = directory / f"portal-{stamp}-{secrets.token_hex(4)}.sqlite3"
    handle = tempfile.NamedTemporaryFile(prefix=".portal-", suffix=".tmp", dir=directory, delete=False)
    temporary = Path(handle.name)
    handle.close()
    try:
        with closing(read_only(source)) as live, closing(sqlite3.connect(temporary)) as copy:
            live.backup(copy)
            standalone_snapshot(copy)
            check_database(copy)
        stats = restore_check(temporary, directory)
        os.chmod(temporary, 0o600)
        with temporary.open("rb") as file:
            os.fsync(file.fileno())
        os.replace(temporary, final)
        descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        return final, stats
    finally:
        temporary.unlink(missing_ok=True)
        for suffix in ("-wal", "-shm"):
            Path(str(temporary) + suffix).unlink(missing_ok=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--verify", type=Path, help="Verify one existing backup by restoring it to a temporary DB")
    args = parser.parse_args()
    os.umask(0o077)
    if args.verify:
        if args.verify.parent.resolve() != DIRECTORY.resolve():
            raise SystemExit("Backup must be inside the UCcloud backup directory")
        statistics = restore_check(args.verify, DIRECTORY)
        print(json.dumps({"verified": str(args.verify), "rows": statistics}))
    else:
        filename, statistics = create_backup()
        print(json.dumps({"backup": str(filename), "rows": statistics}))
