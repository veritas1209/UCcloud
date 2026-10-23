"""Controller-only disposable VM preset integration check. No existing VM mutations."""
import asyncio
import argparse
import json
import secrets
import time
import hashlib
from pathlib import Path
import asyncssh
from tests.live_browser_ssh_check import team_row, cleanup, wait_for_guest_ssh, TEAM_SLUG
from backend.openstack_service import create_server, list_catalog, quota, team_connection
from backend import terminal
from backend.software import validate_software

CANDIDATE = Path(__file__).resolve().parent.parent / 'backend/guest_software.py'
REPAIR = False
START_DIGEST = ''


async def check(address, password, host_key, selections):
    async with asyncssh.connect(address, username="student", password=password, client_keys=[],
                               known_hosts=f"{address} {host_key}\n".encode()) as conn:
        async def sudo(command):
            process = await conn.create_process("sudo -S -p '' " + command)
            process.stdin.write(password + "\n")
            process.stdin.write_eof()
            result = await process.wait(timeout=900)
            if result.exit_status:
                raise RuntimeError(result.stderr or result.stdout)
            return result.stdout
        previous = None
        candidate_digest = START_DIGEST
        repairs = 0
        for _ in range(180):
            result = await conn.run("cat /var/lib/uccloud-software/status.json 2>/dev/null", check=False)
            if result.stdout.strip():
                state = json.loads(result.stdout)
                if state != previous:
                    print("Guest setup:", state["state"], state["message"], flush=True)
                    previous = state
                if state["state"] == "ready":
                    break
                if state["state"] == "failed":
                    # Logs contain install commands but no generated DB credentials.
                    log = await sudo("journalctl -a -u uccloud-software-install.service -n 65 --no-pager")
                    print(log, flush=True)
                    if any(item['id'] == 'xampp' for item in selections):
                        print(await sudo('journalctl -a -u uccloud-xampp.service -n 25 --no-pager'), flush=True)
                        print(await sudo('ss -lnt'), flush=True)
                        print(await sudo('ldd /opt/lampp/sbin/mysqld'), flush=True)
                    if REPAIR and repairs < 3:
                        print('QA-only repair window: waiting up to 10 minutes for an updated installer candidate', flush=True)
                        for _ in range(120):
                            current = await conn.run('cat /var/lib/uccloud-software/status.json', check=False)
                            if current.stdout.strip() and json.loads(current.stdout).get('state') in {'installing', 'ready'}:
                                # Automatic retries can begin while the QA harness is waiting.
                                break
                            digest = hashlib.sha256(CANDIDATE.read_bytes()).hexdigest()
                            if digest != candidate_digest:
                                candidate_digest = digest
                                repairs += 1
                                async with conn.start_sftp_client() as sftp:
                                    target = '/home/student/.uccloud-qa-installer.py'
                                    await sftp.put(str(CANDIDATE), target)
                                    await sftp.chmod(target, 0o600)
                                    await sudo('install -o root -g root -m 755 /home/student/.uccloud-qa-installer.py /usr/local/sbin/uccloud-install-software')
                                    await sftp.remove(target)
                                await sudo('systemctl reset-failed uccloud-software-install.service')
                                await sudo('systemctl restart --no-block uccloud-software-install.service')
                                print('Updated installer is being retried inside the disposable QA VM', flush=True)
                                await asyncio.sleep(5)
                                previous = None
                                break
                            await asyncio.sleep(5)
                        else:
                            raise RuntimeError('QA repair window expired')
                        continue
                    raise RuntimeError("Guest software installer failed")
            await asyncio.sleep(10)
        else:
            raise RuntimeError("Guest software installation timed out")
        if len(selections) == 1 and selections[0]['id'] == 'xampp':
            port = selections[0]['port']
            result = await conn.run(f"curl -fsS http://127.0.0.1:{port}/", check=True)
            assert 'XAMPP' in result.stdout and 'PHP 8.2.12' in result.stdout
            await sudo("/opt/lampp/bin/mysql --defaults-extra-file=/opt/uccloud-services/xampp-client.cnf -Dapp -e 'SELECT 1'")
            bindings = await sudo('ss -lnt')
            for value in (port, 33306):
                assert f'127.0.0.1:{value}' in bindings and f'0.0.0.0:{value} ' not in bindings
            print('Actual XAMPP installer, PHP HTTP, authenticated DB and private binds passed', flush=True)
            return
        result = await conn.run("node --version && npm --version && python --version && java -version 2>&1 && mvn -version | head -n 1", check=True)
        print(result.stdout, flush=True)
        expected = {item["id"]: item["version"] for item in selections}
        ports = {item["id"]: item["port"] for item in selections if 'port' in item}
        assert result.stdout.splitlines()[0].startswith("v" + expected["node"] + ".")
        assert f"Python {expected['python']}." in result.stdout
        assert f'openjdk version "{expected["java"]}.' in result.stdout
        await conn.run("python -m venv ~/.uccloud-qa-venv && ~/.uccloud-qa-venv/bin/python -m pip --version", check=True)
        # Feed sudo through stdin, never embed the password in the shell command.
        rows = await sudo("docker compose -p uccloud -f /opt/uccloud-services/compose.yml ps --format json")
        for line in rows.splitlines():
            item = json.loads(line)
            print(dict(service=item["Service"], state=item["State"], health=item.get("Health")), flush=True)
            assert item["State"] == "running"
            if item["Service"] not in {"apache", "tomcat"}:
                assert item["Health"] == "healthy"
            image = await sudo(f"docker inspect --format '{{{{.Config.Image}}}}' {item['ID']}")
            tag = ('1.30' if item['Service'] == 'react' else expected[item["Service"]]) + ('-jdk21-temurin' if item['Service'] == 'tomcat' else '')
            assert image.strip().endswith(":" + tag)
        docker_version = await sudo("docker version --format '{{.Server.Version}}'")
        assert docker_version.startswith(expected["docker"] + ".")
        for key in ('nginx', 'apache', 'tomcat', 'xampp'):
            port = ports[key]
            await conn.run(f"curl -fsS http://127.0.0.1:{port}/ >/dev/null", check=True)
        await conn.run("tsc --version && firebase --version", check=True)
        await conn.run("cd ~/react-app && npm run build", check=True)
        await conn.run("cd ~/spring-app && mvn -q -DskipTests package", check=True)
        await conn.run("systemctl is-active uccloud-spring && systemctl is-enabled uccloud-spring && systemctl is-enabled uccloud-xampp", check=True)
        for key in ('react', 'spring'):
            for attempt in range(60):
                result = await conn.run(f"curl -fsS http://127.0.0.1:{ports[key]}/ >/dev/null", check=False)
                if result.exit_status == 0:
                    break
                await asyncio.sleep(2)
            else:
                raise RuntimeError(key + ' HTTP failed')
        await sudo("/opt/lampp/bin/mysql --defaults-extra-file=/opt/uccloud-services/xampp-client.cnf -Dapp -e 'SELECT 1'")
        blocked = await conn.run(f"curl -s -o /dev/null -w '%{{http_code}}' http://127.0.0.1:{ports['xampp']}/phpmyadmin/", check=True)
        assert blocked.stdout == '403'
        result = await conn.run("stat -c '%a' ~/UCcloud-설치안내.txt", check=True)
        assert result.stdout.strip() == "600"
        assert await sudo("stat -c '%a' /opt/uccloud-services/compose.yml") == "600\n"
        output = await sudo("ss -lnt")
        for port in (*ports.values(), 33306):
            addresses = [line.split()[3] for line in output.splitlines()[1:] if len(line.split()) >= 5 and line.split()[3].endswith(f':{port}')]
            allowed = {f'127.0.0.1:{port}', f'[::ffff:127.0.0.1]:{port}'}
            assert addresses and set(addresses) <= allowed, f'Unexpected service bind on {port}: {addresses}'
        print("All 17 presets: automatic startup, commands, DB health, Spring/React/JSP/XAMPP HTTP, private binds and credentials permissions passed", flush=True)
        return (await conn.run('cat /proc/sys/kernel/random/boot_id', check=True)).stdout.strip()


