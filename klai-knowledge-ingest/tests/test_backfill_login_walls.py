"""Backfill + recovery tests for SPEC-INGEST-LOGIN-WALL-DETECT-002 Phase D.

Covers:

* AC-04.1 — re-running on a fresh tenant computes hashes for every page.
* AC-04.2 — backfill purges template clusters; FPs untouched.
* AC-04.3 — idempotent: re-running with no changes is a no-op.
* AC-04.4 — Qdrant deletes carry org_id + kb_slug + path (REQ-09.1 isolation).
* AC-05.1 — recover_purged_pages clears placeholder for pages no longer in
  a cluster.
* AC-05.2 — no spurious recovery: pages still clustering stay purged.

Production Postgres / Qdrant are mocked. The mock connection's ``fetch``
returns a configurable row list; ``execute`` is recorded so we can assert
on UPDATE statements (placeholder writes for backfill, content_hash clear
for recovery, content_simhash write for the pass-1 ensure-simhash loop).
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from knowledge_ingest.backfill_tasks import (
    PURGED_PLACEHOLDER_HASH,
    _count_cluster_siblings,
    backfill_detect_login_walls,
    recover_purged_pages,
)
from knowledge_ingest.utils.content_fingerprint import compute_simhash

FIXTURES = Path(__file__).parent / "fixtures"


def _walled_md() -> str:
    return (FIXTURES / "auth_walls" / "redcactus_hubspot.md").read_text(encoding="utf-8")


def _clean_md() -> str:
    return (FIXTURES / "clean_pages" / "redcactus_ifttt.md").read_text(encoding="utf-8")


def _row(
    *,
    url: str,
    raw: str,
    content_hash: str = "live-hash",
    content_simhash: int | None = None,
) -> dict:
    """Build a fake ``crawled_pages`` row with v2 columns."""
    return {
        "url": url,
        "raw_markdown": raw,
        "content_hash": content_hash,
        "content_simhash": content_simhash,
    }


def _walled_cluster(count: int) -> list[dict]:
    """Build ``count`` rows, all sharing the wall content (= one cluster)."""
    base = _walled_md()
    return [
        _row(url=f"https://wiki.redcactus.cloud/wall-{i}", raw=base)
        for i in range(count)
    ]


def _make_conn(rows: list[dict]):
    """asyncpg-like connection mock: ``fetch`` returns ``rows``; ``execute``
    + ``fetchval`` are recorded for assertion. ``fetchval`` is required
    because ``pg_store.update_crawled_page_simhash`` uses ``RETURNING url``
    to detect race-deleted rows (SPEC-LOGIN-WALL-002 follow-up)."""
    conn = MagicMock()

    async def fetch_side_effect(_sql: str, *_args):
        return rows

    conn.fetch = AsyncMock(side_effect=fetch_side_effect)
    conn.execute = AsyncMock(return_value=None)
    # Default: row found (return any non-None URL). Tests that need to
    # exercise the no-row warning path override this per-call.
    conn.fetchval = AsyncMock(return_value="https://x/dummy")
    return conn


def _make_qdrant():
    client = MagicMock()
    client.delete = AsyncMock(return_value=None)
    return client


@pytest.fixture()
def patched_externals():
    """Patches tenant_scoped_connection + Qdrant client.

    Yields ``(conn, qdrant, set_rows)`` where ``set_rows`` reseeds the rows
    returned by ``conn.fetch``.
    """
    rows: list[dict] = []
    conn = _make_conn(rows)
    qdrant = _make_qdrant()

    @asynccontextmanager
    async def fake_scope(_org_id: str):
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
# AC-04.1 / AC-04.2 — backfill detects + purges template clusters
# ---------------------------------------------------------------------------


class TestBackfillCluster:
    @pytest.mark.asyncio()
    async def test_walled_cluster_purged(self, patched_externals) -> None:
        """6 wall-cluster pages → each has 5 OTHERS within Hamming 3 → all flag.

        Default cluster_min is 5 OTHERS (REQ-02 strict interpretation: "N or
        more OTHER pages"). With 6 identical pages, each has 5 OTHERS at
        Hamming 0 → all flagged + Qdrant-deleted + placeholder-set.
        """
        _conn, qdrant, set_rows = patched_externals
        set_rows(_walled_cluster(6))

        result = await backfill_detect_login_walls(
            org_id="100000000000000002",
            kb_slug="support",
        )

        assert result == {"processed": 6, "flagged": 6, "qdrant_deleted": 6}
        assert qdrant.delete.await_count == 6

    @pytest.mark.asyncio()
    async def test_at_threshold_minus_one_not_flagged(
        self, patched_externals
    ) -> None:
        """5 identical pages → 4 OTHERS each → below default threshold 5 → no flag."""
        _conn, qdrant, set_rows = patched_externals
        set_rows(_walled_cluster(5))

        result = await backfill_detect_login_walls(
            org_id="100000000000000002",
            kb_slug="support",
        )

        assert result == {"processed": 5, "flagged": 0, "qdrant_deleted": 0}
        qdrant.delete.assert_not_called()

    @pytest.mark.asyncio()
    async def test_mixed_batch_only_cluster_purged(self, patched_externals) -> None:
        """Cluster + isolated FP page → only cluster members are purged."""
        _conn, qdrant, set_rows = patched_externals
        rows = _walled_cluster(6)
        rows.append(_row(url="https://help.voys.nl/2fa-freedom", raw=_clean_md()))
        set_rows(rows)

        result = await backfill_detect_login_walls(
            org_id="100000000000000002",
            kb_slug="support",
        )

        assert result["processed"] == 7
        assert result["flagged"] == 6  # cluster only — FP unaffected
        assert qdrant.delete.await_count == 6


# ---------------------------------------------------------------------------
# AC-04.3 — idempotent re-run
# ---------------------------------------------------------------------------


class TestIdempotency:
    @pytest.mark.asyncio()
    async def test_already_purged_pages_skipped(self, patched_externals) -> None:
        """SQL filter excludes ``content_hash = PURGED_PLACEHOLDER_HASH`` rows.

        The fixture returns [] to model "all matching rows already purged",
        which mirrors the production server-side filter.
        """
        conn, qdrant, set_rows = patched_externals
        set_rows([])

        result = await backfill_detect_login_walls(
            org_id="100000000000000002",
            kb_slug="support",
        )

        assert result == {"processed": 0, "flagged": 0, "qdrant_deleted": 0}
        qdrant.delete.assert_not_called()
        # Confirm the SQL filtered server-side.
        fetch_calls = conn.fetch.await_args_list
        assert len(fetch_calls) == 1
        sql = fetch_calls[0].args[0]
        assert "content_hash <> $3" in sql, (
            "SELECT must filter out already-purged pages via parametrised <>"
        )
        assert PURGED_PLACEHOLDER_HASH in fetch_calls[0].args


# ---------------------------------------------------------------------------
# REQ-01 pass-1 — backfill computes + stores SimHashes for NULL rows
# ---------------------------------------------------------------------------


class TestSimhashBackfillPass:
    @pytest.mark.asyncio()
    async def test_null_simhashes_get_computed_and_stored(self, patched_externals) -> None:
        """Pass-1 of backfill writes SimHash for every NULL row."""
        conn, _qdrant, set_rows = patched_externals
        rows = _walled_cluster(6)
        # All rows start with NULL content_simhash (default in _row()).
        set_rows(rows)

        await backfill_detect_login_walls(
            org_id="100000000000000002",
            kb_slug="support",
        )

        # Each row triggers an UPDATE ... SET content_simhash = $1
        # ... RETURNING url (via conn.fetchval, not conn.execute).
        update_calls = [
            c
            for c in conn.fetchval.await_args_list
            if c.args and "SET content_simhash" in c.args[0]
        ]
        assert len(update_calls) == 6
        # The stored hash matches compute_simhash on the wall content.
        expected = compute_simhash(_walled_md())
        for call in update_calls:
            assert call.args[1] == expected  # $1 = content_simhash

    @pytest.mark.asyncio()
    async def test_existing_simhashes_not_recomputed(self, patched_externals) -> None:
        """Rows with non-NULL content_simhash are reused, not re-hashed."""
        conn, _qdrant, set_rows = patched_externals
        rows = _walled_cluster(6)
        # Pre-populate simhashes — backfill should not write them again.
        precomputed = compute_simhash(_walled_md())
        for r in rows:
            r["content_simhash"] = precomputed
        set_rows(rows)

        await backfill_detect_login_walls(
            org_id="100000000000000002",
            kb_slug="support",
        )

        update_calls = [
            c
            for c in conn.fetchval.await_args_list
            if c.args and "SET content_simhash" in c.args[0]
        ]
        assert update_calls == [], (
            "content_simhash UPDATE should not run when already populated"
        )

    @pytest.mark.asyncio()
    async def test_empty_raw_markdown_does_not_store_zero_hash(
        self, patched_externals
    ) -> None:
        """Pages with empty/whitespace raw_markdown produce simhash=0; the
        backfill MUST NOT persist 0 (would let empty pages cluster across a
        tenant). Row stays content_simhash=NULL.
        """
        conn, _qdrant, set_rows = patched_externals
        set_rows(
            [
                _row(url="https://x/empty-1", raw=""),
                _row(url="https://x/whitespace", raw="\n\t  "),
                _row(url="https://x/empty-3", raw=""),
            ]
        )

        result = await backfill_detect_login_walls(
            org_id="100000000000000002",
            kb_slug="support",
        )

        assert result["flagged"] == 0
        update_calls = [
            c
            for c in conn.fetchval.await_args_list
            if c.args and "SET content_simhash" in c.args[0]
        ]
        assert update_calls == [], (
            "Empty-content rows must not have content_simhash=0 written"
        )


class TestZeroHashSafeguard:
    """The 0 SimHash sentinel must never cluster — neither as target nor
    as a sibling. Otherwise empty/whitespace pages across a tenant would
    falsely surface as a "wall" cluster.
    """

    def test_zero_target_returns_zero_count(self) -> None:
        url_to_hash = {f"https://x/{i}": 0 for i in range(10)}
        url_to_hash["https://x/target"] = 0
        siblings = _count_cluster_siblings(
            "https://x/target",
            0,
            url_to_hash,
            hamming_max=3,
        )
        assert siblings == 0

    def test_zero_siblings_filtered_for_real_target(self) -> None:
        target_hash = compute_simhash("real content with words")
        url_to_hash = {
            "https://x/target": target_hash,
            "https://x/zero-1": 0,
            "https://x/zero-2": 0,
            "https://x/match-1": target_hash,
            "https://x/match-2": target_hash,
        }
        siblings = _count_cluster_siblings(
            "https://x/target",
            target_hash,
            url_to_hash,
            hamming_max=3,
        )
        # 2 zero-siblings filtered out; 2 matching siblings counted.
        assert siblings == 2


# ---------------------------------------------------------------------------
# REQ-09 — tenant isolation in Qdrant filter (AC-04.4)
# ---------------------------------------------------------------------------


class TestTenantIsolation:
    @pytest.mark.asyncio()
    async def test_qdrant_filter_includes_org_id_kb_slug_path(
        self, patched_externals
    ) -> None:
        """REQ-09.1 — Qdrant delete filter MUST contain org_id + kb_slug + path.

        Removing any one of these is blocked by the semgrep rule in
        ``.github/workflows/tenant-isolation-review.yml``.
        """
        _conn, qdrant, set_rows = patched_externals
        set_rows(_walled_cluster(6))

        await backfill_detect_login_walls(
            org_id="100000000000000002",
            kb_slug="support",
        )

        # Inspect the first delete's filter.
        kwargs = qdrant.delete.await_args.kwargs
        selector = kwargs.get("points_selector") or qdrant.delete.await_args.args[1]
        s = repr(selector)
        for required in ("org_id", "kb_slug", "path"):
            assert required in s, f"Filter missing {required}"
        assert "100000000000000002" in s
        assert "support" in s


# ---------------------------------------------------------------------------
# AC-05 — recover_purged_pages
# ---------------------------------------------------------------------------


class TestRecovery:
    @pytest.mark.asyncio()
    async def test_unpurges_isolated_purged_page(self, patched_externals) -> None:
        """AC-05.1: a purged page no longer in a cluster gets its content_hash cleared."""
        conn, _qdrant, set_rows = patched_externals
        # Single purged FP page; no cluster context around it.
        set_rows(
            [
                _row(
                    url="https://help.voys.nl/2fa-freedom",
                    raw=_clean_md(),
                    content_hash=PURGED_PLACEHOLDER_HASH,
                )
            ]
        )

        result = await recover_purged_pages(
            org_id="100000000000000002",
            kb_slug="voys-test",
        )

        assert result == {"processed": 1, "recovered": 1}
        # Verify content_hash UPDATE happened.
        clear_calls = [
            c
            for c in conn.execute.await_args_list
            if c.args
            and "SET content_hash = ''" in c.args[0]
            and "https://help.voys.nl/2fa-freedom" in c.args
        ]
        assert len(clear_calls) == 1

    @pytest.mark.asyncio()
    async def test_no_recovery_when_still_clustering(self, patched_externals) -> None:
        """AC-05.2: a purged page that still clusters under v2 stays purged."""
        conn, _qdrant, set_rows = patched_externals
        # 6 purged wall pages — they still cluster.
        rows = _walled_cluster(6)
        for r in rows:
            r["content_hash"] = PURGED_PLACEHOLDER_HASH
        set_rows(rows)

        result = await recover_purged_pages(
            org_id="100000000000000002",
            kb_slug="support",
        )

        assert result == {"processed": 6, "recovered": 0}
        clear_calls = [
            c
            for c in conn.execute.await_args_list
            if c.args and "SET content_hash = ''" in c.args[0]
        ]
        assert clear_calls == []

    @pytest.mark.asyncio()
    async def test_only_purged_rows_processed(self, patched_externals) -> None:
        """Live (non-purged) rows aren't touched by recovery — only purged are."""
        _conn, _qdrant, set_rows = patched_externals
        rows = [
            _row(
                url="https://x/live",
                raw=_clean_md(),
                content_hash="live-hash",
            ),
            _row(
                url="https://x/purged",
                raw=_clean_md(),
                content_hash=PURGED_PLACEHOLDER_HASH,
            ),
        ]
        set_rows(rows)

        result = await recover_purged_pages(
            org_id="100000000000000002",
            kb_slug="support",
        )

        # Only the 1 purged row counted.
        assert result["processed"] == 1
        assert result["recovered"] == 1
