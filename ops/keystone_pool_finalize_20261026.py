"""Finalize sanitized evidence after disposable QA cleanup."""
import concurrent.futures
import datetime as dt
import hashlib
import json
from pathlib import Path
import shlex
import subprocess
import time

STATE = Path('/var/lib/uccloud-keystone-pool-20261026')
CONTAINER = 'controller1-keystone-container-301f3fef'

def run(args):
    return subprocess.check_output(args, text=True, timeout=30).strip()

def main():
    checks = json.loads((STATE / 'service-verification.json').read_text())
    assert checks and all(checks.values()) and not (STATE / 'qa-private.json').exists()
    before = json.loads((STATE / 'before.json').read_text())['processes']
    after = json.loads((STATE / 'after-processes.json').read_text())
    assert before['master'] == after['master'] and len(after['workers']) == 16
    assert not set(before['workers']) & set(after['workers'])
    config_program = '''import configparser,json; c=configparser.ConfigParser(interpolation=None); c.read('/etc/keystone/keystone.conf'); print(json.dumps({k:c.get('database',k) for k in ['max_pool_size','max_overflow','pool_timeout']}))'''
    pool = json.loads(run(['lxc-attach', '-n', CONTAINER, '--', 'python3', '-c', config_program]))
    assert pool == {'max_pool_size': '5', 'max_overflow': '50', 'pool_timeout': '30'}
    since = dt.datetime.fromtimestamp((STATE / 'rolling.json').stat().st_mtime, dt.timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')
    logs = run(['lxc-attach', '-n', CONTAINER, '--', 'journalctl', '-u', 'keystone-wsgi-public', '--since', since, '--no-pager', '-o', 'cat'])
    pool_errors = sum('QueuePool limit' in line for line in logs.splitlines())
    assert pool_errors == 0
    code = '''import subprocess,json; v=subprocess.check_output(['virsh','list','--state-running','--uuid'],text=True).split(); q=[int(l.split()[0]) for l in subprocess.check_output(['ps','-eo','etimes,comm'],text=True).splitlines()[1:] if 'qemu-system' in l]; print(json.dumps({'running_vms':len(v),'qemu_count':len(q),'minimum_qemu_uptime_seconds':min(q) if q else None}))'''
    hosts = [('comsv-01', '172.29.236.12'), ('comsv-04', '172.29.236.13'),
             ('comsv-05', '172.29.236.14'), ('user-ProLiant-DL20-Gen11', '172.29.236.15')]
    def collect(item):
        name, address = item
        value = json.loads(run(['ssh', '-n', '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes',
                                'root@' + address, 'python3 -c ' + shlex.quote(code)]))
        value['host'] = name
        return value
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
        compute = list(executor.map(collect, hosts))
    assert [v['running_vms'] for v in compute] == [1, 2, 1, 2]
    assert all(v['minimum_qemu_uptime_seconds'] > 86400 for v in compute)
    units = ['cloud-portal-v2', 'cloud-portal-public-v2', 'uccloud-provisioner',
             'uccloud-portal-edge', 'uccloud-keystone-pool-watch.timer']
    states = {unit: run(['systemctl', 'is-active', unit]) for unit in units}
    assert set(states.values()) == {'active'}
    # Stop only our temporary continuity sampling loop; the read-only watch timer remains.
    (STATE / 'monitor-stop').write_text('finished\n')
    time.sleep(3)
    continuity = json.loads((STATE / 'continuity.json').read_text())
    assert all(continuity[k] == 0 for k in ['auth_fail', 'keystone_fail', 'portal_fail', 'public_fail'])
    watch = json.loads((STATE / 'watch.json').read_text())
    assert watch['patch_digest_matches'] and not watch['problems']
    result = {'completed': dt.datetime.now(dt.timezone.utc).isoformat(),
              'keystone_version': '29.0.2', 'sqlalchemy_version': '2.0.46', 'oslo_db_version': '18.0.0',
              'cause': 'Lazy federation Query executed after session close; connection return depended on garbage collection.',
              'fix': 'Materialize SELECT using .all() inside session_for_read.',
              'local_vendor_patch': json.loads((STATE / 'patch.json').read_text()),
              'pool_configuration_unchanged': pool, 'master_process_preserved': True,
              'workers_updated': 16, 'pool_errors_after_rollout': pool_errors,
              'before_live_select': json.loads((STATE / 'live-select-original.json').read_text()),
              'after_live_select': json.loads((STATE / 'live-select-installed.json').read_text()),
              'after_isolated': json.loads((STATE / 'isolated-installed.json').read_text()),
              'canary_http': json.loads((STATE / 'canary-http-120.json').read_text()),
              'canary_pool': json.loads((STATE / 'canary-pool.json').read_text()),
              'production_http': json.loads((STATE / 'production-http-160.json').read_text()),
              'functional_checks': checks, 'continuity': continuity,
              'watch': watch, 'production_services': states, 'student_vms': compute,
              'temporary_qa_secrets_removed': True,
              'upstream_reference': 'https://lists.ubuntu.com/archives/ubuntu-openstack-bugs/2026-June/040128.html'}
    raw = json.dumps(result, ensure_ascii=False, indent=2)
    (STATE / 'final-summary.json').write_text(raw)
    (STATE / 'final-summary.json').chmod(0o600)
    export = Path('/tmp/UCcloud_Keystone_검증결과_20261026.json')
    export.write_text(raw)
    export.chmod(0o644)
    print(raw)

if __name__ == '__main__':
    main()
