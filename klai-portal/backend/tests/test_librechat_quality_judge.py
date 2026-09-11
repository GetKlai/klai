"""LLM-as-judge quality pass over LibreChat conversations (SPEC-CHAT-QUALITY-LOOP-001 REQ-5).

Covers the acceptance scenarios of §11 REQ-5:
  (a) an org without the ``librechat_quality_judge`` platform unlock is never
      selected and no Mongo client is ever constructed;
  (b) an org with the unlock: a LibreChat conversation with at least one user
      and one assistant message is judged through the LiteLLM call and
      UPSERTed with channel='librechat', external_conversation_id filled and
      no conversation_id (NULL);
  (c) a conversation whose external id already has a judgment row is filtered
      out before the LLM is called — a second cycle neither duplicates nor
      re-judges;
  (d) ``isCreatedByUser`` drives the turn role, ``feedback.rating`` of the
      last assistant message becomes ``explicit_rating``, an assistant
      ``error: true`` becomes ``had_error``;
  (e) an invalid judge response logs and skips only that conversation, the
      rest of the batch still lands.

Mock style mirrors tests/test_conversation_judge.py: fake DB sessions that
simulate the SQL contract, a fake pymongo.MongoClient patched in THIS
module's namespace, no real network, no real Mongo.

# @MX:SPEC: SPEC-CHAT-QUALITY-LOOP-001 REQ-5
"""

from __future__ import annotations

import asyncio
import datetime as dt
import json
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

_CREATED = dt.datetime(2026, 9, 11, 10, 0, 0)


def _verdict_raw(**overrides) -> str:
    """A valid judge response: one JSON object matching the SPEC schema."""
    verdict = {
        "outcome": "resolved",
        "failure_category": "none",
        "reasoning": "De kennisassistent antwoordde correct op de vraag.",
        "confidence": "high",
        "suggested_action": None,
    }
    verdict.update(overrides)
    return json.dumps(verdict, ensure_ascii=False)


def _conv(conversation_id: str, *, minutes: int = 0) -> dict:
    return {"conversationId": conversation_id, "updatedAt": _CREATED + dt.timedelta(minutes=minutes)}


def _msg(
    conversation_id: str,
    *,
    user: bool,
    minutes: int,
    text: str | None = None,
    content=None,
    feedback: dict | None = None,
    error: bool | None = None,
    unfinished: bool | None = None,
) -> dict:
    doc = {
        "conversationId": conversation_id,
        "isCreatedByUser": user,
        "createdAt": _CREATED + dt.timedelta(minutes=minutes),
    }
    if text is not None:
        doc["text"] = text
    if content is not None:
        doc["content"] = content
    if feedback is not None:
        doc["feedback"] = feedback
    if error is not None:
        doc["error"] = error
    if unfinished is not None:
        doc["unfinished"] = unfinished
    return doc


# ---------------------------------------------------------------------------
# Fake pymongo — patched as app.services.librechat_quality_judge.pymongo.MongoClient
# ---------------------------------------------------------------------------


class _Cursor:
    def __init__(self, docs):
        self._docs = list(docs)

    def sort(self, key, direction):
        self._docs.sort(key=lambda d: d.get(key), reverse=direction < 0)
        return self

    def limit(self, n):
        self._docs = self._docs[:n]
        return self

    def __iter__(self):
        return iter(self._docs)


class _FakeMongo:
    """Stands in for pymongo.MongoClient: records connect kwargs and find
    calls, serves fixed per-database conversations/messages documents."""

    def __init__(self, dbs: dict[str, dict[str, list[dict]]] | None = None):
        self.dbs = dbs or {}
        self.connect_kwargs: list[dict] = []
        self.finds: list[tuple[str, str, dict]] = []

    def client(self, **kwargs):
        self.connect_kwargs.append(kwargs)
        return _FakeClient(self)


class _FakeClient:
    def __init__(self, mongo: _FakeMongo):
        self._mongo = mongo

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def __getitem__(self, db_name: str):
        return _FakeDb(self._mongo, db_name)


