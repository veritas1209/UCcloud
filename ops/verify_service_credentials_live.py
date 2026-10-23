"""Disposable first-boot integration test, using a marked VM with spare quota only."""
import asyncio
import json
import os
import secrets
import shlex
import subprocess
import time
from pathlib import Path
from uuid import uuid4

for filename in ('/etc/cloud-portal/portal.env','/etc/cloud-portal/public.env'):
    for line in Path(filename).read_text().splitlines():
        if line.strip() and not line.lstrip().startswith('#') and '=' in line:
            name,value=line.split('=',1); parsed=shlex.split(value); os.environ[name]=parsed[0] if parsed else ''
pid=subprocess.check_output(['systemctl','show','-p','MainPID','--value','cloud-portal-public'],text=True).strip()
for entry in Path('/proc/'+pid+'/environ').read_bytes().split(b'\0'):
    if b'=' in entry:
        name,value=entry.decode().split('=',1)
        if name.startswith(('OS_','PORTAL_ALLOWED_')) or name in ('PORTAL_DB','PORTAL_SHARED_ROUTER','PORTAL_DNS_SERVERS','PORTAL_BROWSER_SSH_SOURCE','PORTAL_SOFTWARE_PRESETS_ENABLED'): os.environ[name]=value

import asyncssh
from backend.database import db
from backend import openstack_service as cloud
from backend.software import BY_ID

STATE=Path('/var/lib/cloud-portal/credentials-qa-state.json')
MARKER=str(uuid4())
PASSWORD=secrets.token_urlsafe(24)
SERVICE_PASSWORD=secrets.token_hex(12)+"$'\\test"
IDS=('postgres','mysql','maria','mongo','redis','xampp','nginx')
SELECTIONS=[dict(id=key,version=BY_ID[key]['versions'][0],**({'username':'qa_user','password':SERVICE_PASSWORD} if key!='nginx' else {})) for key in IDS]

PROBE=r'''
import json,os,subprocess
from pathlib import Path
c=json.loads(Path('/etc/uccloud-service-credentials.json').read_text())
prefix=['docker','compose','-p','uccloud','-f','/opt/uccloud-services/compose.yml','exec','-T']
checks={
'postgres':['sh','-c','PGPASSWORD="$POSTGRES_PASSWORD" psql -h127.0.0.1 -U"$POSTGRES_USER" -d app -tAc "SELECT current_user"'],
'mysql':['sh','-c','MYSQL_PWD="$UCCLOUD_DB_PASSWORD" mysql -u"$UCCLOUD_DB_USER" -Dapp -N -B -e "SELECT CURRENT_USER()"'],
'maria':['sh','-c','MYSQL_PWD="$UCCLOUD_DB_PASSWORD" mariadb -u"$UCCLOUD_DB_USER" -Dapp -N -B -e "SELECT CURRENT_USER()"'],
'mongo':['mongosh','--quiet','--eval','db.getSiblingDB("admin").auth(process.env.MONGO_INITDB_ROOT_USERNAME,process.env.MONGO_INITDB_ROOT_PASSWORD); print(db.runCommand({connectionStatus:1}).authInfo.authenticatedUsers[0].user)'],
'redis':['sh','-c','redis-cli --user "$REDISCLI_USER" ACL WHOAMI']}
for key,args in checks.items():
 r=subprocess.run(prefix+[key]+args,text=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=30)
 if r.returncode or 'qa_user' not in r.stdout: raise RuntimeError(key+' custom authentication failed')
 print(key+': chosen ID/password authenticated')
env=dict(os.environ,MYSQL_PWD=c['xampp']['password'])
r=subprocess.run(['/opt/lampp/bin/mysql','--no-defaults','-uqa_user','--protocol=SOCKET','--socket=/opt/lampp/var/mysql/mysql.sock','-Dapp','-N','-B','-e','SELECT CURRENT_USER()'],env=env,text=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=15)
if r.returncode or 'qa_user' not in r.stdout: raise RuntimeError('xampp custom authentication failed')
print('xampp: chosen ID/password authenticated')
env['MYSQL_PWD']='definitely-wrong-password'
r=subprocess.run(['/opt/lampp/bin/mysql','--no-defaults','-uqa_user','--protocol=SOCKET','--socket=/opt/lampp/var/mysql/mysql.sock','-Dapp','-e','SELECT 1'],env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=15)
assert r.returncode != 0
assert (Path('/etc/uccloud-service-credentials.json').stat().st_mode & 0o777)==0o600
guide=Path('/home/student/UCcloud-설치안내.txt').read_text()
assert all(value['password'] not in guide for value in c.values())
r=subprocess.run(['curl','-fsS','http://127.0.0.1:8082/'],capture_output=True,text=True,timeout=10)
assert r.returncode==0 and '준비 완료' in r.stdout
print('Wrong password denied; private credential modes, secret-free guide and Nginx HTTP passed')
'''

