"""Quota usage must come from SDK QuotaSet.usage, not limit properties."""

from types import SimpleNamespace

from backend import openstack_service


class Compute:
    def __init__(self, quota_set):
        self.quota_set = quota_set

    def get_quota_set(self, project_id, usage=False):
        assert project_id == "project-a" and usage is True
        return self.quota_set


def set_quota_set(quota_set):
    openstack_service.admin_connection = lambda: SimpleNamespace(compute=Compute(quota_set))


set_quota_set(SimpleNamespace(
    instances=4, cores=8, ram=16384,
    usage={"instances": 1, "cores": 1, "ram": 2048},
))
assert openstack_service.quota("project-a") == {
    "instances": {"limit": 4, "in_use": 1},
    "cores": {"limit": 8, "in_use": 1},
    "ram": {"limit": 16384, "in_use": 2048},
}

set_quota_set(SimpleNamespace(instances=4, cores=8, ram=16384, usage={}))
try:
    openstack_service.quota("project-a")
    raise AssertionError("Missing usage was displayed as zero")
except RuntimeError as exc:
    assert "quota" in str(exc)

print("Quota usage checks passed")
