"""Only re-enable after runtime, guest and integration evidence all passes."""
import datetime,json,runpy
from pathlib import Path

ROOT=Path('/var/lib/uccloud-maintenance/20261008')
m=runpy.run_path(str(ROOT/'maintenance.py'));m['quarantine_required']();c=m['admin']()
gate=runpy.run_path(str(ROOT/'verify_hypervisor_gate.py'))
integration=json.loads((ROOT/'integration-gate.json').read_text())
required=('canary_boot','canary_serial','canary_network','canary_file_io','snapshot','cold_migration','controller_reboot','web_ssh','public_site')
assert all(integration.get(key)=='PASS' for key in required),'Incomplete integration tests: do not resume scheduling'
guests=json.loads((ROOT/'guest-gate.json').read_text())
servers=list(c.compute.servers(all_projects=True,details=True))
for server in servers:
    if server.status!='ACTIVE':continue
    result=guests.get(server.id,{})
    assert all(result.get(key)=='PASS' for key in ('vmx_absent','kvm_intel_blocked','dev_kvm_unusable','ssh_or_serial','network','file_io')),'A running guest has not passed required checks'
for host in m['HOSTS']:
    report=gate['verify'](host)
    assert report['passed'],'Runtime drift: retain quarantine'
    monitor=json.loads(m['node'](host,'cat /var/lib/uccloud-hypervisor-monitor/report.json',timeout=15))
    assert not monitor['latched_alerts'],'Review preserved security/availability events'
    assert (datetime.datetime.now(datetime.UTC)-datetime.datetime.fromisoformat(monitor['timestamp'])).total_seconds()<300
controller=json.loads((ROOT/'controller-after-reboot.json').read_text())
assert controller['passed'] and controller['boot_id']!=json.loads((ROOT/'controller-baseline.json').read_text())['boot_id']
# All tests are checked before changing any service. No forced_down manipulation.
services=list(c.compute.services(binary='nova-compute'))
try:
    for service in services:c.compute.enable_service(service)
    assert all(s.status=='enabled' and not s.is_forced_down for s in c.compute.services(binary='nova-compute'))
except Exception:
    restore_failed=[]
    for service in services:
        try:c.compute.disable_service(service,disabled_reason='UCcloud final scheduling resume failed; retain quarantine')
        except Exception:restore_failed.append(service.host)
    p=ROOT/'scheduler-resume-failure.json';p.write_text(json.dumps({'rollback_failed_hosts':restore_failed}));p.chmod(0o600)
    raise RuntimeError('Scheduling resume failed; quarantine restoration requires verification') from None
for service in services:m['record'](service.host,state='COMPLIANT',scheduler_enabled=True)
print('ALL_FOUR_COMPLIANT_COMPUTES_REENABLED')