async def main():
    if STATE.exists(): raise RuntimeError('Earlier QA state exists; no resources changed')
    with db() as database:
        row=database.execute("SELECT t.* FROM teams t JOIN users u ON t.id=u.team_id WHERE u.username='gimpengtu' COLLATE NOCASE AND u.status='active'").fetchone()
    if not row: raise RuntimeError('Expected managed test project missing')
    team=dict(row); conn=cloud.team_connection(team['slug'])
    flavors=sorted([value for value in conn.compute.flavors() if value.name in cloud.ALLOWED_FLAVORS and value.ram>=8192],key=lambda value:(value.ram,value.vcpus))
    quota=cloud.quota(team['openstack_project_id'])
    flavor=next((value for value in flavors if all(quota[key]['limit']<0 or quota[key]['limit']-quota[key]['in_use']>=amount for key,amount in {'instances':1,'cores':value.vcpus,'ram':value.ram}.items())),None)
    if not flavor: raise RuntimeError('No spare QA quota; no VM created')
    image=next(value for value in conn.image.images(status='active',visibility='public') if value.name=='ubuntu-24.04')
    result=cloud.create_server(team['slug'],team['openstack_project_id'],'verification',team['network_id'],'uccloud-credentials-qa-'+MARKER[:8],image.id,flavor.id,'student',PASSWORD,request_id=MARKER,software=SELECTIONS)
    vm_id=result['id']
    with os.fdopen(os.open(STATE,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600),'w') as output: json.dump({'id':vm_id,'marker':MARKER,'project':team['openstack_project_id']},output)
    print('Created only marked disposable QA VM: '+vm_id,flush=True)
    try:
        await asyncio.to_thread(conn.compute.wait_for_server,conn.compute.get_server(vm_id),wait=180)
        ssh=None
        for _ in range(40):
            ports=list(conn.network.ports(device_id=vm_id,network_id=team['network_id']))
            addresses=[entry['ip_address'] for port in ports for entry in port.fixed_ips if ':' not in entry['ip_address']]
            if len(addresses)==1:
                try:
                    ssh=await asyncssh.connect(addresses[0],username='student',password=PASSWORD,known_hosts=None,client_keys=[],connect_timeout=8,login_timeout=10); break
                except (OSError,asyncssh.Error): pass
            await asyncio.sleep(5)
        if not ssh: raise RuntimeError('QA VM SSH unavailable')
        async with ssh:
            previous=None
            for _ in range(180):
                result=await ssh.run('cat /var/lib/uccloud-software/status.json',check=False)
                state=json.loads(result.stdout) if result.stdout.strip() else {}
                if state.get('message')!=previous:
                    previous=state.get('message'); print('Guest: '+str(previous),flush=True)
                if state.get('state')=='ready': break
                if state.get('state')=='failed':
                    diagnostic = "import json,subprocess\nfrom pathlib import Path\ntext=subprocess.run(['journalctl','-u','uccloud-software-install.service','-n','45','--no-pager'],capture_output=True,text=True).stdout\nfor path in ('/etc/uccloud-service-credentials.json','/opt/uccloud-services/passwords.json'):\n try:\n  data=json.loads(Path(path).read_text())\n  values=[value.get('password','') if isinstance(value,dict) else value for value in data.values()]\n  for secret in values:\n   if isinstance(secret,str) and secret: text=text.replace(secret,'[REDACTED]').replace(repr(secret),'[REDACTED]')\n except (OSError,ValueError): pass\nprint(text)"
                    diagnostic_result = await ssh.run('sudo -S -k -p \'\' python3 -c '+shlex.quote(diagnostic), input=PASSWORD+'\n',check=False,timeout=20)
                    print(diagnostic_result.stdout,flush=True)
                    raise RuntimeError('QA first-boot installation failed; secret-redacted diagnostic above')
                await asyncio.sleep(10)
            else: raise RuntimeError('QA installation timed out')
            result=await ssh.run('sudo -S -k -p \'\' python3 -c '+shlex.quote(PROBE),input=PASSWORD+'\n',check=False,timeout=180)
            if result.exit_status: raise RuntimeError('Guest custom authentication probe failed: '+result.stdout+result.stderr[-800:])
            print(result.stdout,flush=True)
            shell=await ssh.create_process(term_type='xterm',term_size=(120,35))
            await asyncio.sleep(1); shell.stdin.write('exit\n'); login=await shell.wait(timeout=15)
            assert 'UCcloud · 내 서버' in login.stdout and 'uccloud nginx -h' in login.stdout
            assert 'Enable ESM Apps' not in login.stdout and 'ABSOLUTELY NO WARRANTY' not in login.stdout and 'sudo <command>' not in login.stdout
            print('Actual first SSH login: concise Korean service list, no Ubuntu ESM/legal/admin hints.',flush=True)
            print('ALL SEVEN PRESETS AND SIX CUSTOM CREDENTIALS VERIFIED',flush=True)
    finally:
        server=conn.compute.get_server(vm_id)
        if server and server.project_id==team['openstack_project_id'] and (server.metadata or {}).get('portal_request_id')==MARKER:
            conn.compute.delete_server(server,ignore_missing=False)
            conn.compute.wait_for_delete(server,wait=180)
            STATE.unlink(); print('Only this run\'s marked QA VM deleted.',flush=True)

if __name__=='__main__': asyncio.run(main())
