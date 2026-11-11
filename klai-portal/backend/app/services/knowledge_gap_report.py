"""The weekly knowledge-gap list of one organisation, from the signals every chat turn already stores.

Every widget answer keeps its whole trail in ``widget_messages.answer_signals``
(decision, gap_type, the search with the passages it found, the case, the
selection with the parts it left open); the internal chat keeps the same
signals without text in ``internal_chat_turns``. This module reads that trail
and makes no model call: a turn counts when it ended in a refusal, a
clarifying question, a request for a person or a partial answer, or answered
on a weak search (gap_type ``soft``).

Turns are grouped on topic without a model, on one of two anchors. A turn the
search found an article that fell short (``article_misses_part``,
``variant_unclear``) is anchored on that article (``source_url_key`` of its
url without query string and fragment, so tracking parameters do not split it):
an owner fixes one article, not one wording, and the case's problem text varies
per visitor while the article they matched does not. A ``weak_match`` is not
anchored: its search scored below the weak-search threshold, so the top article
is weak evidence of the topic, and one generic page can be the top hit of
unrelated questions, which one decision would then hide together. Every other
turn (``no_article``, ``contact_wish``, ``weak_match``, a best article without
url) is grouped on the product the case named plus the three
longest content words of the case's problem (the visitor's search when the
problem holds no content word). Words are lower-cased, chat filler is a
stopword, and a trailing "en" or "s" is stripped from a word when at least four
letters stay, so "oproep" and "oproepen" are one word. The key is a hash of the
anchor, a pure function of one turn's signals, so the same topic lands on the
same key next week, which is what a decision (``portal_gap_proposals``) and a
proposal hang on, and no visitor word reaches a URL or a stored key. Known
ceiling: two wordings of one need without an article that share fewer than
three long words make two clusters; the upgrade is a deterministic synonym
table, still without a model.

A cluster without an article and without topic words, and every internal cluster (internal turns
carry no text, so they are counted per kind only), is a catch-all: it is not
``decidable``, because a final decision on it would hide every future turn of
that kind, and a proposal on it would mix unrelated questions.

Below telemetry ``full`` the list carries no visitor text: counts, kinds,
product, the articles found and ``last_seen`` only, the same line
``record_gap_event`` draws with ``[REDACTED:shadow]``.
"""

from __future__ import annotations

import hashlib
import re
from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from sqlalchemy import column, func, or_, select, table, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.models.gap_proposals import PortalGapKeptQuestion, PortalGapProposal
from app.models.portal import PortalOrg, PortalUser
from app.models.retrieval_gaps import PortalRetrievalGap
from app.models.widgets import WidgetConversation, WidgetMessage
from app.services.citations import source_url_key
from app.services.gap_events import JUDGE_CALLER_CLIENT_ID, REVIEW_CALLER_CLIENT_ID
from app.services.widget_audit import hash_audit_value

GAP_DECISIONS = ("refusal", "clarifying_question", "human_request", "partial_answer")
GapKind = Literal[
    "no_article",
    "article_misses_part",
    "variant_unclear",
    "contact_wish",
    "weak_match",
    "reported_review",
    "reported_judge",
]
# A row a reviewer or the conversation judge filed in portal_retrieval_gaps, by the kind it shows as.
_REPORTED_KINDS: dict[str, GapKind] = {"review": "reported_review", "judge": "reported_judge"}
# Ties in a cluster's main kind fall to the one an owner can act on first.
_KIND_ORDER: tuple[GapKind, ...] = (
    "no_article",
    "article_misses_part",
    "variant_unclear",
    "weak_match",
    "contact_wish",
    "reported_review",
    "reported_judge",
)

# The widget monitoring probe (scripts/widget-probe.py, docs/runbooks/widget-probe.md)
# asks this fixed question from this origin every run; it is not a visitor.
PROBE_ORIGIN = "https://probe.example.com"
PROBE_QUESTION = "Hoe lang rijst het deeg van het Proefbrood van Probe Bakkerij?"

