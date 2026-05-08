"""Feature derivation for SPEC-PORTAL-RBAC-001.

Single source of truth for "which products does a given (role, plan, enabled_addons)
combination grant". Replaces the per-user / per-group entitlement tables that
SPEC-PORTAL-PROFILES-001 Phase 2 set up but never produced an industry-standard
result.

Three-concept model (Linear / Notion / Slack / GitHub / Auth0):
    workspace features = plan features OR enabled add-ons
    user permissions   = profile rank
    feature x profile  = filter via FEATURE_MIN_PROFILE

There are no per-user feature flags. If a feature is enabled at workspace level
and the user's profile is high enough, they get it. Period.
"""

from app.core.profiles import PROFILE_RANK

# @MX:ANCHOR fan_in=2+ -- single source of truth for plan-included products.
# SPEC-PORTAL-RBAC-REFACTOR-001 Phase 1 (REQ-2): the legacy app.core.plans
# module was removed; PLAN_FEATURES is now the canonical mapping for both
# derive_user_products() and admin/settings.py::change_plan validation.
PLAN_FEATURES: dict[str, frozenset[str]] = {
    "free": frozenset(),
    "core": frozenset({"chat", "knowledge"}),
    "professional": frozenset({"chat", "knowledge"}),
    "complete": frozenset({"chat", "knowledge"}),
}

# @MX:ANCHOR fan_in=2+ -- canonical add-on registry.
ADDON_FEATURES: frozenset[str] = frozenset({"scribe", "docs"})

# Minimum profile rung required for each feature. A user's profile rank must
# be >= the rank of FEATURE_MIN_PROFILE[feature] for the feature to be granted.
#
# Sparring decision (SPEC-PORTAL-RBAC-001 v0.2.0): scribe/docs gate at
# `company`. Personal chat is the "only-my-own-work" rung — secretarial-style
# users who legitimately should not see meeting transcripts or org-wide docs.
FEATURE_MIN_PROFILE: dict[str, str] = {
    "chat": "personal",
    "knowledge": "personal",
    "scribe": "company",
    "docs": "company",
}


# @MX:ANCHOR fan_in=2+ -- pure derivation, called from get_effective_products
# and from tests. No DB access; signature is stable contract.
def derive_user_products(role: str, plan: str, enabled_addons: list[str]) -> set[str]:
    """Return the set of products a user with this (role, plan, enabled_addons) has.

    plan_features      always granted to anyone in the org (modulo profile floor).
    addon_features     granted iff (a) tenant has the add-on enabled AND
                       (b) user's profile rank >= FEATURE_MIN_PROFILE[addon].

    Unknown plan -> empty plan_features. Unknown role -> rank -1, fails every
    FEATURE_MIN_PROFILE check, so user effectively has nothing. Both are safe
    fallbacks that deny rather than over-grant.
    """
    plan_features = set(PLAN_FEATURES.get(plan, frozenset()))
    caller_rank = PROFILE_RANK.get(role, -1)

    addon_features: set[str] = set()
    for addon in enabled_addons:
        if addon not in ADDON_FEATURES:
            continue
        floor = FEATURE_MIN_PROFILE.get(addon)
        if floor is None:
            continue
        if caller_rank >= PROFILE_RANK.get(floor, -1):
            addon_features.add(addon)

    # Plan features still need to clear FEATURE_MIN_PROFILE — chat and
    # knowledge default to "personal" so this is a no-op for the current
    # plan list, but the symmetry matters if a future plan adds a feature
    # with a higher floor.
    plan_features = {
        f for f in plan_features if caller_rank >= PROFILE_RANK.get(FEATURE_MIN_PROFILE.get(f, "personal"), -1)
    }

    return plan_features | addon_features
