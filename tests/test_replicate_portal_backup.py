"""No-network tests for encrypted backup transfer sequencing and cleanup."""

import hashlib
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ops"))
import replicate_portal_backup as replication  # noqa: E402


class ReplicationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.backup = self.directory / "portal-20260924T000000Z-deadbeef.sqlite3"
        self.backup.write_bytes(b"fake backup for mocked transport")

    def test_verified_backup_is_sent_by_checksum_then_renamed(self):
        commands = []

        def fake_encrypt(source, public_key, signing_private_key, destination):
            self.assertEqual(source, self.backup)
            destination.write_bytes(b"authenticated encrypted backup")

        def fake_ssh(command):
            commands.append(command)
            if command.startswith("sha256sum"):
                return hashlib.sha256(b"authenticated encrypted backup").hexdigest() + "  remote"
            return ""

        with patch.object(replication.os, "geteuid", return_value=0, create=True), \
             patch.object(replication, "restore_check") as restore, \
             patch.object(replication, "encrypt", side_effect=fake_encrypt), \
             patch.object(replication, "_ssh", side_effect=fake_ssh), \
             patch.object(replication, "_run", return_value="") as transfer:
            result = replication.replicate(self.directory, self.directory / "recovery.pub")
        restore.assert_called_once_with(self.backup, self.directory)
        self.assertEqual(result["source"], self.backup.name)
        self.assertIn("172.29.236.12:", result["remote"])
        self.assertEqual(len(transfer.call_args_list), 1)
        self.assertEqual(transfer.call_args.args[0][0], "scp")
        self.assertTrue(commands[0].startswith("test ! -L"))
        self.assertTrue(commands[1].startswith("sha256sum"))
        self.assertIn("&& ln --", commands[2])
        self.assertEqual(list(self.directory.glob(".transfer-*.ucb")), [])

    def test_failed_upload_cleans_up_both_temporary_paths(self):
        commands = []

        def fake_encrypt(source, public_key, signing_private_key, destination):
            destination.write_bytes(b"sealed")

        with patch.object(replication.os, "geteuid", return_value=0, create=True), \
             patch.object(replication, "restore_check"), \
             patch.object(replication, "encrypt", side_effect=fake_encrypt), \
             patch.object(replication, "_ssh", side_effect=lambda command: commands.append(command) or ""), \
             patch.object(replication, "_run", side_effect=subprocess.CalledProcessError(1, "scp")):
            with self.assertRaises(subprocess.CalledProcessError):
                replication.replicate(self.directory, self.directory / "recovery.pub")
        self.assertTrue(commands[-1].startswith("rm -f --"))
        self.assertEqual(list(self.directory.glob(".transfer-*.ucb")), [])

    def test_stale_backup_is_not_sent(self):
        old = time.time() - replication.MAX_BACKUP_AGE_SECONDS - 60
        os.utime(self.backup, (old, old))
        with self.assertRaisesRegex(RuntimeError, "stale"):
            replication.newest_verified_backup(self.directory)


if __name__ == "__main__":
    unittest.main()