# How long the question of a counted widget turn is kept after its messages are deleted, at telemetry
# ``full`` only (portal_gap_kept_questions): long enough for a subject to collect conversations over four
# weekly lists. Owner decision of 2026-11-11, docs/architecture/knowledge-gap-to-article.md §6.
KEPT_QUESTION_DAYS = 28
# Advisory lock class (with the org id as second key) that orders the keep against every removal. The
# retention sweep takes it shared before it reads the level, the test marks and the decisions it keeps on;
# a removal takes it exclusively before its DELETE. So a removal either waits for the sweep's commit and
# deletes what it kept, or the sweep waits for the removal's commit and sees the new state.
KEPT_QUESTIONS_LOCK = 20261111

_TOPIC_WORDS = 3
_MIN_WORD = 3
# Letters that must stay after a plural or infinitive ending is stripped.
_MIN_STEM = 4
_ANCHORED_KINDS = frozenset({"article_misses_part", "variant_unclear"})
# Longer than any Dutch or English word an owner would write about; an IBAN
# without spaces, a token or a hash is longer.
_MAX_WORD = 24
_QUESTION_CHARS = 200
_MAX_QUESTIONS = 20
_MAX_ARTICLES = 5
_MAX_OPEN_PARTS = 10
_WORD = re.compile(r"[^\W_]+")
# What a list row may show of a visitor's words: an e-mail address or a run of
# seven digits (customer, order, phone and account numbers) never reaches the page.
_MASKED = re.compile(r"\S+@\S+|\d{7,}")
# The kinds of the default view: a question the chat could not answer. A weak match was answered and a contact
# request asks for a person; each has its own filter value.
ARTICLE_GAP_KINDS = frozenset(
    {"no_article", "article_misses_part", "variant_unclear", "reported_review", "reported_judge"}
)
# How clear a gap is, for the order of groups with equal conversations; lower comes first.
_SEVERITY = {"no_article": 0, "article_misses_part": 1, "variant_unclear": 2, "reported_review": 3, "reported_judge": 3}
# Chat filler says nothing about the subject: pronouns, auxiliaries, the words a
# visitor uses to ask ("graag", "hoe", "lukt") and the generic verbs and
# nouns around a request ("instellen", "aanvragen", "klant").
_STOPWORDS = frozenset(
    """
    aan alle als bij dan dat de deze die dit door een en er geen heb hebben het hoe ik in is je jij kan
    kun kunt maar met mijn moet naar niet nog of om ons onze ook op te tot u uw van voor waar wanneer
    waarom wat wel werkt wil wordt worden zijn zou graag mogelijk iets zet doe gaat krijg vind
    bezoeker bezoekers klant klanten weten weet lukt lukken willen wilt kunnen
    mag mogen moeten zouden zal zullen heeft had hadden ben bent was waren werd wij we jullie
    ze zij hij haar hun hem mij me jouw wie welke welk hier daar toch al even erg zeer heel veel
    meer minder anders ander andere nieuw nieuwe opnieuw via vraag vragen vraagt aanvragen toevoegen
    wijzigen instellen bijvoorbeeld krijgen krijgt doen maken gaan komen helpen hulp
    dus want omdat dank bedankt hallo
    the and are can does for from get
    have how not our that this what when where which who why will with you your about into there
    could would should want need please help know able cannot customer customers visitor visitors
    user users ask question questions request requests new other add change set again via
    me my we us they their them its she his her was were been being than then also
    """.split()
)


@dataclass(frozen=True)
class GapTurn:
    surface: Literal["widget", "internal"]
    signals: dict[str, Any]
    created_at: datetime
    # The visitor's own message of this turn (widget_messages, role user), when stored.
    visitor_text: str | None = None
    # Widget turns of one conversation count once; an internal turn has none and counts alone. The loaders
    # fill in conversation_ref(), the value a kept turn also carries.
    conversation_id: int | str | None = None


def is_gap(signals: Mapping[str, Any]) -> bool:
    return signals.get("decision") in GAP_DECISIONS or signals.get("gap_type") == "soft"


def gap_kind(signals: Mapping[str, Any]) -> GapKind:
    """What the owner has to do for one counted turn, from the stored decision, search and selection."""
    if signals.get("filed_by") in _REPORTED_KINDS:
        return _REPORTED_KINDS[signals["filed_by"]]
    decision = signals.get("decision")
    if decision == "clarifying_question":
        return "variant_unclear"
    if decision == "human_request":
        return "contact_wish"
    search = signals.get("search")
    if signals.get("gap_type") == "hard" or (isinstance(search, dict) and not search.get("found")):
        return "no_article"
    if _open_parts(signals) or decision == "partial_answer":
        return "article_misses_part"
    # A refusal with articles found: the selection said none of them answers.
    return "no_article" if decision == "refusal" else "weak_match"


