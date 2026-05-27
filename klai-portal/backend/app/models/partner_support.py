"""Partner support-session models.

Stores integration-specific support assistant sessions around the generic
Partner Chat API. The chat endpoint stays stateless; HubSpot and future
integrations can persist their own turn history here.
"""

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class PartnerSupportSession(Base):
    __tablename__ = "partner_support_sessions"
    __table_args__ = (
        CheckConstraint(
            "integration_type IN ('hubspot_email_support')",
            name="ck_partner_support_sessions_integration_type",
        ),
        CheckConstraint(
            "status IN ('active','archived')",
            name="ck_partner_support_sessions_status",
        ),
        UniqueConstraint(
            "org_id",
            "partner_api_key_id",
            "integration_type",
            "hubspot_portal_id",
            "hubspot_ticket_id",
            "hubspot_user_id_hash",
            name="uq_partner_support_session_scope",
        ),
        Index("ix_partner_support_sessions_org_updated", "org_id", "updated_at"),
        Index("ix_partner_support_sessions_ticket", "org_id", "hubspot_portal_id", "hubspot_ticket_id"),
    )

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        primary_key=True,
        server_default=func.gen_random_uuid().cast(String),
    )
    org_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("portal_orgs.id", ondelete="CASCADE"),
        nullable=False,
    )
    partner_api_key_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("partner_api_keys.id", ondelete="CASCADE"),
        nullable=False,
    )
    integration_type: Mapped[str] = mapped_column(String(64), nullable=False)
    hubspot_portal_id: Mapped[str] = mapped_column(String(64), nullable=False)
    hubspot_ticket_id: Mapped[str] = mapped_column(String(64), nullable=False)
    hubspot_user_id_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    contact_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    subject_snapshot: Mapped[str | None] = mapped_column(Text, nullable=True)
    content_snapshot: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, server_default="active")
    session_metadata: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    last_message_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    message_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")


class PartnerSupportMessage(Base):
    __tablename__ = "partner_support_messages"
    __table_args__ = (
        CheckConstraint(
            "role IN ('agent','assistant','system')",
            name="ck_partner_support_messages_role",
        ),
        Index("ix_partner_support_messages_session_seq", "session_id", "sequence"),
        Index("ix_partner_support_messages_org_created", "org_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    session_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("partner_support_sessions.id", ondelete="CASCADE"),
        nullable=False,
    )
    org_id: Mapped[int] = mapped_column(Integer, nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    draft_body: Mapped[str | None] = mapped_column(Text, nullable=True)
    sources: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    model_alias: Mapped[str | None] = mapped_column(String(64), nullable=True)
    completion_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
