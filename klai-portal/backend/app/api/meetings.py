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

from app.api.dependencies import require_product
from app.core.config import settings
from app.core.database import get_db, set_tenant
from app.models.groups import PortalGroup
from app.models.meetings import VexaMeeting
from app.models.portal import PortalUser
from app.services.access import can_write_meeting, get_accessible_meetings, is_member_of_group
from app.services.audit import log_event
from app.services.events import emit_event
from app.services.recording_cleanup import cleanup_recording
from app.services.vexa import parse_meeting_url, vexa
from app.services.zitadel import zitadel

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/bots", tags=["meetings"])
bearer = HTTPBearer()

ACTIVE_STATUSES = ("pending", "joining", "recording")
_BILLABLE_STATUSES = (*ACTIVE_STATUSES, "stopping")
MAX_CONCURRENT_BOTS = 2


async def _get_user_and_org(
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
    db: AsyncSession = Depends(get_db),
) -> tuple[str, int | None]:
    """Validate the OIDC access token and return (user_id, org_id)."""
    try:
        info = await zitadel.get_userinfo(credentials.credentials)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
        ) from exc
    user_id = info.get("sub", "")
    if not user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="No user found")

    portal_user = await db.scalar(select(PortalUser).where(PortalUser.zitadel_user_id == user_id))
    org_id = portal_user.org_id if portal_user else None
    if org_id is not None:
        await set_tenant(db, org_id)
    return user_id, org_id


def _require_webhook_secret(request: Request) -> None:
    # Internal Docker network callers (172.x, 10.x, 192.168.x) are trusted without a token.
    # This avoids embedding secrets in POST_MEETING_HOOKS URLs where they appear in container logs.
    client_host = request.client.host if request.client else ""
    if client_host.startswith(("172.", "10.", "192.168.")):
        return
    if not settings.vexa_webhook_secret:
        return  # No secret configured
    auth_header = request.headers.get("Authorization", "")
    if auth_header != f"Bearer {settings.vexa_webhook_secret}":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")


# -- Request / Response models -----------------------------------------------


class StartMeetingRequest(BaseModel):
    meeting_url: str
    meeting_title: str | None = None
    consent_given: bool = False
    group_id: int | None = None


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
    summary_json: dict | None
    started_at: datetime | None
    ended_at: datetime | None
    created_at: datetime
    group_id: int | None
    visibility: str

    model_config = {"from_attributes": True}


class MeetingListResponse(BaseModel):
    items: list[MeetingResponse]
    total: int


# -- Helpers -----------------------------------------------------------------


async def _build_meeting_response(meeting: VexaMeeting, db: AsyncSession) -> MeetingResponse:
    """Build MeetingResponse enriched with human-readable visibility label."""
    visibility = "personal"
    if meeting.group_id is not None:
        group = await db.scalar(select(PortalGroup).where(PortalGroup.id == meeting.group_id))
        visibility = group.name if group else f"group:{meeting.group_id}"

    data = {
        "id": meeting.id,
        "platform": meeting.platform,
        "meeting_url": meeting.meeting_url,
        "meeting_title": meeting.meeting_title,
        "status": meeting.status,
        "consent_given": meeting.consent_given,
        "transcript_text": meeting.transcript_text,
        "transcript_segments": meeting.transcript_segments,
        "language": meeting.language,
        "duration_seconds": meeting.duration_seconds,
        "error_message": meeting.error_message,
        "summary_json": meeting.summary_json,
        "started_at": meeting.started_at,
        "ended_at": meeting.ended_at,
        "created_at": meeting.created_at,
        "group_id": meeting.group_id,
        "visibility": visibility,
    }
    return MeetingResponse(**data)


# -- Endpoints ---------------------------------------------------------------


