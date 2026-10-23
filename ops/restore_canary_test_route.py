"""Restore only the reserved admin canary's temporary route after controller reboot."""
import ipaddress,json,runpy,subprocess
from pathlib import Path
ROOT=Path('/var/lib/uccloud-maintenance/20261008')
m=runpy.run_path(str(ROOT/'maintenance.py'));m['quarantine_required']();c=m['admin']()
route=json.loads((ROOT/'canary-test-route.json').read_text())
assert route['temporary'] is True
cidr=str(ipaddress.ip_network(route['cidr'],strict=True));gateway=str(ipaddress.ip_address(route['gateway']))
assert route['dev']=='br-mgmt'
canary=json.loads((ROOT/'canary-vm.json').read_text());server=c.compute.get_server(canary['id'])
assert server.project_id==c.current_project_id and server.status=='ACTIVE'
ports=list(c.network.ports(device_id=server.id));subnets={item['subnet_id'] for port in ports for item in port.fixed_ips if ipaddress.ip_address(item['ip_address']).version==4}
assert len(subnets)==1 and c.network.get_subnet(next(iter(subnets))).cidr==cidr
routes=json.loads(subprocess.check_output(['ip','-j','route','show'],text=True))
same=[r for r in routes if r.get('dst')==cidr]
if same:
    assert len(same)==1 and same[0].get('dev')==route['dev'] and same[0].get('gateway')==gateway
else:
    # Use only the exact next hop already serving an existing private VM route.
    assert any(r.get('dev')==route['dev'] and r.get('gateway')==gateway and ipaddress.ip_network(r.get('dst','0.0.0.0/0')).is_private for r in routes)
    subprocess.run(['ip','route','add',cidr,'via',gateway,'dev',route['dev']],check=True)
print('ONLY_RESERVED_ADMIN_CANARY_TEMPORARY_ROUTE_READY')
