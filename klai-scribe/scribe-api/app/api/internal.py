from __future__ import annotations

import hmac

import structlog
from fastapi import APIRouter, Header, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import delete, select

from app.core.config import settings
from app.core.database import AsyncSessionLocal
from app.models.transcription import Transcription
from app.services.audio_storage import delete_audio

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/internal/v1", tags=["internal"])


class WipeStateResponse(BaseModel):
    rows_deleted: int
    audio_files_deleted: int
    status: str


def _require_internal_secret(value: str | None) -> None:
    expected = settings.portal_internal_secret
    if not value or not expected or not hmac.compare_digest(value.encode(), expected.encode()):
        logger.warning("scribe_internal_secret_mismatch")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="unauthenticated",
        )


@router.post("/orgs/{org_id}/wipe-state", response_model=WipeStateResponse)
async def wipe_org_state(
    org_id: str,
    x_internal_secret: str | None = Header(default=None, alias="X-Internal-Secret"),
) -> WipeStateResponse:
    """Hard-delete every Scribe row and retained audio file for one org.

    Tenant deprovisioning calls this before portal deletes the org row. The
    endpoint is idempotent: a second call returns zero rows/files deleted.
    """
    _require_internal_secret(x_internal_secret)

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Transcription.audio_path)
            .where(Transcription.org_id == org_id)
            .with_for_update()
        )
        audio_paths = [path for path in result.scalars().all() if path]

        audio_files_deleted = 0
        for audio_path in audio_paths:
            delete_audio(audio_path)
            audio_files_deleted += 1

        delete_result = await session.execute(
            delete(Transcription).where(Transcription.org_id == org_id)
        )
        await session.commit()

    rows_deleted = delete_result.rowcount if delete_result.rowcount is not None else 0
    logger.info(
        "scribe_org_state_wiped",
        org_id=org_id,
        rows_deleted=rows_deleted,
        audio_files_deleted=audio_files_deleted,
    )
    return WipeStateResponse(
        rows_deleted=rows_deleted,
        audio_files_deleted=audio_files_deleted,
        status="ok",
    )
