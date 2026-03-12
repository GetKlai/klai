"""
Transcription API endpoints:

  POST /v1/transcribe              — upload + transcribe (does NOT save)
  POST /v1/transcriptions          — save a transcription draft
  GET  /v1/transcriptions          — list user's transcripts
  GET  /v1/transcriptions/{id}     — get single transcript
  DELETE /v1/transcriptions/{id}  — delete transcript
"""
import asyncio
import logging
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from pydantic import BaseModel
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_current_user_id
from app.core.config import settings
from app.core.database import get_db
from app.models.transcription import Transcription
from app.services.audio import normalize_audio
from app.services.providers import get_provider

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1", tags=["transcription"])


# ── Response models ──────────────────────────────────────────────────────────

class TranscriptionDraft(BaseModel):
    """Result of transcription — not yet persisted."""
    text: str
    language: str
    duration_seconds: float
    inference_time_seconds: float | None = None
    provider: str
    model: str


class TranscriptionResponse(BaseModel):
    id: str
    text: str
    language: str
    duration_seconds: float
    inference_time_seconds: float | None = None
    created_at: datetime

    class Config:
        from_attributes = True


class TranscriptionListResponse(BaseModel):
    items: list[TranscriptionResponse]
    total: int


# ── POST /v1/transcribe ───────────────────────────────────────────────────────

@router.post("/transcribe", response_model=TranscriptionDraft, status_code=200)
async def transcribe(
    file: UploadFile = File(...),
    language: str | None = Form(default=None),
    user_id: str = Depends(get_current_user_id),
) -> TranscriptionDraft:
    """Transcribe audio. Does NOT save to database — call POST /v1/transcriptions to persist."""
    raw = await file.read(settings.max_upload_bytes + 1)
    if len(raw) > settings.max_upload_bytes:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Bestand te groot (max {settings.max_upload_mb} MB)",
        )

    filename = file.filename or "upload"

    loop = asyncio.get_event_loop()
    wav_bytes = await loop.run_in_executor(None, normalize_audio, raw, filename)

    provider = get_provider()
    result = await provider.transcribe(wav_bytes, language)

    return TranscriptionDraft(
        text=result.text,
        language=result.language,
        duration_seconds=result.duration_seconds,
        inference_time_seconds=result.inference_time_seconds,
        provider=result.provider,
        model=result.model,
    )


# ── POST /v1/transcriptions ───────────────────────────────────────────────────

@router.post("/transcriptions", response_model=TranscriptionResponse, status_code=201)
async def save_transcription(
    body: TranscriptionDraft,
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
) -> TranscriptionResponse:
    """Persist a transcription draft. Called only when the user explicitly saves."""
    txn_id = "txn_" + uuid.uuid4().hex
    record = Transcription(
        id=txn_id,
        user_id=user_id,
        text=body.text,
        language=body.language,
        duration_seconds=body.duration_seconds,
        inference_time_seconds=body.inference_time_seconds or 0,
        provider=body.provider,
        model=body.model,
        created_at=datetime.utcnow(),
    )
    db.add(record)
    await db.commit()
    await db.refresh(record)

    return TranscriptionResponse(
        id=record.id,
        text=record.text,
        language=record.language,
        duration_seconds=float(record.duration_seconds),
        inference_time_seconds=float(record.inference_time_seconds),
        created_at=record.created_at,
    )


# ── GET /v1/transcriptions ────────────────────────────────────────────────────

@router.get("/transcriptions", response_model=TranscriptionListResponse)
async def list_transcriptions(
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
) -> TranscriptionListResponse:
    total_result = await db.execute(
        select(func.count()).select_from(Transcription).where(Transcription.user_id == user_id)
    )
    total = total_result.scalar_one()

    rows = await db.execute(
        select(Transcription)
        .where(Transcription.user_id == user_id)
        .order_by(Transcription.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    items = rows.scalars().all()

    return TranscriptionListResponse(
        items=[
            TranscriptionResponse(
                id=t.id,
                text=t.text,
                language=t.language,
                duration_seconds=float(t.duration_seconds),
                inference_time_seconds=float(t.inference_time_seconds),
                created_at=t.created_at,
            )
            for t in items
        ],
        total=total,
    )


# ── GET /v1/transcriptions/{id} ───────────────────────────────────────────────

@router.get("/transcriptions/{txn_id}", response_model=TranscriptionResponse)
async def get_transcription(
    txn_id: str,
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
) -> TranscriptionResponse:
    result = await db.execute(
        select(Transcription).where(
            Transcription.id == txn_id,
            Transcription.user_id == user_id,
        )
    )
    record = result.scalar_one_or_none()
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Transcript niet gevonden")

    return TranscriptionResponse(
        id=record.id,
        text=record.text,
        language=record.language,
        duration_seconds=float(record.duration_seconds),
        inference_time_seconds=float(record.inference_time_seconds),
        created_at=record.created_at,
    )


# ── DELETE /v1/transcriptions/{id} ───────────────────────────────────────────

@router.delete("/transcriptions/{txn_id}", status_code=204)
async def delete_transcription(
    txn_id: str,
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
) -> None:
    result = await db.execute(
        select(Transcription).where(
            Transcription.id == txn_id,
            Transcription.user_id == user_id,
        )
    )
    record = result.scalar_one_or_none()
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Transcript niet gevonden")

    await db.execute(
        delete(Transcription).where(
            Transcription.id == txn_id,
            Transcription.user_id == user_id,
        )
    )
    await db.commit()
