"""Regression tests for the 2026-08-17 multi-question hallucination incident.

A Strict-mode user pasted a message with 11 technical questions. One
retrieval pass returned evidence for roughly one topic; the model answered
all 11 definitively, reused a number from an unrelated context as a fact,
and a follow-up refusal answer still cited a 0.05-relevance source.

Four fixes are locked in here (all fixtures synthetic):

1. ``has_direct_evidence_for_query`` requires token COVERAGE, not any
   single-token overlap, before skipping the low-confidence guard.
2. Multi-question messages suppress the strict deterministic refusal but
   inject ``MULTI_QUESTION_GUARD_TEXT`` so the model judges coverage per
   question.
3. Evidence chunks below ``KLAI_KB_MIN_EVIDENCE_SCORE`` are dropped before
   prompt build and citation rendering.
4. The grounded system prompt carries the multi-part-questions and
   derived-values rules (byte-identical in canonical + vendored copies —
   see test_klai_chat_prompts_drift.py).
"""

from __future__ import annotations

import importlib
import sys
import types

import pytest

from tests.klai_module_reset import reset_klai_kb_modules


@pytest.fixture(autouse=True)
def _mock_litellm():
    litellm_mod = types.ModuleType("litellm")
    integrations_mod = types.ModuleType("litellm.integrations")
    custom_logger_mod = types.ModuleType("litellm.integrations.custom_logger")

    class CustomLogger:
        async def async_pre_call_hook(self, *args, **kwargs):
            pass

        async def async_post_call_success_hook(self, *args, **kwargs):
            pass

        async def async_post_call_failure_hook(self, *args, **kwargs):
            pass

    custom_logger_mod.CustomLogger = CustomLogger
    litellm_mod.integrations = integrations_mod
    integrations_mod.custom_logger = custom_logger_mod

    sys.modules["litellm"] = litellm_mod
    sys.modules["litellm.integrations"] = integrations_mod
    sys.modules["litellm.integrations.custom_logger"] = custom_logger_mod

    yield

    for mod_name in [
        "litellm",
        "litellm.integrations",
        "litellm.integrations.custom_logger",
    ]:
        sys.modules.pop(mod_name, None)
    reset_klai_kb_modules()


def _load_hook(monkeypatch, extra_env=None):
    env = {
        "PORTAL_INTERNAL_SECRET": "test-portal-secret",
        "RETRIEVAL_INTERNAL_SECRET": "test-retrieval-secret",
        "KNOWLEDGE_RETRIEVE_URL": "http://retrieval-api:8040/retrieve",
        "PORTAL_API_URL": "http://portal-api:8000",
    }
    if extra_env:
        env.update(extra_env)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    reset_klai_kb_modules()
    import klai_knowledge

    importlib.reload(klai_knowledge)
    return klai_knowledge


# ---------------------------------------------------------------------------
# 1. Direct-evidence coverage requirement
# ---------------------------------------------------------------------------


class TestDirectEvidenceCoverage:
    def test_single_shared_token_no_longer_skips_the_guard(self, monkeypatch) -> None:
        """A webhook question sharing only the token 'webhook' with tangential
        webhook chunks must keep the guard active (incident message 2)."""
        klai_knowledge = _load_hook(monkeypatch)
        assert (
            klai_knowledge._should_apply_low_confidence_injection(
                "low",
                user_query="wat is de maximale responstijd van de webhook koppeling?",
                evidence_chunks=[
                    {
                        "title": "Webhook voorbeelden",
                        "text": "Heb ik een server nodig om webhooks te gebruiken? Ja.",
                    }
                ],
            )
            is True
        )

    def test_two_covered_tokens_still_skip_the_guard(self, monkeypatch) -> None:
        klai_knowledge = _load_hook(monkeypatch)
        assert (
            klai_knowledge._should_apply_low_confidence_injection(
                "low",
                user_query="wat is de maximale responstijd van de webhook?",
                evidence_chunks=[
                    {
                        "title": "Webhook instellingen",
                        "text": "De responstijd van de webhook is instelbaar.",
                    }
                ],
            )
            is False
        )

    def test_single_token_query_keeps_single_match_behaviour(self, monkeypatch) -> None:
        """'wie is jantine?' has one salient token; one match must still skip
        the guard (pre-existing contract in test_low_confidence_injection)."""
        klai_knowledge = _load_hook(monkeypatch)
        assert (
            klai_knowledge._should_apply_low_confidence_injection(
                "low",
                user_query="wie is jantine?",
                evidence_chunks=[
                    {
                        "title": "CV_Jantine_Voorbeeld.pdf",
                        "text": "Jantine Voorbeeld\nAI-ontwikkelaar & adviseur",
                    }
                ],
            )
            is False
        )

    def test_coverage_may_span_multiple_chunks(self, monkeypatch) -> None:
        klai_knowledge = _load_hook(monkeypatch)
        assert (
            klai_knowledge._should_apply_low_confidence_injection(
                "low",
                user_query="hoe stel ik de responstijd van de webhook in?",
                evidence_chunks=[
                    {"title": "Webhook basis", "text": "Over de webhook module."},
                    {"title": "Timers", "text": "De responstijd stel je hier in."},
                ],
            )
            is False
        )


# ---------------------------------------------------------------------------
# 2. Multi-question detection + guard text
# ---------------------------------------------------------------------------


