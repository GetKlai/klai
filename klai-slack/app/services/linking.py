"""Persist a workspace link the portal already recorded (`/klai connect` and the OAuth link)."""

from __future__ import annotations

import structlog

from app import db, repo
from app.clients.portal import LinkResult, PortalClient, PortalUnavailable
from app.services import allowed_kbs

logger = structlog.get_logger()


async def store_link(
    portal: PortalClient,
    *,
    team_id: str,
    result: LinkResult,
    api_key: str,
    linked_by_slack_user_id: str,
) -> bool:
    """Save the encrypted key; False when that failed and the portal link was released.

    The portal records its side before klai-slack stores the key, so a failed
    write is compensated with an unlink: the portal then shows "revoked" with a
    re-link action instead of a link this service cannot serve.
    """
    try:
        async with db.session() as s:
            await repo.save_link(
                s,
                team_id=team_id,
                org_id=result.org_id,
                org_name=result.org_name,
                api_key=api_key,
                key_id=result.key_id,
                linked_by_slack_user_id=linked_by_slack_user_id,
            )
    except Exception:
        logger.exception("slack_link_store_failed", team_id=team_id, org_id=result.org_id)
        try:
            await portal.unlink(team_id)
        except PortalUnavailable:
            logger.exception("slack_link_compensating_unlink_failed", org_id=result.org_id)
        return False
    allowed_kbs.invalidate(team_id)
    logger.info(
        "slack_workspace_linked",
        team_id=team_id,
        org_id=result.org_id,
        key_id=result.key_id,
        kb_count=len(result.kb_ids),
    )
    return True
