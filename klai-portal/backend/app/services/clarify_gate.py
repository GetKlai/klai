"""Decide, from the retrieved articles, whether this turn asks one question first.

A support answer commits to one reading of a short question: "ik kan niet
bellen met mijn apparaat" got an iPhone answer for a visitor who never named a
device (SPEC-RAG-ANSWER-JUDGES-001, logbook 2.44). A rule in the profile prompt
did not change that (2.46), and a model deciding per turn whether to ask
reached the visitor 0 times in 488 replayed questions while costing 0.6 s a
turn (2.53, 2.54). The research the logbook cites points the same way: the
spread of the retrieved articles predicts that a question is needed, a model
reading the question does not.

So the decision is deterministic and runs in microseconds. It asks only when
the strong articles (reranker score at or above the soft gap threshold) come
from two or more documents that cover the same topic in different variants
(iPhone and Android troubleshooters with the same "can't call" section; a
Basic and an Advanced edition of one setting), and the conversation names none
of those variants yet. Two documents cover the same topic when they share a
section heading or most of their title, and that shared part overlaps what the
visitor said, so two procedures that merely share a verb ("set up voicemail",
"set up your call plan") are no variants of each other. The variants are what
is left of each title once the words the group shares are removed, so the
options come from the articles by construction. A small model only writes the
question around them.
"""

from __future__ import annotations

import dataclasses
import re
from dataclasses import dataclass
from typing import Literal

from klai_chat_prompts import clarify_question_addendum

from app.services.answer_footer import strip_answer_footer_from_text

# Pure on purpose: the labelling script runs this gate over cached retrievals
# on a laptop, where no portal settings exist (scripts/clarify_gate_eval.py).

Reason = Literal[
    "soft_gap", "one_document", "variant_named", "asked_last_turn", "no_axis", "model_failed", "question_shape", "asked"
]
Axis = Literal["device", "direction", "edition", "product"]
_MAX_OPTIONS = 4

_WORD = re.compile(r"[^\W_]+")
# A title word as the title writes it, hyphenated compounds whole ("iOS-app").
_TITLE_WORD = re.compile(r"[^\W_]+(?:-[^\W_]+)*")
# Function words and how-to verbs in the two languages the knowledge bases are
# written in; they say nothing about the topic or the variant of an article.
# Without the verbs, "Hoe stel ik mijn X in" and "X functies" offered "stel"
# and "functies" as the two options on a replay of real questions.
_STOPWORDS = frozenset(
    "a about all an and are at be by can do does for from get how i in into is it me my not of on or set the "
    "this to up use using we what when where which why with work works you your "
    "aan af al alle als bij dat de den der die dit een en er gebruik gebruiken heb hebben hebt heeft het hoe ik "
    "in is je jij kan kun maar met mijn na naar niet of om ons op over stel te tot u uw van voor waar wanneer "
    "waarom wat we welke wel werken werkt wij zijn zo".split()
)
# Section and title words every kind of article carries. A shared "Setup" or
# "FAQ" heading is no shared topic, and "functies" or "installatie" left over
# from a title is no variant: on a production sample of 251 questions two of
# ten written questions offered such leftovers as the choice.
_GENERIC = frozenset(
    "faq features functies guide handleiding inleiding installatie installation instellen instellingen "
    "introduction manual opties options overview overzicht probleemoplosser problemen problems questions setup "
    "troubleshooter veelgestelde vragen".split()
)
# The devices the knowledge bases write separate articles for, by class: two
# names in one class ("iPhone", "iOS") are the same device to the visitor.
_DEVICE_CLASSES = {
    "iphone": "iphone ios ipad",
    "android": "android",
    "web": "webphone browser web",
    "computer": "desktop laptop computer pc mac macos macbook windows",
    "desk_phone": "toestel bureautelefoon desk yealink snom cisco gigaset grandstream",
    "wireless": "dect draadloos draadloze wireless",
    "headset": "headset",
}
_DIRECTION = frozenset("incoming outgoing inbound outbound inkomend inkomende uitgaand uitgaande".split())
_MAX_OPTION_WORDS = 2


