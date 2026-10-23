"""Root preflight + isolated non-root TCG appliance for approved offline key injection."""
import base64,hashlib,json,os,pwd,secrets,subprocess,sys,xml.etree.ElementTree as ET
import shutil
from pathlib import Path
os.environ['LC_ALL']='C'
assert os.geteuid()==0
uuid=sys.argv[1];assert len(uuid)==36 and all(c in '0123456789abcdef-' for c in uuid)
domain=next(n for n in subprocess.check_output(['virsh','list','--all','--name'],text=True).splitlines() if n and ET.fromstring(subprocess.check_output(['virsh','dumpxml',n,'--inactive'])).findtext('uuid')==uuid)
assert subprocess.check_output(['virsh','domstate',domain],text=True).strip()=='shut off'
root=ET.fromstring(subprocess.check_output(['virsh','dumpxml',domain,'--inactive']))
disks=[d for d in root.findall('./devices/disk') if d.get('device')=='disk'];assert len(disks)==1
disk=Path(disks[0].find('source').get('file')).resolve();assert disk.is_relative_to(Path('/var/lib/nova/instances'))
backup=Path('/var/backups/uccloud-hypervisor/20261008/disks')/(uuid+'-vda.json');assert backup.exists()
policy=json.loads(Path('/etc/uccloud-hypervisor/qemu-approved.json').read_text())
assert policy['release']=='1:10.2.1+ds-1ubuntu3.2+uccloud1'
with Path('/usr/bin/qemu-system-x86_64').open('rb') as f:assert hashlib.file_digest(f,'sha256').hexdigest()==policy['runtime_files']['/usr/bin/qemu-system-x86_64']
account=pwd.getpwnam('libvirt-qemu');folder=Path('/var/lib/uccloud-guest-inspection')/uuid
assert not folder.exists(),'Review prior inspection state instead of overwriting it'
folder.mkdir(parents=True,mode=0o700);folder.chmod(0o700);os.chown(folder,account.pw_uid,account.pw_gid)
public_key=Path(sys.argv[2]).read_text().strip();assert public_key.startswith('ssh-ed25519 ') and '\n' not in public_key
marker='uccloud-security-inspection-'+secrets.token_hex(12)
data={'disk':str(disk),'uuid':uuid,'public_key':public_key,'marker':marker}
p=folder/'input.json';p.write_text(json.dumps(data));p.chmod(0o600);os.chown(p,account.pw_uid,account.pw_gid)
p=folder/'worker.py';p.write_bytes(Path(sys.argv[3]).read_bytes());p.chmod(0o644)
wrapper=folder/'qemu-inspection'
wrapper.write_text('#!/usr/bin/python3\nimport os,sys\nos.execv("/usr/bin/qemu-system-x86_64",["qemu-system-x86_64"]+sys.argv[1:]+["-machine","smm=off","-cpu","max,-vmx","-sandbox","on,obsolete=deny,elevateprivileges=deny,spawn=deny,resourcecontrol=deny"])\n');wrapper.chmod(0o755)
kernel=folder/'appliance-kernel';shutil.copyfile('/boot/vmlinuz-7.0.0-38-generic',kernel);kernel.chmod(0o644)
info=json.loads(subprocess.check_output(['qemu-img','info','--output=json',str(disk)],text=True))
backing=info.get('full-backing-filename')
if backing:assert Path(backing).resolve().is_relative_to(Path('/var/lib/nova/instances/_base'))
acl_targets=[Path('/var/lib/nova'),Path('/var/lib/nova/instances'),disk.parent,disk]
if backing:acl_targets += [Path(backing).parent,Path(backing)]
acl_backup=Path('/var/backups/uccloud-hypervisor/20261008/inspection')/(uuid+'-host-acl-before.txt')
acl_backup.parent.mkdir(mode=0o700,exist_ok=True);assert not acl_backup.exists()
acl_backup.write_text(subprocess.check_output(['getfacl','-p']+[str(p) for p in acl_targets],text=True));acl_backup.chmod(0o600)
profile='uccloud-inspect-'+uuid
text='''#include <tunables/global>
profile REPLACE_PROFILE {
 #include <abstractions/base>
 /usr/** rixm,
 /lib/** rixm,
 /lib64/** rixm,
 /boot/** r,
 /etc/** r,
 deny /etc/nova/** r,
 deny /etc/openstack_deploy/** r,
 deny /root/** rwkl,
 /proc/** r,
 /sys/** r,
 /dev/null rw,
 /dev/zero rw,
 /dev/random r,
 /dev/urandom r,
 /var/lib/dpkg/** r,
 /tmp/ rw,
 /tmp/** rwkixm,
 /var/lib/uccloud-guest-inspection/ r,
 REPLACE_FOLDER/ rw,
 REPLACE_FOLDER/** rwkixm,
 REPLACE_DISK rwk,
 REPLACE_BACKING
 network unix,
}
'''.replace('REPLACE_PROFILE',profile).replace('REPLACE_FOLDER',str(folder)).replace('REPLACE_DISK',str(disk)).replace('REPLACE_BACKING',(backing+' rk,') if backing else '')
path=Path('/etc/apparmor.d')/profile;assert not path.exists();path.write_text(text);path.chmod(0o644)
subprocess.run(['apparmor_parser','-r',str(path)],check=True)
cmd=['systemd-run','--unit='+profile,'--property=User=libvirt-qemu','--property=Group='+account.pw_name,
 '--property=NoNewPrivileges=yes','--property=CapabilityBoundingSet=','--property=PrivateDevices=yes',
 '--property=PrivateMounts=yes','--property=PrivateTmp=yes','--property=PrivateNetwork=yes','--property=ProtectHome=yes',
 '--property=ProtectSystem=strict','--property=ReadWritePaths='+str(folder)+' '+str(disk),
 '--property=RestrictAddressFamilies=AF_UNIX','--property=AppArmorProfile='+profile,
 '--property=RuntimeMaxSec=600','--setenv=LIBGUESTFS_CACHEDIR='+str(folder),
 '--setenv=LIBGUESTFS_TMPDIR='+str(folder),'/usr/bin/python3',str(folder/'worker.py'),str(folder)]
