"""Moved from deploy/litellm/tests/test_multi_question_and_evidence_floor.py and
test_sub_question_fanout_hook.py (one-chat-pipeline slice 4): the multi-part
question rule (PR #1626), the sub-question splitter and the Strict evidence
floor, now in portal-api for every surface. All fixtures synthetic.
"""

from __future__ import annotations

import time

from klai_chat_prompts.kb_modes import MULTI_QUESTION_GUARD_TEXT

from app.services.chat_turn_rules import is_multi_question_query, split_sub_questions
from app.services.partner_chat import _chunk_below_evidence_floor

INCIDENT_MESSAGE = (
    "Geef netjes antwoorden:\n"
    "Wat moet onze webhook exact teruggeven om te routeren?\n"
    "Wat is de maximale responstijd van deze webhook?\n"
    "Welke fallback wordt uitgevoerd wanneer onze webhook niet bereikbaar is?\n"
    "Worden gespreksmeldingen bij een tijdelijke storing opnieuw aangeboden?\n"
    "Hoe worden gespreksmeldingen beveiligd?\n"
    "Kunnen we een apart technisch serviceaccount gebruiken?\n"
    "Hoe lang na een gesprek is de opname gemiddeld beschikbaar?\n"
)


class TestMultiQuestion:
    def test_detects_multiple_questions(self) -> None:
        assert is_multi_question_query(
            "Wat moet de webhook teruggeven? Wat is de maximale responstijd? Welke fallback wordt uitgevoerd?"
        )

    def test_single_question_is_not_multi(self) -> None:
        assert not is_multi_question_query("Wat is de maximale responstijd van de webhook?")
        assert not is_multi_question_query(None)

    def test_two_questions_behind_one_question_mark(self) -> None:
        """Missing this gives the employee one answer for two questions."""
        assert is_multi_question_query("Wat is de opzegtermijn en hoe zeg ik namens een klant op?")
        assert is_multi_question_query("Wanneer wordt de factuur verstuurd en naar welk adres gaat die?")
        assert is_multi_question_query(
            "Kun je uitleggen hoe de nummerweergave werkt en daarnaast checken of dat ook geldt voor mobiele toestellen?"
        )

    def test_coordinated_words_in_one_question_stay_one(self) -> None:
        assert not is_multi_question_query("Werkt dit op iOS en Android?")
        assert not is_multi_question_query("Kun je dit controleren en aanpassen?")

    def test_short_and_elliptic_questions_stay_multi(self) -> None:
        assert is_multi_question_query("Wat kost het? Hoe bestel ik?")
        assert is_multi_question_query("Prijs per gebruiker per maand? Opzegtermijn voor ons contract?")

    def test_statement_with_wh_words_is_not_a_question(self) -> None:
        assert not is_multi_question_query(
            "Hoe verleng ik mijn contract? Ik weet hoe het contract werkt en ik weet hoe de verlenging werkt."
        )

    def test_guard_text_demands_per_question_coverage(self) -> None:
        text = MULTI_QUESTION_GUARD_TEXT.lower()
        assert "per question" in text
        assert "number of answers must equal the number of questions" in text
        assert "do not invent or substitute questions" in text


class TestSplitSubQuestions:
    def test_splits_pasted_question_list_uncapped_by_default(self) -> None:
        questions = split_sub_questions(INCIDENT_MESSAGE)
        assert len(questions) == 7
        assert questions[0] == "Wat moet onze webhook exact teruggeven om te routeren?"

    def test_max_questions_caps_the_returned_list(self) -> None:
        assert len(split_sub_questions(INCIDENT_MESSAGE, max_questions=6)) == 6

    def test_strips_list_markers(self) -> None:
        assert split_sub_questions("1. Wat is de responstijd?\n- Welke fallback geldt er?") == [
            "Wat is de responstijd?",
            "Welke fallback geldt er?",
        ]

    def test_inline_prose_falls_back_to_segments(self) -> None:
        assert len(split_sub_questions("Wat kost een extra seat? En hoe zeg ik het abonnement op?")) == 2

    def test_single_question_returns_empty(self) -> None:
        assert split_sub_questions("Wat is de responstijd?") == []
        assert split_sub_questions(None) == []

    def test_numbered_list_without_question_marks_splits(self) -> None:
        assert split_sub_questions("1. Geef de maximale timeout\n2. Beschrijf het retrygedrag") == [
            "Geef de maximale timeout",
            "Beschrijf het retrygedrag",
        ]

    def test_one_real_question_blocks_the_list_fallback(self) -> None:
        assert split_sub_questions("Waarom werkt stap 3 niet?\n1. Open de app\n2. Klik op start") == []
        assert split_sub_questions("Can you help? Details:\n1. First instruction\n2. Second instruction") == []

    def test_largest_partner_body_without_question_mark_splits_in_linear_time(self) -> None:
        """The splitter now runs on anonymous widget and partner turns too. The
        hook's findall took 1.4 s on 20 000 characters without a '?' and grew
        quadratically; a partner body may carry 128 KB."""
        started = time.perf_counter()
        assert split_sub_questions("woord " * 21_845) == []
        assert time.perf_counter() - started < 0.5

    def test_full_width_question_mark_splits_like_ascii(self) -> None:
        assert split_sub_questions("响应时间是多少\uff1f\n回退机制是什么\uff1f") == [
            "响应时间是多少\uff1f",
            "回退机制是什么\uff1f",
        ]


class TestEvidenceScoreFloor:
    def test_incident_chunk_at_005_is_below_floor(self) -> None:
        assert _chunk_below_evidence_floor({"chunk_id": "c1", "reranker_score": 0.05033})

    def test_healthy_chunk_is_kept(self) -> None:
        assert not _chunk_below_evidence_floor({"chunk_id": "c1", "reranker_score": 0.61})

    def test_chunk_without_score_is_kept_fail_open(self) -> None:
        assert not _chunk_below_evidence_floor({"chunk_id": "c1"})

    def test_final_score_wins_over_reranker_score(self) -> None:
        assert _chunk_below_evidence_floor({"chunk_id": "c1", "final_score": 0.1, "reranker_score": 0.9})

    def test_raw_score_alone_never_drops(self) -> None:
        assert not _chunk_below_evidence_floor({"chunk_id": "c1", "score": 0.0})
