"""Encrypt UCcloud portal backups for storage away from the controller.

Keep the X25519 private recovery key off the controller. The controller needs
only the public key to create authenticated AES-GCM backup envelopes.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
from contextlib import closing
from pathlib import Path

from cryptography.exceptions import InvalidSignature, InvalidTag
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from backup_portal_db import check_database


MAGIC = b"UCCLOUD-PORTAL-DB-V2\n"
HEADER_SIZE = len(MAGIC) + 32 + 16 + 12
SIGNATURE_SIZE = 64
MAX_PLAINTEXT_BYTES = 100 * 1024 * 1024


def _read_regular(path: Path, maximum: int) -> bytes:
    if not path.is_file() or path.is_symlink():
        raise ValueError(f"Not a regular file: {path}")
    if path.stat().st_size > maximum:
        raise ValueError(f"File is too large: {path}")
    return path.read_bytes()


def _write_new(path: Path, content: bytes) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as output:
            descriptor = -1
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
    except BaseException:
        if descriptor >= 0:
            os.close(descriptor)
        path.unlink(missing_ok=True)
        raise


def _key(shared_secret: bytes, salt: bytes) -> bytes:
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=salt,
                info=MAGIC).derive(shared_secret)


def _check_plaintext(content: bytes) -> dict[str, int]:
    if len(content) > MAX_PLAINTEXT_BYTES:
        raise ValueError("Backup exceeds the size limit")
    with closing(sqlite3.connect(":memory:")) as conn:
        conn.deserialize(content)
        return check_database(conn)


def generate_keys(private_path: Path, public_path: Path) -> None:
    if private_path.exists() or public_path.exists():
        raise FileExistsError("Key files already exist")
    private = X25519PrivateKey.generate()
    secret = private.private_bytes(serialization.Encoding.Raw,
                                   serialization.PrivateFormat.Raw,
                                   serialization.NoEncryption())
    public = private.public_key().public_bytes(serialization.Encoding.Raw,
                                               serialization.PublicFormat.Raw)
    _write_new(private_path, secret)
    try:
        _write_new(public_path, public)
    except BaseException:
        private_path.unlink(missing_ok=True)
        raise


def generate_signing_keys(private_path: Path, public_path: Path) -> None:
    if private_path.exists() or public_path.exists():
        raise FileExistsError("Signing key files already exist")
    private = Ed25519PrivateKey.generate()
    secret = private.private_bytes(serialization.Encoding.Raw,
                                   serialization.PrivateFormat.Raw,
                                   serialization.NoEncryption())
    public = private.public_key().public_bytes(serialization.Encoding.Raw,
                                               serialization.PublicFormat.Raw)
    _write_new(private_path, secret)
    try:
        _write_new(public_path, public)
    except BaseException:
        private_path.unlink(missing_ok=True)
        raise


def encrypt(source: Path, public_path: Path, signing_private_path: Path, destination: Path) -> dict[str, int]:
    plaintext = _read_regular(source, MAX_PLAINTEXT_BYTES)
    statistics = _check_plaintext(plaintext)
    public_bytes = _read_regular(public_path, 32)
    if len(public_bytes) != 32:
        raise ValueError("Invalid public key size")
    recipient = X25519PublicKey.from_public_bytes(public_bytes)
    ephemeral = X25519PrivateKey.generate()
    ephemeral_public = ephemeral.public_key().public_bytes(serialization.Encoding.Raw,
                                                            serialization.PublicFormat.Raw)
    salt = os.urandom(16)
    nonce = os.urandom(12)
    header = MAGIC + ephemeral_public + salt + nonce
    sealed = AESGCM(_key(ephemeral.exchange(recipient), salt)).encrypt(nonce, plaintext, header)
    signing_secret = _read_regular(signing_private_path, 32)
    if len(signing_secret) != 32:
        raise ValueError("Invalid signing private key size")
    signed = header + sealed
    signature = Ed25519PrivateKey.from_private_bytes(signing_secret).sign(signed)
    _write_new(destination, signed + signature)
    return statistics


def decrypt_content(source: Path, private_path: Path, signing_public_path: Path) -> bytes:
    envelope = _read_regular(source, MAX_PLAINTEXT_BYTES + HEADER_SIZE + 16 + SIGNATURE_SIZE)
    if len(envelope) < HEADER_SIZE + 16 + SIGNATURE_SIZE or not envelope.startswith(MAGIC):
        raise ValueError("Invalid UCcloud backup envelope")
    signing_public = _read_regular(signing_public_path, 32)
    if len(signing_public) != 32:
        raise ValueError("Invalid signing public key size")
    signed, signature = envelope[:-SIGNATURE_SIZE], envelope[-SIGNATURE_SIZE:]
    try:
        Ed25519PublicKey.from_public_bytes(signing_public).verify(signature, signed)
    except InvalidSignature as error:
        raise ValueError("Backup signature verification failed") from error
    secret = _read_regular(private_path, 32)
    if len(secret) != 32:
        raise ValueError("Invalid private key size")
    ephemeral_start = len(MAGIC)
    ephemeral = X25519PublicKey.from_public_bytes(envelope[ephemeral_start:ephemeral_start + 32])
    salt = envelope[ephemeral_start + 32:ephemeral_start + 48]
    nonce = envelope[ephemeral_start + 48:HEADER_SIZE]
    try:
        plaintext = AESGCM(_key(X25519PrivateKey.from_private_bytes(secret).exchange(ephemeral), salt)).decrypt(
            nonce, signed[HEADER_SIZE:], signed[:HEADER_SIZE])
    except InvalidTag as error:
        raise ValueError("Backup authentication failed") from error
    _check_plaintext(plaintext)
    return plaintext


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_subparsers(dest="action", required=True)
    keygen = actions.add_parser("keygen")
    keygen.add_argument("--private", required=True, type=Path)
    keygen.add_argument("--public", required=True, type=Path)
    signer_keygen = actions.add_parser("signer-keygen")
    signer_keygen.add_argument("--private", required=True, type=Path)
    signer_keygen.add_argument("--public", required=True, type=Path)
    seal = actions.add_parser("encrypt")
    seal.add_argument("--input", required=True, type=Path)
    seal.add_argument("--public", required=True, type=Path)
    seal.add_argument("--signing-private", required=True, type=Path)
    seal.add_argument("--output", required=True, type=Path)
    verify = actions.add_parser("verify")
    verify.add_argument("--input", required=True, type=Path)
    verify.add_argument("--private", required=True, type=Path)
    verify.add_argument("--signing-public", required=True, type=Path)
    restore = actions.add_parser("decrypt")
    restore.add_argument("--input", required=True, type=Path)
    restore.add_argument("--private", required=True, type=Path)
    restore.add_argument("--signing-public", required=True, type=Path)
    restore.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    os.umask(0o077)
    if args.action == "keygen":
        generate_keys(args.private, args.public)
        print(json.dumps({"private": str(args.private), "public": str(args.public)}))
    elif args.action == "signer-keygen":
        generate_signing_keys(args.private, args.public)
        print(json.dumps({"private": str(args.private), "public": str(args.public)}))
    elif args.action == "encrypt":
        rows = encrypt(args.input, args.public, args.signing_private, args.output)
        print(json.dumps({"encrypted": str(args.output), "sha256": hashlib.sha256(args.output.read_bytes()).hexdigest(), "rows": rows}))
    else:
        content = decrypt_content(args.input, args.private, args.signing_public)
        rows = _check_plaintext(content)
        if args.action == "decrypt":
            _write_new(args.output, content)
            print(json.dumps({"restored": str(args.output), "rows": rows}))
        else:
            print(json.dumps({"verified": str(args.input), "rows": rows}))


if __name__ == "__main__":
    main()
