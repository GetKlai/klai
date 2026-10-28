"""Internal link API for klai-slack — SPEC-SLACK-001 §7.2.

klai-slack calls these when a Slack workspace admin runs ``/klai connect`` with
the org's Slack key, and when the app is uninstalled or its tokens revoked.
Auth is the shared ``INTERNAL_SECRET`` Bearer guard of every other
``/internal/*`` route (``internal._require_internal_token``), which fails
closed when the secret is unset or does not match.

The key, not the caller, decides the org: a link only ever lands on the org
that owns the verified key. On a conflict the answer is a code, never the
other org, so a workspace cannot learn which org holds another link.
"""

from __future__ import annotations

import hashlib

from fastapi import APIRouter, Depends, HTTPException, Path, Request, Response, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.internal import _audit_internal_call, _require_internal_token
from app.api.partner_dependencies import _org_accepts_credentials
from app.core.database import cross_org_scope, get_db, set_tenant
from app.core.permissions import assert_platform_unlocked
from app.models.partner_api_keys import PartnerAPIKey
from app.models.portal import PortalOrg
from app.models.slack import PortalSlackInstallation
from app.services.events import emit_event
from app.services.partner_keys import verify_partner_key
from app.services.slack_integration import get_installation, key_kb_ids

router = APIRouter(prefix="/internal/slack", tags=["internal"])

# Slack team and user ids are upper-case alphanumerics (T…, E…, U…, W…). The
# team id is also put in klai-slack's URL on disconnect, so nothing else passes.
_SLACK_ID = r"^[A-Z0-9]{1,32}$"
_UNKNOWN_KEY = "Unknown Slack key"


class LinkRequest(BaseModel):
    api_key: str = Field(min_length=1, max_length=128)
    team_id: str = Field(pattern=_SLACK_ID)
    team_name: str = Field(min_length=1, max_length=255)
    slack_user_id: str = Field(pattern=_SLACK_ID)


class LinkResponse(BaseModel):
    org_id: int
    org_name: str
    key_id: str
    kb_ids: list[int]


def _conflict(code: str) -> JSONResponse:
    return JSONResponse(status_code=status.HTTP_409_CONFLICT, content={"code": code})


@router.post("/link", response_model=LinkResponse)
async def link_slack_workspace(
    body: LinkRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> LinkResponse | JSONResponse:
    await _require_internal_token(request)

    # partner_api_keys is Cat-B (SELECT open before tenant context), the same
    # lookup get_partner_key does; the hash comparison is constant-time.
    key_hash = hashlib.sha256(body.api_key.encode()).hexdigest()
    key = (await db.execute(select(PartnerAPIKey).where(PartnerAPIKey.key_hash == key_hash))).scalar_one_or_none()
    # One answer for unknown, non-slack and dead-org keys: no key enumeration.
    if key is None or not verify_partner_key(body.api_key, key.key_hash) or key.permissions.get("slack") is not True:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=_UNKNOWN_KEY)
    org = (await db.execute(select(PortalOrg).where(PortalOrg.id == key.org_id))).scalar_one_or_none()
    if org is None or not _org_accepts_credentials(org):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=_UNKNOWN_KEY)

    await set_tenant(db, org.id)
    assert_platform_unlocked(org, "slack")

    if await get_installation(db, org.id) is not None:
        return _conflict("org_already_linked")
    db.add(
        PortalSlackInstallation(
            org_id=org.id,
            team_id=body.team_id,
            team_name=body.team_name,
            partner_api_key_id=key.id,
            installed_by_slack_user_id=body.slack_user_id,
        )
    )
    try:
        await db.flush()
    except IntegrityError as exc:
        await db.rollback()
        # A row of another org is invisible under this org's RLS scope; only
        # the UNIQUE constraints see it. A concurrent link of the same org
        # trips the org constraint instead.
        if "uq_portal_slack_installations_team_id" in str(exc.orig):
            return _conflict("team_already_linked")
        return _conflict("org_already_linked")
    kb_ids = await key_kb_ids(db, key.id)
    await db.commit()

    emit_event(
        "slack.linked",
        org_id=org.id,
        properties={"api_key_id": key.id, "team_id": body.team_id, "slack_user_id": body.slack_user_id},
    )
    await _audit_internal_call(request, org_id=org.id)
    return LinkResponse(org_id=org.id, org_name=org.name, key_id=key.id, kb_ids=kb_ids)


@router.delete("/link/{team_id}", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
async def unlink_slack_workspace(
    request: Request,
    team_id: str = Path(pattern=_SLACK_ID),
    db: AsyncSession = Depends(get_db),
) -> Response:
    await _require_internal_token(request)

    # @MX:REASON klai-slack only knows the Slack team on uninstall, not the
    # org, and the row is the only place that maps one to the other. The
    # bypass covers this single UPDATE keyed on the globally unique team_id.
    async with cross_org_scope(db):
        result = await db.execute(
            update(PortalSlackInstallation)
            .where(PortalSlackInstallation.team_id == team_id, PortalSlackInstallation.revoked_at.is_(None))
            .values(revoked_at=func.now())
            .returning(PortalSlackInstallation.org_id)
        )
        org_id = result.scalar_one_or_none()
        await db.commit()

    if org_id is not None:
        emit_event("slack.revoked", org_id=org_id, properties={"team_id": team_id})
    await _audit_internal_call(request, org_id=org_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