class TestMultiQuestionGuard:
    def test_detects_multiple_questions(self, monkeypatch) -> None:
        klai_knowledge = _load_hook(monkeypatch)
        assert klai_knowledge._is_multi_question_query(
            "Wat moet de webhook teruggeven? Wat is de maximale responstijd? "
            "Welke fallback wordt uitgevoerd?"
        )

    def test_single_question_is_not_multi(self, monkeypatch) -> None:
        klai_knowledge = _load_hook(monkeypatch)
        assert not klai_knowledge._is_multi_question_query(
            "Wat is de maximale responstijd van de webhook?"
        )
        assert not klai_knowledge._is_multi_question_query(None)

    def test_guard_text_demands_per_question_coverage(self, monkeypatch) -> None:
        klai_knowledge = _load_hook(monkeypatch)
        text = klai_knowledge._MULTI_QUESTION_GUARD_TEXT
        assert "per question" in text.lower()
        assert "number of answers must equal the number of questions" in text.lower()
        assert "do not invent or substitute questions" in text.lower()
        # English-only instruction block (SPEC-RAG-MULTILINGUAL-CHAT-001 REQ-10)
        assert "verzin" not in text.lower()

    def test_context_builder_appends_guard_when_set(self, monkeypatch) -> None:
        _load_hook(monkeypatch)
        from klai_kb_context_prompt import build_kb_context_prompt
        from klai_kb_confidence_policy import MULTI_QUESTION_GUARD_TEXT

        prompt = build_kb_context_prompt(
            kb_narrow=True,
            context_chunks=[{"chunk_id": "c1", "text": "inhoud", "title": "Titel"}],
            trusted_sources=[],
            templates_block="",
            images_base_url="https://example.test",
            low_confidence_inject=False,
            low_confidence_injection_disabled=False,
            low_confidence_strict_text="STRICT-GUARD",
            low_confidence_open_text="OPEN-GUARD",
            multi_question_guard_text=MULTI_QUESTION_GUARD_TEXT,
        )
        assert MULTI_QUESTION_GUARD_TEXT in prompt.context_block
        assert "STRICT-GUARD" not in prompt.context_block

    def test_context_builder_omits_guard_by_default(self, monkeypatch) -> None:
        _load_hook(monkeypatch)
        from klai_kb_context_prompt import build_kb_context_prompt

        prompt = build_kb_context_prompt(
            kb_narrow=True,
            context_chunks=[{"chunk_id": "c1", "text": "inhoud", "title": "Titel"}],
            trusted_sources=[],
            templates_block="",
            images_base_url="https://example.test",
            low_confidence_inject=False,
            low_confidence_injection_disabled=False,
            low_confidence_strict_text="STRICT-GUARD",
            low_confidence_open_text="OPEN-GUARD",
        )
        assert "multi-part question" not in prompt.context_block


# ---------------------------------------------------------------------------
# 3. Evidence score floor
# ---------------------------------------------------------------------------


class TestEvidenceScoreFloor:
    def test_default_floor_value(self, monkeypatch) -> None:
        klai_knowledge = _load_hook(monkeypatch)
        assert klai_knowledge.KLAI_KB_MIN_EVIDENCE_SCORE == 0.15

    def test_env_override(self, monkeypatch) -> None:
        klai_knowledge = _load_hook(
            monkeypatch, extra_env={"KLAI_KB_MIN_EVIDENCE_SCORE": "0.30"}
        )
        assert klai_knowledge.KLAI_KB_MIN_EVIDENCE_SCORE == 0.30

    def test_incident_chunk_at_005_is_below_floor(self, monkeypatch) -> None:
        """The refusal answer cited a 0.05-relevance tag page; that chunk must
        now be dropped before prompt build and citation rendering."""
        klai_knowledge = _load_hook(monkeypatch)
        assert klai_knowledge._chunk_below_evidence_floor(
            {"chunk_id": "c1", "reranker_score": 0.05033}
        )

    def test_healthy_chunk_is_kept(self, monkeypatch) -> None:
        klai_knowledge = _load_hook(monkeypatch)
        assert not klai_knowledge._chunk_below_evidence_floor(
            {"chunk_id": "c1", "reranker_score": 0.61}
        )

    def test_chunk_without_score_is_kept_fail_open(self, monkeypatch) -> None:
        klai_knowledge = _load_hook(monkeypatch)
        assert not klai_knowledge._chunk_below_evidence_floor({"chunk_id": "c1"})

    def test_first_present_score_key_wins(self, monkeypatch) -> None:
        """reranker_score is authoritative when present; a high raw score on
        the same chunk must not resurrect it."""
        klai_knowledge = _load_hook(monkeypatch)
        assert klai_knowledge._chunk_below_evidence_floor(
            {"chunk_id": "c1", "reranker_score": 0.05, "score": 0.9}
        )

    def test_raw_score_alone_never_drops(self, monkeypatch) -> None:
        """The raw retrieval ``score`` uses a different scale and defaults to
        0.0 in several producers — it must not drop chunks on its own."""
        klai_knowledge = _load_hook(monkeypatch)
        assert not klai_knowledge._chunk_below_evidence_floor(
            {"chunk_id": "c1", "score": 0.0}
        )


# ---------------------------------------------------------------------------
# 4. Grounded prompt carries the new rules
# ---------------------------------------------------------------------------


class TestGroundedPromptRules:
    def test_multi_part_question_rule_present(self, monkeypatch) -> None:
        _load_hook(monkeypatch)
        from klai_chat_prompts import GROUNDED_CHAT_SYSTEM_PROMPT

        text = GROUNDED_CHAT_SYSTEM_PROMPT
        assert "## Multi-part questions" in text
        assert "MUST equal the number of questions" in text
        assert "never invent questions the user did not ask" in text

    def test_derived_values_rule_present(self, monkeypatch) -> None:
        _load_hook(monkeypatch)
        from klai_chat_prompts import GROUNDED_CHAT_SYSTEM_PROMPT

        text = GROUNDED_CHAT_SYSTEM_PROMPT
        assert "## Numbers and derived values" in text
        assert "DIFFERENT" in text
        assert "NOT evidence" in text
