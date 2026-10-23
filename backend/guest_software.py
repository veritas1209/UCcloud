#!/usr/bin/python3
"""Runs as root INSIDE a newly created student VM, never on the controller."""
import json
import hashlib
import os
import pwd
import secrets
import signal
import subprocess
import re
import time
from pathlib import Path

STATE = Path("/var/lib/uccloud-software/status.json")
BASE = Path("/opt/uccloud-services")
PROGRESS = {}
os.environ["DEBIAN_FRONTEND"] = "noninteractive"
# CVE-2026-111497 (Node2Shell), Node.js security release 2026-10-21.
# Keep this self-contained: cloud-init copies this script into student VMs.
NODE_SECURITY_MINIMUM = {"24": (24, 22, 1), "22": (22, 23, 4), "26": (26, 12, 1)}
NODE_INSTALL_MAJORS = ("24", "22")


def require_safe_node(actual, expected_major=None):
    match = re.fullmatch(r"v?(\d+)\.(\d+)\.(\d+)", actual.strip())
    if not match:
        raise RuntimeError("Node.js 버전을 확인하지 못했습니다. 설치를 중단합니다.")
    parsed = tuple(map(int, match.groups()))
    major = str(parsed[0])
    minimum = NODE_SECURITY_MINIMUM.get(major)
    if minimum is None or (expected_major is not None and major != expected_major):
        raise RuntimeError("지원하지 않거나 선택과 다른 Node.js 버전입니다. 설치를 중단합니다.")
    if parsed < minimum:
        floor = '.'.join(map(str, minimum))
        raise RuntimeError(f"Node.js 보안 업데이트가 필요합니다. {floor} 이상으로 업데이트한 뒤 다시 설치하세요.")
    return parsed


def secure_node_package(versions, major):
    if major not in NODE_INSTALL_MAJORS:
        raise RuntimeError("지원하지 않는 Node.js 계열입니다.")
    candidates = []
    for line in versions.splitlines():
        fields = line.split('|')
        if len(fields) < 3:
            continue
        package = fields[1].strip()
        # Only stable NodeSource packages; never accept rc/nightly or arbitrary text.
        match = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)-\d+nodesource\d+", package)
        if not match:
            continue
        parsed = tuple(map(int, match.groups()))
        if str(parsed[0]) == major and parsed >= NODE_SECURITY_MINIMUM[major]:
            candidates.append((parsed, package))
    if not candidates:
        raise RuntimeError("Node.js 보안 수정본을 저장소에서 찾지 못했습니다. 구버전은 설치하지 않습니다.")
    return max(candidates)[1]


def run(*args, capture=False, env=None):
    result = subprocess.run(args, check=True, text=True, stdout=subprocess.PIPE if capture else None,
                            env=env, timeout=900)
    return result.stdout.strip() if capture else None


def status(state, message):
    STATE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE.with_suffix(".tmp")
    if PROGRESS:
        if state == "installing":
            PROGRESS["step"] += 1
        if state == "ready":
            PROGRESS["step"] = PROGRESS["total_steps"]
        PROGRESS["updated_at"] = int(time.time())
    tmp.write_text(json.dumps(dict(state=state, message=message, **PROGRESS), ensure_ascii=False))
    tmp.chmod(0o644)
    tmp.replace(STATE)


def apt(*packages):
    run("apt-get", "-o", "DPkg::Lock::Timeout=180", "-o", "Acquire::Retries=3", "install", "-y", "-qq", *packages)


def update():
    run("apt-get", "-o", "DPkg::Lock::Timeout=180", "-o", "Acquire::Retries=3", "update", "-qq")


def download(url, path):
    run("curl", "--retry", "3", "--retry-delay", "5", "-fsSL", url, "-o", str(path))
    Path(path).chmod(0o644)


def docker_install(major):
    Path("/etc/apt/keyrings").mkdir(exist_ok=True)
    download("https://download.docker.com/linux/ubuntu/gpg", "/etc/apt/keyrings/docker.asc")
    Path("/etc/apt/sources.list.d/docker.list").write_text(
        "deb [arch=amd64 signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu noble stable\n")
    update()
    versions = run("apt-cache", "madison", "docker-ce", capture=True)
    version = next(line.split("|")[1].strip() for line in versions.splitlines()
                   if line.split("|")[1].strip().startswith(f"5:{major}."))
    apt(f"docker-ce={version}", f"docker-ce-cli={version}", "containerd.io", "docker-buildx-plugin", "docker-compose-plugin")
    Path("/etc/apt/preferences.d/uccloud-docker").write_text(
        f"Package: docker-ce docker-ce-cli\nPin: version 5:{major}.*\nPin-Priority: 1001\n")
    run("systemctl", "enable", "--now", "docker")


