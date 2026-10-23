"""Credential validation, privacy, SQL/Compose escaping and login hints."""
import asyncio
import base64
import json
import tempfile
import subprocess
import os
from pathlib import Path
from types import SimpleNamespace
from backend.software import AUTH_SERVICES, public_software, validate_software, BY_ID
from backend.openstack_service import _vm_user_data
from backend import guest_software as guest
from backend.guest_welcome import render
from backend.app import InstanceBody, safe_validation_error
from fastapi.exceptions import RequestValidationError

password = "simple$'\\password 한글"
items = validate_software([dict(id=key,version=BY_ID[key]['versions'][0],username='dbuser',password=password) for key in sorted(AUTH_SERVICES)])
assert len(items) == 6
assert password not in json.dumps(public_software(items), ensure_ascii=False)
for bad in [dict(id='node',version='24',username='dbuser',password=password),
            dict(id='mysql',version='8.4',username='root',password=password),
            dict(id='mysql',version='8.4',username='dbuser'),
            dict(id='mysql',version='8.4',username='dbuser',password='short'),
            dict(id='redis',version='8.2',username='bad;id',password=password),
            dict(id='postgres',version='18',username='dbuser',password='line\nbreak')]:
    try: validate_software([bad]); raise AssertionError('Unsafe credential accepted')
    except ValueError: pass
body = InstanceBody(name='test',image_id='d58be315-054e-49fb-bb25-14a5544adf39',flavor_id='small',vm_username='student',vm_password='vmtesting',software=items)
assert password not in repr(body)
config = json.loads(base64.b64decode(_vm_user_data('student','vmtesting',items)).decode().split('\n',1)[1])
files = {file['path']:file for file in config['write_files']}
assert files['/etc/uccloud-service-credentials.json']['permissions'] == '0600'
assert password not in files['/etc/uccloud-software.json']['content']
assert json.loads(files['/etc/uccloud-service-credentials.json']['content'])['redis']['password'] == password
assert files['/home/student/.hushlogin']['defer'] is True
assert '/usr/local/bin/uccloud-welcome' in files['/etc/profile.d/uccloud-welcome.sh']['content']
assert '/usr/local/bin/uccloud' in files
for file in files.values():
    if file['path'].startswith('/usr/local/bin/uccloud') or file['path'].endswith('install-software'):
        compile(file['content'],file['path'],'exec')
error = RequestValidationError([{'loc':('body','software'),'msg':'Invalid','type':'value_error','input':password}])
assert password.encode() not in asyncio.run(safe_validation_error(None,error)).body
with tempfile.TemporaryDirectory() as directory:
    guest.BASE = Path(directory)
    home = Path(directory)/'home'; home.mkdir()
    for key in AUTH_SERVICES-{'xampp'}:
        common,note = guest.container(key,BY_ID[key]['versions'][0],password,home,12345,'dbuser')
        assert password not in note
        assert 'dbuser' in note
        assert all(password not in value for value in common.get('command',[]))
        if key=='redis':
            acl = (guest.BASE/'redis-users.acl').read_text()
            assert 'user default off' in acl and 'user dbuser on #' in acl and password not in acl
        if key in {'mysql','maria'}:
            assert 'MYSQL_USER' not in common['environment'] and 'MARIADB_USER' not in common['environment']
            init_path=guest.BASE/(key+'-account-init.sh')
            output=subprocess.run(['bash','-c','docker_process_sql() { cat; }; source "$1"','bash',str(init_path)],
                                  env=dict(os.environ,UCCLOUD_DB_USER='dbuser',UCCLOUD_DB_PASSWORD=password),capture_output=True,text=True,check=True).stdout
            assert "IDENTIFIED BY '"+password.replace("'","''")+"';" in output
            assert "CREATE USER 'dbuser'@'%'" in output
        assert '$$' in json.dumps(common.get('environment',{}))
sql = guest.xampp_account_sql('dbuser',password,'root-random')
assert "sql_mode='NO_BACKSLASH_ESCAPES'" in sql and "'dbuser'@'localhost'" in sql
assert "simple$''\\password" in sql
hint = render(items,{'state':'ready'})
assert password not in hint and 'uccloud redis --help' in hint
assert 'Ubuntu' not in hint and 'cat ~/UCcloud-설치안내.txt' in hint
assert '준비 중' in render(items,{'state':'installing'})
assert '설치에 문제가' in render(items,{'state':'failed'})
assert 'Docker 29' in hint
print('Six service credentials, private cloud-init, audit redaction, escaping and concise login hints passed')
