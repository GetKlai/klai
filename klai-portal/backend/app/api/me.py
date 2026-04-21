"""
GET /api/me

Validates the OIDC access token forwarded by the frontend and returns
the current user's profile + org info.
"""

import logging
from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.bearer import bearer
from app.core.config import settings
from app.core.database import get_db
from app.models.audit import PortalAuditLog
from app.models.events import ProductEvent
from app.models.groups import PortalGroup, PortalGroupMembership
from app.models.knowledge_bases import PortalKnowledgeBase, PortalUserKBAccess
from app.models.meetings import VexaMeeting
from app.models.portal import PortalOrg, PortalUser
from app.services.entitlements import get_effective_products
from app.services.zitadel import zitadel

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["auth"])


class LanguageUpdate(BaseModel):
    preferred_language: Literal["nl", "en"]


class MessageResponse(BaseModel):
    message: str


class MeResponse(BaseModel):
    user_id: str
    email: str
    name: str
    org_id: str | None = None
    roles: list[str] = []
    workspace_url: str | None = None
    provisioning_status: str = "pending"
    mfa_enrolled: bool = False
    mfa_policy: str = "optional"
    preferred_language: Literal["nl", "en"] = "nl"
    portal_role: str = "member"
    products: list[str] = []
    org_found: bool = False


def _extract_roles(info: dict) -> list[str]:
    """Extract project role names from Zitadel userinfo claims.

    Zitadel encodes roles as:
    "urn:zitadel:iam:org:project:roles": {"org:owner": {"orgId": "orgName"}}
    """
    raw = info.get("urn:zitadel:iam:org:project:roles", {})
    if isinstance(raw, dict):
        return list(raw.keys())
    return []


@router.get("/me", response_model=MeResponse)
async def me(
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
    db: AsyncSession = Depends(get_db),
) -> MeResponse:
    try:
        info = await zitadel.get_userinfo(credentials.credentials)
    except Exception as exc:
        logger.exception("Userinfo fetch failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
        ) from exc

    zitadel_user_id = info.get("sub", "")

    # Resolve org + user preferences from portal_users -> portal_orgs
    workspace_url: str | None = None
    provisioning_status: str = "pending"
    mfa_policy: str = "optional"
    preferred_language: Literal["nl", "en"] = "nl"
    portal_role: str = "member"
    org_found: bool = False
    if zitadel_user_id:
        result = await db.execute(
            select(PortalOrg, PortalUser)
            .join(PortalUser, PortalUser.org_id == PortalOrg.id)
            .where(PortalUser.zitadel_user_id == zitadel_user_id)
        )
        row = result.one_or_none()
        if row:
            org, portal_user = row
            org_found = True
            provisioning_status = org.provisioning_status
            mfa_policy = org.mfa_policy
            preferred_language = portal_user.preferred_language
            portal_role = portal_user.role
            if org.slug:
                workspace_url = f"https://{org.slug}.{settings.domain}"
            # Cache display info from OIDC token so members endpoints can resolve names
            new_display_name = info.get("name", info.get("preferred_username")) or None
            new_email = info.get("email") or None
            if portal_user.display_name != new_display_name or portal_user.email != new_email:
                portal_user.display_name = new_display_name
                portal_user.email = new_email
                await db.commit()

    # Check whether the user has any MFA method enrolled
    mfa_enrolled = False
    if zitadel_user_id:
        try:
            mfa_enrolled = await zitadel.has_any_mfa(zitadel_user_id)
        except Exception as exc:
            logger.warning("MFA check failed for user %s, skipping: %s", zitadel_user_id, exc)

    products = await get_effective_products(zitadel_user_id, db) if zitadel_user_id else []

    return MeResponse(
        user_id=zitadel_user_id,
        email=info.get("email", ""),
        name=info.get("name", info.get("preferred_username", "")),
        org_id=info.get("urn:zitadel:iam:user:resourceowner:id"),
        roles=_extract_roles(info),
        workspace_url=workspace_url,
        provisioning_status=provisioning_status,
        mfa_enrolled=mfa_enrolled,
        mfa_policy=mfa_policy,
        preferred_language=preferred_language,
        portal_role=portal_role,
        products=products,
        org_found=org_found,
    )


class SarIdentity(BaseModel):
    """SAR response - identity section (SPEC-GDPR-001)"""

    first_name: str | None
    last_name: str | None
    display_name: str | None
    email: str | None
    created_at: str | None
    mfa_enrolled: bool


class SarAccount(BaseModel):
    role: str
    status: str
    preferred_language: str
    github_username: str | None
    display_name: str | None
    email: str | None
    kb_retrieval_enabled: bool
    kb_personal_enabled: bool
    kb_slugs_filter: list[str] | None
    created_at: datetime


class SarGroupMembership(BaseModel):
    group_name: str
    joined_at: datetime
    is_group_admin: bool


