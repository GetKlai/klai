"""A retrieval round never lets a model write an answer, whichever way the turn leaves retrieval.

The probe of ``meetlat retrieval`` (scripts/meetlat.py) cuts an armed turn short in the
retrieval step. These tests drive ``chat_completions`` end to end, with the harness of
test_one_pipeline_answer_decision.py, down the paths that do not reach the selection step
and assert the writer's LiteLLM call never left portal-api.

synthetic-data: generator=hand-written seed=0 (fictional org "Acme Telecom", example.com URLs).
"""

from __future__ import annotations

import sys
from pathlib import Path

import httpx
import pytest
from test_one_pipeline_answer_decision import QUESTION, _internal, _LiteLLM, _pack, _turn

from app.services.chat_profile import ChatProfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import meetlat


@pytest.fixture
def armed_probe(monkeypatch):
    """The probe installed and armed for one turn; the three module functions it replaces are restored afterwards."""
    from app.services import retrieval_context

    for name in ("_call_retrieval", "_choose_and_prompt", "_without_search"):
        monkeypatch.setattr(retrieval_context, name, getattr(retrieval_context, name))
    meetlat._install_retrieval_probe()
    meetlat._arm()
    yield
    meetlat._take()


PACK = {"evidence_pack": _pack(), "confidence_band": "high"}


@pytest.mark.parametrize(
    ("profile", "question"),
    [
        (_internal(kb_mode="general", kb_slugs=None), QUESTION),
        (_internal(kb_mode="open"), "   "),
        (_internal(kb_mode="open"), "Bedankt!"),
    ],
    ids=["internal_general_mode", "internal_without_a_query", "internal_trivial_turn"],
)
@pytest.mark.asyncio
async def test_a_turn_that_searches_nothing_is_not_written_in_a_retrieval_round(
    monkeypatch, armed_probe, profile, question
):
    llm = _LiteLLM()

    await _turn(monkeypatch, llm, profile=profile, question=question, retrieval=PACK)

    assert llm.of("answer") == []
    assert meetlat._probe["record"] is None


@pytest.mark.asyncio
async def test_a_widget_visitor_asking_for_a_person_is_not_written_in_a_retrieval_round(monkeypatch, armed_probe):
    llm = _LiteLLM()

    await _turn(
        monkeypatch,
        llm,
        profile=ChatProfile(surface="widget"),
        question="Kan ik een medewerker spreken?",
        retrieval=PACK,
        key_id="wgt_acme",
    )

    assert llm.of("answer") == []


@pytest.mark.asyncio
async def test_a_widget_turn_whose_identity_assertion_degrades_is_not_written_in_a_retrieval_round(
    monkeypatch, armed_probe
):
    llm = _LiteLLM()
    degraded = httpx.Response(403, json={"detail": {"error": "identity_assertion_failed"}})

    await _turn(
        monkeypatch,
        llm,
        profile=ChatProfile(surface="widget"),
        retrieval_response=degraded,
        key_id="wgt_acme",
    )

    assert llm.of("answer") == []
    assert meetlat._probe["record"] == {"failed": True}
