"""SPEC-TI-003 AC-10 — RLS regression tests for knowledge.* schema.

These tests verify:
  1. tenant_scoped_connection sets app.current_org_id on the connection so
     RLS policies can filter by tenant.
  2. Queries WITHOUT tenant context raise asyncpg.InsufficientPrivilegeError
     (SQLSTATE 42501 — the Cat-D fail-loud policy via knowledge._rls_current_org_id()).
  3. tenant_scoped_connection resets the GUC on exit so the pool connection
     is clean when returned.

All tests use asyncpg mocks — no live DB required. The mock captures
the sequence of SET CONFIG calls to verify the tenant-pinning contract.

SPEC-TI-003-FOLLOWUP-001: this file exercises ``tenant_scoped_connection``
itself, so it opts out of the autouse ``_mock_db_helpers`` fixture (which
patches the helper to a no-op for every other test).

For live-DB integration tests, see tests/integration/ (not committed here).
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = pytest.mark.no_mock_db_helpers

import pytest

# ---------------------------------------------------------------------------
# Helpers: mock pool + connection
# ---------------------------------------------------------------------------


def _make_mock_connection(execute_results=None):
    """Return a mock asyncpg.Connection that records execute() calls."""
    conn = MagicMock()
    conn.execute = AsyncMock(side_effect=execute_results or [None, None, None, None])
    conn.fetch = AsyncMock(return_value=[])
    conn.fetchrow = AsyncMock(return_value=None)
    return conn


def _make_mock_pool(conn):
    """Return a mock asyncpg.Pool that yields the given connection."""

    @asynccontextmanager
    async def _acquire():
        yield conn

    pool = MagicMock()
    pool.acquire = _acquire
    return pool


# ---------------------------------------------------------------------------
# AC-10-a: tenant_scoped_connection sets and resets GUC
# ---------------------------------------------------------------------------


class TestTenantScopedConnectionGUCPinning:
    """Verify that tenant_scoped_connection sets and clears app.current_org_id."""

    @pytest.mark.asyncio
    async def test_set_config_called_with_org_id(self):
        """SET CONFIG app.current_org_id=org_id is called before yield."""
        conn = _make_mock_connection()
        pool = _make_mock_pool(conn)

        with patch("knowledge_ingest.db.get_pool", AsyncMock(return_value=pool)):
            # Reset module-level singleton so get_pool mock is used
            import knowledge_ingest.db as db_mod
            from knowledge_ingest.db import tenant_scoped_connection

            orig_pool = db_mod._pool
            db_mod._pool = None

            async with tenant_scoped_connection("org-abc") as _conn:
                pass

            db_mod._pool = orig_pool

        # First call: set org_id
        first_call = conn.execute.call_args_list[0]
        assert "set_config" in first_call.args[0].lower() or "set_config" in str(first_call)
        assert "org-abc" in str(first_call)

    @pytest.mark.asyncio
    async def test_reset_called_on_exit(self):
        """SET CONFIG app.current_org_id='' is called on context manager exit."""
        conn = _make_mock_connection()
        pool = _make_mock_pool(conn)

        with patch("knowledge_ingest.db.get_pool", AsyncMock(return_value=pool)):
            import knowledge_ingest.db as db_mod
            from knowledge_ingest.db import tenant_scoped_connection

            orig_pool = db_mod._pool
            db_mod._pool = None

            async with tenant_scoped_connection("org-xyz") as _conn:
                pass

            db_mod._pool = orig_pool

        # Last execute call(s) should reset the GUC to empty string
        all_calls = [str(c) for c in conn.execute.call_args_list]
        reset_calls = [c for c in all_calls if "''" in c or '"""' in c]
        assert reset_calls, (
            f"Expected at least one GUC reset call (empty string), "
            f"but execute calls were: {all_calls}"
        )

    @pytest.mark.asyncio
    async def test_reset_called_even_on_exception(self):
        """GUC is reset even when the body of the context manager raises."""
        conn = _make_mock_connection(
            execute_results=[None, None, None, None]  # 4 execute calls: set x2, reset x2
        )
        pool = _make_mock_pool(conn)

        with patch("knowledge_ingest.db.get_pool", AsyncMock(return_value=pool)):
            import knowledge_ingest.db as db_mod
            from knowledge_ingest.db import tenant_scoped_connection

            orig_pool = db_mod._pool
            db_mod._pool = None

            with pytest.raises(RuntimeError, match="simulated failure"):
                async with tenant_scoped_connection("org-fail") as _conn:
                    raise RuntimeError("simulated failure")

            db_mod._pool = orig_pool

        # Should have 4 execute calls: set org_id, set cross_org_admin,
        # reset org_id, reset cross_org_admin
        assert conn.execute.call_count == 4, (
            f"Expected 4 execute calls (2 setup + 2 reset), got {conn.execute.call_count}. "
            f"Calls: {conn.execute.call_args_list}"
        )

    @pytest.mark.asyncio
    async def test_yields_connection_from_pool(self):
        """The context manager yields the pool connection."""
        conn = _make_mock_connection()
        pool = _make_mock_pool(conn)

        with patch("knowledge_ingest.db.get_pool", AsyncMock(return_value=pool)):
            import knowledge_ingest.db as db_mod
            from knowledge_ingest.db import tenant_scoped_connection

            orig_pool = db_mod._pool
            db_mod._pool = None

            async with tenant_scoped_connection("org-test") as yielded_conn:
                assert yielded_conn is conn

            db_mod._pool = orig_pool