def _stem(word: str) -> str:
    """Crude plural/infinitive strip: a trailing "en", else "s", when four or more letters stay."""
    for suffix in ("en", "s"):
        if word.endswith(suffix) and len(word) - len(suffix) >= _MIN_STEM:
            return word[: -len(suffix)]
    return word


def _topic_terms(text: str, skip: frozenset[str]) -> list[tuple[str, str]]:
    """The three longest content words as (stem, shown word), sorted by stem.

    A whitespace token holding a digit or an ``@`` (a phone, order or account
    number, an e-mail address) never becomes a topic word, nor does a token
    longer than ``_MAX_WORD``; stopwords and the product's own words, already
    in the key, go before the choice. Words with one stem are one term and
    show as the shortest of them.
    """
    shown: dict[str, str] = {}
    for token in text.lower().split():
        if "@" in token or any(char.isdigit() for char in token):
            continue
        for word in _WORD.findall(token):
            stem = _stem(word)
            if _MIN_WORD <= len(word) <= _MAX_WORD and word not in skip and stem not in skip:
                shown[stem] = min(word, shown.get(stem, word), key=lambda w: (len(w), w))
    return sorted(sorted(shown.items(), key=lambda kv: (-len(kv[0]), kv[0]))[:_TOPIC_WORDS])


def topic_words(text: str, *, product: str = "") -> list[str]:
    """The three longest content words as shown, in stem order."""
    return [word for _, word in _topic_terms(text, _STOPWORDS | set(product.split()))]


def _score(found: Mapping[str, Any]) -> float:
    # A kept passage has no score and goes last.
    return found["score"] if isinstance(found.get("score"), int | float) else -1.0


def _anchor(signals: Mapping[str, Any]) -> tuple[str, dict] | None:
    """(url key, article) a turn is grouped on: its best found article, for the kinds where one fell short."""
    found = _found(signals)
    if not found or gap_kind(signals) not in _ANCHORED_KINDS:
        return None
    article = max(found, key=_score)
    url = article.get("url")
    url_key = source_url_key(url.split("#")[0].split("?")[0] if isinstance(url, str) else url)
    return (url_key, article) if url_key else None


def _topic(turn: GapTurn) -> tuple[str, list[tuple[str, str]]]:
    case = turn.signals.get("case")
    if not isinstance(case, dict):
        case = {}
    product = " ".join(str(case.get("product") or "").lower().split())
    skip = _STOPWORDS | set(product.split())
    # The case's problem names the subject; the search query is a rewrite of the
    # whole question and only stands in when the problem holds no content word.
    terms = _topic_terms(str(case.get("problem") or ""), skip) or _topic_terms(_query(turn.signals), skip)
    return product, terms


def cluster_key(turn: GapTurn) -> str:
    if turn.surface == "internal":
        return f"internal|{gap_kind(turn.signals)}"
    product, terms = _topic(turn)
    anchor = _anchor(turn.signals)
    basis = f"{product}|article:{anchor[0]}" if anchor else f"{product}|{' '.join(stem for stem, _ in terms)}"
    return "t" + hashlib.sha256(basis.encode()).hexdigest()[:20]


def _query(signals: Mapping[str, Any]) -> str:
    search = signals.get("search")
    return str(search.get("query") or "") if isinstance(search, dict) else ""


def _open_parts(signals: Mapping[str, Any]) -> list[str]:
    selection = signals.get("selection")
    return [str(p) for p in selection.get("open") or []] if isinstance(selection, dict) else []


def _found(signals: Mapping[str, Any]) -> list[dict]:
    search = signals.get("search")
    found = search.get("found") if isinstance(search, dict) else None
    return [f for f in found or [] if isinstance(f, dict) and (f.get("title") or f.get("url"))]


def _distinct(values: Iterable[str], limit: int) -> list[str]:
    return list(dict.fromkeys(v for v in values if v))[:limit]


def _filed(turn: GapTurn) -> bool:
    return "filed_by" in turn.signals


def mask(text: str) -> str:
    return _MASKED.sub("…", text)


