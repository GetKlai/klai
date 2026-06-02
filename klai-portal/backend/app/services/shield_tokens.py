"""Shield extension token helpers."""

from __future__ import annotations

import hashlib
import hmac
import secrets


SHIELD_TOKEN_PREFIX = "ks_live_"


def hash_shield_token(plaintext: str) -> str:
    """Return the SHA-256 hex digest stored in the database."""
    return hashlib.sha256(plaintext.encode()).hexdigest()


def generate_shield_token() -> tuple[str, str]:
    """Generate a Shield token and its persisted hash.

    Returns:
        ``(plaintext_token, sha256_hex_hash)``. The plaintext token is shown
        once to the platform admin and never stored.
    """
    random_hex = secrets.token_hex(20)
    plaintext = f"{SHIELD_TOKEN_PREFIX}{random_hex}"
    return plaintext, hash_shield_token(plaintext)


def verify_shield_token(plaintext: str, stored_hash: str) -> bool:
    """Constant-time comparison of a plaintext Shield token against a hash."""
    return hmac.compare_digest(hash_shield_token(plaintext), stored_hash)
