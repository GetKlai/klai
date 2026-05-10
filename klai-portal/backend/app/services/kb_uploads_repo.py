"""CRUD helpers for the ``kb_uploads`` tracking table.

SPEC-KB-FILE-UPLOAD-001 — small repository module so the route, the
poller and any future callers go through the same idempotent helpers.

All functions take an ``AsyncSession`` parameter — the caller is
responsible for opening it via the right tenant context:

- Routes use the request-scoped ``Depends(get_db)`` session (tenant
  context is set by ``Depends(get_caller)`` upstream).
- The poller uses :func:`app.core.database.cross_org_session` for the
  initial SELECT (sees all orgs) and
  :func:`app.core.database.tenant_scoped_session` for per-row UPDATEs
  (so the cat-D RLS WITH-CHECK clause matches).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.kb_uploads import KBUpload

# Status enum — must match the alembic CHECK constraint.
STATUS_PROCESSING = "processing"
STATUS_INGESTING = "ingesting"
STATUS_DONE = "done"
STATUS_FAILED = "failed"

_TERMINAL_STATUSES: frozenset[str] = frozenset({STATUS_DONE, STATUS_FAILED})
_PENDING_STATUSES: frozenset[str] = frozenset({STATUS_PROCESSING, STATUS_INGESTING})


@dataclass(frozen=True)
class KBUploadView:
    """Minimal read-projection — what the route + poller need.

    Decoupled from the SQLAlchemy model so callers don't accidentally
    mutate persistent state by setting attributes on a returned object.
    """

    id: uuid.UUID
    kb_id: int
    org_id: int
    created_by: str
    filename: str
    extension: str
    mime: str
    bytes: int
    source_ref: str
    status: str
    failure_reason: str | None
    docling_task_id: str | None
    artifact_id: str | None
    created_at: datetime
    updated_at: datetime

    @property
    def is_terminal(self) -> bool:
        return self.status in _TERMINAL_STATUSES


def _to_view(row: KBUpload) -> KBUploadView:
    return KBUploadView(
        id=row.id,
        kb_id=row.kb_id,
        org_id=row.org_id,
        created_by=row.created_by,
        filename=row.filename,
        extension=row.extension,
        mime=row.mime,
        bytes=row.bytes,
        source_ref=row.source_ref,
        status=row.status,
        failure_reason=row.failure_reason,
        docling_task_id=row.docling_task_id,
        artifact_id=row.artifact_id,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


async def create_upload(
    db: AsyncSession,
    *,
    kb_id: int,
    org_id: int,
    created_by: str,
    filename: str,
    extension: str,
    mime: str,
    bytes_count: int,
    source_ref: str,
    status: str,
    docling_task_id: str | None = None,
    artifact_id: str | None = None,
    failure_reason: str | None = None,
) -> KBUploadView:
    """Insert a new row and return its view.

    The session must be tenant-scoped to ``org_id`` (cat-D RLS WITH
    CHECK fires on insert).
    """
    row = KBUpload(
        id=uuid.uuid4(),
        kb_id=kb_id,
        org_id=org_id,
        created_by=created_by,
        filename=filename,
        extension=extension,
        mime=mime,
        bytes=bytes_count,
        source_ref=source_ref,
        status=status,
        docling_task_id=docling_task_id,
        artifact_id=artifact_id,
        failure_reason=failure_reason,
    )
    db.add(row)
    # Flush so the DB-side defaults (created_at, updated_at) populate
    # while the tenant context is still active. db.refresh() after
    # commit would open a fresh transaction without the GUC and 42501
    # on cat-D — see post_commit_db_refresh pitfall.
    await db.flush()
    await db.refresh(row)
    return _to_view(row)


async def mark_done(
    db: AsyncSession,
    *,
    upload_id: uuid.UUID,
    artifact_id: str,
) -> None:
    """Transition a row to ``done`` with the resulting artifact id."""
    await db.execute(
        update(KBUpload)
        .where(KBUpload.id == upload_id)
        .values(status=STATUS_DONE, artifact_id=artifact_id, updated_at=_now())
    )


async def mark_failed(
    db: AsyncSession,
    *,
    upload_id: uuid.UUID,
    failure_reason: str,
) -> None:
    """Transition a row to ``failed`` with a structured reason."""
    await db.execute(
        update(KBUpload)
        .where(KBUpload.id == upload_id)
        .values(status=STATUS_FAILED, failure_reason=failure_reason, updated_at=_now())
    )


async def mark_ingesting(db: AsyncSession, *, upload_id: uuid.UUID) -> None:
    """Transition ``processing`` → ``ingesting`` once docling finishes."""
    await db.execute(
        update(KBUpload).where(KBUpload.id == upload_id).values(status=STATUS_INGESTING, updated_at=_now())
    )


async def get_view(db: AsyncSession, *, upload_id: uuid.UUID) -> KBUploadView | None:
    """Fetch the view for a single row, or None when not found.

    The session must be tenant-scoped (cat-D RLS filters cross-org rows).
    """
    result = await db.execute(select(KBUpload).where(KBUpload.id == upload_id))
    row = result.scalar_one_or_none()
    return _to_view(row) if row is not None else None


async def list_pending(
    db: AsyncSession,
    *,
    limit: int = 50,
) -> list[KBUploadView]:
    """List uploads still being processed.

    Used by the poller. Caller must use ``cross_org_session`` so RLS
    does not filter rows from other tenants — every pending upload
    needs to be advanced regardless of who uploaded it.
    """
    result = await db.execute(
        select(KBUpload).where(KBUpload.status.in_(_PENDING_STATUSES)).order_by(KBUpload.updated_at.asc()).limit(limit)
    )
    return [_to_view(row) for row in result.scalars().all()]


def _now() -> Any:
    """Return a SQL ``NOW()`` expression for ``updated_at`` writes.

    Imported lazily to avoid a top-level dependency cycle if tests stub
    the SQLAlchemy module.
    """
    from sqlalchemy import func

    return func.now()