def _shown_question(turn: GapTurn) -> str:
    # The visitor's own words; the search query only when the message is gone
    # (the case step rewrites the query into the articles' language).
    return mask(" ".join((turn.visitor_text or _query(turn.signals)).split()))[:_QUESTION_CHARS]


def conversation_ref(org_id: int, conversation_id: int | None) -> str | None:
    """What the list counts distinct conversations on: a keyed hash, so a kept turn has no link back to its row."""
    return None if conversation_id is None else hash_audit_value(f"gap-conversation:{org_id}:{conversation_id}")


async def drop_kept_questions(
    db: AsyncSession, org_id: int, *, cluster_key: str | None = None, conversation_id: int | None = None
) -> int:
    """Delete an org's kept gap questions (of one group, or one conversation) in the caller's transaction.

    Takes KEPT_QUESTIONS_LOCK exclusively first, so rows a running sweep is
    about to commit are deleted as well; the lock ends with the transaction.
    """
    await db.execute(
        text("SELECT pg_advisory_xact_lock(:lock, :org_id)"), {"lock": KEPT_QUESTIONS_LOCK, "org_id": org_id}
    )
    sql = "DELETE FROM portal_gap_kept_questions WHERE org_id = :org_id"
    params: dict[str, Any] = {"org_id": org_id}
    if cluster_key is not None:
        sql += " AND cluster_key = :cluster_key"
        params["cluster_key"] = cluster_key
    if conversation_id is not None:
        sql += " AND conversation_ref = :conversation_ref"
        params["conversation_ref"] = conversation_ref(org_id, conversation_id)
    result = await db.execute(text(sql), params)
    return result.rowcount or 0  # type: ignore[attr-defined]


def is_probe(loaded_origin: str | None, first_user_query: str | None) -> bool:
    return loaded_origin == PROBE_ORIGIN or " ".join((first_user_query or "").split()) == PROBE_QUESTION


def kept_question(turn: GapTurn) -> tuple[str, dict[str, Any]] | None:
    """(question, signals) a counted widget turn keeps once its messages are deleted: only what the list reads.

    The question is the one the list shows (masked, cut); the signals keep the
    decision, gap type and language, the case's product and its topic words in
    place of the problem text, the found articles' title, url and score, and the
    masked open parts. The search query, the passages, the reply and every other
    field are dropped. None when the reduced signals would land the turn in
    another group or kind (a product that masking changes, or a change to the
    grouping); such a turn is not kept.
    """
    signals = turn.signals
    product, terms = _topic(turn)
    kept: dict[str, Any] = {
        "decision": signals.get("decision"),
        "gap_type": signals.get("gap_type"),
        "language": signals.get("language"),
        # A product carrying an address or a digit run changes when masked, so its key changes and the
        # guard below keeps nothing of that turn.
        "case": {"product": mask(product), "problem": " ".join(word for _, word in terms)},
        "selection": {"open": [mask(part) for part in _open_parts(signals)]},
    }
    search = signals.get("search")
    if isinstance(search, dict):
        found = [f for f in search.get("found") or [] if isinstance(f, dict)]
        kept["search"] = {"query": "", "found": [{k: f.get(k) for k in ("title", "url", "score")} for f in found]}
    question = _shown_question(turn)
    reduced = GapTurn("widget", kept, turn.created_at, question)
    if cluster_key(reduced) != cluster_key(turn) or gap_kind(kept) != gap_kind(signals):
        return None
    return question, kept


GapView = Literal["article_gaps", "weak_matches", "contact_requests", "all"]


def filter_clusters(clusters: list[dict[str, Any]], show: GapView) -> list[dict[str, Any]]:
    """The clusters of one view; the list defaults to ``article_gaps``, the gaps an owner can write an article for.

    A group is in a view when it holds at least one turn of that view's kinds
    (``kinds`` counts every turn), whichever kind is its dominant, displayed one.
    """
    if show == "all":
        return clusters
    wanted = {
        "article_gaps": ARTICLE_GAP_KINDS,
        "weak_matches": {"weak_match"},
        "contact_requests": {"contact_wish"},
    }[show]
    return [c for c in clusters if wanted & c["kinds"].keys()]


def article_gap_group_count(clusters: list[dict[str, Any]]) -> int:
    """What the Insights tile shows: every group the default view lists."""
    return len(filter_clusters(clusters, "article_gaps"))


