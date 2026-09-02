"""KB upload tracking model.

SPEC-KB-FILE-UPLOAD-001 — persists per-upload state for the file
ingestion pipeline. The row's lifecycle:

1. ``processing`` — created on a successful POST to
   ``/sources/file``. ``docling_task_id`` is populated for binary-path
   uploads; null for text-path uploads (which skip docling).
2. ``ingesting`` — docling has finished and the markdown was handed
   off to ``/ingest/v1/document``; we are awaiting the
   ``artifact_id`` response. (Transient state — usually <1 s.)
3. ``done`` — terminal success. ``artifact_id`` populated.
4. ``failed`` — terminal failure. ``failure_reason`` carries one of
   the structured codes from ``app.services.file_upload``.

The frontend polls a row by ``id`` (UUID) via
``GET /api/app/knowledge-bases/{kb}/sources/file/{id}/status`` until
the row reaches a terminal state.

``target_path`` marks a row as a REPLACEMENT for an existing source
(SPEC-free change, GetKlai/klai feedback item 28). A normal upload
ingests under ``path = source_ref`` — the sha256 of its own bytes — so an
edited file lands as a second source next to the old one. A replacement
ingests under the ORIGINAL source's path instead, which is exactly what
knowledge-ingest's ``ingest_document`` treats as a new version: it closes
the active artifact, creates the replacement, links ``superseded_by`` and
clears the old Qdrant points. ``source_ref`` still holds the new file's
own content hash, so it stays content-addressed.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    func,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.ext.hybrid import hybrid_property
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class KBUpload(Base):
    """One file upload tracked from POST through completion."""

    __tablename__ = "kb_uploads"
    __table_args__ = (
        CheckConstraint(
            "status IN ('processing', 'ingesting', 'done', 'failed')",
            name="ck_kb_uploads_status",
        ),
        Index(
            "ix_kb_uploads_poller",
            "status",
            "updated_at",
            postgresql_where="status IN ('processing', 'ingesting')",
        ),
        Index("ix_kb_uploads_org_kb", "org_id", "kb_id", "created_at"),
        Index(
            "ix_kb_uploads_docling_task_id",
            "docling_task_id",
            unique=True,
            postgresql_where="docling_task_id IS NOT NULL",
        ),
        Index("ix_kb_uploads_source_ref", "org_id", "kb_id", "source_ref"),
        Index(
            "ix_kb_uploads_target_path",
            "org_id",
            "kb_id",
            "target_path",
            postgresql_where="target_path IS NOT NULL",
        ),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    kb_id: Mapped[int] = mapped_column(
        ForeignKey("portal_knowledge_bases.id", ondelete="CASCADE"),
        nullable=False,
    )
    org_id: Mapped[int] = mapped_column(Integer, nullable=False)
    created_by: Mapped[str] = mapped_column(String(64), nullable=False)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    extension: Mapped[str] = mapped_column(String(16), nullable=False)
    mime: Mapped[str] = mapped_column(String(127), nullable=False)
    bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    source_ref: Mapped[str] = mapped_column(String(128), nullable=False)
    #: Document key to ingest under, when this upload REPLACES an existing
    #: source. NULL for a normal upload, which ingests under ``source_ref``.
    #: See the module docstring.
    target_path: Mapped[str | None] = mapped_column(String(128), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    failure_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    docling_task_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    artifact_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    @hybrid_property
    def document_path(self) -> str:
        """The key this upload's content lives under in knowledge-ingest.

        ``target_path`` for a replacement, ``source_ref`` for a first upload.
        Every version of one source shares this value — it is what ties a
        replacement to the source it overwrites, what the poller ingests
        under, and what a delete has to clear.

        A hybrid so the same name works on a loaded row and inside a query;
        spelling ``target_path or source_ref`` by hand at each call site is
        how one of them ends up disagreeing with the others.
        """
        return self.target_path or self.source_ref

    @document_path.inplace.expression
    @classmethod
    def _document_path_expr(cls):
        return func.coalesce(cls.target_path, cls.source_ref)
