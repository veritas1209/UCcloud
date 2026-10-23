"""Run on the controller to smoke-test the deployed auth endpoints."""

import http.cookiejar
import json
import time
import urllib.error
import urllib.request
from pathlib import Path


BASE = "http://127.0.0.1:8088"
PASSWORD = next(
    json.loads(line.partition("=")[2])
    for line in Path("/etc/cloud-portal/portal.env").read_text().splitlines()
    if line.startswith("PORTAL_ADMIN_PASSWORD=")
)
opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))


def post(path, payload, headers=None):
    request = urllib.request.Request(
        BASE + path,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", **(headers or {})},
        method="POST",
    )
    started = time.perf_counter()
    try:
        response = opener.open(request)
    except urllib.error.HTTPError as error:
        response = error
    with response:
        return response.status, time.perf_counter() - started, json.loads(response.read())


wrong = post("/api/auth/login", {"username": "admin", "password": "incorrect-password"})
unknown = post("/api/auth/login", {"username": "no-such-account", "password": "incorrect-password"})
correct = post("/api/auth/login", {"username": "admin", "password": PASSWORD})
invalid = post("/api/auth/register", {
    "username": "test-input-only", "email": "not-an-email", "password": "long-enough-password", "team_name": "QA",
})
duplicate = post("/api/auth/register", {
    "username": "admin", "email": "admin-duplicate@example.com", "password": "long-enough-password", "team_name": "QA",
})
logout = post("/api/auth/logout", {}, {"X-CSRF-Token": correct[2]["csrf_token"]})

print("Auth status:", [wrong[0], unknown[0], correct[0], invalid[0], duplicate[0], logout[0]])
print("Login seconds (wrong, unknown, correct):", [round(item[1], 3) for item in (wrong, unknown, correct)])
assert [wrong[0], unknown[0], correct[0], invalid[0], duplicate[0], logout[0]] == [401, 401, 200, 422, 409, 200]
assert all(item[1] >= 0.95 for item in (wrong, unknown, correct))
