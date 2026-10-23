from __future__ import annotations

import hmac
import json
import logging
import os
import re
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit
from uuid import UUID

from fastapi import BackgroundTasks, Cookie, Depends, FastAPI, Header, HTTPException, Request, Response
from fastapi.responses import FileResponse
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, EmailStr, Field, field_validator
from .software import validate_software, public_software
from .software_state import completed as software_completed, forget as forget_software
from starlette.middleware.body_limit import RequestBodyLimitMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .database import audit, db, init_db
from .openstack_service import (
    _owned_server, cluster_capacity, create_server, delete_server, ensure_team_project, get_console_url,
    InstanceQuotaError, InstanceStateError, list_catalog, list_servers, quota, server_action,
    team_connection,
)
from .security import hash_password, needs_password_rehash, new_token, token_hash, verify_password
from . import recovery
from . import hardware
from .site_status import check_public_site, normalize_site_url
from .vm_backups import (
    BackupConflict, backup_busy, has_backups, list_team_backups, queue_backup,
    queue_delete_backup, queue_restore,
)

LOG = logging.getLogger("cloud_portal")
ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "static"
SESSION_HOURS = max(1, min(24, int(os.getenv("PORTAL_SESSION_HOURS", "12"))))
COOKIE_SECURE = os.getenv("PORTAL_COOKIE_SECURE", "false").lower() in {"1", "true", "yes"}
COOKIE_NAME = "school_cloud_session"
DUMMY_HASH = hash_password("not-a-real-password")
AUTH_MIN_SECONDS = 1.0
LOGIN_IP_ATTEMPTS_PER_MINUTE = max(1, min(1000, int(os.getenv("PORTAL_LOGIN_IP_ATTEMPTS_PER_MINUTE", "120"))))
REGISTRATIONS_PER_HOUR = max(1, min(10000, int(os.getenv("PORTAL_REGISTRATIONS_PER_HOUR", "1000"))))
MAX_TEAMS = max(1, min(200, int(os.getenv("PORTAL_MAX_TEAMS", "20"))))
VM_BACKUPS_ENABLED = os.getenv("PORTAL_VM_BACKUPS_ENABLED", "false").lower() in {"1", "true", "yes"}
REGISTRATION_CODE = os.getenv("PORTAL_REGISTRATION_CODE", "").strip().upper()
CONSOLE_HOSTS = {host.strip() for host in os.getenv("PORTAL_CONSOLE_UPSTREAM_HOSTS", "172.141.0.231,172.29.236.11").split(",") if host.strip()}
CONSOLE_BROWSER_ORIGIN = os.getenv("PORTAL_CONSOLE_BROWSER_ORIGIN", "http://127.0.0.1:6080")
CONSOLE_UI_PATH = os.getenv("PORTAL_CONSOLE_UI_PATH", "")
BROWSER_SSH_ORIGIN = os.getenv("PORTAL_BROWSER_SSH_ORIGIN", "")
ALLOWED_HOSTS = [host.strip() for host in os.getenv("PORTAL_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",") if host.strip()]

app = FastAPI(title="School Cloud Portal", version="1.0.0", docs_url=None, redoc_url=None, openapi_url=None)


@app.exception_handler(RequestValidationError)
async def safe_validation_error(request, exc):
    # Never echo request inputs: VM and service passwords can be nested in them.
    return JSONResponse(status_code=422, content={'detail':[
        {key: error[key] for key in ('loc', 'msg', 'type') if key in error} for error in exc.errors()]})
app.add_middleware(TrustedHostMiddleware, allowed_hosts=ALLOWED_HOSTS)
app.add_middleware(RequestBodyLimitMiddleware, max_body_size=16384)
app.mount("/assets", StaticFiles(directory=STATIC), name="assets")


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    connect_src = "'self'"
    if request.url.path == "/assets/console.html":
        console_origin = urlsplit(CONSOLE_BROWSER_ORIGIN)
        websocket_scheme = "wss" if console_origin.scheme == "https" else "ws"
        connect_src += f" {websocket_scheme}://{console_origin.netloc}"
    if request.url.path == "/terminal" and BROWSER_SSH_ORIGIN:
        terminal_origin = urlsplit(BROWSER_SSH_ORIGIN)
        websocket_scheme = "wss" if terminal_origin.scheme == "https" else "ws"
        connect_src += f" {websocket_scheme}://{terminal_origin.netloc}"
    response.headers["Content-Security-Policy"] = (
        "default-src 'none'; base-uri 'none'; frame-ancestors 'none'; "
        f"form-action 'self'; connect-src {connect_src}; img-src 'self' data:; "
        "script-src 'self'; style-src 'self' 'unsafe-inline'; manifest-src 'self'"
    )
    if request.url.path in {"/terminal", "/reset-password", "/assets/index.html", "/assets/funnel-guide.html", "/assets/terminal.html", "/assets/reset-password.html", "/assets/console.html"}:
        response.headers["X-Robots-Tag"] = "noindex, nofollow"
    if request.url.path.startswith("/api/") or response.headers.get("content-type", "").startswith("text/html"):
        response.headers["Cache-Control"] = "no-store"
    if COOKIE_SECURE:
        response.headers["Strict-Transport-Security"] = "max-age=31536000"
    return response


class RegisterBody(BaseModel):
    username: str = Field(min_length=3, max_length=32, pattern=r"^[a-zA-Z0-9._-]+$")
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    team_name: str = Field(min_length=2, max_length=60)
    registration_code: str | None = Field(default=None, max_length=64)
    join_existing: bool = False


class LoginBody(BaseModel):
    username: str = Field(min_length=1, max_length=254)
    password: str = Field(min_length=1, max_length=128)


class ChangePasswordBody(BaseModel):
    current_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=8, max_length=128)


class RecoveryEmailBody(BaseModel):
    email: EmailStr


class ResetPasswordBody(BaseModel):
    token: str = Field(min_length=40, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    password: str = Field(min_length=8, max_length=128)


class SSHKeyBody(BaseModel):
    public_key: str = Field(min_length=60, max_length=1024)


class SoftwareBody(BaseModel):
    id: str = Field(min_length=1, max_length=20)
    version: str = Field(min_length=1, max_length=10)
    port: int | None = Field(default=None, ge=1024, le=65535)
    username: str | None = Field(default=None, min_length=2, max_length=32)
    password: str | None = Field(default=None, min_length=8, max_length=128, repr=False)


class InstanceBody(BaseModel):
    name: str = Field(min_length=2, max_length=63, pattern=r"^[a-zA-Z0-9][a-zA-Z0-9._-]*$")
    image_id: UUID
    flavor_id: str = Field(min_length=1, max_length=128)
    vm_username: str = Field(min_length=2, max_length=32, pattern=r"^[a-z_][a-z0-9_-]*$")
    vm_password: str = Field(min_length=8, max_length=128)
    request_id: UUID | None = None
    software: list[SoftwareBody] = Field(default_factory=list, max_length=20)

    @field_validator("software")
    @classmethod
    def allowed_software(cls, value):
        validate_software([item.model_dump() for item in value])
        return value


class ActionBody(BaseModel):
    action: str = Field(pattern=r"^(start|stop|reboot)$")


class SiteCheckBody(BaseModel):
    url: str = Field(min_length=12, max_length=200)


def _public_user(row) -> dict:
    return {
        "id": row["id"], "username": row["username"], "email": row["email"],
        "role": row["role"], "status": row["status"],
        "requested_team": row["requested_team"], "team_id": row["team_id"],
        "team_owner": bool(row["team_owner"]),
        "team_name": row["team_name"] if "team_name" in row.keys() else None,
        "team_slug": row["team_slug"] if "team_slug" in row.keys() else None,
        "has_ssh_key": bool(row["ssh_public_key"]),
    }


def _throttle(conn, key: str, limit: int, window_seconds: int, increment: bool = True) -> None:
    now = datetime.now(timezone.utc)
    current = conn.execute("SELECT count, expires_at FROM throttle WHERE key=?", (key,)).fetchone()
    if current and datetime.fromisoformat(current["expires_at"]) > now and current["count"] >= limit:
        raise HTTPException(status_code=429, detail="Too many requests. Try again later.")
    if increment:
        expires = (now + timedelta(seconds=window_seconds)).isoformat()
        if current and datetime.fromisoformat(current["expires_at"]) > now:
            conn.execute("UPDATE throttle SET count=count+1 WHERE key=?", (key,))
        else:
            conn.execute("INSERT OR REPLACE INTO throttle(key,count,expires_at) VALUES(?,?,?)", (key, 1, expires))


def _registration_capacity(conn, increment: bool = False) -> None:
    key = "register-global-success"
    _throttle(conn, key, REGISTRATIONS_PER_HOUR, 3600, increment=increment)
    if not increment:
        return
    count = conn.execute("SELECT count FROM throttle WHERE key=?", (key,)).fetchone()["count"]
    warning_at = (REGISTRATIONS_PER_HOUR * 4 + 4) // 5
    if count == warning_at and count < REGISTRATIONS_PER_HOUR:
        audit(conn, None, "auth.register.rate_warning", f"{count}/{REGISTRATIONS_PER_HOUR}")
    elif count == REGISTRATIONS_PER_HOUR:
        audit(conn, None, "auth.register.rate_limited", f"{count}/{REGISTRATIONS_PER_HOUR}")


def _current_user(session_token: str | None = Cookie(default=None, alias=COOKIE_NAME)):
    if not session_token:
        raise HTTPException(status_code=401, detail="Login required")
    with db() as conn:
        row = conn.execute(
            """SELECT u.*, t.name AS team_name, t.slug AS team_slug, t.openstack_project_id,
                      t.network_id, t.cidr, s.csrf_token
               FROM sessions s JOIN users u ON u.id=s.user_id
               LEFT JOIN teams t ON t.id=u.team_id
               WHERE s.token_hash=? AND s.expires_at > CURRENT_TIMESTAMP""",
            (token_hash(session_token),),
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=401, detail="Session expired")
        return dict(row)


def _active(user=Depends(_current_user)):
    if user["status"] != "active":
        raise HTTPException(status_code=403, detail="Account is not ready")
    return user


def _admin(user=Depends(_active)):
    if user["role"] != "admin":
        raise HTTPException(status_code=403, detail="Administrator access required")
    return user


def _csrf(user=Depends(_current_user), x_csrf_token: str | None = Header(default=None)):
    if not x_csrf_token or not hmac.compare_digest(x_csrf_token, user["csrf_token"]):
        raise HTTPException(status_code=403, detail="Invalid CSRF token")
    return user


def _team(user: dict) -> tuple[str, str, str]:
    if not all((user.get("team_slug"), user.get("openstack_project_id"), user.get("network_id"))):
        raise HTTPException(status_code=409, detail="Team resources are not ready")
    return user["team_slug"], user["openstack_project_id"], user["network_id"]


def _backend_error(operation: str, exc: Exception):
    LOG.exception("OpenStack %s failed", operation, exc_info=exc)
    raise HTTPException(status_code=502, detail="클라우드 연결에 문제가 생겼습니다. 잠시 후 다시 시도해 주세요.")


@app.on_event("startup")
def startup():
    init_db()
    username = os.getenv("PORTAL_ADMIN_USERNAME", "admin")
    password = os.getenv("PORTAL_ADMIN_PASSWORD", "")
    email = os.getenv("PORTAL_ADMIN_EMAIL", "admin@example.invalid")
    with db() as conn:
        existing = conn.execute("SELECT id,password_hash FROM users WHERE username=?", (username,)).fetchone()
        if not existing:
            if not password:
                raise RuntimeError("PORTAL_ADMIN_PASSWORD must be set for initial startup")
            conn.execute(
                "INSERT INTO users(username,email,password_hash,role,status) VALUES(?,?,?,?,?)",
                (username, email, hash_password(password), "admin", "active"),
            )
        elif password and needs_password_rehash(existing["password_hash"]) and verify_password(password, existing["password_hash"]):
            conn.execute("UPDATE users SET password_hash=? WHERE id=?", (hash_password(password), existing["id"]))


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/guide/funnel")
def funnel_guide():
    return FileResponse(STATIC / "funnel-guide.html")


@app.get("/favicon.ico")
def favicon():
    return FileResponse(STATIC / "brand" / "favicon-v1.ico", media_type="image/vnd.microsoft.icon")


@app.get("/robots.txt")
def robots():
    return FileResponse(STATIC / "brand" / "robots.txt", media_type="text/plain")


@app.get("/sitemap.xml")
def sitemap():
    return FileResponse(STATIC / "brand" / "sitemap.xml", media_type="application/xml")


@app.get("/terminal")
def browser_terminal():
    return FileResponse(STATIC / "terminal.html")


@app.get("/reset-password")
def reset_password_page():
    return FileResponse(STATIC / "reset-password.html")


@app.post("/api/auth/find-id")
def find_id(body: RecoveryEmailBody, request: Request):
    remote = request.client.host if request.client else "unknown"
    with db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        _throttle(conn, f"find-id-ip:{remote}", 30, 600)
        _throttle(conn, f"find-id-email:{token_hash(body.email.lower())}", 10, 3600)
        row = conn.execute("SELECT username FROM users WHERE email=? COLLATE NOCASE", (body.email.lower(),)).fetchone()
    return {"username": row["username"] if row else None}


def _deliver_reset(user_id: int, email: str, token: str):
    if not recovery.send_reset(email, token):
        with db() as conn:
            conn.execute("DELETE FROM password_resets WHERE token_hash=?", (token_hash(token),))
            audit(conn, user_id, "auth.reset.mail_failed")
    else:
        with db() as conn:
            audit(conn, user_id, "auth.reset.mail_sent")


@app.post("/api/auth/forgot-password", status_code=202)
def forgot_password(body: RecoveryEmailBody, request: Request, background: BackgroundTasks):
    started = time.perf_counter()
    remote = request.client.host if request.client else "unknown"
    try:
        if not recovery.configured():
            raise HTTPException(status_code=503, detail="메일 발송을 준비 중입니다. 운영자에게 문의해 주세요.")
        email = body.email.lower()
        with db() as conn:
            conn.execute("BEGIN IMMEDIATE")
            _throttle(conn, f"reset-mail-ip:{remote}", 20, 3600)
            _throttle(conn, f"reset-mail-email:{token_hash(email)}", 3, 3600)
            _throttle(conn, "reset-mail-global", 60, 3600)
            row = conn.execute("SELECT id FROM users WHERE email=? COLLATE NOCASE", (email,)).fetchone()
            conn.execute("DELETE FROM password_resets WHERE expires_at<=?", (int(time.time()),))
            if row:
                token = new_token()
                conn.execute("INSERT INTO password_resets(token_hash,user_id,expires_at) VALUES(?,?,?)",
                             (token_hash(token), row["id"], int(time.time()) + 1800))
                background.add_task(_deliver_reset, row["id"], email, token)
        return {"message": "가입된 이메일이면 비밀번호 변경 주소를 보냈습니다. 메일함과 스팸함을 확인해 주세요."}
    finally:
        time.sleep(max(0, AUTH_MIN_SECONDS - (time.perf_counter() - started)))


@app.post("/api/auth/reset-password")
def reset_password(body: ResetPasswordBody, request: Request):
    started = time.perf_counter()
    remote = request.client.host if request.client else "unknown"
    try:
        with db() as conn:
            conn.execute("BEGIN IMMEDIATE")
            _throttle(conn, f"reset-use-ip:{remote}", 20, 900)
        replacement = hash_password(body.password)
        with db() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT user_id FROM password_resets WHERE token_hash=? AND expires_at>?",
                               (token_hash(body.token), int(time.time()))).fetchone()
            if not row:
                raise HTTPException(status_code=400, detail="주소가 만료됐거나 이미 사용됐습니다. 비밀번호 찾기를 다시 눌러 주세요.")
            conn.execute("UPDATE users SET password_hash=? WHERE id=?", (replacement, row["user_id"]))
            conn.execute("DELETE FROM sessions WHERE user_id=?", (row["user_id"],))
            conn.execute("UPDATE agent_grants SET revoked=1,private_key='' WHERE user_id=?", (row["user_id"],))
            conn.execute("DELETE FROM password_resets WHERE user_id=?", (row["user_id"],))
            audit(conn, row["user_id"], "auth.reset.completed")
        return {"message": "비밀번호를 바꿨습니다. 새 비밀번호로 로그인하세요."}
    finally:
        time.sleep(max(0, AUTH_MIN_SECONDS - (time.perf_counter() - started)))


@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.post("/api/auth/register", status_code=201)
def register(body: RegisterBody, request: Request):
    remote = request.client.host if request.client else "unknown"
    team_name = " ".join(body.team_name.split())
    if len(team_name) < 2:
        raise HTTPException(status_code=422, detail="팀 이름을 확인하세요.")
    with db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        _throttle(conn, f"register-ip:{remote}", 60, 3600)
        _throttle(conn, f"register-name:{remote}:{body.username.lower()}", 8, 3600)
        if REGISTRATION_CODE and not hmac.compare_digest(
            (body.registration_code or "").strip().upper(), REGISTRATION_CODE
        ):
            _throttle(conn, f"register-code-failure:{remote}", 30, 3600)
            conn.commit()
            raise HTTPException(status_code=403, detail="가입 코드를 확인하세요.")
        # Show the team-join confirmation before doing the expensive hash.
        team_exists = conn.execute("SELECT 1 FROM teams WHERE name=? COLLATE NOCASE", (team_name,)).fetchone()
        if team_exists and not body.join_existing:
            conn.commit()
            raise HTTPException(status_code=409, detail={"code": "team_exists", "message": "이미 있는 팀입니다."})
        if not team_exists and body.join_existing:
            conn.commit()
            raise HTTPException(status_code=409, detail="팀을 찾을 수 없습니다. 다시 확인하세요.")
        _registration_capacity(conn)
    # Bcrypt is intentionally outside the SQLite write transaction so other
    # students can log in while a registration password is being hashed.
    password_hash = hash_password(body.password)
    with db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        team = conn.execute("SELECT * FROM teams WHERE name=? COLLATE NOCASE", (team_name,)).fetchone()
        if team and not body.join_existing:
            conn.commit()
            raise HTTPException(status_code=409, detail={"code": "team_exists", "message": "이미 있는 팀입니다."})
        if not team and body.join_existing:
            conn.commit()
            raise HTTPException(status_code=409, detail="팀을 찾을 수 없습니다. 다시 확인하세요.")
        if not team and conn.execute("SELECT count(*) FROM teams").fetchone()[0] >= MAX_TEAMS:
            conn.commit()
            raise HTTPException(status_code=503, detail="새 작업 공간을 만들 수 없습니다. 운영자에게 문의하세요.")
        _registration_capacity(conn)
        try:
            cursor = conn.execute(
                "INSERT INTO users(username,email,password_hash,requested_team,team_id,team_owner) VALUES(?,?,?,?,?,?)",
                (body.username.strip(), body.email.lower(), password_hash, team_name,
                 team["id"] if team else None, 0 if team else 1),
            )
        except sqlite3.IntegrityError:
            conn.commit()
            raise HTTPException(status_code=409, detail="Username or email already exists")
        _registration_capacity(conn, increment=True)
        audit(conn, cursor.lastrowid, "user.register", body.username)
        user_id = cursor.lastrowid
        if team:
            audit(conn, user_id, "team.join.request", str(team["id"]), json.dumps({"team": team_name}))
        else:
            team_cursor = conn.execute("INSERT INTO teams(name,slug) VALUES(?,?)", (team_name, f"user-{user_id}"))
            team_id = team_cursor.lastrowid
            conn.execute("UPDATE users SET team_id=? WHERE id=?", (team_id, user_id))
            audit(conn, user_id, "team.create.request", str(team_id), json.dumps({"team": team_name}))
    if team:
        return {"ready": False, "message": "팀장에게 가입 요청을 보냈습니다."}
    try:
        details = ensure_team_project(team_name, f"user-{user_id}", team_id)
    except Exception as exc:
        LOG.exception("Self-service team provisioning failed", exc_info=exc)
        with db() as conn:
            audit(conn, user_id, "team.provision.failed", str(team_id))
        return {"ready": False, "message": "계정은 생성됐지만 작업 공간 준비가 지연되고 있습니다. 로그인 후 다시 시도해 주세요."}
    with db() as conn:
        conn.execute(
            "UPDATE teams SET openstack_project_id=?,network_id=?,subnet_id=?,router_id=?,cidr=? WHERE id=?",
            (details["project_id"], details["network_id"], details["subnet_id"],
             details["router_id"], details["cidr"], team_id),
        )
        conn.execute("UPDATE users SET status='active' WHERE id=?", (user_id,))
        audit(conn, user_id, "team.provision.ready", str(team_id))
    return {"ready": True, "message": "계정과 작업 공간이 준비됐습니다."}


@app.post("/api/auth/login")
def login(body: LoginBody, response: Response, request: Request):
    started = time.perf_counter()
    remote = request.client.host if request.client else "unknown"
    identifier = body.username.strip()
    key = f"login:{remote}:{identifier.lower()}"
    ip_key = f"login-ip:{remote}"
    try:
        # Reserve attempts before bcrypt so parallel guesses cannot bypass the limits.
        with db() as conn:
            conn.execute("BEGIN IMMEDIATE")
            _throttle(conn, ip_key, LOGIN_IP_ATTEMPTS_PER_MINUTE, 60)
            try:
                _throttle(conn, key, 6, 900)
            except HTTPException:
                conn.commit()  # A blocked identifier still counts toward this IP's budget.
                raise
        with db() as conn:
            user = conn.execute(
                "SELECT * FROM users WHERE username=? OR email=?", (identifier, identifier)
            ).fetchone()
            valid = verify_password(body.password, user["password_hash"] if user else DUMMY_HASH)
            if not user or not valid:
                audit(conn, user["id"] if user else None, "auth.failure", body.username[:32])
                conn.commit()
                raise HTTPException(status_code=401, detail="Invalid username or password")
            replacement_hash = hash_password(body.password) if needs_password_rehash(user["password_hash"]) else None
            conn.execute("BEGIN IMMEDIATE")
            current = conn.execute("SELECT password_hash FROM users WHERE id=?", (user["id"],)).fetchone()
            if not current or current["password_hash"] != user["password_hash"]:
                raise HTTPException(status_code=401, detail="Invalid username or password")
            if replacement_hash:
                conn.execute("UPDATE users SET password_hash=? WHERE id=?", (replacement_hash, user["id"]))
            conn.execute("DELETE FROM throttle WHERE key=?", (key,))
            token, csrf = new_token(), new_token()
            expires = datetime.now(timezone.utc) + timedelta(hours=SESSION_HOURS)
            conn.execute("DELETE FROM sessions WHERE expires_at <= CURRENT_TIMESTAMP")
            conn.execute(
                "INSERT INTO sessions(token_hash,user_id,csrf_token,expires_at) VALUES(?,?,?,?)",
                (token_hash(token), user["id"], csrf, expires.strftime("%Y-%m-%d %H:%M:%S")),
            )
            audit(conn, user["id"], "auth.login", user["username"])
    finally:
        # Apply the same minimum response time to valid and invalid credentials.
        time.sleep(max(0.0, AUTH_MIN_SECONDS - (time.perf_counter() - started)))
    response.set_cookie(COOKIE_NAME, token, max_age=SESSION_HOURS * 3600,
                        httponly=True, secure=COOKIE_SECURE, samesite="lax", path="/")
    return {"csrf_token": csrf}


@app.post("/api/auth/logout")
def logout(response: Response, session_token: str | None = Cookie(default=None, alias=COOKIE_NAME), user=Depends(_csrf)):
    if session_token:
        with db() as conn:
            conn.execute("DELETE FROM sessions WHERE token_hash=?", (token_hash(session_token),))
            audit(conn, user["id"], "auth.logout", user["username"])
    response.delete_cookie(COOKIE_NAME, path="/")
    return {"ok": True}


@app.get("/api/me")
def me(user=Depends(_current_user)):
    result = _public_user(user)
    result.update(csrf_token=user["csrf_token"], team_cidr=user.get("cidr"))
    return result


@app.post("/api/profile/password")
def change_password(body: ChangePasswordBody, request: Request, user=Depends(_csrf)):
    started = time.perf_counter()
    throttle_key = f"password-change:{user['id']}"
    try:
        with db() as conn:
            conn.execute("BEGIN IMMEDIATE")
            _throttle(conn, throttle_key, 8, 900)
        if not verify_password(body.current_password, user["password_hash"]):
            with db() as conn:
                audit(conn, user["id"], "profile.password.failure", user["username"])
            raise HTTPException(status_code=401, detail="Invalid current password")
        if body.current_password == body.new_password:
            raise HTTPException(status_code=422, detail="새 비밀번호를 입력해 주세요.")
        replacement_hash = hash_password(body.new_password)
        with db() as conn:
            conn.execute("BEGIN IMMEDIATE")
            updated = conn.execute(
                "UPDATE users SET password_hash=? WHERE id=? AND password_hash=?",
                (replacement_hash, user["id"], user["password_hash"]),
            )
            if updated.rowcount != 1:
                raise HTTPException(status_code=409, detail="비밀번호가 바뀌었습니다. 다시 로그인해 주세요.")
            conn.execute("DELETE FROM sessions WHERE user_id=? AND token_hash<>?",
                         (user["id"], token_hash(request.cookies[COOKIE_NAME])))
            conn.execute("DELETE FROM throttle WHERE key=?", (throttle_key,))
            conn.execute("UPDATE agent_grants SET revoked=1,private_key='' WHERE user_id=?", (user["id"],))
            audit(conn, user["id"], "profile.password.update", user["username"])
        return {"ok": True}
    finally:
        time.sleep(max(0.0, AUTH_MIN_SECONDS - (time.perf_counter() - started)))


@app.put("/api/profile/ssh-key")
def save_ssh_key(body: SSHKeyBody, user=Depends(_csrf)):
    import base64
    parts = body.public_key.strip().split()
    if len(parts) not in (2, 3) or parts[0] != "ssh-ed25519":
        raise HTTPException(status_code=422, detail="Only ssh-ed25519 public keys are accepted")
    try:
        raw = base64.b64decode(parts[1], validate=True)
        if len(raw) < 40 or len(raw) > 100:
            raise ValueError()
    except (ValueError, base64.binascii.Error):
        raise HTTPException(status_code=422, detail="Invalid SSH public key")
    with db() as conn:
        conn.execute("UPDATE users SET ssh_public_key=? WHERE id=?", (body.public_key.strip(), user["id"]))
        audit(conn, user["id"], "profile.ssh_key.update", user["username"])
    return {"ok": True}


@app.get("/api/catalog")
def catalog(user=Depends(_active)):
    slug, _, _ = _team(user)
    try:
        return list_catalog(slug)
    except Exception as exc:
        _backend_error("catalog", exc)


@app.get("/api/instances")
def instances(user=Depends(_active)):
    slug, project_id, _ = _team(user)
    try:
        instances = list_servers(slug, project_id)
        for instance in instances:
            instance['software_status'] = software_completed(user['team_id'], instance['id'])
        return {"instances": instances}
    except Exception as exc:
        _backend_error("list instances", exc)


@app.post("/api/instances", status_code=202)
def instance_create(body: InstanceBody, user=Depends(_csrf)):
    if user["status"] != "active":
        raise HTTPException(status_code=403, detail="Account is not active")
    slug, project_id, network_id = _team(user)
    try:
        server = create_server(slug, project_id, user["username"], network_id,
                               body.name, str(body.image_id), body.flavor_id,
                               body.vm_username, body.vm_password, user["ssh_public_key"],
                               str(body.request_id) if body.request_id else None,
                               [item.model_dump() for item in body.software])
        with db() as conn:
            audit(conn, user["id"], "instance.create", server["id"], json.dumps({"name": body.name, "software": public_software([item.model_dump() for item in body.software])}))
        return server
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except InstanceQuotaError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    except Exception as exc:
        _backend_error("create instance", exc)


@app.post("/api/instances/{server_id}/console")
def instance_console(server_id: UUID, user=Depends(_csrf)):
    if user["status"] != "active":
        raise HTTPException(status_code=403, detail="Account is not active")
    slug, project_id, _ = _team(user)
    try:
        upstream = urlsplit(get_console_url(slug, project_id, str(server_id)))
        if upstream.scheme not in {"http", "https"} or upstream.hostname not in CONSOLE_HOSTS or upstream.port != 6080:
            raise RuntimeError("Unexpected console URL")
        browser = urlsplit(CONSOLE_BROWSER_ORIGIN)
        if browser.scheme not in {"http", "https"} or not browser.netloc:
            raise RuntimeError("Invalid browser console origin")
        path = CONSOLE_UI_PATH or upstream.path
        if CONSOLE_UI_PATH and CONSOLE_UI_PATH != "/ucconsole/console.html":
            raise RuntimeError("Invalid console UI path")
        url = urlunsplit((browser.scheme, browser.netloc, path, upstream.query, ""))
        with db() as conn:
            audit(conn, user["id"], "instance.console", str(server_id))
        return {"url": url}
    except ValueError:
        raise HTTPException(status_code=404, detail="Instance not found or not running")
    except Exception as exc:
        _backend_error("instance console", exc)


@app.post("/api/instances/{server_id}/action", status_code=202)
def instance_action(server_id: UUID, body: ActionBody, user=Depends(_csrf)):
    if user["status"] != "active":
        raise HTTPException(status_code=403, detail="Account is not active")
    slug, project_id, _ = _team(user)
    try:
        if body.action in {"start", "reboot"} and backup_busy(user["team_id"], str(server_id)):
            raise BackupConflict("백업 또는 복구가 끝난 뒤 VM을 켤 수 있습니다.")
        server_action(slug, project_id, str(server_id), body.action)
        with db() as conn:
            audit(conn, user["id"], f"instance.{body.action}", str(server_id))
        return {"ok": True}
    except (InstanceStateError, BackupConflict) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    except ValueError:
        raise HTTPException(status_code=404, detail="Instance not found")
    except Exception as exc:
        _backend_error("instance action", exc)


@app.delete("/api/instances/{server_id}", status_code=202)
def instance_delete(server_id: UUID, user=Depends(_csrf)):
    if user["status"] != "active":
        raise HTTPException(status_code=403, detail="Account is not active")
    slug, project_id, _ = _team(user)
    try:
        if backup_busy(user["team_id"], str(server_id)):
            raise BackupConflict("백업 또는 복구가 끝난 뒤 VM을 삭제할 수 있습니다.")
        if has_backups(user["team_id"], str(server_id)):
            raise BackupConflict("VM을 삭제하려면 백업·복구에서 백업을 먼저 삭제해 주세요.")
        delete_server(slug, project_id, str(server_id))
        forget_software(user['team_id'], str(server_id))
        with db() as conn:
            conn.execute("DELETE FROM vm_sites WHERE team_id=? AND server_id=?", (user["team_id"], str(server_id)))
            conn.execute("UPDATE agent_grants SET revoked=1,private_key='' WHERE team_id=? AND server_id=?", (user["team_id"], str(server_id)))
            audit(conn, user["id"], "instance.delete", str(server_id))
        return {"ok": True}
    except BackupConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    except ValueError:
        raise HTTPException(status_code=404, detail="Instance not found")
    except Exception as exc:
        _backend_error("delete instance", exc)


def _owned_site_vm(user: dict, server_id: UUID) -> None:
    slug, project_id, _ = _team(user)
    try:
        _owned_server(team_connection(slug), str(server_id), project_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Instance not found") from None
    except Exception as exc:
        _backend_error("site VM ownership", exc)


@app.get("/api/instances/{server_id}/site")
def vm_site(server_id: UUID, user=Depends(_active)):
    _owned_site_vm(user, server_id)
    with db() as conn:
        row = conn.execute("SELECT url,state,http_status,message,checked_at FROM vm_sites WHERE team_id=? AND server_id=?",
                           (user["team_id"], str(server_id))).fetchone()
    return {"site": dict(row) if row else None}


@app.post("/api/instances/{server_id}/site/check")
def vm_site_check(server_id: UUID, body: SiteCheckBody, user=Depends(_csrf)):
    if user["status"] != "active":
        raise HTTPException(status_code=403, detail="Account is not active")
    try:
        url, _ = normalize_site_url(body.url)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    _owned_site_vm(user, server_id)
    with db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        _throttle(conn, f"site-check:{user['team_id']}", 30, 600)
    result = check_public_site(url)
    with db() as conn:
        conn.execute(
            "INSERT INTO vm_sites(team_id,server_id,url,state,http_status,message) VALUES(?,?,?,?,?,?) "
            "ON CONFLICT(team_id,server_id) DO UPDATE SET url=excluded.url,state=excluded.state,"
            "http_status=excluded.http_status,message=excluded.message,checked_at=CURRENT_TIMESTAMP",
            (user["team_id"], str(server_id), result["url"], result["state"], result["http_status"], result["message"]),
        )
        audit(conn, user["id"], "site.check", str(server_id), result["state"])
    return result


@app.get("/api/backups")
def vm_backups_list(user=Depends(_active)):
    _team(user)
    return {"enabled": VM_BACKUPS_ENABLED, "backups": list_team_backups(user["team_id"]) if VM_BACKUPS_ENABLED else []}


def _require_vm_backups() -> None:
    if not VM_BACKUPS_ENABLED:
        raise HTTPException(status_code=503, detail="백업 기능을 점검 중입니다. 잠시 후 다시 확인해 주세요.")


@app.post("/api/instances/{server_id}/backups", status_code=202)
def vm_backup_create(server_id: UUID, user=Depends(_csrf)):
    _require_vm_backups()
    if user["status"] != "active":
        raise HTTPException(status_code=403, detail="Account is not active")
    slug, project_id, _ = _team(user)
    try:
        return queue_backup(user["team_id"], user["id"], slug, project_id, str(server_id))
    except BackupConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    except ValueError:
        raise HTTPException(status_code=404, detail="Instance not found") from None
    except Exception as exc:
        _backend_error("queue VM backup", exc)


@app.post("/api/instances/{server_id}/backups/{backup_id}/restore", status_code=202)
def vm_backup_restore(server_id: UUID, backup_id: UUID, user=Depends(_csrf)):
    _require_vm_backups()
    if user["status"] != "active":
        raise HTTPException(status_code=403, detail="Account is not active")
    slug, project_id, _ = _team(user)
    try:
        result = queue_restore(user["team_id"], user["id"], slug, project_id, str(server_id), str(backup_id))
        forget_software(user['team_id'], str(server_id))
        with db() as conn:
            conn.execute("UPDATE agent_grants SET revoked=1,private_key='' WHERE team_id=? AND server_id=?", (user["team_id"], str(server_id)))
        return result
    except BackupConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    except ValueError:
        raise HTTPException(status_code=404, detail="백업 또는 VM을 찾을 수 없습니다.") from None
    except Exception as exc:
        _backend_error("queue VM restore", exc)


@app.delete("/api/instances/{server_id}/backups/{backup_id}", status_code=202)
def vm_backup_delete(server_id: UUID, backup_id: UUID, user=Depends(_csrf)):
    _require_vm_backups()
    if user["status"] != "active":
        raise HTTPException(status_code=403, detail="Account is not active")
    _, project_id, _ = _team(user)
    try:
        queue_delete_backup(user["team_id"], user["id"], project_id, str(server_id), str(backup_id))
        return {"ok": True}
    except BackupConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    except ValueError:
        raise HTTPException(status_code=404, detail="백업을 찾을 수 없습니다.") from None


@app.get("/api/quota")
def team_quota(user=Depends(_active)):
    _, project_id, _ = _team(user)
    try:
        return quota(project_id)
    except Exception as exc:
        _backend_error("quota", exc)


@app.get("/api/admin/users")
def admin_users(user=Depends(_admin)):
    with db() as conn:
        rows = conn.execute(
            """SELECT u.*, t.name AS team_name, t.slug AS team_slug
               FROM users u LEFT JOIN teams t ON t.id=u.team_id ORDER BY u.created_at DESC"""
        ).fetchall()
    return {"users": [_public_user(row) for row in rows]}


@app.get("/api/admin/events")
def admin_events(user=Depends(_admin)):
    with db() as conn:
        rows = conn.execute(
            """SELECT a.id,a.action,a.target,a.created_at,u.username
               FROM audit_log a LEFT JOIN users u ON u.id=a.user_id
               WHERE a.action NOT IN ('auth.login','auth.failure')
               ORDER BY a.id DESC LIMIT 100"""
        ).fetchall()
    return {"events": [dict(row) for row in rows]}


@app.get("/api/admin/capacity")
def admin_capacity(user=Depends(_admin)):
    try:
        return cluster_capacity()
    except Exception as exc:
        _backend_error("cluster capacity", exc)


class FanMinimumBody(BaseModel):
    model_config = {"extra": "forbid"}
    minimum: int = Field(strict=True, ge=0, le=100)
    expected: int = Field(strict=True, ge=0, le=100)


@app.get("/api/admin/fans")
def admin_fans(user=Depends(_admin)):
    try:
        return hardware.request({"op": "status"})
    except hardware.HardwareError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from None


@app.post("/api/admin/fans/{node_id}/minimum")
def admin_fan_minimum(node_id: str, body: FanMinimumBody, user=Depends(_admin), csrf=Depends(_csrf)):
    if node_id not in {"controller", "compute-1", "compute-2", "compute-3", "compute-4"}:
        raise HTTPException(status_code=404, detail="서버를 찾지 못했습니다.")
    detail = json.dumps({"minimum": body.minimum, "previous": body.expected})
    with db() as conn:
        recent = conn.execute("SELECT COUNT(*) FROM audit_log WHERE user_id=? AND action='hardware.fan.request' AND created_at >= datetime('now','-1 minute')", (user["id"],)).fetchone()[0]
        if recent >= 15:
            raise HTTPException(status_code=429, detail="설정 요청이 많습니다. 1분 뒤 다시 시도해 주세요.")
        audit(conn, user["id"], "hardware.fan.request", node_id, detail)
    try:
        result = hardware.request({"op": "set", "node": node_id, "minimum": body.minimum, "expected": body.expected})
    except hardware.HardwareError as exc:
        with db() as conn:
            audit(conn, user["id"], "hardware.fan.failed", node_id, exc.code)
        raise HTTPException(status_code=409 if exc.code in {"conflict", "warning", "busy"} else 503, detail=str(exc)) from None
    with db() as conn:
        audit(conn, user["id"], "hardware.fan.updated", node_id, detail)
    return result


@app.get("/api/admin/registration-code")
def admin_registration_code(response: Response, user=Depends(_admin)):
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    code = REGISTRATION_CODE
    credentials = os.getenv("CREDENTIALS_DIRECTORY", "")
    if credentials:
        try:
            code = (Path(credentials) / "registration-code").read_text(encoding="utf-8").strip().upper()
        except OSError:
            LOG.exception("Could not read registration code credential")
            code = ""
    if code and not re.fullmatch(r"[A-Z0-9-]{8,64}", code):
        LOG.warning("Invalid registration code credential")
        code = ""
    return {"registration_code": code}


@app.post("/api/team/provision")
def retry_team_provision(user=Depends(_csrf)):
    if user["status"] == "active":
        return {"ready": True}
    if user["status"] != "pending" or not user["team_owner"] or not user["team_id"]:
        raise HTTPException(status_code=403, detail="Not a team owner awaiting setup")
    with db() as conn:
        team = conn.execute("SELECT * FROM teams WHERE id=?", (user["team_id"],)).fetchone()
        if not team:
            raise HTTPException(status_code=404, detail="Team not found")
    try:
        details = ensure_team_project(team["name"], team["slug"], team["id"])
    except Exception as exc:
        _backend_error("team provisioning", exc)
    with db() as conn:
        conn.execute(
            "UPDATE teams SET openstack_project_id=?,network_id=?,subnet_id=?,router_id=?,cidr=? WHERE id=?",
            (details["project_id"], details["network_id"], details["subnet_id"],
             details["router_id"], details["cidr"], team["id"]),
        )
        conn.execute("UPDATE users SET status='active' WHERE id=? AND status='pending'", (user["id"],))
        audit(conn, user["id"], "team.provision.ready", str(team["id"]))
    return {"ready": True}


@app.get("/api/team/requests")
def team_requests(user=Depends(_active)):
    if not user["team_owner"] or not user["team_id"]:
        raise HTTPException(status_code=403, detail="Team owner access required")
    with db() as conn:
        rows = conn.execute(
            "SELECT id,username,email,created_at FROM users WHERE team_id=? AND status='pending' AND team_owner=0 ORDER BY id",
            (user["team_id"],),
        ).fetchall()
    return {"requests": [dict(row) for row in rows]}


@app.post("/api/team/requests/{user_id}/{decision}")
def decide_team_request(user_id: int, decision: str, user=Depends(_csrf)):
    if user["status"] != "active" or not user["team_owner"] or not user["team_id"]:
        raise HTTPException(status_code=403, detail="Team owner access required")
    if decision not in {"approve", "reject"}:
        raise HTTPException(status_code=404, detail="Unknown action")
    status = "active" if decision == "approve" else "rejected"
    with db() as conn:
        cursor = conn.execute(
            "UPDATE users SET status=? WHERE id=? AND team_id=? AND status='pending' AND team_owner=0",
            (status, user_id, user["team_id"]),
        )
        if cursor.rowcount != 1:
            raise HTTPException(status_code=409, detail="Request is not pending")
        audit(conn, user["id"], f"team.join.{decision}", str(user_id))
    return {"ok": True}


from .terminal import terminal as browser_ssh_terminal  # noqa: E402

# FastAPI's deferred include_router leaves WebSocket routes unregistered here.
# Register this endpoint directly so the public ASGI app can handle upgrades.
app.websocket("/api/instances/{server_id}/terminal")(browser_ssh_terminal)

from .agent_access import register as register_agent_access  # noqa: E402
register_agent_access(app, _active, _csrf)

