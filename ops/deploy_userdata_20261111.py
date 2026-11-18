"""Blue/green deployment; retain old workers for existing SSH/WebSocket sessions."""
import argparse
import hashlib
import json
import subprocess
import time
import urllib.request
from pathlib import Path

OLD = Path('/opt/uccloud-releases/provisioner-20261026')
RELEASE = Path('/opt/uccloud-releases/userdata-20261111')
STATE = Path('/var/lib/uccloud-userdata-20261111')
EDGE = Path('/etc/haproxy/uccloud-portal-edge.cfg')
UNITS = [('cloud-portal-v2', 'cloud-portal-v3', 8188, 8288),
         ('cloud-portal-public-v2', 'cloud-portal-public-v3', 18180, 18280)]
BASELINE = {'backend/openstack_service.py': '00f8b393a2905bd7fc728765234855f6eb294a797f50d9a5887ba380bd071951',
            'static/app.js': '30c60518bd66c6b4917fe5a43f61098bcf4d74f594b6e8338253ae34a97c6884'}


def run(*args):
    return subprocess.check_output(args, text=True)


def health(port):
    host = 'comsv-03.tail5819db.ts.net' if port in [18280, 18281, 19080, 18080] else 'localhost'
    req = urllib.request.Request(f'http://127.0.0.1:{port}/api/health', headers={'Host': host})
    with urllib.request.urlopen(req, timeout=5) as response:
        assert response.status == 200


def prepare():
    STATE.mkdir(mode=0o700, exist_ok=True)
    for name, expected in BASELINE.items():
        assert hashlib.sha256((OLD/name).read_bytes()).hexdigest() == expected, 'Original source changed'
    assert (RELEASE/'backend/cloud_init.py').is_file()
    assert not (STATE/'edge-before.cfg').exists(), 'Deployment state already exists; inspect it'
    (STATE/'edge-before.cfg').write_bytes(EDGE.read_bytes())
    for original, candidate, before, after in UNITS:
        target = Path('/etc/systemd/system')/(candidate+'.service')
        assert not target.exists(), 'Candidate unit already exists'
        text = run('systemctl', 'cat', original)
        text = text.replace(str(OLD), str(RELEASE)).replace(f'--port {before}', f'--port {after}')
        assert f'--port {after}' in text and str(RELEASE) in text
        assert 'User=cloud-portal' in text and 'InaccessiblePaths=/etc/cloud-portal' in text
        assert 'PORTAL_ADMIN_PASSWORD=' not in text and 'OS_PASSWORD=' not in text
        target.write_text(text)
        target.chmod(0o644)
    run('systemctl', 'daemon-reload')
    run('systemctl', 'start', *(x[1] for x in UNITS))
    for _, _, _, port in UNITS:
        for attempt in range(30):
            try:
                health(port)
                break
            except Exception:
                if attempt == 29:
                    raise
                time.sleep(1)
    print('CANDIDATES_HEALTHY_OLD_PORTAL_UNCHANGED', flush=True)


def cutover():
    assert not (STATE/'switched.json').exists(), 'Already switched'
    for _, _, _, port in UNITS:
        health(port)
    before = (STATE/'edge-before.cfg').read_text()
    assert EDGE.read_text() == before, 'Edge configuration changed elsewhere'
    after = before.replace('127.0.0.1:18180 check', '127.0.0.1:18280 check').replace('127.0.0.1:8188 check', '127.0.0.1:8288 check')
    assert before != after
    candidate = STATE/'edge-candidate.cfg'
    candidate.write_text(after)
    run('haproxy', '-c', '-f', str(candidate))
    try:
        EDGE.write_text(after)
        run('systemctl', 'reload', 'uccloud-portal-edge')
        for port in [19080, 19088, 8088, 18080]:
            health(port)
        run('systemctl', 'enable', *(x[1] for x in UNITS))
        # Existing connections keep their old workers; no stop/restart here.
        run('systemctl', 'disable', *(x[0] for x in UNITS))
    except Exception:
        EDGE.write_text(before)
        run('systemctl', 'reload', 'uccloud-portal-edge')
        raise
    (STATE/'switched.json').write_text(json.dumps({'release': str(RELEASE), 'time': time.time()}))
    print('CUTOVER_HEALTHY_OLD_CONNECTIONS_RETAINED', flush=True)


def finalize():
    assert (STATE/'switched.json').exists()
    for port in [19080, 19088]:
        health(port)
    for alias, candidate in [('cloud-portal', 'cloud-portal-v3'), ('cloud-portal-public', 'cloud-portal-public-v3')]:
        link = Path('/etc/systemd/system')/(alias+'.service')
        # The approved follow-up public worker uses 18281 and public-v4.
        if alias == 'cloud-portal-public' and Path('/etc/systemd/system/cloud-portal-public-v4.service').exists():
            candidate = 'cloud-portal-public-v4'
        target = Path('/etc/systemd/system')/(candidate+'.service')
        if link.is_symlink():
            if link.resolve() == target:
                continue
            assert link.resolve() in [target, Path(str(target).replace('-v3.service', '-v2.service'))]
            link.unlink()
        else:
            assert not link.exists(), 'Unexpected compatibility unit'
        link.symlink_to(target)
    run('systemctl', 'daemon-reload')
    connections = run('ss', '-Htn', 'state', 'established', '( sport = :8188 or sport = :18180 )')
    if connections.strip():
        print('OLD_WORKERS_RETAINED_ACTIVE_CONNECTIONS', flush=True)
        return
    run('systemctl', 'stop', *(x[0] for x in UNITS))
    for port in [19080, 19088]:
        health(port)
    print('OLD_WORKERS_DRAINED_COMPATIBILITY_ALIASES_READY', flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('phase', choices=['prepare', 'cutover', 'finalize'])
    globals()[parser.parse_args().phase]()
