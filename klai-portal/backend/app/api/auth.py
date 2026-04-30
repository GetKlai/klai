"""
Auth endpoints for the custom login UI.

POST /api/auth/login          -- email+password -> Zitadel session -> OIDC callback URL
POST /api/auth/totp-login     -- complete login with TOTP code (when user has 2FA)
POST /api/auth/sso-complete   -- reuse portal session to silently complete LibreChat OIDC
POST /api/auth/totp/setup     -- initiate TOTP registration (requires Bearer token)
POST /api/auth/totp/confirm   -- activate TOTP after scanning QR (requires Bearer token)

Logout of the `klai_sso` cookie is handled by `POST /api/auth/bff/logout` in
`auth_bff.py`, which clears the BFF session + CSRF cookies alongside the SSO
cookie in a single call. The former `POST /api/auth/logout` endpoint has been
removed — its behaviour lives on as `_clear_cookies()` inside auth_bff.

The authRequestId is issued by Zitadel when it redirects to the custom login UI:
  https://my.getklai.com/login?authRequest=<id>

The service account (zitadel_pat) must have the ``IAM_LOGIN_CLIENT`` role in Zitadel
for the finalize step to succeed.

SSO cookie mechanism
--------------------
When a user logs in, the portal encrypts their Zitadel session (session_id + session_token)
into the ``klai_sso`` cookie using Fernet symmetric encryption.  The cookie is scoped to
``.getklai.com`` so all subdomains can send it.

When LibreChat later opens an OIDC flow in an iframe, Zitadel redirects to
``my.getklai.com/login?authRequest=<id>``.  The login page sends the cookie to
``/api/auth/sso-complete``, which decrypts it and reuses the session to finalize the auth
request automatically -- no second password prompt.

This is fully stateless on the server side: no in-memory cache, survives restarts, and
scales horizontally.  Zitadel is the sole authority on session validity -- if the session
has expired there, ``finalize_auth_request`` will fail and the user sees the login form.
"""

import asyncio
import hashlib
import json
import logging
import secrets
import time
from functools import lru_cache
from typing import TYPE_CHECKING, Any, cast
from urllib.parse import quote, urlparse

import httpx
import redis.exceptions as redis_exc
import structlog
from cryptography.fernet import Fernet
from fastapi import APIRouter, Cookie, Depends, HTTPException, Query, Request, Response, status
from fastapi.responses import RedirectResponse
from fastapi.security import HTTPAuthorizationCredentials
from pydantic import BaseModel, EmailStr, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.bearer import bearer  # BFF Phase A4 — session-aware bearer shim
from app.core.config import settings
from app.core.database import AsyncSessionLocal, get_db
from app.models.portal import PortalOrg, PortalUser
from app.services import audit
from app.services.bff_session import SessionService
from app.services.events import emit_event
from app.services.pending_session import PendingSessionService
from app.services.redis_client import get_redis_pool
from app.services.request_ip import resolve_caller_ip_subnet
from app.services.zitadel import zitadel
from app.utils.response_sanitizer import sanitize_response_body  # SPEC-SEC-INTERNAL-001 REQ-4

if TYPE_CHECKING:
    import redis.asyncio as aioredis

# SPEC-SEC-SESSION-001 REQ-1.7: transient Redis failures that translate to
# fail-CLOSED HTTP 503. ConnectionError covers the unreachable case the SPEC
# names; TimeoutError covers slow-but-eventually-failing networks (still a
# brute-force ceiling lift if we let it through).
_REDIS_UNAVAILABLE_ERRORS = (redis_exc.ConnectionError, redis_exc.TimeoutError)

logger = logging.getLogger(__name__)
_slog = structlog.get_logger()

router = APIRouter(prefix="/api", tags=["auth"])

# ---------------------------------------------------------------------------
# Generic TTL cache
# ---------------------------------------------------------------------------


class TTLCache:
    """Simple in-memory cache with per-entry TTL. Single-instance only."""

    def __init__(self, ttl: int) -> None:
        self._ttl = ttl
        self._store: dict[str, dict] = {}

    def put(self, value: dict) -> str:
        """Store *value* and return an opaque token that can retrieve it."""
        token = secrets.token_urlsafe(32)
        self._store[token] = {**value, "expires_at": time.monotonic() + self._ttl}
        return token

    def get(self, token: str) -> dict | None:
        """Return the entry for *token*, or None if missing/expired."""
        entry = self._store.get(token)
        if not entry:
            return None
        if time.monotonic() > entry["expires_at"]:
            self._store.pop(token, None)
            return None
        return entry

    def pop(self, token: str) -> None:
        """Remove *token* from the cache (no-op if absent)."""
        self._store.pop(token, None)


# ---------------------------------------------------------------------------
# Stateless SSO cookie (Fernet-encrypted)
# The cookie value contains the Zitadel session_id + session_token, encrypted
# with a server-side key.  No server-side state is needed -- Zitadel is the
# authority on whether the session is still valid.
#
# SPEC-SEC-SESSION-001 REQ-3: fail-closed initialisation. Refuse to construct
# the cipher when ``SSO_COOKIE_KEY`` is empty rather than fall back to
# ``Fernet.generate_key()``. The fallback would mint a per-replica ephemeral
# key on every restart — cookies issued by replica A would be undecryptable on
# replica B, and outstanding cookies would silently invalidate on each deploy.
# Mirror of ``signup.py::_get_fernet``; the two stay in lock-step until a
# follow-up SPEC consolidates them into ``app/core/sso_crypto.py``.
# ---------------------------------------------------------------------------


# @MX:ANCHOR: SSO Fernet cipher accessor — fail-closed on missing SSO_COOKIE_KEY
# @MX:REASON: fan_in=4 (lifespan startup guard + _encrypt_sso + _decrypt_sso +
#   idp_signup_callback pending-cookie issue). A regression here breaks every
#   authenticated klai_sso session AND every social-signup pending cookie.
#   The removed ``Fernet.generate_key()`` fallback must never come back —
#   per-replica ephemeral keys would silently invalidate every outstanding
#   cookie on each restart.
# @MX:SPEC: SPEC-SEC-SESSION-001 REQ-3
@lru_cache(maxsize=1)
def _get_sso_fernet() -> Fernet:
    """Return the cached SSO Fernet cipher.

    Raises:
        RuntimeError: when ``settings.sso_cookie_key`` is empty or
            whitespace-only. The lifespan startup hook in ``app.main`` calls
            this once so a misconfigured deployment is caught at deploy time
            rather than on the first cookie operation.
    """
    key = settings.sso_cookie_key
    if not key or not key.strip():
        raise RuntimeError(
            "SSO_COOKIE_KEY is not set. "
            "Configure klai-infra SOPS-encrypted .env (klai-infra/core-01/.env.sops) "
            "before starting portal-api."
        )
    return Fernet(key.encode())


def _encrypt_sso(session_id: str, session_token: str) -> str:
    """Encrypt session credentials into an opaque cookie value."""
    payload = json.dumps({"sid": session_id, "stk": session_token}).encode()
    return _get_sso_fernet().encrypt(payload).decode()


def _decrypt_sso(cookie_value: str) -> dict | None:
    """Decrypt the SSO cookie.  Returns {"sid": ..., "stk": ...} or None."""
    try:
        payload = _get_sso_fernet().decrypt(cookie_value.encode())
        return json.loads(payload)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Pending TOTP state — Redis-backed (SPEC-SEC-SESSION-001 REQ-1)
#
# Pre-SPEC, ``_pending_totp = TTLCache(...)`` lived in process memory: each
# replica kept its own ``failures`` counter, so a 5-failure lockout was
# really an N*5 lockout when the proxy round-robinned across N replicas.
# Pending state now lives in Redis; the failure counter is incremented with
# atomic ``INCR`` so the ceiling is cross-replica consistent.
#
# Two keys per token (kept separate so the read-mostly state hash and the
# write-mostly counter do not contend on the same primitive):
#   ``totp_pending:<token>``           HASH  session_id, session_token, ua_hash, ip_subnet
#   ``totp_pending_failures:<token>``  STR   incremented; ``INCR`` returns the new count
#
# Both keys carry the same TTL set at create time. ``INCR`` does not refresh
# the TTL — the counter cannot outlive the session it counts against, so
# orphan counters cannot survive the state-hash expiry.
# ---------------------------------------------------------------------------
_TOTP_MAX_FAILURES = 5  # lockout threshold; matches the pre-SPEC ceiling
_TOTP_PENDING_KEY_PREFIX = "totp_pending:"
_TOTP_PENDING_FAILURES_PREFIX = "totp_pending_failures:"


async def _get_totp_redis_or_503(*, phase: str):
    """Return the Redis pool, or raise HTTP 503 (REQ-1.7 fail-closed).

    Different threat model from ``partner_rate_limit.check_rate_limit`` which
    fails OPEN: opening the door on TOTP would lift the brute-force ceiling
    entirely (the very bug we are closing). The ``phase`` kwarg is for
    operator forensics — ``totp_pending_redis_unavailable`` log records make
    it clear which Redis op failed without dumping the token.

    ``get_redis_pool`` does not raise — it lazily constructs the connection
    pool and returns ``None`` only when ``settings.redis_url`` is unset.
    The actual network failure surfaces on the FIRST per-call op (HSET /
    HGETALL / INCR / DEL); each helper wraps its own op in a
    ``_REDIS_UNAVAILABLE_ERRORS`` except.
    """
    pool = await get_redis_pool()
    if pool is None:
        _slog.error(
            "totp_pending_redis_unavailable",
            phase=phase,
            reason="redis_pool_none",
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authentication unavailable, please retry",
        )
    return pool


