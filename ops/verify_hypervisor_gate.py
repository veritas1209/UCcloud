"""Fail-closed maintenance evidence gate; never enables a compute service itself."""
import json,runpy
from pathlib import Path

m=runpy.run_path('/var/lib/uccloud-maintenance/20261008/maintenance.py')
STORE=Path('/var/lib/uccloud-maintenance/20261008')
RELEASE='1:10.2.1+ds-1ubuntu3.2+uccloud1'
def verify(host):
    result=json.loads(m['python_node'](host,m['AUDIT'],timeout=240))
    checks={}
    checks['kernel']=result['kernel']=='7.0.0-38-generic' and result['kernel_package']=='7.0.0-38.38'
    checks['nested_off']=result['nested']=='N' and result['enable_shadow_vmcs']=='N'
    checks['cpu_policy']='-vmx' in result['cpu_flags'].split(',')
    checks['central_driver']=result['compute_driver']=='libvirt.uccloud_security.SecurityLibvirtDriver'
    checks['qemu_patched_package']=result['qemu_package']==RELEASE
    checks['serial_enabled']=result['serial_enabled'].lower()=='true'
    expected={server.id for server in m['admin']().compute.servers(all_projects=True,details=True) if server.compute_host==host and server.status=='ACTIVE'}
    actual={vm['uuid'] for vm in result['vms'] if vm['state']=='running'}
    checks['running_inventory_matches_api']=actual==expected
    checks['recognized_domain_states']=all(vm['state'] in ('running','shut off') for vm in result['vms'])
    state=json.loads((STORE/'state.json').read_text())['hosts'][host]
    old_pids=set(state.get('processes_before',{}).get('pids',[]))
    boot_id=m['node'](host,'cat /proc/sys/kernel/random/boot_id').strip()
    for vm in result['vms']:
        prefix=vm['uuid']+':'
        checks[prefix+'xml_smm_off']=vm['smm']=={'state':'off'} and vm['inactive_smm']=={'state':'off'}
        checks[prefix+'xml_vmx_disabled']=vm['cpu_vmx']==[{'policy':'disable','name':'vmx'}]
        checks[prefix+'devices']=not vm['filesystem'] and vm['hostdev']==0
        if vm['state']!='running':continue
        checks[prefix+'effective_smm_off']=bool(vm.get('effective_smm')) and all(s['value'] is False for s in vm['effective_smm'])
        checks[prefix+'effective_vmx_off']=bool(vm.get('effective_vmx')) and all(flag is False for flag in vm['effective_vmx'])
        checks[prefix+'query_version']=RELEASE in str(vm['qmp_version'])
        old_process=boot_id==state.get('processes_before',{}).get('boot_id') and vm['pid'] in old_pids
        checks[prefix+'process_refreshed']=not old_process and not vm['exe_deleted'] and vm['running_exe_sha256']==vm['installed_exe_sha256'] and not vm['deleted_qemu_modules']
        isolation=vm['isolation']
        checks[prefix+'process_isolation']=vm['qemu_user']=='libvirt-qemu' and isolation['Seccomp']=='2' and isolation['NoNewPrivs']=='1' and int(isolation['CapEff'],16)==0 and '(enforce)' in vm['apparmor'] and vm['mount_namespace_isolated'] and vm['cgroup_v2'] and vm['private_cgroup']
    status={'host':host,'checks':checks,'passed':all(checks.values()),'audit':result}
    dest=STORE/(host+'-gate.json');dest.write_text(json.dumps(status,indent=2));dest.chmod(0o600)
    print(host,'RUNTIME_GATE', 'PASS' if status['passed'] else 'FAIL', 'failed_checks',[key for key,value in checks.items() if not value])
    return status
if __name__=='__main__':
    import sys
    m['quarantine_required']()
    for host in sys.argv[1:]:
        if host not in m['HOSTS']:raise ValueError('Unknown compute')
        verify(host)
