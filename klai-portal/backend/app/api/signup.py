"""
POST /api/signup

Creates:
  1. A Zitadel org  (company name → slug)
  2. A human user in that org
  3. Assigns org:owner role to the user (so /api/me returns isAdmin=true)
  4. A portal_orgs + portal_users row in PostgreSQL

Returns 201 on success. The user still needs to verify their email before logging in.

POST /api/signup/social  (SPEC-AUTH-001)

Completes a social signup started via GET /api/auth/idp-signup-callback.
Reads the encrypted klai_idp_pending cookie (Fernet, TTL 10 min) which contains
the pre-created Zitadel session. Only asks for company_name.
"""

import json
import logging
import re
import unicodedata
from typing import Any

import httpx
import structlog
from cryptography.fernet import Fernet, InvalidToken
from fastapi import APIRouter, BackgroundTasks, Cookie, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, EmailStr, field_validator, model_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import invalidate_tenant_slug_cache
from app.core.config import settings
from app.core.database import get_db, set_tenant
from app.models.portal import PortalOrg, PortalUser
from app.services.bff_session import SessionService
from app.services.events import emit_event
from app.services.provisioning import provision_tenant
from app.services.request_ip import resolve_caller_ip_subnet
from app.services.signup_email_rl import check_signup_email_rate_limit
from app.services.zitadel import zitadel

logger = logging.getLogger(__name__)
_slog = structlog.get_logger()

# SPEC-SEC-HYGIENE-001 REQ-22: zxcvbn-backed password-strength check.
# Pure Python (no native extensions); MIT-licensed. If the import ever
# fails (misconfigured deployment, future drop), fall back to length-only
# at REQ-22.4. _ZXCVBN_AVAILABLE is module-level so tests can monkey-patch
# the unavailable path without breaking the import.
try:
    from zxcvbn import zxcvbn as _zxcvbn

    _ZXCVBN_AVAILABLE = True
except ImportError:
    _zxcvbn = None  # type: ignore[assignment]
    _ZXCVBN_AVAILABLE = False
    logger.exception("zxcvbn_unavailable_falling_back_to_length_check")

# REQ-22.1: zxcvbn 0-4 scale; reject score < 3.
_ZXCVBN_MIN_SCORE = 3
_PASSWORD_TOO_WEAK_MSG = "Wachtwoord is te zwak. Kies een langer of minder voorspelbaar wachtwoord."

_IDP_PENDING_COOKIE = "klai_idp_pending"
_IDP_PENDING_MAX_AGE = 600  # 10 minutes — must match auth.py

router = APIRouter(prefix="/api", tags=["auth"])


class SignupRequest(BaseModel):
    first_name: str
    last_name: str
    email: EmailStr
    password: str
    company_name: str
    preferred_language: str = "nl"

    @field_validator("company_name", "first_name", "last_name")
    @classmethod
    def not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("Field cannot be empty")
        return v.strip()

    @field_validator("preferred_language")
    @classmethod
    def valid_language(cls, v: str) -> str:
        return v if v in ("nl", "en") else "nl"

    @model_validator(mode="after")
    def password_strength(self) -> "SignupRequest":
        """SPEC-SEC-HYGIENE-001 REQ-22: length floor + zxcvbn score floor.

        REQ-22.2: minimum length of 12 characters is the FIRST gate (fast
        path; zxcvbn is only invoked if length passes).

        REQ-22.1, REQ-22.3: zxcvbn is invoked with the user's email,
        first_name, last_name, and company_name as ``user_inputs`` so a
        password derived from the user's own PII (e.g. "Voys2026Klai" for
        company "Voys") scores low against itself.

        REQ-22.4: if zxcvbn is unavailable (import failed at module load —
        misconfigured deployment), fall back to the length-only check and
        rely on the module-load error log to surface the degradation.
        """
        if len(self.password) < 12:
            raise ValueError("Wachtwoord moet minimaal 12 tekens bevatten")
        if not _ZXCVBN_AVAILABLE or _zxcvbn is None:
            return self
        result = _zxcvbn(
            self.password,
            user_inputs=[self.email, self.first_name, self.last_name, self.company_name],
        )
        if int(result.get("score", 0)) < _ZXCVBN_MIN_SCORE:
            raise ValueError(_PASSWORD_TOO_WEAK_MSG)
        return self


