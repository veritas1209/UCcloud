"""Disposable OpenStack VM/HTTPS check. Run as root on the controller.

Only the VM created by this run is deleted, after checking its identity marker.
The verification node uses an ephemeral auth key so it is automatically removed.
No student VM, management tailnet or existing public connection is changed.
"""
import asyncio
import http.client
import json
import os
import secrets
import shlex
import socket
import ssl
import subprocess
import time
from pathlib import Path
from uuid import uuid4

for file in ('/etc/cloud-portal/portal.env','/etc/cloud-portal/public.env'):
    for line in Path(file).read_text().splitlines():
        if line.strip() and not line.lstrip().startswith('#') and '=' in line:
            name, value = line.split('=', 1)
            parsed = shlex.split(value)
            os.environ[name] = parsed[0] if parsed else ''

# Match the running public portal's service overrides without displaying values.
pid = subprocess.check_output(['systemctl','show','-p','MainPID','--value','cloud-portal-public'],text=True).strip()
for entry in Path('/proc/'+pid+'/environ').read_bytes().split(b'\0'):
    if b'=' in entry:
        name,value = entry.decode().split('=',1)
        if name.startswith(('OS_','PORTAL_ALLOWED_')) or name in ('PORTAL_DB','PORTAL_SHARED_ROUTER','PORTAL_DNS_SERVERS','PORTAL_BROWSER_SSH_SOURCE'):
            os.environ[name]=value

import asyncssh
from backend.database import db
from backend import openstack_service as cloud
from backend import publication as p
from backend.site_status import _public_address
from backend.security import hash_password

MARKER = str(uuid4())
NAME = 'uccloud-public-verify-' + MARKER[:8]
USER = 'ucverify'
PASSWORD = secrets.token_urlsafe(24)
STATE = Path('/var/lib/cloud-portal/publication-verification.json')
PORTAL_USER = 'guide-' + MARKER[:8]
PORTAL_PASSWORD = secrets.token_urlsafe(24)

def public_body(url):
    host = url.split('://', 1)[1]
    address = _public_address(host)
    with socket.create_connection((address,443), timeout=6) as raw:
        with ssl.create_default_context().wrap_socket(raw,server_hostname=host) as tls:
            tls.settimeout(6)
            tls.sendall(('GET / HTTP/1.1\r\nHost: '+host+'\r\nConnection: close\r\n\r\n').encode())
            response = http.client.HTTPResponse(tls); response.begin()
            return response.status, response.read(4096)

