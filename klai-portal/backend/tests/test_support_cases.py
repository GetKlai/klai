"""Unit tests for the support-case store's DB-independent behavior.

The upsert/serialization/RLS/concurrency contract is proven against real
PostgreSQL in ``test_support_cases_postgres.py``; these cover the pure logic:
payload hashing, transcript normalization, content-gap finding shape, the
telemetry gate, size limits, the ``_analyze_case`` / ``_apply_findings`` seams
and the case-detail KB firewall.
"""

from __future__ import annotations

import asyncio
import sys
import types
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models.support_cases import PortalSupportCase
from app.schemas_support_cases import MessageKind, SupportCaseMessage, SupportCasePayload
from app.services.support_cases import (
    MAX_CASE_MESSAGES,
    MAX_CASE_TEXT_CHARS,
    OversizedCaseError,
    SupportTelemetryError,
    TranscriptError,
    _analyze_case,
    _apply_findings,
    _classify_findings,
    _finding_gap,
    _finding_occurred_at,
    _question_key,
    normalize_whisper_transcript,
    require_full_telemetry,
    upsert_support_case,
)

_HASH = "a" * 64


def _payload(**over: Any) -> SupportCasePayload:
    base: dict[str, Any] = {
        "source": "hubspot",
        "account_id": "123",
        "external_id": "t-1",
        "subject": "Login broken",
        "messages": [SupportCaseMessage(id="m1", kind="message", role="customer", text="I cannot log in")],
    }
    base.update(over)
    p = SupportCasePayload(**base)
    p.bind_kb("kb-a")
    return p


@pytest.fixture
def stub_analyzer() -> Iterator[types.ModuleType]:
    """Inject a stub ``app.services.support_case_analysis`` at the store's import
    seam so tests configure the analyzer without the (separate-lane) module
    existing yet."""
    module = types.ModuleType("app.services.support_case_analysis")
    module.ANALYSIS_VERSION = "vtest"  # type: ignore[attr-defined]
    module.analyze_support_case = AsyncMock(return_value=[])  # type: ignore[attr-defined]
    sys.modules["app.services.support_case_analysis"] = module
    try:
        yield module
    finally:
        sys.modules.pop("app.services.support_case_analysis", None)


# --------------------------------------------------------------------------- #
# Content hash — change detection
# --------------------------------------------------------------------------- #


def test_content_hash_stable_across_provenance_only_changes() -> None:
    """A source-timestamp / url / metadata bump is not a content change: the
    hash must not move, so an unchanged case is never needlessly reanalysed."""
    a = _payload(source_updated_at="2026-01-01T00:00:00Z", source_url="https://x/1", metadata={"stage": "open"})
    b = _payload(source_updated_at="2026-09-09T00:00:00Z", source_url="https://x/2", metadata={"stage": "closed"})
    assert a.content_hash() == b.content_hash()


def test_content_hash_moves_when_a_message_changes() -> None:
    a = _payload()
    b = _payload(messages=[SupportCaseMessage(id="m1", kind="message", role="customer", text="different text")])
    assert a.content_hash() != b.content_hash()


def test_message_additive_fields_roundtrip() -> None:
    m = SupportCaseMessage(
        id="m1",
        kind="message",
        role="customer",
        text="hi",
        medium="chat",
        channel_id="1000",
        thread_id="t-9",
        reply_to_id="m0",
        speaker_id="spk-1",
    )
    assert (m.medium, m.channel_id, m.thread_id, m.reply_to_id, m.speaker_id) == (
        "chat",
        "1000",
        "t-9",
        "m0",
        "spk-1",
    )


def test_content_hash_default_medium_fields_equal_to_omitted() -> None:
    legacy = _payload(messages=[SupportCaseMessage(id="m1", kind="message", role="customer", text="x")])
    explicit = _payload(
        messages=[
            SupportCaseMessage(
                id="m1",
                kind="message",
                role="customer",
                text="x",
                medium="unknown",
                channel_id=None,
                thread_id=None,
                reply_to_id=None,
                speaker_id=None,
            )
        ]
    )
    assert legacy.content_hash() == explicit.content_hash()


def test_content_hash_moves_when_structure_is_corrected() -> None:
    flat = _payload(messages=[SupportCaseMessage(id="m1", kind="message", role="customer", text="x")])
    corrections = {"medium": "chat", "channel_id": "1000", "thread_id": "t-9", "reply_to_id": "m0", "speaker_id": "s1"}
    hashes = {flat.content_hash()}
    for field, value in corrections.items():
        corrected = flat.model_copy(deep=True)
        setattr(corrected.messages[0], field, value)
        hashes.add(corrected.content_hash())
    assert len(hashes) == len(corrections) + 1


