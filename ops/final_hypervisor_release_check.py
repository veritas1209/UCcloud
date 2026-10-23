"""Read-only final operational check; preserve private completion evidence."""
import datetime
import json
import runpy
import subprocess
from pathlib import Path

ROOT = Path('/var/lib/uccloud-maintenance/20261008')
m = runpy.run_path(str(ROOT / 'maintenance.py'))
c = m['admin']()
gate = runpy.run_path(str(ROOT / 'verify_hypervisor_gate.py'))
services = list(c.compute.services(binary='nova-compute'))
checks = {
    'exact_compute_inventory': {s.host for s in services} == set(m['HOSTS']),
    'scheduling_enabled_and_up': all(s.status == 'enabled' and s.state == 'up' and not s.is_forced_down for s in services),
}
state = json.loads((ROOT / 'state.json').read_text())
monitors = {}
for host in m['HOSTS']:
    checks[host + ':compliant'] = state['hosts'][host]['state'] == 'COMPLIANT'
    checks[host + ':runtime'] = gate['verify'](host)['passed']
    report = json.loads(m['node'](host, 'cat /var/lib/uccloud-hypervisor-monitor/report.json', timeout=15))
    age = (datetime.datetime.now(datetime.UTC) - datetime.datetime.fromisoformat(report['timestamp'])).total_seconds()
    checks[host + ':monitor'] = not report['latched_alerts'] and 0 <= age < 300
    monitors[host] = {'age_seconds': age, 'alerts': len(report['latched_alerts'])}

integration = json.loads((ROOT / 'integration-gate.json').read_text())
required = ('canary_boot', 'canary_serial', 'canary_network', 'canary_file_io', 'snapshot', 'cold_migration', 'controller_reboot', 'web_ssh', 'public_site')
checks['integration'] = all(integration.get(key) == 'PASS' for key in required)
guests = json.loads((ROOT / 'guest-gate.json').read_text())
canary = json.loads((ROOT / 'canary-vm.json').read_text())['id']
original = {uuid for uuid, item in guests.items() if item.get('inspection_key_removed') == 'PASS'}
servers = {s.id: s for s in c.compute.servers(all_projects=True, details=True)}
checks['original_five_active'] = len(original) == 5 and all(uuid in servers and servers[uuid].status == 'ACTIVE' for uuid in original)
checks['canary_active'] = canary in servers and servers[canary].status == 'ACTIVE'
checks['inspection_private_key_destroyed'] = not (ROOT / 'guest-inspection-key').exists() and not (ROOT / 'guest-inspection-key.pub').exists()
web = json.loads((ROOT / 'web-ssh-after-reboot.json').read_text())
checks['user_confirmed_browser_ssh'] = web['passed'] and web.get('temporary_maintenance_session_issued') is False
rc = subprocess.run(['/opt/cloud-portal/.venv/bin/python', str(ROOT / 'verify_controller_restart.py'), 'after-reboot'], capture_output=True, text=True, timeout=240)
checks['controller_live'] = rc.returncode == 0 and json.loads((ROOT / 'controller-after-reboot.json').read_text())['passed']
report = {
    'time': datetime.datetime.now(datetime.UTC).isoformat(),
    'checks': checks,
    'passed': all(checks.values()),
    'compute_services': [{'host': s.host, 'status': s.status, 'state': s.state, 'forced_down': s.is_forced_down} for s in services],
    'active_original_vms': sum(uuid in servers and servers[uuid].status == 'ACTIVE' for uuid in original),
    'monitor_summary': monitors,
    'web_ssh_verification': web.get('verification_method'),
    'retained_mitigation': 'SMM off; kernel 7.0.0-38.38 is not claimed to patch CVE-2026-98164',
}
target = ROOT / 'final-release-verification.json'
target.write_text(json.dumps(report, indent=2))
target.chmod(0o600)
print('FINAL_RELEASE', 'PASS' if report['passed'] else 'FAIL', 'failed_checks', [k for k, v in checks.items() if not v])
assert report['passed'], 'Final release check failed; inspect private evidence'
