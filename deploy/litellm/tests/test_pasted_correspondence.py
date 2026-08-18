"""Tests for pasted third-party correspondence detection + epistemic contract.

Voys trunk incident (2026-08-17): two customer emails pasted into chat were
answered as verified fact. The prevention is code-first — these tests pin the
deterministic detector, the policy-flag propagation into ``_klai_kb_meta``,
the visible agent-activity footer line, and the contract wording itself
(so a prompt edit that weakens the contract fails CI).
"""

from __future__ import annotations

import pytest
from klai_pasted_correspondence import (
    PASTED_CORRESPONDENCE_SCOPE,
    detect_pasted_correspondence,
    pasted_correspondence_activity_line,
    pasted_correspondence_detected_from_meta,
    text_contains_pasted_correspondence,
)

# The (redacted) shape of the actual incident paste: NL header block from a
# forwarded customer email inside a chat message.
_VOYS_INCIDENT_PASTE = """Dit is een aanvulling op de eerdere mail.

Van: Naam Achternaam <klant@example.nl>
Verzonden: Vrijdag, 14 Augustus, 2026 21:22
Aan: Support <support@example.nl>
CC: partner@example.net
Onderwerp: RE: klant example URGENT

Beste support,

Aan onze kant is alles geverifieerd correct.
"""

_ENGLISH_HEADER_PASTE = """Please have a look at this:

From: John Doe <john@example.com>
Sent: Friday, August 14, 2026 9:22 PM
To: support@example.com
Subject: Outbound calls failing

Everything on our side is configured correctly.
"""


class TestDetector:
    def test_dutch_email_header_block_detected(self):
        assert text_contains_pasted_correspondence(_VOYS_INCIDENT_PASTE) is True

    def test_english_email_header_block_detected(self):
        assert text_contains_pasted_correspondence(_ENGLISH_HEADER_PASTE) is True

    def test_bold_markdown_headers_detected(self):
        text = (
            "**Van:** Klant <k@example.nl>\n"
            "**Verzonden:** maandag 17 augustus 2026\n"
            "**Onderwerp:** storing\n"
        )
        assert text_contains_pasted_correspondence(text) is True

    def test_german_email_header_block_detected(self):
        text = (
            "Von: Kunde <kunde@example.de>\n"
            "An: support@example.de\n"
            "Betreff: Störung dringend\n"
        )
        assert text_contains_pasted_correspondence(text) is True

    def test_original_message_marker_alone_is_sufficient(self):
        assert (
            text_contains_pasted_correspondence(
                "zie hieronder\n-----Original Message-----\nblah"
            )
            is True
        )

    def test_forwarded_message_marker_detected(self):
        assert (
            text_contains_pasted_correspondence(
                "---------- Forwarded message ----------\ninhoud"
            )
            is True
        )

    def test_quote_line_with_email_address_detected(self):
        text = (
            "Op vr 14 aug 2026 om 21:22 schreef Naam Achternaam "
            "<klant@example.nl>:\n> wij zien geen fout aan onze kant"
        )
        assert text_contains_pasted_correspondence(text) is True

    def test_quote_line_without_email_address_is_not_detected(self):
        # Plain prose that happens to match the "op ... schreef ...:" shape.
        assert (
            text_contains_pasted_correspondence(
                "op maandag schreef ik alles op: eerst dit, dan dat"
            )
            is False
        )

    def test_plain_question_not_detected(self):
        assert (
            text_contains_pasted_correspondence(
                "Kan ik je 2 emails geven over een voip-trunk die niet "
                "goed ingesteld staat?"
            )
            is False
        )

    def test_fewer_than_three_distinct_header_labels_not_detected(self):
        assert (
            text_contains_pasted_correspondence(
                "Van: de helpdesk\nOnderwerp: mijn vraag\nkan dit?"
            )
            is False
        )

    def test_language_variants_of_same_label_count_once(self):
        # "Van:" + "From:" + "Aan:" is only from+to → below threshold.
        assert (
            text_contains_pasted_correspondence(
                "Van: a@example.nl\nFrom: a@example.nl\nAan: b@example.nl\n"
            )
            is False
        )

    def test_empty_and_non_string_inputs(self):
        assert text_contains_pasted_correspondence("") is False
        assert text_contains_pasted_correspondence("   \n  ") is False
        assert text_contains_pasted_correspondence(None) is False  # type: ignore[arg-type]

    def test_detects_in_user_string_message(self):
        messages = [
            {"role": "system", "content": "irrelevant"},
            {"role": "user", "content": _VOYS_INCIDENT_PASTE},
        ]
        assert detect_pasted_correspondence(messages) is True

    def test_detects_in_user_text_parts(self):
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "kijk hier eens naar:"},
                    {"type": "text", "text": _ENGLISH_HEADER_PASTE},
                ],
            }
        ]
        assert detect_pasted_correspondence(messages) is True

    def test_assistant_message_with_headers_is_ignored(self):
        # Only USER input counts — an assistant answer that quotes mail
        # headers must not re-trigger the contract on the next turn.
        messages = [{"role": "assistant", "content": _VOYS_INCIDENT_PASTE}]
        assert detect_pasted_correspondence(messages) is False

    def test_non_list_messages_do_not_crash(self):
        assert detect_pasted_correspondence(None) is False
        assert detect_pasted_correspondence("not a list") is False
        assert detect_pasted_correspondence([{"role": "user"}, "junk", 42]) is False