@pytest.mark.parametrize(
    ("kind", "expected"),
    [("transcript", "call"), ("email", "email"), ("message", "unknown"), ("note", "unknown"), ("ticket", "unknown")],
)
def test_unknown_medium_inferred_from_legacy_kind(kind: MessageKind, expected: str) -> None:
    """Legacy payloads carry no medium; derive it from the kind so old and new
    evidence for the same exchange judge the same. transcript => call,
    email => email, every other kind stays unknown."""
    m = SupportCaseMessage(id="m1", kind=kind, role="unknown", text="x")
    assert m.medium == expected


def test_explicit_medium_is_never_overwritten_by_kind_inference() -> None:
    m = SupportCaseMessage(id="m1", kind="email", role="unknown", text="x", medium="chat")
    assert m.medium == "chat"


# --------------------------------------------------------------------------- #
# Telemetry gate + size limits
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("level", ["off", "shadow"])
def test_require_full_telemetry_rejects_non_full(level: str) -> None:
    with pytest.raises(SupportTelemetryError):
        require_full_telemetry(level)


@pytest.mark.asyncio
@pytest.mark.parametrize("level", ["off", "shadow"])
async def test_upsert_rejects_before_any_db_work_when_not_full(level: str) -> None:
    """Non-full telemetry is rejected before persistence or model calls — the
    session is never touched."""
    db = AsyncMock()
    with pytest.raises(SupportTelemetryError):
        await upsert_support_case(
            db,
            org_id=1,
            zitadel_org_id="z1",
            telemetry_level=level,
            connector_id="c1",
            created_by="u1",
            kb_slug="kb-a",
            payload=_payload(),
        )
    db.execute.assert_not_called()


@pytest.mark.asyncio
async def test_upsert_rejects_oversized_case_by_message_count() -> None:
    db = AsyncMock()
    many = [
        SupportCaseMessage(id=f"m{i}", kind="message", role="customer", text="x") for i in range(MAX_CASE_MESSAGES + 1)
    ]
    with pytest.raises(OversizedCaseError):
        await upsert_support_case(
            db,
            org_id=1,
            zitadel_org_id="z1",
            telemetry_level="full",
            connector_id="c1",
            created_by="u1",
            kb_slug="kb-a",
            payload=_payload(messages=many),
        )
    db.execute.assert_not_called()


@pytest.mark.asyncio
async def test_upsert_rejects_oversized_case_by_text_chars() -> None:
    db = AsyncMock()
    big = SupportCaseMessage(id="m1", kind="message", role="customer", text="x" * (MAX_CASE_TEXT_CHARS + 1))
    with pytest.raises(OversizedCaseError):
        await upsert_support_case(
            db,
            org_id=1,
            zitadel_org_id="z1",
            telemetry_level="full",
            connector_id="c1",
            created_by="u1",
            kb_slug="kb-a",
            payload=_payload(messages=[big]),
        )
    db.execute.assert_not_called()


# --------------------------------------------------------------------------- #
# Content-gap finding shape (gap_type='content', retrieval signal preserved)
# --------------------------------------------------------------------------- #


def test_finding_gap_is_always_content_type_with_signal_in_evidence() -> None:
    """A healthy retrieval score with an 'incomplete' diagnosis still imports as
    a content gap; the analyzer's nullable hard/soft signal is kept in
    evidence, never as gap_type."""
    gap = _finding_gap(
        org_id=7,
        user_id="u1",
        kb_slug="kb-a",
        case_id=99,
        occurred_at=datetime(2026, 9, 1, tzinfo=UTC),
        finding={
            "question": "How do I reset 2FA?",
            "language": "en",
            "diagnosis": "incomplete",
            "gap_type": None,  # retrieval scored fine
            "top_score": 0.91,
            "audience": "customer",
            "message_ids": ["m1"],
            "articles": [{"chunk_id": "c1"}],
            "missing_information": "the fallback path",
            "rationale": "article omits the fallback",
        },
    )
    assert gap.gap_type == "content"
    assert gap.diagnosis == "incomplete"
    assert gap.support_case_id == 99
    assert gap.top_score == 0.91
    assert gap.evidence["retrieval_signal"] is None
    assert gap.evidence["message_ids"] == ["m1"]


