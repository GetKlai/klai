"""Proposal and decision per knowledge-gap cluster (app/api/app_gap_clusters.py).

synthetic-data: generator=tests/test_gap_cluster_proposals.py::_cluster seed=0
Fictional questions, people and example.com URLs only.

There is no live-PostgreSQL lane for these endpoints locally, so ``_Store``
stands in for the session: it keeps portal_gap_proposals rows in a list,
enforces the unique (org_id, cluster_key) the migration declares, and applies
an UPDATE only when the row meets the statement's own WHERE guards. The
cluster list itself is patched per org, so a cluster another org owns is
simply absent.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import IntegrityError

from app.api import app_gap_clusters as api
from app.api.app_gap_clusters import GapDecisionRequest
from app.models.gap_proposals import PortalGapProposal
from tests.conftest import make_perms

_KEY = "t0123456789abcdef0123"
_ARTICLE = {"title": "Wachtmuziek instellen", "url": "https://help.example.com/wachtmuziek", "score": 0.8}
_COLUMNS = {c.name for c in PortalGapProposal.__table__.columns}


def _cluster(key: str = _KEY, *, decidable: bool = True) -> dict:
    return {
        "cluster_key": key,
        "questions": ["hoe krijg ik muziek als bellers in de wacht staan"],
        "open_parts": ["wachtmuziek na 18 uur"],
        "articles": [_ARTICLE],
        "language": "nl",
        "decidable": decidable,
    }


class _Store:
    def __init__(self, *, telemetry_level: str = "full") -> None:
        self.rows: list[PortalGapProposal] = []
        self.telemetry_level = telemetry_level
        self.pending: PortalGapProposal | None = None

    @staticmethod
    def _param(compiled: Any, prefix: str) -> Any:
        return next(v for k, v in compiled.params.items() if k.startswith(prefix))

    def _update(self, compiled: Any, sql: str) -> int:
        p = compiled.params
        row = next(r for r in self.rows if r.id == p["id_1"])
        increments = "attempts + " in sql
        ok = True
        if "decision_1" in p:
            ok = ok and row.decision == p["decision_1"]
        guard = "attempts_2" if increments else "attempts_1"
        if guard in p:
            ok = ok and row.attempts < p[guard]
        if "status_1" in p:
            ok = ok and (
                row.status == p["status_1"] or (row.status == p["status_2"] and row.attempted_at < p["attempted_at_1"])
            )
        if ok:
            for name, value in p.items():
                if name in _COLUMNS:
                    setattr(row, name, value)
            if increments:
                row.attempts += 1
        return int(ok)

    async def execute(self, stmt: Any) -> MagicMock:
        compiled = stmt.compile(dialect=postgresql.dialect())
        sql = str(compiled)
        result = MagicMock()
        if sql.startswith("UPDATE portal_gap_proposals"):
            result.rowcount = self._update(compiled, sql)
        elif "FROM portal_gap_proposals LEFT OUTER JOIN portal_users" in sql:
            org, key = self._param(compiled, "org_id"), self._param(compiled, "cluster_key")
            row = next((r for r in self.rows if r.org_id == org and r.cluster_key == key), None)
            result.one_or_none.return_value = (
                None if row is None else (row, "Fictief Persoon" if row.decided_by else None)
            )
        elif "sum(portal_gap_proposals.attempts)" in sql:
            org, since = self._param(compiled, "org_id"), self._param(compiled, "attempted_at")
            result.scalar_one.return_value = sum(
                r.attempts for r in self.rows if r.org_id == org and r.attempted_at and r.attempted_at >= since
            )
        elif "FROM portal_orgs" in sql:
            result.one.return_value = SimpleNamespace(telemetry_level=self.telemetry_level, zitadel_org_id="zit-org-a")
        elif "FROM portal_users" in sql:
            result.one_or_none.return_value = (7, "Fictief Persoon")
        return result

    def add(self, row: PortalGapProposal) -> None:
        self.pending = row

    async def flush(self) -> None:
        row, self.pending = self.pending, None
        assert row is not None
        if any(r.org_id == row.org_id and r.cluster_key == row.cluster_key for r in self.rows):
            raise IntegrityError("INSERT", {}, Exception("uq_gap_proposals_org_cluster"))
        row.id = len(self.rows) + 1
        row.attempts = row.attempts or 0
        self.rows.append(row)

    async def commit(self) -> None:
        return None

    async def rollback(self) -> None:
        return None


class _LiteLLM:
    """Stands in for httpx in turn_judge: records each request body and answers with ``draft``.

    ``during`` runs inside the call, while the first request holds its reservation.
    """

    def __init__(self, draft: dict, during: Any = None) -> None:
        self.draft = draft
        self.during = during
        self.bodies: list[dict] = []

    def client(self, timeout: float) -> Any:
        outer = self

        class _Client:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *_):
                return None

            async def post(self, url, headers=None, json=None):
                outer.bodies.append(json)
                if outer.during is not None:
                    await outer.during()
                response = MagicMock()
                response.json.return_value = {"choices": [{"message": {"content": _json(outer.draft)}}]}
                return response

        return _Client()


def _json(value: dict) -> str:
    return json.dumps(value)


def _draft(**overrides: Any) -> dict:
    return {
        "missing": "Hoe je wachtmuziek na kantooruren instelt, staat nergens.",
        "where_kind": "existing",
        "where_title": "Wachtmuziek instellen",
        "where_section": "Na kantooruren",
        "example_text": "## Wachtmuziek na 18 uur\n\nOpen [menupad].\n\nOok genoemd als: wachtmuziek na 18 uur",
        "open_facts": ["Het menupad"],
        **overrides,
    }


@pytest.fixture
def clusters(monkeypatch) -> dict[int, list[dict]]:
    by_org: dict[int, list[dict]] = {101: [_cluster(), _cluster("internal|no_article", decidable=False)]}

    async def _clusters(db, *, org_id, days, include_decided):
        assert days == api.CLUSTER_WINDOW_DAYS
        return by_org.get(org_id, [])

    monkeypatch.setattr(api, "knowledge_gap_clusters", _clusters)
    return by_org


def _llm(monkeypatch, draft: dict | None = None, during: Any = None) -> _LiteLLM:
    llm = _LiteLLM(draft or _draft(), during)
    monkeypatch.setattr("app.services.turn_judge.httpx.AsyncClient", llm.client)
    return llm


_ORG_A = make_perms(org_id=101)
_ORG_B = make_perms(org_id=202)


async def _status(call: Any) -> int:
    with pytest.raises(HTTPException) as exc:
        await call
    return exc.value.status_code


@pytest.mark.asyncio
async def test_a_proposal_is_generated_on_klai_judge_with_the_org_delegated_and_stored(monkeypatch, clusters) -> None:
    llm = _llm(monkeypatch)
    db = _Store()

    out = await api.create_gap_proposal(_KEY, perms=_ORG_A, db=db)  # type: ignore[arg-type]

    assert (out.cluster_key, out.where_kind, out.where_title) == (_KEY, "existing", "Wachtmuziek instellen")
    # The link is the found article's, never one the model wrote.
    assert out.where_url == "https://help.example.com/wachtmuziek"
    assert out.open_facts == ["Het menupad"]
    assert (out.model, out.decision, out.language) == ("klai-judge", "none", "nl")
    assert [(r.status, r.attempts) for r in db.rows] == [("ready", 1)]
    (body,) = llm.bodies
    assert body["model"] == "klai-judge"
    # PII masking: LiteLLM masks a master-key call for the org that travels with it.
    assert body["metadata"]["_klai_delegated_org_id"] == "zit-org-a"
    prompt = body["messages"][1]["content"]
    # The visitor's own wording, not the case step's rewritten search query.
    assert "hoe krijg ik muziek als bellers in de wacht staan" in prompt
    assert "wachtmuziek na 18 uur" in prompt


@pytest.mark.asyncio
async def test_a_second_request_returns_the_stored_proposal_without_a_model_call(monkeypatch, clusters) -> None:
    llm = _llm(monkeypatch)
    db = _Store()

    first = await api.create_gap_proposal(_KEY, perms=_ORG_A, db=db)  # type: ignore[arg-type]
    second = await api.create_gap_proposal(_KEY, perms=_ORG_A, db=db)  # type: ignore[arg-type]
    fetched = await api.get_gap_proposal(_KEY, perms=_ORG_A, db=db)  # type: ignore[arg-type]

    assert first.id == second.id == fetched.id
    assert len(llm.bodies) == 1


@pytest.mark.asyncio
async def test_a_request_while_another_generates_is_409_and_leaves_one_row(monkeypatch, clusters) -> None:
    db = _Store()
    seen: list[int] = []

    async def concurrent() -> None:
        seen.append(await _status(api.create_gap_proposal(_KEY, perms=_ORG_A, db=db)))  # type: ignore[arg-type]

    llm = _llm(monkeypatch, during=concurrent)

    await api.create_gap_proposal(_KEY, perms=_ORG_A, db=db)  # type: ignore[arg-type]

    assert seen == [409]
    assert len(llm.bodies) == 1
    assert [(r.status, r.attempts) for r in db.rows] == [("ready", 1)]


@pytest.mark.asyncio
async def test_below_full_telemetry_no_proposal_and_no_model_call(monkeypatch, clusters) -> None:
    llm = _llm(monkeypatch)

    with pytest.raises(HTTPException) as exc:
        await api.create_gap_proposal(_KEY, perms=_ORG_A, db=_Store(telemetry_level="shadow"))  # type: ignore[arg-type]

    assert exc.value.status_code == 409
    assert "full" in exc.value.detail
    assert llm.bodies == []


@pytest.mark.asyncio
async def test_the_twenty_first_call_in_seven_days_is_refused_with_the_count(monkeypatch, clusters) -> None:
    llm = _llm(monkeypatch)
    db = _Store()
    recent = datetime.now(tz=UTC) - timedelta(days=6)
    db.rows = [
        PortalGapProposal(
            id=n, org_id=101, cluster_key=f"t{n}", status="ready", attempts=1, attempted_at=recent, decision="none"
        )
        for n in range(1, 21)
    ]

    with pytest.raises(HTTPException) as exc:
        await api.create_gap_proposal(_KEY, perms=_ORG_A, db=db)  # type: ignore[arg-type]

    assert exc.value.status_code == 429
    assert "(20 in the last 7 days)" in exc.value.detail
    assert llm.bodies == []


@pytest.mark.asyncio
async def test_calls_older_than_seven_days_do_not_count(monkeypatch, clusters) -> None:
    _llm(monkeypatch)
    db = _Store()
    old = datetime.now(tz=UTC) - timedelta(days=8)
    db.rows = [
        PortalGapProposal(
            id=n, org_id=101, cluster_key=f"t{n}", status="ready", attempts=1, attempted_at=old, decision="none"
        )
        for n in range(1, 21)
    ]

    out = await api.create_gap_proposal(_KEY, perms=_ORG_A, db=db)  # type: ignore[arg-type]

    assert out.cluster_key == _KEY


@pytest.mark.asyncio
async def test_a_failed_model_output_counts_toward_the_cap_and_may_be_retried_once(monkeypatch, clusters) -> None:
    llm = _llm(monkeypatch, _draft(example_text="x" * 4001))
    db = _Store()

    first = await _status(api.create_gap_proposal(_KEY, perms=_ORG_A, db=db))  # type: ignore[arg-type]
    second = await _status(api.create_gap_proposal(_KEY, perms=_ORG_A, db=db))  # type: ignore[arg-type]
    third = await _status(api.create_gap_proposal(_KEY, perms=_ORG_A, db=db))  # type: ignore[arg-type]

    assert (first, second, third) == (502, 502, 409)
    assert len(llm.bodies) == 2
    assert [(r.status, r.attempts) for r in db.rows] == [("failed", 2)]
    assert await _status(api.get_gap_proposal(_KEY, perms=_ORG_A, db=db)) == 404  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_an_article_the_cluster_never_found_is_rejected(monkeypatch, clusters) -> None:
    _llm(monkeypatch, _draft(where_title="Belplan voor het weekend"))
    db = _Store()

    assert await _status(api.create_gap_proposal(_KEY, perms=_ORG_A, db=db)) == 502  # type: ignore[arg-type]
    assert [r.status for r in db.rows] == ["failed"]


@pytest.mark.asyncio
async def test_a_decision_is_final(monkeypatch, clusters) -> None:
    db = _Store()

    out = await api.decide_gap_cluster(_KEY, GapDecisionRequest(decision="rejected"), perms=_ORG_A, db=db)  # type: ignore[arg-type]
    second = await _status(
        api.decide_gap_cluster(_KEY, GapDecisionRequest(decision="out_of_scope"), perms=_ORG_A, db=db)  # type: ignore[arg-type]
    )

    assert (out.decision, out.decided_by) == ("rejected", "Fictief Persoon")
    assert second == 409
    assert [(r.decision, r.decided_by) for r in db.rows] == [("rejected", 7)]


@pytest.mark.asyncio
async def test_a_decision_on_a_cluster_with_a_proposal_is_final_too(monkeypatch, clusters) -> None:
    _llm(monkeypatch)
    db = _Store()
    await api.create_gap_proposal(_KEY, perms=_ORG_A, db=db)  # type: ignore[arg-type]

    await api.decide_gap_cluster(_KEY, GapDecisionRequest(decision="out_of_scope"), perms=_ORG_A, db=db)  # type: ignore[arg-type]
    second = await _status(
        api.decide_gap_cluster(_KEY, GapDecisionRequest(decision="rejected"), perms=_ORG_A, db=db)  # type: ignore[arg-type]
    )

    assert second == 409
    assert db.rows[0].decision == "out_of_scope"


@pytest.mark.asyncio
async def test_a_catch_all_cluster_takes_no_decision_and_no_proposal(monkeypatch, clusters) -> None:
    llm = _llm(monkeypatch)
    db = _Store()
    key = "internal|no_article"

    decision = await _status(api.decide_gap_cluster(key, GapDecisionRequest(decision="rejected"), perms=_ORG_A, db=db))  # type: ignore[arg-type]
    proposal = await _status(api.create_gap_proposal(key, perms=_ORG_A, db=db))  # type: ignore[arg-type]

    assert (decision, proposal) == (409, 409)
    assert db.rows == []
    assert llm.bodies == []


@pytest.mark.asyncio
async def test_another_org_gets_404_for_the_cluster_and_its_proposal(monkeypatch, clusters) -> None:
    _llm(monkeypatch)
    db = _Store()
    await api.create_gap_proposal(_KEY, perms=_ORG_A, db=db)  # type: ignore[arg-type]

    for call in (
        api.get_gap_proposal(_KEY, perms=_ORG_B, db=db),  # type: ignore[arg-type]
        api.create_gap_proposal(_KEY, perms=_ORG_B, db=_Store()),  # type: ignore[arg-type]
        api.decide_gap_cluster(_KEY, GapDecisionRequest(decision="rejected"), perms=_ORG_B, db=db),  # type: ignore[arg-type]
    ):
        assert await _status(call) == 404
    assert db.rows[0].decision == "none"


def test_the_cluster_router_sits_behind_the_gaps_capability_and_unlock() -> None:
    import inspect

    features = [
        inspect.getclosurevars(dep.dependency).nonlocals.get("feature")
        for dep in api.router.dependencies
        if getattr(dep.dependency, "__closure__", None)
    ]
    assert "knowledge_gaps" in features
    assert len(api.router.dependencies) == 2
