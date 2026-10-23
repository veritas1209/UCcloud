"""Sequential single-host drain, private backups and signed security preparation."""
import json,runpy,subprocess,sys,time
from pathlib import Path
ROOT=Path('/var/lib/uccloud-maintenance/20261008')
m=runpy.run_path(str(ROOT/'maintenance.py'));m['quarantine_required']();c=m['admin']()
host=sys.argv[1];assert host in m['HOSTS'] and host!='comsv-01'
order=['comsv-01','comsv-04','comsv-05','user-ProLiant-DL20-Gen11']
previous=order[order.index(host)-1]
state=json.loads((ROOT/'state.json').read_text())['hosts']
assert state[previous]['state'] in ('VM_REFRESHED','COMPLIANT'),'Previous host has not passed its VM tests'
assert state[host].get('security_assets_staged')
servers=[s for s in c.compute.servers(all_projects=True,details=True) if s.compute_host==host]
assert all(s.status in ('ACTIVE','SHUTOFF') and s.task_state is None for s in servers),'Review busy instances before drain'
p=ROOT/(host+'-instances-before.json');assert not p.exists(),'Drain evidence exists; review before resuming'
records=[]
for s in servers:
    image=s.image or {};flavor=s.flavor or {}
    image_id=getattr(image,'id',None) or image.get('id')
    flavor_record={k:flavor.get(k) for k in ('id','original_name','vcpus','ram','disk')}
    records.append({'id':s.id,'name':s.name,'status':s.status,'image':{'id':image_id},'flavor':flavor_record,
        'project_id':s.project_id,'metadata':s.metadata,'addresses':s.addresses})
p.write_text(json.dumps(records,indent=2));p.chmod(0o600)
for s in servers:
    if s.status=='ACTIVE':c.compute.stop_server(s)
deadline=time.monotonic()+240
while time.monotonic()<deadline:
    if all(c.compute.get_server(s.id).status=='SHUTOFF' for s in servers):break
    time.sleep(3)
assert all(c.compute.get_server(s.id).status=='SHUTOFF' for s in servers)
print('ORIGINAL_USER_VMS_STOPPED_WITHOUT_DELETION',host,len(servers),flush=True)
print(m['node'](host,'systemctl stop nova-compute',timeout=240),flush=True)
activation="""import json,subprocess
from pathlib import Path
assert not subprocess.check_output(['virsh','list','--name'],text=True).strip()
stage=Path('/var/backups/uccloud-hypervisor/20261008/staged-security')
for name,item in json.loads((stage/'assets.json').read_text()).items():
    p=Path(item['dest']);p.parent.mkdir(parents=True,exist_ok=True)
    if p.exists():
        old=stage/(name+'.previous')
        assert not old.exists(),'Existing deployment requires review'
        old.write_bytes(p.read_bytes());old.chmod(0o600)
    p.write_bytes((stage/name).read_bytes());p.chmod(item['mode'])
print('SECURITY_ASSETS_ACTIVATED_ON_DRAINED_COMPUTE')
"""
print(m['python_node'](host,activation),flush=True)
read="import configparser\nc=configparser.ConfigParser(strict=False,interpolation=None);c.read('/etc/nova/nova.conf');print(c.get('serial_console','base_url'))"
base=subprocess.check_output(['lxc-attach','-n','controller1-nova-api-container-cb1ec29d','--','python3','-c',read],text=True).strip()
import shlex
command='python3 /usr/local/sbin/uccloud-prepare-compute-security 21383b152eeb0cc12afbcee3c1af9babb21c2c4701c753006286f4eb61d90a46 '+shlex.quote(base)
result=m['node'](host,command,timeout=900)
log=ROOT/(host+'-signed-preparation.log');log.write_text(result);log.chmod(0o600)
print('SIGNED_QEMU_AND_PERSISTENT_KERNEL_NOVA_PREPARED',host,flush=True)
result=m['node'](host,'python3 -',input=(ROOT/'snapshot_stopped_disks.py').read_text(),timeout=900)
snapshots=json.loads(result);assert snapshots['checks']=='PASS'
p=ROOT/(host+'-offline-snapshots.json');p.write_text(json.dumps(snapshots,indent=2));p.chmod(0o600)
m['record'](host,state='QEMU_READY',offline_disks_backed_up=True,original_active_vm_ids=[s.id for s in servers if s.status=='ACTIVE'])
print('OFFLINE_USER_DISKS_INDEPENDENT_BACKUPS_PASS',host,snapshots['offline_snapshots'],flush=True)
# Keep Nova stopped until temporary guest inspection setup is reviewed.
