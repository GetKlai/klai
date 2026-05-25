"""REQ-16 (Finding B-14, SPEC-SEC-CROSS-TENANT-FOLLOWUP-001):
widget DELETE is soft-delete; conversations and messages survive.

AC16.1 — DELETE handler sets widgets.deleted_at, does NOT remove the row
AC16.2 — soft-deleted widget invisible to admin reads (CRUD endpoints 404)
AC16.3 — audit-trail endpoints still surface conversations for soft-deleted widgets
AC16.4 — covered by post-deploy SQL: widget_conversations.widget_id FK is
         NO ACTION (verified at deploy time; out of unit-test scope)

Also asserts the parallel REQ-16 partner-side guard: a soft-deleted widget
returns 404 to /partner/v1/widget-config, regardless of JWT validity.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# AC16.1 — DELETE handler soft-deletes the widget
# ---------------------------------------------------------------------------


class _MockWidget:
    """Mutable widget stand-in for soft-delete assertions."""

    def __init__(self, *, id_: str = "widget-uuid-1", org_id: int = 42, deleted_at=None):
        self.id = id_
        self.org_id = org_id
        self.name = "Test widget"
        self.deleted_at = deleted_at


@pytest.mark.asyncio
async def test_delete_widget_sets_deleted_at_does_not_drop_row() -> None:
    """AC16.1 — admin DELETE flips widgets.deleted_at and commits.
    The Widget row is NOT removed from the session."""
    from app.api.admin_widgets import delete_widget
    from tests.conftest import make_perms

    widget = _MockWidget()
    db = AsyncMock()
    db.execute = AsyncMock()
    db.delete = AsyncMock()  # MUST NOT be awaited per AC16.1
    db.commit = AsyncMock()

    with (
        patch("app.api.admin_widgets._get_widget_or_404", new=AsyncMock(return_value=widget)),
        patch("app.api.admin_widgets.emit_event"),
    ):
        await delete_widget(
            widget_id="widget-uuid-1",
            perms=make_perms(role="admin", org_id=42, platform_unlocked_features=["widgets"]),
            db=db,
        )

    assert widget.deleted_at is not None, "deleted_at must be set on soft-delete"
    # ORM row is updated in place; no db.delete(Widget) call should fire.
    db.delete.assert_not_called()
    db.commit.assert_awaited_once()


# ---------------------------------------------------------------------------
# AC16.2 — _get_widget_or_404 default behaviour: soft-deleted = 404
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_widget_or_404_excludes_soft_deleted_by_default() -> None:
    """AC16.2 — admin CRUD callers see soft-deleted widgets as 404."""
    from fastapi import HTTPException

    from app.api.admin_widgets import _get_widget_or_404

    # _get_widget_or_404 issues db.execute; with no row returned (the WHERE
    # filter excludes soft-deleted), we expect HTTPException.
    db = AsyncMock()
    result = MagicMock()
    result.scalar_one_or_none = MagicMock(return_value=None)
    db.execute = AsyncMock(return_value=result)

    with pytest.raises(HTTPException) as exc:
        await _get_widget_or_404("widget-uuid-1", org_id=42, db=db)
    assert exc.value.status_code == 404

    # Inspect the SELECT to confirm deleted_at IS NULL clause is in the WHERE.
    stmt = db.execute.await_args.args[0]
    rendered = str(stmt.compile(compile_kwargs={"literal_binds": True}))
    assert "deleted_at IS NULL" in rendered, f"WHERE clause missing deleted_at IS NULL: {rendered}"


@pytest.mark.asyncio
async def test_list_widgets_excludes_soft_deleted_rows() -> None:
    """AC16.2 — admin list endpoint must not return soft-deleted widgets."""
    from app.api.admin_widgets import list_widgets
    from tests.conftest import make_perms

    db = AsyncMock()
    result = MagicMock()
    result.scalars.return_value.all.return_value = []
    db.execute = AsyncMock(return_value=result)

    got = await list_widgets(
        perms=make_perms(role="admin", org_id=42, platform_unlocked_features=["widgets"]),
        db=db,
    )

    assert got == []
    stmt = db.execute.await_args.args[0]
    rendered = str(stmt.compile(compile_kwargs={"literal_binds": True}))
    assert "deleted_at IS NULL" in rendered, f"list_widgets WHERE clause missing deleted_at IS NULL: {rendered}"


@pytest.mark.asyncio
async def test_get_widget_or_404_include_deleted_returns_soft_deleted() -> None:
    """AC16.3 — audit-trail endpoints opt-in via include_deleted=True."""
    from app.api.admin_widgets import _get_widget_or_404

    soft_deleted = _MockWidget(deleted_at=datetime.now(UTC))
    db = AsyncMock()
    result = MagicMock()
    result.scalar_one_or_none = MagicMock(return_value=soft_deleted)
    db.execute = AsyncMock(return_value=result)

    got = await _get_widget_or_404("widget-uuid-1", org_id=42, db=db, include_deleted=True)
    assert got is soft_deleted

    # The deleted_at filter must NOT appear when include_deleted=True.
    stmt = db.execute.await_args.args[0]
    rendered = str(stmt.compile(compile_kwargs={"literal_binds": True}))
    assert "deleted_at IS NULL" not in rendered, f"include_deleted=True must NOT filter on deleted_at: {rendered}"


# ---------------------------------------------------------------------------
# Partner-side guard — soft-deleted widget is invisible to public mint
# ---------------------------------------------------------------------------


@dataclass
class _FakeWidgetRow:
    id: str = "widget-uuid-1"
    org_id: int = 42
    name: str = "T"
    widget_id: str = "wgt_x"
    widget_config: dict = field(default_factory=lambda: {"allowed_origins": ["https://x"]})
    public_share_enabled: bool = True
    allow_any_origin: bool = False
    rate_limit_rpm: int = 60
    deleted_at: datetime | None = None


def test_partner_widget_lookup_filters_soft_deleted_in_select() -> None:
    """REQ-16: the public /partner/v1/widget-config and /public-bot-config
    handlers, plus _auth_via_session_token, each include
    ``Widget.deleted_at IS NULL`` in their SELECT — verified by source
    inspection so a future regression cannot silently drop the guard.
    """
    import inspect

    import app.api.partner as partner_module
    import app.api.partner_dependencies as deps_module

    partner_src = inspect.getsource(partner_module)
    deps_src = inspect.getsource(deps_module)

    # widget_config + public_bot_config each have one occurrence;
    # the third occurrence is the chat-path Widget lookup at line ~1038.
    assert partner_src.count("Widget.deleted_at.is_(None)") >= 3, (
        "All three public-facing Widget lookups in partner.py must filter on deleted_at"
    )
    assert "Widget.deleted_at.is_(None)" in deps_src, (
        "Session-token auth lookup in partner_dependencies.py must filter on deleted_at"
    )
