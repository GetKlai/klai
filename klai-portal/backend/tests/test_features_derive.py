"""SPEC-PORTAL-RBAC-001: derive_user_products parametrized matrix.

Pure-function tests on the canonical (role, plan, enabled_addons) -> products
derivation. No DB, no mocks; just the function contract.
"""

import pytest

from app.core.features import (
    ADDON_FEATURES,
    FEATURE_MIN_PROFILE,
    PLAN_FEATURES,
    derive_user_products,
)

# ---------------------------------------------------------------------------
# Plan + role matrix (no add-ons)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "role,plan,expected",
    [
        # free plan: nothing
        ("personal", "free", set()),
        ("admin", "free", set()),
        # core plan: chat + knowledge for everyone
        ("personal", "core", {"chat", "knowledge"}),
        ("company", "core", {"chat", "knowledge"}),
        ("kb_manager", "core", {"chat", "knowledge"}),
        ("group_manager", "core", {"chat", "knowledge"}),
        ("admin", "core", {"chat", "knowledge"}),
        # professional/complete same as core (current PLAN_FEATURES)
        ("admin", "professional", {"chat", "knowledge"}),
        ("admin", "complete", {"chat", "knowledge"}),
    ],
)
def test_plan_features_only(role: str, plan: str, expected: set[str]) -> None:
    assert derive_user_products(role, plan, []) == expected


# ---------------------------------------------------------------------------
# Add-on threshold gating: scribe/docs floor at "company"
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "role,enabled_addons,expected_added",
    [
        # personal: addons enabled at tenant but not granted
        ("personal", ["scribe"], set()),
        ("personal", ["docs"], set()),
        ("personal", ["scribe", "docs"], set()),
        # company: addons granted
        ("company", ["scribe"], {"scribe"}),
        ("company", ["docs"], {"docs"}),
        ("company", ["scribe", "docs"], {"scribe", "docs"}),
        # kb_manager and above: same as company (addon FLOOR is "company")
        ("kb_manager", ["scribe", "docs"], {"scribe", "docs"}),
        ("group_manager", ["scribe", "docs"], {"scribe", "docs"}),
        ("admin", ["scribe", "docs"], {"scribe", "docs"}),
    ],
)
def test_addon_threshold(role: str, enabled_addons: list[str], expected_added: set[str]) -> None:
    base = {"chat", "knowledge"}
    result = derive_user_products(role, "core", enabled_addons)
    assert result == base | expected_added


def test_disabled_addon_not_granted_even_to_admin() -> None:
    assert derive_user_products("admin", "core", []) == {"chat", "knowledge"}
    assert "scribe" not in derive_user_products("admin", "core", [])


def test_unknown_addon_in_enabled_list_ignored() -> None:
    # If a stale db row has e.g. "x_legacy_feature" the derivation drops it.
    result = derive_user_products("admin", "core", ["scribe", "x_legacy_feature"])
    assert result == {"chat", "knowledge", "scribe"}


# ---------------------------------------------------------------------------
# Defensive cases: unknown plan, unknown role
# ---------------------------------------------------------------------------


def test_unknown_plan_returns_empty() -> None:
    assert derive_user_products("admin", "enterprise_xl", []) == set()


def test_unknown_role_grants_nothing_with_addons() -> None:
    # Unknown role -> rank -1, fails every FEATURE_MIN_PROFILE check.
    assert derive_user_products("nobody", "core", ["scribe"]) == set()


def test_empty_enabled_addons_treated_as_no_addons() -> None:
    assert derive_user_products("admin", "core", []) == {"chat", "knowledge"}


# ---------------------------------------------------------------------------
# Constants surface (catches accidental edits)
# ---------------------------------------------------------------------------


def test_addon_features_set() -> None:
    assert ADDON_FEATURES == frozenset({"scribe", "docs"})


def test_plan_features_keys() -> None:
    assert set(PLAN_FEATURES.keys()) == {"free", "core", "professional", "complete"}


def test_feature_min_profile_keys() -> None:
    # Every PLAN_FEATURES product and every ADDON_FEATURE must declare a floor.
    declared = set(FEATURE_MIN_PROFILE.keys())
    plan_products: set[str] = set()
    for s in PLAN_FEATURES.values():
        plan_products |= s
    expected = plan_products | set(ADDON_FEATURES)
    assert declared >= expected, f"missing floors for: {expected - declared}"


def test_addon_floor_company() -> None:
    # SPEC sparring decision #1: scribe/docs gate at "company".
    assert FEATURE_MIN_PROFILE["scribe"] == "company"
    assert FEATURE_MIN_PROFILE["docs"] == "company"
