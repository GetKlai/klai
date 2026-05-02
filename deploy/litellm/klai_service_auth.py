"""Vendored single-file copy of ``klai-libs/service-auth/klai_service_auth``.

SPEC-SEC-SERVICE-AUTH-001 Phase C-1.

Why a vendored copy
-------------------

The LiteLLM container (``ghcr.io/berriai/litellm:v1.83.7-stable``) is a stock
upstream image; klai mounts ``klai_knowledge.py`` and ``custom_router.py`` as
files into ``/app/`` (which is on PYTHONPATH). There is no ``pyproject.toml``
inside the container, no ``pip install`` step, and no klai-libs path-dep
mechanism the way other klai services have.

The two clean options were:

1. Build a custom litellm Dockerfile that ``pip install``s
   ``klai-libs/service-auth`` on top of the upstream image. This is the
   long-term plan but requires a separate CI workflow + image push pipeline.
2. Vendor a single-file copy here. Mount it next to ``klai_knowledge.py``.
   Refresh manually when the canonical library changes. Drift is detected
   by ``deploy/litellm/tests/test_klai_service_auth_drift.py``.

Phase C-1 ships option 2. Phase D plans to switch to option 1 alongside
removing the legacy ``X-Internal-Secret`` path and other deferred cleanup.

When updating
-------------

If you change ``klai-libs/service-auth/klai_service_auth/client.py``, copy
the new version below verbatim (preserving the SPEC reference comments).
The drift-detection test compares public API surface and rejects PRs that
forget the copy.
"""

from __future__ import annotations

import asyncio
import datetime as _dt
from typing import Any

import httpx

# Vendored copy: the canonical klai-libs/service-auth uses structlog, but the
# stock LiteLLM container does not bundle structlog. Stdlib logging keeps the
# vendored copy zero-dep — drift test verifies behavioural equivalence, not
# logging library identity.
import logging

logger = logging.getLogger(__name__)


class ServiceAuthError(Exception):
    """Raised when the token client cannot produce a valid access token.

    Wraps:
      * Network errors talking to the IdP token endpoint.
      * Non-2xx responses from the IdP (rejected credentials, scope errors).
      * Malformed token responses (missing access_token / expires_in).

    Callers decide how to handle: fail-closed (re-raise) or fall back to the
    legacy auth path during migration (SPEC-SEC-SERVICE-AUTH-001 REQ-5).
    """


# Refresh at 80% of advertised TTL. For Zitadel's 1h default that gives:
#   * Cache-hit window: 48 minutes
#   * Fail-recovery window: 12 minutes (IdP can be down for up to 12 min
#     before live calls start failing)
_REFRESH_FRACTION: float = 0.8

# Lower bound on TTL we'll accept from the IdP. Anything shorter is treated
# as a config error (someone misconfigured the token TTL on the service
# account in Zitadel) — we do NOT want to mint a fresh token every second.
_MIN_TTL_SECONDS: int = 60


# --- canonical scope constants (subset needed by LiteLLM hook) -------------
# Mirrors klai-libs/service-auth/klai_service_auth/scopes.py. Only the scopes
# this caller (svc-litellm) might request are vendored — adding more is fine
# but unused in the current hook.

SCOPE_RETRIEVAL_QUERY = "klai:internal:retrieval:query"


