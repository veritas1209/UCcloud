"""Keystone leak repair controller: secret-free evidence, canary and rolling apply."""
import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import runpy
import shlex
import subprocess
import time

CONTAINER = 'controller1-keystone-container-301f3fef'
ROOTFS = Path('/var/lib/lxc') / CONTAINER / 'rootfs'
SITE = Path('/openstack/venvs/keystone-33.0.1/lib/python3.14/site-packages')
SOURCE = SITE / 'keystone/federation/backends/sql.py'
STATE = Path('/var/lib/uccloud-keystone-pool-20261026')
PYTHON = '/openstack/venvs/keystone-33.0.1/bin/python'
OLD = '''    def get_enabled_service_providers(self):
        with sql.session_for_read() as session:
            service_providers = session.query(ServiceProviderModel)
            service_providers = service_providers.filter_by(enabled=True)
            return service_providers'''
NEW = OLD.replace('return service_providers', 'return service_providers.all()')

def run(args, **kwargs):
    return subprocess.run(args, check=True, capture_output=True, text=True, **kwargs).stdout.strip()

def lxc(args, **kwargs):
    return run(['lxc-attach', '-n', CONTAINER, '--'] + args, **kwargs)

def save(name, data):
    STATE.mkdir(mode=0o700, exist_ok=True)
    path = STATE / name
    temporary = path.with_suffix('.next')
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    temporary.chmod(0o600)
    temporary.replace(path)

def test(canary, count, concurrency):
    env = {}
    for line in Path('/etc/cloud-portal/portal.env').read_text().splitlines():
        if not line.strip() or line.lstrip().startswith('#'):
            continue
        items = shlex.split(line, comments=False)
        assert len(items) == 1 and '=' in items[0]
        key, value = items[0].split('=', 1)
        env[key] = value
    if canary:
        answer = lxc([PYTHON, '/opt/keystone_pool_http_test_20261026.py',
                      'http://127.0.0.1:15000/v3', str(count), str(concurrency)], input=json.dumps(env), timeout=count*15+60)
    else:
        answer = run(['/opt/cloud-portal/.venv/bin/python', '/tmp/keystone_pool_http_test_20261026.py',
                      env['OS_AUTH_URL'], str(count), str(concurrency)], input=json.dumps(env), timeout=count*15+60)
    result = json.loads(answer.splitlines()[-1])
    save(('canary' if canary else 'production') + '-http-' + str(count) + '.json', result)
    print(json.dumps(result), flush=True)
    return result

def snapshot():
    program = '''import json,subprocess,pathlib; m=int(subprocess.check_output(['systemctl','show','keystone-wsgi-public','-p','MainPID','--value'],text=True)); print(json.dumps({'master':m,'workers':list(map(int,pathlib.Path(f'/proc/{m}/task/{m}/children').read_text().split()))}))'''
    return json.loads(lxc(['python3', '-c', program]))

def evidence():
    assert not (STATE / 'before.json').exists(), 'Original evidence already exists; refusing to overwrite it.'
    result = {'time': datetime.datetime.now(datetime.timezone.utc).isoformat(), 'processes': snapshot(),
              'source_sha256': hashlib.sha256((ROOTFS / str(SOURCE).lstrip('/')).read_bytes()).hexdigest()}
    save('before.json', result)
    for kind, path in [('isolated', '/opt/keystone_pool_repro_20261026.py'),
                       ('live-select', '/opt/keystone_pool_live_probe_20261026.py')]:
        for mode in ['original', 'fixed']:
            raw = lxc(['runuser', '-u', 'keystone', '--', PYTHON, path, mode], timeout=30)
            result = json.loads(raw.splitlines()[-1].removeprefix('UCCLOUD_RESULT '))
            save(kind + '-' + mode + '.json', result)
            assert result['completed'] == (200 if mode == 'fixed' else 2)
            assert result['checkedout_before_gc'] == (0 if mode == 'fixed' else 2)
            print(kind, json.dumps(result), flush=True)

