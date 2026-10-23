"""Team-scoped browser SSH. Optional AI sudo credentials are encrypted/expiring."""

from __future__ import annotations

import asyncio
import hmac
import ipaddress
import json
import logging
import os
import re
import threading
from datetime import datetime, timedelta, timezone
from uuid import UUID

import asyncssh
from fastapi import APIRouter, WebSocket
from starlette.websockets import WebSocketDisconnect

from .database import audit, db
from .openstack_service import _owned_server, team_connection
from .security import token_hash
from .guest_files import Uploads
from .publication import Publisher

LOG = logging.getLogger("cloud_portal.terminal")
router = APIRouter()
COOKIE_NAME = "school_cloud_session"
BROWSER_ORIGIN = os.getenv("PORTAL_BROWSER_SSH_ORIGIN", "").rstrip("/")
USER_PATTERN = re.compile(r"^[a-z_][a-z0-9_-]{1,31}$")
FIRST_MESSAGE_LIMIT = 4096
INPUT_MESSAGE_LIMIT = 65536
ACTIVE_LOCK = threading.Lock()
ACTIVE_BY_USER: dict[int, int] = {}


def _reserve_slot(user_id: int) -> bool:
    with ACTIVE_LOCK:
        if ACTIVE_BY_USER.get(user_id, 0) >= 3 or sum(ACTIVE_BY_USER.values()) >= 30:
            return False
        ACTIVE_BY_USER[user_id] = ACTIVE_BY_USER.get(user_id, 0) + 1
        return True


def _release_slot(user_id: int) -> None:
    with ACTIVE_LOCK:
        count = ACTIVE_BY_USER.get(user_id, 0)
        if count <= 1:
            ACTIVE_BY_USER.pop(user_id, None)
        else:
            ACTIVE_BY_USER[user_id] = count - 1


def _session(token: str | None) -> dict | None:
    if not token:
        return None
    with db() as conn:
        row = conn.execute(
            """SELECT u.id, u.username, u.status, u.team_id, t.slug AS team_slug,
                      t.openstack_project_id, t.network_id, t.cidr, s.csrf_token, s.token_hash AS session_hash
               FROM sessions s JOIN users u ON u.id=s.user_id
               LEFT JOIN teams t ON t.id=u.team_id
               WHERE s.token_hash=? AND s.expires_at > CURRENT_TIMESTAMP""",
            (token_hash(token),),
        ).fetchone()
        return dict(row) if row else None


def _ssh_target(user: dict, server_id: str) -> str:
    """Resolve the VM address from Neutron, never from the browser."""
    if not all((user.get("team_slug"), user.get("openstack_project_id"),
                user.get("network_id"), user.get("cidr"))):
        raise ValueError("팀 준비가 끝나지 않았습니다.")
    conn = team_connection(user["team_slug"])
    server = _owned_server(conn, server_id, user["openstack_project_id"])
    if server.status != "ACTIVE":
        raise ValueError("VM이 켜질 때까지 기다려 주세요.")
    network = ipaddress.ip_network(user["cidr"], strict=True)
    if network.version != 4 or network.prefixlen != 24 or not network.subnet_of(ipaddress.ip_network("10.30.0.0/16")):
        raise ValueError("팀 네트워크를 확인하지 못했습니다.")
    addresses = []
    for port in conn.network.ports(device_id=server_id, network_id=user["network_id"]):
        if port.project_id != user["openstack_project_id"]:
            continue
        for item in port.fixed_ips or []:
            address = ipaddress.ip_address(item["ip_address"])
            if address.version == 4 and address in network:
                addresses.append(str(address))
    if len(addresses) != 1:
        raise ValueError("VM의 IP를 확인 중입니다. 잠시 후 다시 시도해 주세요.")
    return addresses[0]


def _allow_attempt(user_id: int, server_id: str) -> bool:
    key = f"browser-ssh:{user_id}:{server_id}"
    now = datetime.now(timezone.utc)
    with db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT count,expires_at FROM throttle WHERE key=?", (key,)).fetchone()
        if row and datetime.fromisoformat(row["expires_at"]) > now and row["count"] >= 8:
            return False
        count = row["count"] + 1 if row and datetime.fromisoformat(row["expires_at"]) > now else 1
        expires = (now + timedelta(minutes=15)).isoformat()
        conn.execute("INSERT OR REPLACE INTO throttle(key,count,expires_at) VALUES(?,?,?)",
                     (key, count, expires))
    return True