def native(key, version):
    if key == "node":
        if version not in NODE_INSTALL_MAJORS:
            raise RuntimeError("지원하지 않는 Node.js 계열입니다.")
        Path("/etc/apt/keyrings").mkdir(exist_ok=True)
        download("https://deb.nodesource.com/gpgkey/nodesource-repo.gpg.key", "/etc/apt/keyrings/nodesource.asc")
        Path("/etc/apt/sources.list.d/nodesource.list").write_text(
            f"deb [arch=amd64 signed-by=/etc/apt/keyrings/nodesource.asc] https://deb.nodesource.com/node_{version}.x nodistro main\n")
        update()
        package = secure_node_package(run("apt-cache", "madison", "nodejs", capture=True), version)
        apt(f"nodejs={package}", "build-essential")
        actual = run("node", "--version", capture=True)
        require_safe_node(actual, version)
        return f"Node.js {actual} · npm {run('npm', '--version', capture=True)}"
    if key == "java":
        apt(f"openjdk-{version}-jdk-headless", "maven")
        run("update-alternatives", "--set", "java", f"/usr/lib/jvm/java-{version}-openjdk-amd64/bin/java")
        run("update-alternatives", "--set", "javac", f"/usr/lib/jvm/java-{version}-openjdk-amd64/bin/javac")
        return f"Java {version} · java / javac / mvn"
    if key == "python":
        apt("python3-venv", "python3-pip", "build-essential")
        if version == "3.12":
            target = "/usr/bin/python3.12"
        else:
            if not Path("/opt/uccloud-tools/bin/uv").exists():
                run("python3", "-m", "venv", "/opt/uccloud-tools")
                run("/opt/uccloud-tools/bin/pip", "install", "--disable-pip-version-check", "uv>=0.8,<1")
            env = dict(os.environ, UV_PYTHON_INSTALL_DIR="/opt/uccloud-python")
            run("/opt/uccloud-tools/bin/uv", "python", "install", version, env=env)
            target = run("/opt/uccloud-tools/bin/uv", "python", "find", "--managed-python", version, capture=True, env=env)
            for directory, folders, files in os.walk("/opt/uccloud-python"):
                Path(directory).chmod(0o755)
                for filename in files:
                    path = Path(directory) / filename
                    if not path.is_symlink():
                        mode = path.stat().st_mode
                        path.chmod(mode | 0o444 | (0o111 if mode & 0o100 else 0))
            link = Path(f"/usr/local/bin/python{version}")
            if not link.exists():
                link.symlink_to(target)
        link = Path("/usr/local/bin/python")
        if link.is_symlink():
            link.unlink()
        link.symlink_to(target)
        return f"Python {version} · python -m venv .venv"


