"""Actual public WSS, VM-scoped AI and file API smoke checks on the audit QA VM."""
import asyncio
import base64
import datetime as dt
import http.client
import json
from pathlib import Path
import runpy
import socket
import ssl
import time
from urllib.parse import urlsplit
from uuid import uuid4

ROOT = Path('/var/lib/uccloud-tailworm-20261026')
s = json.loads(Path('/var/lib/uccloud-provisioner-deploy-20261026/qa-private.json').read_text())
assert s['vm_name'].startswith('privsep-qa-')
probe = runpy.run_path('/var/lib/uccloud-maintenance/20261008/public_funnel_probe.py')
HOST = probe['HOST']
IP = probe['public_addresses']()[0]


def api(path, payload=None, token=None):
    headers = {'Host': HOST, 'Origin': 'https://' + HOST, 'Content-Type': 'application/json'}
    if token: headers['Authorization'] = 'Bearer ' + token
    else:
        headers['Cookie'] = s['cookie']; headers['X-CSRF-Token'] = s['csrf']
    conn = http.client.HTTPSConnection(HOST, timeout=30)
    try:
        conn.sock = ssl.create_default_context().wrap_socket(socket.create_connection((IP, 443), timeout=15), server_hostname=HOST)
        conn.request('POST' if payload is not None else 'GET', path,
                     body=json.dumps(payload).encode() if payload is not None else None, headers=headers)
        r = conn.getresponse(); body = r.read(1048576)
        assert r.status in (200, 202), f'API returned {r.status}'
        return json.loads(body)
    finally: conn.close()


def job(token, command, sudo=False):
    data = api('/api/agent/v1/jobs', {'command': command, 'cwd': '.', 'timeout_seconds': 60,
                                    'request_id': str(uuid4()), 'sudo': sudo}, token)
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        data = api('/api/agent/v1/jobs/' + data['id'], token=token)
        if data['state'] in ('succeeded', 'failed', 'timed_out', 'cancelled', 'interrupted'):
            assert data['state'] == 'succeeded' and data['exit_code'] == 0
            return data
        time.sleep(2)
    raise TimeoutError('AI job did not finish')


async def main():
    import websockets
    token = None
    result = {}
    try:
        async with websockets.connect('wss://' + HOST + '/api/instances/' + s['server_id'] + '/terminal',
                host=IP, port=443, server_hostname=HOST, proxy=None,
                additional_headers={'Cookie': s['cookie']}, origin='https://' + HOST,
                subprotocols=['uccloud', 'csrf.' + s['csrf']], open_timeout=20) as ws:
            await ws.send(json.dumps({'username': 'student', 'password': s['vm_password'], 'cols': 90, 'rows': 25}))
            while True:
                raw = await asyncio.wait_for(ws.recv(), 30)
                if isinstance(raw, bytes): continue
                msg = json.loads(raw)
                assert msg.get('type') != 'error'
                if msg.get('type') == 'ready': break
            await ws.send(json.dumps({'type': 'input', 'data': "printf 'PUBLIC_%s\\n' WSS_VERIFIED\n"}))
            output = ''
            while 'PUBLIC_WSS_VERIFIED' not in output:
                raw = await asyncio.wait_for(ws.recv(), 30)
                if isinstance(raw, bytes): output += raw.decode(errors='replace')
            result['real_public_websocket_command'] = True
            await ws.send(json.dumps({'type': 'agent.issue', 'request_id': str(uuid4()), 'hours': 1}))
            while True:
                raw = await asyncio.wait_for(ws.recv(), 90)
                if isinstance(raw, bytes): continue
                msg = json.loads(raw)
                if msg.get('type') == 'agent':
                    assert msg['state'] == 'ready', 'AI issuance failed'
                    token = msg['key']; result['agent_issued'] = True
                    break
        context = api('/api/agent/v1/context', token=token)
        assert context['server_id'] == s['server_id'] and context['sudo_available'] is True
        assert 'UCCLOUD_AGENT_AUDIT_OK' in job(token, "printf '%s\\n' UCCLOUD_AGENT_AUDIT_OK")['stdout']
        result['agent_normal_job'] = True
        assert job(token, 'id -u', sudo=True)['stdout'].strip() == '0'
        result['agent_authorized_sudo'] = True
        body = b'UCcloud audit file roundtrip\n'
        path = 'tailworm-verify-' + uuid4().hex + '.txt'
        api('/api/agent/v1/files', {'operation': 'write', 'path': path,
            'data_base64': base64.b64encode(body).decode(), 'offset': 0, 'truncate': True}, token)
        value = api('/api/agent/v1/files', {'operation': 'read', 'path': path}, token)
        assert base64.b64decode(value['data_base64']) == body
        job(token, 'rm -- ' + path)
        result['agent_file_roundtrip_and_cleanup'] = True
        value = api('/api/instances/' + s['server_id'] + '/console', {})
        u = urlsplit(value['url'])
        assert u.scheme == 'https' and u.hostname == HOST and u.port == 8443
        result['console_url_issued'] = True
    finally:
        if token:
            api('/api/agent/v1/disconnect', {}, token)
            result['agent_revoked'] = True
    result['time'] = dt.datetime.now(dt.timezone.utc).isoformat()
    p = ROOT / 'integration.json'; p.write_text(json.dumps(result, indent=2)); p.chmod(0o600)
    print(json.dumps(result))


if __name__ == '__main__': asyncio.run(main())
