"""McpTokenAsserter — entry point for service-to-service MCP-token verification.

SPEC-MCP-AUTH-001 REQ-9 + Fase 2c: consumers (knowledge-mcp first) call
``verify_mcp_token(raw_token)`` to validate a klai_mcp_<...> bearer token.
Mirrors :class:`klai_identity_assert.client.IdentityAsserter` in lifecycle
and fail-closed semantics — only the wire-shape differs.

Contract:

- Returns :class:`McpTokenVerifyResult` for every code path. Consumers
  branch on ``verified`` and refuse the upstream operation on ``False``.
- Caches successful verifications for 60 seconds in-process (denials are
  never cached).
- Fails closed: portal unreachable / network error / 5xx → returns a denial
  with ``reason="portal_unreachable"``.
- Emits one ``mcp_token_assert_call`` structlog event per call.
- Propagates ``X-Request-ID`` via headers from the caller for trace
  correlation (one ``request_id:<uuid>`` query in VictoriaLogs shows the
  full chain).
"""

from __future__ import annotations

import hashlib
import time
from collections import OrderedDict
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, cast

import httpx
import structlog

_logger = structlog.get_logger("klai_identity_assert.mcp_token_client")

_DEFAULT_TIMEOUT_SECONDS = 2.0
_DEFAULT_CACHE_TTL_SECONDS = 60.0


class _McpTokenCache:
    """Bounded TTL cache keyed by SHA-256 of the raw access token.

    Distinct from :class:`klai_identity_assert.cache.IdentityCache`:
    - IdentityCache keys on (caller_service, claimed_user_id, claimed_org_id,
      bearer_jwt_fp) — JWT-shaped LibreChat path.
    - This cache keys on a single hex string (sha256 of the raw mcp token) —
      OAuth-shaped MCP path. The earlier reuse of IdentityCache passed the
      hex digest as a positional argument, which raised
      ``IdentityCache.get() takes 1 positional argument but 2 were given``
      because IdentityCache.get is keyword-only.

    Stores ``McpTokenVerifyResult`` with a per-entry expiry. LRU eviction
    when ``max_entries`` is exceeded.
    """

    __slots__ = ("_store", "_ttl_seconds", "_max_entries")

    def __init__(self, *, ttl_seconds: float, max_entries: int) -> None:
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        if max_entries <= 0:
            raise ValueError("max_entries must be positive")
        self._ttl_seconds = ttl_seconds
        self._max_entries = max_entries
        self._store: OrderedDict[str, tuple[float, McpTokenVerifyResult]] = OrderedDict()

    def get(self, key: str) -> McpTokenVerifyResult | None:
        entry = self._store.get(key)
        if entry is None:
            return None
        expires_at, value = entry
        if expires_at <= time.monotonic():
            self._store.pop(key, None)
            return None
        # Touch LRU.
        self._store.move_to_end(key)
        return value

    def set(self, key: str, value: McpTokenVerifyResult) -> None:
        expires_at = time.monotonic() + self._ttl_seconds
        self._store[key] = (expires_at, value)
        self._store.move_to_end(key)
        while len(self._store) > self._max_entries:
            self._store.popitem(last=False)


@dataclass(frozen=True, slots=True)
class McpTokenVerifyResult:
    """Result of an mcp-token verify call.

    On ``verified=False``: ``reason`` carries the deny reason (matches the
    set defined by SPEC-MCP-AUTH-001 REQ-9). All other fields are None.
    On ``verified=True``: ``user_id``, ``org_id``, ``org_slug``, ``scopes``
    and ``resource_uri`` are populated.

    Note: ``user_id`` and ``org_id`` are strings (zitadel_user_id and
    str(portal_orgs.id) respectively) — same wire shape as IdentityAsserter's
    ``VerifyResult`` so consumers can treat both verifiers symmetrically.
    """

    verified: bool
    user_id: str | None = None
    org_id: str | None = None
    org_slug: str | None = None
    scopes: tuple[str, ...] = ()
    resource_uri: str | None = None
    # SPEC-MCP-RETRIEVAL-001 REQ-6: OAuth client_id (the DCR-issued
    # ``portal_oauth_clients.client_id`` string) for telemetry attribution
    # in downstream consumers (knowledge-mcp's search_knowledge tool).
    # ``None`` means the portal-side resolution didn't find a matching
    # client row — defensive default; in practice the FK exists.
    client_id: str | None = None
    reason: str | None = None

    @classmethod
    def allow(
        cls,
        *,
        user_id: str,
        org_id: str,
        org_slug: str | None,
        scopes: tuple[str, ...],
        resource_uri: str | None,
        client_id: str | None = None,
    ) -> McpTokenVerifyResult:
        return cls(
            verified=True,
            user_id=user_id,
            org_id=org_id,
            org_slug=org_slug,
            scopes=scopes,
            resource_uri=resource_uri,
            client_id=client_id,
        )

    @classmethod
    def deny(cls, reason: str) -> McpTokenVerifyResult:
        return cls(verified=False, reason=reason)


_KNOWN_DENY_REASONS: frozenset[str] = frozenset(
    {
        "unknown_caller_service",
        "invalid_format",
        "unknown_token",
        "token_revoked",
        "token_expired",
        "audience_mismatch",
        "user_inactive",
        "org_deprovisioning",
        "cache_unavailable",
        "portal_unreachable",
    }
)


