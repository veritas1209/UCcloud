"""Controller-only keyless VM/noVNC smoke test; removes only its own VM."""

import secrets
import time
import urllib.request
from urllib.parse import urlsplit

from openstack.exceptions import NotFoundException

from backend.database import db
from backend.openstack_service import (
    create_server, delete_server, get_console_url, list_catalog, team_connection,
)


with db() as conn:
    team = conn.execute("SELECT * FROM teams WHERE id=3 AND slug='hajin'").fetchone()
    assert team and team["openstack_project_id"] == "ccac6ab29ec243aba503965495a8ac05"

catalog = list_catalog(team["slug"])
assert catalog["images"] and catalog["flavors"]
flavor = next((item for item in catalog["flavors"] if item["name"] == "small"), catalog["flavors"][0])
name = "portal-qa-keyless-" + secrets.token_hex(4)
server_id = None
try:
    created = create_server(
        team["slug"], team["openstack_project_id"], "portal-qa", team["network_id"],
        name, catalog["images"][0]["id"], flavor["id"], "student",
        secrets.token_urlsafe(24), None,
    )
    server_id = created["id"]
    cloud = team_connection(team["slug"])
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        server = cloud.compute.get_server(server_id)
        if server.status == "ACTIVE":
            break
        if server.status == "ERROR":
            raise RuntimeError("Test VM entered ERROR")
        time.sleep(5)
    else:
        raise RuntimeError("Test VM did not become ACTIVE")
    url = get_console_url(team["slug"], team["openstack_project_id"], server_id)
    parsed = urlsplit(url)
    assert parsed.scheme in {"http", "https"} and parsed.port == 6080
    with urllib.request.urlopen(url, timeout=20) as response:
        assert response.status == 200
    print("Keyless VM ACTIVE; noVNC HTTP 200")
finally:
    if not server_id:
        cloud = team_connection(team["slug"])
        matches = [item for item in cloud.compute.servers(details=True)
                   if item.name == name and item.project_id == team["openstack_project_id"]]
        if matches:
            server_id = matches[0].id
    if server_id:
        delete_server(team["slug"], team["openstack_project_id"], server_id)
        cloud = team_connection(team["slug"])
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            try:
                if cloud.compute.get_server(server_id) is None:
                    break
            except NotFoundException:
                break
            time.sleep(4)
        else:
            raise RuntimeError("Test VM deletion did not complete")
        print("Test VM deleted")