def container(key, version, secret, home, host, username='student'):
    common = dict(restart="unless-stopped", logging=dict(driver="json-file", options={"max-size": "10m", "max-file": "3"}))
    guest = dict(nginx=80, apache=80, postgres=5432, mysql=3306, maria=3306, redis=6379, mongo=27017, tomcat=8080, react=80)[key]
    common["ports"] = [f"127.0.0.1:{host}:{guest}"]
    common["image"] = f"{dict(apache='httpd', maria='mariadb').get(key, key)}:{version}"
    if key == "tomcat":
        common["image"] += "-jdk21-temurin"
    if key == "react":
        common["image"] = "nginx:1.30"
        config = BASE / "react-nginx.conf"
        config.write_text('server { listen 80; server_name _; root /usr/share/nginx/html; index index.html; location / { try_files $uri $uri/ /index.html; } }\n')
        config.chmod(0o644)
        common["volumes"] = [f"{home}/react-app/dist:/usr/share/nginx/html:ro", f"{config}:/etc/nginx/conf.d/default.conf:ro"]
        health = ["CMD-SHELL", "curl -fsS http://127.0.0.1/ >/dev/null"]
        help_text = f"React 사이트: 실행 중 · http://localhost:{host}\n공개: sudo tailscale funnel --bg {host}"
    elif key in {"nginx", "apache"}:
        directory = home / "website"
        directory.mkdir(exist_ok=True)
        directory.chmod(0o755)
        page = directory / "index.html"
        if not page.exists():
            page.write_text('<!doctype html><html lang="ko"><meta charset="utf-8"><title>UCcloud</title><h1>웹사이트 준비 완료</h1></html>')
            page.chmod(0o644)
        destination = "/usr/share/nginx/html" if key == "nginx" else "/usr/local/apache2/htdocs"
        common["volumes"] = [f"{directory}:{destination}:ro"]
        health = ["CMD-SHELL", "curl -fsS http://127.0.0.1/ >/dev/null" ] if key == "nginx" else None
        help_text = f"{key} {version}: http://localhost:{host} · ~/website/index.html 수정\n공개: sudo tailscale funnel --bg {host}"
    elif key == "tomcat":
        directory = home / "jsp" / "ROOT"
        directory.mkdir(parents=True, exist_ok=True)
        page = directory / "index.jsp"
        if not page.exists():
            page.write_text('<%@ page contentType="text/html; charset=UTF-8" %><h1>JSP 준비 완료</h1><p><%= java.time.LocalDate.now() %></p>')
        # JSP applications can use the same localhost DB addresses as native Spring apps.
        # Host networking is confined to this student's VM; the connector remains loopback-only.
        import xml.etree.ElementTree as ET
        xml = run("docker", "run", "--rm", "--entrypoint", "cat", common["image"], "/usr/local/tomcat/conf/server.xml", capture=True)
        root = ET.fromstring(xml)
        root.set("port", "-1")
        for service in root.findall("Service"):
            for connector in list(service.findall("Connector")):
                if connector.get("port") != "8080":
                    service.remove(connector)
                else:
                    connector.set("port", str(host))
                    connector.set("address", "127.0.0.1")
        config = BASE / "tomcat-server.xml"
        config.write_text(ET.tostring(root, encoding="unicode"))
        config.chmod(0o644)
        common.pop("ports")
        common["network_mode"] = "host"
        common["volumes"] = [f"{directory}:/usr/local/tomcat/webapps/ROOT:ro", f"{config}:/usr/local/tomcat/conf/server.xml:ro"]
        health = None
        help_text = f"Tomcat {version} · JSP: http://localhost:{host}\n~/jsp/ROOT/index.jsp 수정\n공개: sudo tailscale funnel --bg {host}"
    else:
        target = dict(postgres="/var/lib/postgresql", mysql="/var/lib/mysql", maria="/var/lib/mysql", redis="/data", mongo="/data/db")[key]
        if key == "postgres" and not version.startswith("18"):
            target = "/var/lib/postgresql/data"
        common["volumes"] = [f"{key}-data:{target}"]
        if key == "postgres":
            common["environment"] = dict(POSTGRES_USER=username, POSTGRES_DB="app", POSTGRES_PASSWORD=secret)
            health = ["CMD-SHELL", 'pg_isready -U "$$POSTGRES_USER" -d app']
        elif key == "mysql":
            common["environment"] = dict(UCCLOUD_DB_USER=username, MYSQL_DATABASE="app", UCCLOUD_DB_PASSWORD=secret,
                                          MYSQL_ROOT_PASSWORD=hashlib.sha256((secret + ":root").encode()).hexdigest())
            health = ["CMD-SHELL", 'MYSQL_PWD="$$UCCLOUD_DB_PASSWORD" mysql -u"$$UCCLOUD_DB_USER" -Dapp -e "SELECT 1" >/dev/null']
        elif key == "maria":
            common["environment"] = dict(UCCLOUD_DB_USER=username, MARIADB_DATABASE="app", UCCLOUD_DB_PASSWORD=secret,
                                          MARIADB_ROOT_PASSWORD=hashlib.sha256((secret + ":root").encode()).hexdigest())
            health = ["CMD", "healthcheck.sh", "--connect", "--innodb_initialized"]
        elif key == "mongo":
            common["environment"] = dict(MONGO_INITDB_ROOT_USERNAME=username, MONGO_INITDB_ROOT_PASSWORD=secret)
            common["command"] = ["mongod", "--wiredTigerCacheSizeGB", "0.5"]
            health = ["CMD-SHELL", 'mongosh --quiet --username "$$MONGO_INITDB_ROOT_USERNAME" --password "$$MONGO_INITDB_ROOT_PASSWORD" --authenticationDatabase admin --eval "db.adminCommand({ping:1})" >/dev/null']
        else:
            acl = BASE / 'redis-users.acl'
            acl.write_text('user default off\nuser ' + username + ' on #' + hashlib.sha256(secret.encode()).hexdigest() + ' ~* &* +@all\n')
            acl.chmod(0o644)  # Only a one-way hash, readable by the container's redis user.
            common['volumes'].append(f'{acl}:/usr/local/etc/redis/users.acl:ro')
            common["command"] = ["redis-server", "--appendonly", "yes", "--aclfile", "/usr/local/etc/redis/users.acl"]
            common["environment"] = dict(REDISCLI_AUTH=secret, REDISCLI_USER=username)
            health = ["CMD-SHELL", 'redis-cli --user "$$REDISCLI_USER" ping | grep -q PONG']
        help_text = f"{key} {version}: localhost:{host}\nID: {username} · DB: app" if key in {"postgres", "mysql", "maria"} else f"{key} {version}: localhost:{host}"
        if key == "mongo":
            help_text += f"\nID: {username} · 인증 DB: admin"
        if key == 'redis':
            help_text += f"\nID: {username}"
        help_text += "\n비밀번호: VM 생성 때 설정한 값 (자동 발급 값은 sudo cat /opt/uccloud-services/passwords.json)"
        if key in {'mysql', 'maria'}:
            # Vendor init interpolates MYSQL_PASSWORD directly into SQL. Do not use
            # it for user-chosen values. A sourced, non-secret init script quotes
            # apostrophes and treats backslashes literally before creating the user.
            init = BASE / (key + '-account-init.sh')
            init.write_text(r'''uccloud_sql_password="${UCCLOUD_DB_PASSWORD//\'/\'\'}"
docker_process_sql --binary-mode --default-character-set=utf8mb4 <<EOSQL
SET SESSION sql_mode='NO_BACKSLASH_ESCAPES';
CREATE USER '${UCCLOUD_DB_USER}'@'%' IDENTIFIED BY '${uccloud_sql_password}';
GRANT ALL ON app.* TO '${UCCLOUD_DB_USER}'@'%';
EOSQL
unset uccloud_sql_password
''')
            init.chmod(0o644)
            common['volumes'].append(f'{init}:/docker-entrypoint-initdb.d/90-uccloud-account.sh:ro')
    if health:
        common["healthcheck"] = dict(test=health, interval="10s", timeout="5s", retries=30, start_period="30s")
    if 'environment' in common:
        # Compose substitutes $VARIABLE even in YAML strings. Escape user secrets literally.
        common['environment'] = {key: value.replace('$', '$$') for key, value in common['environment'].items()}
    return common, help_text


