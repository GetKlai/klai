"""Visitor contact details land on the conversation row, not in the transcript.

The widget's pre-chat step (widget_config.collect_user_info) asks for a name
and an e-mail so a reviewer can mail a correction when an answer was wrong.
They used to be prepended to the first user message as a "Visitor details:"
block, which made ``widget_conversations.first_user_query`` — the label in the
activity list and the key the top-queries aggregate groups on — show the
contact block instead of the question the visitor asked.

Covered here:
- the two values reach the conversation UPSERT as their own parameters;
- ``first_user_query`` stays the plain question;
- blank input persists as NULL rather than an empty string.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


async def _capture_turn(**kwargs) -> dict:
    """Run record_widget_turn against a mocked DB and return the UPSERT params."""
    from app.services.widget_audit import record_widget_turn

    captured: dict = {}

    async def fake_execute(sql, params=None):
        if params and "first_user_query" in params:
            captured.update(params)
        fake_result = MagicMock()
        fake_result.first.return_value = (7, 0)
        return fake_result

    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(side_effect=fake_execute)
    mock_db.commit = AsyncMock()

    lookup_row = MagicMock()
    lookup_row.first.return_value = (1,)
    lookup_db = AsyncMock()
    lookup_db.execute = AsyncMock(return_value=lookup_row)

    with (
        patch("app.services.widget_audit.cross_org_session") as mock_cross,
        patch("app.services.widget_audit.tenant_scoped_session") as mock_ctx,
    ):
        mock_cross.return_value.__aenter__ = AsyncMock(return_value=lookup_db)
        mock_cross.return_value.__aexit__ = AsyncMock(return_value=False)
        mock_ctx.return_value.__aenter__ = AsyncMock(return_value=mock_db)
        mock_ctx.return_value.__aexit__ = AsyncMock(return_value=False)

        await record_widget_turn(
            widget_id="00000000-0000-0000-0000-000000000001",
            session_key="session-key",
            role="user",
            **kwargs,
        )
    return captured


@pytest.mark.asyncio
async def test_visitor_contact_is_stored_beside_the_question_not_inside_it():
    params = await _capture_turn(
        content="Hoe zeg ik mijn abonnement op?",
        visitor_name="Mark",
        visitor_email="mark@example.com",
    )

    assert params["visitor_name"] == "Mark"
    assert params["visitor_email"] == "mark@example.com"
    # The activity list and the top-queries aggregate both read this column.
    assert params["first_user_query"] == "Hoe zeg ik mijn abonnement op?"


@pytest.mark.asyncio
async def test_skipped_pre_chat_step_persists_null_not_empty_string():
    params = await _capture_turn(content="Vraag zonder gegevens", visitor_name="   ", visitor_email="")

    assert params["visitor_name"] is None
    assert params["visitor_email"] is None


@pytest.mark.asyncio
async def test_visitor_contact_is_clamped_to_the_column_widths():
    params = await _capture_turn(
        content="Vraag",
        visitor_name="n" * 300,
        visitor_email=f"{'e' * 300}@example.com",
    )

    assert len(params["visitor_name"]) == 120
    assert len(params["visitor_email"]) == 254
