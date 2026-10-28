"""The weekly knowledge-gap list of one organisation, from the signals every chat turn already stores.

Every widget answer keeps its whole trail in ``widget_messages.answer_signals``
(decision, gap_type, the search with the passages it found, the case, the
selection with the parts it left open); the internal chat keeps the same
signals without text in ``internal_chat_turns``. This module reads that trail
and makes no model call: a turn counts when it ended in a refusal, a
clarifying question, a request for a person or a partial answer, or answered
on a weak search (gap_type ``soft``).

Turns are grouped on topic without a model: the product the case named plus
the three longest content words of the case's problem (the visitor's search
when the case has none). The key is a pure function of one turn's signals, so
the same topic lands on the same key next week, which is what a decision
(``portal_gap_proposals``) and a proposal hang on. Known ceiling: two wordings
of one need that share fewer than three long words make two clusters; the
upgrade is a deterministic synonym/stem table, still without a model.

Internal turns carry no text, so they are counted per kind only, one cluster
per kind.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from sqlalchemy import column, func, or_, select, table
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.gap_proposals import PortalGapProposal
from app.models.portal import PortalUser
from app.models.widgets import WidgetConversation, WidgetMessage

GAP_DECISIONS = ("refusal", "clarifying_question", "human_request", "partial_answer")
GapKind = Literal["no_article", "article_misses_part", "variant_unclear", "contact_wish", "weak_match"]
# Ties in a cluster's main kind fall to the one an owner can act on first.
_KIND_ORDER: tuple[GapKind, ...] = (
    "no_article",
    "article_misses_part",
    "variant_unclear",
    "weak_match",
    "contact_wish",
)

# The widget monitoring probe (scripts/widget-probe.py, docs/runbooks/widget-probe.md)
# asks this fixed question from this origin every run; it is not a visitor.
PROBE_ORIGIN = "https://probe.example.com"
PROBE_QUESTION = "Hoe lang rijst het deeg van het Proefbrood van Probe Bakkerij?"

_TOPIC_WORDS = 3
_MIN_WORD = 3
_QUESTION_CHARS = 200
_MAX_QUESTIONS = 20
_MAX_ARTICLES = 5
_MAX_OPEN_PARTS = 10
_WORD = re.compile(r"[^\W_]+")
_STOPWORDS = frozenset(
    """
    aan alle als bij dan dat de deze die dit door een en er geen heb hebben het hoe ik in is je jij kan
    kun kunt maar met mijn moet naar niet nog of om ons onze ook op te tot u uw van voor waar wanneer
    waarom wat wel werkt wil wordt worden zijn zou graag mogelijk iets zet doe gaat krijg vind
    the and are can does for from get
    have how not our that this what when where which who why will with you your about into there
    """.split()
)


@dataclass(frozen=True)
class GapTurn:
    surface: Literal["widget", "internal"]
    signals: dict[str, Any]
    created_at: datetime


def is_gap(signals: Mapping[str, Any]) -> bool:
    return signals.get("decision") in GAP_DECISIONS or signals.get("gap_type") == "soft"


def gap_kind(signals: Mapping[str, Any]) -> GapKind:
    """What the owner has to do for one counted turn, from the stored decision, search and selection."""
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


def topic_words(text: str, *, product: str = "") -> list[str]:
    """The three longest content words, sorted; the product's own words are already in the key."""
    skip = _STOPWORDS | set(product.split())
    words = {w for w in _WORD.findall(text.lower()) if len(w) >= _MIN_WORD and w not in skip}
    return sorted(sorted(words, key=lambda w: (-len(w), w))[:_TOPIC_WORDS])


def cluster_key(turn: GapTurn) -> str:
    if turn.surface == "internal":
        return f"internal|{gap_kind(turn.signals)}"
    case = turn.signals.get("case")
    if not isinstance(case, dict):
        case = {}
    product = " ".join(str(case.get("product") or "").lower().split())
    text = str(case.get("problem") or "") or _query(turn.signals)
    return f"{product}|{' '.join(topic_words(text, product=product))}"


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