async def _totp_pending_create(
    *,
    session_id: str,
    session_token: str,
    ua_hash: str,
    ip_subnet: str,
) -> str:
    """Allocate a fresh ``temp_token`` and store the pending state in Redis.

    The three Redis writes (state HASH + state TTL + failure counter with
    its own TTL) execute in a single ``MULTI``/``EXEC`` pipeline so the
    state hash never lands without a TTL. The naive sequential form
    (``HSET`` then ``EXPIRE``) leaks one orphan hash per portal-api crash
    that lands in the microsecond window between the two commands;
    ``transaction=True`` closes that window.

    The opaque token returned to the client preserves the legacy
    ``secrets.token_urlsafe(32)`` contract — 256 bits of entropy, URL-safe.
    Raises HTTP 503 when Redis is unreachable (REQ-1.7).
    """
    pool = cast("aioredis.Redis", await _get_totp_redis_or_503(phase="create"))
    token = secrets.token_urlsafe(32)
    state_key = f"{_TOTP_PENDING_KEY_PREFIX}{token}"
    counter_key = f"{_TOTP_PENDING_FAILURES_PREFIX}{token}"
    ttl = settings.totp_pending_ttl_seconds
    try:
        async with pool.pipeline(transaction=True) as pipe:
            pipe.hset(
                state_key,
                mapping={
                    "session_id": session_id,
                    "session_token": session_token,
                    "ua_hash": ua_hash,
                    "ip_subnet": ip_subnet,
                },
            )
            pipe.expire(state_key, ttl)
            # ``SET ... EX`` initialises the counter at 0 with TTL in one round trip.
            pipe.set(counter_key, 0, ex=ttl)
            await pipe.execute()
    except _REDIS_UNAVAILABLE_ERRORS:
        _slog.error("totp_pending_redis_unavailable", phase="create", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authentication unavailable, please retry",
        ) from None
    return token


async def _totp_pending_get(token: str) -> dict[str, str] | None:
    """Look up the pending state. Returns the field map, or ``None`` if
    the token is unknown or already expired."""
    pool = cast("aioredis.Redis", await _get_totp_redis_or_503(phase="get"))
    try:
        # redis-py async stubs treat ``Redis.hgetall`` as the sync
        # ``dict[Unknown, Unknown]`` return type; the runtime override on
        # ``redis.asyncio.Redis`` is awaitable. ``hset`` had the same
        # stub gap pre-pipeline-refactor; after that refactor the call
        # is queued via ``pipeline()`` and pyright is happy. ``hgetall``
        # is the only remaining direct-call site that needs the
        # suppression.
        data = await pool.hgetall(  # pyright: ignore[reportGeneralTypeIssues]
            f"{_TOTP_PENDING_KEY_PREFIX}{token}"
        )
    except _REDIS_UNAVAILABLE_ERRORS:
        _slog.error("totp_pending_redis_unavailable", phase="get", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authentication unavailable, please retry",
        ) from None
    return data or None


async def _totp_pending_incr_failures(token: str) -> int:
    """Atomically increment + return the new failure count.

    REQ-1.4: a single-round-trip ``INCR`` is the atomicity primitive that
    prevents the cross-replica read-modify-write race a naive ``HGET + 1
    + HSET`` would re-introduce.
    """
    pool = cast("aioredis.Redis", await _get_totp_redis_or_503(phase="incr"))
    try:
        return await pool.incr(f"{_TOTP_PENDING_FAILURES_PREFIX}{token}")
    except _REDIS_UNAVAILABLE_ERRORS:
        _slog.error("totp_pending_redis_unavailable", phase="incr", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authentication unavailable, please retry",
        ) from None


async def _totp_pending_delete(token: str) -> None:
    """Drop both the state hash and the counter. Idempotent."""
    pool = cast("aioredis.Redis", await _get_totp_redis_or_503(phase="delete"))
    try:
        await pool.delete(
            f"{_TOTP_PENDING_KEY_PREFIX}{token}",
            f"{_TOTP_PENDING_FAILURES_PREFIX}{token}",
        )
    except _REDIS_UNAVAILABLE_ERRORS:
        _slog.error("totp_pending_redis_unavailable", phase="delete", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authentication unavailable, please retry",
        ) from None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


# SPEC-SEC-HYGIENE-001 REQ-20.2: in-process cache of active tenant slugs.
# Refreshed via the 60-second TTL OR explicit invalidate_tenant_slug_cache()
# from tenant create / soft-delete sites (signup.py, orchestrator.py,
# retry_provisioning.py). The TTL is the correctness floor — if an
# explicit invalidation site is missed, the cache self-heals within 60s.
_TENANT_SLUG_CACHE_TTL_SECONDS = 60
_tenant_slug_cache: set[str] | None = None
_tenant_slug_cache_expiry: float = 0.0
_tenant_slug_cache_lock: asyncio.Lock | None = None


async def _load_tenant_slugs_from_db() -> set[str]:
    """Read the active-slug set from portal_orgs (deleted_at IS NULL)."""
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(PortalOrg.slug).where(PortalOrg.deleted_at.is_(None)))
        return {row[0] for row in result.all() if row[0]}


async def _get_tenant_slug_allowlist() -> set[str]:
    """SPEC-SEC-HYGIENE-001 REQ-20.2: cached active-tenant-slug allowlist.

    Cache TTL: 60s. Cache miss triggers a DB read AND emits the structlog
    event ``tenant_slug_allowlist_cache_miss`` for observability. The
    in-process lock ensures only one DB read fires per cold-cache window.
    """
    global _tenant_slug_cache, _tenant_slug_cache_expiry, _tenant_slug_cache_lock
    now = time.time()
    if _tenant_slug_cache is not None and now < _tenant_slug_cache_expiry:
        return _tenant_slug_cache

    if _tenant_slug_cache_lock is None:
        _tenant_slug_cache_lock = asyncio.Lock()

    async with _tenant_slug_cache_lock:
        # Double-check after acquiring the lock — another coroutine may
        # have refreshed the cache while we were waiting.
        now = time.time()
        if _tenant_slug_cache is not None and now < _tenant_slug_cache_expiry:
            return _tenant_slug_cache

        logger.info("tenant_slug_allowlist_cache_miss")
        slugs = await _load_tenant_slugs_from_db()
        _tenant_slug_cache = slugs
        _tenant_slug_cache_expiry = time.time() + _TENANT_SLUG_CACHE_TTL_SECONDS
        return slugs


def invalidate_tenant_slug_cache() -> None:
    """SPEC-SEC-HYGIENE-001 REQ-20.2: explicit cache-invalidation hook.

    Call from sites that mutate the active-tenant-slug set:
    - signup.py after a fresh PortalOrg insert
    - provisioning/orchestrator.py after a soft-delete (deleted_at = now)
    - admin/retry_provisioning.py after un-soft-delete (deleted_at = None)

    The 60s TTL is the correctness floor; missing a site self-heals
    within a minute.
    """
    global _tenant_slug_cache, _tenant_slug_cache_expiry
    _tenant_slug_cache = None
    _tenant_slug_cache_expiry = 0.0


# SPEC-SEC-HYGIENE-001 REQ-20.5: every static system subdomain that has
# a Zitadel OIDC client registered with `*.{settings.domain}/...` redirect
# URIs. These hosts are operationally-pinned (created at infra deploy time,
# not per-tenant) and protected by Zitadel's primary `redirect_uri` exact
# match. This validator is defense-in-depth.
#
# MUST be kept in sync with the registered redirect_uris on every Zitadel
# OIDC app under the Klai Platform project. Verify quarterly via:
#
#   curl -sf "https://auth.getklai.com/management/v1/projects/<klai-platform>/apps/_search" \
#     -H "Authorization: Bearer $ZITADEL_ADMIN_PAT" \
#     -H "X-Zitadel-Orgid: <klai-org>" -X POST -d '{}' \
#   | jq '.result[].oidcConfig.redirectUris[]?' \
#   | xargs -I{} python -c "from urllib.parse import urlparse; \
#                            print(urlparse('{}').hostname.split('.')[0])"
#
# Any first-label not in the union of (this set + tenant-slug allowlist +
# `chat-{slug}` derived hosts) is a NEW class — extend the validator AND
# add a test BEFORE the new OIDC app ships.
_STATIC_SYSTEM_SUBDOMAINS: frozenset[str] = frozenset(
    {
        # Portal — login + dev variant
        # `my` (frontend_url) is added dynamically below from settings.
        "dev",
        # LibreChat
        "chat",
        "chat-dev",
        # Auth provider
        "auth",
        # Observability
        "grafana",
        "errors",
    }
)

# SPEC-SEC-HYGIENE-001 REQ-20.5: per-tenant host prefixes — first label
# of the form ``<prefix><slug>`` is accepted iff ``<slug>`` is in the
# active tenant allowlist. The set is hardcoded because each prefix
# represents a runtime architectural decision (a per-tenant subdomain
# pattern owned by a specific service) that requires a coordinated
# review to add. Currently:
#
#   chat-     LibreChat per-tenant instance (chat-{slug}.{domain})
#
# To add a new prefix: extend this set, extend the audit-test in
# tests/test_validate_callback_url.py, and verify the corresponding
# service registers the matching redirect_uri pattern in Zitadel. The
# nightly drift workflow (.github/workflows/zitadel-oidc-drift.yml)
# fires if a new host class appears in Zitadel without code update.
_TENANT_HOST_PREFIXES: frozenset[str] = frozenset({"chat-"})

# Sentinel passed to Zitadel ``/v2/sessions`` when ``find_user_by_email``
# returned None (user does not exist). Zitadel issues snowflake user
# IDs of 18 numeric digits; 14 zeros can never collide with a real ID.
# Using a syntactically-valid but unknown user_id keeps the timing
# close to the user-found path, preserving the uniform-401
# anti-enumeration property from SPEC-SEC-MFA-001 finding #12 /
# REQ-2.3 / REQ-2.5. See ``login`` handler call site for context.
_NONEXISTENT_USER_ID_SENTINEL: str = "00000000000000"


@lru_cache(maxsize=1)
def _system_callback_hosts() -> frozenset[str]:
    """SPEC-SEC-HYGIENE-001 REQ-20.4 + REQ-20.5: trusted callback hosts that
    are NOT tenant subdomains.

    The callback-URL allowlist must accept every legitimate hostname class
    that a Zitadel-issued ``callback_url`` can resolve to:

    - the bare apex (``settings.domain``) — used by the SPA itself
    - the canonical login domain (``urlparse(settings.frontend_url).hostname``)
      — Zitadel redirects through this host on every OIDC flow per
      SPEC-AUTH-008 / portal-backend.md ``FRONTEND_URL`` rule (REQ-20.4)
    - static system service subdomains (REQ-20.5) — see
      ``_STATIC_SYSTEM_SUBDOMAINS`` for the curated list
    - tenant subdomains — handled separately via
      ``_get_tenant_slug_allowlist`` (REQ-20.1)
    - per-tenant LibreChat subdomains (``chat-{slug}.{domain}``) — handled
      inline in ``_validate_callback_url`` (REQ-20.5)

    Derived from settings, cached for the process lifetime — these settings
    are deploy-immutable. Synchronous so it can be called from anywhere
    without an await. Tests that vary settings should call
    ``_system_callback_hosts.cache_clear()`` after monkeypatching.
    """
    hosts: set[str] = {settings.domain}
    fe_host = urlparse(str(settings.frontend_url)).hostname
    if fe_host:
        hosts.add(fe_host)
    # REQ-20.5: each static system subdomain combines with the bare apex
    # to produce one fully-qualified host. Doing the join once at boot
    # keeps the hot path on a single set lookup.
    for subdomain in _STATIC_SYSTEM_SUBDOMAINS:
        hosts.add(f"{subdomain}.{settings.domain}")
    return frozenset(hosts)


