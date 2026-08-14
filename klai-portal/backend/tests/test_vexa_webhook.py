"""Regression tests for VexaWebhookPayload normalisation.

SPEC-VEXA-003 §4.2 — ensure portal-api can parse all three webhook wire formats
emitted by (a) upstream Vexa v0.10 meeting-api, (b) legacy agentic-runtime
meeting-api, and (c) bare flat completion dicts. Without these fixtures the
normaliser can silently regress when refactored.
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.api.meetings import VexaWebhookPayload


class TestUpstreamV10Envelope:
    """Shape 1: upstream v0.10 — meeting nested under `data.meeting`."""

    @pytest.fixture
    def envelope(self) -> dict:
        # Taken verbatim from SPEC-VEXA-003 research.md §3.5 (WEBHOOK_API_VERSION = 2026-03-01).
        return {
            "event_id": "evt_abc",
            "event_type": "meeting.completed",
            "api_version": "2026-03-01",
            "created_at": "2026-04-19T10:00:00+00:00",
            "data": {
                "meeting": {
                    "id": 1,
                    "user_id": 7,
                    "user_email": "alice@example.com",
                    "platform": "google_meet",
                    "status": "completed",
                    "duration_seconds": 482.5,
                    "start_time": "2026-04-19T09:50:00+00:00",
                    "end_time": "2026-04-19T10:05:00+00:00",
                    "created_at": "2026-04-19T09:45:00+00:00",
                    "transcription_enabled": True,
                    "native_meeting_id": "abc-def-ghi",
                }
            },
        }

    def test_parses_meeting_identity(self, envelope: dict) -> None:
        model = VexaWebhookPayload.model_validate(envelope)
        assert model.platform == "google_meet"
        assert model.native_meeting_id == "abc-def-ghi"
        assert model.status == "completed"
        assert model.vexa_meeting_id == 1
        assert model.ended_at == "2026-04-19T10:05:00+00:00"

    def test_ignores_unknown_outer_fields(self, envelope: dict) -> None:
        # extra=ignore — the outer event_id/event_type/api_version/created_at
        # must not fail validation when Vexa adds more envelope metadata.
        envelope["novel_top_level_field"] = "ignored"
        envelope["data"]["meeting"]["novel_meeting_field"] = "ignored"
        model = VexaWebhookPayload.model_validate(envelope)
        assert model.native_meeting_id == "abc-def-ghi"

    def test_recording_id_extracted_when_present(self) -> None:
        payload = {
            "event_id": "evt_rec",
            "event_type": "recording.ready",
            "data": {
                "meeting": {
                    "id": 2,
                    "platform": "google_meet",
                    "native_meeting_id": "xyz",
                    "status": "recorded",
                    "end_time": None,
                },
                "recording": {"id": 99, "duration_ms": 400000},
            },
        }
        model = VexaWebhookPayload.model_validate(payload)
        assert model.vexa_meeting_id == 2
        assert model.recording_id == 99


class TestLegacyAgenticRuntimeEnvelope:
    """Shape 2: legacy agentic-runtime — `meeting` at top level.

    Kept as a regression guard so the old deploy-generated traffic keeps parsing
    during rollout overlap (see SPEC-VEXA-003 plan.md Phase 6.X cutover).
    """

    def test_legacy_envelope_still_parses(self) -> None:
        payload = {
            "event_type": "meeting.completed",
            "meeting": {
                "id": 42,
                "platform": "teams",
                "native_meeting_id": "legacy-id",
                "status": "completed",
                "end_time": "2026-03-01T12:00:00+00:00",
            },
            "recording": {"id": 7},
        }
        model = VexaWebhookPayload.model_validate(payload)
        assert model.platform == "teams"
        assert model.native_meeting_id == "legacy-id"
        assert model.vexa_meeting_id == 42
        assert model.recording_id == 7


class TestFlatCompletionShape:
    """Shape 3: flat dict with meeting fields at top level (no envelope at all)."""

    def test_flat_payload_parses(self) -> None:
        payload = {
            "id": 77,
            "platform": "google_meet",
            "native_meeting_id": "flat-id",
            "status": "completed",
            "ended_at": "2026-04-19T11:00:00+00:00",
            "speaker_events": [
                {"timestamp": 1.0, "participant_name": "Alice"},
                {"timestamp": 12.3, "participant_name": None},
            ],
        }
        model = VexaWebhookPayload.model_validate(payload)
        assert model.vexa_meeting_id == 77
        assert model.platform == "google_meet"
        assert len(model.speaker_events) == 2
        assert model.speaker_events[0].participant_name == "Alice"
        assert model.speaker_events[1].participant_name is None


class TestUpstreamFirePostMeetingHooksShape:
    """Shape 1b: `fire_post_meeting_hooks` in upstream meeting-api omits `native_meeting_id`.

    Observed live on 2026-04-19 during SPEC-VEXA-003 real-meet E2E test (meeting 6):
    the internal POST_MEETING_HOOKS delivery arrives with `meeting.platform` and
    `meeting.id` but **without** `meeting.native_meeting_id`. Portal-api's handler
    falls back to vexa_meeting_id correlation for this case.
    """

    def test_payload_without_native_meeting_id_extracts_vexa_meeting_id(self) -> None:
        # Exact shape meeting-api's fire_post_meeting_hooks produces (see
        # services/meeting-api/meeting_api/post_meeting.py `build_envelope`).
        payload = {
            "event_id": "evt_live",
            "event_type": "meeting.completed",
            "api_version": "2026-03-01",
            "created_at": "2026-04-19T15:04:13+00:00",
            "data": {
                "meeting": {
                    "id": 6,
                    "user_id": 1,
                    "user_email": "klai-system@klai.internal",
                    "platform": "google_meet",
                    "status": "completed",
                    "duration_seconds": 137.96,
                    "start_time": "2026-04-19T15:01:55+00:00",
                    "end_time": "2026-04-19T15:04:12+00:00",
                    "created_at": "2026-04-19T15:01:50+00:00",
                    "transcription_enabled": True,
                    # NOTE: no native_meeting_id field
                },
            },
        }
        model = VexaWebhookPayload.model_validate(payload)
        assert model.vexa_meeting_id == 6
        assert model.platform == "google_meet"
        assert model.native_meeting_id is None  # known upstream gap
        assert model.status == "completed"
        assert model.ended_at == "2026-04-19T15:04:12+00:00"


@pytest.mark.asyncio
async def test_webhook_rearms_tenant_context_after_stopping_commit(monkeypatch) -> None:
    """The interim stopping commit can release the RLS GUC; re-set it before transcription."""
    from app.api import meetings as meetings_module
    from app.core import database as database_module

    events: list[str] = []
    meeting = MagicMock()
    meeting.id = uuid.uuid4()
    meeting.org_id = 42
    meeting.status = "recording"
    meeting.ended_at = None

    lookup_db = MagicMock()
    lookup_db.scalar = AsyncMock(return_value=meeting)
    lookup_db.expunge = MagicMock()

    scoped_db = MagicMock()
    scoped_db.merge = AsyncMock(return_value=meeting)
    scoped_db.commit = AsyncMock(side_effect=lambda: events.append("commit"))

    @asynccontextmanager
    async def _cross_org_session():
        yield lookup_db

    @asynccontextmanager
    async def _tenant_session(_org_id):
        yield scoped_db

    async def _set_tenant(_db, _org_id):
        events.append("set_tenant")

    async def _run_transcription(_meeting, _db):
        events.append("run_transcription")
        _meeting.status = "done"

    monkeypatch.setattr(meetings_module, "_require_webhook_secret", lambda _request: None)
    monkeypatch.setattr(database_module, "cross_org_session", _cross_org_session)
    monkeypatch.setattr(database_module, "tenant_scoped_session", _tenant_session)
    monkeypatch.setattr(database_module, "set_tenant", _set_tenant)
    cleanup_recording = AsyncMock()
    emit_event = MagicMock()
    monkeypatch.setattr(meetings_module, "run_transcription", _run_transcription)
    monkeypatch.setattr(meetings_module, "cleanup_recording", cleanup_recording)
    monkeypatch.setattr(meetings_module, "emit_event", emit_event)

    payload = VexaWebhookPayload(
        platform="google_meet",
        native_meeting_id="abc-def-ghi",
        status="completed",
    )

    result = await meetings_module.vexa_webhook(payload, request=MagicMock(), db=AsyncMock())

    assert result == {"status": "ok"}
    assert events == ["commit", "set_tenant", "run_transcription", "commit"]
    cleanup_recording.assert_awaited_once_with(meeting, scoped_db, recording_id=None)
    emit_event.assert_called_once()


class TestWebhookIdempotency:
    """webhook.v1 is at-least-once; event_id is the receiver's idempotency key.

    Upstream retries a failed delivery on a 60/300/1800/7200s schedule and replays
    the queue after a restart, always with the SAME event_id. Without a dedupe
    gate a redelivered meeting.completed rewinds the meeting to `stopping` and
    re-runs transcription and cleanup.
    """

    def test_payload_carries_event_id_from_the_typed_envelope(self) -> None:
        envelope = {
            "event_id": "evt_5f3c1a9b8d2e4f6a7b0c1d2e3f4a5b6c",
            "event_type": "meeting.completed",
            "api_version": "2026-03-01",
            "created_at": "2026-06-18T10:42:00.000Z",
            "data": {
                "meeting": {
                    "id": 11367,
                    "platform": "google_meet",
                    "native_meeting_id": "abc-defg-hij",
                    "status": "completed",
                    "end_time": "2026-06-18T10:42:00.000Z",
                }
            },
        }
        parsed = VexaWebhookPayload.model_validate(envelope)
        assert parsed.event_id == envelope["event_id"]
        assert parsed.event_type == "meeting.completed"

    def test_event_id_is_stable_across_redeliveries(self) -> None:
        """created_at and the signature change per delivery; event_id must not.

        Keying on the body would treat every retry as a new event — the exact
        mistake upstream's contract calls out ("Do NOT key on the body").
        """
        base = {
            "event_id": "evt_stable",
            "event_type": "meeting.completed",
            "data": {"meeting": {"id": 7, "platform": "google_meet", "native_meeting_id": "x-y-z"}},
        }
        first = VexaWebhookPayload.model_validate({**base, "created_at": "2026-06-18T10:42:00Z"})
        retry = VexaWebhookPayload.model_validate({**base, "created_at": "2026-06-18T10:43:00Z"})
        assert first.event_id == retry.event_id

    def test_legacy_shapes_have_no_event_id_and_stay_processable(self) -> None:
        """The flat/legacy envelopes predate the contract — they must not be dropped."""
        flat = VexaWebhookPayload.model_validate(
            {"id": 5, "platform": "google_meet", "native_meeting_id": "a-b-c", "status": "completed"}
        )
        assert flat.event_id is None
        assert flat.vexa_meeting_id == 5
