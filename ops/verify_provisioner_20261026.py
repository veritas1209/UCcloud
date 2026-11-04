"""Live checks on a disposable QA team/VM. Outputs status, never credentials."""
from __future__ import annotations
import argparse
import asyncio
import hashlib
import json
import os
import secrets
import socket
import sqlite3
import subprocess
import sys
import time
import runpy
import urllib.error
import urllib.request
from pathlib import Path
from http.cookies import SimpleCookie

STATE=Path('/var/lib/uccloud-provisioner-deploy-20261026')
HOST='comsv-03.tail5819db.ts.net'
ORIGIN='https://'+HOST
BASE='http://127.0.0.1:19080'
QA=STATE/'qa-private.json'
RESULT=STATE/'verification.json'


def save(path,value):
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2))
    path.chmod(0o600)


def api(path,payload=None,method=None,expected=200):
    state=json.loads(QA.read_text()) if QA.exists() else {}
    headers={'Host':HOST,'Origin':ORIGIN,'Content-Type':'application/json'}
    if state.get('cookie'):headers['Cookie']=state['cookie']
    if state.get('csrf'):headers['X-CSRF-Token']=state['csrf']
    request=urllib.request.Request(BASE+path,data=json.dumps(payload).encode() if payload is not None else None,
                                   headers=headers,method=method or ('POST' if payload is not None else 'GET'))
    try:
        response=urllib.request.urlopen(request,timeout=165)
    except urllib.error.HTTPError as exc:
        response=exc
    with response:
        body=response.read()
        assert response.status==expected, f'API status mismatch: {path} {response.status}'
        result=json.loads(body) if body else {}
        if response.headers.get('Set-Cookie'):
            cookies=SimpleCookie();cookies.load(response.headers['Set-Cookie'])
            state['cookie']='; '.join(f'{name}={m.value}' for name,m in cookies.items())
            if result.get('csrf_token'):state['csrf']=result['csrf_token']
            save(QA,state)
        return result


def result(**values):
    state=json.loads(RESULT.read_text()) if RESULT.exists() else {}
    state.update(values)
    save(RESULT,state)
    print('VERIFIED',','.join(values),flush=True)


def check_boundary():
    from backend.provisioner_client import request
    project=None
    with sqlite3.connect('file:/var/lib/cloud-portal/portal.db?mode=ro',uri=True) as db:
        project=db.execute('SELECT openstack_project_id FROM teams WHERE openstack_project_id IS NOT NULL LIMIT 1').fetchone()[0]
    # Call as web UID, not as the inspecting root process.
    command=[str(Path('/opt/cloud-portal/.venv/bin/python')),'-c',
             'import json;from backend.provisioner_client import request;import sys;print(json.dumps(request("quota",project_id=sys.argv[1])))',project]
    env=dict(os.environ,PYTHONPATH='/opt/uccloud-releases/provisioner-20261026',PORTAL_PROVISIONER_SOCKET='/run/uccloud-provisioner/control.sock')
    quota=json.loads(subprocess.check_output(['runuser','-u','cloud-portal','--',*command],env=env,text=True))
    assert set(quota)=={'instances','cores','ram'}
    for unit in ['cloud-portal-v2','cloud-portal-public-v2']:
        pid=subprocess.check_output(['systemctl','show',unit,'-p','MainPID','--value'],text=True).strip()
        process_env={p.split(b'=',1)[0].decode():p.split(b'=',1)[1] for p in Path('/proc/'+pid+'/environ').read_bytes().split(b'\0') if b'=' in p}
        assert not {'OS_USERNAME','OS_PASSWORD','PORTAL_ADMIN_PASSWORD'} & process_env.keys()
        assert process_env.get('PORTAL_PROVISIONER_SOCKET')
        for path in ['/etc/cloud-portal/portal.env','/etc/cloud-portal/provisioner.env']:
            completed=subprocess.run(['nsenter','-t',pid,'-m','--','runuser','-u','cloud-portal','--','test','-r',path])
            assert completed.returncode!=0
    # Unprivileged users must not even connect to the Unix socket.
    command=['runuser','-u','nobody','--','/usr/bin/python3','-c',
             'import socket;s=socket.socket(socket.AF_UNIX);s.connect("/run/uccloud-provisioner/control.sock")']
    assert subprocess.run(command,capture_output=True).returncode!=0
    malformed=[{'op':'delete_project'},{'op':'capacity','command':'id'},
               {'op':'ensure_team','team_id':1,'quota':999999}]
    for payload in malformed:
        with socket.socket(socket.AF_UNIX) as conn:
            conn.settimeout(5);conn.connect('/run/uccloud-provisioner/control.sock')
            conn.sendall(json.dumps(payload).encode()+b'\n')
            answer=json.loads(conn.makefile('rb').readline())
            assert answer=={'ok':False,'error':'unavailable'}
    assert set(request('capacity'))=={'online','total','memory_free_mb'}
    result(web_admin_environment_absent=True,web_private_files_denied=True,
           untrusted_peer_denied=True,broker_malformed_requests_denied=True,real_quota_capacity=True)


