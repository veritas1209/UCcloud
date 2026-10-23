"""Non-mutating controller health evidence, reusable before/after reboot."""
import datetime,json,runpy,subprocess,sys,urllib.parse,urllib.request
from pathlib import Path

folder=Path('/var/lib/uccloud-maintenance/20261008')
m=runpy.run_path(str(folder/'maintenance.py'));c=m['admin']()
def run(args):
    result=subprocess.run(args,text=True,capture_output=True,timeout=30)
    return result.returncode,result.stdout.strip()
phase=sys.argv[1] if len(sys.argv)>1 else 'baseline'
assert phase in ('baseline','after-reboot')
services={}
for name in ('cloud-portal','cloud-portal-public','cloud-portal-agent','cloud-portal-console-gateway','tailscaled','haproxy','openvswitch-switch','ovn-controller'):
    rc,content=run(['systemctl','show',name,'-p','LoadState','-p','ActiveState','-p','SubState','-p','UnitFileState'])
    state=dict(line.split('=',1) for line in content.splitlines() if '=' in line)
    if state.get('LoadState')=='loaded':services[name]=state
containers=[]
for name in run(['lxc-ls','-1'])[1].splitlines():
    if name:containers.append({'name':name,'running':run(['lxc-info','-n',name,'-sH'])[1]=='RUNNING'})
checks={}
public=runpy.run_path(str(folder/'public_funnel_probe.py'))
try:public_addresses=public['public_addresses']();checks['public_dns_route']=True
except Exception:public_addresses=[];checks['public_dns_route']=False
for label,url in {'public_https':'https://comsv-03.tail5819db.ts.net/','public_health':'https://comsv-03.tail5819db.ts.net/api/health','local_health':'http://127.0.0.1:18080/api/health','terminal_page':'https://comsv-03.tail5819db.ts.net/terminal'}.items():
    try:
        if label!='local_health':
            status,body=public['public_get'](urllib.parse.urlsplit(url).path or '/',public_addresses)
            checks[label]=status==200 and (b'UCcloud' in body if label in ('public_https','terminal_page') else True)
            continue
        headers={'Host':'comsv-03.tail5819db.ts.net','X-Forwarded-Proto':'https'} if label=='local_health' else {}
        request=urllib.request.Request(url,headers=headers)
        with urllib.request.urlopen(request,timeout=15) as response:
            body=response.read(256000)
            checks[label]=response.status==200 and (b'UCcloud' in body if label in ('public_https','terminal_page') else True)
    except Exception:checks[label]=False
compute=[{'host':s.host,'status':s.status,'state':s.state} for s in c.compute.services(binary='nova-compute')]
checks['compute_heartbeats']=all(s['state']=='up' for s in compute)
checks['identity_and_compute_api']=bool(c.identity.get_project(c.current_project_id)) and len(compute)==4
checks['glance_api']=bool(list(c.image.images(status='active')))
checks['neutron_api']=bool(list(c.network.networks(limit=1)))
checks['containers_running']=all(item['running'] for item in containers)
checks['portal_services']=all(item['ActiveState']=='active' for name,item in services.items() if name in ('cloud-portal','cloud-portal-public','cloud-portal-agent','cloud-portal-console-gateway','tailscaled','haproxy','openvswitch-switch'))
required_services=('cloud-portal','cloud-portal-public','cloud-portal-console-gateway','tailscaled','haproxy','openvswitch-switch','ovn-controller')
checks['required_services_present']=all(name in services and services[name]['ActiveState']=='active' for name in required_services)
checks['serial_proxy']=run(['lxc-attach','-n','controller1-nova-api-container-cb1ec29d','--','systemctl','is-active','nova-serialproxy'])[1]=='active'
if phase=='after-reboot':
    before=json.loads((folder/'controller-baseline.json').read_text())
    checks['new_boot']=Path('/proc/sys/kernel/random/boot_id').read_text().strip()!=before['boot_id']
    checks['kernel']=run(['uname','-r'])[1]=='7.0.0-38-generic' and run(['dpkg-query','-W','-f=${Version}','linux-image-7.0.0-38-generic'])[1]=='7.0.0-38.38'
    checks['quarantine_watchdog']=run(['systemctl','is-active','uccloud-hypervisor-monitor-controller.timer'])[1]=='active' and run(['systemctl','is-enabled','uccloud-hypervisor-monitor-controller.timer'])[1]=='enabled'
    import os
    new_flavors=json.loads((folder/'security-flavors.json').read_text())
    checks['new_flavors_allowlist']=new_flavors['published'] and set(os.environ.get('PORTAL_ALLOWED_FLAVORS','').split(','))=={item['new_name'] for item in new_flavors['flavors']}
report={'time':datetime.datetime.now(datetime.UTC).isoformat(),'phase':phase,'boot_id':Path('/proc/sys/kernel/random/boot_id').read_text().strip(),'kernel':run(['uname','-r'])[1],'services':services,'containers':containers,'compute_services':compute,'checks':checks,'passed':all(checks.values())}
file=folder/('controller-'+phase+'.json');file.write_text(json.dumps(report,indent=2));file.chmod(0o600)
print('CONTROLLER_'+phase.upper(), 'PASS' if report['passed'] else 'FAIL', 'failed_checks',[name for name,value in checks.items() if not value])
