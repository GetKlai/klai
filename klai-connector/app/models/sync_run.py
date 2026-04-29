"""SQLAlchemy model for the connector.sync_runs table."""

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, Integer, String, func
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.connector import Base


class SyncRun(Base):
    """Sync run history model.

    connector_id is a portal connector UUID (portal_connectors.id in the portal DB).
    There is intentionally NO foreign key to connector.connectors — klai-connector is
    a stateless execution plane and does not maintain a local connector registry.

    Columns:
        id: UUID primary key
        connector_id: UUID — portal_connectors.id (no FK; portal is source of truth)
        org_id: VARCHAR(255) — Zitadel resourceowner; tenant scope for sync routes
            (SPEC-SEC-TENANT-001 REQ-7.2). Same shape and source of truth as
            ``Connector.org_id``: the value the portal asserts in the
            ``X-Org-ID`` header on every sync proxy call. No FK; portal is
            source of truth (consistent with ``connector_id``).

            Nullable: historical rows (pre-migration 006) keep NULL —
            no backfill is performed. Those rows fall outside per-org
            filters and are invisible to all tenants. New rows always
            populate org_id because ``trigger_sync`` requires the
            ``X-Org-ID`` header (REQ-7.4).
        status: VARCHAR(20) -- 'running', 'completed', 'failed', 'auth_error', 'pending'
        started_at: TIMESTAMPTZ
        completed_at: TIMESTAMPTZ
        documents_total: INTEGER
        documents_ok: INTEGER
        documents_failed: INTEGER
        bytes_processed: BIGINT
        error_details: JSONB -- array of per-document errors
        cursor_state: JSONB -- bookmark for incremental sync
        quality_status: VARCHAR(20) -- 'healthy' | 'degraded' | 'failed' | NULL
            Added by SPEC-CRAWL-003 migration 005. NULL on historical rows (no backfill).
    """

    __tablename__ = "sync_runs"
    __table_args__ = {"schema": "connector"}

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    connector_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
        index=True,
        # No ForeignKey — connector_id is a portal UUID, portal is source of truth.
    )
    # SPEC-SEC-TENANT-001 REQ-7.2 (v0.5.1 / Zitadel-resourceowner string).
    # Nullable: no backfill on migration 006 — historical rows keep NULL and
    # are invisible to per-org filters. trigger_sync requires X-Org-ID for
    # every new row (REQ-7.4), so the column is effectively NOT NULL on the
    # write path.
    org_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    started_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    documents_total: Mapped[int] = mapped_column(Integer, default=0)
    documents_ok: Mapped[int] = mapped_column(Integer, default=0)
    documents_failed: Mapped[int] = mapped_column(Integer, default=0)
    bytes_processed: Mapped[int] = mapped_column(BigInteger, default=0)
    error_details: Mapped[list | None] = mapped_column(JSONB, nullable=True)  # type: ignore[type-arg]
    cursor_state: Mapped[dict | None] = mapped_column(JSONB, nullable=True)  # type: ignore[type-arg]
    # SPEC-CRAWL-003 REQ-2: content quality guardrail result per sync run.
    # Values: 'healthy' | 'degraded' | 'failed' | NULL (historical rows keep NULL).
    quality_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
