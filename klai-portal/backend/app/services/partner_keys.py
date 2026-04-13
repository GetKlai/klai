"""Partner API key generation and verification.

SPEC-API-001 REQ-1.1:
- Generate keys in format pk_live_ + 40 hex chars
- Store only SHA-256 hash
- Constant-time verification via hmac.compare_digest
"""

import hashlib
import hmac
import secrets


def generate_partner_key() -> tuple[str, str]:
    """Generate a partner API key.

    Returns:
        (plaintext_key, sha256_hex_hash) — the plaintext is shown once,
        only the hash is stored.
    """
    random_hex = secrets.token_hex(20)  # 40 hex chars
    plaintext = f"pk_live_{random_hex}"
    key_hash = hashlib.sha256(plaintext.encode()).hexdigest()
    return plaintext, key_hash


def verify_partner_key(plaintext: str, stored_hash: str) -> bool:
    """Constant-time comparison of key against stored hash.

    Uses hmac.compare_digest to prevent timing attacks.
    """
    computed = hashlib.sha256(plaintext.encode()).hexdigest()
    return hmac.compare_digest(computed, stored_hash)
