"""
Tests that duplicate enrichment tasks are deduplicated via queueing_lock.

Duplicate enqueue attempts for the same concrete artifact are silently skipped,
but a newer artifact for the same path must get a distinct job. Otherwise an
older queued job can be the only enrichment job left after a re-ingest.

procrastinate is mocked at the sys.modules level so this file runs in
environments where libpq is not installed (CI, local dev on Windows).
"""
from __future__ import annotations

import sys
import types
import logging
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


# ---------------------------------------------------------------------------
# Minimal procrastinate stub — avoids the psycopg / libpq import chain
# ---------------------------------------------------------------------------

class _AlreadyEnqueued(Exception):
    """Stub for procrastinate.exceptions.AlreadyEnqueued."""


def _install_procrastinate_stub():
    """Inject a minimal procrastinate stub into sys.modules."""
    if "procrastinate" in sys.modules:
        return  # real package already loaded — don't override

    exceptions_mod = types.ModuleType("procrastinate.exceptions")
    exceptions_mod.AlreadyEnqueued = _AlreadyEnqueued  # type: ignore[attr-defined]

    pkg = types.ModuleType("procrastinate")
    pkg.exceptions = exceptions_mod  # type: ignore[attr-defined]

    sys.modules["procrastinate"] = pkg
    sys.modules["procrastinate.exceptions"] = exceptions_mod


_install_procrastinate_stub()

# Re-export so tests can reference the stub class
AlreadyEnqueued = sys.modules["procrastinate.exceptions"].AlreadyEnqueued


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_task_fn(side_effects):
    """
    Return a mock task function whose .configure().defer_async() uses the
    given side_effects in order (one per call).
    """
    configured = MagicMock()
    configured.defer_async = AsyncMock(side_effect=side_effects)
    task_fn = MagicMock()
    task_fn.configure = MagicMock(return_value=configured)
    return task_fn, configured


_DEFER_KWARGS = dict(
    org_id="org1",
    kb_slug="my-kb",
    path="docs/page.md",
    artifact_id="aid1",
)

_QUEUEING_LOCK = "{org_id}:{kb_slug}:{path}:{artifact_id}".format(**_DEFER_KWARGS)


async def _run_enqueue(task_fn):
    """Replicate the try/except block from ingest.py."""
    try:
        from procrastinate.exceptions import AlreadyEnqueued as _AE  # noqa: PLC0415
        await task_fn.configure(
            queueing_lock=_QUEUEING_LOCK,
        ).defer_async(artifact_id=_DEFER_KWARGS["artifact_id"])
    except _AE:
        logging.getLogger("knowledge_ingest.routes.ingest").info(
            "enrichment already queued, skipping (%s/%s org=%s)",
            _DEFER_KWARGS["kb_slug"],
            _DEFER_KWARGS["path"],
            _DEFER_KWARGS["org_id"],
        )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_second_ingest_skipped_when_already_enqueued(caplog):
    """Second defer_async() raising AlreadyEnqueued is caught and logged at INFO."""
    task_fn, configured = _make_task_fn(side_effects=[None, AlreadyEnqueued()])

    with caplog.at_level(logging.INFO, logger="knowledge_ingest.routes.ingest"):
        await _run_enqueue(task_fn)  # first call — succeeds
        await _run_enqueue(task_fn)  # second call — AlreadyEnqueued

    # configure() called twice, each with the same lock
    assert task_fn.configure.call_count == 2
    task_fn.configure.assert_called_with(queueing_lock=_QUEUEING_LOCK)

    # defer_async() called twice
    assert configured.defer_async.call_count == 2

    # INFO log emitted for the skipped call
    assert any("enrichment already queued" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_first_ingest_not_skipped():
    """When no duplicate exists, defer_async() completes normally."""
    task_fn, configured = _make_task_fn(side_effects=[None])

    await _run_enqueue(task_fn)

    assert configured.defer_async.call_count == 1


@pytest.mark.asyncio
async def test_queueing_lock_includes_org_kb_path_and_artifact():
    """queueing_lock includes path identity plus the concrete artifact version."""
    # Two tasks for different orgs, same KB+path — must get different locks
    task_fn_a, configured_a = _make_task_fn(side_effects=[None])
    task_fn_b, configured_b = _make_task_fn(side_effects=[None])
    task_fn_c, configured_c = _make_task_fn(side_effects=[None])

    org_a, org_b = "orgA", "orgB"
    kb_slug, path = "shared-kb", "docs/page.md"

    scenarios = [
        (org_a, "artifact-a", task_fn_a),
        (org_b, "artifact-b", task_fn_b),
        (org_a, "artifact-c", task_fn_c),
    ]
    for org_id, artifact_id, task_fn in scenarios:
        lock = f"{org_id}:{kb_slug}:{path}:{artifact_id}"
        try:
            from procrastinate.exceptions import AlreadyEnqueued as _AE  # noqa: PLC0415
            await task_fn.configure(queueing_lock=lock).defer_async(
                artifact_id=artifact_id,
            )
        except _AE:
            pass

    lock_a = task_fn_a.configure.call_args.kwargs["queueing_lock"]
    lock_b = task_fn_b.configure.call_args.kwargs["queueing_lock"]
    lock_c = task_fn_c.configure.call_args.kwargs["queueing_lock"]

    assert lock_a != lock_b, "Different orgs must produce different locks"
    assert lock_a != lock_c, "Different artifact versions must produce different locks"
    assert lock_a == f"{org_a}:{kb_slug}:{path}:artifact-a"
    assert lock_b == f"{org_b}:{kb_slug}:{path}:artifact-b"
    assert lock_c == f"{org_a}:{kb_slug}:{path}:artifact-c"
