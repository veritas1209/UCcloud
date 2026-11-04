"""Phased blue/green privilege separation. Never prints credential values."""
from __future__ import annotations
import argparse
import datetime
import hashlib
import json
import os
import pwd
import re
import shlex
import shutil
import socket
import sqlite3
import subprocess
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path('/opt/cloud-portal')
RELEASE = Path('/opt/uccloud-releases/provisioner-20261026')
STATE = Path('/var/lib/uccloud-provisioner-deploy-20261026')
CONF = Path('/etc/cloud-portal')
UNITS = Path('/etc/systemd/system')
SOCKET = '/run/uccloud-provisioner/control.sock'
PUBLIC_HOST = 'comsv-03.tail5819db.ts.net'
CHANGED = ['backend/app.py', 'backend/openstack_service.py', 'backend/vm_backups.py', 'ops/check_cloud_health.py']
ORIGINAL = {'backend/openstack_service.py': '6583166eed8d07e9e9d41d505dbe4a83843832210defb84831397e296be05dac',
            'backend/vm_backups.py': '42587915ab02f49cbc88519485c813510f59c3c85ced1c8c39616d84788000ca'}


def run(*args, **kw):
    return subprocess.run(args, check=True, capture_output=True, text=True, **kw).stdout.strip()


def write(path, value, mode=0o600):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.provisioner-next')
    temporary.write_text(value, encoding='utf-8')
    temporary.chmod(mode)
    temporary.replace(path)


def load_env(path):
    result = {}
    for line in path.read_text().splitlines():
        if not line.strip() or line.lstrip().startswith('#'):
            continue
        items = shlex.split(line, comments=False)
        if len(items) != 1 or '=' not in items[0]:
            raise RuntimeError('Unsupported environment syntax')
        key, value = items[0].split('=', 1)
        if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', key):
            raise RuntimeError('Unsupported environment key')
        result[key] = value
    return result


def env_text(env):
    return ''.join(key + '=' + json.dumps(value, ensure_ascii=True) + '\n' for key, value in sorted(env.items()))


def health(port, public=True):
    req = urllib.request.Request(f'http://127.0.0.1:{port}/api/health', headers={'Host': PUBLIC_HOST if public else 'localhost'})
    with urllib.request.urlopen(req, timeout=5) as response:
        assert response.status == 200


def wait_health(port, public=True):
    for _ in range(30):
        try:
            health(port, public)
            return
        except Exception:
            time.sleep(1)
    raise RuntimeError('Candidate health failed')


def private_request(payload, uid=None):
    # Run the client as the ordinary web UID to verify actual socket permissions.
    program = "import json; from backend.provisioner_client import request; p=json.loads(__import__('sys').argv[1]); op=p.pop('op'); print(json.dumps(request(op,**p)))"
    args = ['env', 'PYTHONPATH=' + str(RELEASE), 'PORTAL_PROVISIONER_SOCKET=' + SOCKET,
            str(ROOT/'.venv/bin/python'), '-c', program, json.dumps(payload)]
    if uid:
        args = ['runuser', '-u', uid, '--'] + args
    return json.loads(run(*args, cwd=RELEASE))


def web_unit(original, candidate, port):
    text = (UNITS/(original+'.service')).read_text()
    text = text.replace('/opt/cloud-portal', str(RELEASE))
    text = text.replace('EnvironmentFile=/etc/cloud-portal/portal.env', 'EnvironmentFile=/etc/cloud-portal/runtime.env')
    entry = 'backend.public_app:app' if 'public' in original else 'backend.app:app'
    command = f'ExecStart={ROOT}/.venv/bin/uvicorn {entry} --host 127.0.0.1 --port {port} --proxy-headers --forwarded-allow-ips 127.0.0.1 --ws websockets --ws-max-size 65536 --ws-max-queue 8'
    text = re.sub(r'^ExecStart=.*$', command, text, flags=re.M)
    text = text.replace('[Service]', '[Service]\nInaccessiblePaths=/etc/cloud-portal\nTimeoutStopSec=300', 1)
    write(UNITS/(candidate+'.service'), text, 0o644)
    source = UNITS/(original+'.service.d')
    target = UNITS/(candidate+'.service.d')
    if source.is_dir():
        target.mkdir(exist_ok=True)
        for path in source.glob('*.conf'):
            value = path.read_text().replace('/opt/cloud-portal', str(RELEASE))
            if 'OS_PASSWORD' in value or 'OS_USERNAME' in value or 'portal.env' in value:
                raise RuntimeError('Unexpected privileged web override')
            write(target/path.name, value, 0o644)


