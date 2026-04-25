"""HY-34 / REQ-34 — Zitadel `sub` charset whitelist regression test.

A malformed `sub` MUST be rejected at the auth layer before any downstream
handler touches it. See SPEC-SEC-HYGIENE-001 REQ-34.1.

JWKS fetch and `jwt.decode` are mocked because we only care about the
validation that runs AFTER decode returns the payload — the whole point
is that the regex check fires regardless of whether decode succeeded.
"""
from __future__ import annotations

import pytest
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials


async def _fake_get_jwks(force_refresh: bool = False) -> dict:
    return {"keys": [{"kid": "test", "kty": "RSA", "n": "x", "e": "AQAB"}]}


def _fake_find_key(jwks: dict, kid: str | None) -> dict:
    return {"kid": "test"}


def _fake_get_unverified_header(token: str) -> dict:
    return {"kid": "test"}


@pytest.fixture
def patch_jwt(monkeypatch):
    """Stub out the JWKS fetch and JOSE header parsing.

    Returns a callable that pins `jwt.decode` to a payload with the given sub.
    """
    monkeypatch.setattr("app.core.auth._get_jwks", _fake_get_jwks)
    monkeypatch.setattr("app.core.auth._find_key", _fake_find_key)
    monkeypatch.setattr(
        "app.core.auth.jwt.get_unverified_header", _fake_get_unverified_header
    )

    def _set_sub(sub: str | None) -> None:
        payload = {"sub": sub} if sub is not None else {}

        def _fake_decode(*_a, **_kw) -> dict:
            return payload

        monkeypatch.setattr("app.core.auth.jwt.decode", _fake_decode)

    return _set_sub


# ---------------------------------------------------------------------------
# Legitimate Zitadel sub formats — auth MUST pass.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "sub",
    [
        "269462541789364226",            # Numeric string — current Zitadel default
        "user_id-42",                    # Alphanumeric + underscore + hyphen
        "uuid-with-dashes-a1b2c3d4-e5f6",  # UUID-style
        "a" * 64,                        # Boundary: exactly max length
        "X",                             # Boundary: single char
    ],
)
async def test_legitimate_sub_passes(patch_jwt, sub: str) -> None:
    from app.core.auth import get_current_user_id

    patch_jwt(sub)
    creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials="fake")

    result = await get_current_user_id(creds)
    assert result == sub


# ---------------------------------------------------------------------------
# Malformed subs — auth MUST return 401 BEFORE downstream sees the sub.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "sub",
    [
        "../evil",            # Path traversal
        "/absolute/path",     # Absolute path attempt
        "..\\win",            # Windows-style traversal
        "user with spaces",   # Whitespace
        "a" * 65,             # Boundary: too long
        "evil$$inject",       # Special chars
        "user.dot",           # Dot — defense-in-depth (HY-33 path check would catch)
        "user@host",          # Email-like
        "user:colon",         # Colon
        "user/slash",         # Forward slash
    ],
)
async def test_malformed_sub_rejected(patch_jwt, sub: str) -> None:
    from app.core.auth import get_current_user_id

    patch_jwt(sub)
    creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials="fake")

    with pytest.raises(HTTPException) as exc_info:
        await get_current_user_id(creds)
    assert exc_info.value.status_code == 401


async def test_empty_sub_rejected(patch_jwt) -> None:
    """Empty sub is rejected by the existing `not user_id` check, but the
    test pins the contract so the regex change does not regress it."""
    from app.core.auth import get_current_user_id

    patch_jwt("")
    creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials="fake")

    with pytest.raises(HTTPException) as exc_info:
        await get_current_user_id(creds)
    assert exc_info.value.status_code == 401


async def test_missing_sub_claim_rejected(patch_jwt) -> None:
    """No sub key at all in the payload."""
    from app.core.auth import get_current_user_id

    patch_jwt(None)
    creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials="fake")

    with pytest.raises(HTTPException) as exc_info:
        await get_current_user_id(creds)
    assert exc_info.value.status_code == 401