def own_tree(directory, user):
    for root, folders, files in os.walk(directory):
        os.chown(root, user.pw_uid, user.pw_gid)
        Path(root).chmod(0o755)
        for filename in files:
            path = Path(root) / filename
            if not path.is_symlink():
                os.chown(path, user.pw_uid, user.pw_gid)
                path.chmod(0o755 if path.stat().st_mode & 0o100 else 0o644)


def user_run(user, directory, *args):
    subprocess.run(["runuser", "-u", user.pw_name, "--", "env", f"HOME={user.pw_dir}", *args],
                   cwd=directory, check=True, timeout=900)


def npm_global(package):
    subprocess.run(["npm", "install", "-g", package, "--no-fund", "--no-audit"],
                   check=True, timeout=900, umask=0o022)
    # Repair modes if this is a retry of a setup run with a restrictive umask.
    root = Path(run("npm", "root", "-g", capture=True))
    for directory, folders, files in os.walk(root):
        Path(directory).chmod(0o755)
        for filename in files:
            path = Path(directory) / filename
            if path.is_file() and not path.is_symlink():
                path.chmod(path.stat().st_mode | 0o444 | (0o111 if path.stat().st_mode & 0o100 else 0))


def app_service(key, user, directory, command, port):
    """Start the prepared app as its student account, including after a reboot."""
    unit = Path(f"/etc/systemd/system/uccloud-{key}.service")
    unit.write_text(f"""[Unit]
Description=UCcloud {key} application
After=network-online.target
Wants=network-online.target
[Service]
User={user.pw_name}
Group={user.pw_gid}
WorkingDirectory={directory}
Environment=HOME={user.pw_dir}
ExecStart={command}
Restart=on-failure
RestartSec=5
UMask=0027
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=read-only
ReadWritePaths={directory}
[Install]
WantedBy=multi-user.target
""")
    unit.chmod(0o644)
    run("systemctl", "daemon-reload")
    run("systemctl", "enable", f"uccloud-{key}")
    run("systemctl", "restart", f"uccloud-{key}")
    run("curl", "--retry", "30", "--retry-connrefused", "--retry-delay", "2", "-fsS", f"http://127.0.0.1:{port}/", capture=True)


def spring_project(version, port, user):
    directory = Path(user.pw_dir) / "spring-app"
    source = directory / "src/main/java/com/uccloud"
    source.mkdir(parents=True, exist_ok=True)
    pom = directory / "pom.xml"
    if not pom.exists():
        pom.write_text(f'''<project xmlns="http://maven.apache.org/POM/4.0.0" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xsi:schemaLocation="http://maven.apache.org/POM/4.0.0 https://maven.apache.org/xsd/maven-4.0.0.xsd">
<modelVersion>4.0.0</modelVersion><parent><groupId>org.springframework.boot</groupId><artifactId>spring-boot-starter-parent</artifactId><version>{version}</version><relativePath/></parent>
<groupId>com.uccloud</groupId><artifactId>spring-app</artifactId><version>0.0.1-SNAPSHOT</version><properties><java.version>17</java.version></properties>
<dependencies><dependency><groupId>org.springframework.boot</groupId><artifactId>spring-boot-starter-web</artifactId></dependency></dependencies>
<build><plugins><plugin><groupId>org.springframework.boot</groupId><artifactId>spring-boot-maven-plugin</artifactId></plugin></plugins></build></project>''')
        (source / "Application.java").write_text('''package com.uccloud;
import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RestController;
@SpringBootApplication @RestController
public class Application {
  public static void main(String[] args) { SpringApplication.run(Application.class, args); }
  @GetMapping("/") public String home() { return "UCcloud Spring Boot ready"; }
}''')
        resources = directory / "src/main/resources"
        resources.mkdir(parents=True, exist_ok=True)
        (resources / "application.properties").write_text(f"server.address=127.0.0.1\nserver.port={port}\n")
    own_tree(directory, user)
    user_run(user, directory, "mvn", "-q", "-DskipTests", "package")
    app_service("spring", user, directory, "/usr/bin/java -jar target/spring-app-0.0.1-SNAPSHOT.jar", port)
    return f"Spring Boot {version}: 실행 중 · http://localhost:{port}\n프로젝트: ~/spring-app\n수정 후 적용: cd ~/spring-app && mvn package && sudo systemctl restart uccloud-spring\n실행 상태: systemctl status uccloud-spring\n공개: sudo tailscale funnel --bg {port}"


