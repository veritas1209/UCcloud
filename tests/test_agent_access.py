"""Isolated API/guest tests. No cloud resources or production DB are accessed."""
import asyncio
import base64
import json
import os
import subprocess
import tempfile
import time
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import uuid4

import httpx
from cryptography.fernet import Fernet
from fastapi import FastAPI, HTTPException
from backend import database, agent_access as agent
from backend.database import db, init_db
from backend.security import token_hash


async def main():
    with tempfile.TemporaryDirectory(prefix='uccloud-agent-test-') as temp:
        root=Path(temp); home=root/'home'; home.mkdir()
        database.DB_PATH=root/'test.db'
        os.environ['CREDENTIALS_DIRECTORY']=temp
        (root/'uccloud-agent-master').write_bytes(Fernet.generate_key())
        init_db()
        key='uca_'+'A'*43
        grant_id=str(uuid4()); other_id=str(uuid4()); server_id=str(uuid4()); expires=int(time.time())+3600
        with db() as conn:
            conn.execute("INSERT INTO teams(id,name,slug,openstack_project_id) VALUES(1,'one','one','project-one'),(2,'two','two','project-two')")
            conn.execute("INSERT INTO users(id,username,email,password_hash,status,team_id) VALUES(1,'one','one@example.test','hash','active',1),(2,'two','two@example.test','hash','active',2)")
            for ident,uid,team,project,token in [(grant_id,1,1,'project-one',key),(other_id,2,2,'project-two','uca_'+'B'*43)]:
                conn.execute('INSERT INTO agent_grants(id,token_hash,user_id,team_id,project_id,server_id,vm_username,private_key,expires_at) VALUES(?,?,?,?,?,?,?,?,?)',(ident,token_hash(token),uid,team,project,server_id,'student',agent.encrypt('private-test-key'),expires))
        async def target(grant):
            return '10.30.1.10'
        agent.target=target
        class SSH:
            async def create_process(self,command,**kwargs):
                child=await asyncio.create_subprocess_exec('python3','-c',agent.SCRIPT,env=dict(os.environ,HOME=str(home)),stdin=asyncio.subprocess.PIPE,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE)
                class Process:
                    stdin=child.stdin; stdout=child.stdout; stderr=child.stderr
                    @property
                    def exit_status(self):return child.returncode
                    async def wait_closed(self):await child.wait()
                    def terminate(self):
                        if child.returncode is None:child.terminate()
                    def close(self):
                        if child.returncode is None:child.kill()
                return Process()
        @asynccontextmanager
        async def connect(grant):
            agent.valid_grant(grant['id'])
            yield SSH()
        agent.connect=connect
        jobs=[]
        async def run_job(job_id,grant,payload):
            jobs.append(job_id)
            agent.save_result(job_id,'succeeded',{'stdout':'ok','stderr':'','exit_code':0})
            agent.TASKS.pop(job_id,None)
        agent.run_job=run_job
        app=FastAPI()
        def active():return {'id':1}
        agent.register(app,active,active)
        headers={'Authorization':'Bearer '+key}
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='https://portal.test') as client:
            assert (await client.get('/api/agent/v1/context')).status_code==401
            assert (await client.get('/api/agent/v1/context?key='+key)).status_code==401
            assert (await client.get('/api/agent/v1/context',headers={'Authorization':'Bearer uca_'+'C'*43})).status_code==401
            context=await client.get('/api/agent/v1/context',headers=headers); assert context.status_code==200 and context.json()['server_id']==server_id
            assert (await client.get('/api/agent/v1/instructions',headers=headers)).status_code==200
            sudo_request={'command':'id -u','sudo':True,'request_id':str(uuid4())}
            assert (await client.post('/api/agent/v1/jobs',headers=headers,json=sudo_request)).status_code==409
            with db() as conn:
                conn.execute('UPDATE agent_grants SET private_key=? WHERE id=?',(agent.encrypt({'key':'private-test-key','sudo_password':'private-sudo-password'}),grant_id))
            assert (await client.get('/api/agent/v1/context',headers=headers)).json()['sudo_available'] is True
            sudo_result=await client.post('/api/agent/v1/jobs',headers=headers,json=sudo_request)
            assert sudo_result.status_code==202
            await asyncio.sleep(.01)
            assert (await client.post('/api/agent/v1/jobs',headers=headers,json=dict(sudo_request,sudo='true'))).status_code==422
            jobs.clear()
            request={'command':'echo ok','cwd':'.','timeout_seconds':5,'request_id':str(uuid4())}
            assert (await client.post('/api/agent/v1/jobs',headers=headers,json=dict(request,server_id=str(uuid4())))).status_code==422
            first=await client.post('/api/agent/v1/jobs',headers=headers,json=request); assert first.status_code==202,first.text
            await asyncio.sleep(.01)
            second=await client.post('/api/agent/v1/jobs',headers=headers,json=request); assert first.json()['id']==second.json()['id'] and len(jobs)==1
            conflict=await client.post('/api/agent/v1/jobs',headers=headers,json=dict(request,command='different')); assert conflict.status_code==409
            job_id=first.json()['id']
            result=await client.get('/api/agent/v1/jobs/'+job_id,headers=headers); assert result.json()['stdout']=='ok'
            assert (await client.get('/api/agent/v1/jobs/'+job_id,headers={'Authorization':'Bearer uca_'+'B'*43})).status_code==404
            assert (await client.delete('/api/instances/'+server_id+'/agent-connections/'+other_id)).status_code==404
            value='한글 $ \' \\ 파일\n'
            write=await client.post('/api/agent/v1/files',headers=headers,json={'operation':'write','path':'project/app.txt','data_base64':base64.b64encode(value.encode()).decode(),'truncate':True,'parents':True})
            assert write.status_code==200,write.text
            read=await client.post('/api/agent/v1/files',headers=headers,json={'operation':'read','path':'project/app.txt'})
            assert base64.b64decode(read.json()['data_base64']).decode()==value
            listing=await client.post('/api/agent/v1/files',headers=headers,json={'operation':'list','path':'project'}); assert listing.json()['entries'][0]['name']=='app.txt'
            for path in ('../escape','/etc/passwd','.ssh/authorized_keys'):
                denied=await client.post('/api/agent/v1/files',headers=headers,json={'operation':'write','path':path,'data_base64':'YQ=='});assert denied.status_code==409,(path,denied.text)
            (home/'link').symlink_to(root/'outside')
            denied=await client.post('/api/agent/v1/files',headers=headers,json={'operation':'write','path':'link','data_base64':'YQ=='});assert denied.status_code==409
            assert not (root/'outside').exists()
            assert (await client.post('/api/agent/v1/files',headers=headers,json={'operation':'write','path':'project/app.txt','data_base64':'not-base64'})).status_code==422
            with db() as conn:
                stored=conn.execute('SELECT * FROM agent_grants WHERE id=?',(grant_id,)).fetchone(); assert key not in str(dict(stored)) and 'private-test-key' not in stored['private_key'] and 'private-sudo-password' not in stored['private_key']
                job=conn.execute('SELECT payload,result FROM agent_jobs WHERE id=?',(job_id,)).fetchone();assert 'echo ok' not in job['payload']
                audit=' '.join(str(dict(row)) for row in conn.execute('SELECT * FROM audit_log')); assert key not in audit and 'echo ok' not in audit and value not in audit and 'private-sudo-password' not in audit
                conn.execute('UPDATE agent_grants SET expires_at=? WHERE id=?',(0,grant_id))
            assert (await client.get('/api/agent/v1/context',headers=headers)).status_code==401
            with db() as conn: conn.execute('UPDATE agent_grants SET expires_at=? WHERE id=?',(expires,grant_id))
            with db() as conn: conn.execute("UPDATE users SET status='rejected' WHERE id=1")
            assert (await client.get('/api/agent/v1/context',headers=headers)).status_code==401
            with db() as conn:conn.execute("UPDATE users SET status='active',team_id=2 WHERE id=1")
            assert (await client.get('/api/agent/v1/context',headers=headers)).status_code==401
            with db() as conn:conn.execute('UPDATE users SET team_id=1 WHERE id=1')
            assert (await client.post('/api/agent/v1/disconnect',headers=headers)).status_code==200
            assert (await client.get('/api/agent/v1/context',headers=headers)).status_code==401
        key_payload={'operation':'install_key','owner':'1','line':'restrict ssh-ed25519 TEST uccloud-agent-1'}
        result=subprocess.run(['python3','-c',agent.SCRIPT],input=json.dumps(key_payload)+'\n',env=dict(os.environ,HOME=str(home)),capture_output=True,text=True,timeout=10)
        assert result.returncode==0,result.stderr
        auth=(home/'.ssh/authorized_keys');assert auth.read_text().count('uccloud-agent-1')==1
        assert auth.stat().st_mode & 0o777==0o600
        result=subprocess.run(['python3','-c',agent.SCRIPT],input=json.dumps(key_payload)+'\n',env=dict(os.environ,HOME=str(home)),capture_output=True,text=True,timeout=10)
        assert result.returncode==0 and auth.read_text().count('uccloud-agent-1')==1
        # Isolated real subprocess command: fresh cwd, Unicode, nonzero exit.
        result=subprocess.run(['python3','-c',agent.SCRIPT],input=json.dumps({'operation':'exec','cwd':'project','command':'printf test; exit 7'})+'\n',env=dict(os.environ,HOME=str(home)),capture_output=True,text=True,timeout=10)
        assert result.returncode==7 and result.stdout=='test',result
        # Explicit cancellation kills the process group, not only the SSH channel.
        job_id='a'*64
        child=subprocess.Popen(['python3','-c',agent.SCRIPT],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,env=dict(os.environ,HOME=str(home)))
        child.stdin.write(json.dumps({'operation':'exec','job_id':job_id,'cwd':'project','command':'sleep 60','timeout_seconds':60})+'\n');child.stdin.close()
        record=home/'.uccloud-agent-jobs'/job_id
        for _ in range(100):
            if record.exists() and record.stat().st_size:break
            await asyncio.sleep(.02)
        cancel=subprocess.run(['python3','-c',agent.SCRIPT],input=json.dumps({'operation':'cancel','job_id':job_id})+'\n',env=dict(os.environ,HOME=str(home)),capture_output=True,text=True,timeout=10)
        assert cancel.returncode==0,cancel.stderr
        child.wait(timeout=5);assert child.returncode!=0 and not record.exists()
        if os.getuid()==0:
            # Isolate root-runner metadata and account HOME entirely in this temp tree.
            root_script=agent.SCRIPT.replace("account=pwd.getpwnam(value['vm_username'])", "account=type('QAAccount',(),{'pw_uid':1000,'pw_dir':"+repr(str(home))+"})()")
            root_script=root_script.replace("Path('/run/uccloud-agent-jobs')",'Path('+repr(str(root/'root-jobs'))+')')
            privileged={'protocol':'uccloud-agent-v1','operation':'exec','sudo':True,'vm_username':'student','grant_id':str(uuid4()),'job_id':'b'*64,'cwd':'project','command':'id -u','timeout_seconds':5}
            for prefix in ('','private-sudo-password\n'):
                root_result=subprocess.run(['python3','-c',root_script,'--sudo-runner'],input=prefix+json.dumps(privileged)+'\n',capture_output=True,text=True,timeout=10)
                assert root_result.returncode==0 and root_result.stdout.strip()=='0',root_result.stderr
                assert 'private-sudo-password' not in root_result.stdout+root_result.stderr
                assert not (root/'root-jobs'/privileged['grant_id']).exists()
            print('Isolated root runner, sudo/NOPASSWD stdin framing, private metadata cleanup and no password output passed')
        # Lifecycle guards are tested against this temporary DB, never real users/VMs.
        from backend import app as portal
        from backend.security import hash_password
        from starlette.requests import Request
        portal.AUTH_MIN_SECONDS=0
        old_hash=hash_password('oldpassword')
        def reset_grant():
            with db() as conn:
                conn.execute('UPDATE agent_grants SET revoked=0,private_key=? WHERE id=?',(agent.encrypt('private-test-key'),grant_id))
        def assert_revoked():
            with db() as conn:
                row=conn.execute('SELECT revoked,private_key FROM agent_grants WHERE id=?',(grant_id,)).fetchone()
                assert row['revoked']==1 and row['private_key']==''
        with db() as conn:
            conn.execute('UPDATE users SET password_hash=? WHERE id=1',(old_hash,))
            user=dict(conn.execute('SELECT * FROM users WHERE id=1').fetchone())
        reset_grant()
        request=Request({'type':'http','method':'POST','path':'/','headers':[(b'cookie',b'school_cloud_session=test-session')],'client':('127.0.0.1',1234)})
        portal.change_password(portal.ChangePasswordBody(current_password='oldpassword',new_password='newpassword'),request,user)
        assert_revoked()
        reset_grant()
        with db() as conn:
            conn.execute('INSERT INTO password_resets(token_hash,user_id,expires_at) VALUES(?,?,?)',(token_hash('A'*43),1,int(time.time())+60))
        portal.reset_password(portal.ResetPasswordBody(token='A'*43,password='anotherpassword'),request)
        assert_revoked()
        reset_grant()
        user.update(team_slug='one',openstack_project_id='project-one',network_id='network-one')
        portal.delete_server=lambda *args:None;portal.backup_busy=lambda *args:False;portal.has_backups=lambda *args:False
        from uuid import UUID
        portal.instance_delete(UUID(server_id),user);assert_revoked()
        reset_grant()
        portal.VM_BACKUPS_ENABLED=True;portal.queue_restore=lambda *args:{'ok':True}
        portal.vm_backup_restore(UUID(server_id),uuid4(),user);assert_revoked()
        print('Account password change/reset and VM delete/restore revoke agent credentials passed')
        print('Agent API authorization, isolation, idempotency, encryption, guest files/key installation and command exit code passed')


asyncio.run(main())