# @MX:ANCHOR: Trust boundary for OIDC callback URLs returned by Zitadel.
# @MX:REASON: fan_in=3 — called from login() pre-finalize, idp_callback,
#   and sso_complete after every successful finalize. Loosening the
#   trusted-host check (e.g. allowing wildcards or new domain suffixes)
#   opens an open-redirect across the entire auth surface. Coordinate
#   with frontend host config + Caddy redirect rules before changing.
# @MX:SPEC: SPEC-SEC-AUTH-COVERAGE-001 + SPEC-SEC-HYGIENE-001 REQ-20
#   (REQ-20.1 tenant-slug allowlist on top of .{domain} suffix check,
#   REQ-20.4 system-host bypass for FRONTEND_URL host, on top of
#   Zitadel's OIDC client redirect_uri validation)
async def _validate_callback_url(url: str) -> str:
    """Ensure callback_url points to a trusted host.

    Trusted classes, in evaluation order:

    1. ``localhost`` / ``127.0.0.1`` (REQ-20.3) — registered as valid
       redirect URIs in the Zitadel OIDC app for dev mode.
    2. System hosts (REQ-20.4) — the bare apex and the canonical login
       domain (``settings.frontend_url`` host). Both are non-tenant trusted
       targets. See ``_system_callback_hosts``.
    3. Tenant subdomains (REQ-20.1) — first subdomain label of any
       ``*.{settings.domain}`` host MUST appear in the active-tenant slug
       allowlist (``portal_orgs.slug WHERE deleted_at IS NULL``). This
       prevents dangling-DNS or abandoned-tenant subdomains from acting
       as open-redirect targets.

    Anything else returns 502 with a generic body (no information leak).
    Zitadel itself validates the registered ``redirect_uri`` list before
    issuing the callback URL, so this validator is defense-in-depth.
    """
    try:
        hostname = urlparse(url).hostname or ""
    except Exception:
        hostname = ""
    # REQ-20.3: localhost short-circuit preserved unchanged.
    if hostname in ("localhost", "127.0.0.1"):
        return url
    # REQ-20.4 + REQ-20.5: bare apex, FRONTEND_URL host, and static
    # system service subdomains (chat, chat-dev, dev, grafana, errors,
    # auth) — non-tenant trusted hosts.
    if hostname in _system_callback_hosts():
        return url
    trusted = settings.domain  # getklai.com
    # Anything outside .{domain} is rejected before we hit the slug allowlist.
    if not hostname.endswith(f".{trusted}"):
        logger.error("callback_url failed validation: %r", url)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Login failed, please try again later",
        )
    # REQ-20.1 + REQ-20.5: subdomain label MUST be in the active allowlist
    # — either as the bare slug (``voys.getklai.com``) OR as a per-tenant
    # prefixed host like ``chat-voys.getklai.com``.
    suffix = f".{trusted}"
    subdomain = hostname[: -len(suffix)]
    # Take the first label (e.g. "voys" from "voys.subsection.getklai.com").
    first_label = subdomain.split(".")[0] if subdomain else ""
    # REQ-20.5: strip a single per-tenant host prefix (``chat-``) before
    # the slug check. Strict single-level strip — only the FIRST matching
    # prefix is removed; "chat-chat-foo" still rejects because the result
    # is "chat-foo" which is itself a chat-prefixed label, not a slug.
    candidate_slug = first_label
    for prefix in _TENANT_HOST_PREFIXES:
        if first_label.startswith(prefix) and len(first_label) > len(prefix):
            candidate_slug = first_label[len(prefix) :]
            break
    allowed_slugs = await _get_tenant_slug_allowlist()
    if candidate_slug not in allowed_slugs:
        # SPEC-SEC-HYGIENE-001 REQ-20: structlog kwargs (NOT stdlib
        # ``extra={...}``) so the hostname survives the wrapper — this
        # lost the diagnostic field on the 2026-04-29 callback-allowlist
        # incident, which made the regression class harder to locate.
        _slog.error(
            "callback_url_subdomain_not_allowlisted",
            hostname=hostname,
            first_label=first_label,
            candidate_slug=candidate_slug,
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Login failed, please try again later",
        )
    return url


async def get_current_user_id(
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
) -> str:
    """FastAPI dependency: validate Bearer token and return the Zitadel user_id (sub)."""
    if settings.is_auth_dev_mode:
        return settings.auth_dev_user_id
    try:
        info = await zitadel.get_userinfo(credentials.credentials)
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token") from exc
    user_id = info.get("sub")
    if not user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
    return user_id


# @MX:ANCHOR: Single helper that mints the klai_sso cookie + finalizes
#   the OIDC auth request. fan_in=3 across login, totp_login, sso_complete.
# @MX:REASON: All three callers depend on this helper to (a) set
#   `klai_sso` consistently, (b) handle stale-auth-request 409, and
#   (c) call _validate_callback_url before redirecting. Changing cookie
#   attributes (max_age, samesite, domain) here shifts the contract for
#   every authenticated session. Coordinate with frontend SSO consumers
#   and the LibreChat iframe flow before touching.
# @MX:SPEC: SPEC-SEC-AUTH-COVERAGE-001 (predecessor: SPEC-SEC-MFA-001)
async def _finalize_and_set_cookie(
    response: Response,
    auth_request_id: str,
    session_id: str,
    session_token: str,
) -> "LoginResponse":
    """Finalize the Zitadel OIDC auth request, set the SSO cookie, and return a LoginResponse."""
    try:
        callback_url = await zitadel.finalize_auth_request(
            auth_request_id=auth_request_id,
            session_id=session_id,
            session_token=session_token,
        )
    except httpx.HTTPStatusError as exc:
        resp_text = sanitize_response_body(exc)
        # Auth request already handled (stale browser tab / back button / double-submit)
        if exc.response.status_code == 400 and "already been handled" in resp_text:
            logger.warning("finalize_auth_request: stale auth request %s", auth_request_id)
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="auth_request_stale",
            ) from exc
        logger.exception("finalize_auth_request failed %s: %s", exc.response.status_code, resp_text)
        if exc.response.status_code == 404:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Login request expired, please try again",
            ) from exc
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Login failed, please try again later",
        ) from exc

    response.set_cookie(
        key="klai_sso",
        value=_encrypt_sso(session_id, session_token),
        domain=f".{settings.domain}",
        httponly=True,
        secure=True,
        samesite="lax",
        max_age=settings.sso_cookie_max_age,
    )
    return LoginResponse(callback_url=await _validate_callback_url(callback_url))


# ---------------------------------------------------------------------------
# SPEC-SEC-MFA-001: MFA fail-closed enforcement helpers
# ---------------------------------------------------------------------------

_MFA_503_DETAIL = "Authentication service temporarily unavailable, please retry in a moment"
_MFA_503_HEADERS = {"Retry-After": "5"}


# @MX:ANCHOR: Single source of truth for the SPEC-SEC-MFA-001 fail-closed 503.
# @MX:REASON: fan_in=6 — both login() pre-auth raises and every fail-closed
#   branch in _resolve_and_enforce_mfa raise via this helper. Changing the
#   detail or Retry-After header here shifts contract for every fail-closed
#   path at once. Coordinate with frontend and the Grafana mfa_check_failed
#   alert annotation before touching.
# @MX:SPEC: SPEC-SEC-MFA-001
def _mfa_unavailable() -> HTTPException:
    """Return the 503 raised when MFA enforcement cannot complete (SPEC-SEC-MFA-001)."""
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail=_MFA_503_DETAIL,
        headers=_MFA_503_HEADERS,
    )


# @MX:ANCHOR: Single emit point for any structured auth-flow failure event.
# @MX:REASON: fan_in projected ≥20 across SPEC-SEC-MFA-001 and SPEC-SEC-AUTH-
#   COVERAGE-001. Every Zitadel/DB failure leg in login(), _resolve_and_enforce_mfa,
#   totp_login, totp_setup, totp_confirm, idp_intent, idp_callback,
#   password_reset, password_set, sso_complete, passkey_*, email_otp_*,
#   verify_email funnels through here. The kwargs produced (event, reason,
#   outcome, zitadel_status, email_hash, log_level + ad-hoc fields) are the
#   schema consumed by Grafana alerts and the mfa-check-failed runbook.
#   Adding a field is fine; renaming or removing the existing fields breaks
#   alerting and on-call queries.
# @MX:SPEC: SPEC-SEC-AUTH-COVERAGE-001 (predecessor: SPEC-SEC-MFA-001)
def _emit_auth_event(
    event: str,
    *,
    reason: str,
    outcome: str,
    level: str = "warning",
    email: str | None = None,
    email_hash: str | None = None,
    zitadel_status: int | None = None,
    **fields: Any,
) -> None:
    """Emit a structured auth-flow event via structlog (SPEC-SEC-AUTH-COVERAGE-001 REQ-5.1).

    Generalisation of ``_emit_mfa_check_failed``: the event name is a parameter,
    so any auth endpoint can emit a queryable failure event with the same
    schema as ``mfa_check_failed``.

    Privacy: pass either ``email`` (raw, sha256-hashed inside) or
    ``email_hash`` (pre-hashed). Plaintext email is NEVER emitted (REQ-5.2).

    Routing: ``request_id`` is auto-bound by structlog contextvars from
    ``LoggingContextMiddleware``; no manual propagation needed.
    """
    if email is not None and email_hash is None:
        email_hash = hashlib.sha256(email.lower().encode("utf-8")).hexdigest()
    log_method = getattr(_slog, level, _slog.warning)
    payload: dict[str, Any] = {
        "reason": reason,
        "outcome": outcome,
        "zitadel_status": zitadel_status,
        **fields,
    }
    if email_hash is not None:
        payload["email_hash"] = email_hash
    log_method(event, **payload)