def build_clusters(
    turns: Iterable[GapTurn],
    decisions: Mapping[str, dict[str, Any]],
    *,
    include_decided: bool = False,
    literal: bool = True,
) -> list[dict[str, Any]]:
    """Group the counted turns per topic, decidable and biggest first; a cluster of one stays, at the bottom of its block.

    "Biggest" is the number of distinct conversations (``count``), not turns: a
    visitor who rephrases a question five times is one conversation.

    ``decisions`` maps a cluster key to its ``portal_gap_proposals`` row
    (``load_cluster_decisions``); a decided cluster is left out unless
    ``include_decided``. ``literal`` False (telemetry below ``full``) leaves
    every visitor text out.
    """
    groups: dict[str, list[GapTurn]] = {}
    reported: list[GapTurn] = []
    for turn in turns:
        if not is_gap(turn.signals):
            continue
        if _filed(turn):
            reported.append(turn)
        else:
            groups.setdefault(cluster_key(turn), []).append(turn)
    # A reported row has no product, so it joins the chat group on the same topic
    # words (the biggest one when several products share them), else its own group.
    by_words: dict[tuple[str, ...], str] = {}
    for key, members in sorted(groups.items(), key=lambda kv: len(kv[1])):
        words = tuple(stem for stem, _ in _topic(members[0])[1])
        if words and not _anchor(members[0].signals):
            by_words[words] = key
    for turn in reported:
        words = tuple(stem for stem, _ in _topic(turn)[1])
        groups.setdefault(cluster_key(turn) if not words else by_words.get(words, cluster_key(turn)), []).append(turn)

    clusters = []
    for key, members in groups.items():
        decided = decisions.get(key) or {}
        if decided.get("decision", "none") != "none" and not include_decided:
            continue
        members.sort(key=lambda t: t.created_at, reverse=True)
        kinds = Counter(gap_kind(t.signals) for t in members)
        chat_kinds = {k: n for k, n in kinds.items() if k not in _REPORTED_KINDS.values()}
        widget = [t for t in members if t.surface == "widget"]
        # A chat turn names the product; a reported row only has its question text.
        lead = next((t for t in widget if not _filed(t)), widget[0]) if widget else None
        product, terms = _topic(lead) if lead else ("", [])
        words = [word for _, word in terms]
        anchored = [a for a in (_anchor(t.signals) for t in widget) if a]
        # Best score first; a kept passage has no score and goes last.
        best: dict[str, dict] = {}
        for f in sorted(
            (f for t in widget for f in _found(t.signals)),
            key=_score,
            reverse=True,
        ):
            best.setdefault(str(f.get("url") or f.get("title")), f)
        articles = [{"title": f.get("title"), "url": f.get("url"), "score": f.get("score")} for f in best.values()]
        questions = _distinct((_shown_question(t) for t in widget), _MAX_QUESTIONS)
        open_parts = _distinct((mask(p) for t in widget for p in _open_parts(t.signals)), _MAX_OPEN_PARTS)
        languages = Counter(str(t.signals["language"]) for t in members if t.signals.get("language"))
        if anchored:
            # An article title is the owner's own text, not the visitor's, so it shows at every telemetry level.
            _, title, url = min((-_score(a), str(a.get("title") or ""), str(a.get("url"))) for _, a in anchored)
            topic = title or url
        else:
            topic = " ".join(words) if literal else ""
        clusters.append(
            {
                "cluster_key": key,
                "product": product or None,
                "topic": topic or product or "—",
                "count": len({t.conversation_id if t.conversation_id is not None else id(t) for t in members}),
                "widget_count": len(widget),
                "internal_count": len(members) - len(widget),
                # A group with chat turns keeps their kind; "reported" only names a group that exists through filed rows.
                "kind": max(chat_kinds or kinds, key=lambda k: (kinds[k], -_KIND_ORDER.index(k))),
                "reported_by": sorted({t.signals["filed_by"] for t in members if _filed(t)}),
                "kinds": dict(kinds),
                "question": questions[0] if questions and literal else None,
                "questions": questions if literal else [],
                "article": articles[0] if articles else None,
                "articles": articles[:_MAX_ARTICLES],
                "open_parts": open_parts if literal else [],
                "language": languages.most_common(1)[0][0] if languages else None,
                "last_seen": members[0].created_at.isoformat(),
                "decidable": bool(widget) and bool(anchored or words),
                "decision": decided.get("decision", "none"),
                "decided_by": decided.get("decided_by"),
                "decided_at": decided.get("decided_at"),
                "has_proposal": bool(decided.get("has_proposal")),
            }
        )
    # Decidable clusters first (a singleton stays at the bottom of that block),
    # then the widget catch-alls, then the internal per-kind aggregates; within a
    # block biggest first, then the clearer kind (``_SEVERITY``), then newest, then by key so the order is reproducible.
    clusters.sort(key=lambda c: c["cluster_key"])
    clusters.sort(key=lambda c: c["last_seen"], reverse=True)
    clusters.sort(key=lambda c: _SEVERITY.get(c["kind"], len(_SEVERITY)))
    clusters.sort(key=lambda c: c["count"], reverse=True)
    clusters.sort(key=lambda c: (not c["decidable"]) + (c["widget_count"] == 0))
    return clusters


