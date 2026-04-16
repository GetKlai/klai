"""OAuth provider routes for connector credential acquisition (SPEC-KB-025).

Flow (happy path):
1. Frontend calls GET /api/oauth/providers to learn which providers are enabled.
2. User clicks "Connect Google Drive" — frontend navigates to
   GET /api/oauth/google_drive/authorize?kb_slug=<slug>&connector_id=<uuid>.
3. Backend signs a state blob (connector_id + user_id + provider), sets it as
   an HttpOnly cookie, and 302-redirects the browser to Google's consent page.
4. Google redirects back to GET /api/oauth/google_drive/callback?code=...&state=...
5. Backend verifies state cookie vs query param, exchanges the code for tokens,
   encrypts them via ConnectorCredentialStore (SPEC-KB-020), writes them to
   the connector row, and 302-redirects the user back to the portal frontend.

Security:
- state cookie is Fernet-encrypted (same key as the SSO cookie).
- Tokens are never logged or returned to the browser.
- Encrypted via two-tier KEK→DEK hierarchy before hitting the database.
"""

import json
import logging
import secrets
from typing import Any
from urllib.parse import urlencode

import httpx
from cryptography.fernet import Fernet, InvalidToken
from fastapi import APIRouter, Cookie, Depends, HTTPException, Query, status
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import get_current_user_id
from app.core.config import settings
from app.core.database import get_db, set_tenant
from app.models.connectors import PortalConnector
from app.models.portal import PortalUser
from app.services.connector_credentials import credential_store

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/oauth", tags=["oauth"])


# Supported provider identifiers (connector_type values).
_SUPPORTED_PROVIDERS = {"google_drive"}

# Google Drive OAuth endpoints (constants -- never secrets).
_GOOGLE_AUTHORIZE_URL = "https://accounts.google.com/o/oauth2/v2/auth"
_GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
_GOOGLE_SCOPES = "https://www.googleapis.com/auth/drive.readonly"

_STATE_COOKIE_NAME = "klai_oauth_state"
_STATE_COOKIE_MAX_AGE = 600  # 10 minutes — state is short-lived


def _get_fernet() -> Fernet:
    """Construct a Fernet instance from sso_cookie_key.

    Called per request to pick up any settings monkeypatching in tests.
    """
    key = settings.sso_cookie_key
    if not key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="OAuth not configured",
        )
    return Fernet(key.encode())


# @MX:ANCHOR: [AUTO] Signs and verifies OAuth CSRF state. Shared by authorize + callback.
# @MX:REASON: fan_in>=3 (authorize, callback, tests). Integrity-critical — tamper = silent hijack.
def _sign_state(payload: dict[str, Any]) -> str:
    """Fernet-encrypt+sign a short-lived OAuth state payload."""
    data = json.dumps(payload).encode()
    return _get_fernet().encrypt(data).decode()


def _verify_state(token: str) -> dict[str, Any] | None:
    """Decrypt a state token. Returns None on any validation failure."""
    try:
        raw = _get_fernet().decrypt(token.encode(), ttl=_STATE_COOKIE_MAX_AGE)
        return json.loads(raw)
    except (InvalidToken, ValueError, json.JSONDecodeError):
        return None


def _provider_enabled(provider: str) -> bool:
    """True when the provider has a non-empty client_id configured."""
    if provider == "google_drive":
        return bool(settings.google_drive_client_id)
    return False


def _frontend_redirect_url(path: str) -> str:
    """Build the browser redirect URL for post-callback navigation."""
    base = settings.frontend_url.rstrip("/") if settings.frontend_url else ""
    return f"{base}{path}" if base else path


# ---------------------------------------------------------------------------
# GET /api/oauth/providers
# ---------------------------------------------------------------------------


@router.get("/providers")
async def list_providers(
    user_id: str = Depends(get_current_user_id),
) -> dict[str, dict[str, Any]]:
    """Advertise which OAuth providers are enabled via backend settings.

    Empty client_id means the corresponding provider is disabled; the frontend
    should hide the "Connect" button instead of attempting an authorize flow.
    """
    return {
        "google_drive": {
            "enabled": bool(settings.google_drive_client_id),
            "scopes": [_GOOGLE_SCOPES],
        },
    }


# ---------------------------------------------------------------------------
# GET /api/oauth/{provider}/authorize
# ---------------------------------------------------------------------------


