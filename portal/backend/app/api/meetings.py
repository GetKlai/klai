"""
Meeting bot API -- start/stop Vexa bots and serve transcripts.
Route prefix: /api/bots
"""

import asyncio
import io
import logging
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, model_validator
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.models.meetings import VexaMeeting
from app.services.vexa import parse_meeting_url, vexa
from app.services.zitadel import zitadel

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/bots", tags=["meetings"])
bearer = HTTPBearer()

ACTIVE_STATUSES = ("pending", "joining", "recording")
MAX_CONCURRENT_BOTS = 2


async def _get_user_id(
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
) -> str:
    """Validate the OIDC access token via Zitadel and return the user sub.

    Follows the same auth pattern as app/api/me.py.
    """
    try:
        info = await zitadel.get_userinfo(credentials.credentials)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Ongeldig of verlopen token",
        ) from exc
    user_id = info.get("sub", "")
    if not user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Geen gebruiker gevonden")
    return user_id


def _require_webhook_secret(request: Request) -> None:
    if not settings.vexa_webhook_secret:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Webhook not configured")
    token = request.headers.get("Authorization", "")
    if token != f"Bearer {settings.vexa_webhook_secret}":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")


# -- Request / Response models -----------------------------------------------


class StartMeetingRequest(BaseModel):
    meeting_url: str
    meeting_title: str | None = None
    consent_given: bool = False


class MeetingResponse(BaseModel):
    id: UUID
    platform: str
    meeting_url: str
    meeting_title: str | None
    status: str
    consent_given: bool
    transcript_text: str | None
    transcript_segments: list[dict] | None
    language: str | None
    duration_seconds: int | None
    error_message: str | None
    started_at: datetime | None
    ended_at: datetime | None
    created_at: datetime

    model_config = {"from_attributes": True}


class MeetingListResponse(BaseModel):
    items: list[MeetingResponse]
    total: int


# -- Endpoints ---------------------------------------------------------------


@router.get("/meetings", response_model=MeetingListResponse)
async def list_meetings(
    user_id: str = Depends(_get_user_id),
    db: AsyncSession = Depends(get_db),
    limit: int = 50,
    offset: int = 0,
) -> MeetingListResponse:
    result = await db.execute(
        select(VexaMeeting)
        .where(VexaMeeting.zitadel_user_id == user_id)
        .order_by(VexaMeeting.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    items = list(result.scalars().all())
    count = await db.scalar(select(func.count(VexaMeeting.id)).where(VexaMeeting.zitadel_user_id == user_id))
    return MeetingListResponse(
        items=[MeetingResponse.model_validate(i) for i in items],
        total=count or 0,
    )


@router.post("/meetings", status_code=status.HTTP_202_ACCEPTED, response_model=MeetingResponse)
async def start_meeting(
    body: StartMeetingRequest,
    user_id: str = Depends(_get_user_id),
    db: AsyncSession = Depends(get_db),
) -> MeetingResponse:
    if not body.consent_given:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Consent required")

    ref = parse_meeting_url(body.meeting_url)
    if ref is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Geen geldig vergader-URL (Google Meet, Zoom of Teams)",
        )

    # Enforce system-wide concurrent limit
    active_count = await db.scalar(select(func.count(VexaMeeting.id)).where(VexaMeeting.status.in_(ACTIVE_STATUSES)))
    if (active_count or 0) >= MAX_CONCURRENT_BOTS:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Maximaal 2 actieve vergaderingen tegelijk. Stop een bestaande bot om door te gaan.",
        )

    # Create meeting record
    meeting = VexaMeeting(
        zitadel_user_id=user_id,
        platform=ref.platform,
        native_meeting_id=ref.native_meeting_id,
        meeting_url=body.meeting_url,
        meeting_title=body.meeting_title,
        consent_given=True,
        status="pending",
    )
    db.add(meeting)
    await db.flush()

    # Dispatch bot
    try:
        bot_resp = await vexa.start_bot(ref.platform, ref.native_meeting_id)
        meeting.bot_id = str(bot_resp.get("bot_id", ""))
        meeting.status = "joining"
        meeting.started_at = datetime.now(UTC)
    except httpx.HTTPStatusError as exc:
        meeting.status = "failed"
        meeting.error_message = f"Bot start failed: {exc.response.status_code}"
        logger.error("Vexa bot start failed: %s", exc)

    await db.commit()
    await db.refresh(meeting)
    return MeetingResponse.model_validate(meeting)


