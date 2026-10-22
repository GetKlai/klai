"""The one check after the reply: hard facts by regex and claims by the statement check, in one shape.

synthetic-data: generator=hand-written seed=0 (invented prices, example.com
addresses and fictional phone numbers).
"""

from __future__ import annotations

import json

import pytest

from app.services import answer_grounding
from app.services.answer_check import AnswerCheck, check_hard_facts


class _Settings:
    answer_grounding_model = "klai-medium"
    litellm_base_url = "http://litellm.example.com"
    litellm_master_key = "k"


def test_a_hard_fact_counts_as_outside_only_when_no_passage_holds_it_in_any_notation():
    text = (
        "Het kost € 1.250,00 per jaar, zie https://kb.example.com/prijzen/ of bel 050 700 9999. "
        "Mail naar sales@example.com, of lees https://other.example.com/faq. Een licentie kost 99 euro."
    )
    passages = [
        {"text": "De prijs is 1250 euro per jaar. Bel +31 50 700 9999.", "source_url": "https://kb.example.com/prijzen"}
    ]

    assert check_hard_facts(text, passages, []).hard_facts_outside == {"email": 1, "link": 1, "amount": 1}


def test_a_malformed_link_in_the_reply_is_not_a_hard_fact_and_does_not_fail_the_turn():
    """The normaliser raises "Invalid IPv6 URL" on "http://[stap 1]"; the
    reply must settle, and the token is neither a link nor an error."""
    text = "Open http://[stap 1] en kijk dan op https://kb.example.com/verlof."
    passages = [{"text": "Zie https://kb.example.com/verlof.", "source_url": "https://kb.example.com/verlof"}]

    assert check_hard_facts(text, passages, []).hard_facts_outside == {}


@pytest.mark.parametrize(
    ("amount", "passage"),
    [("€ 12,50", "1250 euro"), ("$ 1250", "1250 euro"), ("16.95 euro", "€ 16,- per maand of 95 cent")],
    ids=["decimals", "currency", "fraction"],
)
def test_an_amount_stands_in_a_passage_only_with_the_same_value_and_currency(amount, passage):
    """ "€ 12,50" and "1250 euro" shared one key, so an invented price could
    pass as supported by a passage with another one."""
    assert check_hard_facts(f"Het kost {amount}.", [{"text": passage}], []).hard_facts_outside == {"amount": 1}


@pytest.mark.asyncio
async def test_an_unsupported_claim_and_an_amount_outside_the_passages_land_in_one_check_with_their_kinds(monkeypatch):
    answer = "Een aanvraag kost 10 euro. Je leidinggevende keurt binnen een dag goed."
    passages = [{"text": "Je leidinggevende keurt de aanvraag goed."}]
    verdict = {
        "statements": [
            {
                "statement": "Je leidinggevende keurt goed.",
                "evidence": "keurt de aanvraag goed",
                "support": "supported",
            },
            {"statement": "Binnen een dag.", "evidence": "", "support": "not_in_articles"},
        ]
    }

    async def _post(**_kwargs):
        return json.dumps(verdict)

    monkeypatch.setattr(answer_grounding, "_post", _post)

    claims = await answer_grounding.check_statements(
        question="Hoe vraag ik verlof aan?",
        draft=answer,
        articles=[("Verlof", passages[0]["text"])],
        settings=_Settings(),
    )
    assert claims is not None
    check = AnswerCheck([*check_hard_facts(answer, passages, []).statements, *claims.statements])

    assert [(item.kind, item.support is None) for item in check.statements] == [
        ("amount", True),
        ("claim", False),
        ("claim", True),
    ]
    assert [item.text for item in check.unsupported] == ["Binnen een dag."]
    assert check.hard_facts_outside == {"amount": 1}
