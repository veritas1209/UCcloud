"""Check a student's public Tailscale Funnel address without reaching private networks."""

from __future__ import annotations

import http.client
import ipaddress
import re
import socket
import ssl
import subprocess
from urllib.parse import urlsplit


HOST_PATTERN = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.ts\.net\Z")


def normalize_site_url(value: str) -> tuple[str, str]:
    parsed = urlsplit(value.strip())
    host = (parsed.hostname or "").lower()
    try:
        port = parsed.port
    except ValueError:
        port = -1
    if (parsed.scheme.lower() != "https" or parsed.username or parsed.password or
            port not in (None, 443) or not HOST_PATTERN.fullmatch(host) or
            parsed.path not in ("", "/") or parsed.query or parsed.fragment):
        raise ValueError("Tailscale의 https://…ts.net 주소를 입력해 주세요.")
    return f"https://{host}", host


def _public_address(host: str) -> str:
    # MagicDNS returns a private 100.x address on the controller. Query public DNS
    # and pin the validated address for the TLS connection to avoid DNS rebinding.
    result = subprocess.run(
        ["dig", "+short", "+time=2", "+tries=1", "@1.1.1.1", host, "A"],
        capture_output=True, text=True, timeout=4, check=False,
    )
    if result.returncode:
        raise ConnectionError("Public DNS lookup failed")
    for line in result.stdout.splitlines():
        try:
            address = ipaddress.IPv4Address(line.strip())
        except ipaddress.AddressValueError:
            continue
        if address.is_global:
            return str(address)
    raise ConnectionError("No public address")


def check_public_site(value: str) -> dict:
    url, host = normalize_site_url(value)
    try:
        address = _public_address(host)
        with socket.create_connection((address, 443), timeout=5) as raw:
            with ssl.create_default_context().wrap_socket(raw, server_hostname=host) as secure:
                secure.settimeout(5)
                request = (f"GET / HTTP/1.1\r\nHost: {host}\r\nUser-Agent: UCcloud-SiteCheck/1\r\n"
                           "Accept: */*\r\nConnection: close\r\n\r\n")
                secure.sendall(request.encode("ascii"))
                response = http.client.HTTPResponse(secure)
                response.begin()  # Only read headers; never download site content.
                status = response.status
        if 200 <= status < 400:
            label = "접속 가능"
            state = "online"
        elif status in (401, 403):
            label = "사이트는 켜져 있지만 로그인 또는 권한이 필요합니다."
            state = "online"
        elif status == 404:
            label = "서버는 응답하지만 첫 화면을 찾지 못했습니다."
            state = "warning"
        else:
            label = f"사이트가 오류를 반환했습니다. (HTTP {status})"
            state = "offline"
        return {"url": url, "state": state, "http_status": status, "message": label}
    except (OSError, ValueError, subprocess.SubprocessError, http.client.HTTPException):
        return {"url": url, "state": "offline", "http_status": None,
                "message": "UCcloud에서 지금 연결되지 않습니다. Tailscale Funnel과 웹사이트 실행 상태를 확인해 주세요."}
