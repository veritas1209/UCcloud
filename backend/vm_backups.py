"""Off-host VM backups. The web process queues jobs; a root-only worker moves image bytes."""

from __future__ import annotations

import hashlib
import fcntl
import json
import logging
import os
import re
import shutil
import subprocess
import time
from pathlib import Path
from uuid import UUID, uuid4

from openstack.exceptions import NotFoundException

from .database import audit, db
from .openstack_service import _owned_server, admin_connection, team_connection

LOG = logging.getLogger("cloud_portal.backups")
GIB = 1024 ** 3
NODE_LIMIT = 200 * GIB
FREE_RESERVE = 100 * GIB
LOCAL_DIR = Path("/var/lib/uccloud-vm-backups")
REMOTE_DIR = "/var/backups/uccloud-vms"
SOURCE_STAGE_DIR = "/var/lib/nova/uccloud-backup-staging"
NODES = {
    "comsv-01": "172.29.236.12",
    "comsv-04": "172.29.236.13",
    "comsv-05": "172.29.236.14",
    "user-ProLiant-DL20-Gen11": "172.29.236.15",
}
SSH = ("-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes", "-o", "ConnectTimeout=10")
ACTIVE_STATES = ("queued", "snapshotting", "exporting")


class BackupConflict(ValueError):
    pass


def _record(row) -> dict:
    return {key: row[key] for key in ("id", "server_id", "server_name", "state", "restore_state", "size_bytes", "created_at", "updated_at", "error")}


def list_backups(team_id: int, server_id: str) -> list[dict]:
    with db() as conn:
        rows = conn.execute(
            "SELECT * FROM vm_backups WHERE team_id=? AND server_id=? ORDER BY created_at DESC",
            (team_id, server_id),
        ).fetchall()
    return [_record(row) for row in rows]


def list_team_backups(team_id: int) -> list[dict]:
    with db() as conn:
        rows = conn.execute("SELECT * FROM vm_backups WHERE team_id=? ORDER BY created_at DESC", (team_id,)).fetchall()
    return [_record(row) for row in rows]


def _source_server(slug: str, project_id: str, server_id: str):
    server = _owned_server(team_connection(slug), server_id, project_id)
    if server.status != "SHUTOFF":
        raise BackupConflict("먼저 VM을 꺼 주세요. 꺼진 상태에서 백업하거나 복구할 수 있습니다.")
    admin_server = _owned_server(admin_connection(), server_id, project_id)
    host = admin_server.hypervisor_hostname
    if host not in NODES:
        raise BackupConflict("VM 위치를 확인할 수 없습니다. 운영자에게 문의해 주세요.")
    return server, host


def queue_backup(team_id: int, user_id: int, slug: str, project_id: str, server_id: str) -> dict:
    server, host = _source_server(slug, project_id, server_id)
    with db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        busy = conn.execute(
            "SELECT 1 FROM vm_backups WHERE team_id=? AND server_id=? AND (state IN ('queued','snapshotting','exporting') OR restore_state IN ('queued','importing','rebuilding'))",
            (team_id, server_id),
        ).fetchone()
        if busy:
            raise BackupConflict("이미 백업 또는 복구 작업 중입니다. 잠시 후 다시 확인해 주세요.")
        backup_id = str(uuid4())
        conn.execute(
            "INSERT INTO vm_backups(id,team_id,project_id,server_id,server_name,source_host,state) VALUES(?,?,?,?,?,?,'queued')",
            (backup_id, team_id, project_id, server_id, server.name, host),
        )
        audit(conn, user_id, "vm.backup.queued", server_id, backup_id)
    return {"id": backup_id, "state": "queued"}


