"""Tests for the shared test helpers in ``tests/_helpers.py``.

These tests guard the contract that 53+ other tests rely on:
- Default identity tuple is ``user1/org1/testorg`` with ``jwt`` evidence
- Override via kwargs works for spoof / membership scenarios
- Result is a properly-typed ``VerifyResult`` allow-result
"""

from __future__ import annotations

from klai_identity_assert import VerifyResult

from tests._helpers import allow_verify_result


class TestAllowVerifyResultDefaults:
    """The no-args call must produce the standard test fixture identity."""

    def test_returns_verify_result_instance(self):
        result = allow_verify_result()
        assert isinstance(result, VerifyResult)

    def test_default_identity_tuple(self):
        result = allow_verify_result()
        # Defaults match the standard fixture used by taxonomy / security /
        # sec-internal tests. Changing these silently would shift the
        # identity surface for ~50 tests.
        assert result.user_id == "user1"
        assert result.org_id == "org1"
        assert result.org_slug == "testorg"

    def test_default_evidence_is_jwt(self):
        result = allow_verify_result()
        assert result.evidence == "jwt"

    def test_verified_is_true(self):
        # An allow-result MUST set ``verified=True`` — otherwise the helper
        # is silently behaving like a deny-result and tests would short-
        # circuit through ``_ERR_IDENTITY_REJECTED`` instead of the path
        # they meant to exercise.
        result = allow_verify_result()
        assert result.verified is True


class TestAllowVerifyResultOverrides:
    """Identity-specific tests (spoof / membership) override via kwargs."""

    def test_user_id_override(self):
        result = allow_verify_result(user_id="VERIFIED-USER")
        assert result.user_id == "VERIFIED-USER"
        # Other defaults stay intact.
        assert result.org_id == "org1"

    def test_full_override(self):
        result = allow_verify_result(
            user_id="u-1",
            org_id="o-1",
            org_slug="acme",
            evidence="membership",
        )
        assert result.user_id == "u-1"
        assert result.org_id == "o-1"
        assert result.org_slug == "acme"
        assert result.evidence == "membership"
