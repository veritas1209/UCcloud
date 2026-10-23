"""Read-only audit of UCcloud-managed VM ingress; run on the controller."""

import ipaddress
import os

from backend.database import db
from backend.openstack_service import team_connection


source = ipaddress.ip_network(os.environ["PORTAL_BROWSER_SSH_SOURCE"], strict=True)
assert source.version == 4 and source.prefixlen == 32
with db() as conn:
    teams = conn.execute(
        "SELECT slug,openstack_project_id,network_id,cidr FROM teams "
        "WHERE openstack_project_id IS NOT NULL"
    ).fetchall()

findings = []
for team in teams:
    cloud = team_connection(team["slug"])
    group = cloud.network.find_security_group("default", ignore_missing=False)
    assert group.project_id == team["openstack_project_id"], "Wrong security-group owner"
    ingress = list(cloud.network.security_group_rules(security_group_id=group.id))
    ingress = [rule for rule in ingress if rule.direction == "ingress"]
    print(f"Team {team['slug']}: {len(ingress)} ingress rules")
    for rule in ingress:
        peer = rule.remote_ip_prefix or rule.remote_group_id or "any"
        ports = f"{rule.port_range_min}-{rule.port_range_max}" if rule.port_range_min else "all"
        print(f"  {rule.ether_type} {rule.protocol or 'any'} {ports} from {peer}")
        if rule.remote_ip_prefix:
            network = ipaddress.ip_network(rule.remote_ip_prefix, strict=False)
            if network.prefixlen == 0:
                findings.append(f"{team['slug']}: unrestricted ingress")
        elif not rule.remote_group_id:
            findings.append(f"{team['slug']}: ingress without a bounded peer")
    ports = list(cloud.network.ports(network_id=team["network_id"]))
    vm_ports = [port for port in ports if port.device_owner and port.device_owner.startswith("compute:")]
    for port in vm_ports:
        if port.project_id != team["openstack_project_id"]:
            findings.append(f"{team['slug']}: VM port owner mismatch")
        if port.is_port_security_enabled is False:
            findings.append(f"{team['slug']}: VM port security disabled")
        groups = port.security_group_ids or []
        if group.id not in groups:
            findings.append(f"{team['slug']}: VM port not in default security group")
    print(f"  VM ports: {len(vm_ports)}")

print("Findings:", findings or "none")
if findings:
    raise SystemExit(1)