class _FakeDb:
    def __init__(self, mongo: _FakeMongo, db_name: str):
        self._mongo = mongo
        self._db_name = db_name
        docs = mongo.dbs.get(db_name, {})
        self.conversations = _FakeCollection(mongo, db_name, "conversations", docs.get("conversations", []))
        self.messages = _FakeCollection(mongo, db_name, "messages", docs.get("messages", []))


class _FakeCollection:
    def __init__(self, mongo: _FakeMongo, db_name: str, name: str, docs: list[dict]):
        self._mongo = mongo
        self._db_name = db_name
        self._name = name
        self._docs = docs

    def find(self, flt=None, projection=None):
        self._mongo.finds.append((self._db_name, self._name, flt or {}))
        docs = self._docs
        cid_filter = (flt or {}).get("conversationId")
        if isinstance(cid_filter, dict) and "$in" in cid_filter:
            wanted = set(cid_filter["$in"])
            docs = [d for d in docs if d.get("conversationId") in wanted]
        return _Cursor(docs)


# ---------------------------------------------------------------------------
# Fake DB sessions — same mock-session style as test_conversation_judge
# ---------------------------------------------------------------------------


@dataclass
class _OrgDb:
    """One tenant's RLS-scoped session over the judgment table.

    Simulates the exclude query: rows listed in ``judged_external`` already
    have a judgment row for this org and channel. Records every UPSERT;
    raises if asked to read or write another org's rows.
    """

    org_id: int
    judged_external: set[str] = field(default_factory=set)
    inserts: dict[str, dict] = field(default_factory=dict)
    sql_log: list[str] = field(default_factory=list)

    def _result(self, rows):
        res = MagicMock()
        res.all.return_value = rows
        res.rowcount = len(rows)
        return res

    async def commit(self):
        pass

    async def execute(self, stmt, params: dict | None = None, **kwargs):
        sql = str(stmt)
        self.sql_log.append(sql)
        assert params is not None, "every query in this fake session expects params"
        if "SELECT external_conversation_id" in sql:
            assert "channel = 'librechat'" in sql, "exclude query must scope to the librechat channel"
            assert params["org_id"] == self.org_id, "exclude SELECT leaked another org's id"
            rows = [MagicMock(external_conversation_id=e) for e in sorted(self.judged_external)]
            return self._result(rows)
        if "INSERT INTO conversation_quality_judgments" in sql:
            assert "'librechat'" in sql, "UPSERT must pin channel='librechat'"
            assert params["org_id"] == self.org_id, "UPSERT leaked another org's id"
            self.inserts[params["external_conversation_id"]] = dict(params)
            res = MagicMock()
            res.rowcount = 1
            return res
        raise AssertionError(f"unexpected SQL in tenant session:\n{sql}")


def _cross_org_returning(orgs: list[tuple[int, str, list[str]]]):
    """Discovery session over portal_orgs. Simulates the WHERE clause: only
    orgs whose platform_unlocked_features contain the requested feature are
    returned, so a missing flag must cost zero Mongo connections downstream."""

    @asynccontextmanager
    async def _cross():
        db = AsyncMock()

        async def _exec(stmt, params: dict | None = None, **kwargs):
            sql = str(stmt)
            assert "portal_orgs" in sql, "discovery must read portal_orgs"
            assert "platform_unlocked_features" in sql, "discovery must filter on the platform unlock"
            assert params is not None
            feature = params["feature"]
            res = MagicMock()
            res.all.return_value = [
                MagicMock(id=org_id, slug=slug) for org_id, slug, features in orgs if feature in features
            ]
            return res

        db.execute = _exec
        yield db

    return _cross


def _tenant_returning(org: _OrgDb):
    @asynccontextmanager
    async def _tenant(org_id):
        assert org_id == org.org_id
        yield org

    return _tenant


_LJ = "app.services.librechat_quality_judge"


