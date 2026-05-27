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

import secrets
from datetime import UTC, datetime, timedelta
from urllib.parse import urlparse

import jwt
import structlog
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

logger = structlog.get_logger()

_SESSION_TTL_SECONDS = 3600  # 1 hour
_SESSION_TOKEN_KID_PREFIX = "org:"

# SPEC-SEC-HYGIENE-001 REQ-24.1: HKDF parameters. The salt is a fixed
# v1 marker so a future migration to v2 can flip the constant + bump
# the cache without the full asymmetric-signing rework.
_HKDF_SALT = b"klai-widget-jwt-v1"
_HKDF_LENGTH = 32  # 32 bytes — appropriate for HS256.


def session_token_key_id(org_id: int) -> str:
    """Return the JWT ``kid`` used to select the tenant signing key.

    The ``kid`` is intentionally only a routing hint. Callers must verify the
    JWT signature with the selected key and then compare the verified
    ``org_id`` claim against the org selected by this key id.
    """
    return f"{_SESSION_TOKEN_KID_PREFIX}{org_id}"


def get_unverified_session_token_key_id(token: str) -> str | None:
    """Read the JWT ``kid`` header without reading unverified claims."""
    header = jwt.get_unverified_header(token)
    kid = header.get("kid")
    if kid is None:
        return None
    if not isinstance(kid, str):
        raise jwt.InvalidTokenError("Invalid widget JWT kid header")
    return kid


def org_id_from_session_token_key_id(kid: str) -> int | None:
    """Parse a widget JWT ``kid`` into a portal org id."""
    if not kid.startswith(_SESSION_TOKEN_KID_PREFIX):
        return None
    raw_org_id = kid[len(_SESSION_TOKEN_KID_PREFIX) :]
    if not raw_org_id.isdecimal():
        return None
    org_id = int(raw_org_id)
    return org_id if org_id > 0 else None


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
    *,
    is_preview: bool = False,
    session_id: str | None = None,
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
        jti: per-session nonce used to derive audit session keys

    Header:
        kid: key selector in ``org:<portal_org_id>`` format. The auth path
            uses this header to pick the tenant-specific HKDF key before
            signature verification, then checks the verified ``org_id`` claim
            still matches.

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

    # @MX:SPEC SPEC-SEC-CROSS-TENANT-FOLLOWUP-001 REQ-15 (Finding B-11)
    # The preview path mints a JWT with ``is_preview: true``; widget_audit
    # uses this to tag the resulting conversation row so admin probing does
    # not pollute the visitor-facing stats.
    payload = {
        "wgt_id": wgt_id,
        "org_id": org_id,
        "kb_ids": kb_ids,
        "exp": int(exp.timestamp()),
        "jti": session_id or secrets.token_urlsafe(24),
        "is_preview": is_preview,
    }

    derived_key = _derive_tenant_key(secret, tenant_slug)
    return jwt.encode(
        payload,
        derived_key,
        algorithm="HS256",
        headers={"kid": session_token_key_id(org_id), "typ": "JWT"},
    )


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


def origin_allowed(
    origin: str,
    allowed_origins: list[str],
    *,
    allow_any_origin: bool = False,
) -> bool:
    """Validate origin against allowed list.

    # @MX:ANCHOR: [AUTO] CORS origin gate — called for every widget request
    # @MX:REASON: Default-deny since REQ-2 (SPEC-SEC-CROSS-TENANT-FOLLOWUP-001):
    #             empty allowed_origins no longer grants open access. Callers
    #             must pass allow_any_origin=True (from widget_row.allow_any_origin)
    #             to restore open-world behaviour for explicitly opted-in widgets.
    # @MX:SPEC: SPEC-SEC-CROSS-TENANT-FOLLOWUP-001 REQ-2

    Supports two formats:
    - Exact match: "https://example.com" matches only that origin.
    - Wildcard subdomain: "https://*.example.com" matches any subdomain
      (e.g. https://app.example.com, https://test.example.com) but NOT
      the bare domain (https://example.com). List both if you need both.

    Trailing slashes are stripped before comparison.

    An empty allowed_origins list denies ALL origins unless allow_any_origin=True.
    Admins can either list specific domains in allowed_origins (lock-down mode) or
    set allow_any_origin=True (open mode — explicitly opted-in).  This replaces the
    pre-REQ-2 "empty list = open world" behaviour which silently defaulted every
    newly-created widget to a universal phishing vector.

    Args:
        origin: The Origin header value from the request
        allowed_origins: List of allowed origin strings from widget_config
        allow_any_origin: When True, bypass origin checking entirely (widget DB column)

    Returns:
        True if allow_any_origin is True OR origin is in the allowed list.
        False when allowed_origins is empty and allow_any_origin is False.
    """
    if allow_any_origin:
        return True

    if not allowed_origins:
        return False

    origin_parts = _parse_origin(origin)
    if origin_parts is None:
        return False

    for allowed in allowed_origins:
        allowed_parts = _parse_origin(allowed)
        if allowed_parts is None:
            continue

        # Wildcard subdomain: https://*.example.com
        if allowed_parts.host.startswith("*."):
            suffix = allowed_parts.host[2:]
            if (
                origin_parts.scheme == allowed_parts.scheme
                and _ports_match(origin_parts, allowed_parts)
                and origin_parts.host != suffix
                and origin_parts.host.endswith("." + suffix)
            ):
                return True
        elif (
            origin_parts.scheme == allowed_parts.scheme
            and origin_parts.host == allowed_parts.host
            and _ports_match(origin_parts, allowed_parts)
        ):
            return True

    return False


class _OriginParts:
    def __init__(self, *, scheme: str, host: str, port: int | None, explicit_port: bool) -> None:
        self.scheme = scheme
        self.host = host
        self.port = port
        self.explicit_port = explicit_port


def _parse_origin(value: str) -> _OriginParts | None:
    parsed = urlparse(value.rstrip("/"))
    if not parsed.scheme or not parsed.hostname:
        return None
    if parsed.path not in ("", "/") or parsed.params or parsed.query or parsed.fragment:
        return None
    try:
        port = parsed.port
    except ValueError:
        return None
    explicit_port = ":" in parsed.netloc.rsplit("@", 1)[-1]
    return _OriginParts(
        scheme=parsed.scheme.lower(),
        host=parsed.hostname.lower(),
        port=port,
        explicit_port=explicit_port,
    )


def _ports_match(origin: _OriginParts, allowed: _OriginParts) -> bool:
    if not origin.explicit_port and not allowed.explicit_port:
        return True
    return _effective_port(origin) == _effective_port(allowed)


def _effective_port(parts: _OriginParts) -> int | None:
    if parts.port is not None:
        return parts.port
    if parts.scheme == "https":
        return 443
    if parts.scheme == "http":
        return 80
    return None
