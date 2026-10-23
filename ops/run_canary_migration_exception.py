"""Install and always revoke the approved, expiring single-VM scheduler exception."""
import base64,json,os,runpy,subprocess,sys
from pathlib import Path
ROOT=Path('/var/lib/uccloud-maintenance/20261008')
attempt=int(sys.argv[1]) if len(sys.argv)>1 else 1;assert 1<=attempt<=2
m=runpy.run_path(str(ROOT/'maintenance.py'));m['quarantine_required']();c=m['admin']()
state=json.loads((ROOT/'canary-vm.json').read_text());server=c.compute.get_server(state['id'])
assert server.status=='ACTIVE' and server.task_state is None and server.compute_host=='comsv-01'
assert server.project_id==c.current_project_id and server.metadata.get('uccloud_security_canary')==state['marker']
prior=json.loads((ROOT/'canary-cold-migration.json').read_text())
assert prior['placement_restored'] and prior['request_spec_restored'] and 'checks' not in prior
loaded=subprocess.check_output(['systemctl','show','uccloud-canary-cold-migration','-p','LoadState','--value'],text=True).strip()
if loaded!='not-found':subprocess.run(['systemctl','stop','uccloud-canary-cold-migration'],check=True,timeout=30)
archive=ROOT/('canary-cold-migration-scheduling-failed.json' if attempt==1 else 'canary-cold-migration-filter-attempt1-failed.json');assert not archive.exists()
(ROOT/'canary-cold-migration.json').rename(archive)
for host in ('comsv-01','comsv-04'):
    assert runpy.run_path(str(ROOT/'verify_hypervisor_gate.py'))['verify'](host)['passed']
container='controller1-nova-api-container-cb1ec29d'
base='/usr/local/lib/uccloud-maintenance'+('' if attempt==1 else '-'+str(attempt))
permit={'operation':'cold_migration','instance_uuid':server.id,'project_id':server.project_id,'host':'comsv-04','runtime_gate':'PASS'}
payload={name:base64.b64encode((ROOT/name).read_bytes()).decode() for name in ('temporary_canary_scheduler.py','nova_canary_compute_filter.py')}
payload['canary-permit.json']=base64.b64encode(json.dumps(permit).encode()).decode()
source="from pathlib import Path\nimport json,base64\np=Path("+repr(base)+");p.mkdir(mode=0o755,exist_ok=True)\nd=json.loads("+repr(json.dumps(payload))+")\nfor n,v in d.items():\n f=p/n;assert not f.exists();f.write_bytes(base64.b64decode(v));f.chmod(0o600)\n"
result=subprocess.run(['lxc-attach','-n',container,'--','python3','-'],input=source,text=True,capture_output=True,timeout=30)
assert result.returncode==0,'Exception staging failed; no permission armed'
def setup(mode):
    arg=base+'/nova_canary_compute_filter.py' if mode=='install' else base+'/canary-permit.json'
    result=subprocess.run(['lxc-attach','-n',container,'--','/openstack/venvs/nova-33.0.1/bin/python',base+'/temporary_canary_scheduler.py',mode,arg,str(attempt)],capture_output=True,text=True,timeout=300)
    log=ROOT/('canary-scheduler-'+mode+'-attempt'+str(attempt)+'.log');log.write_text('returncode='+str(result.returncode)+'\n'+result.stdout+'\n'+result.stderr);log.chmod(0o600)
    assert result.returncode==0,'Temporary scheduler '+mode+' failed; review private log'
    print('SCHEDULER_EXCEPTION_'+mode.upper()+'_PASS',flush=True)
success=False;cleanup_errors=[]
try:
    setup('install');setup('arm')
    result=subprocess.run(['/opt/cloud-portal/.venv/bin/python',str(ROOT/'test_canary_cold_migration.py')],capture_output=True,text=True,timeout=1100,
        env=dict(os.environ,UCCLOUD_CANARY_FILTER_EXCEPTION='1'))
    log=ROOT/('canary-cold-migration-retry-attempt'+str(attempt)+'.log');log.write_text(result.stdout+'\n'+result.stderr);log.chmod(0o600)
    success=result.returncode==0
finally:
    # Independently restore global placement even if the child times out.
    try:
        rp=next(p for p in c.placement.resource_providers() if p.name=='comsv-04')
        current=c.placement.get_resource_provider_trait(rp)
        c.placement.set_resource_provider_trait(current,traits=sorted(set(current.traits)|{'COMPUTE_STATUS_DISABLED'}),resource_provider_generation=current.resource_provider_generation)
        m['quarantine_required']()
    except Exception:cleanup_errors.append('placement')
    try:
        source="from nova import config,objects,context\nconfig.parse_args(['uccloud-maintenance','--config-file','/etc/nova/nova.conf']);objects.register_all()\nr=objects.RequestSpec.get_by_instance_uuid(context.get_admin_context(),"+repr(server.id)+")\nr.force_hosts="+repr(prior['request_spec_before']['force_hosts'])+";r.force_nodes="+repr(prior['request_spec_before']['force_nodes'])+";r.save()\n"
        result=subprocess.run(['lxc-attach','-n',container,'--','/openstack/venvs/nova-33.0.1/bin/python','-'],input=source,capture_output=True,text=True,timeout=45)
        assert result.returncode==0
    except Exception:cleanup_errors.append('canary_request_spec')
    try:setup('restore')
    except Exception:cleanup_errors.append('scheduler')
    report={'test_passed':success,'cleanup_errors':cleanup_errors,'scheduler_services_remain_disabled':all(s.status=='disabled' for s in c.compute.services(binary='nova-compute'))}
    p=ROOT/('canary-exception-cleanup-attempt'+str(attempt)+'.json');p.write_text(json.dumps(report,indent=2));p.chmod(0o600)
    print('CANARY_MIGRATION_RESULT','PASS' if success else 'FAIL','EXCEPTION_CLEANUP','PASS' if not cleanup_errors else 'REVIEW_REQUIRED',flush=True)
assert success and not cleanup_errors,'Migration evidence incomplete; retain quarantine'