class SignupResponse(BaseModel):
    org_id: str
    user_id: str
    message: str


def _slugify(name: str) -> str:
    """Convert company name to a Zitadel-safe org name."""
    name = unicodedata.normalize("NFKD", name)
    name = name.encode("ascii", "ignore").decode()
    name = re.sub(r"[^a-zA-Z0-9\s-]", "", name)
    name = re.sub(r"\s+", " ", name).strip()
    return name[:60] if name else "org"


def _to_slug(name: str, suffix: str = "") -> str:
    """Convert company name to a unique URL slug (lowercase, dashes)."""
    base = _slugify(name).lower()
    base = re.sub(r"[^a-z0-9]+", "-", base).strip("-")
    if not base:
        base = "org"
    if suffix:
        base = f"{base}-{suffix[:8]}"
    return base[:64]


@router.post("/signup", response_model=SignupResponse, status_code=status.HTTP_201_CREATED)
async def signup(
    body: SignupRequest, background_tasks: BackgroundTasks, db: AsyncSession = Depends(get_db)
) -> SignupResponse:
    # SPEC-SEC-HYGIENE-001 REQ-19.5: per-email rate-limit check runs AFTER
    # Pydantic validation (so malformed emails never hit Redis) and BEFORE
    # Zitadel org-creation (so rejected attempts never consume Zitadel quota).
    # Fail-open on Redis unreachable — see REQ-19.4 + check_signup_email_rate_limit.
    if not await check_signup_email_rate_limit(body.email):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many signup attempts for this email. Please try again tomorrow.",
        )

    # 1. Create Zitadel org
    try:
        org_data = await zitadel.create_org(_slugify(body.company_name))
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 409:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="This company name is already in use. Please try a different name.",
            ) from exc
        logger.exception("Org creation failed for %s: %s", body.company_name, exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Creation failed, please try again later",
        ) from exc
    except Exception as exc:
        logger.exception("Org creation failed for %s: %s", body.company_name, exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Creation failed, please try again later",
        ) from exc

    zitadel_org_id: str = org_data["id"]
    logger.info("Org created in Zitadel: name=%s, org_id=%s", body.company_name, zitadel_org_id)

    # 2. Create human user in the portal org (all users live here for OIDC compatibility)
    try:
        user_data = await zitadel.create_human_user(
            org_id=settings.zitadel_portal_org_id,
            email=body.email,
            first_name=body.first_name,
            last_name=body.last_name,
            password=body.password,
            preferred_language=body.preferred_language,
        )
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 409:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="This email address is already registered. Please try logging in.",
            ) from exc
        logger.exception("User creation failed during signup for org %s: %s", body.company_name, exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Creation failed, please try again later",
        ) from exc
    except Exception as exc:
        logger.exception("User creation failed during signup for org %s: %s", body.company_name, exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Creation failed, please try again later",
        ) from exc

    zitadel_user_id: str = user_data["userId"]

    # 3. Assign org:owner role in the portal org's project
    try:
        await zitadel.grant_user_role(
            org_id=settings.zitadel_portal_org_id,
            user_id=zitadel_user_id,
            role="org:owner",
        )
    except Exception as exc:
        logger.exception("Role grant failed during signup for user %s: %s", body.email, exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Creation failed, please try again later",
        ) from exc

    # 4. Persist to PostgreSQL
    try:
        org_row = PortalOrg(
            zitadel_org_id=zitadel_org_id,
            name=body.company_name,
            slug=_to_slug(body.company_name, zitadel_org_id),
        )
        db.add(org_row)
        await db.flush()  # get org_row.id without committing yet

        # Set tenant context so the portal_users RLS policy passes for the INSERT.
        await set_tenant(db, org_row.id)

        user_row = PortalUser(
            zitadel_user_id=zitadel_user_id,
            org_id=org_row.id,
            role="admin",  # org creator is always admin
            preferred_language=body.preferred_language,
        )
        db.add(user_row)
        await db.commit()
    except Exception as exc:
        await db.rollback()
        logger.exception("DB commit failed during signup for org %s: %s", body.company_name, exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Creation failed, please try again later",
        ) from exc

    # SPEC-SEC-HYGIENE-001 REQ-20.2: invalidate the tenant-slug cache so the
    # callback-URL allowlist picks up the new slug immediately (rather than
    # waiting for the 60s TTL to expire).
    invalidate_tenant_slug_cache()

    logger.info("Provisioning queued for org_id=%d, slug=%s", org_row.id, org_row.slug)
    background_tasks.add_task(provision_tenant, org_row.id)
    emit_event("signup", org_id=org_row.id, user_id=zitadel_user_id, properties={"plan": org_row.plan})

    return SignupResponse(
        org_id=zitadel_org_id,
        user_id=zitadel_user_id,
        message="Account created. Check your email to confirm your account.",
    )


