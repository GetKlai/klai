"""The portal side of Klai in Slack — SPEC-SLACK-001 §4, §6.2, §7.1.

An org's Slack workspace talks to Klai with one managed Partner API key, minted
here and nowhere else: ``admin_api_keys`` hides and refuses it, and
``partner_dependencies.get_partner_key`` gates it on the ``slack`` unlock
instead of ``partner_api``. Its knowledge-base rows are the maximum scope of
every Slack answer (layer 1 of §5), so only org KBs qualify, never a personal
one. ``portal_slack_installations`` records which workspace holds the key; the
row is written by klai-slack through ``app/api/internal_slack.py``.

Every function here takes the request session of an admin route, which
``get_caller`` has already scoped to the caller's org (Cat-D RLS).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from urllib.parse import urlencode

import httpx
import jwt
import structlog
from fastapi import HTTPException, status
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.knowledge_bases import PortalKnowledgeBase
from app.models.partner_api_keys import PartnerAPIKey, PartnerApiKeyKbAccess
from app.models.portal import PortalOrg
from app.models.slack import PortalSlackInstallation
from app.services.access import is_personal_kb
from app.services.events import emit_event
from app.services.partner_keys import generate_partner_key

logger = structlog.get_logger()

SLACK_KEY_NAME = "Slack"
SLACK_KEY_PERMISSIONS = {
    "slack": True,
    "chat": True,
    "feedback": True,
    "knowledge_append": False,
    "general_chat": False,
}
# The schema maximum of a partner key: the whole workspace shares this one key.
SLACK_KEY_RATE_LIMIT_RPM = 600
# klai-slack answers its idempotent DELETE locally (token revoke + row deletes).
_KLAI_SLACK_TIMEOUT_SECONDS = 10.0
# The portal-driven link (§8 target state): the admin's choice travels to
# klai-slack and back inside a short-lived token signed with the secret both
# services already share, so nothing is minted until Slack OAuth succeeded.
LINK_TOKEN_TYP = "slack_link"
LINK_TOKEN_TTL = timedelta(minutes=15)
_LINK_TOKEN_CLAIMS = ["typ", "org_id", "user_id", "kb_ids", "return_url", "jti", "iat", "exp"]

SlackStatus = Literal["not_connected", "awaiting_link", "connected", "revoked"]


async def get_slack_key(db: AsyncSession, org_id: int) -> PartnerAPIKey | None:
    result = await db.execute(
        select(PartnerAPIKey)
        .where(PartnerAPIKey.org_id == org_id, PartnerAPIKey.permissions["slack"].as_boolean().is_(True))
        .limit(1)
    )
    return result.scalar_one_or_none()


async def get_installation(db: AsyncSession, org_id: int) -> PortalSlackInstallation | None:
    result = await db.execute(select(PortalSlackInstallation).where(PortalSlackInstallation.org_id == org_id))
    return result.scalar_one_or_none()


async def key_kb_ids(db: AsyncSession, key_id: str) -> list[int]:
    result = await db.execute(
        select(PartnerApiKeyKbAccess.kb_id).where(PartnerApiKeyKbAccess.partner_api_key_id == key_id)
    )
    return sorted(result.scalars().all())


def derive_status(key: object | None, installation: PortalSlackInstallation | None) -> SlackStatus:
    if key is None:
        return "not_connected"
    if installation is None:
        return "awaiting_link"
    if installation.revoked_at is not None:
        return "revoked"
    return "connected"


async def _org_kb_ids(db: AsyncSession, org_id: int, kb_ids: list[int]) -> list[int]:
    """Validate a layer-1 choice: at least one KB, all of this org, none personal."""
    wanted = sorted(set(kb_ids))
    if not wanted:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Choose at least one knowledge base")
    result = await db.execute(
        select(PortalKnowledgeBase).where(PortalKnowledgeBase.id.in_(wanted), PortalKnowledgeBase.org_id == org_id)
    )
    found = list(result.scalars().all())
    missing = set(wanted) - {kb.id for kb in found}
    if missing:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail=f"Knowledge base IDs not found in your organisation: {sorted(missing)}",
        )
    personal = sorted(kb.id for kb in found if is_personal_kb(kb))
    if personal:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail=f"Personal knowledge bases cannot be shared with Slack: {personal}",
        )
    return wanted


def _add_read_access(db: AsyncSession, key_id: str, kb_ids: list[int]) -> None:
    for kb_id in kb_ids:
        db.add(PartnerApiKeyKbAccess(partner_api_key_id=key_id, kb_id=kb_id, access_level="read"))


async def mint_slack_key(
    db: AsyncSession, *, org_id: int, user_id: str, kb_ids: list[int]
) -> tuple[str, PartnerAPIKey, list[int]]:
    """Add the org's Slack key and its KB rows to the session, uncommitted.

    The caller commits and emits ``slack.key_created``, which lets the
    link-oauth route commit the key and the installation row as one unit.
    """
    # Get-or-create on a per-org credential: lock the org row so two concurrent
    # creates cannot both mint a key (portal-backend.md, SELECT FOR UPDATE).
    await db.execute(select(PortalOrg).where(PortalOrg.id == org_id).with_for_update())
    if await get_slack_key(db, org_id) is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="This organisation already has a Slack key")
    wanted = await _org_kb_ids(db, org_id, kb_ids)

    plaintext, key_hash = generate_partner_key()
    key = PartnerAPIKey(
        id=str(uuid.uuid4()),
        org_id=org_id,
        name=SLACK_KEY_NAME,
        key_prefix=plaintext[:12],
        key_hash=key_hash,
        permissions=dict(SLACK_KEY_PERMISSIONS),
        rate_limit_rpm=SLACK_KEY_RATE_LIMIT_RPM,
        created_by=user_id,
    )
    db.add(key)
    await db.flush()  # the key row must exist before its KB rows reference it
    _add_read_access(db, key.id, wanted)
    await db.flush()
    await db.refresh(key)  # server defaults (created_at) while tenant context is still bound
    return plaintext, key, wanted


async def create_slack_key(
    db: AsyncSession, *, org_id: int, user_id: str, kb_ids: list[int]
) -> tuple[str, PartnerAPIKey]:
    """Mint the org's Slack key and return its plaintext, shown this once.

    Serves the deprecated paste-the-key flow; the portal now links through
    ``start_slack_connect`` and ``/internal/slack/link-oauth`` mints the key.
    """
    plaintext, key, wanted = await mint_slack_key(db, org_id=org_id, user_id=user_id, kb_ids=kb_ids)
    await db.commit()

    emit_event(
        "slack.key_created",
        org_id=org_id,
        user_id=user_id,
        properties={"api_key_id": key.id, "kb_ids": wanted},
    )
    logger.info("Slack key created", api_key_id=key.id, org_id=org_id)
    return plaintext, key


async def start_slack_connect(db: AsyncSession, *, org_id: int, org_slug: str, user_id: str, kb_ids: list[int]) -> str:
    """Validate the admin's KB choice and return the klai-slack URL that starts Slack OAuth.

    Mints nothing: ``/internal/slack/link-oauth`` creates the key once the
    workspace approved the app, after running the same checks again.
    """
    if await get_slack_key(db, org_id) is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="This organisation already has a Slack key")
    wanted = await _org_kb_ids(db, org_id, kb_ids)

    now = datetime.now(UTC)
    token = jwt.encode(
        {
            "typ": LINK_TOKEN_TYP,
            "org_id": org_id,
            "user_id": user_id,
            "kb_ids": wanted,
            "return_url": f"https://{org_slug}.{settings.domain}/admin/slack",
            "jti": uuid.uuid4().hex,
            "iat": now,
            "exp": now + LINK_TOKEN_TTL,
        },
        settings.internal_secret,
        algorithm="HS256",
    )
    emit_event("slack.connect_started", org_id=org_id, user_id=user_id, properties={"kb_ids": wanted})
    return f"{settings.slack_service_public_url.rstrip('/')}/slack/connect?{urlencode({'token': token})}"


def decode_link_token(token: str) -> dict[str, Any]:
    """Verify signature, expiry and type of a link token; raises ``jwt.InvalidTokenError``."""
    claims = jwt.decode(token, settings.internal_secret, algorithms=["HS256"], options={"require": _LINK_TOKEN_CLAIMS})
    if claims["typ"] != LINK_TOKEN_TYP:
        raise jwt.InvalidTokenError("Not a Slack link token")
    return claims


async def set_slack_kb_access(db: AsyncSession, *, org_id: int, user_id: str, kb_ids: list[int]) -> PartnerAPIKey:
    """Replace the KB rows of the org's Slack key; the next Slack turn sees the new scope."""
    key = await get_slack_key(db, org_id)
    if key is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="This organisation has no Slack key")
    wanted = await _org_kb_ids(db, org_id, kb_ids)

    await db.execute(delete(PartnerApiKeyKbAccess).where(PartnerApiKeyKbAccess.partner_api_key_id == key.id))
    _add_read_access(db, key.id, wanted)
    await db.commit()

    emit_event(
        "slack.kb_access_updated",
        org_id=org_id,
        user_id=user_id,
        properties={"api_key_id": key.id, "kb_ids": wanted},
    )
    return key


