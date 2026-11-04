"""Read-only service preflight plus a pinned-host-key recovery check file."""
import datetime as dt
import hashlib
import json
from pathlib import Path
import shlex
import subprocess
import sys
import time
import runpy

STATE = Path('/var/lib/uccloud-tailnet-acl-20261026')

def run(args, **kwargs):
    return subprocess.check_output(args, text=True, timeout=30, **kwargs).strip()

def remote(host, code):
    return run(['ssh', '-n', '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes',
                'root@' + host, 'python3 -c ' + shlex.quote(code)])

def save(name, data):
    STATE.mkdir(mode=0o700, exist_ok=True)
    p = STATE / name
    p.write_text(json.dumps(data, indent=2))
    p.chmod(0o600)

def prepare():
    observed = ' '.join(Path('/etc/ssh/ssh_host_ed25519_key.pub').read_text().split()[:2])
    fingerprint = run(['ssh-keygen', '-lf', '/etc/ssh/ssh_host_ed25519_key.pub']).split()[1]
    assert fingerprint == 'SHA256:wlG3g5N9E8RDcankuTWaFLNUyqtKLpZ3Qn2fuAa37sc'
    # Public host key is read from the already authenticated controller, not a network scan.
    code = '''from pathlib import Path; p=Path('/var/lib/uccloud-tailnet-acl-20261026'); p.mkdir(mode=0o700,exist_ok=True); k=p/'controller-known-hosts'; value='172.29.236.11 %s\\n'; assert not k.exists() or k.read_text()==value; k.write_text(value); k.chmod(0o600); print('PINNED_CONTROLLER_HOST_KEY_READY')''' % observed
    assert remote('172.29.236.12', code) == 'PINNED_CONTROLLER_HOST_KEY_READY'
    status = json.loads(run(['tailscale', 'status', '--json']))
    nodes = [status['Self']] + [v for v in status.get('Peer', {}).values() if not v.get('ShareeNode')]
    inventory = [{k: n.get(k) for k in ['HostName', 'DNSName', 'TailscaleIPs', 'Online', 'Tags']} for n in nodes]
    probe = runpy.run_path('/var/lib/uccloud-maintenance/20261008/public_funnel_probe.py')
    assert probe['public_get']('/api/health', probe['public_addresses']())[0] == 200
    data = {'time': dt.datetime.now(dt.timezone.utc).isoformat(), 'policy_applied': False,
            'controller_host_key_pinned': True, 'controller_host_key_fingerprint': fingerprint,
            'public_https_before': 200, 'serve_before': json.loads(run(['tailscale', 'serve', 'status', '--json'])),
            'nodes': inventory}
    save('preflight.json', data)
    print(json.dumps({'host_key_pinned': True, 'public_https_before': 200, 'policy_applied': False}))

if __name__ == '__main__':
    prepare()
