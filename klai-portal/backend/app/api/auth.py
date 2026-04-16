"""
Auth endpoints for the custom login UI.

POST /api/auth/login          -- email+password -> Zitadel session -> OIDC callback URL
POST /api/auth/totp-login     -- complete login with TOTP code (when user has 2FA)
POST /api/auth/sso-complete   -- reuse portal session to silently complete LibreChat OIDC
POST /api/auth/logout         -- clear the SSO cookie
POST /api/auth/totp/setup     -- initiate TOTP registration (requires Bearer token)
POST /api/auth/totp/confirm   -- activate TOTP after scanning QR (requires Bearer token)

The authRequestId is issued by Zitadel when it redirects to the custom login UI:
  https://getklai.getklai.com/login?authRequest=<id>

The service account (zitadel_pat) must have the ``IAM_LOGIN_CLIENT`` role in Zitadel
for the finalize step to succeed.

SSO cookie mechanism
--------------------
When a user logs in, the portal encrypts their Zitadel session (session_id + session_token)
into the ``klai_sso`` cookie using Fernet symmetric encryption.  The cookie is scoped to
``.getklai.com`` so all subdomains can send it.

When LibreChat later opens an OIDC flow in an iframe, Zitadel redirects to
``getklai.getklai.com/login?authRequest=<id>``.  The login page sends the cookie to
``/api/auth/sso-complete``, which decrypts it and reuses the session to finalize the auth
request automatically -- no second password prompt.

This is fully stateless on the server side: no in-memory cache, survives restarts, and
scales horizontally.  Zitadel is the sole authority on session validity -- if the session
has expired there, ``finalize_auth_request`` will fail and the user sees the login form.
"""

import asyncio
import json
import logging
import secrets
import time
from urllib.parse import quote, urlparse

import httpx
import structlog
from cryptography.fernet import Fernet
from fastapi import APIRouter, Cookie, Depends, HTTPException, Query, Request, Response, status
from fastapi.responses import RedirectResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, EmailStr, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.models.portal import PortalOrg, PortalOrgAllowedDomain, PortalUser
from app.services import audit
from app.services.events import emit_event
from app.services.zitadel import zitadel

logger = logging.getLogger(__name__)
_slog = structlog.get_logger()

router = APIRouter(prefix="/api", tags=["auth"])
bearer = HTTPBearer()

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
# ---------------------------------------------------------------------------
_fernet = Fernet(settings.sso_cookie_key.encode() if settings.sso_cookie_key else Fernet.generate_key())


def _encrypt_sso(session_id: str, session_token: str) -> str:
    """Encrypt session credentials into an opaque cookie value."""
    payload = json.dumps({"sid": session_id, "stk": session_token}).encode()
    return _fernet.encrypt(payload).decode()


def _decrypt_sso(cookie_value: str) -> dict | None:
    """Decrypt the SSO cookie.  Returns {"sid": ..., "stk": ...} or None."""
    try:
        payload = _fernet.decrypt(cookie_value.encode())
        return json.loads(payload)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Pending TOTP cache
# After password check, store the session here while waiting for the TOTP code.
# ---------------------------------------------------------------------------
_TOTP_PENDING_TTL = 300  # 5 minutes
_TOTP_MAX_FAILURES = 5  # invalidate token after this many wrong codes
_pending_totp = TTLCache(_TOTP_PENDING_TTL)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _validate_callback_url(url: str) -> str:
    """Ensure callback_url points to a trusted domain, not an attacker-controlled one.

    localhost/127.0.0.1 are allowed because they are registered as valid redirect URIs
    in the Zitadel OIDC app (dev mode). Zitadel itself validates the redirect_uri against
    the registered list before returning the callback_url, so this is defense-in-depth only.
    """
    try:
        hostname = urlparse(url).hostname or ""
    except Exception:
        hostname = ""
    # Allow localhost for local development — Zitadel validates redirect URIs at the OIDC layer
    if hostname in ("localhost", "127.0.0.1"):
        return url
    trusted = settings.domain  # getklai.com
    if not (hostname == trusted or hostname.endswith(f".{trusted}")):
        logger.error("callback_url failed validation: %r", url)
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
        resp_text = exc.response.text
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
    return LoginResponse(callback_url=_validate_callback_url(callback_url))


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
    """Send a password reset email. Always returns 204 to prevent email enumeration."""
    try:
        user_id = await zitadel.find_user_id_by_email(body.email)
    except httpx.HTTPStatusError as exc:
        logger.exception("find_user_id_by_email failed %s: %s", exc.response.status_code, exc.response.text)
        return  # fail silently

    if not user_id:
        return  # unknown email — return 204 silently

    try:
        await zitadel.send_password_reset(user_id)
    except httpx.HTTPStatusError as exc:
        logger.exception(  # nosemgrep: python-logger-credential-disclosure
            "send_password_reset failed status=%s", exc.response.status_code
        )
        return  # fail silently