class SarKBAccess(BaseModel):
    kb_name: str
    kb_slug: str
    role: str
    granted_at: datetime


class SarAuditEvent(BaseModel):
    action: str
    resource_type: str
    resource_id: str
    created_at: datetime


class SarUsageEvent(BaseModel):
    event_type: str
    created_at: datetime


class SarMeeting(BaseModel):
    meeting_title: str | None
    platform: str
    meeting_url: str
    status: str
    language: str | None
    duration_seconds: int | None
    started_at: datetime | None
    ended_at: datetime | None
    created_at: datetime
    transcript_text: str | None
    summary_json: dict[str, Any] | None


class SarKlaiPortal(BaseModel):
    identity: SarIdentity
    account: SarAccount
    group_memberships: list[SarGroupMembership]
    knowledge_base_access: list[SarKBAccess]
    audit_events: list[SarAuditEvent]
    usage_events: list[SarUsageEvent]
    meetings: list[SarMeeting]


class SarMoneybird(BaseModel):
    note: str
    contact_id: str | None


class SarLibreChat(BaseModel):
    note: str
    librechat_user_id: str | None


class SarTwentyCRM(BaseModel):
    note: str


class SarExternalSystems(BaseModel):
    moneybird: SarMoneybird
    librechat: SarLibreChat
    twenty_crm: SarTwentyCRM


class SarExportResponse(BaseModel):
    generated_at: datetime
    request_user_id: str
    klai_portal: SarKlaiPortal
    external_systems: SarExternalSystems


