"""Preserve OSA source formatting while persisting reviewed security overrides."""
import configparser,json,runpy,subprocess
from urllib.parse import urlsplit
from pathlib import Path
import yaml
ROOT=Path('/var/lib/uccloud-maintenance/20261008')
m=runpy.run_path(str(ROOT/'maintenance.py'));m['quarantine_required']()
path=Path('/etc/openstack_deploy/user_variables.yml');original=path.read_text()
current=yaml.safe_load(original) or {}
names=('nova_compute_driver','nova_nova_conf_overrides','nova_console_proxy_types','uccloud_cpu_flags_by_host','nova_serialconsoleproxy_base_url','glance_api_uwsgi_ini_overrides')
assert not any(key in current for key in names),'Existing overrides require an explicit merge'
flags={}
for host,alias in m['HOSTS'].items():
    text=m['python_node'](host,"import configparser\nc=configparser.ConfigParser(strict=False,interpolation=None);c.read('/etc/nova/nova.conf');print(c.get('libvirt','cpu_model_extra_flags',fallback=''))")
    values=[x.strip() for x in text.strip().split(',') if x.strip() and x.strip().lstrip('+-').lower()!='vmx']+['-vmx']
    flags[alias]=','.join(values);flags[host]=','.join(values)
read="import configparser\nc=configparser.ConfigParser(strict=False,interpolation=None);c.read('/etc/nova/nova.conf');print(c.get('serial_console','base_url'))"
base=subprocess.check_output(['lxc-attach','-n','controller1-nova-api-container-cb1ec29d','--','python3','-c',read],text=True).strip()
parsed_base=urlsplit(base)
assert parsed_base.scheme=='ws' and parsed_base.hostname and parsed_base.port==6083
overrides={
 'uccloud_cpu_flags_by_host':flags,
 'nova_compute_driver':"{{ 'libvirt.uccloud_security.SecurityLibvirtDriver' if 'nova_compute' in group_names else 'libvirt.LibvirtDriver' }}",
 'nova_console_proxy_types':['novnc','serialconsole'],
 'nova_serialconsoleproxy_base_url':base,
 'nova_nova_conf_overrides':{
  'libvirt':{'cpu_model_extra_flags':"{{ uccloud_cpu_flags_by_host.get(inventory_hostname, '-vmx') }}"},
  'serial_console':{'enabled':True,'proxyclient_address':'{{ nova_management_address }}','base_url':base}},
 'glance_api_uwsgi_ini_overrides':{'uwsgi':{'chunked-input-limit':4194304,'chunked-input-timeout':120}},
}
backup=ROOT/'user_variables-before-security.yml';assert not backup.exists()
backup.write_text(original);backup.chmod(0o600)
updated=original.rstrip()+'\n\n# UCcloud reviewed KVM/QEMU security policy, 2026-10-08\n'+yaml.safe_dump(overrides,sort_keys=False)
parsed=yaml.safe_load(updated);assert all(parsed.get(k)==v for k,v in current.items())
path.write_text(updated)
print('OSA_SECURITY_AND_SERIAL_SNAPSHOT_OVERRIDES_PERSISTED')