async def disconnect_slack(db: AsyncSession, *, org_id: int, user_id: str) -> bool:
    """Delete the installation row, the KB rows and the key, then tell klai-slack.

    Returns whether the Slack side is clean: True when no workspace was linked
    or klai-slack acknowledged the revoke, False when it could not be told. The
    portal side is deleted first either way, so the key stops working at once;
    a False only means the bot may still sit in the Slack workspace.
    """
    key = await get_slack_key(db, org_id)
    if key is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="This organisation has no Slack key")
    installation = await get_installation(db, org_id)
    team_id = installation.team_id if installation is not None else None

    await db.execute(delete(PortalSlackInstallation).where(PortalSlackInstallation.org_id == org_id))
    await db.execute(delete(PartnerApiKeyKbAccess).where(PartnerApiKeyKbAccess.partner_api_key_id == key.id))
    await db.execute(delete(PartnerAPIKey).where(PartnerAPIKey.id == key.id, PartnerAPIKey.org_id == org_id))
    await db.commit()

    slack_revoked = True if team_id is None else await _revoke_in_klai_slack(team_id, org_id)
    emit_event(
        "slack.disconnected",
        org_id=org_id,
        user_id=user_id,
        properties={"api_key_id": key.id, "team_id": team_id, "slack_revoked": slack_revoked},
    )
    logger.info("Slack disconnected", org_id=org_id, team_id=team_id, slack_revoked=slack_revoked)
    return slack_revoked


async def _revoke_in_klai_slack(team_id: str, org_id: int) -> bool:
    if not settings.slack_service_url:
        logger.error("klai-slack not configured, Slack workspace not revoked", org_id=org_id, team_id=team_id)
        return False
    url = f"{settings.slack_service_url.rstrip('/')}/internal/installations/{team_id}"
    try:
        async with httpx.AsyncClient(timeout=_KLAI_SLACK_TIMEOUT_SECONDS) as client:
            response = await client.delete(url, headers={"Authorization": f"Bearer {settings.internal_secret}"})
    except httpx.HTTPError:
        logger.exception("klai-slack revoke failed", org_id=org_id, team_id=team_id, url=url)
        return False
    if not response.is_success:
        logger.error(
            "klai-slack revoke rejected",
            org_id=org_id,
            team_id=team_id,
            status_code=response.status_code,
            body=response.text[:200],
        )
        return False
    return True
