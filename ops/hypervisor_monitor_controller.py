"""Poll signed-policy compute watchdogs; quarantine, never auto-restart VMs."""
import datetime,json,runpy
from pathlib import Path

m=runpy.run_path('/var/lib/uccloud-maintenance/20261008/maintenance.py')
c=m['admin']();services={s.host:s for s in c.compute.services(binary='nova-compute')}
reports=[]
for host in m['HOSTS']:
    try:
        report=json.loads(m['node'](host,'cat /var/lib/uccloud-hypervisor-monitor/report.json',timeout=15))
        timestamp=datetime.datetime.fromisoformat(report['timestamp'])
        stale=(datetime.datetime.now(datetime.UTC)-timestamp).total_seconds()>300
        unsafe=bool(report['latched_alerts']) or stale
    except Exception:unsafe=True;report={'host':host,'latched_alerts':[{'kind':'monitor_unavailable'}]}
    if unsafe and services[host].status!='disabled':
        c.compute.disable_service(services[host],disabled_reason='UCcloud watchdog: security/runtime evidence requires operator review')
    reports.append({'host':host,'quarantined':unsafe,'alerts':report.get('latched_alerts',[])})
store=Path('/var/lib/uccloud-maintenance/20261008/monitor-controller.json')
temporary=store.with_suffix('.next');temporary.write_text(json.dumps({'time':datetime.datetime.now(datetime.UTC).isoformat(),'reports':reports},indent=2));temporary.chmod(0o600);temporary.replace(store)
print('WATCHDOG_POLLED',len(reports),'COMPUTES')