@router.get("/meetings/{meeting_id}", response_model=MeetingResponse)
async def get_meeting(
    meeting_id: UUID,
    user_id: str = Depends(_get_user_id),
    db: AsyncSession = Depends(get_db),
) -> MeetingResponse:
    meeting = await db.scalar(
        select(VexaMeeting).where(VexaMeeting.id == meeting_id, VexaMeeting.zitadel_user_id == user_id)
    )
    if meeting is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Meeting not found")
    return MeetingResponse.model_validate(meeting)


@router.post("/meetings/{meeting_id}/stop", response_model=MeetingResponse)
async def stop_meeting(
    meeting_id: UUID,
    user_id: str = Depends(_get_user_id),
    db: AsyncSession = Depends(get_db),
) -> MeetingResponse:
    meeting = await db.scalar(
        select(VexaMeeting).where(VexaMeeting.id == meeting_id, VexaMeeting.zitadel_user_id == user_id)
    )
    if meeting is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Meeting not found")
    if meeting.status not in ACTIVE_STATUSES:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Meeting is not active")

    ref = parse_meeting_url(meeting.meeting_url)
    if ref:
        try:
            await vexa.stop_bot(ref.platform, ref.native_meeting_id)
        except httpx.HTTPStatusError as exc:
            logger.warning("Vexa stop_bot failed (continuing): %s", exc)

    meeting.status = "processing"
    meeting.ended_at = datetime.now(UTC)
    await db.commit()
    await db.refresh(meeting)
    return MeetingResponse.model_validate(meeting)


@router.delete("/meetings/{meeting_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_meeting(
    meeting_id: UUID,
    user_id: str = Depends(_get_user_id),
    db: AsyncSession = Depends(get_db),
) -> None:
    meeting = await db.scalar(
        select(VexaMeeting).where(VexaMeeting.id == meeting_id, VexaMeeting.zitadel_user_id == user_id)
    )
    if meeting is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Meeting not found")

    ref = parse_meeting_url(meeting.meeting_url)
    if ref and meeting.status in ACTIVE_STATUSES:
        try:
            await vexa.stop_bot(ref.platform, ref.native_meeting_id)
        except httpx.HTTPStatusError:
            pass

    await db.delete(meeting)
    await db.commit()


# -- Webhook from Vexa -------------------------------------------------------


class SpeakerEvent(BaseModel):
    timestamp: float  # seconds from meeting start
    participant_name: str | None = None


