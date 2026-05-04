"""Plan-to-product mapping for Klai subscription tiers.

SPEC-PORTAL-PROFILES-001 Phase 2 P2.2:
- ADDON_PRODUCTS: products that require explicit tenant-level enablement via
  portal_orgs.enabled_addons AND per-user/group entitlement. Not auto-granted
  by plan. Admin must toggle on via PATCH /api/admin/settings/addons.
- PLAN_PRODUCTS: products included with the plan (no add-on toggle needed).
  scribe and docs were moved out of plan products into ADDON_PRODUCTS.
"""

# @MX:NOTE: SPEC-PORTAL-PROFILES-001 Phase 2 — add-on products require
# BOTH tenant-level enable (portal_orgs.enabled_addons) AND user/group
# entitlement (portal_user_products / portal_group_products). Two-layer gate.
ADDON_PRODUCTS: frozenset[str] = frozenset({"scribe", "docs"})

PLAN_PRODUCTS: dict[str, list[str]] = {
    "free": [],
    "core": ["chat", "knowledge"],
    # scribe and docs are add-ons — not auto-included in any plan.
    # Tenant admin enables them via PATCH /api/admin/settings/addons.
    "professional": ["chat", "knowledge"],
    "complete": ["chat", "knowledge"],
}


def get_plan_products(plan: str) -> list[str]:
    """Return products for a plan. Returns [] for unknown plans (safe default)."""
    return PLAN_PRODUCTS.get(plan, [])
