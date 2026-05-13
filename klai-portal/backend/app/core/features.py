"""Feature derivation for SPEC-PORTAL-RBAC-001 + SPEC-PORTAL-EXTENSIONS-UNIFY-001.

Single source of truth for "which products does a given (role, plan,
platform_unlocked_features) combination grant".

SPEC-PORTAL-EXTENSIONS-UNIFY-001 (2026-05-12): the previous dual-track
gating (enabled_addons tenant-self-service + platform_unlocked_features
Klai-staff) has been unified onto `platform_unlocked_features`. The old
`ADDON_FEATURES` constant and `enabled_addons` parameter are gone. The
set of features that surface as user-facing **products** in
`derive_user_products` is exactly the subset declared in
`FEATURE_MIN_PROFILE` (so adding a feature there makes it a product;
adding a feature only to `_KNOWN_FEATURES` in admin/platform_unlocks.py
keeps it as a pure platform-gate without product semantics).

Three-concept model (Linear / Notion / Slack / GitHub / Auth0):
    workspace features = plan features OR platform-unlocked features
    user permissions   = profile rank
    feature x profile  = filter via FEATURE_MIN_PROFILE

There are no per-user feature flags. If a feature is enabled at workspace
level and the user's profile is high enough, they get it. Period.
"""

from app.core.profiles import PROFILE_RANK

# @MX:ANCHOR fan_in=2+ -- single source of truth for plan-included products.
# SPEC-PORTAL-PLAN-RENAME-001: keys MUST stay in sync with PLAN_LIMITS
# (core/plan_limits.py) and ALLOWED_PROFILES_PER_PLAN (core/profiles.py).
# The portal_orgs.plan CHECK constraint enforces this set at the DB level.
#
# Note: the slugs `chat` and `knowledge` are also feature-strings — that
# overlap is intentional. Keys in this dict are PLAN names; values are
# FEATURE name sets. The two namespaces never collide at a call site.
PLAN_FEATURES: dict[str, frozenset[str]] = {
    "free": frozenset(),
    "chat": frozenset({"chat", "knowledge"}),
    "knowledge": frozenset({"chat", "knowledge"}),
}

# Minimum profile rung required for each USER-FACING PRODUCT. A user's profile
# rank must be >= FEATURE_MIN_PROFILE[product] for the product to be granted.
#
# Features that exist in `_KNOWN_FEATURES` (admin/platform_unlocks.py) but NOT
# here are pure platform-gates: they unlock admin-only endpoints
# (`/admin/api-keys`, `/admin/widgets`, `/admin/mcps`) via
# `require_platform_unlocked(...)`, but they do NOT surface in
# `effective_products`. They are not "products" in the tenant-app sense.
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
def derive_user_products(role: str, plan: str, platform_unlocked_features: list[str]) -> set[str]:
    """Return the set of products a user with this combination has.

    plan_features        always granted to anyone in the org (modulo profile floor).
    unlocked_products    granted iff (a) the feature is in
                         ``platform_unlocked_features`` AND (b) the feature has a
                         ``FEATURE_MIN_PROFILE`` entry (i.e. is a user-facing
                         product, not a pure platform-gate) AND (c) the user's
                         profile rank >= ``FEATURE_MIN_PROFILE[feature]``.

    Features that exist in ``_KNOWN_FEATURES`` but lack a ``FEATURE_MIN_PROFILE``
    entry are pure platform-gates (widgets, custom_mcps, partner_api) — they
    unlock admin endpoints but do not surface as user-facing products here.

    Unknown plan -> empty plan_features. Unknown role -> rank -1, fails every
    FEATURE_MIN_PROFILE check, so user effectively has nothing. Both are safe
    fallbacks that deny rather than over-grant.
    """
    plan_features = set(PLAN_FEATURES.get(plan, frozenset()))
    caller_rank = PROFILE_RANK.get(role, -1)

    unlocked_products: set[str] = set()
    for feature in platform_unlocked_features:
        floor = FEATURE_MIN_PROFILE.get(feature)
        if floor is None:
            # Platform-gate only (widgets, custom_mcps, partner_api).
            # Not a user-facing product — skip.
            continue
        if caller_rank >= PROFILE_RANK.get(floor, -1):
            unlocked_products.add(feature)

    # Plan features still need to clear FEATURE_MIN_PROFILE — chat and
    # knowledge default to "personal" so this is a no-op for the current
    # plan list, but the symmetry matters if a future plan adds a feature
    # with a higher floor.
    plan_features = {
        f for f in plan_features if caller_rank >= PROFILE_RANK.get(FEATURE_MIN_PROFILE.get(f, "personal"), -1)
    }

    return plan_features | unlocked_products
