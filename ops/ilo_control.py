"""HPE fan floor control; never set individual PWM or disable thermal protection.

Setup with --nodes on the controller; the controller's iLO credential is configured
on comsv-01 to avoid shared-NIC hairpin limitations. Credentials entered privately.
Then: sudo python3 ilo_control.py status
      sudo python3 ilo_control.py set compute-1 20 --expected 0
      sudo python3 ilo_control.py set compute-1 0 --expected 20  # automatic
"""
from __future__ import annotations

import argparse
import base64
from concurrent.futures import ThreadPoolExecutor
import datetime
import getpass
import hashlib
import http.client
import json
import os
from pathlib import Path
import ssl
import stat
import sys
import time

CONFIG = Path('/etc/uccloud-fans/ilo.json')
NODES = {
    'controller': ('comsv-03', '컨트롤러', '172.141.0.106', 'SGHD38FHJ8'),
    'compute-1': ('comsv-01', '컴퓨트', '172.141.0.107', 'SGHD38FHKC'),
    'compute-2': ('comsv-04', '컴퓨트', '172.141.0.109', 'SGHD38FHJG'),
    'compute-3': ('comsv-05', '컴퓨트', '172.141.0.108', 'SGHD38FHJJ'),
    'compute-4': ('user-ProLiant-DL20-Gen11', '컴퓨트', '172.141.0.103', 'SGHD4JL1QP'),
}
THERMAL = '/redfish/v1/Chassis/1/Thermal/'

class FanError(Exception):
    def __init__(self, code):
        self.code = code

def validate_request(request):
    if not isinstance(request, dict):
        raise FanError('invalid')
    if request == {'op': 'status'}:
        return request
    if set(request) != {'op', 'node', 'minimum', 'expected'} or request['op'] != 'set':
        raise FanError('invalid')
    if not isinstance(request['node'], str) or request['node'] not in NODES:
        raise FanError('invalid')
    for key in ('minimum', 'expected'):
        if type(request[key]) is not int or not 0 <= request[key] <= 100:
            raise FanError('invalid')
    return request

class Ilo:
    def __init__(self, node, secret):
        self.host = NODES[node][2]
        self.secret = secret
        self.context = ssl.create_default_context(cadata=secret['certificate'])
        self.context.check_hostname = False  # Exact certificate pin checked before credentials.
        self.context.verify_flags &= ~ssl.VERIFY_X509_STRICT

    def request(self, method, path, body=None, etag=None):
        conn = http.client.HTTPSConnection(self.host, timeout=8, context=self.context)
        try:
            conn.connect()
            if hashlib.sha256(conn.sock.getpeercert(binary_form=True)).hexdigest() != self.secret['pin']:
                raise FanError('certificate')
            authorization = base64.b64encode(('Administrator:' + self.secret['password']).encode()).decode()
            headers = {'Authorization': 'Basic ' + authorization, 'Accept': 'application/json'}
            if body is not None:
                headers['Content-Type'] = 'application/json'
            if etag:
                headers['If-Match'] = etag
            conn.request(method, path, body=json.dumps(body) if body is not None else None, headers=headers)
            response = conn.getresponse()
            payload = response.read(262145)
            if len(payload) > 262144:
                raise FanError('unavailable')
            if response.status in (401, 403):
                raise FanError('permission')
            if response.status == 412:
                raise FanError('conflict')
            if response.status not in (200, 201, 202, 204):
                raise FanError('unsupported' if method == 'PATCH' else 'unavailable')
            return (json.loads(payload) if payload else {}, response.getheader('ETag'))
        finally:
            conn.close()

    def thermal(self):
        return self.request('GET', THERMAL)

def summarize(node, data):
    hpe = data.get('Oem', {}).get('Hpe', {})
    minimum = hpe.get('FanPercentMinimum')
    warnings = [str(s.get('Name', 'sensor'))[:60] for s in data.get('Fans', []) + data.get('Temperatures', [])
                if s.get('Status', {}).get('Health') in ('Warning', 'Critical')]
    fans = [{'name': str(f.get('Name', '팬'))[:40], 'percent': f.get('Reading')}
            for f in data.get('Fans', []) if f.get('Status', {}).get('State') == 'Enabled']
    cpu = next((t.get('ReadingCelsius') for t in data.get('Temperatures', [])
                if 'CPU' in t.get('Name', '') and t.get('Status', {}).get('State') == 'Enabled'), None)
    return {'id': node, 'name': NODES[node][0], 'role': NODES[node][1], 'fans': fans,
            'cpu_c': cpu, 'minimum': minimum, 'supported': type(minimum) is int and 0 <= minimum <= 100,
            'warnings': warnings, 'state': 'warning' if warnings else 'ok',
            'updated_at': datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='seconds')}