def create():
    existed=QA.exists()
    if existed:
        state=json.loads(QA.read_text())
    else:
        suffix=secrets.token_hex(4)
        state={'username':'privqa'+suffix,'password':secrets.token_urlsafe(20),'team':'privsep-qa-'+suffix,
               'vm_password':secrets.token_urlsafe(20),'vm_name':'privsep-qa-'+suffix}
        save(QA,state)
        pid=subprocess.check_output(['systemctl','show','cloud-portal-public-v2','-p','MainPID','--value'],text=True).strip()
        process_env=dict(p.split(b'=',1) for p in Path('/proc/'+pid+'/environ').read_bytes().split(b'\0') if b'=' in p)
        registration=api('/api/auth/register',{'username':state['username'],'email':state['username']+'@example.com',
            'team_name':state['team'],'password':state['password'],'registration_code':process_env.get(b'PORTAL_REGISTRATION_CODE',b'').decode()},expected=201)
        assert registration['ready'] is True,'Team provisioning failed'
    api('/api/auth/login',{'username':state['username'],'password':state['password']})
    me=api('/api/me')
    if me['status']=='pending':
        assert api('/api/team/provision',{})['ready'] is True
        result(real_team_provision_retry=True)
    quota=api('/api/quota')
    assert quota['instances']['in_use']==0
    catalog=api('/api/catalog')
    image=next(x for x in catalog['images'] if x['name']=='ubuntu-24.04')
    flavor=min(catalog['flavors'],key=lambda x:(x['ram'],x['vcpus']))
    vm=api('/api/instances',{'name':state['vm_name'],'image_id':image['id'],'flavor_id':flavor['id'],
        'vm_username':'student','vm_password':state['vm_password'],'software':[]},expected=202)
    state=json.loads(QA.read_text());state['server_id']=vm['id'];save(QA,state)
    wait_vm('ACTIVE')
    assert api('/api/quota')['instances']['in_use']==1
    assert api('/api/instances/'+vm['id']+'/console',{})['url']
    result(real_signup_project=True,real_login=True,real_vm_create=True,real_console_url=True)


def wait_vm(status,timeout=360):
    state=json.loads(QA.read_text())
    deadline=time.monotonic()+timeout
    while time.monotonic()<deadline:
        servers=api('/api/instances')['instances']
        vm=next((x for x in servers if x['id']==state['server_id']),None)
        if status=='DELETED' and vm is None:return
        if vm and vm['status']==status:return
        if vm and vm['status']=='ERROR':raise RuntimeError('QA VM entered ERROR')
        time.sleep(3)
    raise TimeoutError('QA VM did not reach target state')


