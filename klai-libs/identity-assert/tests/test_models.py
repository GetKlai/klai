"""Unit tests for VerifyResult dataclass."""

from __future__ import annotations

import dataclasses

import pytest

from klai_identity_assert import KNOWN_CALLER_SERVICES, VerifyResult


def test_allow_factory_sets_verified_true_with_canonical_identity() -> None:
    result = VerifyResult.allow(user_id="u-1", org_id="o-1", org_slug="acme", evidence="jwt")

    assert result.verified is True
    assert result.user_id == "u-1"
    assert result.org_id == "o-1"
    assert result.org_slug == "acme"
    assert result.evidence == "jwt"
    assert result.reason is None
    assert result.cached is False


def test_allow_factory_supports_membership_evidence() -> None:
    result = VerifyResult.allow(
        user_id="u-1", org_id="o-1", org_slug="acme", evidence="membership", cached=True
    )

    assert result.evidence == "membership"
    assert result.org_slug == "acme"
    assert result.cached is True


def test_deny_factory_sets_reason_and_clears_identity() -> None:
    result = VerifyResult.deny("no_membership")

    assert result.verified is False
    assert result.user_id is None
    assert result.org_id is None
    assert result.org_slug is None
    assert result.evidence is None
    assert result.reason == "no_membership"
    assert result.cached is False


def test_deny_supports_org_slug_mismatch_reason_code() -> None:
    # REQ-2.6 deny code added in Phase B.
    result = VerifyResult.deny("org_slug_mismatch")

    assert result.verified is False
    assert result.reason == "org_slug_mismatch"
    assert result.org_slug is None


def test_dataclass_is_frozen() -> None:
    result = VerifyResult.allow(user_id="u-1", org_id="o-1", org_slug="acme", evidence="jwt")

    with pytest.raises(dataclasses.FrozenInstanceError):
        result.verified = False  # type: ignore[misc]


def test_known_caller_services_includes_required_consumers() -> None:
    # SPEC REQ-1.2 reject list. Adding a caller without portal-side change
    # would be silent fail-closed at the library; this assertion guards
    # against accidental removals.
    assert "knowledge-mcp" in KNOWN_CALLER_SERVICES
    assert "scribe" in KNOWN_CALLER_SERVICES
    assert "retrieval-api" in KNOWN_CALLER_SERVICES
    assert "connector" in KNOWN_CALLER_SERVICES
    assert "mailer" in KNOWN_CALLER_SERVICES