@pytest.mark.asyncio
async def test_classify_findings_stamps_only_actionable_findings() -> None:
    findings = [
        {"question": "Reset 2FA?", "diagnosis": "missing"},
        {"question": "All good", "diagnosis": "covered"},  # not an inbox diagnosis
    ]
    classify = AsyncMock(return_value=[5, 7])
    with patch("app.services.knowledge_ingest_client.classify_gap_taxonomy", classify):
        out = await _classify_findings(zitadel_org_id="z1", kb_slug="kb-a", findings=findings)

    classify.assert_awaited_once_with("z1", "kb-a", "Reset 2FA?")
    assert out[0]["taxonomy_node_ids"] == [5, 7]
    assert "taxonomy_node_ids" not in out[1]


@pytest.mark.asyncio
async def test_classify_findings_retries_failed_finding_without_marking_successful_empty() -> None:
    findings = [
        {"question": "Transient failure", "diagnosis": "missing", "comparison_limitations": []},
        {"question": "No taxonomy match", "diagnosis": "missing", "comparison_limitations": []},
    ]
    classify = AsyncMock(side_effect=[None, [], [5]])
    with patch("app.services.knowledge_ingest_client.classify_gap_taxonomy", classify):
        out = await _classify_findings(zitadel_org_id="z1", kb_slug="kb-a", findings=findings)

    assert out[0]["taxonomy_node_ids"] == [5]
    assert out[0]["comparison_limitations"] == []
    assert "taxonomy_node_ids" not in out[1]
    assert out[1]["comparison_limitations"] == []


@pytest.mark.asyncio
async def test_classify_findings_marks_exhausted_taxonomy_failure_on_finding() -> None:
    findings = [{"question": "Unavailable", "diagnosis": "missing", "comparison_limitations": []}]
    classify = AsyncMock(side_effect=[None, None])
    with patch("app.services.knowledge_ingest_client.classify_gap_taxonomy", classify):
        out = await _classify_findings(zitadel_org_id="z1", kb_slug="kb-a", findings=findings)

    assert "taxonomy_node_ids" not in out[0]
    assert "taxonomy classification was unavailable" in " ".join(out[0]["comparison_limitations"]).lower()


@pytest.mark.asyncio
async def test_taxonomy_outage_returns_findings_with_partial_labels_within_retry_budget(monkeypatch) -> None:
    findings = [{"question": name, "diagnosis": "missing"} for name in ("available", "recovered", "stalled", "queued")]
    attempts = dict.fromkeys((f["question"] for f in findings), 0)

    async def classify(_org, _kb, question):
        assert (_org, _kb) == ("z1", "kb-a")
        attempts[question] += 1
        if question == "available":
            return [3]
        if attempts[question] == 1:
            return None
        if question == "recovered":
            return [5]
        await asyncio.Event().wait()

    real_timeout = asyncio.timeout
    monkeypatch.setattr(asyncio, "timeout", lambda seconds: real_timeout(None if seconds is None else seconds / 1000))
    with patch("app.services.knowledge_ingest_client.classify_gap_taxonomy", classify):
        result = await asyncio.wait_for(
            _classify_findings(zitadel_org_id="z1", kb_slug="kb-a", findings=findings), timeout=0.2
        )

    assert result == findings
    assert result[0]["taxonomy_node_ids"] == [3]
    assert result[1]["taxonomy_node_ids"] == [5]
    assert all("taxonomy_node_ids" not in finding and finding["comparison_limitations"] for finding in result[2:])
    assert attempts == {"available": 1, "recovered": 2, "stalled": 2, "queued": 1}


def test_question_key_keeps_kb_audience_diagnosis_distinct() -> None:
    """Normalized grouping may never merge different KB, audience or diagnosis."""
    base = dict(question="How do I reset 2FA?", diagnosis="missing", language="en", kb_slug="kb-a", audience="customer")
    same = _question_key(**{**base, "question": "  how   do i   RESET 2fa? "})  # whitespace/case only
    assert _question_key(**base) == same
    assert _question_key(**base) != _question_key(**{**base, "kb_slug": "kb-b"})
    assert _question_key(**base) != _question_key(**{**base, "audience": "internal"})
    assert _question_key(**base) != _question_key(**{**base, "diagnosis": "incomplete"})


# --------------------------------------------------------------------------- #
# _run_analysis seam — only inbox diagnoses, dedupe, failure handling
# --------------------------------------------------------------------------- #


def _analysis_db() -> MagicMock:
    db = MagicMock()
    db.add = MagicMock()
    return db


def _case() -> PortalSupportCase:
    return PortalSupportCase(id=1, org_id=7, payload={"messages": []}, status="pending")


