"""Tests for SPEC-KB-026 R5: Procrastinate auto-categorise job endpoint.

Verifies the new job enqueue endpoint and Procrastinate task registration.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class TestAutoCategoriseJobEndpoint:
    """R5: POST /ingest/v1/taxonomy/auto-categorise-job enqueues a Procrastinate job."""

    @pytest.mark.asyncio
    async def test_enqueue_endpoint_returns_202(self, client):
        """Endpoint accepts request and returns job_id."""
        mock_proc_app = MagicMock()
        mock_task = MagicMock()
        mock_task.configure = MagicMock(return_value=mock_task)
        mock_task.defer_async = AsyncMock(return_value=42)
        mock_proc_app.run_auto_categorise = mock_task

        with patch("knowledge_ingest.enrichment_tasks.get_app", return_value=mock_proc_app):
            resp = client.post(
                "/ingest/v1/taxonomy/auto-categorise-job",
                json={
                    "org_id": "org1",
                    "kb_slug": "kb1",
                    "node_id": 5,
                    "cluster_centroid": [0.1, 0.2, 0.3],
                },
                headers={"X-Internal-Secret": ""},
            )

        assert resp.status_code == 202
        data = resp.json()
        assert data["job_id"] == 42
        assert data["status"] == "queued"

    @pytest.mark.asyncio
    async def test_enqueue_with_null_centroid(self, client):
        """Endpoint accepts null cluster_centroid."""
        mock_proc_app = MagicMock()
        mock_task = MagicMock()
        mock_task.configure = MagicMock(return_value=mock_task)
        mock_task.defer_async = AsyncMock(return_value=99)
        mock_proc_app.run_auto_categorise = mock_task

        with patch("knowledge_ingest.enrichment_tasks.get_app", return_value=mock_proc_app):
            resp = client.post(
                "/ingest/v1/taxonomy/auto-categorise-job",
                json={
                    "org_id": "org1",
                    "kb_slug": "kb1",
                    "node_id": 10,
                    "cluster_centroid": None,
                },
                headers={"X-Internal-Secret": ""},
            )

        assert resp.status_code == 202


class TestAutoCategoriseTaskRegistration:
    """R5: Procrastinate task for auto-categorise is registered correctly."""

    def test_task_registered_with_retry(self):
        """run_auto_categorise task should be registered on the Procrastinate app."""
        mock_app = MagicMock()
        registered_tasks = {}

        def mock_task(**kwargs):
            def decorator(fn):
                registered_tasks[fn.__name__] = kwargs
                mock_app.__setattr__(fn.__name__, fn)
                return fn
            return decorator

        mock_app.task = mock_task

        # procrastinate must be importable for register_auto_categorise_task
        import sys
        mock_procrastinate = MagicMock()

        # Create a real RetryStrategy-like object
        class FakeRetryStrategy:
            def __init__(self, max_attempts=1, wait=0):
                self.max_attempts = max_attempts
                self.wait = wait

        mock_procrastinate.RetryStrategy = FakeRetryStrategy

        with patch.dict(sys.modules, {"procrastinate": mock_procrastinate}):
            from knowledge_ingest.clustering_tasks import register_auto_categorise_task
            register_auto_categorise_task(mock_app)

        assert "run_auto_categorise" in registered_tasks
        task_config = registered_tasks["run_auto_categorise"]
        assert task_config["queue"] == "taxonomy-backfill"
        assert task_config["retry"].max_attempts == 3


