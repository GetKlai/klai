"""A referral that names what the visitor asked about, for a subject the widget does not answer.

The tenant's fixed reply lists every excluded subject, so a visitor who asked
for an invoice was told the assistant cannot discuss prices, quotes or
contracts, and a request for a technical call got the same sentence
(SPEC-RAG-ANSWER-JUDGES-001, logbook 2.42-2.43).

The model only names the subject; the sentence around it is ours. The subject
may hold nothing but letters, digits and spaces, every content word must come
from the visitor's own question, and every number too. So the model cannot add
a claim, a price or a link of its own: the fixed reply exists so that no price
reaches the visitor, and a blocklist on free text did not hold (review of
2026-09-22: "is gratis", spelled-out amounts, protocol-relative links). When
the call fails, the subject does not pass, or the language has no template, the
tenant's fixed reply is used as before.
"""

from __future__ import annotations

import re

from pydantic import BaseModel, ConfigDict

from app.core.config import Settings
from app.services.turn_judge import structured_judge_call

SUBJECT_SYSTEM_PROMPT = (
    "A visitor of a company's help chat asked about something a colleague handles personally. Name what the "
    "visitor wants as a short noun phrase of at most eight words, in the language named in the message, made "
    "of the visitor's own words. Address the visitor as 'je' in Dutch and 'your' in English. It must fit the "
    "sentence 'Over <subject> kijkt een collega graag met je mee.' (Dutch) or 'A colleague will gladly look at "
    "<subject> with you.' (English). Only letters, digits and spaces. Examples: 'je factuur', 'een offerte "
    "voor 25 gebruikers', 'de koppeling met jullie CRM', 'your contract'."
)

_TEMPLATES = {
    "nl": "Over {subject} kijkt een collega graag persoonlijk met je mee. Plan hieronder een afspraak, dan helpen we je verder.",
    "en": "A colleague will gladly look at {subject} with you in person. Plan an appointment below and we will help you further.",
}

_TIMEOUT_SECONDS = 2.5
_MAX_WORDS = 8
_SUBJECT = re.compile(r"[^\W_]+(?: [^\W_]+)*")
# Words the phrase may use without the visitor having typed them: the address
# form and the articles and prepositions a noun phrase needs.
_FREE_WORDS = frozenset(
    "je jouw jullie your you een de het van voor met in op over bij aan om en a an the of for with to and on at".split()
)


# The phrase is often the visitor's words verbatim, first person included:
# "uitgaand bellen met mijn mobiele nummer" measured 2026-09-22. Our sentence
# speaks to the visitor, so the person is switched here rather than asked for.
_SECOND_PERSON = {"mijn": "je", "m'n": "je", "ons": "jullie", "onze": "jullie", "my": "your", "our": "your"}


class OffTopicSubject(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    subject: str


def _from_question(subject: str, question: str) -> bool:
    """Every word is a free word, or its first five characters occur in the question."""
    asked = question.lower()
    return all(word in _FREE_WORDS or word[:5] in asked for word in subject.lower().split())


async def off_topic_referral(
    question: str, language: str | None, settings: Settings, *, delegated_org_id: str | None = None
) -> str | None:
    """The referral for ``question``, or ``None`` when the tenant's fixed reply should be used."""
    template = _TEMPLATES.get(language or "")
    if template is None:
        return None
    question = question[:800]
    result = await structured_judge_call(
        name="off_topic_referral",
        system_prompt=SUBJECT_SYSTEM_PROMPT,
        user_content=f"Language: {language}\n\nVisitor: {question}",
        schema=OffTopicSubject,
        timeout_seconds=_TIMEOUT_SECONDS,
        delegated_org_id=delegated_org_id,
        settings=settings,
    )
    words = result.subject.split() if result is not None else []
    words = [_SECOND_PERSON.get(word.lower(), word) for word in words]
    if words and words[0].lower() in _FREE_WORDS:
        words[0] = words[0].lower()
    subject = " ".join(words)
    if not _SUBJECT.fullmatch(subject) or len(subject.split()) > _MAX_WORDS:
        return None
    if not _from_question(subject, question):
        return None
    return template.format(subject=subject)