def test_apply_findings_only_inbox_diagnoses() -> None:
    findings = [
        {"question": "Q1", "diagnosis": "missing", "gap_type": "hard"},
        {"question": "Q2", "diagnosis": "covered", "gap_type": None},  # not an inbox diagnosis
        {"question": "Q3", "diagnosis": "audience", "gap_type": "soft"},
    ]
    db = _analysis_db()
    case = _case()
    count = _apply_findings(db, case=case, findings=findings, created_by="u1", kb_slug="kb-a")

    assert count == 2  # missing + audience; covered excluded
    assert db.add.call_count == 2
    # The full analysis (including 'covered') is kept on the case so an empty
    # gap list is distinguishable from failed analysis.
    assert case.analysis is not None and len(case.analysis) == 3


def test_apply_findings_dedupes_but_keeps_audience_and_language_distinct() -> None:
    """#8: dedupe on the full grouping key — a byte-identical duplicate collapses,
    but the same question for a different audience or language stays a finding."""
    findings = [
        {"question": "Reset password", "diagnosis": "missing", "language": "en", "audience": "customer"},
        {"question": " reset  PASSWORD ", "diagnosis": "missing", "language": "en", "audience": "customer"},  # dup
        {"question": "Reset password", "diagnosis": "missing", "language": "en", "audience": "internal"},  # kept
        {"question": "Reset password", "diagnosis": "missing", "language": "nl", "audience": "customer"},  # kept
    ]
    db = _analysis_db()
    case = _case()
    count = _apply_findings(db, case=case, findings=findings, created_by="u1", kb_slug="kb-a")
    assert count == 3


@pytest.mark.asyncio
async def test_analyze_case_failure_is_reported_without_raw_text(stub_analyzer) -> None:
    """#5: analyzer failure returns failed=True with no findings, and never logs
    the exception text/body (which can carry customer evidence)."""
    from traceback import format_exception

    secret = "CUSTOMER_SECRET_IN_PARSE_ERROR_9f3c"
    stub_analyzer.analyze_support_case = AsyncMock(side_effect=ValueError(secret))

    with patch("app.services.support_cases.logger") as logger:
        findings, version, failed = await _analyze_case(
            body={"messages": []}, kb_slug="kb-a", zitadel_org_id="z1", user_id="u1", case_id=1
        )

    assert failed is True
    assert findings == []
    assert version is None
    diagnostic = "".join(format_exception(*logger.warning.call_args.kwargs["exc_info"]))
    assert "_analyze_case" in diagnostic
    assert secret not in diagnostic


@pytest.mark.asyncio
async def test_analyze_case_success_returns_version(stub_analyzer) -> None:
    stub_analyzer.analyze_support_case = AsyncMock(return_value=[{"question": "Q", "diagnosis": "missing"}])
    findings, version, failed = await _analyze_case(
        body={"messages": []}, kb_slug="kb-a", zitadel_org_id="z1", user_id="u1", case_id=1
    )
    assert failed is False
    assert version == "vtest"
    assert len(findings) == 1


# --------------------------------------------------------------------------- #
# Transcript normalization
# --------------------------------------------------------------------------- #


def test_transcript_identity_is_hash_not_filename() -> None:
    t1 = normalize_whisper_transcript(
        {"_source": {"sha256": _HASH, "filename": "call-a.mp3"}, "segments": [{"start": 0, "end": 1, "text": "hi"}]}
    )
    t2 = normalize_whisper_transcript(
        {"_source": {"sha256": _HASH, "filename": "renamed.mp3"}, "segments": [{"start": 0, "end": 1, "text": "hi"}]}
    )
    # Same recording, different filename -> same identity -> one case.
    assert t1.external_id == t2.external_id == _HASH
    assert t1.account_id == "audio"
    assert all(m.role == "unknown" and m.occurred_at is None for m in t1.messages)
    # #B: the filename is the audio subject, so a rename must NOT move the
    # content hash — otherwise a duplicate copy reruns analysis and reopens
    # manually-closed findings.
    assert t1.content_hash() == t2.content_hash()


def test_transcript_messages_are_call_medium_threaded_by_recording() -> None:
    """A Whisper replay is the call medium; the recording hash groups its own
    segments as one exchange, and a supplied diarization label is preserved
    verbatim without ever becoming a business role."""
    t = normalize_whisper_transcript(
        {"_source": {"sha256": _HASH}, "segments": [{"start": 0, "end": 1, "text": "hi", "speaker": "SPEAKER_00"}]}
    )
    m = t.messages[0]
    assert m.medium == "call"
    assert m.thread_id == _HASH
    assert m.speaker_id == "SPEAKER_00"
    assert m.role == "unknown"


def test_transcript_missing_speaker_stays_none_and_unknown_role() -> None:
    t = normalize_whisper_transcript({"_source": {"sha256": _HASH}, "segments": [{"start": 0, "end": 1, "text": "hi"}]})
    m = t.messages[0]
    assert m.speaker_id is None
    assert m.role == "unknown"
    assert m.medium == "call"


