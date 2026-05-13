"""
Personal knowledge item routes:
  GET    /knowledge/v1/personal/items              — list personal artifacts
  DELETE /knowledge/v1/personal/items/{artifact_id} — soft-delete a personal artifact

SPEC-TI-003-FOLLOWUP-001 AC-1: each handler opens a tenant_scoped_connection
on the caller's org_id and threads the conn into every pg_store call so RLS
sees the tenant context.
"""

from datetime import UTC, datetime

import structlog
from fastapi import APIRouter, HTTPException, Query

from knowledge_ingest import pg_store, qdrant_store
from knowledge_ingest.db import tenant_scoped_connection
from knowledge_ingest.models import ArtifactSummary, PersonalItemsResponse

logger = structlog.get_logger()
router = APIRouter()


def _unix_to_iso(ts: int) -> str:
    """Convert a Unix timestamp (int) to ISO 8601 string."""
    return datetime.fromtimestamp(ts, tz=UTC).isoformat()


@router.get("/knowledge/v1/personal/items", response_model=PersonalItemsResponse)
async def list_personal_items(
    org_id: str = Query(...),
    user_id: str = Query(...),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> PersonalItemsResponse:
    """List personal knowledge artifacts for a user."""
    if not org_id or not user_id:
        raise HTTPException(status_code=400, detail="org_id and user_id are required")

    async with tenant_scoped_connection(org_id) as conn:
        rows = await pg_store.list_personal_artifacts(conn, org_id, user_id, limit, offset)
        total = await pg_store.count_personal_artifacts(conn, org_id, user_id)

    items = [
        ArtifactSummary(
            id=str(row["id"]),
            path=row["path"],
            assertion_mode=row.get("assertion_mode"),
            tags=[],
            created_at=_unix_to_iso(row["created_at"]),
        )
        for row in rows
    ]

    return PersonalItemsResponse(items=items, total=total, limit=limit, offset=offset)


@router.delete("/knowledge/v1/personal/items/{artifact_id}")
async def delete_personal_item(
    artifact_id: str,
    org_id: str = Query(...),
    user_id: str = Query(...),
) -> dict:
    """Soft-delete a personal knowledge artifact and remove its Qdrant vectors."""
    if not org_id or not user_id:
        raise HTTPException(status_code=400, detail="org_id and user_id are required")

    async with tenant_scoped_connection(org_id) as conn:
        artifact = await pg_store.get_personal_artifact(conn, artifact_id, org_id, user_id)
        if artifact is None:
            raise HTTPException(status_code=404, detail="Artifact not found")

        path = artifact["path"]
        await pg_store.soft_delete_artifact(conn, org_id, "personal", path)

    # Qdrant is outside the tenant_scoped_connection -- it has its own auth.
    await qdrant_store.delete_document(org_id, "personal", path)

    logger.info(
        "personal_artifact_deleted",
        artifact_id=artifact_id,
        path=path,
        user_id=user_id,
        org_id=org_id,
    )
    return {"status": "ok"}