@router.patch("/me/language", response_model=MessageResponse)
async def update_my_language(
    body: LanguageUpdate,
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
    db: AsyncSession = Depends(get_db),
) -> MessageResponse:
    try:
        info = await zitadel.get_userinfo(credentials.credentials)
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token") from exc

    zitadel_user_id = info.get("sub", "")
    if not zitadel_user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="No user found")

    result = await db.execute(select(PortalUser).where(PortalUser.zitadel_user_id == zitadel_user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    user.preferred_language = body.preferred_language
    await db.commit()

    # Best-effort sync to Zitadel - don't fail if it doesn't work
    try:
        await zitadel.update_user_language(
            org_id=settings.zitadel_portal_org_id,
            user_id=zitadel_user_id,
            language=body.preferred_language,
        )
    except Exception:
        logger.warning("Could not sync preferred_language to Zitadel for user %s", zitadel_user_id)

    return MessageResponse(message="Taalvoorkeur opgeslagen.")


@router.post("/me/sar-export", response_model=SarExportResponse)
# @MX:ANCHOR SPEC-GDPR-001 AVG Art. 15 endpoint (graceful degradation on Zitadel)
async def sar_export(
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
    db: AsyncSession = Depends(get_db),
) -> SarExportResponse:
    """POST /api/me/sar-export - AVG Art. 15 subject access request.

    Returns a self-service export of all personal data Klai holds for the
    authenticated user. Always scoped to the requesting user - no admin override.
    """
    try:
        info = await zitadel.get_userinfo(credentials.credentials)
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token") from exc

    user_id = info.get("sub", "")
    if not user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="No user found")

    # 1. Portal user + org
    result = await db.execute(
        select(PortalOrg, PortalUser)
        .join(PortalUser, PortalUser.org_id == PortalOrg.id)
        .where(PortalUser.zitadel_user_id == user_id)
    )
    row = result.one_or_none()
    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    org, portal_user = row

    # 2. Zitadel identity (live fetch - source of truth for name/email)
    zitadel_user_data: dict[str, Any] = {}
    try:
        zitadel_response = await zitadel.get_user_by_id(user_id)
        zitadel_user_data = zitadel_response.get("user", {})
    except Exception as exc:
        logger.warning("SAR: Zitadel identity fetch failed for %s: %s", user_id, exc)

    profile = zitadel_user_data.get("human", {}).get("profile", {})
    email_obj = zitadel_user_data.get("human", {}).get("email", {})
    details = zitadel_user_data.get("details", {})

    mfa_enrolled = False
    try:
        mfa_enrolled = await zitadel.has_any_mfa(user_id)
    except Exception as exc:
        logger.warning("SAR: MFA check failed for %s: %s", user_id, exc)

    identity = SarIdentity(
        first_name=profile.get("firstName"),
        last_name=profile.get("lastName"),
        display_name=profile.get("displayName"),
        email=email_obj.get("email"),
        created_at=details.get("creationDate"),
        mfa_enrolled=mfa_enrolled,
    )

    # 3. Portal account fields
    account = SarAccount(
        role=portal_user.role,
        status=portal_user.status,
        preferred_language=portal_user.preferred_language,
        github_username=portal_user.github_username,
        display_name=portal_user.display_name,
        email=portal_user.email,
        kb_retrieval_enabled=portal_user.kb_retrieval_enabled,
        kb_personal_enabled=portal_user.kb_personal_enabled,
        kb_slugs_filter=portal_user.kb_slugs_filter,
        created_at=portal_user.created_at,
    )

    # 4. Group memberships
    gm_rows = (
        await db.execute(
            select(PortalGroup.name, PortalGroupMembership.joined_at, PortalGroupMembership.is_group_admin)
            .join(PortalGroup, PortalGroup.id == PortalGroupMembership.group_id)
            .where(PortalGroupMembership.zitadel_user_id == user_id)
        )
    ).all()
    group_memberships = [
        SarGroupMembership(group_name=r.name, joined_at=r.joined_at, is_group_admin=r.is_group_admin) for r in gm_rows
    ]

    # 5. Knowledge base access
    kb_rows = (
        await db.execute(
            select(
                PortalKnowledgeBase.name,
                PortalKnowledgeBase.slug,
                PortalUserKBAccess.role,
                PortalUserKBAccess.granted_at,
            )
            .join(PortalKnowledgeBase, PortalKnowledgeBase.id == PortalUserKBAccess.kb_id)
            .where(PortalUserKBAccess.user_id == user_id)
        )
    ).all()
    knowledge_base_access = [
        SarKBAccess(kb_name=r.name, kb_slug=r.slug, role=r.role, granted_at=r.granted_at) for r in kb_rows
    ]

    # 6. Audit events where this user was the actor (no details field - may contain org-wide data)
    audit_rows = (
        await db.execute(
            select(
                PortalAuditLog.action,
                PortalAuditLog.resource_type,
                PortalAuditLog.resource_id,
                PortalAuditLog.created_at,
            )
            .where(PortalAuditLog.actor_user_id == user_id)
            .order_by(PortalAuditLog.created_at.desc())
        )
    ).all()
    audit_events = [
        SarAuditEvent(
            action=r.action,
            resource_type=r.resource_type,
            resource_id=r.resource_id,
            created_at=r.created_at,
        )
        for r in audit_rows
    ]

    # 7. Product usage events (type + timestamp only - no properties, may contain org-wide data)
    event_rows = (
        await db.execute(
            select(ProductEvent.event_type, ProductEvent.created_at)
            .where(ProductEvent.user_id == user_id)
            .order_by(ProductEvent.created_at.desc())
        )
    ).all()
    usage_events = [SarUsageEvent(event_type=r.event_type, created_at=r.created_at) for r in event_rows]

    # 8. Meetings - includes transcript and summary (most sensitive personal data)
    meeting_rows = (
        (
            await db.execute(
                select(VexaMeeting)
                .where(VexaMeeting.zitadel_user_id == user_id)
                .order_by(VexaMeeting.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    meetings = [
        SarMeeting(
            meeting_title=mtg.meeting_title,
            platform=mtg.platform,
            meeting_url=mtg.meeting_url,
            status=mtg.status,
            language=mtg.language,
            duration_seconds=mtg.duration_seconds,
            started_at=mtg.started_at,
            ended_at=mtg.ended_at,
            created_at=mtg.created_at,
            transcript_text=mtg.transcript_text,
            summary_json=mtg.summary_json,
        )
        for mtg in meeting_rows
    ]

    # 9. External systems - data not held in the portal DB
    external_systems = SarExternalSystems(
        moneybird=SarMoneybird(
            note=(
                "Betalingsgegevens worden beheerd door Moneybird. "
                f"Uw organisatie contact-ID: {org.moneybird_contact_id}. "
                "Neem contact op met privacy@getklai.com voor een volledige Moneybird export."
            ),
            contact_id=org.moneybird_contact_id,
        ),
        librechat=SarLibreChat(
            note=(
                "AI-gespreksgeschiedenis wordt bewaard in de LibreChat omgeving van uw organisatie. "
                "Neem contact op met uw organisatiebeheerder of stuur een verzoek naar privacy@getklai.com."
            ),
            librechat_user_id=portal_user.librechat_user_id,
        ),
        twenty_crm=SarTwentyCRM(
            note=(
                "Klai verwerkt mogelijk de volgende persoonsgegevens in haar interne CRM: "
                "voornaam, achternaam, e-mailadres en bedrijfsnaam. "
                "Deze gegevens vallen buiten de self-service export. "
                "Neem contact op met privacy@getklai.com voor een volledig overzicht."
            ),
        ),
    )

    return SarExportResponse(
        generated_at=datetime.now(tz=UTC),
        request_user_id=user_id,
        klai_portal=SarKlaiPortal(
            identity=identity,
            account=account,
            group_memberships=group_memberships,
            knowledge_base_access=knowledge_base_access,
            audit_events=audit_events,
            usage_events=usage_events,
            meetings=meetings,
        ),
        external_systems=external_systems,
    )
