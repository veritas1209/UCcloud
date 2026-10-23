"""One release only: verify sources, save recoverable backup, deploy, check health."""
from pathlib import Path
import datetime, hashlib, json, os, shutil, sqlite3, subprocess, sys, tarfile, time, urllib.request

stage = Path('/tmp/uccloud-vm-ui-20261008.lIwzgp')
root = Path('/opt/cloud-portal')
original = {
 'backend/terminal.py':'b46564044ac53dd74f738a75ebb48228563c27bc152feb07fa170b05d2f1ea59',
 'backend/guest_software.py':'e965e766c979ffd57f7f4a68b05fcd781566bf079713465b426cffdcfcb8a818',
 'backend/app.py':'5cf5fe1c28a88ea7c5f15da321ae8b518a1de0a7ee10528f0d92e5465671e368',
 'backend/database.py':'0c040e2e75491eb5a1f223cc8ebde97e09fc141718de656f46818baa435a8eb7',
 'static/terminal.js':'742c163d1d4e1bbbe3c65e8d328149efec58cea66f0a428c42fae8d879864f76',
 'static/app.js':'b02620966b9ef98ef101138d89ed7dbfaff1bdd639e51bd4d3da6071b3093b6a',
 'static/styles.css':'ab342ce5653d6b7b559254610d5c813a59528a9e53548ad0ad9d110feda7b01a',
 'static/terminal.html':'55eb10890da91f3886e987c903e73c9033841a54622fcdb4dfe93712d49f2abf',
 'static/index.html':'c6e5549bcf61313db7dabc5854429e5b36cf6948ee1c966d33e0381ea20c2286',
 'static/terminal.css':'407b329e410e221a417998d1ef9f97655003c2cd75dc4d22fc48f3e6ef1768be',
}
release = {
 'backend/terminal.py':'328bf4e8b0aa7c16d8d0296b345c2dbb121a23392e632e1e3817463d29417806',
 'backend/guest_software.py':'831c380024df69d3bf2f5d2b595e62473e8bdc2b4950a8e985d9baf2916d2f0f',
 'backend/app.py':'6bd4dbc51a3353a76496df53d1b65fcbe2d9c3ae66c174a7afd0a20d069e98ef',
 'backend/database.py':'2eee191ce06d28baca610b49d57019362c9fb376108ee7ea986da89f47565388',
 'backend/software_install.py':'5c00578175ef298a8035087dc3d575c13834aa6db467527d86562673c75ad16b',
 'backend/software_state.py':'beee701b2fb340c71de831465874b1534884c0e6ed0e93e11e7a8a0f416c19bd',
 'static/terminal.js':'45d2047f1ba8387b68eef92bca53e2374d926c3f6a622479dc14b4e874866bbd',
 'static/terminal-software.js':'3ac63c21ecbf62cf750d8aedb41a7d8c9a37f38c90660e85939177789d75868b',
 'static/terminal.css':'ce2f884849f6c8d0a5c71b7e8e9dcff07eb8caada0746cda313c9524643ab2a0',
 'static/app.js':'30c60518bd66c6b4917fe5a43f61098bcf4d74f594b6e8338253ae34a97c6884',
 'static/styles.css':'26b05d0fef0fcfdff85694be3fec87e320ea703ea4947f9eef459c855c0e1b5f',
 'static/terminal.html':'69a8069e1c2594d0d9653f9645c65d4b2c2c650a8eb464d90d1958e0e6f7849b',
 'static/index.html':'210ad0fe9f5220dd8d19c56b9a5705dc4ff25b1781bc80e5183f7d3046fe6fa6',
}
def digest(path): return hashlib.sha256(path.read_bytes()).hexdigest()
for name, expected in original.items():
    if digest(root/name) != expected: sys.exit('ABORT: production source changed: '+name)
for name, expected in release.items():
    if digest(stage/name) != expected: sys.exit('ABORT: staged source differs: '+name)
    if name not in original and (root/name).exists(): sys.exit('ABORT: new file already exists: '+name)
    if name.endswith('.py'): compile((stage/name).read_bytes(), name, 'exec')
pid = subprocess.check_output(['systemctl','show','cloud-portal-public','-p','MainPID','--value'],text=True).strip()
runtime = dict(entry.split(b'=',1) for entry in Path('/proc/'+pid+'/environ').read_bytes().split(b'\0') if b'=' in entry)
db_path = Path(runtime.get(b'PORTAL_DB',b'/var/lib/cloud-portal/portal.db').decode())
backup = Path('/var/backups')/('uccloud-vm-ui-'+datetime.datetime.now().strftime('%Y%m%d-%H%M%S'))
backup.mkdir(mode=0o700)
os.chmod(backup,0o700)
with tarfile.open(backup/'source.tar','w') as archive:
    for name in original: archive.add(root/name,arcname=name)
with sqlite3.connect('file:'+str(db_path)+'?mode=ro',uri=True) as source, sqlite3.connect(backup/'portal.db') as target:
    source.backup(target)
os.chmod(backup/'portal.db',0o600)
(backup/'manifest.json').write_text(json.dumps(dict(original=original,release=release)))
print('BACKUP:',backup,flush=True)
try:
    for name in release:
        shutil.copyfile(stage/name,root/name)
        os.chmod(root/name,0o644)
    subprocess.run(['systemctl','restart','cloud-portal','cloud-portal-public'],check=True)
    for unit,port,host in [('cloud-portal',8088,'localhost'),('cloud-portal-public',18080,'comsv-03.tail5819db.ts.net')]:
        subprocess.run(['systemctl','is-active','--quiet',unit],check=True)
        request=urllib.request.Request('http://127.0.0.1:'+str(port)+'/api/health',headers={'Host':host})
        for attempt in range(15):
            try:
                with urllib.request.urlopen(request,timeout=3) as response:
                    if response.status!=200: raise RuntimeError('Health check failed')
                break
            except Exception:
                if attempt==14: raise
                time.sleep(2)
        print('HEALTHY:',unit,flush=True)
except Exception:
    # Restore only this exact release's source files. Leave the additive DB schema and user data intact.
    with tarfile.open(backup/'source.tar','r') as archive:
        for name in original:
            with archive.extractfile(name) as stream: (root/name).write_bytes(stream.read())
            os.chmod(root/name,0o644)
    for name in release.keys()-original.keys(): (root/name).unlink()
    subprocess.run(['systemctl','restart','cloud-portal','cloud-portal-public'],check=False)
    raise
print('DEPLOYED:',len(release),'verified files',flush=True)