@router.get("/meetings", response_model=MeetingListResponse, dependencies=[Depends(require_product("scribe"))])
async def list_meetings(
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
    db: AsyncSession = Depends(get_db),
    limit: int = 50,
    offset: int = 0,
) -> MeetingListResponse:
    # @MX:ANCHOR -- scoped via get_accessible_meetings; do not add direct queries here
    user_id, org_id = await _get_user_and_org(credentials, db)
    if org_id is None:
        return MeetingListResponse(items=[], total=0)

    meetings = await get_accessible_meetings(user_id, org_id, db)
    # Apply pagination in Python (list is already fetched; acceptable for typical sizes)
    total = len(meetings)
    meetings.sort(key=lambda m: m.created_at, reverse=True)
    page = meetings[offset : offset + limit]

    items = [await _build_meeting_response(m, db) for m in page]
    return MeetingListResponse(items=items, total=total)


@router.post(
    "/meetings",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=MeetingResponse,
    dependencies=[Depends(require_product("scribe"))],
)
async def start_meeting(
    body: StartMeetingRequest,
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
    db: AsyncSession = Depends(get_db),
) -> MeetingResponse:
    user_id, org_id = await _get_user_and_org(credentials, db)

    if not body.consent_given:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Consent required")

    ref = parse_meeting_url(body.meeting_url)
    if ref is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Invalid meeting URL (Google Meet, Zoom or Teams)",
        )

    # R5: Validate group membership before setting group_id
    if body.group_id is not None:
        if not await is_member_of_group(user_id, body.group_id, db):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Not a member of the specified group",
            )

    active_count = await db.scalar(select(func.count(VexaMeeting.id)).where(VexaMeeting.status.in_(_BILLABLE_STATUSES)))
    if (active_count or 0) >= MAX_CONCURRENT_BOTS:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Maximum 2 active meetings at a time. Stop an existing bot to continue.",
        )

    meeting = VexaMeeting(
        zitadel_user_id=user_id,
        org_id=org_id,
        group_id=body.group_id,
        platform=ref.platform,
        native_meeting_id=ref.native_meeting_id,
        meeting_url=body.meeting_url,
        meeting_title=body.meeting_title,
        consent_given=True,
        status="pending",
    )
    db.add(meeting)
    await db.flush()

    if org_id is not None:
        await log_event(
            db,
            org_id=org_id,
            actor=user_id,
            action="meeting.created",
            resource_type="meeting",
            resource_id=str(meeting.id),
            details={"group_id": body.group_id} if body.group_id else None,
        )

    try:
        bot_resp = await vexa.start_bot(ref.platform, ref.native_meeting_id)
        meeting.bot_id = str(bot_resp.get("bot_container_id") or bot_resp.get("id") or "")
        meeting.vexa_meeting_id = bot_resp.get("id")
        meeting.status = "joining"
        meeting.started_at = datetime.now(UTC)
    except httpx.HTTPStatusError as exc:
        meeting.status = "failed"
        meeting.error_message = f"Bot start failed: {exc.response.status_code}"
        logger.exception("Vexa bot start failed: %s", exc)

    await db.commit()
    await db.refresh(meeting)
    emit_event("meeting.started", org_id=org_id, user_id=user_id, properties={"platform": ref.platform})
    return await _build_meeting_response(meeting, db)


@router.get("/meetings/{meeting_id}", response_model=MeetingResponse, dependencies=[Depends(require_product("scribe"))])
async def get_meeting(
    meeting_id: UUID,
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
    db: AsyncSession = Depends(get_db),
) -> MeetingResponse:
    user_id, org_id = await _get_user_and_org(credentials, db)

    meeting = await db.scalar(select(VexaMeeting).where(VexaMeeting.id == meeting_id))
    if meeting is None or meeting.org_id != org_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Meeting not found")

    # Check read access: owner, group member, or same org
    accessible = await get_accessible_meetings(user_id, org_id, db)
    if not any(m.id == meeting_id for m in accessible):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="No access to this meeting")

    return await _build_meeting_response(meeting, db)


