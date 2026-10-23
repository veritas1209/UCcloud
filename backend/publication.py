"""UCcloud-managed Funnel over an owned VM's authenticated SSH connection.

The OAuth credential never enters a VM, browser, command argument or audit log.
Only a short-lived single-use tagged auth key is passed to the guest on stdin.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import shlex
import time
import urllib.parse
import urllib.request
from pathlib import Path
from uuid import UUID

TAG = 'tag:uccloud-public'
DOMAIN = 'tail2d25b3.ts.net'
API = 'https://api.tailscale.com/api/v2'
POLICY = {'tagOwners':{TAG:['autogroup:admin']}, 'acls':[], 'grants':[], 'ssh':[],
          'nodeAttrs':[{'target':[TAG], 'attr':['funnel']}]}
LOCK_DIRECTORY = Path('/var/lib/cloud-portal/publication-locks')


def credential_path():
    return Path(os.getenv('CREDENTIALS_DIRECTORY', '/run/credentials/unavailable')) / 'uccloud-public-oauth'


def configured():
    return credential_path().is_file()


def validate_policy(policy):
    # Fail closed if the public account is changed to allow lateral connectivity.
    if policy != POLICY:
        raise ValueError('공개용 네트워크의 보호 설정을 확인해야 합니다. 운영자에게 문의해 주세요.')


def _json_request(url, data=None, headers=None):
    request = urllib.request.Request(url, data=data, headers=headers or {})
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            value = response.read(1024 * 1024 + 1)
        if len(value) > 1024 * 1024:
            raise ValueError()
        return json.loads(value)
    except Exception:
        # Provider error bodies may contain credentials. Never expose them.
        raise ValueError('공개 연결 서비스에 응답이 없습니다. 잠시 후 다시 시도해 주세요.') from None


def check_policy():
    try:
        credential = json.loads(credential_path().read_text())
    except Exception:
        raise ValueError('자동 공개를 준비 중입니다. 운영자에게 문의해 주세요.') from None
    token = _json_request(API + '/oauth/token', urllib.parse.urlencode(credential).encode(),
                          {'Content-Type':'application/x-www-form-urlencoded'})
    access = token['access_token']
    headers = {'Authorization':'Bearer ' + access, 'Accept':'application/json'}
    validate_policy(_json_request(API + '/tailnet/-/acl', headers=headers))
    return access


def mint_key():
    access = check_policy()
    headers = {'Authorization':'Bearer ' + access, 'Accept':'application/json'}
    payload = {'expirySeconds':600, 'description':'UCcloud single-use public VM connection',
               'capabilities':{'devices':{'create':{'reusable':False, 'ephemeral':False,
                                                   'preauthorized':True, 'tags':[TAG]}}}}
    response = _json_request(API + '/tailnet/-/keys', json.dumps(payload).encode(),
                             dict(headers, **{'Content-Type':'application/json'}))
    key, key_id = response.get('key'), response.get('id')
    if not isinstance(key, str) or not re.fullmatch(r'tskey-auth-[A-Za-z0-9-]+', key):
        raise ValueError('공개 연결 키를 만들지 못했습니다. 운영자에게 문의해 주세요.')
    if not isinstance(key_id, str) or not re.fullmatch(r'[A-Za-z0-9]+', key_id):
        raise ValueError('공개 연결 키를 확인하지 못했습니다. 운영자에게 문의해 주세요.')
    return key, key_id, access


def revoke_key(key_id, access):
    try:
        request = urllib.request.Request(API + '/tailnet/-/keys/' + key_id, method='DELETE',
                                          headers={'Authorization':'Bearer ' + access})
        with urllib.request.urlopen(request, timeout=10):
            pass
    except Exception:
        pass  # Used single-use keys disappear; unused keys expire after ten minutes.


READ_SCRIPT = r'''
import json, subprocess
def read(args):
    r = subprocess.run(['tailscale'] + args, capture_output=True, text=True, timeout=8)
    return json.loads(r.stdout or '{}')
try:
    status = read(['status', '--json'])
    brief = {key:status.get(key) for key in ('BackendState','CurrentTailnet','Self')}
    print(json.dumps({'status':brief, 'funnel':read(['funnel','status','--json'])}))
except Exception:
    print('{}')
'''

HTTP_SCRIPT = r'''
import http.client, json, sys
try:
    port = int(sys.argv[1])
    c = http.client.HTTPConnection('127.0.0.1', port, timeout=4)
    c.request('GET', '/', headers={'Host':'localhost', 'Connection':'close'})
    r = c.getresponse()
    print(json.dumps({'http_status':r.status}))
    c.close()
except Exception:
    print('{}')
'''

GUEST_SCRIPT = r'''
import json, os, subprocess, sys, tempfile
def run(args, timeout=75):
    r = subprocess.run(['tailscale'] + args, capture_output=True, text=True, timeout=timeout)
    if r.returncode:
        raise RuntimeError('tailscale operation failed')
    return r.stdout
def status():
    r = subprocess.run(['tailscale','status','--json'], capture_output=True, text=True, timeout=8)
    return json.loads(r.stdout or '{}')
def managed(s):
    return ((s.get('CurrentTailnet') or {}).get('MagicDNSSuffix') == 'tail2d25b3.ts.net'
            and 'tag:uccloud-public' in (s.get('Self') or {}).get('Tags', []))
try:
    payload = None
    # sudo -S consumes the first password line; NOPASSWD may leave it on stdin.
    for _ in range(2):
        try:
            candidate = json.loads(sys.stdin.readline(8192))
            if isinstance(candidate, dict) and candidate.get('protocol') == 1:
                payload = candidate
                break
        except Exception:
            pass
    if not payload or payload.get('op') not in ('start', 'stop'):
        raise RuntimeError('invalid payload')
    s = status()
    if s.get('CurrentTailnet') and not managed(s):
        print(json.dumps({'error':'existing-account'}))
        sys.exit(0)
    if payload['op'] == 'start':
        port = payload.get('port')
        if type(port) is not int or not 1 <= port <= 65535 or port == 22:
            raise RuntimeError('invalid port')
        if not managed(s) or s.get('BackendState') not in ('Running', 'Stopped'):
            key = payload.get('key', '')
            hostname = payload.get('hostname', '')
            import re
            if not re.fullmatch(r'tskey-auth-[A-Za-z0-9-]+', key) or not re.fullmatch(r'uccloud-[0-9a-f-]{36}', hostname):
                raise RuntimeError('invalid connection')
            fd, name = tempfile.mkstemp(prefix='uccloud-public-', dir='/run')
            try:
                with os.fdopen(fd, 'w') as f:
                    f.write(key)
                run(['up','--reset','--auth-key=file:' + name,'--advertise-tags=tag:uccloud-public',
                     '--hostname=' + hostname,'--accept-dns=false','--accept-routes=false',
                     '--ssh=false','--timeout=60s'])
            finally:
                os.unlink(name)
        if not managed(status()):
            raise RuntimeError('unexpected public network')
        if status().get('BackendState') == 'Stopped':
            run(['up','--reset','--advertise-tags=tag:uccloud-public',
                 '--accept-dns=false','--accept-routes=false','--ssh=false','--timeout=60s'])
        run(['funnel','--bg','--yes','--https=443','http://127.0.0.1:' + str(port)])
    else:
        if not managed(s):
            raise RuntimeError('not managed')
        run(['funnel','--bg','--yes','--https=443','off'])
    print(json.dumps({'ok':True}))
except Exception:
    # Do not forward tailscale output, auth URLs, passwords or auth keys.
    print(json.dumps({'error':'operation-failed'}))
'''


def identity(status):
    status = status or {}
    tailnet = status.get('CurrentTailnet') or {}
    node = status.get('Self') or {}
    if tailnet and (tailnet.get('MagicDNSSuffix') != DOMAIN or TAG not in (node.get('Tags') or [])):
        raise ValueError('이 VM은 다른 Tailscale 계정에 연결돼 있습니다. 기존 연결을 유지했습니다. 운영자에게 문의해 주세요.')
    host = (node.get('DNSName') or '').rstrip('.').lower()
    if tailnet and not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,62}\.' + re.escape(DOMAIN), host):
        raise ValueError('공개용 서버 주소를 확인하지 못했습니다.')
    return host if tailnet else None


def public_view(snapshot):
    status = snapshot.get('status') or {}
    host = identity(status)
    if not host:
        return {'state':'private', 'message':'웹사이트를 켠 뒤 사용하는 포트 번호를 입력하세요.'}
    config = snapshot.get('funnel') or {}
    endpoint = host + ':443'
    proxy = config.get('Web', {}).get(endpoint, {}).get('Handlers', {}).get('/', {}).get('Proxy', '')
    match = re.fullmatch(r'http://127\.0\.0\.1:([0-9]{1,5})/?', proxy)
    if config.get('AllowFunnel', {}).get(endpoint) is True and match:
        return {'state':'public', 'url':'https://' + host, 'port':int(match[1]),
                'message':'공개 중입니다.'}
    return {'state':'private', 'message':'공개가 꺼져 있습니다.'}


class Publisher:
    def __init__(self, connection, password, server_id, send, guard, record):
        self.connection, self.password = connection, password
        self.server_id = str(UUID(server_id))
        self.send, self.guard, self.record = send, guard, record
        self.task = None

    async def read(self):
        result = await self.connection.run('python3 -c ' + shlex.quote(READ_SCRIPT), check=False, timeout=20)
        try:
            value = json.loads(result.stdout)
            if 'status' not in value:
                raise ValueError()
            return value
        except Exception:
            raise ValueError('공개 도구를 준비 중입니다. 몇 분 뒤 다시 시도해 주세요.') from None

    async def submit(self, message):
        if self.task and not self.task.done():
            await self.send({'type':'publish', 'state':'busy', 'message':'이미 진행 중입니다. 잠시 기다려 주세요.'})
            return
        self.task = asyncio.create_task(self._operate(message))
        self.task.add_done_callback(lambda task: task.exception() if not task.cancelled() else None)

    async def _operate(self, message):
        started = time.monotonic()
        key_info = None
        lock = None
        request_id = message['request_id']
        async def feedback(state, text, **fields):
            await self.send(dict(type='publish', request_id=request_id, state=state, message=text,
                                 elapsed_seconds=int(time.monotonic()-started), **fields))
        try:
            operation = message.get('type')
            if operation not in ('publish.status','publish.start','publish.stop'):
                raise ValueError('공개 요청을 확인해 주세요.')
            if not configured():
                raise ValueError('자동 공개를 준비 중입니다. 운영자에게 문의해 주세요.')
            await self.guard(operation)
            if operation != 'publish.status':
                import fcntl
                folder = LOCK_DIRECTORY
                folder.mkdir(mode=0o700, exist_ok=True)
                lock = os.open(folder / (self.server_id + '.lock'), os.O_CREAT | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    raise ValueError('이 VM의 공개 설정이 이미 진행 중입니다. 잠시 기다려 주세요.') from None
            await feedback('working', '공개 상태를 확인하고 있습니다.')
            snapshot = await self.read()
            current = public_view(snapshot)
            if operation == 'publish.status':
                text = current.pop('message')
                await feedback(text=text, **current)
                return
            payload = {'protocol':1, 'op':operation.split('.')[1]}
            if operation == 'publish.start':
                port = message.get('port')
                if type(port) is not int or not 1 <= port <= 65535 or port == 22:
                    raise ValueError('웹사이트에서 사용하는 포트 번호를 입력해 주세요. (1~65535, SSH 제외)')
                await feedback('working', '서버에서 웹사이트가 켜져 있는지 확인하고 있습니다.')
                probe = await self.connection.run('python3 -c ' + shlex.quote(HTTP_SCRIPT) + ' ' + str(port), timeout=8)
                if not 100 <= json.loads(probe.stdout).get('http_status', 0) <= 599:
                    raise ValueError(f'{port}번 포트에서 웹사이트가 응답하지 않습니다. 웹사이트를 먼저 켜고 다시 눌러 주세요.')
                payload.update(port=port, hostname='uccloud-' + self.server_id)
                if not identity(snapshot['status']) or snapshot['status'].get('BackendState') not in ('Running','Stopped'):
                    await feedback('working', '서버를 공개용 네트워크에 연결하고 있습니다. 처음에는 1~2분 걸릴 수 있습니다.')
                    key_info = await asyncio.to_thread(mint_key)
                    payload['key'] = key_info[0]
                else:
                    await asyncio.to_thread(check_policy)
            await self.guard('publish.validate')
            await feedback('working', 'HTTPS 주소를 설정하고 있습니다.' if operation == 'publish.start' else '공개를 끄고 있습니다.')
            result = await self.connection.run('sudo -S -k -p \'\' -- python3 -c ' + shlex.quote(GUEST_SCRIPT),
                                               input=self.password + '\n' + json.dumps(payload) + '\n',
                                               check=False, timeout=145)
            payload.clear()
            try:
                reply = json.loads(result.stdout)
            except Exception:
                raise ValueError('VM 관리 권한을 확인하지 못했습니다. VM 비밀번호가 바뀌었다면 다시 접속해 주세요.') from None
            if reply.get('error') == 'existing-account':
                raise ValueError('이 VM의 기존 Tailscale 계정은 변경하지 않았습니다. 운영자에게 문의해 주세요.')
            if not reply.get('ok'):
                raise ValueError('공개 설정을 완료하지 못했습니다. 잠시 후 다시 확인하세요. 계속되면 운영자에게 HTTPS 설정 확인을 요청해 주세요.')
            final = public_view(await self.read())
            if operation == 'publish.start' and final['state'] != 'public':
                raise ValueError('공개 설정을 확인 중입니다. 잠시 후 상태 확인을 눌러 주세요.')
            await self.guard('publish.validate')
            await self.record(operation, final)
            if operation == 'publish.start':
                final['message'] = '공개 설정이 완료됐습니다. 처음에는 주소가 연결되기까지 최대 10분 걸릴 수 있습니다.'
            text = final.pop('message')
            await feedback(text=text, **final)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            await feedback('error', str(exc) if isinstance(exc, ValueError) else '공개 설정에 응답이 없습니다. 상태 확인 후 다시 시도해 주세요.')
        finally:
            if key_info:
                await asyncio.to_thread(revoke_key, key_info[1], key_info[2])
            if lock is not None:
                os.close(lock)

    async def close(self):
        if self.task and not self.task.done():
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
        self.password = ''
