"""
Transcription API endpoints:

  POST /v1/transcribe                    - upload + persist audio + transcribe
  POST /v1/transcriptions/{id}/retry     - retry failed transcription
  GET  /v1/transcriptions                - list user's transcripts
  GET  /v1/transcriptions/{id}           - get single transcript
  PATCH /v1/transcriptions/{id}          - update name
  DELETE /v1/transcriptions/{id}         - delete transcript + audio file
  POST /v1/transcriptions/{id}/summarize - AI summarization
  POST /v1/transcriptions/{id}/ingest    - ingest into knowledge base
"""
import asyncio
import logging
import uuid
from datetime import datetime
from typing import Literal

import httpx
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from pydantic import BaseModel
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_current_user_id
from app.core.config import settings
from app.core.database import get_db
from app.models.transcription import Transcription
from app.services import summarizer
from app.services.audio import normalize_audio
from app.services.audio_storage import (
    delete_audio,
    finalize_success,
    read_audio,
    save_audio,
)
from app.services.providers import get_provider

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1", tags=["transcription"])


# -- Response models -----------------------------------------------------------

class TranscriptionResponse(BaseModel):
    id: str
    name: str | None = None
    status: str = "transcribed"
    text: str | None = None
    language: str | None = None
    duration_seconds: float | None = None
    inference_time_seconds: float | None = None
    summary_json: dict | None = None
    created_at: datetime

    class Config:
        from_attributes = True


class TranscriptionListItem(BaseModel):
    id: str
    name: str | None = None
    status: str = "transcribed"
    text: str | None = None
    language: str | None = None
    duration_seconds: float | None = None
    created_at: datetime
    has_summary: bool = False

    class Config:
        from_attributes = True


class TranscriptionListResponse(BaseModel):
    items: list[TranscriptionListItem]
    total: int


class TranscriptionPatch(BaseModel):
    name: str | None = None


class SummarizeRequest(BaseModel):
    recording_type: Literal["meeting", "recording"]
    language: str | None = None


class SummarizeResponse(BaseModel):
    summary_json: dict


# -- Audio storage helpers ----------------------------------------------------
# save_audio / read_audio / delete_audio / finalize_success live in
# app.services.audio_storage — see that module for the retention policy.


def _title_from_text(text: str | None, max_words: int = 8) -> str | None:
    """Generate a short title from the first words of a transcription."""
    if not text:
        return None
    words = text.split()[:max_words]
    title = " ".join(words)
    if len(text.split()) > max_words:
        title += "…"
    return title


def _to_response(record: Transcription) -> TranscriptionResponse:
    return TranscriptionResponse(
        id=record.id,
        name=record.name,
        status=record.status,
        text=record.text,
        language=record.language,
        duration_seconds=float(record.duration_seconds) if record.duration_seconds else None,
        inference_time_seconds=float(record.inference_time_seconds) if record.inference_time_seconds else None,
        summary_json=record.summary_json,
        created_at=record.created_at,
    )


# -- POST /v1/transcribe ------------------------------------------------------

@router.post("/transcribe", response_model=TranscriptionResponse, status_code=201)
async def transcribe(
    file: UploadFile = File(...),
    language: str | None = Form(default=None),
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
) -> TranscriptionResponse:
    """Upload audio, persist to disk, attempt transcription.

    Audio is always saved before transcription starts. If whisper fails,
    the record stays with status='failed' and the audio is retained for retry.
    """
    raw = await file.read(settings.max_upload_bytes + 1)
    if len(raw) > settings.max_upload_bytes:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Bestand te groot (max {settings.max_upload_mb} MB)",
        )

    filename = file.filename or "upload"

    # Normalize audio (validates format + converts to WAV 16kHz mono)
    loop = asyncio.get_event_loop()
    wav_bytes = await loop.run_in_executor(None, normalize_audio, raw, filename)

    # Generate ID and save audio to disk FIRST
    txn_id = "txn_" + uuid.uuid4().hex
    audio_path = await loop.run_in_executor(None, save_audio, user_id, txn_id, wav_bytes)

    # Create DB record with status=processing
    record = Transcription(
        id=txn_id,
        user_id=user_id,
        name=filename if filename != "upload" else None,
        status="processing",
        audio_path=audio_path,
        created_at=datetime.utcnow(),
    )
    db.add(record)
    await db.commit()

    # Attempt transcription
    try:
        provider = get_provider()
        result = await provider.transcribe(wav_bytes, language)
    except Exception:
        record.status = "failed"
        await db.commit()
        await db.refresh(record)
        logger.warning("Transcription failed for %s, audio preserved at %s", txn_id, audio_path)
        return _to_response(record)

    # Success — mutate record + purge audio from disk (retention policy).
    finalize_success(record, result)

    # Auto-generate a title from transcription when name is a generic filename
    if record.name and record.name.lower().startswith("recording."):
        record.name = _title_from_text(result.text)

    await db.commit()
    await db.refresh(record)

    return _to_response(record)


# -- POST /v1/transcriptions/{id}/retry ----------------------------------------