def read_node(node, config):
    try:
        data, _ = Ilo(node, config[node]).thermal()
        return summarize(node, data)
    except Exception:
        return {'id': node, 'name': NODES[node][0], 'role': NODES[node][1],
                'state': 'unavailable', 'supported': False, 'fans': [], 'cpu_c': None, 'minimum': None}

def execute(request, config):
    validate_request(request)
    if request['op'] == 'status':
        with ThreadPoolExecutor(max_workers=5) as pool:
            return {'nodes': list(pool.map(lambda node: read_node(node, config), [n for n in NODES if n in config]))}
    node = request['node']
    if node not in config:
        raise FanError('configuration')
    client = Ilo(node, config[node])
    before, etag = client.thermal()
    current = summarize(node, before)
    if not current['supported']:
        raise FanError('unsupported')
    if current['minimum'] != request['expected']:
        raise FanError('conflict')
    if current['warnings']:
        raise FanError('warning')
    if request['minimum'] == current['minimum']:
        return {'node': current, 'changed': False}
    # The sole writable property. FanPercentAdjust, modes, shutdown and PWM are never written.
    client.request('PATCH', THERMAL, {'Oem': {'Hpe': {'FanPercentMinimum': request['minimum']}}}, etag)
    for _ in range(6):
        after, _ = client.thermal()
        result = summarize(node, after)
        if result['minimum'] == request['minimum']:
            return {'node': result, 'changed': True}
        time.sleep(1)
    raise FanError('uncertain')

def load_config():
    info = CONFIG.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or stat.S_IMODE(info.st_mode) != 0o600:
        raise FanError('configuration')
    return json.loads(CONFIG.read_text())

def setup(nodes):
    if os.geteuid() != 0 or CONFIG.exists():
        raise FanError('configuration')
    secrets = {}
    for node, (name, _, host, serial) in NODES.items():
        if node not in nodes:
            continue
        conn = http.client.HTTPSConnection(host, timeout=8, context=ssl._create_unverified_context())
        try:
            conn.connect()
            certificate = conn.sock.getpeercert(binary_form=True)
            conn.request('GET', '/redfish/v1/')
            response = conn.getresponse()
            root = json.loads(response.read(262144))
            if response.status != 200 or 'Hpe' not in root.get('Oem', {}):
                raise FanError('configuration')
        finally:
            conn.close()
        secret = {'certificate': ssl.DER_cert_to_PEM_cert(certificate),
                  'pin': hashlib.sha256(certificate).hexdigest(),
                  'password': getpass.getpass(name + ' iLO password: ')}
        client = Ilo(node, secret)
        system, _ = client.request('GET', '/redfish/v1/Systems/1/')
        if system.get('SerialNumber') != serial:
            raise FanError('configuration')
        data, _ = client.thermal()
        secrets[node] = secret
        print(json.dumps(summarize(node, data), ensure_ascii=False), flush=True)
    CONFIG.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with os.fdopen(os.open(CONFIG, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600), 'w') as output:
        json.dump(secrets, output)
    print('Root-only credentials configured. No fan settings changed.', flush=True)

def main():
    parser = argparse.ArgumentParser(description='UCcloud automatic fan cooling / minimum speed')
    sub = parser.add_subparsers(dest='command', required=True)
    setup_parser = sub.add_parser('setup')
    setup_parser.add_argument('--nodes', nargs='+', choices=NODES, required=True)
    sub.add_parser('status')
    sub.add_parser('request')
    setter = sub.add_parser('set')
    setter.add_argument('node', choices=NODES)
    setter.add_argument('minimum', type=int)
    setter.add_argument('--expected', required=True, type=int)
    args = parser.parse_args()
    try:
        if args.command == 'setup':
            setup(args.nodes)
            return
        if args.command == 'request':
            raw = sys.stdin.buffer.readline(4097)
            if len(raw) > 4096:
                raise FanError('invalid')
            request = json.loads(raw)
        else:
            request = {'op': 'status'} if args.command == 'status' else {
                'op': 'set', 'node': args.node, 'minimum': args.minimum, 'expected': args.expected}
        result = execute(request, load_config())
        print(json.dumps(result, ensure_ascii=False), flush=True)
    except FanError as exc:
        print(json.dumps({'error': exc.code}), flush=True)
    except Exception:
        print(json.dumps({'error': 'unavailable'}), flush=True)

if __name__ == '__main__':
    main()
