"""Proposal and decision per knowledge-gap cluster (app/api/app_gap_clusters.py).

synthetic-data: generator=tests/test_gap_cluster_proposals.py::_cluster seed=0
Fictional questions, people and example.com URLs only.

There is no live-PostgreSQL lane for these endpoints locally, so ``_Store``
stands in for the session: it keeps portal_gap_proposals rows in a list and
answers each statement by the table it reads, enforcing the unique
(org_id, cluster_key) the migration declares. The cluster list itself is
patched per org, so a cluster another org owns is simply absent.
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

_KEY = "wolkcentrale|instellen wachtmuziek wolkcentrale"
_ARTICLE = {"title": "Wachtmuziek instellen", "url": "https://help.example.com/wachtmuziek", "score": 0.8}


def _cluster() -> dict:
    return {
        "cluster_key": _KEY,
        "questions": ["Hoe zet ik wachtmuziek aan voor jan@example.com?"],
        "open_parts": ["wachtmuziek na 18 uur"],
        "articles": [_ARTICLE],
        "language": "nl",
    }


class _Store:
    def __init__(self, *, telemetry_level: str = "full") -> None:
        self.rows: list[PortalGapProposal] = []
        self.telemetry_level = telemetry_level

    @staticmethod
    def _param(compiled: Any, prefix: str) -> Any:
        return next(v for k, v in compiled.params.items() if k.startswith(prefix))

    async def execute(self, stmt: Any) -> MagicMock:
        compiled = stmt.compile(dialect=postgresql.dialect())
        sql = str(compiled)
        result = MagicMock()
        if sql.startswith("UPDATE portal_gap_proposals"):
            row = next(r for r in self.rows if r.id == self._param(compiled, "id_"))
            # Only the statement's own WHERE on the current decision can refuse it.
            hit = "decision_1" not in compiled.params or row.decision == compiled.params["decision_1"]
            if hit:
                row.decision = compiled.params["decision"]
                row.decided_by = compiled.params["decided_by"]
                row.decided_at = compiled.params["decided_at"]
            result.rowcount = int(hit)
        elif "FROM portal_gap_proposals LEFT OUTER JOIN portal_users" in sql:
            org, key = self._param(compiled, "org_id"), self._param(compiled, "cluster_key")
            row = next((r for r in self.rows if r.org_id == org and r.cluster_key == key), None)
            result.one_or_none.return_value = (
                None if row is None else (row, "Fictief Persoon" if row.decided_by else None)
            )
        elif "count(*)" in sql:
            org, since = self._param(compiled, "org_id"), self._param(compiled, "generated_at")
            result.scalar_one.return_value = sum(
                1 for r in self.rows if r.org_id == org and r.generated_at and r.generated_at >= since
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
        if any(r.org_id == row.org_id and r.cluster_key == row.cluster_key for r in self.rows):
            raise IntegrityError("INSERT", {}, Exception("uq_gap_proposals_org_cluster"))
        row.id = len(self.rows) + 1
        self.rows.append(row)

    async def commit(self) -> None:
        return None

    async def rollback(self) -> None:
        return None


class _LiteLLM:
    """Stands in for httpx in turn_judge: records each request body and answers with ``draft``."""

    def __init__(self, draft: dict) -> None:
        self.draft = draft
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
    by_org: dict[int, list[dict]] = {101: [_cluster()]}

    async def _clusters(db, *, org_id, days, include_decided):
        return by_org.get(org_id, [])

    monkeypatch.setattr(api, "knowledge_gap_clusters", _clusters)
    return by_org


def _llm(monkeypatch, draft: dict | None = None) -> _LiteLLM:
    llm = _LiteLLM(draft or _draft())
    monkeypatch.setattr("app.services.turn_judge.httpx.AsyncClient", llm.client)
    return llm


_ORG_A = make_perms(org_id=101)
_ORG_B = make_perms(org_id=202)


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
    assert len(db.rows) == 1
    (body,) = llm.bodies
    assert body["model"] == "klai-judge"
    # PII masking: LiteLLM masks a master-key call for the org that travels with it.
    assert body["metadata"]["_klai_delegated_org_id"] == "zit-org-a"
    prompt = body["messages"][1]["content"]
    assert "wachtmuziek na 18 uur" in prompt
    assert "https://help.example.com/wachtmuziek" in prompt


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
async def test_below_full_telemetry_no_proposal_and_no_model_call(monkeypatch, clusters) -> None:
    llm = _llm(monkeypatch)

    with pytest.raises(HTTPException) as exc:
        await api.create_gap_proposal(_KEY, perms=_ORG_A, db=_Store(telemetry_level="shadow"))  # type: ignore[arg-type]

    assert exc.value.status_code == 409
    assert "full" in exc.value.detail
    assert llm.bodies == []


@pytest.mark.asyncio
async def test_the_twenty_first_generation_in_seven_days_is_refused_with_the_count(monkeypatch, clusters) -> None:
    llm = _llm(monkeypatch)
    db = _Store()
    recent = datetime.now(tz=UTC) - timedelta(days=6)
    db.rows = [
        PortalGapProposal(id=n, org_id=101, cluster_key=f"other|{n}", generated_at=recent, decision="none")
        for n in range(1, 21)
    ]

    with pytest.raises(HTTPException) as exc:
        await api.create_gap_proposal(_KEY, perms=_ORG_A, db=db)  # type: ignore[arg-type]

    assert exc.value.status_code == 429
    assert "(20 in the last 7 days)" in exc.value.detail
    assert llm.bodies == []


@pytest.mark.asyncio
async def test_an_example_text_over_4000_characters_is_rejected_and_not_stored(monkeypatch, clusters) -> None:
    _llm(monkeypatch, _draft(example_text="x" * 4001))
    db = _Store()

    with pytest.raises(HTTPException) as exc:
        await api.create_gap_proposal(_KEY, perms=_ORG_A, db=db)  # type: ignore[arg-type]

    assert exc.value.status_code == 502
    assert db.rows == []


@pytest.mark.asyncio
async def test_a_decision_is_final(monkeypatch, clusters) -> None:
    db = _Store()

    out = await api.decide_gap_cluster(_KEY, GapDecisionRequest(decision="rejected"), perms=_ORG_A, db=db)  # type: ignore[arg-type]
    with pytest.raises(HTTPException) as exc:
        await api.decide_gap_cluster(_KEY, GapDecisionRequest(decision="out_of_scope"), perms=_ORG_A, db=db)  # type: ignore[arg-type]

    assert (out.decision, out.decided_by) == ("rejected", "Fictief Persoon")
    assert exc.value.status_code == 409
    assert [(r.decision, r.decided_by) for r in db.rows] == [("rejected", 7)]


@pytest.mark.asyncio
async def test_a_decision_on_a_cluster_with_a_proposal_is_final_too(monkeypatch, clusters) -> None:
    _llm(monkeypatch)
    db = _Store()
    await api.create_gap_proposal(_KEY, perms=_ORG_A, db=db)  # type: ignore[arg-type]

    await api.decide_gap_cluster(_KEY, GapDecisionRequest(decision="out_of_scope"), perms=_ORG_A, db=db)  # type: ignore[arg-type]
    with pytest.raises(HTTPException) as exc:
        await api.decide_gap_cluster(_KEY, GapDecisionRequest(decision="rejected"), perms=_ORG_A, db=db)  # type: ignore[arg-type]

    assert exc.value.status_code == 409
    assert db.rows[0].decision == "out_of_scope"


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
        with pytest.raises(HTTPException) as exc:
            await call
        assert exc.value.status_code == 404
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
