"""Tests for the shared destructive-rewrite guard.

This logic was moved (not duplicated) out of
``deploy/litellm/klai_kb_query_rewrite.py`` so both the LiteLLM pre-call hook
and ``klai-retrieval-api``'s coreference resolver share one deterministic,
LLM-free backstop against topic-hijacking rewrites.
"""

from __future__ import annotations

from klai_citations.query_guard import (
    is_followup_query,
    rewrite_preserves_subject,
    salient_tokens,
)


class TestSalientTokens:
    def test_extracts_lowercase_content_words(self):
        assert salient_tokens("Wat weet je over klai?") == {"klai"}

    def test_filters_short_tokens(self):
        # "ai" is 2 chars — below the length-3 floor.
        assert "ai" not in salient_tokens("ai is leuk")

    def test_filters_stopwords(self):
        tokens = salient_tokens("Kun je me meer vertellen over Salesforce?")
        assert "salesforce" in tokens
        assert "kun" not in tokens
        assert "over" not in tokens

    def test_empty_string_returns_empty_set(self):
        assert salient_tokens("") == set()


class TestIsFollowupQuery:
    def test_pure_deictic_query_is_followup(self):
        # Every token here is either a deictic reference ("je", "daarover")
        # or a stopword ("wat", "weet") — no salient subject of its own.
        assert is_followup_query("Wat weet je daarover?") is True

    def test_query_with_a_subject_is_not_followup(self):
        assert is_followup_query("Wat weet je over klai?") is False

    def test_empty_string_is_not_followup(self):
        # No deictic tokens present at all.
        assert is_followup_query("") is False


class TestRewritePreservesSubject:
    def test_rejects_rewrite_that_drops_all_salient_tokens(self):
        """The canonical incident: a self-contained question rewritten into
        an unrelated historical topic."""
        assert (
            rewrite_preserves_subject(
                "Wat weet je over klai?",
                "Hoe stel ik een Yealink toestel in en welke instellingen zijn er mogelijk?",
            )
            is False
        )

    def test_accepts_rewrite_that_keeps_a_salient_token(self):
        assert (
            rewrite_preserves_subject(
                "Wat kost dat?",
                "Wat kost de Klai integratie met Salesforce precies?",
            )
            is True
        )

    def test_accepts_unchanged_rewrite(self):
        assert (
            rewrite_preserves_subject(
                "Hoe troubleshoot ik Bubble?",
                "Hoe troubleshoot ik Bubble?",
            )
            is True
        )

    def test_pure_followup_query_always_passes(self):
        """A short follow-up like "Wat weet je daarover?" has no subject of
        its own — history may freely supply one."""
        assert (
            rewrite_preserves_subject(
                "Wat weet je daarover?",
                "Hoe stel ik een Yealink toestel in?",
            )
            is True
        )

    def test_raw_query_with_no_salient_tokens_passes(self):
        # Entirely stopwords/deictic tokens — nothing to protect.
        assert (
            rewrite_preserves_subject("Wat weet je daarover?", "Iets heel anders.") is True
        )

    def test_brand_bridging_rewrite_that_keeps_original_brand_passes(self):
        """Brand-bridging rewrites add related terms but must keep the
        original brand token — this must not be flagged as destructive."""
        assert (
            rewrite_preserves_subject(
                "Werkt het met Salesforce?",
                "Voys Salesforce CRM-koppeling Bubble RedCactus",
            )
            is True
        )
