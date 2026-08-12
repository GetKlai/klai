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
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

import httpx
import structlog
from cryptography.fernet import Fernet, InvalidToken
from fastapi import APIRouter, BackgroundTasks, Cookie, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, EmailStr, field_validator, model_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import invalidate_tenant_slug_cache
from app.core.config import settings
from app.core.database import get_db, set_tenant
from app.core.password_policy import (
    ZITADEL_PASSWORD_POLICY_MSG,
    is_zitadel_password_policy_error,
    validate_password_strength,
)
from app.core.provisioning_names import TENANT_SLUG_MAX_LENGTH, validate_slug_for_provisioning
from app.core.seats import suggest_seat
from app.models.portal import PortalJoinRequest, PortalOrg, PortalUser
from app.services.auth_links import AuthLinkRoute, build_url_template
from app.services.bff_session import SessionService
from app.services.domain_match import find_domain_match_orgs
from app.services.domain_validation import is_free_email_provider, primary_domain_for_email_domain
from app.services.events import emit_event
from app.services.join_request_token import generate_approval_token
from app.services.password_policy_guard import PasswordPolicyGuardError, assert_zitadel_password_policy_compatible
from app.services.provisioning import provision_tenant
from app.services.request_ip import resolve_caller_ip_subnet
from app.services.signup_email_rl import check_signup_email_rate_limit
from app.services.waitlist_token import verify_invite_token
from app.services.zitadel import zitadel

