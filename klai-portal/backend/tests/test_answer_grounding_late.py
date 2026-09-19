"""A check that outlives its budget still reports what it would have found.

The visitor gets the answer at the budget exactly as before. What changed is
that the slow call is no longer thrown away: without its verdict, the daily
report can say how often the budget was hit but never whether those answers
carried an invention, which is the only question that decides whether the
budget should move (logboek 2.37).
"""

from __future__ import annotations

import asyncio

import pytest

from app.services import answer_grounding as ag


class _Settings:
    answer_grounding_model = "klai-medium"
    litellm_base_url = "http://litellm:4000"
    litellm_master_key = "k"


_VERDICT = (
    '{"statements": ['
    '{"statement": "Bel 020-1234567.", "evidence": "", "support": "not_in_articles"},'
    '{"statement": "Dat kost 5 euro.", "evidence": "", "support": "contradicted"}'
    "]}"
)


@pytest.mark.asyncio
async def test_the_visitor_is_not_kept_waiting_past_the_budget(monkeypatch):
    released = asyncio.Event()

    async def _slow_post(**_kwargs):
        await released.wait()
        return _VERDICT

    monkeypatch.setattr(ag, "_post", _slow_post)
    monkeypatch.setattr(ag, "_CHECK_TIMEOUT_SECONDS", 0.05)

    result = await ag.check_grounding(question="Q", draft="D", articles=[], settings=_Settings(), org_id=8)

    assert result is None, "past the budget the caller gets what it always got"
    released.set()
    await asyncio.sleep(0.01)


@pytest.mark.asyncio
async def test_the_late_verdict_is_logged_when_the_call_finishes(monkeypatch):
    released = asyncio.Event()
    logged: list[tuple[str, dict]] = []

    async def _slow_post(**_kwargs):
        await released.wait()
        return _VERDICT

    monkeypatch.setattr(ag, "_post", _slow_post)
    monkeypatch.setattr(ag, "_CHECK_TIMEOUT_SECONDS", 0.05)
    monkeypatch.setattr(ag.logger, "info", lambda event, **kw: logged.append((event, kw)))

    await ag.check_grounding(question="Q", draft="D", articles=[], settings=_Settings(), org_id=8)
    released.set()
    await asyncio.sleep(0.02)

    late = [kw for event, kw in logged if event == "answer_grounding_late"]
    assert len(late) == 1, "the call kept running after the budget and reported back"
    assert late[0]["org_id"] == 8
    assert late[0]["unsupported"] == 2
    assert late[0]["contradicted"] == 1
    assert late[0]["worth_repairing"] is True


@pytest.mark.asyncio
async def test_a_check_within_budget_behaves_as_before(monkeypatch):
    async def _fast_post(**_kwargs):
        return _VERDICT

    monkeypatch.setattr(ag, "_post", _fast_post)

    result = await ag.check_grounding(question="Q", draft="D", articles=[], settings=_Settings())

    assert result is not None
    assert len(result.unsupported) == 2
