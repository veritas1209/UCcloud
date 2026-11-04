"""Deploy only the Node2Shell installer/catalog update, preserving user VM data."""
import datetime
import hashlib
import json
import os
import runpy
import shutil
import subprocess
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path('/opt/cloud-portal')
STAGE = Path(Path('/run/uccloud-node-security-stage-20261024').read_text().strip())
assert str(STAGE).startswith('/var/lib/uccloud-node-security-20261024-')
assert STAGE.is_dir() and not STAGE.is_symlink() and STAGE.stat().st_uid == 0
ORIGINAL = {
    'backend/guest_software.py': '831c380024df69d3bf2f5d2b595e62473e8bdc2b4950a8e985d9baf2916d2f0f',
    'backend/software.py': 'ce9720c4f1a41918dd6fb8fab51d440874d213e2a84dc8eabadc986283c691d2',
}
RELEASE = {
    'backend/guest_software.py': '83e9a4f68e1df4e9116fa32e054a842c8cfd4523294fabab2bdb7df3ec68b7ee',
    'backend/software.py': '6d35194b057c7ed7a40036ad391c333777847a7e17954892db805aa085c66ff4',
}
UNITS = ('cloud-portal', 'cloud-portal-public')


def digest(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def health():
    for unit, port, host in [('cloud-portal', 8088, 'localhost'), ('cloud-portal-public', 18080, 'comsv-03.tail5819db.ts.net')]:
        subprocess.run(['systemctl', 'is-active', '--quiet', unit], check=True)
        request = urllib.request.Request(f'http://127.0.0.1:{port}/api/health', headers={'Host': host})
        for attempt in range(30):
            try:
                with urllib.request.urlopen(request, timeout=4) as response:
                    assert response.status == 200
                break
            except Exception:
                if attempt == 29:
                    raise RuntimeError('Portal health check failed') from None
                time.sleep(2)


for name, expected in ORIGINAL.items():
    assert digest(ROOT / name) == expected, 'Production source changed; do not overwrite'
for name, expected in RELEASE.items():
    assert digest(STAGE / name) == expected, 'Staged source changed'
    compile((STAGE / name).read_bytes(), name, 'exec')
assert (STAGE / 'repository-check.json').is_file(), 'Repository availability check missing'
backup = Path(tempfile.mkdtemp(prefix='uccloud-node-security-20261024-', dir='/var/backups'))
backup.chmod(0o700)
for name in ORIGINAL:
    target = backup / name
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(ROOT / name, target)
    target.chmod(0o600)
print('SOURCE_BACKUP', backup, flush=True)
try:
    subprocess.run(['systemctl', 'stop', *UNITS], check=True)
    for name in RELEASE:
        target = ROOT / name
        temporary = target.with_name(target.name + '.node-security-next')
        assert not temporary.exists()
        shutil.copyfile(STAGE / name, temporary)
        temporary.chmod(0o644)
        temporary.replace(target)
        assert digest(target) == RELEASE[name]
    subprocess.run(['systemctl', 'start', *UNITS], check=True)
    health()
    tests = subprocess.run(['/opt/cloud-portal/.venv/bin/python', str(STAGE / 'tests/test_node_security.py'), '-v'],
        cwd=ROOT, env=dict(os.environ, PYTHONPATH=str(ROOT)), capture_output=True, text=True, timeout=90)
    (backup / 'production-policy-tests.log').write_text(tests.stdout + tests.stderr)
    (backup / 'production-policy-tests.log').chmod(0o600)
    assert tests.returncode == 0, 'Deployed regression tests failed'
    public = runpy.run_path('/var/lib/uccloud-maintenance/20261008/public_funnel_probe.py')
    addresses = public['public_addresses']()
    for route in ('/', '/api/health', '/terminal'):
        status, _ = public['public_get'](route, addresses)
        assert status == 200, 'Public HTTPS verification failed'
    report = {'time': datetime.datetime.now(datetime.UTC).isoformat(), 'passed': True,
        'release_commit': 'c2b3c66768784106a4d5a45b8675744e42ca22b9',
        'original': ORIGINAL, 'release': RELEASE, 'tests': 7,
        'repository_candidates': json.loads((STAGE / 'repository-check.json').read_text()),
        'health': 'Both portal services and real public HTTPS passed',
        'existing_vms_updated': False, 'db_schema_changed': False}
    (backup / 'deployment.json').write_text(json.dumps(report, indent=2))
    (backup / 'deployment.json').chmod(0o600)
except Exception:
    subprocess.run(['systemctl', 'stop', *UNITS], check=False)
    for name in ORIGINAL:
        shutil.copyfile(backup / name, ROOT / name)
        (ROOT / name).chmod(0o644)
    subprocess.run(['systemctl', 'start', *UNITS], check=True)
    health()
    print('DEPLOYMENT_FAILED_ORIGINAL_SOURCE_RESTORED', flush=True)
    raise
print('NODE_SECURITY_DEPLOYED_HEALTH_AND_7_TESTS_PASS_EXISTING_VMS_UNCHANGED', flush=True)
