"""Internal link API for klai-slack — SPEC-SLACK-001 §7.2.

klai-slack calls these when Slack OAuth completes for a link the portal
started (``/link-oauth``, which mints the key here) and when the app is
uninstalled or its tokens revoked.
Auth is the shared ``INTERNAL_SECRET`` Bearer guard of every other
``/internal/*`` route (``internal._require_internal_token``), which fails
closed when the secret is unset or does not match.

The signed link token, not the caller, decides the org: a link only ever lands
on the org that issued the token. On a conflict the answer is a code, never the
other org, so a workspace cannot learn which org holds another link.
"""

from __future__ import annotations

import jwt
import structlog
from fastapi import APIRouter, Depends, HTTPException, Path, Request, Response, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from redis.exceptions import RedisError
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.internal import _audit_internal_call, _require_internal_token
from app.api.partner_dependencies import _org_accepts_credentials
from app.core.database import cross_org_scope, get_db, set_tenant
from app.core.permissions import ProfileRole, assert_platform_unlocked, has_role_at_least, resolve_user_permissions
from app.models.portal import PortalOrg
from app.models.slack import PortalSlackInstallation
from app.services.events import emit_event
from app.services.redis_client import get_redis_pool
from app.services.slack_integration import LINK_TOKEN_TTL, decode_link_token, mint_slack_key

logger = structlog.get_logger()

router = APIRouter(prefix="/internal/slack", tags=["internal"])

# Slack team and user ids are upper-case alphanumerics (T…, E…, U…, W…). The
# team id is also put in klai-slack's URL on disconnect, so nothing else passes.
_SLACK_ID = r"^[A-Z0-9]{1,32}$"
_INVALID_TOKEN = "Invalid link token"
_LINK_JTI_KEY_PREFIX = "slack_link_jti:"


class _SlackWorkspace(BaseModel):
    team_id: str = Field(pattern=_SLACK_ID)
    team_name: str = Field(min_length=1, max_length=255)
    slack_user_id: str = Field(pattern=_SLACK_ID)


class LinkOAuthRequest(_SlackWorkspace):
    token: str = Field(min_length=1, max_length=4096)


class LinkOAuthResponse(BaseModel):
    org_id: int
    org_name: str
    key_id: str
    kb_ids: list[int]
    api_key: str  # plaintext; leaves the portal this once, to klai-slack
    return_url: str


def _conflict(code: str) -> JSONResponse:
    return JSONResponse(status_code=status.HTTP_409_CONFLICT, content={"code": code})


async def _insert_installation(
    db: AsyncSession, *, org_id: int, key_id: str, workspace: _SlackWorkspace
) -> JSONResponse | None:
    """Flush the installation row; a 409 response when a UNIQUE constraint refuses it."""
    db.add(
        PortalSlackInstallation(
            org_id=org_id,
            team_id=workspace.team_id,
            team_name=workspace.team_name,
            partner_api_key_id=key_id,
            installed_by_slack_user_id=workspace.slack_user_id,
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
    return None


async def _claim_link_token(jti: str) -> bool:
    """Record the token as used; False when it was used before.

    Fails closed: without Redis a replay cannot be ruled out, so no key is minted.
    """
    redis_pool = await get_redis_pool()
    if redis_pool is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail="Link token store unavailable")
    try:
        claimed = await redis_pool.set(
            f"{_LINK_JTI_KEY_PREFIX}{jti}", "1", nx=True, ex=int(LINK_TOKEN_TTL.total_seconds())
        )
    except RedisError as exc:
        logger.exception("slack_link_token_store_failed")
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail="Link token store unavailable") from exc
    return bool(claimed)


@router.post("/link-oauth", response_model=LinkOAuthResponse)
async def link_slack_workspace_oauth(
    body: LinkOAuthRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> LinkOAuthResponse | JSONResponse:
    await _require_internal_token(request)

    try:
        claims = decode_link_token(body.token)
    except jwt.InvalidTokenError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail=_INVALID_TOKEN) from None
    if not await _claim_link_token(claims["jti"]):
        return _conflict("token_used")

    org = (await db.execute(select(PortalOrg).where(PortalOrg.id == claims["org_id"]))).scalar_one_or_none()
    if org is None or not _org_accepts_credentials(org):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail=_INVALID_TOKEN)

    await set_tenant(db, org.id)
    assert_platform_unlocked(org, "slack")

    user_id = claims["user_id"]
    # The token proves who was admin when the link started, up to 15 minutes
    # ago; a suspension, offboarding or demotion since then stops the mint.
    # The jti is already claimed, so the same token cannot be retried.
    perms = await resolve_user_permissions(user_id, db, org_id=org.id)
    if perms is None or perms.status != "active" or not has_role_at_least(perms, ProfileRole.ADMIN):
        logger.warning("slack_link_user_not_admin", org_id=org.id, user_id=user_id)
        return JSONResponse(status_code=status.HTTP_403_FORBIDDEN, content={"code": "not_admin"})
    # mint_slack_key re-validates kb_ids against the org now (400): a KB may
    # have been deleted while the admin was in Slack.
    try:
        plaintext, key, kb_ids = await mint_slack_key(db, org_id=org.id, user_id=user_id, kb_ids=claims["kb_ids"])
    except HTTPException as exc:
        if exc.status_code == status.HTTP_409_CONFLICT:
            # An installation row implies a key (FK), so this covers both.
            return _conflict("org_already_linked")
        raise
    conflict = await _insert_installation(db, org_id=org.id, key_id=key.id, workspace=body)
    if conflict is not None:
        return conflict
    await db.commit()

    emit_event("slack.key_created", org_id=org.id, user_id=user_id, properties={"api_key_id": key.id, "kb_ids": kb_ids})
    emit_event(
        "slack.linked",
        org_id=org.id,
        properties={"api_key_id": key.id, "team_id": body.team_id, "slack_user_id": body.slack_user_id},
    )
    await _audit_internal_call(request, org_id=org.id)
    return LinkOAuthResponse(
        org_id=org.id,
        org_name=org.name,
        key_id=key.id,
        kb_ids=kb_ids,
        api_key=plaintext,
        return_url=claims["return_url"],
    )


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
