"""Scoped one-off setup for gimpengtu's VM named test; password only via getpass."""
import asyncio
import getpass
import http.client
import json
import os
import pwd
import shlex
import socket
import ssl
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

for filename in ('/etc/cloud-portal/portal.env', '/etc/cloud-portal/public.env'):
    for line in Path(filename).read_text().splitlines():
        if line.strip() and not line.lstrip().startswith('#') and '=' in line:
            key, value = line.split('=', 1)
            parsed = shlex.split(value)
            os.environ[key] = parsed[0] if parsed else ''
pid = subprocess.check_output(['systemctl', 'show', '-p', 'MainPID', '--value', 'cloud-portal-public'], text=True).strip()
for entry in Path('/proc/' + pid + '/environ').read_bytes().split(b'\0'):
    if b'=' in entry:
        key, value = entry.decode().split('=', 1)
        if key.startswith(('OS_', 'PORTAL_ALLOWED_')) or key in ('PORTAL_DB', 'PORTAL_SHARED_ROUTER', 'PORTAL_DNS_SERVERS', 'PORTAL_BROWSER_SSH_SOURCE'):
            os.environ[key] = value

import asyncssh
from backend.database import db, audit
from backend import openstack_service as cloud
from backend import publication as publication
from backend.terminal import _ssh_target, _known_hosts, _pin_host_key
from backend.site_status import _public_address

PAGE = '''<!doctype html><html lang="ko"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>UCcloud · 연결 테스트</title><style>body{margin:0;background:#f5f7fa;color:#172536;font-family:"Malgun Gothic",sans-serif}main{max-width:600px;margin:18vh auto;padding:40px}small{color:#2467c9;font-weight:700}h1{font-size:38px;letter-spacing:-.05em;margin:20px 0}p{line-height:1.8;color:#536174}.dot{display:inline-block;width:9px;height:9px;background:#27834a;border-radius:50%;margin-right:8px}</style><main><small>UCcloud</small><h1>연결 성공!</h1><p><span class="dot"></span>test 서버의 웹사이트가 열렸습니다.</p><p>이 주소를 공유하면 외부에서도 볼 수 있습니다.</p></main></html>'''
SERVER = '''from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path not in ('/', '/index.html'):
            self.send_error(404); return
        body = Path(__file__).with_name('index.html').read_bytes()
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.end_headers(); self.wfile.write(body)
HTTPServer(('127.0.0.1', PORT), Handler).serve_forever()
'''

def target():
    with db() as conn:
        rows = list(conn.execute("SELECT u.id,u.username,u.status,u.team_id,t.slug AS team_slug,t.openstack_project_id,t.network_id,t.cidr FROM users u JOIN teams t ON t.id=u.team_id WHERE u.username=? COLLATE NOCASE", ('gimpengtu',)))
    if len(rows) != 1 or rows[0]['status'] != 'active':
        raise RuntimeError('Exactly one active gimpengtu account required; no changes')
    user = dict(rows[0])
    connection = cloud.team_connection(user['team_slug'])
    matches = [server for server in connection.compute.servers() if server.name == 'test' and server.project_id == user['openstack_project_id']]
    if len(matches) != 1:
        raise RuntimeError('Exactly one owned VM named test required; no changes')
    server = matches[0]
    address = _ssh_target(user, server.id)
    vm_user = (server.metadata or {}).get('portal_username')
    if not vm_user:
        raise RuntimeError('VM login ID absent; no changes')
    print(json.dumps({'account': user['username'], 'vm': server.name, 'id': server.id, 'state': server.status, 'address': address, 'vm_user': vm_user}, ensure_ascii=False), flush=True)
    return user, server, address, vm_user

def public_body(url):
    host = url.split('://', 1)[1]
    address = _public_address(host)
    with socket.create_connection((address, 443), timeout=6) as raw:
        with ssl.create_default_context().wrap_socket(raw, server_hostname=host) as tls:
            tls.settimeout(6)
            tls.sendall(('GET / HTTP/1.1\r\nHost: ' + host + '\r\nConnection: close\r\n\r\n').encode())
            response = http.client.HTTPResponse(tls); response.begin()
            return response.status, response.read(8192)