@router.post(
    "/meetings/{meeting_id}/stop",
    response_model=MeetingResponse,
    dependencies=[Depends(require_product("scribe"))],
)
async def stop_meeting(
    meeting_id: UUID,
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
    db: AsyncSession = Depends(get_db),
) -> MeetingResponse:
    user_id, org_id = await _get_user_and_org(credentials, db)

    meeting = await db.scalar(select(VexaMeeting).where(VexaMeeting.id == meeting_id, VexaMeeting.org_id == org_id))
    if meeting is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Meeting not found")
    if not await can_write_meeting(user_id, meeting, db):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="No write access to this meeting")
    if meeting.status not in ACTIVE_STATUSES:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Meeting is not active")

    ref = parse_meeting_url(meeting.meeting_url)
    if ref:
        try:
            await vexa.stop_bot(ref.platform, ref.native_meeting_id)
        except httpx.HTTPStatusError as exc:
            logger.warning("Vexa stop_bot failed (continuing): %s", exc)

    meeting.status = "stopping"
    meeting.ended_at = datetime.now(UTC)
    await db.commit()
    await db.refresh(meeting)
    return await _build_meeting_response(meeting, db)


@router.delete(
    "/meetings/{meeting_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_product("scribe"))],
)
async def delete_meeting(
    meeting_id: UUID,
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
    db: AsyncSession = Depends(get_db),
) -> None:
    user_id, org_id = await _get_user_and_org(credentials, db)

    meeting = await db.scalar(select(VexaMeeting).where(VexaMeeting.id == meeting_id, VexaMeeting.org_id == org_id))
    if meeting is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Meeting not found")
    if not await can_write_meeting(user_id, meeting, db):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="No write access to this meeting")

    ref = parse_meeting_url(meeting.meeting_url)
    if ref and meeting.status in ACTIVE_STATUSES:
        try:
            await vexa.stop_bot(ref.platform, ref.native_meeting_id)
        except httpx.HTTPStatusError:
            pass

    await db.delete(meeting)
    await db.commit()


@router.post("/meetings/{meeting_id}/summarize", response_model=dict)
async def summarize_meeting_endpoint(
    meeting_id: UUID,
    force: bool = False,
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
    db: AsyncSession = Depends(get_db),
) -> dict:
    from app.services.summarizer import summarize_meeting

    user_id, org_id = await _get_user_and_org(credentials, db)

    meeting = await db.scalar(select(VexaMeeting).where(VexaMeeting.id == meeting_id, VexaMeeting.org_id == org_id))
    if meeting is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Meeting not found")

    # Read access sufficient for summarize
    accessible = await get_accessible_meetings(user_id, org_id, db)
    if not any(m.id == meeting_id for m in accessible):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="No access to this meeting")

    if not meeting.transcript_text:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="No transcript available for summarization",
        )

    if meeting.summary_json and not force:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Summary already exists. Use force=true to re-summarize.",
        )

    try:
        summary = await summarize_meeting(
            transcript_text=meeting.transcript_text,
            segments=meeting.transcript_segments,
            language=meeting.language or "en",
        )
    except Exception as exc:
        logger.exception("Summarization failed for meeting %s: %s", meeting_id, exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Summarization failed: {exc}",
        ) from exc

    meeting.summary_json = summary
    await db.commit()
    emit_event(
        "meeting.summarized",
        org_id=meeting.org_id,
        user_id=meeting.zitadel_user_id,
        properties={"language": meeting.language or "unknown"},
    )
    return {"summary": summary}


# -- Knowledge ingest --------------------------------------------------------


class IngestMeetingRequest(BaseModel):
    kb_slug: str
    org_id: str


class IngestMeetingResponse(BaseModel):
    artifact_id: str
    status: str


