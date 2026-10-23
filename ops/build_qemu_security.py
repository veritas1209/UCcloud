"""Build Ubuntu QEMU source with only audited upstream fixes; never overwrite a binary."""
import hashlib,json,os,pwd,re,shutil,subprocess,sys,urllib.request
from pathlib import Path

VERSION='1:10.2.1+ds-1ubuntu3.2'
RELEASE=VERSION+'+uccloud1'
WORK=Path('/var/lib/uccloud-qemu-build')
COMMITS={
 'CVE-2026-3886':'c035d5eadf400670593a76778f98f052d7482968',
 'CVE-2026-48914':'f5e2c6906cad9a84140e232f2e3eb7a46bf07f62',
 'CVE-2026-5761':'4913ae36f9796c55d434dcbfa6bdb9ebb3e5e4b1',
 'CVE-2026-93834':'fe9e30899ffceef25cf345ec83f76546afca19dd',
}
def run(args,**kwargs):return subprocess.run(args,check=True,**kwargs)
def fetch(url):
    return urllib.request.urlopen(urllib.request.Request(url,headers={'User-Agent':'UCcloud-security-maintenance'}),timeout=45).read()
if os.geteuid()!=0:sys.exit('Run on the empty canary as root')
assert subprocess.check_output(['hostname'],text=True).strip()=='comsv-01'
assert not subprocess.check_output(['virsh','list','--name'],text=True).strip(),'Canary must have no running VMs'
assert os.uname().release=='7.0.0-38-generic','Verify the canary kernel first'
assert Path('/sys/module/kvm_intel/parameters/nested').read_text().strip()=='N'
WORK.mkdir(mode=0o750,exist_ok=True)
sources=Path('/etc/apt/sources.list.d/uccloud-qemu-build.sources')
if not sources.exists():
    original=Path('/etc/apt/sources.list.d/ubuntu.sources').read_text()
    assert 'Types: deb' in original
    sources.write_text(original.replace('Types: deb','Types: deb-src'));sources.chmod(0o644)
run(['apt-get','update'])
run(['apt-get','install','-y','--no-install-recommends','build-essential','dpkg-dev','devscripts','quilt','gnupg','fakeroot','apt-utils'])
run(['apt-get','build-dep','-y',f'qemu={VERSION}'])
try:user=pwd.getpwnam('uccloud-build')
except KeyError:
    run(['useradd','--system','--home-dir',str(WORK),'--shell','/usr/sbin/nologin','uccloud-build']);user=pwd.getpwnam('uccloud-build')
os.chown(WORK,user.pw_uid,user.pw_gid)
rollback=WORK/'rollback';rollback.mkdir(exist_ok=True);os.chown(rollback,user.pw_uid,user.pw_gid)
installed=subprocess.check_output(['dpkg-query','-W','-f=${db:Status-Abbrev}\t${binary:Package}\t${Version}\n','qemu-*'],text=True)
for line in installed.splitlines():
    status,name,version=line.split('\t')
    if status.startswith('ii') and version==VERSION:
        run(['runuser','-u','uccloud-build','--','apt-get','download',name+'='+version],cwd=rollback)
run(['runuser','-u','uccloud-build','--','apt-get','source',f'qemu={VERSION}'],cwd=WORK)
directories=[p for p in WORK.glob('qemu-*') if p.is_dir() and (p/'debian/changelog').exists()]
assert len(directories)==1,'Unexpected source directories; do not reuse a partial build'
source=directories[0]
manifest={'ubuntu_source':VERSION,'release':RELEASE,'upstream':{}}
patch_dir=source/'debian/patches/uccloud';patch_dir.mkdir(exist_ok=True)
for cve,commit in COMMITS.items():
    patch=fetch('https://github.com/qemu/qemu/commit/'+commit+'.patch')
    assert patch.startswith(b'From '+commit.encode()+b' ')
    assert cve.encode() in patch or cve=='CVE-2026-3886'
    destination=patch_dir/(cve+'.patch');destination.write_bytes(patch)
    manifest['upstream'][cve]={'commit':commit,'sha256':hashlib.sha256(patch).hexdigest()}
    # Fail closed if the exact upstream fix does not apply; never silently adapt unrelated source.
    run(['patch','--dry-run','--fuzz=0','-p1','-i',str(destination)],cwd=source)
    with (source/'debian/patches/series').open('a') as stream:stream.write('\nuccloud/'+cve+'.patch\n')
    run(['quilt','push'],cwd=source,env={**os.environ,'QUILT_PATCHES':'debian/patches'})
(WORK/'patch-manifest.json').write_text(json.dumps(manifest,indent=2))
env={**os.environ,'DEBFULLNAME':'UCcloud Security Maintenance','DEBEMAIL':'uccloud-security@localhost','HOME':str(WORK),
     'DEB_BUILD_OPTIONS':'nocheck'}  # Upstream vulnerability-input regression tests are explicitly out of scope.
run(['dch','--newversion',RELEASE,'--distribution','resolute','UCcloud upstream-only security backports: '+', '.join(COMMITS)],cwd=source,env=env)
run(['chown','-R','uccloud-build:uccloud-build',str(source)])
run(['runuser','-u','uccloud-build','--','dpkg-buildpackage','-B','-us','-uc','-j6'],cwd=source,env=env)
packages=list(WORK.glob('*.deb'))+list(WORK.glob('*.ddeb'))
assert packages,'No packages produced'
repo=WORK/'repository';repo.mkdir(mode=0o755)
for package in packages:shutil.copy2(package,repo/package.name)
manifest['packages']={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in packages}
(repo/'patch-manifest.json').write_text(json.dumps(manifest,indent=2))
gnupg=Path('/var/lib/uccloud-qemu-signing');gnupg.mkdir(mode=0o700)
gpg=['gpg','--homedir',str(gnupg),'--batch','--pinentry-mode','loopback','--passphrase','']
run(gpg+['--quick-generate-key','UCcloud QEMU Security Repository <uccloud-security@localhost>','rsa3072','sign','1y'])
with (repo/'uccloud-qemu-key.asc').open('wb') as stream:run(gpg+['--armor','--export'],stdout=stream)
with (repo/'Packages').open('wb') as stream:run(['dpkg-scanpackages','--multiversion','.'],cwd=repo,stdout=stream)
with (repo/'Release').open('wb') as stream:
    run(['apt-ftparchive','-o','APT::FTPArchive::Release::Origin=UCcloud','-o','APT::FTPArchive::Release::Label=UCcloud-QEMU-Security','release','.'],cwd=repo,stdout=stream)
run(gpg+['--armor','--detach-sign','--output',str(repo/'Release.gpg'),str(repo/'Release')])
run(gpg+['--clearsign','--output',str(repo/'InRelease'),str(repo/'Release')])
for package in (p for p in WORK.glob('*.changes')):
    run(gpg+['--clearsign','--output',str(package)+'.asc',str(package)])
(WORK/'BUILD_READY').write_text(RELEASE+'\n')
print('SIGNED_QEMU_REPOSITORY_READY',RELEASE,flush=True)
