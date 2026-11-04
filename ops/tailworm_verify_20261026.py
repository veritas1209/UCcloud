"""Continuity and read-only QA guest checks; credentials never printed."""
import asyncio
import datetime as dt
import hashlib
import json
from pathlib import Path
import runpy
import shlex
import sqlite3
import sys
import time
import urllib.request

STATE = Path('/var/lib/uccloud-tailworm-20261026')
QA = Path('/var/lib/uccloud-provisioner-deploy-20261026/qa-private.json')
HOST = 'comsv-03.tail5819db.ts.net'


def save(name, data):
    STATE.mkdir(mode=0o700, exist_ok=True)
    p = STATE / name
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    p.chmod(0o600)


def health_monitor():
    probe = runpy.run_path('/var/lib/uccloud-maintenance/20261008/public_funnel_probe.py')
    addresses = probe['public_addresses']()
    result = {'started': dt.datetime.now(dt.timezone.utc).isoformat(),
              'local_ok': 0, 'local_fail': 0, 'public_ok': 0, 'public_fail': 0, 'failures': []}
    end = time.monotonic() + 5400
    tick = 0
    while time.monotonic() < end and not (STATE / 'monitor-stop').exists():
        for kind in ['local'] + (['public'] if tick % 2 == 0 else []):
            try:
                if kind == 'local':
                    req = urllib.request.Request('http://127.0.0.1:19080/api/health', headers={'Host': HOST})
                    with urllib.request.urlopen(req, timeout=3) as r: assert r.status == 200
                else:
                    assert probe['public_get']('/api/health', addresses)[0] == 200
                result[kind + '_ok'] += 1
            except Exception as e:
                result[kind + '_fail'] += 1
                result['failures'].append({'time': dt.datetime.now(dt.timezone.utc).isoformat(),
                                           'kind': kind, 'error': type(e).__name__})
                result['failures'] = result['failures'][-30:]
        result['updated'] = dt.datetime.now(dt.timezone.utc).isoformat()
        save('continuity.json', result)
        tick += 1
        time.sleep(2)
    print(json.dumps(result))


async def guest():
    import asyncssh
    s = json.loads(QA.read_text())
    req = urllib.request.Request('http://127.0.0.1:19080/api/instances',
                                headers={'Host': HOST, 'Cookie': s['cookie']})
    with urllib.request.urlopen(req, timeout=30) as r: values = json.load(r)
    vm = next(v for v in values['instances'] if v['id'] == s['server_id'])
    addr = vm['addresses'][0]['address']
    with sqlite3.connect('file:/var/lib/cloud-portal/portal.db?mode=ro', uri=True) as db:
        key = db.execute('SELECT public_key FROM instance_ssh_host_keys WHERE server_id=?', (vm['id'],)).fetchone()
    assert key
    script = r'''
import json,subprocess,collections
def call(a): return subprocess.check_output(a,text=True).strip()
p=json.loads(call(['tailscale','debug','prefs']))
s=json.loads(call(['tailscale','status','--json']))
sshd=dict(x.split(' ',1) for x in call(['/usr/sbin/sshd','-T']).splitlines() if ' ' in x)
out={'os':call(['lsb_release','-ds']),'kernel':call(['uname','-r']),
     'tailscale_cli':call(['tailscale','version']).splitlines()[0], 'tailscale_daemon':s.get('Version'),
     'state':s.get('BackendState'), 'RunSSH':p.get('RunSSH'),'OperatorUser':p.get('OperatorUser'),
     'AppConnector':p.get('AppConnector'),'serve':json.loads(call(['tailscale','serve','status','--json'])),
     'openssh':{k:sshd.get(k) for k in ['permitrootlogin','allowtcpforwarding','allowagentforwarding','allowstreamlocalforwarding']}}
print(json.dumps(out))
'''
    async with asyncssh.connect(addr, username='student', password=s['vm_password'],
                               client_keys=[], known_hosts=(addr + ' ' + key[0].strip() + '\n').encode(),
                               connect_timeout=10, login_timeout=10) as ssh:
        cmd = 'sudo -S -p "" python3 -c ' + shlex.quote(script)
        answer = await ssh.run(cmd, input=s['vm_password'] + '\n', check=True, timeout=45)
        result = json.loads(answer.stdout)
        result['time'] = dt.datetime.now(dt.timezone.utc).isoformat()
        result['read_only'] = True
        save('qa-guest.json', result)
        print(json.dumps(result))


def smoke():
    # Existing helper uses only the disposable QA account for the shell assertion.
    test = runpy.run_path('/var/lib/uccloud-provisioner-deploy-20261026/verify_provisioner_20261026.py')
    asyncio.run(test['shell']())
    probe = runpy.run_path('/var/lib/uccloud-maintenance/20261008/public_funnel_probe.py')
    addresses = probe['public_addresses']()
    routes = {}
    for path in ['/', '/api/health', '/terminal', '/publish-guide']:
        status, _ = probe['public_get'](path, addresses)
        routes[path] = status
    assert all(code == 200 for path, code in routes.items() if path != '/publish-guide')
    save('smoke.json', {'time': dt.datetime.now(dt.timezone.utc).isoformat(),
                        'public_routes': routes, 'actual_browser_ssh_command': True})
    print('PUBLIC_HTTPS_AND_BROWSER_SSH_PASS')


if __name__ == '__main__':
    if sys.argv[1] == 'monitor': health_monitor()
    elif sys.argv[1] == 'guest': asyncio.run(guest())
    elif sys.argv[1] == 'smoke': smoke()
    else: raise SystemExit('Unknown action')
