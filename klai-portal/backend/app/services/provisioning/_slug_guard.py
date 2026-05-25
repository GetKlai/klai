"""Slug-validation guard for provisioning functions.

REQ-18 (Finding C-3, SPEC-SEC-CROSS-TENANT-FOLLOWUP-001): every provisioning
function that consumes ``slug`` for container names, volume-mount paths,
Caddyfile content, Mongo db/user names, or Redis-key patterns MUST validate
the slug at the function boundary. The historical `_to_slug` helper only
runs at signup; a future caller bypassing it (admin endpoint, retry handler,
migration) opens path-traversal + Caddyfile-injection.

Pair: the same regex is enforced at the DB level via a ``CHECK CONSTRAINT``
on ``portal_orgs.slug`` so a row with a malformed slug cannot exist in the
first place (see alembic migration ``45b528904319``).

# @MX:ANCHOR fan_in=5
# @MX:REASON Used by every Docker/Caddy/Mongo/Redis call-site that consumes
#   a tenant slug; treat the regex as a security boundary, not as a hint.
# @MX:SPEC SPEC-SEC-CROSS-TENANT-FOLLOWUP-001 REQ-18
"""

from __future__ import annotations

import re

# Lowercase alphanum + hyphen. MUST start and end with alphanum. Max 64 chars.
# Identical to the regex enforced by the portal_orgs.slug CHECK CONSTRAINT.
_SAFE_SLUG_RE = re.compile(r"^[a-z0-9]([a-z0-9-]{0,62}[a-z0-9])?$")


def _assert_safe_slug(slug: str) -> None:
    """Raise ValueError when ``slug`` is not a safe tenant identifier.

    Idempotent: calling twice with the same valid slug is free; calling with
    a non-string or otherwise-invalid value raises immediately.
    """
    if not isinstance(slug, str) or not _SAFE_SLUG_RE.fullmatch(slug):
        raise ValueError(f"slug failed safe-slug validation: {slug!r}")


__all__ = ["_assert_safe_slug"]
