"""The KB item quota must count items the user still has.

``knowledge.artifacts`` closes a deleted row by setting ``belief_time_end``,
and the canonical "still active" test in this codebase is
``belief_time_end = _SENTINEL`` (``pg_store``, ``rebuild_tasks``,
``list_kb_sources``). ``get_source_count`` feeds portal-api's
``max_items_per_kb`` check, so a predicate that misses deleted rows turns
every upload-then-delete into permanent quota loss on a KB that reads as
empty.

Shape assertions rather than rows: knowledge-ingest has no real-Postgres
harness, so this pins the predicate, not the result set.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock

import pytest

from knowledge_ingest.pg_store import _SENTINEL

_CALLER = {"X-Caller-Service": "portal-api"}


@pytest.fixture
def captured(monkeypatch):
    """Run the endpoint against a connection that records the count query."""
    seen: dict = {}
    conn = MagicMock()

    async def _fetchval(query, *args):
        seen["query"] = query
        seen["args"] = args
        return 7

    conn.fetchval = AsyncMock(side_effect=_fetchval)

    @asynccontextmanager
    async def _tenant_scoped(org_id):
        yield conn

    monkeypatch.setattr("knowledge_ingest.routes.stats.tenant_scoped_connection", _tenant_scoped)

    async def _asserter(request, *, claimed_org_id):
        return claimed_org_id

    monkeypatch.setattr(
        "knowledge_ingest.routes.stats.assert_caller_identity_tenant_only", _asserter
    )
    return seen


def _count(client, captured):
    resp = client.get(
        "/ingest/v1/source-count",
        headers=_CALLER,
        params={"org_id": "org-1", "kb_slug": "personal-1"},
    )
    assert resp.status_code == 200, resp.text
    # Guards the fail-open branch: a broken harness would return null here and
    # make the predicate assertions below vacuous.
    assert resp.json()["source_count"] == 7, resp.text
    return captured


def test_a_deleted_artifact_does_not_consume_quota(client, captured):
    seen = _count(client, captured)
    assert "belief_time_end" in seen["query"], (
        "the item quota counts rows that are not restricted to active "
        "artifacts, so deleting a source does not free its slot"
    )
    assert _SENTINEL in seen["args"], (
        "the active-artifact sentinel must be bound as a parameter, matching "
        "list_kb_sources and rebuild_tasks"
    )


def test_the_count_is_still_scoped_to_one_tenant_and_kb(client, captured):
    seen = _count(client, captured)
    assert "org_id = $1" in seen["query"]
    assert "kb_slug = $2" in seen["query"]
    assert "org-1" in seen["args"] and "personal-1" in seen["args"]