def react_project(version, port, typescript, user):
    directory = Path(user.pw_dir) / "react-app"
    directory.mkdir(exist_ok=True)
    if not (directory / "package.json").exists():
        extension = "tsx" if typescript else "jsx"
        package = dict(name="uccloud-react", private=True, version="1.0.0", type="module",
                       scripts=dict(dev=f"vite --host 127.0.0.1 --port {port}", build="vite build",
                                    start=f"vite preview --host 127.0.0.1 --port {port}"),
                       dependencies={"react": version, "react-dom": version}, devDependencies={"vite": "8"})
        if typescript:
            package["devDependencies"].update({"typescript": typescript, "@types/react": version, "@types/react-dom": version})
            (directory / "tsconfig.json").write_text(json.dumps(dict(compilerOptions=dict(target="ES2022", module="ESNext", moduleResolution="bundler", jsx="react-jsx", strict=True, noEmit=True), include=[f"main.{extension}"])))
        (directory / "package.json").write_text(json.dumps(package, indent=2))
        (directory / "index.html").write_text(f'<html lang="ko"><meta charset="utf-8"><title>UCcloud React</title><div id="root"></div><script type="module" src="/main.{extension}"></script></html>')
        (directory / f"main.{extension}").write_text("import React from 'react';\nimport { createRoot } from 'react-dom/client';\ncreateRoot(document.getElementById('root')" + ("!" if typescript else "") + ").render(<h1>React 준비 완료</h1>);\n")
    own_tree(directory, user)
    user_run(user, directory, "npm", "install", "--no-fund", "--no-audit")
    user_run(user, directory, "npm", "run", "build")
    own_tree(directory / "dist", user)
    # Compose starts Nginx on the chosen port once the build is ready.
    return f"React {version}: http://localhost:{port}\n프로젝트: ~/react-app\n수정 후 적용: cd ~/react-app && npm run build\n공개: sudo tailscale funnel --bg {port}"


def xampp_account_sql(username, secret, root):
    # NO_BACKSLASH_ESCAPES makes quotes unambiguous even for user-chosen passwords.
    literal = lambda value: "'" + value.replace("'", "''") + "'"
    account = literal(username) + "@'localhost'"
    return ("SET SESSION sql_mode='NO_BACKSLASH_ESCAPES'; CREATE DATABASE IF NOT EXISTS app; "
            f"CREATE USER IF NOT EXISTS {account} IDENTIFIED BY {literal(secret)}; "
            f"GRANT ALL ON app.* TO {account}; ALTER USER 'root'@'localhost' IDENTIFIED BY {literal(root)};")


