"""Internal API for portal-api (SPEC-SLACK-001 §7.3), guarded by the shared secret."""

from __future__ import annotations

from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, Header, HTTPException, Response, status
from log_utils import verify_shared_secret
from slack_sdk.web.async_client import AsyncWebClient

from app import db, repo
from app.config import Settings, get_settings

logger = structlog.get_logger()

router = APIRouter()


async def require_internal_secret(
    settings: Annotated[Settings, Depends(get_settings)],
    authorization: Annotated[str | None, Header()] = None,
) -> None:
    """Fail closed: an unset configured secret is a 503, a missing or wrong one a 401."""
    if not settings.internal_secret:
        logger.error("internal_secret_not_configured")
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "internal auth not configured")
    supplied = (
        authorization.removeprefix("Bearer ")
        if authorization and authorization.startswith("Bearer ")
        else None
    )
    if not verify_shared_secret(supplied, settings.internal_secret):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "unauthorized")


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@router.delete(
    "/internal/installations/{team_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_internal_secret)],
)
async def delete_installation(team_id: str) -> Response:
    """Portal-side disconnect: revoke the bot token, delete every row of the team. Idempotent."""
    async with db.session() as s:
        bot_token = await repo.get_bot_token(s, team_id)
    if bot_token:
        try:
            await AsyncWebClient(token=bot_token).auth_revoke()
        except Exception:
            # Best effort: the rows go regardless, so the token is unusable by us either way.
            logger.warning("slack_token_revoke_failed", team_id=team_id, exc_info=True)
    async with db.session() as s:
        await repo.purge_team(s, team_id)
    logger.info("slack_installation_deleted", team_id=team_id, token_revoked=bool(bot_token))
    return Response(status_code=status.HTTP_204_NO_CONTENT)