@router.post("/auth/password/set", status_code=status.HTTP_204_NO_CONTENT)
async def password_set(body: PasswordSetRequest) -> None:
    """Complete a password reset using the code from the reset email."""
    try:
        await zitadel.set_password_with_code(body.user_id, body.code, body.new_password)
    except httpx.HTTPStatusError as exc:
        logger.exception(  # nosemgrep: python-logger-credential-disclosure
            "set_password_with_code failed status=%s", exc.response.status_code
        )
        if exc.response.status_code in (400, 404, 410):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Link has expired or is invalid, request a new reset link",
            ) from exc
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Failed to set password, please try again later",
        ) from exc


@router.post("/auth/login", response_model=LoginResponse)
async def login(body: LoginRequest, response: Response, db: AsyncSession = Depends(get_db)) -> LoginResponse:
    # 1. Check if user has TOTP registered
    has_totp = False
    zitadel_user_id: str | None = None
    try:
        user_info = await zitadel.find_user_by_email(body.email)
        if user_info:
            zitadel_user_id, org_id = user_info
            has_totp = await zitadel.has_totp(zitadel_user_id, org_id)
    except httpx.HTTPStatusError as exc:
        logger.warning("TOTP check failed %s — continuing without 2FA check", exc.response.status_code)

    # 2. Create a Zitadel session by checking email + password
    try:
        session = await zitadel.create_session_with_password(body.email, body.password)
    except httpx.HTTPStatusError as exc:
        logger.exception("create_session failed %s: %s", exc.response.status_code, exc.response.text)
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

    # 2b. Enforce MFA policy (NEN 7510: REQ-SEC-001-08)
    portal_user_for_mfa: PortalUser | None = None
    mfa_policy = "optional"
    if zitadel_user_id:
        try:
            portal_user_for_mfa = await db.scalar(
                select(PortalUser).where(PortalUser.zitadel_user_id == zitadel_user_id)
            )
            if portal_user_for_mfa:
                org_for_mfa = await db.get(PortalOrg, portal_user_for_mfa.org_id)
                mfa_policy = org_for_mfa.mfa_policy if org_for_mfa else "optional"
        except Exception:
            logger.warning("MFA policy lookup failed -- defaulting to optional (fail-open)")

        if mfa_policy == "required":
            try:
                user_has_mfa = await zitadel.has_any_mfa(zitadel_user_id)
            except httpx.HTTPStatusError as exc:
                logger.warning(
                    "has_any_mfa check failed %s -- defaulting to pass (fail-open)",
                    exc.response.status_code,
                )
                user_has_mfa = True
            if not user_has_mfa:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="MFA required by your organization. Please set up two-factor authentication.",
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
        logger.warning("Audit log write failed for auth.login (non-fatal)")

    # 3. If the user has TOTP, require a code before finalizing
    if has_totp:
        temp_token = _pending_totp.put(
            {
                "session_id": session["sessionId"],
                "session_token": session["sessionToken"],
                "failures": 0,
            }
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
    """Complete login by providing a TOTP code after password was accepted."""
    pending = _pending_totp.get(body.temp_token)
    if not pending:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Session expired, please log in again",
        )

    # Reject immediately if the token is already locked out
    if pending["failures"] >= _TOTP_MAX_FAILURES:
        _pending_totp.pop(body.temp_token)
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many failed attempts, please log in again",
        )

    # Verify TOTP code by updating the session
    try:
        updated = await zitadel.update_session_with_totp(
            session_id=pending["session_id"],
            session_token=pending["session_token"],
            code=body.code,
        )
    except httpx.HTTPStatusError as exc:
        logger.exception("update_session_with_totp failed %s: %s", exc.response.status_code, exc.response.text)
        if exc.response.status_code in (400, 401):
            pending["failures"] += 1
            await audit.log_event(
                org_id=0,
                actor="unknown",
                action="auth.totp.failed",
                resource_type="session",
                resource_id=pending["session_id"],
                details={"reason": "invalid_code"},
            )
            if pending["failures"] >= _TOTP_MAX_FAILURES:
                _pending_totp.pop(body.temp_token)
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail="Too many failed attempts, please log in again",
                ) from exc
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid code, please try again",
            ) from exc
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

    # Clean up pending token
    _pending_totp.pop(body.temp_token)

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
    """
    if not klai_sso:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="No SSO session")

    session_data = _decrypt_sso(klai_sso)
    if not session_data:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="SSO cookie invalid")

    try:
        callback_url = await zitadel.finalize_auth_request(
            auth_request_id=body.auth_request_id,
            session_id=session_data["sid"],
            session_token=session_data["stk"],
        )
    except httpx.HTTPStatusError as exc:
        logger.exception("sso finalize failed %s: %s", exc.response.status_code, exc.response.text)
        # Session expired in Zitadel -- tell the frontend to show the login form
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="SSO session no longer valid",
        ) from exc

    return LoginResponse(callback_url=_validate_callback_url(callback_url))


@router.post("/auth/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    response: Response,
    klai_sso: str | None = Cookie(default=None),
    db: AsyncSession = Depends(get_db),
) -> None:
    """Clear the SSO cookie on logout."""
    session_data = _decrypt_sso(klai_sso) if klai_sso else None
    session_id = session_data["sid"] if session_data else "unknown"
    await audit.log_event(
        org_id=0,
        actor="unknown",
        action="auth.logout",
        resource_type="session",
        resource_id=session_id,
    )
    response.delete_cookie(key="klai_sso", domain=f".{settings.domain}")


@router.post("/auth/totp/setup", response_model=TOTPSetupResponse)
async def totp_setup(
    user_id: str = Depends(get_current_user_id),
) -> TOTPSetupResponse:
    """Initiate TOTP registration for the logged-in user. Returns QR URI and secret."""
    try:
        result = await zitadel.register_user_totp(user_id)
    except httpx.HTTPStatusError as exc:
        logger.exception("register_user_totp failed %s: %s", exc.response.status_code, exc.response.text)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Failed to set up 2FA, please try again later",
        ) from exc

    return TOTPSetupResponse(uri=result["uri"], secret=result["totpSecret"])


class VerifyEmailRequest(BaseModel):
    user_id: str
    code: str
    org_id: str


@router.post("/auth/verify-email", status_code=status.HTTP_204_NO_CONTENT)
async def verify_email(body: VerifyEmailRequest) -> None:
    """Verify a user's email address using the code from the verification email."""
    try:
        await zitadel.verify_user_email(body.org_id, body.user_id, body.code)
    except httpx.HTTPStatusError as exc:
        logger.exception("verify_user_email failed %s: %s", exc.response.status_code, exc.response.text)
        if exc.response.status_code in (400, 404):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid or expired verification link.",
            ) from exc
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Verification failed, please try again later.",
        ) from exc


