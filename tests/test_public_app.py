"""Isolated checks for the internet-facing portal process."""

import os
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient


temporary = tempfile.TemporaryDirectory(prefix="portal-public-test-")
os.environ["PORTAL_DB"] = str(Path(temporary.name) / "portal.db")
os.environ["PORTAL_ADMIN_PASSWORD"] = "Public-test-admin-passphrase-2026!"
os.environ["PORTAL_ALLOWED_HOSTS"] = "cloud.example.ts.net"
os.environ["PORTAL_COOKIE_SECURE"] = "true"
os.environ["PORTAL_REGISTRATION_CODE"] = "2026UCCS-KKS-1234"
os.environ["PORTAL_CONSOLE_UI_PATH"] = "/ucconsole/console.html"
os.environ["PORTAL_CONSOLE_BROWSER_ORIGIN"] = "https://cloud.example.ts.net:8443"
os.environ["PORTAL_BROWSER_SSH_ORIGIN"] = "https://cloud.example.ts.net"

from backend.public_app import app  # noqa: E402
from backend import app as portal  # noqa: E402
from backend import terminal as ssh_terminal  # noqa: E402
from fastapi.routing import APIWebSocketRoute  # noqa: E402
from starlette.websockets import WebSocketDisconnect  # noqa: E402
from starlette.testclient import WebSocketDenialResponse  # noqa: E402


portal.ensure_team_project = lambda name, slug, index: {
    "project_id": f"project-{index}", "network_id": f"network-{index}",
    "subnet_id": f"subnet-{index}", "router_id": "router-1", "cidr": "10.30.21.0/24",
}
portal.get_console_url = lambda *args: "http://172.141.0.231:6080/vnc_lite.html?path=%3Ftoken%3Dqa"
assert any(isinstance(route, APIWebSocketRoute) and route.path == "/api/instances/{server_id}/terminal"
           for route in app.router.routes), "Browser SSH WebSocket route is missing"


