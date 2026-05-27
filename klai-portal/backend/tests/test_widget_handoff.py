from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.services.widget_handoff import build_handoff_context_text, record_hubspot_agent_reply


class _Result:
    def __init__(self, row):
        self._row = row

    def first(self):
        return self._row


def test_build_handoff_context_text_includes_summary_and_recent_transcript() -> None:
    text = build_handoff_context_text(
        summary="Klant wil realtime hulp.",
        visitor_name="Mark Vletter",
        visitor_email="mark@example.com",
        messages=[
            {"role": "user", "content": "Hoi"},
            {"role": "assistant", "content": "Waarmee kan ik helpen?"},
        ],
    )

    assert "Nieuwe live support overdracht" in text
    assert "Samenvatting:" in text
    assert "Klant wil realtime hulp." in text
    assert "Bezoeker:" in text
    assert "Naam: Mark Vletter" in text
    assert "E-mail: mark@example.com" in text
    assert "Bezoeker: Hoi" in text
    assert "Klai: Waarmee kan ik helpen?" in text


@pytest.mark.asyncio
async def test_record_hubspot_agent_reply_records_and_publishes() -> None:
    db = AsyncMock()
    db.execute = AsyncMock(
        side_effect=[
            _Result(None),  # enable cross-org lookup
            _Result((42, 7)),
            _Result(None),  # disable cross-org lookup
            _Result(None),  # set_tenant
            _Result((99, "2026-05-27T13:05:31Z")),
        ]
    )
    db.commit = AsyncMock()
    payload = {
        "message": {
            "id": "hubspot-msg-1",
            "conversationsThreadId": "19615248623",
            "text": "Hier een reactie",
            "sender": {"name": "Mark Vletter"},
        }
    }

    with (
        patch("app.services.widget_handoff.get_redis_pool", AsyncMock(return_value=None)),
    ):
        result = await record_hubspot_agent_reply(db, payload)

    assert result["status"] == "recorded"
    assert result["handoff_session_id"] == 42
    assert result["id"] == 99
    assert db.execute.await_count == 5
    assert db.execute.await_args_list[4].args[1]["agent_name"] == "Mark Vletter"
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_record_hubspot_agent_reply_ignores_unmapped_thread() -> None:
    db = AsyncMock()
    db.execute = AsyncMock(
        side_effect=[
            _Result(None),  # enable cross-org lookup
            _Result(None),
            _Result(None),  # disable cross-org lookup
        ]
    )
    db.commit = AsyncMock()
    payload = {
        "message": {
            "id": "hubspot-msg-1",
            "conversationsThreadId": "19615248623",
            "text": "Hier een reactie",
        }
    }

    result = await record_hubspot_agent_reply(db, payload)

    assert result == {"status": "ignored", "reason": "unmapped_thread"}
    assert db.execute.await_count == 3
    db.commit.assert_not_awaited()