@router.get("/{provider}/authorize")
async def authorize_provider(
    provider: str,
    kb_slug: str = Query(..., description="Knowledge base slug the new connector will sync into"),
    connector_id: str | None = Query(None, description="Existing connector UUID (reconnect flow)"),
    user_id: str = Depends(get_current_user_id),
) -> RedirectResponse:
    """Redirect the user's browser to the provider consent page.

    A signed state token is set as an HttpOnly cookie AND passed in the ?state=
    query parameter. The callback requires both to match.
    """
    if provider not in _SUPPORTED_PROVIDERS or not _provider_enabled(provider):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Provider not enabled")

    # Build signed state. nonce defends against replay even if state TTL is within window.
    state_payload: dict[str, Any] = {
        "provider": provider,
        "user_id": user_id,
        "kb_slug": kb_slug,
        "nonce": secrets.token_urlsafe(16),
    }
    if connector_id and isinstance(connector_id, str):
        state_payload["connector_id"] = connector_id
    state_token = _sign_state(state_payload)

    redirect_uri = _frontend_redirect_url(f"/api/oauth/{provider}/callback")

    # Provider-specific authorize URL construction
    if provider == "google_drive":
        params = {
            "client_id": settings.google_drive_client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": _GOOGLE_SCOPES,
            "access_type": "offline",  # required for refresh_token
            "prompt": "consent",  # force refresh_token issuance on every connect
            "state": state_token,
        }
        authorize_url = f"{_GOOGLE_AUTHORIZE_URL}?{urlencode(params)}"
    else:  # pragma: no cover -- guarded by _SUPPORTED_PROVIDERS above
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unknown provider")

    response = RedirectResponse(url=authorize_url, status_code=status.HTTP_302_FOUND)

    # Cookie is restricted to /api/oauth paths and short-lived.
    cookie_domain = f".{settings.domain}" if settings.domain else None
    response.set_cookie(
        key=_STATE_COOKIE_NAME,
        value=state_token,
        max_age=_STATE_COOKIE_MAX_AGE,
        httponly=True,
        secure=True,
        samesite="lax",
        domain=cookie_domain,
        path="/api/oauth",
    )
    return response


# ---------------------------------------------------------------------------
# GET /api/oauth/{provider}/callback
# ---------------------------------------------------------------------------


# @MX:ANCHOR: [AUTO] External integration point -- Google OAuth callback.
# @MX:REASON: Validates CSRF state, exchanges code for tokens, persists encrypted credentials.
@router.get("/{provider}/callback")
async def callback_provider(
    provider: str,
    code: str = Query(...),
    state: str = Query(...),
    klai_oauth_state: str | None = Cookie(default=None),
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
) -> RedirectResponse:
    """Validate state, exchange code for tokens, encrypt and store on the connector row."""
    if provider not in _SUPPORTED_PROVIDERS:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Provider not enabled")

    # 1. State cookie must be present AND match the ?state= query param.
    if not klai_oauth_state or not secrets.compare_digest(state, klai_oauth_state):
        logger.warning("oauth_callback_state_mismatch: provider=%s", provider)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid OAuth state")

    payload = _verify_state(state)
    if payload is None or payload.get("user_id") != user_id or payload.get("provider") != provider:
        logger.warning("oauth_callback_state_invalid: provider=%s", provider)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid OAuth state")

    # 2. Resolve portal user and ensure the target connector belongs to their org.
    user_row = await db.scalar(select(PortalUser).where(PortalUser.zitadel_user_id == user_id))
    if user_row is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="User not found")

    connector_id = payload.get("connector_id")
    if not connector_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Missing connector_id")

    connector = await db.get(PortalConnector, connector_id)
    if connector is None or connector.org_id != user_row.org_id:
        # 404 (not 403) to avoid leaking existence across tenants.
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Connector not found")
    await set_tenant(db, connector.org_id)

    # 3. Exchange authorization code for tokens. NEVER log the response body.
    if provider == "google_drive":
        token_payload = {
            "code": code,
            "client_id": settings.google_drive_client_id,
            "client_secret": settings.google_drive_client_secret,
            "redirect_uri": _frontend_redirect_url(f"/api/oauth/{provider}/callback"),
            "grant_type": "authorization_code",
        }
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                token_response = await client.post(_GOOGLE_TOKEN_URL, data=token_payload)
                token_response.raise_for_status()
                tokens = token_response.json()
        except httpx.HTTPStatusError as exc:
            logger.warning(
                "oauth_callback_token_exchange_failed: status=%s",
                exc.response.status_code,
            )
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="OAuth token exchange failed",
            ) from exc
    else:  # pragma: no cover -- guarded by _SUPPORTED_PROVIDERS above
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unknown provider")

    # 4. Build config payload for encryption. Keys must match SENSITIVE_FIELDS.
    new_config: dict[str, Any] = dict(connector.config or {})
    new_config["access_token"] = tokens.get("access_token", "")
    if tokens.get("refresh_token"):
        new_config["refresh_token"] = tokens["refresh_token"]
    if tokens.get("expires_in"):
        new_config["token_expiry_seconds"] = tokens["expires_in"]

    # 5. Encrypt credentials via the two-tier KEK→DEK hierarchy (SPEC-KB-020).
    if credential_store is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Credential store not configured",
        )
    encrypted_blob, redacted_config = await credential_store.encrypt_credentials(
        org_id=connector.org_id,
        connector_type=connector.connector_type,
        config=new_config,
        db=db,
    )
    connector.encrypted_credentials = encrypted_blob
    connector.config = redacted_config
    await db.commit()

    logger.info(
        "oauth_callback_success: provider=%s connector_id=%s",
        provider,
        connector_id,
    )

    # 6. Redirect back to the frontend connectors page.
    kb_slug = payload.get("kb_slug", "")
    target = _frontend_redirect_url(f"/app/knowledge/{kb_slug}/connectors?oauth=connected")
    response = RedirectResponse(url=target, status_code=status.HTTP_302_FOUND)
    # Clear the state cookie now that it's been consumed.
    response.delete_cookie(key=_STATE_COOKIE_NAME, path="/api/oauth")
    return response