def _clear_attempts(user_id: int, server_id: str) -> None:
    with db() as conn:
        conn.execute("DELETE FROM throttle WHERE key=?", (f"browser-ssh:{user_id}:{server_id}",))


def _recent_vm_action(server_id: str) -> bool:
    with db() as conn:
        row = conn.execute(
            """SELECT created_at FROM audit_log
               WHERE target=? AND action IN ('instance.create','instance.start','instance.reboot')
               ORDER BY id DESC LIMIT 1""",
            (server_id,),
        ).fetchone()
    if not row:
        return False
    try:
        occurred = datetime.fromisoformat(row["created_at"])
    except ValueError:
        return False
    if occurred.tzinfo is None:
        occurred = occurred.replace(tzinfo=timezone.utc)
    age = datetime.now(timezone.utc) - occurred
    return timedelta(0) <= age <= timedelta(minutes=5)


async def _connect_ssh(websocket: WebSocket, address: str, username: str, password: str,
                       known_hosts: bytes | None, recent: bool,
                       retry_window: float = 120, retry_interval: float = 8):
    deadline = asyncio.get_running_loop().time() + retry_window if recent else 0
    waiting = False
    while True:
        try:
            return await asyncssh.connect(
                address, port=22, username=username, password=password,
                client_keys=[], known_hosts=known_hosts, connect_timeout=10,
                login_timeout=12,
            )
        except asyncssh.PermissionDenied:
            message = "VM 접속 ID 또는 비밀번호가 올바르지 않습니다."
            if recent:
                message += " VM을 방금 만들었다면 1~2분 뒤 다시 시도해 주세요."
            raise ValueError(message) from None
        except asyncssh.HostKeyNotVerifiable:
            raise ValueError("VM의 SSH 신원이 바뀌었습니다. 운영자에게 문의해 주세요.") from None
        except (OSError, asyncssh.Error):
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                raise ValueError("VM에 아직 연결할 수 없습니다. 방금 만들었다면 1~2분 기다린 뒤 다시 시도해 주세요.") from None
            if not waiting:
                await websocket.send_json({"type": "waiting"})
                waiting = True
            await asyncio.sleep(min(retry_interval, remaining))


def _known_hosts(server_id: str, address: str) -> bytes | None:
    with db() as conn:
        row = conn.execute("SELECT public_key FROM instance_ssh_host_keys WHERE server_id=?",
                           (server_id,)).fetchone()
    return f"{address} {row['public_key']}\n".encode() if row else None


def _pin_host_key(server_id: str, key: str) -> None:
    with db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT public_key FROM instance_ssh_host_keys WHERE server_id=?",
                           (server_id,)).fetchone()
        if row and not hmac.compare_digest(row["public_key"], key):
            raise ValueError("VM의 SSH 신원이 바뀌었습니다. 운영자에게 문의해 주세요.")
        if not row:
            conn.execute("INSERT INTO instance_ssh_host_keys(server_id,public_key) VALUES(?,?)",
                         (server_id, key))


async def _send_output(websocket: WebSocket, process: asyncssh.SSHClientProcess) -> None:
    while chunk := await process.stdout.read(4096):
        await websocket.send_bytes(chunk)


def _first_credentials(raw: str) -> tuple[str, str, int, int]:
    if len(raw) > FIRST_MESSAGE_LIMIT:
        raise ValueError("VM 접속 ID와 비밀번호를 확인해 주세요.")
    try:
        first = json.loads(raw)
    except json.JSONDecodeError:
        raise ValueError("VM 접속 ID와 비밀번호를 확인해 주세요.") from None
    if not isinstance(first, dict):
        raise ValueError("VM 접속 ID와 비밀번호를 확인해 주세요.")
    username, password = first.get("username"), first.get("password")
    cols, rows = first.get("cols", 80), first.get("rows", 24)
    if (not isinstance(username, str) or not USER_PATTERN.fullmatch(username)
            or not isinstance(password, str) or not 1 <= len(password) <= 128
            or type(cols) is not int or type(rows) is not int):
        raise ValueError("VM 접속 ID와 비밀번호를 확인해 주세요.")
    return username, password, cols, rows


