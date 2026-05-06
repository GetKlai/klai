"""Backfill task tests for SPEC-INGEST-LOGIN-WALL-DETECT-001 Phase D.

REQ-06 acceptance criteria:
- AC-06.1: detect + delete + mark placeholder hash for matching pages.
- AC-06.2: idempotent — second run with no new pages does nothing.
- AC-06.3: tenant isolation — voys backfill never touches getklai.

REQ-09 acceptance criteria:
- AC-09.1: Qdrant delete filter MUST contain org_id + kb_slug + path
  (otherwise the cross-tenant-isolation semgrep rule blocks merge).
- AC-09.2: SELECT/UPDATE on knowledge.* go through tenant_scoped_connection.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from knowledge_ingest.backfill_tasks import (
    PURGED_PLACEHOLDER_HASH,
    backfill_detect_login_walls,
)

FIXTURES = Path(__file__).parent / "fixtures"


def _walled_md() -> str:
    return (FIXTURES / "auth_walls" / "redcactus_hubspot.md").read_text(encoding="utf-8")


def _clean_md() -> str:
    return (FIXTURES / "clean_pages" / "redcactus_ifttt.md").read_text(encoding="utf-8")


def _make_conn(rows: list[dict]):
    """Build an asyncpg-like connection mock that returns ``rows`` from
    ``fetch`` and records ``execute`` calls."""
    conn = MagicMock()

    # asyncpg row records support __getitem__ and dict-like .get
    async def fetch_side_effect(sql: str, *args):
        # Filter by content_hash != placeholder for idempotency tests; the
        # actual SQL filters in production code, but tests provide rows
        # already filtered.
        return rows

    conn.fetch = AsyncMock(side_effect=fetch_side_effect)
    conn.execute = AsyncMock(return_value=None)
    return conn


def _make_qdrant():
    """Build a Qdrant-client mock and capture delete() calls."""
    client = MagicMock()
    client.delete = AsyncMock(return_value=None)
    return client


@pytest.fixture()
def patched_externals():
    """Patches tenant_scoped_connection + Qdrant client.

    Returns (conn, qdrant_client, set_rows) where set_rows is a callable
    used to seed the rows fetched from crawled_pages.
    """
    rows: list[dict] = []
    conn = _make_conn(rows)
    qdrant = _make_qdrant()

    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def fake_scope(org_id: str):
        yield conn

    patches = [
        patch(
            "knowledge_ingest.backfill_tasks.tenant_scoped_connection",
            new=fake_scope,
        ),
        patch(
            "knowledge_ingest.backfill_tasks.get_qdrant_client",
            return_value=qdrant,
        ),
    ]
    for p in patches:
        p.start()
    try:

        def set_rows(new_rows: list[dict]) -> None:
            rows.clear()
            rows.extend(new_rows)

        yield conn, qdrant, set_rows
    finally:
        for p in patches:
            p.stop()


# ---------------------------------------------------------------------------
# AC-06.1: detect + delete + mark
# ---------------------------------------------------------------------------


class TestDetectAndPurge:
    @pytest.mark.asyncio()
    async def test_walled_page_purged_and_marked(self, patched_externals) -> None:
        conn, qdrant, set_rows = patched_externals
        set_rows(
            [
                {
                    "url": "https://wiki.redcactus.cloud/nl/crm-software/HubSpot",
                    "raw_markdown": _walled_md(),
                    "content_hash": "abc123",
                },
            ]
        )

        result = await backfill_detect_login_walls(
            org_id="368884765035593759",
            kb_slug="support",
        )

        assert result == {"processed": 1, "flagged": 1, "qdrant_deleted": 1}
        # Qdrant delete called once with proper tenant filter.
        qdrant.delete.assert_awaited_once()
        kwargs = qdrant.delete.await_args.kwargs
        # The filter param goes via points_selector keyword.
        selector = kwargs.get("points_selector") or qdrant.delete.await_args.args[1]
        # selector is a Filter object — check str form contains all 3 conditions.
        sel_str = str(selector)
        assert "368884765035593759" in sel_str
        assert "support" in sel_str
        assert "wiki.redcactus.cloud/nl/crm-software/HubSpot" in sel_str
        # Placeholder hash written.
        update_calls = [
            c
            for c in conn.execute.await_args_list
            if c.args and "UPDATE knowledge.crawled_pages" in c.args[0]
        ]
        assert len(update_calls) == 1
        assert PURGED_PLACEHOLDER_HASH in update_calls[0].args

    @pytest.mark.asyncio()
    async def test_clean_page_untouched(self, patched_externals) -> None:
        conn, qdrant, set_rows = patched_externals
        set_rows(
            [
                {
                    "url": "https://wiki.redcactus.cloud/nl/crm-software/IFTTT",
                    "raw_markdown": _clean_md(),
                    "content_hash": "abc123",
                },
            ]
        )

        result = await backfill_detect_login_walls(org_id="368884765035593759", kb_slug="support")

        assert result == {"processed": 1, "flagged": 0, "qdrant_deleted": 0}
        qdrant.delete.assert_not_called()
        # No UPDATE on crawled_pages either.
        update_calls = [
            c
            for c in conn.execute.await_args_list
            if c.args and "UPDATE knowledge.crawled_pages" in c.args[0]
        ]
        assert update_calls == []

    @pytest.mark.asyncio()
    async def test_mixed_batch(self, patched_externals) -> None:
        _conn, qdrant, set_rows = patched_externals
        set_rows(
            [
                {
                    "url": "https://example.com/clean1",
                    "raw_markdown": _clean_md(),
                    "content_hash": "h1",
                },
                {
                    "url": "https://example.com/walled1",
                    "raw_markdown": _walled_md(),
                    "content_hash": "h2",
                },
                {
                    "url": "https://example.com/clean2",
                    "raw_markdown": _clean_md(),
                    "content_hash": "h3",
                },
                {
                    "url": "https://example.com/walled2",
                    "raw_markdown": _walled_md(),
                    "content_hash": "h4",
                },
            ]
        )

        result = await backfill_detect_login_walls(org_id="368884765035593759", kb_slug="support")

        assert result == {"processed": 4, "flagged": 2, "qdrant_deleted": 2}
        assert qdrant.delete.await_count == 2


# ---------------------------------------------------------------------------
# AC-06.2: idempotency
# ---------------------------------------------------------------------------


class TestIdempotency:
    @pytest.mark.asyncio()
    async def test_already_purged_pages_skipped(self, patched_externals) -> None:
        """The fetch helper filters out content_hash=PURGED_PLACEHOLDER_HASH
        rows (the SQL pre-filter). Verify that when ALL rows are already
        purged, the task is a no-op."""
        conn, qdrant, set_rows = patched_externals
        # Empty result — represents "all matching rows already purged".
        set_rows([])

        result = await backfill_detect_login_walls(org_id="368884765035593759", kb_slug="support")

        assert result == {"processed": 0, "flagged": 0, "qdrant_deleted": 0}
        qdrant.delete.assert_not_called()
        # Confirm the SQL was filtered server-side. The placeholder is
        # passed as a $3 parameter (proper parametrisation), so we look for
        # both the WHERE-clause shape and PURGED_PLACEHOLDER_HASH appearing
        # in the args.
        fetch_calls = conn.fetch.await_args_list
        assert len(fetch_calls) == 1
        sql = fetch_calls[0].args[0]
        assert "content_hash <> $3" in sql, (
            "SELECT must filter out already-purged pages via parametrised <>"
        )
        assert PURGED_PLACEHOLDER_HASH in fetch_calls[0].args, (
            "PURGED_PLACEHOLDER_HASH must be passed as a query parameter"
        )


# ---------------------------------------------------------------------------
# AC-06.3 + AC-09: tenant isolation
# ---------------------------------------------------------------------------


class TestTenantIsolation:
    @pytest.mark.asyncio()
    async def test_query_uses_tenant_scoped_connection(self, patched_externals) -> None:
        """All knowledge.* reads/writes go through tenant_scoped_connection.

        The patched_externals fixture replaces tenant_scoped_connection with a
        scope that yields the mocked connection. If the production code goes
        through any OTHER path (raw pool acquire, etc.), the patch wouldn't
        apply and the test would either crash or not record the calls.
        """
        conn, _qdrant, set_rows = patched_externals
        set_rows([{"url": "https://x/walled", "raw_markdown": _walled_md(), "content_hash": "h"}])

        await backfill_detect_login_walls(org_id="368884765035593759", kb_slug="support")

        # All SELECTs MUST have gone through the scoped connection.
        assert conn.fetch.await_count >= 1

    @pytest.mark.asyncio()
    async def test_qdrant_filter_includes_org_id_and_kb_slug(self, patched_externals) -> None:
        """REQ-09.1 — Qdrant delete filter MUST contain org_id + kb_slug +
        path. Deletion by path alone or by kb_slug alone is FORBIDDEN —
        matches the semgrep rule in
        .github/workflows/tenant-isolation-review.yml."""
        _conn, qdrant, set_rows = patched_externals
        set_rows([{"url": "https://x/walled", "raw_markdown": _walled_md(), "content_hash": "h"}])

        await backfill_detect_login_walls(org_id="368884765035593759", kb_slug="support")

        # Inspect the Filter object passed to qdrant.delete.
        # Real qdrant_client.http.models.Filter has .must field; we serialise
        # to string and grep — the keys org_id, kb_slug, path MUST all appear.
        kwargs = qdrant.delete.await_args.kwargs
        selector = kwargs.get("points_selector") or qdrant.delete.await_args.args[1]
        s = repr(selector)
        assert "org_id" in s
        assert "kb_slug" in s
        assert "path" in s
        # And concretely the values.
        assert "368884765035593759" in s
        assert "support" in s
