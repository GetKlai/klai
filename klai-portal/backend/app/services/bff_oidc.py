"""
OIDC flow helpers for the BFF (SPEC-AUTH-008 R2 through R7).

Builds the Zitadel authorize / end_session URLs, performs the authorization
code + refresh-token exchanges, and validates id_tokens against Zitadel's
published JWKS. Deliberately separate from `zitadel.py` because:

  - These endpoints do NOT use the service-account PAT (they are unauthenticated
    or use the portal client's own credentials).
  - The JWKS-validating JWT client is built once per process and must not be
    tangled with the existing httpx client.

Only the portal BFF (`api/auth_bff.py`) calls this module.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
from dataclasses import dataclass
from urllib.parse import urlencode

import httpx
import jwt
import structlog
from jwt import PyJWKClient

from app.core.config import settings
from app.utils.response_sanitizer import sanitize_response_body  # SPEC-SEC-INTERNAL-001 REQ-4

logger = structlog.get_logger()

# ---------------------------------------------------------------------------
# PKCE helpers
# ---------------------------------------------------------------------------

# RFC 7636: verifier is 43 to 128 chars of [A-Z][a-z][0-9]-._~.
# secrets.token_urlsafe(32) yields 43 chars of `A-Za-z0-9_-` (valid).
_VERIFIER_BYTES = 32


def generate_code_verifier() -> str:
    """Cryptographically random PKCE code verifier (43 URL-safe chars)."""
    return secrets.token_urlsafe(_VERIFIER_BYTES)


def s256_challenge(verifier: str) -> str:
    """S256 transform: base64url(SHA256(verifier)) without padding."""
    digest = hashlib.sha256(verifier.encode()).digest()
    return base64.urlsafe_b64encode(digest).decode().rstrip("=")


def generate_state() -> str:
    """Opaque anti-CSRF state value bound to the pending Redis record."""
    return secrets.token_urlsafe(32)


# ---------------------------------------------------------------------------
# URL builders
# ---------------------------------------------------------------------------


def build_authorize_url(
    *,
    state: str,
    code_challenge: str,
    redirect_uri: str,
    scope: str = "openid profile email offline_access",
    ui_locales: str | None = None,
    prompt: str | None = None,
) -> str:
    params: dict[str, str] = {
        "response_type": "code",
        "client_id": settings.zitadel_portal_client_id,
        "redirect_uri": redirect_uri,
        "scope": scope,
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    }
    if ui_locales:
        params["ui_locales"] = ui_locales
    if prompt:
        params["prompt"] = prompt
    return f"{settings.zitadel_base_url}/oauth/v2/authorize?{urlencode(params)}"


def build_end_session_url(
    *,
    id_token_hint: str,
    post_logout_redirect_uri: str,
    state: str | None = None,
) -> str:
    params: dict[str, str] = {
        "id_token_hint": id_token_hint,
        "post_logout_redirect_uri": post_logout_redirect_uri,
        "client_id": settings.zitadel_portal_client_id,
    }
    if state:
        params["state"] = state
    return f"{settings.zitadel_base_url}/oidc/v1/end_session?{urlencode(params)}"


# ---------------------------------------------------------------------------
# Token exchange + refresh + revoke
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class TokenSet:
    access_token: str
    refresh_token: str
    id_token: str
    expires_in: int
    scope: str | None = None


class OidcFlowError(RuntimeError):
    """Raised when Zitadel rejects a flow step. Carries an OIDC-style code."""

    def __init__(self, code: str, description: str | None = None):
        super().__init__(f"{code}: {description}" if description else code)
        self.code = code
        self.description = description


async def exchange_code_for_tokens(
    *,
    code: str,
    code_verifier: str,
    redirect_uri: str,
) -> TokenSet:
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
        "client_id": settings.zitadel_portal_client_id,
        "code_verifier": code_verifier,
    }
    if settings.zitadel_portal_client_secret:
        data["client_secret"] = settings.zitadel_portal_client_secret
    return await _token_endpoint_post(data)


async def refresh_access_token(refresh_token: str) -> TokenSet:
    data = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": settings.zitadel_portal_client_id,
    }
    if settings.zitadel_portal_client_secret:
        data["client_secret"] = settings.zitadel_portal_client_secret
    return await _token_endpoint_post(data)


async def revoke_token(token: str, *, token_type_hint: str = "refresh_token") -> None:  # noqa: S107
    data = {
        "token": token,
        "token_type_hint": token_type_hint,
        "client_id": settings.zitadel_portal_client_id,
    }
    if settings.zitadel_portal_client_secret:
        data["client_secret"] = settings.zitadel_portal_client_secret
    async with httpx.AsyncClient(base_url=settings.zitadel_base_url, timeout=5.0) as client:
        try:
            await client.post(
                "/oauth/v2/revoke",
                data=data,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
        except httpx.RequestError as exc:
            logger.warning("bff_oidc_revoke_failed", error=str(exc))


async def _token_endpoint_post(data: dict[str, str]) -> TokenSet:
    async with httpx.AsyncClient(base_url=settings.zitadel_base_url, timeout=10.0) as client:
        resp = await client.post(
            "/oauth/v2/token",
            data=data,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
    if resp.status_code >= 400:
        try:
            body = resp.json()
        except ValueError:
            body = {
                "error": "token_endpoint_error",
                "error_description": sanitize_response_body(resp, max_len=200),
            }
        logger.warning(
            "bff_oidc_token_exchange_failed",
            status=resp.status_code,
            error=body.get("error"),
            desc=body.get("error_description"),
        )
        raise OidcFlowError(
            code=body.get("error") or "token_endpoint_error",
            description=body.get("error_description"),
        )

    payload = resp.json()
    return TokenSet(
        access_token=payload["access_token"],
        refresh_token=payload.get("refresh_token", ""),
        id_token=payload.get("id_token", ""),
        expires_in=int(payload.get("expires_in", 0)),
        scope=payload.get("scope"),
    )


# ---------------------------------------------------------------------------
# id_token validation
# ---------------------------------------------------------------------------


_jwks_client: PyJWKClient | None = None


def _get_jwks_client() -> PyJWKClient:
    """Return the process-wide JWKS client.

    Keys are cached in memory with a 1-hour lifespan so Zitadel signing-key
    rotations propagate within an hour even for tokens whose kid was already
    cached. Cache misses (new kid) still trigger an immediate refetch.
    """
    global _jwks_client
    if _jwks_client is None:
        _jwks_client = PyJWKClient(
            f"{settings.zitadel_base_url}/oauth/v2/keys",
            cache_keys=True,
            max_cached_keys=16,
            lifespan=3600,
        )
    return _jwks_client


def verify_id_token(id_token: str) -> dict:
    try:
        signing_key = _get_jwks_client().get_signing_key_from_jwt(id_token).key
        claims = jwt.decode(
            id_token,
            signing_key,
            algorithms=["RS256"],
            audience=settings.zitadel_portal_client_id,
            issuer=settings.zitadel_base_url,
            options={"require": ["sub", "iss", "aud", "exp"]},
        )
    except jwt.PyJWTError as exc:
        logger.warning("bff_oidc_id_token_invalid", error=str(exc))
        raise OidcFlowError("id_token_invalid", str(exc)) from exc
    return claims
