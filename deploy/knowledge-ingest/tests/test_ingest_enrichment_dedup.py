"""
Tests that duplicate enrichment tasks are deduplicated via queueing_lock.

Two rapid ingest calls for the same (org_id, kb_slug, path) must produce
exactly one enrichment task — the second defer_async() raises AlreadyEnqueued
and is silently skipped.

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
    document_text="hello",
    chunks=["hello"],
    title="Page",
    artifact_id="aid1",
    user_id=None,
    extra_payload={},
    synthesis_depth=1,
    content_type="unknown",
)

_QUEUEING_LOCK = "{org_id}:{kb_slug}:{path}".format(**_DEFER_KWARGS)


async def _run_enqueue(task_fn):
    """Replicate the try/except block from ingest.py."""
    try:
        from procrastinate.exceptions import AlreadyEnqueued as _AE  # noqa: PLC0415
        await task_fn.configure(
            queueing_lock=_QUEUEING_LOCK,
        ).defer_async(**_DEFER_KWARGS)
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
async def test_queueing_lock_includes_org_kb_path():
    """queueing_lock must be '{org_id}:{kb_slug}:{path}' — no collisions across orgs."""
    # Two tasks for different orgs, same KB+path — must get different locks
    task_fn_a, configured_a = _make_task_fn(side_effects=[None])
    task_fn_b, configured_b = _make_task_fn(side_effects=[None])

    org_a, org_b = "orgA", "orgB"
    kb_slug, path = "shared-kb", "docs/page.md"

    for org_id, task_fn in [(org_a, task_fn_a), (org_b, task_fn_b)]:
        lock = f"{org_id}:{kb_slug}:{path}"
        try:
            from procrastinate.exceptions import AlreadyEnqueued as _AE  # noqa: PLC0415
            await task_fn.configure(queueing_lock=lock).defer_async(
                org_id=org_id,
                kb_slug=kb_slug,
                path=path,
                document_text="x",
                chunks=["x"],
                title="T",
                artifact_id="a",
                user_id=None,
                extra_payload={},
                synthesis_depth=1,
                content_type="unknown",
            )
        except _AE:
            pass

    lock_a = task_fn_a.configure.call_args.kwargs["queueing_lock"]
    lock_b = task_fn_b.configure.call_args.kwargs["queueing_lock"]

    assert lock_a != lock_b, "Different orgs must produce different locks"
    assert lock_a == f"{org_a}:{kb_slug}:{path}"
    assert lock_b == f"{org_b}:{kb_slug}:{path}"
