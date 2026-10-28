"""Data access for klai-slack. Every function takes the caller's session, so one
``async with db.session() as s:`` block is one transaction.

Scope convention: ``kb_ids=None`` means "no stored choice" (the row is
deleted), which the scope resolver reads as "all allowed".
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Literal

from sqlalchemy import delete, desc, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.crypto import decrypt, encrypt
from app.models import (
    ChannelDefault,
    ProcessedEvent,
    ThreadScope,
    Turn,
    WorkspaceLink,
    slack_bots,
    slack_installations,
)

TURN_RETENTION = timedelta(days=30)
EVENT_RETENTION = timedelta(hours=24)


# --- workspace link --------------------------------------------------------


async def get_link(session: AsyncSession, team_id: str) -> WorkspaceLink | None:
    return await session.get(WorkspaceLink, team_id)


async def save_link(
    session: AsyncSession,
    *,
    team_id: str,
    org_id: int,
    org_name: str,
    api_key: str,
    key_id: int,
    linked_by_slack_user_id: str,
) -> None:
    """Insert or replace the team's link; a revoked link is overwritten by a fresh one."""
    values = {
        "team_id": team_id,
        "org_id": org_id,
        "org_name": org_name,
        "klai_api_key_enc": encrypt(api_key),
        "key_id": key_id,
        "linked_by_slack_user_id": linked_by_slack_user_id,
        "linked_at": datetime.now(UTC),
        "revoked_at": None,
    }
    stmt = insert(WorkspaceLink).values(**values)
    stmt = stmt.on_conflict_do_update(
        index_elements=[WorkspaceLink.team_id],
        set_={k: stmt.excluded[k] for k in values if k != "team_id"},
    )
    await session.execute(stmt)


def link_api_key(link: WorkspaceLink) -> str:
    """The decrypted Klai partner key of a link. Never log the result."""
    return decrypt(link.klai_api_key_enc)


# --- knowledge-base scope --------------------------------------------------


async def get_channel_default(
    session: AsyncSession, team_id: str, channel_id: str
) -> list[int] | None:
    row = await session.get(ChannelDefault, (team_id, channel_id))
    return None if row is None else list(row.kb_ids)


async def set_channel_default(
    session: AsyncSession, team_id: str, channel_id: str, kb_ids: list[int] | None, set_by: str
) -> None:
    if not kb_ids:
        await session.execute(
            delete(ChannelDefault).where(
                ChannelDefault.team_id == team_id, ChannelDefault.channel_id == channel_id
            )
        )
        return
    stmt = insert(ChannelDefault).values(
        team_id=team_id, channel_id=channel_id, kb_ids=kb_ids, set_by=set_by
    )
    await session.execute(
        stmt.on_conflict_do_update(
            index_elements=[ChannelDefault.team_id, ChannelDefault.channel_id],
            set_={
                "kb_ids": stmt.excluded.kb_ids,
                "set_by": stmt.excluded.set_by,
                "updated_at": datetime.now(UTC),
            },
        )
    )


async def get_thread_scope(
    session: AsyncSession, team_id: str, channel_id: str, thread_ts: str
) -> list[int] | None:
    row = await session.get(ThreadScope, (team_id, channel_id, thread_ts))
    return None if row is None else list(row.kb_ids)


async def set_thread_scope(
    session: AsyncSession,
    team_id: str,
    channel_id: str,
    thread_ts: str,
    kb_ids: list[int] | None,
    set_by: str,
) -> None:
    if not kb_ids:
        await session.execute(
            delete(ThreadScope).where(
                ThreadScope.team_id == team_id,
                ThreadScope.channel_id == channel_id,
                ThreadScope.thread_ts == thread_ts,
            )
        )
        return
    stmt = insert(ThreadScope).values(
        team_id=team_id, channel_id=channel_id, thread_ts=thread_ts, kb_ids=kb_ids, set_by=set_by
    )
    await session.execute(
        stmt.on_conflict_do_update(
            index_elements=[ThreadScope.team_id, ThreadScope.channel_id, ThreadScope.thread_ts],
            set_={
                "kb_ids": stmt.excluded.kb_ids,
                "set_by": stmt.excluded.set_by,
                "updated_at": datetime.now(UTC),
            },
        )
    )


# --- transcript -----------------------------------------------------------


async def record_turn(
    session: AsyncSession,
    *,
    team_id: str,
    channel_id: str,
    thread_ts: str,
    message_ts: str,
    role: Literal["user", "assistant"],
    text: str,
    completion_id: str | None = None,
    kb_ids: list[int] | None = None,
) -> None:
    session.add(
        Turn(
            team_id=team_id,
            channel_id=channel_id,
            thread_ts=thread_ts,
            message_ts=message_ts,
            role=role,
            text=text,
            completion_id=completion_id,
            kb_ids=kb_ids,
        )
    )
    await session.flush()


async def list_turns(
    session: AsyncSession, team_id: str, channel_id: str, thread_ts: str
) -> list[Turn]:
    """The thread's turns, oldest first."""
    result = await session.execute(
        select(Turn)
        .where(Turn.team_id == team_id, Turn.channel_id == channel_id, Turn.thread_ts == thread_ts)
        .order_by(Turn.created_at, Turn.id)
    )
    return list(result.scalars())


# --- event dedupe ---------------------------------------------------------


async def mark_event_processed(session: AsyncSession, event_id: str) -> bool:
    """Insert-first dedupe: True for the first delivery of ``event_id``, False for a retry."""
    result = await session.execute(
        insert(ProcessedEvent)
        .values(event_id=event_id)
        .on_conflict_do_nothing(index_elements=[ProcessedEvent.event_id])
        .returning(ProcessedEvent.event_id)
    )
    return result.scalar_one_or_none() is not None


# --- installation + teardown ----------------------------------------------


async def get_bot_token(session: AsyncSession, team_id: str) -> str | None:
    """The team's latest bot token (decrypted by the column type), or None."""
    result = await session.execute(
        select(slack_bots.c.bot_token)
        .where(slack_bots.c.team_id == team_id, slack_bots.c.bot_token.is_not(None))
        .order_by(desc(slack_bots.c.installed_at))
        .limit(1)
    )
    return result.scalar_one_or_none()


async def get_team_name(session: AsyncSession, team_id: str) -> str | None:
    result = await session.execute(
        select(slack_bots.c.team_name)
        .where(slack_bots.c.team_id == team_id)
        .order_by(desc(slack_bots.c.installed_at))
        .limit(1)
    )
    return result.scalar_one_or_none()


async def purge_team(session: AsyncSession, team_id: str) -> None:
    """Delete every row klai-slack holds for a team, Bolt installation rows included."""
    for model in (WorkspaceLink, ChannelDefault, ThreadScope, Turn):
        await session.execute(delete(model).where(model.team_id == team_id))
    for table in (slack_bots, slack_installations):
        await session.execute(delete(table).where(table.c.team_id == team_id))


async def purge_expired(session: AsyncSession) -> None:
    """Retention per spec §6.1: turns after 30 days, dedupe rows after 24 hours."""
    now = datetime.now(UTC)
    await session.execute(delete(Turn).where(Turn.created_at < now - TURN_RETENTION))
    await session.execute(
        delete(ProcessedEvent).where(ProcessedEvent.received_at < now - EVENT_RETENTION)
    )