def _interpret_response(payload: Any) -> McpTokenVerifyResult:
    if not isinstance(payload, dict):
        return McpTokenVerifyResult.deny("portal_unreachable")
    body = cast("dict[str, Any]", payload)

    if not bool(body.get("verified")):
        reason = body.get("reason")
        if isinstance(reason, str) and reason in _KNOWN_DENY_REASONS:
            return McpTokenVerifyResult.deny(reason)
        return McpTokenVerifyResult.deny("portal_unreachable")

    user_id = body.get("user_id")
    org_id = body.get("org_id")
    org_slug = body.get("org_slug")
    scopes = body.get("scopes") or []
    resource_uri = body.get("resource_uri")
    client_id = body.get("client_id")

    if not isinstance(user_id, str) or not isinstance(org_id, str):
        return McpTokenVerifyResult.deny("portal_unreachable")
    if org_slug is not None and not isinstance(org_slug, str):
        return McpTokenVerifyResult.deny("portal_unreachable")
    if not isinstance(scopes, list):
        return McpTokenVerifyResult.deny("portal_unreachable")
    # client_id is optional in the wire shape (older portal builds may
    # omit the key entirely). Reject only if the type is wrong, not if
    # the value is None.
    if client_id is not None and not isinstance(client_id, str):
        return McpTokenVerifyResult.deny("portal_unreachable")

    return McpTokenVerifyResult.allow(
        user_id=user_id,
        org_id=org_id,
        org_slug=org_slug,
        scopes=tuple(str(s) for s in scopes),
        resource_uri=resource_uri if isinstance(resource_uri, str) else None,
        client_id=client_id if isinstance(client_id, str) else None,
    )


class McpTokenAsserter:
    """Service-to-service MCP-token verifier.

    Instantiate once per service process (typically as a module-level
    singleton — the embedded LRU cache assumes single-process scope).
    """

    def __init__(
        self,
        *,
        portal_base_url: str,
        internal_secret: str,
        caller_service: str,
        timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
        cache_ttl_seconds: float = _DEFAULT_CACHE_TTL_SECONDS,
        cache_max_size: int = 1024,
    ) -> None:
        if not portal_base_url.strip():
            raise ValueError("portal_base_url must be non-empty")
        if not internal_secret.strip():
            raise ValueError("internal_secret must be non-empty")
        if not caller_service.strip():
            raise ValueError("caller_service must be non-empty")

        self._portal_base_url = portal_base_url.rstrip("/")
        self._internal_secret = internal_secret
        self._caller_service = caller_service
        self._timeout_seconds = timeout_seconds
        self._client: httpx.AsyncClient | None = None
        self._cache = _McpTokenCache(
            ttl_seconds=cache_ttl_seconds,
            max_entries=cache_max_size,
        )

    async def verify(
        self,
        *,
        raw_token: str,
        request_headers: Mapping[str, str] | None = None,
    ) -> McpTokenVerifyResult:
        """Verify a raw bearer token. Returns deny on any uncertainty."""
        if not raw_token:
            return McpTokenVerifyResult.deny("invalid_format")

        cache_key = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached

        # portal-api's /internal/* endpoints carry the shared INTERNAL_SECRET
        # via ``Authorization: Bearer ...`` (see _require_internal_token in
        # klai-portal/backend/app/api/internal.py — same contract as
        # IdentityAsserter). The X-Internal-Secret convention is for callees
        # OF portal-api (knowledge-ingest, retrieval-api), not callers TO it.
        outbound_headers: dict[str, str] = {
            "Authorization": f"Bearer {self._internal_secret}",
            "Content-Type": "application/json",
        }
        if request_headers:
            for header in ("x-request-id", "X-Request-ID"):
                if header in request_headers:
                    outbound_headers["X-Request-ID"] = request_headers[header]
                    break

        try:
            client = await self._get_client()
            response = await client.post(
                "/internal/mcp-token/verify",
                json={"caller_service": self._caller_service, "raw_token": raw_token},
                headers=outbound_headers,
            )
        except httpx.HTTPError as exc:
            _logger.warning(
                "mcp_token_assert_call",
                caller_service=self._caller_service,
                verified=False,
                reason="portal_unreachable",
                error=str(exc),
            )
            return McpTokenVerifyResult.deny("portal_unreachable")

        if response.status_code >= 500:
            _logger.warning(
                "mcp_token_assert_call",
                caller_service=self._caller_service,
                verified=False,
                reason="portal_unreachable",
                status_code=response.status_code,
            )
            return McpTokenVerifyResult.deny("portal_unreachable")

        try:
            payload = response.json()
        except Exception:
            return McpTokenVerifyResult.deny("portal_unreachable")

        result = _interpret_response(payload)

        _logger.info(
            "mcp_token_assert_call",
            caller_service=self._caller_service,
            verified=result.verified,
            reason=result.reason,
            status_code=response.status_code,
        )

        # Cache only successful verifications (REQ-7.2 mirror).
        if result.verified:
            self._cache.set(cache_key, result)
        return result

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self._portal_base_url,
                timeout=self._timeout_seconds,
            )
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None