# ---------------------------------------------------------------------------
# (a) org without the feature flag: skipped, zero Mongo connections
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_org_without_feature_flag_makes_no_mongo_call():
    from app.core.extensions_registry import KNOWN_FEATURES
    from app.services import librechat_quality_judge as lj

    mongo = _FakeMongo()
    org = _OrgDb(org_id=1)

    with (
        patch(f"{_LJ}.pymongo.MongoClient", mongo.client),
        patch.object(lj, "cross_org_session", _cross_org_returning([(1, "voys", ["widgets", "partner_api"])])),
        patch.object(lj, "tenant_scoped_session", _tenant_returning(org)),
    ):
        result = await lj.librechat_judge_run_once()

    assert result == {"org_count": 0, "judged_count": 0}
    assert mongo.connect_kwargs == [], "no-flag org must never open a Mongo connection"
    assert mongo.finds == []
    # The flag name the pass queries on is a real platform feature.
    assert "librechat_quality_judge" in KNOWN_FEATURES


# ---------------------------------------------------------------------------
# (b) org with the flag: conversation judged and stored on the librechat channel
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unlocked_org_conversation_is_judged_and_stored():
    from app.core.config import settings
    from app.core.provisioning_names import provisioning_names_for_slug
    from app.services import librechat_quality_judge as lj

    db_name = provisioning_names_for_slug("voys", domain=settings.domain).mongodb_database
    mongo = _FakeMongo(
        {
            db_name: {
                "conversations": [_conv("c-new", minutes=5)],
                "messages": [
                    _msg("c-new", user=True, minutes=0, text="hoe regel ik een vergoeding?"),
                    _msg(
                        "c-new",
                        user=False,
                        minutes=1,
                        text="Dat kan via het portaal [1].",
                        feedback={"rating": "thumbsUp"},
                    ),
                ],
            }
        }
    )
    org = _OrgDb(org_id=7)

    captured: dict = {}

    async def _fake_llm(*, model: str, user: str, system: str) -> str:
        captured["model"] = model
        captured["user"] = user
        captured["system"] = system
        return _verdict_raw()

    with (
        patch(f"{_LJ}.pymongo.MongoClient", mongo.client),
        patch.object(lj, "cross_org_session", _cross_org_returning([(7, "voys", ["librechat_quality_judge"])])),
        patch.object(lj, "tenant_scoped_session", _tenant_returning(org)),
        patch.object(lj, "_call_judge_llm", _fake_llm),
    ):
        result = await lj.librechat_judge_run_once()

    assert result == {"org_count": 1, "judged_count": 1}
    # Connection mirrors librechat_chat_context.py's constructor exactly.
    assert mongo.connect_kwargs and mongo.connect_kwargs[0] == {
        "host": settings.mongodb_container_name,
        "port": 27017,
        "username": settings.mongo_root_username,
        "password": settings.mongo_root_password,
        "authSource": "admin",
        "serverSelectionTimeoutMS": 2000,
        "connectTimeoutMS": 2000,
        "socketTimeoutMS": 2000,
    }
    # The second (internal-audience) rubric is what reaches the model.
    assert captured["system"] == lj.LIBRECHAT_JUDGE_SYSTEM_PROMPT
    assert captured["model"] == settings.conversation_judge_model
    payload = json.loads(captured["user"])
    assert payload["signals"] == {"explicit_rating": "thumbsUp", "had_error": False}

    row = org.inserts["c-new"]
    assert row["org_id"] == 7
    assert row["external_conversation_id"] == "c-new"
    # conversation_id stays NULL for this channel: the UPSERT never binds it.
    assert "conversation_id" not in row
    assert row["outcome"] == "resolved"
    assert row["confidence"] == "high"
    assert row["model_used"] == settings.conversation_judge_model


