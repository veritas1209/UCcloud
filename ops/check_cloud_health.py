"""Probe real OpenStack auth/quota and log only health transitions for operators."""

from __future__ import annotations

import sys

from backend.database import audit, db
from backend.openstack_service import admin_connection, quota


FAILED = "cloud.health.failed"
RECOVERED = "cloud.health.recovered"


def error_kind(exc: Exception) -> str:
    if isinstance(exc, OSError) and exc.errno is not None:
        return f"{type(exc).__name__}(errno={exc.errno})"
    return type(exc).__name__


def probe() -> None:
    with db() as conn:
        row = conn.execute(
            "SELECT openstack_project_id FROM teams "
            "WHERE openstack_project_id IS NOT NULL ORDER BY id LIMIT 1"
        ).fetchone()
    admin_connection().authorize()
    if row:
        quota(row["openstack_project_id"])


def record_transition(healthy: bool) -> None:
    with db() as conn:
        previous = conn.execute(
            "SELECT action FROM audit_log WHERE action IN (?, ?) ORDER BY id DESC LIMIT 1",
            (FAILED, RECOVERED),
        ).fetchone()
        last = previous["action"] if previous else None
        if healthy and last == FAILED:
            audit(conn, None, RECOVERED, "openstack")
        elif not healthy and last != FAILED:
            audit(conn, None, FAILED, "openstack")


def main() -> int:
    try:
        probe()
    except Exception as exc:
        try:
            record_transition(False)
        except Exception as audit_exc:
            print(f"Cloud health audit failed: {error_kind(audit_exc)}", file=sys.stderr)
        print(f"Cloud health probe failed: {error_kind(exc)}", file=sys.stderr)
        return 1
    try:
        record_transition(True)
    except Exception as exc:
        print(f"Cloud health audit failed: {error_kind(exc)}", file=sys.stderr)
        return 1
    print("Cloud auth and quota healthy")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
