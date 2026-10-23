"""Allowlisted, additive installation through authenticated VM SSH, never host root."""
import json
import os
import re
import shlex
from pathlib import Path
from .software import validate_software, minimum_ram, public_software

SETUP = r'''
import fcntl,json,os,pwd,subprocess,socket,sys,time
from pathlib import Path
def stop(message):
    print(json.dumps({'error':message}));sys.exit(0)
payload=None
for _ in range(2):
    try:
        value=json.loads(sys.stdin.readline(200000))
        if isinstance(value,dict) and value.get('protocol')=='uccloud-software-v1':payload=value;break
    except Exception:pass
if not payload:stop('VM 관리 권한을 확인하지 못했습니다. 다시 접속해 주세요.')
lock=open('/run/uccloud-software-add.lock','w')
try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
except BlockingIOError:stop('이미 설치를 준비하고 있습니다. 잠시 기다려 주세요.')
for unit in ('uccloud-software-install','uccloud-software-add'):
    state=subprocess.run(['systemctl','show',unit,'-p','ActiveState','--value'],capture_output=True,text=True).stdout.strip()
    if state in ('active','activating','reloading'):stop('프로그램 설치가 진행 중입니다. 완료 후 추가해 주세요.')
user=pwd.getpwnam(payload['username'])
config_path=Path('/etc/uccloud-software.json')
original=json.loads(config_path.read_text()) if config_path.exists() else {}
if original.get('username') not in (None,user.pw_name):stop('VM을 만들 때 정한 접속 ID로 연결해 주세요.')
status_path=Path('/var/lib/uccloud-software/status.json')
if original.get('selections'):
    state=json.loads(status_path.read_text()) if status_path.exists() else {}
    if state.get('state')!='ready':stop('기존 프로그램 설치가 끝난 뒤 추가해 주세요. 설치 상태를 확인하세요.')
existing={item['id'] for item in original.get('selections',[])}
new=payload['selections']
if any(item['id'] in existing for item in new):stop('이미 설치한 프로그램은 추가할 수 없습니다. 버전 변경은 지원하지 않습니다.')
ports=[item['port'] for item in original.get('selections',[])+new if 'port' in item]
if len(ports)!=len(set(ports)):stop('기존 프로그램과 포트가 겹칩니다. 다른 포트를 입력해 주세요.')
memory=int(next(line.split()[1] for line in Path('/proc/meminfo').read_text().splitlines() if line.startswith('MemTotal:')))//1024
db_count=sum(item['id'] in ('postgres','mysql','maria','mongo','xampp') for item in original.get('selections',[])+new)
required=max(payload['minimum_ram'],8192 if db_count>=4 else 4096 if db_count>=2 else 0)
if memory < required*0.9:stop('선택한 프로그램을 설치할 메모리가 부족합니다. 더 큰 VM을 사용해 주세요.')
native={'node':'node','python':'python','java':'java','docker':'docker','typescript':'tsc','firebase':'firebase'}
projects={'spring':'spring-app','react':'react-app','nginx':'website','apache':'website','tomcat':'jsp','xampp':'xampp-website'}
for item in new:
    key=item['id']
    if key in native and subprocess.run(['which',native[key]],stdout=subprocess.DEVNULL).returncode==0:stop('이미 설치된 프로그램이 있습니다. 기존 설치를 덮어쓰지 않았습니다.')
    if key in projects and (Path(user.pw_dir)/projects[key]).exists():stop('기존 프로젝트 폴더가 있습니다. 파일을 덮어쓰지 않았습니다.')
    if key=='xampp' and Path('/opt/lampp').exists():stop('XAMPP가 이미 설치되어 있습니다.')
    for port in ([item['port']] if 'port' in item else [])+([33306] if key=='xampp' else []):
        with socket.socket() as s:
            try:s.bind(('127.0.0.1',port))
            except OSError:stop('사용 중인 포트가 있습니다. 다른 포트를 입력해 주세요.')
compose=Path('/opt/uccloud-services/compose.yml')
if compose.exists():
    import yaml
    services=(yaml.safe_load(compose.read_text()) or {}).get('services',{})
    if any(item['id'] in services for item in new):stop('기존 서비스는 덮어쓰지 않습니다.')
# Configuration writes follow every preflight check. Preserve old credentials and volumes.
script=Path('/usr/local/sbin/uccloud-install-additional')
script.write_text(payload['installer']);script.chmod(0o700)
batch=Path('/etc/uccloud-software-add.json')
batch.write_text(json.dumps({'username':user.pw_name,'selections':new,'additive':True,'credentials':payload['credentials']}));batch.chmod(0o600)
status_path.parent.mkdir(parents=True,exist_ok=True);status_path.parent.chmod(0o755)
now=int(time.time())
status_path.write_text(json.dumps({'state':'pending','message':'추가 프로그램 설치를 준비하고 있습니다. 창을 닫아도 설치는 계속됩니다.','started_at':now,'updated_at':now}));status_path.chmod(0o644)
result=subprocess.run(['systemd-run','--collect','--unit=uccloud-software-add','--property=Type=oneshot',
    '--property=UMask=0022','--property=TimeoutStartSec=1800','--setenv=UCCLOUD_INSTALL_CONFIG='+str(batch),
    '/usr/bin/python3',str(script)],capture_output=True,text=True)
if result.returncode:
    status_path.write_text(json.dumps({'state':'failed','message':'추가 설치를 시작하지 못했습니다. 운영자에게 문의해 주세요.','started_at':now,'updated_at':int(time.time())}));status_path.chmod(0o644)
    stop('추가 설치를 시작하지 못했습니다. 운영자에게 문의해 주세요.')
print(json.dumps({'ok':True}))
'''


async def start(connection, password, username, selections):
    if os.getenv('PORTAL_SOFTWARE_PRESETS_ENABLED','false').lower() != 'true':
        raise ValueError('프로그램 설치를 준비 중입니다. 운영자에게 문의해 주세요.')
    if not re.fullmatch(r'[a-z_][a-z0-9_-]{1,31}', username) or username == 'root':
        raise ValueError('VM을 만들 때 정한 접속 ID로 연결해 주세요.')
    selected = validate_software(selections)
    if not selected:
        raise ValueError('추가할 프로그램을 선택해 주세요.')
    credentials = {item['id']:dict(username=item['username'],password=item['password']) for item in selected if 'password' in item}
    payload = dict(protocol='uccloud-software-v1',username=username,selections=public_software(selected),
                   credentials=credentials,minimum_ram=minimum_ram(selected),
                   installer=(Path(__file__).parent/'guest_software.py').read_text(encoding='utf-8'))
    try:
        result = await connection.run("sudo -S -k -p '' -- python3 -c "+shlex.quote(SETUP),
                                      input=password+'\n'+json.dumps(payload)+'\n',check=False,timeout=45)
        try:
            response = json.loads(result.stdout)
        except Exception:
            raise ValueError('VM 관리 권한을 확인하지 못했습니다. 다시 접속해 주세요.') from None
        if response.get('error'):
            raise ValueError(response['error'])
        if not response.get('ok'):
            raise ValueError('추가 설치를 시작하지 못했습니다. 다시 접속해 주세요.')
        return dict(state='accepted',message='추가 설치를 시작했습니다. 창을 닫아도 설치는 계속됩니다.')
    finally:
        payload.clear();credentials.clear()
