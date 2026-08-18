"""Unit tests for Sol delta-review Fix 4 (dedicated live-eval throttle
budget) and Fix 5 (skipped samples must not count toward a canary's pass
rate) in scripts/eval_pasted_correspondence_live.py.

No network calls — httpx.AsyncClient / rewrite_and_classify / retrieve are
never exercised for real here; running the actual distillation + retrieval
pipeline remains scripts/eval_pasted_correspondence_live.py's own manually
invoked job (see its module docstring). These tests exercise only the
script's own orchestration logic (env-var budget defaulting, client-side
pacing, skip-handling) in isolation — same split as
tests/test_correspondence_eval.py vs. the live script itself.
"""

from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from tests.klai_module_reset import reset_klai_kb_modules

_SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"


def _load_script(monkeypatch, extra_env=None):
    """Import eval_pasted_correspondence_live fresh, with a controlled env.

    Mirrors test_query_rewrite.py's _load_hook pattern: reset the klai_kb_*
    module cache (klai_kb_query_rewrite reads DIRECT_MISTRAL_RATE_LIMIT_RPS/
    _BURST into module-level constants at import time, so a stale cached
    import would silently keep an earlier test's env values) and set a
    baseline env before (re)importing.
    """
    env = {
        "KNOWLEDGE_RETRIEVE_URL": "http://retrieval-api:8040/retrieve",
        "PORTAL_INTERNAL_SECRET": "test-portal-secret",
        "RETRIEVAL_INTERNAL_SECRET": "test-retrieval-secret",
        "MISTRAL_API_KEY": "test-mistral-key",
    }
    if extra_env:
        env.update(extra_env)
    for key in ("DIRECT_MISTRAL_RATE_LIMIT_RPS", "DIRECT_MISTRAL_RATE_LIMIT_BURST"):
        if key not in env:
            monkeypatch.delenv(key, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)

    reset_klai_kb_modules()
    sys.modules.pop("eval_pasted_correspondence_live", None)

    if str(_SCRIPT_DIR) not in sys.path:
        sys.path.insert(0, str(_SCRIPT_DIR))

    module = importlib.import_module("eval_pasted_correspondence_live")
    return module


# ---------------------------------------------------------------------------
# Fix 4 — dedicated throttle budget for the second process
# ---------------------------------------------------------------------------


def test_sets_dedicated_rate_limit_defaults_when_unconfigured(monkeypatch):
    module = _load_script(monkeypatch)

    assert os.environ["DIRECT_MISTRAL_RATE_LIMIT_RPS"] == "0.05"
    assert os.environ["DIRECT_MISTRAL_RATE_LIMIT_BURST"] == "1"
    assert module.DIRECT_MISTRAL_RATE_LIMIT_RPS == pytest.approx(0.05)


def test_operator_override_of_rate_limit_env_vars_wins(monkeypatch):
    module = _load_script(
        monkeypatch,
        extra_env={
            "DIRECT_MISTRAL_RATE_LIMIT_RPS": "0.2",
            "DIRECT_MISTRAL_RATE_LIMIT_BURST": "5",
        },
    )

    assert os.environ["DIRECT_MISTRAL_RATE_LIMIT_RPS"] == "0.2"
    assert os.environ["DIRECT_MISTRAL_RATE_LIMIT_BURST"] == "5"
    assert module.DIRECT_MISTRAL_RATE_LIMIT_RPS == pytest.approx(0.2)


# ---------------------------------------------------------------------------
# Fix 4 — client-side pacing
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sample_pacer_does_not_sleep_before_the_first_wait(monkeypatch):
    module = _load_script(monkeypatch)
    sleep_mock = AsyncMock()
    monkeypatch.setattr(module.asyncio, "sleep", sleep_mock)

    pacer = module._SamplePacer(delay_seconds=21.0)
    await pacer.wait()

    sleep_mock.assert_not_called()


