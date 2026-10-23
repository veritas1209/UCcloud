"""Add only UCcloud team routes to the existing shared OpenStack router."""

import ipaddress
import json
import sqlite3
import subprocess
import sys

DB = "/var/lib/cloud-portal/portal.db"
ROUTER = "172.141.0.236"
DEVICE = "br-mgmt"


def run(*args):
    return subprocess.run(["/usr/sbin/ip", *args], check=True, capture_output=True, text=True).stdout


def main():
    with sqlite3.connect(f"file:{DB}?mode=ro", uri=True) as conn:
        teams = conn.execute(
            "SELECT id,cidr FROM teams WHERE openstack_project_id IS NOT NULL AND network_id IS NOT NULL"
        ).fetchall()
    for team_id, raw_cidr in teams:
        if not isinstance(team_id, int) or not 1 <= team_id <= 200:
            raise ValueError("Invalid team index")
        expected = ipaddress.ip_network(f"10.30.{20 + team_id}.0/24")
        if ipaddress.ip_network(raw_cidr, strict=True) != expected:
            raise ValueError("Team CIDR does not match allocation")
        cidr = str(expected)
        existing = json.loads(run("-j", "route", "show", cidr))
        if existing:
            if len(existing) != 1 or existing[0].get("gateway") != ROUTER or existing[0].get("dev") != DEVICE:
                raise RuntimeError(f"Route conflict for {cidr}")
            continue
        run("route", "add", cidr, "via", ROUTER, "dev", DEVICE, "proto", "static")
        print(f"Added UCcloud route: {cidr}")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"Browser SSH route sync failed: {exc}", file=sys.stderr)
        sys.exit(1)
