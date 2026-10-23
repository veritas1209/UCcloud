"""Controller-only admin code check; never print the password or signup code."""

import http.cookiejar
import json
import urllib.error
import urllib.request
from pathlib import Path


base = "http://127.0.0.1:8088"
password = next(
    json.loads(line.partition("=")[2])
    for line in Path("/etc/cloud-portal/portal.env").read_text().splitlines()
    if line.startswith("PORTAL_ADMIN_PASSWORD=")
)
expected = Path("/etc/cloud-portal/registration-code").read_text().strip()
opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))


def request(path, payload=None, headers=None):
    data = json.dumps(payload).encode() if payload is not None else None
    call = urllib.request.Request(
        base + path, data=data,
        headers={"Content-Type": "application/json", **(headers or {})},
        method="POST" if payload is not None else "GET",
    )
    try:
        response = opener.open(call, timeout=10)
    except urllib.error.HTTPError as error:
        response = error
    with response:
        return response.status, response.headers, json.loads(response.read())


assert request("/api/admin/registration-code")[0] == 401
login = request("/api/auth/login", {"username": "admin", "password": password})
assert login[0] == 200
password = ""
try:
    status, headers, data = request("/api/admin/registration-code")
    assert status == 200 and data == {"registration_code": expected}
    assert headers["Cache-Control"] == "no-store"
finally:
    assert request("/api/auth/logout", {}, {"X-CSRF-Token": login[2]["csrf_token"]})[0] == 200
expected = ""
print("Private admin registration code check passed")
