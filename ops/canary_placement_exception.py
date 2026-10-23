"""Requires explicit user approval: one admin canary, disabled Nova services kept."""
import json,runpy,subprocess,time
from pathlib import Path

folder=Path('/var/lib/uccloud-maintenance/20261008')
assert (folder/'canary-placement-exception-approved').exists(),'Explicit one-canary exception has not been approved'
m=runpy.run_path(str(folder/'maintenance.py'));m['quarantine_required']();c=m['admin']()
rp=next(provider for provider in c.placement.resource_providers() if provider.name=='comsv-01')
before=c.placement.get_resource_provider_trait(rp)
assert 'COMPUTE_STATUS_DISABLED' in before.traits
record=folder/'canary-placement-exception.json'
record.write_text(json.dumps({'host':rp.name,'traits_before':before.traits,'generation_before':before.resource_provider_generation,'restored':False},indent=2));record.chmod(0o600)
statefile=folder/'canary-vm.json'
if statefile.exists():
    state=json.loads(statefile.read_text());server=c.compute.get_server(state['id'])
    assert server.status=='ERROR' and not server.compute_host,'Do not replace a running or partially placed canary'
    # Retain failed VM records without overwriting prior evidence. Only one
    # canary may be active, and stop retrying after three retained failures.
    trials=list(c.compute.servers(all_projects=True))
    canaries=[s for s in trials if s.name=='uccloud-security-canary-20261008']
    assert all(s.status=='ERROR' for s in canaries),'Another canary may still be active'
    assert len(canaries)<3,'Canary retry limit reached; operator review is required'
    archive=folder/('canary-failed-'+state['id']+'.json')
    assert not archive.exists(),'Failed canary evidence already exists'
    statefile.replace(archive)  # The ERROR VM is retained, not deleted.
try:
    c.placement.set_resource_provider_trait(before,traits=[t for t in before.traits if t!='COMPUTE_STATUS_DISABLED'],resource_provider_generation=before.resource_provider_generation)
    # Only this root-owned script can force the specific canary host. ComputeFilter
    # remains enabled and all four Nova compute services stay disabled.
    process=subprocess.Popen(['/opt/cloud-portal/.venv/bin/python',str(folder/'canary_hypervisor_test.py')],cwd='/opt/cloud-portal')
    deadline=time.monotonic()+180
    while time.monotonic()<deadline:
        if statefile.exists():
            state=json.loads(statefile.read_text())
            if state.get('id'):
                server=c.compute.get_server(state['id'])
                if server.compute_host=='comsv-01' or server.status=='ERROR':break
        if process.poll() is not None:break
        time.sleep(2)
finally:
    current=c.placement.get_resource_provider_trait(rp)
    c.placement.set_resource_provider_trait(current,traits=sorted(set(current.traits)|{'COMPUTE_STATUS_DISABLED'}),resource_provider_generation=current.resource_provider_generation)
    assert 'COMPUTE_STATUS_DISABLED' in c.placement.get_resource_provider_trait(rp).traits
    m['quarantine_required']()
    report=json.loads(record.read_text());report['restored']=True;record.write_text(json.dumps(report,indent=2));record.chmod(0o600)
    print('PLACEMENT_BLOCK_RESTORED_NOVA_STILL_DISABLED',flush=True)
assert process.wait(timeout=720)==0,'Canary test failed; the host remains quarantined'
