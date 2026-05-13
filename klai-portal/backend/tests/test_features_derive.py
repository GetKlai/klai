"""SPEC-PORTAL-RBAC-001 + SPEC-PORTAL-EXTENSIONS-UNIFY-001:
derive_user_products parametrized matrix.

Pure-function tests on the canonical (role, plan, platform_unlocked_features)
-> products derivation. No DB, no mocks; just the function contract.

NB: positional args throughout so the tests stay readable regardless of the
third-parameter rename history (enabled_addons -> platform_unlocked_features
on 2026-05-12).
"""

import pytest

from app.core.features import (
    FEATURE_MIN_PROFILE,
    PLAN_FEATURES,
    derive_user_products,
)

# ---------------------------------------------------------------------------
# Plan + role matrix (no platform-unlocks)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "role,plan,expected",
    [
        # free plan: nothing
        ("personal", "free", set()),
        ("admin", "free", set()),
        # chat plan: chat + knowledge product for every role (PLAN_LIMITS
        # ceilings differ; PLAN_FEATURES only emits the product set).
        ("personal", "chat", {"chat", "knowledge"}),
        ("company", "chat", {"chat", "knowledge"}),
        ("kb_manager", "chat", {"chat", "knowledge"}),
        ("group_manager", "chat", {"chat", "knowledge"}),
        ("admin", "chat", {"chat", "knowledge"}),
        # knowledge plan: same product set, full unlock via PLAN_LIMITS.
        ("admin", "knowledge", {"chat", "knowledge"}),
    ],
)
def test_plan_features_only(role: str, plan: str, expected: set[str]) -> None:
    assert derive_user_products(role, plan, []) == expected


# ---------------------------------------------------------------------------
# Platform-unlocked product threshold gating: scribe/docs floor at "company"
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "role,unlocked,expected_added",
    [
        # personal: products unlocked at tenant but not granted to personal-rank
        ("personal", ["scribe"], set()),
        ("personal", ["docs"], set()),
        ("personal", ["scribe", "docs"], set()),
        # company: products granted
        ("company", ["scribe"], {"scribe"}),
        ("company", ["docs"], {"docs"}),
        ("company", ["scribe", "docs"], {"scribe", "docs"}),
        # kb_manager and above: same as company (product FLOOR is "company")
        ("kb_manager", ["scribe", "docs"], {"scribe", "docs"}),
        ("group_manager", ["scribe", "docs"], {"scribe", "docs"}),
        ("admin", ["scribe", "docs"], {"scribe", "docs"}),
    ],
)
def test_product_threshold(role: str, unlocked: list[str], expected_added: set[str]) -> None:
    base = {"chat", "knowledge"}
    result = derive_user_products(role, "chat", unlocked)
    assert result == base | expected_added


def test_no_unlocks_admin_gets_plan_only() -> None:
    assert derive_user_products("admin", "chat", []) == {"chat", "knowledge"}
    assert "scribe" not in derive_user_products("admin", "chat", [])


def test_unknown_feature_in_unlocked_list_ignored() -> None:
    # If a stale db row has e.g. "x_legacy_feature" the derivation drops it.
    result = derive_user_products("admin", "chat", ["scribe", "x_legacy_feature"])
    assert result == {"chat", "knowledge", "scribe"}


def test_pure_platform_gate_does_not_appear_as_product() -> None:
    # SPEC-PORTAL-EXTENSIONS-UNIFY-001: features without FEATURE_MIN_PROFILE
    # entry are platform-gates (widgets/custom_mcps/partner_api), not products.
    # They never surface in derive_user_products output regardless of role.
    result = derive_user_products("admin", "knowledge", ["widgets", "custom_mcps", "partner_api"])
    assert result == {"chat", "knowledge"}
    assert "widgets" not in result
    assert "custom_mcps" not in result
    assert "partner_api" not in result


# ---------------------------------------------------------------------------
# Defensive cases: unknown plan, unknown role
# ---------------------------------------------------------------------------


def test_unknown_plan_returns_empty() -> None:
    assert derive_user_products("admin", "enterprise_xl", []) == set()


def test_unknown_role_grants_nothing_with_unlocks() -> None:
    # Unknown role -> rank -1, fails every FEATURE_MIN_PROFILE check.
    assert derive_user_products("nobody", "chat", ["scribe"]) == set()


def test_empty_unlocks_treated_as_plan_only() -> None:
    assert derive_user_products("admin", "chat", []) == {"chat", "knowledge"}


# ---------------------------------------------------------------------------
# Constants surface (catches accidental edits)
# ---------------------------------------------------------------------------


def test_plan_features_keys() -> None:
    assert set(PLAN_FEATURES.keys()) == {"free", "chat", "knowledge"}


def test_feature_min_profile_covers_plan_products() -> None:
    """Every product that comes from a plan MUST declare a profile-floor."""
    declared = set(FEATURE_MIN_PROFILE.keys())
    plan_products: set[str] = set()
    for s in PLAN_FEATURES.values():
        plan_products |= s
    assert declared >= plan_products, f"missing floors for plan-products: {plan_products - declared}"


def test_addon_products_floor_company() -> None:
    # SPEC sparring decision: scribe/docs gate at "company".
    assert FEATURE_MIN_PROFILE["scribe"] == "company"
    assert FEATURE_MIN_PROFILE["docs"] == "company"


def test_known_features_consistent_with_feature_min_profile() -> None:
    """SPEC-PORTAL-EXTENSIONS-UNIFY-001 drift-guard.

    A platform-feature that surfaces as a user-facing product MUST:
    - Be in ``KNOWN_FEATURES`` (so PATCH validation accepts it).
    - Have a ``FEATURE_MIN_PROFILE`` entry (so derive_user_products emits it).
    - Be in ``PRODUCT_FEATURES`` (the explicit subset).

    Adding a new product means editing both files; this test fails CI if
    one of the edits is missed, mechanically preventing the "silent skip
    in derive_user_products" failure mode.
    """
    from app.core.extensions_registry import KNOWN_FEATURES, PRODUCT_FEATURES

    # Plan-only products (chat / knowledge) are NOT platform-features and
    # therefore not in KNOWN_FEATURES.
    plan_only_products = {"chat", "knowledge"}
    product_floors = set(FEATURE_MIN_PROFILE.keys()) - plan_only_products

    # The product-floors must exactly match PRODUCT_FEATURES.
    assert product_floors == PRODUCT_FEATURES, (
        f"FEATURE_MIN_PROFILE (minus plan-products) = {sorted(product_floors)} "
        f"diverges from PRODUCT_FEATURES = {sorted(PRODUCT_FEATURES)}"
    )

    # And every product-feature must be a member of KNOWN_FEATURES.
    assert PRODUCT_FEATURES <= KNOWN_FEATURES, (
        f"PRODUCT_FEATURES not a subset of KNOWN_FEATURES: {sorted(PRODUCT_FEATURES - KNOWN_FEATURES)}"
    )
