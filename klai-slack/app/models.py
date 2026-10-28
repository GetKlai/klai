"""klai-slack data model (SPEC-SLACK-001 §6.1).

``kb_ids`` columns hold a stored narrowing choice; "all allowed" is the
absence of a row, never an empty array. The Bolt OAuth tables are declared on
the same metadata (with encrypted token columns) so the repository can purge a
team's installation rows in the same transaction as everything else.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.oauth_store import build_bots_table, build_installations_table, build_oauth_states_table


class Base(DeclarativeBase):
    pass


slack_installations = build_installations_table(Base.metadata)
slack_bots = build_bots_table(Base.metadata)
slack_oauth_states = build_oauth_states_table(Base.metadata)


class WorkspaceLink(Base):
    __tablename__ = "workspace_links"

    team_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    org_id: Mapped[int] = mapped_column(Integer, nullable=False)
    org_name: Mapped[str] = mapped_column(Text, nullable=False)
    klai_api_key_enc: Mapped[str] = mapped_column(Text, nullable=False)
    key_id: Mapped[str] = mapped_column(String(36), nullable=False)
    linked_by_slack_user_id: Mapped[str] = mapped_column(String(32), nullable=False)
    linked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ChannelDefault(Base):
    __tablename__ = "channel_defaults"

    team_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    channel_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    kb_ids: Mapped[list[int]] = mapped_column(ARRAY(Integer), nullable=False)
    set_by: Mapped[str] = mapped_column(String(32), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class ThreadScope(Base):
    __tablename__ = "thread_scopes"

    team_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    channel_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    thread_ts: Mapped[str] = mapped_column(String(32), primary_key=True)
    kb_ids: Mapped[list[int]] = mapped_column(ARRAY(Integer), nullable=False)
    set_by: Mapped[str] = mapped_column(String(32), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class Turn(Base):
    __tablename__ = "turns"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    team_id: Mapped[str] = mapped_column(String(32), nullable=False)
    channel_id: Mapped[str] = mapped_column(String(32), nullable=False)
    thread_ts: Mapped[str] = mapped_column(String(32), nullable=False)
    message_ts: Mapped[str] = mapped_column(String(32), nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    completion_id: Mapped[str | None] = mapped_column(String(128))
    kb_ids: Mapped[list[int] | None] = mapped_column(ARRAY(Integer))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ProcessedEvent(Base):
    __tablename__ = "processed_events"

    event_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
