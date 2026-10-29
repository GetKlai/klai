"""Which retrieved passages answer the visitor's question, decided before anyone writes.

The reranker score says a passage resembles the search query, not that it
answers the question. Measured on 101 replayed turns of the help widget
(2026-09-30): in 37 of the 82 turns where the answer model wrote, no passage
contained the answer, 22 of those 37 scored 0.4 or higher, and the model wrote
anyway, inventing menu names and steps in 17. Every check after the reply then
had to undo that. With this step in front, on the same passages, replies that
were mostly unsupported went from 19 to 1 and unsupported statements from 32%
to 16% of all statements.

One call on the checker model lists the parts the visitor asked and, per part,
the sentence of a passage that carries the answer; code checks each sentence
against its passage and derives everything else from that one structure:
whether anything is answered, which asked parts stay open (the brief's one
sentence and the appointment button), and whether the steps depend on a
variant. The writer then reads only the chosen passages. No
filter on product or passage length in code: both were tried in the same
measurement and cost answers whose only source was such a passage.

There is no second owner of "answered". Until 2026-10-24 the model also wrote
a verdict word and a free-text gap, and a light judge read the written reply
again to decide the button: on the widget meetlat the free-text gap named the
visitor's own situation in 4 of 18 cases, and in production the light judge
disagreed with the selection's "answers" on 15 of about 255 written replies.

A failed call is tried once more; with still no selection, a source-only turn
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
from app.services.turn_judge import conversation_excerpt, every_field_required, structured_judge_call

logger = structlog.get_logger()

SELECTION_SYSTEM_PROMPT = (
    "You prepare a help-centre reply. You get the conversation and numbered passages from the help articles. "
    "Work in this order.\n"
    "need: in one sentence, what the visitor wants to know or do now. Use the earlier turns to understand a short "
    "latest message; an answer to the assistant's own question narrows the visitor's earlier question, it does not "
    "replace it.\n"
    "parts: one entry per question the visitor asks now, at most six; a message with one question has one part. A "
    "request for a person or an appointment, a thank-you or a remark about the chat asks nothing of the articles: "
    "then parts is empty.\n"
    "ask: the question of this part, copied word for word from the visitor's own messages (from the earlier turn it "
    "narrows when the latest message is short). Only what the visitor asked: never a fact about their own situation "
    "or setup (whether something is switched on, installed, configured or already tried), never a check they can "
    "do.\n"
    "evidence: for this part, go through the passages and copy, word for word, the sentence or step that tells the "
    "visitor how to do it or gives the fact they asked for, with the passage number: at most three sentences per "
    "part and six in total. Copy only sentences that carry the how or the fact. A sentence that merely says "
    "something exists, is supported or is available does not tell how to do it and is not evidence. A sentence "
    "saying that what the visitor wants is not possible or no longer supported IS the fact they need. It must be "
    "about the same product, device and direction (import is not export, inbound is not outbound, the mobile app "
    "is not a desk phone), and about the same feature: a sentence about another feature or setting that only "
    "shares words with the question is not evidence. Leave the list empty when no passage has such a sentence; the "
    "visitor is then told that part is not covered, which is better than an answer about something else.\n"
    "must_ask_variant: true only when you cannot give the visitor the steps without knowing their variant (device, "
    "app): the evidence you copied gives different steps for different variants and the conversation does not say "
    "which applies. False when one set of steps or one fact answers the question, when the variants differ only in "
    "what the visitor did not ask about, and when the visitor describes a problem whose checks hold for every "
    "variant. Never to ask about something the passages do not cover.\n"
    "question: only when must_ask_variant is true, the one question to ask the visitor, in their language, at most fifteen "
    "words. Otherwise an empty string."
)

# The internal chat's own words for the same step. Its user is an employee
# who relays a customer's question, asks what something is or whether it
# exists, or asks to rewrite text already in the conversation: on the internal
# meetlat of 22 October the widget's "how to do it" rule called 13 of 48
# turns "not in the passages" while the owner expected a knowledge-base
# answer (a product's Windows app, what an Ascend feature is, a mail written
# from the previous answer).
SELECTION_SYSTEM_PROMPT_INTERNAL = (
    "You prepare a colleague's answer from the knowledge base. You get the conversation and numbered passages. "
    "The user is an employee: they may relay a customer's question, ask what something is or whether it exists, "
    "or ask you to rewrite, review or shorten text that is already in the conversation. Work in this order.\n"
    "need: in one sentence, what the user needs now. Use the earlier turns to understand a short latest message.\n"
    "parts: one entry per thing the user asks now, at most six; a message with one question has one part. A "
    "request to rewrite, review, translate or shorten text already in the conversation, a request for a person, a "
    "thank-you or a remark about the chat needs no passage: then parts is empty.\n"
    "ask: the question of this part, copied word for word from the user's own messages (from the earlier turn it "
    "narrows when the latest message is short). Only what the user asked: never a fact about the situation of the "
    "user or the customer they help (whether something is switched on, installed, configured or already tried), "
    "never a check they can do.\n"
    "evidence: for this part, go through the passages and copy, word for word, the sentence that answers it, with "
    "the passage number: the step that tells how to do it, the fact asked for, the sentence that says what "
    "something is or which options exist, or the sentence that says it is not possible or no longer supported. At "
    "most three sentences per part and six in total. Copy only sentences about the same product, device and "
    "direction (import is not export, inbound is not outbound, the mobile app is not a desk phone). Leave the list "
    "empty when no passage says anything about this part; a sentence about another feature or setting that only "
    "shares words with the question is not about it.\n"
    "must_ask_variant: true only when the evidence you copied gives different steps or availability for different "
    "variants (device, app), the conversation does not say which applies, and one set of steps or one fact cannot "
    "answer the question.\n"
    "question: only when must_ask_variant is true, the one question that would settle the variant, in the user's language, at "
    "most fifteen words. Otherwise an empty string."
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


class Part(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, json_schema_extra=every_field_required)

    ask: str
    evidence: list[Evidence]


def _found(evidence: list[Evidence], chunks: list[dict]) -> list[tuple[dict, str]]:
    """Per quoted sentence that really stands in its passage: the passage, and the passage's own words for it.

    The quote is checked in code: "this part is answered" is only as good as
    the sentence it can point at. What travels on is never the model's copy
    but the lines of the passage it matches, so a word the model changed while
    copying cannot reach the writer.
    """
    pairs: list[tuple[dict, str]] = []
    for item in evidence:
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


class PassageSelection(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, json_schema_extra=every_field_required)

    # Field order is the order the model writes in: it states the need,
    # lists the parts with the sentences it finds for each, and only then
    # decides whether the steps vary. With a verdict word first it refused
    # answerable questions in 7 of 24 repeats on read cases; with the evidence
    # first, 0 of 32, and on the 108 stored turns it was not tuned on it turned
    # no answer the owner called good into "not found". There is no verdict
    # field any more: code derives it from the parts (``verdict``).
    need: str
    parts: list[Part]
    must_ask_variant: bool
    question: str

    def verdict(self, chunks: list[dict]) -> Literal["answers", "depends", "not_in_passages"]:
        """The one word the record, the log and the routing read, derived from the parts and checked in code."""
        if self.not_in_passages(chunks):
            return "not_in_passages"
        return "depends" if self.must_ask_variant and self.found(chunks) else "answers"

    def found(self, chunks: list[dict]) -> list[tuple[dict, str]]:
        """The quoted sentences of every part that stand in their passage (``_found``)."""
        return _found([item for part in self.parts for item in part.evidence], chunks)

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
        """The visitor asked something and no quoted sentence of any part stands in a passage.

        No parts (a thank-you, a request for a person, a remark about the
        chat) asks nothing of the articles and is never "not found" here.
        """
        return bool(self.parts) and not self.found(chunks)

    def open_parts(self, chunks: list[dict], visitor_texts: list[str]) -> list[str]:
        """The asks no sentence answers, that the visitor really wrote.

        An ask counts only when its words stand in one of the visitor's own
        messages (all turns: a short follow-up narrows an earlier question).
        What the model wrote under ``ask`` that the visitor did not say, a
        fact about their setup, is never named to them and never adds the
        appointment button. The free-text gap this replaces named the
        visitor's own situation in 4 of 18 widget meetlat cases, and once
        answered "Ja." and then said the same part was not stated.
        """
        asks = [
            " ".join(part.ask.split()).rstrip(" ?!.")
            for part in self.parts
            if not _found(part.evidence, chunks) and any(words_stand_in(part.ask, text) for text in visitor_texts)
        ]
        return list(dict.fromkeys(ask for ask in asks if ask))


def _product_note(chunk: dict) -> str:
    return f" (product: {chunk['product']})" if chunk.get("product") else ""


async def select_passages(
    messages: list[dict],
    chunks: list[dict],
    settings: Settings,
    *,
    delegated_org_id: str | None = None,
    internal: bool = False,
    product: str = "",
) -> PassageSelection | None:
    passages = "\n\n".join(
        f"[{number}] {chunk.get('title') or ''}{_product_note(chunk) if product else ''}\n"
        f"{str(chunk.get('text') or '')[:_PASSAGE_CHARS]}"
        for number, chunk in enumerate(chunks, 1)
    )
    # The question's product (knowledge-base plan, step 7): a lookalike
    # article of another product, worded the same, is not evidence. Told to the
    # model, not filtered in code; see the module docstring.
    about = (
        f"The question is about the product {product}. A passage labelled with another product is about "
        "something else: it is not evidence, however alike its words.\n\n"
        if product
        else ""
    )
    # Six calls failed in the week of 14 to 21 October, and without a verdict
    # a source-only turn now answers "not found", so a failed call gets one
    # more try with the same budget. The retry is for a timeout or a dropped
    # connection: at temperature 0 a reply that is not the schema comes back
    # the same.
    for _ in range(2):
        selection = await structured_judge_call(
            name="passage_selection",
            system_prompt=SELECTION_SYSTEM_PROMPT_INTERNAL if internal else SELECTION_SYSTEM_PROMPT,
            # The latest message whole: the week's multi-part messages ran
            # 1,100 to 2,600 characters, and at the excerpt's 800 the later
            # questions never reached this step.
            user_content=f"Conversation:\n{conversation_excerpt(messages, latest_chars=_LATEST_CHARS)}\n\n"
            f"{about}Passages:\n{passages}",
            schema=PassageSelection,
            timeout_seconds=_TIMEOUT_SECONDS + _TIMEOUT_SECONDS_PER_PASSAGE * len(chunks),
            settings=settings,
            model=settings.answer_grounding_model,
            delegated_org_id=delegated_org_id,
        )
        if selection is not None:
            # Counts and the closed verdict only; the asks are the visitor's words.
            logger.info(
                "passage_selection",
                verdict=selection.verdict(chunks),
                offered=len(chunks),
                chosen=len(selection.chosen(chunks)),
            )
            return selection
    logger.warning("passage_selection_failed", attempt=2, offered=len(chunks))
    return None


def _coverage(selection: PassageSelection, *, what: str, internal: bool, unchecked: bool, open_parts: list[str]) -> str:
    """The brief's one line on what the sources leave open, decided by the selection, never by the writer.

    The asks no found sentence answers (``open_parts``) are the only gap the
    writer names. A "depends" turn asks about its variant or covers every
    one, and questions beyond the search limit carry their own note, so
    neither is told the lines answer everything.
    """
    verb = "does" if internal else "do"
    if open_parts:
        return (
            f"\nThe {what} {verb} not cover this part: {'; '.join(open_parts)}. After the answer, say in one "
            f"sentence that the {what} {verb} not cover it, naming that part, and nothing else about what is missing."
        )
    if selection.must_ask_variant:
        return ""
    if unchecked:
        return (
            "\nThese lines answer what was searched; each question marked as not separately searched keeps its "
            "own note."
        )
    return f"\nThese lines answer all of it: do not say that the {what} {'lacks' if internal else 'lack'} anything."


def writer_brief(
    selection: PassageSelection,
    chunks: list[dict],
    *,
    internal: bool,
    source_only: bool,
    question: str = "",
    unchecked: bool = False,
    open_parts: list[str] | None = None,
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

    ``unchecked`` says questions beyond the search limit follow the brief
    with their own note. ``open_parts`` are the asks of the visitor that no
    found sentence answers (``PassageSelection.open_parts``): the one gap the
    reply may name.

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
            "split or complete a step, and name a menu, button or setting exactly as the source names it. Where the "
            "source gives no cause, give none; where it gives no example, give none."
            if source_only
            else f"\nBuild the reply on these lines and the steps that stand with them in the source (translated into "
            f"the {who}'s language when needed). Do not add a "
            "step, menu, cause or example that is not in the source. An e-mail address, a link, a phone number, a "
            "price or an amount appears in the reply only when it stands in the lines above; otherwise say that "
            f"the {what} {'does' if internal else 'do'} not give it."
        )
        + _coverage(selection, what=what, internal=internal, unchecked=unchecked, open_parts=open_parts or [])
        + (
            "\nThe employee relays this for someone else; give the steps for each variant the lines name, each "
            "under its own heading, instead of asking which applies."
            if internal and selection.verdict(chunks) == "depends"
            else ""
        )
        + (f"\nFor the part that depends on it, ask: {' '.join(question.split())}" if question else "")
    )
