"""Prepare approved one-time guest probes on a drained, backed-up compute only."""
import base64,json,runpy,sys
from pathlib import Path
ROOT=Path('/var/lib/uccloud-maintenance/20261008')
m=runpy.run_path(str(ROOT/'maintenance.py'));m['quarantine_required']()
host=sys.argv[1];assert host in m['HOSTS'] and host not in ('comsv-01','comsv-04')
state=json.loads((ROOT/'state.json').read_text())['hosts'][host]
assert state['state']=='QEMU_READY' and state['offline_disks_backed_up']
servers=json.loads((ROOT/(host+'-instances-before.json')).read_text())
payload={n:base64.b64encode((ROOT/n).read_bytes()).decode() for n in ('prepare_guest_inspection.py','offline_guest_inspection_key.py','guest-inspection-key.pub')}
source="""import base64,json,subprocess
from pathlib import Path
assert not subprocess.check_output(['virsh','list','--name'],text=True).strip()
assert subprocess.check_output(['systemctl','show','nova-compute','-p','MainPID','--value'],text=True).strip()=='0'
p=Path('/var/backups/uccloud-hypervisor/20261008/inspection/helpers');p.mkdir(parents=True,mode=0o700,exist_ok=True);p.chmod(0o700)
result=subprocess.run(['apt-get','install','-y','guestfs-tools','python3-guestfs'],capture_output=True,text=True,timeout=600,env=dict(__import__('os').environ,DEBIAN_FRONTEND='noninteractive'))
log=p/'guestfs-tools-install.log';log.write_text(result.stdout+'\\n'+result.stderr);log.chmod(0o600)
assert result.returncode==0,'Inspection tool installation failed; keep the compute stopped'
assert subprocess.check_output(['systemctl','show','nova-compute','-p','MainPID','--value'],text=True).strip()=='0'
assert subprocess.check_output(['dpkg-query','-W','-f=${Version}','qemu-system-x86'],text=True).strip()=='1:10.2.1+ds-1ubuntu3.2+uccloud1'
payload=json.loads(REPLACE_PAYLOAD)
for n,v in payload.items():
 f=p/n;assert not f.exists();f.write_bytes(base64.b64decode(v));f.chmod(0o600)
print('ISOLATED_INSPECTION_TOOLS_READY_QEMU_APPROVAL_PRESERVED')
""".replace('REPLACE_PAYLOAD',repr(json.dumps(payload)))
print(m['python_node'](host,source,timeout=660),flush=True)
for server in servers:
    uuid=server['id'];assert len(uuid)==36 and all(x in '0123456789abcdef-' for x in uuid)
    source="""import subprocess,json
from pathlib import Path
p=Path('/var/backups/uccloud-hypervisor/20261008/inspection/helpers')
uuid=REPLACE_UUID
result=subprocess.run(['python3',str(p/'prepare_guest_inspection.py'),uuid,str(p/'guest-inspection-key.pub'),str(p/'offline_guest_inspection_key.py')],capture_output=True,text=True,timeout=750)
log=p/(uuid+'-preparation.log');log.write_text(result.stdout+'\\n'+result.stderr);log.chmod(0o600)
assert result.returncode==0,'Guest inspection preparation failed; retain quarantine and offline backup'
r=json.loads((Path('/var/lib/uccloud-guest-inspection')/uuid/'result.json').read_text())
assert r['key_injected'] and r['password_data_unchanged']
print('APPROVED_GUEST_KEY_PREPARED_WITH_PASSWORD_UNCHANGED')
""".replace('REPLACE_UUID',repr(uuid))
    print(m['python_node'](host,source,timeout=800),flush=True)
m['record'](host,guest_inspection_keys_prepared=True)
print(host,'ALL_APPROVED_ONE_TIME_GUEST_KEYS_PREPARED',len(servers),flush=True)