def edge_config(legacy=False):
    binds = [('public_next', 19080, 18180), ('private_next', 19088, 8188)]
    if legacy:
        binds += [('public_legacy', 18080, 18180), ('private_legacy', 8088, 8188)]
    text = '''global
    log stdout format raw local0
    maxconn 512
defaults
    log global
    mode http
    timeout connect 5s
    timeout client 1h
    timeout server 1h
    timeout tunnel 1h
'''
    for name, port, backend in binds:
        text += f'\nlisten {name}\n    bind 127.0.0.1:{port}\n    server app 127.0.0.1:{backend} check\n'
    return text


def prepare():
    if (STATE/'prepared.json').exists():
        raise RuntimeError('Already prepared; inspect state instead of overwriting')
    STATE.mkdir(mode=0o700)
    for name, expected in ORIGINAL.items():
        assert hashlib.sha256((ROOT/name).read_bytes()).hexdigest() == expected, 'Production changed'
    backup = Path(tempfile.mkdtemp(prefix='uccloud-before-provisioner-20261026-', dir='/var/backups'))
    backup.chmod(0o700)
    for path in [CONF, UNITS/'cloud-portal.service', UNITS/'cloud-portal-public.service',
                 UNITS/'cloud-portal-cloud-health.service', Path('/etc/haproxy/cloud-portal-console.cfg')]:
        target = backup/str(path).lstrip('/')
        if path.is_dir():
            shutil.copytree(path, target)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
    for name in CHANGED:
        target = backup/'source'/name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT/name, target)
    for name in ['cloud-portal', 'cloud-portal-public', 'cloud-portal-cloud-health']:
        directory = UNITS/(name+'.service.d')
        if directory.exists():
            shutil.copytree(directory, backup/'dropins'/name)
    write(STATE/'backup-path', str(backup))
    run(str(ROOT/'.venv/bin/python'), str(ROOT/'ops/backup_portal_db.py'))
    try:
        pwd.getpwnam('uccloud-provisioner')
    except KeyError:
        run('useradd', '--system', '--no-create-home', '--home-dir', '/nonexistent', '--shell', '/usr/sbin/nologin', 'uccloud-provisioner')
    original = load_env(CONF/'portal.env')
    privileged = {key:value for key,value in original.items() if key.startswith('OS_') or key in
                  {'PORTAL_SHARED_ROUTER','PORTAL_DNS_SERVERS','PORTAL_BROWSER_SSH_SOURCE','PORTAL_DB'}}
    # The SSH source is commonly supplied by a systemd drop-in, not portal.env.
    for directory in [UNITS/'cloud-portal.service.d', UNITS/'cloud-portal-public.service.d']:
        for path in directory.glob('*.conf'):
            for line in path.read_text().splitlines():
                if line.startswith('Environment='):
                    for item in shlex.split(line.partition('=')[2]):
                        key, sep, value = item.partition('=')
                        if sep and key == 'PORTAL_BROWSER_SSH_SOURCE':
                            privileged[key] = value
    assert privileged.get('OS_USERNAME') and privileged.get('OS_PASSWORD')
    write(CONF/'provisioner.env', env_text(privileged))
    runtime = {key:value for key,value in original.items() if key not in
               {'OS_USERNAME','OS_PASSWORD','OS_PROJECT_NAME','PORTAL_ADMIN_PASSWORD'}}
    runtime['PORTAL_PROVISIONER_SOCKET'] = SOCKET
    write(CONF/'runtime.env', env_text(runtime))
    # Dedicated UID gets read-only access to the registry and any WAL sidecars.
    db = Path(original.get('PORTAL_DB','/var/lib/cloud-portal/portal.db'))
    run('getfacl', '-p', str(db.parent), str(db))
    write(STATE/'registry-acl-before', run('getfacl', '-p', str(db.parent), str(db)))
    run('setfacl', '-m', 'u:uccloud-provisioner:r-x', str(db.parent))
    run('setfacl', '-m', 'd:u:uccloud-provisioner:r-x', str(db.parent))
    for path in [db, Path(str(db)+'-wal'), Path(str(db)+'-shm')]:
        if path.exists():
            run('setfacl', '-m', 'u:uccloud-provisioner:r--', str(path))
    unit = (RELEASE/'ops/uccloud-provisioner.service').read_text().replace('/opt/cloud-portal',str(RELEASE))
    # Use the already-installed venv; the release's copied venv is not needed.
    unit = unit.replace(str(RELEASE/'.venv'), str(ROOT/'.venv'))
    write(UNITS/'uccloud-provisioner.service', unit, 0o644)
    web_unit('cloud-portal', 'cloud-portal-v2', 8188)
    web_unit('cloud-portal-public', 'cloud-portal-public-v2', 18180)
    write(Path('/etc/haproxy/uccloud-portal-edge.cfg'), edge_config(), 0o644)
    write(UNITS/'uccloud-portal-edge.service', '''[Unit]
Description=UCcloud stable blue-green portal entry
After=network-online.target cloud-portal-v2.service cloud-portal-public-v2.service
[Service]
Type=notify
ExecStart=/usr/sbin/haproxy -Ws -f /etc/haproxy/uccloud-portal-edge.cfg -p /run/uccloud-portal-edge.pid
ExecReload=/usr/sbin/haproxy -c -f /etc/haproxy/uccloud-portal-edge.cfg
ExecReload=/bin/kill -USR2 $MAINPID
Restart=on-failure
KillMode=mixed
TimeoutStopSec=300
[Install]
WantedBy=multi-user.target
''', 0o644)
    run('systemd-analyze','verify',str(UNITS/'uccloud-provisioner.service'),str(UNITS/'cloud-portal-v2.service'),str(UNITS/'cloud-portal-public-v2.service'),str(UNITS/'uccloud-portal-edge.service'))
    run('haproxy','-c','-f','/etc/haproxy/uccloud-portal-edge.cfg')
    run('systemctl','daemon-reload')
    run('systemctl','enable','--now','uccloud-provisioner')
    for _ in range(20):
        if Path(SOCKET).exists(): break
        time.sleep(1)
    assert private_request({'op':'health'}, 'cloud-portal') == {'healthy':True}
    run('systemctl','start','cloud-portal-v2','cloud-portal-public-v2')
    wait_health(8188,False)
    wait_health(18180)
    run('systemctl','start','uccloud-portal-edge')
    wait_health(19080)
    wait_health(19088,False)
    write(STATE/'prepared.json', json.dumps({'prepared':True,'backup':str(backup),'time':datetime.datetime.now(datetime.UTC).isoformat()}))
    print('PREPARED candidate healthy; existing portals unchanged')


