import asyncio
import importlib.util
import json
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from fastapi import FastAPI

from app.api import internal
from app.core.database import get_db
from app.services import ingest_gap_evaluation as gap_evaluation
from app.services import support_case_analysis as sca

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "evaluate_ingest_gaps.py"
_SPEC = importlib.util.spec_from_file_location("evaluate_ingest_gaps", _SCRIPT)
assert _SPEC and _SPEC.loader
ingest_eval = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(ingest_eval)


async def test_known_source_trials_distinguish_detected_gap_from_unscorable_question(monkeypatch) -> None:
    def verdict(diagnosis: str, article_ids: list[str] | None = None) -> str:
        return json.dumps(
            {
                "diagnosis": diagnosis,
                "rationale": "Grounded test verdict.",
                "missing_information": "The documented steps." if diagnosis == "missing" else "",
                "proposed_change": "Document the steps." if diagnosis == "missing" else "",
                "article_ids": article_ids or [],
            }
        )

    async def judge(*, system: str, user: str) -> str:
        if system == sca.QUERY_REWRITE_SYSTEM_PROMPT:
            return json.dumps({"query": "reset access"})
        if system == sca.ANSWER_CHECK_SYSTEM_PROMPT:
            return json.dumps({"answers_question": True, "reason": "Grounded."})
        data = json.loads(user)
        if "unsupported generated question" in data["question"]:
            return verdict("uncertain")
        return verdict("covered", [data["passages"][0]["id"]]) if data["passages"] else verdict("missing")

    monkeypatch.setattr(sca, "_call_llm", judge)
    monkeypatch.setattr(sca, "_retrieve", AsyncMock(side_effect=AssertionError("global retriever used")))
    snapshot = {
        "snapshot_id": "snapshot-7",
        "chunks": [
            {
                "chunk_id": "chunk-covered",
                "org_id": "org-1",
                "kb_slug": "support",
                "artifact_id": "article-1",
                "text": "Open settings and choose Reset access.",
                "questions": ["How do I reset access?", "An unsupported generated question"],
            }
        ],
    }

    report = await ingest_eval.evaluate(snapshot, limit=10)

    assert report["counts"] == {"detected_missing": 1, "unscorable": 1}
    detected, unscorable = report["results"]
    assert detected["present_diagnosis"] == "covered"
    assert detected["withheld_diagnosis"] == "missing"
    assert (unscorable["present_diagnosis"], unscorable["status"]) == ("uncertain", "unscorable")
    assert all(secret not in json.dumps(report) for secret in ("How do I reset", "Open settings"))
    assert report["quality_status"] == "inconclusive"
    assert ingest_eval.report_exit_code(report) == 1
    for counts in ({"unscorable": 2}, {"uncertain": 1}):
        assert ingest_eval.report_exit_code({"counts": counts}) == 1


@pytest.mark.asyncio
async def test_model_http_failure_is_sanitized_but_keeps_status(monkeypatch) -> None:
    response = httpx.Response(429, request=httpx.Request("POST", "http://judge"), text="customer text")
    monkeypatch.setattr(
        sca,
        "_call_llm",
        AsyncMock(side_effect=httpx.HTTPStatusError("rate limited", request=response.request, response=response)),
    )
    report = await ingest_eval.evaluate(
        _snapshot_body() | {"chunks": [{**_snapshot_body()["chunks"][0], "org_id": "org-1", "kb_slug": "support"}]}
    )

    assert report["results"][0]["status"] == "failed"
    assert report["results"][0]["http_status"] == 429
    assert "customer text" not in json.dumps(report)


@pytest.mark.asyncio
async def test_assessment_budget_preserves_completed_results(monkeypatch) -> None:
    calls = 0

    async def trial(*_args, **_kwargs) -> dict:
        nonlocal calls
        calls += 1
        if calls > 2:
            await asyncio.sleep(1)
        return {"diagnosis": "covered" if calls % 2 else "missing"}

    monkeypatch.setattr(gap_evaluation, "_trial", trial)
    body = _snapshot_body()
    body["chunks"][0]["questions"].append("What is the second procedure?")
    for chunk in body["chunks"]:
        chunk.update(org_id="org-1", kb_slug="support")

    report = await gap_evaluation.evaluate_ingest_snapshot(body, budget_seconds=0.01)

    assert [result["status"] for result in report["results"]] == [
        "detected_missing",
        "failed",
    ]
    assert report["results"][1]["error_type"] == "TimeoutError"


