"""
Auth endpoints for the custom login UI.

POST /api/auth/login          -- email+password -> Zitadel session -> OIDC callback URL
POST /api/auth/totp-login     -- complete login with TOTP code (when user has 2FA)
POST /api/auth/sso-complete   -- reuse portal session to silently complete LibreChat OIDC
POST /api/auth/logout         -- clear the SSO cookie
POST /api/auth/totp/setup     -- initiate TOTP registration (requires Bearer token)
POST /api/auth/totp/confirm   -- activate TOTP after scanning QR (requires Bearer token)

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

import json
import logging
import secrets
import time
from urllib.parse import urlparse

import httpx
from cryptography.fernet import Fernet
from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, Response, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, EmailStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.models.portal import PortalOrg, PortalUser
from app.services import audit
from app.services.events import emit_event
from app.services.zitadel import zitadel

logger = logging.getLogger(__name__)

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
_fernet = Fernet(settings.sso_cookie_key.encode())


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
    """Ensure callback_url points to a trusted domain, not an attacker-controlled one."""
    try:
        hostname = urlparse(url).hostname or ""
    except Exception:
        hostname = ""
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
        logger.exception("finalize_auth_request failed %s: %s", exc.response.status_code, exc.response.text)
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
        logger.exception("send_password_reset failed status=%s", exc.response.status_code)  # nosemgrep: python-logger-credential-disclosure
        return  # fail silently


@router.post("/auth/password/set", status_code=status.HTTP_204_NO_CONTENT)
async def password_set(body: PasswordSetRequest) -> None:
    """Complete a password reset using the code from the reset email."""
    try:
        await zitadel.set_password_with_code(body.user_id, body.code, body.new_password)
    except httpx.HTTPStatusError as exc:
        logger.exception("set_password_with_code failed status=%s", exc.response.status_code)  # nosemgrep: python-logger-credential-disclosure
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
                db,
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
            db,
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
                db,
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
        db,
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
        db,
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