def test_hubspot_subject_change_does_move_the_hash() -> None:
    """Contrast with audio: a HubSpot ticket subject is meaningful evidence, so
    changing it is a real content change."""
    a = _payload(subject="Login broken")
    b = _payload(subject="Cannot sign in")
    assert a.content_hash() != b.content_hash()


def test_transcript_never_uses_processed_at_as_call_date() -> None:
    t = normalize_whisper_transcript(
        {
            "_source": {"sha256": _HASH, "processed_at": "2026-09-17T10:00:00Z"},
            "segments": [{"start": 0, "end": 1, "text": "hi"}],
        }
    )
    assert t.source_updated_at is None
    assert t.metadata["processed_at"] == "2026-09-17T10:00:00Z"  # kept as metadata only


@pytest.mark.parametrize(
    "body",
    [
        {"segments": []},  # no _source
        {"_source": {"sha256": "zz"}, "segments": []},  # bad hash
        {"_source": {"sha256": "A" * 64}, "segments": []},  # uppercase hex rejected
        {
            "_source": {"sha256": _HASH, "audio_duration_seconds": 10.0},
            "segments": [{"start": 0, "end": 13, "text": "x"}],
        },
        {
            "_source": {"sha256": _HASH},
            "segments": [{"start": 5, "end": 6, "text": "a"}, {"start": 1, "end": 2, "text": "b"}],
        },
        {"_source": {"sha256": _HASH}, "segments": [{"start": -1, "end": 2, "text": "a"}]},
        {"_source": {"sha256": _HASH}, "segments": [{"start": 3, "end": 1, "text": "a"}]},
    ],
)
def test_transcript_rejects_malformed(body: dict) -> None:
    with pytest.raises(TranscriptError):
        normalize_whisper_transcript(body)


def test_transcript_allows_two_second_model_overrun() -> None:
    # End 0.648s past duration is within tolerance; 2.0s exactly is allowed.
    t = normalize_whisper_transcript(
        {
            "_source": {"sha256": _HASH, "audio_duration_seconds": 10.0},
            "segments": [{"start": 0, "end": 10.648, "text": "a"}, {"start": 10.648, "end": 12.0, "text": "b"}],
        }
    )
    assert len(t.messages) == 2


def test_transcript_accepts_large_valid_file() -> None:
    """The supplied aggregate has files over 400 segments; the normalizer must
    accept them (up to the analyzer's 1000/200000 limits)."""
    segments = [{"start": float(i), "end": float(i) + 1.0, "text": "line"} for i in range(804)]
    t = normalize_whisper_transcript({"_source": {"sha256": _HASH}, "segments": segments})
    assert len(t.messages) == 804


# --------------------------------------------------------------------------- #
# Transcript role assignment — explicit only, never inferred from position
# --------------------------------------------------------------------------- #


def test_transcript_explicit_segment_role_is_honoured() -> None:
    t = normalize_whisper_transcript(
        {
            "_source": {"sha256": _HASH},
            "segments": [
                {"start": 0, "end": 1, "text": "hi", "role": "customer"},
                {"start": 1, "end": 2, "text": "how can I help", "role": "agent"},
            ],
        }
    )
    assert [m.role for m in t.messages] == ["customer", "agent"]


def test_transcript_speaker_roles_map_resolves_by_speaker_id() -> None:
    t = normalize_whisper_transcript(
        {
            "_source": {"sha256": _HASH, "speaker_roles": {"SPEAKER_00": "customer", "SPEAKER_01": "agent"}},
            "segments": [
                {"start": 0, "end": 1, "text": "hi", "speaker": "SPEAKER_00"},
                {"start": 1, "end": 2, "text": "hello", "speaker": "SPEAKER_01"},
                {"start": 2, "end": 3, "text": "?", "speaker": "SPEAKER_00"},
            ],
        }
    )
    assert [m.role for m in t.messages] == ["customer", "agent", "customer"]
    # The mapping is preserved verbatim for audit; the diarization label is not a role.
    assert t.metadata["speaker_roles"] == {"SPEAKER_00": "customer", "SPEAKER_01": "agent"}


def test_transcript_speaker_present_without_mapping_stays_unknown() -> None:
    """A speaker label alone never becomes a business role — no index guessing."""
    t = normalize_whisper_transcript(
        {
            "_source": {"sha256": _HASH},
            "segments": [{"start": 0, "end": 1, "text": "hi", "speaker": "SPEAKER_00"}],
        }
    )
    assert t.messages[0].role == "unknown"


