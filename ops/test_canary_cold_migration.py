"""Explicitly approved, single administrator canary migration; normal placement stays blocked."""
import asyncio,json,os,runpy,subprocess,time
from pathlib import Path
import asyncssh
ROOT=Path('/var/lib/uccloud-maintenance/20261008')
assert (ROOT/'canary-cold-migration-exception-approved').exists()
m=runpy.run_path(str(ROOT/'maintenance.py'));m['quarantine_required']();c=m['admin']()
for host in ('comsv-01','comsv-04'):
    assert runpy.run_path(str(ROOT/'verify_hypervisor_gate.py'))['verify'](host)['passed']
state=json.loads((ROOT/'canary-vm.json').read_text());server=c.compute.get_server(state['id'])
assert server.status=='ACTIVE' and server.compute_host=='comsv-01'
assert server.project_id==c.current_project_id and server.metadata.get('uccloud_security_canary')==state['marker']
port=next(c.network.ports(device_id=server.id));address=port.fixed_ips[0]['ip_address']
async def checksum():
    async with asyncssh.connect(address,username='student',password=state['password'],client_keys=[],
            known_hosts=(address+' '+state['host_key']+'\n').encode(),connect_timeout=10) as ssh:
        value=await ssh.run('sudo sha256sum /var/lib/uccloud-canary/io.bin',check=True,timeout=15)
        return value.stdout.split()[0]
before_hash=asyncio.run(checksum())
container='controller1-nova-api-container-cb1ec29d'
prefix="from nova import config,objects,context\nconfig.parse_args(['uccloud-maintenance','--config-file','/etc/nova/nova.conf']);objects.register_all()\nr=objects.RequestSpec.get_by_instance_uuid(context.get_admin_context(),"+repr(server.id)+")\n"
def nova(source):
    result=subprocess.run(['lxc-attach','-n',container,'--','/openstack/venvs/nova-33.0.1/bin/python','-'],input=prefix+source,text=True,capture_output=True,timeout=45)
    assert result.returncode==0,'Canary-specific request-spec operation failed; review private service logs'
    return result.stdout.strip()
old=json.loads(nova("import json\nprint(json.dumps({'force_hosts':r.force_hosts,'force_nodes':r.force_nodes}))\n"))
assert old['force_hosts']==['comsv-01'] and old['force_nodes'] is None
rp=next(p for p in c.placement.resource_providers() if p.name=='comsv-04')
traits=c.placement.get_resource_provider_trait(rp);assert 'COMPUTE_STATUS_DISABLED' in traits.traits
path=ROOT/'canary-cold-migration.json';assert not path.exists()
report={'server_id':server.id,'source':'comsv-01','destination':'comsv-04','request_spec_before':old,
    'traits_before':traits.traits,'file_sha256_before':before_hash,'placement_restored':False,'request_spec_restored':False}
migration_ids_before={x.id for x in c.compute.migrations(instance_uuid=server.id) if x.server_id==server.id}
report['prior_migration_ids']=sorted(migration_ids_before)
def save():path.write_text(json.dumps(report,indent=2));path.chmod(0o600)
save()
try:
    if os.getenv('UCCLOUD_CANARY_FILTER_EXCEPTION')!='1':
        nova("r.force_hosts=['comsv-04'];r.force_nodes=None;r.save()\n")
    c.placement.set_resource_provider_trait(traits,traits=[t for t in traits.traits if t!='COMPUTE_STATUS_DISABLED'],resource_provider_generation=traits.resource_provider_generation)
    c.compute.migrate_server(server,host='comsv-04')
    deadline=time.monotonic()+180
    while time.monotonic()<deadline:
        server=c.compute.get_server(server.id)
        migrations=list(c.compute.migrations(instance_uuid=server.id))
        if any(x.server_id==server.id and x.id not in migration_ids_before and
               (x.status=='error' or x.dest_compute=='comsv-04' and x.status not in ('accepted','pre-migrating')) for x in migrations):break
        if server.status in ('ERROR','VERIFY_RESIZE'):break
        time.sleep(3)
finally:
    current=c.placement.get_resource_provider_trait(rp)
    c.placement.set_resource_provider_trait(current,traits=sorted(set(current.traits)|{'COMPUTE_STATUS_DISABLED'}),resource_provider_generation=current.resource_provider_generation)
    report['placement_restored']='COMPUTE_STATUS_DISABLED' in c.placement.get_resource_provider_trait(rp).traits
    nova('r.force_hosts='+repr(old['force_hosts'])+';r.force_nodes='+repr(old['force_nodes'])+';r.save()\n')
    report['request_spec_restored']=json.loads(nova("import json\nprint(json.dumps({'force_hosts':r.force_hosts,'force_nodes':r.force_nodes}))\n"))==old
    m['quarantine_required']();save()
    print('SINGLE_CANARY_MIGRATION_EXCEPTION_RESTORED_NORMAL_PLACEMENT_BLOCKED',flush=True)
deadline=time.monotonic()+600
while time.monotonic()<deadline:
    server=c.compute.get_server(server.id)
    assert server.status!='ERROR','Canary migration failed; retain quarantine and evidence'
    migrations=list(c.compute.migrations(instance_uuid=server.id))
    fresh=[x for x in migrations if x.server_id==server.id and x.id not in migration_ids_before]
    if fresh:
        latest=max(fresh,key=lambda x:int(x.id))
        assert latest.status!='error','Migration failed before destination assignment; retain quarantine'
    if server.status=='VERIFY_RESIZE':break
    time.sleep(5)
assert server.status=='VERIFY_RESIZE' and server.compute_host=='comsv-04'
c.compute.confirm_server_resize(server);c.compute.wait_for_server(server,wait=240)
after_hash=asyncio.run(checksum());assert after_hash==before_hash
report['file_sha256_after']=after_hash;report['data_preserved']='PASS';save()
os.environ['UCCLOUD_CANARY_EXPECTED_HOST']='comsv-04'
runpy.run_path(str(ROOT/'canary_hypervisor_test.py'),run_name='__main__')
for host in ('comsv-01','comsv-04'):
    assert runpy.run_path(str(ROOT/'verify_hypervisor_gate.py'))['verify'](host)['passed']
report['checks']='PASS';save()
gate=ROOT/'integration-gate.json';checks=json.loads(gate.read_text());checks['cold_migration']='PASS';gate.write_text(json.dumps(checks,indent=2));gate.chmod(0o600)
print('COLD_MIGRATION_GUEST_SSH_SERIAL_NETWORK_DATA_AND_SECURITY_PASS')
