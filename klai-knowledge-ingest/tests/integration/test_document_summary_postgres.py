"""The document summary is computed once per document version and kept on it.

Before this, every enrichment run (a retry, each further batch of a large
document, a knowledge-base rebuild) paid for a new summary call, because the
summary was only ever set on an in-memory dict. These tests run the real job
functions against a real Postgres; only the model calls, the embedders and the
vector store are replaced. Needs ``GRAPH_REFRESH_BUDGET_TEST_DSN``.
"""

from __future__ import annotations

import json
import os
import uuid
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, patch

import asyncpg
import pytest

from knowledge_ingest import contextual, enrichment_tasks, rebuild_tasks

_DSN = os.environ.get("GRAPH_REFRESH_BUDGET_TEST_DSN")
_ACTIVE = 253402300800
_TEXT = "# Opening hours\n\nThe example office opens at nine and closes at five on weekdays."

pytestmark = [
    pytest.mark.postgres,
    pytest.mark.no_mock_db_helpers,
    pytest.mark.skipif(not _DSN, reason="GRAPH_REFRESH_BUDGET_TEST_DSN is not configured"),
]


@pytest.fixture
async def db(monkeypatch: pytest.MonkeyPatch):
    assert _DSN is not None
    admin = await asyncpg.connect(_DSN)
    orgs: list[str] = []

    @asynccontextmanager
    async def _tenant(org_id: str):
        conn = await asyncpg.connect(_DSN)
        await conn.execute("SELECT set_config('app.current_org_id', $1, false)", org_id)
        try:
            yield conn
        finally:
            await conn.close()

    @asynccontextmanager
    async def _admin():
        yield admin

    for module in (enrichment_tasks, rebuild_tasks):
        monkeypatch.setattr(module, "tenant_scoped_connection", _tenant)
    monkeypatch.setattr(enrichment_tasks, "cross_org_admin_connection", _admin)

    async def insert(org_id: str, *, active: bool = True) -> str:
        orgs.append(org_id)
        artifact_id = str(uuid.uuid4())
        await admin.execute(
            """
            INSERT INTO knowledge.artifacts (
                id, org_id, provenance_type, assertion_mode, belief_time_start,
                belief_time_end, created_at, kb_slug, path, extra
            ) VALUES ($1, $2, 'observed', 'factual', 1, $3, 1, 'docs', 'hours.md', $4::jsonb)
            """,
            artifact_id,
            org_id,
            _ACTIVE if active else 2,
            json.dumps({"document_text": _TEXT, "title": "Opening hours"}),
        )
        return artifact_id

    async def extra(artifact_id: str) -> dict:
        raw = await admin.fetchval(
            "SELECT extra::text FROM knowledge.artifacts WHERE id = $1", artifact_id
        )
        return json.loads(raw)

    try:
        yield insert, extra, admin
    finally:
        for org_id in orgs:
            await admin.execute("DELETE FROM knowledge.artifacts WHERE org_id = $1", org_id)
        await admin.close()


@pytest.fixture
def model():
    """The two model calls of an enrichment run: the summary, then one per chunk."""
    summary = AsyncMock(side_effect=lambda **kw: f"Summary {summary.await_count}")
    seen: list[str | None] = []
    chunk_errors: list[Exception] = []

    async def _chunks(**kw):
        seen.append(kw["document_summary"])
        if chunk_errors:
            raise chunk_errors.pop()
        return [enrichment_tasks.enrichment.EnrichedChunk(c, c, "", []) for c in kw["chunks"]]

    with (
        patch.object(contextual, "generate_document_summary", summary),
        patch.object(enrichment_tasks.enrichment, "enrich_chunks", _chunks),
        patch.object(
            enrichment_tasks.embedder, "embed", AsyncMock(side_effect=lambda t: [[0.1]] * len(t))
        ),
        patch.object(
            enrichment_tasks.sparse_embedder,
            "embed_sparse_batch",
            AsyncMock(side_effect=lambda t: [None] * len(t)),
        ),
        patch.object(enrichment_tasks.qdrant_store, "upsert_enriched_chunks", AsyncMock()),
        patch.object(
            enrichment_tasks.qdrant_store, "has_chunks_from", AsyncMock(return_value=False)
        ),
        patch.object(
            enrichment_tasks.qdrant_store,
            "read_chunk_batch",
            AsyncMock(return_value=[{"text": "A later chunk.", "heading_path": ""}]),
        ),
        patch.object(
            enrichment_tasks.kb_config, "get_kb_visibility", AsyncMock(return_value="internal")
        ),
        patch.object(enrichment_tasks.kb_config, "get_kb_aliases", AsyncMock(return_value=[])),
        patch.object(
            enrichment_tasks.kb_config, "get_kb_product_rules", AsyncMock(return_value=[])
        ),
    ):
        yield summary, seen, chunk_errors


async def test_a_retry_a_later_batch_and_a_rebuild_reuse_the_versions_summary(db, model) -> None:
    insert, extra, _admin = db
    summary, seen, chunk_errors = model
    org_id = f"org-summary-{uuid.uuid4().hex[:8]}"
    version = await insert(org_id)

    chunk_errors.append(enrichment_tasks.enrichment.EnrichmentError("provider 500"))
    with pytest.raises(enrichment_tasks.enrichment.EnrichmentError):
        await enrichment_tasks._load_and_enrich(version)
    await enrichment_tasks._load_and_enrich(version)  # the retry
    await enrichment_tasks._load_and_enrich(version, batch_offset=200)  # a later batch
    await rebuild_tasks._rebuild_kb_core(org_id, "docs")

    assert summary.await_count == 1
    assert seen == ["Summary 1"] * 4
    stored = await extra(version)
    assert stored["document_summary"] == "Summary 1"
    assert stored["document_language"] == "en"


async def test_a_new_version_and_another_tenants_version_compute_their_own(db, model) -> None:
    insert, extra, admin = db
    summary, seen, _ = model
    org_id = f"org-summary-{uuid.uuid4().hex[:8]}"
    other_org = f"org-summary-{uuid.uuid4().hex[:8]}"
    old = await insert(org_id)
    await enrichment_tasks._load_and_enrich(old)

    await admin.execute("UPDATE knowledge.artifacts SET belief_time_end = 2 WHERE id = $1", old)
    new = await insert(org_id)
    await enrichment_tasks._load_and_enrich(new)
    other = await insert(other_org)  # same text, path and KB name, other tenant
    await enrichment_tasks._load_and_enrich(other)

    assert summary.await_count == 3
    assert seen == ["Summary 1", "Summary 2", "Summary 3"]
    assert [(await extra(a))["document_summary"] for a in (old, new, other)] == [
        "Summary 1",
        "Summary 2",
        "Summary 3",
    ]
