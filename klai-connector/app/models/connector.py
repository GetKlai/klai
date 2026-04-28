"""SQLAlchemy model for the connector.connectors table.

NOTE: This table is the legacy connector registry used by the scheduler.
      New connector config is owned by the portal (portal_connectors).
      sync_runs no longer has a FK back to this table.
"""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, Integer, LargeBinary, String, func
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Base class for all models."""


class Connector(Base):
    """Connector configuration model.

    Columns:
        id: UUID primary key
        org_id: UUID (tenant isolation)
        name: VARCHAR(255)
        connector_type: VARCHAR(50) -- 'github'
        config: JSONB -- type-specific config (installation_id, repo, branch, etc.)
        credentials_enc: BYTEA -- AES-256-GCM encrypted credentials
        encryption_key_version: INTEGER -- for future key rotation
        schedule: VARCHAR(100) -- cron expression, NULL = on-demand only
        is_enabled: BOOLEAN
        last_sync_at: TIMESTAMPTZ
        last_sync_status: VARCHAR(20)
        created_at: TIMESTAMPTZ
        updated_at: TIMESTAMPTZ
    """

    __tablename__ = "connectors"
    __table_args__ = {"schema": "connector"}

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    connector_type: Mapped[str] = mapped_column(String(50), nullable=False)
    config: Mapped[dict] = mapped_column(JSONB, nullable=False)  # type: ignore[type-arg]
    credentials_enc: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    encryption_key_version: Mapped[int] = mapped_column(Integer, default=1)
    schedule: Mapped[str | None] = mapped_column(String(100), nullable=True)
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    last_sync_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    last_sync_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )

