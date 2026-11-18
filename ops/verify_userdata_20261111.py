"""A disposable team/VM verifies compressed cloud-init and SSH continuity."""
import argparse
import asyncio
import json
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import verify_provisioner_20261026 as qa

qa.STATE = Path('/var/lib/uccloud-userdata-20261111')
qa.QA = qa.STATE/'qa-private.json'
qa.RESULT = qa.STATE/'verification.json'
qa.BASE = 'http://127.0.0.1:18280'
original_api = qa.api


def api(path, payload=None, method=None, expected=200):
    if path == '/api/instances' and payload is not None:
        payload = dict(payload, software=[{'id': 'nginx', 'version': '1.30'}], request_id=str(uuid.uuid4()))
    return original_api(path, payload, method, expected)


qa.api = api


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('phase', choices=['create', 'hold', 'program', 'failure', 'public', 'cleanup'])
    phase = parser.parse_args().phase
    if phase == 'create':
        qa.create()
    elif phase == 'hold':
        asyncio.run(qa.shell(True))
    elif phase == 'program':
        qa.BASE = 'http://127.0.0.1:19080'
        # The public status file is intentionally readable without root.
        command = "python3 -c 'import json; s=json.load(open(\"/var/lib/uccloud-software/status.json\")); assert s.get(\"state\")==\"ready\"' && curl -fsS http://127.0.0.1:8082/ >/dev/null && printf '\\nNGINX_%s\\n' VERIFIED\n"
        asyncio.run(qa.shell(command=command, expected='NGINX_VERIFIED'))
        qa.result(real_nginx_installed_and_http=True)
    elif phase == 'failure':
        qa.BASE = 'http://127.0.0.1:19080'
        state = json.loads(qa.QA.read_text())
        catalog = original_api('/api/catalog')
        image = next(x for x in catalog['images'] if x['name'] == 'ubuntu-24.04')
        flavor = min(catalog['flavors'], key=lambda x: x['ram'])
        assert flavor['ram'] < 4096
        before = original_api('/api/instances')['instances']
        error = original_api('/api/instances', {'name': state['vm_name']+'-reject',
            'image_id': image['id'], 'flavor_id': flavor['id'], 'vm_username': 'student',
            'vm_password': state['vm_password'], 'software': [{'id': 'mysql', 'version': '8.4',
            'username': 'dbuser', 'password': state['vm_password']}]}, expected=422)
        assert '메모리 4GB 이상' in error['detail']
        assert len(original_api('/api/instances')['instances']) == len(before)
        qa.result(real_creation_rejection_korean=True, rejected_request_created_no_vm=True)
        print(error['detail'], flush=True)
    elif phase == 'public':
        import runpy
        import websockets
        probe = runpy.run_path('/var/lib/uccloud-maintenance/20261008/public_funnel_probe.py')
        connect = websockets.connect
        def public_connect(uri, **options):
            options.update(host=probe['public_addresses']()[0], port=443, server_hostname=qa.HOST)
            return connect(uri.replace('ws://', 'wss://', 1), **options)
        websockets.connect = public_connect
        try:
            asyncio.run(qa.shell())
            qa.result(real_public_https_websocket_shell=True)
        finally:
            websockets.connect = connect
    elif phase == 'cleanup':
        qa.BASE = 'http://127.0.0.1:19080'
        state = json.loads(qa.QA.read_text())
        original_api('/api/auth/login', {'username': state['username'], 'password': state['password']})
        present = any(v['id'] == state['server_id'] for v in original_api('/api/instances')['instances'])
        if not present:
            def cleanup_api(path, payload=None, method=None, expected=200):
                if method == 'DELETE' and path == '/api/instances/'+state['server_id']:
                    return {}
                return original_api(path, payload, method, expected)
            qa.api = cleanup_api
        qa.cleanup()
