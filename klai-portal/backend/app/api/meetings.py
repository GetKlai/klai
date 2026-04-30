"""
Meeting bot API -- start/stop Vexa bots and serve transcripts.
Route prefix: /api/bots
"""

import asyncio
import base64
import binascii
import hmac
import io
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import httpx
import structlog
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials
from pydantic import BaseModel, model_validator
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import _get_caller_org, bearer, require_product
from app.core.config import settings
from app.core.database import get_db
from app.models.groups import PortalGroup
from app.models.meetings import VexaMeeting
from app.services.access import (
    can_write_meeting,
    count_accessible_meetings,
    get_accessible_meetings,
    is_member_of_group,
)
from app.services.audit import log_event
from app.services.events import emit_event
from app.services.recording_cleanup import cleanup_recording
from app.services.vexa import parse_meeting_url, vexa

logger = structlog.get_logger()

router = APIRouter(prefix="/api/bots", tags=["meetings"])

ACTIVE_STATUSES = ("pending", "joining", "recording")
_BILLABLE_STATUSES = (*ACTIVE_STATUSES, "stopping")
MAX_CONCURRENT_BOTS = 2


def _require_webhook_secret(request: Request) -> None:
    # SPEC-SEC-WEBHOOK-001 REQ-2: fail-closed auth on /api/bots/internal/webhook.
    # Authentication is the Authorization-header compare alone — NO IP-range
    # short-circuit. Two accepted forms:
    #
    # 1. `Authorization: Bearer <vexa_webhook_secret>` — canonical form, used by any
    #    caller that can add a custom header (e.g. a proxy shim or a future Vexa
    #    release that wires `webhook_secret` into `fire_post_meeting_hooks`).
    #
    # 2. `Authorization: Basic <base64(user:vexa_webhook_secret)>` — derived
    #    automatically by httpx when the POST URL contains userinfo
    #    (`http://u:secret@portal-api:8010/...`). This is the path used today by
    #    Vexa's `POST_MEETING_HOOKS` — Vexa's `fire_post_meeting_hooks` does not
    #    expose a header-injection hook, but it passes the URL verbatim to
    #    httpx.AsyncClient.post which auto-adds Basic auth from userinfo. The
    #    `user` component is ignored by this guard; the secret lives in the
    #    password half. See SPEC-SEC-WEBHOOK-001 Assumptions ("URL variant that
    #    embeds the secret").
    #
    # The IP-range "trusted Docker networks" shortcut has been permanently removed;
    # every caller MUST present one of the two valid Authorization forms.
    # Startup validator `_require_vexa_webhook_secret` in app.core.config guarantees
    # settings.vexa_webhook_secret is non-empty, so an empty expected value cannot
    # lead to a compare_digest false-positive.
    auth_header = request.headers.get("Authorization", "")
    expected_secret = settings.vexa_webhook_secret.encode("utf-8")

    if auth_header.startswith("Bearer "):
        expected = f"Bearer {settings.vexa_webhook_secret}".encode()
        if hmac.compare_digest(auth_header.encode("utf-8"), expected):
            return
    elif auth_header.startswith("Basic "):
        try:
            decoded = base64.b64decode(auth_header[len("Basic ") :], validate=True).decode("utf-8")
        except (binascii.Error, UnicodeDecodeError):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized") from None
        # Basic auth format is "user:password"; only the password carries the secret.
        _, sep, password = decoded.partition(":")
        if sep and hmac.compare_digest(password.encode("utf-8"), expected_secret):
            return

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
    user_id, org, _caller = await _get_caller_org(credentials, db)

    total = await count_accessible_meetings(user_id, org.id, db)
    page = await get_accessible_meetings(user_id, org.id, db, limit=limit, offset=offset)

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
    user_id, org, _caller = await _get_caller_org(credentials, db)

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

    active_count = await db.scalar(
        select(func.count(VexaMeeting.id)).where(
            VexaMeeting.status.in_(_BILLABLE_STATUSES), VexaMeeting.org_id == org.id
        )
    )
    if (active_count or 0) >= MAX_CONCURRENT_BOTS:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Maximum 2 active meetings at a time. Stop an existing bot to continue.",
        )

    meeting = VexaMeeting(
        zitadel_user_id=user_id,
        org_id=org.id,
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

    await log_event(
        org_id=org.id,
        actor=user_id,
        action="meeting.created",
        resource_type="meeting",
        resource_id=str(meeting.id),
        details={"group_id": body.group_id} if body.group_id else None,
    )

    try:
        bot_resp = await vexa.start_bot(ref.platform, ref.native_meeting_id, meeting_url=ref.meeting_url)
        meeting.bot_id = str(bot_resp.get("bot_container_id") or bot_resp.get("id") or "")
        meeting.vexa_meeting_id = bot_resp.get("id")
        meeting.status = "joining"
        meeting.started_at = datetime.now(UTC)
    except httpx.HTTPStatusError as exc:
        meeting.status = "failed"
        meeting.error_message = f"Bot start failed: {exc.response.status_code}"
        logger.exception("Vexa bot start failed", meeting_id=str(meeting.id), status_code=exc.response.status_code)
    except Exception as exc:
        meeting.status = "failed"
        meeting.error_message = f"Bot start failed: {type(exc).__name__}"
        logger.exception("Vexa bot start failed", meeting_id=str(meeting.id), error=str(exc))

    await db.commit()
    # No post-commit refresh: `SET LOCAL app.current_org_id` is transaction-scoped
    # and gone after commit. A db.refresh() here opens a fresh transaction without
    # tenant context and trips the category-D RLS guard on vexa_meetings. All
    # fields (bot_id, status, started_at, error_message) are set above and persist
    # after commit thanks to AsyncSessionLocal(expire_on_commit=False).
    emit_event("meeting.started", org_id=org.id, user_id=user_id, properties={"platform": ref.platform})
    return await _build_meeting_response(meeting, db)


@router.get("/meetings/{meeting_id}", response_model=MeetingResponse, dependencies=[Depends(require_product("scribe"))])
async def get_meeting(
    meeting_id: UUID,
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
    db: AsyncSession = Depends(get_db),
) -> MeetingResponse:
    user_id, org, _caller = await _get_caller_org(credentials, db)

    meeting = await db.scalar(select(VexaMeeting).where(VexaMeeting.id == meeting_id))
    if meeting is None or meeting.org_id != org.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Meeting not found")

    # Check read access: owner, group member, or same org
    accessible = await get_accessible_meetings(user_id, org.id, db)
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
    user_id, org, _caller = await _get_caller_org(credentials, db)

    meeting = await db.scalar(select(VexaMeeting).where(VexaMeeting.id == meeting_id, VexaMeeting.org_id == org.id))
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
        except Exception as exc:
            logger.warning(
                "Vexa stop_bot failed, continuing", meeting_id=str(meeting.id), error=str(exc), exc_info=True
            )

    meeting.status = "stopping"
    meeting.ended_at = datetime.now(UTC)
    await db.commit()
    # See start_meeting: no post-commit refresh — RLS tenant context is gone
    # after commit, and expire_on_commit=False keeps the mutated fields intact.
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
    user_id, org, _caller = await _get_caller_org(credentials, db)

    meeting = await db.scalar(select(VexaMeeting).where(VexaMeeting.id == meeting_id, VexaMeeting.org_id == org.id))
    if meeting is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Meeting not found")
    if not await can_write_meeting(user_id, meeting, db):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="No write access to this meeting")

    ref = parse_meeting_url(meeting.meeting_url)
    if ref and meeting.status in ACTIVE_STATUSES:
        try:
            await vexa.stop_bot(ref.platform, ref.native_meeting_id)
        except Exception as exc:
            logger.warning(
                "Vexa stop_bot failed during delete, continuing",
                meeting_id=str(meeting.id),
                error=str(exc),
                exc_info=True,
            )

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

    user_id, org, _caller = await _get_caller_org(credentials, db)

    meeting = await db.scalar(select(VexaMeeting).where(VexaMeeting.id == meeting_id, VexaMeeting.org_id == org.id))
    if meeting is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Meeting not found")

    # Read access sufficient for summarize
    accessible = await get_accessible_meetings(user_id, org.id, db)
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
        logger.exception("Summarization failed", meeting_id=str(meeting_id))
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

    user_id, org, _caller = await _get_caller_org(credentials, db)

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
        org_id=org.zitadel_org_id,
        kb_slug=body.kb_slug,
        meeting=meeting,
    )
    return IngestMeetingResponse(artifact_id=artifact_id, status="ok")


