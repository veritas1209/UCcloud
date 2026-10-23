"""Preset validation and generated first-boot config, no live cloud access."""
import base64
import json
import tempfile
from pathlib import Path

from backend.software import SOFTWARE, minimum_ram, validate_software
from backend.openstack_service import _vm_user_data, _software_metadata
from backend.app import InstanceBody
from pydantic import ValidationError

assert len(SOFTWARE) == 17
for bad in [[dict(id="node", version="24;curl evil")], [dict(id="../../tmp", version="24")],
            [dict(id="node", version="24"), dict(id="node", version="22")],
            [dict(id="mysql", version="8.4", port=22)],
            [dict(id="nginx", version="1.30", port=3000), dict(id="react", version="19", port=3000)],
            [dict(id="xampp", version="8.2.12"), dict(id="mysql", version="8.4", port=33306)]]:
    try:
        validate_software(bad)
        raise AssertionError("unsafe selection accepted")
    except ValueError:
        pass
    try:
        InstanceBody(name="test", image_id="d58be315-054e-49fb-bb25-14a5544adf39", flavor_id="small",
                     vm_username="student", vm_password="testpassword", software=bad)
        raise AssertionError("API accepted unsafe selection")
    except ValidationError:
        pass
all_items = validate_software([dict(id=item["id"], version=item["versions"][0]) for item in SOFTWARE])
assert minimum_ram(all_items) == 8192
assert minimum_ram([item for item in all_items if item['id'] == 'xampp']) == 4096
assert minimum_ram([item for item in all_items if item['id'] == 'node']) == 2048
metadata = ",".join(f"{item['id']}:{item['version']}" + (f":{item['port']}" if 'port' in item else '') for item in all_items)
assert _software_metadata(dict(portal_software=metadata[:255], portal_software_extra=metadata[255:])) == all_items
assert validate_software([dict(id='postgres', version='18', port=5555)])[0]['port'] == 5555
assert _software_metadata(dict(portal_software="untrusted")) == []
config = json.loads(base64.b64decode(_vm_user_data("student", "testpassword", all_items)).decode().split("\n", 1)[1])
files = {item["path"]: item for item in config["write_files"]}
assert json.loads(files["/etc/uccloud-software.json"]["content"])["selections"] == all_items
assert "testpassword" not in json.dumps(config)
assert config["runcmd"][-1] == ["systemctl", "start", "--no-block", "uccloud-software-install.service"]
assert "ConditionPathExists=!/var/lib/uccloud-software/completed" in files["/etc/systemd/system/uccloud-software-install.service"]["content"]
assert type(json.loads(files["/var/lib/uccloud-software/status.json"]["content"])["started_at"]) is int
with tempfile.TemporaryDirectory() as directory:
    installer = Path(directory) / "guest.py"
    installer.write_text(files["/usr/local/sbin/uccloud-install-software"]["content"])
    compile(installer.read_text(), str(installer), "exec")
import asyncio
from types import SimpleNamespace
from backend.terminal import _watch_software

class FakeSocket:
    def __init__(self):
        self.messages = []
    async def send_json(self, value):
        self.messages.append(value)

class FakeSSH:
    async def run(self, *args, **kwargs):
        return SimpleNamespace(stdout=json.dumps(dict(state="ready", message="완료", type="error")))

socket = FakeSocket()
asyncio.run(_watch_software(socket, FakeSSH()))
assert socket.messages == [dict(type="software", state="ready", message="완료")]
class ProgressSSH:
    async def run(self, *args, **kwargs):
        return SimpleNamespace(stdout=json.dumps(dict(state="ready", message="완료", started_at=1780000000,
            updated_at=1780000125, step=12, total_steps=12, estimate_min_seconds=480,
            estimate_max_seconds=1500, password="must-not-forward")))
socket = FakeSocket()
asyncio.run(_watch_software(socket, ProgressSSH()))
assert socket.messages[0]['started_at'] == 1780000000 and socket.messages[0]['total_steps'] == 12
assert 'password' not in socket.messages[0]
print("Software allowlist, API validation, metadata and cloud-init checks passed")

# Generated container configs are checked without executing Docker or installing anything.
from backend import guest_software as guest
with tempfile.TemporaryDirectory() as directory:
    root = Path(directory)
    guest.BASE = root / 'services'
    guest.BASE.mkdir()
    guest.run = lambda *args, **kwargs: '<Server port="8005"><Service><Connector port="8080"/><Connector port="8009" protocol="AJP/1.3"/></Service></Server>'
    home = root / 'student'
    home.mkdir()
    tomcat, note = guest.container('tomcat', '10.1', 'not-secret', home, 8888)
    assert tomcat['network_mode'] == 'host' and 'ports' not in tomcat
    xml = (guest.BASE / 'tomcat-server.xml').read_text()
    assert '127.0.0.1' in xml and '8888' in xml and '8009' not in xml and 'port="-1"' in xml
    postgres, _ = guest.container('postgres', '18.6', 'not-secret', home, 5555)
    assert postgres['ports'] == ['127.0.0.1:5555:5432'] and postgres['volumes'] == ['postgres-data:/var/lib/postgresql']
    react, _ = guest.container('react', '19', 'not-secret', home, 3000)
    assert react['image'] == 'nginx:1.30' and react['ports'] == ['127.0.0.1:3000:80']
    assert react['restart'] == 'unless-stopped' and all(value.endswith(':ro') for value in react['volumes'])
print('Loopback ports, PostgreSQL 18 layout and JSP localhost DB access configs passed')
