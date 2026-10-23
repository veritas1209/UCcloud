"""Preserve the tested serial-console wrapper across OSA service regeneration."""
from pathlib import Path
import json,subprocess,yaml
ROOT=Path('/var/lib/uccloud-maintenance/20261008')
role=Path('/etc/ansible/roles/os_nova/defaults/main.yml').read_text()
assert 'nova_serialproxy_init_overrides' in role
unit=subprocess.check_output(['lxc-attach','-n','controller1-nova-api-container-cb1ec29d','--','systemctl','show','nova-serialproxy','-p','ExecStart','--value'],text=True)
assert '/usr/local/lib/cloud-portal/serialproxy_fork.py' in unit
path=Path('/etc/openstack_deploy/user_variables.yml');before=path.read_text();values=yaml.safe_load(before)
key='nova_serialproxy_init_overrides';assert key not in values
backup=ROOT/'user_variables-before-serial-wrapper.yml';assert not backup.exists()
backup.write_text(before);backup.chmod(0o600)
override={key:{'Service':{'ExecStart':'{{ nova_bin }}/python3 /usr/local/lib/cloud-portal/serialproxy_fork.py'}}}
updated=before.rstrip()+'\n\n# Tested serial proxy wrapper for Python 3.14, 2026-10-08\n'+yaml.safe_dump(override,sort_keys=False)
after=yaml.safe_load(updated);assert all(after.get(k)==v for k,v in values.items())
path.write_text(updated)
print('TESTED_SERIAL_PROXY_OVERRIDE_PERSISTED_WITHOUT_SERVICE_RESTART')
