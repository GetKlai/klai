"""Retry-strategy configuration for the enrichment tasks.

Regression for the intermedia.com incident (2026-08-14): enrichment failures
are dominated by LiteLLM 429s during bulk crawls, and the previous
``RetryStrategy(max_attempts=2)`` (implicit wait=0) burned every attempt
inside the same rate-limit window — the artifact failed 3x within 20 seconds
and went permanent-failed. These tests pin the exponential backoff so a
future "simplification" cannot silently reintroduce instant retries.
"""

from __future__ import annotations

import sys

import pytest

from knowledge_ingest import enrichment_tasks, queues


class _RecordingRetryStrategy:
    def __init__(self, **kwargs):
        self.kwargs = kwargs


class _FakeApp:
    """Records @app.task(...) registrations without running anything."""

    def __init__(self) -> None:
        self.task_kwargs: list[dict] = []

    def task(self, **kwargs):
        self.task_kwargs.append(kwargs)

        def _decorator(fn):
            return fn

        return _decorator


@pytest.fixture
def registered_tasks(monkeypatch) -> dict[str, dict]:
    """Register tasks against a fake app; return {queue_name: retry_kwargs}."""
    monkeypatch.setattr(sys.modules["procrastinate"], "RetryStrategy", _RecordingRetryStrategy)
    app = _FakeApp()
    enrichment_tasks._register_tasks(app)
    by_queue: dict[str, dict] = {}
    for kwargs in app.task_kwargs:
        retry = kwargs.get("retry")
        if isinstance(retry, _RecordingRetryStrategy):
            by_queue[kwargs["queue"]] = retry.kwargs
    return by_queue


def test_interactive_enrichment_backs_off_exponentially(registered_tasks):
    retry = registered_tasks[queues.ENRICH_INTERACTIVE]
    assert retry["max_attempts"] == 4
    assert retry["exponential_wait"] == 3  # waits 3s, 9s, 27s


def test_bulk_enrichment_backs_off_exponentially(registered_tasks):
    retry = registered_tasks[queues.ENRICH_BULK]
    assert retry["max_attempts"] == 5
    assert retry["exponential_wait"] == 4  # waits 4s, 16s, 64s, 256s


def test_graphiti_backs_off_exponentially(registered_tasks):
    """September 2026: max_attempts=3 (~84s of backoff) gave up on an episode
    a few seconds after graph.py's own inner retry loop already had -- 50
    episodes dropped for good that month. The bounded lifetime must be hours,
    not seconds, so a saturated klai-fast budget gets a real chance to drain.
    """
    retry = registered_tasks[queues.GRAPHITI_BULK]
    # procrastinate counts retries after the first run: 8 means 9 runs, and
    # the waits 4s, 16s, ..., 65536s add up to ~24h.
    assert retry["max_attempts"] == 8
    assert retry["exponential_wait"] == 4


def test_no_enrichment_task_retries_instantly(registered_tasks):
    """Every LLM-calling task must have SOME backoff — wait-free retries are
    exactly the 429 failure mode this configuration exists to prevent."""
    for queue_name in (queues.ENRICH_INTERACTIVE, queues.ENRICH_BULK, queues.GRAPHITI_BULK):
        retry = registered_tasks[queue_name]
        assert (
            retry.get("wait", 0) or retry.get("linear_wait", 0) or retry.get("exponential_wait", 0)
        ), f"task on {queue_name} retries with zero wait"


class TestGraphitiEpisodeFailureExhaustion:
    """A failed episode must be re-queued (WARNING) on every attempt except
    the last, and fail loudly (ERROR, artifact_id in the log) only once
    procrastinate has actually given up -- not dropped silently the moment
    graph.py's own inner retry loop returns None, the September 2026 bug."""

    def test_early_attempt_is_re_queued_not_dropped(self):
        event, exhausted = enrichment_tasks._graphiti_episode_failure_event(
            attempt=0, max_attempts=enrichment_tasks._GRAPHITI_MAX_ATTEMPTS
        )
        assert exhausted is False
        assert event == "graphiti_episode_partial"

    def test_second_to_last_attempt_still_re_queues(self):
        """Regression anchor for the old max_attempts=3 cutoff: attempt index
        2 (the 3rd try) used to be the final one and must now still retry."""
        event, exhausted = enrichment_tasks._graphiti_episode_failure_event(
            attempt=2, max_attempts=enrichment_tasks._GRAPHITI_MAX_ATTEMPTS
        )
        assert exhausted is False
        assert event == "graphiti_episode_partial"

    def test_attempt_before_the_last_is_still_retried(self):
        """procrastinate only gives up once job.attempts reaches max_attempts."""
        _, exhausted = enrichment_tasks._graphiti_episode_failure_event(
            attempt=enrichment_tasks._GRAPHITI_MAX_ATTEMPTS - 1,
            max_attempts=enrichment_tasks._GRAPHITI_MAX_ATTEMPTS,
        )
        assert exhausted is False

    def test_last_attempt_fails_loudly(self):
        event, exhausted = enrichment_tasks._graphiti_episode_failure_event(
            attempt=enrichment_tasks._GRAPHITI_MAX_ATTEMPTS,
            max_attempts=enrichment_tasks._GRAPHITI_MAX_ATTEMPTS,
        )
        assert exhausted is True
        assert event == "graphiti_episode_exhausted"
