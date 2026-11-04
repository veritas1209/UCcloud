"""Dedicated UID broker: fixed operations, managed teams only, no arbitrary RPC."""
from __future__ import annotations

import json
import logging
import os
import re
import socket
import socketserver
import sqlite3
import struct
import threading
from contextlib import closing
from pathlib import Path
from uuid import UUID

from . import openstack_service as cloud

LOG = logging.getLogger("uccloud.provisioner")
LOCK = threading.Lock()
SLOTS = threading.BoundedSemaphore(8)
FIELDS = {"health": {"op"}, "capacity": {"op"},
          "ensure_team": {"op", "team_id"}, "quota": {"op", "project_id"},
          "backup_source": {"op", "project_id", "server_id"}}


def registry(**criteria):
    # The service has read-only ACL/mount access; never import database.db(),
    # which enables WAL writes. Parameters are bound, not interpolated inputs.
    if set(criteria) not in ({"id"}, {"openstack_project_id"}):
        raise ValueError("Invalid team lookup")
    key, value = next(iter(criteria.items()))
    path = Path(os.getenv("PORTAL_DB", "/var/lib/cloud-portal/portal.db"))
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=15)) as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute(
            f"SELECT id,name,slug,openstack_project_id FROM teams WHERE {key}=?", (value,)
        ).fetchone()
    if not row or not 1 <= row["id"] <= 200 or not re.fullmatch(r"[a-z0-9][a-z0-9-]{1,38}[a-z0-9]", row["slug"]):
        raise ValueError("Unmanaged team")
    return dict(row)


def uuid_value(value):
    if not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{32}|[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}", value):
        raise ValueError("Invalid object identifier")
    UUID(value)
    return value


def dispatch(payload):
    if not isinstance(payload, dict):
        raise ValueError("Invalid request")
    operation = payload.get("op")
    if not isinstance(operation, str) or operation not in FIELDS or set(payload) != FIELDS[operation]:
        raise ValueError("Unsupported request")
    if operation == "health":
        cloud.admin_connection().authorize()
        return {"healthy": True}
    if operation == "capacity":
        return cloud._direct_cluster_capacity()
    if operation == "ensure_team":
        index = payload["team_id"]
        if type(index) is not int:
            raise ValueError("Invalid team index")
        team = registry(id=index)
        # One provisioning transaction at a time; names, networks, role, quota,
        # service account, and router all come from trusted server policy.
        with LOCK:
            details = cloud._direct_ensure_team_project(team["name"], team["slug"], team["id"])
            if team["openstack_project_id"] and details["project_id"] != team["openstack_project_id"]:
                raise ValueError("Managed project mismatch")
            return details
    project_id = uuid_value(payload["project_id"])
    team = registry(openstack_project_id=project_id)
    # Check the OpenStack ownership marker too, not merely the portal registry.
    project = cloud.admin_connection().identity.get_project(project_id)
    if project.name != "portal-" + team["slug"] or project.description != "School Cloud Portal managed project":
        raise ValueError("Unmanaged project")
    if operation == "quota":
        return cloud._direct_quota(project_id)
    server_id = uuid_value(payload["server_id"])
    server = cloud._owned_server(cloud.admin_connection(), server_id, project_id)
    if server.status != "SHUTOFF":
        raise ValueError("VM must be stopped")
    from .vm_backups import NODES
    if server.hypervisor_hostname not in NODES:
        raise ValueError("Unmanaged host")
    return {"host": server.hypervisor_hostname}


class Handler(socketserver.StreamRequestHandler):
    def handle(self):
        import pwd
        self.connection.settimeout(8)
        acquired = False
        operation = "invalid"
        try:
            _, uid, _ = struct.unpack("3i", self.connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
            if uid not in (0, pwd.getpwnam("cloud-portal").pw_uid):
                raise PermissionError("Unauthorized peer")
            acquired = SLOTS.acquire(blocking=False)
            if not acquired:
                raise RuntimeError("Busy")
            raw = self.rfile.readline(2049)
            if len(raw) > 2048 or not raw.endswith(b"\n"):
                raise ValueError("Invalid frame")
            def unique(pairs):
                result = {}
                for key, value in pairs:
                    if key in result:
                        raise ValueError("Duplicate field")
                    result[key] = value
                return result
            payload = json.loads(raw, object_pairs_hook=unique)
            if isinstance(payload, dict) and payload.get("op") in FIELDS:
                operation = payload["op"]
            result = {"ok": True, "result": dispatch(payload)}
            LOG.info("operation=%s outcome=success", operation)
        except Exception as exc:
            LOG.warning("operation=%s outcome=denied_or_failed kind=%s", operation, type(exc).__name__)
            result = {"ok": False, "error": "unavailable"}
        finally:
            if acquired:
                SLOTS.release()
        try:
            self.wfile.write(json.dumps(result, separators=(",", ":")).encode() + b"\n")
        except OSError:
            pass


class Server(socketserver.ThreadingUnixStreamServer):
    daemon_threads = True
    request_queue_size = 8

    def process_request(self, request, client_address):
        # Bound accepted handler threads, including peers that never send a frame.
        if threading.active_count() >= 12:
            request.close()
            return
        super().process_request(request, client_address)


def main():
    logging.basicConfig(level=logging.INFO, format="%(name)s %(message)s")
    path = "/run/uccloud-provisioner/control.sock"
    os.umask(0o007)
    if os.path.lexists(path):
        os.unlink(path)
    with Server(path, Handler) as server:
        os.chmod(path, 0o660)
        server.serve_forever()


if __name__ == "__main__":
    main()
