from __future__ import annotations

import base64
import bcrypt
import hashlib
import hmac
import secrets


BCRYPT_ROUNDS = 12
BCRYPT_PREFIX = "bcrypt-sha256$"


def _bcrypt_input(password: str) -> bytes:
    # Base64(SHA-256) uses the full password while staying below bcrypt's 72-byte limit.
    return base64.b64encode(hashlib.sha256(password.encode("utf-8")).digest())


def hash_password(password: str) -> str:
    if len(password) < 8:
        raise ValueError("Password must contain at least 8 characters")
    return BCRYPT_PREFIX + bcrypt.hashpw(
        _bcrypt_input(password), bcrypt.gensalt(rounds=BCRYPT_ROUNDS)
    ).decode("ascii")


def verify_password(password: str, encoded: str) -> bool:
    try:
        if encoded.startswith(BCRYPT_PREFIX):
            return bcrypt.checkpw(
                _bcrypt_input(password), encoded[len(BCRYPT_PREFIX):].encode("ascii")
            )
        # Legacy scrypt accounts are upgraded after their next successful login.
        algorithm, n, r, p, salt, expected = encoded.split("$", 5)
        if algorithm != "scrypt":
            return False
        derived = hashlib.scrypt(
            password.encode(),
            salt=base64.urlsafe_b64decode(salt),
            n=int(n),
            r=int(r),
            p=int(p),
            dklen=32,
        )
        return hmac.compare_digest(derived, base64.urlsafe_b64decode(expected))
    except (ValueError, TypeError, UnicodeEncodeError):
        return False


def needs_password_rehash(encoded: str) -> bool:
    return not encoded.startswith(BCRYPT_PREFIX)


def new_token() -> str:
    return secrets.token_urlsafe(32)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()
