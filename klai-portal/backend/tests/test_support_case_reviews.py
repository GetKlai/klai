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
    # FOR SHARE, then the case FOR UPDATE — so the mock returns a coherent org
    # row for the first execute, the case for the second.
    policy_res = MagicMock()
    policy_res.one_or_none.return_value = SimpleNamespace(
        telemetry_level=telemetry, platform_unlocked_features=list(features)
    )
    case_res = MagicMock()
    case_res.scalar_one_or_none.return_value = case
    db.execute = AsyncMock(side_effect=[policy_res, case_res])
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
async def test_detail_null_analysis_has_null_revision(monkeypatch) -> None:
    out = await _get_detail(monkeypatch, _detail_case(status="failed"))
    assert out.analysis is None
    assert out.analysis_revision is None


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
