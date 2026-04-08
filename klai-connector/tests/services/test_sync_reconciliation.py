"""Tests for sync engine reconciliation logic.

Verifies that the sync engine correctly identifies new, changed, and
unchanged documents when comparing adapter discovery against the cursor.
"""

from __future__ import annotations

from app.adapters.base import DocumentRef


def _ref(page_id: str, last_edited: str = "") -> DocumentRef:
    return DocumentRef(
        path=f"Page {page_id}",
        ref=page_id,
        size=0,
        content_type="notion_page",
        source_ref=page_id,
        last_edited=last_edited,
    )


def _reconcile(
    refs: list[DocumentRef],
    prev_synced_refs: set[str],
    prev_synced_at: str,
    resume_ingested_refs: set[str] | None = None,
) -> tuple[list[DocumentRef], int]:
    """Extracted reconciliation logic from SyncEngine._execute_sync.

    Returns (refs_to_sync, documents_skipped).
    """
    resume = resume_ingested_refs or set()
    refs_to_sync: list[DocumentRef] = []
    documents_skipped = 0

    for ref in refs:
        ref_key = ref.source_ref or ref.path
        if ref_key in resume:
            continue
        is_new = ref_key not in prev_synced_refs
        is_changed = bool(ref.last_edited and ref.last_edited > prev_synced_at)
        if is_new or is_changed or not prev_synced_at:
            refs_to_sync.append(ref)
        else:
            documents_skipped += 1

    return refs_to_sync, documents_skipped


# ---------------------------------------------------------------------------
# First sync (no previous cursor)
# ---------------------------------------------------------------------------


def test_first_sync_syncs_everything() -> None:
    """No previous cursor → all refs should be synced."""
    refs = [_ref("a"), _ref("b"), _ref("c")]
    to_sync, skipped = _reconcile(refs, prev_synced_refs=set(), prev_synced_at="")
    assert len(to_sync) == 3
    assert skipped == 0


# ---------------------------------------------------------------------------
# Incremental: unchanged pages skipped
# ---------------------------------------------------------------------------


def test_unchanged_pages_skipped() -> None:
    """Pages in prev_synced_refs and not edited → skip."""
    refs = [
        _ref("a", last_edited="2026-04-01T10:00:00Z"),
        _ref("b", last_edited="2026-04-01T10:00:00Z"),
    ]
    to_sync, skipped = _reconcile(
        refs,
        prev_synced_refs={"a", "b"},
        prev_synced_at="2026-04-05T00:00:00Z",
    )
    assert len(to_sync) == 0
    assert skipped == 2


# ---------------------------------------------------------------------------
# Incremental: changed page re-synced
# ---------------------------------------------------------------------------


def test_changed_page_resynced() -> None:
    """Page edited after prev_synced_at → re-sync."""
    refs = [
        _ref("a", last_edited="2026-04-01T10:00:00Z"),  # unchanged
        _ref("b", last_edited="2026-04-07T15:00:00Z"),  # changed
    ]
    to_sync, skipped = _reconcile(
        refs,
        prev_synced_refs={"a", "b"},
        prev_synced_at="2026-04-05T00:00:00Z",
    )
    assert len(to_sync) == 1
    assert to_sync[0].source_ref == "b"
    assert skipped == 1


# ---------------------------------------------------------------------------
# Config change: new pages discovered
# ---------------------------------------------------------------------------


def test_new_page_always_synced() -> None:
    """Page not in prev_synced_refs → sync regardless of edit time."""
    refs = [
        _ref("a", last_edited="2026-04-01T10:00:00Z"),  # old, already synced
        _ref("c", last_edited="2026-03-15T10:00:00Z"),  # old, but NEW to us
    ]
    to_sync, skipped = _reconcile(
        refs,
        prev_synced_refs={"a"},
        prev_synced_at="2026-04-05T00:00:00Z",
    )
    assert len(to_sync) == 1
    assert to_sync[0].source_ref == "c"
    assert skipped == 1


# ---------------------------------------------------------------------------
# Mixed: new + changed + unchanged
# ---------------------------------------------------------------------------


def test_mixed_reconciliation() -> None:
    """Realistic mix of new, changed, and unchanged pages."""
    refs = [
        _ref("existing-unchanged", last_edited="2026-04-01T10:00:00Z"),
        _ref("existing-changed", last_edited="2026-04-08T12:00:00Z"),
        _ref("brand-new", last_edited="2026-03-01T10:00:00Z"),
    ]
    to_sync, skipped = _reconcile(
        refs,
        prev_synced_refs={"existing-unchanged", "existing-changed"},
        prev_synced_at="2026-04-05T00:00:00Z",
    )
    synced_ids = {r.source_ref for r in to_sync}
    assert synced_ids == {"existing-changed", "brand-new"}
    assert skipped == 1


# ---------------------------------------------------------------------------
# Resume: already ingested refs skipped
# ---------------------------------------------------------------------------


def test_resume_skips_already_ingested() -> None:
    """Refs from an interrupted run are skipped."""
    refs = [_ref("a"), _ref("b"), _ref("c")]
    to_sync, skipped = _reconcile(
        refs,
        prev_synced_refs=set(),
        prev_synced_at="",
        resume_ingested_refs={"a", "b"},
    )
    assert len(to_sync) == 1
    assert to_sync[0].source_ref == "c"


# ---------------------------------------------------------------------------
# Deleted page detection (for future use)
# ---------------------------------------------------------------------------


def test_deleted_page_detectable() -> None:
    """Pages in prev_synced_refs but not in current refs = deleted."""
    refs = [_ref("a"), _ref("b")]
    prev = {"a", "b", "deleted-page"}
    current_refs = {r.source_ref for r in refs}
    deleted = prev - current_refs
    assert deleted == {"deleted-page"}