def xampp_install(version, port, user, secret, username='student'):
    # Official Linux release, pinned to the SHA-256 of the published installer.
    installer = Path(f"/var/cache/uccloud-xampp-{version}.run")
    digest = "df0774e7a6d0d0754a5f0132015411234b5f953df2365b693d3758fc35a30374"
    apt("libcrypt1", "net-tools")
    if not Path("/opt/lampp/lampp").exists():
        download(f"https://downloads.sourceforge.net/project/xampp/XAMPP%20Linux/{version}/xampp-linux-x64-{version}-0-installer.run", installer)
        with installer.open("rb") as stream:
            actual = hashlib.file_digest(stream, "sha256").hexdigest()
        if actual != digest:
            raise RuntimeError("XAMPP installer checksum mismatch")
        installer.chmod(0o700)
        run("sh", "-c", 'umask 022; exec "$@"', "sh", str(installer), "--mode", "unattended")
        installer.unlink()
    # The parent installer service has umask 077. Vendor files need normal read/execute
    # permissions, while actual SQL data stays private and owned by the database account.
    run("/opt/lampp/lampp", "stop")
    mysql_user = pwd.getpwnam("mysql")
    for directory, folders, files in os.walk("/opt/lampp"):
        path = Path(directory)
        path.chmod(0o755)
        data = path.is_relative_to("/opt/lampp/var/mysql")
        if data:
            os.chown(path, mysql_user.pw_uid, mysql_user.pw_gid)
        for filename in files:
            file = path / filename
            if file.is_symlink() or not file.is_file():
                continue
            if data:
                os.chown(file, mysql_user.pw_uid, mysql_user.pw_gid)
                file.chmod(0o600)
            elif (path.relative_to("/opt/lampp").parts or ("",))[0] in {"bin", "sbin", "lib", "share", "modules"}:
                file.chmod(file.stat().st_mode | 0o444 | (0o111 if file.stat().st_mode & 0o100 else 0))
    directory = Path("/var/www/uccloud-xampp")
    directory.mkdir(parents=True, exist_ok=True)
    directory.parent.chmod(0o755)
    shortcut = Path(user.pw_dir) / "xampp-website"
    if not shortcut.exists():
        shortcut.symlink_to(directory, target_is_directory=True)
    if not (directory / "index.php").exists():
        (directory / "index.php").write_text('<html lang="ko"><meta charset="utf-8"><h1>XAMPP 준비 완료</h1><?php echo "PHP " . PHP_VERSION; ?>')
    own_tree(directory, user)
    conf = Path("/opt/lampp/etc/httpd.conf")
    text = conf.read_text()
    text = re.sub(r"^Listen .*$", f"Listen 127.0.0.1:{port}", text, flags=re.M)
    text = text.replace('"/opt/lampp/htdocs"', f'"{directory}"')
    text = re.sub(r"^Include .*httpd-ssl.conf.*$", "# UCcloud uses Funnel for HTTPS", text, flags=re.M)
    marker = "# UCcloud blocks bundled administration pages"
    if marker not in text:
        text += f'\n{marker}\n<LocationMatch "^/(phpmyadmin|dashboard|xampp)(/|$)">\nRequire all denied\n</LocationMatch>\n'
    conf.write_text(text)
    conf.chmod(0o644)
    Path("/opt/lampp/etc/php.ini").chmod(0o644)
    mysql = Path("/opt/lampp/etc/my.cnf")
    text = mysql.read_text()
    text = re.sub(r"^port\s*=.*$", "port = 33306", text, flags=re.M)
    text = re.sub(r"^socket\s*=.*$", "socket = /opt/lampp/var/mysql/mysql.sock", text, flags=re.M)
    text = re.sub(r"^user\s*=.*$", "user = mysql", text, flags=re.M)
    if "user = mysql" not in text:
        text = text.replace("[mysqld]", "[mysqld]\nuser = mysql")
    if "bind-address = 127.0.0.1" not in text:
        text = text.replace("[mysqld]", "[mysqld]\nbind-address = 127.0.0.1")
    mysql.write_text(text)
    # This file contains only server settings; mysqld must read it after dropping root.
    # Passwords are kept separately in the root-only client file below.
    mysql.chmod(0o644)
    service = Path("/etc/systemd/system/uccloud-xampp.service")
    service.write_text('''[Unit]
Description=UCcloud XAMPP Apache and MariaDB
After=network.target
[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/opt/lampp/lampp startapache
ExecStart=/opt/lampp/lampp startmysql
ExecStop=/opt/lampp/lampp stopapache
ExecStop=/opt/lampp/lampp stopmysql
[Install]
WantedBy=multi-user.target
''')
    run("systemctl", "daemon-reload")
    run("systemctl", "enable", "uccloud-xampp")
    run("systemctl", "restart", "uccloud-xampp")
    client = BASE / "xampp-client.cnf"
    root = hashlib.sha256((secret + ":root").encode()).hexdigest()
    client.write_text(f"[client]\nuser=root\npassword={root}\nsocket=/opt/lampp/var/mysql/mysql.sock\n")
    client.chmod(0o600)
    args = ["/opt/lampp/bin/mysql", f"--defaults-file={client}", "-uroot",
            "--binary-mode", "--default-character-set=utf8mb4", "--protocol=SOCKET", "--socket=/opt/lampp/var/mysql/mysql.sock"]
    bootstrap = ["/opt/lampp/bin/mysql", "--no-defaults", "-uroot", "--binary-mode", "--default-character-set=utf8mb4", "--protocol=SOCKET", "--socket=/opt/lampp/var/mysql/mysql.sock"]
    # SQL travels through stdin, not a command line or journal entry.
    # Bundled MariaDB releases differ in their system-table layout. Account SQL is
    # portable; never write mysql.user/global_priv directly.
    sql = xampp_account_sql(username, secret, root)
    upgraded = False
    for attempt in range(30):
        active_args = args
        result = subprocess.run(args, input=sql, text=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=15)
        if result.returncode and 'ERROR 1045' in result.stderr:
            # Official XAMPP initially has a blank root password. Only bootstrap on first setup.
            active_args = bootstrap
            result = subprocess.run(bootstrap, input=sql, text=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=15)
        if result.returncode and 'ERROR 1146' in result.stderr and not upgraded:
            # Some bundled data directories still have the older system-table layout.
            # This runs only during first-boot setup in the new student VM.
            upgrade = subprocess.run(["/opt/lampp/bin/mysql_upgrade", *active_args[1:], "--force", "--upgrade-system-tables"],
                                     text=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=120)
            if upgrade.returncode:
                raise RuntimeError("XAMPP system-table upgrade failed")
            upgraded = True
            continue
        if result.returncode == 0:
            break
        time.sleep(2)
    else:
        code = re.search(r"ERROR (\d+)", result.stderr)
        raise RuntimeError("XAMPP database initialization failed" + (f" (code {code[1]})" if code else ""))
    accounts = subprocess.run(args + ["-N", "-B", "-e", "SELECT User,Host FROM mysql.user WHERE User='' OR (User='root' AND Host<>'localhost')"],
                              check=True, text=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=15)
    def literal(value):
        return "'" + value.replace("\\", "\\\\").replace("'", "''") + "'"
    drops = ["DROP USER IF EXISTS " + literal(name) + "@" + literal(host) + ";"
             for name, host in (line.split("\t", 1) for line in accounts.stdout.splitlines())]
    if drops:
        subprocess.run(args, input="\n".join(drops), check=True, text=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15)
    run("curl", "--retry", "10", "--retry-connrefused", "--retry-delay", "2", "-fsS", f"http://127.0.0.1:{port}/", capture=True)
    return f"XAMPP {version}: http://localhost:{port}\n~/xampp-website/index.php 수정\nDB: localhost:33306 · ID: {username} · DB 이름: app\n비밀번호: VM 생성 때 설정한 값\n공개: 접속 화면의 공개하기 버튼"


