"""Tests for REQ-2 (Finding B-2): record_widget_turn loaded_origin truncation.

SPEC-SEC-CROSS-TENANT-FOLLOWUP-001 REQ-2.

AC-tested:
- record_widget_turn() with a loaded_origin > 200 chars persists only the
  first 200 characters (SQL column constraint protection via Python truncation).
- record_widget_turn() with loaded_origin=None persists NULL without crashing.

@MX:SPEC: SPEC-SEC-CROSS-TENANT-FOLLOWUP-001 REQ-2
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_record_widget_turn_persists_loaded_origin_truncated_to_200():
    """record_widget_turn() with loaded_origin > 200 chars → persists first 200 chars.

    The widget_conversations.loaded_origin column is VARCHAR(200). Python-side
    truncation in record_widget_turn guarantees the INSERT never exceeds the
    column limit, even when a browser sends a very long Origin header.
    """
    from app.services.widget_audit import record_widget_turn

    long_origin = "https://very-long-subdomain.example.com/" + "a" * 500
    assert len(long_origin) > 200, "Precondition: test value must exceed 200 chars"

    captured_params: dict = {}

    async def fake_execute(sql, params=None):
        if params and "loaded_origin" in params:
            captured_params.update(params)
        fake_result = MagicMock()
        fake_result.first.return_value = ("conv-uuid-1", 0)
        return fake_result

    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(side_effect=fake_execute)
    mock_db.commit = AsyncMock()

    # REQ-14: cross_org_session lookup yields org_id from widgets table.
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
            session_key="test-session-key",
            role="user",
            content="Hello world",
            loaded_origin=long_origin,
        )

    # The INSERT must have received a truncated value of at most 200 chars.
    assert "loaded_origin" in captured_params, (
        "loaded_origin was not passed to db.execute — check record_widget_turn implementation"
    )
    persisted = captured_params["loaded_origin"]
    assert persisted == long_origin[:200], f"Expected first 200 chars but got length {len(persisted)}: {persisted!r}"
    assert len(persisted) == 200


@pytest.mark.asyncio
async def test_record_widget_turn_persists_loaded_origin_none():
    """record_widget_turn() with loaded_origin=None → persists NULL (no crash).

    When the Origin header is absent (e.g. direct API call, same-origin
    navigation without header), record_widget_turn must handle None gracefully
    and persist NULL in the loaded_origin column.
    """
    from app.services.widget_audit import record_widget_turn

    captured_params: dict = {}

    async def fake_execute(sql, params=None):
        if params and "loaded_origin" in params:
            captured_params.update(params)
        fake_result = MagicMock()
        fake_result.first.return_value = ("conv-uuid-2", 0)
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

        # Must not raise even with loaded_origin=None
        await record_widget_turn(
            widget_id="00000000-0000-0000-0000-000000000002",
            session_key="test-session-key-2",
            role="assistant",
            content="Response content",
            loaded_origin=None,
        )

    assert "loaded_origin" in captured_params, "loaded_origin was not passed to db.execute"
    assert captured_params["loaded_origin"] is None, f"Expected None but got {captured_params['loaded_origin']!r}"