def _emit_mfa_check_failed(
    *,
    reason: str,
    mfa_policy: str,
    outcome: str,
    email: str,
    zitadel_status: int | None = None,
    level: str = "warning",
) -> None:
    """Emit the SPEC-SEC-MFA-001 ``mfa_check_failed`` event.

    Thin backward-compatible wrapper around ``_emit_auth_event`` —
    preserved as a stable public-call surface so SPEC-SEC-MFA-001 callers
    remain unchanged. New auth endpoints SHOULD call ``_emit_auth_event``
    directly with their own event name.
    """
    _emit_auth_event(
        "mfa_check_failed",
        reason=reason,
        outcome=outcome,
        level=level,
        email=email,
        zitadel_status=zitadel_status,
        mfa_policy=mfa_policy,
    )


async def _resolve_and_enforce_mfa(
    *,
    zitadel_user_id: str,
    email: str,
    db: AsyncSession,
) -> "PortalUser | None":
    """Resolve mfa_policy for the calling user and enforce SPEC-SEC-MFA-001.

    Returns the ``PortalUser`` row for downstream audit context, or ``None``
    when the user is not yet provisioned in portal.

    Raises:
        HTTPException(503): Org fetch failed (cannot determine policy for a
            known portal user) OR Zitadel/connection failure during
            ``has_any_mfa`` under ``mfa_policy="required"``.
        HTTPException(403): ``mfa_policy="required"`` and the user has no MFA
            enrolled (existing behaviour, unchanged).

    Fail-open paths (login proceeds):
        - portal_user lookup raised — cannot map email to org; preserve
          provisioning grace (REQ-3.2 fail-open arm).
        - portal_user found but PortalOrg row is missing (orphan FK — deleted
          or soft-deleted org). We log + fail-open since this is data-integrity,
          not infrastructure failure, and a real user with a stale org should
          not be locked out without observability.
        - ``mfa_policy in {"optional", "recommended"}`` regardless of
          ``has_any_mfa`` outcome — orgs that have not opted into enforcement
          accept availability over security at login time (REQ-3).
    """
    portal_user: PortalUser | None = None
    db_failure: str | None = None
    try:
        portal_user = await db.scalar(select(PortalUser).where(PortalUser.zitadel_user_id == zitadel_user_id))
    except Exception:
        db_failure = "portal_user"
        logger.warning("portal_user lookup failed", exc_info=True)

    org: PortalOrg | None = None
    if portal_user is not None and db_failure is None:
        try:
            org = await db.get(PortalOrg, portal_user.org_id)
        except Exception:
            db_failure = "portal_org"
            logger.warning("portal_org lookup failed", exc_info=True)

    if db_failure == "portal_user":
        # REQ-3.2 fail-open arm: cannot map email to portal-org; if we 503'd
        # here every brand-new tenant before provisioning would be locked out.
        # mfa_policy="unresolved" per SPEC REQ-4.1 — the lookup itself failed,
        # so we cannot honestly claim the policy is "optional"; we are forcing
        # optional behaviour as the deliberate fail-open trade-off, but
        # operators triaging in Grafana need to distinguish that from a
        # genuinely-resolved optional policy.
        _emit_mfa_check_failed(
            reason="db_lookup_failed",
            mfa_policy="unresolved",
            outcome="fail-open",
            email=email,
            level="warning",
        )
        return portal_user  # always None on this branch

    if db_failure == "portal_org":
        # REQ-3.2 fail-closed arm: known portal_user but unresolvable org
        # policy — refuse rather than silently downgrade to optional.
        _emit_mfa_check_failed(
            reason="db_lookup_failed",
            mfa_policy="unresolved",
            outcome="503",
            email=email,
            level="error",
        )
        raise _mfa_unavailable()

    if portal_user is not None and org is None:
        # Orphan FK: portal_user.org_id points at a row that does not exist
        # (deleted org, soft-deleted row, migration rollback). Pre-existing
        # behaviour silently fell back to mfa_policy="optional" without any
        # signal — that hid data-integrity bugs from operators. We keep the
        # fail-open semantics (the user should still be able to log in) but
        # emit a warning so the orphan is observable in Grafana.
        # mfa_policy="unresolved" per SPEC REQ-4.1 — the org row is gone, so
        # the policy could not be resolved. The handler still applies optional
        # behaviour (no enforcement) as the documented fail-open path.
        _emit_mfa_check_failed(
            reason="db_lookup_failed",
            mfa_policy="unresolved",
            outcome="fail-open",
            email=email,
            level="warning",
        )

    mfa_policy = org.mfa_policy if org else "optional"
    if mfa_policy != "required":
        # REQ-3 / REQ-3.4: optional and recommended preserve fail-open.
        # has_any_mfa is short-circuited entirely under these policies.
        return portal_user

    try:
        user_has_mfa = await zitadel.has_any_mfa(zitadel_user_id)
    except httpx.HTTPStatusError as exc:
        _emit_mfa_check_failed(
            reason="has_any_mfa_5xx",
            mfa_policy="required",
            outcome="503",
            email=email,
            zitadel_status=exc.response.status_code,
            level="error",
        )
        raise _mfa_unavailable() from exc
    except httpx.RequestError as exc:
        _emit_mfa_check_failed(
            reason="has_any_mfa_5xx",
            mfa_policy="required",
            outcome="503",
            email=email,
            zitadel_status=None,
            level="error",
        )
        raise _mfa_unavailable() from exc
    except Exception as exc:
        # REQ-1.6: any unexpected exception type still fails closed under
        # required policy. Better a transient 503 than a silent bypass.
        _emit_mfa_check_failed(
            reason="unexpected",
            mfa_policy="required",
            outcome="503",
            email=email,
            zitadel_status=None,
            level="error",
        )
        raise _mfa_unavailable() from exc

    if not user_has_mfa:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="MFA required by your organization. Please set up two-factor authentication.",
        )

    return portal_user


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------


class LoginRequest(BaseModel):
    email: EmailStr
    password: str
    auth_request_id: str


class LoginResponse(BaseModel):
    # Normal login: callback_url is set, status = "ok"
    # TOTP required: status = "totp_required", temp_token is set
    callback_url: str | None = None
    status: str = "ok"
    temp_token: str | None = None


class TOTPLoginRequest(BaseModel):
    temp_token: str
    code: str
    auth_request_id: str


class SSOCompleteRequest(BaseModel):
    auth_request_id: str


class PasswordResetRequest(BaseModel):
    email: EmailStr


class PasswordSetRequest(BaseModel):
    user_id: str
    code: str
    new_password: str


class TOTPSetupResponse(BaseModel):
    uri: str
    secret: str


class TOTPConfirmRequest(BaseModel):
    code: str


class PasskeySetupResponse(BaseModel):
    passkey_id: str
    options: dict


class PasskeyConfirmRequest(BaseModel):
    passkey_id: str
    public_key_credential: dict
    passkey_name: str = "My passkey"


class EmailOTPConfirmRequest(BaseModel):
    code: str


class IDPIntentRequest(BaseModel):
    idp_id: str
    auth_request_id: str


class IDPIntentResponse(BaseModel):
    auth_url: str


_SUPPORTED_LOCALES = {"nl", "en"}


class IDPIntentSignupRequest(BaseModel):
    idp_id: str
    locale: str = "nl"

    @field_validator("locale")
    @classmethod
    def valid_locale(cls, v: str) -> str:
        return v if v in _SUPPORTED_LOCALES else "nl"


# Pending social signup cookie name — short-lived, Fernet-encrypted
_IDP_PENDING_COOKIE = "klai_idp_pending"
_IDP_PENDING_MAX_AGE = 600  # 10 minutes


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("/auth/password/reset", status_code=status.HTTP_204_NO_CONTENT)
async def password_reset(body: PasswordResetRequest) -> None:
    """Send a password reset email. Always returns 204 to prevent email enumeration.

    SPEC-SEC-AUTH-COVERAGE-001 REQ-3.1: every call (success OR fail) emits
    `audit.log_event(action="auth.password.reset")` so compliance can answer
    "who requested a password reset on date X". Failure paths additionally
    emit `password_reset_failed` events for ops alerting; the HTTP response
    stays 204 (anti-enumeration is preserved).
    """
    await audit.log_event(
        org_id=0,
        actor="anonymous",
        action="auth.password.reset",
        resource_type="user",
        resource_id="unknown",
        details={"email_hash": hashlib.sha256(body.email.lower().encode("utf-8")).hexdigest()},
    )

    try:
        user_id = await zitadel.find_user_id_by_email(body.email)
    except httpx.HTTPStatusError as exc:
        _slog.exception("find_user_id_by_email_failed", zitadel_status=exc.response.status_code)
        _emit_auth_event(
            "password_reset_failed",
            reason="zitadel_5xx",
            zitadel_status=exc.response.status_code,
            email=body.email,
            outcome="204",
            level="error",
        )
        return  # fail silently — 204 (REQ-3.3)

    if not user_id:
        _emit_auth_event(
            "password_reset_failed",
            reason="unknown_email",
            email=body.email,
            outcome="204",
            level="warning",
        )
        return  # unknown email — return 204 silently (REQ-3.2)

    try:
        await zitadel.send_password_reset(user_id)
    except httpx.HTTPStatusError as exc:
        _slog.exception("send_password_reset_failed", zitadel_status=exc.response.status_code)
        _emit_auth_event(
            "password_reset_failed",
            reason="zitadel_5xx",
            zitadel_status=exc.response.status_code,
            email=body.email,
            outcome="204",
            level="error",
        )
        return  # fail silently


@router.post("/auth/password/set", status_code=status.HTTP_204_NO_CONTENT)
async def password_set(body: PasswordSetRequest) -> None:
    """Complete a password reset using the code from the reset email.

    SPEC-SEC-AUTH-COVERAGE-001 REQ-3.4..3.6: emit `audit.log_event` on
    success and `password_set_failed` events on every failure leg.
    """
    try:
        await zitadel.set_password_with_code(body.user_id, body.code, body.new_password)
    except httpx.HTTPStatusError as exc:
        _slog.exception("set_password_with_code_failed", zitadel_status=exc.response.status_code)
        if exc.response.status_code in (400, 404, 410):
            _emit_auth_event(
                "password_set_failed",
                reason="expired_link" if exc.response.status_code == 410 else "invalid_code",
                zitadel_status=exc.response.status_code,
                actor_user_id=body.user_id,
                outcome="400",
                level="warning",
            )
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Link has expired or is invalid, request a new reset link",
            ) from exc
        _emit_auth_event(
            "password_set_failed",
            reason="zitadel_5xx",
            zitadel_status=exc.response.status_code,
            actor_user_id=body.user_id,
            outcome="502",
            level="error",
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Failed to set password, please try again later",
        ) from exc

    await audit.log_event(
        org_id=0,
        actor=body.user_id,
        action="auth.password.set",
        resource_type="user",
        resource_id=body.user_id,
        details={"reason": "set"},
    )


