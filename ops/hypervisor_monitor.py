"""Read-only compute watchdog. A controller quarantines reported unsafe hosts."""
import configparser,datetime,hashlib,importlib.util,json,os,re,subprocess
from pathlib import Path
os.environ['LC_ALL']='C'

STORE=Path('/var/lib/uccloud-hypervisor-monitor')
STORE.mkdir(mode=0o700,exist_ok=True);STORE.chmod(0o700)
def run(args):
    result=subprocess.run(args,capture_output=True,text=True,timeout=30)
    if result.returncode:raise RuntimeError('Required monitoring command failed')
    return result.stdout.strip()
def sha(p):
    with Path(p).open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()
def field_text(item,key):
    value=item.get(key,'')
    if isinstance(value,str):return value
    if isinstance(value,list) and all(isinstance(part,int) and 0<=part<=255 for part in value):
        return bytes(value).decode('utf-8','replace')
    return '' if value is None else json.dumps(value)
alerts=[]
spec=importlib.util.spec_from_file_location('policy','/usr/local/lib/uccloud/hypervisor_xml_policy.py')
policy=importlib.util.module_from_spec(spec);spec.loader.exec_module(policy)
try:
    policy.approved_qemu()
    assert tuple(int(x) for x in os.uname().release.split('-')[0].split('.')) >= (7,0,0)
    assert int(os.uname().release.split('-')[1])>=38
    for setting in ('nested','enable_shadow_vmcs'):
        assert (Path('/sys/module/kvm_intel/parameters')/setting).read_text().strip()=='N'
    conf=configparser.ConfigParser(strict=False,interpolation=None);conf.read('/etc/nova/nova.conf')
    assert '-vmx' in [x.strip() for x in conf.get('libvirt','cpu_model_extra_flags').split(',')]
    assert conf.get('DEFAULT','compute_driver')=='libvirt.uccloud_security.SecurityLibvirtDriver'
    for domain in run(['virsh','list','--all','--name']).splitlines():
        if not domain:continue
        policy.validate(run(['virsh','dumpxml',domain,'--inactive']))
        if run(['virsh','domstate',domain])!='running':continue
        policy.validate(run(['virsh','dumpxml',domain]))
        pid=(Path('/run/libvirt/qemu')/(domain+'.pid')).read_text().strip()
        assert sha('/proc/'+pid+'/exe')==sha('/usr/bin/qemu-system-x86_64')
        status=dict(line.split(':',1) for line in Path('/proc/'+pid+'/status').read_text().splitlines() if ':' in line)
        assert status['NoNewPrivs'].strip()=='1' and status['Seccomp'].strip()=='2' and int(status['CapEff'].strip(),16)==0
        assert '(enforce)' in Path('/proc/'+pid+'/attr/current').read_text()
        def qmp(command,arguments):
            answer=json.loads(run(['virsh','qemu-monitor-command',domain,json.dumps({'execute':command,'arguments':arguments})]))
            assert 'return' in answer,'QMP monitoring failed'
            return answer['return']
        cpus=qmp('query-cpus-fast',{})
        assert cpus and all(qmp('qom-get',{'path':cpu['qom-path'],'property':'vmx'}) is False for cpu in cpus)
        queue=['/machine'];visited=set();smm=[]
        while queue and len(visited)<400:
            node=queue.pop(0)
            if node in visited:continue
            visited.add(node)
            for prop in qmp('qom-list',{'path':node}):
                if prop['name']=='smm-enabled':smm.append(qmp('qom-get',{'path':node,'property':'smm-enabled'}))
                if prop.get('type','').startswith('child<'):queue.append(node+'/'+prop['name'])
        assert smm and all(value is False for value in smm),'Effective SMM drift'
except Exception as exc:
    alerts.append({'kind':'configuration_or_runtime_drift','detail':type(exc).__name__})

cursor=STORE/'journal-cursor'
boot_file=STORE/'journal-boot-id'
current_boot=Path('/proc/sys/kernel/random/boot_id').read_text().strip()
args=['journalctl','--no-pager','--all','-o','json','--show-cursor']
if cursor.exists() and boot_file.exists() and boot_file.read_text().strip()==current_boot:
    args+=['--after-cursor',cursor.read_text().strip()]
else:
    # Volatile journals lose old cursors on reboot. Inspect the complete new boot
    # rather than treating the expected journal reset as a monitoring outage.
    args+=['-b']
try:
    result=subprocess.run(args,capture_output=True,text=True,timeout=30)
    if result.returncode and '--after-cursor' in args and 'seek' in result.stderr.lower():
        result=subprocess.run(['journalctl','--no-pager','--all','-o','json','--show-cursor','-b'],capture_output=True,text=True,timeout=30)
    if result.returncode:raise RuntimeError('Journal monitoring command failed')
    log=result.stdout
    pattern=re.compile(r'BUG:|Oops:|KASAN:|use.after.free|pte_list_remove|shadow.?VMCS.*(?:fault|error|fail)|SMM.*(?:fault|error|fail)|soft lockup|RCU.*stall|I/O.*(?:timeout|timed out)|apparmor="DENIED"',re.I)
    evidence=[];last_cursor=None
    for line in log.splitlines():
        if line.startswith('-- cursor:'):last_cursor=line.partition(':')[2].strip();continue
        try:item=json.loads(line)
        except json.JSONDecodeError:continue
        message=field_text(item,'MESSAGE');command=field_text(item,'_COMM');unit=field_text(item,'_SYSTEMD_UNIT')
        relevant=command in ('kernel','qemu-system-x86','qemu-system-x86_64','libvirtd','nova-compute','systemd-coredump') or unit in ('libvirtd.service','nova-compute.service')
        if relevant and (pattern.search(message) or 'qemu' in message.lower() and re.search(r'core dump|segfault|unexpectedly.*(?:closed|exit)|crash',message,re.I)):
            evidence.append(item)
    if evidence:
        out=STORE/('events-'+datetime.datetime.now(datetime.UTC).strftime('%Y%m%dT%H%M%S%f')+'.json')
        out.write_text(json.dumps(evidence));out.chmod(0o600)
        alerts.append({'kind':'security_or_availability_event','count':len(evidence)})
    if last_cursor:cursor.write_text(last_cursor);cursor.chmod(0o600)
    boot_file.write_text(current_boot);boot_file.chmod(0o600)
except Exception as exc:alerts.append({'kind':'journal_monitor_unavailable','detail':type(exc).__name__})
previous=STORE/'report.json'
if previous.exists():
    try:alerts=json.loads(previous.read_text()).get('latched_alerts',[])+alerts
    except Exception:alerts.append({'kind':'invalid_monitor_state'})
report={'host':os.uname().nodename,'timestamp':datetime.datetime.now(datetime.UTC).isoformat(),
        'latched_alerts':list({json.dumps(item,sort_keys=True):item for item in alerts}.values())}
tmp=STORE/'report.next';tmp.write_text(json.dumps(report));tmp.chmod(0o600);tmp.replace(previous)
# Alerts are latched: an operator must review preserved evidence before clearing them.
print(json.dumps({'host':report['host'],'alerts':len(report['latched_alerts'])}))
