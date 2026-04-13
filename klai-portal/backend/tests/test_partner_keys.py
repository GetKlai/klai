"""RED: Verify partner key generation and SHA-256 hashing.

SPEC-API-001 REQ-1.1, non-functional privacy:
- Format: pk_live_ + 40 hex chars
- Hash: 64 hex chars (SHA-256)
- Two generated keys differ
- Constant-time comparison via hmac.compare_digest
"""

import re

import pytest


def test_generate_key_format():
    """Key format is pk_live_ followed by 40 hex characters."""
    from app.services.partner_keys import generate_partner_key

    plaintext, _hash = generate_partner_key()
    assert plaintext.startswith("pk_live_")
    hex_part = plaintext[len("pk_live_"):]
    assert len(hex_part) == 40
    assert re.fullmatch(r"[0-9a-f]{40}", hex_part), f"Not valid hex: {hex_part}"


def test_generate_key_hash_format():
    """Hash is a 64-char hex string (SHA-256)."""
    from app.services.partner_keys import generate_partner_key

    _plaintext, key_hash = generate_partner_key()
    assert len(key_hash) == 64
    assert re.fullmatch(r"[0-9a-f]{64}", key_hash), f"Not valid hex hash: {key_hash}"


def test_two_keys_differ():
    """Two generated keys must be unique."""
    from app.services.partner_keys import generate_partner_key

    key1, hash1 = generate_partner_key()
    key2, hash2 = generate_partner_key()
    assert key1 != key2
    assert hash1 != hash2


def test_verify_correct_key():
    """verify_partner_key returns True for matching key."""
    from app.services.partner_keys import generate_partner_key, verify_partner_key

    plaintext, key_hash = generate_partner_key()
    assert verify_partner_key(plaintext, key_hash) is True


def test_verify_wrong_key():
    """verify_partner_key returns False for non-matching key."""
    from app.services.partner_keys import generate_partner_key, verify_partner_key

    plaintext, _hash = generate_partner_key()
    assert verify_partner_key(plaintext, "a" * 64) is False


def test_verify_uses_constant_time_comparison():
    """verify_partner_key uses hmac.compare_digest (constant-time)."""
    from unittest.mock import patch

    from app.services.partner_keys import generate_partner_key, verify_partner_key

    plaintext, key_hash = generate_partner_key()

    with patch("app.services.partner_keys.hmac.compare_digest", return_value=True) as mock_cmp:
        result = verify_partner_key(plaintext, key_hash)
        assert result is True
        mock_cmp.assert_called_once()