# ---------------------------------------------------------------------------
# Social signup completion (SPEC-AUTH-001)
# ---------------------------------------------------------------------------


class SocialSignupRequest(BaseModel):
    company_name: str

    @field_validator("company_name")
    @classmethod
    def not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("Field cannot be empty")
        return v.strip()


class SocialSignupResponse(BaseModel):
    org_id: str
    user_id: str
    redirect_url: str


def _get_fernet() -> Fernet:
    key = settings.sso_cookie_key
    if not key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Social signup not configured",
        )
    return Fernet(key.encode())


def _verify_idp_pending_binding(payload: dict[str, Any], request: Request) -> None:
    """SPEC-SEC-SESSION-001 REQ-2.2: enforce browser + IP-subnet binding.

    Compares the ``ua_hash`` and ``ip_subnet`` fields stored in the encrypted
    ``klai_idp_pending`` cookie against the values derived from the current
    request. Mismatch → HTTP 403 + structlog ``idp_pending_binding_mismatch``
    at ``warning`` level. The original cookie is left intact (caller does
    not delete it) so the legitimate user can resume their flow within the
    TTL.

    A payload without the binding fields is treated as either pre-deploy
    legacy or tampered: same 403, no binding metadata to compare.

    Raises:
        HTTPException(403): on any binding mismatch or missing field.
    """
    stored_ua_hash = payload.get("ua_hash")
    stored_ip_subnet = payload.get("ip_subnet")
    if stored_ua_hash is None or stored_ip_subnet is None:
        # No binding fields → cannot verify → reject. PII-safe log: no payload
        # contents are dumped to avoid leaking session ids on the rare path
        # where the cookie was tampered with.
        _slog.warning("idp_pending_binding_mismatch", reason="missing_binding_fields")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Signup session binding mismatch, please start over",
        )

    current_ua_hash = SessionService.hash_metadata(request.headers.get("user-agent"))
    current_ip_subnet = resolve_caller_ip_subnet(request)

    if stored_ua_hash != current_ua_hash or stored_ip_subnet != current_ip_subnet:
        # REQ-2.2: log only the first 8 chars of each hash + the subnet
        # network address. Never the raw UA, never the raw IP, never the
        # session credentials.
        _slog.warning(
            "idp_pending_binding_mismatch",
            stored_ua_hash_prefix=stored_ua_hash[:8],
            current_ua_hash_prefix=current_ua_hash[:8],
            stored_ip_subnet=stored_ip_subnet,
            current_ip_subnet=current_ip_subnet,
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Signup session binding mismatch, please start over",
        )