# ---------------------------------------------------------------------------
# (c) already-judged conversation: excluded before the LLM, no duplicate
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_already_judged_conversation_is_not_rejudged():
    from app.services import librechat_quality_judge as lj

    mongo = _FakeMongo(
        {
            "librechat-voys": {
                "conversations": [_conv("c-old", minutes=5)],
                "messages": [_msg("c-old", user=True, minutes=0, text="vraag")],
            }
        }
    )
    org = _OrgDb(org_id=7, judged_external={"c-old"})
    llm = AsyncMock()

    with (
        patch(f"{_LJ}.pymongo.MongoClient", mongo.client),
        patch.object(lj, "cross_org_session", _cross_org_returning([(7, "voys", ["librechat_quality_judge"])])),
        patch.object(lj, "tenant_scoped_session", _tenant_returning(org)),
        patch.object(lj, "_call_judge_llm", llm),
    ):
        result = await lj.librechat_judge_run_once()

    llm.assert_not_awaited()
    assert org.inserts == {}
    assert result == {"org_count": 1, "judged_count": 0}


# ---------------------------------------------------------------------------
# (d) isCreatedByUser → role, feedback.rating → explicit_rating, error → had_error
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_roles_and_signals_come_from_the_real_librechat_fields():
    from app.services import librechat_quality_judge as lj

    mongo = _FakeMongo(
        {
            "librechat-voys": {
                "conversations": [_conv("c1", minutes=5)],
                "messages": [
                    _msg("c1", user=True, minutes=0, text="vraag één"),
                    _msg("c1", user=False, minutes=1, text="antwoord met fout", error=True),
                    _msg("c1", user=True, minutes=2, text=""),  # no text, no content → dropped
                    _msg("c1", user=False, minutes=3, content=[{"type": "text", "text": "via content"}]),
                    _msg("c1", user=True, minutes=4, text="opvolger", feedback={"rating": "thumbsDown"}),
                    _msg("c1", user=False, minutes=5, text="laatste antwoord", feedback={"rating": "thumbsDown"}),
                ],
            }
        }
    )
    org = _OrgDb(org_id=7)

    captured: dict = {}

    async def _fake_llm(*, model: str, user: str, system: str) -> str:
        captured["user"] = user
        return _verdict_raw(outcome="unresolved", failure_category="generation_error", confidence="medium")

    with (
        patch(f"{_LJ}.pymongo.MongoClient", mongo.client),
        patch.object(lj, "cross_org_session", _cross_org_returning([(7, "voys", ["librechat_quality_judge"])])),
        patch.object(lj, "tenant_scoped_session", _tenant_returning(org)),
        patch.object(lj, "_call_judge_llm", _fake_llm),
    ):
        await lj.librechat_judge_run_once()

    payload = json.loads(captured["user"])
    assert [t["role"] for t in payload["transcript"]] == ["user", "assistant", "assistant", "user", "assistant"]
    assert payload["transcript"][2]["content"] == "via content"  # text empty → content parts flattened
    # empty user message never reached the transcript as an empty turn
    assert all(t["content"].strip() for t in payload["transcript"])
    # explicit_rating = feedback of the LAST assistant message; had_error from
    # any assistant error:true. The user-message feedback above must not count.
    assert payload["signals"] == {"explicit_rating": "thumbsDown", "had_error": True}
    assert org.inserts["c1"]["outcome"] == "unresolved"


# ---------------------------------------------------------------------------
# (e) invalid judge JSON: conversation skipped, batch continues
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_invalid_judge_json_is_skipped_and_batch_continues():
    from app.services import librechat_quality_judge as lj

    mongo = _FakeMongo(
        {
            "librechat-voys": {
                "conversations": [_conv("c1", minutes=5), _conv("c2", minutes=6)],
                "messages": [
                    _msg("c1", user=True, minutes=0, text="q1"),
                    _msg("c1", user=False, minutes=1, text="a1"),
                    _msg("c2", user=True, minutes=2, text="q2"),
                    _msg("c2", user=False, minutes=3, text="a2"),
                ],
            }
        }
    )
    org = _OrgDb(org_id=7)

    async def _llm_returns_garbage_for_c1(*, model: str, user: str, system: str) -> str:
        payload = json.loads(user)
        if payload["transcript"][0]["content"] == "q1":
            return "this is definitely not the JSON the schema asked for"
        return _verdict_raw(outcome="escalated", failure_category="none", confidence="medium")

    warn = MagicMock()
    with (
        patch(f"{_LJ}.pymongo.MongoClient", mongo.client),
        patch.object(lj, "cross_org_session", _cross_org_returning([(7, "voys", ["librechat_quality_judge"])])),
        patch.object(lj, "tenant_scoped_session", _tenant_returning(org)),
        patch.object(lj, "_call_judge_llm", _llm_returns_garbage_for_c1),
        patch.object(lj.logger, "warning", warn),
    ):
        result = await lj.librechat_judge_run_once()

    warn.assert_called_once()
    assert warn.call_args.args[0] == "librechat_judge_parse_failed"
    assert warn.call_args.kwargs["conversation_id"] == "c1"
    assert warn.call_args.kwargs["exc_info"] is True
    assert set(org.inserts) == {"c2"}
    assert result["judged_count"] == 1


