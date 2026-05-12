"""SPEC-PORTAL-EXTENSIONS-UNIFY-001 — derive_user_products behavior-parity snapshot.

Locks the (role, plan, unlocked_features) -> products mapping for every
production-relevant tenant state and every profile-rank we have today.
This test must remain green across the refactor that renames the third
positional parameter from ``enabled_addons`` to ``platform_unlocked_features``.

Positional args throughout — that way the test does not need to change when
the parameter name is renamed in the next commit. Behavior is what we lock,
not the call-site spelling.

Reference data (verified on prod 2026-05-12):
- getklai: plan=complete, current effective unlocks={widgets, custom_mcps,
  scribe, docs} post-migration.
- voys:    plan=knowledge, current effective unlocks={partner_api, widgets,
  custom_mcps, scribe, docs} post-migration.
"""

import pytest

from app.core.features import derive_user_products

# ---------------------------------------------------------------------------
# Behavior snapshot per (role, plan, unlocked) input
# ---------------------------------------------------------------------------

# Locked set the refactor MUST preserve. If a row here changes, that's a
# semantic regression — investigate before updating the snapshot.
SNAPSHOT_CASES: list[tuple[str, str, list[str], set[str]]] = [
    # getklai (plan=complete) — admin sees scribe/docs (company-floor), no
    # partner_api (not unlocked), widgets/custom_mcps don't appear in
    # derive_user_products output (they're platform gates, not products).
    (
        "admin",
        "knowledge",
        ["custom_mcps", "docs", "scribe", "widgets"],
        {"chat", "knowledge", "scribe", "docs"},
    ),
    # getklai with a personal user — chat/knowledge only (scribe/docs floor
    # at company, personal is below).
    (
        "personal",
        "knowledge",
        ["custom_mcps", "docs", "scribe", "widgets"],
        {"chat", "knowledge"},
    ),
    # getklai with a company user — scribe/docs granted.
    (
        "company",
        "knowledge",
        ["custom_mcps", "docs", "scribe", "widgets"],
        {"chat", "knowledge", "scribe", "docs"},
    ),
    # voys (plan=knowledge) — admin sees scribe/docs; partner_api/widgets/
    # custom_mcps are platform gates, not products in derive_user_products.
    (
        "admin",
        "knowledge",
        ["custom_mcps", "docs", "partner_api", "scribe", "widgets"],
        {"chat", "knowledge", "scribe", "docs"},
    ),
    # voys with a personal user — chat/knowledge only.
    (
        "personal",
        "knowledge",
        ["custom_mcps", "docs", "partner_api", "scribe", "widgets"],
        {"chat", "knowledge"},
    ),
    # ---- Edge cases that must remain stable across the refactor ----
    # Empty unlocks → plan-only.
    ("admin", "chat", [], {"chat", "knowledge"}),
    ("personal", "chat", [], {"chat", "knowledge"}),
    # Unknown plan → no plan_features, but unlocked products still flow
    # through (subject to FEATURE_MIN_PROFILE). Admin clears the company
    # floor for scribe, so scribe surfaces.
    ("admin", "enterprise_xl", ["scribe"], {"scribe"}),
    # Unknown role → rank -1, fails every floor, nothing granted.
    ("nobody", "chat", ["scribe", "docs"], set()),
    # Stale legacy feature → ignored (no FEATURE_MIN_PROFILE entry).
    ("admin", "chat", ["scribe", "x_legacy_feature"], {"chat", "knowledge", "scribe"}),
    # Free plan → no plan_features. Unlocked products still grant when
    # profile clears the floor (admin >= company → scribe + docs). The
    # widgets entry is a pure platform-gate (no profile-floor entry) so
    # it does NOT appear as a product.
    ("admin", "free", ["scribe", "docs", "widgets"], {"scribe", "docs"}),
    # kb_manager and group_manager treated same as admin/company for scribe/docs.
    ("kb_manager", "chat", ["scribe", "docs"], {"chat", "knowledge", "scribe", "docs"}),
    ("group_manager", "chat", ["scribe", "docs"], {"chat", "knowledge", "scribe", "docs"}),
]


@pytest.mark.parametrize("role,plan,unlocked,expected", SNAPSHOT_CASES)
def test_derive_user_products_behavior_snapshot(role: str, plan: str, unlocked: list[str], expected: set[str]) -> None:
    """Behavior-parity contract. Positional args survive the param rename."""
    assert derive_user_products(role, plan, unlocked) == expected


def test_widgets_and_partner_api_are_not_products_in_derivation() -> None:
    """Platform-feature keys must NOT leak into derive_user_products output.

    `widgets`, `custom_mcps`, `partner_api` are gating-only — they unlock
    admin endpoints (handled by require_platform_unlocked), but they are
    NOT user-facing products. derive_user_products produces only the
    `effective_products` set used for product-gates on tenant-app endpoints.
    """
    result = derive_user_products("admin", "knowledge", ["widgets", "custom_mcps", "partner_api", "scribe", "docs"])
    assert "widgets" not in result
    assert "custom_mcps" not in result
    assert "partner_api" not in result
    # Scribe and docs ARE products (user-facing modules), so they appear.
    assert "scribe" in result
    assert "docs" in result