class ZitadelTokenClient:
    """Async OAuth 2.0 Client Credentials token client.

    See ``ZitadelTokenClient.__init__`` for parameters.

    Thread/concurrency model: safe for concurrent ``get_token`` calls within
    a single event loop. Not designed for cross-loop sharing — each event
    loop should own its own instance.
    """

    def __init__(
        self,
        *,
        client_id: str,
        client_secret: str,
        token_url: str,
        scope: str | None = None,
        timeout_seconds: float = 10.0,
    ) -> None:
        if not (client_id and client_id.strip()):
            raise ValueError("client_id must be non-empty")
        if not (client_secret and client_secret.strip()):
            raise ValueError("client_secret must be non-empty")
        if not (token_url and token_url.strip()):
            raise ValueError("token_url must be non-empty")

        self._client_id = client_id
        self._client_secret = client_secret
        self._token_url = token_url
        self._scope = scope
        self._timeout_seconds = timeout_seconds

        self._cache: tuple[str, _dt.datetime] | None = None
        self._lock = asyncio.Lock()

    async def get_token(self) -> str:
        """Return a valid access token, minting or refreshing as needed.

        Raises:
            ServiceAuthError: token mint failed.
        """
        cached = self._cache
        if cached is not None and self._is_fresh(cached[1]):
            # nosemgrep: python.lang.security.audit.logging.logger-credential-leak.python-logger-credential-disclosure
            logger.debug("service_auth_token_cache_hit client_id=%s", self._client_id)
            return cached[0]

        async with self._lock:
            cached = self._cache
            if cached is not None and self._is_fresh(cached[1]):
                # nosemgrep: python.lang.security.audit.logging.logger-credential-leak.python-logger-credential-disclosure
                logger.debug("service_auth_token_cache_hit client_id=%s", self._client_id)
                return cached[0]

            token, expires_at = await self._mint_token()
            self._cache = (token, expires_at)
            return token

    @staticmethod
    def _is_fresh(expires_at: _dt.datetime) -> bool:
        return _dt.datetime.now(_dt.UTC) < expires_at

    async def _mint_token(self) -> tuple[str, _dt.datetime]:
        body: dict[str, str] = {"grant_type": "client_credentials"}
        if self._scope:
            body["scope"] = self._scope

        try:
            async with httpx.AsyncClient(timeout=self._timeout_seconds) as client:
                resp = await client.post(
                    self._token_url,
                    data=body,
                    auth=(self._client_id, self._client_secret),
                    headers={"Accept": "application/json"},
                )
        except httpx.HTTPError as exc:
            # nosemgrep: python.lang.security.audit.logging.logger-credential-leak.python-logger-credential-disclosure
            logger.warning(
                "service_auth_token_mint_failed client_id=%s token_url=%s "
                "reason=http_error error=%s",
                self._client_id,
                self._token_url,
                exc,
            )
            raise ServiceAuthError(f"token mint network error: {exc}") from exc

        if resp.status_code != 200:
            body_excerpt = resp.text[:500] if resp.text else ""
            # nosemgrep: python.lang.security.audit.logging.logger-credential-leak.python-logger-credential-disclosure
            logger.warning(
                "service_auth_token_mint_failed client_id=%s status_code=%d "
                "reason=non_2xx body_excerpt=%s",
                self._client_id,
                resp.status_code,
                body_excerpt,
            )
            raise ServiceAuthError(f"token mint rejected by IdP: {resp.status_code} {body_excerpt}")

        try:
            payload: dict[str, Any] = resp.json()
        except ValueError as exc:
            # nosemgrep: python.lang.security.audit.logging.logger-credential-leak.python-logger-credential-disclosure
            logger.warning(
                "service_auth_token_mint_failed client_id=%s reason=malformed_json "
                "error=%s",
                self._client_id,
                exc,
            )
            raise ServiceAuthError(f"token endpoint returned non-JSON: {exc}") from exc

        access_token = payload.get("access_token")
        if not access_token or not isinstance(access_token, str):
            # nosemgrep: python.lang.security.audit.logging.logger-credential-leak.python-logger-credential-disclosure
            logger.warning(
                "service_auth_token_mint_failed client_id=%s "
                "reason=missing_access_token",
                self._client_id,
            )
            raise ServiceAuthError("token response missing access_token")

        expires_in = payload.get("expires_in")
        if not isinstance(expires_in, int) or expires_in < _MIN_TTL_SECONDS:
            # nosemgrep: python.lang.security.audit.logging.logger-credential-leak.python-logger-credential-disclosure
            logger.warning(
                "service_auth_token_mint_failed client_id=%s "
                "reason=invalid_expires_in expires_in=%r",
                self._client_id,
                expires_in,
            )
            raise ServiceAuthError(f"token response has invalid expires_in: {expires_in!r}")

        refresh_at = _dt.datetime.now(_dt.UTC) + _dt.timedelta(
            seconds=int(expires_in * _REFRESH_FRACTION)
        )
        # nosemgrep: python.lang.security.audit.logging.logger-credential-leak.python-logger-credential-disclosure
        logger.info(
            "service_auth_token_minted client_id=%s expires_in=%d refresh_at=%s",
            self._client_id,
            expires_in,
            refresh_at.isoformat(),
        )
        return access_token, refresh_at