@router.post("/auth/login", response_model=LoginResponse)
async def login(
    body: LoginRequest,
    response: Response,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> LoginResponse:
    # 1a. Find Zitadel user by email — SPEC-SEC-MFA-001 REQ-2: split 4xx ↔ 5xx
    zitadel_user_id: str | None = None
    org_id_zitadel: str | None = None
    try:
        user_info = await zitadel.find_user_by_email(body.email)
        if user_info:
            zitadel_user_id, org_id_zitadel = user_info
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code >= 500:
            _emit_mfa_check_failed(
                reason="find_user_by_email_5xx",
                mfa_policy="unresolved",
                outcome="503",
                email=body.email,
                zitadel_status=exc.response.status_code,
                level="error",
            )
            raise _mfa_unavailable() from exc
        # 4xx: well-formed not-found / client error — treat as user_info=None
        # and continue to the password check (which will return 401 for an
        # unknown user). Closes finding #12 (REQ-2.3, REQ-2.5).
    except httpx.RequestError as exc:
        _emit_mfa_check_failed(
            reason="find_user_by_email_5xx",
            mfa_policy="unresolved",
            outcome="503",
            email=body.email,
            zitadel_status=None,
            level="error",
        )
        raise _mfa_unavailable() from exc

    # 1b. has_totp — UI-flag only; failure is fail-open (no enforcement
    # implication; user simply does not see the TOTP prompt). REQ-2.6 moves
    # this OUT of the find_user_by_email try so a TOTP outage never causes a
    # find_user_by_email-style 5xx escalation.
    has_totp = False
    if zitadel_user_id:
        try:
            has_totp = await zitadel.has_totp(zitadel_user_id, org_id_zitadel)
        except (httpx.HTTPStatusError, httpx.RequestError):
            # has_totp drives only the UI prompt; failure here is fail-open
            # (user falls through to password-only screen). We use structlog
            # explicitly because portal-logging-py rules require it for any
            # NEW log statement, and this catch is added by SPEC-SEC-MFA-001.
            _slog.warning("has_totp_check_failed", exc_info=True)
            has_totp = False

    # 2. Create a Zitadel session — pass the canonical Zitadel user_id
    # resolved in step 1a, NOT the raw user-typed email. Zitadel matches
    # `loginName` case-sensitively in /v2/sessions checks, so a user whose
    # stored loginName is `Steven@getklai.com` cannot sign in by typing
    # `steven@getklai.com` if we forward the typed value. The IGNORE_CASE
    # fix on `find_user_by_email` (commit 7e92e089) already gives us the
    # canonical user_id; we simply need to use it. When find returned None
    # (user not found), pass a syntactically-valid sentinel so Zitadel
    # returns 4xx and the handler emits the SAME uniform "Email address or
    # password is incorrect" 401 — the anti-enumeration pattern from
    # SPEC-SEC-MFA-001 finding #12 / REQ-2.3 / REQ-2.5.
    session_user_id = zitadel_user_id or _NONEXISTENT_USER_ID_SENTINEL
    try:
        session = await zitadel.create_session_with_password(session_user_id, body.password)
    except httpx.HTTPStatusError as exc:
        logger.exception("create_session failed %s: %s", exc.response.status_code, sanitize_response_body(exc))
        if exc.response.status_code in (400, 401, 404, 412):
            await audit.log_event(
                org_id=0,
                actor=zitadel_user_id or "unknown",
                action="auth.login.failed",
                resource_type="session",
                resource_id=zitadel_user_id or "unknown",
                details={"reason": "invalid_credentials"},
            )
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Email address or password is incorrect",
            ) from exc
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Login failed, please try again later",
        ) from exc

    # 2b. Enforce MFA policy — SPEC-SEC-MFA-001 (supersedes the previous
    # NEN 7510 REQ-SEC-001-08 implementation: fail-closed under required,
    # documented fail-open under optional).
    portal_user_for_mfa: PortalUser | None = None
    if zitadel_user_id:
        portal_user_for_mfa = await _resolve_and_enforce_mfa(
            zitadel_user_id=zitadel_user_id,
            email=body.email,
            db=db,
        )

    emit_event("login", user_id=zitadel_user_id, properties={"method": "password"})

    # Audit log: successful login (non-fatal -- must not block login)
    try:
        await audit.log_event(
            org_id=portal_user_for_mfa.org_id if portal_user_for_mfa else 0,
            actor=zitadel_user_id or "unknown",
            action="auth.login",
            resource_type="session",
            resource_id=zitadel_user_id or "unknown",
            details={"method": "password"},
        )
    except Exception:
        logger.warning("Audit log write failed for auth.login (non-fatal)", exc_info=True)

    # 3. If the user has TOTP, require a code before finalizing
    if has_totp:
        # SPEC-SEC-SESSION-001 REQ-1.1: store the pending state in Redis
        # (cross-replica atomic counter) and snapshot UA + IP-subnet so a
        # follow-up SPEC can add binding-on-consume without a Redis schema
        # migration. ``ua_hash`` reuses the SHA-256-hex helper from
        # ``BFFSessionService`` — same primitive as the BFF session-theft
        # detector, so two surfaces stay in lock-step.
        ua_hash = SessionService.hash_metadata(request.headers.get("user-agent"))
        ip_subnet = resolve_caller_ip_subnet(request)
        temp_token = await _totp_pending_create(
            session_id=session["sessionId"],
            session_token=session["sessionToken"],
            ua_hash=ua_hash,
            ip_subnet=ip_subnet,
        )
        return LoginResponse(status="totp_required", temp_token=temp_token)

    # 4. No TOTP — finalize and set cookie
    return await _finalize_and_set_cookie(
        response=response,
        auth_request_id=body.auth_request_id,
        session_id=session["sessionId"],
        session_token=session["sessionToken"],
    )


@router.post("/auth/totp-login", response_model=LoginResponse)
async def totp_login(body: TOTPLoginRequest, response: Response, db: AsyncSession = Depends(get_db)) -> LoginResponse:
    """Complete login by providing a TOTP code after password was accepted.

    SPEC-SEC-SESSION-001 REQ-1.3..1.6: pending state lives in Redis. The
    failure counter is incremented atomically with ``INCR`` so the lockout
    ceiling holds across replicas. Lockout deletes both keys, so the
    pre-SPEC pre-emptive ``failures >= MAX`` check is redundant — a 6th
    attempt naturally falls into the ``expired_token`` leg.

    SPEC-SEC-AUTH-COVERAGE-001 REQ-1.6/1.7/1.8: every failure leg
    (expired_token, invalid_code, lockout-after-fail, zitadel_5xx) emits a
    ``totp_login_failed`` structured event in addition to the existing
    ``audit.log_event(action="auth.totp.failed")`` call. The Redis-rebase
    drops the never-reached "immediate lockout on entry" leg.
    """
    pending = await _totp_pending_get(body.temp_token)
    if not pending:
        _emit_auth_event(
            "totp_login_failed",
            reason="expired_token",
            outcome="400",
            level="warning",
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Session expired, please log in again",
        )

    # Verify TOTP code by updating the session
    try:
        updated = await zitadel.update_session_with_totp(
            session_id=pending["session_id"],
            session_token=pending["session_token"],
            code=body.code,
        )
    except httpx.HTTPStatusError as exc:
        _slog.exception("update_session_with_totp_failed", zitadel_status=exc.response.status_code)
        if exc.response.status_code in (400, 401):
            new_failures = await _totp_pending_incr_failures(body.temp_token)
            await audit.log_event(
                org_id=0,
                actor="unknown",
                action="auth.totp.failed",
                resource_type="session",
                resource_id=pending["session_id"],
                details={"reason": "invalid_code"},
            )
            if new_failures >= _TOTP_MAX_FAILURES:
                await _totp_pending_delete(body.temp_token)
                # REQ-5.1 (SPEC-SEC-SESSION-001): token_prefix only — never
                # the full token, never the session credentials. PII guard
                # verified by ``test_session_logging_pii``.
                _slog.warning(
                    "totp_pending_lockout",
                    failures=new_failures,
                    token_prefix=body.temp_token[:8],
                )
                _emit_auth_event(
                    "totp_login_failed",
                    reason="lockout",
                    failures=new_failures,
                    zitadel_status=exc.response.status_code,
                    outcome="429",
                    level="error",
                )
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail="Too many failed attempts, please log in again",
                ) from exc
            _emit_auth_event(
                "totp_login_failed",
                reason="invalid_code",
                failures=new_failures,
                zitadel_status=exc.response.status_code,
                outcome="400",
                level="warning",
            )
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid code, please try again",
            ) from exc
        _emit_auth_event(
            "totp_login_failed",
            reason="zitadel_5xx",
            zitadel_status=exc.response.status_code,
            outcome="502",
            level="error",
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Verification failed, please try again later",
        ) from exc

    # Audit: successful TOTP login
    await audit.log_event(
        org_id=0,
        actor="unknown",
        action="auth.login.totp",
        resource_type="session",
        resource_id=pending["session_id"],
        details={"method": "totp"},
    )

    session_id = updated.get("sessionId", pending["session_id"])
    session_token = updated.get("sessionToken", pending["session_token"])

    # Clean up pending token (REQ-1.6)
    await _totp_pending_delete(body.temp_token)

    # Finalize and set cookie
    return await _finalize_and_set_cookie(
        response=response,
        auth_request_id=body.auth_request_id,
        session_id=session_id,
        session_token=session_token,
    )


