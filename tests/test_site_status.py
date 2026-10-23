"""Public site checks must reject private destinations and pin the verified TLS host."""

from __future__ import annotations

from subprocess import CompletedProcess
from unittest.mock import patch

from backend import site_status


for bad in (
    "http://hello.example.ts.net", "https://127.0.0.1", "https://a.b.ts.net:8443",
    "https://a.b.ts.net@127.0.0.1", "https://a.b.ts.net/admin",
    "https://a.b.ts.net?next=http://127.0.0.1", "https://a.b.ts.net.evil.test",
):
    try:
        site_status.normalize_site_url(bad)
    except ValueError:
        pass
    else:
        raise AssertionError(f"Accepted unsafe site URL: {bad}")

assert site_status.normalize_site_url("https://team.tail123.ts.net/") == (
    "https://team.tail123.ts.net", "team.tail123.ts.net",
)

with patch.object(site_status.subprocess, "run", return_value=CompletedProcess([], 0, "100.74.1.2\n127.0.0.1\n103.84.155.153\n", "")):
    assert site_status._public_address("team.tail123.ts.net") == "103.84.155.153"
with patch.object(site_status.subprocess, "run", return_value=CompletedProcess([], 0, "100.74.1.2\n", "")):
    try:
        site_status._public_address("team.tail123.ts.net")
    except ConnectionError:
        pass
    else:
        raise AssertionError("Accepted private-only DNS result")


class Socket:
    def __init__(self):
        self.sent = b""

    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass

    def settimeout(self, value):
        assert value == 5

    def sendall(self, data):
        self.sent = data


class Context:
    def wrap_socket(self, sock, *, server_hostname):
        assert server_hostname == "team.tail123.ts.net"
        return sock


class Response:
    status = 200

    def __init__(self, sock):
        assert isinstance(sock, Socket)

    def begin(self):
        pass


sock = Socket()
with (patch.object(site_status, "_public_address", return_value="103.84.155.153"),
      patch.object(site_status.socket, "create_connection", return_value=sock) as connect,
      patch.object(site_status.ssl, "create_default_context", return_value=Context()),
      patch.object(site_status.http.client, "HTTPResponse", Response)):
    result = site_status.check_public_site("https://team.tail123.ts.net")
assert result["state"] == "online" and result["http_status"] == 200
connect.assert_called_once_with(("103.84.155.153", 443), timeout=5)
assert b"Host: team.tail123.ts.net\r\n" in sock.sent
assert b"GET / HTTP/1.1\r\n" in sock.sent
print("Site status safety checks passed")
