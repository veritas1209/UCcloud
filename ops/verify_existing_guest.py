"""Run the approved self-removing guest probe; never change guest passwords."""
import asyncio,datetime,ipaddress,json,runpy,sys
from pathlib import Path
import asyncssh

ROOT=Path('/var/lib/uccloud-maintenance/20261008')
m=runpy.run_path(str(ROOT/'maintenance.py'));m['quarantine_required']();c=m['admin']()
host=sys.argv[1];server_id=sys.argv[2]
assert host in m['HOSTS']
server=c.compute.get_server(server_id)
assert server.status=='ACTIVE' and server.compute_host==host
remote='/var/lib/uccloud-guest-inspection/'+server_id+'/result.json'
inspection=json.loads(m['node'](host,'cat '+remote))
assert inspection['uuid']==server_id and inspection['password_data_unchanged']
addresses=[a['addr'] for values in server.addresses.values() for a in values if a.get('OS-EXT-IPS:type')=='fixed' and ipaddress.ip_address(a['addr']).version==4]
assert len(addresses)==1
address=addresses[0]
known=''.join(address+' '+key+'\n' for key in inspection['host_keys']).encode()
key=ROOT/'guest-inspection-key'
username=inspection.get('ssh_username','root')

async def test():
    deadline=asyncio.get_running_loop().time()+150
    while True:
        try:
            ssh=await asyncssh.connect(address,username=username,client_keys=[str(key)],known_hosts=known,connect_timeout=10,login_timeout=10)
            break
        except (OSError,asyncssh.ConnectionLost):
            if asyncio.get_running_loop().time()>=deadline:raise RuntimeError('Guest SSH not available; retain quarantine') from None
            await asyncio.sleep(5)
    async with ssh:
        # The authorized-key forced command restricts this key to the approved probe.
        result=await ssh.run('uccloud-approved-inspection',check=False,timeout=90)
        report=json.loads(result.stdout.strip())
        assert result.exit_status==0 and all(value=='PASS' for value in report.values()),'Guest checks failed; retain quarantine'
    try:
        async with await asyncssh.connect(address,username=username,client_keys=[str(key)],known_hosts=known,connect_timeout=10,login_timeout=10):
            raise RuntimeError('Temporary guest inspection key remains valid')
    except asyncssh.PermissionDenied:
        report['inspection_key_removed']='PASS'
    report['host']=host;report['time']=datetime.datetime.now(datetime.UTC).isoformat()
    return report

report=asyncio.run(test())
path=ROOT/'guest-gate.json';gate=json.loads(path.read_text()) if path.exists() else {}
assert server_id not in gate,'Preserve previous guest evidence rather than overwrite it'
gate[server_id]=report;path.write_text(json.dumps(gate,indent=2));path.chmod(0o600)
local=ROOT/(server_id+'-inspection.json');local.write_text(json.dumps(inspection,indent=2));local.chmod(0o600)
print(host,'EXISTING_GUEST_CHECKS_PASS_PASSWORD_UNCHANGED_TEMPORARY_KEY_REMOVED')
