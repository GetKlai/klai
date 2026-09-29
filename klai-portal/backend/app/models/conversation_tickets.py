"""Ticket settings per widget and tickets per conversation — SPEC-KNOWLEDGE-ESCALATION-001 §4.1.

``widget_ticket_settings`` holds the tenant's HubSpot service key (encrypted
with ``portal_secrets``) and the ticket targets a reviewer can pick.
``conversation_tickets`` records every ticket attempt; its conversation FK is
``SET NULL`` so the record that a conversation was escalated outlives the
retention purge of the conversation itself, like ``answer_reviews``.

DDL lives in ``post_deploy_e8b3c5a1f9d2_conversation_tickets_rls.sql``
(klai-owned tables, Cat-D RLS) — these classes only describe the shape.
"""

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class WidgetTicketSettings(Base):
    __tablename__ = "widget_ticket_settings"

    widget_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("widgets.id", ondelete="CASCADE"), primary_key=True
    )
    org_id: Mapped[int] = mapped_column(Integer, ForeignKey("portal_orgs.id", ondelete="CASCADE"), nullable=False)
    service_key_encrypted: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    # Entered by the admin; account-info would need the `oauth` scope.
    hubspot_portal_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    # [{"key", "label", "pipeline_id", "stage_id"}], 1-5 items, validated by
    # the admin route's pydantic model before it is stored.
    targets: Mapped[list] = mapped_column(JSONB, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_by_user_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("portal_users.id", ondelete="SET NULL"), nullable=True
    )


class ConversationTicket(Base):
    __tablename__ = "conversation_tickets"
    __table_args__ = (
        CheckConstraint("status IN ('pending','created','failed')", name="ck_conversation_tickets_status"),
        CheckConstraint(
            "contact_status IS NULL OR contact_status IN ('existing','not_found')",
            name="ck_conversation_tickets_contact_status",
        ),
        # Partial unique: purged rows (conversation_id NULL) must not collide.
        Index(
            "uq_conversation_tickets_conversation_target",
            "conversation_id",
            "target_key",
            unique=True,
            postgresql_where=text("conversation_id IS NOT NULL"),
        ),
        Index("ix_conversation_tickets_org_created", "org_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    org_id: Mapped[int] = mapped_column(Integer, ForeignKey("portal_orgs.id", ondelete="CASCADE"), nullable=False)
    conversation_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("widget_conversations.id", ondelete="SET NULL"), nullable=True
    )
    target_key: Mapped[str] = mapped_column(Text, nullable=False)
    # Snapshot: an admin renaming the target later must not rewrite history.
    target_label: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    hubspot_ticket_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    hubspot_contact_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    contact_status: Mapped[str | None] = mapped_column(Text, nullable=True)
    ticket_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by_user_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("portal_users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
