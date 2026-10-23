"""Restart original VMs with new XML/QEMU, verify guest/runtime gates, leave placement disabled."""
import json,runpy,subprocess,sys,time
from pathlib import Path
ROOT=Path('/var/lib/uccloud-maintenance/20261008')
m=runpy.run_path(str(ROOT/'maintenance.py'));m['quarantine_required']();c=m['admin']()
host=sys.argv[1];assert host in m['HOSTS'] and host not in ('comsv-01','comsv-04')
records=json.loads((ROOT/(host+'-instances-before.json')).read_text())
probe="""import os,subprocess
from pathlib import Path
assert os.uname().release=='7.0.0-38-generic'
assert all((Path('/sys/module/kvm_intel/parameters')/n).read_text().strip()=='N' for n in ('nested','enable_shadow_vmcs'))
assert all(subprocess.run(['systemctl','is-active','--quiet',n]).returncode==0 for n in ('nova-compute','libvirtd','openvswitch-switch','ovn-controller'))
print('REBOOTED_KERNEL_POLICY_AND_SERVICES_PASS')
"""
print(m['python_node'](host,probe),flush=True)
for record in records:
    server=c.compute.get_server(record['id']);assert server.compute_host==host
    assert server.image.id==record['image']['id']
    assert all((server.flavor or {}).get(k)==v for k,v in record['flavor'].items())
    if record['status']=='ACTIVE':
        assert server.status in ('SHUTOFF','ACTIVE') and server.task_state is None
        if server.status=='SHUTOFF':c.compute.start_server(server)
        c.compute.wait_for_server(server,wait=240)
        result=subprocess.run(['/opt/cloud-portal/.venv/bin/python',str(ROOT/'verify_existing_guest.py'),host,server.id],capture_output=True,text=True,timeout=300)
        log=ROOT/(server.id+'-guest-check.log');log.write_text(result.stdout+'\n'+result.stderr);log.chmod(0o600)
        assert result.returncode==0,'Existing VM checks failed; inspect private evidence and retain quarantine'
        print('EXISTING_USER_VM_GUEST_CHECKS_PASS_PASSWORD_UNCHANGED_TEMPORARY_KEY_REMOVED',flush=True)
    else:assert server.status=='SHUTOFF','Do not change the original power state'
report=runpy.run_path(str(ROOT/'verify_hypervisor_gate.py'))['verify'](host);assert report['passed']
m['record'](host,state='VM_REFRESHED',existing_guests='PASS',reboot_verified=True)
print(host,'ALL_ORIGINAL_VMS_REFRESHED_AND_VERIFIED_PLACEMENT_STILL_DISABLED',flush=True)
