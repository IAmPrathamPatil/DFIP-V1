"""Password hashing for application sign-in.

Uses stdlib PBKDF2-HMAC-SHA256. Not bcrypt. Never log password or hash values.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import threading

SCHEME = "pbkdf2_sha256"
MAX_PBKDF2_ITERATIONS = 1_000_000
_DUMMY_LOCK = threading.Lock()
_DUMMY_BY_ITERATIONS: dict[int, str] = {}


def hash_password(password: str, iterations: int) -> str:
    """Return a stored verifier ``pbkdf2_sha256$iterations$salt$hash``."""
    if iterations < 1 or iterations > MAX_PBKDF2_ITERATIONS:
        raise ValueError("Unsupported PBKDF2 iteration count.")
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return f"{SCHEME}${iterations}${salt.hex()}${digest.hex()}"


def dummy_password_hash(iterations: int) -> str:
    """Cached verifier used to keep missing-user checks closer in duration."""
    safe = min(max(iterations, 1), MAX_PBKDF2_ITERATIONS)
    with _DUMMY_LOCK:
        existing = _DUMMY_BY_ITERATIONS.get(safe)
        if existing is not None:
            return existing
        hashed = hash_password("dfip-dummy-password", safe)
        _DUMMY_BY_ITERATIONS[safe] = hashed
        return hashed


def verify_password(password: str, stored: str) -> bool:
    """Return True when *password* matches *stored*. Invalid records are False."""
    parts = stored.split("$")
    if len(parts) != 4 or parts[0] != SCHEME:
        return False
    try:
        iterations = int(parts[1])
    except ValueError:
        return False
    if iterations < 1 or iterations > MAX_PBKDF2_ITERATIONS:
        return False
    try:
        salt = bytes.fromhex(parts[2])
        expected = bytes.fromhex(parts[3])
    except ValueError:
        return False
    actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return hmac.compare_digest(actual, expected)
