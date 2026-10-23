"""Creating a VM must report quota shortages before asking Nova to create it."""

from types import SimpleNamespace

from backend import openstack_service as service


created = []
image = SimpleNamespace(id="image-a", name="ubuntu-24.04", status="active")
flavor = SimpleNamespace(id="flavor-a", name="small", vcpus=1, ram=2048)
network = SimpleNamespace(id="network-a", project_id="project-a")
server = SimpleNamespace(
    id="server-a", name="my-vm", project_id="project-a", status="BUILD",
    created_at=None, updated_at=None, addresses={}, flavor={"id": "flavor-a", "vcpus": 1, "ram": 2048, "disk": 20},
    image={"id": "image-a"}, metadata={"portal_username": "student"},
)
compute = SimpleNamespace(
    get_flavor=lambda flavor_id: flavor,
    create_server=lambda **payload: created.append(payload) or server,
)
connection = SimpleNamespace(
    image=SimpleNamespace(get_image=lambda image_id: image),
    compute=compute,
    network=SimpleNamespace(get_network=lambda network_id: network),
)
service.team_connection = lambda slug: connection


def attempt(usage):
    service.quota = lambda project_id: usage
    return service.create_server(
        "team-a", "project-a", "student", "network-a", "my-vm",
        "image-a", "flavor-a", "student", "abcdefgh", None,
    )


base = {
    "instances": {"limit": 4, "in_use": 1},
    "cores": {"limit": 8, "in_use": 1},
    "ram": {"limit": 16384, "in_use": 2048},
}

for shortage, expected in (("instances", "VM을 더 만들 수 없습니다"),
                           ("cores", "남은 사양이 부족합니다"),
                           ("ram", "남은 사양이 부족합니다")):
    usage = {key: value.copy() for key, value in base.items()}
    usage[shortage]["in_use"] = usage[shortage]["limit"]
    try:
        attempt(usage)
        raise AssertionError(f"{shortage} shortage was accepted")
    except service.InstanceQuotaError as exc:
        assert expected in str(exc)
    assert created == []

created_payload = attempt(base)
assert created_payload["id"] == "server-a"
assert created_payload["flavor_details"] == {"vcpus": 1, "ram": 2048, "disk": 20}
assert len(created) == 1
request_id = "c6e69a7b-41bf-4bd4-bbac-1eed7aa26205"
compute.servers = lambda details: []
service.quota = lambda project_id: base
service.create_server(
    "team-a", "project-a", "student", "network-a", "my-vm",
    "image-a", "flavor-a", "student", "abcdefgh", None, request_id,
)
assert created[-1]["metadata"]["portal_request_id"] == request_id
server.metadata["portal_request_id"] = request_id
compute.servers = lambda details: [server]
service.quota = lambda project_id: (_ for _ in ()).throw(AssertionError("quota checked before duplicate request"))
assert service.create_server(
    "team-a", "project-a", "student", "network-a", "my-vm",
    "image-a", "flavor-a", "student", "abcdefgh", None, request_id,
)["request_id"] == request_id
assert len(created) == 2
print("VM create quota checks passed")
