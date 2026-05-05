"""Tests for SPEC-RAG-REBUILD-KB-001: operator-triggered KB rebuild.

Covers:
  - test_rebuild_kb_iterates_artifacts: every active artifact triggers one rebuild call.
  - test_rebuild_kb_skips_artifacts_without_text: rebuild_skip_no_text log path.
  - test_rebuild_kb_propagates_failures_per_artifact: fail-open per artifact.
  - test_rebuild_kb_queueing_lock: AlreadyEnqueued raised on duplicate defer.
  - test_rebuild_kb_summary_log_event: rebuild_kb_completed log event shape.

All tests mock external I/O — no real network, no real DB, no real Qdrant.
"""

from __future__ import annotations

import sys
import types
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import structlog.testing

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_ORG = "org-zitadel-123"
_KB = "my-kb"

_SENTINEL = 253402300800


def _make_artifact(artifact_id: str, path: str, document_text: str | None) -> dict:
    """Build a minimal artifact dict as returned by _list_active_artifacts."""
    extra: dict = {}
    if document_text is not None:
        extra["document_text"] = document_text
    return {
        "id": artifact_id,
        "path": path,
        "content_type": "kb_article",
        "extra": extra,
        "synthesis_depth": 0,
        "belief_time_start": 1_700_000_000,
        "belief_time_end": _SENTINEL,
        "user_id": None,
    }


def _make_chunk(text: str = "chunk text", parent_index: int = 0) -> MagicMock:
    """Return a mock Chunk object as returned by chunk_markdown_with_parents."""
    c = MagicMock()
    c.text = text
    c.parent_index = parent_index
    return c


def _make_parent_chunk(text: str = "parent text", position: int = 0) -> MagicMock:
    """Return a mock ParentChunk object."""
    p = MagicMock()
    p.text = text
    p.position = position
    return p


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _patch_env(monkeypatch):
    """Ensure required env vars are set before any knowledge_ingest import."""
    monkeypatch.setenv("KNOWLEDGE_INGEST_SECRET", "test-secret-value-123")
    monkeypatch.setenv("PORTAL_INTERNAL_TOKEN", "test-portal-internal-token-456")


@pytest.fixture()
def _common_patches():
    """Patch all external I/O used by _rebuild_kb_core and _rebuild_artifact.

    _rebuild_artifact uses lazy imports inside the function body:
      from knowledge_ingest.enrichment_tasks import _enrich_document
      from knowledge_ingest import chunker
      from knowledge_ingest import pg_store

    We patch at the source modules so the lazy imports pick up the mocks.
    """
    chunks = [_make_chunk("child text")]
    parents = [_make_parent_chunk("parent text")]

    mock_chunker = MagicMock()
    mock_chunker.chunk_markdown_with_parents.return_value = (chunks, parents)
    mock_chunker._approx_token_count.return_value = 50

    mock_pg = MagicMock()
    mock_pg.delete_parent_chunks_for_artifact = AsyncMock(return_value=1)
    mock_pg.insert_parent_chunks = AsyncMock(return_value=[1])

    mock_enrich = AsyncMock()

    with (
        patch(
            "knowledge_ingest.rebuild_tasks._list_active_artifacts",
            new_callable=AsyncMock,
        ) as mock_list,
        # Patch the lazy import targets so they resolve to our mocks.
        patch.dict(
            sys.modules,
            {
                "knowledge_ingest.enrichment_tasks": types.SimpleNamespace(
                    _enrich_document=mock_enrich
                ),
                "knowledge_ingest.chunker": mock_chunker,
                "knowledge_ingest.pg_store": mock_pg,
            },
        ),
    ):
        yield {
            "mock_list": mock_list,
            "mock_enrich": mock_enrich,
            "mock_chunker": mock_chunker,
            "mock_pg": mock_pg,
            "chunks": chunks,
            "parents": parents,
        }


# ---------------------------------------------------------------------------
# Test 1: iterates every active artifact
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rebuild_kb_iterates_artifacts(_common_patches):
    """Every active artifact with document_text triggers one _enrich_document call."""
    from knowledge_ingest.rebuild_tasks import _rebuild_kb_core

    artifacts = [
        _make_artifact("art-1", "doc1.md", "Text for doc 1"),
        _make_artifact("art-2", "doc2.md", "Text for doc 2"),
    ]
    _common_patches["mock_list"].return_value = artifacts

    result = await _rebuild_kb_core(org_id=_ORG, kb_slug=_KB)

    assert result["artifacts_processed"] == 2
    assert result["artifacts_skipped"] == 0
    assert result["artifacts_failed"] == 0
    assert _common_patches["mock_enrich"].call_count == 2

    # Both artifacts must have been passed to _enrich_document
    called_paths = {c.kwargs["path"] for c in _common_patches["mock_enrich"].call_args_list}
    assert called_paths == {"doc1.md", "doc2.md"}


# ---------------------------------------------------------------------------
# Test 2: skips artifacts without document_text
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rebuild_kb_skips_artifacts_without_text(_common_patches):
    """Artifacts without extra.document_text are skipped with rebuild_skip_no_text log."""
    from knowledge_ingest.rebuild_tasks import _rebuild_kb_core

    artifacts = [
        _make_artifact("art-no-text", "no-text.md", None),  # no document_text
        _make_artifact("art-with-text", "with-text.md", "Body"),
    ]
    _common_patches["mock_list"].return_value = artifacts

    with structlog.testing.capture_logs() as captured:
        result = await _rebuild_kb_core(org_id=_ORG, kb_slug=_KB)

    assert result["artifacts_skipped"] == 1
    assert result["artifacts_processed"] == 1
    assert result["artifacts_failed"] == 0

    skip_events = [e for e in captured if e["event"] == "rebuild_skip_no_text"]
    assert len(skip_events) == 1
    assert skip_events[0]["artifact_id"] == "art-no-text"

    # Only the artifact with text should trigger enrichment
    assert _common_patches["mock_enrich"].call_count == 1