@router.post("/auth/totp/confirm", status_code=status.HTTP_204_NO_CONTENT)
async def totp_confirm(
    body: TOTPConfirmRequest,
    user_id: str = Depends(get_current_user_id),
) -> None:
    """Verify and activate the TOTP registration."""
    try:
        await zitadel.verify_user_totp(user_id, body.code)
    except httpx.HTTPStatusError as exc:
        logger.exception("verify_user_totp failed %s: %s", exc.response.status_code, exc.response.text)
        if exc.response.status_code in (400, 401):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid code, please try again",
            ) from exc
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Failed to confirm 2FA, please try again later",
        ) from exc


@router.post("/auth/passkey/setup", response_model=PasskeySetupResponse)
async def passkey_setup(
    request: Request,
    user_id: str = Depends(get_current_user_id),
) -> PasskeySetupResponse:
    """Start WebAuthn passkey registration. Returns options for navigator.credentials.create()."""
    domain = request.headers.get("x-forwarded-host") or request.headers.get("host", settings.domain)
    # Strip port if present
    domain = domain.split(":")[0]
    try:
        result = await zitadel.start_passkey_registration(user_id, domain)
    except httpx.HTTPStatusError as exc:
        logger.exception("start_passkey_registration failed %s: %s", exc.response.status_code, exc.response.text)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Failed to set up passkey, please try again later",
        ) from exc
    return PasskeySetupResponse(
        passkey_id=result["passkeyId"],
        options=result.get("publicKeyCredentialCreationOptions", {}),
    )


