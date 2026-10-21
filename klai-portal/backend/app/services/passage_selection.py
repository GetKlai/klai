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

A failed call is tried once more; with still no verdict, a source-only turn
(the widget, internal Strict) answers "not found" (retrieval_context), because
a writer handed every passage is how an invented answer got through.
"""

from __future__ import annotations

import re
from typing import Literal

import structlog
from pydantic import BaseModel, ConfigDict

from app.core.config import Settings
from app.services.quotes import QUOTE_WORD_SHARE as _QUOTE_WORD_SHARE
from app.services.quotes import WORD as _WORD
from app.services.quotes import words_stand_in
from app.services.turn_judge import conversation_excerpt, structured_judge_call

logger = structlog.get_logger()

SELECTION_SYSTEM_PROMPT = (
    "You prepare a help-centre reply. You get the conversation and numbered passages from the help articles. "
    "Work in this order.\n"
    "need: in one sentence, what the visitor wants to know or do now. Use the earlier turns to understand a short "
    "latest message; an answer to the assistant's own question narrows the visitor's earlier question, it does not "
    "replace it. When the message asks several questions, the one sentence names each part.\n"
    "evidence: go through the passages and copy, word for word, the sentence or step that tells the visitor how to "
    "do it or gives the fact they asked for, with the passage number: for one question at most three passages, "
    "one sentence each; for a message with several questions one sentence per answered part, at most six lines in "
    "total. "
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
# The budget grows with what is offered: an internal turn offers 13 to 22
# passages, and at a flat 4 s the call timed out twice on 7 of 48 turns of the
# internal meetlat (21 October), handing the writer nothing chosen.
_TIMEOUT_SECONDS = 4.0
_TIMEOUT_SECONDS_PER_PASSAGE = 0.25
_PASSAGE_CHARS = 2500
_LATEST_CHARS = 4000
# A passage line belongs to the quote when most of its words are in it; the
# model often strings the items of a step list into one sentence.
_LINE_END = re.compile(r"\n|(?<=[.!?])\s+")
_LINE_WORD_SHARE = 0.6
_MAX_LINES_PER_QUOTE = 6
# A quote that outranks a "not in the passages" verdict must be a sentence,
# not a few common words; the 39 measured cases all quoted whole sentences.
_OVERRIDE_MIN_WORDS = 5
# The selection's question goes out as the whole reply, so it has to look like
# a question and nothing else: one line, a question mark, no link or code.
_QUESTION_MAX_CHARS = 200
_QUESTION_MAX_WORDS = 25
_NOT_A_QUESTION = re.compile(r"https?:|www\.|[\[\]`{}<>|]")


def plain_question(question: str) -> bool:
    """One short line that ends on a question mark and carries no link, list or instruction."""
    asked = " ".join(question.split())
    return (
        "\n" not in question.strip()
        and asked.endswith("?")
        and len(asked) <= _QUESTION_MAX_CHARS
        and len(asked.split()) <= _QUESTION_MAX_WORDS
        and not _NOT_A_QUESTION.search(asked)
    )


def _share(quote: list[str], chunk: dict) -> float:
    """The share of the quote's words that stand in the passage."""
    words = set(_WORD.findall(str(chunk.get("text") or "").lower()))
    return sum(word in words for word in quote) / len(quote)


def _article(chunk: dict) -> str:
    """What two passages of one article share: the source, or the title where there is none."""
    return str(chunk.get("source_url") or chunk.get("title") or "")


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
            quote = _WORD.findall(item.quote.lower())
            if not quote:
                continue
            # The sentence is the evidence; the passage number is the model's
            # weakest output. Retrieval hands over up to seven near-identical
            # sections of one article, and in two meetlat turns (both replay
            # rounds, 2026-10-13) the model copied the right sentence and
            # numbered the wrong section, which turned an answer into "not
            # found". The pointed passage first; otherwise the best-matching
            # section of the same article (same source, or title without one),
            # never another article, whose like-worded sentence could be about
            # another product.
            if not 0 < item.passage <= len(chunks):
                continue
            pointed = chunks[item.passage - 1]
            if _share(quote, pointed) >= _QUOTE_WORD_SHARE:
                chunk = pointed
            else:
                same_article = [c for c in chunks if _article(c) == _article(pointed)]
                best = max(same_article, key=lambda c: _share(quote, c), default=None)
                if best is None or _share(quote, best) < _QUOTE_WORD_SHARE:
                    continue
                chunk = best
            text = str(chunk.get("text") or "")
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
        """No passage answers: no quoted sentence stands in a passage, or, without any quote, the verdict says so.

        A verified quote outranks the verdict word. In five replay rounds of
        the meetlat (2026-10-13) the model copied a sentence that stood in a
        passage and still wrote "not_in_passages" 39 times; the owner expected
        an answer in 31 of those turns and "not found" in 6. The prompt asks
        for the list to be empty when there is no evidence, so a filled list
        is the model's own finding, and the writer gets those passages with
        the brief to say what they do not cover. Only a quote of a sentence's
        length outranks the verdict: "Dat kan niet." stands in many passages.
        And it must stand as one sentence of the passage (quotes.py), not as
        words scattered over three.
        """
        if self.verdict == "not_in_passages":
            strong = [item for item in self.evidence if len(_WORD.findall(item.quote)) >= _OVERRIDE_MIN_WORDS]
            return not any(
                words_stand_in(item.quote, str(chunk.get("text") or ""))
                for chunk, _ in self.model_copy(update={"evidence": strong}).found(chunks)
                for item in strong
            )
        return bool(self.evidence) and not self.found(chunks)