async def shell(hold=False,command=None,expected=None):
    import websockets
    state=json.loads(QA.read_text())
    connect_options={'host':'127.0.0.1','port':19080}
    uri='ws://'+HOST+'/api/instances/'+state['server_id']+'/terminal'
    if hold:
        public=runpy.run_path('/var/lib/uccloud-maintenance/20261008/public_funnel_probe.py')
        connect_options={'host':public['public_addresses']()[0],'port':443,'server_hostname':HOST}
        uri='wss://'+HOST+'/api/instances/'+state['server_id']+'/terminal'
    async with websockets.connect(uri,
            **connect_options,additional_headers={'Cookie':state['cookie']},
            subprotocols=['uccloud','csrf.'+state['csrf']],
            origin=ORIGIN,open_timeout=15,max_size=1048576) as ws:
        await ws.send(json.dumps({'username':'student','password':state['vm_password'],'cols':100,'rows':30}))
        deadline=time.monotonic()+180
        while time.monotonic()<deadline:
            raw=await asyncio.wait_for(ws.recv(),180)
            if isinstance(raw,bytes):continue
            message=json.loads(raw)
            if message.get('type')=='ready':break
            if message.get('type')=='error':raise RuntimeError('QA browser SSH failed')
        else:raise TimeoutError('QA browser SSH not ready')
        await ws.send(json.dumps({'type':'input','data':command or "printf '\\nPRIVSEP_%s\\n' VERIFIED\n"}))
        output=''
        while (expected or 'PRIVSEP_VERIFIED') not in output:
            raw=await asyncio.wait_for(ws.recv(),30)
            if isinstance(raw,bytes):output+=raw.decode('utf-8',errors='replace')
            elif json.loads(raw).get('type')=='error':raise RuntimeError('QA shell command failed')
        if hold:
            result(websocket_hold_ready=True)
            deadline=time.monotonic()+300
            while not (STATE/'switched.json').exists() and time.monotonic()<deadline:
                await asyncio.sleep(1)
            assert (STATE/'switched.json').exists()
            await ws.send(json.dumps({'type':'input','data':"printf '\\nAFTER_%s\\n' CUTOVER\n"}))
            output=''
            while 'AFTER_CUTOVER' not in output:
                raw=await asyncio.wait_for(ws.recv(),30)
                if isinstance(raw,bytes):output+=raw.decode('utf-8',errors='replace')
                elif json.loads(raw).get('type')=='error':raise RuntimeError('QA shell command failed')
            result(websocket_survived_cutover=True)
        else:
            result(real_websocket_shell=True)


def cleanup():
    state=json.loads(QA.read_text())
    if state.get('server_id'):
        api('/api/instances/'+state['server_id'],method='DELETE',expected=202)
        wait_vm('DELETED')
    api('/api/auth/logout',{})
    # Only this randomly named QA team's cloud resources are removed.
    from deploy_provisioner_20261026 import load_env
    os.environ.update(load_env(Path('/etc/cloud-portal/portal.env')))
    from backend import openstack_service as cloud
    from backend.database import db
    with db() as conn:
        row=conn.execute('SELECT t.* FROM teams t JOIN users u ON u.team_id=t.id WHERE u.username=? AND t.name=?',
                         (state['username'],state['team'])).fetchone()
    assert row and row['name'].startswith('privsep-qa-')
    team=cloud.team_connection(row['slug'])
    assert not list(team.compute.servers())
    admin=cloud.admin_connection()
    admin.network.remove_interface_from_router(row['router_id'],subnet_id=row['subnet_id'])
    for router in team.network.routers():
        raise RuntimeError('Unexpected QA router')
    team.network.delete_subnet(row['subnet_id'],ignore_missing=False)
    team.network.delete_network(row['network_id'],ignore_missing=False)
    admin.identity.delete_project(row['openstack_project_id'],ignore_missing=False)
    with db() as conn:
        user=conn.execute('SELECT id FROM users WHERE username=? AND team_id=?',(state['username'],row['id'])).fetchone()
        conn.execute('DELETE FROM sessions WHERE user_id=?',(user['id'],))
        conn.execute('UPDATE audit_log SET user_id=NULL WHERE user_id=?',(user['id'],))
        conn.execute('DELETE FROM instance_ssh_host_keys WHERE server_id=?',(state.get('server_id',''),))
        conn.execute('DELETE FROM users WHERE id=?',(user['id'],))
        conn.execute('DELETE FROM teams WHERE id=?',(row['id'],))
    QA.unlink()
    result(qa_resources_cleaned=True)


def monitor():
    public=runpy.run_path('/var/lib/uccloud-maintenance/20261008/public_funnel_probe.py')
    addresses=public['public_addresses']()
    started=time.time();count=0;failed=0;external=0;external_failed=0
    while not (STATE/'monitor-stop').exists() and time.time()-started<900:
        try:api('/api/health');count+=1
        except Exception:failed+=1
        if (count+failed)%4==0:
            try:
                assert public['public_get']('/api/health',addresses)[0]==200
                external+=1
            except Exception:external_failed+=1
        save(STATE/'continuity.json',{'samples':count,'failures':failed,'public_samples':external,
             'public_failures':external_failed,'elapsed_seconds':round(time.time()-started,1)})
        time.sleep(.5)


