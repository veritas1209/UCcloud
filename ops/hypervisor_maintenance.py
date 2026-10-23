"""Explicit host-scoped UCcloud maintenance; no exploit payloads or VM deletion."""
import argparse
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys

HOSTS = {'comsv-01':'compute1','comsv-04':'compute2','comsv-05':'compute3','user-ProLiant-DL20-Gen11':'compute4'}
EVIDENCE = Path('/var/lib/uccloud-maintenance/20261008')

def node(host, command, *, input=None, timeout=60):
    if host not in HOSTS: raise ValueError('Host is not in this maintenance scope')
    inventory=json.loads(Path('/etc/openstack_deploy/openstack_inventory.json').read_text())
    variables=inventory['_meta']['hostvars'][HOSTS[host]]
    address=variables.get('ansible_host') or variables['management_address']
    try:
        result=subprocess.run(['ssh','-i','/root/.ssh/id_ed25519_osa','-o','BatchMode=yes',
                               '-o','StrictHostKeyChecking=yes','-o','ConnectTimeout=8',
                               'root@'+address,'env LC_ALL=C /bin/sh -c '+shlex.quote(command)],input=input,capture_output=True,text=True,timeout=timeout)
    except subprocess.TimeoutExpired:
        raise RuntimeError('Host command timed out: '+host+'; inspect the server-side job before retrying') from None
    if result.returncode:
        # Never dump arbitrary configuration, credentials, or SSH targets in an exception.
        raise RuntimeError('Host command failed: '+host+' (exit '+str(result.returncode)+')')
    return result.stdout

def python_node(host, source, timeout=60):
    return node(host,'python3 -',input=source,timeout=timeout)

def admin():
    pid=subprocess.check_output(['systemctl','show','cloud-portal-public','-p','MainPID','--value'],text=True).strip()
    for item in Path('/proc/'+pid+'/environ').read_bytes().split(b'\0'):
        if b'=' in item:
            key,value=item.decode().split('=',1);os.environ[key]=value
    sys.path.insert(0,'/opt/cloud-portal')
    from backend.openstack_service import admin_connection
    return admin_connection()

def quarantine_required():
    services=list(admin().compute.services(binary='nova-compute'))
    if {service.host for service in services} != set(HOSTS): raise RuntimeError('Compute inventory changed')
    if any(service.status!='disabled' or service.is_forced_down for service in services):
        raise RuntimeError('All compute hosts must be quarantined without forced_down')

AUDIT = r'''
import configparser,glob,hashlib,json,os,pwd,subprocess,xml.etree.ElementTree as ET
from pathlib import Path
def run(args):
 r=subprocess.run(args,capture_output=True,text=True,timeout=30)
 if r.returncode:raise RuntimeError('Required host audit command failed')
 return r.stdout.strip()
c=configparser.ConfigParser(strict=False,interpolation=None);c.read('/etc/nova/nova.conf')
summary={'host':run(['hostname']),'kernel':os.uname().release,
 'kernel_package':run(['dpkg-query','-W','-f=${Version}','linux-image-'+os.uname().release]),
 'qemu_package':run(['dpkg-query','-W','-f=${Version}','qemu-system-x86']),
 'qemu_binary':run(['/usr/bin/qemu-system-x86_64','--version']).splitlines()[0],
 'libvirt_package':run(['dpkg-query','-W','-f=${Version}','libvirt0']),
 'cpu_flags':c.get('libvirt','cpu_model_extra_flags',fallback=''),
 'cpu_mode':c.get('libvirt','cpu_mode',fallback=''),
 'compute_driver':c.get('DEFAULT','compute_driver',fallback='libvirt.LibvirtDriver'),
 'serial_enabled':c.get('serial_console','enabled',fallback='false'),
 'vms':[]}
for key in ('nested','enable_shadow_vmcs','ept','unrestricted_guest'):
 p=Path('/sys/module/kvm_intel/parameters')/key;summary[key]=p.read_text().strip() if p.exists() else 'ABSENT'
for name in run(['virsh','list','--all','--name']).splitlines():
 if not name or name=='UNAVAILABLE':continue
 xml=run(['virsh','dumpxml',name]);root=ET.fromstring(xml)
 inactive=ET.fromstring(run(['virsh','dumpxml',name,'--inactive']))
 item={'domain':name,'uuid':root.findtext('uuid'),'state':run(['virsh','domstate',name]),
       'cpu_vmx':[f.attrib for f in root.findall('./cpu/feature') if f.get('name')=='vmx'],
       'smm':root.find('./features/smm').attrib if root.find('./features/smm') is not None else 'auto',
       'inactive_smm':inactive.find('./features/smm').attrib if inactive.find('./features/smm') is not None else 'auto',
       'video':[x.attrib for x in root.findall('./devices/video/model')],
       'disk_models':[x.attrib for x in root.findall('./devices/disk/target')],
       'filesystem':[x.attrib for x in root.findall('./devices/filesystem')],
       'hostdev':len(root.findall('./devices/hostdev')),
       'secure_boot':root.find('./os/loader').get('secure') if root.find('./os/loader') is not None else None,
       'firmware_features':[x.attrib for x in root.findall('./os/firmware/feature')],
       'serial':len(root.findall('./devices/serial'))}
 if item['state']=='running':
  item['qmp_version']=json.loads(run(['virsh','qemu-monitor-command',name,'{"execute":"query-version"}'])).get('return')
  def qmp(command,arguments):
   return json.loads(run(['virsh','qemu-monitor-command',name,json.dumps({'execute':command,'arguments':arguments})])).get('return')
  queue=['/machine'];visited=set();smm=[]
  while queue and len(visited)<400:
   path=queue.pop(0)
   if path in visited:continue
   visited.add(path)
   for prop in qmp('qom-list',{'path':path}) or []:
    if prop['name']=='smm-enabled':smm.append({'path':path,'value':qmp('qom-get',{'path':path,'property':'smm-enabled'})})
    if prop.get('type','').startswith('child<'):queue.append(path+'/'+prop['name'])
  item['effective_smm']=smm
  cpus=qmp('query-cpus-fast',{}) or []
  item['effective_vmx']=[qmp('qom-get',{'path':cpu['qom-path'],'property':'vmx'}) for cpu in cpus]
  pidfile=Path('/run/libvirt/qemu')/(name+'.pid')
  if pidfile.exists():
   pid=pidfile.read_text().strip();status=Path('/proc/'+pid+'/status').read_text()
   item['pid']=int(pid)
   item['isolation']={line.split(':')[0]:line.split(':',1)[1].strip() for line in status.splitlines() if line.split(':')[0] in ('Uid','CapEff','NoNewPrivs','Seccomp')}
   item['apparmor']=Path('/proc/'+pid+'/attr/current').read_text().strip()
   item['exe_deleted']=os.readlink('/proc/'+pid+'/exe').endswith(' (deleted)')
   with Path('/proc/'+pid+'/exe').open('rb') as stream:item['running_exe_sha256']=hashlib.file_digest(stream,'sha256').hexdigest()
   with Path('/usr/bin/qemu-system-x86_64').open('rb') as stream:item['installed_exe_sha256']=hashlib.file_digest(stream,'sha256').hexdigest()
   item['qemu_user']=pwd.getpwuid(int(item['isolation']['Uid'].split()[0])).pw_name
   item['mount_namespace_isolated']=os.readlink('/proc/'+pid+'/ns/mnt')!=os.readlink('/proc/1/ns/mnt')
   item['cgroup_v2']=Path('/sys/fs/cgroup/cgroup.controllers').exists()
   item['private_cgroup']=any('machine-qemu' in line or '/machine.slice/' in line for line in Path('/proc/'+pid+'/cgroup').read_text().splitlines())
   item['deleted_qemu_modules']=any('qemu' in line and '(deleted)' in line for line in Path('/proc/'+pid+'/maps').read_text().splitlines())
 summary['vms'].append(item)
print(json.dumps(summary))
'''

