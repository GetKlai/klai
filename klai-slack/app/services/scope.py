"""Effective knowledge-base scope of a turn (SPEC-SLACK-001 §5).

Thread scope wins over the channel default, the channel default over "all
allowed"; a stored choice is always intersected with the live layer-1 list
from the Partner API, so a revocation in the portal takes effect on the next
turn. An empty intersection returns ``[]``: the caller shows "kies opnieuw"
and must not call the Partner API, because widening silently to all KBs is
exactly what the governance model forbids.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app import repo


def effective_kb_ids(
    allowed: list[int],
    channel_default: list[int] | None,
    thread_scope: list[int] | None,
) -> list[int] | None:
    """None = "all allowed" (omit ``knowledge_base_ids``); [] = stored choice no longer valid."""
    stored = thread_scope if thread_scope is not None else channel_default
    if stored is None:
        return None
    allowed_set = set(allowed)
    return [kb_id for kb_id in stored if kb_id in allowed_set]


async def resolve_kb_ids(
    session: AsyncSession, team_id: str, channel_id: str, thread_ts: str | None, allowed: list[int]
) -> list[int] | None:
    """Load the stored choices for a turn and apply :func:`effective_kb_ids`."""
    thread = (
        await repo.get_thread_scope(session, team_id, channel_id, thread_ts) if thread_ts else None
    )
    channel = await repo.get_channel_default(session, team_id, channel_id)
    return effective_kb_ids(allowed, channel, thread)
