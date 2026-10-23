"""Stage reviewed assets/repository only; do not stop or change a running VM."""
import hashlib,json,runpy,subprocess,sys
from pathlib import Path
ROOT=Path('/var/lib/uccloud-maintenance/20261008')
m=runpy.run_path(str(ROOT/'maintenance.py'));m['quarantine_required']()
host=sys.argv[1];assert host in m['HOSTS'] and host!='comsv-01'
first=json.loads((ROOT/'integration-gate.json').read_text())
assert all(first.get(k)=='PASS' for k in ('canary_boot','canary_serial','canary_network','canary_file_io','snapshot'))
repo=Path('/var/lib/uccloud-qemu-repository/20261008')
assert (repo/'InRelease').is_file()
inventory=json.loads(Path('/etc/openstack_deploy/openstack_inventory.json').read_text())
v=inventory['_meta']['hostvars'][m['HOSTS'][host]];address=v.get('ansible_host') or v['management_address']
m['node'](host,'mkdir -p /var/lib/uccloud-qemu-repository/20261008')
r=subprocess.run(['scp','-q','-r','-i','/root/.ssh/id_ed25519_osa','-o','BatchMode=yes','-o','StrictHostKeyChecking=yes',str(repo)+'/.','root@'+address+':'+str(repo)],capture_output=True,text=True,timeout=600)
assert r.returncode==0,'Repository staging failed; do not patch the host'
assets={
 'hypervisor_xml_policy.py':('/usr/local/lib/uccloud/hypervisor_xml_policy.py',0o644),
 'nova_uccloud_security.py':('/usr/local/lib/uccloud/nova_uccloud_security.py',0o644),
 'uccloud_libvirt_hook.py':('/etc/libvirt/hooks/qemu.d/90-uccloud-security',0o755),
 'hypervisor_policy_guard.py':('/usr/local/sbin/uccloud-hypervisor-policy-guard',0o755),
 'install_qemu_security.py':('/usr/local/sbin/uccloud-install-qemu-security',0o700),
 'prepare_compute_security.py':('/usr/local/sbin/uccloud-prepare-compute-security',0o700),
 'hypervisor_monitor.py':('/usr/local/sbin/uccloud-hypervisor-monitor',0o755),
}
# Assets are staged privately, not activated until the compute is drained.
stage='/var/backups/uccloud-hypervisor/20261008/staged-security'
import base64
payload={name:{'data':base64.b64encode((ROOT/name).read_bytes()).decode(),'dest':dest,'mode':mode} for name,(dest,mode) in assets.items()}
source="from pathlib import Path\nimport json,base64\nroot=Path("+repr(stage)+");root.mkdir(mode=0o700,exist_ok=True)\npayload=json.loads("+repr(json.dumps(payload))+")\nfor name,item in payload.items():\n p=root/name;p.write_bytes(base64.b64decode(item['data']));p.chmod(0o600)\np=root/'assets.json';p.write_text(json.dumps({n:{k:v for k,v in i.items() if k!='data'} for n,i in payload.items()}));p.chmod(0o600)\nprint('REVIEWED_SECURITY_ASSETS_STAGED')\n"
print(m['python_node'](host,source),flush=True)
m['record'](host,security_assets_staged=True)
print('SIGNED_REPOSITORY_STAGED',host,flush=True)