async def verify():
    p.LOCK_DIRECTORY = Path('/run/uccloud-verification-locks')
    p.credential_path = lambda:Path('/etc/cloud-portal/public-oauth.json')
    original_request = p._json_request
    def ephemeral_request(url, data=None, headers=None):
        if url.endswith('/tailnet/-/keys') and data:
            payload = json.loads(data)
            payload['capabilities']['devices']['create']['ephemeral'] = True
            data = json.dumps(payload).encode()
        return original_request(url,data,headers)
    p._json_request = ephemeral_request
    with db() as database:
        teams = [dict(row) for row in database.execute("SELECT t.id,t.slug,t.openstack_project_id,t.network_id FROM teams t WHERE network_id IS NOT NULL AND EXISTS(SELECT 1 FROM users u WHERE u.team_id=t.id AND u.status='active') ORDER BY t.id DESC")]
    team, conn, image, flavor = None, None, None, None
    for item in teams:
        print('Checking spare quota in managed project '+str(item['id']),flush=True)
        try:
            candidate = cloud.team_connection(item['slug'])
        except Exception:
            print('Project authentication unavailable; checking next managed project.',flush=True)
            continue
        print('Team authentication ready.',flush=True)
        images = [value for value in candidate.image.images(status='active',visibility='public') if value.name=='ubuntu-24.04' and value.status=='active']
        flavors = sorted([value for value in candidate.compute.flavors() if value.name in cloud.ALLOWED_FLAVORS],key=lambda value:(value.ram,value.vcpus))
        if not images or not flavors: continue
        quota = cloud.quota(item['openstack_project_id'])
        needed = {'instances':1,'cores':flavors[0].vcpus,'ram':flavors[0].ram}
        if all(quota[key]['limit'] < 0 or quota[key]['limit']-quota[key]['in_use']>=amount for key,amount in needed.items()):
            team, conn, image, flavor = item,candidate,images[0],flavors[0]
            break
    if not team: raise RuntimeError('No spare VM quota; no resources changed')
    if STATE.exists(): raise RuntimeError('Earlier verification state exists; no VM was created')
    result = cloud.create_server(team['slug'],team['openstack_project_id'],'verification',team['network_id'],
                                 NAME,image.id,flavor.id,USER,PASSWORD,request_id=MARKER)
    vm_id = result['id']
    with db() as database:
        portal_id = database.execute(
            "INSERT INTO users(username,email,password_hash,role,status,team_id) VALUES(?,?,?,'member','active',?)",
            (PORTAL_USER, PORTAL_USER+'@example.invalid', hash_password(PORTAL_PASSWORD), team['id'])).lastrowid
    with os.fdopen(os.open(STATE,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600),'w') as output:
        json.dump({'id':vm_id,'slug':team['slug'],'name':NAME,'marker':MARKER,
                   'vm_user':USER,'vm_password':PASSWORD,'username':PORTAL_USER,'password':PORTAL_PASSWORD},output)
    print('Verification VM created: '+vm_id,flush=True)
    ssh = None
    try:
        server = await asyncio.to_thread(conn.compute.wait_for_server,conn.compute.get_server(vm_id),wait=180)
        deadline = time.monotonic()+240
        while time.monotonic()<deadline:
            ports = list(conn.network.ports(device_id=vm_id,network_id=team['network_id']))
            addresses = [entry['ip_address'] for port in ports for entry in port.fixed_ips if ':' not in entry['ip_address']]
            if len(addresses)==1:
                try:
                    # New random-password verification VM; pin its first host key below.
                    ssh = await asyncssh.connect(addresses[0],username=USER,password=PASSWORD,
                        client_keys=[],known_hosts=None,connect_timeout=8,login_timeout=10)
                    break
                except (OSError,asyncssh.Error): pass
            await asyncio.sleep(8)
        if not ssh: raise RuntimeError('Verification VM SSH did not become ready')
        host_key = ssh.get_server_host_key()
        ssh.close(); await ssh.wait_closed()
        known = (addresses[0]+' '+host_key.export_public_key().decode()).encode()
        ssh = await asyncssh.connect(addresses[0],username=USER,password=PASSWORD,client_keys=[],known_hosts=known)
        body = 'UCcloud publication verification ' + MARKER
        server_code = "from http.server import BaseHTTPRequestHandler,HTTPServer\nclass Handler(BaseHTTPRequestHandler):\n def do_GET(self):\n  body="+repr(body.encode())+"\n  self.send_response(200); self.end_headers(); self.wfile.write(body)\n def log_message(self,*args): pass\nHTTPServer(('127.0.0.1',18081),Handler).serve_forever()"
        web = await ssh.create_process('python3 -u -c '+shlex.quote(server_code))
        await asyncio.sleep(1)
        deadline = time.monotonic()+240
        while time.monotonic()<deadline:
            check = await ssh.run('command -v tailscale',check=False,timeout=5)
            if check.exit_status==0: break
            await asyncio.sleep(8)
        messages = []
        async def send(message):
            messages.append(message)
            print('Verification: '+message.get('state','')+' '+message.get('message',''),flush=True)
        async def guard(operation):
            owned = cloud._owned_server(conn,vm_id,team['openstack_project_id'])
            if owned.name!=NAME or owned.metadata.get('portal_request_id')!=MARKER:
                raise RuntimeError('Verification ownership changed')
        async def record(operation,final): pass
        publisher = p.Publisher(ssh,PASSWORD,vm_id,send,guard,record)
        await publisher.submit({'type':'publish.start','request_id':str(uuid4()),'port':18081})
        await publisher.task
        if messages[-1]['state']!='public': raise RuntimeError('Verification publication failed')
        url = messages[-1]['url']
        deadline = time.monotonic()+600
        while time.monotonic()<deadline:
            try:
                status, content = await asyncio.to_thread(public_body,url)
                if status==200 and content.decode()==body:
                    print('Verified public HTTPS 200 with exact verification response: '+url,flush=True)
                    break
            except Exception: pass
            await asyncio.sleep(15)
        else: raise RuntimeError('Public HTTPS verification timed out')
        print('Ready for live PC screenshots.',flush=True)
        deadline = time.monotonic()+600
        while time.monotonic()<deadline and not Path('/run/uccloud-verification-screenshots-done').exists():
            await asyncio.sleep(5)
        await publisher.submit({'type':'publish.stop','request_id':str(uuid4())})
        await publisher.task
        assert messages[-1]['state']=='private'
        print('Public stop verified; management tailnet untouched.',flush=True)
        await publisher.close()
        web.terminate()
    finally:
        if ssh:
            ssh.close(); await ssh.wait_closed()
        owned = cloud._owned_server(conn,vm_id,team['openstack_project_id'])
        if owned.name==NAME and owned.metadata.get('portal_request_id')==MARKER:
            conn.compute.delete_server(vm_id)
            await asyncio.to_thread(conn.compute.wait_for_delete,owned,wait=120)
            with db() as database:
                database.execute('UPDATE audit_log SET user_id=NULL WHERE user_id=?',(portal_id,))
                database.execute('DELETE FROM users WHERE id=? AND username=?',(portal_id,PORTAL_USER))
                database.execute('DELETE FROM vm_sites WHERE team_id=? AND server_id=?',(team['id'],vm_id))
            STATE.unlink()
            print('Only this run\'s verification VM was deleted.',flush=True)
        else: raise RuntimeError('VM identity differs; cleanup refused')

if __name__=='__main__':
    try: asyncio.run(verify())
    except Exception as exc:
        # No traceback of provider responses or credentials.
        safe = str(exc)[:500]
        for key,value in os.environ.items():
            if value and any(part in key.upper() for part in ('PASSWORD','SECRET','TOKEN','KEY')):
                safe = safe.replace(value,'[redacted]')
        safe = __import__('re').sub(r'tskey-[A-Za-z0-9-]+','[redacted]',safe)
        print('Verification did not complete: '+type(exc).__name__+' HTTP '+str(getattr(exc,'status_code','n/a'))+' '+safe,flush=True)
        raise SystemExit(1)
