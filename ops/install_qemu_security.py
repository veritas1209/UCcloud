"""Install a pinned signed Ubuntu-derived QEMU build on an evacuated compute."""
import hashlib,json,os,subprocess,sys,xml.etree.ElementTree as ET
from pathlib import Path

REPO=Path('/var/lib/uccloud-qemu-repository/20261008')
RELEASE='1:10.2.1+ds-1ubuntu3.2+uccloud1'
REQUIRED={'CVE-2026-3886':'c035d5eadf400670593a76778f98f052d7482968',
          'CVE-2026-48914':'f5e2c6906cad9a84140e232f2e3eb7a46bf07f62'}
def run(args,**kwargs):return subprocess.run(args,check=True,**kwargs)
def sha(path):
    with Path(path).open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()

if os.geteuid()!=0:sys.exit('Root required')
assert os.uname().nodename in ('comsv-01','comsv-04','comsv-05','user-ProLiant-DL20-Gen11')
assert not subprocess.check_output(['virsh','list','--name'],text=True).strip(),'Stop VMs before installing QEMU'
manifest=json.loads((REPO/'patch-manifest.json').read_text())
assert manifest['release']==RELEASE
assert all(manifest['upstream'][cve]['commit']==commit for cve,commit in REQUIRED.items())
assert sha(REPO/'uccloud-qemu-key.asc')==sys.argv[1],'Repository key does not match the controller-pinned key'
for name,expected in manifest['packages'].items():assert sha(REPO/name)==expected,'Package checksum mismatch'
key=Path('/usr/share/keyrings/uccloud-qemu-security.gpg')
run(['gpg','--batch','--yes','--dearmor','--output',str(key),str(REPO/'uccloud-qemu-key.asc')])
key.chmod(0o644)
run(['gpgv','--keyring',str(key),str(REPO/'InRelease')])
run(['gpgv','--keyring',str(key),str(REPO/'patch-manifest.json.asc'),str(REPO/'patch-manifest.json')])
source=Path('/etc/apt/sources.list.d/uccloud-qemu-security.list')
source.write_text('deb [signed-by='+str(key)+'] file:'+str(REPO)+' ./\n');source.chmod(0o644)
pin=Path('/etc/apt/preferences.d/uccloud-qemu-security')
pin.write_text('Package: qemu-*\nPin: version '+RELEASE+'\nPin-Priority: 1001\n');pin.chmod(0o644)
run(['apt-get','update'])
installed=subprocess.check_output(['dpkg-query','-W','-f=${db:Status-Abbrev}\t${binary:Package}\t${Version}\n','qemu-*'],text=True)
names=[line.split('\t')[1] for line in installed.splitlines() if line.startswith('ii')]
assert 'qemu-system-x86' in names
# The -B Ubuntu source build produces architecture-dependent runtime packages.
# Keep Ubuntu's architecture-independent firmware/data package unchanged; it has
# no affected QEMU code and the runtime packages require only its upstream version.
names=[name for name in names if name!='qemu-system-data']
# Debug symbols remain available through the same authenticated repository.
names+=['qemu-system-x86-dbgsym','qemu-system-common-dbgsym']
run(['apt-get','install','-y','--no-install-recommends']+[name+'='+RELEASE for name in sorted(set(names))])
assert subprocess.check_output(['dpkg-query','-W','-f=${Version}','qemu-system-x86'],text=True)==RELEASE
files={Path('/usr/bin/qemu-system-x86_64')}
for name in names:
    for value in subprocess.check_output(['dpkg-query','-L',name],text=True).splitlines():
        p=Path(value)
        if p.is_file() and (str(p).startswith('/usr/lib/') and p.suffix=='.so' or str(p).startswith('/usr/bin/qemu-system-')):files.add(p)
manifest['runtime_files']={str(p):sha(p) for p in sorted(files)}
policy=Path('/etc/uccloud-hypervisor/qemu-approved.json');policy.parent.mkdir(mode=0o755,exist_ok=True)
temporary=policy.with_suffix('.next');temporary.write_text(json.dumps(manifest,indent=2));temporary.chmod(0o644);temporary.replace(policy)
print('QEMU_SIGNED_PACKAGE_AND_RUNTIME_MANIFEST_READY',RELEASE,flush=True)