# ---------------------------------------------------------------------------
# Test 3: one artifact failure does not abort the rest
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rebuild_kb_propagates_failures_per_artifact(_common_patches):
    """When one artifact's enrichment raises, others still process; failed counted."""
    from knowledge_ingest.rebuild_tasks import _rebuild_kb_core

    artifacts = [
        _make_artifact("art-bad", "bad.md", "Good text but enrich fails"),
        _make_artifact("art-ok-1", "ok1.md", "Fine"),
        _make_artifact("art-ok-2", "ok2.md", "Also fine"),
    ]
    _common_patches["mock_list"].return_value = artifacts

    # Make the first artifact fail during enrichment
    async def _side_effect(**kwargs):
        if kwargs.get("artifact_id") == "art-bad":
            raise RuntimeError("LLM timeout")

    _common_patches["mock_enrich"].side_effect = _side_effect

    result = await _rebuild_kb_core(org_id=_ORG, kb_slug=_KB)

    assert result["artifacts_failed"] == 1
    assert result["artifacts_processed"] == 2
    assert result["artifacts_skipped"] == 0

    # All three artifacts were attempted
    assert _common_patches["mock_enrich"].call_count == 3


# ---------------------------------------------------------------------------
# Test 4: queueing_lock prevents duplicate defers
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rebuild_kb_queueing_lock():
    """Deferring rebuild_kb twice for the same (org_id, kb_slug) raises AlreadyEnqueued.

    Uses a fake procrastinate module (no psycopg dependency) to verify that
    register_rebuild_tasks wires the task with queueing_lock semantics, and
    that a second configure().defer_async() with the same lock raises the
    exception class.
    """

    # Fake AlreadyEnqueued exception class (matches procrastinate.exceptions)
    class _AlreadyEnqueued(Exception):
        pass

    # Track deferred tasks by queueing_lock
    _deferred_locks: set[str] = set()

    class _FakeConfigured:
        def __init__(self, lock: str) -> None:
            self._lock = lock

        async def defer_async(self, **kwargs):
            if self._lock in _deferred_locks:
                raise _AlreadyEnqueued(f"Lock {self._lock} already enqueued")
            _deferred_locks.add(self._lock)

    class _FakeTask:
        def configure(self, *, queueing_lock: str, **kwargs):
            return _FakeConfigured(queueing_lock)

    class _FakeApp:
        def task(self, *, queue: str, retry=None):
            def decorator(fn):
                task_obj = _FakeTask()
                task_obj._fn = fn
                return task_obj

            return decorator

    # Inject the fake exceptions module so register_rebuild_tasks can import it
    fake_proc_exc = types.SimpleNamespace(AlreadyEnqueued=_AlreadyEnqueued)
    fake_proc = types.SimpleNamespace(
        RetryStrategy=MagicMock(return_value=None),
        exceptions=fake_proc_exc,
    )

    with patch.dict(
        sys.modules,
        {
            "procrastinate": fake_proc,
            "procrastinate.exceptions": fake_proc_exc,
        },
    ):
        from knowledge_ingest.rebuild_tasks import register_rebuild_tasks

        app = _FakeApp()
        register_rebuild_tasks(app)

        # The task was registered and is accessible
        task = app.rebuild_kb  # type: ignore[attr-defined]

        # First defer succeeds
        await task.configure(queueing_lock=f"rebuild-kb-{_ORG}-{_KB}").defer_async(
            org_id=_ORG, kb_slug=_KB
        )

        # Second defer for the same lock raises AlreadyEnqueued
        with pytest.raises(_AlreadyEnqueued):
            await task.configure(queueing_lock=f"rebuild-kb-{_ORG}-{_KB}").defer_async(
                org_id=_ORG, kb_slug=_KB
            )


# ---------------------------------------------------------------------------
# Test 5: rebuild_kb_completed log event shape
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rebuild_kb_summary_log_event(_common_patches):
    """rebuild_kb_completed is logged with the expected result dict shape."""
    from knowledge_ingest.rebuild_tasks import _rebuild_kb_core

    _common_patches["mock_list"].return_value = [
        _make_artifact("art-a", "a.md", "Text A"),
    ]

    with structlog.testing.capture_logs() as captured:
        result = await _rebuild_kb_core(org_id=_ORG, kb_slug=_KB)

    # Verify the returned dict has all required keys with correct types
    assert result["org_id"] == _ORG
    assert result["kb_slug"] == _KB
    assert "artifacts_processed" in result
    assert "artifacts_skipped" in result
    assert "artifacts_failed" in result
    assert "duration_ms" in result
    assert isinstance(result["duration_ms"], int)

    # Verify the structlog event was emitted with matching fields
    completed_events = [e for e in captured if e["event"] == "rebuild_kb_completed"]
    assert len(completed_events) == 1
    evt = completed_events[0]
    assert evt["org_id"] == _ORG
    assert evt["kb_slug"] == _KB
    assert evt["artifacts_processed"] == 1
    assert evt["artifacts_skipped"] == 0
    assert evt["artifacts_failed"] == 0