@router.post("/auth/passkey/confirm", status_code=status.HTTP_204_NO_CONTENT)
async def passkey_confirm(
    body: PasskeyConfirmRequest,
    user_id: str = Depends(get_current_user_id),
) -> None:
    """Complete passkey registration by submitting the browser's PublicKeyCredential."""
    try:
        await zitadel.verify_passkey_registration(
            user_id, body.passkey_id, body.public_key_credential, body.passkey_name
        )
    except httpx.HTTPStatusError as exc:
        logger.exception("verify_passkey_registration failed %s: %s", exc.response.status_code, exc.response.text)
        if exc.response.status_code in (400, 401):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Passkey verification failed, please try again",
            ) from exc
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Failed to set up passkey, please try again later",
        ) from exc


@router.post("/auth/email-otp/setup", status_code=status.HTTP_204_NO_CONTENT)
async def email_otp_setup(
    user_id: str = Depends(get_current_user_id),
) -> None:
    """Register email OTP for the user. Zitadel sends a verification code to the user's email."""
    try:
        await zitadel.register_email_otp(user_id)
    except httpx.HTTPStatusError as exc:
        logger.exception("register_email_otp failed %s: %s", exc.response.status_code, exc.response.text)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Failed to set up email code, please try again later",
        ) from exc


@router.post("/auth/email-otp/resend", status_code=status.HTTP_204_NO_CONTENT)
async def email_otp_resend(
    user_id: str = Depends(get_current_user_id),
) -> None:
    """Resend the email OTP verification code by removing and re-registering the method."""
    try:
        await zitadel.remove_email_otp(user_id)
    except httpx.HTTPStatusError as exc:
        # If not registered yet, ignore — proceed to register
        if exc.response.status_code != 404:
            logger.exception("remove_email_otp failed %s: %s", exc.response.status_code, exc.response.text)
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Failed to resend email code, please try again later",
            ) from exc
    try:
        await zitadel.register_email_otp(user_id)
    except httpx.HTTPStatusError as exc:
        logger.exception("register_email_otp (resend) failed %s: %s", exc.response.status_code, exc.response.text)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Failed to resend email code, please try again later",
        ) from exc


@router.post("/auth/email-otp/confirm", status_code=status.HTTP_204_NO_CONTENT)
async def email_otp_confirm(
    body: EmailOTPConfirmRequest,
    user_id: str = Depends(get_current_user_id),
) -> None:
    """Verify and activate the email OTP using the code sent during setup."""
    try:
        await zitadel.verify_email_otp(user_id, body.code)
    except httpx.HTTPStatusError as exc:
        logger.exception("verify_email_otp failed %s: %s", exc.response.status_code, exc.response.text)
        if exc.response.status_code in (400, 401):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid code, please try again",
            ) from exc
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Failed to confirm email code, please try again later",
        ) from exc