def patch():
    assert (STATE / 'canary-http-120.json').exists()
    tested = json.loads((STATE / 'canary-http-120.json').read_text())
    assert tested['passed'] == 120 and tested['failed'] == 0
    metrics = json.loads(lxc(['cat', '/var/lib/keystone/uccloud-pool-canary.json']))
    assert metrics['outstanding'] == 0 and metrics['materialized_calls'] >= 120
    save('canary-pool.json', metrics)
    source = ROOTFS / str(SOURCE).lstrip('/')
    value = source.read_text()
    assert value.count(OLD) == 1 and NEW not in value
    backup = STATE / 'federation-sql.py.before'
    assert not backup.exists()
    backup.write_text(value)
    backup.chmod(0o600)
    updated = value.replace(OLD, NEW)
    compile(updated, str(SOURCE), 'exec')
    temporary = source.with_suffix('.uccloud-next')
    temporary.write_text(updated)
    stat = source.stat()
    os.chmod(temporary, stat.st_mode)
    os.chown(temporary, stat.st_uid, stat.st_gid)
    temporary.replace(source)
    save('patch.json', {'before_sha256': hashlib.sha256(value.encode()).hexdigest(),
                        'after_sha256': hashlib.sha256(updated.encode()).hexdigest(),
                        'change': 'Materialize federation provider SELECT before closing the session; no pool sizing change.'})
    print('source_patch_written', flush=True)

def rolling():
    before = json.loads((STATE / 'before.json').read_text())['processes']
    assert snapshot()['master'] == before['master']
    history = []
    for worker in before['workers']:
        current = snapshot()
        assert current['master'] == before['master'] and len(current['workers']) == 16
        assert worker in current['workers']
        lxc(['kill', '-HUP', str(worker)])
        end = time.monotonic() + 60
        while time.monotonic() < end:
            after = snapshot()
            if worker not in after['workers'] and len(after['workers']) == 16:
                break
            time.sleep(.5)
        else:
            raise RuntimeError('Worker graceful replacement did not finish; stopping rollout')
        assert after['master'] == before['master']
        # Authenticated requests must succeed before replacing the next worker.
        test(False, 2, 1)
        history.append({'old': worker, 'new': list(set(after['workers']) - set(current['workers']))})
        save('rolling.json', {'master': before['master'], 'replaced': history, 'latest': after})
        print('graceful_workers_applied', len(history), flush=True)
        time.sleep(1)
    test(False, 160, 4)
    assert not set(before['workers']) & set(snapshot()['workers'])

def postcheck():
    for kind, path in [('isolated', '/opt/keystone_pool_repro_20261026.py'),
                       ('live-select', '/opt/keystone_pool_live_probe_20261026.py')]:
        raw = lxc(['runuser', '-u', 'keystone', '--', PYTHON, path, 'installed'], timeout=40)
        result = json.loads(raw.splitlines()[-1].removeprefix('UCCLOUD_RESULT '))
        save(kind + '-installed.json', result)
        assert result['completed'] == 200 and result['checkedout_before_gc'] == 0
        if kind == 'isolated':
            assert result['exception_returns_connection'] and result['concurrent_nonempty_checks'] == 400
        print(kind, json.dumps(result), flush=True)
    before = json.loads((STATE / 'before.json').read_text())['processes']
    after = snapshot()
    assert after['master'] == before['master'] and len(after['workers']) == 16
    assert not set(before['workers']) & set(after['workers'])
    save('after-processes.json', after)

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['evidence', 'canary', 'patch', 'rolling', 'verify', 'postcheck'])
    args = parser.parse_args()
    if args.action == 'evidence': evidence()
    elif args.action == 'canary': test(True, 120, 4)
    elif args.action == 'patch': patch()
    elif args.action == 'rolling': rolling()
    elif args.action == 'postcheck': postcheck()
    else: test(False, 160, 4)
