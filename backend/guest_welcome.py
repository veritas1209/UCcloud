#!/usr/bin/python3
"""Short, non-secret login hints for student VMs only."""
import json
from pathlib import Path

HELP = {
    'node': ('Node.js', 'JavaScript 실행', 'node --help · npm --help'),
    'python': ('Python', 'Python 실행', 'python --help'),
    'java': ('Java', 'Java 실행', 'java --help · mvn --help'),
    'docker': ('Docker', '프로그램 실행 도구', 'sudo docker --help'),
    'nginx': ('Nginx', '웹서버 · ~/website', 'uccloud nginx -h'),
    'apache': ('Apache', '웹서버 · ~/website', 'uccloud apache -h'),
    'postgres': ('PostgreSQL', '데이터베이스 · DB 이름 app', 'uccloud postgres --help'),
    'mysql': ('MySQL', '데이터베이스 · DB 이름 app', 'uccloud mysql --help'),
    'maria': ('MariaDB', '데이터베이스 · DB 이름 app', 'uccloud maria --help'),
    'mongo': ('MongoDB', '데이터베이스 · 인증 DB admin', 'uccloud mongo --help'),
    'redis': ('Redis', '빠른 데이터 저장소', 'uccloud redis --help'),
    'spring': ('Spring Boot', '웹앱 · ~/spring-app', 'systemctl status uccloud-spring --no-pager'),
    'tomcat': ('Tomcat · JSP', '웹서버 · ~/jsp/ROOT', 'uccloud tomcat help'),
    'react': ('React', '웹앱 · ~/react-app', 'cd ~/react-app && npm run build'),
    'typescript': ('TypeScript', 'TypeScript 개발 도구', 'tsc --help'),
    'firebase': ('Firebase', 'Google 서비스 연결 도구', 'firebase --help'),
    'xampp': ('XAMPP', '웹서버 · ~/xampp-website · DB app:33306', 'sudo /opt/lampp/lampp status'),
}


def render(selections, state=None):
    lines = ['UCcloud · 내 서버']
    if not selections:
        lines += ['파일 올리기·웹사이트 공개는 접속 화면 아래에서 할 수 있습니다.']
    elif not state or state.get('state') != 'ready':
        lines += ['프로그램 준비 중입니다. 접속 화면에서 진행 상황을 확인하세요.'
                  if not state or state.get('state') != 'failed' else '프로그램 설치에 문제가 있습니다. 접속 화면에서 확인하세요.']
    else:
        installed = [dict(item) for item in selections]
        keys = {item['id'] for item in installed}
        dependencies = {}
        if keys & {'nginx','apache','postgres','mysql','maria','mongo','redis','tomcat','react'}: dependencies['docker'] = '29'
        if keys & {'react','typescript','firebase'}: dependencies['node'] = '24'
        if keys & {'spring','firebase'}: dependencies['java'] = '21'
        installed.extend(dict(id=key,version=version) for key,version in dependencies.items() if key not in keys)
        for item in installed:
            if item.get('id') not in HELP: continue
            name, description, command = HELP[item['id']]
            port = ' · localhost:' + str(item['port']) if 'port' in item else ''
            lines += ['', name + ' ' + item['version'] + ' — ' + description + port, '  ' + command]
        lines += ['', '자세한 설정: cat ~/UCcloud-설치안내.txt']
    return '\n'.join(lines) + '\n'


if __name__ == '__main__':
    try:
        config_path = Path('/etc/uccloud-software.json')
        config = json.loads(config_path.read_text()) if config_path.exists() else {}
        state_path = Path('/var/lib/uccloud-software/status.json')
        state = json.loads(state_path.read_text()) if state_path.exists() else None
        print(render(config.get('selections', []), state))
    except (OSError, ValueError, KeyError):
        print('UCcloud · 내 서버\n접속 화면에서 프로그램 준비 상태를 확인하세요.\n')
