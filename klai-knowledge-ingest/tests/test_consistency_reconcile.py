from __future__ import annotations

import sys
import types
from collections import Counter
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import yaml

from knowledge_ingest.consistency_reconcile import (
    ArtifactKey,
    _diff_inventory,
    _fetch_qdrant_artifact_counts,
    reconcile_pg_qdrant,
    register_consistency_reconcile_task,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
INGEST_ALERT_PATH = (
    REPO_ROOT / "deploy" / "grafana" / "provisioning" / "alerting" / "ingest-rules.yaml"
)


class _RetryStrategy:
    def __init__(self, *, max_attempts: int):
        self.max_attempts = max_attempts


def _install_procrastinate_stub() -> None:
    if "procrastinate" in sys.modules:
        return
    proc_mod = types.ModuleType("procrastinate")
    proc_mod.RetryStrategy = _RetryStrategy
    sys.modules["procrastinate"] = proc_mod


_install_procrastinate_stub()


class _FakeApp:
    def __init__(self) -> None:
        self.tasks: list[tuple[object, dict]] = []
        self.periodics: list[tuple[object, dict]] = []

    def task(self, **kwargs):
        def _decorator(fn):
            self.tasks.append((fn, kwargs))
            return fn

        return _decorator

    def periodic(self, **kwargs):
        def _decorator(fn):
            self.periodics.append((fn, kwargs))
            return fn

        return _decorator


def test_diff_inventory_reports_missing_and_orphaned_artifacts():
    pg_artifacts = [
        {
            "org_id": "org-1",
            "kb_slug": "kb",
            "path": "present.md",
            "artifact_id": "art-present",
        },
        {
            "org_id": "org-1",
            "kb_slug": "kb",
            "path": "missing.md",
            "artifact_id": "art-missing",
        },
    ]
    qdrant_counts = Counter(
        {
            ArtifactKey("org-1", "kb", "present.md", "art-present"): 2,
            ArtifactKey("org-1", "kb", "orphan.md", "art-orphan"): 1,
        }
    )

    report = _diff_inventory(pg_artifacts, qdrant_counts)

    assert report["pg_active_artifacts"] == 2
    assert report["qdrant_artifacts"] == 2
    assert report["qdrant_points"] == 3
    assert report["missing_in_qdrant"] == 1
    assert report["orphaned_in_qdrant"] == 1
    assert report["discrepancies_total"] == 2
    assert report["missing_sample"][0]["artifact_id"] == "art-missing"
    assert report["orphaned_sample"][0]["artifact_id"] == "art-orphan"


@pytest.mark.asyncio
async def test_fetch_qdrant_artifact_counts_scrolls_all_pages():
    client = MagicMock()
    client.scroll = AsyncMock(
        side_effect=[
            (
                [
                    SimpleNamespace(
                        payload={
                            "org_id": "org-1",
                            "kb_slug": "kb",
                            "path": "page.md",
                            "artifact_id": "art-1",
                        }
                    ),
                    SimpleNamespace(
                        payload={
                            "org_id": "org-1",
                            "kb_slug": "kb",
                            "path": "page.md",
                            "artifact_id": "art-1",
                        }
                    ),
                ],
                "next",
            ),
            (
                [
                    SimpleNamespace(
                        payload={
                            "org_id": "org-2",
                            "kb_slug": "kb",
                            "path": "other.md",
                            "artifact_id": "art-2",
                        }
                    )
                ],
                None,
            ),
        ]
    )

    with patch(
        "knowledge_ingest.consistency_reconcile.qdrant_store.get_client",
        return_value=client,
    ):
        counts = await _fetch_qdrant_artifact_counts()

    assert counts[ArtifactKey("org-1", "kb", "page.md", "art-1")] == 2
    assert counts[ArtifactKey("org-2", "kb", "other.md", "art-2")] == 1
    assert client.scroll.await_count == 2
    assert client.scroll.call_args_list[0].kwargs["offset"] is None
    assert client.scroll.call_args_list[1].kwargs["offset"] == "next"


def _patched_reconcile_env(
    pg_artifacts: list[dict],
    recent_artifacts: list[dict],
    qdrant_counts: Counter,
):
    conn = MagicMock()

    @asynccontextmanager
    async def _ctx():
        yield conn

    return (
        patch(
            "knowledge_ingest.consistency_reconcile.cross_org_admin_connection",
            return_value=_ctx(),
        ),
        patch(
            "knowledge_ingest.consistency_reconcile.pg_store.list_active_synced_artifacts",
            new_callable=AsyncMock,
            return_value=pg_artifacts,
        ),
        patch(
            "knowledge_ingest.consistency_reconcile.pg_store.list_recent_artifact_keys",
            new_callable=AsyncMock,
            return_value=recent_artifacts,
        ),
        patch(
            "knowledge_ingest.consistency_reconcile._fetch_qdrant_artifact_counts",
            new_callable=AsyncMock,
            return_value=qdrant_counts,
        ),
    )


@pytest.mark.asyncio
async def test_reconcile_pg_qdrant_logs_failed_status_on_discrepancy():
    env = _patched_reconcile_env(
        pg_artifacts=[
            {
                "org_id": "org-1",
                "kb_slug": "kb",
                "path": "missing.md",
                "artifact_id": "art-missing",
            }
        ],
        recent_artifacts=[],
        qdrant_counts=Counter(),
    )
    with (
        env[0],
        env[1] as mock_list_active,
        env[2],
        env[3],
        patch("knowledge_ingest.consistency_reconcile.logger") as mock_logger,
    ):
        result = await reconcile_pg_qdrant()

    assert result["status"] == "failed"
    assert result["discrepancies_total"] == 1
    # Race-tolerance window: very recent artifacts are excluded from the
    # active list via the created_before cutoff.
    assert "created_before" in mock_list_active.call_args.kwargs
    mock_logger.info.assert_called_once()
    assert mock_logger.info.call_args.args == ("pg_qdrant_reconcile",)
    assert mock_logger.info.call_args.kwargs["status"] == "failed"
    assert mock_logger.info.call_args.kwargs["missing_in_qdrant"] == 1


@pytest.mark.asyncio
async def test_reconcile_pg_qdrant_logs_ok_status_when_inventories_match():
    env = _patched_reconcile_env(
        pg_artifacts=[
            {
                "org_id": "org-1",
                "kb_slug": "kb",
                "path": "present.md",
                "artifact_id": "art-present",
            }
        ],
        recent_artifacts=[],
        qdrant_counts=Counter({ArtifactKey("org-1", "kb", "present.md", "art-present"): 3}),
    )
    with (
        env[0],
        env[1],
        env[2],
        env[3],
        patch("knowledge_ingest.consistency_reconcile.logger") as mock_logger,
    ):
        result = await reconcile_pg_qdrant()

    assert result["status"] == "ok"
    assert result["discrepancies_total"] == 0
    assert mock_logger.info.call_args.kwargs["status"] == "ok"


@pytest.mark.asyncio
async def test_reconcile_pg_qdrant_ignores_recently_touched_artifacts():
    # An orphan-looking Qdrant key that belongs to a just-created replacement
    # artifact (excluded from the active list by created_before) must not be
    # reported as drift.
    env = _patched_reconcile_env(
        pg_artifacts=[],
        recent_artifacts=[
            {
                "org_id": "org-1",
                "kb_slug": "kb",
                "path": "fresh.md",
                "artifact_id": "art-fresh",
            }
        ],
        qdrant_counts=Counter({ArtifactKey("org-1", "kb", "fresh.md", "art-fresh"): 2}),
    )
    with env[0], env[1], env[2], env[3]:
        result = await reconcile_pg_qdrant()

    assert result["status"] == "ok"
    assert result["orphaned_in_qdrant"] == 0
    assert result["discrepancies_total"] == 0


@pytest.mark.asyncio
async def test_reconcile_pg_qdrant_logs_error_status_and_reraises_on_crash():
    conn = MagicMock()

    @asynccontextmanager
    async def _ctx():
        yield conn

    with (
        patch(
            "knowledge_ingest.consistency_reconcile.cross_org_admin_connection",
            return_value=_ctx(),
        ),
        patch(
            "knowledge_ingest.consistency_reconcile.pg_store.list_active_synced_artifacts",
            new_callable=AsyncMock,
            side_effect=RuntimeError("pg down"),
        ),
        patch("knowledge_ingest.consistency_reconcile.logger") as mock_logger,
        pytest.raises(RuntimeError, match="pg down"),
    ):
        await reconcile_pg_qdrant()

    # The crash must still emit the alertable pg_qdrant_reconcile event,
    # otherwise the Grafana rule never sees a status for that night.
    mock_logger.exception.assert_called_once()
    assert mock_logger.exception.call_args.args == ("pg_qdrant_reconcile",)
    assert mock_logger.exception.call_args.kwargs["status"] == "error"


def test_register_consistency_reconcile_task_registers_periodic_batch_task():
    app = _FakeApp()

    register_consistency_reconcile_task(app)

    assert len(app.periodics) == 1
    assert app.periodics[0][1] == {
        "cron": "30 3 * * *",
        "periodic_id": "pg-qdrant-reconcile",
    }
    assert len(app.tasks) == 1
    task_kwargs = app.tasks[0][1]
    assert task_kwargs["name"] == (
        "knowledge_ingest.consistency_reconcile.reconcile_pg_qdrant_periodic"
    )
    # Nightly batch lane — the full-collection scroll must not hold a slot
    # on the latency-sensitive I/O lane (adversarial review 2026-06-11).
    assert task_kwargs["queue"] == "rag-eval"
    assert task_kwargs["queueing_lock"] == "pg-qdrant-reconcile"
    assert task_kwargs["retry"] is not None
    assert hasattr(app, "reconcile_pg_qdrant_periodic")


@pytest.mark.asyncio
async def test_periodic_task_returns_reconcile_report():
    app = _FakeApp()
    register_consistency_reconcile_task(app)

    with patch(
        "knowledge_ingest.consistency_reconcile.reconcile_pg_qdrant",
        new_callable=AsyncMock,
        return_value={"status": "ok", "discrepancies_total": 0},
    ):
        result = await app.reconcile_pg_qdrant_periodic(timestamp=1_700_000_000)

    assert result == {"status": "ok", "discrepancies_total": 0}


def test_pg_qdrant_reconcile_alert_rule_present():
    parsed = yaml.safe_load(INGEST_ALERT_PATH.read_text(encoding="utf-8"))
    rules = parsed["groups"][0]["rules"]
    rule = next(r for r in rules if r["uid"] == "obs-001-pg-qdrant-divergence")

    assert rule["title"] == "pg_qdrant_reconcile_failed"
    assert rule["labels"]["alert_type"] == "consistency_reconcile"
    assert rule["labels"]["severity"] == "high"
    assert rule["annotations"]["runbook_url"] == "docs/runbooks/pg-qdrant-reconcile.md"
    query = rule["data"][0]["model"]["expr"]
    assert "event:pg_qdrant_reconcile" in query
    assert "status:failed" in query
    # A crashed job logs status=error; the alert must catch that too.
    assert "status:error" in query
