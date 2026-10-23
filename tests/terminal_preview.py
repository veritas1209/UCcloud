"""Local-only visual preview of the portal and browser SSH screens."""

import json
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

STATIC = Path(__file__).resolve().parent.parent / "static"
VM_ID = "d58be315-054e-49fb-bb25-14a5544adf39"


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(STATIC), **kwargs)

    def do_GET(self):
        path = urlsplit(self.path).path
        if path == "/api/me":
            return self.json({"csrf_token": "preview", "status": "active", "team_slug": "demo",
                              "team_name": "우리 팀", "username": "student", "role": "user",
                              "team_owner": False})
        if path == "/api/public-config":
            return self.json({"operator_enabled": False, "registration_code_required": True})
        if path == "/api/quota":
            return self.json({"instances": {"limit": 4, "in_use": 2},
                              "cores": {"limit": 8, "in_use": 3},
                              "ram": {"limit": 16384, "in_use": 6144}})
        if path == "/api/instances":
            return self.json({"instances": [
                {"id": VM_ID, "name": "my-server", "status": "ACTIVE", "vm_username": "student",
                 "addresses": [{"address": "10.30.23.177", "version": 4}], "flavor": "small"},
                {"id": "efc7e990-93ae-4169-9a2e-6f0d371bccb4", "name": "web-test", "status": "ACTIVE",
                 "addresses": [], "flavor": "medium"},
            ]})
        self.path = "/terminal.html" if path == "/terminal" else path.removeprefix("/assets/")
        return super().do_GET()

    def json(self, payload):
        data = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


if __name__ == "__main__":
    server = ThreadingHTTPServer(("127.0.0.1", 8765), Handler)
    print("Terminal preview on http://127.0.0.1:8765/terminal?vm=" + VM_ID, flush=True)
    server.serve_forever()
