"""Privilege-boundary regressions. No real OpenStack calls or production DB."""
import importlib
import json
import os
import socket
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from backend import openstack_service as cloud
from backend import provisioner as broker
from backend import provisioner_client as client

PROJECT = "a1111111-1111-4111-8111-111111111111"
SERVER = "b2222222-2222-4222-8222-222222222222"
TEAM = dict(id=1, name="Test", slug="user-1", openstack_project_id=PROJECT)


class BoundaryTests(unittest.TestCase):
    def test_keystone_compact_uuid_and_nova_uuid_are_valid(self):
        self.assertEqual(broker.uuid_value(PROJECT.replace('-', '')), PROJECT.replace('-', ''))
        self.assertEqual(broker.uuid_value(SERVER), SERVER)

    def test_web_uid_cannot_authorize_even_with_admin_environment(self):
        cloud.admin_connection.cache_clear()
        with patch.object(os, "geteuid", return_value=65432), \
             patch("pwd.getpwnam", return_value=SimpleNamespace(pw_uid=65431)), \
             patch.dict(os.environ, {"OS_USERNAME": "admin", "OS_PASSWORD": "test-value"}), \
             patch.object(cloud, "_connect") as connect:
            with self.assertRaises(PermissionError):
                cloud.admin_connection()
            connect.assert_not_called()
        self.assertFalse(hasattr(cloud, "ADMIN_PASSWORD"))
        self.assertFalse(hasattr(cloud, "ADMIN_USER"))

    def test_web_paths_only_use_fixed_broker_operations(self):
        with patch.dict(os.environ, {"PORTAL_PROVISIONER_SOCKET": "/run/uccloud-provisioner/test.sock"}), \
             patch.object(cloud, "_provisioner", return_value={"host": "comsv-01"}) as call, \
             patch.object(cloud, "admin_connection") as admin:
            cloud.ensure_team_project("ignored", "ignored", 3)
            call.assert_called_with("ensure_team", team_id=3)
            cloud.quota(PROJECT)
            call.assert_called_with("quota", project_id=PROJECT)
            cloud.cluster_capacity()
            call.assert_called_with("capacity")
            self.assertEqual(cloud.backup_source_host("user-1", PROJECT, SERVER), "comsv-01")
            admin.assert_not_called()

    def test_no_admin_fallback_when_socket_is_unavailable(self):
        with patch.dict(os.environ, {"PORTAL_PROVISIONER_SOCKET": "/run/uccloud-provisioner/missing.sock"}), \
             patch.object(cloud, "admin_connection") as admin:
            with self.assertRaises(client.ProvisionerUnavailable):
                cloud.quota(PROJECT)
            admin.assert_not_called()

    def test_unknown_operations_and_extra_fields_are_rejected(self):
        invalid = [[], None, {"op": "delete_project"}, {"op": ["health"]},
                   {"op": "capacity", "command": "id"}, {"op": "ensure_team", "team_id": True},
                   {"op": "ensure_team", "team_id": 1, "quota": 999999},
                   {"op": "quota", "project_id": "not-a-uuid"},
                   {"op": "backup_source", "project_id": PROJECT, "server_id": "../host"}]
        with patch.object(cloud, "admin_connection") as admin, patch.object(broker, "registry", return_value=TEAM):
            for payload in invalid:
                with self.subTest(payload=payload), self.assertRaises((ValueError, TypeError)):
                    broker.dispatch(payload)
            # Invalid server UUID is checked after the managed-project lookup;
            # it still never obtains an arbitrary SDK method or host command.

    def test_team_data_comes_from_registry_not_caller(self):
        details = dict(project_id=PROJECT, network_id="net", subnet_id="subnet", router_id="router", cidr="cidr")
        with patch.object(broker, "registry", return_value=TEAM) as lookup, \
             patch.object(cloud, "_direct_ensure_team_project", return_value=details) as ensure:
            self.assertEqual(broker.dispatch(dict(op="ensure_team", team_id=1)), details)
            lookup.assert_called_once_with(id=1)
            ensure.assert_called_once_with("Test", "user-1", 1)

    def test_arbitrary_cloud_project_is_rejected(self):
        fake = SimpleNamespace(identity=SimpleNamespace(get_project=lambda _: SimpleNamespace(
            name="unmanaged", description="School Cloud Portal managed project")))
        with patch.object(broker, "registry", return_value=TEAM), \
             patch.object(cloud, "admin_connection", return_value=fake), \
             patch.object(cloud, "_direct_quota") as quota:
            with self.assertRaises(ValueError):
                broker.dispatch(dict(op="quota", project_id=PROJECT))
            quota.assert_not_called()

    def test_registry_uses_read_only_database_and_fixed_fields(self):
        import sqlite3
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "registry.db"
            with closing(sqlite3.connect(path)) as conn:
                conn.execute("CREATE TABLE teams(id INTEGER,name TEXT,slug TEXT,openstack_project_id TEXT)")
                conn.execute("INSERT INTO teams VALUES(1,'Test','user-1',?)", (PROJECT,))
                conn.commit()
            with patch.dict(os.environ, {"PORTAL_DB": str(path)}):
                self.assertEqual(broker.registry(id=1), TEAM)
                for kwargs in ({"id": 2}, {"name": "Test"}, {"id": 1, "name": "Test"}):
                    with self.assertRaises(ValueError):
                        broker.registry(**kwargs)
            with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as conn:
                with self.assertRaises(sqlite3.OperationalError):
                    conn.execute("DELETE FROM teams")

    def test_client_rejects_oversized_or_error_responses(self):
        import io
        for answer in (b"{}\n", b"x" * 16385, b'{"ok":false,"error":"private-secret"}\n'):
            connection = unittest.mock.MagicMock()
            connection.__enter__.return_value = connection
            connection.makefile.return_value.__enter__.return_value = io.BytesIO(answer)
            with patch.dict(os.environ, {"PORTAL_PROVISIONER_SOCKET": "/run/uccloud-provisioner/test.sock"}), \
                 patch.object(socket, "socket", return_value=connection):
                with self.assertRaises(client.ProvisionerUnavailable) as caught:
                    client.request("health")
                self.assertNotIn("private-secret", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