async def _receive_input(websocket: WebSocket, process: asyncssh.SSHClientProcess,
                         uploads=None, feature_guard=None, record_upload=None, publisher=None, agent_handler=None, software_handler=None) -> None:
    while True:
        raw = await websocket.receive_text()
        if len(raw) > INPUT_MESSAGE_LIMIT:
            raise ValueError("입력 길이가 너무 깁니다.")
        try:
            message = json.loads(raw)
        except json.JSONDecodeError:
            raise ValueError("요청을 확인해 주세요.") from None
        if not isinstance(message, dict):
            raise ValueError("요청을 확인해 주세요.")
        if message.get('type') == 'software.install' and software_handler:
            request_id = message.get('request_id')
            if not isinstance(request_id, str) or not re.fullmatch(r'[0-9a-f-]{36}', request_id):
                raise ValueError('프로그램 설치 요청을 확인해 주세요.')
            try:
                if not feature_guard():
                    raise ValueError('로그인이 만료됐습니다. 다시 로그인해 주세요.')
                result = await software_handler(message)
                await websocket.send_json(dict(type='software.install',request_id=request_id,**result))
            except Exception as exc:
                await websocket.send_json(dict(type='software.install',request_id=request_id,state='error',
                    message=str(exc) if isinstance(exc,ValueError) else '추가 설치를 시작하지 못했습니다. 다시 접속해 주세요.'))
        elif message.get('type') == 'agent.issue' and agent_handler:
            request_id = message.get('request_id')
            if not isinstance(request_id, str) or not re.fullmatch(r'[0-9a-f-]{36}', request_id):
                raise ValueError('AI 연결 요청을 확인해 주세요.')
            try:
                if not feature_guard():
                    raise ValueError('로그인이 만료됐습니다. 다시 로그인해 주세요.')
                result = await asyncio.wait_for(agent_handler(message), timeout=60)
                await websocket.send_json(dict(type='agent', request_id=request_id, state='ready', **result))
            except Exception as exc:
                from fastapi import HTTPException
                detail = str(exc) if isinstance(exc, ValueError) else str(exc.detail) if isinstance(exc, HTTPException) else 'AI 연결을 만들지 못했습니다. 잠시 후 다시 시도해 주세요.'
                await websocket.send_json(dict(type='agent', request_id=request_id, state='error', message=detail))
        elif isinstance(message.get('type'), str) and message['type'].startswith('upload.') and uploads:
            request_id = message.get('request_id')
            if not isinstance(request_id, str) or not re.fullmatch(r'[0-9a-f-]{36}', request_id):
                raise ValueError('파일 전송 요청을 확인해 주세요.')
            try:
                if feature_guard and not feature_guard():
                    raise ValueError('로그인이 만료됐습니다. 다시 로그인해 주세요.')
                result = await asyncio.wait_for(uploads.handle(message), timeout=30)
                if result.get('state') == 'complete' and record_upload:
                    record_upload(result)
                await websocket.send_json(dict(type='upload', request_id=request_id, **result))
            except Exception as exc:
                try:
                    await asyncio.wait_for(uploads.cancel(), timeout=5)
                except Exception:
                    pass
                await websocket.send_json({'type':'upload', 'request_id':request_id, 'state':'error',
                    'message':str(exc) if isinstance(exc, ValueError) else '파일을 올리지 못했습니다. 서버의 남은 용량과 연결을 확인해 주세요.'})
        elif isinstance(message.get('type'), str) and message['type'].startswith('publish.') and publisher:
            request_id = message.get('request_id')
            if not isinstance(request_id, str) or not re.fullmatch(r'[0-9a-f-]{36}', request_id):
                raise ValueError('공개 요청을 확인해 주세요.')
            await publisher.submit(message)
        elif message.get("type") == "input":
            data = message.get("data")
            if not isinstance(data, str) or len(data.encode("utf-8")) > 8192:
                raise ValueError("입력을 확인해 주세요.")
            process.stdin.write(data.encode("utf-8"))
            await process.stdin.drain()
        elif message.get("type") == "resize":
            cols, rows = message.get("cols"), message.get("rows")
            if type(cols) is not int or type(rows) is not int:
                raise ValueError("화면 크기를 확인해 주세요.")
            process.change_terminal_size(max(20, min(cols, 300)), max(10, min(rows, 100)))
        else:
            raise ValueError("알 수 없는 요청입니다.")


async def _watch_session(websocket: WebSocket, token: str, project_id: str, interval: float = 30) -> None:
    while True:
        await asyncio.sleep(interval)
        user = _session(token)
        if not user or user["status"] != "active" or user["openstack_project_id"] != project_id:
            await websocket.close(code=4401)
            return