def switch():
    assert (STATE/'prepared.json').exists()
    wait_health(19080)
    run('tailscale','funnel','--bg','--https=443','http://127.0.0.1:19080')
    # Console WebSocket endpoint stays unchanged; only its static assets proxy changes.
    path = Path('/etc/haproxy/cloud-portal-console.cfg')
    old = path.read_text()
    new = old.replace('127.0.0.1:18080', '127.0.0.1:19080')
    write(path,new,0o644)
    run('haproxy','-c','-f',str(path))
    # Keep the old console worker alive; no active noVNC transport is restarted.
    # Existing config is persisted for its next normal restart. Assets can still
    # load through legacy 18080 until finalize installs the stable proxy.
    run('systemctl','enable','cloud-portal-v2','cloud-portal-public-v2','uccloud-portal-edge')
    write(STATE/'switched.json', json.dumps({'switched':True,'time':datetime.datetime.now(datetime.UTC).isoformat()}))
    print('SWITCHED public requests to isolated workers; old connections preserved')


def connections(port):
    result = run('ss','-Hnt','state','established',f'( sport = :{port} )')
    return len(result.splitlines()) if result else 0


def finalize():
    assert (STATE/'switched.json').exists()
    if connections(18080) or connections(8088):
        raise RuntimeError('Legacy sessions still active; leave them running and retry later')
    wait_health(19080)
    wait_health(19088,False)
    run('systemctl','stop','cloud-portal-public','cloud-portal')
    try:
        write(Path('/etc/haproxy/uccloud-portal-edge.cfg'),edge_config(True),0o644)
        run('haproxy','-c','-f','/etc/haproxy/uccloud-portal-edge.cfg')
        run('systemctl','reload','uccloud-portal-edge')
        wait_health(18080)
        wait_health(8088,False)
    except Exception:
        run('systemctl','start','cloud-portal','cloud-portal-public')
        raise
    run('systemctl','disable','cloud-portal','cloud-portal-public')
    # Preserve familiar service names as aliases without restarting the already
    # verified workers. Alias drop-ins also reset legacy environment sources.
    for old,new in [('cloud-portal','cloud-portal-v2'),('cloud-portal-public','cloud-portal-public-v2')]:
        public_env='EnvironmentFile=/etc/cloud-portal/public.env\n' if 'public' in old else ''
        write(UNITS/(old+'.service.d/zz-privilege-separation.conf'),f'[Service]\nEnvironmentFile=\nEnvironmentFile=/etc/cloud-portal/runtime.env\n{public_env}EnvironmentFile=/etc/cloud-portal/security-flavors.env\nEnvironmentFile=/etc/cloud-portal/vm-backups-enabled.env\nInaccessiblePaths=/etc/cloud-portal\n',0o644)
        path=UNITS/(old+'.service')
        assert path.parent==Path('/etc/systemd/system') and not path.is_symlink()
        assert (Path((STATE/'backup-path').read_text())/str(path).lstrip('/')).is_file()
        path.unlink()
        path.symlink_to(new+'.service')
    # Health timer adopts the broker. Root-only maintenance/backup workers retain
    # their original private environment and are not exposed on HTTP.
    write(UNITS/'cloud-portal-cloud-health.service.d/zz-privilege-separation.conf',f'[Service]\nEnvironmentFile=\nEnvironmentFile=/etc/cloud-portal/runtime.env\nWorkingDirectory={RELEASE}\nEnvironment=PYTHONPATH={RELEASE}\nExecStart=\nExecStart={ROOT}/.venv/bin/python {RELEASE}/ops/check_cloud_health.py\nInaccessiblePaths=/etc/cloud-portal\n',0o644)
    run('systemctl','daemon-reload')
    run('systemctl','start','cloud-portal-cloud-health')
    write(STATE/'finalized.json',json.dumps({'finalized':True,'time':datetime.datetime.now(datetime.UTC).isoformat()}))
    print('FINALIZED no privileged web processes; legacy ports proxy isolated workers')


if __name__ == '__main__':
    assert os.geteuid() == 0
    parser=argparse.ArgumentParser()
    parser.add_argument('phase',choices=['prepare','switch','finalize'])
    phase=parser.parse_args().phase
    globals()[phase]()
