"""Private live test. Credentials stay in memory; all fan floors return to automatic."""
import http.cookiejar
import json
from pathlib import Path
import shlex
import urllib.request

config = {}
for line in Path('/etc/cloud-portal/portal.env').read_text().splitlines():
    key, sep, value = line.partition('=')
    if sep and key in ('PORTAL_ADMIN_USERNAME','PORTAL_ADMIN_PASSWORD'):
        config[key] = shlex.split(value)[0]
base = 'http://127.0.0.1:8088'
opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
def call(path, body=None, csrf=None):
    headers = {'Content-Type':'application/json'}
    if csrf: headers['X-CSRF-Token'] = csrf
    request = urllib.request.Request(base+path, data=json.dumps(body).encode() if body is not None else None, headers=headers)
    with opener.open(request,timeout=65) as response:
        return json.load(response)

login = call('/api/auth/login',{'username':config.get('PORTAL_ADMIN_USERNAME','admin'),'password':config['PORTAL_ADMIN_PASSWORD']})
config.clear()
csrf = login['csrf_token']
status = call('/api/admin/fans')
assert len(status['nodes']) == 5 and all(n['state']=='ok' and n['minimum']==0 for n in status['nodes']), 'Initial fan state unavailable / nonautomatic'
for node in status['nodes']:
    id = node['id']
    try:
        result = call(f'/api/admin/fans/{id}/minimum', {'minimum':10,'expected':0},csrf)
        assert result['node']['minimum'] == 10
    finally:
        current = next(n for n in call('/api/admin/fans')['nodes'] if n['id']==id)
        if current.get('minimum') != 0:
            assert current.get('state') == 'ok' and isinstance(current.get('minimum'),int)
            result = call(f'/api/admin/fans/{id}/minimum', {'minimum':0,'expected':current['minimum']},csrf)
            assert result['node']['minimum'] == 0
    print(id+': minimum 10% -> automatic verified',flush=True)
final = call('/api/admin/fans')
assert all(n['state']=='ok' and n['minimum']==0 for n in final['nodes'])
print('Five-node authenticated API check passed; all nodes automatic',flush=True)
