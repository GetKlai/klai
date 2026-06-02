"""Shield token generation and verification."""

import re


def test_generate_shield_token_format():
    from app.services.shield_tokens import SHIELD_TOKEN_PREFIX, generate_shield_token

    plaintext, _hash = generate_shield_token()
    assert plaintext.startswith(SHIELD_TOKEN_PREFIX)
    hex_part = plaintext[len(SHIELD_TOKEN_PREFIX) :]
    assert len(hex_part) == 40
    assert re.fullmatch(r"[0-9a-f]{40}", hex_part)


def test_generate_shield_token_hash_format():
    from app.services.shield_tokens import generate_shield_token

    _plaintext, token_hash = generate_shield_token()
    assert len(token_hash) == 64
    assert re.fullmatch(r"[0-9a-f]{64}", token_hash)


def test_shield_tokens_are_unique():
    from app.services.shield_tokens import generate_shield_token

    token_a, hash_a = generate_shield_token()
    token_b, hash_b = generate_shield_token()
    assert token_a != token_b
    assert hash_a != hash_b


def test_verify_shield_token_accepts_matching_key():
    from app.services.shield_tokens import generate_shield_token, verify_shield_token

    plaintext, token_hash = generate_shield_token()
    assert verify_shield_token(plaintext, token_hash) is True


def test_verify_shield_token_rejects_wrong_key():
    from app.services.shield_tokens import generate_shield_token, verify_shield_token

    plaintext, _hash = generate_shield_token()
    assert verify_shield_token(plaintext, "a" * 64) is False


def test_verify_shield_token_uses_constant_time_compare():
    from unittest.mock import patch

    from app.services.shield_tokens import generate_shield_token, verify_shield_token

    plaintext, token_hash = generate_shield_token()
    with patch("app.services.shield_tokens.hmac.compare_digest", return_value=True) as mock_cmp:
        assert verify_shield_token(plaintext, token_hash) is True
        mock_cmp.assert_called_once()
