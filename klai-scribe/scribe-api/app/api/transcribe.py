"""
Transcription API endpoints:

  POST /v1/transcribe                    - upload + persist audio + queue transcription
  POST /v1/transcriptions/{id}/retry     - re-queue failed transcription
  GET  /v1/transcriptions                - list user's transcripts
  GET  /v1/transcriptions/{id}           - get single transcript
  PATCH /v1/transcriptions/{id}          - update name
  DELETE /v1/transcriptions/{id}         - delete transcript + audio file
  POST /v1/transcriptions/{id}/summarize - AI summarization
  POST /v1/transcriptions/{id}/ingest    - ingest into knowledge base
"""

import asyncio
import contextlib
import uuid
from datetime import UTC, datetime
from typing import Literal

import httpx
import structlog
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import CallerIdentity, get_authenticated_caller
from app.core.config import settings
from app.core.database import get_db
from app.models.transcription import Transcription
from app.services import jobs, summarizer
from app.services.audio_storage import delete_audio, save_audio

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/v1", tags=["transcription"])
_UPLOAD_FILE = File(...)
# 16 is the width of `transcriptions.language_hint`; longer input is a 422, not a failed insert.
_LANGUAGE_FORM = Form(default=None, max_length=16)
_CALLER = Depends(get_authenticated_caller)
_DB = Depends(get_db)
_RETRY_LANGUAGE = Query(default=None, max_length=16)
_LIST_LIMIT = Query(default=20, ge=1, le=100)
_LIST_OFFSET = Query(default=0, ge=0)
_FORCE_SUMMARY = Query(default=False)


# -- Response models -----------------------------------------------------------


class TranscriptionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str | None = None
    status: str = "transcribed"
    text: str | None = None
    language: str | None = None
    duration_seconds: float | None = None
    inference_time_seconds: float | None = None
    summary_json: dict | None = None
    created_at: datetime


class TranscriptionListItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str | None = None
    status: str = "transcribed"
    text: str | None = None
    language: str | None = None
    duration_seconds: float | None = None
    created_at: datetime
    has_summary: bool = False


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


def normalize_audio(raw: bytes, filename: str) -> bytes:
    """Lazy wrapper so importing API schemas does not require libmagic."""
    from app.services.audio import normalize_audio as _normalize_audio

    return _normalize_audio(raw, filename)


async def _wait_for_job(txn_id: str) -> None:
    """Wait up to `transcribe_wait_seconds`; the job keeps running after a timeout."""
    with contextlib.suppress(TimeoutError):
        await asyncio.wait_for(jobs.enqueue(txn_id), timeout=settings.transcribe_wait_seconds)


def _to_response(record: Transcription) -> TranscriptionResponse:
    return TranscriptionResponse(
        id=record.id,
        name=record.name,
        status=record.status,
        text=record.text,
        language=record.language,
        duration_seconds=float(record.duration_seconds) if record.duration_seconds else None,
        inference_time_seconds=(
            float(record.inference_time_seconds) if record.inference_time_seconds else None
        ),
        summary_json=record.summary_json,
        created_at=record.created_at,
    )


def _owned_transcription_filters(txn_id: str, caller: CallerIdentity):
    """Return the ownership + tenant predicates for a single transcription."""
    return (
        Transcription.id == txn_id,
        Transcription.user_id == caller.user_id,
        Transcription.org_id == caller.org_id,
    )


def _caller_transcription_filters(caller: CallerIdentity):
    """Return the tenant-scoped collection predicates for a caller."""
    return (
        Transcription.user_id == caller.user_id,
        Transcription.org_id == caller.org_id,
    )


# -- POST /v1/transcribe ------------------------------------------------------


