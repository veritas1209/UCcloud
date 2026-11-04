"""Non-mutating recurrence checks; never restarts services or changes packages."""
import datetime as dt
import hashlib
import json
from pathlib import Path
import subprocess

STATE = Path('/var/lib/uccloud-keystone-pool-20261026')
CONTAINER = 'controller1-keystone-container-301f3fef'
SOURCE = Path('/var/lib/lxc') / CONTAINER / 'rootfs/openstack/venvs/keystone-33.0.1/lib/python3.14/site-packages/keystone/federation/backends/sql.py'

def run(args):
    return subprocess.check_output(args, text=True, timeout=15).strip()

def main():
    result = {'checked': dt.datetime.now(dt.timezone.utc).isoformat(), 'problems': []}
    expected = json.loads((STATE / 'patch.json').read_text())['after_sha256']
    result['patch_digest_matches'] = hashlib.sha256(SOURCE.read_bytes()).hexdigest() == expected
    if not result['patch_digest_matches']:
        result['problems'].append('Keystone package source changed; revalidate connection lifetime fix before another rollout.')
    lines = run(['lxc-attach', '-n', CONTAINER, '--', 'journalctl', '-u', 'keystone-wsgi-public',
                 '--since', '-2 minutes', '--no-pager', '-o', 'cat']).splitlines()
    result['pool_timeouts_last_2m'] = sum('QueuePool limit' in line for line in lines)
    if result['pool_timeouts_last_2m']:
        result['problems'].append('Keystone connection pool timeout detected.')
    sql = '''SELECT COUNT(*) FROM information_schema.PROCESSLIST WHERE DB='keystone';
SELECT COUNT(*) FROM information_schema.INNODB_TRX t JOIN information_schema.PROCESSLIST p ON t.trx_mysql_thread_id=p.ID WHERE p.DB='keystone';'''
    values = run(['lxc-attach', '-n', 'controller1-galera-container-e76712d7', '--',
                  'mariadb', '--batch', '--skip-column-names', '-e', sql]).splitlines()
    result['keystone_db_connections'] = int(values[0])
    result['keystone_open_transactions'] = int(values[1])
    if result['keystone_db_connections'] > 100:
        result['problems'].append('Keystone DB connections exceed the investigated baseline.')
    previous = json.loads((STATE / 'watch.json').read_text()) if (STATE / 'watch.json').exists() else {}
    temporary = STATE / 'watch.next'
    temporary.write_text(json.dumps(result, indent=2))
    temporary.chmod(0o600)
    temporary.replace(STATE / 'watch.json')
    if result['problems'] or previous.get('problems') != result['problems']:
        print(json.dumps(result), flush=True)
    return 1 if result['problems'] else 0

if __name__ == '__main__':
    raise SystemExit(main())
