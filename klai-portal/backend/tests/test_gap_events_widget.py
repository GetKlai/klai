"""KB-014 gap-event registration on the widget / partner chatpad path.

`retrieve_context` must record knowledge gaps through the shared in-process
service (app.services.gap_events.record_gap_event) — no HTTP loopback into
portal-api's own /internal/v1/gap-events — inheriting the
SPEC-PRIVACY-QUERY-SHADOW-001 REQ-8 telemetry gating and RLS tenant
scoping, while a failing write never blocks or fails the chat answer.

Unit-test style mirrors test_partner_chat.py (httpx.AsyncClient stand-ins
around retrieve_context) and test_gap_events_telemetry.py (mocked
AsyncSession org rows).
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services import partner_chat

_REAL_QUERY = "Waar vind ik het retourbeleid?"


class _FakeOrg:
    def __init__(self, telemetry_level: str = "full") -> None:
        self.id = 42
        self.zitadel_org_id = "zit-org-1"
        self.telemetry_level = telemetry_level


def _scalar_result(value: object) -> MagicMock:
    res = MagicMock()
    res.scalar_one_or_none.return_value = value
    return res


def _patch_retrieve(monkeypatch, payload: dict[str, Any]) -> None:
    """Stand in for the retrieval-api /retrieve call with a fixed response."""

    class _MockResp:
        def raise_for_status(self):
            return None

        def json(self):
            return payload

    class _MockClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return None

        async def post(self, url, json=None, headers=None):
            return _MockResp()

    monkeypatch.setattr("app.services.partner_chat.httpx.AsyncClient", lambda timeout: _MockClient())


def _fake_settings() -> MagicMock:
    fake_settings = MagicMock()
    fake_settings.knowledge_retrieve_url = "http://retrieval-api:8040"
    fake_settings.retrieval_api_internal_secret = "secret"
    fake_settings.internal_secret = "fallback"
    return fake_settings


def _fake_tenant_session(captured: dict[str, Any], org: _FakeOrg | None = None):
    """tenant_scoped_session stand-in yielding a mock AsyncSession.

    Records the org id it was opened for (RLS scoping) and answers the
    service's PortalOrg lookup with ``org``.
    """

    @contextlib.asynccontextmanager
    async def _session(org_id: int):
        captured["session_org_id"] = org_id
        session = AsyncMock()
        captured["session"] = session
        session.execute = AsyncMock(return_value=_scalar_result(org or _FakeOrg("full")))
        rows: list[Any] = []
        session.add = MagicMock(side_effect=lambda obj: rows.append(obj))
        session.commit = AsyncMock()
        captured["rows"] = rows
        yield session

    return _session


async def _call_retrieve_context(**overrides: Any) -> tuple[list[dict], str, list[dict], bool]:
    kwargs: dict[str, Any] = {
        "org_id": 42,
        "zitadel_org_id": "zit-org-1",
        "kb_slugs": ["kb-alpha"],
        "messages": [{"role": "user", "content": _REAL_QUERY}],
        "settings": _fake_settings(),
    }
    kwargs.update(overrides)
    return await partner_chat.retrieve_context(**kwargs)


async def _drain_gap_tasks() -> None:
    """Await the fire-and-forget gap tasks scheduled during the test."""
    tasks = list(partner_chat._pending_gap_tasks)
    if tasks:
        await asyncio.gather(*tasks)


@pytest.mark.asyncio
async def test_widget_hard_gap_registers_gap_event(monkeypatch):
    """No results at all on the widget pad → 'hard' gap event, labelled as
    widget traffic and scoped to the org id the pad already knows."""
    captured: dict[str, Any] = {}
    _patch_retrieve(monkeypatch, {"chunks": []})  # no evidence_pack → 0 chunks
    monkeypatch.setattr("app.services.partner_chat.tenant_scoped_session", _fake_tenant_session(captured))

    with patch("app.services.partner_chat.record_gap_event", AsyncMock()) as mock_record:
        chunks, _, _, _broad = await _call_retrieve_context()
        await _drain_gap_tasks()

    assert chunks == []
    mock_record.assert_awaited_once()
    kwargs = mock_record.await_args.kwargs
    assert kwargs["gap_type"] == "hard"
    assert kwargs["zitadel_org_id"] == "zit-org-1"
    assert kwargs["query_text"] == _REAL_QUERY
    assert kwargs["user_id"] == partner_chat._WIDGET_ANONYMOUS_USER_ID
    assert kwargs["caller_client_id"] == partner_chat._WIDGET_GAP_CALLER_CLIENT_ID
    assert kwargs["chunks_retrieved"] == 0
    # RLS: the write runs on a session bound to the portal-internal org id.
    assert captured["session_org_id"] == 42


@pytest.mark.asyncio
async def test_widget_good_results_registers_no_gap_event(monkeypatch):
    """Strong retrieval results are not a gap — nothing may be written."""
    captured: dict[str, Any] = {}
    _patch_retrieve(
        monkeypatch,
        {
            "evidence_pack": {
                "items": [
                    {
                        "chunk_id": "c1",
                        "text": "Retour binnen 30 dagen, gratis via het punt.",
                        "source_url": "https://example.com/retour",
                        "reranker_score": 0.9,
                    }
                ],
                "sources": [],
            }
        },
    )
    monkeypatch.setattr("app.services.partner_chat.tenant_scoped_session", _fake_tenant_session(captured))

    with patch("app.services.partner_chat.record_gap_event", AsyncMock()) as mock_record:
        chunks, _, _, _broad = await _call_retrieve_context()
        await _drain_gap_tasks()

    assert len(chunks) == 1
    mock_record.assert_not_awaited()
    assert "session_org_id" not in captured


@pytest.mark.asyncio
async def test_widget_gap_respects_shadow_telemetry_level(monkeypatch):
    """REQ-8: the pad never opts itself into storing literal queries — the
    canonical telemetry_level is re-fetched by the shared service, so a
    'shadow' org gets the redaction marker instead of the real question."""
    captured: dict[str, Any] = {}
    _patch_retrieve(monkeypatch, {"chunks": []})
    monkeypatch.setattr(
        "app.services.partner_chat.tenant_scoped_session",
        _fake_tenant_session(captured, org=_FakeOrg("shadow")),
    )

    await _call_retrieve_context()
    await _drain_gap_tasks()

    assert len(captured["rows"]) == 1
    gap = captured["rows"][0]
    assert gap.query_text == "[REDACTED:shadow]"
    assert _REAL_QUERY not in gap.query_text
    assert gap.gap_type == "hard"
    assert gap.org_id == 42
    assert gap.caller_client_id == partner_chat._WIDGET_GAP_CALLER_CLIENT_ID


@pytest.mark.asyncio
async def test_widget_gap_soft_classification_with_none_scores(monkeypatch):
    """Evidence-pack items carry ``score: None`` when nothing reranked them.
    Regression: classify_gap must treat that as the dense-score default and
    register a 'soft' gap instead of raising into the chat path."""
    captured: dict[str, Any] = {}
    _patch_retrieve(
        monkeypatch,
        {
            "evidence_pack": {
                "items": [
                    {
                        "chunk_id": "c1",
                        "text": "Onzeker antwoord over een niche-onderwerp.",
                        "source_url": "https://example.com/niche",
                        "score": None,
                    }
                ],
                "sources": [],
            }
        },
    )
    monkeypatch.setattr("app.services.partner_chat.tenant_scoped_session", _fake_tenant_session(captured))

    with patch("app.services.partner_chat.record_gap_event", AsyncMock()) as mock_record:
        chunks, _, _, _broad = await _call_retrieve_context()
        await _drain_gap_tasks()

    assert len(chunks) == 1
    mock_record.assert_awaited_once()
    kwargs = mock_record.await_args.kwargs
    assert kwargs["gap_type"] == "soft"
    assert kwargs["top_score"] is None
    assert kwargs["chunks_retrieved"] == 1


@pytest.mark.asyncio
async def test_widget_soft_gap_derives_kb_slug_from_widget_scope(monkeypatch):
    """Root cause: evidence-pack chunks carry no ``metadata.kb_slug``, so a
    widget gap's ``nearest_kb_slug`` was always None and
    ``record_gap_event``'s ``taxonomy_node_ids is None and nearest_kb_slug``
    gate (app/services/gap_events.py) never started classification for
    widget rows. When the turn was scoped to exactly one KB, that KB is
    the KB the answer was retrieved from — use it."""
    captured: dict[str, Any] = {}
    _patch_retrieve(
        monkeypatch,
        {
            "evidence_pack": {
                "items": [
                    {
                        "chunk_id": "c1",
                        "text": "Onzeker antwoord over een niche-onderwerp.",
                        "source_url": "https://example.com/niche",
                        "score": None,
                    }
                ],
                "sources": [],
            }
        },
    )
    monkeypatch.setattr("app.services.partner_chat.tenant_scoped_session", _fake_tenant_session(captured))

    with patch("app.services.partner_chat.record_gap_event", AsyncMock()) as mock_record:
        await _call_retrieve_context(kb_slugs=["kb-alpha"])
        await _drain_gap_tasks()

    mock_record.assert_awaited_once()
    kwargs = mock_record.await_args.kwargs
    assert kwargs["gap_type"] == "soft"
    assert kwargs["nearest_kb_slug"] == "kb-alpha"


@pytest.mark.asyncio
async def test_widget_soft_gap_keeps_kb_slug_none_when_scope_is_ambiguous(monkeypatch):
    """Adjacent edge: a turn scoped to several KBs cannot be attributed to
    one without knowing which KB the top chunk's artifact belongs to
    (evidence-pack items carry no such field) — never guess an unrelated
    KB, keep None."""
    captured: dict[str, Any] = {}
    _patch_retrieve(
        monkeypatch,
        {
            "evidence_pack": {
                "items": [
                    {
                        "chunk_id": "c1",
                        "text": "Onzeker antwoord over een niche-onderwerp.",
                        "source_url": "https://example.com/niche",
                        "score": None,
                    }
                ],
                "sources": [],
            }
        },
    )
    monkeypatch.setattr("app.services.partner_chat.tenant_scoped_session", _fake_tenant_session(captured))

    with patch("app.services.partner_chat.record_gap_event", AsyncMock()) as mock_record:
        await _call_retrieve_context(kb_slugs=["kb-alpha", "kb-beta"])
        await _drain_gap_tasks()

    mock_record.assert_awaited_once()
    assert mock_record.await_args.kwargs["nearest_kb_slug"] is None


@pytest.mark.asyncio
async def test_widget_gap_write_failure_does_not_break_chat(monkeypatch):
    """A failing gap write is logged fire-and-forget; the chat answer still
    returns normally."""
    captured: dict[str, Any] = {}
    _patch_retrieve(monkeypatch, {"chunks": []})
    monkeypatch.setattr("app.services.partner_chat.tenant_scoped_session", _fake_tenant_session(captured))
    mock_logger = MagicMock()
    monkeypatch.setattr("app.services.partner_chat.logger", mock_logger)

    with patch(
        "app.services.partner_chat.record_gap_event",
        AsyncMock(side_effect=RuntimeError("database is down")),
    ):
        chunks, system_prompt, trusted_sources, _broad = await _call_retrieve_context()
        await _drain_gap_tasks()

    assert chunks == []
    assert system_prompt
    assert trusted_sources == []
    assert any(
        call.args and call.args[0] == "partner_chat_gap_event_write_failed"
        for call in mock_logger.warning.call_args_list
    )


@pytest.mark.asyncio
async def test_off_telemetry_level_writes_nothing(monkeypatch):
    """REQ-8 at the service level: 'off' orgs never get a row — the pad
    inherits the skip without knowing about the level."""
    captured: dict[str, Any] = {}
    _patch_retrieve(monkeypatch, {"chunks": []})
    monkeypatch.setattr(
        "app.services.partner_chat.tenant_scoped_session",
        _fake_tenant_session(captured, org=_FakeOrg("off")),
    )

    await _call_retrieve_context()
    await _drain_gap_tasks()

    assert captured["rows"] == []
    captured["session"].commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_widget_gap_records_visitor_question_language(monkeypatch):
    """§4.5: 'missing in English' is a different gap than 'missing in Dutch'.
    The language recorded is that of the visitor's question — never the
    answer, which does not exist yet when the gap is scheduled."""
    for query, expected in (
        ("Where do I find the return policy?", "en"),
        ("Waar vind ik het retourbeleid?", "nl"),
    ):
        captured: dict[str, Any] = {}
        _patch_retrieve(monkeypatch, {"chunks": []})
        monkeypatch.setattr("app.services.partner_chat.tenant_scoped_session", _fake_tenant_session(captured))

        with patch("app.services.partner_chat.record_gap_event", AsyncMock()) as mock_record:
            await _call_retrieve_context(messages=[{"role": "user", "content": query}])
            await _drain_gap_tasks()

        mock_record.assert_awaited_once()
        assert mock_record.await_args.kwargs["language"] == expected


@pytest.mark.asyncio
async def test_widget_gap_attaches_audit_conversation_id(monkeypatch):
    """§4.5 provenance: a gap from a widget turn points at the conversation
    row the audit trail keys on (widget_id, session_key), so the knowledge
    side can jump to the conversation. When that row does not exist yet —
    the audit write is fire-and-forget and can lose the race against this
    one — the gap is still written, just without provenance."""
    with patch("app.services.partner_chat.find_conversation_id", AsyncMock(return_value=(77, False))) as mock_find:
        captured: dict[str, Any] = {}
        _patch_retrieve(monkeypatch, {"chunks": []})
        monkeypatch.setattr("app.services.partner_chat.tenant_scoped_session", _fake_tenant_session(captured))
        with patch("app.services.partner_chat.record_gap_event", AsyncMock()) as mock_record:
            await _call_retrieve_context(audit_widget_id="11111111-1111-1111-1111-111111111111", audit_session_key="sk")
            await _drain_gap_tasks()

    mock_find.assert_awaited_once_with(
        widget_id="11111111-1111-1111-1111-111111111111",
        session_key="sk",
    )
    assert mock_record.await_args.kwargs["conversation_id"] == 77

    with patch("app.services.partner_chat.find_conversation_id", AsyncMock(return_value=None)):
        captured_missing: dict[str, Any] = {}
        _patch_retrieve(monkeypatch, {"chunks": []})
        monkeypatch.setattr(
            "app.services.partner_chat.tenant_scoped_session",
            _fake_tenant_session(captured_missing),
        )
        with patch("app.services.partner_chat.record_gap_event", AsyncMock()) as mock_missing:
            await _call_retrieve_context(audit_widget_id="11111111-1111-1111-1111-111111111111", audit_session_key="sk")
            await _drain_gap_tasks()

    mock_missing.assert_awaited_once()
    assert mock_missing.await_args.kwargs["conversation_id"] is None


@pytest.mark.asyncio
async def test_widget_gap_skips_a_test_marked_conversation(monkeypatch):
    """SPEC-KNOWLEDGE-ACTIVITY-001 test-mark: a conversation a reviewer
    already marked as a test message must not editorialise the gap backlog,
    same as it drops out of the activity list, the summary, the outcome loop
    and the nightly judge."""
    with patch("app.services.partner_chat.find_conversation_id", AsyncMock(return_value=(77, True))):
        captured: dict[str, Any] = {}
        _patch_retrieve(monkeypatch, {"chunks": []})
        monkeypatch.setattr("app.services.partner_chat.tenant_scoped_session", _fake_tenant_session(captured))
        with patch("app.services.partner_chat.record_gap_event", AsyncMock()) as mock_record:
            await _call_retrieve_context(audit_widget_id="11111111-1111-1111-1111-111111111111", audit_session_key="sk")
            await _drain_gap_tasks()

    mock_record.assert_not_awaited()


@pytest.mark.asyncio
async def test_widget_gap_survives_conversation_lookup_failure(monkeypatch):
    """Provenance is optional metadata: a failing conversation lookup costs
    the conversation id, never the gap row and never the chat answer."""
    captured: dict[str, Any] = {}
    _patch_retrieve(monkeypatch, {"chunks": []})
    monkeypatch.setattr("app.services.partner_chat.tenant_scoped_session", _fake_tenant_session(captured))
    mock_logger = MagicMock()
    monkeypatch.setattr("app.services.partner_chat.logger", mock_logger)

    with (
        patch(
            "app.services.partner_chat.find_conversation_id",
            AsyncMock(side_effect=RuntimeError("connection reset")),
        ),
        patch("app.services.partner_chat.record_gap_event", AsyncMock()) as mock_record,
    ):
        chunks, system_prompt, trusted_sources, _broad = await _call_retrieve_context(
            audit_widget_id="11111111-1111-1111-1111-111111111111",
            audit_session_key="sk",
        )
        await _drain_gap_tasks()

    assert chunks == []
    assert system_prompt
    assert trusted_sources == []
    mock_record.assert_awaited_once()
    assert mock_record.await_args.kwargs["conversation_id"] is None
    assert len(mock_logger.warning.call_args_list) == 1
    assert mock_logger.warning.call_args_list[0].args[0] == "partner_chat_gap_conversation_lookup_failed"


@pytest.mark.asyncio
async def test_widget_gap_waits_for_the_user_turn_audit_write(monkeypatch):
    """First-turn provenance: the conversation row is created by the user-turn
    audit task started in the same request. The gap task waits for that task
    before looking the row up, so a single-turn gap still links to its
    conversation instead of racing the insert and losing."""
    loop = asyncio.get_running_loop()
    audit_write: asyncio.Future[None] = loop.create_future()
    seen_done: list[bool] = []

    async def _lookup(**_kwargs: Any) -> tuple[int, bool]:
        seen_done.append(audit_write.done())
        return (77, False)

    captured: dict[str, Any] = {}
    _patch_retrieve(monkeypatch, {"chunks": []})
    monkeypatch.setattr("app.services.partner_chat.tenant_scoped_session", _fake_tenant_session(captured))
    with (
        patch("app.services.partner_chat.find_conversation_id", AsyncMock(side_effect=_lookup)),
        patch("app.services.partner_chat.record_gap_event", AsyncMock()) as mock_record,
    ):
        await _call_retrieve_context(
            audit_widget_id="11111111-1111-1111-1111-111111111111",
            audit_session_key="sk",
            audit_write=audit_write,
        )
        loop.call_later(0.05, audit_write.set_result, None)
        await _drain_gap_tasks()

    assert seen_done == [True]
    assert mock_record.await_args.kwargs["conversation_id"] == 77
