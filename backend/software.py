"""Allowlisted first-boot software catalog. Never accept package names or URLs from users."""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

SOFTWARE = [
    dict(id="node", name="Node.js", group="개발 환경", versions=["24", "22"],
         note="npm 포함 · 24.22.1 / 22.23.4 이상 보안 패치 적용", min_ram=2048,
         minimum_versions={"24": "24.22.1", "22": "22.23.4"}),
    dict(id="python", name="Python", group="개발 환경", versions=["3.14", "3.13", "3.12"], note="가상환경 도구 포함", min_ram=2048),
    dict(id="java", name="Java", group="개발 환경", versions=["21", "17"], note="OpenJDK · Maven 포함", min_ram=2048),
    dict(id="nginx", name="Nginx", group="웹서버", versions=["1.30", "1.31"], note="Docker로 실행 · 8080번", min_ram=2048),
    dict(id="apache", name="Apache", group="웹서버", versions=["2.4"], note="Docker로 실행 · 8081번", min_ram=2048),
    dict(id="postgres", name="PostgreSQL", group="데이터베이스", versions=["18", "17", "16"], note="Docker로 실행 · 5432번", min_ram=2048),
    dict(id="mysql", name="MySQL", group="데이터베이스", versions=["8.4", "9.7"], note="Docker로 실행 · 3306번", min_ram=4096),
    dict(id="mongo", name="MongoDB", group="데이터베이스", versions=["8.0", "7.0"], note="Docker로 실행 · 27017번", min_ram=4096),
    dict(id="redis", name="Redis", group="캐시", versions=["8.2", "7.4"], note="Docker로 실행 · 6379번", min_ram=2048),
    dict(id="docker", name="Docker", group="실행 도구", versions=["29", "28"], note="Compose 포함 · DB 선택 시 자동 설치", min_ram=2048),
    dict(id="spring", name="Spring Boot", group="팀 개발 도구", versions=["3.5.16", "4.1.1", "4.0.8"], note="Java·Maven·예제 프로젝트 포함", min_ram=2048),
    dict(id="tomcat", name="Tomcat · JSP", group="팀 개발 도구", versions=["10.1", "11.0", "9.0"], note="JSP 실행 서버", min_ram=2048),
    dict(id="maria", name="MariaDB", group="데이터베이스", versions=["11.8", "10.11"], note="Docker로 실행", min_ram=2048),
    dict(id="react", name="React", group="팀 개발 도구", versions=["19", "18"], note="Node.js·시작 프로젝트 포함", min_ram=2048),
    dict(id="typescript", name="TypeScript", group="팀 개발 도구", versions=["7.0", "6.0", "5.9"], note="Node.js 포함", min_ram=2048),
    dict(id="firebase", name="Firebase 도구", group="클라우드 도구", versions=["15", "14"], note="CLI 설치 · Google 계정 로그인 필요", min_ram=2048),
    dict(id="xampp", name="XAMPP", group="팀 개발 도구", versions=["8.2.12"], note="개발용 Apache·PHP·MariaDB 묶음", min_ram=4096),
]
PORTS = dict(nginx=8082, apache=8081, spring=8080, tomcat=8083, postgres=5432, mysql=3306, maria=3307,
             mongo=27017, redis=6379, react=3000, xampp=8084)
for item in SOFTWARE:
    if item["id"] in PORTS:
        item["port"] = PORTS[item["id"]]
for item in SOFTWARE:
    if item["id"] == "nginx":
        item["note"] = "Docker로 실행"
    if item["id"] == "postgres":
        item["versions"].insert(1, "18.6")
BY_ID = {item["id"]: item for item in SOFTWARE}
AUTH_SERVICES = {'postgres', 'mysql', 'maria', 'mongo', 'redis', 'xampp'}
for item in SOFTWARE:
    if item['id'] in AUTH_SERVICES:
        item['credentials'] = True


def public_software(items):
    return [{key: item[key] for key in ('id', 'version', 'port') if key in item} for item in items]


def minimum_ram(items: list[dict]) -> int:
    database_count = sum(item['id'] in {'postgres', 'mysql', 'mongo', 'maria', 'xampp'} for item in items)
    return max(8192 if database_count >= 4 else 4096 if database_count >= 2 else 0,
               max((BY_ID[item['id']]['min_ram'] for item in items), default=0))


