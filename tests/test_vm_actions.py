"""VM action state checks without a live OpenStack connection."""

from types import SimpleNamespace

from backend import openstack_service as service


server = SimpleNamespace(status="ACTIVE")
calls = []
compute = SimpleNamespace(
    start_server=lambda server: calls.append("start"),
    stop_server=lambda server: calls.append("stop"),
    reboot_server=lambda server, reboot_type: calls.append("reboot"),
)
service.team_connection = lambda slug: SimpleNamespace(compute=compute)
service._owned_server = lambda conn, server_id, project_id: server

try:
    service.server_action("team", "project", "vm", "start")
    raise AssertionError("starting an active VM should have failed")
except service.InstanceStateError as exc:
    assert "이미 켜져" in str(exc)
assert calls == []

server.status = "SHUTOFF"
service.server_action("team", "project", "vm", "start")
assert calls == ["start"]

server.status = "BUILD"
try:
    service.server_action("team", "project", "vm", "stop")
    raise AssertionError("stopping a building VM should have failed")
except service.InstanceStateError:
    pass
assert calls == ["start"]
print("VM action state checks passed")
