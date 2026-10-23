"""The periodic probe records only failure/recovery transitions, never secrets."""

import contextlib
import importlib.util
import io
import os
import sys
import tempfile
from pathlib import Path
from types import ModuleType, SimpleNamespace


temporary = tempfile.TemporaryDirectory(prefix="uccloud-health-test-")
os.environ["PORTAL_DB"] = str(Path(temporary.name) / "portal.db")
fake_service = ModuleType("backend.openstack_service")
fake_service.admin_connection = lambda: None
fake_service.quota = lambda project_id: None
sys.modules["backend.openstack_service"] = fake_service

from backend.database import db, init_db  # noqa: E402

init_db()
with db() as conn:
    conn.execute(
        "INSERT INTO teams(name, slug, openstack_project_id) VALUES(?,?,?)",
        ("QA Team", "qa-team", "qa-project"),
    )

source = Path(__file__).resolve().parents[1] / "ops" / "check_cloud_health.py"
spec = importlib.util.spec_from_file_location("check_cloud_health", source)
monitor = importlib.util.module_from_spec(spec)
spec.loader.exec_module(monitor)
calls = []
monitor.admin_connection = lambda: SimpleNamespace(authorize=lambda: calls.append("auth"))
monitor.quota = lambda project_id: calls.append(project_id)


def actions():
    with db() as conn:
        return [row["action"] for row in conn.execute(
            "SELECT action FROM audit_log ORDER BY id"
        )]


assert monitor.main() == 0
assert calls == ["auth", "qa-project"]
assert actions() == []


def unavailable(project_id):
    assert project_id == "qa-project"
    raise RuntimeError("secret-token-should-never-print")


monitor.quota = unavailable
errors = io.StringIO()
with contextlib.redirect_stderr(errors):
    assert monitor.main() == 1
    assert monitor.main() == 1
assert "secret-token-should-never-print" not in errors.getvalue()
assert actions() == [monitor.FAILED]

monitor.quota = lambda project_id: calls.append(project_id)
assert monitor.main() == 0
assert monitor.main() == 0
assert actions() == [monitor.FAILED, monitor.RECOVERED]

with db() as conn:
    conn.execute("DELETE FROM teams WHERE slug='qa-team'")
calls.clear()
assert monitor.main() == 0
assert calls == ["auth"]
assert actions() == [monitor.FAILED, monitor.RECOVERED]
temporary.cleanup()
print("Cloud health transition checks passed")
