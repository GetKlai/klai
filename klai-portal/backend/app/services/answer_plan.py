"""Decide, from the retrieved articles, whether this turn should ask one question first.

A widget answer commits to one reading of a short question: "iedereen die belt
gaat naar voicemail" got instructions to send every caller to voicemail, and
"ik kan niet bellen met mijn Voys-apparaat" got an iPhone answer for a visitor
who never named a device (SPEC-RAG-ANSWER-JUDGES-001, logbook 2.44). Telling the
answer model in its profile to ask when the question is vague did not change
that: measured on the reviewed conversations it moved nothing (2.46), which
matches the research finding that a model judges ambiguity in the question
poorly while the spread of the retrieved articles predicts it well.

So the decision is taken here, against what retrieval actually found, and the
answer model is handed one concrete question instead of a rule. The options
must come from the articles: a word of four characters or more has to appear
there (five characters are enough for a Dutch compound) and a number has to
match exactly, the rule the off-topic referral uses, so an option cannot offer a
product, a cause or a price the knowledge base does not carry. The question in
turn has to name one of those options, so it stays tied to the articles without
being written in their words.

It reads the conversation, not only the latest message. Measured on 2026-09-23
with the latest message alone, a turn that said no more than "Heb een macbook"
was asked what problem the visitor was having, which the turn before it had
already said.
"""

from __future__ import annotations

import re
from typing import Literal

import structlog
from pydantic import BaseModel, ConfigDict

from app.core.config import Settings
from app.services.partner_chat import _normalize_llm_message
from app.services.turn_judge import structured_judge_call

logger = structlog.get_logger()

PLAN_SYSTEM_PROMPT = (
    "You prepare one turn of a company's help chat. You get the visitor's question and the help "
    "articles retrieval found for it. Decide what the assistant should do:\n"
    "diagnose — the visitor reports that something does not work or happens when it should not, the "
    "articles describe more than one cause for it, and the visitor has not said which applies (which "
    "device, app or account, since when, who is affected).\n"
    "choose — the articles describe different procedures that could each be what the visitor means, and "
    "the question does not say which one.\n"
    "direct — anything else: the articles point at one answer, or the visitor already gave the detail.\n\n"
    "Read the whole conversation. Anything the visitor said earlier counts as given: never ask for it "
    "again, and never ask about a case they already ruled out.\n\n"
    "For diagnose and choose, write ONE short question in the visitor's language that tells the causes or "
    "procedures apart, and list them as two to four options of at most four words each. Name each option "
    "after the cause or the procedure itself, in the words of the article it comes from; do not restate the "
    "visitor's symptom and do not repeat an option. Choose direct when your question would ask something "
    "the conversation already answers. For direct, leave question and options empty."
)

_TIMEOUT_SECONDS = 2.0
_HISTORY_TURNS = 6
_HISTORY_CHARS = 400
# The question is written by a model over text the visitor and the knowledge
# base supply, and it lands in the answer model's system prompt. So it has to
# look like a question and nothing else: one line, ending in a question mark,
# no link for the visitor to click.
_QUESTION_MAX_CHARS = 200
_MAX_CHUNKS = 8
_MAX_OPTION_WORDS = 4
_NOT_A_QUESTION = re.compile(r"https?:|www\.|[\[\]`{}<>|]")
_MAX_QUESTION_WORDS = 25
_MAX_OPTIONS = 4
_MIN_OPTIONS = 2
# Per article: enough to recognise what it covers, short enough that eight of
# them still fit the 2 s budget this call gets beside the rest of the turn.
_CHUNK_CHARS = 400

_ADDENDUM = {
    "diagnose": (
        "\n\n[This turn] The visitor reports a problem and the help articles above describe more than one "
        "cause. Name each likely cause in one short line, using only the articles above: {options}. Do NOT "
        "give the steps for one of them yet. End your reply with this question, in the visitor's language, "
        "in your own words: {question}"
    ),
    "choose": (
        "\n\n[This turn] The visitor's question fits several procedures in the help articles above: "
        "{options}. Say in one short line what each one is for, using only those articles, and do NOT walk "
        "through one of them yet. End your reply with this question, in the visitor's language, in your own "
        "words: {question}"
    ),
}


