"""Read-only continuity monitor; no credential or API body logging."""
import datetime as dt
import json
from pathlib import Path
import runpy
import shlex
import time
import urllib.request

STATE = Path('/var/lib/uccloud-keystone-pool-20261026')
env = {}
for line in Path('/etc/cloud-portal/portal.env').read_text().splitlines():
    if line.strip() and not line.lstrip().startswith('#'):
        key, value = shlex.split(line, comments=False)[0].split('=', 1)
        env[key] = value
base = env['OS_AUTH_URL'].rstrip('/')
auth = {'auth': {'identity': {'methods': ['password'], 'password': {'user': {
    'name': env['OS_USERNAME'], 'password': env['OS_PASSWORD'],
    'domain': {'name': env.get('OS_USER_DOMAIN_NAME', 'Default')}}}},
    'scope': {'project': {'name': env['OS_PROJECT_NAME'],
                         'domain': {'name': env.get('OS_PROJECT_DOMAIN_NAME', 'Default')}}}}}
probe = runpy.run_path('/var/lib/uccloud-maintenance/20261008/public_funnel_probe.py')
addresses = probe['public_addresses']()
result = {'started': dt.datetime.now(dt.timezone.utc).isoformat(),
          'keystone_ok': 0, 'keystone_fail': 0, 'portal_ok': 0, 'portal_fail': 0,
          'public_ok': 0, 'public_fail': 0, 'auth_ok': 0, 'auth_fail': 0, 'errors': []}
token = None
renew_at = 0
tick = 0
end = time.monotonic() + 3600

def fail(kind, exc):
    result[kind + '_fail'] += 1
    result['errors'].append({'time': dt.datetime.now(dt.timezone.utc).isoformat(),
                             'kind': kind, 'error': type(exc).__name__})
    result['errors'] = result['errors'][-30:]

while time.monotonic() < end and not (STATE / 'monitor-stop').exists():
    if time.monotonic() >= renew_at:
        try:
            request = urllib.request.Request(base + '/auth/tokens', data=json.dumps(auth).encode(),
                                              headers={'Content-Type': 'application/json'})
            with urllib.request.urlopen(request, timeout=10) as response:
                response.read()
                assert response.status == 201
                token = response.headers['X-Subject-Token']
            result['auth_ok'] += 1
            renew_at = time.monotonic() + 60
        except Exception as exc:
            fail('auth', exc)
            renew_at = time.monotonic() + 10
    for kind in ['keystone', 'portal'] + (['public'] if tick % 5 == 0 else []):
        try:
            if kind == 'public':
                assert probe['public_get']('/api/health', addresses)[0] == 200
            else:
                request = urllib.request.Request(base + '/projects?limit=1', headers={'X-Auth-Token': token or ''}) if kind == 'keystone' else urllib.request.Request('http://127.0.0.1:19080/api/health', headers={'Host': 'comsv-03.tail5819db.ts.net'})
                with urllib.request.urlopen(request, timeout=10) as response:
                    response.read()
                    assert response.status == 200
            result[kind + '_ok'] += 1
        except Exception as exc:
            fail(kind, exc)
    result['updated'] = dt.datetime.now(dt.timezone.utc).isoformat()
    temporary = STATE / 'continuity.next'
    temporary.write_text(json.dumps(result, indent=2))
    temporary.chmod(0o600)
    temporary.replace(STATE / 'continuity.json')
    tick += 1
    time.sleep(2)
