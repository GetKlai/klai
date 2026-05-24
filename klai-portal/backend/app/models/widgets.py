"""Widget ORM models — SPEC-WIDGET-002.

Chat widgets as a first-class domain, separated from partner API keys.

Design decisions (see SPEC-WIDGET-002):
- No authentication-secret columns (no key_prefix, key_hash, permissions).
  Widget auth is 100% JWT-based via WIDGET_JWT_SECRET.
- No `active` / soft-delete field. DELETE is the only way to end a widget.
- widget_kb_access junction has no `access_level` column — widgets always
  have read-only access to their linked KBs.
"""

import secrets
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
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


def generate_widget_id() -> str:
    """Generate a unique widget identifier.

    Format: wgt_ + 40 lowercase hexadecimal characters.
    """
    return f"wgt_{secrets.token_hex(20)}"


class Widget(Base):
    __tablename__ = "widgets"
    __table_args__ = (
        Index("ix_widgets_org_id", "org_id"),
        Index("ix_widgets_widget_id", "widget_id", unique=True),
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
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str | None] = mapped_column(String(512), nullable=True)
    widget_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    widget_config: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        server_default='{"allowed_origins": [], "title": "", "welcome_message": "", "css_variables": {}}',
    )
    public_share_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false", default=False)
    # REQ-2 (Finding B-2): explicit "allow any origin" flag. When True, bypasses the
    # allowed_origins list entirely so the widget is embeddable on any site.
    # Default False so new rows require explicit opt-in.
    # @MX:NOTE: [AUTO] Replaces the old "empty list = open world" behaviour in origin_allowed().
    # @MX:SPEC: SPEC-SEC-CROSS-TENANT-FOLLOWUP-001 REQ-2
    allow_any_origin: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false", default=False)
    rate_limit_rpm: Mapped[int] = mapped_column(Integer, nullable=False, server_default="60")
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    created_by: Mapped[str] = mapped_column(String(64), nullable=False)


class WidgetKbAccess(Base):
    __tablename__ = "widget_kb_access"

    widget_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("widgets.id", ondelete="CASCADE"),
        primary_key=True,
    )
    kb_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("portal_knowledge_bases.id", ondelete="CASCADE"),
        primary_key=True,
    )


class WidgetConversation(Base):
    """Audit-trail row per widget chat session (one widget-load).

    Keyed on (widget_id, session_key) where session_key is the JWT
    JTI from the widget's session token — one browser session =
    one row. RLS Cat-D enforced via post_deploy SQL.
    """

    __tablename__ = "widget_conversations"
    __table_args__ = (
        UniqueConstraint(
            "widget_id",
            "session_key",
            name="uq_widget_conversations_widget_session",
        ),
        Index("ix_widget_conversations_widget_started", "widget_id", "started_at"),
        Index("ix_widget_conversations_org_started", "org_id", "started_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    org_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("portal_orgs.id", ondelete="CASCADE"),
        nullable=False,
    )
    widget_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("widgets.id", ondelete="CASCADE"),
        nullable=False,
    )
    session_key: Mapped[str] = mapped_column(Text, nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    last_message_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    message_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    ip_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    user_agent_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    first_user_query: Mapped[str | None] = mapped_column(Text, nullable=True)
    language_detected: Mapped[str | None] = mapped_column(String(8), nullable=True)
    # REQ-2 (Finding B-2): record origin of each conversation for audit visibility.
    # Truncated to 200 chars. NULL when Origin header was absent (e.g. direct API call).
    # @MX:SPEC: SPEC-SEC-CROSS-TENANT-FOLLOWUP-001 REQ-2
    loaded_origin: Mapped[str | None] = mapped_column(String(200), nullable=True)


class WidgetMessage(Base):
    """One turn (user or assistant) inside a WidgetConversation.

    ``org_id`` is denormalised so the RLS Cat-D policy can scope by
    tenant without a join. ``sources`` carries the citation list
    that came back on the assistant turn (NULL on user turns).
    """

    __tablename__ = "widget_messages"
    __table_args__ = (
        CheckConstraint("role IN ('user','assistant')", name="ck_widget_messages_role"),
        Index("ix_widget_messages_conv_seq", "conversation_id", "sequence"),
        Index("ix_widget_messages_org_created", "org_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    conversation_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("widget_conversations.id", ondelete="CASCADE"),
        nullable=False,
    )
    org_id: Mapped[int] = mapped_column(Integer, nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    sources: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
