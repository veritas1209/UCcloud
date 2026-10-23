"""Persist the verified Neutron WSGI compatibility loader in the OSA source."""
import configparser,json,subprocess,yaml
from pathlib import Path
ROOT=Path('/var/lib/uccloud-maintenance/20261008')
key='neutron_api_uwsgi_ini_overrides'
assert key in Path('/etc/ansible/roles/os_neutron/defaults/main.yml').read_text()
code="import configparser\nc=configparser.ConfigParser(strict=False,interpolation=None);c.read('/etc/uwsgi/neutron-server.ini');assert c['uwsgi']['wsgi']=='neutron_api_fork:application' and c['uwsgi']['pythonpath']=='/usr/local/lib/cloud-portal'\nprint('VERIFIED')"
assert subprocess.check_output(['lxc-attach','-n','controller1-neutron-server-container-182c42bf','--','python3','-c',code],text=True).strip()=='VERIFIED'
p=Path('/etc/openstack_deploy/user_variables.yml');before=p.read_text();values=yaml.safe_load(before)
assert key not in values,'Merge an existing override explicitly instead of overwriting it'
b=ROOT/'user_variables-before-neutron-api-wrapper.yml';assert not b.exists();b.write_text(before);b.chmod(0o600)
override={key:{'uwsgi':{'wsgi':'neutron_api_fork:application','pythonpath':'/usr/local/lib/cloud-portal'}}}
updated=before.rstrip()+'\n\n# Tested Neutron WSGI process-context loader, 2026-10-08\n'+yaml.safe_dump(override,sort_keys=False)
after=yaml.safe_load(updated);assert all(after.get(k)==v for k,v in values.items())
p.write_text(updated)
print('VERIFIED_NEUTRON_WSGI_LOADER_OVERRIDE_PERSISTED')
