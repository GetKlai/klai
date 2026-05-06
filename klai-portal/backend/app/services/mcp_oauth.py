"""MCP OAuth 2.1 authorization-server logic — SPEC-MCP-AUTH-001.

This module bundles the full OAuth surface for ``mcp.getklai.com``:

- Dynamic Client Registration (DCR, RFC 7591) with strict redirect_uri allowlist
- Authorization-code + PKCE flow (RFC 6749 + RFC 7636 S256)
- Refresh-token rotation with replay-detection (RFC 6819 § 5.2.2.3)
- Bearer-token verification with Redis cache + DB fallback
- Audience-binding via ``resource_uri`` column (RFC 8707)

The shapes returned from this module are deliberately Pydantic-free dataclasses
(or plain dicts where the wire-shape is dictated by the OAuth spec). The HTTP
layer in ``app/api/oauth.py`` validates wire input via Pydantic before
delegating here.

Token format:
- Access tokens: ``klai_mcp_<43 base64url chars>`` (32 random bytes).
- Refresh tokens: ``klai_mcp_rt_<43 base64url chars>``.
- Hashes: SHA-256 raw bytes (32 bytes), stored in ``LargeBinary(32)`` columns.
- Comparison: ``hmac.compare_digest`` only — never ``==`` (mechanical: ast-grep
  rule ``no-secret-eq-compare``).

Cache:
- Redis key ``mcp_token_verify:<hex-hash>`` → JSON ``VerifyResult``, TTL 60s.
- Cache-unavailable fails closed with HTTP 503 (mirrors ``identity_verify_cache``).

Audit emits:
- ``mcp_token.issued`` on successful ``/oauth/token``
- ``mcp_token.revoked`` on ``DELETE /api/me/mcp-tokens/{id}`` and on rotation
- ``mcp_token.refreshed`` on successful refresh-token grant
- ``oauth_client.registered`` on DCR success

Cross-service contract: see ``klai-libs/identity-assert/klai_identity_assert/
mcp_token_client.py`` for the matching client-side library.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import urllib.parse
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

import structlog
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.mcp_oauth import PortalMcpToken, PortalOAuthClient

logger = structlog.get_logger()

# ─── Constants ────────────────────────────────────────────────────────────

ACCESS_TOKEN_PREFIX = "klai_mcp_"
REFRESH_TOKEN_PREFIX = "klai_mcp_rt_"
SUPPORTED_SCOPES = frozenset({"mcp:knowledge"})
DEFAULT_SCOPE = "mcp:knowledge"
SUPPORTED_GRANT_TYPES = frozenset({"authorization_code", "refresh_token"})
SUPPORTED_RESPONSE_TYPES = frozenset({"code"})
SUPPORTED_PKCE_METHODS = frozenset({"S256"})
SUPPORTED_TOKEN_ENDPOINT_AUTH_METHODS = frozenset({"none"})
APPLICATION_TYPES = frozenset({"native", "web"})

# Cache key prefixes (Redis)
_CACHE_KEY_VERIFY = "mcp_token_verify:"
_CACHE_KEY_AUTH_REQUEST = "oauth:auth_request:"
_CACHE_KEY_AUTH_CODE = "oauth:auth_code:"
_CACHE_KEY_DCR_RATE = "oauth:dcr_rate:"
_CACHE_KEY_LAST_USED = "mcp_last_used:"

_CACHE_TTL_VERIFY_SECONDS = 60
_CACHE_TTL_AUTH_REQUEST_SECONDS = 600  # 10 min
_CACHE_TTL_AUTH_CODE_SECONDS = 60
_CACHE_TTL_LAST_USED_SECONDS = 60  # Rate-limit last_used_at writes to 1/min/token


# ─── Redirect URI allowlist (REQ-20 + A10) ────────────────────────────────

# Native MCP clients (Claude Desktop, Cursor) bind to a localhost port. The
# allowlist matches exactly two host literals; ports are wildcarded.
_NATIVE_HOSTS = frozenset({"localhost", "127.0.0.1"})

# Web MCP clients (ChatGPT custom connectors, Claude.ai). Hard-coded list,
# no subdomain wildcards (avoid attacker.openai.com.evil.com bypass — see
# research.md §11). HTTPS only.
_WEB_HOST_ALLOWLIST = frozenset(
    {
        "chat.openai.com",
        "chatgpt.com",
        "claude.ai",
    }
)


def is_redirect_uri_allowed(redirect_uri: str, application_type: str) -> bool:
    """Validate redirect_uri against the SPEC-MCP-AUTH-001 REQ-20 allowlist.

    Strict separation per A10: native = localhost only (HTTP allowed), web =
    pre-approved HTTPS hostnames only. URL parsing via stdlib
    ``urllib.parse.urlsplit`` — no regex (avoids wildcard exploits).
    """
    if application_type not in APPLICATION_TYPES:
        return False
    try:
        parts = urllib.parse.urlsplit(redirect_uri)
    except ValueError:
        return False
    if not parts.hostname:
        return False
    host = parts.hostname.lower()

    if application_type == "native":
        # Native: localhost or 127.0.0.1 over HTTP. Any port accepted.
        # (Claude Desktop / Cursor pick dynamic ports.)
        if parts.scheme != "http":
            return False
        return host in _NATIVE_HOSTS

    # application_type == "web": HTTPS only, host in allowlist.
    if parts.scheme != "https":
        return False
    return host in _WEB_HOST_ALLOWLIST


# ─── Token generation + hashing ───────────────────────────────────────────


def _hash_token(raw: str) -> bytes:
    """SHA-256 raw bytes (32 bytes) for DB lookup-key.

    Deliberately raw bytes (not hex) — saves 50% storage, makes
    ``hmac.compare_digest`` the only sensible comparison primitive.
    """
    return hashlib.sha256(raw.encode("utf-8")).digest()


def _new_access_token() -> str:
    return f"{ACCESS_TOKEN_PREFIX}{secrets.token_urlsafe(32)}"


def _new_refresh_token() -> str:
    return f"{REFRESH_TOKEN_PREFIX}{secrets.token_urlsafe(32)}"


def looks_like_access_token(raw: str) -> bool:
    """Return True iff `raw` matches the access-token prefix shape.

    Used by the knowledge-mcp dispatcher to branch between OAuth-token
    pad and the LibreChat internal-secret pad. Refresh-tokens are
    explicitly excluded — they should never be used as bearer credentials
    on knowledge-mcp.
    """
    return raw.startswith(ACCESS_TOKEN_PREFIX) and not raw.startswith(REFRESH_TOKEN_PREFIX)


# ─── PKCE (RFC 7636) ──────────────────────────────────────────────────────


def verify_pkce_s256(code_verifier: str, code_challenge: str) -> bool:
    """SHA-256(code_verifier) base64url-encoded should equal code_challenge.

    OAuth 2.1 mandates S256; ``plain`` is forbidden. We never accept anything
    other than S256 — REQ-13.
    """
    digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
    import base64

    expected = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return hmac.compare_digest(expected, code_challenge)


# ─── Verify result (cross-service contract) ───────────────────────────────


@dataclass(frozen=True, slots=True)
class VerifyResult:
    """Single response shape for ``POST /internal/mcp-token/verify``.

    Mirrors the existing ``IdentityVerifySuccess`` / ``IdentityVerifyDeny``
    union-shape so the calling library can switch on ``verified``.
    """

    verified: bool
    user_id: int | None = None
    org_id: int | None = None
    org_slug: str | None = None
    scopes: tuple[str, ...] = ()
    resource_uri: str | None = None
    cache_ttl_seconds: int = _CACHE_TTL_VERIFY_SECONDS
    reason: str | None = None  # populated only when verified=False

    def to_dict(self) -> dict[str, Any]:
        if self.verified:
            return {
                "verified": True,
                "user_id": self.user_id,
                "org_id": self.org_id,
                "org_slug": self.org_slug,
                "scopes": list(self.scopes),
                "resource_uri": self.resource_uri,
                "cache_ttl_seconds": self.cache_ttl_seconds,
            }
        return {"verified": False, "reason": self.reason or "unknown"}


# ─── Issuance ─────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class IssuedTokens:
    """Returned from ``issue_token_pair`` and forwarded to the client.

    The raw values are returned ONCE; subsequent reads only see hashes.
    """

    token_id: int
    access_token: str
    refresh_token: str
    expires_in: int  # seconds until access-token expiry
    refresh_expires_in: int  # seconds until refresh-token expiry


async def issue_token_pair(
    db: AsyncSession,
    *,
    org_id: int,
    user_id: int,
    client_db_id: int,
    scopes: list[str],
    resource_uri: str,
) -> IssuedTokens:
    """Mint a fresh access+refresh token pair, persist hashes, return raw values.

    Caller (``/oauth/token`` endpoint) is responsible for the surrounding
    audit-log emit (REQ-24) — this function is pure DB write + token mint.
    """
    access_token = _new_access_token()
    refresh_token = _new_refresh_token()
    now = datetime.now(UTC)
    expires_at = now + timedelta(days=settings.mcp_oauth_token_ttl_days)
    refresh_expires_at = now + timedelta(days=settings.mcp_oauth_refresh_ttl_days)

    row = PortalMcpToken(
        org_id=org_id,
        user_id=user_id,
        client_id=client_db_id,
        access_token_hash=_hash_token(access_token),
        refresh_token_hash=_hash_token(refresh_token),
        scopes=scopes,
        resource_uri=resource_uri,
        expires_at=expires_at,
        refresh_expires_at=refresh_expires_at,
    )
    db.add(row)
    await db.flush()
    await db.refresh(row)

    return IssuedTokens(
        token_id=row.id,
        access_token=access_token,
        refresh_token=refresh_token,
        expires_in=int((expires_at - now).total_seconds()),
        refresh_expires_in=int((refresh_expires_at - now).total_seconds()),
    )


# ─── Verification (Redis-cached + DB-fallback) ────────────────────────────


async def verify_access_token(
    db: AsyncSession,
    redis: Any,  # redis.asyncio.Redis (typed loosely to avoid import-cycle)
    *,
    raw_token: str,
    expected_resource: str,
) -> VerifyResult:
    """Validate an access token and return the verified identity tuple.

    Decision tree:

    1. Empty/malformed prefix → reason="invalid_format"
    2. Cache hit → return cached VerifyResult
    3. Cache-unavailable → fail-closed, reason="cache_unavailable"
       (caller MUST translate to HTTP 503; mirror /internal/identity/verify)
    4. DB lookup by hash (SHA-256 of raw_token):
       - no row → reason="unknown_token"
       - row.revoked_at IS NOT NULL → reason="token_revoked"
       - row.expires_at < now → reason="token_expired"
       - row.resource_uri != expected_resource → reason="audience_mismatch"
       - portal_user.status != 'active' → reason="user_inactive"
       - portal_org.provisioning_status in ('deprovisioning','deprovisioned')
         → reason="org_deprovisioning"
       - else → success
    5. Cache the success/deny decision for `_CACHE_TTL_VERIFY_SECONDS`
    """
    if not looks_like_access_token(raw_token):
        return VerifyResult(verified=False, reason="invalid_format")

    token_hash = _hash_token(raw_token)
    cache_key = f"{_CACHE_KEY_VERIFY}{token_hash.hex()}"

    # 1. Cache lookup
    try:
        cached_raw = await redis.get(cache_key)
    except Exception:
        # Redis unavailable — fail-closed (REQ-9 + AC-8).
        logger.warning("mcp_token_verify_cache_unavailable")
        return VerifyResult(verified=False, reason="cache_unavailable")

    if cached_raw is not None:
        try:
            cached = json.loads(cached_raw)
        except json.JSONDecodeError:
            cached = None
        if isinstance(cached, dict):
            if cached.get("verified"):
                return VerifyResult(
                    verified=True,
                    user_id=cached.get("user_id"),
                    org_id=cached.get("org_id"),
                    org_slug=cached.get("org_slug"),
                    scopes=tuple(cached.get("scopes", [])),
                    resource_uri=cached.get("resource_uri"),
                )
            return VerifyResult(verified=False, reason=cached.get("reason", "unknown"))

    # 2. DB lookup — uses cross_org_session-style: portal_mcp_tokens is Cat-D,
    #    so we can't query without tenant context. Caller wraps this in a
    #    cross-org session that bypasses RLS via a privileged role, OR uses
    #    the server-side helper that sets app.current_org_id from the token-row
    #    AFTER finding the row by hash. We use the latter pattern below by
    #    issuing a SECURITY-DEFINER-style query through the verify role.
    #
    #    For the v0.2.1 scope: we run the query in a session where
    #    `_rls_current_org_id()` is intentionally NULL — that triggers the
    #    Cat-D strict policy and zero rows return. Solution: the verify
    #    endpoint calls this function inside a cross-org session created with
    #    `cross_org_session()` from app/core/database.py (set by caller).
    #    See app/api/internal.py for the wrapper.
    result = await db.execute(select(PortalMcpToken).where(PortalMcpToken.access_token_hash == token_hash).limit(1))
    token_row = result.scalar_one_or_none()
    if token_row is None:
        deny = VerifyResult(verified=False, reason="unknown_token")
        await _cache_verify_result(redis, cache_key, deny)
        return deny

    now = datetime.now(UTC)
    if token_row.revoked_at is not None:
        deny = VerifyResult(verified=False, reason="token_revoked")
        await _cache_verify_result(redis, cache_key, deny)
        return deny
    if token_row.expires_at < now:
        deny = VerifyResult(verified=False, reason="token_expired")
        await _cache_verify_result(redis, cache_key, deny)
        return deny
    if token_row.resource_uri != expected_resource:
        deny = VerifyResult(verified=False, reason="audience_mismatch")
        await _cache_verify_result(redis, cache_key, deny)
        return deny

    # 3. Resolve user/org freshness — user must be active, org not deprovisioning.
    from app.models.portal import PortalOrg, PortalUser

    user_row = await db.get(PortalUser, token_row.user_id)
    if user_row is None or user_row.status != "active":
        deny = VerifyResult(verified=False, reason="user_inactive")
        await _cache_verify_result(redis, cache_key, deny)
        return deny

    org_row = await db.get(PortalOrg, token_row.org_id)
    if org_row is None or org_row.provisioning_status in (
        "deprovisioning",
        "deprovisioned",
    ):
        deny = VerifyResult(verified=False, reason="org_deprovisioning")
        await _cache_verify_result(redis, cache_key, deny)
        return deny

    success = VerifyResult(
        verified=True,
        user_id=token_row.user_id,
        org_id=token_row.org_id,
        org_slug=org_row.slug,
        scopes=tuple(token_row.scopes or [DEFAULT_SCOPE]),
        resource_uri=token_row.resource_uri,
    )
    await _cache_verify_result(redis, cache_key, success)
    return success


async def _cache_verify_result(redis: Any, cache_key: str, result: VerifyResult) -> None:
    """Best-effort cache write. Redis failures are logged, never propagated."""
    try:
        await redis.set(
            cache_key,
            json.dumps(result.to_dict()),
            ex=_CACHE_TTL_VERIFY_SECONDS,
        )
    except Exception as exc:  # pragma: no cover — defensive
        logger.warning("mcp_token_verify_cache_set_failed", error=str(exc))


async def invalidate_token_cache(redis: Any, raw_or_hash: str | bytes) -> None:
    """Drop the Redis verify-cache entry for a token.

    Accepts either the raw token (will be hashed) or the already-hex-
    encoded hash. Used after revoke / refresh-rotation so the previous
    decision is purged within ~1s instead of waiting for TTL expiry (REQ-22).
    """
    if isinstance(raw_or_hash, str) and not raw_or_hash.startswith(ACCESS_TOKEN_PREFIX):
        # Already a hex-hash
        hash_hex = raw_or_hash
    elif isinstance(raw_or_hash, bytes):
        hash_hex = raw_or_hash.hex()
    else:
        hash_hex = _hash_token(raw_or_hash).hex()
    try:
        await redis.delete(f"{_CACHE_KEY_VERIFY}{hash_hex}")
    except Exception as exc:  # pragma: no cover
        logger.warning("mcp_token_verify_cache_invalidate_failed", error=str(exc))


# ─── Refresh-token rotation (RFC 6819 + REQ-26) ───────────────────────────


@dataclass(frozen=True, slots=True)
class RefreshOutcome:
    """Either a fresh token-pair or a security-policy revocation event."""

    success: IssuedTokens | None = None
    failure_reason: str | None = None
    revoked_chain: bool = False  # True when replay-detection triggered mass-revoke


async def refresh_access_token(
    db: AsyncSession,
    redis: Any,
    *,
    raw_refresh_token: str,
    expected_resource: str,
) -> RefreshOutcome:
    """Exchange a refresh-token for a new access+refresh pair, with rotation.

    Replay-detection: if the supplied refresh-token's hash matches a row that
    is already revoked (``revoked_at IS NOT NULL`` AND ``replaced_by_token_id
    IS NOT NULL``), revoke the entire ``(client_id, user_id)`` token-set.
    See SPEC-MCP-AUTH-001 REQ-26.
    """
    if not raw_refresh_token.startswith(REFRESH_TOKEN_PREFIX):
        return RefreshOutcome(failure_reason="invalid_grant")

    refresh_hash = _hash_token(raw_refresh_token)
    result = await db.execute(select(PortalMcpToken).where(PortalMcpToken.refresh_token_hash == refresh_hash).limit(1))
    row = result.scalar_one_or_none()
    if row is None:
        return RefreshOutcome(failure_reason="invalid_grant")

    # Replay-detection: a revoked-but-rotated token was just presented.
    if row.revoked_at is not None and row.replaced_by_token_id is not None:
        await _revoke_chain(db, redis, client_db_id=row.client_id, user_id=row.user_id)
        return RefreshOutcome(failure_reason="invalid_grant", revoked_chain=True)

    if row.revoked_at is not None:
        return RefreshOutcome(failure_reason="invalid_grant")

    now = datetime.now(UTC)
    if row.refresh_expires_at is None or row.refresh_expires_at < now:
        return RefreshOutcome(failure_reason="invalid_grant")
    if row.resource_uri != expected_resource:
        return RefreshOutcome(failure_reason="invalid_grant")

    # Mint fresh pair
    new_pair = await issue_token_pair(
        db,
        org_id=row.org_id,
        user_id=row.user_id,
        client_db_id=row.client_id,
        scopes=list(row.scopes or [DEFAULT_SCOPE]),
        resource_uri=row.resource_uri,
    )

    # Mark old as revoked + linked to new
    row.revoked_at = now
    row.replaced_by_token_id = new_pair.token_id
    await db.flush()

    # Invalidate cache for the OLD access-token-hash (the new one is fresh,
    # nothing cached yet).
    await invalidate_token_cache(redis, row.access_token_hash)

    return RefreshOutcome(success=new_pair)


async def _revoke_chain(db: AsyncSession, redis: Any, *, client_db_id: int, user_id: int) -> None:
    """Revoke every active token for a (client_id, user_id) pair.

    Trip-wire on refresh-token replay (REQ-26). Sets ``revoked_at = NOW()``
    on every still-active row and invalidates the Redis cache for each
    access-token-hash.
    """
    now = datetime.now(UTC)
    result = await db.execute(
        select(PortalMcpToken).where(
            PortalMcpToken.client_id == client_db_id,
            PortalMcpToken.user_id == user_id,
            PortalMcpToken.revoked_at.is_(None),
        )
    )
    rows = result.scalars().all()
    if not rows:
        return

    await db.execute(
        update(PortalMcpToken)
        .where(
            PortalMcpToken.client_id == client_db_id,
            PortalMcpToken.user_id == user_id,
            PortalMcpToken.revoked_at.is_(None),
        )
        .values(revoked_at=now)
    )
    for row in rows:
        await invalidate_token_cache(redis, row.access_token_hash)
    logger.warning(
        "mcp_token_replay_chain_revoked",
        client_db_id=client_db_id,
        user_id=user_id,
        revoked_count=len(rows),
    )


# ─── DCR (Dynamic Client Registration, RFC 7591) ──────────────────────────


@dataclass(frozen=True, slots=True)
class RegisteredClient:
    """Subset of the DCR response shape returned to callers."""

    client_id: str
    client_name: str
    redirect_uris: list[str]
    application_type: Literal["native", "web"]
    grant_types: list[str]
    response_types: list[str]
    token_endpoint_auth_method: str
    scopes: list[str]


async def register_client(
    db: AsyncSession,
    *,
    client_name: str,
    redirect_uris: list[str],
    application_type: str,
    source_ip: str | None = None,
    grant_types: list[str] | None = None,
    response_types: list[str] | None = None,
) -> RegisteredClient:
    """Register a new OAuth client (DCR RFC 7591) with strict validation.

    Validation order:
    1. ``application_type`` must be 'native' or 'web' (REQ-13a).
    2. ``redirect_uris`` non-empty.
    3. Every redirect_uri matches the allowlist for the given application_type.
    4. ``grant_types`` subset of supported (default authorization_code + refresh).
    5. ``response_types`` subset of supported (default ['code']).

    Caller (``POST /oauth/register`` endpoint) handles per-IP rate-limit BEFORE
    calling this function (REQ-27). Audit emit is the caller's responsibility.
    """
    if application_type not in APPLICATION_TYPES:
        raise ValueError("invalid_request: application_type must be 'native' or 'web'")
    if not redirect_uris:
        raise ValueError("invalid_request: redirect_uris must not be empty")
    for uri in redirect_uris:
        if not is_redirect_uri_allowed(uri, application_type):
            raise ValueError(f"invalid_redirect_uri: {uri}")

    grant_types_final = grant_types or ["authorization_code", "refresh_token"]
    if not set(grant_types_final).issubset(SUPPORTED_GRANT_TYPES):
        raise ValueError("invalid_request: unsupported grant_types")
    response_types_final = response_types or ["code"]
    if not set(response_types_final).issubset(SUPPORTED_RESPONSE_TYPES):
        raise ValueError("invalid_request: unsupported response_types")

    client_id_str = secrets.token_urlsafe(16)  # 128 bits
    row = PortalOAuthClient(
        client_id=client_id_str,
        client_name=client_name[:255],
        redirect_uris=redirect_uris,
        grant_types=grant_types_final,
        response_types=response_types_final,
        token_endpoint_auth_method="none",  # noqa: S106 — RFC 7591 literal, not a password
        application_type=application_type,
        scopes=[DEFAULT_SCOPE],
        created_by_ip=source_ip,
    )
    db.add(row)
    await db.flush()

    return RegisteredClient(
        client_id=client_id_str,
        client_name=row.client_name,
        redirect_uris=list(redirect_uris),
        application_type=application_type,  # type: ignore[arg-type]
        grant_types=grant_types_final,
        response_types=response_types_final,
        token_endpoint_auth_method="none",  # noqa: S106 — RFC 7591 literal
        scopes=[DEFAULT_SCOPE],
    )


async def get_client_by_id(db: AsyncSession, client_id: str) -> PortalOAuthClient | None:
    """Lookup an OAuth client by its public client_id (URL-safe hash).

    Returns None when not found OR when soft-deleted. Callers must handle
    None explicitly — never raise an HTTP 5xx on a 404-class miss.
    """
    result = await db.execute(
        select(PortalOAuthClient).where(
            PortalOAuthClient.client_id == client_id,
            PortalOAuthClient.soft_deleted_at.is_(None),
        )
    )
    return result.scalar_one_or_none()


async def check_dcr_rate_limit(redis: Any, source_ip: str) -> bool:
    """Return True iff the source IP is under the per-hour DCR limit (REQ-27).

    Uses Redis INCR + EXPIRE atomically. On Redis failure: fail-closed (return
    False). Better than fail-open which would let a botnet pollute the table.
    """
    if not source_ip:
        # No IP — disallow. Caller should pass `unknown` or actual IP.
        return False
    key = f"{_CACHE_KEY_DCR_RATE}{source_ip}:{datetime.now(UTC).strftime('%Y%m%d%H')}"
    try:
        count = await redis.incr(key)
        if count == 1:
            await redis.expire(key, 3600)
    except Exception:
        return False
    return count <= settings.mcp_oauth_dcr_rate_limit_per_hour


# ─── Authorization-request store (Redis-backed) ───────────────────────────
#
# We store pending authorization requests + issued (single-use) auth-codes in
# Redis. The state-machine:
#
#   POST /oauth/authorize (initial GET)
#       → creates oauth:auth_request:<request_id>, redirect to consent UI
#   POST /oauth/authorize (consent submit, approve)
#       → marks request approved, mints auth-code → oauth:auth_code:<code>
#   POST /oauth/token (code-exchange)
#       → consumes auth_code (DEL), validates PKCE, mints token-pair
#
# The auth-request payload carries: client_id, redirect_uri, code_challenge,
# scopes[], state, resource. Approval adds user_id + org_id + approved_at.


@dataclass(frozen=True, slots=True)
class PendingAuthRequest:
    request_id: str
    client_id: str
    redirect_uri: str
    code_challenge: str
    code_challenge_method: str
    scopes: tuple[str, ...]
    state: str
    resource: str
    user_id: int | None
    org_id: int | None
    approved: bool


async def create_auth_request(
    redis: Any,
    *,
    client_id: str,
    redirect_uri: str,
    code_challenge: str,
    code_challenge_method: str,
    scopes: list[str],
    state: str,
    resource: str,
) -> str:
    """Persist a pending auth request and return the opaque request_id."""
    request_id = secrets.token_urlsafe(24)
    payload = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "code_challenge": code_challenge,
        "code_challenge_method": code_challenge_method,
        "scopes": scopes,
        "state": state,
        "resource": resource,
        "approved": False,
    }
    await redis.set(
        f"{_CACHE_KEY_AUTH_REQUEST}{request_id}",
        json.dumps(payload),
        ex=_CACHE_TTL_AUTH_REQUEST_SECONDS,
    )
    return request_id


async def fetch_auth_request(redis: Any, request_id: str) -> PendingAuthRequest | None:
    raw = await redis.get(f"{_CACHE_KEY_AUTH_REQUEST}{request_id}")
    if raw is None:
        return None
    data = json.loads(raw)
    return PendingAuthRequest(
        request_id=request_id,
        client_id=data["client_id"],
        redirect_uri=data["redirect_uri"],
        code_challenge=data["code_challenge"],
        code_challenge_method=data["code_challenge_method"],
        scopes=tuple(data.get("scopes", [DEFAULT_SCOPE])),
        state=data.get("state", ""),
        resource=data["resource"],
        user_id=data.get("user_id"),
        org_id=data.get("org_id"),
        approved=bool(data.get("approved", False)),
    )


async def approve_auth_request(
    redis: Any,
    request_id: str,
    *,
    user_id: int,
    org_id: int,
) -> str | None:
    """Mark an auth request as approved by the user and mint a one-time code.

    Returns the auth-code (43 base64url chars) or None if the request was
    expired / unknown.
    """
    pending = await fetch_auth_request(redis, request_id)
    if pending is None:
        return None
    code = secrets.token_urlsafe(32)
    code_payload = {
        "client_id": pending.client_id,
        "redirect_uri": pending.redirect_uri,
        "code_challenge": pending.code_challenge,
        "scopes": list(pending.scopes),
        "state": pending.state,
        "resource": pending.resource,
        "user_id": user_id,
        "org_id": org_id,
    }
    await redis.set(
        f"{_CACHE_KEY_AUTH_CODE}{code}",
        json.dumps(code_payload),
        ex=_CACHE_TTL_AUTH_CODE_SECONDS,
    )
    # Drop the auth-request — the code now carries the state.
    await redis.delete(f"{_CACHE_KEY_AUTH_REQUEST}{request_id}")
    return code


async def consume_auth_code(redis: Any, code: str) -> dict[str, Any] | None:
    """Atomically GET + DEL an auth code. Returns None on miss or replay."""
    raw = await redis.get(f"{_CACHE_KEY_AUTH_CODE}{code}")
    if raw is None:
        return None
    # DEL must happen before the caller observes the payload. If DEL fails,
    # we re-raise — caller treats as invalid_grant.
    await redis.delete(f"{_CACHE_KEY_AUTH_CODE}{code}")
    return json.loads(raw)


# ─── Revoke (user-initiated via /api/me/mcp-tokens/{id}) ──────────────────


async def revoke_token(db: AsyncSession, redis: Any, *, token_id: int, org_id: int, user_id: int) -> bool:
    """Set ``revoked_at = NOW()`` on a single token row, scoped by user+org.

    Returns True on success, False on not-found (caller maps to 404).
    """
    result = await db.execute(
        select(PortalMcpToken).where(
            PortalMcpToken.id == token_id,
            PortalMcpToken.org_id == org_id,
            PortalMcpToken.user_id == user_id,
        )
    )
    row = result.scalar_one_or_none()
    if row is None:
        return False
    if row.revoked_at is not None:
        # Already revoked — idempotent success.
        return True
    row.revoked_at = datetime.now(UTC)
    await db.flush()
    await invalidate_token_cache(redis, row.access_token_hash)
    return True


async def list_user_tokens(db: AsyncSession, *, org_id: int, user_id: int) -> list[PortalMcpToken]:
    """Return all non-soft-deleted tokens for the (org, user) pair."""
    result = await db.execute(
        select(PortalMcpToken)
        .where(
            PortalMcpToken.org_id == org_id,
            PortalMcpToken.user_id == user_id,
        )
        .order_by(PortalMcpToken.created_at.desc())
    )
    return list(result.scalars().all())


# ─── Last-used update (rate-limited fire-and-forget) ──────────────────────


async def maybe_update_last_used(db: AsyncSession, redis: Any, *, token_id: int, token_hash_hex: str) -> None:
    """Update ``last_used_at = NOW()`` at most once per minute per token (REQ-23).

    Call site: knowledge-mcp's verify endpoint, AFTER a successful verify.
    Rate-limit via Redis SETNX with 60s TTL.
    """
    rate_key = f"{_CACHE_KEY_LAST_USED}{token_hash_hex}"
    try:
        # SET NX EX — atomic check-and-set. Returns True on first write per minute.
        was_set = await redis.set(rate_key, "1", nx=True, ex=_CACHE_TTL_LAST_USED_SECONDS)
        if not was_set:
            return
    except Exception:
        # Redis down → skip the update. last_used_at will lag, no functional impact.
        return

    await db.execute(update(PortalMcpToken).where(PortalMcpToken.id == token_id).values(last_used_at=datetime.now(UTC)))
