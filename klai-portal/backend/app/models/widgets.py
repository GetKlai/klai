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

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, func
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
