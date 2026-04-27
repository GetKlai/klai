"""IdentityAsserter — entry point for service-to-service identity verification.

SPEC-SEC-IDENTITY-ASSERT-001 REQ-7.1 / REQ-7.2: consumers instantiate one
:class:`IdentityAsserter` per service (or use the module-level singleton via
:func:`verify_identity`) and call :meth:`IdentityAsserter.verify` for every
service-to-service call that carries a tenant or user identity claim.

Contract summary:

- Returns :class:`~klai_identity_assert.models.VerifyResult` for every code
  path. Consumers branch on ``verified`` and refuse the upstream operation
  when ``False``.
- Caches successful verifications for 60 seconds in-process. Denials are
  never cached (REQ-1.5).
- Fails closed: portal unreachable / network error / 5xx → returns a denial
  with ``reason="portal_unreachable"`` (REQ-7.2).
- Emits one ``identity_assert_call`` structlog event per call (REQ-7.5).
- Propagates ``X-Request-ID`` via the headers passed by the caller, so the
  portal's ``identity_verify_decision`` log entry shares the same trace ID
  (REQ-4.6 / observability rule).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

import httpx
import structlog

from klai_identity_assert.cache import IdentityCache
from klai_identity_assert.exceptions import IdentityDenied, PortalUnreachable
from klai_identity_assert.models import KNOWN_CALLER_SERVICES, VerifyResult
from klai_identity_assert.telemetry import emit_call, measure_latency

if TYPE_CHECKING:
    from collections.abc import Mapping

_logger = structlog.get_logger("klai_identity_assert.client")

# Default network timeout matches portal /internal/* p95 budget (research §4.2).
# Portal is expected to respond within 100 ms cold-cache; 2 seconds gives
# ample headroom while still fail-closing fast under genuine outage.
_DEFAULT_TIMEOUT_SECONDS = 2.0

# Default cache TTL — REQ-7.2.
_DEFAULT_CACHE_TTL_SECONDS = 60.0


def _interpret_response(payload: Any) -> VerifyResult:
    """Map a JSON body from /internal/identity/verify to a VerifyResult.

    Defensive parser: the portal contract is fixed (REQ-1.1) but the consumer
    is the second line of defence. Unknown shapes fail closed.
    """

    if not isinstance(payload, dict):
        return VerifyResult.deny("portal_unreachable")
    body = cast("dict[str, Any]", payload)

    if not bool(body.get("verified")):
        reason = body.get("reason")
        # Trust only the documented reason codes; anything else is treated as
        # a generic portal-unreachable so downstream log analysis stays clean.
        known: tuple[str, ...] = (
            "unknown_caller_service",
            "invalid_jwt",
            "jwt_identity_mismatch",
            "no_membership",
            "cache_unavailable",
        )
        if isinstance(reason, str) and reason in known:
            return VerifyResult.deny(reason)  # type: ignore[arg-type]
        return VerifyResult.deny("portal_unreachable")

    user_id = body.get("user_id")
    org_id = body.get("org_id")
    evidence = body.get("evidence")
    if not isinstance(user_id, str) or not isinstance(org_id, str):
        return VerifyResult.deny("portal_unreachable")
    if evidence not in ("jwt", "membership"):
        return VerifyResult.deny("portal_unreachable")
    return VerifyResult.allow(user_id=user_id, org_id=org_id, evidence=evidence)


class IdentityAsserter:
    """Stateful client for portal-api ``/internal/identity/verify``.

    Construct one per process. The instance owns an httpx.AsyncClient with
    connection pooling and the per-process LRU cache. Reuse it across calls
    — instantiating per-request would defeat both pooling and caching.
    """

    def __init__(
        self,
        *,
        portal_base_url: str,
        internal_secret: str,
        timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
        cache_ttl_seconds: float = _DEFAULT_CACHE_TTL_SECONDS,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        if not portal_base_url:
            raise ValueError("portal_base_url is required")
        if not internal_secret:
            raise ValueError("internal_secret is required")
        self._portal_base_url = portal_base_url.rstrip("/")
        self._internal_secret = internal_secret
        self._timeout_seconds = timeout_seconds
        self._owns_client = http_client is None
        self._http: httpx.AsyncClient = http_client or httpx.AsyncClient(
            timeout=httpx.Timeout(timeout_seconds),
        )
        self._cache = IdentityCache(ttl_seconds=cache_ttl_seconds)

    @property
    def cache(self) -> IdentityCache:
        """Expose the cache for inspection (test fixtures, operator tools)."""
        return self._cache

    async def aclose(self) -> None:
        """Release resources. Idempotent. Only closes the http client we own."""
        if self._owns_client:
            await self._http.aclose()

    async def __aenter__(self) -> IdentityAsserter:
        return self

    async def __aexit__(self, *_exc_info: object) -> None:
        await self.aclose()

    async def verify(
        self,
        *,
        caller_service: str,
        claimed_user_id: str,
        claimed_org_id: str,
        bearer_jwt: str | None,
        request_headers: Mapping[str, str] | None = None,
    ) -> VerifyResult:
        """Verify a claimed identity against portal-api.

        Returns
        -------
        VerifyResult
            Always returns a result; never raises for portal/network failure.
            Use :meth:`verify_or_raise` for exception-driven flow.
        """

        if caller_service not in KNOWN_CALLER_SERVICES:
            # Misconfigured caller: fail closed AND loud (REQ-1.2 says portal
            # returns 400 — we surface the same outcome here without a network
            # round trip, since the call would always fail anyway).
            result = VerifyResult.deny("library_misconfigured")
            with measure_latency() as latency:
                pass
            emit_call(
                caller_service=caller_service,
                claimed_user_id=claimed_user_id,
                claimed_org_id=claimed_org_id,
                result=result,
                latency_ms=latency["latency_ms"],
            )
            return result

        cached = self._cache.get(
            caller_service=caller_service,
            claimed_user_id=claimed_user_id,
            claimed_org_id=claimed_org_id,
            bearer_jwt=bearer_jwt,
        )
        if cached is not None:
            with measure_latency() as latency:
                pass
            emit_call(
                caller_service=caller_service,
                claimed_user_id=claimed_user_id,
                claimed_org_id=claimed_org_id,
                result=cached,
                latency_ms=latency["latency_ms"],
            )
            return cached

        body: dict[str, str | None] = {
            "caller_service": caller_service,
            "claimed_user_id": claimed_user_id,
            "claimed_org_id": claimed_org_id,
            "bearer_jwt": bearer_jwt,
        }
        # portal-api's /internal/* endpoints carry the shared INTERNAL_SECRET in
        # ``Authorization: Bearer ...`` (see _require_internal_token in
        # klai-portal/backend/app/api/internal.py). We follow that contract here.
        # Custom ``X-Internal-Secret`` is the convention used by callees of
        # portal-api (knowledge-ingest, retrieval-api), not by callers OF
        # portal-api — different direction, different header.
        headers: dict[str, str] = {
            "Authorization": f"Bearer {self._internal_secret}",
            "Content-Type": "application/json",
        }
        if request_headers is not None:
            request_id = request_headers.get("X-Request-ID") or request_headers.get("x-request-id")
            if request_id:
                headers["X-Request-ID"] = request_id

        with measure_latency() as latency:
            try:
                response = await self._http.post(
                    f"{self._portal_base_url}/internal/identity/verify",
                    json=body,
                    headers=headers,
                )
            except httpx.HTTPError as exc:
                _logger.warning(
                    "identity_assert_portal_unreachable",
                    caller_service=caller_service,
                    error=str(exc),
                )
                result = VerifyResult.deny("portal_unreachable")
                emit_call(
                    caller_service=caller_service,
                    claimed_user_id=claimed_user_id,
                    claimed_org_id=claimed_org_id,
                    result=result,
                    latency_ms=latency["latency_ms"],
                )
                return result

        # 5xx → fail closed as portal_unreachable. 4xx with verified=false body
        # → use the documented reason. Anything else with a malformed body →
        # also fail closed.
        if response.status_code >= 500:
            result = VerifyResult.deny("portal_unreachable")
            emit_call(
                caller_service=caller_service,
                claimed_user_id=claimed_user_id,
                claimed_org_id=claimed_org_id,
                result=result,
                latency_ms=latency["latency_ms"],
            )
            return result

        try:
            payload = response.json()
        except ValueError:
            result = VerifyResult.deny("portal_unreachable")
            emit_call(
                caller_service=caller_service,
                claimed_user_id=claimed_user_id,
                claimed_org_id=claimed_org_id,
                result=result,
                latency_ms=latency["latency_ms"],
            )
            return result

        result = _interpret_response(payload)
        if result.verified:
            self._cache.put(
                caller_service=caller_service,
                claimed_user_id=claimed_user_id,
                claimed_org_id=claimed_org_id,
                bearer_jwt=bearer_jwt,
                result=result,
            )

        emit_call(
            caller_service=caller_service,
            claimed_user_id=claimed_user_id,
            claimed_org_id=claimed_org_id,
            result=result,
            latency_ms=latency["latency_ms"],
        )
        return result

    async def verify_or_raise(
        self,
        *,
        caller_service: str,
        claimed_user_id: str,
        claimed_org_id: str,
        bearer_jwt: str | None,
        request_headers: Mapping[str, str] | None = None,
    ) -> VerifyResult:
        """Like :meth:`verify` but raises on every non-verified outcome.

        Maps consumer-side and portal-side failures onto two exception types:

        - :class:`~klai_identity_assert.exceptions.PortalUnreachable` for
          network errors and ``portal_unreachable``-coded results.
        - :class:`~klai_identity_assert.exceptions.IdentityDenied` for every
          other denial (``no_membership``, ``jwt_identity_mismatch``, etc.).

        Use this when the calling site prefers an early-return-via-raise
        pattern. Default :meth:`verify` is the recommended path.
        """

        result = await self.verify(
            caller_service=caller_service,
            claimed_user_id=claimed_user_id,
            claimed_org_id=claimed_org_id,
            bearer_jwt=bearer_jwt,
            request_headers=request_headers,
        )
        if result.verified:
            return result
        if result.reason == "portal_unreachable":
            raise PortalUnreachable(f"portal /internal/identity/verify failed for {caller_service}")
        raise IdentityDenied(result.reason or "unknown")
