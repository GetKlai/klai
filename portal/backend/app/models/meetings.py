"""
VexaMeeting model -- stores meeting bot sessions and transcripts.
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class VexaMeeting(Base):
    __tablename__ = "vexa_meetings"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    zitadel_user_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    org_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("portal_orgs.id"), nullable=True)
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
    language: Mapped[str | None] = mapped_column(String(16), nullable=True)
    duration_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