@pytest.mark.parametrize(
    "body",
    [
        # Malformed per-segment role.
        {"_source": {"sha256": _HASH}, "segments": [{"start": 0, "end": 1, "text": "x", "role": "boss"}]},
        # Malformed speaker_roles container / value.
        {
            "_source": {"sha256": _HASH, "speaker_roles": ["customer"]},
            "segments": [{"start": 0, "end": 1, "text": "x"}],
        },
        {
            "_source": {"sha256": _HASH, "speaker_roles": {"SPEAKER_00": "boss"}},
            "segments": [{"start": 0, "end": 1, "text": "x", "speaker": "SPEAKER_00"}],
        },
        # Conflict: explicit segment role disagrees with the mapping for its speaker.
        {
            "_source": {"sha256": _HASH, "speaker_roles": {"SPEAKER_00": "agent"}},
            "segments": [{"start": 0, "end": 1, "text": "x", "speaker": "SPEAKER_00", "role": "customer"}],
        },
        # Mismatch: one speaker carries two different explicit roles.
        {
            "_source": {"sha256": _HASH},
            "segments": [
                {"start": 0, "end": 1, "text": "a", "speaker": "SPEAKER_00", "role": "customer"},
                {"start": 1, "end": 2, "text": "b", "speaker": "SPEAKER_00", "role": "agent"},
            ],
        },
    ],
)
def test_transcript_rejects_bad_roles(body: dict) -> None:
    with pytest.raises(TranscriptError):
        normalize_whisper_transcript(body)


def test_transcript_roles_move_the_content_hash() -> None:
    """Assigning roles is a real evidence change, so the hash moves and stale
    analysis is invalidated (contract 1: role correction re-runs analysis)."""
    plain = normalize_whisper_transcript(
        {"_source": {"sha256": _HASH}, "segments": [{"start": 0, "end": 1, "text": "hi"}]}
    )
    roled = normalize_whisper_transcript(
        {"_source": {"sha256": _HASH}, "segments": [{"start": 0, "end": 1, "text": "hi", "role": "customer"}]}
    )
    assert plain.content_hash() != roled.content_hash()


# --------------------------------------------------------------------------- #
# #C: case-detail evidence is gated by tenant/KB access, not org+policy alone
# --------------------------------------------------------------------------- #


def _detail_db(case: object, telemetry: str = "full") -> AsyncMock:
    from types import SimpleNamespace

    db = AsyncMock()
    db.get = AsyncMock(return_value=SimpleNamespace(telemetry_level=telemetry))
    result = MagicMock()
    result.scalar_one_or_none = MagicMock(return_value=case)
    db.execute = AsyncMock(return_value=result)
    return db


def _fake_case() -> object:
    from datetime import UTC, datetime
    from types import SimpleNamespace

    return SimpleNamespace(
        id=5,
        kb_slug="kb-a",
        payload={"messages": []},
        status="analyzed",
        analysis=[],
        analysis_version="v1",
        content_hash="a" * 64,
        reviews=None,
        imported_at=datetime(2026, 9, 17, tzinfo=UTC),
    )


@pytest.mark.asyncio
async def test_get_support_case_resolves_kb_via_firewall(monkeypatch) -> None:
    from types import SimpleNamespace

    from app.api.app_gaps import get_support_case
    from tests.conftest import make_perms

    seen: dict[str, object] = {}

    async def _fake_kb(kb_slug, perms, db):  # records the firewall was consulted
        seen["kb_slug"] = kb_slug
        return SimpleNamespace(owner_type="org")

    monkeypatch.setattr("app.api.app_gaps.get_kb_with_access", _fake_kb)
    monkeypatch.setattr("app.api.app_gaps.is_personal_kb", lambda kb: False)

    out = await get_support_case(5, perms=make_perms(role="admin"), db=_detail_db(_fake_case()))
    assert out.id == 5 and out.kb_slug == "kb-a"
    assert seen["kb_slug"] == "kb-a"  # KB access was checked


