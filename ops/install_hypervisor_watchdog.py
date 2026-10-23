"""Install reviewed watchdog timers; never clear alerts or enable VM placement."""
import json,runpy,subprocess,sys
from pathlib import Path
ROOT=Path('/var/lib/uccloud-maintenance/20261008')
m=runpy.run_path(str(ROOT/'maintenance.py'));m['quarantine_required']()
target=sys.argv[1]
service='''[Unit]
Description=UCcloud hypervisor evidence and drift watchdog
After=libvirtd.service
[Service]
Type=oneshot
ExecStart=/usr/bin/python3 /usr/local/sbin/uccloud-hypervisor-monitor
Nice=10
TimeoutStartSec=180
'''
timer='''[Unit]
Description=UCcloud hypervisor watchdog schedule
[Timer]
OnBootSec=90
OnUnitActiveSec=90
RandomizedDelaySec=5
[Install]
WantedBy=timers.target
'''
if target=='controller':
    name='uccloud-hypervisor-monitor-controller'
    service='''[Unit]
Description=UCcloud compute security quarantine watchdog
After=network-online.target cloud-portal-public.service
[Service]
Type=oneshot
ExecStart=/opt/cloud-portal/.venv/bin/python /var/lib/uccloud-maintenance/20261008/hypervisor_monitor_controller.py
Nice=10
TimeoutStartSec=150
'''
    for suffix,text in (('.service',service),('.timer',timer)):
        p=Path('/etc/systemd/system')/(name+suffix);assert not p.exists()
        p.write_text(text);p.chmod(0o644)
    subprocess.run(['systemctl','daemon-reload'],check=True)
    subprocess.run(['systemctl','enable','--now',name+'.timer'],check=True)
    subprocess.run(['systemctl','start',name+'.service'],check=True,timeout=155)
    print('CONTROLLER_QUARANTINE_WATCHDOG_ENABLED_NO_AUTO_RESTART_OR_RESUME')
else:
    assert target in m['HOSTS']
    assert runpy.run_path(str(ROOT/'verify_hypervisor_gate.py'))['verify'](target)['passed']
    name='uccloud-hypervisor-monitor'
    source="from pathlib import Path\nimport subprocess\n"
    for suffix,text in (('.service',service),('.timer',timer)):
        path='/etc/systemd/system/'+name+suffix
        source+="p=Path("+repr(path)+");assert not p.exists();p.write_text("+repr(text)+");p.chmod(0o644)\n"
    source+="subprocess.run(['systemctl','daemon-reload'],check=True)\nsubprocess.run(['systemctl','enable','--now','uccloud-hypervisor-monitor.timer'],check=True)\nsubprocess.run(['systemctl','start','uccloud-hypervisor-monitor.service'],check=True,timeout=185)\nprint('COMPUTE_WATCHDOG_ENABLED')\n"
    print(m['python_node'](target,source,timeout=220))
    report=json.loads(m['node'](target,'cat /var/lib/uccloud-hypervisor-monitor/report.json'))
    assert not report['latched_alerts'],'Review preserved watchdog events; do not clear automatically'
    m['record'](target,watchdog_enabled=True)
    print(target,'WATCHDOG_RUNNING_WITH_NO_LATCHED_ALERTS')