class TestScopeBlockContract:
    """Drift gate: weakening the contract wording must fail CI."""

    @pytest.mark.parametrize(
        "phrase",
        [
            "[Pasted third-party correspondence]",
            "CLAIM by its author",
            "the sender claims/reports",
            "their 'we/you' framing does not transfer to the user",
            "never advise the user to contact their own organisation",
            "NOT a knowledge-base source",
            "never state that the knowledge base 'confirms'",
            "what should be verified first",
            "Attribute each claim to the actual author",
        ],
    )
    def test_contract_phrase_present(self, phrase):
        assert phrase in PASTED_CORRESPONDENCE_SCOPE


class TestPolicyAndMetaPropagation:
    def test_policy_metadata_carries_flag(self):
        import klai_kb_answer_policy as policy_module

        policy = policy_module.KbAnswerPolicy(
            state="chunks_present",
            prompt_mode="strict_kb",
            user_provided_content_context=True,
            pasted_correspondence=True,
        )
        assert policy.metadata()["pasted_correspondence_detected"] is True
        meta = policy.to_kb_meta(org_id="o", user_id="u", retrieval_ms=1)
        assert meta["pasted_correspondence_detected"] is True
        assert pasted_correspondence_detected_from_meta(meta) is True

    def test_policy_flag_defaults_to_false(self):
        import klai_kb_answer_policy as policy_module

        policy = policy_module.KbAnswerPolicy(
            state="chunks_present",
            prompt_mode="open_kb",
            user_provided_content_context=False,
        )
        meta = policy.to_kb_meta(org_id="o", user_id="u", retrieval_ms=1)
        assert meta["pasted_correspondence_detected"] is False
        assert pasted_correspondence_detected_from_meta(meta) is False
        assert pasted_correspondence_detected_from_meta(None) is False


class TestFooterRendering:
    def test_footer_line_rendered_nl_and_en(self):
        from klai_kb_citation_render import (
            _format_visible_agent_activity,
            _has_visible_agent_activity,
        )

        kb_meta = {"pasted_correspondence_detected": True}
        assert _has_visible_agent_activity(kb_meta) is True
        text_nl = _format_visible_agent_activity(kb_meta, [], language="nl")
        assert "Geplakte correspondentie gedetecteerd" in text_nl
        assert "claims van de afzender" in text_nl
        text_en = _format_visible_agent_activity(kb_meta, [], language="en")
        assert "Pasted correspondence detected" in text_en
        assert "the sender's claims" in text_en

    def test_footer_line_absent_without_flag(self):
        from klai_kb_citation_render import _format_visible_agent_activity

        text = _format_visible_agent_activity(
            {"chunks_injected": 3, "kb_narrow": True}, [], language="nl"
        )
        assert "Geplakte correspondentie" not in text

    def test_gate_bypassed_still_suppresses_footer(self):
        from klai_kb_citation_render import _has_visible_agent_activity

        assert (
            _has_visible_agent_activity(
                {"gate_bypassed": True, "pasted_correspondence_detected": True}
            )
            is False
        )

    def test_activity_line_texts(self):
        assert pasted_correspondence_activity_line("nl").startswith(
            "- Geplakte correspondentie gedetecteerd"
        )
        assert pasted_correspondence_activity_line("en").startswith(
            "- Pasted correspondence detected"
        )