@pytest.mark.asyncio
async def test_sample_pacer_sleeps_the_configured_delay_on_later_waits(monkeypatch):
    module = _load_script(monkeypatch)
    sleep_mock = AsyncMock()
    monkeypatch.setattr(module.asyncio, "sleep", sleep_mock)

    pacer = module._SamplePacer(delay_seconds=21.0)
    await pacer.wait()
    await pacer.wait()
    await pacer.wait()

    assert sleep_mock.await_count == 2
    sleep_mock.assert_awaited_with(21.0)


# ---------------------------------------------------------------------------
# Fix 5 — skipped samples excluded from the pass rate
# ---------------------------------------------------------------------------


class _FakeHttp:
    """Placeholder — _run_one_sample is monkeypatched in these tests, so no
    real httpx.AsyncClient behavior is needed."""


@pytest.mark.asyncio
async def test_run_canary_excludes_skipped_samples_from_pass_rate(monkeypatch):
    module = _load_script(monkeypatch)
    monkeypatch.setattr(module.asyncio, "sleep", AsyncMock())

    canary = module.CorrespondenceCanary(
        id="canary-1",
        org_zitadel_id="1",
        query="q",
        expected_chunks=["marker"],
    )

    # 3 samples: one real pass, one real fail, one limiter-timeout skip.
    responses = [
        (True, "distilled-1", True, None),
        (False, "distilled-2", True, None),
        (False, "raw-query", True, "exception"),
    ]

    async def _fake_run_one_sample(http, canary_arg):
        assert canary_arg is canary
        return responses.pop(0)

    monkeypatch.setattr(module, "_run_one_sample", _fake_run_one_sample)

    pacer = module._SamplePacer(delay_seconds=0.0)
    summary = await module._run_canary(_FakeHttp(), canary, samples=3, pacer=pacer)

    # Only the 2 non-skipped samples count toward total/passed/pass_rate.
    assert summary["total"] == 2
    assert summary["passed"] == 1
    assert summary["pass_rate"] == pytest.approx(0.5)
    assert summary["skipped_samples"] == ["exception"]
    assert summary["distilled_queries"] == ["distilled-1", "distilled-2"]


@pytest.mark.asyncio
async def test_run_canary_all_samples_invalid_marks_canary_failed_with_note(
    monkeypatch,
):
    module = _load_script(monkeypatch)
    monkeypatch.setattr(module.asyncio, "sleep", AsyncMock())

    canary = module.CorrespondenceCanary(
        id="canary-all-invalid",
        org_zitadel_id="1",
        query="q",
        expected_chunks=["marker"],
    )

    async def _always_skipped(http, canary_arg):
        return (False, "raw-query", True, "destructive_rewrite")

    monkeypatch.setattr(module, "_run_one_sample", _always_skipped)

    pacer = module._SamplePacer(delay_seconds=0.0)
    summary = await module._run_canary(_FakeHttp(), canary, samples=3, pacer=pacer)

    assert summary["majority_pass"] is False
    assert summary["total"] == 0
    assert summary["skipped_samples"] == [
        "destructive_rewrite",
        "destructive_rewrite",
        "destructive_rewrite",
    ]
    assert summary.get("note")


@pytest.mark.asyncio
async def test_run_canary_paces_between_every_sample_except_the_first(monkeypatch):
    """Verifies _run_canary actually calls pacer.wait() before each sample —
    the sleep count assertion lives in the _SamplePacer-level tests above,
    this confirms the wiring inside _run_canary itself."""
    module = _load_script(monkeypatch)

    canary = module.CorrespondenceCanary(
        id="canary-pacing", org_zitadel_id="1", query="q", expected_chunks=["marker"]
    )

    async def _fake_run_one_sample(http, canary_arg):
        return (True, "distilled", True, None)

    monkeypatch.setattr(module, "_run_one_sample", _fake_run_one_sample)

    wait_mock = AsyncMock()
    pacer = module._SamplePacer(delay_seconds=0.0)
    monkeypatch.setattr(pacer, "wait", wait_mock)

    await module._run_canary(_FakeHttp(), canary, samples=3, pacer=pacer)

    assert wait_mock.await_count == 3