def queue_restore(team_id: int, user_id: int, slug: str, project_id: str, server_id: str, backup_id: str) -> dict:
    _source_server(slug, project_id, server_id)
    with db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT * FROM vm_backups WHERE id=? AND team_id=? AND project_id=? AND server_id=? AND state='ready'",
            (backup_id, team_id, project_id, server_id),
        ).fetchone()
        if not row:
            raise ValueError("백업을 찾을 수 없습니다.")
        if row["restore_state"] in ("queued", "importing", "rebuilding"):
            raise BackupConflict("이미 복구 중입니다. 잠시 후 다시 확인해 주세요.")
        busy = conn.execute(
            "SELECT 1 FROM vm_backups WHERE team_id=? AND server_id=? AND (state IN ('queued','snapshotting','exporting') OR restore_state IN ('queued','importing','rebuilding'))",
            (team_id, server_id),
        ).fetchone()
        if busy:
            raise BackupConflict("백업이 끝난 뒤 복구할 수 있습니다.")
        conn.execute(
            "UPDATE vm_backups SET restore_state='queued',error=NULL,updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (backup_id,),
        )
        audit(conn, user_id, "vm.restore.queued", server_id, backup_id)
    return {"id": backup_id, "restore_state": "queued"}


def has_backups(team_id: int, server_id: str) -> bool:
    with db() as conn:
        return conn.execute("SELECT 1 FROM vm_backups WHERE team_id=? AND server_id=? AND state!='failed' LIMIT 1", (team_id, server_id)).fetchone() is not None


def queue_delete_backup(team_id: int, user_id: int, project_id: str, server_id: str, backup_id: str) -> None:
    with db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT * FROM vm_backups WHERE id=? AND team_id=? AND project_id=? AND server_id=?", (backup_id, team_id, project_id, server_id)).fetchone()
        if not row:
            raise ValueError("백업을 찾을 수 없습니다.")
        if row["state"] not in {"ready", "failed"} or row["restore_state"] in {"queued", "importing", "rebuilding"}:
            raise BackupConflict("작업이 끝난 뒤 백업을 삭제할 수 있습니다.")
        conn.execute("UPDATE vm_backups SET state='deleting',updated_at=CURRENT_TIMESTAMP WHERE id=?", (backup_id,))
        audit(conn, user_id, "vm.backup.delete.queued", server_id, backup_id)


def backup_busy(team_id: int, server_id: str) -> bool:
    with db() as conn:
        return conn.execute("SELECT 1 FROM vm_backups WHERE team_id=? AND server_id=? AND (state IN ('queued','snapshotting','exporting') OR restore_state IN ('queued','importing','rebuilding')) LIMIT 1", (team_id, server_id)).fetchone() is not None


def _run(args: list[str], *, input_data: bytes | None = None, timeout: int = 90) -> str:
    result = subprocess.run(args, input=input_data, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout, check=True)
    return result.stdout.decode("utf-8", "replace").strip()


def _remote(host: str, command: str, *, input_data: bytes | None = None, timeout: int = 90) -> str:
    if host not in NODES:
        raise ValueError("Untrusted backup host")
    return _run(["ssh", *SSH, f"root@{NODES[host]}", command], input_data=input_data, timeout=timeout)


def _remote_usage(host: str) -> tuple[int, int]:
    output = _remote(host, f"du -sb {REMOTE_DIR} | cut -f1; df -B1 --output=avail / | tail -1")
    used, free = (int(item.strip()) for item in output.splitlines())
    return used, free


def _target_host(source: str, anticipated: int) -> str:
    candidates = []
    for host in NODES:
        if host == source:
            continue
        try:
            used, free = _remote_usage(host)
            if used + anticipated <= NODE_LIMIT and free - anticipated >= FREE_RESERVE:
                candidates.append((used, host))
        except Exception:
            LOG.warning("Backup host %s unavailable", host, exc_info=True)
    if not candidates:
        raise BackupConflict("백업 공간이 부족하거나 백업 노드에 연결할 수 없습니다. 운영자에게 문의해 주세요.")
    return min(candidates)[1]


def _set(backup_id: str, **updates) -> None:
    allowed = {"state", "restore_state", "target_host", "image_id", "disk_format", "size_bytes", "sha256", "error"}
    if not updates.keys() <= allowed:
        raise ValueError("Invalid backup update")
    columns = ",".join(f"{name}=?" for name in updates)
    with db() as conn:
        conn.execute(f"UPDATE vm_backups SET {columns},updated_at=CURRENT_TIMESTAMP WHERE id=?", (*updates.values(), backup_id))