def validate_software(items: list[dict] | None) -> list[dict]:
    if not isinstance(items or [], list) or len(items or []) > 20:
        raise ValueError("설치할 프로그램을 다시 선택해 주세요.")
    selected, seen, ports = [], set(), set()
    for item in items or []:
        if not isinstance(item, dict):
            raise ValueError("설치할 프로그램을 다시 선택해 주세요.")
        key, version = item.get("id"), item.get("version")
        if not isinstance(key, str) or key not in BY_ID or version not in BY_ID[key]["versions"] or key in seen:
            raise ValueError("설치할 프로그램과 버전을 다시 선택해 주세요.")
        result = dict(id=key, version=version)
        username, password = item.get('username'), item.get('password')
        if username is not None or password is not None:
            if key not in AUTH_SERVICES:
                raise ValueError('이 프로그램에는 계정 설정이 필요하지 않습니다.')
            if (not isinstance(username, str) or not re.fullmatch(r'[a-z][a-z0-9_]{1,31}', username)
                    or username in {'root', 'postgres', 'mysql', 'mariadb', 'public'}):
                raise ValueError('서비스 ID는 영문 소문자로 시작하는 2~32자의 소문자·숫자·밑줄로 입력해 주세요. root 등 관리용 이름은 사용할 수 없습니다.')
            if (not isinstance(password, str) or not 8 <= len(password) <= 128
                    or any(ord(char) < 32 or ord(char) == 127 for char in password)):
                raise ValueError('서비스 비밀번호는 8~128자로 입력해 주세요. 줄바꿈은 사용할 수 없습니다.')
            result.update(username=username, password=password)
        if key in PORTS:
            port = item.get("port") if item.get("port") is not None else PORTS[key]
            if type(port) is not int or not 1024 <= port <= 65535 or port in ports:
                raise ValueError("프로그램 포트는 1024~65535 사이에서 서로 다르게 입력해 주세요.")
            # XAMPP's internal DB is intentionally separated from other DB ports.
            if port in {33306, 8444} and any(value.get("id") == "xampp" for value in items):
                raise ValueError("33306·8444번은 XAMPP가 사용합니다. 다른 포트를 입력해 주세요.")
            result["port"] = port
            ports.add(port)
        elif item.get("port") is not None:
            raise ValueError("이 프로그램에는 포트 설정이 필요하지 않습니다.")
        selected.append(result)
        seen.add(key)
    return selected


def software_files(username: str, selections: list[dict]) -> list[dict]:
    script = (Path(__file__).parent / "guest_software.py").read_text(encoding="utf-8")
    return [
        dict(path="/usr/local/sbin/uccloud-install-software", owner="root:root", permissions="0755", content=script),
        dict(path="/etc/uccloud-software.json", owner="root:root", permissions="0644",
             content=json.dumps(dict(username=username, selections=public_software(selections)))),
        dict(path="/etc/uccloud-service-credentials.json", owner="root:root", permissions="0600",
             content=json.dumps({item['id']:dict(username=item['username'],password=item['password'])
                                 for item in selections if 'password' in item})),
        dict(path="/var/lib/uccloud-software/status.json", owner="root:root", permissions="0644",
             content=json.dumps(dict(state="pending", message="VM을 준비하고 있습니다. 이후 선택한 프로그램을 자동으로 설치합니다.", started_at=int(time.time())))),
        dict(path="/etc/systemd/system/uccloud-software-install.service", owner="root:root", permissions="0644", content="""[Unit]
Description=Install selected UCcloud software
Wants=network-online.target uccloud-tailscale-install.service
After=network-online.target uccloud-tailscale-install.service
ConditionPathExists=!/var/lib/uccloud-software/completed
StartLimitIntervalSec=3600
StartLimitBurst=3

[Service]
Type=oneshot
ExecStart=/usr/local/sbin/uccloud-install-software
Restart=on-failure
RestartSec=120
TimeoutStartSec=1800
UMask=0077

[Install]
WantedBy=multi-user.target
"""),
    ]
