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
_ANSWER_SECTIONS = [
    "sender_statements",
    "kb_evidence",
    "open_questions",
    "verify_first",
]


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


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


@pytest.mark.asyncio
async def test_answer_shape_eval_verifies_raw_model_contract(monkeypatch):
    module = _load_script(monkeypatch)
    answer = (
        "[[KLAI_CORRESPONDENCE_SENDER_STATEMENTS]]\nSender.\n"
        "[[KLAI_CORRESPONDENCE_KB_EVIDENCE]]\nSupported (E1).\n"
        "[[KLAI_CORRESPONDENCE_OPEN_QUESTIONS]]\nOpen.\n"
        "[[KLAI_CORRESPONDENCE_VERIFY_FIRST]]\nVerify."
    )
    response = _FakeResponse(
        {"choices": [{"message": {"content": answer}}]}
    )
    client = AsyncMock()
    client.post = AsyncMock(return_value=response)
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)
    monkeypatch.setattr(module.httpx, "AsyncClient", lambda **kwargs: client)
    limiter = AsyncMock()
    limiter.acquire = AsyncMock()
    monkeypatch.setattr(module, "direct_mistral_limiter", lambda: limiter)
    canary = module.CorrespondenceCanary(
        id="shape",
        org_zitadel_id="1",
        query="From: A\nTo: B\nSubject: C\nBody",
        expected_chunks=["marker"],
        expected_answer_sections=_ANSWER_SECTIONS,
    )

    matches = await module._answer_shape_matches(
        canary,
        [{"title": "Source", "text": "Supported"}],
        pasted_correspondence=True,
    )

    assert matches is True
    limiter.acquire.assert_awaited_once()
    payload = client.post.await_args.kwargs["json"]
    assert "[[KLAI_CORRESPONDENCE_SENDER_STATEMENTS]]" in payload["messages"][0]["content"]


def test_answer_eval_messages_match_production_prompt_composition(monkeypatch):
    module = _load_script(monkeypatch)
    query = "From: A\nTo: B\nSubject: C\nBody"
    chunks = [{"title": "Source", "text": "Supported fact"}]

    messages = module._build_answer_eval_messages(
        query,
        chunks,
        pasted_correspondence=True,
    )

    context_prompt = module.build_kb_context_prompt(
        kb_narrow=False,
        context_chunks=chunks,
        trusted_sources=[],
        templates_block="",
        images_base_url=module.KB_IMAGES_BASE_URL,
        low_confidence_inject=False,
        low_confidence_injection_disabled=False,
        low_confidence_strict_text="",
        low_confidence_open_text="",
    )
    expected = [
        {"role": "system", "content": module.PASTED_CORRESPONDENCE_SCOPE},
        {"role": "user", "content": query},
    ]
    module.prepend_system_prefix(
        expected,
        module.compose_kb_mode_chat_prefix(False, context_prompt.context_block),
    )
    module.append_final_language_reminder(expected)

    assert messages == expected


@pytest.mark.asyncio
async def test_run_one_sample_fails_when_answer_shape_fails(monkeypatch):
    module = _load_script(monkeypatch)
    monkeypatch.setattr(
        module,
        "rewrite_and_classify",
        AsyncMock(return_value=("distilled", [], {"skipped": None})),
    )
    retrieval_response = _FakeResponse(
        {
            "chunks": [{"title": "Expected marker", "text": "raw body"}],
            "confidence_band": "low",
            "evidence_pack": {
                "items": [
                    {
                        "evidence_id": "E1",
                        "title": "Production evidence",
                        "text": "selected body",
                    }
                ]
            },
        }
    )
    monkeypatch.setattr(
        module, "retrieve", AsyncMock(return_value=retrieval_response)
    )
    shape_check = AsyncMock(return_value=False)
    monkeypatch.setattr(module, "_answer_shape_matches", shape_check)
    canary = module.CorrespondenceCanary(
        id="shape-failure",
        org_zitadel_id="1",
        query="From: A\nTo: B\nSubject: C\nBody",
        expected_chunks=["Expected marker"],
        expected_answer_sections=_ANSWER_SECTIONS,
    )

    retrieval_passed, contract_passed, _, _, skipped = await module._run_one_sample(
        _FakeHttp(), canary
    )

    assert retrieval_passed is True
    assert contract_passed is False
    assert skipped is None
    shape_check.assert_awaited_once()
    answer_chunks = shape_check.await_args.args[1]
    assert answer_chunks[0]["title"] == "Production evidence"
    assert answer_chunks[0]["text"] == "selected body"
    assert shape_check.await_args.kwargs["confidence_band"] == "low"


@pytest.mark.asyncio
async def test_run_one_sample_skips_before_retrieval_and_answer(monkeypatch):
    module = _load_script(monkeypatch)
    monkeypatch.setattr(
        module,
        "rewrite_and_classify",
        AsyncMock(return_value=("raw", [], {"skipped": "limiter_timeout"})),
    )
    retrieve_mock = AsyncMock()
    answer_mock = AsyncMock()
    monkeypatch.setattr(module, "retrieve", retrieve_mock)
    monkeypatch.setattr(module, "_answer_shape_matches", answer_mock)
    canary = module.CorrespondenceCanary(
        id="skipped",
        org_zitadel_id="1",
        query="From: A\nTo: B\nSubject: C\nBody",
        expected_chunks=["marker"],
        expected_answer_sections=_ANSWER_SECTIONS,
    )

    result = await module._run_one_sample(_FakeHttp(), canary)

    assert result == (False, False, "raw", True, "limiter_timeout")
    retrieve_mock.assert_not_awaited()
    answer_mock.assert_not_awaited()


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
        (True, True, "distilled-1", True, None),
        (False, True, "distilled-2", True, None),
        (False, False, "raw-query", True, "exception"),
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
async def test_run_canary_fails_when_any_answer_contract_sample_fails(monkeypatch):
    module = _load_script(monkeypatch)
    monkeypatch.setattr(module.asyncio, "sleep", AsyncMock())
    canary = module.CorrespondenceCanary(
        id="contract-regression",
        org_zitadel_id="1",
        query="q",
        expected_chunks=["marker"],
    )
    responses = [
        (True, True, "distilled-1", True, None),
        (True, True, "distilled-2", True, None),
        (True, False, "distilled-3", True, None),
    ]

    async def _fake_run_one_sample(http, canary_arg):
        return responses.pop(0)

    monkeypatch.setattr(module, "_run_one_sample", _fake_run_one_sample)

    summary = await module._run_canary(
        _FakeHttp(),
        canary,
        samples=3,
        pacer=module._SamplePacer(delay_seconds=0.0),
    )

    assert summary["retrieval_majority_pass"] is True
    assert summary["answer_contract_all_pass"] is False
    assert summary["majority_pass"] is False


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
        return (False, False, "raw-query", True, "destructive_rewrite")

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
        return (True, True, "distilled", True, None)

    monkeypatch.setattr(module, "_run_one_sample", _fake_run_one_sample)

    wait_mock = AsyncMock()
    pacer = module._SamplePacer(delay_seconds=0.0)
    monkeypatch.setattr(pacer, "wait", wait_mock)

    await module._run_canary(_FakeHttp(), canary, samples=3, pacer=pacer)

    assert wait_mock.await_count == 3