@router.post("/auth/sso-complete", response_model=LoginResponse)
async def sso_complete(
    body: SSOCompleteRequest,
    klai_sso: str | None = Cookie(default=None),
) -> LoginResponse:
    """Auto-complete a Zitadel OIDC auth request using the portal SSO session.

    Called by the custom login page when it loads inside the LibreChat iframe
    (and by silent-renew iframes from react-oidc-context).
    Returns 401 if no valid SSO session exists (frontend falls back to the login form).

    Failure observability: SPEC-SEC-AUTH-COVERAGE-001 REQ-4 emits
    ``sso_complete_failed`` events for every 401 leg (no_cookie /
    cookie_invalid / session_expired). Success is intentionally silent —
    cookie reuse is non-interactive UX, not an audited action (REQ-4.4).
    """
    if not klai_sso:
        _emit_auth_event("sso_complete_failed", reason="no_cookie", outcome="401", level="warning")
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="No SSO session")

    session_data = _decrypt_sso(klai_sso)
    if not session_data:
        _emit_auth_event("sso_complete_failed", reason="cookie_invalid", outcome="401", level="warning")
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="SSO cookie invalid")

    try:
        callback_url = await zitadel.finalize_auth_request(
            auth_request_id=body.auth_request_id,
            session_id=session_data["sid"],
            session_token=session_data["stk"],
        )
    except httpx.HTTPStatusError as exc:
        _slog.exception("sso_finalize_failed", zitadel_status=exc.response.status_code)
        _emit_auth_event(
            "sso_complete_failed",
            reason="session_expired",
            zitadel_status=exc.response.status_code,
            outcome="401",
            level="warning",
        )
        # Session expired in Zitadel -- tell the frontend to show the login form
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="SSO session no longer valid",
        ) from exc

    return LoginResponse(callback_url=await _validate_callback_url(callback_url))


@router.post("/auth/totp/setup", response_model=TOTPSetupResponse)
async def totp_setup(
    user_id: str = Depends(get_current_user_id),
) -> TOTPSetupResponse:
    """Initiate TOTP registration for the logged-in user. Returns QR URI and secret.

    SPEC-SEC-AUTH-COVERAGE-001 REQ-1.1/1.2: emit audit on success, structured
    event on 5xx.
    """
    try:
        result = await zitadel.register_user_totp(user_id)
    except httpx.HTTPStatusError as exc:
        _slog.exception("register_user_totp_failed", zitadel_status=exc.response.status_code)
        _emit_auth_event(
            "totp_setup_failed",
            reason="zitadel_5xx",
            zitadel_status=exc.response.status_code,
            actor_user_id=user_id,
            outcome="502",
            level="error",
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Failed to set up 2FA, please try again later",
        ) from exc

    await audit.log_event(
        org_id=0,
        actor=user_id,
        action="auth.totp.setup",
        resource_type="user",
        resource_id=user_id,
        details={"reason": "initiated"},
    )
    return TOTPSetupResponse(uri=result["uri"], secret=result["totpSecret"])


class VerifyEmailRequest(BaseModel):
    user_id: str
    code: str
    org_id: str


@router.post("/auth/verify-email", status_code=status.HTTP_204_NO_CONTENT)
async def verify_email(body: VerifyEmailRequest) -> None:
    """Verify a user's email address using the code from the verification email.

    SPEC-SEC-AUTH-COVERAGE-001 REQ-3.8/3.9: emit audit on success;
    structured event on 4xx (invalid_code/expired_link) and 5xx (zitadel_5xx).
    """
    try:
        await zitadel.verify_user_email(body.org_id, body.user_id, body.code)
    except httpx.HTTPStatusError as exc:
        _slog.exception("verify_user_email_failed", zitadel_status=exc.response.status_code)
        if exc.response.status_code in (400, 404):
            _emit_auth_event(
                "verify_email_failed",
                reason="expired_link" if exc.response.status_code == 404 else "invalid_code",
                zitadel_status=exc.response.status_code,
                actor_user_id=body.user_id,
                outcome="400",
                level="warning",
            )
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid or expired verification link.",
            ) from exc
        _emit_auth_event(
            "verify_email_failed",
            reason="zitadel_5xx",
            zitadel_status=exc.response.status_code,
            actor_user_id=body.user_id,
            outcome="502",
            level="error",
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Verification failed, please try again later.",
        ) from exc

    await audit.log_event(
        org_id=0,
        actor=body.user_id,
        action="auth.email.verified",
        resource_type="user",
        resource_id=body.user_id,
        details={"reason": "verified"},
    )


@router.post("/auth/totp/confirm", status_code=status.HTTP_204_NO_CONTENT)
async def totp_confirm(
    body: TOTPConfirmRequest,
    user_id: str = Depends(get_current_user_id),
) -> None:
    """Verify and activate the TOTP registration.

    SPEC-SEC-AUTH-COVERAGE-001 REQ-1.3/1.4/1.5: emit audit on success,
    structured event on 4xx (invalid_code) and 5xx (zitadel_5xx).
    """
    try:
        await zitadel.verify_user_totp(user_id, body.code)
    except httpx.HTTPStatusError as exc:
        _slog.exception("verify_user_totp_failed", zitadel_status=exc.response.status_code)
        if exc.response.status_code in (400, 401):
            _emit_auth_event(
                "totp_confirm_failed",
                reason="invalid_code",
                zitadel_status=exc.response.status_code,
                actor_user_id=user_id,
                outcome="400",
                level="warning",
            )
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid code, please try again",
            ) from exc
        _emit_auth_event(
            "totp_confirm_failed",
            reason="zitadel_5xx",
            zitadel_status=exc.response.status_code,
            actor_user_id=user_id,
            outcome="502",
            level="error",
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Failed to confirm 2FA, please try again later",
        ) from exc

    await audit.log_event(
        org_id=0,
        actor=user_id,
        action="auth.totp.confirmed",
        resource_type="user",
        resource_id=user_id,
        details={"reason": "activated"},
    )


@router.post("/auth/passkey/setup", response_model=PasskeySetupResponse)
async def passkey_setup(
    request: Request,
    user_id: str = Depends(get_current_user_id),
) -> PasskeySetupResponse:
    """Start WebAuthn passkey registration. Returns options for navigator.credentials.create().

    SPEC-SEC-AUTH-COVERAGE-001 REQ-1.9: emit audit on success, structured event on 5xx.
    """
    domain = request.headers.get("x-forwarded-host") or request.headers.get("host", settings.domain)
    # Strip port if present
    domain = domain.split(":")[0]
    try:
        result = await zitadel.start_passkey_registration(user_id, domain)
    except httpx.HTTPStatusError as exc:
        _slog.exception("start_passkey_registration_failed", zitadel_status=exc.response.status_code)
        _emit_auth_event(
            "passkey_setup_failed",
            reason="zitadel_5xx",
            zitadel_status=exc.response.status_code,
            actor_user_id=user_id,
            outcome="502",
            level="error",
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Failed to set up passkey, please try again later",
        ) from exc
    await audit.log_event(
        org_id=0,
        actor=user_id,
        action="auth.passkey.setup",
        resource_type="user",
        resource_id=user_id,
        details={"reason": "initiated"},
    )
    return PasskeySetupResponse(
        passkey_id=result["passkeyId"],
        options=result.get("publicKeyCredentialCreationOptions", {}),
    )


@router.post("/auth/passkey/confirm", status_code=status.HTTP_204_NO_CONTENT)
async def passkey_confirm(
    body: PasskeyConfirmRequest,
    user_id: str = Depends(get_current_user_id),
) -> None:
    """Complete passkey registration by submitting the browser's PublicKeyCredential.

    SPEC-SEC-AUTH-COVERAGE-001 REQ-1.10: emit audit on success, structured
    event on 4xx (invalid_attestation) and 5xx (zitadel_5xx).
    """
    try:
        await zitadel.verify_passkey_registration(
            user_id, body.passkey_id, body.public_key_credential, body.passkey_name
        )
    except httpx.HTTPStatusError as exc:
        _slog.exception("verify_passkey_registration_failed", zitadel_status=exc.response.status_code)
        if exc.response.status_code in (400, 401):
            _emit_auth_event(
                "passkey_confirm_failed",
                reason="invalid_attestation",
                zitadel_status=exc.response.status_code,
                actor_user_id=user_id,
                outcome="400",
                level="warning",
            )
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Passkey verification failed, please try again",
            ) from exc
        _emit_auth_event(
            "passkey_confirm_failed",
            reason="zitadel_5xx",
            zitadel_status=exc.response.status_code,
            actor_user_id=user_id,
            outcome="502",
            level="error",
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Failed to set up passkey, please try again later",
        ) from exc

    await audit.log_event(
        org_id=0,
        actor=user_id,
        action="auth.passkey.confirmed",
        resource_type="user",
        resource_id=user_id,
        details={"reason": "activated"},
    )


@router.post("/auth/email-otp/setup", status_code=status.HTTP_204_NO_CONTENT)
async def email_otp_setup(
    user_id: str = Depends(get_current_user_id),
) -> None:
    """Register email OTP for the user. Zitadel sends a verification code to the user's email.

    SPEC-SEC-AUTH-COVERAGE-001 REQ-1.11: emit audit on success, structured event on 5xx.
    """
    try:
        await zitadel.register_email_otp(user_id)
    except httpx.HTTPStatusError as exc:
        _slog.exception("register_email_otp_failed", zitadel_status=exc.response.status_code)
        _emit_auth_event(
            "email_otp_setup_failed",
            reason="zitadel_5xx",
            zitadel_status=exc.response.status_code,
            actor_user_id=user_id,
            outcome="502",
            level="error",
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Failed to set up email code, please try again later",
        ) from exc

    await audit.log_event(
        org_id=0,
        actor=user_id,
        action="auth.email-otp.setup",
        resource_type="user",
        resource_id=user_id,
        details={"reason": "initiated"},
    )


@router.post("/auth/email-otp/resend", status_code=status.HTTP_204_NO_CONTENT)
async def email_otp_resend(
    user_id: str = Depends(get_current_user_id),
) -> None:
    """Resend the email OTP verification code by removing and re-registering the method.

    SPEC-SEC-AUTH-COVERAGE-001 REQ-1.13: emit audit on success, structured event on 5xx.
    """
    try:
        await zitadel.remove_email_otp(user_id)
    except httpx.HTTPStatusError as exc:
        # If not registered yet, ignore — proceed to register
        if exc.response.status_code != 404:
            _slog.exception("remove_email_otp_failed", zitadel_status=exc.response.status_code)
            _emit_auth_event(
                "email_otp_resend_failed",
                reason="zitadel_5xx",
                zitadel_status=exc.response.status_code,
                actor_user_id=user_id,
                outcome="502",
                level="error",
            )
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Failed to resend email code, please try again later",
            ) from exc
    try:
        await zitadel.register_email_otp(user_id)
    except httpx.HTTPStatusError as exc:
        _slog.exception("register_email_otp_resend_failed", zitadel_status=exc.response.status_code)
        _emit_auth_event(
            "email_otp_resend_failed",
            reason="zitadel_5xx",
            zitadel_status=exc.response.status_code,
            actor_user_id=user_id,
            outcome="502",
            level="error",
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Failed to resend email code, please try again later",
        ) from exc

    await audit.log_event(
        org_id=0,
        actor=user_id,
        action="auth.email-otp.resent",
        resource_type="user",
        resource_id=user_id,
        details={"reason": "resent"},
    )


