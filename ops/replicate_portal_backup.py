"""Send a verified, encrypted portal DB backup to a second physical node.

The recovery private key must never be installed on either cloud node.
This program is intentionally not scheduled until the operator stores that key.
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import shlex
import subprocess
import time
from pathlib import Path
from posixpath import join as remote_join

from backup_crypto import encrypt
from backup_portal_db import DIRECTORY, restore_check


PUBLIC_KEY = Path("/etc/cloud-portal/backup-recovery.pub")
SIGNING_PRIVATE_KEY = Path("/etc/cloud-portal/backup-signing.key")
REMOTE_HOST = "172.29.236.12"  # comsv-01, independent of the controller disk
REMOTE_DIRECTORY = "/var/backups/uccloud-portal-encrypted"
MAX_BACKUP_AGE_SECONDS = 36 * 3600
SSH_OPTIONS = ("-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes", "-o", "ConnectTimeout=10")


def _run(command: list[str], timeout: int = 90) -> str:
    result = subprocess.run(command, check=True, capture_output=True, text=True, timeout=timeout)
    return result.stdout.strip()


def _ssh(command: str) -> str:
    return _run(["ssh", *SSH_OPTIONS, f"root@{REMOTE_HOST}", command])


def newest_verified_backup(directory: Path = DIRECTORY) -> Path:
    backups = [path for path in directory.glob("portal-*.sqlite3") if path.is_file() and not path.is_symlink()]
    if not backups:
        raise RuntimeError("No portal database backup is available")
    latest = max(backups, key=lambda path: path.stat().st_mtime)
    if time.time() - latest.stat().st_mtime > MAX_BACKUP_AGE_SECONDS:
        raise RuntimeError("The newest portal database backup is stale")
    restore_check(latest, directory)
    return latest


def replicate(directory: Path = DIRECTORY, public_key: Path = PUBLIC_KEY,
              signing_private_key: Path = SIGNING_PRIVATE_KEY) -> dict[str, str]:
    if os.geteuid() != 0:
        raise PermissionError("Run the backup transfer as root")
    source = newest_verified_backup(directory)
    token = secrets.token_hex(8)
    local_sealed = directory / f".transfer-{token}.ucb"
    remote_part = remote_join(REMOTE_DIRECTORY, f".incoming-{token}.ucb")
    remote_final = remote_join(REMOTE_DIRECTORY, f"{source.stem}-{token}.ucb")
    remote_part_quoted = shlex.quote(remote_part)
    remote_final_quoted = shlex.quote(remote_final)
    uploaded = False
    try:
        encrypt(source, public_key, signing_private_key, local_sealed)
        expected = hashlib.sha256(local_sealed.read_bytes()).hexdigest()
        directory_quoted = shlex.quote(REMOTE_DIRECTORY)
        _ssh(f"test ! -L {directory_quoted} && install -d -o root -g root -m 0700 -- {directory_quoted}"
             f" && test \"$(stat -c '%U:%a' -- {directory_quoted})\" = root:700")
        uploaded = True
        _run(["scp", "-p", *SSH_OPTIONS, str(local_sealed), f"root@{REMOTE_HOST}:{remote_part}"], timeout=180)
        actual = _ssh(f"sha256sum -- {remote_part_quoted}").split()[0]
        if actual != expected:
            raise RuntimeError("Remote encrypted backup checksum mismatch")
        _ssh(f"chmod 0600 -- {remote_part_quoted} && ln -- {remote_part_quoted} {remote_final_quoted}"
             f" && rm -- {remote_part_quoted}")
        uploaded = False
        return {"source": source.name, "remote": f"{REMOTE_HOST}:{remote_final}", "sha256": expected}
    finally:
        local_sealed.unlink(missing_ok=True)
        if uploaded:
            try:
                _ssh(f"rm -f -- {remote_part_quoted}")
            except (OSError, subprocess.SubprocessError):
                pass  # Keep the original transfer failure visible to systemd.


if __name__ == "__main__":
    os.umask(0o077)
    print(json.dumps(replicate()))