logger = logging.getLogger(__name__)
_slog = structlog.get_logger()

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
    # SPEC-LAUNCH-SOFTLAUNCH-001 B-2: optional waitlist invite token.
    # When present and valid, bypasses the free-email-provider block (B-3).
    invite_token: str | None = None
    # SPEC-AUTH-010 R4: two-phase domain-match. None = phase 1 (server answers
    # with kind=domain_match when the email domain matches an existing
    # workspace, no side effects). "join" = create the user only (no org);
    # "create" = explicit escape hatch, always create a new workspace.
    domain_choice: Literal["join", "create"] | None = None
    # SPEC-AUTH-010 R5: founder's choice to let same-domain colleagues join
    # without approval. Server-side guarded: only honoured when the workspace
    # gets a non-empty primary_domain.
    auto_accept_same_domain: bool = False

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

        REQ-22.2: minimum length is the FIRST gate (fast
        path; zxcvbn is only invoked if length passes).

        REQ-22.1, REQ-22.3: zxcvbn is invoked with the user's email,
        first_name, last_name, and company_name as ``user_inputs`` so a
        password derived from the user's own PII (e.g. "Voys2026Klai" for
        company "Voys") scores low against itself.

        REQ-22.4: if zxcvbn is unavailable (misconfigured deployment), the
        password policy module fails loud instead of silently weakening server
        validation.
        """
        validate_password_strength(
            self.password,
            user_inputs=[self.email, self.first_name, self.last_name, self.company_name],
        )
        return self


class SignupResponse(BaseModel):
    # SPEC-AUTH-010 R4: discriminated by kind.
    #   created      -> org_id + user_id set (pre-SPEC shape, default)
    #   domain_match -> domain set, no side effects happened (phase 1)
    #   join_pending -> user_id set; user verifies email, then joins via login
    kind: Literal["created", "domain_match", "join_pending"] = "created"
    org_id: str | None = None
    user_id: str | None = None
    message: str = ""
    domain: str | None = None


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
        suffix_part = f"-{suffix[:8]}"
        base = base[: TENANT_SLUG_MAX_LENGTH - len(suffix_part)].strip("-") or "org"
        return f"{base}{suffix_part}"
    return base[:TENANT_SLUG_MAX_LENGTH].strip("-") or "org"


async def _sync_signup_to_mailing(
    *,
    email: str,
    name: str,
    company: str,
    org_id: int,
    portal_user_id: int | None,
    zitadel_user_id: str,
    source: str,
) -> None:
    from app.services.listmonk import sync_portal_user_best_effort

    await sync_portal_user_best_effort(
        email=email,
        name=name,
        company=company,
        org_id=org_id,
        portal_user_id=portal_user_id,
        zitadel_user_id=zitadel_user_id,
        source=source,
    )


async def _assert_signup_password_policy_ready() -> None:
    try:
        await assert_zitadel_password_policy_compatible()
    except PasswordPolicyGuardError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Wachtwoordbeleid is tijdelijk niet beschikbaar. Probeer later opnieuw.",
        ) from exc


def _validate_invite_token_or_400(invite_token: str | None, email_norm: str, email_domain: str) -> bool:
    """SPEC-LAUNCH-SOFTLAUNCH-001 B-3: validate an optional waitlist invite.

    Returns True when the token is valid for the submitted email. Raises 400
    when a token was supplied but is expired or bound to a different email.
    """
    if not invite_token:
        return False
    payload = verify_invite_token(invite_token)
    if payload is not None and payload.email == email_norm:
        _slog.info(
            "signup_invite_token_accepted",
            email_domain=email_domain,
            company_from_token=payload.company,
        )
        return True
    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail=("Uitnodigingslink is verlopen of klopt niet bij dit e-mailadres. Vraag een nieuwe link aan."),
    )


async def _signup_domain_match_flow(
    body: SignupRequest,
    claimable_domain: str,
    db: AsyncSession,
) -> SignupResponse | None:
    """SPEC-AUTH-010 R4: two-phase domain-match for password signup.

    Returns None when no workspace matches the email domain (caller continues
    with the normal create-workspace path). Otherwise:

    - Phase 1 (``domain_choice is None``): kind=domain_match, no side effects.
      The email is unproven at this point, so only the boolean fact that a
      workspace exists is disclosed — never names (C4.1).
    - Phase 2 (``domain_choice == "join"``): create the Zitadel user with a
      verification mail, but NO tenant org and NO portal_users row. After the
      user verifies and logs in, the R7 login routing offers the picker.
    """
    domain_orgs = await find_domain_match_orgs(db, str(body.email))
    if not domain_orgs:
        if body.domain_choice == "join":
            # The user explicitly chose to JOIN, but the matched workspace
            # disappeared between phase 1 and phase 2. Never silently fall
            # through to creating a workspace they did not ask for.
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "De werkruimte voor dit domein is niet meer beschikbaar. "
                    "Start een eigen werkruimte of probeer het later opnieuw."
                ),
            )
        return None

    if body.domain_choice is None:
        _slog.info("signup_domain_match_choice_offered", email_domain=claimable_domain)
        return SignupResponse(
            kind="domain_match",
            domain=claimable_domain,
            message="A workspace already exists for this email domain.",
        )

    verify_url_template = build_url_template(AuthLinkRoute.VERIFY_EMAIL)
    try:
        user_data = await zitadel.create_human_user_v2_with_verify(
            org_id=settings.zitadel_portal_org_id,
            email=body.email,
            first_name=body.first_name,
            last_name=body.last_name,
            password=body.password,
            preferred_language=body.preferred_language,
            url_template=verify_url_template,
        )
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 409:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="This email address is already registered. Please try logging in.",
            ) from exc
        if is_zitadel_password_policy_error(exc):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=ZITADEL_PASSWORD_POLICY_MSG,
            ) from exc
        logger.exception("User creation failed during join-signup for %s: %s", claimable_domain, exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Creation failed, please try again later",
        ) from exc
    except Exception as exc:
        logger.exception("User creation failed during join-signup for %s: %s", claimable_domain, exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Creation failed, please try again later",
        ) from exc

    join_user_id: str = user_data["userId"]
    _slog.info("signup_domain_join_pending", email_domain=claimable_domain)
    emit_event(
        "signup",
        user_id=join_user_id,
        properties={"method": "password", "domain_join_pending": True},
    )
    return SignupResponse(
        kind="join_pending",
        user_id=join_user_id,
        message="Account created. Check your email to confirm your account.",
    )


@router.post("/signup", response_model=SignupResponse, status_code=status.HTTP_201_CREATED)
async def signup(
    body: SignupRequest, background_tasks: BackgroundTasks, db: AsyncSession = Depends(get_db)
) -> SignupResponse:
    # SPEC-AUTH-009 R1/R7 C1.3: reject free-email domains before any Zitadel/DB work.
    # C7.2: NL message instructs the user to use a company email or request an invitation.
    # SPEC-LAUNCH-SOFTLAUNCH-001 B-3: bypass the free-email block when the
    # request carries a valid invite_token whose embedded email matches the
    # submitted email.
    _email_domain = body.email.split("@")[-1].strip().lower()
    _submitted_email_norm = body.email.strip().lower()
    _has_valid_invite = _validate_invite_token_or_400(body.invite_token, _submitted_email_norm, _email_domain)

    if not _has_valid_invite and is_free_email_provider(_email_domain):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "Klai-werkruimtes kun je alleen aanmaken met een zakelijk mailadres. "
                "Vraag je beheerder om een uitnodiging als je via een privé-mailadres "
                "wilt deelnemen."
            ),
        )

    await _assert_signup_password_policy_ready()

    # SPEC-SEC-HYGIENE-001 REQ-19.5: rate-limit only attempts that can reach
    # Zitadel. Rejected free-email attempts should not poison a later valid
    # invite retry, and a verified invite is already a narrow allowlist gate.
    if not _has_valid_invite and not await check_signup_email_rate_limit(body.email):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many signup attempts for this email. Please wait a while and try again.",
        )

    # SPEC-AUTH-010 R4: two-phase domain-match. Runs after the free-email and
    # rate-limit gates (C4.1) and before any Zitadel/DB write. Invited users
    # skip it: an invite is an explicit workspace-create allowlist.
    _claimable_domain = primary_domain_for_email_domain(_email_domain)
    if not _has_valid_invite and _claimable_domain and body.domain_choice != "create":
        domain_match_response = await _signup_domain_match_flow(body, _claimable_domain, db)
        if domain_match_response is not None:
            return domain_match_response

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

    # Orphan-prevention: any failure AFTER create_org must cascade-delete the
    # Zitadel org, else a half-built tenant leaks and the user cannot retry
    # (the org name 409s on "already exists"). delete_org is idempotent and
    # cascades users + grants.
    async def _rollback_zitadel_org() -> None:
        with suppress(Exception):
            await zitadel.delete_org(zitadel_org_id)

    # 2. Create human user via Zitadel v2, atomically firing a Klai-branded
    # email-verification mail. The link lands on /verify, not Zitadel's hosted
    # init UI.
    verify_url_template = build_url_template(AuthLinkRoute.VERIFY_EMAIL)
    try:
        user_data = await zitadel.create_human_user_v2_with_verify(
            org_id=settings.zitadel_portal_org_id,
            email=body.email,
            first_name=body.first_name,
            last_name=body.last_name,
            password=body.password,
            preferred_language=body.preferred_language,
            url_template=verify_url_template,
        )
    except httpx.HTTPStatusError as exc:
        await _rollback_zitadel_org()
        if exc.response.status_code == 409:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="This email address is already registered. Please try logging in.",
            ) from exc
        if is_zitadel_password_policy_error(exc):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=ZITADEL_PASSWORD_POLICY_MSG,
            ) from exc
        logger.exception("User creation failed during signup for org %s: %s", body.company_name, exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Creation failed, please try again later",
        ) from exc
    except Exception as exc:
        await _rollback_zitadel_org()
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
        await _rollback_zitadel_org()
        logger.exception("Role grant failed during signup for user %s: %s", body.email, exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Creation failed, please try again later",
        ) from exc

    # 4. Persist to PostgreSQL
    try:
        org_slug = _to_slug(body.company_name, zitadel_org_id)
        validate_slug_for_provisioning(org_slug, domain=settings.domain)
        org_row = PortalOrg(
            zitadel_org_id=zitadel_org_id,
            name=body.company_name,
            slug=org_slug,
            plan="knowledge",
            primary_domain=_claimable_domain,
            # SPEC-AUTH-010 R5/C5.1: founder's checkbox, honoured only when the
            # workspace actually claims a domain (free-email/invite → False).
            auto_accept_same_domain=bool(body.auto_accept_same_domain and _claimable_domain),
        )
        db.add(org_row)
        await db.flush()  # get org_row.id without committing yet

        # Set tenant context so the portal_users RLS policy passes for the INSERT.
        await set_tenant(db, org_row.id)

        user_row = PortalUser(
            zitadel_user_id=zitadel_user_id,
            org_id=org_row.id,
            role="admin",  # org creator is always admin
            seat_type=str(suggest_seat("admin")),
            preferred_language=body.preferred_language,
        )
        db.add(user_row)
        await db.commit()
    except Exception as exc:
        await db.rollback()
        await _rollback_zitadel_org()
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
    background_tasks.add_task(
        _sync_signup_to_mailing,
        email=str(body.email),
        name=f"{body.first_name} {body.last_name}".strip(),
        company=body.company_name,
        org_id=org_row.id,
        portal_user_id=getattr(user_row, "id", None),
        zitadel_user_id=zitadel_user_id,
        source="portal_signup",
    )
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
    # SPEC-AUTH-010 R1/C1.3: explicit escape hatch — skip the domain-match
    # branch and create a new workspace even when colleagues already have one.
    create_new_workspace: bool = False
    # SPEC-AUTH-010 R5: founder's auto-accept choice (server-side guarded).
    auto_accept_same_domain: bool = False

    @field_validator("company_name")
    @classmethod
    def not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("Field cannot be empty")
        return v.strip()


class SocialSignupResponse(BaseModel):
    kind: Literal["created"] = "created"
    org_id: str
    user_id: str
    redirect_url: str


class SocialSignupDomainMatchOrg(BaseModel):
    org_id: int
    name: str
    auto_accept: bool


class SocialSignupDomainMatch(BaseModel):
    # SPEC-AUTH-010 R1: email is IdP-verified at this point (C1.1), so
    # workspace names may be disclosed.
    kind: Literal["domain_match"] = "domain_match"
    domain: str
    orgs: list[SocialSignupDomainMatchOrg]


class SocialJoinRequestBody(BaseModel):
    org_id: int


class SocialJoinAutoResponse(BaseModel):
    kind: Literal["auto_join"] = "auto_join"
    redirect_url: str


class SocialJoinPendingResponse(BaseModel):
    kind: Literal["join_request_pending"] = "join_request_pending"
    redirect_to: str


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


def _decode_idp_pending_or_400(klai_idp_pending: str | None, request: Request) -> dict[str, Any]:
    """Decrypt + binding-check the klai_idp_pending cookie.

    Shared by /signup/social and /signup/social/join. Raises 400 on a
    missing/expired/tampered cookie; 403 on a binding mismatch
    (SPEC-SEC-SESSION-001 REQ-2.2/2.5 — binding check runs AFTER the Fernet
    TTL decrypt succeeds).
    """
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

    _verify_idp_pending_binding(pending, request)
    return pending


def _set_klai_sso_cookie(response: Response, session_id: str, session_token: str) -> None:
    """Set the klai_sso cookie so the user is logged in via sso-complete."""
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


@router.post(
    "/signup/social",
    response_model=SocialSignupResponse | SocialSignupDomainMatch,
    status_code=status.HTTP_201_CREATED,
)
async def signup_social(
    body: SocialSignupRequest,
    response: Response,
    background_tasks: BackgroundTasks,
    request: Request,
    db: AsyncSession = Depends(get_db),
    klai_idp_pending: str | None = Cookie(default=None),
) -> SocialSignupResponse | SocialSignupDomainMatch:
    """Complete a social signup started via GET /api/auth/idp-signup-callback.

    Reads the encrypted klai_idp_pending cookie which contains the IDP session.
    When the IdP-verified email domain matches an existing workspace and the
    request does not opt out, returns kind=domain_match instead of creating
    anything (SPEC-AUTH-010 R1). Otherwise creates the Klai org, grants the
    owner role, creates DB rows, kicks off provisioning, and sets the SSO
    cookie so the user is immediately logged in.
    """
    pending = _decode_idp_pending_or_400(klai_idp_pending, request)

    session_id: str = pending.get("session_id", "")
    session_token: str = pending.get("session_token", "")
    zitadel_user_id: str = pending.get("zitadel_user_id", "")

    if not session_id or not session_token or not zitadel_user_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Social signup session expired. Please try again.",
        )

    # Social signup gets a verified email from the IDP. Free-email providers are
    # allowed to create a workspace, but primary_domain_for_email_domain() below
    # returns "" so gmail.com/hotmail/etc. can never be claimed for domain-match
    # or auto-join.
    _social_email = pending.get("email", "")
    _social_domain = _social_email.split("@")[-1].strip().lower() if "@" in _social_email else ""
    if not _social_domain:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Social signup session expired. Please try again.",
        )

    # SPEC-AUTH-010 R1: domain-match branch before any Zitadel/DB write. The
    # email is IdP-verified, so workspace names may be shown (C1.1). The
    # pending cookie is deliberately NOT consumed here (C1.5) — the user still
    # needs it for /signup/social/join or a create_new_workspace retry.
    _claimable_social = primary_domain_for_email_domain(_social_domain)
    if _claimable_social and not body.create_new_workspace:
        _domain_orgs = await find_domain_match_orgs(db, _social_email)
        if _domain_orgs:
            _slog.info("social_signup_domain_match_offered", email_domain=_claimable_social)
            return SocialSignupDomainMatch(
                domain=_claimable_social,
                orgs=[
                    SocialSignupDomainMatchOrg(
                        org_id=org.id,
                        name=org.name,
                        auto_accept=bool(org.auto_accept_same_domain),
                    )
                    for org in _domain_orgs
                ],
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

    # Orphan-prevention: any failure AFTER create_org must cascade-delete the
    # Zitadel org (the social-login user is pre-existing and is NOT ours to
    # delete). delete_org is idempotent.
    async def _rollback_zitadel_org() -> None:
        with suppress(Exception):
            await zitadel.delete_org(zitadel_org_id)

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
            await _rollback_zitadel_org()
            logger.exception("Social signup: role grant failed for user %s: %s", zitadel_user_id, exc)
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Creation failed, please try again later",
            ) from exc
    except Exception as exc:
        await _rollback_zitadel_org()
        logger.exception("Social signup: role grant failed for user %s: %s", zitadel_user_id, exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Creation failed, please try again later",
        ) from exc

    # 4. Persist to PostgreSQL
    try:
        org_slug = _to_slug(body.company_name, zitadel_org_id)
        validate_slug_for_provisioning(org_slug, domain=settings.domain)
        org_row = PortalOrg(
            zitadel_org_id=zitadel_org_id,
            name=body.company_name,
            slug=org_slug,
            plan="knowledge",
            primary_domain=_claimable_social,
            # SPEC-AUTH-010 R5/C5.1: founder's checkbox, honoured only when the
            # workspace actually claims a domain (free-email → False).
            auto_accept_same_domain=bool(body.auto_accept_same_domain and _claimable_social),
        )
        db.add(org_row)
        await db.flush()

        # Set tenant context so the portal_users RLS policy passes for the INSERT.
        await set_tenant(db, org_row.id)

        user_row = PortalUser(
            zitadel_user_id=zitadel_user_id,
            org_id=org_row.id,
            role="admin",
            seat_type=str(suggest_seat("admin")),
        )
        db.add(user_row)
        await db.commit()
    except Exception as exc:
        await db.rollback()
        await _rollback_zitadel_org()
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
    background_tasks.add_task(
        _sync_signup_to_mailing,
        email=_social_email,
        name=_social_email,
        company=body.company_name,
        org_id=org_row.id,
        portal_user_id=getattr(user_row, "id", None),
        zitadel_user_id=zitadel_user_id,
        source="portal_social_signup",
    )
    emit_event(
        "signup", org_id=org_row.id, user_id=zitadel_user_id, properties={"plan": org_row.plan, "method": "social"}
    )

    # 6. Set SSO cookie so the user is immediately logged in via sso-complete
    _set_klai_sso_cookie(response, session_id, session_token)

    # 7. Clear the pending cookie
    response.delete_cookie(
        key=_IDP_PENDING_COOKIE,
        domain=f".{settings.domain}" if settings.domain else None,
        path="/",
    )

    return SocialSignupResponse(
        org_id=zitadel_org_id,
        user_id=zitadel_user_id,
        redirect_url="/",
    )


# ---------------------------------------------------------------------------
# Social signup — join an existing domain-match workspace (SPEC-AUTH-010 R2)
# ---------------------------------------------------------------------------


@router.post(
    "/signup/social/join",
    response_model=SocialJoinAutoResponse | SocialJoinPendingResponse,
)
async def signup_social_join(
    body: SocialJoinRequestBody,
    response: Response,
    request: Request,
    db: AsyncSession = Depends(get_db),
    klai_idp_pending: str | None = Cookie(default=None),
) -> SocialJoinAutoResponse | SocialJoinPendingResponse:
    """Join an existing workspace picked from the social-signup domain match.

    Same cookie binding as /signup/social. The org_id is re-validated against
    a fresh domain-match query (C2.1) — the client's list is never trusted.

    - auto_accept_same_domain=True  -> INSERT portal_users + notify admins +
      log the user in (kind=auto_join)
    - auto_accept_same_domain=False -> INSERT portal_join_requests + notify
      admins, no session (kind=join_request_pending)
    """
    pending = _decode_idp_pending_or_400(klai_idp_pending, request)

    session_id: str = pending.get("session_id", "")
    session_token: str = pending.get("session_token", "")
    zitadel_user_id: str = pending.get("zitadel_user_id", "")
    _social_email: str = pending.get("email", "")

    if not session_id or not session_token or not zitadel_user_id or "@" not in _social_email:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Social signup session expired. Please try again.",
        )

    # C2.1: server-side re-validation — the chosen org must be a current
    # domain match for the IdP-verified email.
    matches = await find_domain_match_orgs(db, _social_email)
    org = next((o for o in matches if o.id == body.org_id), None)
    if org is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Organisation not available for this account",
        )

    if org.auto_accept_same_domain:
        # C2.2: RLS WITH CHECK requires the tenant GUC before the INSERT.
        await set_tenant(db, org.id)
        new_user: PortalUser | None = None
        try:
            # SPEC-PORTAL-RBAC-REFACTOR-001 REQ-11: joiners are "personal".
            new_user = PortalUser(
                zitadel_user_id=zitadel_user_id,
                org_id=org.id,
                role="personal",
                seat_type=str(suggest_seat("personal")),
                status="active",
                email=_social_email,
            )
            db.add(new_user)
            await db.flush()
        except IntegrityError:
            # C2.4: race with another tab — already a member; treat as joined.
            _slog.info("social_join_duplicate_ignored", org_id=org.id)
            new_user = None
            await db.rollback()
            await set_tenant(db, org.id)

        # Notify only on an ACTUAL new join — the IntegrityError replay/race
        # path must not spam admins with duplicate "X joined" mails.
        if new_user is not None:
            from app.api.auth_select import notify_auto_join_admins

            await notify_auto_join_admins(
                email=_social_email,
                display_name=_social_email,
                org_id=org.id,
                db=db,
            )
        await db.commit()

        if new_user is not None:
            from app.services.listmonk import sync_portal_user_best_effort

            await sync_portal_user_best_effort(
                email=_social_email,
                name=_social_email,
                org_id=org.id,
                portal_user_id=getattr(new_user, "id", None),
                zitadel_user_id=zitadel_user_id,
                source="portal_social_auto_join",
            )

        _set_klai_sso_cookie(response, session_id, session_token)
        response.delete_cookie(
            key=_IDP_PENDING_COOKIE,
            domain=f".{settings.domain}" if settings.domain else None,
            path="/",
        )
        emit_event(
            "login",
            user_id=zitadel_user_id,
            properties={"method": "idp", "auto_join": True, "org_id": org.id},
        )
        _slog.info("social_signup_auto_joined", org_id=org.id)
        return SocialJoinAutoResponse(redirect_url="/")

    # Join-request branch — mirrors auth_select._handle_join_request.
    await set_tenant(db, org.id)

    # Idempotency: the pending cookie is replayable for its 10-minute TTL, so
    # a stuck client retry loop must not stack duplicate pending requests or
    # re-notify admins. One pending request per (user, org).
    existing_result = await db.execute(
        select(PortalJoinRequest)
        .where(
            PortalJoinRequest.zitadel_user_id == zitadel_user_id,
            PortalJoinRequest.org_id == org.id,
            PortalJoinRequest.status == "pending",
        )
        .with_for_update()
    )
    if existing_result.scalar_one_or_none() is not None:
        _slog.info("social_join_request_duplicate_ignored", org_id=org.id)
        response.delete_cookie(
            key=_IDP_PENDING_COOKIE,
            domain=f".{settings.domain}" if settings.domain else None,
            path="/",
        )
        return SocialJoinPendingResponse(redirect_to="/join-request/sent")

    join_request = PortalJoinRequest(
        zitadel_user_id=zitadel_user_id,
        email=_social_email,
        org_id=org.id,
        status="pending",
        approval_token="placeholder",  # noqa: S106
        expires_at=datetime.now(UTC) + timedelta(days=7),
    )
    db.add(join_request)
    await db.flush()
    join_request.approval_token = generate_approval_token(join_request.id, zitadel_user_id)

    try:
        result = await db.execute(
            select(PortalUser).where(
                PortalUser.org_id == org.id,
                PortalUser.role.in_(["admin", "group_manager"]),
                PortalUser.status == "active",
            )
        )
        admins = result.scalars().all()
        from app.services.notifications import notify_admin_join_request

        for admin in admins:
            if admin.email:
                await notify_admin_join_request(
                    email=_social_email,
                    display_name=_social_email,
                    org_id=org.id,
                    admin_email=admin.email,
                )
    except Exception:
        _slog.warning("social_join_request_notify_failed", org_id=org.id, exc_info=True)

    await db.commit()

    response.delete_cookie(
        key=_IDP_PENDING_COOKIE,
        domain=f".{settings.domain}" if settings.domain else None,
        path="/",
    )
    _slog.info("social_signup_join_request_created", org_id=org.id)
    return SocialJoinPendingResponse(redirect_to="/join-request/sent")
