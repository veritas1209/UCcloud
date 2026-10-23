"""Controller-only email login check. Reads the existing admin secret without printing it."""

import http.cookiejar
import json
import time
import urllib.error
import urllib.request
from pathlib import Path

from backend.database import db


base = "http://127.0.0.1:8088"
password = next(
    json.loads(line.partition("=")[2])
    for line in Path("/etc/cloud-portal/portal.env").read_text().splitlines()
    if line.startswith("PORTAL_ADMIN_PASSWORD=")
)
with db() as conn:
    admin = conn.execute("SELECT username,email FROM users WHERE username='admin' AND role='admin'").fetchone()
assert admin and admin["email"]

opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))


def request(path, payload=None, headers=None):
    data = json.dumps(payload).encode() if payload is not None else None
    call = urllib.request.Request(
        base + path, data=data,
        headers={"Content-Type": "application/json", **(headers or {})},
        method="POST" if payload is not None else "GET",
    )
    started = time.perf_counter()
    try:
        response = opener.open(call, timeout=10)
    except urllib.error.HTTPError as error:
        response = error
    with response:
        return response.status, time.perf_counter() - started, json.loads(response.read())


login = request("/api/auth/login", {"username": admin["email"].upper(), "password": password})
assert login[0] == 200
try:
    assert login[1] >= 0.95
    profile = request("/api/me")
    assert profile[0] == 200 and profile[2]["username"] == admin["username"]
finally:
    logout = request("/api/auth/logout", {}, {"X-CSRF-Token": login[2]["csrf_token"]})
    assert logout[0] == 200
password = ""
print("Existing admin email login, profile, and logout passed")
