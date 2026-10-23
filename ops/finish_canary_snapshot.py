"""Canary-only snapshot and full stop/start tests; keep all evidence and images."""
import asyncio,datetime,json,runpy,secrets,shlex,time,urllib.request
from pathlib import Path
import asyncssh

ROOT=Path('/var/lib/uccloud-maintenance/20261008')
m=runpy.run_path(str(ROOT/'maintenance.py'));m['quarantine_required']();c=m['admin']()
state=json.loads((ROOT/'canary-vm.json').read_text())
assert state['status']=='GUEST_VERIFIED'
server=c.compute.get_server(state['id'])
assert server.status=='ACTIVE' and server.compute_host=='comsv-01'
port=next(c.network.ports(device_id=server.id));address=port.fixed_ips[0]['ip_address']
with urllib.request.urlopen('http://'+address+':8080/',timeout=10) as response:
    assert response.status==200 and state['marker'].encode() in response.read()
print('CANARY_CONTROLLER_TO_GUEST_HTTP_PASS',flush=True)

async def checksum():
    async with asyncssh.connect(address,username='student',password=state['password'],client_keys=[],
            known_hosts=(address+' '+state['host_key']+'\n').encode(),connect_timeout=10) as ssh:
        r=await ssh.run('sudo sha256sum /var/lib/uccloud-canary/io.bin',check=True,timeout=15)
        return r.stdout.split()[0]
before=asyncio.run(checksum())
record=ROOT/'canary-snapshot-test.json'
assert not record.exists(),'Review existing snapshot evidence before repeating'
image_id=c.compute.create_server_image(server,name='uccloud-security-canary-snapshot-'+secrets.token_hex(4))
if not isinstance(image_id,str):image_id=image_id.id
evidence={'server_id':server.id,'snapshot_image_id':image_id,'file_sha256_before':before,
          'created_at':datetime.datetime.now(datetime.UTC).isoformat()}
record.write_text(json.dumps(evidence,indent=2));record.chmod(0o600)
deadline=time.monotonic()+300
while time.monotonic()<deadline:
    image=c.image.get_image(image_id)
    if image.status=='active':break
    assert image.status not in ('killed','deleted','deactivated'),'Canary image snapshot failed'
    time.sleep(5)
assert image.status=='active' and image.size>0
print('CANARY_GLANCE_SNAPSHOT_ACTIVE_PASS',flush=True)
c.compute.stop_server(server)
deadline=time.monotonic()+180
while time.monotonic()<deadline:
    server=c.compute.get_server(server.id)
    if server.status=='SHUTOFF':break
    time.sleep(3)
assert server.status=='SHUTOFF'
result=m['node']('comsv-01','python3 -',input=(ROOT/'snapshot_stopped_disks.py').read_text(),timeout=600)
evidence['offline_backup']=json.loads(result)
assert evidence['offline_backup']['checks']=='PASS'
record.write_text(json.dumps(evidence,indent=2));record.chmod(0o600)
c.compute.start_server(server);c.compute.wait_for_server(server,wait=240)
deadline=time.monotonic()+180;after=None
while time.monotonic()<deadline:
    try:after=asyncio.run(checksum());break
    except (OSError,asyncssh.Error):time.sleep(5)
assert after==before,'Canary file changed across backup and full stop/start'
evidence['file_sha256_after']=after;evidence['checks']='PASS'
record.write_text(json.dumps(evidence,indent=2));record.chmod(0o600)
print('CANARY_OFFLINE_BACKUP_AND_FULL_STOP_START_PRESERVED_DATA_PASS',flush=True)
runpy.run_path(str(ROOT/'canary_hypervisor_test.py'),run_name='__main__')
report=runpy.run_path(str(ROOT/'verify_hypervisor_gate.py'))['verify']('comsv-01')
assert report['passed']
integration=ROOT/'integration-gate.json'
checks=json.loads(integration.read_text()) if integration.exists() else {}
for key in ('canary_boot','canary_serial','canary_network','canary_file_io','snapshot'):checks[key]='PASS'
integration.write_text(json.dumps(checks,indent=2));integration.chmod(0o600)
m['record']('comsv-01',state='VM_REFRESHED',canary_integration='PASS',cold_migration='DEFERRED_UNTIL_SECOND_HOST_PATCHED')
print('CANARY_ALL_PRE_SECOND_HOST_GATES_PASS',flush=True)