async def _watch_software(websocket: WebSocket, connection, on_ready=None) -> None:
    """Read non-secret guest status over the already authenticated SSH connection."""
    async def unavailable():
        await websocket.send_json({"type": "software", "state": "unknown",
                                   "message": "설치 상태를 확인하지 못했습니다. SSH는 계속 사용할 수 있습니다."})

    previous = None
    for attempt in range(360):
        try:
            result = await connection.run("head -c 2048 /var/lib/uccloud-software/status.json 2>/dev/null", check=False, timeout=8)
            if not result.stdout.strip():
                if attempt >= 2:
                    await unavailable()
                    return  # Do not leave a speculative or stale installation timer running.
                await asyncio.sleep(5)
                continue  # cloud-init may still be writing the status file.
            value = json.loads(result.stdout)
            if (value.get("state") not in {"pending", "installing", "ready", "failed"}
                    or not isinstance(value.get("message"), str) or len(value["message"]) > 300):
                await unavailable()
                return
            if value != previous:
                message = dict(type="software", state=value["state"], message=value["message"])
                for key in ("started_at", "updated_at", "step", "total_steps", "estimate_min_seconds", "estimate_max_seconds"):
                    item = value.get(key)
                    if type(item) is int and 0 <= item <= 4102444800:
                        message[key] = item
                await websocket.send_json(message)
                previous = value
            if value["state"] == "ready":
                if on_ready:
                    try:
                        await on_ready(message)
                    except Exception:
                        LOG.warning('Unable to cache software completion')
                return
        except Exception:
            # A status read must never tear down the student's shell.
            try:
                await unavailable()
            except Exception:
                pass  # The shell itself may already be disconnected.
            return
        await asyncio.sleep(10)
    await unavailable()


