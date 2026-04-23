"""Unit tests for GET /internal/templates/effective.

Covers SPEC-CHAT-TEMPLATES-001 REQ-TEMPLATES-INTERNAL:
- 401 when bearer missing/invalid (before DB access).
- 404 when Zitadel org unknown.
- 200 empty when librechat_user_id has no PortalUser mapping (fail-safe).
- 200 empty when user has no active_template_ids.
- 200 with instructions in the order specified by active_template_ids.
- Inactive/deleted templates silently skipped.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException


def _org(org_id: int = 42, zitadel_org_id: str = "zo-1") -> MagicMock:
    org = MagicMock()
    org.id = org_id
    org.zitadel_org_id = zitadel_org_id
    return org


def _portal_user(org_id: int = 42, active_ids: list[int] | None = None, lc_uid: str = "lc-1") -> MagicMock:
    u = MagicMock()
    u.org_id = org_id
    u.librechat_user_id = lc_uid
    u.active_template_ids = active_ids
    return u


def _template(id: int, name: str, text: str, is_active: bool = True, org_id: int = 42) -> MagicMock:
    t = MagicMock()
    t.id = id
    t.name = name
    t.prompt_text = text
    t.is_active = is_active
    t.org_id = org_id
    return t


def _scalar_one_or_none_result(value):
    """Build a SQLAlchemy Result-like mock whose scalar_one_or_none returns `value`."""
    r = MagicMock()
    r.scalar_one_or_none = MagicMock(return_value=value)
    return r


def _scalars_all_result(values: list):
    """Build a SQLAlchemy Result-like mock whose scalars().all() returns `values`."""
    scalars = MagicMock()
    scalars.all = MagicMock(return_value=values)
    r = MagicMock()
    r.scalars = MagicMock(return_value=scalars)
    return r


@pytest.mark.asyncio
async def test_unknown_org_returns_404(monkeypatch):
    from app.api import internal

    monkeypatch.setattr(internal, "_require_internal_token", AsyncMock())
    monkeypatch.setattr(internal, "_audit_internal_call", AsyncMock())
    monkeypatch.setattr(internal, "set_tenant", AsyncMock())

    db = MagicMock()
    db.execute = AsyncMock(return_value=_scalar_one_or_none_result(None))

    with pytest.raises(HTTPException) as exc:
        await internal.get_effective_templates(
            request=MagicMock(), zitadel_org_id="nope", librechat_user_id="x", db=db
        )

    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_unknown_librechat_user_returns_empty_200_failsafe(monkeypatch):
    """REQ-TEMPLATES-INTERNAL-E2: missing mapping → 200 empty, NOT 404."""
    from app.api import internal

    monkeypatch.setattr(internal, "_require_internal_token", AsyncMock())
    monkeypatch.setattr(internal, "_audit_internal_call", AsyncMock())
    monkeypatch.setattr(internal, "set_tenant", AsyncMock())

    org = _org()
    db = MagicMock()
    # First call resolves the org; second call (portal_users) returns None.
    db.execute = AsyncMock(
        side_effect=[
            _scalar_one_or_none_result(org),  # org lookup
            _scalar_one_or_none_result(None),  # user lookup — no mapping
        ]
    )

    out = await internal.get_effective_templates(
        request=MagicMock(), zitadel_org_id="zo-1", librechat_user_id="unknown", db=db
    )

    assert out.instructions == []


@pytest.mark.asyncio
async def test_null_active_template_ids_returns_empty(monkeypatch):
    """User exists but active_template_ids is NULL → empty list."""
    from app.api import internal

    monkeypatch.setattr(internal, "_require_internal_token", AsyncMock())
    monkeypatch.setattr(internal, "_audit_internal_call", AsyncMock())
    monkeypatch.setattr(internal, "set_tenant", AsyncMock())

    org = _org()
    user = _portal_user(active_ids=None)
    db = MagicMock()
    db.execute = AsyncMock(
        side_effect=[
            _scalar_one_or_none_result(org),
            _scalar_one_or_none_result(user),
        ]
    )

    out = await internal.get_effective_templates(
        request=MagicMock(), zitadel_org_id="zo-1", librechat_user_id="lc-1", db=db
    )

    assert out.instructions == []


@pytest.mark.asyncio
async def test_empty_active_template_ids_returns_empty(monkeypatch):
    """User has active_template_ids=[] → empty list (no template query made)."""
    from app.api import internal

    monkeypatch.setattr(internal, "_require_internal_token", AsyncMock())
    monkeypatch.setattr(internal, "_audit_internal_call", AsyncMock())
    monkeypatch.setattr(internal, "set_tenant", AsyncMock())

    org = _org()
    user = _portal_user(active_ids=[])
    db = MagicMock()
    db.execute = AsyncMock(
        side_effect=[
            _scalar_one_or_none_result(org),
            _scalar_one_or_none_result(user),
        ]
    )

    out = await internal.get_effective_templates(
        request=MagicMock(), zitadel_org_id="zo-1", librechat_user_id="lc-1", db=db
    )

    assert out.instructions == []


@pytest.mark.asyncio
async def test_preserves_user_specified_order(monkeypatch):
    """active_template_ids=[3,1,2] → instructions in that exact order."""
    from app.api import internal

    monkeypatch.setattr(internal, "_require_internal_token", AsyncMock())
    monkeypatch.setattr(internal, "_audit_internal_call", AsyncMock())
    monkeypatch.setattr(internal, "set_tenant", AsyncMock())

    org = _org()
    user = _portal_user(active_ids=[3, 1, 2])
    templates = [
        _template(1, "First", "t1"),
        _template(2, "Second", "t2"),
        _template(3, "Third", "t3"),
    ]

    db = MagicMock()
    db.execute = AsyncMock(
        side_effect=[
            _scalar_one_or_none_result(org),
            _scalar_one_or_none_result(user),
            _scalars_all_result(templates),
        ]
    )

    out = await internal.get_effective_templates(
        request=MagicMock(), zitadel_org_id="zo-1", librechat_user_id="lc-1", db=db
    )

    # Must follow active_template_ids order: 3, 1, 2
    assert [i.name for i in out.instructions] == ["Third", "First", "Second"]
    assert [i.text for i in out.instructions] == ["t3", "t1", "t2"]
    assert all(i.source == "template" for i in out.instructions)


@pytest.mark.asyncio
async def test_silently_skips_missing_and_inactive(monkeypatch):
    """REQ-TEMPLATES-INTERNAL-E5: referenced but inactive/deleted → skipped silently."""
    from app.api import internal

    monkeypatch.setattr(internal, "_require_internal_token", AsyncMock())
    monkeypatch.setattr(internal, "_audit_internal_call", AsyncMock())
    monkeypatch.setattr(internal, "set_tenant", AsyncMock())

    org = _org()
    user = _portal_user(active_ids=[1, 2, 99])  # 99 doesn't exist, 2 inactive
    # Query with is_active=True filter returns only id=1.
    # id=2 is filtered out by is_active, id=99 never existed.
    templates = [_template(1, "Only", "only-text")]

    db = MagicMock()
    db.execute = AsyncMock(
        side_effect=[
            _scalar_one_or_none_result(org),
            _scalar_one_or_none_result(user),
            _scalars_all_result(templates),
        ]
    )

    out = await internal.get_effective_templates(
        request=MagicMock(), zitadel_org_id="zo-1", librechat_user_id="lc-1", db=db
    )

    assert len(out.instructions) == 1
    assert out.instructions[0].name == "Only"


@pytest.mark.asyncio
async def test_missing_bearer_raises_before_db_access(monkeypatch):
    """REQ-TEMPLATES-INTERNAL-E4: 401 must fire before any DB query."""
    from app.api import internal

    # _require_internal_token raises 401 as its contract when bearer is bad.
    monkeypatch.setattr(
        internal,
        "_require_internal_token",
        AsyncMock(side_effect=HTTPException(status_code=401, detail="Unauthorized")),
    )

    db = MagicMock()
    db.execute = AsyncMock()  # If this is called we've violated the contract.

    with pytest.raises(HTTPException) as exc:
        await internal.get_effective_templates(
            request=MagicMock(), zitadel_org_id="zo-1", librechat_user_id="lc-1", db=db
        )

    assert exc.value.status_code == 401
    db.execute.assert_not_called()
