"""Bounded wait for a compute's new kernel, then verify original VM recovery."""
import json,runpy,subprocess,sys,time
from pathlib import Path
ROOT=Path('/var/lib/uccloud-maintenance/20261008')
m=runpy.run_path(str(ROOT/'maintenance.py'));m['quarantine_required']()
host=sys.argv[1];assert host in m['HOSTS'] and host not in ('comsv-01','comsv-04')
state=json.loads((ROOT/'state.json').read_text())['hosts'][host]
assert state['guest_inspection_keys_prepared'] and state['offline_disks_backed_up']
deadline=time.monotonic()+600
while time.monotonic()<deadline:
    try:
        probe="import os,subprocess\nassert os.uname().release=='7.0.0-38-generic'\nassert all(subprocess.run(['systemctl','is-active','--quiet',n]).returncode==0 for n in ('nova-compute','libvirtd','openvswitch-switch','ovn-controller'))\nprint('READY')"
        if m['python_node'](host,probe,timeout=45).strip()=='READY':break
    except RuntimeError:pass
    time.sleep(15)
else:raise RuntimeError('Compute did not recover in the bounded reboot window; retain quarantine')
print(host,'NEW_KERNEL_AND_SERVICES_READY',flush=True)
result=subprocess.run(['/opt/cloud-portal/.venv/bin/python',str(ROOT/'restore_compute_after_reboot.py'),host],capture_output=True,text=True,timeout=900)
p=ROOT/(host+'-post-reboot-restore.log');assert not p.exists();p.write_text(result.stdout+'\n'+result.stderr);p.chmod(0o600)
assert result.returncode==0,'Post-reboot checks failed; retain quarantine and inspect private evidence'
print(host,'ORIGINAL_GUESTS_AND_RUNTIME_PASS',flush=True)
result=subprocess.run(['/opt/cloud-portal/.venv/bin/python',str(ROOT/'install_hypervisor_watchdog.py'),host],capture_output=True,text=True,timeout=300)
p=ROOT/(host+'-watchdog-install.log');assert not p.exists();p.write_text(result.stdout+'\n'+result.stderr);p.chmod(0o600)
assert result.returncode==0,'Watchdog checks failed; retain quarantine'
print(host,'POST_REBOOT_ALL_CHECKS_PASS_PLACEMENT_STILL_DISABLED',flush=True)