async def setup(expected):
    user, server, address, vm_user = target()
    if server.id != expected:
        raise RuntimeError('VM identity changed; no changes')
    password = getpass.getpass('VM password (not echoed): ')
    async with asyncssh.connect(address, username=vm_user, password=password, client_keys=[], known_hosts=_known_hosts(server.id,address), connect_timeout=10, login_timeout=12) as ssh:
        _pin_host_key(server.id, ssh.get_server_host_key().export_public_key().decode().strip())
        publication.credential_path = lambda: Path('/etc/cloud-portal/public-oauth.json')
        async def send(message):
            print(json.dumps(message, ensure_ascii=False), flush=True)
            messages.append(message)
        async def guard(operation):
            if await asyncio.to_thread(_ssh_target, user, server.id) != address:
                raise ValueError('VM ownership or address changed')
        async def record(operation, result):
            with db() as conn:
                conn.execute('INSERT INTO vm_sites(team_id,server_id,url,state,message) VALUES(?,?,?,?,?) ON CONFLICT(team_id,server_id) DO UPDATE SET url=excluded.url,state=excluded.state,http_status=NULL,message=excluded.message,checked_at=CURRENT_TIMESTAMP',
                             (user['team_id'], server.id, result['url'], 'warning', '테스트 웹사이트 공개 완료.'))
                audit(conn, user['id'], 'instance.' + operation, server.id, json.dumps({key: result[key] for key in ('url','port','state') if key in result}))
        messages = []
        publisher = publication.Publisher(ssh, password, server.id, send, guard, record)
        try:
            current = await publisher.read()
            print('Tailscale state: ' + str(current['status'].get('BackendState')), flush=True)
            publication.identity(current['status'])  # Refuses other tailnets before writing files.
            view = publication.public_view(current)
            if view['state'] == 'public':
                raise RuntimeError('Existing Funnel publication; refusing to replace it')
            home = (await ssh.run('printf %s "$HOME"', check=True)).stdout
            folder = home + '/uccloud-web-test'
            unit_folder = home + '/.config/systemd/user'
            unit_path = unit_folder + '/uccloud-web-test.service'
            async with ssh.start_sftp_client() as sftp:
                if await sftp.exists(folder) or await sftp.exists(unit_path):
                    raise RuntimeError('Test paths already exist; refusing to overwrite')
                probe = await ssh.run("python3 -c " + shlex.quote("import socket,json\nfor p in (8080,18082,18083):\n s=socket.socket()\n try: s.bind(('127.0.0.1',p)); print(p); break\n except OSError: pass\n finally: s.close()"), check=True)
                port = int(probe.stdout.strip())
                await sftp.mkdir(folder)
                await sftp.makedirs(unit_folder, exist_ok=True)
                unit = '[Unit]\nDescription=UCcloud test website\n[Service]\nExecStart=/usr/bin/python3 ' + folder + '/server.py\nWorkingDirectory=' + folder + '\nRestart=on-failure\nNoNewPrivileges=true\nPrivateTmp=true\n[Install]\nWantedBy=default.target\n'
                for filename, content in ((folder+'/index.html', PAGE), (folder+'/server.py', SERVER.replace('PORT', str(port))), (unit_path, unit)):
                    async with sftp.open(filename, 'xb') as file:
                        await file.write(content.encode())
            await ssh.run('sudo -S -k -p \'\' -- loginctl enable-linger ' + shlex.quote(vm_user), input=password+'\n', check=True, timeout=15)
            await ssh.run('systemctl --user daemon-reload && systemctl --user enable --now uccloud-web-test.service', check=True, timeout=20)
            for attempt in range(10):
                result = await ssh.run('python3 -c ' + shlex.quote(publication.HTTP_SCRIPT) + ' ' + str(port), timeout=8)
                if json.loads(result.stdout).get('http_status') == 200: break
                await asyncio.sleep(1)
            else: raise RuntimeError('Test web service failed to start')
            service_user = pwd.getpwnam('cloud-portal')
            lock_path = publication.LOCK_DIRECTORY / (server.id + '.lock')
            if not lock_path.exists():
                fd = os.open(lock_path, os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW|os.O_WRONLY, 0o600)
                os.fchown(fd, service_user.pw_uid, service_user.pw_gid); os.close(fd)
            await publisher.submit({'type':'publish.start', 'request_id':str(uuid4()), 'port':port})
            await publisher.task
            if messages[-1]['state'] != 'public': raise RuntimeError('Publication did not complete; test website remains running')
            url = messages[-1]['url']
            for attempt in range(40):
                try:
                    status, body = await asyncio.to_thread(public_body, url)
                    if status == 200 and '연결 성공!'.encode() in body:
                        with db() as conn:
                            conn.execute("UPDATE vm_sites SET state='online',http_status=200,message='접속 가능',checked_at=CURRENT_TIMESTAMP WHERE team_id=? AND server_id=?", (user['team_id'],server.id))
                            audit(conn,user['id'],'instance.web-test.setup',server.id,json.dumps({'port':port,'service':'uccloud-web-test.service','path':folder,'url':url}))
                        print('PUBLIC HTTPS VERIFIED 200: ' + url, flush=True)
                        print('WEB PORT: ' + str(port), flush=True)
                        return
                except (OSError, http.client.HTTPException, subprocess.SubprocessError): pass
                print('Waiting for public DNS/HTTPS (' + str(attempt+1) + '/40)', flush=True)
                await asyncio.sleep(15)
            raise RuntimeError('Public DNS not ready; service and publication remain configured')
        finally:
            await publisher.close()

if __name__ == '__main__':
    if len(sys.argv) == 1: target()
    else: asyncio.run(setup(sys.argv[1]))