class AnswerPlan(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    route: Literal["direct", "diagnose", "choose"]
    question: str
    options: list[str]


def _articles(chunks: list[dict]) -> str:
    lines = []
    for chunk in chunks[:_MAX_CHUNKS]:
        title = str(chunk.get("title") or "").strip()
        text = " ".join(str(chunk.get("text") or "").split())[:_CHUNK_CHARS]
        lines.append(f"### {title}\n{text}")
    return "\n\n".join(lines)


def _source_text(chunks: list[dict]) -> str:
    return " ".join(f"{c.get('title') or ''} {c.get('text') or ''}" for c in chunks).lower()


def _grounded(word: str, source: str) -> bool:
    """A word the articles carry: five characters are enough for Dutch compounds, a number must match exactly."""
    word = word.lower().strip(".,:;!?()")
    if not word:
        return True
    if any(character.isdigit() for character in word):
        return word in source
    return len(word) < 4 or word[:5] in source


def _grounded_options(options: list[str], chunks: list[dict]) -> list[str]:
    """Distinct options that name something the articles carry, not a bare number or symbol."""
    source = _source_text(chunks)
    kept: list[str] = []
    for option in options:
        words = " ".join(option.split()).split()
        if not words or len(words) > _MAX_OPTION_WORDS:
            continue
        if not any(len(w) >= 4 and not any(c.isdigit() for c in w) for w in words):
            continue
        if all(_grounded(w, source) for w in words):
            text = " ".join(words)
            if text.lower() not in {k.lower() for k in kept}:
                kept.append(text)
    return kept


def _conversation(messages: list[dict]) -> str:
    """The last turns, in the same shape retrieval reads them: content can arrive as text parts."""
    normalized = [msg for m in messages if (msg := _normalize_llm_message(m)) is not None]
    lines = []
    for message in normalized[-_HISTORY_TURNS:]:
        role = "Visitor" if message["role"] == "user" else "Assistant"
        content = " ".join(message["content"].split())[:_HISTORY_CHARS]
        if content:
            lines.append(f"{role}: {content}")
    return "\n".join(lines)


def _checked(result: AnswerPlan, chunks: list[dict]) -> tuple[str | None, str]:
    """The addendum for a plan that passes every check, and the outcome to log either way."""
    if result.route == "direct":
        return None, "direct"
    asked = " ".join(result.question.split())
    if not asked.endswith("?") or "\n" in result.question.strip():
        return None, "question_shape"
    if len(asked) > _QUESTION_MAX_CHARS or len(asked.split()) > _MAX_QUESTION_WORDS or _NOT_A_QUESTION.search(asked):
        return None, "question_shape"
    options = _grounded_options(result.options, chunks)[:_MAX_OPTIONS]
    if len(options) < _MIN_OPTIONS:
        return None, "options_not_in_articles"
    # The question lands in the answer model's system prompt, so it has to be
    # tied to the options, which are themselves tied to the articles. Grounding
    # every word of the question against the articles was measured on
    # 2026-09-23 and dropped the diagnostic questions it exists for (1 of 6
    # instead of 4 of 6). Accepting one option word instead of a whole option
    # was measured on 2026-09-24 and did not win (13-19, then 16-16).
    if not any(option.lower() in asked.lower() for option in options):
        return None, "question_names_no_option"
    return _ADDENDUM[result.route].format(options="; ".join(options), question=asked), result.route


async def answer_plan(
    messages: list[dict], chunks: list[dict], settings: Settings, *, delegated_org_id: str | None = None
) -> str | None:
    """A system-prompt addendum naming the one question to ask, or ``None`` to answer as before.

    Every surface asks through this step; the internal chat's old instruction
    to ask inside the answer is gone, because a model asked to do that almost
    never did (plan §7.5).
    """
    if not chunks:
        return None
    result = await structured_judge_call(
        name="answer_plan",
        system_prompt=PLAN_SYSTEM_PROMPT,
        user_content=f"Conversation:\n{_conversation(messages)}\n\nHelp articles:\n{_articles(chunks)}",
        schema=AnswerPlan,
        timeout_seconds=_TIMEOUT_SECONDS,
        settings=settings,
        delegated_org_id=delegated_org_id,
    )
    if result is None:
        return None
    addendum, outcome = _checked(result, chunks)
    # Counts and the outcome only, never the question text: a dropped plan is
    # invisible in the reply, and without this line nobody could tell how often
    # the step fires on real traffic or which check stops it.
    logger.info("answer_plan_decision", route=result.route, outcome=outcome, options=len(result.options))
    return addendum


# Every retrieved article scored below the gap threshold (classify_gap "soft").
# On the reviewed conversations that is where the wrong answers sit: a reply the
# owner called correct had a best source of 0.80 at the median, one called wrong
# for its knowledge 0.37, and five of those six sat under 0.5. Over a quarter of
# real widget answers in the thirty days before were written over a best source
# below 0.3, which is the "why is it talking about Grandstream" class. The turn may still answer when an article
# really does cover the question; what it may not do is build a plausible answer
# out of a neighbouring one.
WEAK_SOURCES_ADDENDUM = (
    "\n\n[This turn] Retrieval found nothing that clearly matches: every help article above scored below "
    "the bar. Use them only if one of them literally answers what the visitor asked. If none does, say "
    "plainly in the visitor's language that you cannot find this in the help articles, give no steps and no "
    "workaround from a neighbouring article, and say that the visitor can plan an appointment with the "
    "button under this reply. Then end the reply with the exact token [[APPOINTMENT_OFFER]] on its own "
    "final line."
)
