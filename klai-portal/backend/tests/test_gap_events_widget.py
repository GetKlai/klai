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
