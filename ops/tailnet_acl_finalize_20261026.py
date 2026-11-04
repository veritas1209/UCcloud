"""Sanitized post-ACL verification summary after removal of owned test resources."""
import concurrent.futures
import datetime as dt
import json
from pathlib import Path
import shlex
import socket
import subprocess
import time

STATE=Path('/var/lib/uccloud-tailnet-acl-20261026')

def run(args):
    return subprocess.check_output(args,text=True,timeout=30).strip()

def main():
    filter_result=json.loads((STATE/'filter-verification.json').read_text())
    laptop=json.loads((STATE/'laptop-verification.json').read_text())
    network=json.loads((STATE/'network-verification.json').read_text())
    checks=json.loads((STATE/'service-verification.json').read_text())
    assert checks and all(checks.values()) and not (STATE/'qa-private.json').exists()
    assert filter_result['real_filter_assertions_passed']==69 and laptop['actual_laptop_filter_assertions']==8
    # The destination was demonstrably listening during the deny comparison;
    # the disposable listener must now be gone on its independently reachable LAN socket.
    try:
        with socket.create_connection(('172.29.236.13',49091),timeout=3):
            raise AssertionError('Temporary probe listener still active')
    except OSError:
        pass
    code="""import json,subprocess; v=subprocess.check_output(['virsh','list','--state-running','--uuid'],text=True).split(); q=[int(l.split()[0]) for l in subprocess.check_output(['ps','-eo','etimes,comm'],text=True).splitlines()[1:] if 'qemu-system' in l]; print(json.dumps({'running_vms':len(v),'qemu_count':len(q),'minimum_qemu_uptime_seconds':min(q) if q else None}))"""
    hosts=[('comsv-01','172.29.236.12'),('comsv-04','172.29.236.13'),('comsv-05','172.29.236.14'),('user-ProLiant-DL20-Gen11','172.29.236.15')]
    def collect(item):
        name,address=item
        value=json.loads(run(['ssh','-n','-o','BatchMode=yes','-o','StrictHostKeyChecking=yes','root@'+address,'python3 -c '+shlex.quote(code)]))
        value['host']=name
        return value
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
        computes=list(executor.map(collect,hosts))
    assert [x['running_vms'] for x in computes]==[1,2,1,2]
    assert all(x['minimum_qemu_uptime_seconds']>86400 for x in computes)
    units=['cloud-portal-v2','cloud-portal-public-v2','uccloud-provisioner','uccloud-portal-edge','uccloud-keystone-pool-watch.timer']
    services={x:run(['systemctl','is-active',x]) for x in units}
    assert set(services.values())=={'active'}
    (STATE/'monitor-stop').write_text('verification finished\n')
    time.sleep(4)
    continuity=json.loads((STATE/'continuity.json').read_text())
    result={'completed':dt.datetime.now(dt.timezone.utc).isoformat(),
            'policy_application':'User saved management policy through Tailscale admin console; no management policy API credential issued.',
            'actual_filters':filter_result,'laptop':laptop,'network':network,'functional_checks':checks,
            'tool_observed_ssh':{'admin_pc_authenticated_servers':5,'controller_authenticated_computes':4,
                                'compute1_controller_tailnet_authenticated':True,'compute1_controller_lan_authenticated':True,
                                'new_admin_local_forward_http_status':200},
            'public_tailnet_policy_readonly_verified_deny_all':True,
            'student_vms':computes,'services':services,'continuity':continuity,
            'temporary_probe_listener_removed':True,'qa_secrets_removed':True,
            'offline_desktop_receiver_assertions_not_live_verified':8,
            'policy_candidate_sha256':filter_result['candidate_sha256']}
    # Summary contains no credentials, VM IDs or internal addresses.
    raw=json.dumps(result,ensure_ascii=False,indent=2)
    (STATE/'final-summary.json').write_text(raw);(STATE/'final-summary.json').chmod(0o600)
    output=Path('/tmp/uccloud-tailnet-acl-verification-20261026.json')
    output.write_text(raw);output.chmod(0o644)
    print(raw)

if __name__=='__main__':main()