async def select_passages(
    messages: list[dict], chunks: list[dict], settings: Settings, *, delegated_org_id: str | None = None
) -> PassageSelection | None:
    passages = "\n\n".join(
        f"[{number}] {chunk.get('title') or ''}\n{str(chunk.get('text') or '')[:_PASSAGE_CHARS]}"
        for number, chunk in enumerate(chunks, 1)
    )
    # Six calls failed in the week of 14 to 21 October, and without a verdict
    # a source-only turn now answers "not found", so a failed call gets one
    # more try with the same budget. The retry is for a timeout or a dropped
    # connection: at temperature 0 a reply that is not the schema comes back
    # the same.
    for _ in range(2):
        selection = await structured_judge_call(
            name="passage_selection",
            system_prompt=SELECTION_SYSTEM_PROMPT,
            # The latest message whole: the week's multi-part messages ran
            # 1,100 to 2,600 characters, and at the excerpt's 800 the later
            # questions never reached this step.
            user_content=f"Conversation:\n{conversation_excerpt(messages, latest_chars=_LATEST_CHARS)}\n\n"
            f"Passages:\n{passages}",
            schema=PassageSelection,
            timeout_seconds=_TIMEOUT_SECONDS + _TIMEOUT_SECONDS_PER_PASSAGE * len(chunks),
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
    logger.warning("passage_selection_failed", attempt=2, offered=len(chunks))
    return None


def writer_brief(
    selection: PassageSelection, chunks: list[dict], *, internal: bool, source_only: bool, question: str = ""
) -> str:
    """Tell the writer what the chosen passages were chosen for, and how closely to keep to them.

    Handed only the passage, the writer still built a complete-looking
    procedure around it. The lines the selection pointed at are the answer;
    naming them, and the need they answer, keeps the reply on them. A
    ``source_only`` reply (the widget, internal Strict) is also asked for the
    source's own order and words, and no step, cause or example of the
    writer's own: two replay rounds of the widget meetlat (2026-10-13) gave
    21% and 23% flagged sentences against 26% and 25% before, and 6 and 9
    answers that were mostly unsupported against 13 and 9; four fewer of the
    81 expected answers came in both rounds, two of them read as an honest
    "not covered" where a procedure used to be invented. On internal Strict
    (2026-10-14, 36 checked answers) unsupported sentences per answer went
    from 2.9 to 2.6. Internal Open may fill from general knowledge, and there
    the stricter sentence made answers worse in two rounds (2.7 to 3.3 and
    3.7 to 4.4 per answer), so Open keeps the lines and the shorter rule. The
    internal chat gets the brief in its own words: its user is an employee
    and the passages are the knowledge base, not help articles.

    ``question`` is the selection's variant question on a message with several
    questions: sent as the whole reply it would drop the parts that were
    answered, so the writer answers those and asks it for the rest. The
    internal chat never asks it: an employee relaying a customer's question
    was asked "iPhone or Android?", which only the customer could answer, so
    a ``depends`` verdict there gets the steps for every variant instead.

    An Open reply may add general knowledge, but an e-mail address, a link, a
    phone number or a price it adds is invented: in the internal chat of 14 to
    21 October one gave an address and a menu path for a data-processing
    agreement and another a product price, none of them in the knowledge base.
    """
    lines = list(dict.fromkeys(own for _, own in selection.found(chunks) if own))
    if not lines:
        return ""
    listed = "\n".join(f"- {line}" for line in lines)
    who, what = ("user", "knowledge base") if internal else ("visitor", "help articles")
    return (
        f"\n\n[This turn] What the {who} needs: "
        + " ".join(selection.need.split())
        + f"\nThe {what} above answer{'s' if internal else ''} it in these lines:\n"
        + listed
        + (
            f"\nWrite the reply from these lines and the steps that stand with them in the source, in the source's "
            f"own order and its own words (translated into the {who}'s language when needed): do not reword, merge, "
            "split or complete a step, and name a menu, button or setting exactly as the source names it. If the "
            f"lines cover only part of what the {who} needs, give that part and say in one sentence what the {what} "
            "does not cover. Where the source gives no cause, give none; where it gives no example, give none."
            if source_only
            else f"\nBuild the reply on these lines and the steps that stand with them in the source. If they cover "
            f"only part of what the {who} needs, give that part and say in one sentence what the {what} does not "
            "cover. Do not add a step, menu, cause or example that is not in the source. An e-mail address, a link, "
            "a phone number, a price or an amount appears in the reply only when it stands in the lines above; "
            f"otherwise say that the {what} does not give it."
        )
        + (
            "\nThe employee relays this for someone else; give the steps for each variant the lines name, each "
            "under its own heading, instead of asking which applies."
            if internal and selection.verdict == "depends"
            else ""
        )
        + (f"\nFor the part that depends on it, ask: {' '.join(question.split())}" if question else "")
    )