BACKUP = r'''
import datetime,json,os,subprocess,tarfile
from pathlib import Path
folder=Path('/var/backups/uccloud-hypervisor/20261008');folder.mkdir(parents=True,exist_ok=True);folder.chmod(0o700)
archive=folder/'host-config.tar'
if not archive.exists():
 with tarfile.open(archive,'w') as tar:
  for name in ('/etc/nova','/etc/libvirt','/etc/modprobe.d','/etc/default/grub','/etc/apt/sources.list.d','/etc/apt/preferences.d','/etc/systemd/system/nova-compute.service'):
   p=Path(name)
   if p.exists():tar.add(p,arcname=name.lstrip('/'))
 archive.chmod(0o600)
 for name in subprocess.check_output(['virsh','list','--all','--name'],text=True).splitlines():
  if name:
   for suffix,args in (('live',[]),('inactive',['--inactive'])):
    output=folder/(name+'.'+suffix+'.xml')
    output.write_bytes(subprocess.check_output(['virsh','dumpxml',name]+args));output.chmod(0o600)
print('CONFIG_AND_XML_BACKUP_READY')
'''

def record(host, **updates):
    path=EVIDENCE/'state.json';state=json.loads(path.read_text());state['hosts'][host].update(updates)
    temporary=path.with_suffix('.next');temporary.write_text(json.dumps(state,indent=2));temporary.chmod(0o600);temporary.replace(path)

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('operation',choices=['audit','backup','kernel-canary']);parser.add_argument('host',choices=HOSTS)
    args=parser.parse_args()
    quarantine_required()
    if args.operation=='audit':
        result=json.loads(python_node(args.host,AUDIT,timeout=240));path=EVIDENCE/(args.host+'.json');path.write_text(json.dumps(result,indent=2));path.chmod(0o600)
        print(json.dumps(result,indent=2))
    elif args.operation=='backup':print(args.host,python_node(args.host,BACKUP).strip())
    elif args.operation=='kernel-canary':
        if args.host!='comsv-01':raise RuntimeError('Only the empty canary is permitted by this operation')
        before=json.loads(python_node(args.host,AUDIT))
        if before['vms']:raise RuntimeError('Canary is no longer empty')
        print(python_node(args.host,BACKUP).strip())
        source=r'''
import os,subprocess
from pathlib import Path
assert not subprocess.check_output(['virsh','list','--name'],text=True).strip()
assert subprocess.check_output(['dpkg-query','-W','-f=${Version}','linux-image-7.0.0-38-generic'],text=True).strip()=='7.0.0-38.38'
path=Path('/etc/modprobe.d/uccloud-kvm-security.conf')
assert not path.exists(),'Existing UCcloud policy needs review'
path.write_text('# UCcloud: nested VMX disabled until security maintenance is complete.\noptions kvm_intel nested=0 enable_shadow_vmcs=0\n');path.chmod(0o644)
subprocess.run(['update-initramfs','-u','-k','7.0.0-38-generic'],check=True)
print('CANARY_POLICY_READY')
subprocess.run(['shutdown','-r','+1','UCcloud security maintenance'],check=True)
'''
        print(python_node(args.host,source,timeout=180).strip());record(args.host,reboot_requested=True)