_internal_chat_turns = table(
    "internal_chat_turns", column("org_id"), column("answer_signals", JSONB), column("created_at")
)


def _counted(signals: Any) -> Any:
    return or_(signals["decision"].astext.in_(GAP_DECISIONS), signals["gap_type"].astext == "soft")


async def load_gap_turns(
    db: AsyncSession, *, org_id: int, since: datetime, now: datetime | None = None
) -> list[GapTurn]:
    """The org's counted turns since ``since``: widget answers (preview, test and probe left out) and internal turns.

    Runs on a tenant-scoped session (Cat-D on all three tables) and names the
    org in every predicate as well. A widget answer brings the visitor's own
    message: the latest user row of the conversation before it (the user row
    is written first, record_widget_turn). A widget turn whose messages the
    retention sweep deleted comes from portal_gap_kept_questions, while the
    org is at ``full``.
    """
    visitor = aliased(WidgetMessage)
    visitor_text = (
        select(visitor.content)
        .where(
            visitor.conversation_id == WidgetMessage.conversation_id,
            visitor.org_id == org_id,
            visitor.role == "user",
            visitor.sequence < WidgetMessage.sequence,
        )
        .order_by(visitor.sequence.desc())
        .limit(1)
        .correlate(WidgetMessage)
        .scalar_subquery()
    )
    widget_rows = (
        await db.execute(
            select(
                WidgetMessage.id,
                WidgetMessage.answer_signals,
                WidgetMessage.created_at,
                WidgetMessage.conversation_id,
                WidgetConversation.loaded_origin,
                WidgetConversation.first_user_query,
                visitor_text.label("visitor_text"),
            )
            .join(WidgetConversation, WidgetConversation.id == WidgetMessage.conversation_id)
            .where(
                WidgetMessage.org_id == org_id,
                WidgetConversation.org_id == org_id,
                WidgetMessage.role == "assistant",
                WidgetMessage.created_at >= since,
                WidgetConversation.is_preview.is_(False),
                WidgetConversation.is_test.is_(False),
                _counted(WidgetMessage.answer_signals),
            )
        )
    ).all()
    # Read after the live turns: a turn the retention sweep moves in between is
    # kept in this read and dropped below by its message id, so it counts once.
    kept_rows = (
        await db.execute(
            select(
                PortalGapKeptQuestion.message_id,
                PortalGapKeptQuestion.signals,
                PortalGapKeptQuestion.turn_at,
                PortalGapKeptQuestion.question,
                PortalGapKeptQuestion.conversation_ref,
            )
            .join(PortalOrg, PortalOrg.id == PortalGapKeptQuestion.org_id)
            .where(
                PortalGapKeptQuestion.org_id == org_id,
                PortalOrg.telemetry_level == "full",
                # Never older than the kept lifetime, whatever window was asked: the daily purge may be a day behind.
                PortalGapKeptQuestion.turn_at
                >= max(since, (now or datetime.now(tz=UTC)) - timedelta(days=KEPT_QUESTION_DAYS)),
            )
        )
    ).all()
    internal_rows = (
        await db.execute(
            select(_internal_chat_turns.c.answer_signals, _internal_chat_turns.c.created_at).where(
                _internal_chat_turns.c.org_id == org_id,
                _internal_chat_turns.c.created_at >= since,
                _counted(_internal_chat_turns.c.answer_signals),
            )
        )
    ).all()
    live_ids = {row.id for row in widget_rows}
    return (
        [
            GapTurn(
                "widget",
                row.answer_signals,
                row.created_at,
                row.visitor_text,
                conversation_ref(org_id, row.conversation_id),
            )
            for row in widget_rows
            if not is_probe(row.loaded_origin, row.first_user_query)
        ]
        + [
            GapTurn("widget", row.signals, row.turn_at, row.question, row.conversation_ref)
            for row in kept_rows
            if row.message_id not in live_ids
        ]
        + [GapTurn("internal", row.answer_signals, row.created_at) for row in internal_rows]
    )