# -- Webhook from Vexa -------------------------------------------------------


class SpeakerEvent(BaseModel):
    timestamp: float
    participant_name: str | None = None


class VexaWebhookPayload(BaseModel):
    """Vexa webhook envelope — accepts three wire formats.

    1. Upstream v0.10 envelope (``WEBHOOK_API_VERSION = "2026-03-01"``):
       ``{event_id, event_type, api_version, created_at, data: {meeting: {...}}}``
    2. Legacy agentic-runtime envelope:
       ``{event_type, meeting: {id, platform, native_meeting_id, status, ...}}``
    3. Flat completion format (bare meeting dict):
       ``{id, platform, native_meeting_id, status, ended_at, speaker_events}``

    See SPEC-VEXA-003 research.md §3.5 for the full upstream schema.
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

        # Shape 1: upstream v0.10 envelope — meeting nested under `data.meeting`.
        inner = data.get("data")
        if isinstance(inner, dict) and isinstance(inner.get("meeting"), dict):
            meeting = inner["meeting"]
            recording = inner.get("recording")
            return {
                "vexa_meeting_id": meeting.get("id"),
                "platform": meeting.get("platform"),
                "native_meeting_id": meeting.get("native_meeting_id"),
                "status": meeting.get("status"),
                "ended_at": meeting.get("end_time"),
                "recording_id": recording.get("id") if isinstance(recording, dict) else None,
            }

        # Shape 2: legacy envelope — `meeting` at top level.
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

        # Shape 3: flat completion format.
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
                        "No transcript segments yet, retrying",
                        meeting_id=str(meeting.id),
                        attempt=seg_attempt + 1,
                        max_attempts=6,
                    )
                    await asyncio.sleep(15)
            except Exception as exc:
                logger.warning(
                    "Segment fetch failed",
                    meeting_id=str(meeting.id),
                    attempt=seg_attempt + 1,
                    max_attempts=6,
                    error=str(exc),
                    exc_info=True,
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
            meeting.status = "done"
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
                        "Recording not ready, retrying",
                        vexa_meeting_id=meeting.vexa_meeting_id,
                        attempt=attempt + 1,
                        max_attempts=5,
                    )
                    await asyncio.sleep(5)
                else:
                    # No recording available (recording_enabled=False or meeting too short).
                    # Complete with empty transcript rather than failing.
                    logger.info(
                        "No recording and no segments, completing with empty transcript",
                        vexa_meeting_id=meeting.vexa_meeting_id,
                    )
                    meeting.status = "done"
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
        meeting.status = "done"
        meeting.error_message = None

    except Exception as exc:
        logger.exception("Transcription failed", meeting_id=str(meeting.id))
        meeting.status = "failed"
        meeting.error_message = str(exc)


@router.post("/internal/webhook", status_code=status.HTTP_200_OK)
async def vexa_webhook(
    payload: VexaWebhookPayload,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> dict:
    _require_webhook_secret(request)

    # SPEC-VEXA-003: upstream `fire_post_meeting_hooks` omits native_meeting_id from the
    # payload (only `meeting.id` + `meeting.platform`). Fall back to vexa_meeting_id
    # lookup when the envelope lacks the natural key.
    if not payload.vexa_meeting_id and (not payload.platform or not payload.native_meeting_id):
        logger.info(
            "Vexa webhook: no correlation key (vexa_meeting_id / platform+native_meeting_id), ignoring",
            payload_status=payload.status,
        )
        return {"status": "ignored"}

    VEXA_STATUS_MAP = {
        "joining": "joining",
        "awaiting_admission": "joining",
        "active": "recording",
        "recording": "recording",
        "failed": "failed",
    }
    portal_status = VEXA_STATUS_MAP.get(payload.status or "")

    # The webhook has no tenant context (Vexa only knows its own meeting
    # id, not our org_id), so the correlation SELECT must cross tenants.
    # Use an explicit cross_org_session; the subsequent UPDATE is then
    # scoped to the meeting's own org via tenant_scoped_session.
    from app.core.database import cross_org_session, tenant_scoped_session

    async with cross_org_session() as lookup_db:
        # Prefer vexa_meeting_id (unambiguous FK); fall back to
        # (platform, native_meeting_id) pair which can correlate even
        # during the "pending" phase before a bot is spawned.
        if payload.vexa_meeting_id is not None:
            meeting = await lookup_db.scalar(
                select(VexaMeeting)
                .where(VexaMeeting.vexa_meeting_id == payload.vexa_meeting_id)
                .order_by(VexaMeeting.created_at.desc())
            )
        else:
            meeting = await lookup_db.scalar(
                select(VexaMeeting)
                .where(
                    VexaMeeting.platform == payload.platform,
                    VexaMeeting.native_meeting_id == payload.native_meeting_id,
                    VexaMeeting.status.in_((*ACTIVE_STATUSES, "stopping")),
                )
                .order_by(VexaMeeting.created_at.desc())
            )
        # Detach so we can use the instance on a different (tenant-scoped) session below.
        if meeting is not None:
            lookup_db.expunge(meeting)
    if meeting is None:
        logger.warning(
            "Vexa webhook: no matching meeting",
            vexa_meeting_id=payload.vexa_meeting_id,
            platform=payload.platform,
            native_meeting_id=payload.native_meeting_id,
        )
        return {"status": "ignored"}

    # From here on we mutate the meeting — switch to a session scoped to
    # the meeting's own tenant so vexa_meetings' tenant_update RLS policy
    # accepts the write. The request-level `db` (from get_db, no tenant
    # set) cannot be reused for UPDATEs on RLS-strict tables.
    if meeting.org_id is None:
        logger.warning("vexa_webhook_skipped_missing_org_id", meeting_id=str(meeting.id))
        return {"status": "ignored"}

    async with tenant_scoped_session(meeting.org_id) as scoped_db:
        # Re-attach the detached ORM instance to the new session.
        meeting = await scoped_db.merge(meeting)

        if payload.status is not None and payload.status != "completed":
            if portal_status and meeting.status != portal_status and meeting.status != "stopping":
                meeting.status = portal_status
                await scoped_db.commit()
                logger.info(
                    "Vexa webhook: synced status",
                    vexa_status=payload.status,
                    portal_status=portal_status,
                    meeting_id=str(meeting.id),
                )
            return {"status": "synced"}

        meeting.status = "stopping"
        meeting.ended_at = datetime.now(UTC) if not meeting.ended_at else meeting.ended_at
        if payload.vexa_meeting_id:
            meeting.vexa_meeting_id = payload.vexa_meeting_id
        await scoped_db.commit()

        await run_transcription(meeting, scoped_db)
        await scoped_db.commit()

        if meeting.status == "completed":
            await cleanup_recording(meeting, scoped_db, recording_id=payload.recording_id)
            emit_event(
                "meeting.completed",
                org_id=meeting.org_id,
                user_id=meeting.zitadel_user_id,
                properties={"platform": meeting.platform, "duration_seconds": meeting.duration_seconds},
            )

    return {"status": "ok"}