def _wait_image(connection, image_id: str, timeout: int = 1800):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            image = connection.image.get_image(image_id)
        except NotFoundException:
            time.sleep(5)
            continue
        if image.status == "active":
            return image
        if image.status in {"killed", "deleted", "deactivated"}:
            raise RuntimeError("Snapshot image failed")
        time.sleep(5)
    raise TimeoutError("Snapshot image timed out")


def _stage_dir() -> None:
    LOCAL_DIR.mkdir(mode=0o700, exist_ok=True)
    if LOCAL_DIR.is_symlink() or LOCAL_DIR.stat().st_uid != 0 or LOCAL_DIR.stat().st_mode & 0o077:
        raise RuntimeError("Unsafe backup staging directory")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _backup(row) -> None:
    backup_id = row["id"]
    UUID(row["server_id"])
    UUID(backup_id)
    host = None
    source = None
    staged = LOCAL_DIR / f"{backup_id}.part"
    manifest = LOCAL_DIR / f"{backup_id}.json.part"
    try:
        server, source = _source_server(row["team_slug"], row["project_id"], row["server_id"])
        disk_gb = int((server.flavor or {}).get("disk", 0))
        if not 1 <= disk_gb <= 80:
            raise BackupConflict("이 VM의 저장공간 크기를 확인할 수 없습니다.")
        anticipated = disk_gb * GIB
        if shutil.disk_usage("/").free < anticipated + FREE_RESERVE:
            raise BackupConflict("컨트롤러의 임시 공간이 부족합니다. 운영자에게 문의해 주세요.")
        _, source_free = _remote_usage(source)
        if source_free < anticipated + FREE_RESERVE:
            raise BackupConflict("VM 노드의 임시 공간이 부족합니다. 운영자에게 문의해 주세요.")
        host = _target_host(source, anticipated)
        _set(backup_id, state="snapshotting", target_host=host)
        disk = f"/var/lib/nova/instances/{row['server_id']}/disk"
        source_temp = f"{SOURCE_STAGE_DIR}/{backup_id}.qcow2"
        _remote(source, f"test -f {disk} && test ! -L {disk} && runuser -u nova -- qemu-img convert -O qcow2 -c {disk} {source_temp}", timeout=3600)
        info = json.loads(_remote(source, f"runuser -u nova -- qemu-img info --output=json {source_temp}"))
        if info.get("format") != "qcow2" or info.get("backing-filename") or info.get("virtual-size") != anticipated:
            raise RuntimeError("Unexpected flattened VM image")
        _remote(source, f"runuser -u nova -- qemu-img check -q {source_temp}", timeout=3600)
        _set(backup_id, state="exporting", disk_format="qcow2")
        _stage_dir()
        _run(["scp", *SSH, f"root@{NODES[source]}:{source_temp}", str(staged)], timeout=3600)
        size = staged.stat().st_size
        if size > anticipated + GIB:
            raise RuntimeError("Flattened image exceeds the VM disk size")
        digest = _sha256(staged)
        used, free = _remote_usage(host)
        if not size or used + size > NODE_LIMIT or free - size < FREE_RESERVE:
            raise BackupConflict("선택한 백업 노드의 공간이 부족합니다. 운영자에게 문의해 주세요.")
        details = {"id": backup_id, "server_id": row["server_id"], "project_id": row["project_id"],
                   "team_id": row["team_id"], "disk_format": "qcow2", "sha256": digest,
                   "size_bytes": size, "created_at": row["created_at"]}
        manifest.write_text(json.dumps(details, sort_keys=True), encoding="utf-8")
        manifest.chmod(0o600)
        address = f"root@{NODES[host]}:{REMOTE_DIR}/"
        _run(["scp", *SSH, str(staged), address + f".{backup_id}.part"], timeout=3600)
        _run(["scp", *SSH, str(manifest), address + f".{backup_id}.json.part"], timeout=90)
        remote_hash = _remote(host, f"sha256sum {REMOTE_DIR}/.{backup_id}.part | cut -d' ' -f1")
        if remote_hash != digest:
            raise RuntimeError("Remote backup checksum mismatch")
        _remote(host, f"mv {REMOTE_DIR}/.{backup_id}.part {REMOTE_DIR}/{backup_id}.img && mv {REMOTE_DIR}/.{backup_id}.json.part {REMOTE_DIR}/{backup_id}.json")
        _set(backup_id, state="ready", size_bytes=size, sha256=digest, error=None)
        with db() as conn:
            audit(conn, None, "vm.backup.ready", row["server_id"], backup_id)
        # Keep only the newest verified backup for this VM.
        with db() as conn:
            old = conn.execute("SELECT id,target_host FROM vm_backups WHERE team_id=? AND server_id=? AND id!=? AND state='ready' AND restore_state='idle'", (row["team_id"], row["server_id"], backup_id)).fetchall()
        for previous in old:
            try:
                _remove_files(previous["target_host"], previous["id"])
                with db() as conn:
                    conn.execute("DELETE FROM vm_backups WHERE id=?", (previous["id"],))
            except Exception:
                LOG.exception("Could not prune old backup %s", previous["id"])
    except Exception as exc:
        LOG.exception("VM backup %s failed", backup_id)
        _set(backup_id, state="failed", error=str(exc)[:200] if isinstance(exc, BackupConflict) else "백업에 실패했습니다. 운영자에게 문의해 주세요.")
        if host:
            try:
                _remove_files(host, backup_id)
            except Exception:
                LOG.exception("Could not clean incomplete backup %s", backup_id)
    finally:
        staged.unlink(missing_ok=True)
        manifest.unlink(missing_ok=True)
        if source:
            try:
                _remote(source, f"rm -f -- {SOURCE_STAGE_DIR}/{backup_id}.qcow2")
            except Exception:
                LOG.exception("Could not remove temporary source image %s", backup_id)


