"""Controller-only idempotent-create check; deletes only its disposable VM(s)."""

import secrets
import time
import uuid

from openstack.exceptions import NotFoundException

from backend.database import db
from backend.openstack_service import (
    create_server, delete_server, list_catalog, list_servers, quota, team_connection,
)


TEAM_SLUG = "hajin"
TEAM_PROJECT_ID = "ccac6ab29ec243aba503965495a8ac05"
with db() as conn:
    team = conn.execute("SELECT * FROM teams WHERE slug=?", (TEAM_SLUG,)).fetchone()
    assert team and team["openstack_project_id"] == TEAM_PROJECT_ID and team["network_id"]

catalog = list_catalog(TEAM_SLUG)
image = catalog["images"][0]
flavor = next(item for item in catalog["flavors"] if item["name"] == "small")
usage = quota(TEAM_PROJECT_ID)
for key, needed in (("instances", 1), ("cores", flavor["vcpus"]), ("ram", flavor["ram"])):
    item = usage[key]
    if item["limit"] >= 0 and item["limit"] - item["in_use"] < needed:
        raise RuntimeError("Not enough team quota for disposable VM")

name = "portal-qa-request-" + secrets.token_hex(5)
request_id = str(uuid.uuid4())
known_ids = set()


def matching_servers():
    return [server for server in team_connection(TEAM_SLUG).compute.servers(details=True)
            if server.project_id == TEAM_PROJECT_ID
            and (server.name == name or (server.metadata or {}).get("portal_request_id") == request_id)]


try:
    first = create_server(
        TEAM_SLUG, TEAM_PROJECT_ID, "portal-qa", team["network_id"], name,
        image["id"], flavor["id"], "student", secrets.token_urlsafe(24), None, request_id,
    )
    known_ids.add(first["id"])
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        matches = matching_servers()
        if any(server.id == first["id"] for server in matches):
            break
        time.sleep(3)
    else:
        raise RuntimeError("Created VM not visible in Nova list")
    second = create_server(
        TEAM_SLUG, TEAM_PROJECT_ID, "portal-qa", team["network_id"], name,
        image["id"], flavor["id"], "student", secrets.token_urlsafe(24), None, request_id,
    )
    known_ids.add(second["id"])
    assert second["id"] == first["id"], "Duplicate VM created for the same request ID"
    matches = matching_servers()
    assert len(matches) == 1 and matches[0].id == first["id"]
    listed = next(item for item in list_servers(TEAM_SLUG, TEAM_PROJECT_ID)
                  if item["id"] == first["id"])
    assert listed["request_id"] == request_id
    print("Real Nova create and retry returned one VM with the same request ID")
finally:
    try:
        matches = matching_servers()
    except Exception as exc:
        matches = []
        print(f"Cleanup listing failed; falling back to known VM IDs: {type(exc).__name__}")
    known_ids.update(server.id for server in matches)
    for server_id in known_ids:
        try:
            delete_server(TEAM_SLUG, TEAM_PROJECT_ID, server_id)
        except (NotFoundException, ValueError):
            pass
    deadline = time.monotonic() + 120
    while known_ids and time.monotonic() < deadline:
        cloud = team_connection(TEAM_SLUG)
        remaining = set()
        for server_id in known_ids:
            try:
                if cloud.compute.get_server(server_id) is not None:
                    remaining.add(server_id)
            except NotFoundException:
                pass
        known_ids = remaining
        if known_ids:
            time.sleep(4)
    if known_ids:
        raise RuntimeError(f"Disposable VM deletion not complete: {sorted(known_ids)}")
    print("Disposable VM removed")