@router.post("/transcribe", response_model=TranscriptionResponse, status_code=201)
async def transcribe(
    file: UploadFile = _UPLOAD_FILE,
    language: str | None = _LANGUAGE_FORM,
    caller: CallerIdentity = _CALLER,
    db: AsyncSession = _DB,
) -> TranscriptionResponse:
    """Upload audio, persist to disk, queue the transcription.

    Audio is always saved before transcription starts. The answer carries the
    record as it is after at most `transcribe_wait_seconds`: `transcribed`,
    `failed` (audio retained for retry) or `processing` (the job continues).
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

    # Generate ID and save audio to disk FIRST.
    # SPEC-SEC-HYGIENE-001 REQ-33.1: `_safe_audio_path` raises ValueError on
    # malformed user_id / txn_id. With HY-34 in place upstream this is purely
    # defense-in-depth (auth.get_authenticated_caller already rejects malformed
    # sub), but the SPEC asks the caller to map ValueError → 400.
    txn_id = "txn_" + uuid.uuid4().hex
    try:
        audio_path = await loop.run_in_executor(None, save_audio, caller.user_id, txn_id, wav_bytes)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Ongeldige opslaglocatie",
        ) from exc

    # Create DB record with status=processing
    record = Transcription(
        id=txn_id,
        user_id=caller.user_id,
        org_id=caller.org_id,
        name=filename if filename != "upload" else None,
        status="processing",
        audio_path=audio_path,
        language_hint=language,
        created_at=datetime.now(UTC),
    )
    db.add(record)
    await db.commit()

    await _wait_for_job(txn_id)
    await db.refresh(record)

    return _to_response(record)


# -- POST /v1/transcriptions/{id}/retry ----------------------------------------


@router.post("/transcriptions/{txn_id}/retry", response_model=TranscriptionResponse)
async def retry_transcription(
    txn_id: str,
    language: str | None = _RETRY_LANGUAGE,
    caller: CallerIdentity = _CALLER,
    db: AsyncSession = _DB,
) -> TranscriptionResponse:
    """Retry transcription for a failed record using the preserved audio file."""
    result = await db.execute(
        select(Transcription).where(*_owned_transcription_filters(txn_id, caller))
    )
    record = result.scalar_one_or_none()
    if record is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Transcript niet gevonden",
        )

    # A `processing` row is owned by the queue (or recovered by it after a
    # restart); re-queueing it would start a second job on the same audio.
    if record.status != "failed":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Alleen mislukte transcripties kunnen opnieuw worden geprobeerd",
        )

    if not record.audio_path:
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail="Audio bestand niet meer beschikbaar",
        )

    record.status = "processing"
    record.claimed_at = None
    record.attempts = 0
    if language is not None:
        record.language_hint = language
    await db.commit()

    await _wait_for_job(txn_id)
    await db.refresh(record)

    return _to_response(record)


# -- GET /v1/transcriptions ----------------------------------------------------


@router.get("/transcriptions", response_model=TranscriptionListResponse)
async def list_transcriptions(
    limit: int = _LIST_LIMIT,
    offset: int = _LIST_OFFSET,
    caller: CallerIdentity = _CALLER,
    db: AsyncSession = _DB,
) -> TranscriptionListResponse:
    total_result = await db.execute(
        select(func.count())
        .select_from(Transcription)
        .where(*_caller_transcription_filters(caller))
    )
    total = total_result.scalar_one()

    rows = await db.execute(
        select(Transcription)
        .where(*_caller_transcription_filters(caller))
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
    caller: CallerIdentity = _CALLER,
    db: AsyncSession = _DB,
) -> TranscriptionResponse:
    result = await db.execute(
        select(Transcription).where(*_owned_transcription_filters(txn_id, caller))
    )
    record = result.scalar_one_or_none()
    if record is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Transcript niet gevonden",
        )

    return _to_response(record)


# -- PATCH /v1/transcriptions/{id} ---------------------------------------------


@router.patch("/transcriptions/{txn_id}", response_model=TranscriptionResponse)
async def patch_transcription(
    txn_id: str,
    body: TranscriptionPatch,
    caller: CallerIdentity = _CALLER,
    db: AsyncSession = _DB,
) -> TranscriptionResponse:
    """Update the name of a transcription."""
    result = await db.execute(
        select(Transcription).where(*_owned_transcription_filters(txn_id, caller))
    )
    record = result.scalar_one_or_none()
    if record is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Transcript niet gevonden",
        )

    record.name = body.name
    await db.commit()
    await db.refresh(record)

    return _to_response(record)


# -- DELETE /v1/transcriptions/{id} --------------------------------------------


@router.delete("/transcriptions/{txn_id}", status_code=204)
async def delete_transcription(
    txn_id: str,
    caller: CallerIdentity = _CALLER,
    db: AsyncSession = _DB,
) -> None:
    result = await db.execute(
        select(Transcription).where(*_owned_transcription_filters(txn_id, caller))
    )
    record = result.scalar_one_or_none()
    if record is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Transcript niet gevonden",
        )

    # Clean up audio file (legacy records that still have audio at time of delete)
    delete_audio(record.audio_path)

    await db.execute(delete(Transcription).where(*_owned_transcription_filters(txn_id, caller)))
    await db.commit()


# -- POST /v1/transcriptions/{id}/summarize ------------------------------------


@router.post("/transcriptions/{txn_id}/summarize", response_model=SummarizeResponse)
async def summarize_transcription(
    txn_id: str,
    body: SummarizeRequest,
    force: bool = _FORCE_SUMMARY,
    caller: CallerIdentity = _CALLER,
    db: AsyncSession = _DB,
) -> SummarizeResponse:
    """Generate an AI summary for a transcription."""
    result = await db.execute(
        select(Transcription).where(*_owned_transcription_filters(txn_id, caller))
    )
    record = result.scalar_one_or_none()
    if record is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Transcript niet gevonden",
        )

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
            record.text,
            body.recording_type,
            language,
            # Scribe calls LiteLLM on the master key, which names no tenant.
            # Without this the transcript reaches Mistral unmasked.
            org_id=caller.org_id,
        )
    except httpx.HTTPStatusError as exc:
        logger.exception("summarization_litellm_error", status=exc.response.status_code)
        raise HTTPException(status_code=502, detail="Summarization failed") from exc
    except Exception as exc:
        logger.exception("summarization_unexpected_error")
        raise HTTPException(status_code=502, detail="Summarization failed") from exc

    record.summary_json = summary_result
    await db.commit()
    await db.refresh(record)

    return SummarizeResponse(summary_json=record.summary_json)


# -- POST /v1/transcriptions/{id}/ingest --------------------------------------


class IngestToKBRequest(BaseModel):
    """Body schema for transcription-to-KB ingest.

    SPEC-SEC-IDENTITY-ASSERT-001 REQ-3.1: ``org_id`` was removed. The
    target tenant is derived from portal-verified caller identity (see
    ``get_authenticated_caller``) — a body field would let any caller with a
    valid JWT push into any org's KB (the S1 finding in spec.md).
    """

    kb_slug: str


class IngestToKBResponse(BaseModel):
    artifact_id: str
    status: str


@router.post("/transcriptions/{txn_id}/ingest", response_model=IngestToKBResponse)
async def ingest_transcription_to_kb(
    txn_id: str,
    body: IngestToKBRequest,
    caller: CallerIdentity = _CALLER,
    db: AsyncSession = _DB,
) -> IngestToKBResponse:
    """Add a transcription to the authenticated user's primary-org KB.

    Tenant identity is sourced from portal-verified caller identity — never
    from the request body. Transcription ownership is enforced by both
    ``Transcription.user_id`` and ``Transcription.org_id``.
    """
    from app.services.knowledge_adapter import ingest_scribe_transcript

    result = await db.execute(
        select(Transcription).where(*_owned_transcription_filters(txn_id, caller))
    )
    record = result.scalar_one_or_none()
    if record is None:
        raise HTTPException(status_code=404, detail="Transcript niet gevonden")

    artifact_id = await ingest_scribe_transcript(
        org_id=caller.org_id,
        kb_slug=body.kb_slug,
        transcription=record,
    )
    return IngestToKBResponse(artifact_id=artifact_id, status="ok")
