"""Tests for SPEC-INFRA-TENANT-DELETE-002 G3.

POST /internal/v1/orgs/{org_id}/wipe-postgres

Covers:
  Test 1: 200 with per-table row counts summing to seeded count for tenant-a.
  Test 2: Cross-tenant isolation -- tenant-b rows survive after tenant-a wipe.
  Test 3: Idempotency -- second call returns all zeros.
  Test 4: Wrong/missing X-Internal-Secret returns 401.
  Test 5: Schema regression guard -- asserts every knowledge.* table
          that has an org_id column is present in the route's _LEAF_TABLES.
          Fails CI if a new table is added without updating the wipe list.

Tests 1-3 use a mocked asyncpg pool (no live DB required).
Test 5 uses information_schema introspection; skipped when POSTGRES_DSN is not set.
"""

from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from knowledge_ingest.routes.internal import _LEAF_TABLES

_ORG_A = "tenant-wipe-pg-a"
_ORG_B = "tenant-wipe-pg-b"
_SECRET = os.environ.get("KNOWLEDGE_INGEST_SECRET", "test-secret-value-123")
_ENDPOINT_A = f"/internal/v1/orgs/{_ORG_A}/wipe-postgres"
_ENDPOINT_B = f"/internal/v1/orgs/{_ORG_B}/wipe-postgres"

# All tables the route is expected to wipe (leaf tables + artifacts).
_EXPECTED_WIPE_TABLES = set(_LEAF_TABLES) | {"artifacts"}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_conn_mock(per_table_counts: dict) -> MagicMock:
    """Return a mock asyncpg connection whose execute() simulates row counts."""
    conn = MagicMock()

    async def _execute(query: str, *args, **kwargs) -> str:
        if "superseded_by" in query:
            return "UPDATE 0"
        parts = query.split()
        table_full = parts[2] if len(parts) > 2 else ""
        table = table_full.split(".")[-1] if "." in table_full else table_full
        count = per_table_counts.get(table, 0)
        return f"DELETE {count}"

    conn.execute = _execute
    tx = MagicMock()
    tx.__aenter__ = AsyncMock(return_value=None)
    tx.__aexit__ = AsyncMock(return_value=False)
    conn.transaction = MagicMock(return_value=tx)
    return conn


def _make_pool_mock(conn_mock: MagicMock) -> MagicMock:
    """Return a mock asyncpg pool that yields conn_mock via acquire()."""
    pool = MagicMock()
    acquire_ctx = MagicMock()
    acquire_ctx.__aenter__ = AsyncMock(return_value=conn_mock)
    acquire_ctx.__aexit__ = AsyncMock(return_value=False)
    pool.acquire = MagicMock(return_value=acquire_ctx)
    pool.close = AsyncMock(return_value=None)
    return pool


@pytest.fixture
def wipe_pg_client():
    """TestClient with the full app, DB and Qdrant mocked out."""
    mock_pool_outer = MagicMock()
    mock_pool_outer.close = AsyncMock(return_value=None)

    with (
        patch("knowledge_ingest.qdrant_store.ensure_collection", new_callable=AsyncMock),
        patch(
            "knowledge_ingest.db.get_pool",
            new_callable=AsyncMock,
            return_value=mock_pool_outer,
        ),
        patch("knowledge_ingest.db.close_pool", new_callable=AsyncMock),
        patch("knowledge_ingest.config.settings.enrichment_enabled", False),
    ):
        from fastapi.testclient import TestClient

        from knowledge_ingest.app import app

        with TestClient(app, raise_server_exceptions=False) as c:
            yield c


# ---------------------------------------------------------------------------
# Test 1: 200 with correct per-table row counts
# ---------------------------------------------------------------------------


class TestWipePostgresHappyPath:
    def test_200_returns_per_table_counts(self, wipe_pg_client) -> None:
        seeded = {
            "page_links": 3,
            "crawled_pages": 5,
            "crawl_jobs": 2,
            "crawl_domains": 1,
            "kb_config": 1,
            "org_config": 1,
            "entities": 4,
            "artifacts": 7,
        }
        conn_mock = _make_conn_mock(seeded)
        pool_mock = _make_pool_mock(conn_mock)

        with patch(
            "knowledge_ingest.routes.internal.get_pool",
            new_callable=AsyncMock,
            return_value=pool_mock,
        ):
            resp = wipe_pg_client.post(
                _ENDPOINT_A,
                headers={"X-Internal-Secret": _SECRET},
            )

        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        rows = body["rows_deleted"]
        assert rows["artifacts"] == 7
        assert rows["page_links"] == 3
        assert rows["crawled_pages"] == 5
        assert rows["crawl_jobs"] == 2
        assert rows["crawl_domains"] == 1
        assert rows["kb_config"] == 1
        assert rows["org_config"] == 1
        assert rows["entities"] == 4
        assert set(rows.keys()) == _EXPECTED_WIPE_TABLES

    def test_200_status_ok_field_present(self, wipe_pg_client) -> None:
        conn_mock = _make_conn_mock({})
        pool_mock = _make_pool_mock(conn_mock)
        with patch(
            "knowledge_ingest.routes.internal.get_pool",
            new_callable=AsyncMock,
            return_value=pool_mock,
        ):
            resp = wipe_pg_client.post(
                _ENDPOINT_A,
                headers={"X-Internal-Secret": _SECRET},
            )
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"