@router.post("/auth/idp-intent", response_model=IDPIntentResponse)
async def idp_intent(body: IDPIntentRequest) -> IDPIntentResponse:
    """Start a social login flow. Returns the IDP auth URL to redirect the user to."""
    known_idps = {settings.zitadel_idp_google_id, settings.zitadel_idp_microsoft_id} - {""}
    if body.idp_id not in known_idps:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unknown IDP")

    success_url = f"{settings.portal_url}/api/auth/idp-callback?auth_request_id={body.auth_request_id}"
    failure_url = f"{settings.portal_url}/login?authRequest={body.auth_request_id}"

    try:
        result = await zitadel.create_idp_intent(body.idp_id, success_url, failure_url)
    except httpx.HTTPStatusError as exc:
        logger.exception("create_idp_intent failed %s: %s", exc.response.status_code, exc.response.text)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Login failed, please try again later",
        ) from exc

    auth_url = result.get("authUrl")
    if not auth_url:
        logger.error("create_idp_intent returned no authUrl: %s", result)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Login failed, please try again later",
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
        logger.exception("create_session_with_idp_intent failed %s: %s", exc.response.status_code, exc.response.text)
        return RedirectResponse(url=failure_url, status_code=302)
    except Exception:
        logger.exception("create_session_with_idp_intent failed (non-HTTP)")
        return RedirectResponse(url=failure_url, status_code=302)

    session_id: str | None = session.get("sessionId")
    session_token: str | None = session.get("sessionToken")

    if not session_id or not session_token:
        logger.error("create_session_with_idp_intent returned no session: %s", session)
        return RedirectResponse(url=failure_url, status_code=302)

    # Fetch user identity from the session
    try:
        details = await zitadel.get_session_details(session_id, session_token)
    except Exception:
        logger.exception("get_session_details failed — continuing without auto-provision")
        details = {"zitadel_user_id": "", "email": ""}

    zitadel_user_id = details.get("zitadel_user_id", "")
    email = details.get("email", "")

    # Look up existing portal_users rows for this zitadel_user_id
    if zitadel_user_id:
        user_result = await db.execute(select(PortalUser).where(PortalUser.zitadel_user_id == zitadel_user_id))
        existing_users = user_result.scalars().all()
    else:
        existing_users = []

    # C9.3: Multiple orgs → Redis pending-session, redirect to /select-workspace
    if len(existing_users) > 1:
        from app.services.pending_session import PendingSessionService

        try:
            svc = PendingSessionService()
            ref = await svc.store(
                session_id=session_id,
                session_token=session_token,
                zitadel_user_id=zitadel_user_id,
                email=email,
                auth_request_id=auth_request_id,
                org_ids=[u.org_id for u in existing_users],
            )
            return RedirectResponse(url=f"/select-workspace?ref={ref}", status_code=302)
        except Exception:
            _slog.exception("Failed to store pending session — falling through to first org")

    if not existing_users and zitadel_user_id and email:
        # No portal_users row — check allowed domains for auto-provision
        email_domain = email.rsplit("@", 1)[-1].lower() if "@" in email else ""
        if email_domain:
            domain_result = await db.execute(
                select(PortalOrgAllowedDomain).where(PortalOrgAllowedDomain.domain == email_domain)
            )
            matched_domain = domain_result.scalar_one_or_none()

            if matched_domain:
                # C4.4: DB error → log + fall through, never 500
                try:
                    new_user = PortalUser(
                        zitadel_user_id=zitadel_user_id,
                        org_id=matched_domain.org_id,
                        role="member",
                        status="active",
                        display_name=email.split("@")[0],
                        email=email,
                    )
                    db.add(new_user)
                    await db.commit()
                    _slog.info(
                        "Auto-provisioned SSO user",
                        zitadel_user_id=zitadel_user_id,
                        org_id=matched_domain.org_id,
                        domain=email_domain,
                    )
                except Exception:
                    _slog.exception(
                        "Auto-provision failed — user will see no-account page",
                        zitadel_user_id=zitadel_user_id,
                    )
                    await db.rollback()

    # Finalize the auth request (always, even if no portal_users row)
    # The callback.tsx will check org_found and redirect to /no-account if needed
    try:
        callback_url = await zitadel.finalize_auth_request(
            auth_request_id=auth_request_id,
            session_id=session_id,
            session_token=session_token,
        )
    except httpx.HTTPStatusError as exc:
        logger.exception("idp finalize_auth_request failed %s: %s", exc.response.status_code, exc.response.text)
        return RedirectResponse(url=failure_url, status_code=302)

    redirect = RedirectResponse(url=_validate_callback_url(callback_url), status_code=302)
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
    """
    known_idps = {settings.zitadel_idp_google_id, settings.zitadel_idp_microsoft_id} - {""}
    if body.idp_id not in known_idps:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unknown IDP")

    success_url = f"{settings.portal_url}/api/auth/idp-signup-callback?locale={body.locale}"
    failure_url = f"{settings.portal_url}/{body.locale}/signup?error=idp_failed"

    try:
        result = await zitadel.create_idp_intent(body.idp_id, success_url, failure_url)
    except httpx.HTTPStatusError as exc:
        logger.exception("create_idp_intent (signup) failed %s: %s", exc.response.status_code, exc.response.text)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Signup failed, please try again later",
        ) from exc

    auth_url = result.get("authUrl")
    if not auth_url:
        logger.error("create_idp_intent (signup) returned no authUrl: %s", result)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Signup failed, please try again later",
        )

    return IDPIntentResponse(auth_url=auth_url)


@router.get("/auth/idp-signup-callback")
async def idp_signup_callback(
    id: str,
    token: str,
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

    # 1. Retrieve the IDP intent to get user info and optional Zitadel userId
    try:
        intent_data = await zitadel.retrieve_idp_intent(id, token)
    except httpx.HTTPStatusError as exc:
        logger.exception(
            "idp_signup_callback retrieve_idp_intent failed %s: %s",
            exc.response.status_code,
            exc.response.text,
        )
        return RedirectResponse(url=failure_url, status_code=302)

    idp_user_id: str | None = intent_data.get("userId")

    # 1b. New user — no Zitadel account yet. Create one from the IDP profile.
    if not idp_user_id:
        try:
            idp_user_id = await zitadel.create_zitadel_user_from_idp(intent_data, settings.zitadel_portal_org_id)
            logger.info("idp_signup_callback: created Zitadel user %s from IDP", idp_user_id)
        except httpx.HTTPStatusError as exc:
            logger.exception(
                "idp_signup_callback create_zitadel_user failed %s: %s",
                exc.response.status_code,
                exc.response.text,
            )
            return RedirectResponse(url=failure_url, status_code=302)
        except Exception:
            logger.exception("idp_signup_callback create_zitadel_user failed")
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
                logger.warning(
                    "idp_signup_callback create_session 404 on attempt %d, retrying",
                    attempt + 1,
                )
                continue
            logger.exception(
                "idp_signup_callback create_session failed %s: %s",
                exc.response.status_code,
                exc.response.text,
            )
            return RedirectResponse(url=failure_url, status_code=302)
    if session is None:
        logger.error(
            "idp_signup_callback create_session failed after retries: %s",
            last_exc,
        )
        return RedirectResponse(url=failure_url, status_code=302)

    session_id: str | None = session.get("sessionId")
    session_token: str | None = session.get("sessionToken")
    if not session_id or not session_token:
        logger.error("idp_signup_callback: no session in response: %s", session)
        return RedirectResponse(url=failure_url, status_code=302)

    # 2. Fetch full session to get the Zitadel user ID and IDP profile
    try:
        session_detail = await zitadel.get_session(session_id, session_token)
    except httpx.HTTPStatusError as exc:
        logger.exception(
            "idp_signup_callback get_session failed %s: %s",
            exc.response.status_code,
            exc.response.text,
        )
        return RedirectResponse(url=failure_url, status_code=302)

    session_obj = session_detail.get("session", {})
    factors = session_obj.get("factors", {})
    user_factor = factors.get("user", {})
    zitadel_user_id: str = user_factor.get("id", "")
    if not zitadel_user_id:
        logger.error("idp_signup_callback: no user.id in session factors: %s", session_detail)
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
        logger.info("idp_signup_callback: existing user %s, setting SSO cookie", zitadel_user_id)
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
        return response

    # 4. New user — store pending session in encrypted cookie, redirect to company name form
    pending_payload = json.dumps(
        {
            "session_id": session_id,
            "session_token": session_token,
            "zitadel_user_id": zitadel_user_id,
        }
    ).encode()
    encrypted_pending = _fernet.encrypt(pending_payload).decode()

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
