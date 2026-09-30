"""Which retrieved passages answer the visitor's question, decided before anyone writes.

The reranker score says a passage resembles the search query, not that it
answers the question. Measured on 101 replayed turns of the help widget
(2026-09-30): in 37 of the 82 turns where the answer model wrote, no passage
contained the answer, 22 of those 37 scored 0.4 or higher, and the model wrote
anyway, inventing menu names and steps in 17. Every check after the reply then
had to undo that. With this step in front, on the same passages, replies that
were mostly unsupported went from 19 to 1 and unsupported statements from 32%
to 16% of all statements.

One call on the checker model returns a closed verdict and, per passage it
relies on, the sentence that carries the answer; code checks that sentence
against the passage and decides the route. The writer then reads only the
chosen passages. No
filter on product or passage length in code: both were tried in the same
measurement and cost answers whose only source was such a passage.

Fails open: no verdict means the turn runs as it did before this step existed.
"""

from __future__ import annotations

import re
from typing import Literal

import structlog
from pydantic import BaseModel, ConfigDict

from app.core.config import Settings
from app.services.turn_judge import conversation_excerpt, structured_judge_call

logger = structlog.get_logger()

SELECTION_SYSTEM_PROMPT = (
    "You prepare a help-centre reply. You get the conversation and numbered passages from the help articles. "
    "Work in this order.\n"
    "need: in one sentence, what the visitor wants to know or do now. Use the earlier turns to understand a short "
    "latest message; an answer to the assistant's own question narrows the visitor's earlier question, it does not "
    "replace it.\n"
    "evidence: go through the passages and copy, word for word, the sentence or step that tells the visitor how to "
    "do it or gives the fact they asked for. At most three passages, one sentence each, with the passage number. "
    "Copy only sentences that carry the how or the fact. A sentence that merely says something exists, is "
    "supported or is available does not tell how to do it and is not evidence. A sentence saying that what the "
    "visitor wants is not possible or no longer supported IS the fact they need. Leave the list empty when there "
    "is no such sentence. It must be about the same product, device and direction (import is not export, inbound "
    "is not outbound, the mobile app is not a desk phone).\n"
    "verdict: answers when the evidence covers the need or a clear part of it; a missing part is no reason to ask "
    "or to refuse, the writer will say what the articles do not cover. depends only when you copied evidence from "
    "passages whose steps DIFFER per variant (device, app) and the conversation does not say which applies; never "
    "to ask about something the passages do not cover, and not when the steps are the same for each variant. "
    "not_in_passages when there is no evidence. A request for a person or an appointment, a thank-you or a remark about the chat "
    "needs no passage: answers with empty evidence.\n"
    "question: only for depends, the one question to ask the visitor, in their language, at most fifteen words. "
    "Otherwise an empty string."
)

# The checker's own budget on the widget is 4 s for a reply that lists every
# statement; this call reads the same passages and returns a few short fields.
_TIMEOUT_SECONDS = 4.0
_PASSAGE_CHARS = 2500
# A copied sentence is accepted when four fifths of its words stand in the
# passage: the model drops markup and mends a broken link text, and a looser
# bar would let through a quote that was never there.
_QUOTE_WORD_SHARE = 0.8
_WORD = re.compile(r"\w+")
# A passage line belongs to the quote when most of its words are in it; the
# model often strings the items of a step list into one sentence.
_LINE_END = re.compile(r"\n|(?<=[.!?])\s+")
_LINE_WORD_SHARE = 0.6
_MAX_LINES_PER_QUOTE = 6