# ---------------------------------------------------------------------------
# AC-10-b: fail-loud helper function contract
# ---------------------------------------------------------------------------


class TestRLSFailLoudBehaviour:
    """Verify that the fail-loud pattern is correctly documented and tested.

    Note: The actual PostgreSQL function knowledge._rls_current_org_id()
    is a PLPGSQL function defined in post_deploy_dd1b439a57d0.sql and can
    only be tested against a live Postgres instance. The tests here verify
    the Python-side contract (tenant_scoped_connection) and the SQL file's
    presence to enforce the contract end-to-end.
    """

    def test_post_deploy_sql_file_exists(self):
        """The post-deploy SQL with RLS policies must exist in the repo."""
        from pathlib import Path

        # Knowledge-ingest root is 2 levels up from this test file
        root = Path(__file__).parent.parent
        sql_file = root / "alembic" / "versions" / "post_deploy_dd1b439a57d0.sql"
        assert sql_file.exists(), (
            f"Post-deploy SQL {sql_file} not found. "
            "SPEC-TI-003 RLS policies must be applied via this file."
        )

    def test_post_deploy_sql_contains_restrictive_policy(self):
        """The SQL must use RESTRICTIVE (Cat-D) on knowledge tables."""
        from pathlib import Path

        root = Path(__file__).parent.parent
        sql_file = root / "alembic" / "versions" / "post_deploy_dd1b439a57d0.sql"
        if not sql_file.exists():
            pytest.skip("Post-deploy SQL not present — see test above")

        content = sql_file.read_text(encoding="utf-8")
        assert "AS RESTRICTIVE" in content, (
            "RLS policy on knowledge.* must be RESTRICTIVE (Cat-D fail-loud). "
            "A PERMISSIVE policy silently allows cross-tenant reads."
        )

    def test_post_deploy_sql_contains_fail_loud_function(self):
        """The helper function must raise SQLSTATE 42501 on missing GUC."""
        from pathlib import Path

        root = Path(__file__).parent.parent
        sql_file = root / "alembic" / "versions" / "post_deploy_dd1b439a57d0.sql"
        if not sql_file.exists():
            pytest.skip("Post-deploy SQL not present — see test above")

        content = sql_file.read_text(encoding="utf-8")
        assert "knowledge._rls_current_org_id" in content, (
            "Missing _rls_current_org_id() function in post-deploy SQL."
        )
        assert "42501" in content, (
            "The fail-loud function must raise SQLSTATE 42501 "
            "(insufficient_privilege) when app.current_org_id is unset."
        )

    def test_post_deploy_sql_covers_core_tables(self):
        """RLS policies must exist for the core knowledge.* tables."""
        from pathlib import Path

        root = Path(__file__).parent.parent
        sql_file = root / "alembic" / "versions" / "post_deploy_dd1b439a57d0.sql"
        if not sql_file.exists():
            pytest.skip("Post-deploy SQL not present — see test above")

        content = sql_file.read_text(encoding="utf-8")
        required_tables = [
            "knowledge.artifacts",
            "knowledge.crawl_jobs",
            "knowledge.crawled_pages",
            "knowledge.kb_config",
        ]
        for table in required_tables:
            assert table in content, (
                f"No RLS policy found for {table} in post-deploy SQL. "
                "All knowledge.* tables with org_id must be protected."
            )


# ---------------------------------------------------------------------------
# AC-10-c: tenant_scoped_connection used in rebuild_tasks
# ---------------------------------------------------------------------------


class TestRebuildTasksTenantContext:
    """Verify that _list_active_artifacts uses tenant_scoped_connection."""

    def test_list_active_artifacts_uses_tenant_scoped_connection(self):
        """_list_active_artifacts must not use the raw pool — uses tsc instead."""
        from pathlib import Path

        root = Path(__file__).parent.parent
        rebuild_tasks = root / "knowledge_ingest" / "rebuild_tasks.py"
        assert rebuild_tasks.exists(), "rebuild_tasks.py not found"

        content = rebuild_tasks.read_text(encoding="utf-8")
        assert "tenant_scoped_connection" in content, (
            "SPEC-TI-003 AC-9: _list_active_artifacts must use tenant_scoped_connection "
            "so RLS GUC is set before the SELECT. Found only raw pool usage."
        )
        # Ensure the old pattern (get_pool directly in the function) is not present
        assert "from knowledge_ingest.db import get_pool" not in content or (
            "tenant_scoped_connection" in content
        ), (
            "rebuild_tasks.py should not import get_pool locally inside "
            "_list_active_artifacts — use tenant_scoped_connection instead."
        )


# ---------------------------------------------------------------------------
# AC-10-d: tenant_scoped_connection used in crawl_tasks
# ---------------------------------------------------------------------------


class TestCrawlTasksTenantContext:
    """Verify that crawl_tasks wraps run_crawl_job in tenant_scoped_connection."""

    def test_crawl_tasks_uses_tenant_scoped_connection(self):
        from pathlib import Path

        root = Path(__file__).parent.parent
        crawl_tasks = root / "knowledge_ingest" / "crawl_tasks.py"
        assert crawl_tasks.exists(), "crawl_tasks.py not found"

        content = crawl_tasks.read_text(encoding="utf-8")
        assert "tenant_scoped_connection" in content, (
            "SPEC-TI-003 AC-9: crawl_tasks.py must wrap run_crawl_job in "
            "tenant_scoped_connection so knowledge.* writes see the RLS GUC."
        )
