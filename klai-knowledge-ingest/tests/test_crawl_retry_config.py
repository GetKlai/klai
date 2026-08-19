"""Retry configuration for deploy-interrupted crawl jobs."""

from __future__ import annotations

import asyncio
import sys
from typing import Any

from knowledge_ingest import crawl_tasks, queues


class _RecordingRetryStrategy:
    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs


class _FakeApp:
    def __init__(self) -> None:
        self.task_kwargs: list[dict[str, Any]] = []

    def task(self, **kwargs: Any):
        self.task_kwargs.append(kwargs)

        def _decorator(fn):
            return fn

        return _decorator


def test_crawl_retries_only_bounded_worker_shutdown_cancellation(monkeypatch) -> None:
    monkeypatch.setattr(sys.modules["procrastinate"], "RetryStrategy", _RecordingRetryStrategy)
    app = _FakeApp()

    crawl_tasks.register_crawl_tasks(app)

    retry = app.task_kwargs[0]["retry"]
    assert app.task_kwargs[0]["queue"] == queues.CRAWL_JOBS
    assert retry.kwargs["max_attempts"] == 20
    assert retry.kwargs["wait"] == 5
    assert tuple(retry.kwargs["retry_exceptions"]) == (asyncio.CancelledError,)