async def load_filed_gaps(db: AsyncSession, *, org_id: int, since: datetime) -> list[GapTurn]:
    """Open rows a reviewer or the conversation judge filed, as turns: they join a group by the same key as chat turns.

    Support-case findings stay out (their own screens), as do rows of a test or
    preview conversation (a purged conversation has no row to join and stays).
    A row whose text the telemetry level redacted has no question text, so it
    forms a group that cannot be decided.
    """
    rows = (
        await db.execute(
            select(
                PortalRetrievalGap.query_text,
                PortalRetrievalGap.caller_client_id,
                PortalRetrievalGap.conversation_id,
                PortalRetrievalGap.occurred_at,
                PortalRetrievalGap.language,
            )
            .outerjoin(
                WidgetConversation,
                (WidgetConversation.id == PortalRetrievalGap.conversation_id) & (WidgetConversation.org_id == org_id),
            )
            .where(
                PortalRetrievalGap.org_id == org_id,
                PortalRetrievalGap.occurred_at >= since,
                PortalRetrievalGap.resolved_at.is_(None),
                PortalRetrievalGap.support_case_id.is_(None),
                PortalRetrievalGap.caller_client_id.in_((REVIEW_CALLER_CLIENT_ID, JUDGE_CALLER_CLIENT_ID)),
                or_(
                    WidgetConversation.id.is_(None),
                    (WidgetConversation.is_test.is_(False)) & (WidgetConversation.is_preview.is_(False)),
                ),
            )
        )
    ).all()
    turns = []
    for row in rows:
        text = "" if row.query_text.startswith("[REDACTED:") else row.query_text
        signals = {
            "decision": "refusal",
            "filed_by": "review" if row.caller_client_id == REVIEW_CALLER_CLIENT_ID else "judge",
            "language": row.language,
            "case": {"problem": text, "product": ""},
            "search": {"query": text, "found": []},
        }
        turns.append(GapTurn("widget", signals, row.occurred_at, text, conversation_ref(org_id, row.conversation_id)))
    return turns


async def load_cluster_decisions(db: AsyncSession, *, org_id: int) -> dict[str, dict[str, Any]]:
    rows = (
        await db.execute(
            select(
                PortalGapProposal.cluster_key,
                PortalGapProposal.decision,
                PortalGapProposal.decided_at,
                PortalGapProposal.status,
                func.coalesce(PortalUser.display_name, PortalUser.email).label("decided_by"),
            )
            .outerjoin(PortalUser, (PortalUser.id == PortalGapProposal.decided_by) & (PortalUser.org_id == org_id))
            .where(PortalGapProposal.org_id == org_id)
        )
    ).all()
    return {
        row.cluster_key: {
            "decision": row.decision,
            "decided_by": row.decided_by,
            "decided_at": row.decided_at.isoformat() if row.decided_at else None,
            "has_proposal": row.status == "ready",
        }
        for row in rows
    }


async def knowledge_gap_clusters(
    db: AsyncSession, *, org_id: int, days: int = 7, include_decided: bool = False, now: datetime | None = None
) -> list[dict[str, Any]]:
    # The level is the org's own, re-read here as record_gap_event does: never a caller's.
    level = (await db.execute(select(PortalOrg.telemetry_level).where(PortalOrg.id == org_id))).scalar_one()
    now = now or datetime.now(tz=UTC)
    since = now - timedelta(days=days)
    turns = await load_gap_turns(db, org_id=org_id, since=since, now=now) + await load_filed_gaps(
        db, org_id=org_id, since=since
    )
    return build_clusters(
        turns,
        await load_cluster_decisions(db, org_id=org_id),
        include_decided=include_decided,
        literal=level == "full",
    )
