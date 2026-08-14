"""
VexaMeeting model -- stores meeting bot sessions and transcripts.
"""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class VexaMeeting(Base):
    __tablename__ = "vexa_meetings"
    __table_args__ = (Index("ix_vexa_meetings_group_id", "group_id"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    zitadel_user_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    org_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("portal_orgs.id"), nullable=True)
    group_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("portal_groups.id", ondelete="SET NULL"), nullable=True
    )
    platform: Mapped[str] = mapped_column(String(32), nullable=False)  # google_meet, zoom, teams
    native_meeting_id: Mapped[str] = mapped_column(String(128), nullable=False)
    meeting_url: Mapped[str] = mapped_column(Text, nullable=False)
    meeting_title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    bot_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    vexa_meeting_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending", server_default="pending")
    consent_given: Mapped[bool] = mapped_column(nullable=False, default=False, server_default="false")
    transcript_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    transcript_segments: Mapped[list[dict] | None] = mapped_column(JSONB, nullable=True)
    summary_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    language: Mapped[str | None] = mapped_column(String(16), nullable=True)
    duration_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    ical_uid: Mapped[str | None] = mapped_column(String(512), nullable=True, unique=True, index=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    recording_deleted: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    recording_deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class VexaWebhookReceipt(Base):
    """Idempotency ledger for Vexa's at-least-once webhook deliveries.

    `event_id` is stable across redeliveries of one logical event (upstream
    derives it from connection_id · event_type · new_status), so it is the
    contract's designated idempotency key. Rows older than 48h are prunable —
    that is the dedupe window webhook.v1 requires receivers to honour.
    """

    __tablename__ = "vexa_webhook_receipts"

    event_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    vexa_meeting_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