@router.websocket("/api/instances/{server_id}/terminal")
async def terminal(websocket: WebSocket, server_id: str) -> None:
    token = websocket.cookies.get(COOKIE_NAME)
    user = _session(token)
    protocols = [part.strip() for part in websocket.headers.get("sec-websocket-protocol", "").split(",")]
    csrf = next((part[5:] for part in protocols if part.startswith("csrf.")), "")
    if (not BROWSER_ORIGIN or websocket.headers.get("origin") != BROWSER_ORIGIN
            or "uccloud" not in protocols or not user or user["status"] != "active"
            or not csrf or not hmac.compare_digest(csrf, user["csrf_token"])):
        await websocket.close(code=1008)
        return
    try:
        server_id = str(UUID(server_id))
        address = await asyncio.to_thread(_ssh_target, user, server_id)
    except Exception as exc:
        await websocket.accept(subprotocol="uccloud")
        await websocket.send_json({"type": "error", "message": str(exc) if isinstance(exc, ValueError)
                                   else "VM 연결 정보를 확인하지 못했습니다. 잠시 후 다시 시도해 주세요."})
        await websocket.close()
        return

    await websocket.accept(subprotocol="uccloud")
    reserved = _reserve_slot(user["id"])
    try:
        if not reserved:
            raise ValueError("열린 접속이 많습니다. 사용하지 않는 접속 창을 닫고 다시 시도해 주세요.")
        username, password, cols, rows = _first_credentials(
            await asyncio.wait_for(websocket.receive_text(), timeout=30))
        if not _allow_attempt(user["id"], server_id):
            raise ValueError("접속 시도가 많습니다. 15분 뒤 다시 시도해 주세요.")
        known_hosts = _known_hosts(server_id, address)
        connection = await _connect_ssh(websocket, address, username, password, known_hosts,
                                        _recent_vm_action(server_id))
        async with connection:
            key = connection.get_server_host_key().export_public_key("openssh").decode().strip()
            _pin_host_key(server_id, key)
            _clear_attempts(user["id"], server_id)
            process = await connection.create_process(
                term_type="xterm-256color", term_size=(max(20, min(cols, 300)), max(10, min(rows, 100))),
                encoding=None, stderr=asyncssh.STDOUT,
            )
            with db() as conn:
                audit(conn, user["id"], "instance.terminal", server_id)
            await websocket.send_json({"type": "ready"})
            uploads = Uploads(connection)
            def feature_guard():
                current = _session(token)
                return bool(current and current['status'] == 'active'
                            and current['openstack_project_id'] == user['openstack_project_id'])
            def record_upload(result):
                with db() as conn:
                    audit(conn, user['id'], 'instance.file.upload', server_id,
                          json.dumps({'name':result['name'], 'size':result['size']}, ensure_ascii=False))
            async def publication_guard(operation):
                if not feature_guard():
                    raise ValueError('로그인이 만료됐습니다. 다시 로그인해 주세요.')
                if await asyncio.to_thread(_ssh_target, user, server_id) != address:
                    raise ValueError('VM 연결 정보가 바뀌었습니다. 다시 접속해 주세요.')
                if operation in ('publish.start', 'publish.stop', 'software.install'):
                    kind = 'software' if operation == 'software.install' else 'publish'
                    key = f"{kind}:{user['id']}:{server_id}"
                    now = datetime.now(timezone.utc)
                    with db() as conn:
                        conn.execute('BEGIN IMMEDIATE')
                        row = conn.execute('SELECT count,expires_at FROM throttle WHERE key=?', (key,)).fetchone()
                        active = row and datetime.fromisoformat(row['expires_at']) > now
                        if active and row['count'] >= 6:
                            raise ValueError('요청이 많습니다. 10분 뒤 다시 시도해 주세요.')
                        conn.execute('INSERT OR REPLACE INTO throttle(key,count,expires_at) VALUES(?,?,?)',
                                     (key, row['count']+1 if active else 1, (now+timedelta(minutes=10)).isoformat()))
            async def record_publication(operation, result):
                with db() as conn:
                    if result.get('url'):
                        conn.execute(
                            'INSERT INTO vm_sites(team_id,server_id,url,state,message) VALUES(?,?,?,?,?) '
                            'ON CONFLICT(team_id,server_id) DO UPDATE SET url=excluded.url,state=excluded.state,'
                            'http_status=NULL,message=excluded.message,checked_at=CURRENT_TIMESTAMP',
                            (user['team_id'], server_id, result['url'], 'warning', '공개 설정 완료. 사이트 상태 확인을 눌러 주세요.'))
                    else:
                        conn.execute("UPDATE vm_sites SET state='offline',message='공개가 꺼져 있습니다.',"
                                     'http_status=NULL,checked_at=CURRENT_TIMESTAMP WHERE team_id=? AND server_id=?',
                                     (user['team_id'], server_id))
                    audit(conn, user['id'], 'instance.'+operation, server_id,
                          json.dumps({k:result[k] for k in ('url','port','state') if k in result}, ensure_ascii=False))
            publisher = Publisher(connection, password, server_id, websocket.send_json,
                                  publication_guard, record_publication)
            password = ''
            async def agent_handler(message):
                from .agent_access import issue
                return await issue(user, server_id, username, connection, message.get('hours', 4), publisher.password)
            from .software_state import completed, remember, forget
            async def cache_software(message):
                if feature_guard():
                    await asyncio.to_thread(remember,user['team_id'],server_id,message)
            software_task = None
            async def software_handler(message):
                nonlocal software_task
                from .software_install import start
                await publication_guard('software.install')
                result = await start(connection,publisher.password,username,message.get('selections'))
                if software_task:
                    software_task.cancel()
                    await asyncio.gather(software_task,return_exceptions=True)
                await asyncio.to_thread(forget,user['team_id'],server_id)
                with db() as conn:
                    audit(conn,user['id'],'instance.software.add',server_id)
                software_task = asyncio.create_task(_watch_software(websocket,connection,cache_software))
                return result
            if not await asyncio.to_thread(completed,user['team_id'],server_id):
                software_task = asyncio.create_task(_watch_software(websocket,connection,cache_software))
            tasks = [asyncio.create_task(_send_output(websocket, process)),
                     asyncio.create_task(_receive_input(websocket, process, uploads, feature_guard, record_upload, publisher, agent_handler, software_handler)),
                     asyncio.create_task(_watch_session(websocket, token, user["openstack_project_id"]))]
            try:
                # Active shells follow the login session, not a separate one-hour timer.
                done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                for task in done:
                    task.result()
            finally:
                if software_task:
                    software_task.cancel()
                    await asyncio.gather(software_task, return_exceptions=True)
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                await publisher.close()
                try:
                    await asyncio.wait_for(uploads.close(), timeout=10)
                except Exception:
                    pass  # Unreachable guest may retain a hidden, unpublished partial file.
    except WebSocketDisconnect:
        pass
    except (ValueError, json.JSONDecodeError) as exc:
        await websocket.send_json({"type": "error", "message": str(exc)})
    except Exception:
        LOG.exception("Browser SSH failed for VM %s", server_id)
        try:
            await websocket.send_json({"type": "error", "message": "SSH 연결이 끊겼습니다. 다시 접속해 주세요."})
        except RuntimeError:
            pass
    finally:
        if reserved:
            _release_slot(user["id"])
        try:
            await websocket.close()
        except RuntimeError:
            pass
