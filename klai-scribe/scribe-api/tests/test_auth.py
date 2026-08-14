"""SPEC-SEC-IDENTITY-ASSERT-002 REQ-3: scribe-api authenticates from
portal-api BFF verified headers, NOT from a Bearer JWT.

Pinned invariants:
- C1: valid X-Internal-Secret + X-Klai-Verified-* → CallerIdentity returned.
- C2: wrong X-Internal-Secret → 401, no identity returned.
- C3: missing X-Internal-Secret → 401.
- C4: missing X-Klai-Verified-User-Id → 401.
- C5: missing X-Klai-Verified-Org-Id → 401.
- C6: empty-string verified header → 401 (treated identically to missing).
- C7: scribe auth.py contains NO references to JWT decode, JWKS, or
  IdentityAsserter (regression guard against re-introduction).
- C8: Authorization header is ignored — even if invalid garbage, the
  identity decision is unaffected.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.core import auth as auth_module
from app.core.auth import CallerIdentity, get_authenticated_caller

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _expected_secret(monkeypatch: pytest.MonkeyPatch) -> str:
    """Set settings.portal_internal_secret to a known value and return it."""
    from app.core.config import settings

    secret = "real-portal-secret-from-sops-xxxxxxxxxxxxx"
    monkeypatch.setattr(settings, "portal_internal_secret", secret)
    return secret


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


class TestAuthHappyPath:
    async def test_returns_caller_identity_on_valid_headers(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        secret = _expected_secret(monkeypatch)

        result = await get_authenticated_caller(
            x_internal_secret=secret,
            x_klai_verified_user_id="362760545968848902",
            x_klai_verified_org_id="100000000000000001",
        )

        assert isinstance(result, CallerIdentity)
        assert result.user_id == "362760545968848902"
        assert result.org_id == "100000000000000001"


# ---------------------------------------------------------------------------
# Reject paths — REQ-3.1, REQ-3.6
# ---------------------------------------------------------------------------


class TestAuthRejectPaths:
    async def test_missing_x_internal_secret_returns_401(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _expected_secret(monkeypatch)

        with pytest.raises(HTTPException) as exc:
            await get_authenticated_caller(
                x_internal_secret=None,
                x_klai_verified_user_id="u-1",
                x_klai_verified_org_id="o-1",
            )

        assert exc.value.status_code == 401
        assert exc.value.detail == "unauthenticated"

    async def test_missing_user_id_header_returns_401(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        secret = _expected_secret(monkeypatch)

        with pytest.raises(HTTPException) as exc:
            await get_authenticated_caller(
                x_internal_secret=secret,
                x_klai_verified_user_id=None,
                x_klai_verified_org_id="o-1",
            )

        assert exc.value.status_code == 401
        assert exc.value.detail == "unauthenticated"

    async def test_missing_org_id_header_returns_401(self, monkeypatch: pytest.MonkeyPatch) -> None:
        secret = _expected_secret(monkeypatch)

        with pytest.raises(HTTPException) as exc:
            await get_authenticated_caller(
                x_internal_secret=secret,
                x_klai_verified_user_id="u-1",
                x_klai_verified_org_id=None,
            )

        assert exc.value.status_code == 401
        assert exc.value.detail == "unauthenticated"

    async def test_empty_string_user_id_treated_as_missing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        secret = _expected_secret(monkeypatch)

        with pytest.raises(HTTPException) as exc:
            await get_authenticated_caller(
                x_internal_secret=secret,
                x_klai_verified_user_id="",
                x_klai_verified_org_id="o-1",
            )

        assert exc.value.status_code == 401

    async def test_empty_string_org_id_treated_as_missing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        secret = _expected_secret(monkeypatch)

        with pytest.raises(HTTPException) as exc:
            await get_authenticated_caller(
                x_internal_secret=secret,
                x_klai_verified_user_id="u-1",
                x_klai_verified_org_id="",
            )

        assert exc.value.status_code == 401

    async def test_wrong_secret_returns_401(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _expected_secret(monkeypatch)

        with pytest.raises(HTTPException) as exc:
            await get_authenticated_caller(
                x_internal_secret="WRONG-SECRET-attacker-attempt",
                x_klai_verified_user_id="u-1",
                x_klai_verified_org_id="o-1",
            )

        assert exc.value.status_code == 401
        assert exc.value.detail == "unauthenticated"


# ---------------------------------------------------------------------------
# Static contract: NO JWT decoding remains in scribe auth (REQ-3.2 / REQ-3.4)
# ---------------------------------------------------------------------------


class TestNoJwtDecodingRemains:
    """C7: scribe auth.py module must contain no JWT-decode primitives."""

    def test_no_jwt_decoding_in_auth_module(self) -> None:
        # Look at the auth module's source via dunder attributes — no live
        # references to jwt decode helpers, JWKS fetchers, or
        # IdentityAsserter.
        forbidden = (
            "_decode_zitadel_token",
            "_fetch_jwks",
            "_get_jwks",
            "_validate_sub",
            "_jwks_cache",
            "IdentityAsserter",
            "klai_identity_assert",
        )
        for name in forbidden:
            assert not hasattr(auth_module, name), (
                f"{name} is still defined in app.core.auth — "
                "SPEC-SEC-IDENTITY-ASSERT-002 REQ-3 requires its removal"
            )

    def test_no_resourceowner_claim_constant(self) -> None:
        # Klai zitadel.md rule: never read this claim. After SPEC-002 even
        # the constant is gone.
        assert not hasattr(auth_module, "_ZITADEL_RESOURCEOWNER_CLAIM"), (
            "_ZITADEL_RESOURCEOWNER_CLAIM was removed by SPEC-SEC-IDENTITY-ASSERT-002"
        )

    def test_klai_identity_assert_not_imported(self) -> None:
        import sys

        # The library must not be loaded as part of scribe-api's import
        # graph. If it is, a transitive import sneaked back in.
        assert "klai_identity_assert" not in sys.modules, (
            "klai_identity_assert is still importable in scribe-api — "
            "SPEC-SEC-IDENTITY-ASSERT-002 REQ-3.4 requires the dependency to be dropped"
        )


# ---------------------------------------------------------------------------
# Authorization header ignored (REQ-3.5)
# ---------------------------------------------------------------------------


class TestAuthorizationHeaderIgnored:
    """C8: Authorization header is irrelevant for identity. Scribe never
    consults it; transcription providers downstream may use it for their
    own auth model."""

    async def test_get_authenticated_caller_accepts_no_authorization_param(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Deliberate signature contract: get_authenticated_caller has only
        # the three BFF-header parameters. Any future change that adds an
        # Authorization parameter to this signature is a regression that
        # this test catches.
        import inspect

        sig = inspect.signature(get_authenticated_caller)
        params = set(sig.parameters)
        assert params == {
            "x_internal_secret",
            "x_klai_verified_user_id",
            "x_klai_verified_org_id",
        }, f"unexpected parameter set: {params}"
