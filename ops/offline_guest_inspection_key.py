"""Non-root libguestfs appliance: inject one restricted, self-removing probe key."""
import base64,hashlib,json,os,pwd,sys
from pathlib import Path
import guestfs
sys.excepthook=sys.__excepthook__

assert os.geteuid()==pwd.getpwnam('libvirt-qemu').pw_uid
folder=Path(sys.argv[1]);data=json.loads((folder/'input.json').read_text())
g=guestfs.GuestFS(python_return_dict=True)
g.set_tmpdir(str(folder));g.set_cachedir(str(folder));g.set_verbose(True)
g.set_backend('direct');g.set_backend_setting('force_tcg','true')
g.set_hv(str(folder/'qemu-inspection'))
g.add_drive_opts(data['disk'],format='qcow2',readonly=False)
g.launch()
g.set_verbose(False)
print('INSPECTION_APPLIANCE_READY',flush=True)
roots=g.inspect_os();assert len(roots)==1,'Cannot uniquely identify the guest root'
g.mount(roots[0],'/')
print('GUEST_ROOT_MOUNTED',flush=True)
passwd=g.read_file('/etc/passwd').decode().splitlines()
root=next(line.split(':') for line in passwd if line.startswith('root:'))
root_home=root[5];assert root_home in ('/root','/home/root')
user=root;command='/bin/sh '
if any(line.startswith('cirros:') for line in passwd):
    user=next(line.split(':') for line in passwd if line.startswith('cirros:'))
    assert user[5]=='/home/cirros' and g.is_file('/usr/bin/sudo')
    # CirrOS disables direct root login. Check its existing passwordless sudo;
    # the regular account's new key can execute only the named root probe.
    g.command(['/bin/su','-s','/bin/sh','cirros','-c','/usr/bin/sudo -n true'])
    command='/usr/bin/sudo -n /bin/sh '
home=user[5];uid=int(user[2]);gid=int(user[3])
auth=home+'/.ssh/authorized_keys';work=root_home+'/.uccloud-maintenance'
assert not any(g.is_symlink(p) for p in (home,home+'/.ssh',auth,work))
before=g.read_file(auth) if g.is_file(auth,followsymlinks=True) else b''
assert data['marker'].encode() not in before
shadow_hash=hashlib.sha256(g.read_file('/etc/shadow')).hexdigest()
host_keys=[g.read_file(p).decode().strip() for p in g.glob_expand('/etc/ssh/ssh_host_*_key.pub')]
if not host_keys:
    for p in g.glob_expand('/etc/dropbear/*host_key'):
        output=g.command(['dropbearkey','-y','-f',p])
        host_keys.extend(line.strip() for line in output.splitlines() if line.startswith(('ssh-rsa ','ssh-ed25519 ','ecdsa-sha2-')))
assert host_keys,'Cannot pin the guest SSH identity'
g.mkdir_p(home+'/.ssh');g.chmod(0o700,home+'/.ssh');g.chown(uid,gid,home+'/.ssh')
g.mkdir_p(work);g.chmod(0o700,work);g.chown(0,0,work)
script_path=work+'/'+data['marker']+'.sh'
script='''#!/bin/sh
vmx=FAIL; kvm=FAIL; device=FAIL; network=FAIL; io=FAIL
if ! grep -qw vmx /proc/cpuinfo; then vmx=PASS; fi
if ! modprobe kvm_intel >/dev/null 2>&1; then kvm=PASS; fi
if [ ! -e /dev/kvm ]; then device=PASS; fi
if awk 'NR>1 && $2 == "00000000" {found=1} END {exit !found}' /proc/net/route; then network=PASS; fi
f=/tmp/REPLACE_MARKER-io
if dd if=/dev/urandom of="$f" bs=65536 count=1 >/dev/null 2>&1; then
 chmod 600 "$f"; sync
 a=$(sha256sum "$f" | awk '{print $1}'); b=$(sha256sum "$f" | awk '{print $1}')
 if [ -n "$a" ] && [ "$a" = "$b" ]; then io=PASS; fi
fi
rm -f "$f"
# Remove only this inspection key; preserve every other authorized-key line.
auth=REPLACE_AUTH
t=REPLACE_WORK/REPLACE_MARKER-auth
if awk 'index($0,"REPLACE_MARKER")==0' "$auth" > "$t"; then
 cat "$t" > "$auth"; chmod 600 "$auth"; rm -f "$t"
else
 printf '{"cleanup":"FAIL"}\n'; exit 1
fi
rm -f REPLACE_SCRIPT
rmdir REPLACE_WORK 2>/dev/null || true
printf '{"vmx_absent":"%s","kvm_intel_blocked":"%s","dev_kvm_unusable":"%s","network":"%s","file_io":"%s","ssh_or_serial":"PASS","cleanup":"PASS"}\n' "$vmx" "$kvm" "$device" "$network" "$io"
test "$vmx$kvm$device$network$io" = PASSPASSPASSPASSPASS
'''
for key,value in {'REPLACE_MARKER':data['marker'],'REPLACE_AUTH':auth,'REPLACE_WORK':work,'REPLACE_SCRIPT':script_path}.items():
    script=script.replace(key,value)
g.write(script_path,script.encode());g.chmod(0o700,script_path);g.chown(0,0,script_path)
line='no-port-forwarding,no-agent-forwarding,no-X11-forwarding,no-pty,command="'+command+script_path+'" '+data['public_key']+' '+data['marker']
g.write(auth,before+(b'\n' if before and not before.endswith(b'\n') else b'')+line.encode()+b'\n')
g.chmod(0o600,auth);g.chown(uid,gid,auth)
assert hashlib.sha256(g.read_file('/etc/shadow')).hexdigest()==shadow_hash,'Guest password data changed'
report={'uuid':data['uuid'],'marker':data['marker'],'key_injected':True,'password_data_unchanged':True,
        'authorized_keys_before_b64':base64.b64encode(before).decode(),'root_home':root_home,'host_keys':host_keys,'ssh_username':user[0]}
g.sync();g.umount_all()
p=folder/'result-pending-shutdown.json';p.write_text(json.dumps(report));p.chmod(0o600)
print('GUEST_WRITES_SYNCED_AND_UNMOUNTED',flush=True)
g.shutdown();g.close()
p=folder/'result.json';p.write_text(json.dumps(report));p.chmod(0o600)
print('RESTRICTED_ONE_TIME_GUEST_PROBE_KEY_INJECTED_PASSWORD_UNCHANGED')
