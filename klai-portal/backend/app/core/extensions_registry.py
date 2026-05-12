"""Single registry for tenant extensions — SPEC-PORTAL-EXTENSIONS-UNIFY-001.

One source of truth for:

- ``KNOWN_FEATURES`` — the set of platform-managed feature keys that may
  appear in ``portal_orgs.platform_unlocked_features``. Validated by every
  write path (admin/platform_unlocks PATCH, admin/extensions GET context).
- ``EXTENSION_LABELS`` / ``EXTENSION_DESCRIPTIONS`` — user-facing strings
  surfaced by ``GET /api/admin/extensions`` so the frontend renders a
  consistent list across tenants.

Adding a new platform-managed feature: add the key here AND give it a
``FEATURE_MIN_PROFILE`` entry in ``app/core/features.py`` if it is a
user-facing product. Features without a ``FEATURE_MIN_PROFILE`` entry
remain platform-gates only (no surface in ``derive_user_products``).
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


EXTENSION_LABELS: dict[str, str] = {
    "partner_api": "API keys",
    "widgets": "Chat-widgets",
    "custom_mcps": "Custom MCP servers",
    "scribe": "Scribe — meeting-transcripties",
    "docs": "Docs — gedeelde KBs",
}


EXTENSION_DESCRIPTIONS: dict[str, str] = {
    "partner_api": "Programmatische toegang via pk_live_* API-keys.",
    "widgets": "Embed chat-widget op klant-website.",
    "custom_mcps": "Eigen Model Context Protocol servers koppelen.",
    "scribe": "Automatische meeting-transcriptie.",
    "docs": "Documentatie-KBs delen binnen de organisatie.",
}
