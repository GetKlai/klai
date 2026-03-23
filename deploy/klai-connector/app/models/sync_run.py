"""SQLAlchemy model for the connector.sync_runs table."""

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, ForeignKey, Integer, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.connector import Base


class SyncRun(Base):
    """Sync run history model.

    Columns:
        id: UUID primary key
        connector_id: UUID foreign key -> connector.connectors
        status: VARCHAR(20) -- 'running', 'completed', 'failed', 'auth_error'
        started_at: TIMESTAMPTZ
        completed_at: TIMESTAMPTZ
        documents_total: INTEGER
        documents_ok: INTEGER
        documents_failed: INTEGER
        bytes_processed: BIGINT
        error_details: JSONB -- array of per-document errors
        cursor_state: JSONB -- bookmark for incremental sync
    """

    __tablename__ = "sync_runs"
    __table_args__ = {"schema": "connector"}

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    connector_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("connector.connectors.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    started_at: Mapped[datetime] = mapped_column(server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(nullable=True)
    documents_total: Mapped[int] = mapped_column(Integer, default=0)
    documents_ok: Mapped[int] = mapped_column(Integer, default=0)
    documents_failed: Mapped[int] = mapped_column(Integer, default=0)
    bytes_processed: Mapped[int] = mapped_column(BigInteger, default=0)
    error_details: Mapped[list | None] = mapped_column(JSONB, nullable=True)  # type: ignore[type-arg]
    cursor_state: Mapped[dict | None] = mapped_column(JSONB, nullable=True)  # type: ignore[type-arg]

    connector: Mapped["Connector"] = relationship(  # type: ignore[name-defined]  # noqa: F821
        "Connector",
        back_populates="sync_runs",
    )
