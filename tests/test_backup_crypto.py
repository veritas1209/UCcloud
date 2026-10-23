"""Offline authenticated backup round-trip; no live portal data is read."""

import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ops"))
from backup_crypto import decrypt_content, encrypt, generate_keys, generate_signing_keys  # noqa: E402


class BackupCryptoTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.database = self.directory / "portal.sqlite3"
        self.private = self.directory / "recovery.key"
        self.public = self.directory / "recovery.pub"
        self.signing_private = self.directory / "signing.key"
        self.signing_public = self.directory / "signing.pub"
        self.encrypted = self.directory / "portal.ucb"
        with closing(sqlite3.connect(self.database)) as conn:
            with conn:
                for table in ("teams", "users", "sessions", "audit_log", "instance_ssh_host_keys"):
                    conn.execute(f"CREATE TABLE {table} (id INTEGER PRIMARY KEY)")
                conn.execute("INSERT INTO teams DEFAULT VALUES")
        generate_keys(self.private, self.public)
        generate_signing_keys(self.signing_private, self.signing_public)

    def test_round_trip_and_integrity(self):
        rows = encrypt(self.database, self.public, self.signing_private, self.encrypted)
        self.assertEqual(rows["teams"], 1)
        self.assertNotIn(b"SQLite format 3", self.encrypted.read_bytes())
        self.assertEqual(decrypt_content(self.encrypted, self.private, self.signing_public), self.database.read_bytes())
        with self.assertRaises(FileExistsError):
            encrypt(self.database, self.public, self.signing_private, self.encrypted)

    def test_tampering_and_wrong_key_are_rejected(self):
        encrypt(self.database, self.public, self.signing_private, self.encrypted)
        other_private = self.directory / "other.key"
        other_public = self.directory / "other.pub"
        generate_keys(other_private, other_public)
        with self.assertRaisesRegex(ValueError, "authentication failed"):
            decrypt_content(self.encrypted, other_private, self.signing_public)
        tampered = bytearray(self.encrypted.read_bytes())
        tampered[-1] ^= 1
        self.encrypted.write_bytes(tampered)
        with self.assertRaisesRegex(ValueError, "signature verification failed"):
            decrypt_content(self.encrypted, self.private, self.signing_public)

    def test_replacement_by_another_signer_is_rejected(self):
        other_private = self.directory / "other-signing.key"
        other_public = self.directory / "other-signing.pub"
        generate_signing_keys(other_private, other_public)
        encrypt(self.database, self.public, other_private, self.encrypted)
        with self.assertRaisesRegex(ValueError, "signature verification failed"):
            decrypt_content(self.encrypted, self.private, self.signing_public)

    def test_bad_source_and_existing_key_are_rejected(self):
        bad = self.directory / "bad.sqlite3"
        bad.write_bytes(b"not a database")
        with self.assertRaises(sqlite3.DatabaseError):
            encrypt(bad, self.public, self.signing_private, self.encrypted)
        self.assertFalse(self.encrypted.exists())
        with self.assertRaises(FileExistsError):
            generate_keys(self.private, self.directory / "replacement.pub")


if __name__ == "__main__":
    unittest.main()
