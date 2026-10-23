"""Exercise the real additive installer with package/service execution mocked."""
import asyncio
import copy
import json
import os
import tempfile
from pathlib import Path
from types import SimpleNamespace
import yaml
from backend import guest_software as guest
from backend import software_install

compile(software_install.SETUP,'guest-setup','exec')

async def validation():
    class SSH:
        def __init__(self): self.calls=[]
        async def run(self, command, **kwargs):
            self.calls.append((command,kwargs))
            return SimpleNamespace(stdout='{"ok":true}')
    os.environ['PORTAL_SOFTWARE_PRESETS_ENABLED']='true'
    connection=SSH()
    result=await software_install.start(connection,'vm-secret','student',[
        dict(id='redis',version='8.2',port=6379,username='appuser',password='db-secret')])
    assert result['state']=='accepted'
    command,options=connection.calls[0]
    assert 'vm-secret' not in command and 'db-secret' not in command
    payload=json.loads(options['input'].split('\n',1)[1])
    assert payload['selections']==[dict(id='redis',version='8.2',port=6379)]
    assert payload['credentials']['redis']['password']=='db-secret'
    assert len(options['input'])<200000
    for items in ([],[dict(id='curl evil',version='1')],[dict(id='node',version='24;bad')]):
        try:
            await software_install.start(connection,'vm-secret','student',items)
            raise AssertionError('Unsafe program accepted')
        except ValueError: pass
    assert len(connection.calls)==1

asyncio.run(validation())

with tempfile.TemporaryDirectory() as directory:
    root=Path(directory)
    etc=root/'etc';etc.mkdir()
    home=root/'home';home.mkdir()
    guest.BASE=root/'services';guest.BASE.mkdir()
    guest.STATE=root/'state/status.json';guest.STATE.parent.mkdir()
    guest.STATE.write_text('{"state":"pending","started_at":1780000000}')
    guest.PROGRESS.clear()
    original=dict(username='student',selections=[dict(id='postgres',version='18',port=5432)])
    (etc/'uccloud-software.json').write_text(json.dumps(original))
    (etc/'uccloud-software-add.json').write_text(json.dumps(dict(username='student',additive=True,
        selections=[dict(id='redis',version='8.2',port=6379)],
        credentials={'redis':dict(username='newuser',password='new-password')})))
    credentials={'postgres':dict(username='olduser',password='old-password')}
    (etc/'uccloud-service-credentials.json').write_text(json.dumps(credentials))
    old_compose=dict(services={'postgres':{'image':'postgres:18','environment':{'CUSTOM':'unchanged'},'volumes':['postgres-data:/var/lib/postgresql']}},volumes={'postgres-data':{'labels':{'keep':'true'}}})
    (guest.BASE/'compose.yml').write_text(yaml.safe_dump(old_compose))
    (guest.BASE/'passwords.json').write_text('{"postgres":"old-password"}')
    (home/'UCcloud-설치안내.txt').write_text('KEEP EXISTING GUIDE\n')
    user=SimpleNamespace(pw_dir=str(home),pw_name='student',pw_uid=os.getuid(),pw_gid=os.getgid())
    guest.pwd=SimpleNamespace(getpwnam=lambda name:user)
    guest.Path=lambda value:etc/Path(value).name if str(value).startswith('/etc/') else Path(value)
    guest.subprocess=SimpleNamespace(DEVNULL=-3,run=lambda *args,**kwargs:SimpleNamespace(returncode=0))
    guest.update=lambda:None;guest.apt=lambda *args:None
    calls=[]
    guest.run=lambda *args,**kwargs:calls.append(args) or ''
    os.environ['UCCLOUD_INSTALL_CONFIG']='/etc/uccloud-software-add.json'
    guest.main()
    merged=yaml.safe_load((guest.BASE/'compose.yml').read_text())
    assert merged['services']['postgres']==old_compose['services']['postgres']
    assert merged['volumes']['postgres-data']==old_compose['volumes']['postgres-data']
    assert merged['services']['redis']['ports']==['127.0.0.1:6379:6379']
    assert json.loads((guest.BASE/'passwords.json').read_text())['postgres']=='old-password'
    assert json.loads((etc/'uccloud-service-credentials.json').read_text())['postgres']==credentials['postgres']
    assert json.loads((etc/'uccloud-software.json').read_text())['selections']==original['selections']+[dict(id='redis',version='8.2',port=6379)]
    assert 'credentials' not in json.loads((etc/'uccloud-software-add.json').read_text())
    assert (home/'UCcloud-설치안내.txt').read_text().startswith('KEEP EXISTING GUIDE')
    assert json.loads(guest.STATE.read_text())['state']=='ready'
    up=next(args for args in calls if 'up' in args)
    assert up[-2:]==('--no-deps','redis')
print('Allowlist, stdin-only credentials, additive config/volumes/passwords/guide preservation and new-service-only start passed')