@router.post("/transcriptions/{txn_id}/retry", response_model=TranscriptionResponse)
async def retry_transcription(
    txn_id: str,
    language: str | None = Query(default=None),
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
) -> TranscriptionResponse:
    """Retry transcription for a failed record using the preserved audio file."""
    result = await db.execute(
        select(Transcription).where(
            Transcription.id == txn_id,
            Transcription.user_id == user_id,
        )
    )
    record = result.scalar_one_or_none()
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Transcript niet gevonden")

    if record.status not in ("failed", "processing"):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Alleen mislukte transcripties kunnen opnieuw worden geprobeerd",
        )

    if not record.audio_path:
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail="Audio bestand niet meer beschikbaar",
        )

    # Read audio from disk
    loop = asyncio.get_event_loop()
    wav_bytes = await loop.run_in_executor(None, read_audio, record.audio_path)

    record.status = "processing"
    await db.commit()

    try:
        provider = get_provider()
        transcription_result = await provider.transcribe(wav_bytes, language)
    except Exception:
        record.status = "failed"
        await db.commit()
        await db.refresh(record)
        logger.warning("Retry failed for %s", txn_id)
        return _to_response(record)

    # Success — mutate record + purge audio from disk (retention policy).
    finalize_success(record, transcription_result)

    # Auto-generate a title from transcription when name is a generic filename
    if record.name and record.name.lower().startswith("recording."):
        record.name = _title_from_text(transcription_result.text)

    await db.commit()
    await db.refresh(record)

    return _to_response(record)


# -- GET /v1/transcriptions ----------------------------------------------------

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
            TranscriptionListItem(
                id=t.id,
                name=t.name,
                status=t.status,
                text=t.text,
                language=t.language,
                duration_seconds=float(t.duration_seconds) if t.duration_seconds else None,
                created_at=t.created_at,
                has_summary=t.summary_json is not None,
            )
            for t in items
        ],
        total=total,
    )


# -- GET /v1/transcriptions/{id} -----------------------------------------------

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

    return _to_response(record)


# -- PATCH /v1/transcriptions/{id} ---------------------------------------------

@router.patch("/transcriptions/{txn_id}", response_model=TranscriptionResponse)
async def patch_transcription(
    txn_id: str,
    body: TranscriptionPatch,
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
) -> TranscriptionResponse:
    """Update the name of a transcription."""
    result = await db.execute(
        select(Transcription).where(
            Transcription.id == txn_id,
            Transcription.user_id == user_id,
        )
    )
    record = result.scalar_one_or_none()
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Transcript niet gevonden")

    record.name = body.name
    await db.commit()
    await db.refresh(record)

    return _to_response(record)


# -- DELETE /v1/transcriptions/{id} --------------------------------------------

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

    # Clean up audio file (legacy records that still have audio at time of delete)
    delete_audio(record.audio_path)

    await db.execute(
        delete(Transcription).where(
            Transcription.id == txn_id,
            Transcription.user_id == user_id,
        )
    )
    await db.commit()


# -- POST /v1/transcriptions/{id}/summarize ------------------------------------

@router.post("/transcriptions/{txn_id}/summarize", response_model=SummarizeResponse)
async def summarize_transcription(
    txn_id: str,
    body: SummarizeRequest,
    force: bool = Query(default=False),
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
) -> SummarizeResponse:
    """Generate an AI summary for a transcription."""
    result = await db.execute(
        select(Transcription).where(
            Transcription.id == txn_id,
            Transcription.user_id == user_id,
        )
    )
    record = result.scalar_one_or_none()
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Transcript niet gevonden")

    if not record.text or not record.text.strip():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Transcriptie bevat geen tekst",
        )

    # Return existing summary if not forcing re-summarization
    if record.summary_json is not None and not force:
        return SummarizeResponse(summary_json=record.summary_json)

    language = body.language or record.language

    try:
        summary_result = await summarizer.summarize_transcription(
            record.text, body.recording_type, language
        )
    except httpx.HTTPStatusError as exc:
        logger.error("LiteLLM HTTP error during summarization: %s", exc)
        raise HTTPException(status_code=502, detail="Summarization failed") from exc
    except Exception as exc:
        logger.error("Unexpected error during summarization: %s", exc)
        raise HTTPException(status_code=502, detail="Summarization failed") from exc

    record.summary_json = summary_result
    await db.commit()
    await db.refresh(record)

    return SummarizeResponse(summary_json=record.summary_json)


# -- POST /v1/transcriptions/{id}/ingest --------------------------------------

class IngestToKBRequest(BaseModel):
    kb_slug: str
    org_id: str


class IngestToKBResponse(BaseModel):
    artifact_id: str
    status: str


@router.post("/transcriptions/{txn_id}/ingest", response_model=IngestToKBResponse)
async def ingest_transcription_to_kb(
    txn_id: str,
    body: IngestToKBRequest,
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
) -> IngestToKBResponse:
    """Add a transcription to a knowledge base."""
    from app.services.knowledge_adapter import ingest_scribe_transcript  # noqa: PLC0415

    result = await db.execute(
        select(Transcription).where(
            Transcription.id == txn_id,
            Transcription.user_id == user_id,
        )
    )
    record = result.scalar_one_or_none()
    if record is None:
        raise HTTPException(status_code=404, detail="Transcript niet gevonden")

    artifact_id = await ingest_scribe_transcript(
        org_id=body.org_id,
        kb_slug=body.kb_slug,
        transcription=record,
    )
    return IngestToKBResponse(artifact_id=artifact_id, status="ok")