# ---------------------------------------------------------------------------
# Structural: UPSERT shape, verbatim SPEC rubric, loop wiring
# ---------------------------------------------------------------------------


def test_upsert_overwrites_by_external_id_and_never_duplicates():
    """Storage is INSERT … ON CONFLICT (external_conversation_id) DO UPDATE
    with channel pinned to 'librechat' — the separate unique key from
    migration d3c8b6a5f1e0, so a re-judge can never create a second row."""
    from app.services import librechat_quality_judge as lj

    upsert = lj._UPSERT_SQL
    assert "INSERT INTO conversation_quality_judgments" in upsert
    assert "ON CONFLICT (external_conversation_id) DO UPDATE SET" in upsert
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
    # conversation_id is not written at all — it stays NULL for this channel.
    assert "conversation_id" not in upsert.replace("external_conversation_id", "")


def test_system_prompt_matches_spec_verbatim():
    """REQ-5 fixes the second rubric: it must equal the fenced System block in
    docs/specs/SPEC-CHAT-QUALITY-LOOP-001/spec.md byte for byte."""
    import re
    from pathlib import Path

    from app.services.librechat_quality_judge import LIBRECHAT_JUDGE_SYSTEM_PROMPT

    spec = Path(__file__).resolve().parents[3] / "docs" / "specs" / "SPEC-CHAT-QUALITY-LOOP-001" / "spec.md"
    text = spec.read_text()
    section = text.split("## REQ-5", 1)[1].split("## REQ-4", 1)[0]
    match = re.search(r"System \(LETTERLIJK[^)]*\):\n```\n(.*?)\n```", section, re.DOTALL)
    assert match is not None, "spec.md is missing the REQ-5 verbatim System block"
    assert LIBRECHAT_JUDGE_SYSTEM_PROMPT == match.group(1)


@pytest.mark.asyncio
async def test_loop_runs_both_channel_passes_independently():
    """conversation_judge_loop runs the LibreChat pass after the webchat pass
    every cycle; a failure on either channel is logged and never stops the
    other channel's pass in the same or a later cycle."""
    from app.services import conversation_judge as cj
    from app.services import librechat_quality_judge as lj

    # Cycle 1: webchat pass fails → LibreChat pass must still run, and fails.
    # Cycle 2: webchat pass runs again → LibreChat raises CancelledError to end the loop.
    webchat = AsyncMock(side_effect=[RuntimeError("webchat DB hiccup"), {}])
    librechat = AsyncMock(side_effect=[RuntimeError("mongo down"), asyncio.CancelledError()])

    with (
        patch.object(cj, "_judge_run_once", webchat),
        patch.object(lj, "librechat_judge_run_once", librechat),
        patch.object(cj, "_within_judge_window", return_value=True),
        patch("asyncio.sleep", new=AsyncMock()),
    ):
        with pytest.raises(asyncio.CancelledError):
            await cj.conversation_judge_loop()

    assert webchat.await_count == 2, "LibreChat failure stopped the webchat pass"
    assert librechat.await_count == 2, "webchat failure stopped the LibreChat pass"