class TestStreamGuardEvidenceLabels:
    """Sol review P1 (PR #1059): parenthesized evidence labels must be
    withheld by the streaming guard so the flush-time cleaner can strip
    them before the user sees them."""

    def test_paren_evidence_label_is_withheld(self):
        from klai_kb_citation_render import _pop_streaming_guard_text

        safe, kept = _pop_streaming_guard_text(
            "De kennisbank (E3) bevestigt dat dit zo werkt en nog veel meer",
            final=False,
        )
        assert "(E3)" not in safe
        assert kept.startswith("(E3)")

    def test_evidence_word_paren_is_withheld(self):
        from klai_kb_citation_render import _pop_streaming_guard_text

        safe, kept = _pop_streaming_guard_text(
            "Zie de toelichting (Evidence E12) verderop in dit antwoord tekst",
            final=False,
        )
        assert "(Evidence" not in safe
        assert kept.startswith("(Evidence")

    def test_ordinary_parens_still_stream(self):
        from klai_kb_citation_render import _pop_streaming_guard_text

        text = "Dit werkt (zoals eerder uitgelegd) gewoon door en verder nog"
        safe, kept = _pop_streaming_guard_text(text, final=False)
        assert safe == text[: -len(kept)]
        assert "(zoals eerder uitgelegd)" in safe

    def test_final_flush_releases_buffer(self):
        from klai_kb_citation_render import _pop_streaming_guard_text

        text = "De kennisbank (E3) bevestigt"
        safe, kept = _pop_streaming_guard_text(text, final=True)
        assert safe == text
        assert kept == ""


class TestLatestTurnDetection:
    """Review round 2, finding 1: the Strict user-content exception may only
    look at the LATEST user turn — correspondence pasted earlier must not
    keep bypassing the deterministic Strict refusal for unrelated questions."""

    def test_email_in_earlier_turn_only(self):
        from klai_pasted_correspondence import latest_user_turn_has_correspondence

        messages = [
            {"role": "user", "content": _VOYS_INCIDENT_PASTE},
            {"role": "assistant", "content": "Analyse van de mail ..."},
            {"role": "user", "content": "Hoeveel vakantiedagen heb ik?"},
        ]
        assert latest_user_turn_has_correspondence(messages) is False
        # Conversation-wide detection still fires (contract + footer).
        assert detect_pasted_correspondence(messages) is True

    def test_email_in_latest_turn(self):
        from klai_pasted_correspondence import latest_user_turn_has_correspondence

        messages = [
            {"role": "user", "content": "Kan ik je iets voorleggen?"},
            {"role": "assistant", "content": "Ja hoor."},
            {"role": "user", "content": _VOYS_INCIDENT_PASTE},
        ]
        assert latest_user_turn_has_correspondence(messages) is True

    def test_no_user_messages(self):
        from klai_pasted_correspondence import latest_user_turn_has_correspondence

        assert latest_user_turn_has_correspondence([]) is False
        assert latest_user_turn_has_correspondence(None) is False
        assert (
            latest_user_turn_has_correspondence(
                [{"role": "assistant", "content": _VOYS_INCIDENT_PASTE}]
            )
            is False
        )


class TestStreamGuardWordFormAndProse:
    """Review round 2, findings 2+3: bare "Evidence E3" must be withheld,
    ordinary "(Evidence suggests ...)" prose must keep streaming."""

    def test_bare_evidence_word_label_is_withheld(self):
        from klai_kb_citation_render import _pop_streaming_guard_text

        safe, kept = _pop_streaming_guard_text(
            "Zie Evidence E3 hierboven plus nog een flinke lap tekst erna",
            final=False,
        )
        assert "Evidence E3" not in safe
        assert kept.startswith("Evidence E3")

    def test_lowercase_evidence_word_label_is_withheld(self):
        from klai_kb_citation_render import _pop_streaming_guard_text

        safe, _kept = _pop_streaming_guard_text(
            "volgens evidence E3 is dit zo, en er volgt nog veel meer tekst",
            final=False,
        )
        assert "evidence E3" not in safe

    def test_evidence_prose_paren_still_streams(self):
        from klai_kb_citation_render import _pop_streaming_guard_text

        text = (
            "Dit is duidelijk (Evidence suggests more tests are needed) "
            "en daarna gaat de zin nog een heel stuk verder door"
        )
        safe, _kept = _pop_streaming_guard_text(text, final=False)
        assert "(Evidence suggests more tests are needed)" in safe
