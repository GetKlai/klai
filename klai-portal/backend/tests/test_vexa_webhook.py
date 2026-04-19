"""Regression tests for VexaWebhookPayload normalisation.

SPEC-VEXA-003 §4.2 — ensure portal-api can parse all three webhook wire formats
emitted by (a) upstream Vexa v0.10 meeting-api, (b) legacy agentic-runtime
meeting-api, and (c) bare flat completion dicts. Without these fixtures the
normaliser can silently regress when refactored.
"""

from __future__ import annotations

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
