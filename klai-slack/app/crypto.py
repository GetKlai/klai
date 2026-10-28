"""AES-256-GCM encryption for secrets at rest (Slack tokens, the Klai partner key).

Output format: base64(nonce(12) || ciphertext || tag(16)). A tampered or
foreign ciphertext raises ``cryptography.exceptions.InvalidTag`` on decrypt;
that is deliberate, a token that does not authenticate must never be used.
"""

from __future__ import annotations

import base64
import os
from functools import lru_cache

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.config import get_settings

_NONCE_SIZE = 12


@lru_cache
def _cipher() -> AESGCM:
    return AESGCM(base64.b64decode(get_settings().slack_encryption_key))


def encrypt(plaintext: str) -> str:
    nonce = os.urandom(_NONCE_SIZE)
    ciphertext = _cipher().encrypt(nonce, plaintext.encode("utf-8"), None)
    return base64.b64encode(nonce + ciphertext).decode("ascii")


def decrypt(token: str) -> str:
    raw = base64.b64decode(token)
    return _cipher().decrypt(raw[:_NONCE_SIZE], raw[_NONCE_SIZE:], None).decode("utf-8")
