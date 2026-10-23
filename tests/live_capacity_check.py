"""Controller-only authenticated check; never print the operator password or session."""

import http.cookiejar
import json
import urllib.request
from pathlib import Path


password = next(
    json.loads(line.partition("=")[2])
    for line in Path("/etc/cloud-portal/portal.env").read_text().splitlines()
    if line.startswith("PORTAL_ADMIN_PASSWORD=")
)
opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
base = "http://127.0.0.1:8088"


def post(path, payload, headers=None):
    request = urllib.request.Request(
        base + path, data=json.dumps(payload).encode(), method="POST",
        headers={"Content-Type": "application/json", **(headers or {})},
    )
    with opener.open(request) as response:
        return json.loads(response.read())


login = post("/api/auth/login", {"username": "admin", "password": password})
try:
    with opener.open(base + "/api/admin/capacity") as response:
        assert response.status == 200
        capacity = json.loads(response.read())
    assert capacity["total"] == 4 and capacity["online"] == 4, capacity
    assert isinstance(capacity["memory_free_mb"], int) and capacity["memory_free_mb"] > 0
    print("Operator capacity endpoint:", capacity)
finally:
    post("/api/auth/logout", {}, {"X-CSRF-Token": login["csrf_token"]})
