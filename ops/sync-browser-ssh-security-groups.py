"""One-time, idempotent SSH ingress rule for existing UCcloud teams."""

import ipaddress
import os

from backend.database import db
from backend.openstack_service import team_connection


def main():
    source = ipaddress.ip_network(os.environ["PORTAL_BROWSER_SSH_SOURCE"], strict=True)
    if source.version != 4 or source.prefixlen != 32:
        raise ValueError("Expected one controller IPv4 source")
    with db() as conn:
        teams = conn.execute(
            "SELECT slug,openstack_project_id FROM teams WHERE openstack_project_id IS NOT NULL"
        ).fetchall()
    for team in teams:
        network = team_connection(team["slug"]).network
        group = network.find_security_group("default", ignore_missing=False)
        if group.project_id != team["openstack_project_id"]:
            raise ValueError("Security group owner mismatch")
        rules = network.security_group_rules(security_group_id=group.id)
        if any(rule.direction == "ingress" and rule.protocol == "tcp"
               and rule.port_range_min == 22 and rule.port_range_max == 22
               and rule.remote_ip_prefix == str(source) for rule in rules):
            continue
        network.create_security_group_rule(
            security_group_id=group.id, direction="ingress", protocol="tcp",
            port_range_min=22, port_range_max=22, remote_ip_prefix=str(source),
            ethertype="IPv4", description="UCcloud browser SSH gateway only",
        )
        print(f"Added browser SSH ingress to team {team['slug']}")


if __name__ == "__main__":
    main()