class VexaWebhookPayload(BaseModel):
    """Accepts multiple webhook formats from Vexa:

    - send_webhook.py (completion): flat {id, platform, native_meeting_id, status, ...}
    - send_status_webhook.py: {event_type, meeting: {id, platform, native_meeting_id, status}}
    - send_event_webhook (recording.completed): {event_type, recording: {...}} — no meeting ref
    """

    platform: str | None = None
    native_meeting_id: str | None = None
    vexa_meeting_id: int | None = None  # vexa internal integer meeting ID
    status: str | None = None
    ended_at: str | None = None
    speaker_events: list[SpeakerEvent] = []

    model_config = {"extra": "ignore"}

    @model_validator(mode="before")
    @classmethod
    def _normalize(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        # send_status_webhook.py: {event_type, meeting: {id, platform, native_meeting_id, status}}
        if "meeting" in data and "platform" not in data:
            meeting = data["meeting"] or {}
            return {
                "vexa_meeting_id": meeting.get("id"),
                "platform": meeting.get("platform"),
                "native_meeting_id": meeting.get("native_meeting_id"),
                "status": meeting.get("status"),
                "ended_at": meeting.get("end_time"),
            }
        # send_webhook.py: flat {id, platform, native_meeting_id, status, ...}
        return {**data, "vexa_meeting_id": data.get("id")}


async def run_transcription(meeting: VexaMeeting, db: AsyncSession) -> None:
    """Download audio from Vexa and transcribe. Updates meeting in place; caller must commit."""
    try:
        if meeting.vexa_meeting_id is None:
            raise ValueError("vexa_meeting_id is not set — cannot look up recording")

        # Vexa finalizes the recording asynchronously after the meeting ends.
        # Poll until the recording is ready (max 5 attempts x 5s = 25s).
        audio_bytes: bytes | None = None
        audio_fmt: str = "wav"
        for attempt in range(5):
            try:
                audio_bytes, audio_fmt = await vexa.get_recording(meeting.vexa_meeting_id)
                break
            except ValueError:
                if attempt < 4:
                    logger.info(
                        "Recording not ready for vexa meeting %s (attempt %d/5), retrying in 5s…",
                        meeting.vexa_meeting_id,
                        attempt + 1,
                    )
                    await asyncio.sleep(5)
                else:
                    raise
        assert audio_bytes is not None
        filename = f"recording.{audio_fmt}"
        mime = "audio/wav" if audio_fmt == "wav" else f"audio/{audio_fmt}"

        async with httpx.AsyncClient(timeout=300.0) as client:
            resp = await client.post(
                f"{settings.whisper_server_url}/v1/audio/transcriptions",
                files={"file": (filename, io.BytesIO(audio_bytes), mime)},
            )
            resp.raise_for_status()
            whisper_result = resp.json()

        meeting.transcript_text = whisper_result.get("text", "")
        meeting.transcript_segments = None
        meeting.language = whisper_result.get("language", "")
        duration = whisper_result.get("duration", 0)
        meeting.duration_seconds = int(duration) if duration else None
        meeting.status = "done"
        meeting.error_message = None

    except Exception as exc:
        logger.exception("Transcription failed for meeting %s: %s", meeting.id, exc)
        meeting.status = "failed"
        meeting.error_message = str(exc)


@router.post("/internal/webhook", status_code=status.HTTP_200_OK)
async def vexa_webhook(
    payload: VexaWebhookPayload,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> dict:
    _require_webhook_secret(request)

    # Ignore events without a meeting reference (e.g. recording.completed without platform)
    if not payload.platform or not payload.native_meeting_id:
        logger.info("Vexa webhook: no platform/native_meeting_id, ignoring")
        return {"status": "ignored"}

    # Map vexa status → portal status
    VEXA_STATUS_MAP = {
        "joining": "joining",
        "awaiting_admission": "joining",
        "active": "recording",
        "recording": "recording",
    }
    portal_status = VEXA_STATUS_MAP.get(payload.status or "")

    # Find the meeting by platform + native_meeting_id
    meeting = await db.scalar(
        select(VexaMeeting)
        .where(
            VexaMeeting.platform == payload.platform,
            VexaMeeting.native_meeting_id == payload.native_meeting_id,
            VexaMeeting.status.in_((*ACTIVE_STATUSES, "processing")),
        )
        .order_by(VexaMeeting.created_at.desc())
    )
    if meeting is None:
        logger.warning("Vexa webhook: no matching meeting for %s/%s", payload.platform, payload.native_meeting_id)
        return {"status": "ignored"}

    # Non-completion status change: sync status and return
    # Do NOT downgrade from "processing" — user may have already clicked Stop
    if payload.status is not None and payload.status != "completed":
        if portal_status and meeting.status != portal_status and meeting.status != "processing":
            meeting.status = portal_status
            await db.commit()
            logger.info("Vexa webhook: synced status %s→%s for meeting %s", payload.status, portal_status, meeting.id)
        return {"status": "synced"}

    meeting.status = "processing"
    meeting.ended_at = datetime.now(UTC) if not meeting.ended_at else meeting.ended_at
    if payload.vexa_meeting_id:
        meeting.vexa_meeting_id = payload.vexa_meeting_id
    await db.commit()

    await run_transcription(meeting, db)
    await db.commit()
    return {"status": "ok"}