def build_clusters(
    turns: Iterable[GapTurn], decisions: Mapping[str, dict[str, Any]], *, include_decided: bool = False
) -> list[dict[str, Any]]:
    """Group the counted turns per topic, biggest first; a cluster of one stays, at the bottom.

    ``decisions`` maps a cluster key to its ``portal_gap_proposals`` row
    (``load_cluster_decisions``); a decided cluster is left out unless
    ``include_decided``.
    """
    groups: dict[str, list[GapTurn]] = {}
    for turn in turns:
        if is_gap(turn.signals):
            groups.setdefault(cluster_key(turn), []).append(turn)

    clusters = []
    for key, members in groups.items():
        decided = decisions.get(key) or {}
        if decided.get("decision", "none") != "none" and not include_decided:
            continue
        members.sort(key=lambda t: t.created_at, reverse=True)
        kinds = Counter(gap_kind(t.signals) for t in members)
        widget = [t for t in members if t.surface == "widget"]
        # Best score first; a kept passage has no score and goes last.
        best: dict[str, dict] = {}
        for f in sorted(
            (f for t in widget for f in _found(t.signals)),
            key=lambda f: f["score"] if isinstance(f.get("score"), int | float) else -1.0,
            reverse=True,
        ):
            best.setdefault(str(f.get("url") or f.get("title")), f)
        articles = [{"title": f.get("title"), "url": f.get("url"), "score": f.get("score")} for f in best.values()]
        questions = _distinct((" ".join(_query(t.signals).split())[:_QUESTION_CHARS] for t in widget), _MAX_QUESTIONS)
        product, _, words = key.partition("|")
        languages = Counter(str(t.signals["language"]) for t in members if t.signals.get("language"))
        clusters.append(
            {
                "cluster_key": key,
                "product": product if product and product != "internal" else None,
                "topic": words,
                "count": len(members),
                "widget_count": len(widget),
                "internal_count": len(members) - len(widget),
                "kind": max(kinds, key=lambda k: (kinds[k], -_KIND_ORDER.index(k))),
                "kinds": dict(kinds),
                "question": questions[0] if questions else None,
                "questions": questions,
                "article": articles[0] if articles else None,
                "articles": articles[:_MAX_ARTICLES],
                "open_parts": _distinct((p for t in widget for p in _open_parts(t.signals)), _MAX_OPEN_PARTS),
                "language": languages.most_common(1)[0][0] if languages else None,
                "last_seen": members[0].created_at.isoformat(),
                "decision": decided.get("decision", "none"),
                "decided_by": decided.get("decided_by"),
                "decided_at": decided.get("decided_at"),
                "has_proposal": bool(decided.get("has_proposal")),
            }
        )
    # Biggest first, then newest, then by key so the order is reproducible.
    clusters.sort(key=lambda c: c["cluster_key"])
    clusters.sort(key=lambda c: c["last_seen"], reverse=True)
    clusters.sort(key=lambda c: c["count"], reverse=True)
    return clusters


_internal_chat_turns = table(
    "internal_chat_turns", column("org_id"), column("answer_signals", JSONB), column("created_at")
)


def _counted(signals: Any) -> Any:
    return or_(signals["decision"].astext.in_(GAP_DECISIONS), signals["gap_type"].astext == "soft")


async def load_gap_turns(db: AsyncSession, *, org_id: int, since: datetime) -> list[GapTurn]:
    """The org's counted turns since ``since``: widget answers (preview, test and probe left out) and internal turns.

    Runs on a tenant-scoped session (Cat-D on all three tables) and names the
    org in every predicate as well.
    """
    widget_rows = (
        await db.execute(
            select(
                WidgetMessage.answer_signals,
                WidgetMessage.created_at,
                WidgetConversation.loaded_origin,
                WidgetConversation.first_user_query,
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
    internal_rows = (
        await db.execute(
            select(_internal_chat_turns.c.answer_signals, _internal_chat_turns.c.created_at).where(
                _internal_chat_turns.c.org_id == org_id,
                _internal_chat_turns.c.created_at >= since,
                _counted(_internal_chat_turns.c.answer_signals),
            )
        )
    ).all()
    return [
        GapTurn("widget", row.answer_signals, row.created_at)
        for row in widget_rows
        if row.loaded_origin != PROBE_ORIGIN and " ".join((row.first_user_query or "").split()) != PROBE_QUESTION
    ] + [GapTurn("internal", row.answer_signals, row.created_at) for row in internal_rows]


async def load_cluster_decisions(db: AsyncSession, *, org_id: int) -> dict[str, dict[str, Any]]:
    rows = (
        await db.execute(
            select(
                PortalGapProposal.cluster_key,
                PortalGapProposal.decision,
                PortalGapProposal.decided_at,
                PortalGapProposal.generated_at,
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
            "has_proposal": row.generated_at is not None,
        }
        for row in rows
    }


async def knowledge_gap_clusters(
    db: AsyncSession, *, org_id: int, days: int = 7, include_decided: bool = False, now: datetime | None = None
) -> list[dict[str, Any]]:
    since = (now or datetime.now(tz=UTC)) - timedelta(days=days)
    turns = await load_gap_turns(db, org_id=org_id, since=since)
    return build_clusters(turns, await load_cluster_decisions(db, org_id=org_id), include_decided=include_decided)