@router.post("/auth/email-otp/confirm", status_code=status.HTTP_204_NO_CONTENT)
async def email_otp_confirm(
    body: EmailOTPConfirmRequest,
    user_id: str = Depends(get_current_user_id),
) -> None:
    """Verify and activate the email OTP using the code sent during setup.

    SPEC-SEC-AUTH-COVERAGE-001 REQ-1.12: emit audit on success, structured
    event on 4xx (invalid_code) and 5xx (zitadel_5xx).
    """
    try:
        await zitadel.verify_email_otp(user_id, body.code)
    except httpx.HTTPStatusError as exc:
        _slog.exception("verify_email_otp_failed", zitadel_status=exc.response.status_code)
        if exc.response.status_code in (400, 401):
            _emit_auth_event(
                "email_otp_confirm_failed",
                reason="invalid_code",
                zitadel_status=exc.response.status_code,
                actor_user_id=user_id,
                outcome="400",
                level="warning",
            )
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid code, please try again",
            ) from exc
        _emit_auth_event(
            "email_otp_confirm_failed",
            reason="zitadel_5xx",
            zitadel_status=exc.response.status_code,
            actor_user_id=user_id,
            outcome="502",
            level="error",
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Failed to confirm email code, please try again later",
        ) from exc

    await audit.log_event(
        org_id=0,
        actor=user_id,
        action="auth.email-otp.confirmed",
        resource_type="user",
        resource_id=user_id,
        details={"reason": "activated"},
    )


@router.post("/auth/idp-intent", response_model=IDPIntentResponse)
async def idp_intent(body: IDPIntentRequest) -> IDPIntentResponse:
    """Start a social login flow. Returns the IDP auth URL to redirect the user to.

    SPEC-SEC-AUTH-COVERAGE-001 REQ-2.1/2.2: emit audit on success;
    structured event on unknown_idp / zitadel_5xx / missing_auth_url.
    """
    known_idps = {settings.zitadel_idp_google_id, settings.zitadel_idp_microsoft_id} - {""}
    if body.idp_id not in known_idps:
        _emit_auth_event(
            "idp_intent_failed",
            reason="unknown_idp",
            outcome="400",
            level="warning",
        )
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unknown IDP")

    success_url = f"{settings.portal_url}/api/auth/idp-callback?auth_request_id={body.auth_request_id}"
    failure_url = f"{settings.portal_url}/login?authRequest={body.auth_request_id}"

    try:
        result = await zitadel.create_idp_intent(body.idp_id, success_url, failure_url)
    except httpx.HTTPStatusError as exc:
        _slog.exception("create_idp_intent_failed", zitadel_status=exc.response.status_code)
        _emit_auth_event(
            "idp_intent_failed",
            reason="zitadel_5xx",
            zitadel_status=exc.response.status_code,
            outcome="502",
            level="error",
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Login failed, please try again later",
        ) from exc

    auth_url = result.get("authUrl")
    if not auth_url:
        _slog.error("create_idp_intent_no_auth_url", result_keys=list(result.keys()))
        _emit_auth_event(
            "idp_intent_failed",
            reason="missing_auth_url",
            outcome="502",
            level="error",
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Login failed, please try again later",
        )

    await audit.log_event(
        org_id=0,
        actor="anonymous",
        action="auth.idp.intent",
        resource_type="session",
        resource_id="pending",
        details={"idp_id": body.idp_id, "auth_request_id": body.auth_request_id},
    )
    return IDPIntentResponse(auth_url=auth_url)


@router.get("/auth/idp-callback")
async def idp_callback(
    id: str,
    token: str,
    auth_request_id: str,
    db: AsyncSession = Depends(get_db),
) -> RedirectResponse:
    """Handle the redirect back from a social IDP after authentication.

    Zitadel appends ?id=<intentId>&token=<intentToken> to the success_url.
    We create a session from the intent, look up portal_users, auto-provision
    if an allowed domain matches, finalize the auth request, set the SSO cookie,
    and redirect to the OIDC callback URL.
    """
    failure_url = f"/login?authRequest={auth_request_id}"

    try:
        session = await zitadel.create_session_with_idp_intent(id, token)
    except httpx.HTTPStatusError as exc:
        _slog.exception("idp_callback_create_session_failed", zitadel_status=exc.response.status_code)
        _emit_auth_event(
            "idp_callback_failed",
            reason="session_creation_5xx",
            zitadel_status=exc.response.status_code,
            outcome="302→failure_url",
            level="error",
        )
        return RedirectResponse(url=failure_url, status_code=302)
    except Exception:
        _slog.exception("idp_callback_create_session_failed_unexpected")
        _emit_auth_event(
            "idp_callback_failed",
            reason="session_creation_unexpected",
            outcome="302→failure_url",
            level="error",
        )
        return RedirectResponse(url=failure_url, status_code=302)

    session_id: str | None = session.get("sessionId")
    session_token: str | None = session.get("sessionToken")

    if not session_id or not session_token:
        _slog.error("idp_callback_no_session_in_response", session_keys=list(session.keys()))
        _emit_auth_event(
            "idp_callback_failed",
            reason="missing_session",
            outcome="302→failure_url",
            level="error",
        )
        return RedirectResponse(url=failure_url, status_code=302)

    # Fetch user identity from the session — fail-soft: empty values continue.
    try:
        details = await zitadel.get_session_details(session_id, session_token)
    except Exception:
        _slog.exception("idp_callback_get_session_details_failed")
        _emit_auth_event(
            "idp_callback_failed",
            reason="get_session_details_failed",
            outcome="continue-degraded",
            level="warning",
        )
        details = {"zitadel_user_id": "", "email": ""}

    zitadel_user_id = details.get("zitadel_user_id", "")
    email = details.get("email", "")

    # SPEC-AUTH-009 R3: 4-case domain-match decision matrix
    # member_orgs: orgs where the user already has a portal_users row
    # domain_orgs: orgs whose primary_domain matches user email domain
    #              AND user is NOT already a member
    if zitadel_user_id:
        user_result = await db.execute(select(PortalUser).where(PortalUser.zitadel_user_id == zitadel_user_id))
        member_users = list(user_result.scalars().all())
    else:
        member_users = []

    # Query orgs with matching primary_domain that user is NOT already a member of
    email_domain = email.rsplit("@", 1)[-1].strip().lower() if "@" in email else ""
    domain_orgs = []
    if email_domain and zitadel_user_id:
        member_org_ids = {u.org_id for u in member_users}
        domain_result = await db.execute(
            select(PortalOrg).where(
                PortalOrg.primary_domain == email_domain,
                PortalOrg.deleted_at.is_(None),
                PortalOrg.id.not_in(member_org_ids) if member_org_ids else PortalOrg.id.is_not(None),
            )
        )
        domain_orgs = list(domain_result.scalars().all())

    # Build combined entries list: member entries first, then domain_match
    entries = [
        {
            "org_id": u.org_id,
            "name": u.org.name if hasattr(u.org, "name") else "",
            "slug": u.org.slug if hasattr(u.org, "slug") else "",
            "kind": "member",
            "auto_accept": False,
        }
        for u in member_users
    ] + [
        {
            "org_id": o.id,
            "name": o.name,
            "slug": o.slug,
            "kind": "domain_match",
            "auto_accept": bool(o.auto_accept_same_domain),
        }
        for o in domain_orgs
    ]

    total = len(entries)

    # Case 1: no member orgs AND no domain_orgs -> redirect to /no-account
    if total == 0:
        return RedirectResponse(url="/no-account", status_code=302)

    # Case 2: exactly 1 member entry + 0 domain_match -> direct finalize
    if len(member_users) == 1 and len(domain_orgs) == 0:
        pass  # falls through to finalize below

    # Cases 3+4: any domain_match OR multiple total entries -> picker
    elif total >= 1 and (len(domain_orgs) > 0 or total > 1):
        try:
            svc = PendingSessionService()
            ref = await svc.store(
                session_id=session_id,
                session_token=session_token,
                zitadel_user_id=zitadel_user_id,
                email=email,
                auth_request_id=auth_request_id,
                entries=entries,
            )
            return RedirectResponse(url=f"/select-workspace?ref={ref}", status_code=302)
        except Exception:
            _slog.exception("Failed to store pending session -- falling through to first member org")

    # Finalize the auth request (Case 2: single member)
    try:
        callback_url = await zitadel.finalize_auth_request(
            auth_request_id=auth_request_id,
            session_id=session_id,
            session_token=session_token,
        )
    except httpx.HTTPStatusError as exc:
        _slog.exception("idp_callback_finalize_failed", zitadel_status=exc.response.status_code)
        _emit_auth_event(
            "idp_callback_failed",
            reason="finalize_5xx",
            zitadel_status=exc.response.status_code,
            outcome="302→failure_url",
            level="error",
        )
        return RedirectResponse(url=failure_url, status_code=302)

    redirect = RedirectResponse(url=await _validate_callback_url(callback_url), status_code=302)
    redirect.set_cookie(
        key="klai_sso",
        value=_encrypt_sso(session_id, session_token),
        domain=f".{settings.domain}",
        httponly=True,
        secure=True,
        samesite="lax",
        max_age=settings.sso_cookie_max_age,
    )
    emit_event("login", user_id=zitadel_user_id or None, properties={"method": "idp"})
    # SPEC-SEC-AUTH-COVERAGE-001 REQ-2.3: audit log on successful IDP callback completion
    if zitadel_user_id:
        await audit.log_event(
            org_id=0,
            actor=zitadel_user_id,
            action="auth.login.idp",
            resource_type="session",
            resource_id=session_id,
            details={"method": "idp"},
        )
    return redirect


# ---------------------------------------------------------------------------
# Social SIGNUP endpoints (SPEC-AUTH-001)
# ---------------------------------------------------------------------------