async def _request(app: FastAPI, path: str, *, token: str | None, json_body: dict) -> httpx.Response:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        return await client.post(path, headers=headers, json=json_body)


def _app_with_db(db: object) -> FastAPI:
    app = FastAPI()
    app.include_router(internal.router)

    async def override_db():
        yield db

    app.dependency_overrides[get_db] = override_db
    return app


def _snapshot_body() -> dict:
    return {
        "snapshot_id": "nightly-2026-09-19",
        "chunks": [
            {
                "chunk_id": "chunk-1",
                "artifact_id": "artifact-1",
                "text": "Open settings and choose Reset access.",
                "questions": ["How do I reset access?"],
            }
        ],
    }


@pytest.fixture
def internal_gap_eval(monkeypatch):
    monkeypatch.setattr(internal.settings, "internal_secret", "nightly-secret")
    monkeypatch.setattr(internal, "_check_rate_limit_internal", AsyncMock())
    monkeypatch.setattr(internal, "_audit_internal_call", AsyncMock())
    monkeypatch.setattr(internal, "set_tenant", AsyncMock())
    evaluate_mock = AsyncMock(return_value={"counts": {}})
    monkeypatch.setattr(internal, "evaluate_ingest_snapshot", evaluate_mock)
    return evaluate_mock


@pytest.mark.asyncio
async def test_internal_gap_eval_rejects_missing_bearer_before_database(internal_gap_eval) -> None:
    db = MagicMock()
    db.execute = AsyncMock()

    response = await _request(
        _app_with_db(db),
        "/internal/ingest-gap-eval/org-1/support",
        token=None,
        json_body=_snapshot_body(),
    )

    assert response.status_code == 401
    db.execute.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("unknown", [False, True])
async def test_internal_gap_eval_rejects_unknown_or_disabled_tenant_before_model(
    internal_gap_eval, unknown: bool
) -> None:
    disabled_org = (
        None
        if unknown
        else MagicMock(
            id=7,
            zitadel_org_id="org-1",
            deleted_at=None,
            provisioning_status="ready",
            telemetry_level="shadow",
            platform_unlocked_features=["knowledge_gaps"],
        )
    )
    result = MagicMock()
    result.scalar_one_or_none.return_value = disabled_org
    db = MagicMock()
    db.execute = AsyncMock(return_value=result)
    db.rollback = AsyncMock()

    response = await _request(
        _app_with_db(db),
        "/internal/ingest-gap-eval/org-1/support",
        token="nightly-secret",
        json_body=_snapshot_body(),
    )

    assert response.status_code == 403
    internal_gap_eval.assert_not_awaited()


@pytest.mark.asyncio
async def test_internal_gap_eval_rejects_foreign_kb_before_model(internal_gap_eval) -> None:
    enabled_org = MagicMock(
        id=7,
        zitadel_org_id="org-1",
        deleted_at=None,
        provisioning_status="ready",
        telemetry_level="full",
        platform_unlocked_features=["knowledge_gaps"],
    )
    org_result = MagicMock()
    org_result.scalar_one_or_none.return_value = enabled_org
    kb_result = MagicMock()
    kb_result.scalar_one_or_none.return_value = None
    db = MagicMock()
    db.execute = AsyncMock(side_effect=[org_result, kb_result])
    db.rollback = AsyncMock()

    response = await _request(
        _app_with_db(db),
        "/internal/ingest-gap-eval/org-1/foreign-kb",
        token="nightly-secret",
        json_body=_snapshot_body(),
    )

    assert response.status_code == 404
    internal_gap_eval.assert_not_awaited()
    org_query, kb_query = (call.args[0] for call in db.execute.await_args_list)
    assert "portal_orgs.zitadel_org_id =" in str(org_query)
    assert org_query.compile().params == {"zitadel_org_id_1": "org-1"}
    assert "portal_knowledge_bases.org_id =" in str(kb_query)
    assert "portal_knowledge_bases.slug =" in str(kb_query)
    assert "portal_knowledge_bases.owner_type =" in str(kb_query)
    assert set(kb_query.compile().params.values()) == {7, "foreign-kb", "org"}


