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
from typing import Any
from urllib.parse import quote, urlparse

import httpx
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
from app.core.database import get_db
from app.models.portal import PortalOrg, PortalOrgAllowedDomain, PortalUser
from app.services import audit
from app.services.events import emit_event
from app.services.zitadel import zitadel

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


# @MX:ANCHOR: Trust boundary for OIDC callback URLs returned by Zitadel.
# @MX:REASON: fan_in=3 — called from login() pre-finalize, idp_callback,
#   and sso_complete after every successful finalize. Loosening the
#   trusted-host check (e.g. allowing wildcards or new domain suffixes)
#   opens an open-redirect across the entire auth surface. Coordinate
#   with frontend host config + Caddy redirect rules before changing.
# @MX:SPEC: SPEC-SEC-AUTH-COVERAGE-001 (defense-in-depth on top of
#   Zitadel's OIDC client redirect_uri validation)
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
        _emit_mfa_check_failed(
            reason="db_lookup_failed",
            mfa_policy="optional",
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
        _emit_mfa_check_failed(
            reason="db_lookup_failed",
            mfa_policy="optional",
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
async def login(body: LoginRequest, response: Response, db: AsyncSession = Depends(get_db)) -> LoginResponse:
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
    """Complete login by providing a TOTP code after password was accepted.

    SPEC-SEC-AUTH-COVERAGE-001 REQ-1.6/1.7/1.8: every failure leg
    (expired_token, lockout-immediate, invalid_code, lockout-after-fail,
    zitadel_5xx) emits a ``totp_login_failed`` structured event in addition
    to the existing ``audit.log_event(action="auth.totp.failed")`` call.
    """
    pending = _pending_totp.get(body.temp_token)
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

    # Reject immediately if the token is already locked out
    if pending["failures"] >= _TOTP_MAX_FAILURES:
        _pending_totp.pop(body.temp_token)
        _emit_auth_event(
            "totp_login_failed",
            reason="lockout",
            failures=pending["failures"],
            outcome="429",
            level="error",
        )
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
        _slog.exception("update_session_with_totp_failed", zitadel_status=exc.response.status_code)
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
                _emit_auth_event(
                    "totp_login_failed",
                    reason="lockout",
                    failures=pending["failures"],
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
                failures=pending["failures"],
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

    return LoginResponse(callback_url=_validate_callback_url(callback_url))


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
