"""LLM-as-judge quality pass over finished webchat conversations (REQ-2).

Covers the three acceptance scenarios of SPEC-CHAT-QUALITY-LOOP-001 §11 REQ-2:
  (a) a conversation with a non-NULL widget_conversations.outcome and no
      existing judgment row is selected, judged through the LiteLLM call and
      UPSERTed into conversation_quality_judgments with every field filled;
  (b) a conversation whose outcome is still NULL is never selected;
  (c) an invalid judge JSON response logs ``conversation_judge_parse_failed``,
      skips that conversation and never crashes the batch — the other
      conversations in the same pass are still judged.

Plus the structural mirrors of widget_outcome: the selection query text
(outcome IS NOT NULL / is_preview = false / LEFT JOIN … cqj.id IS NULL), the
UPSERT shape (ON CONFLICT (conversation_id) DO UPDATE, judged_at=NOW()) and
per-org RLS isolation (every read and write of a tenant runs through that
tenant's scoped session only).

# @MX:SPEC: SPEC-CHAT-QUALITY-LOOP-001 REQ-2
"""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _verdict_raw(**overrides) -> str:
    """A valid judge response: one JSON object matching the SPEC schema."""
    verdict = {
        "outcome": "resolved",
        "failure_category": "none",
        "reasoning": "De helpverlener antwoordde correct op de vraag over retourtermijnen.",
        "confidence": "high",
        "suggested_action": None,
    }
    verdict.update(overrides)
    return json.dumps(verdict, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Signal derivation — the three known signals handed to the judge
# ---------------------------------------------------------------------------


def _turn(role, content="", sources=None, rating=None):
    from app.services.conversation_judge import JudgeTurn

    return JudgeTurn(role=role, content=content, sources=sources, rating=rating)


def test_signals_rating_of_last_assistant_and_refusal_detection():
    """explicit_rating comes from the LAST assistant turn (a rating on an
    earlier turn is withdrawn/overridden); had_citation_refusal is True when
    ANY assistant turn is exactly the fixed helpdesk refusal — the same
    detection set widget_outcome uses, not a re-invented one."""
    from app.services.conversation_judge import _derive_signals
    from app.services.widget_outcome import _SUPPORT_REFERRAL_TEXTS

    refusal = next(iter(_SUPPORT_REFERRAL_TEXTS))
    turns = [
        _turn("user", "retour?"),
        _turn("assistant", "30 dagen", rating="thumbsDown"),
        _turn("user", "en ik?"),
        _turn("assistant", refusal, rating="thumbsUp"),
    ]
    assert _derive_signals(turns, had_handoff=True) == {
        "explicit_rating": "thumbsUp",
        "had_citation_refusal": True,
        "had_handoff": True,
    }

    quiet = [_turn("user", "prijs?"), _turn("assistant", "€10", sources=[{"title": "p"}])]
    assert _derive_signals(quiet, had_handoff=False) == {
        "explicit_rating": None,
        "had_citation_refusal": False,
        "had_handoff": False,
    }


def test_user_prompt_carries_transcript_and_signals_json():
    """User content is JSON-encoded (ensure_ascii=False) with role/content/
    sources per turn plus the three signals, like _build_triage_prompt."""
    from app.services.conversation_judge import _build_user_prompt

    turns = [_turn("user", "waar is Café?"), _turn("assistant", "Bij de bron [1]", sources=[{"n": 1}])]
    payload = json.loads(
        _build_user_prompt(
            turns,
            explicit_rating="thumbsUp",
            had_citation_refusal=False,
            had_handoff=True,
        )
    )
    assert payload["transcript"] == [
        {"role": "user", "content": "waar is Café?", "sources": None},
        {"role": "assistant", "content": "Bij de bron [1]", "sources": [{"n": 1}]},
    ]
    assert payload["signals"] == {
        "explicit_rating": "thumbsUp",
        "had_citation_refusal": False,
        "had_handoff": True,
    }
    # Non-ASCII is embedded literally, not escaped.
    assert "waar is Café?" in _build_user_prompt(
        turns, explicit_rating=None, had_citation_refusal=False, had_handoff=False
    )


# ---------------------------------------------------------------------------
# Test doubles for the loop — same mock-session style as test_widget_outcome
# ---------------------------------------------------------------------------


@dataclass
class _OrgDb:
    """One tenant's RLS-scoped session over widget tables.

    Simulates the selection contract: a conversation is a candidate only when
    its stored ``outcome`` is non-NULL, ``is_preview`` is False and no
    judgment row exists yet (the cqj.id IS NULL join). Records every UPSERT;
    raises if asked to write another org's row.
    """

    org_id: int
    outcome: dict[int, str | None] = field(default_factory=dict)
    preview: set[int] = field(default_factory=set)
    judged: set[int] = field(default_factory=set)
    messages: dict[int, list[tuple]] = field(default_factory=dict)
    handoffs: set[int] = field(default_factory=set)
    inserts: dict[int, dict] = field(default_factory=dict)
    other_org_judged: set[int] = field(default_factory=set)

    def _result(self, rows):
        res = MagicMock()
        res.all.return_value = rows
        res.rowcount = len(rows)
        return res

    async def commit(self):
        pass

    async def execute(self, stmt, params=None, **kwargs):
        sql = str(stmt)
        if "SELECT wc.id" in sql:
            assert params["org_id"] == self.org_id, "candidate SELECT leaked another org's id"
            rows = [
                MagicMock(id=cid)
                for cid in sorted(self.outcome)
                if self.outcome[cid] is not None
                and cid not in self.preview
                and cid not in self.judged
                and cid not in self.other_org_judged
            ]
            return self._result(rows[: params["batch_size"]])
        if "SELECT conversation_id, role, content, sources, rating" in sql:
            ids = params["conv_ids"]
            assert all(i in self.outcome for i in ids), "cross-org message read"
            rows = []
            for cid in ids:
                for role, content, sources, rating in self.messages.get(cid, []):
                    rows.append(
                        MagicMock(conversation_id=cid, role=role, content=content, sources=sources, rating=rating)
                    )
            return self._result(rows)
        if "SELECT DISTINCT conversation_id" in sql:  # handoff
            ids = params["conv_ids"]
            assert all(i in self.outcome for i in ids), "cross-org handoff read"
            return self._result([MagicMock(conversation_id=cid) for cid in ids if cid in self.handoffs])
        if "INSERT INTO conversation_quality_judgments" in sql:
            assert params["org_id"] == self.org_id, "UPSERT leaked another org's id"
            conv_id = params["conversation_id"]
            assert conv_id in self.outcome, f"UPSERT {conv_id} not owned by org {self.org_id}"
            self.inserts[conv_id] = dict(params)
            res = MagicMock()
            res.rowcount = 1
            return res
        raise AssertionError(f"unexpected SQL in tenant session:\n{sql}")


def _cross_org_returning(org_ids: list[int]):
    @asynccontextmanager
    async def _cross():
        db = AsyncMock()

        async def _exec(stmt, params=None, **kwargs):
            assert "conversation_quality_judgments" in str(stmt), "discovery must skip already-judged orgs"
            res = MagicMock()
            res.scalars.return_value.all.return_value = org_ids
            return res

        db.execute = _exec
        yield db

    return _cross


_Q = "app.services.conversation_judge"


# ---------------------------------------------------------------------------
# (a) selected, judged and written with the right fields
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_finished_conversation_is_judged_and_upserted():
    from app.core.config import settings
    from app.services import conversation_judge as cj

    org = _OrgDb(
        org_id=1,
        outcome={100: "resolved"},
        messages={100: [("user", "vraag", None, None), ("assistant", "antwoord", [{"t": "bron"}], "thumbsUp")]},
    )

    @asynccontextmanager
    async def _tenant(org_id):
        assert org_id == 1
        yield org

    captured: dict = {}

    async def _fake_llm(*, model: str, user: str) -> str:
        captured["model"] = model
        captured["user"] = user
        return _verdict_raw()

    with (
        patch.object(cj, "tenant_scoped_session", _tenant),
        patch.object(cj, "cross_org_session", _cross_org_returning([1])),
        patch.object(cj, "_call_judge_llm", _fake_llm),
    ):
        result = await cj._judge_run_once()

    assert result == {"org_count": 1, "judged_count": 1}
    # Model comes from settings; the user prompt carries transcript + signals.
    assert captured["model"] == settings.conversation_judge_model
    prompt_payload = json.loads(captured["user"])
    assert prompt_payload["signals"] == {
        "explicit_rating": "thumbsUp",
        "had_citation_refusal": False,
        "had_handoff": False,
    }
    row = org.inserts[100]
    assert row["conversation_id"] == 100
    assert row["org_id"] == 1
    assert row["channel"] == "webchat"
    assert row["outcome"] == "resolved"
    assert row["failure_category"] == "none"
    assert row["reasoning"].startswith("De helpverlener")
    assert row["confidence"] == "high"
    assert row["suggested_action"] is None
    assert row["model_used"] == settings.conversation_judge_model


@pytest.mark.asyncio
async def test_selection_query_requires_outcome_and_skips_preview_and_judged():
    """The contract of the candidate SELECT: outcome IS NOT NULL (the heuristic
    already labelled it), is_preview = false, and no existing judgment row via
    LEFT JOIN … cqj.id IS NULL."""
    from app.services import conversation_judge as cj

    org = _OrgDb(
        org_id=1,
        outcome={1: "resolved", 2: None, 3: "unknown", 4: "resolved"},
        preview={3},
        other_org_judged={4},  # simulates "cqj row already exists"
        messages={1: [("user", "q", None, None)], 2: [], 3: [], 4: []},
    )

    @asynccontextmanager
    async def _tenant(org_id):
        yield org

    judged_first: list[str] = []

    async def _only_first_may_reach_judge(*, model: str, user: str) -> str:
        judged_first.append(json.loads(user)["transcript"][0]["content"])
        return _verdict_raw()

    with (
        patch.object(cj, "tenant_scoped_session", _tenant),
        patch.object(cj, "_call_judge_llm", _only_first_may_reach_judge),
    ):
        judged = await cj._judge_org(1)

    assert judged == 1
    assert set(org.inserts) == {1}
    assert judged_first == ["q"]  # conversations 2-4 never reached the judge

    # And the SQL text itself carries the three filters.
    captured_sql: list[str] = []

    @asynccontextmanager
    async def _tenant_capturing(org_id):
        db = AsyncMock()

        async def _exec(stmt, params=None, **kwargs):
            captured_sql.append(str(stmt))
            res = MagicMock()
            res.all.return_value = []
            return res

        db.execute = _exec
        yield db

    with patch.object(cj, "tenant_scoped_session", _tenant_capturing):
        await cj._judge_org(1)

    select_sql = captured_sql[0]
    assert "outcome IS NOT NULL" in select_sql
    assert "is_preview = false" in select_sql
    assert "LEFT JOIN conversation_quality_judgments cqj" in select_sql
    assert "cqj.id IS NULL" in select_sql


@pytest.mark.asyncio
async def test_upsert_overwrites_and_never_duplicates():
    """Storage is INSERT … ON CONFLICT (conversation_id) DO UPDATE with
    judged_at=NOW() — idempotent by the uq_conversation_quality_judgments
    _conversation unique key, so a re-judge can never create a second row."""
    from app.services import conversation_judge as cj

    upsert = cj._UPSERT_SQL
    assert "INSERT INTO conversation_quality_judgments" in upsert
    assert "ON CONFLICT (conversation_id) DO UPDATE SET" in upsert
    for field_name in (
        "outcome",
        "failure_category",
        "reasoning",
        "confidence",
        "suggested_action",
        "model_used",
    ):
        assert f"{field_name} = EXCLUDED.{field_name}" in upsert
    assert "judged_at = NOW()" in upsert


# ---------------------------------------------------------------------------
# (b) outcome IS NULL is never selected — enforced by the SELECT contract
# above; here: the org-level session only ever sees its own rows
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_loop_judges_only_the_owning_org():
    from app.services import conversation_judge as cj

    org1 = _OrgDb(org_id=1, outcome={100: "unknown"}, messages={100: [("user", "vraag", None, None)]})
    org2 = _OrgDb(org_id=2, outcome={})  # nothing of org 2 to judge

    dbs = {1: org1, 2: org2}

    @asynccontextmanager
    async def _tenant(org_id):
        yield dbs[org_id]

    async def _fake_llm(*, model: str, user: str) -> str:
        return _verdict_raw(outcome="unresolved", failure_category="retrieval_miss", confidence="low")

    with (
        patch.object(cj, "tenant_scoped_session", _tenant),
        patch.object(cj, "cross_org_session", _cross_org_returning([1, 2])),
        patch.object(cj, "_call_judge_llm", _fake_llm),
    ):
        result = await cj._judge_run_once()

    assert set(org1.inserts) == {100}
    assert org2.inserts == {}
    assert org1.inserts[100]["outcome"] == "unresolved"
    assert org1.inserts[100]["failure_category"] == "retrieval_miss"
    assert result["judged_count"] == 1


# ---------------------------------------------------------------------------
# (c) invalid judge JSON → warning log, conversation skipped, batch continues
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_invalid_judge_json_is_skipped_and_batch_continues():
    from app.services import conversation_judge as cj

    org = _OrgDb(
        org_id=1,
        outcome={100: "resolved", 101: "escalated"},
        messages={
            100: [("user", "q1", None, None), ("assistant", "a1", None, None)],
            101: [("user", "q2", None, None), ("assistant", "a2", None, None)],
        },
    )

    @asynccontextmanager
    async def _tenant(org_id):
        yield org

    async def _llm_returns_garbage_first(*, model: str, user: str) -> str:
        payload = json.loads(user)
        if payload["transcript"][0]["content"] == "q1":
            return "this is definitely not the JSON the schema asked for"
        return _verdict_raw(outcome="escalated", failure_category="none", confidence="medium")

    warn = MagicMock()
    with (
        patch.object(cj, "tenant_scoped_session", _tenant),
        patch.object(cj, "cross_org_session", _cross_org_returning([1])),
        patch.object(cj, "_call_judge_llm", _llm_returns_garbage_first),
        patch.object(cj.logger, "warning", warn),
    ):
        result = await cj._judge_run_once()

    warn.assert_called_once()
    assert warn.call_args.args[0] == "conversation_judge_parse_failed"
    assert warn.call_args.kwargs["conversation_id"] == 100
    assert warn.call_args.kwargs["exc_info"] is True
    # The bad conversation is skipped, the good one in the same batch lands.
    assert set(org.inserts) == {101}
    assert result["judged_count"] == 1


@pytest.mark.asyncio
async def test_schema_mismatch_is_skipped():
    """Valid JSON but an enum value outside the SPEC schema is a schema
    failure: skipped with the same warning, never written."""
    from app.services import conversation_judge as cj

    org = _OrgDb(org_id=1, outcome={100: "resolved"}, messages={100: [("user", "q", None, None)]})

    @asynccontextmanager
    async def _tenant(org_id):
        yield org

    warn = MagicMock()
    with (
        patch.object(cj, "tenant_scoped_session", _tenant),
        patch.object(cj, "cross_org_session", _cross_org_returning([1])),
        patch.object(cj, "_call_judge_llm", AsyncMock(return_value=_verdict_raw(outcome="fixed"))),
        patch.object(cj.logger, "warning", warn),
    ):
        result = await cj._judge_run_once()

    assert warn.call_args.args[0] == "conversation_judge_parse_failed"
    assert org.inserts == {}
    assert result["judged_count"] == 0


# ---------------------------------------------------------------------------
# Loop resilience + verbatim SPEC prompt
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_loop_continues_after_org_failure():
    """conversation_judge_loop does not abort when one pass raises."""
    from app.services.conversation_judge import conversation_judge_loop

    call_count = 0

    async def _raise_then_cancel():
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise RuntimeError("transient DB error")
        raise asyncio.CancelledError

    with (
        patch("app.services.conversation_judge._judge_run_once", side_effect=_raise_then_cancel),
        patch("app.services.conversation_judge.JUDGE_INTERVAL_SECONDS", 0),
        patch("asyncio.sleep", new=AsyncMock()),
    ):
        with pytest.raises(asyncio.CancelledError):
            await conversation_judge_loop()

    assert call_count >= 2, "Loop did not retry after the exception"


def test_system_prompt_matches_spec_verbatim():
    """REQ-2 fixes the judge prompt: it must equal the fenced block in
    docs/specs/SPEC-CHAT-QUALITY-LOOP-001/spec.md byte for byte."""
    import re
    from pathlib import Path

    from app.services.conversation_judge import JUDGE_SYSTEM_PROMPT

    spec = Path(__file__).resolve().parents[3] / "docs" / "specs" / "SPEC-CHAT-QUALITY-LOOP-001" / "spec.md"
    text = spec.read_text()
    section = text.split("## REQ-2", 1)[1].split("## REQ-3", 1)[0]
    block = re.search(r"System:\n```\n(.*?)\n```", section, re.DOTALL).group(1)
    assert JUDGE_SYSTEM_PROMPT == block


@pytest.mark.asyncio
async def test_litellm_call_uses_triage_pattern():
    """Same endpoint, auth header, trace headers, temperature 0.1 and payload
    shape as triage._call_triage_llm — verified against a mocked httpx client
    (no real network in tests)."""
    from app.core.config import settings
    from app.services import conversation_judge as cj

    posted: dict = {}

    class _Client:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, url, headers=None, json=None):
            posted["url"] = url
            posted["headers"] = headers
            posted["json"] = json
            resp = MagicMock()
            resp.raise_for_status = MagicMock()
            resp.json.return_value = {"choices": [{"message": {"content": "ok"}}]}
            return resp

    with (
        patch("httpx.AsyncClient", _Client),
        patch.object(cj, "get_trace_headers", lambda: {"x-trace": "t"}),
    ):
        out = await cj._call_judge_llm(model="klai-medium", user='{"transcript": []}')

    assert out == "ok"
    assert posted["url"] == f"{settings.litellm_base_url}/v1/chat/completions"
    assert posted["headers"]["Authorization"] == f"Bearer {settings.litellm_master_key}"
    assert posted["headers"]["x-trace"] == "t"
    body = posted["json"]
    assert body["model"] == "klai-medium"
    assert body["temperature"] == 0.1
    assert body["messages"][0] == {"role": "system", "content": cj.JUDGE_SYSTEM_PROMPT}
    assert body["messages"][1] == {"role": "user", "content": '{"transcript": []}'}
