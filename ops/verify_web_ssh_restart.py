"""Approved post-reboot browser-SSH probe with an audited, immediately revoked session."""
import asyncio,datetime,json,runpy,secrets,ssl,sys,time
from pathlib import Path
import websockets

ROOT=Path('/var/lib/uccloud-maintenance/20261008')
m=runpy.run_path(str(ROOT/'maintenance.py'));m['admin']()
from backend.database import db,audit
from backend.security import token_hash
from backend.agent_access import credentials

SERVER='e042b55e-2c4b-4a50-828d-ddbada759dd3'
ORIGIN='https://comsv-03.tail5819db.ts.net'
assert json.loads((ROOT/'controller-after-reboot.json').read_text())['passed']
public=runpy.run_path(str(ROOT/'public_funnel_probe.py'));addresses=public['public_addresses']()
with db() as conn:
    # This is the operator-authorized maintenance check, not an Agent API call.
    # Grant expiry is never extended and its SSH key is never used. Use only the
    # existing encrypted VM password, bound to its still-active original owner.
    row=conn.execute('''SELECT g.* FROM agent_grants g JOIN users u ON u.id=g.user_id
        JOIN teams t ON t.id=u.team_id WHERE g.server_id=? AND g.revoked=0 AND u.status='active'
        AND g.team_id=u.team_id AND g.project_id=t.openstack_project_id
        ORDER BY g.created_at DESC LIMIT 1''',(SERVER,)).fetchone()
assert row,'An existing owner-bound VM credential is required; do not change a user password'
grant=dict(row);password=credentials(grant).get('sudo_password')
server=m['admin']().compute.get_server(SERVER)
assert server.project_id==grant['project_id'] and server.status=='ACTIVE'
assert password,'No existing VM password available'
session=secrets.token_urlsafe(32);csrf=secrets.token_urlsafe(24);hashed=token_hash(session)
expires=(datetime.datetime.now(datetime.UTC)+datetime.timedelta(minutes=5)).strftime('%Y-%m-%d %H:%M:%S')
headers={'Cookie':'school_cloud_session='+session}
protocols=['uccloud','csrf.'+csrf]
url=ORIGIN.replace('https:','wss:')+'/api/instances/'+SERVER+'/terminal'
report={'phase':'after-controller-reboot','public_funnel_route':True,'agent_grant_not_renewed':True,'connected':False,'command_round_trip':False,'temporary_session_revoked':False}

async def probe():
    async with websockets.connect(url,origin=ORIGIN,subprotocols=protocols,additional_headers=headers,
            host=addresses[0],port=443,server_hostname=public['HOST'],ssl=ssl.create_default_context(),open_timeout=20,close_timeout=5) as ws:
        await ws.send(json.dumps({'username':grant['vm_username'],'password':password,'cols':80,'rows':24}))
        while True:
            item=await ws.recv()
            if isinstance(item,str):
                status=json.loads(item)
                if status.get('type')=='error':raise RuntimeError('Browser SSH returned an error; no credentials or raw VM output logged')
                if status.get('type')=='ready':report['connected']=True;break
        # Split the marker so terminal input echo cannot falsely satisfy the test.
        marker='UCCLOUD_WS_'+secrets.token_hex(12)
        command="printf '%s%s\\n' '"+marker[:11]+"' '"+marker[11:]+"'\r"
        await ws.send(json.dumps({'type':'input','data':command}))
        output=b''
        while True:
            item=await ws.recv()
            if isinstance(item,bytes):
                output=(output+item)[-65536:]
                if marker.encode() in output:report['command_round_trip']=True;return

try:
    with db() as conn:
        conn.execute('INSERT INTO sessions(token_hash,user_id,csrf_token,expires_at) VALUES(?,?,?,?)',(hashed,grant['user_id'],csrf,expires))
        audit(conn,grant['user_id'],'maintenance.session.issue',SERVER,'Approved single post-reboot SSH connectivity check; expires in five minutes')
    asyncio.run(asyncio.wait_for(probe(),timeout=100))
finally:
    with db() as conn:
        conn.execute('DELETE FROM sessions WHERE token_hash=?',(hashed,))
        report['temporary_session_revoked']=conn.execute('SELECT 1 FROM sessions WHERE token_hash=?',(hashed,)).fetchone() is None
        audit(conn,grant['user_id'],'maintenance.session.revoke',SERVER,'Temporary connectivity session revoked')
    password=None;session=None
    report['passed']=all(report[k] for k in ('connected','command_round_trip','temporary_session_revoked'))
    path=ROOT/'web-ssh-after-reboot.json';path.write_text(json.dumps(report,indent=2));path.chmod(0o600)
    print('POST_REBOOT_BROWSER_SSH','PASS' if report['passed'] else 'FAIL','TEMPORARY_SESSION_REVOKED',report['temporary_session_revoked'])
if report['passed']:
    path=ROOT/'integration-gate.json';gate=json.loads(path.read_text());gate['web_ssh']='PASS';path.write_text(json.dumps(gate,indent=2));path.chmod(0o600)