@pytest.mark.asyncio
async def test_get_support_case_404_when_kb_firewall_denies(monkeypatch) -> None:
    from fastapi import HTTPException

    from app.api.app_gaps import get_support_case
    from tests.conftest import make_perms

    async def _deny(kb_slug, perms, db):
        raise HTTPException(status_code=404, detail="Knowledge base not found")

    monkeypatch.setattr("app.api.app_gaps.get_kb_with_access", _deny)
    with pytest.raises(HTTPException) as exc:
        await get_support_case(5, perms=make_perms(role="admin"), db=_detail_db(_fake_case()))
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_get_support_case_404_for_personal_kb(monkeypatch) -> None:
    from types import SimpleNamespace

    from fastapi import HTTPException

    from app.api.app_gaps import get_support_case
    from tests.conftest import make_perms

    monkeypatch.setattr(
        "app.api.app_gaps.get_kb_with_access",
        AsyncMock(return_value=SimpleNamespace(owner_type="user")),
    )
    monkeypatch.setattr("app.api.app_gaps.is_personal_kb", lambda kb: True)
    with pytest.raises(HTTPException) as exc:
        await get_support_case(5, perms=make_perms(role="admin"), db=_detail_db(_fake_case()))
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_get_support_case_403_when_not_full_telemetry() -> None:
    from fastapi import HTTPException

    from app.api.app_gaps import get_support_case
    from tests.conftest import make_perms

    with pytest.raises(HTTPException) as exc:
        await get_support_case(5, perms=make_perms(role="admin"), db=_detail_db(_fake_case(), telemetry="shadow"))
    assert exc.value.status_code == 403


async def test_connector_reconcile_uses_the_client_support_cases_route() -> None:
    import httpx
    from fastapi import FastAPI

    from app.api import internal_support_cases as api

    db = AsyncMock()
    connector = types.SimpleNamespace(id="connector-1", org_id=7)
    org = types.SimpleNamespace(id=7, telemetry_level="full", platform_unlocked_features=["knowledge_gaps"])
    kb = types.SimpleNamespace(slug="kb-a")
    app = FastAPI()
    app.include_router(api.router)
    app.dependency_overrides[api.get_db] = lambda: db
    with (
        patch.object(api, "_load_active_support_connector", AsyncMock(return_value=connector)),
        patch.object(api, "_resolve_org_and_kb", AsyncMock(return_value=(org, kb))),
        patch.object(api, "reconcile_support_cases", AsyncMock(return_value=2)) as reconcile,
    ):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                "/api/internal/connectors/connector-1/support-cases/reconcile",
                headers={"Authorization": f"Bearer {api.settings.internal_secret}"},
                json={"external_ids": ["keep-1"]},
            )
        assert response.status_code == 200
        assert response.json() == {"deleted": 2}
        reconcile.assert_awaited_once_with(db, org_id=7, connector_id="connector-1", external_ids=["keep-1"])


# --------------------------------------------------------------------------- #
# knowledge_gaps platform-unlock gate on the internal (connector) ingest surface
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_internal_ingest_rejects_when_feature_not_unlocked() -> None:
    """knowledge_gaps off => the connector ingest 403s before upsert runs, so no
    evidence is stored or analysed for a tenant without the platform unlock. The
    check is on the backend regardless of any UI gating (fail-closed)."""
    from fastapi import HTTPException

    from app.api import internal_support_cases as api

    connector = types.SimpleNamespace(id="c1", org_id=7, created_by="u1", config={"account_id": "123"})
    org = types.SimpleNamespace(id=7, zitadel_org_id="z7", telemetry_level="full", platform_unlocked_features=[])
    kb = types.SimpleNamespace(slug="kb-a")
    with (
        patch.object(api, "_load_active_support_connector", AsyncMock(return_value=connector)),
        patch.object(api, "_resolve_org_and_kb", AsyncMock(return_value=(org, kb))),
        patch.object(api, "upsert_support_case", AsyncMock()) as upsert,
    ):
        with pytest.raises(HTTPException) as exc:
            await api.ingest_support_case(
                "c1", _payload(), authorization=f"Bearer {api.settings.internal_secret}", db=AsyncMock()
            )
    assert exc.value.status_code == 403
    assert exc.value.detail == {"error_code": "feature_not_unlocked", "feature": "knowledge_gaps"}
    upsert.assert_not_awaited()  # rejected before any store/model work


@pytest.mark.asyncio
async def test_internal_reconcile_rejects_when_feature_not_unlocked() -> None:
    """A disabled/partial import may not drive destructive reconciliation: the
    same platform-unlock gate 403s before reconcile_support_cases deletes."""
    from fastapi import HTTPException

    from app.api import internal_support_cases as api

    connector = types.SimpleNamespace(id="c1", org_id=7)
    org = types.SimpleNamespace(id=7, telemetry_level="full", platform_unlocked_features=[])
    kb = types.SimpleNamespace(slug="kb-a")
    with (
        patch.object(api, "_load_active_support_connector", AsyncMock(return_value=connector)),
        patch.object(api, "_resolve_org_and_kb", AsyncMock(return_value=(org, kb))),
        patch.object(api, "reconcile_support_cases", AsyncMock()) as reconcile,
    ):
        with pytest.raises(HTTPException) as exc:
            await api.reconcile_connector_support_cases(
                "c1",
                api.ReconcileRequest(external_ids=["keep"]),
                authorization=f"Bearer {api.settings.internal_secret}",
                db=AsyncMock(),
            )
    assert exc.value.status_code == 403
    assert exc.value.detail == {"error_code": "feature_not_unlocked", "feature": "knowledge_gaps"}
    reconcile.assert_not_awaited()


