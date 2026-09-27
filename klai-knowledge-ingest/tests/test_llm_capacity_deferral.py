"""Enrichment deferral when the klai-ingest key or LiteLLM budget is spent.

The proxy answers below have the shape litellm==1.96.2 produces (see
knowledge_ingest/llm_capacity.py for the source lines). A job that meets one
must be re-queued for later instead of burning its retries and failing.
"""

from __future__ import annotations

import json
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
import structlog.testing

from knowledge_ingest import enrichment, enrichment_tasks, llm_capacity

KEY_FULL = (
    402,
    "litellm.APIError: MistralException - Workspace monthly spending limit reached.",
)
BUDGET_CROSSED = (
    429,
    "No deployments available - crossed budget: Exceeded budget for deployment "
    "model_name: klai-ingest, litellm_params.model: mistral/mistral-vibe-cli-fast, "
    "model_id: 0f3c: 20.4 >= 1d",
)
ALL_ACCOUNTS_FULL = (
    429,
    "No deployments available for selected model, Try again in 5 seconds. "
    "Passed model=klai-ingest. pre-call-checks=False, cooldown_list=[]",
)
COOLING_DOWN = (
    429,
    "No deployments available for selected model, Try again in 60 seconds. "
    "Passed model=klai-ingest. pre-call-checks=False, cooldown_list=['0f3c']",
)
RATE_LIMITED = (
    429,
    "litellm.RateLimitError: Model rate limit exceeded. RPM limit=900, current usage=900",
)
UPSTREAM_500 = (500, "litellm.InternalServerError: MistralException - upstream error")


async def _litellm_error(answer: tuple[int, str]) -> enrichment.EnrichmentError:
    """Run the real enrichment LLM call against a proxy that gives ``answer``."""
    status, message = answer
    body = json.dumps({"error": {"message": message, "type": "None", "param": "None"}})
    transport = httpx.MockTransport(lambda request: httpx.Response(status, text=body))
    real_client = httpx.AsyncClient
    limiter = MagicMock(acquire=AsyncMock())
    with (
        patch(
            "knowledge_ingest.enrichment.httpx.AsyncClient",
            lambda **kwargs: real_client(transport=transport, **kwargs),
        ),
        patch("knowledge_ingest.enrichment.shared_klai_fast_limiter", return_value=limiter),
        pytest.raises(enrichment.EnrichmentError) as raised,
    ):
        await enrichment._call_llm("prompt", "docs/a.md")
    return raised.value


@pytest.mark.parametrize(
    ("answer", "is_capacity"),
    [
        (KEY_FULL, True),
        (BUDGET_CROSSED, True),
        (ALL_ACCOUNTS_FULL, True),
        (COOLING_DOWN, False),
        (RATE_LIMITED, False),
        (UPSTREAM_500, False),
    ],
    ids=["key-full", "budget", "all-full", "cooldown", "rate-limit", "500"],
)
async def test_only_spent_capacity_counts_as_capacity(answer, is_capacity):
    assert llm_capacity.is_llm_capacity_error(await _litellm_error(answer)) is is_capacity


class _Task:
    def __init__(self, fn):
        self.fn = fn
        self.defer_async = AsyncMock()
        self.configure = MagicMock(return_value=MagicMock(defer_async=self.defer_async))

    async def __call__(self, *args, **kwargs):
        return await self.fn(*args, **kwargs)


class _FakeApp:
    def task(self, **kwargs):
        return _Task


async def _run_bulk_enrichment(error: Exception, *, llm_deferred_since: int | None = None):
    """Run the bulk task for one artifact whose chunk enrichment raises ``error``."""
    app = _FakeApp()
    enrichment_tasks._register_tasks(app)
    task = app.enrich_document_bulk

    async def _load_and_enrich(artifact_id, resource_key=None):
        await enrichment_tasks._enrich_document(
            org_id="org-1",
            kb_slug="kb-1",
            path="docs/a.md",
            document_text="body",
            chunks=["body"],
            title="A",
            artifact_id=artifact_id,
            user_id=None,
            extra_payload={"document_summary": "s", "document_language": "en"},
            synthesis_depth=0,
        )

    context = SimpleNamespace(job=SimpleNamespace(queueing_lock="org-1:kb-1:docs/a.md:a1"))
    with (
        patch.object(enrichment_tasks, "_load_and_enrich", _load_and_enrich),
        patch.object(enrichment_tasks.enrichment, "enrich_chunks", AsyncMock(side_effect=error)),
        patch.object(enrichment_tasks, "_set_direct_upload_index_status", AsyncMock()) as status,
        structlog.testing.capture_logs() as logs,
    ):
        outcome = None
        try:
            await task(context, artifact_id="a1", llm_deferred_since=llm_deferred_since)
        except Exception as exc:
            outcome = exc
    return task, status, logs, outcome