@router.post("/signup/social", response_model=SocialSignupResponse, status_code=status.HTTP_201_CREATED)
async def signup_social(
    body: SocialSignupRequest,
    response: Response,
    background_tasks: BackgroundTasks,
    request: Request,
    db: AsyncSession = Depends(get_db),
    klai_idp_pending: str | None = Cookie(default=None),
) -> SocialSignupResponse:
    """Complete a social signup started via GET /api/auth/idp-signup-callback.

    Reads the encrypted klai_idp_pending cookie which contains the IDP session.
    Creates the Klai org, grants the owner role, creates DB rows, kicks off
    provisioning, and sets the SSO cookie so the user is immediately logged in.
    """
    # 1. Verify the pending cookie
    if not klai_idp_pending:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Social signup session expired. Please try again.",
        )
    try:
        raw = _get_fernet().decrypt(klai_idp_pending.encode(), ttl=_IDP_PENDING_MAX_AGE)
        pending = json.loads(raw)
    except (InvalidToken, ValueError, json.JSONDecodeError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Social signup session expired. Please try again.",
        ) from exc

    # SPEC-SEC-SESSION-001 REQ-2.5: binding check runs AFTER the Fernet TTL
    # decrypt succeeds. Mismatch returns 403 with no extra information about
    # whether the cookie was otherwise valid.
    _verify_idp_pending_binding(pending, request)

    session_id: str = pending.get("session_id", "")
    session_token: str = pending.get("session_token", "")
    zitadel_user_id: str = pending.get("zitadel_user_id", "")

    if not session_id or not session_token or not zitadel_user_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Social signup session expired. Please try again.",
        )

    # 2. Create Zitadel org
    try:
        org_data = await zitadel.create_org(_slugify(body.company_name))
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 409:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="This company name is already in use. Please try a different name.",
            ) from exc
        logger.exception("Social signup: org creation failed for %s: %s", body.company_name, exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Creation failed, please try again later",
        ) from exc
    except Exception as exc:
        logger.exception("Social signup: org creation failed for %s: %s", body.company_name, exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Creation failed, please try again later",
        ) from exc

    zitadel_org_id: str = org_data["id"]
    logger.info(
        "Social signup: org created in Zitadel: name=%s, org_id=%s, user_id=%s",
        body.company_name,
        zitadel_org_id,
        zitadel_user_id,
    )

    # 3. Assign org:owner role in the portal org's project
    try:
        await zitadel.grant_user_role(
            org_id=settings.zitadel_portal_org_id,
            user_id=zitadel_user_id,
            role="org:owner",
        )
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 409:
            # Grant already exists from a previous partial attempt — safe to continue.
            logger.warning("Social signup: role grant already exists for user %s, continuing", zitadel_user_id)
        else:
            logger.exception("Social signup: role grant failed for user %s: %s", zitadel_user_id, exc)
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Creation failed, please try again later",
            ) from exc
    except Exception as exc:
        logger.exception("Social signup: role grant failed for user %s: %s", zitadel_user_id, exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Creation failed, please try again later",
        ) from exc

    # 4. Persist to PostgreSQL
    try:
        org_row = PortalOrg(
            zitadel_org_id=zitadel_org_id,
            name=body.company_name,
            slug=_to_slug(body.company_name, zitadel_org_id),
        )
        db.add(org_row)
        await db.flush()

        # Set tenant context so the portal_users RLS policy passes for the INSERT.
        await set_tenant(db, org_row.id)

        user_row = PortalUser(
            zitadel_user_id=zitadel_user_id,
            org_id=org_row.id,
            role="admin",
        )
        db.add(user_row)
        await db.commit()
    except Exception as exc:
        await db.rollback()
        logger.exception("Social signup: DB commit failed for org %s: %s", body.company_name, exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Creation failed, please try again later",
        ) from exc

    # SPEC-SEC-HYGIENE-001 REQ-20.2: invalidate tenant-slug cache (see signup() above).
    invalidate_tenant_slug_cache()

    # 5. Start provisioning
    logger.info("Social signup: provisioning queued for org_id=%d, slug=%s", org_row.id, org_row.slug)
    background_tasks.add_task(provision_tenant, org_row.id)
    emit_event(
        "signup", org_id=org_row.id, user_id=zitadel_user_id, properties={"plan": org_row.plan, "method": "social"}
    )

    # 6. Set SSO cookie so the user is immediately logged in via sso-complete
    _sso_payload = json.dumps({"sid": session_id, "stk": session_token}).encode()
    _sso_value = _get_fernet().encrypt(_sso_payload).decode()
    response.set_cookie(
        key="klai_sso",
        value=_sso_value,
        domain=f".{settings.domain}",
        httponly=True,
        secure=True,
        samesite="lax",
        max_age=settings.sso_cookie_max_age,
    )

    # 7. Clear the pending cookie
    response.delete_cookie(key=_IDP_PENDING_COOKIE, path="/")

    return SocialSignupResponse(
        org_id=zitadel_org_id,
        user_id=zitadel_user_id,
        redirect_url="/",
    )