def _remove_files(host: str, backup_id: str) -> None:
    if not re.fullmatch(r"[0-9a-f-]{36}", backup_id):
        raise ValueError("Invalid backup ID")
    _remote(host, f"rm -f -- {REMOTE_DIR}/{backup_id}.img {REMOTE_DIR}/{backup_id}.json {REMOTE_DIR}/.{backup_id}.part {REMOTE_DIR}/.{backup_id}.json.part")


def _restore(row) -> None:
    backup_id = row["id"]
    UUID(backup_id)
    UUID(row["server_id"])
    staged = LOCAL_DIR / f"{backup_id}.restore.part"
    connection = team_connection(row["team_slug"])
    image_id = None
    try:
        _source_server(row["team_slug"], row["project_id"], row["server_id"])
        _set(backup_id, restore_state="importing")
        _stage_dir()
        if not row["target_host"] or not row["sha256"] or not row["size_bytes"] or row["disk_format"] != "qcow2":
            raise RuntimeError("Incomplete backup record")
        remote_hash = _remote(row["target_host"], f"sha256sum {REMOTE_DIR}/{backup_id}.img | cut -d' ' -f1")
        if remote_hash != row["sha256"]:
            raise RuntimeError("Backup checksum mismatch")
        if shutil.disk_usage("/").free < 2 * row["size_bytes"] + FREE_RESERVE:
            raise BackupConflict("컨트롤러의 임시 공간이 부족합니다. 운영자에게 문의해 주세요.")
        _run(["scp", *SSH, f"root@{NODES[row['target_host']]}:{REMOTE_DIR}/{backup_id}.img", str(staged)], timeout=3600)
        if staged.stat().st_size != row["size_bytes"] or _sha256(staged) != row["sha256"]:
            raise RuntimeError("Downloaded backup checksum mismatch")
        image = connection.image.create_image(
            f"uccloud-restore-{backup_id}", filename=str(staged), disk_format=row["disk_format"],
            container_format="bare", visibility="private", wait=False, timeout=1800,
            meta={"uccloud_backup_id": backup_id},
        )
        image_id = image.id
        image = _wait_image(connection, image_id)
        if image.owner != row["project_id"]:
            raise RuntimeError("Restore image not ready")
        server, _ = _source_server(row["team_slug"], row["project_id"], row["server_id"])
        _set(backup_id, restore_state="rebuilding", image_id=image_id)
        connection.compute.rebuild_server(server, image_id)
        deadline = time.monotonic() + 1800
        saw_rebuild = False
        while time.monotonic() < deadline:
            current = connection.compute.get_server(row["server_id"])
            if current.status == "ERROR":
                raise RuntimeError("VM rebuild failed")
            if current.status not in {"SHUTOFF", "ACTIVE"} or getattr(current, "task_state", None):
                saw_rebuild = True
            rebuilt_image = (current.image or {}).get("id")
            if current.status == "SHUTOFF" and not getattr(current, "task_state", None) and (saw_rebuild or rebuilt_image == image_id):
                break
            time.sleep(5)
        else:
            raise TimeoutError("VM rebuild timed out")
        _set(backup_id, restore_state="idle", image_id=None, error=None)
        with db() as conn:
            audit(conn, None, "vm.restore.ready", row["server_id"], backup_id)
        try:
            connection.image.delete_image(image_id, ignore_missing=True)
        except Exception:
            LOG.exception("Could not remove temporary restore image %s", image_id)
        image_id = None
    except Exception:
        LOG.exception("VM restore %s failed", backup_id)
        _set(backup_id, restore_state="failed", error="복구에 실패했습니다. 운영자에게 문의해 주세요.")
        # An image may still be in use by a slow rebuild; keep it for operator review.
    finally:
        staged.unlink(missing_ok=True)


