"""Admin endpoints for Klai in Slack — SPEC-SLACK-001 §7.1.

The org admin starts the Slack link (the key is minted server-side once Slack
OAuth succeeds), chooses which org knowledge bases Slack may search (layer 1
of §5) and disconnects the workspace. Gated by
the admin role and the ``slack`` platform unlock, like the other extensions.
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Response, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.permissions import (
    ProfileRole,
    UserPermissions,
    get_caller_at_least,
    require_platform_unlocked,
)
from app.models.partner_api_keys import PartnerAPIKey
from app.services.slack_integration import (
    SlackStatus,
    create_slack_key,
    derive_status,
    disconnect_slack,
    get_installation,
    get_slack_key,
    key_kb_ids,
    set_slack_kb_access,
    start_slack_connect,
)

router = APIRouter(prefix="/api/admin/slack", tags=["Slack Admin"])


class SlackKeyOut(BaseModel):
    id: str
    key_prefix: str
    rate_limit_rpm: int
    last_used_at: datetime | None
    created_at: datetime


class SlackInstallationOut(BaseModel):
    team_id: str
    team_name: str
    installed_by_slack_user_id: str
    installed_at: datetime
    revoked_at: datetime | None


class SlackStateOut(BaseModel):
    status: SlackStatus
    key: SlackKeyOut | None
    installation: SlackInstallationOut | None
    kb_ids: list[int]


class SlackKbIdsIn(BaseModel):
    # Emptiness is rejected with a 400 by the service, the same answer as for a
    # personal or foreign KB, so the page has one error path for a bad choice.
    kb_ids: list[int]


class SlackKeyCreatedOut(BaseModel):
    api_key: str  # plaintext, only in this response
    status: SlackStatus
    key: SlackKeyOut


class SlackConnectOut(BaseModel):
    connect_url: str


def _key_out(key: PartnerAPIKey) -> SlackKeyOut:
    return SlackKeyOut(
        id=key.id,
        key_prefix=key.key_prefix,
        rate_limit_rpm=key.rate_limit_rpm,
        last_used_at=key.last_used_at,
        created_at=key.created_at,
    )


async def _state(db: AsyncSession, org_id: int) -> SlackStateOut:
    key = await get_slack_key(db, org_id)
    installation = await get_installation(db, org_id) if key is not None else None
    return SlackStateOut(
        status=derive_status(key, installation),
        key=_key_out(key) if key is not None else None,
        installation=(
            SlackInstallationOut(
                team_id=installation.team_id,
                team_name=installation.team_name,
                installed_by_slack_user_id=installation.installed_by_slack_user_id,
                installed_at=installation.installed_at,
                revoked_at=installation.revoked_at,
            )
            if installation is not None
            else None
        ),
        kb_ids=await key_kb_ids(db, key.id) if key is not None else [],
    )


@router.get("")
async def get_slack(
    perms: UserPermissions = Depends(get_caller_at_least(ProfileRole.ADMIN)),
    _platform: UserPermissions = Depends(require_platform_unlocked("slack")),
    db: AsyncSession = Depends(get_db),
) -> SlackStateOut:
    return await _state(db, perms.org_id)


@router.post("/key", status_code=status.HTTP_201_CREATED)
async def create_key(
    body: SlackKbIdsIn,
    perms: UserPermissions = Depends(get_caller_at_least(ProfileRole.ADMIN)),
    _platform: UserPermissions = Depends(require_platform_unlocked("slack")),
    db: AsyncSession = Depends(get_db),
) -> SlackKeyCreatedOut:
    """Deprecated: the paste-the-key flow, kept while ``/klai connect`` is live.

    The portal page uses ``POST /connect``, which never sends a key to the browser.
    """
    plaintext, key = await create_slack_key(db, org_id=perms.org_id, user_id=perms.user_id, kb_ids=body.kb_ids)
    return SlackKeyCreatedOut(api_key=plaintext, status="awaiting_link", key=_key_out(key))


@router.post("/connect")
async def connect_slack(
    body: SlackKbIdsIn,
    perms: UserPermissions = Depends(get_caller_at_least(ProfileRole.ADMIN)),
    _platform: UserPermissions = Depends(require_platform_unlocked("slack")),
    db: AsyncSession = Depends(get_db),
) -> SlackConnectOut:
    connect_url = await start_slack_connect(
        db, org_id=perms.org_id, org_slug=perms.org_slug, user_id=perms.user_id, kb_ids=body.kb_ids
    )
    return SlackConnectOut(connect_url=connect_url)


@router.patch("")
async def update_slack(
    body: SlackKbIdsIn,
    perms: UserPermissions = Depends(get_caller_at_least(ProfileRole.ADMIN)),
    _platform: UserPermissions = Depends(require_platform_unlocked("slack")),
    db: AsyncSession = Depends(get_db),
) -> SlackStateOut:
    await set_slack_kb_access(db, org_id=perms.org_id, user_id=perms.user_id, kb_ids=body.kb_ids)
    return await _state(db, perms.org_id)


@router.delete("", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
async def delete_slack(
    perms: UserPermissions = Depends(get_caller_at_least(ProfileRole.ADMIN)),
    _platform: UserPermissions = Depends(require_platform_unlocked("slack")),
    db: AsyncSession = Depends(get_db),
) -> Response:
    if await disconnect_slack(db, org_id=perms.org_id, user_id=perms.user_id):
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    # Loud, not silent: the portal side is gone, but the bot may still be
    # installed in Slack, so the page tells the admin to remove the app there.
    return JSONResponse(status_code=status.HTTP_200_OK, content={"slack_revoked": False})