async def check_after_reboot(address, password, host_key, previous_boot, selections):
    ports = {item['id']: item['port'] for item in selections if 'port' in item}
    for attempt in range(90):
        try:
            async with asyncssh.connect(address, username='student', password=password, client_keys=[],
                                       known_hosts=f'{address} {host_key}\n'.encode(), connect_timeout=5, login_timeout=8) as conn:
                boot = (await conn.run('cat /proc/sys/kernel/random/boot_id', check=True)).stdout.strip()
                if boot == previous_boot:
                    await asyncio.sleep(2)
                    continue
                await conn.run('systemctl is-active uccloud-spring uccloud-xampp docker', check=True)
                for key in ('nginx', 'apache', 'spring', 'react', 'tomcat', 'xampp'):
                    await conn.run(f'curl -fsS http://127.0.0.1:{ports[key]}/ >/dev/null', check=True)
                process = await conn.create_process("sudo -S -p '' docker compose -p uccloud -f /opt/uccloud-services/compose.yml ps --format json")
                process.stdin.write(password + '\n')
                process.stdin.write_eof()
                result = await process.wait(timeout=20)
                assert result.exit_status == 0
                rows = [json.loads(line) for line in result.stdout.splitlines()]
                if len(rows) != 9 or any(item['State'] != 'running' or item['Service'] not in {'apache', 'tomcat'} and item.get('Health') != 'healthy' for item in rows):
                    await asyncio.sleep(2)
                    continue
                state = json.loads((await conn.run('cat /var/lib/uccloud-software/status.json', check=True)).stdout)
                assert state['state'] == 'ready', 'completed installer reran after reboot'
                print('VM reboot: all prepared web services started automatically, without reinstalling', flush=True)
                return
        except (OSError, asyncssh.Error):
            await asyncio.sleep(2)
    raise RuntimeError('Prepared services did not recover after VM reboot')


