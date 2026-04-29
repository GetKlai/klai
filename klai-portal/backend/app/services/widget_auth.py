"""Widget authentication service.

SPEC-WIDGET-001 Task 2:
- generate_session_token: create HS256 JWT for widget chat sessions
- origin_allowed: exact origin validation (scheme + host + port)

SPEC-SEC-HYGIENE-001 REQ-24:
- _derive_tenant_key: HKDF-SHA256 derives a per-tenant 32-byte signing
  key from the master ``WIDGET_JWT_SECRET`` and the tenant slug. A leak
  of one tenant's derived key does NOT compromise other tenants. The
  master secret leak is still catastrophic — that is the asymmetric-
  signing migration's job (future SPEC); this narrows the blast radius
  in the meantime.
- generate_session_token / decode_session_token now take a tenant_slug.
- DEPLOY NOTE: rotating WIDGET_JWT_SECRET invalidates ALL live widget
  sessions (TTL = 1h). The HKDF derivation is deterministic per-tenant,
  so rotating only the master secret does NOT auto-rotate per-tenant
  keys — they all flip together. Coordinate with the partner-portal
  team before rotating in production.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import jwt
import structlog
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

logger = structlog.get_logger()

_SESSION_TTL_SECONDS = 3600  # 1 hour

# SPEC-SEC-HYGIENE-001 REQ-24.1: HKDF parameters. The salt is a fixed
# v1 marker so a future migration to v2 can flip the constant + bump
# the cache without the full asymmetric-signing rework.
_HKDF_SALT = b"klai-widget-jwt-v1"
_HKDF_LENGTH = 32  # 32 bytes — appropriate for HS256.


# @MX:NOTE: Cryptographic security boundary — HKDF-derived per-tenant signing key.
# @MX:SPEC: SPEC-SEC-HYGIENE-001 REQ-24.1 (HKDF-SHA256, master + slug -> 32-byte HS256 key).
#   Determinism is the invariant: same (master, slug) MUST yield byte-equal output, and
#   different slug or different master MUST yield different output. Changing the salt
#   (`_HKDF_SALT`) or the length silently invalidates every issued widget JWT.
def _derive_tenant_key(master_secret: str, tenant_slug: str) -> bytes:
    """SPEC-SEC-HYGIENE-001 REQ-24.1: HKDF-SHA256 per-tenant signing key.

    Inputs:
        master_secret: the raw ``settings.widget_jwt_secret`` string.
        tenant_slug: the tenant's ``portal_orgs.slug`` value (e.g. "voys").
            Slug is preferred over the integer ``org_id`` because it is
            stable across tenant-ID re-numbering scenarios and is already
            unique per the partial-unique-index on ``portal_orgs``.

    Output: 32-byte derived key for HS256 signing.

    Determinism: same (master, slug) → same key, every time. Different
    slug or different master → different key. A failed re-derivation
    surface (different bytes) is the security boundary that prevents
    forging tokens cross-tenant or after a master-secret rotation.
    """
    hkdf = HKDF(
        algorithm=hashes.SHA256(),
        length=_HKDF_LENGTH,
        salt=_HKDF_SALT,
        info=tenant_slug.encode("utf-8"),
    )
    return hkdf.derive(master_secret.encode("utf-8"))


def generate_session_token(
    wgt_id: str,
    org_id: int,
    kb_ids: list[int],
    secret: str,
    tenant_slug: str,
) -> str:
    """Generate a HS256-signed JWT session token for widget chat.

    # @MX:ANCHOR: Public widget session token entry point
    # @MX:REASON: Called from widget-config endpoint; claims control chat access.
    # SPEC-SEC-HYGIENE-001 REQ-24: signing key is HKDF-derived per tenant.

    Claims:
        wgt_id: widget identifier
        org_id: organisation integer id
        kb_ids: list of knowledge base ids the widget may access
        exp: expiry timestamp (UTC, 1 hour from now)

    Args:
        wgt_id: The widget_id string (e.g. wgt_abcdef...)
        org_id: Portal organisation integer id
        kb_ids: Knowledge base ids accessible by this widget
        secret: WIDGET_JWT_SECRET from settings — the master secret;
            the actual signing key is derived per-tenant via HKDF.
        tenant_slug: The tenant's ``portal_orgs.slug``; binds the JWT
            signature to a specific tenant (REQ-24.1).

    Returns:
        HS256-signed JWT string
    """
    now = datetime.now(UTC)
    exp = now + timedelta(seconds=_SESSION_TTL_SECONDS)

    payload = {
        "wgt_id": wgt_id,
        "org_id": org_id,
        "kb_ids": kb_ids,
        "exp": int(exp.timestamp()),
    }

    derived_key = _derive_tenant_key(secret, tenant_slug)
    return jwt.encode(payload, derived_key, algorithm="HS256")


def decode_session_token(token: str, master_secret: str, tenant_slug: str) -> dict:
    """Decode and validate a widget session token.

    Raises jwt.ExpiredSignatureError if expired.
    Raises jwt.InvalidSignatureError if the token was issued for a
    DIFFERENT tenant (REQ-24.5 — the canonical regression for the
    HKDF-per-tenant change).
    Raises jwt.InvalidTokenError (or other subclass) if otherwise invalid.

    Args:
        token: JWT string to decode
        master_secret: WIDGET_JWT_SECRET from settings
        tenant_slug: The tenant's ``portal_orgs.slug``; the signing key
            is re-derived from (master_secret, tenant_slug).

    Returns:
        Decoded payload dict
    """
    derived_key = _derive_tenant_key(master_secret, tenant_slug)
    return jwt.decode(token, derived_key, algorithms=["HS256"])


def origin_allowed(origin: str, allowed_origins: list[str]) -> bool:
    """Validate origin against allowed list.

    # @MX:ANCHOR: [AUTO] CORS origin gate — called for every widget request
    # @MX:REASON: Security boundary; must remain fail-closed (empty list → False)
    # @MX:SPEC: SPEC-WIDGET-002

    Supports two formats:
    - Exact match: "https://example.com" matches only that origin.
    - Wildcard subdomain: "https://*.example.com" matches any subdomain
      (e.g. https://app.example.com, https://test.example.com) but NOT
      the bare domain (https://example.com). List both if you need both.

    Trailing slashes are stripped before comparison.
    An empty allowed list always returns False (fail-closed).

    Args:
        origin: The Origin header value from the request
        allowed_origins: List of allowed origin strings from widget_config

    Returns:
        True if origin is in the allowed list, False otherwise
    """
    if not allowed_origins:
        return False

    normalised_origin = origin.rstrip("/")

    for allowed in allowed_origins:
        allowed = allowed.rstrip("/")

        # Wildcard subdomain: https://*.example.com
        if "://*." in allowed:
            # Extract the suffix after the wildcard (e.g. ".example.com")
            scheme_end = allowed.index("://")
            scheme = allowed[: scheme_end + 3]  # "https://"
            suffix = allowed[scheme_end + 4 :]  # "example.com" (after "*.")

            if normalised_origin.startswith(scheme) and normalised_origin.endswith(suffix):
                # Verify there's actually a subdomain (not just the bare domain)
                host_part = normalised_origin[len(scheme) :]
                if host_part != suffix and host_part.endswith(suffix):
                    return True
        elif normalised_origin == allowed:
            return True

    return False
