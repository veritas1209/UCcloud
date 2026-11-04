"""Small bounded client for the private, allow-listed OpenStack broker."""
from __future__ import annotations

import json
import os
import socket


class ProvisionerUnavailable(RuntimeError):
    pass


def request(operation: str, **payload) -> dict:
    path = os.getenv("PORTAL_PROVISIONER_SOCKET", "")
    if not path or not path.startswith("/run/uccloud-provisioner/"):
        raise ProvisionerUnavailable("Cloud provisioning service is unavailable")
    raw = json.dumps(dict(op=operation, **payload), separators=(",", ":")).encode() + b"\n"
    if len(raw) > 2048:
        raise ValueError("Invalid provisioning request")
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.settimeout(150 if operation == "ensure_team" else 35)
            connection.connect(path)
            connection.sendall(raw)
            with connection.makefile("rb") as stream:
                answer = stream.readline(16385)
        if len(answer) > 16384 or not answer.endswith(b"\n"):
            raise ValueError("Invalid provisioning response")
        result = json.loads(answer)
        if not isinstance(result, dict) or result.get("ok") is not True or not isinstance(result.get("result"), dict):
            raise ValueError("Provisioning operation failed")
        return result["result"]
    except (OSError, ValueError, TypeError):
        # Do not return SDK messages, credentials, or private infrastructure data.
        raise ProvisionerUnavailable("Cloud provisioning service is unavailable") from None
