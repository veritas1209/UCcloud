"""Parallel guesses with different account names share one IP budget."""

import os
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from fastapi import HTTPException, Request, Response


temporary = tempfile.TemporaryDirectory(prefix="portal-login-throttle-")
os.environ["PORTAL_DB"] = str(Path(temporary.name) / "portal.db")
os.environ["PORTAL_LOGIN_IP_ATTEMPTS_PER_MINUTE"] = "8"

from backend import app as portal  # noqa: E402


portal.init_db()
portal.AUTH_MIN_SECONDS = 0
portal.verify_password = lambda password, encoded: False


def attempt(username: str, address: str) -> int:
    request = Request({"type": "http", "client": (address, 50000), "headers": []})
    try:
        portal.login(portal.LoginBody(username=username, password="wrong-password"), Response(), request)
    except HTTPException as exc:
        return exc.status_code
    raise AssertionError("Unknown user logged in")


with ThreadPoolExecutor(max_workers=9) as pool:
    statuses = list(pool.map(lambda n: attempt(f"unknown-{n}", "198.51.100.10"), range(9)))
assert sorted(statuses) == [401] * 8 + [429], statuses
assert attempt("another-user", "198.51.100.10") == 429
assert attempt("another-user", "198.51.100.11") == 401

for number in range(6):
    assert attempt("same-user", "198.51.100.12") == 401, number
assert attempt("same-user", "198.51.100.12") == 429
assert attempt("other-user", "198.51.100.12") == 401

print("Parallel login throttling checks passed")
temporary.cleanup()
