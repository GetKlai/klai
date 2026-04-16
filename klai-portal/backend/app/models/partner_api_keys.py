"""Partner API key models.

SPEC-API-001 REQ-1.2, REQ-1.3:
- PartnerAPIKey: org-scoped API key with SHA-256 hashed storage
- PartnerApiKeyKbAccess: junction table for per-KB access levels

SPEC-WIDGET-001 Task 1:
- integration_type: 'api' (default) or 'widget'
- widget_id: unique public identifier for widget integrations (wgt_ + 40 hex chars)
- widget_config: JSONB with allowed_origins, title, welcome_message, css_variables
"""

import secrets
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


def generate_widget_id() -> str:
    """Generate a unique widget identifier.

    Format: wgt_ + 40 lowercase hexadecimal characters.
    Uses secrets.token_hex(20) for cryptographically secure randomness.
    """
    return f"wgt_{secrets.token_hex(20)}"


class PartnerAPIKey(Base):
    __tablename__ = "partner_api_keys"
    __table_args__ = (
        Index("ix_partner_api_keys_key_hash", "key_hash", unique=True),
        Index("ix_partner_api_keys_org_id", "org_id"),
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
    key_prefix: Mapped[str] = mapped_column(String(12), nullable=False)
    key_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    permissions: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        server_default='{"chat": true, "feedback": true, "knowledge_append": false}',
    )
    rate_limit_rpm: Mapped[int] = mapped_column(Integer, nullable=False, server_default="60")
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    created_by: Mapped[str] = mapped_column(String(64), nullable=False)

    # SPEC-WIDGET-001 Task 1: integration type and widget fields
    integration_type: Mapped[str] = mapped_column(
        String(10),
        nullable=False,
        server_default="api",
    )
    widget_id: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
        unique=True,
    )
    widget_config: Mapped[dict | None] = mapped_column(
        JSONB,
        nullable=True,
    )


class PartnerApiKeyKbAccess(Base):
    __tablename__ = "partner_api_key_kb_access"

    partner_api_key_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("partner_api_keys.id", ondelete="CASCADE"),
        primary_key=True,
    )
    kb_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("portal_knowledge_bases.id", ondelete="CASCADE"),
        primary_key=True,
    )
    access_level: Mapped[str] = mapped_column(String(16), nullable=False)