@pytest.mark.asyncio
async def test_grouping_failure_logs_no_customer_text() -> None:
    from traceback import format_exception

    from app.services.support_cases import _grouped_findings

    secret = "CUSTOMER_TEXT_RETURNED_AS_GROUP_KEY"
    diagnostics = []

    def record_warning(*args, **kwargs):
        info = sys.exc_info() if kwargs.get("exc_info") is True else kwargs["exc_info"]
        diagnostics.append("".join(format_exception(*info)))

    findings = [{"question": "Reusable question", "diagnosis": "missing"}]
    with (
        patch("app.services.support_cases._open_group_candidates", AsyncMock(return_value=[{"question_key": "key"}])),
        patch("app.services.support_gap_grouping.group_findings", AsyncMock(side_effect=ValueError(secret))),
        patch("app.services.support_cases.logger") as logger,
    ):
        logger.warning.side_effect = record_warning
        result = await _grouped_findings(AsyncMock(), org_id=901, kb_slug="kb-a", exclude_case_id=1, findings=findings)
    assert result == findings
    assert diagnostics and "_grouped_findings" in diagnostics[0]
    assert secret not in diagnostics[0]


def test_each_finding_is_dated_by_the_messages_it_cites() -> None:
    """A reanalysis must not make every finding look like it happened today, and
    a recent follow-up in the same case must not re-date an older question."""
    case = PortalSupportCase(
        id=1,
        org_id=7,
        status="analyzed",
        imported_at=datetime(2026, 9, 23, tzinfo=UTC),
        payload={
            "source_updated_at": "2026-09-20T08:00:00Z",
            "messages": [
                {"id": "m1", "occurred_at": "2026-06-01T10:00:00Z"},
                {"id": "m2", "occurred_at": "2026-06-01T10:05:00Z"},
                {"id": "m9", "occurred_at": "2026-09-20T07:00:00Z"},
            ],
        },
    )
    db = _analysis_db()
    _apply_findings(
        db,
        case=case,
        findings=[
            {"question": "Old topic", "diagnosis": "missing", "message_ids": ["m1", "m2"]},
            {"question": "New topic", "diagnosis": "missing", "message_ids": ["m9"]},
        ],
        created_by="u1",
        kb_slug="kb-a",
    )

    dated = [call.args[0].occurred_at for call in db.add.call_args_list]
    assert dated == [datetime(2026, 6, 1, 10, 5, tzinfo=UTC), datetime(2026, 9, 20, 7, 0, tzinfo=UTC)]


def test_finding_without_dated_messages_falls_back_to_source_then_import() -> None:
    """A call transcript carries no timestamps, so its import time is the floor."""
    dated = PortalSupportCase(
        id=1,
        org_id=7,
        status="analyzed",
        imported_at=datetime(2026, 9, 23, tzinfo=UTC),
        payload={"source_updated_at": "2026-07-04T09:00:00Z", "messages": [{"id": "m1"}]},
    )
    undated = PortalSupportCase(
        id=2,
        org_id=7,
        status="analyzed",
        imported_at=datetime(2026, 9, 23, tzinfo=UTC),
        payload={"messages": [{"id": "m1", "occurred_at": "not-a-date"}]},
    )

    assert _finding_occurred_at(dated, {"message_ids": ["m1"]}) == datetime(2026, 7, 4, 9, 0, tzinfo=UTC)
    assert _finding_occurred_at(undated, {"message_ids": ["absent"]}) == datetime(2026, 9, 23, tzinfo=UTC)


def test_post_deploy_backfill_redates_existing_findings_idempotently() -> None:
    """Existing rows only pick up the new dating if something re-dates them.

    A finding is rebuilt only when its case is reanalysed, which for unchanged
    evidence never happens, so the rollout carries a backfill. It runs on every
    deploy, so it must converge instead of rewriting rows it already fixed.
    """
    sql = (
        Path(__file__).resolve().parents[1] / "alembic" / "versions" / "post_deploy_gap_finding_occurred_at.sql"
    ).read_text()

    assert "portal_retrieval_gaps" in sql and "support_case_id IS NOT NULL" in sql
    # Same order as _finding_occurred_at: cited messages, any message, source stamp, import.
    assert sql.index("message_ids") < sql.index("source_updated_at") < sql.index("imported_at")
    # Converges: a second run finds nothing left to change.
    assert "IS DISTINCT FROM dated.evidence_at" in sql
