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
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, cast

import httpx
import structlog

from klai_identity_assert.cache import IdentityCache

_logger = structlog.get_logger("klai_identity_assert.mcp_token_client")

_DEFAULT_TIMEOUT_SECONDS = 2.0
_DEFAULT_CACHE_TTL_SECONDS = 60.0


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
    ) -> McpTokenVerifyResult:
        return cls(
            verified=True,
            user_id=user_id,
            org_id=org_id,
            org_slug=org_slug,
            scopes=scopes,
            resource_uri=resource_uri,
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

    if not isinstance(user_id, str) or not isinstance(org_id, str):
        return McpTokenVerifyResult.deny("portal_unreachable")
    if org_slug is not None and not isinstance(org_slug, str):
        return McpTokenVerifyResult.deny("portal_unreachable")
    if not isinstance(scopes, list):
        return McpTokenVerifyResult.deny("portal_unreachable")

    return McpTokenVerifyResult.allow(
        user_id=user_id,
        org_id=org_id,
        org_slug=org_slug,
        scopes=tuple(str(s) for s in scopes),
        resource_uri=resource_uri if isinstance(resource_uri, str) else None,
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
        self._cache = IdentityCache(
            ttl_seconds=cache_ttl_seconds,
            max_size=cache_max_size,
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
            # IdentityCache stores VerifyResult; for mcp-tokens we use a
            # parallel storage shape. Re-build the McpTokenVerifyResult.
            return cached  # type: ignore[return-value]

        outbound_headers: dict[str, str] = {
            "X-Internal-Secret": self._internal_secret,
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
            self._cache.set(cache_key, result)  # type: ignore[arg-type]
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