cmd[-3:-3]=['--setenv=TMPDIR='+str(folder),'--setenv=SUPERMIN_KERNEL='+str(kernel),
 '--setenv=SUPERMIN_KERNEL_VERSION=7.0.0-38-generic','--setenv=SUPERMIN_MODULES=/lib/modules/7.0.0-38-generic']
# Never leave temporary host disk permissions behind, even on appliance failure.
import time,signal
try:
    for target in acl_targets:
        access='x' if target.is_dir() else 'rw' if target==disk else 'r'
        subprocess.run(['setfacl','-m','u:libvirt-qemu:'+access,str(target)],check=True)
    subprocess.run(cmd,check=True)
    print('ISOLATED_TCG_INSPECTION_STARTED',uuid,flush=True)
    deadline=time.monotonic()+660
    pending_since=None;shutdown_signal_sent=False
    while time.monotonic()<deadline:
        state=subprocess.check_output(['systemctl','show',profile,'-p','ActiveState','--value'],text=True).strip()
        if state in ('inactive','failed'):break
        # Some TCG appliances do not exit after libguestfs' orderly shutdown.
        # Terminate only this appliance after guest writes are synced/unmounted.
        pending=folder/'result-pending-shutdown.json'
        if pending.exists():
            verified=json.loads(pending.read_text())
            assert verified['uuid']==uuid and verified['marker']==marker and verified['password_data_unchanged']
            if pending_since is None:pending_since=time.monotonic()
            if not shutdown_signal_sent and time.monotonic()-pending_since>20:
                main=subprocess.check_output(['systemctl','show',profile,'-p','MainPID','--value'],text=True).strip()
                proc=Path('/proc')/main
                if main!='0' and proc.exists() and (proc/'wchan').read_text().strip()=='do_wait':
                    children=(proc/'task'/main/'children').read_text().split()
                    qemu=[p for p in children if (Path('/proc')/p/'comm').read_text().strip().startswith('qemu-system')]
                    assert len(qemu)==1 and profile in (Path('/proc')/qemu[0]/'cgroup').read_text()
                    assert subprocess.check_output(['virsh','domstate',domain],text=True).strip()=='shut off'
                    os.kill(int(qemu[0]),signal.SIGTERM);shutdown_signal_sent=True
                    print('ONLY_SYNCED_ISOLATED_APPLIANCE_TERMINATED',flush=True)
        time.sleep(3)
    assert state=='inactive' and (folder/'result.json').exists(),'Inspection failed; keep the VM stopped'
finally:
    subprocess.run(['setfacl','--restore='+str(acl_backup)],check=True)
    print('ORIGINAL_HOST_DISK_PERMISSIONS_RESTORED',flush=True)