class Evidence(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    passage: int
    quote: str


class PassageSelection(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    # Field order is the order the model writes in: it states the need and
    # looks up the sentences before it decides. With the verdict first it
    # refused answerable questions in 7 of 24 repeats on read cases; in this
    # order, with the prompt above, 0 of 32, and on the 108 stored turns it was
    # not tuned on it turned no answer the owner called good into "not found".
    need: str
    evidence: list[Evidence]
    verdict: Literal["answers", "depends", "not_in_passages"]
    question: str

    def found(self, chunks: list[dict]) -> list[tuple[dict, str]]:
        """Per quoted sentence that really stands in its passage: the passage, and the passage's own words for it.

        The quote is checked in code: a verdict that a passage answers is only
        as good as the sentence it can point at. What travels on is never the
        model's copy but the lines of the passage it matches, so a word the
        model changed while copying cannot reach the writer.
        """
        pairs: list[tuple[dict, str]] = []
        for item in self.evidence:
            if not 0 < item.passage <= len(chunks):
                continue
            chunk = chunks[item.passage - 1]
            text = str(chunk.get("text") or "")
            quote = _WORD.findall(item.quote.lower())
            if not quote or sum(word in set(_WORD.findall(text.lower())) for word in quote) < _QUOTE_WORD_SHARE * len(
                quote
            ):
                continue
            wanted = set(quote)
            lines = [" ".join(line.split()) for line in _LINE_END.split(text)]
            own = [
                line
                for line in lines
                if (words := _WORD.findall(line.lower()))
                and sum(word in wanted for word in words) >= _LINE_WORD_SHARE * len(words)
            ]
            pairs.append((chunk, " ".join(own[:_MAX_LINES_PER_QUOTE])))
        return pairs

    def chosen(self, chunks: list[dict]) -> list[dict]:
        """The passages with a quoted sentence found back in them, without the same text twice.

        A matched FAQ item comes back as its whole section, so two hits in one
        section are one text under two ids (24 of 102 replayed turns).
        """
        picked: dict[str, dict] = {}
        for chunk, _ in self.found(chunks):
            picked.setdefault(" ".join(str(chunk.get("text") or "").split()), chunk)
        return list(picked.values())

    def not_in_passages(self, chunks: list[dict]) -> bool:
        """No passage answers: the verdict says so, or no quoted sentence could be found back."""
        return self.verdict == "not_in_passages" or (bool(self.evidence) and not self.found(chunks))


async def select_passages(
    messages: list[dict], chunks: list[dict], settings: Settings, *, delegated_org_id: str | None = None
) -> PassageSelection | None:
    passages = "\n\n".join(
        f"[{number}] {chunk.get('title') or ''}\n{str(chunk.get('text') or '')[:_PASSAGE_CHARS]}"
        for number, chunk in enumerate(chunks, 1)
    )
    selection = await structured_judge_call(
        name="passage_selection",
        system_prompt=SELECTION_SYSTEM_PROMPT,
        user_content=f"Conversation:\n{conversation_excerpt(messages)}\n\nPassages:\n{passages}",
        schema=PassageSelection,
        timeout_seconds=_TIMEOUT_SECONDS,
        settings=settings,
        model=settings.answer_grounding_model,
        delegated_org_id=delegated_org_id,
    )
    if selection is not None:
        # Counts and the closed verdict only; the missing fact is the visitor's words.
        logger.info(
            "passage_selection",
            verdict=selection.verdict,
            offered=len(chunks),
            chosen=len(selection.chosen(chunks)),
        )
    return selection


def writer_brief(selection: PassageSelection, chunks: list[dict]) -> str:
    """Tell the writer what the chosen passages were chosen for.

    Handed only the passage, the writer still built a complete-looking
    procedure around it. The lines the selection pointed at are the answer;
    naming them, and the need they answer, keeps the reply on them.
    """
    lines = list(dict.fromkeys(own for _, own in selection.found(chunks) if own))
    if not lines:
        return ""
    listed = "\n".join(f"- {line}" for line in lines)
    return (
        "\n\n[This turn] What the visitor needs: "
        + " ".join(selection.need.split())
        + "\nThe help articles above answer it in these lines:\n"
        + listed
        + "\nBuild the reply on these lines and the steps that stand with them in the article. If they cover "
        "only part of what the visitor needs, give that part and say in one sentence what the help articles do not "
        "cover. Do not add a step, menu, cause or example that is not in the article."
    )