@router.post("/{meeting_id}/ingest", response_model=IngestMeetingResponse)
async def ingest_meeting_to_kb(
    meeting_id: UUID,
    body: IngestMeetingRequest,
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
    db: AsyncSession = Depends(get_db),
) -> IngestMeetingResponse:
    """Add a meeting transcript to a knowledge base."""
    from app.services.knowledge_adapter import ingest_vexa_meeting

    user_id, _org_id = await _get_user_and_org(credentials, db)

    meeting = await db.scalar(
        select(VexaMeeting).where(
            VexaMeeting.id == meeting_id,
            VexaMeeting.zitadel_user_id == user_id,
        )
    )
    if meeting is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Meeting niet gevonden")

    if not meeting.transcript_segments:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Geen transcript beschikbaar voor dit meeting",
        )

    artifact_id = await ingest_vexa_meeting(
        org_id=body.org_id,
        kb_slug=body.kb_slug,
        meeting=meeting,
    )
    return IngestMeetingResponse(artifact_id=artifact_id, status="ok")


# -- Webhook from Vexa -------------------------------------------------------


class SpeakerEvent(BaseModel):
    timestamp: float
    participant_name: str | None = None


class VexaWebhookPayload(BaseModel):
    """Vexa agentic-runtime webhook envelope.

    Standard format: {event_type, meeting: {id, platform, native_meeting_id, status, ...}}
    Completion format: {id, platform, native_meeting_id, status, ended_at, speaker_events}
    """

    platform: str | None = None
    native_meeting_id: str | None = None
    vexa_meeting_id: int | None = None
    status: str | None = None
    ended_at: str | None = None
    speaker_events: list[SpeakerEvent] = []
    recording_id: int | None = None  # for recording cleanup via API

    model_config = {"extra": "ignore"}

    @model_validator(mode="before")
    @classmethod
    def _normalize(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        # Envelope format: {event_type, meeting: {...}}
        if "meeting" in data and "platform" not in data:
            meeting = data["meeting"] or {}
            return {
                "vexa_meeting_id": meeting.get("id"),
                "platform": meeting.get("platform"),
                "native_meeting_id": meeting.get("native_meeting_id"),
                "status": meeting.get("status"),
                "ended_at": meeting.get("end_time"),
                "recording_id": data.get("recording", {}).get("id")
                if isinstance(data.get("recording"), dict)
                else None,
            }
        # Flat completion format
        return {**data, "vexa_meeting_id": data.get("id")}


def _correlate_speakers(
    segments: list[dict],
    speaker_events: list["SpeakerEvent"],
    duration_seconds: float,
) -> list[dict]:
    """Assign speaker labels to Whisper segments using timed speaker events."""
    if not speaker_events:
        return segments

    events = sorted(speaker_events, key=lambda e: e.timestamp)
    unknown_labels: dict[int, str] = {}
    unknown_counter = 0

    def _speaker_label(event: "SpeakerEvent") -> str:
        nonlocal unknown_counter
        if event.participant_name:
            return event.participant_name
        idx = id(event)
        if idx not in unknown_labels:
            unknown_counter += 1
            unknown_labels[idx] = f"Deelnemer {unknown_counter}"
        return unknown_labels[idx]

    result = []
    for seg in segments:
        midpoint = (seg.get("start", 0.0) + seg.get("end", 0.0)) / 2
        active_event = None
        for ev in events:
            if ev.timestamp <= midpoint:
                active_event = ev
            else:
                break
        if active_event is None:
            result.append(dict(seg))
        else:
            result.append({**seg, "speaker": _speaker_label(active_event)})
    return result


async def run_transcription(meeting: VexaMeeting, db: AsyncSession) -> None:
    """Fetch transcript segments from Vexa meeting-api; fall back to Whisper audio."""
    from app.services.transcript_filter import filter_segments

    try:
        if meeting.vexa_meeting_id is None:
            raise ValueError("vexa_meeting_id is not set -- cannot look up recording")

        segments_fetched: list[dict] = []
        # Retry fetching segments: Vexa processes audio after the bot leaves,
        # so segments may not be ready immediately when transcription is triggered.
        for seg_attempt in range(6):
            try:
                raw_segments = await vexa.get_transcript_segments(meeting.platform, meeting.native_meeting_id)
                if raw_segments:
                    segments_fetched = filter_segments(raw_segments)
                    break
                if seg_attempt < 5:
                    logger.info(
                        "No transcript segments yet for meeting %s (attempt %d/6), retrying in 15s...",
                        meeting.id,
                        seg_attempt + 1,
                    )
                    await asyncio.sleep(15)
            except Exception as exc:
                logger.warning(
                    "Segment fetch failed for meeting %s (attempt %d/6): %s", meeting.id, seg_attempt + 1, exc
                )
                if seg_attempt < 5:
                    await asyncio.sleep(15)

        if segments_fetched:
            meeting.transcript_segments = segments_fetched
            meeting.transcript_text = "\n".join(
                f"{seg.get('speaker', 'Unknown')}: {seg.get('text', '')}" for seg in segments_fetched
            )
            for seg in segments_fetched:
                if seg.get("language"):
                    meeting.language = seg["language"]
                    break
            meeting.status = "completed"
            meeting.error_message = None
            return

        audio_bytes: bytes | None = None
        audio_fmt: str = "wav"
        for attempt in range(5):
            try:
                audio_bytes, audio_fmt = await vexa.get_recording(meeting.vexa_meeting_id)
                break
            except ValueError:
                if attempt < 4:
                    logger.info(
                        "Recording not ready for vexa meeting %s (attempt %d/5), retrying in 5s...",
                        meeting.vexa_meeting_id,
                        attempt + 1,
                    )
                    await asyncio.sleep(5)
                else:
                    # No recording available (recording_enabled=False or meeting too short).
                    # Complete with empty transcript rather than failing.
                    logger.info(
                        "No recording for vexa meeting %s and no transcript segments — completing with empty transcript",
                        meeting.vexa_meeting_id,
                    )
                    meeting.status = "completed"
                    meeting.error_message = None
                    return
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
        meeting.status = "completed"
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

    if not payload.platform or not payload.native_meeting_id:
        logger.info("Vexa webhook: no platform/native_meeting_id, ignoring")
        return {"status": "ignored"}

    VEXA_STATUS_MAP = {
        "joining": "joining",
        "awaiting_admission": "joining",
        "active": "recording",
        "recording": "recording",
        "failed": "failed",
    }
    portal_status = VEXA_STATUS_MAP.get(payload.status or "")

    meeting = await db.scalar(
        select(VexaMeeting)
        .where(
            VexaMeeting.platform == payload.platform,
            VexaMeeting.native_meeting_id == payload.native_meeting_id,
            VexaMeeting.status.in_((*ACTIVE_STATUSES, "stopping")),
        )
        .order_by(VexaMeeting.created_at.desc())
    )
    if meeting is None:
        logger.warning("Vexa webhook: no matching meeting for %s/%s", payload.platform, payload.native_meeting_id)
        return {"status": "ignored"}

    if payload.status is not None and payload.status != "completed":
        if portal_status and meeting.status != portal_status and meeting.status != "stopping":
            meeting.status = portal_status
            await db.commit()
            logger.info("Vexa webhook: synced status %s->%s for meeting %s", payload.status, portal_status, meeting.id)
        return {"status": "synced"}

    meeting.status = "stopping"
    meeting.ended_at = datetime.now(UTC) if not meeting.ended_at else meeting.ended_at
    if payload.vexa_meeting_id:
        meeting.vexa_meeting_id = payload.vexa_meeting_id
    await db.commit()

    await run_transcription(meeting, db)
    await db.commit()

    if meeting.status == "completed":
        await cleanup_recording(meeting, db, recording_id=payload.recording_id)
        emit_event(
            "meeting.completed",
            org_id=meeting.org_id,
            user_id=meeting.zitadel_user_id,
            properties={"platform": meeting.platform, "duration_seconds": meeting.duration_seconds},
        )

    return {"status": "ok"}
