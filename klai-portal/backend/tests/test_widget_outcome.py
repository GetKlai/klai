"""Widget conversation outcome labelling.

Covers:
  AC-O.1 — derive_outcome rules: one test per outcome value
           (escalated / abandoned / resolved / unknown) and the
           precedence edges (handoff, support-refusal, broad-mode answer,
           thumbsUp vs one-turn).
  AC-O.2 — the background loop is tenant-scoped: a conversation in org 1
           is never labelled through org 2's RLS session, and a cross-tenant
           candidate row is never written by another org's pass.

# @MX:NOTE: Pure-function rules are tested directly; the loop is tested with
#           fake DB sessions that record every SQL statement and its params.
# @MX:SPEC: widget-conversation-outcome
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _turn(role, content="", rating=None):
    from app.services.widget_outcome import ConversationTurn

    return ConversationTurn(role=role, content=content, rating=rating)


# English + Dutch canned helpdesk refusal strings the chat actually stores as
# the assistant content when it cannot answer. Imported from the service so a
# wording change is reflected in the tests, not silently broken.
from app.services.widget_outcome import (  # noqa: E402
    _SUPPORT_REFERRAL_TEXTS,
    derive_outcome,
)

_REFUSAL = next(iter(_SUPPORT_REFERRAL_TEXTS))


# ---------------------------------------------------------------------------
# AC-O.1 — escalated
# ---------------------------------------------------------------------------


def test_derive_escalated_when_handoff_attached():
    """A live handoff session escalates, even though the chat ends on an answer."""
    turns = [_turn("user", "factuur fout"), _turn("assistant", "Ik help je verder.")]
    assert derive_outcome(turns, has_handoff=True) == "escalated"


def test_derive_escalated_when_last_answer_refers_to_support():
    """The canned helpdesk refusal (no citable sources) counts as escalation."""
    turns = [_turn("user", "hoe vraag ik teruggave aan"), _turn("assistant", _REFUSAL)]
    assert derive_outcome(turns, has_handoff=False) == "escalated"


# ---------------------------------------------------------------------------
# AC-O.1 — abandoned
# ---------------------------------------------------------------------------


def test_derive_escalated_on_broad_mode_answer_even_with_thumbs_up():
    """A consented general-knowledge answer means the help articles did NOT
    answer — that is a knowledge gap. The stored marker label escalates it,
    before the rating rule, so a friendly 👍 cannot turn it into 'resolved'."""
    from klai_chat_prompts import broad_mode_answer_marker

    answer = f"{broad_mode_answer_marker('wat is een sip trunk')}\n\nEen SIP trunk is een virtuele lijn."
    turns = [
        _turn("user", "wat is een sip trunk"),
        _turn("assistant", answer, rating="thumbsUp"),
    ]
    assert derive_outcome(turns, has_handoff=False) == "escalated"


def test_broad_marker_mid_text_does_not_escalate():
    """Only a LEADING label marks the answer as broad. An ordinary grounded
    answer that happens to quote the marker phrase is not escalated; without an
    explicit signal it stays 'unknown'."""
    turns = [
        _turn("user", "hoe werkt de bot"),
        _turn("assistant", "De bot zoekt in helpartikelen."),
        _turn("user", "citaten?"),
        _turn("assistant", 'Het artikel zegt: "Algemene kennis — niet afkomstig uit onze helpartikelen."'),
    ]
    assert derive_outcome(turns, has_handoff=False, quiet_period_elapsed=True) == "unknown"


def test_broad_answer_followed_by_grounded_answer_is_resolved():
    """The rule reads the LAST assistant answer: if the strict bot answered
    from the articles afterwards, the earlier broad fallback doesn't pin the
    conversation to escalated. Without a thumbs-up it is 'unknown', not
    'resolved' — silence is not consent."""
    from klai_chat_prompts import broad_mode_answer_marker

    broad = f"{broad_mode_answer_marker('hoe lang duurt portering')}\n\nIn NL meestal 1 werkdag."
    turns = [
        _turn("user", "hoe lang duurt portering"),
        _turn("assistant", broad),
        _turn("user", "staat dat ook in jullie artikelen?"),
        _turn("assistant", "Ja: according to [1] up to 5 business days."),
    ]
    assert derive_outcome(turns, has_handoff=False, quiet_period_elapsed=True) == "unknown"


def test_derive_abandoned_when_ends_on_unanswered_question():
    """Conversation ends on a visitor message with no assistant answer after."""
    turns = [
        _turn("user", "beste prijs?"),
        _turn("assistant", "Wij zijn de goedkoopste."),
        _turn("user", "en de tweede beste?"),
    ]
    assert derive_outcome(turns, has_handoff=False, quiet_period_elapsed=True) == "abandoned"


def test_single_exchange_without_signal_is_unknown():
    """One question, one answer, then silence. That is the SHAPE OF A GOOD
    helpdesk answer as much as of a bounce — the visitor got what they came
    for and left. Reading it as 'abandoned' made the label track turn count
    instead of outcome, so it is 'unknown' until a judge or a thumb says
    otherwise."""
    turns = [_turn("user", "openingstijden"), _turn("assistant", "ma-vr 9-17h")]
    assert derive_outcome(turns, has_handoff=False, quiet_period_elapsed=True) == "unknown"


# ---------------------------------------------------------------------------
# AC-O.1 — resolved
# ---------------------------------------------------------------------------


def test_derive_resolved_positive_rating():
    """Ends on an answer the visitor rated up."""
    turns = [_turn("user", "retour termijn?"), _turn("assistant", "30 dagen", rating="thumbsUp")]
    assert derive_outcome(turns, has_handoff=False, quiet_period_elapsed=True) == "resolved"


def test_multi_turn_ending_on_answer_is_unknown():
    """Ending on an answer proves nothing on its own: it is equally the shape
    of a visitor who gave up after three poor replies. Only an explicit signal
    promotes a conversation to 'resolved'."""
    turns = [
        _turn("user", "bezorgkosten"),
        _turn("assistant", "€4,95"),
        _turn("user", "gratis vanaf?"),
        _turn("assistant", "vanaf €50"),
    ]
    assert derive_outcome(turns, has_handoff=False, quiet_period_elapsed=True) == "unknown"


def test_derive_thumbs_up_beats_single_turn_abandonment():
    """One exchange, but rated up — explicit satisfaction wins over the bounce rule."""
    turns = [_turn("user", "x"), _turn("assistant", "y", rating="thumbsUp")]
    assert derive_outcome(turns, has_handoff=False, quiet_period_elapsed=True) == "resolved"


# ---------------------------------------------------------------------------
# AC-O.1 — unknown
# ---------------------------------------------------------------------------


def test_derive_unknown_when_no_turns():
    """No stored turns (retention purged the messages) = unknown."""
    assert derive_outcome([], has_handoff=False, quiet_period_elapsed=True) == "unknown"


def test_derive_unknown_while_window_not_elapsed():
    """Ends on an answer but the visitor may still reply — stay unknown."""
    turns = [_turn("user", "a"), _turn("assistant", "b"), _turn("user", "c"), _turn("assistant", "d")]
    assert derive_outcome(turns, has_handoff=False, quiet_period_elapsed=False) == "unknown"


# ---------------------------------------------------------------------------
# Test doubles for the loop
# ---------------------------------------------------------------------------


@dataclass
class _OrgDb:
    """One tenant's RLS-scoped session. Records UPDATEs; raises if asked for
    another org's conversation, so a cross-tenant write is impossible to hide.
    """

    org_id: int
    candidates: list[tuple[int, datetime]] = field(default_factory=list)
    messages: dict[int, list[tuple]] = field(default_factory=dict)
    handoffs: set[int] = field(default_factory=set)
    updates: dict[int, str] = field(default_factory=dict)

    def _result(self, rows):
        res = MagicMock()
        res.all.return_value = rows
        res.rowcount = len(rows)
        return res

    async def commit(self):
        pass

    async def execute(self, stmt, params=None, **kwargs):
        sql = str(stmt)
        if "SELECT id, last_message_at" in sql:
            assert params["org_id"] == self.org_id, "candidate SELECT leaked another org's id"
            return self._result([MagicMock(id=c, last_message_at=t) for c, t in self.candidates])
        if "SELECT conversation_id, role, content, rating" in sql:
            ids = params["conv_ids"]
            assert all(i in {c for c, _ in self.candidates} for i in ids), "cross-org message read"
            rows = []
            for cid in ids:
                for role, content, rating in self.messages.get(cid, []):
                    rows.append(MagicMock(conversation_id=cid, role=role, content=content, rating=rating))
            return self._result(rows)
        if "SELECT DISTINCT conversation_id" in sql:  # handoff
            ids = params["conv_ids"]
            assert all(i in {c for c, _ in self.candidates} for i in ids), "cross-org handoff read"
            rows = [MagicMock(conversation_id=cid) for cid in ids if cid in self.handoffs]
            return self._result(rows)
        if "UPDATE widget_conversations" in sql:
            assert params["org_id"] == self.org_id, "UPDATE leaked another org's id"
            conv_id = params["conv_id"]
            assert conv_id in {c for c, _ in self.candidates}, f"UPDATE {conv_id} not owned by org {self.org_id}"
            self.updates[conv_id] = params["outcome"]
            res = MagicMock()
            res.rowcount = 1
            return res
        res = MagicMock()
        res.all.return_value = []
        return res


def _turn_dt(mins_ago=60):
    return datetime.now(UTC) - timedelta(minutes=mins_ago)


# ---------------------------------------------------------------------------
# AC-O.2 — cross-org isolation in the background loop
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_loop_labels_only_the_owning_org():
    """A conversation that exists under org 1 is labelled in org 1's session;
    org 2's pass runs on a separate tenant-scoped session and cannot touch it.
    """
    from app.services import widget_outcome as wo

    org1 = _OrgDb(
        org_id=1,
        candidates=[(100, _turn_dt(120))],
        messages={100: [("user", "vraag", None), ("assistant", "antwoord", None)]},
    )
    org2 = _OrgDb(org_id=2, candidates=[], messages={})  # nothing of org 2

    dbs = {1: org1, 2: org2}

    @asynccontextmanager
    async def _tenant(org_id):
        yield dbs[org_id]

    # Discovery (cross_org_session) returns both orgs — that's the whole point:
    # the cross-org pass only lists which tenants have work; each tenant's own
    # session does the reads+writes.
    @asynccontextmanager
    async def _cross():
        db = AsyncMock()

        async def _exec(stmt, params=None, **kwargs):
            res = MagicMock()
            res.scalars.return_value.all.return_value = [1, 2]
            return res

        db.execute = _exec
        yield db

    with (
        patch.object(wo, "tenant_scoped_session", _tenant),
        patch.object(wo, "cross_org_session", _cross),
    ):
        result = await wo._outcome_run_once()

    # org 1's conversation got a label via org 1's session only.
    assert org1.updates == {100: "unknown"}  # single exchange, no explicit signal
    assert org2.updates == {}
    assert result["labelled_count"] == 1


@pytest.mark.asyncio
async def test_label_org_scopes_every_statement_to_its_org():
    """_label_org never issues a read or write with a foreign org_id."""
    from app.services.widget_outcome import _label_org

    org = _OrgDb(
        org_id=7,
        candidates=[(700, _turn_dt(120))],
        messages={700: [("user", "q", None), ("assistant", "a", "thumbsUp")]},
    )

    @asynccontextmanager
    async def _tenant(org_id):
        assert org_id == 7
        yield org

    with patch("app.services.widget_outcome.tenant_scoped_session", _tenant):
        n = await _label_org(7, _turn_dt(120))

    assert n == 1
    assert org.updates == {700: "resolved"}


# ---------------------------------------------------------------------------
# AC-O.3 — stats endpoint exposes the outcome distribution
# ---------------------------------------------------------------------------


class _ScriptedResult:
    """Minimal asyncio-friendly result for the scripted stats queries."""

    def __init__(self, *, rows=None, first_row=None, scalar_row=None):
        self._rows = rows or []
        self._first_row = first_row
        self._scalar_row = scalar_row

    def all(self):
        return self._rows

    def first(self):
        return self._first_row

    def scalars(self):
        m = MagicMock()
        m.all.return_value = self._rows
        return m

    def scalar_one_or_none(self):
        return self._scalar_row


@pytest.mark.asyncio
async def test_stats_endpoint_reports_outcome_distribution():
    """GET /stats gains outcome_counts without touching the existing fields,
    and the outcome query honours the period window + is_preview filter.
    """
    from types import SimpleNamespace

    from conftest import make_perms

    from app.api.admin_widgets import widget_activity_stats

    widget = SimpleNamespace(id="widget-uuid-1", org_id=101)
    results = [
        _ScriptedResult(scalar_row=widget),  # _get_widget_or_404
        _ScriptedResult(first_row=SimpleNamespace(total_conversations=10, total_messages=25)),  # totals
        _ScriptedResult(rows=[]),  # top queries
        _ScriptedResult(rows=[]),  # hourly
        _ScriptedResult(
            rows=[
                SimpleNamespace(outcome="resolved", c=4),
                SimpleNamespace(outcome="escalated", c=2),
                SimpleNamespace(outcome="abandoned", c=1),
                SimpleNamespace(outcome="unknown", c=1),
                SimpleNamespace(outcome=None, c=2),
            ]
        ),  # outcome distribution
    ]

    captured: list[tuple[str, dict]] = []

    async def _execute(stmt, params=None, **kwargs):
        captured.append((str(stmt), dict(params or {})))
        return results[min(len(captured) - 1, len(results) - 1)]

    db = AsyncMock()
    db.execute = _execute

    stats = await widget_activity_stats("w-1", period="7d", perms=make_perms(), db=db)

    assert stats.outcome_counts.resolved == 4
    assert stats.outcome_counts.escalated == 2
    assert stats.outcome_counts.abandoned == 1
    assert stats.outcome_counts.unknown == 1
    assert stats.outcome_counts.unlabeled == 2
    # Existing fields unchanged.
    assert stats.total_conversations == 10
    assert stats.total_messages == 25
    assert stats.avg_messages_per_conversation == 2.5
    assert stats.top_queries == []
    assert stats.hourly_activity == [0] * 24

    outcome_sql, outcome_params = captured[-1]
    assert "GROUP BY outcome" in outcome_sql
    assert "is_preview = false" in outcome_sql
    assert "started_at >= :cutoff" in outcome_sql
    assert "cutoff" in outcome_params


@pytest.mark.asyncio
async def test_loop_continues_after_org_failure():
    """widget_outcome_loop does not abort when one tenant's pass raises."""
    from app.services.widget_outcome import widget_outcome_loop

    call_count = 0

    async def _raise_then_cancel():
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise RuntimeError("transient DB error")
        raise asyncio.CancelledError

    with (
        patch("app.services.widget_outcome._outcome_run_once", side_effect=_raise_then_cancel),
        patch("app.services.widget_outcome.OUTCOME_INTERVAL_SECONDS", 0),
        patch("asyncio.sleep", new=AsyncMock()),
    ):
        with pytest.raises(asyncio.CancelledError):
            await widget_outcome_loop()

    assert call_count >= 2, "Loop did not retry after the exception"