with TestClient(app, base_url="https://cloud.example.ts.net") as client:
    assert client.get("/api/health").status_code == 200
    home = client.get("/")
    assert home.status_code == 200 and "UCcloud" in home.text
    assert home.headers["cache-control"] == "no-store"
    assert '<meta property="og:title" content="UCcloud">' in home.text
    assert '<meta property="og:description" content="팀 서버를 만들고 사용하세요.">' in home.text
    assert 'content="https://comsv-03.tail5819db.ts.net/assets/brand/share-v1.png"' in home.text
    assert '<meta name="twitter:card" content="summary_large_image">' in home.text
    assert "manifest-src 'self'" in home.headers["content-security-policy"]
    preview = client.get("/assets/brand/share-v1.png")
    assert preview.status_code == 200 and preview.headers["content-type"] == "image/png"
    assert preview.content[:8] == b"\x89PNG\r\n\x1a\n"
    assert int.from_bytes(preview.content[16:20], "big") == 1200
    assert int.from_bytes(preview.content[20:24], "big") == 630
    for size in (180, 192, 512):
        icon = client.get(f"/assets/brand/icon-{size}-v1.png")
        assert icon.status_code == 200 and icon.headers["content-type"] == "image/png"
        assert int.from_bytes(icon.content[16:20], "big") == size
        assert int.from_bytes(icon.content[20:24], "big") == size
    favicon = client.get("/favicon.ico")
    assert favicon.status_code == 200 and favicon.content[:6] == b"\x00\x00\x01\x00\x03\x00"
    assert client.get("/assets/brand/icon.svg").headers["content-type"].startswith("image/svg+xml")
    manifest = client.get("/assets/brand/site.webmanifest")
    assert manifest.status_code == 200 and manifest.json()["name"] == "UCcloud"
    assert len(manifest.json()["icons"]) == 2
    robots = client.get("/robots.txt")
    assert robots.status_code == 200 and "Disallow: /api/" in robots.text
    sitemap = client.get("/sitemap.xml")
    assert sitemap.status_code == 200 and "https://comsv-03.tail5819db.ts.net/guide/funnel" in sitemap.text
    reset = client.get("/reset-password")
    assert reset.headers["x-robots-tag"] == "noindex, nofollow"
    assert 'content="noindex, nofollow"' in reset.text
    assert "og:" not in reset.text
    assert client.get("/assets/index.html").headers["x-robots-tag"] == "noindex, nofollow"
    guide = client.get("/guide/funnel")
    assert guide.status_code == 200 and 'id="funnel-command"' in guide.text
    assert guide.headers["cache-control"] == "no-store"
    assert "UCcloud" in guide.text and "[서비스 가제]" not in guide.text
    assert "sudo tailscale up" in guide.text
    assert "Tailscale 로그인</b>을 누르세요" in guide.text
    assert "https://…ts.net" in guide.text
    assert "휴대폰으로 로그인" in guide.text
    assert "서비스에서 사용하는 포트" in guide.text
    assert 'id="keep-running-command"' in guide.text
    assert "접속 창을 닫거나 VM을 다시 켜도 사이트가 자동으로 시작됩니다" in guide.text
    assert "비밀번호는 넣지 마세요" in guide.text
    console_page = client.get("/assets/console.html")
    assert console_page.status_code == 200
    assert "connect-src 'self' wss://cloud.example.ts.net:8443" in console_page.headers["content-security-policy"]
    assert portal.CONSOLE_UI_PATH == "/ucconsole/console.html"
    terminal_page = client.get("/terminal")
    assert terminal_page.status_code == 200
    assert terminal_page.headers["x-robots-tag"] == "noindex, nofollow"
    assert terminal_page.headers["cache-control"] == "no-store"
    assert "VM 접속" in terminal_page.text
    assert 'href="/guide/funnel" target="_blank"' in terminal_page.text
    assert "addon-web-links-0.12.0.js" in terminal_page.text
    assert "wss://cloud.example.ts.net" in terminal_page.headers["content-security-policy"]
    assert client.get("/api/admin/events").status_code == 404
    assert client.get("/api/admin/capacity").status_code == 404
    assert client.get("/api/admin/registration-code").status_code == 404
    assert client.get("/api/admin/users").status_code == 404
    assert client.get("/api/admin/fans").status_code == 404
    assert client.post("/api/admin/fans/compute-1/minimum", json={"minimum":20,"expected":0}).status_code == 404
    assert client.get("/api/public-config").json() == {
        "registration_code_required": True, "operator_enabled": False,
    }
    max_body = client.post("/api/auth/login", content=iter([b"{}", b" " * (16384 - 2)]),
                           headers={"content-type": "application/json"})
    assert max_body.status_code == 422
    no_length = client.post("/api/auth/login", content=iter([b"{}", b" " * 9000, b" " * 9000]),
                            headers={"content-type": "application/json"})
    assert no_length.status_code == 413
    assert no_length.headers["cache-control"] == "no-store"
    false_length = client.post("/api/auth/login", content=iter([b"{}", b" " * 9000, b" " * 9000]),
                               headers={"content-type": "application/json", "content-length": "2"})
    assert false_length.status_code == 413
    declared_length = client.post("/api/auth/login", content=b"{}",
                                  headers={"content-type": "application/json", "content-length": "16385"})
    assert declared_length.status_code == 413
    assert client.post("/api/auth/register", json={
        "username": "teststudent", "email": "student@example.com",
        "team_name": "Example Team", "password": "student-passphrase-2026",
        "registration_code": "wrong-code",
    }).status_code == 403
    assert client.post("/api/auth/register", json={
        "username": "teststudent", "email": "student@example.com",
        "team_name": "Example Team", "password": "student-passphrase-2026",
        "registration_code": "2026uccs-kks-1234",
    }).status_code == 201
    student_login = client.post("/api/auth/login", json={
        "username": "STUDENT@example.com", "password": "student-passphrase-2026",
    })
    assert student_login.status_code == 200
    console = client.post("/api/instances/d58be315-054e-49fb-bb25-14a5544adf39/console",
                          headers={"X-CSRF-Token": student_login.json()["csrf_token"]})
    assert console.status_code == 200
    assert console.json()["url"] == "https://cloud.example.ts.net:8443/ucconsole/console.html?path=%3Ftoken%3Dqa"
    terminal_path = "/api/instances/d58be315-054e-49fb-bb25-14a5544adf39/terminal"
    terminal_cookie = f"school_cloud_session={client.cookies.get('school_cloud_session')}"
    try:
        with client.websocket_connect(terminal_path, subprotocols=["uccloud", "csrf." + student_login.json()["csrf_token"]],
                                      headers={"origin": "https://evil.example", "host": "cloud.example.ts.net", "cookie": terminal_cookie}):
            raise AssertionError("Cross-origin terminal connection was accepted")
    except WebSocketDenialResponse as exc:
        assert exc.status_code == 403, exc.status_code
    except WebSocketDisconnect as exc:
        assert exc.code == 1008
    ssh_terminal._ssh_target = lambda user, server_id: "10.30.21.99"

    async def no_route(*args, **kwargs):
        raise OSError("test network failure")

    ssh_terminal.asyncssh.connect = no_route
    with client.websocket_connect(terminal_path, subprotocols=["uccloud", "csrf." + student_login.json()["csrf_token"]],
                                  headers={"origin": "https://cloud.example.ts.net", "host": "cloud.example.ts.net", "cookie": terminal_cookie}) as websocket:
        websocket.send_json({"username": "student", "password": "not-stored-here", "cols": 80, "rows": 24})
        assert websocket.receive_json() == {
            "type": "error", "message": "VM에 아직 연결할 수 없습니다. 방금 만들었다면 1~2분 기다린 뒤 다시 시도해 주세요."
        }
    assert client.get("/api/health", headers={"host": "invalid.example"}).status_code == 400
    response = client.post("/api/auth/login", json={
        "username": "admin", "password": "Public-test-admin-passphrase-2026!",
    })
    assert response.status_code == 200, response.text
    assert "Secure" in response.headers["set-cookie"]
    assert response.headers["strict-transport-security"] == "max-age=31536000"

temporary.cleanup()
print("Public portal checks passed")