def main():
    config_path = Path(os.environ.get('UCCLOUD_INSTALL_CONFIG', '/etc/uccloud-software.json'))
    config = json.loads(config_path.read_text())
    additive = bool(config.get('additive'))
    original_path = Path('/etc/uccloud-software.json')
    original = json.loads(original_path.read_text()) if additive and original_path.exists() else {}
    user = pwd.getpwnam(config["username"])
    home = Path(user.pw_dir)
    selections = config["selections"]
    selected = {item["id"]: item["version"] for item in selections}
    ports = {item["id"]: item["port"] for item in selections if "port" in item}
    services = {key: version for key, version in selected.items() if key in {"nginx", "apache", "postgres", "mysql", "maria", "redis", "mongo", "tomcat", "react"}}
    notes = ["UCcloud 설치 안내", "선택한 프로그램은 첫 부팅에 자동 설치됩니다.", ""]
    if any(key in selected for key in ("react", "typescript", "firebase")):
        selected.setdefault("node", "24")
    if "spring" in selected or "firebase" in selected:
        selected.setdefault("java", "21")
    if additive:
        # Existing runtimes (including implicit dependencies) are never replaced.
        for key, command in [('node', 'node'), ('java', 'java'), ('python', 'python'), ('docker', 'docker')]:
            if key in selected and subprocess.run(['which', command], stdout=subprocess.DEVNULL).returncode == 0:
                if key == 'node':
                    # Do not silently reuse an unsafe runtime or replace a student's runtime.
                    require_safe_node(run('node', '--version', capture=True))
                selected.pop(key)
    try:
        started = json.loads(STATE.read_text()).get("started_at", int(time.time()))
    except (OSError, ValueError):
        started = int(time.time())
    count = len(selections)
    PROGRESS.update(started_at=started, step=0,
                    total_steps=2 + int(bool(services or "docker" in selected))
                    + sum(key in {"node", "java", "python"} for key in selected)
                    + sum(key in selected for key in ("spring", "react", "typescript", "firebase", "xampp"))
                    + int(bool(services)),
                    estimate_min_seconds=180 if count <= 3 else 300 if count <= 8 else 480,
                    estimate_max_seconds=480 if count <= 3 else 900 if count <= 8 else 1500)
    status("installing", "설치에 필요한 파일을 준비하고 있습니다. SSH는 지금 사용할 수 있습니다.")
    update()
    apt("curl", "ca-certificates", "git", "python3-yaml")
    Path("/etc/apt/keyrings").mkdir(exist_ok=True)
    if services or "docker" in selected:
        status("installing", "Docker를 설치하는 중입니다. 잠시 기다려 주세요.")
        if not additive or subprocess.run(['which', 'docker'], stdout=subprocess.DEVNULL).returncode:
            docker_install(selected.get("docker", "29"))
        notes.append("Docker: sudo docker · sudo docker compose")
    for key, version in selected.items():
        if key in {"node", "java", "python"}:
            status("installing", f"{key} {version} 설치 중입니다. 잠시 기다려 주세요.")
            notes.append(native(key, version))
    BASE.mkdir(mode=0o700, exist_ok=True)
    secret_file = BASE / "passwords.json"
    passwords = json.loads(secret_file.read_text()) if secret_file.exists() else {}
    credential_file = Path('/etc/uccloud-service-credentials.json')
    credentials = json.loads(credential_file.read_text()) if credential_file.exists() else {}
    if additive:
        credentials.update(config.get('credentials', {}))
        credential_file.write_text(json.dumps(credentials))
        credential_file.chmod(0o600)
    usernames = {key: value['username'] for key, value in credentials.items()}
    for key in list(services) + (["xampp"] if "xampp" in selected else []):
        passwords.setdefault(key, credentials.get(key, {}).get('password') or secrets.token_hex(24))
    secret_file.write_text(json.dumps(passwords))
    secret_file.chmod(0o600)
    for key in ("spring", "react", "typescript", "firebase", "xampp"):
        if key not in selected:
            continue
        status("installing", f"{key} 설치 중입니다. 잠시 기다려 주세요.")
        version = selected[key]
        if key == "spring":
            notes.extend(["", spring_project(version, ports[key], user)])
        elif key == "react":
            notes.extend(["", react_project(version, ports[key], selected.get("typescript"), user)])
        elif key == "typescript":
            npm_global(f"typescript@{version}")
            user_run(user, home, "tsc", "--version")
            notes.append(f"TypeScript {version}: tsc --version")
        elif key == "firebase":
            npm_global(f"firebase-tools@{version}")
            user_run(user, home, "firebase", "--version")
            notes.extend(["", "Firebase: firebase login --no-localhost", "Firestore는 Google의 서비스입니다. 로그인 후 프로젝트를 직접 선택하세요.", "설정 시작: firebase init firestore"])
        else:
            notes.extend(["", xampp_install(version, ports[key], user, passwords[key], usernames.get(key, 'student'))])
    if services:
        import yaml
        compose_file = BASE / 'compose.yml'
        compose = yaml.safe_load(compose_file.read_text()) if additive and compose_file.exists() else dict(services={}, volumes={})
        compose.setdefault('services', {})
        compose.setdefault('volumes', {})
        for key, version in services.items():
            if key in compose['services']:
                raise RuntimeError('Existing service cannot be replaced')
            service, note = container(key, version, passwords[key], home, ports[key], usernames.get(key, 'student'))
            compose["services"][key] = service
            if key not in {"nginx", "apache", "tomcat", "react"}:
                compose["volumes"][f"{key}-data"] = {}
            notes.extend(["", note])
        file = BASE / "compose.yml"
        file.write_text(yaml.safe_dump(compose))
        file.chmod(0o600)
        status("installing", "선택한 프로그램을 실행하는 중입니다. 잠시 기다려 주세요.")
        run("docker", "compose", "-p", "uccloud", "-f", str(file), "up", "-d", "--wait", "--wait-timeout", "360",
            *(['--no-deps', *services.keys()] if additive else []))
        notes.extend(["", "실행 상태: sudo docker compose -p uccloud -f /opt/uccloud-services/compose.yml ps",
                      "DB 주소는 서버 안에서만 연결됩니다. 비밀번호는 공유하지 마세요."])
        directories = (home / "website", home / "jsp") if not additive else (
            ([home / 'website'] if set(services) & {'nginx','apache'} else []) +
            ([home / 'jsp'] if 'tomcat' in services else []))
        for directory in directories:
            if directory.exists():
                own_tree(directory, user)
    status("installing", "실행 상태와 사용 안내를 확인하고 있습니다.")
    guide = home / "UCcloud-설치안내.txt"
    previous_guide = guide.read_text() if additive and guide.exists() else ''
    guide.write_text(previous_guide + '\n'.join(notes) + '\n')
    guide.chmod(0o600)
    os.chown(guide, user.pw_uid, user.pw_gid)
    completed = STATE.parent / "completed"
    completed.write_text("installed\n")
    completed.chmod(0o600)
    if additive:
        original_path.write_text(json.dumps(dict(username=config['username'], selections=original.get('selections', []) + selections)))
        original_path.chmod(0o644)
        config.pop('credentials', None)
        config_path.write_text(json.dumps(config))
        config_path.chmod(0o600)
    status("ready", "프로그램 설치·설정·실행이 끝났습니다. ‘설치 안내’에서 주소와 사용 방법을 확인하세요.")


if __name__ == "__main__":
    def interrupted(signum, frame):
        raise RuntimeError("Software installation was interrupted")
    signal.signal(signal.SIGTERM, interrupted)
    try:
        main()
    except Exception:
        try:
            retries = int(run("systemctl", "show", "uccloud-software-install.service", "-p", "NRestarts", "--value", capture=True))
        except Exception:
            retries = 2
        message = "프로그램 설치에 문제가 생겼습니다. 2분 뒤 자동으로 다시 시도합니다." if retries < 2 and not os.environ.get('UCCLOUD_INSTALL_CONFIG') else "프로그램 설치에 실패했습니다. 운영자에게 문의해 주세요. SSH는 계속 사용할 수 있습니다."
        status("failed", message)
        raise
