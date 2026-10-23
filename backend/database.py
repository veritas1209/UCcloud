from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path


DB_PATH = Path(os.getenv("PORTAL_DB", "/var/lib/cloud-portal/portal.db"))


@contextmanager
def db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DB_PATH, timeout=20)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute("PRAGMA journal_mode=WAL")
    try:
        yield connection
    except BaseException:
        connection.rollback()
        raise
    else:
        connection.commit()
    finally:
        connection.close()


def init_db() -> None:
    with db() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS teams (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              name TEXT NOT NULL,
              slug TEXT NOT NULL UNIQUE,
              openstack_project_id TEXT UNIQUE,
              network_id TEXT,
              subnet_id TEXT,
              router_id TEXT,
              cidr TEXT,
              created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS users (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              username TEXT NOT NULL UNIQUE COLLATE NOCASE,
              email TEXT NOT NULL UNIQUE COLLATE NOCASE,
              password_hash TEXT NOT NULL,
              role TEXT NOT NULL DEFAULT 'member' CHECK(role IN ('admin','member')),
              status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','active','rejected')),
              requested_team TEXT,
              team_id INTEGER REFERENCES teams(id),
              team_owner INTEGER NOT NULL DEFAULT 0,
              ssh_public_key TEXT,
              created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS sessions (
              token_hash TEXT PRIMARY KEY,
              user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
              csrf_token TEXT NOT NULL,
              expires_at TEXT NOT NULL,
              created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS audit_log (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              user_id INTEGER REFERENCES users(id),
              action TEXT NOT NULL,
              target TEXT,
              detail TEXT,
              created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS throttle (
              key TEXT PRIMARY KEY,
              count INTEGER NOT NULL,
              expires_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS password_resets (
              token_hash TEXT PRIMARY KEY,
              user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
              expires_at INTEGER NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_password_resets_user ON password_resets(user_id);
            CREATE TABLE IF NOT EXISTS instance_ssh_host_keys (
              server_id TEXT PRIMARY KEY,
              public_key TEXT NOT NULL,
              first_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS instance_software_status (
              team_id INTEGER NOT NULL REFERENCES teams(id),
              server_id TEXT NOT NULL,
              payload TEXT NOT NULL,
              PRIMARY KEY(team_id,server_id)
            );
            CREATE TABLE IF NOT EXISTS agent_grants (
              id TEXT PRIMARY KEY,
              token_hash TEXT NOT NULL UNIQUE,
              user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
              team_id INTEGER NOT NULL REFERENCES teams(id),
              project_id TEXT NOT NULL,
              server_id TEXT NOT NULL,
              vm_username TEXT NOT NULL,
              private_key TEXT NOT NULL,
              expires_at INTEGER NOT NULL,
              revoked INTEGER NOT NULL DEFAULT 0,
              transferred INTEGER NOT NULL DEFAULT 0,
              created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE INDEX IF NOT EXISTS idx_agent_grants_user_vm ON agent_grants(user_id,server_id);
            CREATE TABLE IF NOT EXISTS agent_jobs (
              id TEXT PRIMARY KEY,
              grant_id TEXT NOT NULL REFERENCES agent_grants(id) ON DELETE CASCADE,
              state TEXT NOT NULL,
              payload TEXT NOT NULL,
              result TEXT,
              cancel_requested INTEGER NOT NULL DEFAULT 0,
              worker_id TEXT NOT NULL,
              created_at INTEGER NOT NULL,
              updated_at INTEGER NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_agent_jobs_grant ON agent_jobs(grant_id,created_at);
            CREATE TABLE IF NOT EXISTS vm_backups (
              id TEXT PRIMARY KEY,
              team_id INTEGER NOT NULL REFERENCES teams(id),
              project_id TEXT NOT NULL,
              server_id TEXT NOT NULL,
              server_name TEXT NOT NULL,
              source_host TEXT NOT NULL,
              target_host TEXT,
              state TEXT NOT NULL CHECK(state IN ('queued','snapshotting','exporting','ready','failed','deleting')),
              restore_state TEXT NOT NULL DEFAULT 'idle' CHECK(restore_state IN ('idle','queued','importing','rebuilding','failed')),
              image_id TEXT,
              disk_format TEXT,
              size_bytes INTEGER,
              sha256 TEXT,
              error TEXT,
              created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
              updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE INDEX IF NOT EXISTS idx_vm_backups_team_server ON vm_backups(team_id,server_id);
            CREATE TABLE IF NOT EXISTS vm_sites (
              team_id INTEGER NOT NULL REFERENCES teams(id),
              server_id TEXT NOT NULL,
              url TEXT NOT NULL,
              state TEXT NOT NULL,
              http_status INTEGER,
              message TEXT NOT NULL,
              checked_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
              PRIMARY KEY(team_id,server_id)
            );
            CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);
            CREATE INDEX IF NOT EXISTS idx_users_team ON users(team_id);
            CREATE UNIQUE INDEX IF NOT EXISTS idx_teams_name_nocase ON teams(name COLLATE NOCASE);
            """
        )
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(users)")}
        if "team_owner" not in columns:
            conn.execute("ALTER TABLE users ADD COLUMN team_owner INTEGER NOT NULL DEFAULT 0")
            conn.execute(
                """UPDATE users SET team_owner=1
                   WHERE team_id IS NOT NULL AND status='active'
                     AND id=(SELECT MIN(other.id) FROM users AS other
                             WHERE other.team_id=users.team_id AND other.status='active')"""
            )


def audit(conn: sqlite3.Connection, user_id: int | None, action: str, target: str = "", detail: str = "") -> None:
    conn.execute(
        "INSERT INTO audit_log(user_id, action, target, detail) VALUES(?,?,?,?)",
        (user_id, action, target[:200], detail[:1000]),
    )
