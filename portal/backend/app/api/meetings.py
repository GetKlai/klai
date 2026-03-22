"""
Meeting bot API -- start/stop Vexa bots and serve transcripts.
Route prefix: /api/bots
"""
import io
import logging
from datetime import datetime, timezone
from uuid import UUID

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel
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
    count = await db.scalar(
        select(func.count(VexaMeeting.id)).where(VexaMeeting.zitadel_user_id == user_id)
    )
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
    active_count = await db.scalar(
        select(func.count(VexaMeeting.id)).where(VexaMeeting.status.in_(ACTIVE_STATUSES))
    )
    if (active_count or 0) >= MAX_CONCURRENT_BOTS:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Maximaal 2 actieve vergaderingen tegelijk. Stop een bestaande bot om door te gaan.",
        )

    # Create meeting record
    meeting = VexaMeeting(
        zitadel_user_id=user_id,
        platform=ref.platform,
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
        meeting.started_at = datetime.now(timezone.utc)
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
    meeting.ended_at = datetime.now(timezone.utc)
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
    platform: str
    native_meeting_id: str
    ended_at: str | None = None
    speaker_events: list[SpeakerEvent] = []


def _correlate_speakers(
    segments: list[dict],
    speaker_events: list[SpeakerEvent],
    duration_seconds: float,
) -> list[dict]:
    """Correlate Whisper segments with Vexa speaker events.

    Each segment gets a speaker name based on who was speaking nearest to the
    segment's start timestamp. Unknown speakers get 'Deelnemer N' labels.
    """
    if not speaker_events:
        return segments

    # Build a timeline of (timestamp, name) pairs
    timeline = [(e.timestamp, e.participant_name or "") for e in speaker_events]
    unknown_map: dict[str, str] = {}
    unknown_count = 0

    def resolve_speaker(name: str) -> str:
        nonlocal unknown_count
        if name:
            return name
        if name not in unknown_map:
            unknown_count += 1
            unknown_map[name] = f"Deelnemer {unknown_count}"
        return unknown_map[name]

    attributed = []
    for seg in segments:
        seg_start = seg.get("start", 0)
        # Find the nearest speaker event at or before this segment's start
        speaker = ""
        for ts, name in reversed(timeline):
            if ts <= seg_start:
                speaker = resolve_speaker(name)
                break
        if not speaker:
            speaker = resolve_speaker("")

        attributed.append({**seg, "speaker": speaker})
    return attributed


@router.post("/internal/webhook", status_code=status.HTTP_200_OK)
async def vexa_webhook(
    payload: VexaWebhookPayload,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> dict:
    _require_webhook_secret(request)

    # Find the meeting by platform + status (most recent active/processing)
    meeting = await db.scalar(
        select(VexaMeeting).where(
            VexaMeeting.platform == payload.platform,
            VexaMeeting.status.in_(ACTIVE_STATUSES + ("processing",)),
        ).order_by(VexaMeeting.created_at.desc())
    )
    if meeting is None:
        logger.warning("Vexa webhook: no matching meeting for %s/%s", payload.platform, payload.native_meeting_id)
        return {"status": "ignored"}

    meeting.status = "processing"
    meeting.ended_at = datetime.now(timezone.utc) if not meeting.ended_at else meeting.ended_at
    await db.commit()

    # Download audio from Vexa and transcribe
    try:
        ref = parse_meeting_url(meeting.meeting_url)
        if ref is None:
            raise ValueError("Cannot parse meeting URL for audio download")

        audio_bytes = await vexa.get_recording(ref.platform, ref.native_meeting_id)

        # POST audio directly to whisper-server (no auth required -- internal network)
        async with httpx.AsyncClient(timeout=300.0) as client:
            resp = await client.post(
                f"{settings.whisper_server_url}/",
                files={"audio_file": ("recording.webm", io.BytesIO(audio_bytes), "audio/webm")},
            )
            resp.raise_for_status()
            whisper_result = resp.json()

        raw_segments = whisper_result.get("segments", [])
        duration = whisper_result.get("duration", 0)
        language = whisper_result.get("language", "")

        attributed_segments = _correlate_speakers(raw_segments, payload.speaker_events, duration)

        meeting.transcript_text = whisper_result.get("text", "")
        meeting.transcript_segments = attributed_segments
        meeting.language = language
        meeting.duration_seconds = int(duration) if duration else None
        meeting.status = "done"

    except Exception as exc:
        logger.exception("Transcription failed for meeting %s: %s", meeting.id, exc)
        meeting.status = "failed"
        meeting.error_message = str(exc)

    await db.commit()
    return {"status": "ok"}