@pytest.mark.asyncio
async def test_internal_gap_eval_does_not_read_expired_org_after_rollback(internal_gap_eval) -> None:
    class ExpiringOrg:
        expired = False
        deleted_at = None
        provisioning_status = "ready"
        telemetry_level = "full"
        platform_unlocked_features = ("knowledge_gaps",)

        @property
        def id(self) -> int:
            if self.expired:
                raise RuntimeError("expired ORM attribute accessed")
            return 7

    org = ExpiringOrg()
    org_result = MagicMock()
    org_result.scalar_one_or_none.return_value = org
    kb_result = MagicMock()
    kb_result.scalar_one_or_none.return_value = MagicMock()
    db = MagicMock()
    db.execute = AsyncMock(side_effect=[org_result, kb_result])

    async def rollback() -> None:
        org.expired = True

    db.rollback = AsyncMock(side_effect=rollback)

    response = await _request(
        _app_with_db(db),
        "/internal/ingest-gap-eval/org-1/support",
        token="nightly-secret",
        json_body={
            **_snapshot_body(),
            "chunks": [{**_snapshot_body()["chunks"][0], "questions": [f"Question {i}" for i in range(10)]}],
        },
    )

    assert response.status_code == 200
    assert isinstance(internal.set_tenant, AsyncMock)
    internal.set_tenant.assert_awaited_once_with(db, 7)
    assert isinstance(internal._audit_internal_call, AsyncMock)
    audit_call = internal._audit_internal_call.await_args
    assert audit_call is not None and audit_call.kwargs == {"org_id": 7}
    call = internal_gap_eval.await_args
    assert call.kwargs == {"limit": 10, "budget_seconds": 270}
    assert "budget_seconds" not in call.args[0]


@pytest.mark.asyncio
async def test_internal_gap_eval_rejects_more_than_ten_questions(internal_gap_eval) -> None:
    org = MagicMock(
        id=7,
        deleted_at=None,
        provisioning_status="ready",
        telemetry_level="full",
        platform_unlocked_features=["knowledge_gaps"],
    )
    org_result = MagicMock()
    org_result.scalar_one_or_none.return_value = org
    kb_result = MagicMock()
    kb_result.scalar_one_or_none.return_value = MagicMock()
    db = MagicMock()
    db.execute = AsyncMock(side_effect=[org_result, kb_result])
    body = _snapshot_body()
    body["chunks"][0]["questions"] = [f"Question {i}" for i in range(11)]

    response = await _request(
        _app_with_db(db),
        "/internal/ingest-gap-eval/org-1/support",
        token="nightly-secret",
        json_body=body,
    )

    assert response.status_code == 422
    internal_gap_eval.assert_not_awaited()


@pytest.mark.asyncio
async def test_internal_gap_eval_lists_only_policy_filtered_org_scopes(internal_gap_eval, monkeypatch) -> None:
    del internal_gap_eval

    @asynccontextmanager
    async def scope(db):
        yield db

    monkeypatch.setattr(internal, "cross_org_scope", scope)
    result = MagicMock()
    result.all.return_value = [("org-1", "support")]
    db = MagicMock()
    db.execute = AsyncMock(return_value=result)
    app = _app_with_db(db)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(
            "/internal/ingest-gap-eval/scopes",
            headers={"Authorization": "Bearer nightly-secret"},
        )

    assert response.json() == [{"org_id": "org-1", "kb_slug": "support"}]
    query = db.execute.await_args.args[0]
    sql = str(query)
    assert "portal_knowledge_bases.org_id = portal_orgs.id" in sql
    assert "portal_orgs.provisioning_status =" in sql
    assert "portal_orgs.telemetry_level =" in sql
    assert "= ANY (portal_orgs.platform_unlocked_features)" in sql
    assert "!= ANY" not in sql
    assert "portal_knowledge_bases.owner_type =" in sql
    assert {"ready", "full", "knowledge_gaps", "org"} <= set(query.compile().params.values())