@pytest.mark.parametrize("answer", [KEY_FULL, BUDGET_CROSSED], ids=["key-full", "budget"])
async def test_spent_capacity_defers_the_enrichment_job_instead_of_failing_it(answer):
    before = int(time.time())
    task, status, logs, outcome = await _run_bulk_enrichment(await _litellm_error(answer))

    assert outcome is None
    status.assert_not_awaited()
    configure = task.configure.call_args.kwargs
    assert configure["queueing_lock"] == "org-1:kb-1:docs/a.md:a1"
    assert 3600 <= configure["schedule_in"]["seconds"] <= 5400
    deferred = task.defer_async.call_args.kwargs
    assert deferred["artifact_id"] == "a1"
    assert deferred["llm_deferred_since"] >= before
    assert [e["log_level"] for e in logs if e["event"] == "enrichment_deferred_llm_capacity"] == [
        "info"
    ]
    assert not [e for e in logs if e["log_level"] == "error"]


async def test_other_llm_failure_still_fails_and_retries_the_job():
    error = await _litellm_error(UPSTREAM_500)
    task, status, _logs, outcome = await _run_bulk_enrichment(error)

    assert outcome is error
    status.assert_awaited_once()
    assert status.await_args.args[1] == "failed"
    task.defer_async.assert_not_awaited()


async def test_deferral_stops_after_the_window_with_a_final_error_log():
    since = int(time.time()) - llm_capacity.DEFERRAL_WINDOW_SECONDS - 1
    task, _status, logs, outcome = await _run_bulk_enrichment(
        await _litellm_error(KEY_FULL), llm_deferred_since=since
    )

    assert isinstance(outcome, llm_capacity.LLMCapacityUnavailable)
    task.defer_async.assert_not_awaited()
    final = [e for e in logs if e["event"] == "enrichment_llm_capacity_deferral_exhausted"]
    assert len(final) == 1
    assert final[0]["log_level"] == "error"
    assert final[0]["artifact_id"] == "a1"
    assert final[0]["deferred_since"] == since


async def test_backfill_leaves_unclassified_documents_for_a_later_run():
    """Null taxonomy (ingest had no capacity) is selected by the backfill; a
    capacity failure during the backfill leaves it null instead of [] and
    re-queues the run, and the next run classifies it."""
    from qdrant_client import AsyncQdrantClient
    from qdrant_client.models import Distance, PointStruct, VectorParams

    from knowledge_ingest import taxonomy_tasks
    from knowledge_ingest.taxonomy_classifier import TaxonomyNode

    client = AsyncQdrantClient(location=":memory:")
    await client.create_collection(
        "klai_knowledge",
        vectors_config={"vector_chunk": VectorParams(size=2, distance=Distance.COSINE)},
    )
    base = {"org_id": "org-1", "kb_slug": "kb-1", "text": "body"}
    await client.upsert(
        "klai_knowledge",
        points=[
            PointStruct(
                id=1,
                vector={"vector_chunk": [0.1, 0.2]},
                payload={
                    **base,
                    "artifact_id": "a-pending",
                    "title": "Pending",
                    "content_label": None,
                    "taxonomy_node_ids": None,
                },
            ),
            PointStruct(
                id=2,
                vector={"vector_chunk": [0.2, 0.1]},
                payload={
                    **base,
                    "artifact_id": "a-done",
                    "title": "Done",
                    "content_label": ["billing"],
                    "taxonomy_node_ids": [7],
                    "tags": ["billing"],
                },
            ),
        ],
    )

    app = _FakeApp()
    taxonomy_tasks.register_taxonomy_tasks(app)
    classify = AsyncMock(side_effect=llm_capacity.LLMCapacityUnavailable("402"))

    with (
        patch("qdrant_client.AsyncQdrantClient", return_value=client),
        patch(
            "knowledge_ingest.portal_client.fetch_taxonomy_nodes",
            AsyncMock(return_value=[TaxonomyNode(7, "Billing")]),
        ),
        patch("knowledge_ingest.portal_client.invalidate_cache"),
        patch(
            "knowledge_ingest.content_labeler.generate_content_label",
            AsyncMock(return_value=["invoices"]),
        ),
        patch("knowledge_ingest.taxonomy_classifier.classify_document", classify),
        patch("knowledge_ingest.enrichment_tasks.get_app", return_value=app),
    ):
        first = await app.run_taxonomy_backfill(org_id="org-1", kb_slug="kb-1")
        (pending,) = await client.retrieve("klai_knowledge", ids=[1])
        assert pending.payload["taxonomy_node_ids"] is None
        assert first["deferred_for_llm_capacity"] is True
        configure = app.run_taxonomy_backfill.configure.call_args.kwargs
        assert configure["queueing_lock"] == "taxonomy-backfill:org-1:kb-1"
        assert "llm_deferred_since" in app.run_taxonomy_backfill.defer_async.call_args.kwargs

        classify.side_effect = None
        classify.return_value = ([(7, 0.9)], ["invoices"])
        classify.reset_mock()
        await app.run_taxonomy_backfill(org_id="org-1", kb_slug="kb-1")

    pending, done = await client.retrieve("klai_knowledge", ids=[1, 2])
    assert pending.payload["content_label"] == ["invoices"]
    assert pending.payload["taxonomy_node_ids"] == [7]
    assert done.payload["taxonomy_node_ids"] == [7]
    assert [c.kwargs["title"] for c in classify.await_args_list] == ["Pending"]