def _delete(row) -> None:
    try:
        if row["target_host"]:
            _remove_files(row["target_host"], row["id"])
        with db() as conn:
            conn.execute("DELETE FROM vm_backups WHERE id=? AND state='deleting'", (row["id"],))
    except Exception:
        LOG.exception("Could not delete backup %s", row["id"])


def process_one() -> bool:
    _stage_dir()
    with db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT b.*,t.slug AS team_slug FROM vm_backups b JOIN teams t ON t.id=b.team_id "
            "WHERE b.state IN ('queued','deleting') OR b.restore_state='queued' ORDER BY b.created_at LIMIT 1"
        ).fetchone()
        if not row:
            return False
        # Claim before leaving the transaction. A duplicate worker cannot run the same job.
        column = "state" if row["state"] == "queued" else "restore_state"
        value = "snapshotting" if row["state"] == "queued" else "importing"
        if row["state"] != "deleting":
            conn.execute(f"UPDATE vm_backups SET {column}=?,updated_at=CURRENT_TIMESTAMP WHERE id=?", (value, row["id"]))
    if row["state"] == "deleting":
        _delete(row)
    elif column == "state":
        _backup(row)
    else:
        _restore(row)
    return True


def recover_interrupted() -> None:
    """A previous one-shot worker exited before recording the final result."""
    with db() as conn:
        rows = conn.execute(
            "SELECT b.*,t.slug AS team_slug FROM vm_backups b JOIN teams t ON t.id=b.team_id "
            "WHERE b.state IN ('snapshotting','exporting') OR b.restore_state IN ('importing','rebuilding')"
        ).fetchall()
    for row in rows:
        try:
            UUID(row["id"])
        except ValueError:
            LOG.error("Invalid backup ID in interrupted job; operator review required")
            continue
        if row["state"] in {"snapshotting", "exporting"}:
            LOG.warning("Recovering interrupted backup %s", row["id"])
            _set(row["id"], state="failed", error="백업이 중단됐습니다. 다시 시도해 주세요.")
            try:
                _remote(row["source_host"], f"rm -f -- {SOURCE_STAGE_DIR}/{row['id']}.qcow2")
            except Exception:
                LOG.exception("Could not clean interrupted conversion %s", row["id"])
            if row["target_host"]:
                try:
                    _remove_files(row["target_host"], row["id"])
                except Exception:
                    LOG.exception("Could not clean interrupted transfer %s", row["id"])
        if row["restore_state"] in {"importing", "rebuilding"}:
            LOG.warning("Interrupted restore %s requires operator review", row["id"])
            _set(row["id"], restore_state="failed", error="복구가 중단됐습니다. VM 상태를 확인한 뒤 다시 시도해 주세요.")
        for suffix in (".part", ".json.part", ".restore.part"):
            (LOCAL_DIR / f"{row['id']}{suffix}").unlink(missing_ok=True)


if __name__ == "__main__":
    if os.geteuid() != 0:
        raise SystemExit("VM backup worker must run as root")
    _stage_dir()
    with (LOCAL_DIR / ".worker.lock").open("a+b") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SystemExit("Backup worker already running")
        recover_interrupted()
        process_one()
