"""Unit tests for the human-review layer over machine analysis (SPEC-RAG-SUPPORT-GAP).

The DB-backed serialization, RLS, telemetry-lock and copy-on-write contract is
proven against real PostgreSQL in ``test_support_cases_postgres.py``; these cover
the pure logic: the analysis-revision hash, the current-revision review lookup,
the list-item counts, and the endpoint's status/revision/index validation.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from app.services.support_case_reviews import (
    compute_analysis_revision,
    review_key,
    reviews_for_current_revision,
)

_HASH = "c" * 64


# --------------------------------------------------------------------------- #
# analysis-revision hash — identifies the exact displayed analysis
# --------------------------------------------------------------------------- #


def test_revision_is_deterministic_for_same_inputs() -> None:
    analysis = [{"question": "Q", "diagnosis": "missing"}]
    a = compute_analysis_revision(content_hash=_HASH, analysis_version="v7", analysis=analysis)
    b = compute_analysis_revision(content_hash=_HASH, analysis_version="v7", analysis=list(analysis))
    assert a == b and len(a) == 64


@pytest.mark.parametrize(
    "over",
    [
        {"content_hash": "d" * 64},
        {"analysis_version": "v8"},
        {"analysis": [{"question": "Q", "diagnosis": "covered"}]},
    ],
)
def test_revision_moves_when_any_component_changes(over: dict) -> None:
    base = dict(content_hash=_HASH, analysis_version="v7", analysis=[{"question": "Q", "diagnosis": "missing"}])
    assert compute_analysis_revision(**base) != compute_analysis_revision(**{**base, **over})


def test_revision_ignores_an_enriched_review_key() -> None:
    """A finding carrying a ``review`` key (the API-enriched shape) must hash the
    same as the raw stored finding — the revision identifies the machine analysis,
    never the human verdict laid over it."""
    raw = [{"question": "Q", "diagnosis": "missing"}]
    enriched = [{"question": "Q", "diagnosis": "missing", "review": {"decision": "correct"}}]
    assert compute_analysis_revision(
        content_hash=_HASH, analysis_version="v7", analysis=raw
    ) == compute_analysis_revision(content_hash=_HASH, analysis_version="v7", analysis=enriched)


# --------------------------------------------------------------------------- #
# current-revision review lookup — old revisions are retained but not shown
# --------------------------------------------------------------------------- #


def test_reviews_for_current_revision_only_matches_current() -> None:
    rev = "r1"
    reviews = {
        f"{rev}:0": {"decision": "correct", "note": "", "reviewed_by": "u", "reviewed_at": "t"},
        "old-rev:1": {"decision": "incorrect", "note": "stale", "reviewed_by": "u", "reviewed_at": "t"},
    }
    out = reviews_for_current_revision(reviews, rev, 2)
    assert out[0] == reviews[f"{rev}:0"]
    assert out[1] is None  # the previous-revision review is retained on the map but not surfaced


def test_reviews_for_current_revision_handles_null_map() -> None:
    assert reviews_for_current_revision(None, "r1", 3) == [None, None, None]


def test_review_key_shape() -> None:
    assert review_key("abc", 4) == "abc:4"


# --------------------------------------------------------------------------- #
# list-item counts — analyzed no-gap/uncertain, and null analysis => zeros
# --------------------------------------------------------------------------- #


def _list_db(cases: list[object], total: int, telemetry: str = "full") -> AsyncMock:
    db = AsyncMock()
    telem_res = MagicMock()
    telem_res.scalar_one_or_none.return_value = telemetry
    count_res = MagicMock()
    count_res.scalar_one.return_value = total
    list_res = MagicMock()
    list_res.scalars.return_value.all.return_value = cases
    # Endpoint reads telemetry, then the total count, then the page of cases.
    db.execute = AsyncMock(side_effect=[telem_res, count_res, list_res])
    return db


def _case(**over: object) -> SimpleNamespace:
    base: dict[str, object] = {
        "id": 1,
        "kb_slug": "kb-a",
        "subject": "Login",
        "source": "hubspot",
        "status": "analyzed",
        "imported_at": datetime(2026, 9, 17, tzinfo=UTC),
        "payload": {"messages": [{"medium": "email"}, {"medium": "call"}, {"medium": "email"}]},
        "analysis": [{"diagnosis": "missing"}, {"diagnosis": "uncertain"}, {"diagnosis": "covered"}],
        "analysis_version": "v7",
        "content_hash": _HASH,
        "reviews": None,
    }
    base.update(over)
    return SimpleNamespace(**base)


@pytest.mark.asyncio
async def test_list_counts_questions_uncertain_and_mediums() -> None:
    from app.api.app_support_cases import list_support_cases
    from tests.conftest import make_perms

    kb = SimpleNamespace(slug="kb-a", owner_type="org")
    out = await list_support_cases(kb=kb, limit=25, offset=0, perms=make_perms(org_id=101), db=_list_db([_case()], 1))
    item = out.cases[0]
    assert (item.question_count, item.uncertain_count, item.reviewed_count) == (3, 1, 0)
    assert sorted(item.mediums) == ["call", "email"]
    assert out.total == 1


@pytest.mark.asyncio
async def test_list_null_analysis_yields_zero_counts_no_crash() -> None:
    from app.api.app_support_cases import list_support_cases
    from tests.conftest import make_perms

    kb = SimpleNamespace(slug="kb-a", owner_type="org")
    failed = _case(id=2, status="failed", analysis=None, analysis_version=None)
    pending = _case(id=3, status="pending", analysis=None, analysis_version=None)
    out = await list_support_cases(
        kb=kb, limit=25, offset=0, perms=make_perms(org_id=101), db=_list_db([failed, pending], 2)
    )
    assert [(c.status, c.question_count, c.uncertain_count, c.reviewed_count) for c in out.cases] == [
        ("failed", 0, 0, 0),
        ("pending", 0, 0, 0),
    ]


@pytest.mark.asyncio
async def test_list_rejects_non_full_telemetry() -> None:
    from app.api.app_support_cases import list_support_cases
    from tests.conftest import make_perms

    kb = SimpleNamespace(slug="kb-a", owner_type="org")
    with pytest.raises(HTTPException) as exc:
        await list_support_cases(
            kb=kb, limit=25, offset=0, perms=make_perms(org_id=101), db=_list_db([], 0, telemetry="shadow")
        )
    assert exc.value.status_code == 403


# --------------------------------------------------------------------------- #
# PATCH review — status / revision / index validation
# --------------------------------------------------------------------------- #


def _review_db(
    case: object | None, telemetry: str = "full", features: tuple[str, ...] = ("knowledge_gaps",)
) -> AsyncMock:
    db = AsyncMock()
    # The review write locks one org-policy row (telemetry + platform unlocks)
    # FOR SHARE, then the case FOR UPDATE; a valid write then looks up the
    # reviewer's portal id and reconciles the finding's derived gap rows, so the
    # mock also answers those two reads (an empty gap set = a clean no-op).
    policy_res = MagicMock()
    policy_res.one_or_none.return_value = SimpleNamespace(
        telemetry_level=telemetry, platform_unlocked_features=list(features)
    )
    case_res = MagicMock()
    case_res.scalar_one_or_none.return_value = case
    reviewer_res = MagicMock()
    reviewer_res.scalar_one_or_none.return_value = 7
    gaps_res = MagicMock()
    gaps_res.scalars.return_value.all.return_value = []
    db.execute = AsyncMock(side_effect=[policy_res, case_res, reviewer_res, gaps_res])
    db.commit = AsyncMock()
    return db


def _analyzed_case() -> SimpleNamespace:
    analysis = [{"question": "Q0", "diagnosis": "missing"}, {"question": "Q1", "diagnosis": "uncertain"}]
    return SimpleNamespace(
        id=5,
        org_id=101,
        kb_slug="kb-a",
        status="analyzed",
        analysis=analysis,
        analysis_version="v7",
        content_hash=_HASH,
        reviews=None,
    )


def _body(**over: object):
    from app.api.app_support_cases import FindingReviewRequest

    base: dict[str, object] = {"analysis_revision": "", "decision": "correct", "note": ""}
    base.update(over)
    return FindingReviewRequest(**base)


async def _call_review(db: AsyncMock, case_id: int, index: int, body, kb_owner: str = "org"):
    from app.api.app_support_cases import review_finding
    from tests.conftest import make_perms

    kb = SimpleNamespace(slug="kb-a", owner_type=kb_owner)
    return await review_finding(
        case_id=case_id,
        finding_index=index,
        body=body,
        kb=kb,
        perms=make_perms(org_id=101, user_id="reviewer-1"),
        db=db,
    )


@pytest.mark.asyncio
async def test_review_valid_writes_and_returns_server_identity() -> None:
    case = _analyzed_case()
    rev = compute_analysis_revision(
        content_hash=case.content_hash, analysis_version=case.analysis_version, analysis=case.analysis
    )
    out = await _call_review(_review_db(case), 5, 0, _body(analysis_revision=rev))
    assert out.analysis_revision == rev
    assert out.review.decision == "correct"
    assert out.review.reviewed_by == "reviewer-1"  # server-driven, never client-supplied
    assert case.reviews[review_key(rev, 0)]["reviewed_by"] == "reviewer-1"


@pytest.mark.asyncio
async def test_review_stale_revision_is_409() -> None:
    case = _analyzed_case()
    with pytest.raises(HTTPException) as exc:
        await _call_review(_review_db(case), 5, 0, _body(analysis_revision="not-the-current-revision"))
    assert exc.value.status_code == 409


@pytest.mark.asyncio
async def test_review_not_analyzed_is_409() -> None:
    case = _analyzed_case()
    case.status = "pending"
    case.analysis = None
    with pytest.raises(HTTPException) as exc:
        await _call_review(_review_db(case), 5, 0, _body(analysis_revision="whatever"))
    assert exc.value.status_code == 409


@pytest.mark.asyncio
async def test_review_out_of_range_index_is_404() -> None:
    case = _analyzed_case()
    rev = compute_analysis_revision(
        content_hash=case.content_hash, analysis_version=case.analysis_version, analysis=case.analysis
    )
    with pytest.raises(HTTPException) as exc:
        await _call_review(_review_db(case), 5, 9, _body(analysis_revision=rev))
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_review_missing_case_is_404() -> None:
    with pytest.raises(HTTPException) as exc:
        await _call_review(_review_db(None), 999, 0, _body(analysis_revision="x"))
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_review_non_full_telemetry_is_403() -> None:
    case = _analyzed_case()
    with pytest.raises(HTTPException) as exc:
        await _call_review(_review_db(case, telemetry="shadow"), 5, 0, _body(analysis_revision="x"))
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_review_feature_not_unlocked_is_403_and_writes_nothing() -> None:
    """knowledge_gaps revoked (telemetry still full) => the authoritative locked
    policy check 403s before the case is touched, so an in-flight review cannot
    commit a verdict after the tenant loses the feature."""
    case = _analyzed_case()
    with pytest.raises(HTTPException) as exc:
        await _call_review(_review_db(case, features=()), 5, 0, _body(analysis_revision="x"))
    assert exc.value.status_code == 403
    assert exc.value.detail == {"error_code": "feature_not_unlocked", "feature": "knowledge_gaps"}
    assert case.reviews is None  # nothing written


@pytest.mark.asyncio
async def test_review_personal_kb_is_404() -> None:
    case = _analyzed_case()
    with pytest.raises(HTTPException) as exc:
        await _call_review(_review_db(case), 5, 0, _body(analysis_revision="x"), kb_owner="user")
    assert exc.value.status_code == 404


def test_review_note_over_2000_chars_rejected() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        _body(note="x" * 2001)


# --------------------------------------------------------------------------- #
# Detail enrichment — per-finding review, current revision only, null-safe
# --------------------------------------------------------------------------- #


def _detail_db(case: object) -> AsyncMock:
    db = AsyncMock()
    db.get = AsyncMock(return_value=SimpleNamespace(telemetry_level="full"))
    result = MagicMock()
    result.scalar_one_or_none = MagicMock(return_value=case)
    db.execute = AsyncMock(return_value=result)
    return db


def _detail_case(**over: object) -> SimpleNamespace:
    base: dict[str, object] = {
        "id": 5,
        "kb_slug": "kb-a",
        "payload": {"messages": []},
        "status": "analyzed",
        "analysis": None,
        "analysis_version": None,
        "content_hash": _HASH,
        "reviews": None,
        "imported_at": datetime(2026, 9, 17, tzinfo=UTC),
    }
    base.update(over)
    return SimpleNamespace(**base)


async def _get_detail(monkeypatch, case: SimpleNamespace):
    from app.api.app_gaps import get_support_case
    from tests.conftest import make_perms

    monkeypatch.setattr(
        "app.api.app_gaps.get_kb_with_access", AsyncMock(return_value=SimpleNamespace(owner_type="org"))
    )
    monkeypatch.setattr("app.api.app_gaps.is_personal_kb", lambda kb: False)
    return await get_support_case(5, perms=make_perms(org_id=101), db=_detail_db(case))


@pytest.mark.asyncio
async def test_detail_null_analysis_still_supplies_a_revision(monkeypatch) -> None:
    """#1: a failed/null-analysis case still exposes a usable revision so the UI can
    offer retry/role correction — the analysis list itself stays null."""
    out = await _get_detail(monkeypatch, _detail_case(status="failed"))
    assert out.analysis is None
    assert out.analysis_revision == compute_analysis_revision(content_hash=_HASH, analysis_version=None, analysis=None)


@pytest.mark.asyncio
async def test_detail_enriches_current_revision_review_only(monkeypatch) -> None:
    analysis = [{"question": "Q0", "diagnosis": "missing"}, {"question": "Q1", "diagnosis": "uncertain"}]
    rev = compute_analysis_revision(content_hash=_HASH, analysis_version="v7", analysis=analysis)
    reviews = {
        review_key(rev, 0): {"decision": "correct", "note": "", "reviewed_by": "u", "reviewed_at": "t"},
        "stale-rev:1": {"decision": "incorrect", "note": "old", "reviewed_by": "u", "reviewed_at": "t"},
    }
    case = _detail_case(analysis=analysis, analysis_version="v7", reviews=reviews)
    out = await _get_detail(monkeypatch, case)
    assert out.analysis_revision == rev
    assert out.analysis[0]["review"]["decision"] == "correct"
    assert out.analysis[1]["review"] is None  # index 1 only has a superseded-revision review
    # The raw stored analysis is not mutated by enrichment.
    assert "review" not in analysis[0]


# --------------------------------------------------------------------------- #
# Role correction — trusted server-owned override, applied by the evidence store
# --------------------------------------------------------------------------- #


def _call_case(**over: object) -> SimpleNamespace:
    from app.schemas_support_cases import SupportCaseMessage, SupportCasePayload

    payload = SupportCasePayload(
        source="audio",
        account_id="audio",
        external_id="h" * 64,
        messages=[
            SupportCaseMessage(id="seg-0", kind="transcript", role="unknown", text="hi", medium="call"),
            SupportCaseMessage(id="seg-1", kind="transcript", role="unknown", text="bye", medium="call"),
            SupportCaseMessage(id="mail-0", kind="email", role="unknown", text="ticket", medium="email"),
        ],
    ).model_dump()
    base: dict[str, object] = {
        "id": 5,
        "org_id": 101,
        "kb_slug": "kb-a",
        "connector_id": None,
        "created_by": "owner-x",
        "status": "analyzed",
        "analysis": [{"question": "Q0", "diagnosis": "uncertain"}],
        "analysis_version": "v7",
        "content_hash": _HASH,
        "payload": payload,
        "reviews": None,
    }
    base.update(over)
    return SimpleNamespace(**base)


@pytest.mark.parametrize("bad", [{"nope": "customer"}, {"mail-0": "agent"}])
def test_assert_call_messages_rejects_non_call_or_unknown_id(bad: dict) -> None:
    from app.api.app_support_cases import _assert_call_messages

    with pytest.raises(HTTPException) as exc:
        _assert_call_messages(_call_case(), bad)
    assert exc.value.status_code == 422


def test_assert_call_messages_accepts_call_ids() -> None:
    from app.api.app_support_cases import _assert_call_messages

    # seg-0/seg-1 are call messages; a clean return means the ids are acceptable.
    assert _assert_call_messages(_call_case(), {"seg-0": "customer", "seg-1": "agent"}) is None


# --------------------------------------------------------------------------- #
# Trusted role overlay — the id + role-independent-segment match matrix (#2)
# --------------------------------------------------------------------------- #


def _call_msg(**over: object):
    from app.schemas_support_cases import SupportCaseMessage

    base: dict[str, object] = {
        "id": "seg-0",
        "kind": "transcript",
        "role": "unknown",
        "text": "hi there",
        "medium": "call",
        "speaker_id": "spk-1",
        "start_seconds": 0.0,
        "end_seconds": 2.0,
    }
    base.update(over)
    return SupportCaseMessage(**base)  # type: ignore[arg-type]


def _overlay_payload(**over: object):
    from app.schemas_support_cases import SupportCasePayload

    return SupportCasePayload(source="audio", account_id="audio", external_id="h" * 64, messages=[_call_msg(**over)])


def _override_for(role: str = "customer"):
    from app.services.support_cases import _segment_fingerprint

    return {
        "seg-0": {
            "role": role,
            "provider_role": "unknown",
            "fingerprint": _segment_fingerprint(_call_msg()),
            "reviewed_by": "r",
            "reviewed_at": "t",
        }
    }


def test_overlay_applies_role_on_unchanged_segment() -> None:
    from app.services.support_cases import _overlay_role_overrides

    payload = _overlay_payload()  # a byte-identical re-import: same id, same identity
    _overlay_role_overrides(payload, _override_for())
    assert payload.messages[0].role == "customer"


@pytest.mark.parametrize(
    "change",
    [
        {"text": "a completely different sentence"},  # reused id, changed content
        {"end_seconds": 9.0},  # changed timing
        {"medium": "chat"},  # changed medium
        {"speaker_id": "spk-2"},  # changed speaker identity
        {"id": "seg-9"},  # the corrected segment is gone/renumbered
    ],
)
def test_overlay_never_inherits_onto_changed_or_missing_segment(change: dict) -> None:
    from app.services.support_cases import _overlay_role_overrides

    payload = _overlay_payload(**change)
    _overlay_role_overrides(payload, _override_for())
    assert payload.messages[0].role == "unknown"  # provider role kept


def test_overlay_noop_without_overrides_keeps_unknown() -> None:
    from app.services.support_cases import _overlay_role_overrides

    payload = _overlay_payload()
    _overlay_role_overrides(payload, {})
    assert payload.messages[0].role == "unknown"


def test_fingerprint_is_role_independent() -> None:
    from app.services.support_cases import _segment_fingerprint

    assert _segment_fingerprint(_call_msg(role="unknown")) == _segment_fingerprint(_call_msg(role="customer"))


def test_merge_records_origin_and_server_audit_and_keeps_origin_on_recorrection() -> None:
    from app.services.support_cases import _merge_role_overrides, _segment_fingerprint

    first = _merge_role_overrides({}, _overlay_payload(), {"seg-0": "customer"}, reviewer="r1")
    assert first["seg-0"]["provider_role"] == "unknown"
    assert first["seg-0"]["reviewed_by"] == "r1" and first["seg-0"]["reviewed_at"]
    assert first["seg-0"]["fingerprint"] == _segment_fingerprint(_call_msg())
    # A second correction on an already-overlaid payload keeps the TRUE origin.
    second = _merge_role_overrides(first, _overlay_payload(role="customer"), {"seg-0": "agent"}, reviewer="r2")
    assert (second["seg-0"]["role"], second["seg-0"]["provider_role"]) == ("agent", "unknown")


# --------------------------------------------------------------------------- #
# PATCH roles endpoint — gating, revision, attribution, response mapping
# --------------------------------------------------------------------------- #


def _roles_db(case: object | None, telemetry: str = "full") -> AsyncMock:
    db = AsyncMock()
    db.get = AsyncMock(return_value=SimpleNamespace(id=101, telemetry_level=telemetry, zitadel_org_id="zit-101"))
    res = MagicMock()
    res.scalar_one_or_none.return_value = case
    db.execute = AsyncMock(return_value=res)
    db.commit = AsyncMock()
    return db


async def _call_roles(db: AsyncMock, case_id: int, revision: str, roles: dict, kb_owner: str = "org"):
    from app.api.app_support_cases import RoleCorrectionRequest, correct_message_roles
    from tests.conftest import make_perms

    kb = SimpleNamespace(slug="kb-a", owner_type=kb_owner)
    body = RoleCorrectionRequest(analysis_revision=revision, message_roles=roles)
    return await correct_message_roles(
        case_id=case_id, body=body, kb=kb, perms=make_perms(org_id=101, user_id="reviewer-1"), db=db
    )


def _rev(case: SimpleNamespace) -> str:
    return compute_analysis_revision(
        content_hash=case.content_hash, analysis_version=case.analysis_version, analysis=case.analysis
    )


@pytest.mark.asyncio
async def test_roles_stale_revision_is_409() -> None:
    case = _call_case()
    with pytest.raises(HTTPException) as exc:
        await _call_roles(_roles_db(case), 5, "stale", {"seg-0": "customer"})
    assert exc.value.status_code == 409


@pytest.mark.asyncio
async def test_roles_failed_case_with_current_revision_reaches_upsert(monkeypatch) -> None:
    """#1: a failed/analysis-null case is retryable — a role correction against its
    current revision reaches the store, not a spurious 'not analyzed' 409."""
    from app.api import app_support_cases as mod
    from app.services.support_cases import UpsertResult

    case = _call_case(status="failed", analysis=None, analysis_version=None)
    captured: dict = {}

    async def _fake_upsert(_db, **kwargs):
        captured.update(kwargs)
        return UpsertResult(case_id=5, status="analyzed", changed=True, findings_count=1)

    monkeypatch.setattr(mod, "upsert_support_case", _fake_upsert)
    out = await _call_roles(_roles_db(case), 5, _rev(case), {"seg-0": "customer"})
    assert out.status == "analyzed"
    assert captured["role_overrides"] == {"seg-0": "customer"}
    assert captured["role_override_reviewer"] == "reviewer-1"
    assert captured["force_reanalysis"] is True


@pytest.mark.asyncio
async def test_roles_incomplete_evidence_is_409() -> None:
    from app.schemas_support_cases import SupportCaseMessage, SupportCasePayload

    incomplete = SupportCasePayload(
        source="audio",
        account_id="audio",
        external_id="h" * 64,
        complete=False,
        messages=[SupportCaseMessage(id="seg-0", kind="transcript", role="unknown", text="hi", medium="call")],
    ).model_dump()
    case = _call_case(payload=incomplete)
    with pytest.raises(HTTPException) as exc:
        await _call_roles(_roles_db(case), 5, "x", {"seg-0": "customer"})
    assert exc.value.status_code == 409
    assert exc.value.detail["error_code"] == "support_case_incomplete"


@pytest.mark.asyncio
async def test_roles_missing_case_is_404() -> None:
    with pytest.raises(HTTPException) as exc:
        await _call_roles(_roles_db(None), 5, "x", {"seg-0": "customer"})
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_roles_personal_kb_is_404() -> None:
    with pytest.raises(HTTPException) as exc:
        await _call_roles(_roles_db(_call_case()), 5, "x", {"seg-0": "customer"}, kb_owner="user")
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_roles_non_full_telemetry_is_403() -> None:
    with pytest.raises(HTTPException) as exc:
        await _call_roles(_roles_db(_call_case(), telemetry="shadow"), 5, "x", {"seg-0": "customer"})
    assert exc.value.status_code == 403


def test_roles_empty_map_is_rejected_by_request_model() -> None:
    from pydantic import ValidationError

    from app.api.app_support_cases import RoleCorrectionRequest

    with pytest.raises(ValidationError):
        RoleCorrectionRequest(analysis_revision="r", message_roles={})


@pytest.mark.asyncio
@pytest.mark.parametrize("legacy", [False, True])
async def test_roles_apply_reruns_with_case_attribution_and_maps_response(monkeypatch, legacy) -> None:
    from app.api import app_support_cases as mod
    from app.services.support_cases import UpsertResult

    case = _call_case(connector_id="conn-1", created_by="owner-x")
    if legacy:
        case.payload["messages"][0].pop("medium", None)
    captured: dict = {}

    async def _fake_upsert(_db, **kwargs):
        captured.update(kwargs)
        return UpsertResult(case_id=5, status="analyzed", changed=True, findings_count=2)

    monkeypatch.setattr(mod, "upsert_support_case", _fake_upsert)
    out = await _call_roles(_roles_db(case), 5, _rev(case), {"seg-0": "customer"})
    assert (out.case_id, out.status, out.findings_count) == (5, "analyzed", 2)
    # Attribution comes from the case, never the reviewer; the store is forced with a version guard.
    assert captured["connector_id"] == "conn-1"
    assert captured["created_by"] == "owner-x"
    assert captured["force_reanalysis"] is True
    assert captured["expected_content_hash"] == _HASH
    # The correction is a TRUSTED, server-owned override (stored on reviews by the
    # store), not something written into the provider-controlled payload metadata.
    assert captured["role_overrides"] == {"seg-0": "customer"}
    assert captured["role_override_reviewer"] == "reviewer-1"
    assert "role_reviews" not in captured["payload"].metadata


@pytest.mark.asyncio
async def test_roles_failed_reanalysis_is_503(monkeypatch) -> None:
    from app.api import app_support_cases as mod
    from app.services.support_cases import UpsertResult

    case = _call_case()

    async def _fake_upsert(_db, **kwargs):
        return UpsertResult(case_id=5, status="analyzed", changed=False, findings_count=1, reanalysis_failed=True)

    monkeypatch.setattr(mod, "upsert_support_case", _fake_upsert)
    with pytest.raises(HTTPException) as exc:
        await _call_roles(_roles_db(case), 5, _rev(case), {"seg-0": "customer"})
    assert exc.value.status_code == 503


# --------------------------------------------------------------------------- #
# POST reanalyze — force a fresh analysis of unchanged evidence
# --------------------------------------------------------------------------- #


async def _call_reanalyze(db: AsyncMock, case_id: int, revision: str, kb_owner: str = "org"):
    from app.api.app_support_cases import ReanalyzeRequest, reanalyze_support_case
    from tests.conftest import make_perms

    kb = SimpleNamespace(slug="kb-a", owner_type=kb_owner)
    return await reanalyze_support_case(
        case_id=case_id,
        body=ReanalyzeRequest(analysis_revision=revision),
        kb=kb,
        perms=make_perms(org_id=101, user_id="reviewer-1"),
        db=db,
    )


@pytest.mark.asyncio
async def test_reanalyze_reruns_unchanged_payload_with_case_attribution(monkeypatch) -> None:
    from app.api import app_support_cases as mod
    from app.services.support_cases import UpsertResult

    case = _call_case(connector_id="conn-1")
    captured: dict = {}

    async def _fake_upsert(_db, **kwargs):
        captured.update(kwargs)
        return UpsertResult(case_id=5, status="analyzed", changed=False, findings_count=3)

    monkeypatch.setattr(mod, "upsert_support_case", _fake_upsert)
    out = await _call_reanalyze(_roles_db(case), 5, _rev(case))
    assert (out.status, out.findings_count) == ("analyzed", 3)
    assert captured["force_reanalysis"] is True
    assert captured["expected_content_hash"] == _HASH
    assert captured["connector_id"] == "conn-1"
    assert captured["payload"].external_id == "h" * 64  # the stored payload, unchanged


@pytest.mark.asyncio
async def test_reanalyze_stale_revision_is_409() -> None:
    with pytest.raises(HTTPException) as exc:
        await _call_reanalyze(_roles_db(_call_case()), 5, "stale")
    assert exc.value.status_code == 409


@pytest.mark.asyncio
async def test_reanalyze_failed_case_with_current_revision_reaches_upsert(monkeypatch) -> None:
    """#1: reanalyse retries a failed case — the current revision reaches the store
    (no role override), instead of the old spurious 'not analyzed' 409."""
    from app.api import app_support_cases as mod
    from app.services.support_cases import UpsertResult

    case = _call_case(status="failed", analysis=None, analysis_version=None)
    captured: dict = {}

    async def _fake_upsert(_db, **kwargs):
        captured.update(kwargs)
        return UpsertResult(case_id=5, status="analyzed", changed=True, findings_count=1)

    monkeypatch.setattr(mod, "upsert_support_case", _fake_upsert)
    out = await _call_reanalyze(_roles_db(case), 5, _rev(case))
    assert out.status == "analyzed"
    assert captured["force_reanalysis"] is True
    assert captured["role_overrides"] is None  # reanalyse never sets a role override


@pytest.mark.asyncio
async def test_reanalyze_failed_run_is_503(monkeypatch) -> None:
    from app.api import app_support_cases as mod
    from app.services.support_cases import UpsertResult

    case = _call_case()

    async def _fake_upsert(_db, **kwargs):
        return UpsertResult(case_id=5, status="analyzed", changed=False, findings_count=1, reanalysis_failed=True)

    monkeypatch.setattr(mod, "upsert_support_case", _fake_upsert)
    with pytest.raises(HTTPException) as exc:
        await _call_reanalyze(_roles_db(case), 5, _rev(case))
    assert exc.value.status_code == 503


# --------------------------------------------------------------------------- #
# Review corrected_diagnosis — the human override that drives inbox visibility
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "machine,decision,corrected,expected",
    [
        ("missing", "correct", None, "missing"),  # correct restores the machine diagnosis
        ("uncertain", "correct", None, "uncertain"),  # an uncertain finding stays out of the inbox
        ("missing", "incorrect", None, None),  # incorrect + no correction = dismiss
        ("missing", "incorrect", "covered", "covered"),  # corrected to a non-actionable label
        ("missing", "incorrect", "outdated", "outdated"),  # re-bucketed to another actionable label
        ("uncertain", "incorrect", "missing", "missing"),  # promotion: uncertain -> actionable
        ("missing", "uncertain", None, None),  # reviewer unsure -> out of the inbox
    ],
)
def test_effective_diagnosis(machine, decision, corrected, expected) -> None:
    from app.services.support_cases import _effective_diagnosis

    assert _effective_diagnosis(machine, decision, corrected) == expected


@pytest.mark.asyncio
async def test_review_stores_and_returns_corrected_diagnosis() -> None:
    case = _analyzed_case()
    rev = compute_analysis_revision(
        content_hash=case.content_hash, analysis_version=case.analysis_version, analysis=case.analysis
    )
    out = await _call_review(
        _review_db(case), 5, 0, _body(analysis_revision=rev, decision="incorrect", corrected_diagnosis="covered")
    )
    assert out.review.corrected_diagnosis == "covered"
    assert case.reviews[review_key(rev, 0)]["corrected_diagnosis"] == "covered"


def test_review_rejects_unknown_corrected_diagnosis() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        _body(corrected_diagnosis="not-a-diagnosis")


# --------------------------------------------------------------------------- #
# PUT reference — a human's whole-case gold answer set (never model output)
# --------------------------------------------------------------------------- #


async def _call_reference(
    db: AsyncMock, case_id: int, content_hash: str, questions: list[dict], complete: bool = False, kb_owner: str = "org"
):
    from app.api.app_support_cases import CaseReferenceRequest, ReferenceQuestion, put_case_reference
    from tests.conftest import make_perms

    kb = SimpleNamespace(slug="kb-a", owner_type=kb_owner)
    body = CaseReferenceRequest(
        content_hash=content_hash,
        questions=[ReferenceQuestion(**q) for q in questions],
        complete=complete,
    )
    return await put_case_reference(
        case_id=case_id, body=body, kb=kb, perms=make_perms(org_id=101, user_id="reviewer-1"), db=db
    )


@pytest.mark.asyncio
async def test_reference_stores_and_returns_server_identity() -> None:
    case = _call_case()
    out = await _call_reference(
        _review_db(case), 5, _HASH, [{"question": "How reset 2FA?", "diagnosis": "missing", "message_ids": ["seg-0"]}]
    )
    assert out.reviewed_by == "reviewer-1"  # server-derived, never client-supplied
    stored = case.reviews["_reference"]
    assert stored["content_hash"] == _HASH
    assert stored["questions"][0]["question"] == "How reset 2FA?"


@pytest.mark.asyncio
async def test_reference_stale_content_hash_is_409() -> None:
    with pytest.raises(HTTPException) as exc:
        await _call_reference(_review_db(_call_case()), 5, "not-the-hash", [])
    assert exc.value.status_code == 409


@pytest.mark.asyncio
async def test_reference_blank_question_is_422() -> None:
    with pytest.raises(HTTPException) as exc:
        await _call_reference(
            _review_db(_call_case()), 5, _HASH, [{"question": "   ", "diagnosis": "missing", "message_ids": []}]
        )
    assert exc.value.status_code == 422


@pytest.mark.asyncio
async def test_reference_unknown_evidence_id_is_422() -> None:
    with pytest.raises(HTTPException) as exc:
        await _call_reference(
            _review_db(_call_case()), 5, _HASH, [{"question": "Q", "diagnosis": "missing", "message_ids": ["nope"]}]
        )
    assert exc.value.status_code == 422


@pytest.mark.asyncio
async def test_reference_complete_true_allows_empty_question_set() -> None:
    case = _call_case()
    out = await _call_reference(_review_db(case), 5, _HASH, [], complete=True)
    assert out.complete is True and out.questions == []
    assert case.reviews["_reference"]["complete"] is True


def test_reference_question_rejects_unknown_diagnosis() -> None:
    from pydantic import ValidationError

    from app.api.app_support_cases import ReferenceQuestion

    with pytest.raises(ValidationError):
        ReferenceQuestion(question="Q", diagnosis="not-a-diagnosis", message_ids=[])


@pytest.mark.asyncio
async def test_detail_surfaces_reference_only_when_hash_matches(monkeypatch) -> None:
    ref = {
        "content_hash": _HASH,
        "questions": [{"question": "Q", "diagnosis": "missing", "message_ids": []}],
        "complete": True,
        "reviewed_by": "u",
        "reviewed_at": "t",
    }
    out = await _get_detail(monkeypatch, _detail_case(reviews={"_reference": ref}))
    assert out.content_hash == _HASH
    assert out.reference == ref


@pytest.mark.asyncio
async def test_detail_hides_reference_written_against_superseded_evidence(monkeypatch) -> None:
    ref = {"content_hash": "stale-hash", "questions": [], "complete": True, "reviewed_by": "u", "reviewed_at": "t"}
    out = await _get_detail(monkeypatch, _detail_case(reviews={"_reference": ref}))
    assert out.reference is None