@router.post("/auth/idp-intent-signup", response_model=IDPIntentResponse)
async def idp_intent_signup(body: IDPIntentSignupRequest) -> IDPIntentResponse:
    """Start a social signup flow. Returns the IDP auth URL to redirect the user to.

    Unlike idp-intent (login), this endpoint does not require an auth_request_id —
    the user is not yet in an OIDC session. After IDP callback we detect new vs
    existing users and branch accordingly.

    SPEC-SEC-AUTH-COVERAGE-001 REQ-2.6: emit audit on success, structured
    event on unknown_idp / zitadel_5xx / missing_auth_url.
    """
    known_idps = {settings.zitadel_idp_google_id, settings.zitadel_idp_microsoft_id} - {""}
    if body.idp_id not in known_idps:
        _emit_auth_event(
            "idp_intent_signup_failed",
            reason="unknown_idp",
            outcome="400",
            level="warning",
        )
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unknown IDP")

    success_url = f"{settings.portal_url}/api/auth/idp-signup-callback?locale={body.locale}"
    failure_url = f"{settings.portal_url}/{body.locale}/signup?error=idp_failed"

    try:
        result = await zitadel.create_idp_intent(body.idp_id, success_url, failure_url)
    except httpx.HTTPStatusError as exc:
        _slog.exception("create_idp_intent_signup_failed", zitadel_status=exc.response.status_code)
        _emit_auth_event(
            "idp_intent_signup_failed",
            reason="zitadel_5xx",
            zitadel_status=exc.response.status_code,
            outcome="502",
            level="error",
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Signup failed, please try again later",
        ) from exc

    auth_url = result.get("authUrl")
    if not auth_url:
        _slog.error("create_idp_intent_signup_no_auth_url", result_keys=list(result.keys()))
        _emit_auth_event(
            "idp_intent_signup_failed",
            reason="missing_auth_url",
            outcome="502",
            level="error",
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Signup failed, please try again later",
        )

    await audit.log_event(
        org_id=0,
        actor="anonymous",
        action="auth.idp.intent_signup",
        resource_type="session",
        resource_id="pending",
        details={"idp_id": body.idp_id, "locale": body.locale},
    )
    return IDPIntentResponse(auth_url=auth_url)


@router.get("/auth/idp-signup-callback")
async def idp_signup_callback(
    id: str,
    token: str,
    request: Request,
    locale: str = Query(default="nl"),
    db: AsyncSession = Depends(get_db),
) -> RedirectResponse:
    """Handle the redirect back from a social IDP during signup.

    Zitadel appends ?id=<intentId>&token=<intentToken> to the success_url.
    The ?locale=<nl|en> param is embedded in success_url by idp_intent_signup.

    - New user  → store session in encrypted cookie → redirect to /signup/social form
    - Existing user → set SSO cookie → redirect to / (auto-login via sso-complete)
    - Failure   → redirect to /{locale}/signup?error=idp_failed
    """
    locale = locale if locale in _SUPPORTED_LOCALES else "nl"
    failure_url = f"{settings.portal_url}/{locale}/signup?error=idp_failed"

    # SPEC-SEC-AUTH-COVERAGE-001 REQ-2.7: every failure leg below emits a
    # structured idp_signup_callback_failed event before the 302-to-failure_url.
    # Existing-user happy path emits audit.log_event(auth.signup.idp.existing).

    # 1. Retrieve the IDP intent to get user info and optional Zitadel userId
    try:
        intent_data = await zitadel.retrieve_idp_intent(id, token)
    except httpx.HTTPStatusError as exc:
        _slog.exception("idp_signup_callback_retrieve_intent_failed", zitadel_status=exc.response.status_code)
        _emit_auth_event(
            "idp_signup_callback_failed",
            reason="retrieve_intent_5xx",
            zitadel_status=exc.response.status_code,
            outcome="302→failure_url",
            level="error",
        )
        return RedirectResponse(url=failure_url, status_code=302)

    idp_user_id: str | None = intent_data.get("userId")

    # 1b. New user — no Zitadel account yet. Create one from the IDP profile.
    if not idp_user_id:
        try:
            idp_user_id = await zitadel.create_zitadel_user_from_idp(intent_data, settings.zitadel_portal_org_id)
            _slog.info("idp_signup_callback_user_created", zitadel_user_id=idp_user_id)
        except httpx.HTTPStatusError as exc:
            _slog.exception("idp_signup_callback_create_user_failed", zitadel_status=exc.response.status_code)
            _emit_auth_event(
                "idp_signup_callback_failed",
                reason="create_user_5xx",
                zitadel_status=exc.response.status_code,
                outcome="302→failure_url",
                level="error",
            )
            return RedirectResponse(url=failure_url, status_code=302)
        except Exception:
            _slog.exception("idp_signup_callback_create_user_failed_unexpected")
            _emit_auth_event(
                "idp_signup_callback_failed",
                reason="create_user_unexpected",
                outcome="302→failure_url",
                level="error",
            )
            return RedirectResponse(url=failure_url, status_code=302)

    # 1c. Create Zitadel session with the resolved user_id + IDP intent.
    # Zitadel uses event sourcing (CQRS): the user is written to the command side but the
    # read side (queried by POST /v2/sessions) may lag briefly after creation. Retry on 404.
    session = None
    last_exc: Exception | None = None
    for attempt in range(4):
        if attempt > 0:
            await asyncio.sleep(attempt * 1.5)
        try:
            session = await zitadel.create_session_for_user_idp(idp_user_id, id, token)
            break
        except httpx.HTTPStatusError as exc:
            last_exc = exc
            if exc.response.status_code == 404 and attempt < 3:
                _slog.warning(
                    "idp_signup_callback_create_session_404_retry",
                    attempt=attempt + 1,
                )
                continue
            _slog.exception("idp_signup_callback_create_session_failed", zitadel_status=exc.response.status_code)
            _emit_auth_event(
                "idp_signup_callback_failed",
                reason="create_session_5xx",
                zitadel_status=exc.response.status_code,
                attempts=attempt + 1,
                outcome="302→failure_url",
                level="error",
            )
            return RedirectResponse(url=failure_url, status_code=302)
    if session is None:
        _slog.error("idp_signup_callback_create_session_retries_exhausted", last_exc=str(last_exc))
        _emit_auth_event(
            "idp_signup_callback_failed",
            reason="create_session_retries_exhausted",
            outcome="302→failure_url",
            level="error",
        )
        return RedirectResponse(url=failure_url, status_code=302)

    session_id: str | None = session.get("sessionId")
    session_token: str | None = session.get("sessionToken")
    if not session_id or not session_token:
        _slog.error("idp_signup_callback_no_session_in_response", session_keys=list(session.keys()))
        _emit_auth_event(
            "idp_signup_callback_failed",
            reason="missing_session",
            outcome="302→failure_url",
            level="error",
        )
        return RedirectResponse(url=failure_url, status_code=302)

    # 2. Fetch full session to get the Zitadel user ID and IDP profile
    try:
        session_detail = await zitadel.get_session(session_id, session_token)
    except httpx.HTTPStatusError as exc:
        _slog.exception("idp_signup_callback_get_session_failed", zitadel_status=exc.response.status_code)
        _emit_auth_event(
            "idp_signup_callback_failed",
            reason="get_session_5xx",
            zitadel_status=exc.response.status_code,
            outcome="302→failure_url",
            level="error",
        )
        return RedirectResponse(url=failure_url, status_code=302)

    session_obj = session_detail.get("session", {})
    factors = session_obj.get("factors", {})
    user_factor = factors.get("user", {})
    zitadel_user_id: str = user_factor.get("id", "")
    if not zitadel_user_id:
        _slog.error("idp_signup_callback_no_user_id_in_factors")
        _emit_auth_event(
            "idp_signup_callback_failed",
            reason="missing_user_id",
            outcome="302→failure_url",
            level="error",
        )
        return RedirectResponse(url=failure_url, status_code=302)

    # Extract IDP display name + email for the social form pre-fill (non-sensitive)
    human_factor = factors.get("intent", {})
    idp_info = human_factor.get("idpInformation", {})
    raw_info = idp_info.get("rawInformation", {})
    first_name: str = raw_info.get("given_name") or user_factor.get("displayName", "").split(" ")[0]
    last_name: str = raw_info.get("family_name") or (" ".join(user_factor.get("displayName", "").split(" ")[1:]) or "")
    email: str = raw_info.get("email") or user_factor.get("loginName", "")

    # 3. Check if a PortalUser already exists for this Zitadel user
    existing_user = await db.scalar(select(PortalUser).where(PortalUser.zitadel_user_id == zitadel_user_id))

    if existing_user is not None:
        # Existing user — just log them in via the SSO cookie
        _slog.info("idp_signup_callback_existing_user_login", zitadel_user_id=zitadel_user_id)
        response = RedirectResponse(url=f"{settings.portal_url}/", status_code=302)
        response.set_cookie(
            key="klai_sso",
            value=_encrypt_sso(session_id, session_token),
            domain=f".{settings.domain}",
            httponly=True,
            secure=True,
            samesite="lax",
            max_age=settings.sso_cookie_max_age,
        )
        emit_event("login", user_id=zitadel_user_id, properties={"method": "idp"})
        # SPEC-SEC-AUTH-COVERAGE-001 REQ-2.7: audit log on successful existing-
        # user IDP signup-callback (existing portal_user → SSO cookie path)
        await audit.log_event(
            org_id=existing_user.org_id,
            actor=zitadel_user_id,
            action="auth.signup.idp.existing_login",
            resource_type="session",
            resource_id=session_id,
            details={"method": "idp"},
        )
        return response

    # 4. New user — store pending session in encrypted cookie, redirect to company name form.
    # SPEC-SEC-SESSION-001 REQ-2.1: snapshot the issuing browser + IP-subnet
    # so the consume side (signup_social) can reject a stolen-cookie replay
    # from a different origin context.
    pending_ua_hash = SessionService.hash_metadata(request.headers.get("user-agent"))
    pending_ip_subnet = resolve_caller_ip_subnet(request)
    pending_payload = json.dumps(
        {
            "session_id": session_id,
            "session_token": session_token,
            "zitadel_user_id": zitadel_user_id,
            "ua_hash": pending_ua_hash,
            "ip_subnet": pending_ip_subnet,
        }
    ).encode()
    encrypted_pending = _get_sso_fernet().encrypt(pending_payload).decode()

    social_url = (
        f"{settings.portal_url}/{locale}/signup/social"
        f"?first_name={quote(first_name)}&last_name={quote(last_name)}&email={quote(email)}"
    )
    response = RedirectResponse(url=social_url, status_code=302)
    cookie_domain = f".{settings.domain}" if settings.domain else None
    response.set_cookie(
        key=_IDP_PENDING_COOKIE,
        value=encrypted_pending,
        max_age=_IDP_PENDING_MAX_AGE,
        httponly=True,
        secure=True,
        samesite="lax",
        domain=cookie_domain,
        path="/",
    )
    return response
