"""Bounded token/read-API test. Credential JSON comes from stdin, never argv/logs."""
import concurrent.futures
import json
import statistics
import sys
import time
import urllib.error
import urllib.request

env = json.load(sys.stdin)
base = sys.argv[1].rstrip('/')
count = int(sys.argv[2])
workers = int(sys.argv[3])
auth = {'auth': {'identity': {'methods': ['password'], 'password': {'user': {
    'name': env['OS_USERNAME'], 'password': env['OS_PASSWORD'],
    'domain': {'name': env.get('OS_USER_DOMAIN_NAME', 'Default')}}}},
    'scope': {'project': {'name': env['OS_PROJECT_NAME'],
                         'domain': {'name': env.get('OS_PROJECT_DOMAIN_NAME', 'Default')}}}}}

def call(path, data=None, token=None):
    headers = {'Content-Type': 'application/json'}
    if token:
        headers['X-Auth-Token'] = token
    request = urllib.request.Request(base + path, data=None if data is None else json.dumps(data).encode(), headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            response.read()
            return response.status, response.headers.get('X-Subject-Token')
    except urllib.error.HTTPError as exc:
        exc.read()
        return exc.code, None

def one(index):
    start = time.monotonic()
    try:
        status, token = call('/auth/tokens', auth)
        assert status == 201 and token
        for path in ['/projects?limit=1', '/users?limit=1', '/roles']:
            code, _ = call(path, token=token)
            assert code == 200
        return {'ok': True, 'seconds': time.monotonic() - start}
    except Exception as exc:
        return {'ok': False, 'error': type(exc).__name__, 'seconds': time.monotonic() - start}

with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
    values = list(executor.map(one, range(count)))
latency = sorted(v['seconds'] for v in values)
bad_token, _ = call('/projects', token='uccloud-invalid-test-token')
bad_body, _ = call('/auth/tokens', {'auth': {}})
result = {'count': count, 'concurrency': workers,
          'passed': sum(v['ok'] for v in values), 'failed': sum(not v['ok'] for v in values),
          'p50_seconds': round(statistics.median(latency), 4),
          'p95_seconds': round(latency[min(len(latency) - 1, int(len(latency) * .95))], 4),
          'invalid_token_status': bad_token, 'invalid_body_status': bad_body,
          'errors': [v['error'] for v in values if not v['ok']]}
print(json.dumps(result))
assert result['failed'] == 0 and bad_token == 401 and bad_body == 400