def main(variant, only_xampp=False):
    team = team_row()
    catalog = list_catalog(TEAM_SLUG)
    flavor = next(item for item in catalog["flavors"] if item["ram"] >= 8192)
    usage = quota(team["openstack_project_id"])
    for key, required in (("instances", 1), ("cores", flavor["vcpus"]), ("ram", flavor["ram"])):
        assert usage[key]["limit"] - usage[key]["in_use"] >= required, "Insufficient QA quota"
    selections = validate_software([dict(id=item["id"], version=item["versions"][min(variant, len(item["versions"]) - 1)]) for item in catalog["software"]])
    if only_xampp:
        selections = [item for item in selections if item['id'] == 'xampp']
    print('Testing versions:', ', '.join(f"{item['id']}={item['version']}" for item in selections), flush=True)
    name = "portal-qa-software-" + secrets.token_hex(6)
    password = secrets.token_urlsafe(24)
    server_id = None
    try:
        server = create_server(TEAM_SLUG, team["openstack_project_id"], "portal-qa", team["network_id"], name,
                               catalog["images"][0]["id"], flavor["id"], "student", password, software=selections)
        server_id = server["id"]
        print("Disposable preset VM created:", server_id, flush=True)
        user = dict(team_slug=TEAM_SLUG, openstack_project_id=team["openstack_project_id"], cidr=team["cidr"], network_id=team["network_id"])
        deadline = time.monotonic() + 240
        while time.monotonic() < deadline:
            try:
                address = terminal._ssh_target(user, server_id)
                break
            except ValueError:
                time.sleep(5)
        else:
            raise RuntimeError("No guest address")
        host_key = asyncio.run(wait_for_guest_ssh(address, "student", password))
        previous_boot = asyncio.run(check(address, password, host_key, selections))
        if not only_xampp:
            team_connection(TEAM_SLUG).compute.reboot_server(server_id, reboot_type='SOFT')
            asyncio.run(check_after_reboot(address, password, host_key, previous_boot, selections))
    finally:
        cleanup(name, server_id)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--variant", type=int, choices=(0, 1, 2), default=0)
    parser.add_argument('--only-xampp', action='store_true')
    parser.add_argument('--repair', action='store_true')
    arguments = parser.parse_args()
    REPAIR = arguments.repair
    START_DIGEST = hashlib.sha256(CANDIDATE.read_bytes()).hexdigest()
    main(arguments.variant, arguments.only_xampp)
