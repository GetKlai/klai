"""The store's source_refs of one connector, read by klai-connector's deletion diff."""

from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from knowledge_ingest import pg_store


def _get(client, **params):
    base = {"org_id": "org-1", "kb_slug": "kb-a", "connector_id": "connector-1"}
    return client.get(
        "/ingest/v1/connector/source-refs",
        params={**base, **params},
        headers={"X-Caller-Service": "connector"},
    )


def _patched(refs: list[str]):
    conn = MagicMock()

    @asynccontextmanager
    async def _tenant_connection(org_id: str):
        assert org_id == "org-verified"
        yield conn

    identity = patch(
        "knowledge_ingest.routes.stats.assert_caller_identity_tenant_only",
        new_callable=AsyncMock,
        return_value="org-verified",
    )
    tenant = patch("knowledge_ingest.routes.stats.tenant_scoped_connection", _tenant_connection)
    query = patch(
        "knowledge_ingest.routes.stats.pg_store.list_connector_source_refs",
        new_callable=AsyncMock,
        return_value=refs,
    )
    return conn, identity, tenant, query


def test_refs_are_read_for_the_verified_org_kb_and_connector(client) -> None:
    rows = [
        {"source_ref": "ref-a", "paths": ["docs/a.md"]},
        {"source_ref": "ref-b", "paths": ["docs/b.md", "docs/b-old.md"]},
    ]
    conn, identity, tenant, query = _patched(rows)
    with identity as asserted, tenant, query as listed:
        response = _get(client, after="ref-0", limit=2)

    assert response.status_code == 200
    assert response.json() == {"source_refs": rows, "next_after": "ref-b"}
    assert asserted.await_args.kwargs == {"claimed_org_id": "org-1"}
    listed.assert_awaited_once_with(conn, "org-verified", "kb-a", "connector-1", "ref-0", 2)


def test_short_page_ends_the_listing(client) -> None:
    rows = [{"source_ref": "ref-a", "paths": ["docs/a.md"]}]
    _conn, identity, tenant, query = _patched(rows)
    with identity, tenant, query:
        response = _get(client, limit=2)

    assert response.json() == {"source_refs": rows, "next_after": None}


def test_store_error_is_not_answered_as_an_empty_store(client) -> None:
    """An empty 200 would read as "nothing stored"; the caller must see the failure."""
    _conn, identity, tenant, query = _patched([])
    with identity, tenant, query as listed:
        listed.side_effect = RuntimeError("pool exhausted")
        response = _get(client)

    assert response.status_code == 500


def test_limit_is_bounded(client) -> None:
    assert _get(client, limit=5001).status_code == 422


@pytest.mark.asyncio
async def test_query_is_scoped_and_keyset_paginated() -> None:
    conn = MagicMock()
    conn.fetch = AsyncMock(return_value=[{"source_ref": "ref-b", "paths": ["docs/b.md"]}])

    refs = await pg_store.list_connector_source_refs(
        conn, "org-1", "kb-a", "connector-1", "ref-a", 10
    )

    assert refs == [{"source_ref": "ref-b", "paths": ["docs/b.md"]}]
    sql, *args = conn.fetch.await_args.args
    assert args == ["org-1", "kb-a", "connector-1", "ref-a", 10]
    for clause in (
        "org_id = $1",
        "kb_slug = $2",
        "extra::jsonb->>'source_connector_id' = $3",
        "extra::jsonb->>'source_ref' > $4",
        "array_agg(DISTINCT path",
        "GROUP BY 1",
        "ORDER BY 1",
        "LIMIT $5",
    ):
        assert clause in sql
    assert "belief_time_end" not in sql