def _stem(word: str) -> str:
    # Five characters are enough for a Dutch compound to meet its head word
    # ("gesprek", "gesprekken"), the same rule the off-topic referral uses.
    return word[:5]


def _stems(text: str) -> set[str]:
    return {_stem(w) for w in _WORD.findall(text.casefold()) if len(w) > 1 and w not in _STOPWORDS}


_GENERIC_STEMS = {_stem(w) for w in _GENERIC}
_DEVICE_CLASS_OF = {_stem(w): name for name, words in _DEVICE_CLASSES.items() for w in words.split()}
_DIRECTION_STEMS = {_stem(w) for w in _DIRECTION}


def _devices(stems: set[str]) -> set[str]:
    return {_DEVICE_CLASS_OF[s] for s in stems if s in _DEVICE_CLASS_OF}


@dataclass(frozen=True)
class ClarifyDecision:
    """What this turn does; ``reason == "asked"`` before the writer means the gate says ask."""

    reason: Reason
    axis: Axis | None = None
    documents: int = 0
    options: tuple[str, ...] = ()
    question: str | None = None

    @property
    def addendum(self) -> str | None:
        return clarify_question_addendum(self.question) if self.question else None


@dataclass
class _Document:
    title: str
    score: float
    stems: set[str]
    tails: set[str] = dataclasses.field(default_factory=set)


def _strong_documents(chunks: list[dict], threshold: float) -> list[_Document]:
    """One entry per document with a chunk at or above the threshold, best first."""
    documents: dict[str, _Document] = {}
    for chunk in chunks:
        score = chunk.get("reranker_score")
        title = " ".join(str(chunk.get("title") or "").split())
        if score is None or score < threshold or not title:
            continue
        key = str(chunk.get("source_url") or "").split("#")[0] or title
        document = documents.setdefault(key, _Document(title, score, _stems(title)))
        document.score = max(document.score, score)
        tail = str(chunk.get("heading_path") or "").rsplit(">", 1)[-1]
        if tail_stems := _stems(tail):
            document.tails.add(" ".join(sorted(tail_stems)))
    return sorted(documents.values(), key=lambda d: d.score, reverse=True)


def _link(a: _Document, b: _Document, asked: set[str]) -> set[str] | None:
    """The topic stems that make two documents variants of one topic, or ``None`` when they are not.

    A shared section heading or a mostly shared title counts only when a
    non-generic part of it is what the visitor asked about. Titles naming
    devices of different classes are variants as well, provided a section of
    one of them is about what the visitor asked: without that, a question about
    calling abroad was asked "Android or iPhone?" on a replay of real questions.
    """
    topic: set[str] = set()
    linked = False
    for tail in a.tails & b.tails:
        if (set(tail.split()) - _GENERIC_STEMS) & asked:
            topic |= set(tail.split())
            linked = True
    a_topic, b_topic = a.stems - _GENERIC_STEMS, b.stems - _GENERIC_STEMS
    shared = a_topic & b_topic
    if shared & asked and 2 * len(shared) >= max(len(a_topic), len(b_topic)):
        topic |= shared
        linked = True
    a_devices, b_devices = _devices(a.stems), _devices(b.stems)
    section_asked = any((set(tail.split()) - _GENERIC_STEMS) & asked for tail in a.tails | b.tails)
    linked = linked or bool(a_devices and b_devices and a_devices != b_devices and section_asked)
    return topic if linked else None


def _variant(document: _Document, others: list[_Document], topic: set[str]) -> str:
    """The words of the title no other document in the group carries, as the title writes them."""
    elsewhere = topic.union(_GENERIC_STEMS, *(o.stems for o in others))
    return " ".join(word for word in _TITLE_WORD.findall(document.title) if _stems(word) - elsewhere)


