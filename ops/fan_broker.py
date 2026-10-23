"""Private Unix-socket bridge. Only fixed fan operations, never shell commands."""
import json
import os
import pwd
import socket
import socketserver
import struct
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from ilo_control import FanError, NODES, validate_request

SOCKET = '/run/uccloud-fans/control.sock'
PORTAL_UID = pwd.getpwnam('cloud-portal').pw_uid
REMOTE = ['/usr/bin/ssh', '-T', '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes',
           '-o', 'ConnectTimeout=5', '-o', 'LogLevel=ERROR', 'root@172.29.236.12',
           '/usr/bin/python3 /usr/local/lib/uccloud/ilo_control.py request']
LOCAL = ['/usr/bin/python3', '/usr/local/lib/uccloud/ilo_control.py', 'request']
LOCK = threading.Lock()
CACHE = None
CACHED_AT = 0

def run_worker(command, request):
    process = subprocess.run(command, input=json.dumps(request)+'\n', text=True,
                             capture_output=True, timeout=55, check=False)
    if process.returncode or len(process.stdout) > 65536:
        raise FanError('unavailable')
    result = json.loads(process.stdout)
    if not isinstance(result, dict):
        raise FanError('unavailable')
    return result

def call_worker(request):
    global CACHE, CACHED_AT
    validate_request(request)
    if not LOCK.acquire(blocking=False):
        raise FanError('busy')
    try:
        if request['op'] == 'status' and CACHE is not None and time.monotonic() - CACHED_AT < 10:
            return CACHE
        if request['op'] == 'set':
            CACHE, CACHED_AT = None, 0
        if request['op'] == 'status':
            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(lambda command: run_worker(command, request), (LOCAL, REMOTE)))
            rows = {node['id']: node for result in results for node in result.get('nodes', []) if node.get('id') in NODES}
            result = {'nodes': [rows.get(node, {'id':node, 'name':value[0], 'role':value[1], 'state':'unavailable', 'supported':False, 'fans':[], 'cpu_c':None, 'minimum':None}) for node, value in NODES.items()]}
        else:
            result = run_worker(REMOTE if request['node'] == 'controller' else LOCAL, request)
        if request['op'] == 'status' and 'nodes' in result:
            CACHE, CACHED_AT = result, time.monotonic()
        else:
            CACHE, CACHED_AT = None, 0
        return result
    finally:
        LOCK.release()

class Handler(socketserver.StreamRequestHandler):
    def handle(self):
        self.connection.settimeout(60)
        try:
            _, uid, _ = struct.unpack('3i', self.connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
            if uid not in (0, PORTAL_UID):
                raise FanError('permission')
            raw = self.rfile.readline(4097)
            if len(raw) > 4096 or not raw.endswith(b'\n'):
                raise FanError('invalid')
            request = json.loads(raw)
            result = call_worker(request)
        except FanError as exc:
            result = {'error': exc.code}
        except Exception:
            # Never expose subprocess stderr, credentials, TLS data or tracebacks.
            result = {'error': 'uncertain' if isinstance(locals().get('request'), dict) and request.get('op') == 'set' else 'unavailable'}
        self.wfile.write((json.dumps(result, ensure_ascii=False)+'\n').encode())

class Server(socketserver.ThreadingUnixStreamServer):
    daemon_threads = True
    request_queue_size = 5

if __name__ == '__main__':
    os.umask(0o007)
    if os.path.lexists(SOCKET):
        os.unlink(SOCKET)
    with Server(SOCKET, Handler) as server:
        os.chmod(SOCKET, 0o660)
        server.serve_forever()
