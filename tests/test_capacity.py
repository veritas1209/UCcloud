"""Operator capacity reports only resources on enabled, running compute nodes."""

from types import SimpleNamespace

from backend import openstack_service as service


nodes = [SimpleNamespace(name="compute-a", state="up", status="enabled"),
         SimpleNamespace(name="compute-b", state="down", status="enabled")]
provider = SimpleNamespace(name="compute-a")
inventory = SimpleNamespace(resource_class="MEMORY_MB", total=8192, reserved=1024, allocation_ratio=1.0)
placement = SimpleNamespace(
    resource_providers=lambda: [provider],
    resource_provider_inventories=lambda item: [inventory],
    fetch_resource_provider_usages=lambda item: SimpleNamespace(usages={"MEMORY_MB": 2048}),
)
service.admin_connection = lambda: SimpleNamespace(
    compute=SimpleNamespace(hypervisors=lambda details: nodes), placement=placement,
)

assert service.cluster_capacity() == {"online": 1, "total": 2, "memory_free_mb": 5120}
nodes[1].state = "up"
assert service.cluster_capacity() == {"online": 2, "total": 2, "memory_free_mb": None}
print("Cluster capacity checks passed")
