"""Single registry for tenant extensions — SPEC-PORTAL-EXTENSIONS-UNIFY-001.

One source of truth for the set of platform-managed feature keys that may
appear in ``portal_orgs.platform_unlocked_features``. Validated by every
write path (``admin/platform_unlocks.py::patch_platform_unlocks``) and
enumerated by every read path (``admin/extensions.py::list_extensions``).

User-facing labels and descriptions used to live here too. They moved to
the frontend (Paraglide i18n) so the backend stays language-agnostic and
EN/NL switching works without a server round-trip.

Adding a new platform-managed feature:

1. Add the key here.
2. If it surfaces as a user-facing product (sidebar item / page), give it
   a ``FEATURE_MIN_PROFILE`` entry in ``app/core/features.py``.
3. Add ``admin_extension_{key}_label`` + ``admin_extension_{key}_description``
   Paraglide messages in ``klai-portal/frontend/messages/{nl,en}.json``.

The drift-guard
``test_features_derive.py::test_known_features_consistent_with_feature_min_profile``
fails CI if (1) and (2) get out of sync.
"""

from __future__ import annotations

# @MX:ANCHOR fan_in=2+ — canonical allow-list for tenant extensions.
# Imported by admin/platform_unlocks.py (PATCH validation) and
# admin/extensions.py (GET enumeration). Don't introduce a second copy.
KNOWN_FEATURES: frozenset[str] = frozenset(
    {
        "partner_api",
        "widgets",
        "custom_mcps",
        "scribe",
        "docs",
    }
)

# Subset of KNOWN_FEATURES whose entries are also user-facing products
# (i.e. show up in derive_user_products output, gated by FEATURE_MIN_PROFILE).
# The drift-guard test pins this to match the FEATURE_MIN_PROFILE keys.
PRODUCT_FEATURES: frozenset[str] = frozenset({"scribe", "docs"})
