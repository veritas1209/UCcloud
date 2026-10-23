"""Stage the new VM-size allowlist for controller reboot; no premature scheduling enable."""
import json,runpy,subprocess
from pathlib import Path
ROOT=Path('/var/lib/uccloud-maintenance/20261008')
m=runpy.run_path(str(ROOT/'maintenance.py'));m['quarantine_required']()
assert json.loads((ROOT/'integration-gate.json').read_text()).get('cold_migration')=='PASS'
verify=runpy.run_path(str(ROOT/'verify_hypervisor_gate.py'))['verify']
for host in m['HOSTS']:assert verify(host)['passed']
path=ROOT/'security-flavors.json';data=json.loads(path.read_text());assert len(data['flavors'])==3 and not data['published']
env=Path('/etc/cloud-portal/security-flavors.env');assert not env.exists()
env.write_text('PORTAL_ALLOWED_FLAVORS='+','.join(item['new_name'] for item in data['flavors'])+'\n');env.chmod(0o600)
for service in ('cloud-portal','cloud-portal-public'):
    folder=Path('/etc/systemd/system')/(service+'.service.d');folder.mkdir(parents=True,exist_ok=True)
    p=folder/'99-uccloud-security-flavors.conf';assert not p.exists()
    p.write_text('[Service]\nEnvironmentFile=/etc/cloud-portal/security-flavors.env\n');p.chmod(0o644)
subprocess.run(['systemctl','daemon-reload'],check=True)
data['published']=True;data['takes_effect']='controller_reboot';path.write_text(json.dumps(data,indent=2));path.chmod(0o600)
print('NEW_FLAVOR_ALLOWLIST_STAGED_FOR_REBOOT_NO_SERVICE_INTERRUPTION')
