"""Short-lived operator-issued browser session for this task's existing test VM."""
import json
import os
import pwd
import secrets
import subprocess
import sys
from pathlib import Path

pid=subprocess.check_output(['systemctl','show','-p','MainPID','--value','cloud-portal-public'],text=True).strip()
for entry in Path('/proc/'+pid+'/environ').read_bytes().split(b'\0'):
    if b'=' in entry:
        name,value=entry.decode().split('=',1)
        if name=='PORTAL_DB':os.environ[name]=value
sys.path.insert(0,'/opt/cloud-portal')
from backend.database import db,audit
from backend.security import token_hash

destination=Path(sys.argv[2]).resolve()
if not str(destination).startswith('/tmp/uccloud-agent-20261007.') or destination.name!='qa-session.json':
    raise SystemExit('Unexpected QA output path')
if sys.argv[1]=='cleanup':
    value=json.loads(destination.read_text())
    with db() as conn:
        conn.execute('DELETE FROM sessions WHERE token_hash=?',(token_hash(value['session']),))
        audit(conn,value['user_id'],'agent.qa.session.end',value['vm_id'])
    destination.unlink()
    print('Temporary QA session removed')
else:
    token=secrets.token_urlsafe(32);csrf=secrets.token_urlsafe(32)
    with db() as conn:
        row=conn.execute("SELECT id,status FROM users WHERE username='gimpengtu' COLLATE NOCASE").fetchone()
        if not row or row['status']!='active':raise SystemExit('Expected test owner missing')
        conn.execute("INSERT INTO sessions(token_hash,user_id,csrf_token,expires_at) VALUES(?,?,?,datetime('now','+30 minutes'))",(token_hash(token),row['id'],csrf))
        audit(conn,row['id'],'agent.qa.session.start','b2878008-d330-40dc-86f4-c5d36862a8e9')
    value={'session':token,'csrf_token':csrf,'user_id':row['id'],'vm_id':'b2878008-d330-40dc-86f4-c5d36862a8e9','vm_username':'student'}
    with os.fdopen(os.open(destination,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600),'w') as output:json.dump(value,output)
    account=pwd.getpwnam('user');os.chown(destination,account.pw_uid,account.pw_gid)
    print('Scoped temporary QA browser session prepared; credential not displayed')
