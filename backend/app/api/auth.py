"""
Auth endpoints for the custom login UI.

POST /api/auth/login          — email+password → Zitadel session → OIDC callback URL
POST /api/auth/sso-complete   — reuse portal session to silently complete LibreChat OIDC
POST /api/auth/logout         — clear the SSO cookie

The authRequestId is issued by Zitadel when it redirects to the custom login UI:
  https://my.getklai.com/login?authRequest=<id>

The service account (zitadel_pat) must have the ``IAM_LOGIN_CLIENT`` role in Zitadel
for the finalize step to succeed.

SSO cookie mechanism
--------------------
When a user logs in, the portal stores their Zitadel session (session_id + session_token)
in an in-memory cache and sets a ``klai_sso`` cookie scoped to ``.getklai.com``.

When LibreChat later opens an OIDC flow in an iframe, Zitadel redirects to
``my.getklai.com/login?authRequest=<id>``.  The login page sends the cookie to
``/api/auth/sso-complete``, which reuses the cached session to finalize the auth
request automatically — no second password prompt.
"""
import logging
import secrets
import time

import httpx
from fastapi import APIRouter, Cookie, HTTPException, Response, status
from pydantic import BaseModel, EmailStr

from app.core.config import settings
from app.services.zitadel import zitadel

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["auth"])

# ---------------------------------------------------------------------------
# In-memory SSO session cache
# Maps an opaque token (stored in the klai_sso cookie) to a Zitadel session.
# Single-instance only — good enough for the portal-api deployment.
# ---------------------------------------------------------------------------
_SSO_TTL = 3600  # seconds (1 hour)
_sso_cache: dict[str, dict] = {}


def _create_sso_token(session_id: str, session_token: str) -> str:
    token = secrets.token_urlsafe(32)
    _sso_cache[token] = {
        "session_id": session_id,
        "session_token": session_token,
        "expires_at": time.monotonic() + _SSO_TTL,
    }
    return token


def _get_sso_session(token: str) -> dict | None:
    entry = _sso_cache.get(token)
    if not entry:
        return None
    if time.monotonic() > entry["expires_at"]:
        _sso_cache.pop(token, None)
        return None
    return entry


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------

class LoginRequest(BaseModel):
    email: EmailStr
    password: str
    auth_request_id: str


class LoginResponse(BaseModel):
    callback_url: str


class SSOCompleteRequest(BaseModel):
    auth_request_id: str


class PasswordResetRequest(BaseModel):
    email: EmailStr


class PasswordSetRequest(BaseModel):
    user_id: str
    code: str
    new_password: str


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post("/auth/password/reset", status_code=status.HTTP_204_NO_CONTENT)
async def password_reset(body: PasswordResetRequest) -> None:
    """Send a password reset email. Always returns 204 to prevent email enumeration."""
    try:
        user_id = await zitadel.find_user_id_by_email(body.email)
    except httpx.HTTPStatusError as exc:
        log.error("find_user_id_by_email failed %s: %s", exc.response.status_code, exc.response.text)
        return  # fail silently

    if not user_id:
        return  # unknown email — return 204 silently

    try:
        await zitadel.send_password_reset(user_id)
    except httpx.HTTPStatusError as exc:
        log.error("send_password_reset failed %s: %s", exc.response.status_code, exc.response.text)
        return  # fail silently


@router.post("/auth/password/set", status_code=status.HTTP_204_NO_CONTENT)
async def password_set(body: PasswordSetRequest) -> None:
    """Complete a password reset using the code from the reset email."""
    try:
        await zitadel.set_password_with_code(body.user_id, body.code, body.new_password)
    except httpx.HTTPStatusError as exc:
        log.error("set_password_with_code failed %s: %s", exc.response.status_code, exc.response.text)
        if exc.response.status_code in (400, 404, 410):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Link is verlopen of ongeldig, vraag een nieuwe reset-link aan",
            ) from exc
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Wachtwoord instellen mislukt, probeer het later opnieuw",
        ) from exc


@router.post("/auth/login", response_model=LoginResponse)
async def login(body: LoginRequest, response: Response) -> LoginResponse:
    # 1. Create a Zitadel session by checking email + password
    try:
        session = await zitadel.create_session_with_password(body.email, body.password)
    except httpx.HTTPStatusError as exc:
        log.error("create_session failed %s: %s", exc.response.status_code, exc.response.text)
        if exc.response.status_code in (400, 401, 404, 412):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="E-mailadres of wachtwoord is onjuist",
            ) from exc
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Inloggen mislukt, probeer het later opnieuw",
        ) from exc

    # 2. Finalize the OIDC auth request with the authenticated session
    try:
        callback_url = await zitadel.finalize_auth_request(
            auth_request_id=body.auth_request_id,
            session_id=session["sessionId"],
            session_token=session["sessionToken"],
        )
    except httpx.HTTPStatusError as exc:
        log.error("finalize_auth_request failed %s: %s", exc.response.status_code, exc.response.text)
        if exc.response.status_code == 404:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Inlogverzoek is verlopen, probeer opnieuw",
            ) from exc
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Inloggen mislukt, probeer het later opnieuw",
        ) from exc

    # 3. Store the session in the SSO cache and set a domain-wide cookie so that
    #    subsequent OIDC flows (e.g. LibreChat in an iframe) can be auto-completed.
    sso_token = _create_sso_token(session["sessionId"], session["sessionToken"])
    response.set_cookie(
        key="klai_sso",
        value=sso_token,
        domain=f".{settings.domain}",
        httponly=True,
        secure=True,
        samesite="lax",
        max_age=_SSO_TTL,
    )

    return LoginResponse(callback_url=callback_url)


@router.post("/auth/sso-complete", response_model=LoginResponse)
async def sso_complete(
    body: SSOCompleteRequest,
    klai_sso: str | None = Cookie(default=None),
) -> LoginResponse:
    """Auto-complete a Zitadel OIDC auth request using the portal SSO session.

    Called by the custom login page when it loads inside the LibreChat iframe.
    Returns 401 if no valid SSO session exists (frontend falls back to the login form).
    """
    if not klai_sso:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="No SSO session")

    session_data = _get_sso_session(klai_sso)
    if not session_data:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="SSO session expired")

    try:
        callback_url = await zitadel.finalize_auth_request(
            auth_request_id=body.auth_request_id,
            session_id=session_data["session_id"],
            session_token=session_data["session_token"],
        )
    except httpx.HTTPStatusError as exc:
        log.error("sso finalize failed %s: %s", exc.response.status_code, exc.response.text)
        # Session may have expired in Zitadel — tell the frontend to show the login form
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="SSO session no longer valid",
        ) from exc

    return LoginResponse(callback_url=callback_url)


@router.post("/auth/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(response: Response, klai_sso: str | None = Cookie(default=None)) -> None:
    """Clear the SSO cookie on logout."""
    if klai_sso:
        _sso_cache.pop(klai_sso, None)
    response.delete_cookie(key="klai_sso", domain=f".{settings.domain}")
