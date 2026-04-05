"""Connector models."""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, LargeBinary, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class PortalConnector(Base):
    __tablename__ = "portal_connectors"
    __table_args__ = (
        Index("ix_portal_connectors_kb_id", "kb_id"),
        Index("ix_portal_connectors_org_id", "org_id"),
    )

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        primary_key=True,
        server_default=func.gen_random_uuid().cast(String),
    )
    kb_id: Mapped[int] = mapped_column(
        ForeignKey("portal_knowledge_bases.id", ondelete="CASCADE"),
        nullable=False,
    )
    org_id: Mapped[int] = mapped_column(
        ForeignKey("portal_orgs.id", ondelete="CASCADE"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    connector_type: Mapped[str] = mapped_column(Text, nullable=False)
    config: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")
    schedule: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_sync_status: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    content_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    allowed_assertion_modes: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    encrypted_credentials: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True, default=None)
    created_by: Mapped[str] = mapped_column(Text, nullable=False)