def _axis(options: tuple[str, ...], titles_overlap: bool) -> Axis:
    stems = set().union(*(_stems(o) for o in options))
    if _devices(stems):
        return "device"
    if stems & _DIRECTION_STEMS:
        return "direction"
    return "edition" if titles_overlap else "product"


def _best_group(documents: list[_Document], asked: set[str]) -> tuple[list[_Document], set[str]]:
    """The best-scoring group of linked documents, best first, and the topic they share."""
    groups: list[tuple[list[_Document], set[str]]] = []
    for document in documents:
        members, topic = [document], set()
        for group in list(groups):
            links = [link for member in group[0] if (link := _link(document, member, asked)) is not None]
            if links:
                groups.remove(group)
                members += group[0]
                topic |= group[1].union(*links)
        groups.append((members, topic))
    linked = [g for g in groups if len(g[0]) >= 2]
    if not linked:
        return [], set()
    members, topic = max(linked, key=lambda g: max(m.score for m in g[0]))
    return sorted(members, key=documents.index), topic


def _messages(messages: list[dict]) -> list[dict]:
    """User and assistant turns as text, content parts joined, an internal answer footer removed."""
    turns = []
    for message in messages:
        role, content = message.get("role"), message.get("content")
        if isinstance(content, list):
            content = " ".join(p.get("text", "") for p in content if isinstance(p, dict) and p.get("type") == "text")
        if role in ("user", "assistant") and isinstance(content, str):
            turns.append(
                {"role": role, "content": strip_answer_footer_from_text(content) if role == "assistant" else content}
            )
    return turns


def _asked_last_turn(messages: list[dict]) -> bool:
    """The reply to the visitor's previous message ended on a question; an opening greeting is no reply."""
    earlier = messages[:-1]
    replies = [index for index, m in enumerate(earlier) if m["role"] == "assistant"]
    if not replies or not any(m["role"] == "user" for m in earlier[: replies[-1]]):
        return False
    return earlier[replies[-1]]["content"].rstrip(" \n*_)\"'").endswith("?")


def clarify_gate(messages: list[dict], chunks: list[dict], threshold: float) -> ClarifyDecision:
    """Whether the retrieved articles call for one question, and its options. Never calls a model."""
    documents = _strong_documents(chunks, threshold)
    if not documents:
        return ClarifyDecision("soft_gap")
    conversation = _messages(messages)
    if _asked_last_turn(conversation):
        return ClarifyDecision("asked_last_turn", documents=len(documents))
    if len(documents) < 2:
        return ClarifyDecision("one_document", documents=len(documents))
    said = set().union(*(_stems(m["content"]) for m in conversation if m["role"] == "user"))
    members, topic = _best_group(documents, said)
    variants: list[str] = []
    for member in members:
        variant = _variant(member, [m for m in members if m is not member], topic)
        if variant and variant.casefold() not in {v.casefold() for v in variants}:
            variants.append(variant)
    options = tuple(variants[:_MAX_OPTIONS])
    # A variant longer than two words is a leftover of titles that differ in
    # more than one thing, not the one fact the visitor is asked for.
    if len(options) < 2 or any(len(o.split()) > _MAX_OPTION_WORDS for o in options):
        return ClarifyDecision("no_axis", documents=len(documents))
    titles_overlap = any(a.stems & b.stems for a in members for b in members if a is not b)
    axis = _axis(options, titles_overlap)
    if axis == "device":
        # A visitor who names any device already said which one they use, even
        # when the articles call it differently ("iPhone" against an "iOS"
        # option). The option is the device itself, not a brand word the
        # title happens to carry beside it ("Zeta iPhone").
        named = bool(_devices(said))
        options = tuple(" ".join(w for w in o.split() if _devices(_stems(w))) or o for o in options)
    else:
        named = bool(said & set().union(*(_stems(v) for v in variants)))
    return ClarifyDecision("variant_named" if named else "asked", axis, len(documents), options)