# ---------------------------------------------------------------------------
# Test 2: Cross-tenant isolation
# ---------------------------------------------------------------------------


class TestWipePostgresTenantIsolation:
    def test_only_target_org_rows_deleted(self, wipe_pg_client) -> None:
        """Verify the DELETE uses org_id=$1 -- tenant-b data must survive."""
        executed_calls = []

        conn = MagicMock()

        async def _tracking_execute(query: str, *args, **kwargs) -> str:
            executed_calls.append((query, args))
            if "superseded_by" in query:
                return "UPDATE 0"
            parts = query.split()
            table_full = parts[2] if len(parts) > 2 else ""
            table = table_full.split(".")[-1] if "." in table_full else table_full
            return "DELETE 2" if table in _EXPECTED_WIPE_TABLES else "DELETE 0"

        conn.execute = _tracking_execute
        tx = MagicMock()
        tx.__aenter__ = AsyncMock(return_value=None)
        tx.__aexit__ = AsyncMock(return_value=False)
        conn.transaction = MagicMock(return_value=tx)
        pool_mock = _make_pool_mock(conn)

        with patch(
            "knowledge_ingest.routes.internal.get_pool",
            new_callable=AsyncMock,
            return_value=pool_mock,
        ):
            resp = wipe_pg_client.post(
                _ENDPOINT_A,
                headers={"X-Internal-Secret": _SECRET},
            )

        assert resp.status_code == 200

        for _query, args in executed_calls:
            assert args, f"Expected org_id bind param in: {_query}"
            assert args[0] == _ORG_A, (
                f"Expected org_id={_ORG_A!r} but got {args[0]!r} in query: {_query}"
            )
            assert _ORG_B not in args, f"Tenant-B org_id leaked into query: {_query}"


# ---------------------------------------------------------------------------
# Test 3: Idempotency
# ---------------------------------------------------------------------------


class TestWipePostgresIdempotency:
    def test_second_call_returns_all_zeros(self, wipe_pg_client) -> None:
        first_seeded = {t: 2 for t in _EXPECTED_WIPE_TABLES}
        conn_first = _make_conn_mock(first_seeded)
        pool_first = _make_pool_mock(conn_first)

        conn_second = _make_conn_mock({})
        pool_second = _make_pool_mock(conn_second)

        pools = iter([pool_first, pool_second])

        async def _rotating_get_pool():
            return next(pools)

        with patch(
            "knowledge_ingest.routes.internal.get_pool",
            side_effect=_rotating_get_pool,
        ):
            resp1 = wipe_pg_client.post(_ENDPOINT_A, headers={"X-Internal-Secret": _SECRET})
            resp2 = wipe_pg_client.post(_ENDPOINT_A, headers={"X-Internal-Secret": _SECRET})

        assert resp1.status_code == 200
        assert resp2.status_code == 200

        rows2 = resp2.json()["rows_deleted"]
        assert all(v == 0 for v in rows2.values()), (
            f"Expected all zeros on second call, got: {rows2}"
        )


# ---------------------------------------------------------------------------
# Test 4: Auth
# ---------------------------------------------------------------------------


class TestWipePostgresAuth:
    def test_401_without_secret_header(self, wipe_pg_client) -> None:
        resp = wipe_pg_client.post(_ENDPOINT_A)
        assert resp.status_code == 401

    def test_401_with_wrong_secret(self, wipe_pg_client) -> None:
        resp = wipe_pg_client.post(
            _ENDPOINT_A,
            headers={"X-Internal-Secret": "wrong-secret-xyz"},
        )
        assert resp.status_code == 401

    def test_200_with_correct_secret(self, wipe_pg_client) -> None:
        conn_mock = _make_conn_mock({})
        pool_mock = _make_pool_mock(conn_mock)
        with patch(
            "knowledge_ingest.routes.internal.get_pool",
            new_callable=AsyncMock,
            return_value=pool_mock,
        ):
            resp = wipe_pg_client.post(
                _ENDPOINT_A,
                headers={"X-Internal-Secret": _SECRET},
            )
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Test 5: Schema regression guard (live DB, skipped without POSTGRES_DSN)
# ---------------------------------------------------------------------------

_pg_available = bool(os.environ.get("POSTGRES_DSN"))


@pytest.mark.skipif(not _pg_available, reason="No POSTGRES_DSN set -- skipping live DB test")
class TestWipePostgresSchemaGuard:
    """Regression guard: fails CI if a new knowledge.* table with org_id is added
    without also being added to the wipe list in routes/internal.py."""

    @pytest.mark.asyncio
    async def test_all_org_id_tables_are_in_wipe_list(self) -> None:
        import asyncpg

        dsn = os.environ["POSTGRES_DSN"]
        conn = await asyncpg.connect(dsn)
        try:
            rows = await conn.fetch(
                """
                SELECT table_name
                FROM information_schema.columns
                WHERE table_schema = 'knowledge'
                  AND column_name = 'org_id'
                ORDER BY table_name
                """
            )
        finally:
            await conn.close()

        live_tables = {row["table_name"] for row in rows}
        missing = live_tables - _EXPECTED_WIPE_TABLES
        assert not missing, (
            f"The following knowledge.* tables have an org_id column but are NOT "
            f"in the wipe list (_LEAF_TABLES + artifacts): {sorted(missing)}. "
            f"Add them to _LEAF_TABLES in knowledge_ingest/routes/internal.py "
            f"(or document why they are intentionally excluded)."
        )
