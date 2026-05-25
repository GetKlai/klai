"""REQ-14 (Finding B-7, SPEC-SEC-CROSS-TENANT-FOLLOWUP-001):
``record_widget_turn`` MUST derive ``org_id`` server-side from the widget
row, not accept it from the caller.

AC14.1 — caller cannot influence org_id; the INSERT uses widget.org_id
AC14.2 — Python TypeError when org_id is passed (signature change enforced)
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _fake_lookup_result(org_id: int | None):
    """Mimic SQLAlchemy `.first()` for the cross_org SELECT."""

    class _Row:
        def __init__(self, value):
            self._value = value

        def __getitem__(self, idx):
            return self._value if idx == 0 else None

    result = MagicMock()
    result.first.return_value = _Row(org_id) if org_id is not None else None
    return result


@pytest.mark.asyncio
async def test_record_widget_turn_uses_widget_row_org_id_for_insert() -> None:
    """AC14.1 — inserted rows carry the org_id derived from the widget table,
    even if a hypothetical bypass tried to set a different one."""
    from app.services.widget_audit import record_widget_turn

    captured: list[dict] = []

    async def _exec(sql, params=None):
        # Capture every INSERT's org_id to assert against the widget row.
        if params is not None:
            captured.append(dict(params))
        # The INSERT INTO widget_conversations ... RETURNING returns
        # (conv_id, message_count). The two subsequent statements return
        # nothing meaningful.
        res = MagicMock()
        res.first.return_value = ("conv-uuid", 0)
        return res

    tenant_db = AsyncMock()
    tenant_db.execute = AsyncMock(side_effect=_exec)
    tenant_db.commit = AsyncMock()

    lookup_db = AsyncMock()
    lookup_db.execute = AsyncMock(return_value=_fake_lookup_result(42))

    with (
        patch("app.services.widget_audit.cross_org_session") as ctx_cross,
        patch("app.services.widget_audit.tenant_scoped_session") as ctx_tenant,
    ):
        ctx_cross.return_value.__aenter__ = AsyncMock(return_value=lookup_db)
        ctx_cross.return_value.__aexit__ = AsyncMock(return_value=False)
        ctx_tenant.return_value.__aenter__ = AsyncMock(return_value=tenant_db)
        ctx_tenant.return_value.__aexit__ = AsyncMock(return_value=False)

        await record_widget_turn(
            widget_id="00000000-0000-0000-0000-000000000001",
            session_key="test-session",
            role="user",
            content="hi",
        )

    # The tenant_scoped_session was opened with the widget's org_id (42).
    ctx_tenant.assert_called_once_with(42)
    # Every INSERT that carries org_id used 42.
    org_ids_used = {p["org_id"] for p in captured if "org_id" in p}
    assert org_ids_used == {42}, f"Expected only org_id=42, got {org_ids_used}"


@pytest.mark.asyncio
async def test_record_widget_turn_returns_silently_when_widget_not_found() -> None:
    """The lookup returns nothing → audit is skipped without raising."""
    from app.services.widget_audit import record_widget_turn

    lookup_db = AsyncMock()
    lookup_db.execute = AsyncMock(return_value=_fake_lookup_result(None))

    tenant_ctx = MagicMock()
    tenant_ctx.return_value.__aenter__ = AsyncMock()

    with (
        patch("app.services.widget_audit.cross_org_session") as ctx_cross,
        patch("app.services.widget_audit.tenant_scoped_session", new=tenant_ctx),
    ):
        ctx_cross.return_value.__aenter__ = AsyncMock(return_value=lookup_db)
        ctx_cross.return_value.__aexit__ = AsyncMock(return_value=False)

        await record_widget_turn(
            widget_id="00000000-0000-0000-0000-000000000002",
            session_key="sk",
            role="user",
            content="hi",
        )

    # tenant_scoped_session must NOT have been opened — there is no org to scope to.
    tenant_ctx.assert_not_called()


def test_record_widget_turn_signature_does_not_accept_org_id() -> None:
    """AC14.2 — passing org_id as a kwarg MUST raise TypeError so any caller
    that still tries to influence org_id surfaces immediately at call time.
    """
    import inspect

    from app.services.widget_audit import record_widget_turn

    sig = inspect.signature(record_widget_turn)
    assert "org_id" not in sig.parameters, (
        "record_widget_turn must NOT accept org_id as a parameter "
        "(REQ-14 / Finding B-7). Derive org_id server-side from the widget row."
    )
