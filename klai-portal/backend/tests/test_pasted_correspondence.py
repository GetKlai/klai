"""Moved from deploy/litellm/tests/test_pasted_correspondence.py (one-chat-
pipeline slice 3) — pins app.services.pasted_correspondence, the module
that owns detection and the epistemic contract wording.

Voys trunk incident (2026-08-17): two customer emails pasted into chat were
answered as verified fact. The prevention is code-first — these tests pin
the deterministic detector and the contract wording itself (so a prompt edit
that weakens the contract fails CI).

Dropped from the original file (not moved in this slice, still hook-only):
``TestPolicyAndMetaPropagation`` (klai_kb_answer_policy, slice 4/5),
``TestFooterRendering``'s render-path cases and ``TestStreamGuard*``
(klai_kb_citation_render, slice 2/6).
"""

from __future__ import annotations

import pytest

from app.services.pasted_correspondence import (
    PASTED_CORRESPONDENCE_SCOPE,
    detect_pasted_correspondence,
    extract_pasted_correspondence_text,
    latest_user_turn_has_correspondence,
    pasted_correspondence_activity_line,
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

    def test_extract_excludes_leading_user_instruction(self):
        extracted = extract_pasted_correspondence_text(_ENGLISH_HEADER_PASTE)

        assert extracted.startswith("From: John Doe")
        assert "Please have a look" not in extracted
        assert "Everything on our side" in extracted

    def test_extract_prefers_anchored_headers_over_inline_instruction_label(self):
        text = (
            "Compare this to: our incident policy.\n\n"
            "From: John Doe <john@example.com>\n"
            "Sent: Friday, August 14, 2026 9:22 PM\n"
            "To: support@example.com\n"
            "Subject: Outbound calls failing\n\n"
            "Everything on our side is configured correctly."
        )

        extracted = extract_pasted_correspondence_text(text, assume_detected=True)

        assert extracted.startswith("From: John Doe")
        assert "Compare this to" not in extracted

    def test_extract_returns_empty_for_plain_question(self):
        assert extract_pasted_correspondence_text("What does SIP 404 mean?") == ""

    def test_bold_markdown_headers_detected(self):
        text = "**Van:** Klant <k@example.nl>\n**Verzonden:** maandag 17 augustus 2026\n**Onderwerp:** storing\n"
        assert text_contains_pasted_correspondence(text) is True

    def test_german_email_header_block_detected(self):
        text = "Von: Kunde <kunde@example.de>\nAn: support@example.de\nBetreff: Störung dringend\n"
        assert text_contains_pasted_correspondence(text) is True

    def test_original_message_marker_alone_is_sufficient(self):
        assert text_contains_pasted_correspondence("zie hieronder\n-----Original Message-----\nblah") is True

    def test_forwarded_message_marker_detected(self):
        assert text_contains_pasted_correspondence("---------- Forwarded message ----------\ninhoud") is True

    def test_quote_line_with_email_address_detected(self):
        text = (
            "Op vr 14 aug 2026 om 21:22 schreef Naam Achternaam <klant@example.nl>:\n> wij zien geen fout aan onze kant"
        )
        assert text_contains_pasted_correspondence(text) is True

    def test_quote_line_without_email_address_is_not_detected(self):
        # Plain prose that happens to match the "op ... schreef ...:" shape.
        assert text_contains_pasted_correspondence("op maandag schreef ik alles op: eerst dit, dan dat") is False

    def test_plain_question_not_detected(self):
        assert (
            text_contains_pasted_correspondence(
                "Kan ik je 2 emails geven over een voip-trunk die niet goed ingesteld staat?"
            )
            is False
        )

    def test_fewer_than_three_distinct_header_labels_not_detected(self):
        assert text_contains_pasted_correspondence("Van: de helpdesk\nOnderwerp: mijn vraag\nkan dit?") is False

    def test_language_variants_of_same_label_count_once(self):
        # "Van:" + "From:" + "Aan:" is only from+to → below threshold.
        assert (
            text_contains_pasted_correspondence("Van: a@example.nl\nFrom: a@example.nl\nAan: b@example.nl\n") is False
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
    """Drift gate for the machine-verifiable four-section answer shape."""

    @pytest.mark.parametrize(
        "phrase",
        [
            "[[KLAI_CORRESPONDENCE_SENDER_STATEMENTS]]",
            "[[KLAI_CORRESPONDENCE_KB_EVIDENCE]]",
            "[[KLAI_CORRESPONDENCE_OPEN_QUESTIONS]]",
            "[[KLAI_CORRESPONDENCE_VERIFY_FIRST]]",
            "exactly four sections",
            "matching internal evidence label",
            "using exactly `(E<n>)`",
            "do not use `E1:` or `[E1]`",
            "sole exception to the general no-citation-marker instruction",
            "Attribute every statement to its actual author",
            "remains open",
        ],
    )
    def test_contract_phrase_present(self, phrase):
        assert phrase in PASTED_CORRESPONDENCE_SCOPE

    def test_contract_defines_no_ranked_cause_or_conclusion_slot(self):
        lowered = PASTED_CORRESPONDENCE_SCOPE.casefold()
        assert "most likely cause" not in lowered
        assert "conclusion section" not in lowered


class TestActivityLine:
    def test_activity_line_texts(self):
        assert pasted_correspondence_activity_line("nl").startswith("- Geplakte correspondentie gedetecteerd")
        assert pasted_correspondence_activity_line("en").startswith("- Pasted correspondence detected")


class TestLatestTurnDetection:
    """Review round 2, finding 1: the Strict user-content exception may only
    look at the LATEST user turn — correspondence pasted earlier must not
    keep bypassing the deterministic Strict refusal for unrelated questions.
    (The Strict exception itself is slice 4/5; only the detector moved here.)
    """

    def test_email_in_earlier_turn_only(self):
        messages = [
            {"role": "user", "content": _VOYS_INCIDENT_PASTE},
            {"role": "assistant", "content": "Analyse van de mail ..."},
            {"role": "user", "content": "Hoeveel vakantiedagen heb ik?"},
        ]
        assert latest_user_turn_has_correspondence(messages) is False
        # Conversation-wide detection still fires (contract + footer).
        assert detect_pasted_correspondence(messages) is True

    def test_email_in_latest_turn(self):
        messages = [
            {"role": "user", "content": "Kan ik je iets voorleggen?"},
            {"role": "assistant", "content": "Ja hoor."},
            {"role": "user", "content": _VOYS_INCIDENT_PASTE},
        ]
        assert latest_user_turn_has_correspondence(messages) is True

    def test_no_user_messages(self):
        assert latest_user_turn_has_correspondence([]) is False
        assert latest_user_turn_has_correspondence(None) is False
        assert latest_user_turn_has_correspondence([{"role": "assistant", "content": _VOYS_INCIDENT_PASTE}]) is False
