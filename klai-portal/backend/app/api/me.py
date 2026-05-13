"""
GET /api/me

Validates the OIDC access token forwarded by the frontend and returns
the current user's profile + org info.
"""

import logging
from datetime import UTC, datetime
from typing import Any, Literal

from bson import ObjectId
from bson.errors import InvalidId
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials
from motor.motor_asyncio import AsyncIOMotorClient
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.bearer import bearer
from app.core.config import settings
from app.core.database import AsyncSessionLocal, get_db, set_tenant
from app.core.permissions import resolve_user_permissions
from app.models.audit import PortalAuditLog
from app.models.events import ProductEvent
from app.models.groups import PortalGroup, PortalGroupMembership
from app.models.knowledge_bases import PortalKnowledgeBase, PortalUserKBAccess
from app.models.portal import PortalOrg, PortalUser
from app.services import twenty as twenty_service
from app.services.partner_rate_limit import check_rate_limit
from app.services.redis_client import get_redis_pool
from app.services.zitadel import zitadel

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["auth"])

_SAR_EXPORT_LIMIT_PER_HOUR = 5
_SAR_EXPORT_WINDOW_SECONDS = 3600
_SAR_EXPORT_RL_KEY_PREFIX = "sar_export:"


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
    portal_role: str = "personal"
    products: list[str] = []
    capabilities: list[str] = []
    effective_role: str = "personal"
    effective_capabilities: list[str] = []
    org_found: bool = False
    # SPEC-PORTAL-EXTENSIONS-UNIFY-001 Phase 3: expose the gating state to
    # the frontend so /admin/index.tsx can filter tiles per tenant and
    # /admin/settings can render the read-only status list. Platform-admin
    # callers (Klai staff) additionally see the tenant-picker.
    is_platform_admin: bool = False
    platform_unlocked_features: list[str] = []


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

    # SPEC-PORTAL-RBAC-REFACTOR-001 Phase 1 (REQ-10/1G):
    # /api/me used to issue THREE separate queries (resolver row,
    # get_effective_products, get_effective_capabilities) and surface
    # two inconsistent capability fields. UserPermissions consolidates
    # everything into one DB query and one source of truth. The
    # `capabilities` and `effective_capabilities` fields now both read
    # from the same `perms.effective_capabilities` (alias-fase per
    # REQ-10) so admin-on-core surfaces the full complete-tier set in
    # both fields instead of the zero-length one previously returned by
    # the regel-163 ``PROFILE_CAPABILITIES.get(role, frozenset())`` path.
    workspace_url: str | None = None
    provisioning_status: str = "pending"
    mfa_policy: str = "optional"
    preferred_language: Literal["nl", "en"] = "nl"
    portal_role: str = "personal"  # SPEC REQ-11: default flipped from "member"
    _eff_role: str = "personal"
    _capabilities: list[str] = []
    _products: list[str] = []
    org_found: bool = False
    # SPEC-SEC-IDENTITY-ASSERT-002 REQ-5: org_id is sourced from
    # portal_users + portal_orgs membership, NOT the JWT
    # urn:zitadel:iam:user:resourceowner:id claim (Klai BFF never requests
    # the scope that would emit it; resolution-via-membership is
    # deterministic and aligned with zitadel.md:99-100).
    resolved_zitadel_org_id: str | None = None
    perms = await resolve_user_permissions(zitadel_user_id, db) if zitadel_user_id else None
    if perms is not None:
        org_found = True
        await set_tenant(db, perms.org_id)
        provisioning_status = perms.provisioning_status

        # Re-fetch portal_user/org for the display-name cache + mfa_policy.
        # Cheaper than carrying every column on UserPermissions; the
        # RLS-permissive lookup is fine after set_tenant.
        result = await db.execute(
            select(PortalOrg, PortalUser)
            .join(PortalUser, PortalUser.org_id == PortalOrg.id)
            .where(PortalUser.zitadel_user_id == zitadel_user_id)
        )
        row = result.one_or_none()
        if row:
            org, portal_user = row
            resolved_zitadel_org_id = org.zitadel_org_id
            mfa_policy = org.mfa_policy
            preferred_language = portal_user.preferred_language
            if org.slug:
                workspace_url = f"https://{org.slug}.{settings.domain}"
            # Cache display info from OIDC token for members endpoints
            new_display_name = info.get("name", info.get("preferred_username")) or None
            new_email = info.get("email") or None
            if portal_user.display_name != new_display_name or portal_user.email != new_email:
                portal_user.display_name = new_display_name
                portal_user.email = new_email
                await db.commit()

        portal_role = perms.role.value
        _eff_role = perms.effective_role.value
        _capabilities = sorted(c.value for c in perms.effective_capabilities)
        _products = sorted(perms.effective_products)

    # Check whether the user has any MFA method enrolled
    mfa_enrolled = False
    if zitadel_user_id:
        try:
            mfa_enrolled = await zitadel.has_any_mfa(zitadel_user_id)
        except Exception as exc:
            logger.warning("MFA check failed for user %s, skipping: %s", zitadel_user_id, exc, exc_info=True)

    return MeResponse(
        user_id=zitadel_user_id,
        email=info.get("email", ""),
        name=info.get("name", info.get("preferred_username", "")),
        org_id=resolved_zitadel_org_id,
        roles=_extract_roles(info),
        workspace_url=workspace_url,
        provisioning_status=provisioning_status,
        mfa_enrolled=mfa_enrolled,
        mfa_policy=mfa_policy,
        preferred_language=preferred_language,
        portal_role=portal_role,
        products=_products,
        # REQ-10: capabilities and effective_capabilities are aliases of the
        # same source. Phase 2 deprecates `capabilities` in favour of
        # `effective_capabilities`; for now both must hold identical content.
        capabilities=_capabilities,
        effective_role=_eff_role,
        effective_capabilities=_capabilities,
        org_found=org_found,
        is_platform_admin=perms.is_platform_admin if perms is not None else False,
        platform_unlocked_features=sorted(perms.platform_unlocked_features) if perms is not None else [],
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


class SarLibreChatMessage(BaseModel):
    role: str
    text: str | None
    created_at: datetime | None


class SarLibreChatConversation(BaseModel):
    title: str | None
    created_at: datetime | None
    updated_at: datetime | None
    messages: list[SarLibreChatMessage]


class SarKlaiPortal(BaseModel):
    identity: SarIdentity
    account: SarAccount
    group_memberships: list[SarGroupMembership]
    knowledge_base_access: list[SarKBAccess]
    audit_events: list[SarAuditEvent]
    usage_events: list[SarUsageEvent]
    librechat_conversations: list[SarLibreChatConversation] | None


class SarMoneybird(BaseModel):
    note: str
    contact_id: str | None


class SarLibreChat(BaseModel):
    note: str
    librechat_user_id: str | None


class SarTwentyCRMRecord(BaseModel):
    first_name: str | None
    last_name: str | None
    email: str | None
    company_name: str | None


class SarTwentyCRM(BaseModel):
    note: str
    records: list[SarTwentyCRMRecord] | None


class SarExternalSystems(BaseModel):
    moneybird: SarMoneybird
    librechat: SarLibreChat
    twenty_crm: SarTwentyCRM


class SarExportResponse(BaseModel):
    generated_at: datetime
    request_user_id: str
    klai_portal: SarKlaiPortal
    external_systems: SarExternalSystems


def _coerce_mongo_datetime(value: Any) -> datetime | None:
    return value if isinstance(value, datetime) else None


def _message_role(message: dict[str, Any]) -> str:
    raw_role = message.get("role")
    if isinstance(raw_role, str) and raw_role:
        return raw_role
    if message.get("isCreatedByUser") is True:
        return "user"
    sender = message.get("sender")
    return sender if isinstance(sender, str) and sender else "assistant"


def _message_text(message: dict[str, Any]) -> str | None:
    text = message.get("text")
    if isinstance(text, str):
        return text
    content = message.get("content")
    if isinstance(content, str):
        return content
    return None


async def _load_librechat_conversations(
    org: PortalOrg,
    portal_user: PortalUser,
) -> list[SarLibreChatConversation] | None:
    librechat_user_id = portal_user.librechat_user_id
    if not librechat_user_id:
        return []
    if not settings.librechat_mongo_root_uri or not org.librechat_container:
        logger.warning(
            "SAR: LibreChat export unavailable for user %s; missing Mongo URI or container",
            portal_user.zitadel_user_id,
        )
        return None

    try:
        user_oid = ObjectId(librechat_user_id)
    except InvalidId:
        logger.warning("SAR: invalid LibreChat user id %s for user %s", librechat_user_id, portal_user.zitadel_user_id)
        return None

    mongo_client: AsyncIOMotorClient | None = None
    try:
        mongo_client = AsyncIOMotorClient(settings.librechat_mongo_root_uri)
        database = mongo_client[org.librechat_container]
        conversation_rows = await (
            database["conversations"].find({"user": user_oid}).sort("updatedAt", -1).to_list(length=None)
        )

        conversations: list[SarLibreChatConversation] = []
        for conversation in conversation_rows:
            conversation_id = conversation.get("conversationId")
            message_filter: dict[str, Any] = {"user": user_oid}
            if conversation_id is not None:
                message_filter["conversationId"] = conversation_id
            message_rows = await database["messages"].find(message_filter).sort("createdAt", 1).to_list(length=None)

            conversations.append(
                SarLibreChatConversation(
                    title=conversation.get("title") if isinstance(conversation.get("title"), str) else None,
                    created_at=_coerce_mongo_datetime(conversation.get("createdAt")),
                    updated_at=_coerce_mongo_datetime(conversation.get("updatedAt")),
                    messages=[
                        SarLibreChatMessage(
                            role=_message_role(message),
                            text=_message_text(message),
                            created_at=_coerce_mongo_datetime(message.get("createdAt")),
                        )
                        for message in message_rows
                        if isinstance(message, dict)
                    ],
                )
            )
        return conversations
    except Exception as exc:
        logger.warning(
            "SAR: LibreChat conversation export failed for user %s: %s",
            portal_user.zitadel_user_id,
            exc,
            exc_info=True,
        )
        return None
    finally:
        if mongo_client is not None:
            mongo_client.close()


async def _load_twenty_records(email: str | None) -> list[SarTwentyCRMRecord] | None:
    if not email:
        return []
    try:
        records = await twenty_service.list_people_by_email(email)
    except Exception as exc:
        logger.warning("SAR: Twenty CRM lookup failed for %s: %s", email, exc, exc_info=True)
        return None
    return [
        SarTwentyCRMRecord(
            first_name=record.first_name,
            last_name=record.last_name,
            email=record.email,
            company_name=record.company_name,
        )
        for record in records
    ]


async def _write_sar_audit(org_id: int, user_id: str, action: Literal["sar.exported", "sar.rate_limited"]) -> None:
    try:
        async with AsyncSessionLocal() as session:
            session.add(
                PortalAuditLog(
                    org_id=org_id,
                    actor_user_id=user_id,
                    action=action,
                    resource_type="self",
                    resource_id=user_id,
                )
            )
            await session.commit()
    except Exception:
        logger.exception("SAR: audit write failed for action %s user %s", action, user_id)


async def _enforce_sar_rate_limit(user_id: str, org_id: int) -> None:
    try:
        redis_pool = await get_redis_pool()
        if redis_pool is None:
            raise RuntimeError("redis_pool_none")
        allowed, retry_after = await check_rate_limit(
            redis_pool,
            f"{_SAR_EXPORT_RL_KEY_PREFIX}{user_id}",
            _SAR_EXPORT_LIMIT_PER_HOUR,
            window_seconds=_SAR_EXPORT_WINDOW_SECONDS,
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.warning("SAR: rate limit backend unavailable for user %s: %s", user_id, exc, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="SAR export rate limit backend unavailable",
        ) from exc

    if not allowed:
        await _write_sar_audit(org_id, user_id, "sar.rate_limited")
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="SAR export rate limit exceeded",
            headers={"Retry-After": str(retry_after)},
        )


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
        logger.warning("Could not sync preferred_language to Zitadel for user %s", zitadel_user_id, exc_info=True)

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

    # All subsequent queries hit RLS-strict tables (portal_groups,
    # portal_knowledge_bases, portal_user_kb_access, portal_audit_log,
    # product_events). Set tenant context once here so the
    # PostgreSQL fail-loud policy lets them through.
    await set_tenant(db, org.id)

    await _enforce_sar_rate_limit(user_id, org.id)

    # 2. Zitadel identity (live fetch - source of truth for name/email)
    zitadel_user_data: dict[str, Any] = {}
    try:
        zitadel_response = await zitadel.get_user_by_id(user_id)
        zitadel_user_data = zitadel_response.get("user", {})
    except Exception as exc:
        logger.warning("SAR: Zitadel identity fetch failed for %s: %s", user_id, exc, exc_info=True)

    profile = zitadel_user_data.get("human", {}).get("profile", {})
    email_obj = zitadel_user_data.get("human", {}).get("email", {})
    details = zitadel_user_data.get("details", {})

    mfa_enrolled = False
    try:
        mfa_enrolled = await zitadel.has_any_mfa(user_id)
    except Exception as exc:
        logger.warning("SAR: MFA check failed for %s: %s", user_id, exc, exc_info=True)

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

    # 8. External systems - graceful degradation mirrors the Zitadel fallback above.
    librechat_conversations = await _load_librechat_conversations(org, portal_user)
    twenty_records = await _load_twenty_records(portal_user.email)

    librechat_note = (
        "AI-gesprekken staan nu in de klai_portal.librechat_conversations sectie. "
        "Het LibreChat user-ID blijft opgenomen voor traceability."
    )
    twenty_note = (
        "Matchende CRM-records op basis van uw bevestigde portal e-mailadres staan in records."
        if twenty_records is not None
        else "Twenty CRM kon niet worden bevraagd; neem contact op met privacy@getklai.com voor de handmatige route."
    )
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
            note=librechat_note,
            librechat_user_id=portal_user.librechat_user_id,
        ),
        twenty_crm=SarTwentyCRM(
            note=twenty_note,
            records=twenty_records,
        ),
    )

    await _write_sar_audit(org.id, user_id, "sar.exported")

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
            librechat_conversations=librechat_conversations,
        ),
        external_systems=external_systems,
    )
