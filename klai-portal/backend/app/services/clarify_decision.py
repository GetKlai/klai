"""Write the one question the clarify gate decided to ask, and hand it to the answer model.

The decision itself is deterministic (clarify_gate.py). A small model on
klai-fast only turns the gate's variants into one natural question in the
visitor's language; when that call fails or its question is not one plain line,
the turn answers directly.
"""

from __future__ import annotations

import dataclasses
import re

from klai_chat_prompts import CLARIFY_QUESTION_WRITER_SYSTEM_PROMPT
from pydantic import BaseModel, ConfigDict

from app.core.config import Settings
from app.services.clarify_gate import ClarifyDecision, clarify_gate
from app.services.turn_judge import conversation_excerpt, structured_judge_call

_TIMEOUT_SECONDS = 2.0
# The question lands in the answer model's system prompt, so it has to look
# like a question and nothing else: one line, a question mark, no link or code.
_QUESTION_MAX_CHARS = 200
_QUESTION_MAX_WORDS = 25
_NOT_A_QUESTION = re.compile(r"https?:|www\.|[\[\]`{}<>|]")
_AXIS_PHRASE: dict[str, str] = {
    "device": "which device, app or platform they use",
    "direction": "whether it is about incoming or outgoing calls",
    "edition": "which edition, module or version they use",
    "product": "which product or brand they use",
}


class ClarifyQuestion(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)
    question: str


def _plain_question(question: str) -> bool:
    asked = " ".join(question.split())
    return (
        "\n" not in question.strip()
        and asked.endswith("?")
        and len(asked) <= _QUESTION_MAX_CHARS
        and len(asked.split()) <= _QUESTION_MAX_WORDS
        and not _NOT_A_QUESTION.search(asked)
    )


async def write_question(
    messages: list[dict], gate: ClarifyDecision, settings: Settings, *, delegated_org_id: str | None = None
) -> ClarifyDecision:
    """The gate's decision with the question written, or the reason no question could be."""
    result = await structured_judge_call(
        name="clarify_question",
        system_prompt=CLARIFY_QUESTION_WRITER_SYSTEM_PROMPT,
        user_content=(
            f"Conversation:\n{conversation_excerpt(messages)}\n\n"
            f"What tells the variants apart: {_AXIS_PHRASE[gate.axis or 'product']}\n"
            f"Variants: {'; '.join(gate.options)}"
        ),
        schema=ClarifyQuestion,
        timeout_seconds=_TIMEOUT_SECONDS,
        settings=settings,
        delegated_org_id=delegated_org_id,
    )
    if result is None:
        return dataclasses.replace(gate, reason="model_failed")
    if not _plain_question(result.question):
        return dataclasses.replace(gate, reason="question_shape")
    return dataclasses.replace(gate, question=" ".join(result.question.split()))


async def clarify_decision(
    messages: list[dict], chunks: list[dict], settings: Settings, *, delegated_org_id: str | None = None
) -> ClarifyDecision:
    """Answer, or the one question to ask; a model runs only when the gate says ask."""
    gate = clarify_gate(messages, chunks, settings.klai_gap_soft_threshold)
    if gate.reason != "asked":
        return gate
    return await write_question(messages, gate, settings, delegated_org_id=delegated_org_id)


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
