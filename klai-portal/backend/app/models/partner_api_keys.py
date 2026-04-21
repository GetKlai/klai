"""Partner API key models — SPEC-API-001 + SPEC-WIDGET-002.

PartnerAPIKey is the developer-facing `pk_live_...` credential for
server-to-server integration. Widget-specific columns and the soft-delete
`active` column were removed in SPEC-WIDGET-002 (see migration
f0a1b2c3d4e5). Widgets now live in their own `widgets` table.
"""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


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
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    created_by: Mapped[str] = mapped_column(String(64), nullable=False)


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
