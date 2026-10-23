"""The repair decides from current state, and never deletes a live document's points."""

import pytest

from knowledge_ingest.scripts.repair_pg_qdrant_drift import _plan


def _row(*, active=True, index_status="synced", has_text=True) -> dict:
    return {"active": active, "index_status": index_status, "has_text": has_text}


@pytest.mark.parametrize(
    "row, points, action",
    [
        # Reported as orphaned in production (2026-10): an upload whose
        # enrichment failed keeps its raw chunks and is still searchable.
        (_row(index_status="failed"), ["p1"], "skip"),
        (_row(), ["p1"], "skip"),
        (None, ["p1"], "delete-points"),
        (_row(active=False), ["p1"], "delete-points"),
        (_row(), [], "reindex"),
        (_row(has_text=False), [], "retire"),
        (_row(index_status="pending"), [], "skip"),
    ],
)
def test_plan_follows_the_current_state(row, points, action):
    assert _plan(row, points)[0] == action