def operator():
    state=json.loads(QA.read_text())
    # Temporary role on this disposable QA account only; never change a real
    # administrator's password or issue a session for an existing account.
    conn=sqlite3.connect('/var/lib/cloud-portal/portal.db')
    row=None
    try:
        row=conn.execute('SELECT id,role FROM users WHERE username=?',(state['username'],)).fetchone()
        assert row and row[1]=='member' and state['username'].startswith('privqa')
        conn.execute("UPDATE users SET role='admin' WHERE id=?",(row[0],));conn.commit()
        req=urllib.request.Request('http://127.0.0.1:19088/api/admin/capacity',
             headers={'Host':'localhost','Cookie':state['cookie']})
        with urllib.request.urlopen(req,timeout=40) as response:
            data=json.load(response)
            assert response.status==200 and data
        api('/api/admin/capacity',expected=404)
    finally:
        if row:
            conn.execute("UPDATE users SET role='member' WHERE id=?",(row[0],));conn.commit()
        conn.close()
    result(private_operator_capacity=True,public_admin_route_absent=True)


def backup():
    state=json.loads(QA.read_text());sid=state['server_id']
    marker='UCCLOUD_PRIVSEP_BACKUP_VERIFIED\n'
    digest=hashlib.sha256(marker.encode()).hexdigest()
    asyncio.run(shell(command="printf '%s\\n' UCCLOUD_PRIVSEP_BACKUP_VERIFIED > ~/privsep-proof.txt; sha256sum ~/privsep-proof.txt\n",expected=digest))
    api('/api/instances/'+sid+'/action',{'action':'stop'},expected=202);wait_vm('SHUTOFF')
    value=api('/api/instances/'+sid+'/backups',{},expected=202);bid=value['id']
    def wait_backup(key,wanted):
        until=time.monotonic()+900
        while time.monotonic()<until:
            rows=api('/api/backups')['backups']
            row=next((x for x in rows if x['id']==bid),None)
            if key=='deleted':
                if row is None:return
                time.sleep(4)
                continue
            if row and row[key]==wanted:return
            if row and (row['state']=='failed' or row['restore_state']=='failed'):
                raise RuntimeError('QA backup/restore failed')
            time.sleep(4)
        raise TimeoutError('QA backup worker timeout')
    wait_backup('state','ready')
    result(real_backup_via_broker=True)
    api('/api/instances/'+sid+'/action',{'action':'start'},expected=202);wait_vm('ACTIVE');time.sleep(12)
    changed=hashlib.sha256(b'CHANGED\n').hexdigest()
    asyncio.run(shell(command="printf '%s\\n' CHANGED > ~/privsep-proof.txt; sha256sum ~/privsep-proof.txt\n",expected=changed))
    api('/api/instances/'+sid+'/action',{'action':'stop'},expected=202);wait_vm('SHUTOFF')
    api('/api/instances/'+sid+'/backups/'+bid+'/restore',{},expected=202)
    wait_backup('restore_state','idle')
    api('/api/instances/'+sid+'/action',{'action':'start'},expected=202);wait_vm('ACTIVE');time.sleep(12)
    asyncio.run(shell(command='sha256sum ~/privsep-proof.txt\n',expected=digest))
    api('/api/instances/'+sid+'/backups/'+bid,method='DELETE',expected=202)
    wait_backup('deleted',None)
    result(real_restore_file_hash_match=True,qa_backup_removed=True)


def finish_backup():
    # Resume only the final assertion/cleanup after an interrupted test runner;
    # do not create another VM or overwrite another backup.
    assert not api('/api/backups')['backups']
    digest=hashlib.sha256(b'UCCLOUD_PRIVSEP_BACKUP_VERIFIED\n').hexdigest()
    asyncio.run(shell(command='sha256sum ~/privsep-proof.txt\n',expected=digest))
    result(real_restore_file_hash_match=True,qa_backup_removed=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('phase',choices=['boundary','create','shell','hold','cleanup','monitor','backup','operator','finish_backup'])
    phase=parser.parse_args().phase
    if phase=='boundary':check_boundary()
    elif phase=='create':create()
    elif phase=='shell':asyncio.run(shell())
    elif phase=='hold':asyncio.run(shell(True))
    else:globals()[phase]()
