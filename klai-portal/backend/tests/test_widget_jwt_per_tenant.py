"""SPEC-SEC-HYGIENE-001 REQ-24 / AC-24: per-tenant HKDF derivation for
widget JWT signing keys.

Pre-fix: every widget JWT was signed with the single
``settings.widget_jwt_secret`` (HS256 shared secret). A single secret
exposure would let an attacker forge tokens for every tenant. Asymmetric
signing (ES256/EdDSA) is the structural fix and is scoped to a future
SPEC. This narrows the blast radius today by deriving per-tenant keys
via HKDF-SHA256.

Tests:
- AC-24 cross-tenant isolation: token issued for tenant A must not
  validate when decoded with tenant B's slug.
- REQ-24.1 determinism + tenant separation + master rotation.
"""

from __future__ import annotations

import jwt
import pytest

from app.services.widget_auth import (
    _derive_tenant_key,
    decode_session_token,
    generate_session_token,
)

_MASTER_SECRET = "test-master-secret-32-bytes-long!!"  # nosec — test placeholder


# AC-24 cross-tenant isolation -------------------------------------------- #


def test_token_for_tenant_a_validates_with_tenant_a_slug() -> None:
    """A token issued for tenant A decodes correctly with tenant A's slug."""
    token = generate_session_token(
        wgt_id="wgt_a",
        org_id=1,
        kb_ids=[10, 11],
        secret=_MASTER_SECRET,
        tenant_slug="alpha",
    )
    payload = decode_session_token(token, _MASTER_SECRET, tenant_slug="alpha")
    assert payload["wgt_id"] == "wgt_a"
    assert payload["org_id"] == 1
    assert payload["kb_ids"] == [10, 11]
    assert "exp" in payload


def test_token_for_tenant_a_does_not_validate_with_tenant_b_slug() -> None:
    """REQ-24.5: cross-tenant decode must fail with InvalidSignatureError
    (not any other exception type — confirms signature mismatch, not a
    schema or TTL failure).
    """
    token = generate_session_token(
        wgt_id="wgt_a",
        org_id=1,
        kb_ids=[],
        secret=_MASTER_SECRET,
        tenant_slug="alpha",
    )
    with pytest.raises(jwt.InvalidSignatureError):
        decode_session_token(token, _MASTER_SECRET, tenant_slug="bravo")


def test_token_for_tenant_b_does_not_validate_with_tenant_a_slug() -> None:
    """Mirror case — confirms isolation is symmetric, not a one-way coincidence."""
    token = generate_session_token(
        wgt_id="wgt_b",
        org_id=2,
        kb_ids=[],
        secret=_MASTER_SECRET,
        tenant_slug="bravo",
    )
    with pytest.raises(jwt.InvalidSignatureError):
        decode_session_token(token, _MASTER_SECRET, tenant_slug="alpha")


# REQ-24.1: HKDF determinism + tenant separation + master rotation -------- #


def test_derive_tenant_key_is_deterministic() -> None:
    """Same master + same slug → same key, every time."""
    k1 = _derive_tenant_key(_MASTER_SECRET, "alpha")
    k2 = _derive_tenant_key(_MASTER_SECRET, "alpha")
    assert k1 == k2
    assert len(k1) == 32  # HKDF length parameter (SHA256, 32 bytes for HS256)


def test_derive_tenant_key_separates_tenants() -> None:
    """Same master + DIFFERENT slug → different keys."""
    k_alpha = _derive_tenant_key(_MASTER_SECRET, "alpha")
    k_bravo = _derive_tenant_key(_MASTER_SECRET, "bravo")
    assert k_alpha != k_bravo


def test_derive_tenant_key_rotates_with_master() -> None:
    """Different master + same slug → different keys.

    Confirms that rotating ``WIDGET_JWT_SECRET`` invalidates all live
    widget sessions (REQ-24.3) — exactly the deploy-time behaviour the
    runbook warns about.
    """
    k_v1 = _derive_tenant_key(_MASTER_SECRET, "alpha")
    k_v2 = _derive_tenant_key(_MASTER_SECRET + "-v2", "alpha")
    assert k_v1 != k_v2
