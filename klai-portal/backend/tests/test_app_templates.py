"""Unit tests for app.api.app_templates.

These tests mock _get_caller_org + DB + Redis so we don't need Postgres.
Full integration-style RLS + migration tests live alongside the other
RLS-smoke-tests (out of scope for this file).

Covers SPEC-CHAT-TEMPLATES-001 REQ-TEMPLATES-CRUD:
- Admin-gate on scope="org" POST (NL message).
- Slug rejection for empty/punctuation-only names.
- Rate-limit fail-open when Redis is unavailable.
- Cache invalidation dispatch (org-wide vs single user).
"""

from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException


def _mock_caller(role: str = "admin", zitadel_user_id: str = "user-1", org_id: int = 42, lc_uid: str | None = "lc-1"):
    """Factory for (zitadel_user_id, org, user) triple returned by _get_caller_org."""
    org = MagicMock()
    org.id = org_id

    user = MagicMock()
    user.role = role
    user.zitadel_user_id = zitadel_user_id
    user.librechat_user_id = lc_uid

    return zitadel_user_id, org, user


def _mock_template(
    id: int = 1,
    slug: str = "test",
    name: str = "Test",
    scope: str = "org",
    created_by: str = "user-1",
    is_active: bool = True,
) -> MagicMock:
    t = MagicMock()
    t.id = id
    t.slug = slug
    t.name = name
    t.description = None
    t.prompt_text = "Hello"
    t.scope = scope
    t.created_by = created_by
    t.is_active = is_active
    t.created_at = datetime(2026, 4, 23, 12, 0, 0)
    t.updated_at = datetime(2026, 4, 23, 12, 0, 0)
    t.org_id = 42
    return t


# ---------------------------------------------------------------------------
# Admin-gate on scope="org" create
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_post_scope_org_as_non_admin_returns_403_nl(monkeypatch):
    """REQ-TEMPLATES-CRUD-E1: non-admin POST scope='org' → 403 with NL message."""
    from app.api import app_templates

    monkeypatch.setattr(
        app_templates,
        "_get_caller_org",
        AsyncMock(return_value=_mock_caller(role="user")),
    )
    monkeypatch.setattr(app_templates, "_enforce_rate_limit", AsyncMock())

    body = app_templates.TemplateCreate(name="X", prompt_text="y", scope="org")

    with pytest.raises(HTTPException) as exc:
        await app_templates.create_template(body=body, credentials=MagicMock(), db=MagicMock())

    assert exc.value.status_code == 403
    assert "beheerders" in exc.value.detail.lower()


@pytest.mark.asyncio
async def test_post_scope_personal_as_non_admin_allowed(monkeypatch):
    """REQ-TEMPLATES-CRUD-E1 inverse: non-admin CAN POST scope='personal'."""
    from app.api import app_templates

    monkeypatch.setattr(
        app_templates,
        "_get_caller_org",
        AsyncMock(return_value=_mock_caller(role="user")),
    )
    monkeypatch.setattr(app_templates, "_enforce_rate_limit", AsyncMock())
    monkeypatch.setattr(app_templates, "invalidate_templates", AsyncMock())

    db = MagicMock()
    db.add = MagicMock()
    db.flush = AsyncMock()
    db.commit = AsyncMock()

    # Canonical CREATE pattern: flush + refresh run BEFORE commit, while the
    # tenant GUC is still active on the transaction.
    async def _refresh(obj):
        obj.id = 123
        obj.slug = "x"
        obj.scope = "personal"
        obj.created_by = "user-1"
        obj.is_active = True
        obj.created_at = datetime(2026, 4, 23, 12, 0, 0)
        obj.updated_at = datetime(2026, 4, 23, 12, 0, 0)

    db.refresh = AsyncMock(side_effect=_refresh)

    body = app_templates.TemplateCreate(name="X", prompt_text="y", scope="personal")
    out = await app_templates.create_template(body=body, credentials=MagicMock(), db=db)

    assert out.scope == "personal"
    # flush → refresh → commit ordering is part of the contract.
    assert db.flush.await_count == 1
    assert db.refresh.await_count == 1
    assert db.commit.await_count == 1
    # Personal write → single-user invalidation, not org-wide.
    app_templates.invalidate_templates.assert_awaited_once_with(42, "lc-1")


@pytest.mark.asyncio
async def test_post_scope_org_as_admin_triggers_org_wide_invalidate(monkeypatch):
    """Admin creates scope='org' → invalidate_templates called with ONLY org_id (org-wide SCAN+DEL)."""
    from app.api import app_templates

    monkeypatch.setattr(
        app_templates,
        "_get_caller_org",
        AsyncMock(return_value=_mock_caller(role="admin")),
    )
    monkeypatch.setattr(app_templates, "_enforce_rate_limit", AsyncMock())
    monkeypatch.setattr(app_templates, "invalidate_templates", AsyncMock())

    db = MagicMock()
    db.add = MagicMock()
    db.flush = AsyncMock()
    db.commit = AsyncMock()

    async def _refresh(obj):
        obj.id = 1
        obj.slug = "x"
        obj.scope = "org"
        obj.created_by = "user-1"
        obj.is_active = True
        obj.created_at = datetime(2026, 4, 23, 12, 0, 0)
        obj.updated_at = datetime(2026, 4, 23, 12, 0, 0)

    db.refresh = AsyncMock(side_effect=_refresh)

    body = app_templates.TemplateCreate(name="X", prompt_text="y", scope="org")
    await app_templates.create_template(body=body, credentials=MagicMock(), db=db)

    # Org-wide invalidation: called with just org_id (second arg defaults to None).
    app_templates.invalidate_templates.assert_awaited_once_with(42)


# ---------------------------------------------------------------------------
# Slug derivation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_post_rejects_name_that_collapses_to_empty_slug(monkeypatch):
    """REQ-TEMPLATES-CRUD-U2: punctuation-only name → 400."""
    from app.api import app_templates

    monkeypatch.setattr(
        app_templates,
        "_get_caller_org",
        AsyncMock(return_value=_mock_caller(role="admin")),
    )
    monkeypatch.setattr(app_templates, "_enforce_rate_limit", AsyncMock())

    # Pydantic min_length=1 blocks empty string; punctuation-only IS >= 1 char
    # so Pydantic accepts it — the business-logic 400 fires inside the endpoint.
    body = app_templates.TemplateCreate(name="!!!", prompt_text="y", scope="org")

    with pytest.raises(HTTPException) as exc:
        await app_templates.create_template(body=body, credentials=MagicMock(), db=MagicMock())

    assert exc.value.status_code == 400
    assert "slug" in exc.value.detail.lower()


# ---------------------------------------------------------------------------
# Rate-limit
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rate_limit_returns_429_with_retry_after(monkeypatch):
    """REQ-TEMPLATES-CRUD-E4: over-limit → 429 with Retry-After header."""
    from app.api import app_templates

    fake_pool = MagicMock()
    monkeypatch.setattr(app_templates, "get_redis_pool", AsyncMock(return_value=fake_pool))
    monkeypatch.setattr(app_templates, "check_rate_limit", AsyncMock(return_value=(False, 37)))

    with pytest.raises(HTTPException) as exc:
        await app_templates._enforce_rate_limit(org_id=42)

    assert exc.value.status_code == 429
    assert exc.value.headers["Retry-After"] == "37"


@pytest.mark.asyncio
async def test_rate_limit_fail_open_when_redis_none(monkeypatch):
    """Redis not configured → rate-limit no-op (fail-open)."""
    from app.api import app_templates

    monkeypatch.setattr(app_templates, "get_redis_pool", AsyncMock(return_value=None))
    # Should not raise; if it reached check_rate_limit with None we'd crash.
    await app_templates._enforce_rate_limit(org_id=42)


@pytest.mark.asyncio
async def test_rate_limit_fail_open_on_redis_error(monkeypatch):
    """check_rate_limit raising → rate-limit no-op (fail-open with warning)."""
    from app.api import app_templates

    fake_pool = MagicMock()
    monkeypatch.setattr(app_templates, "get_redis_pool", AsyncMock(return_value=fake_pool))
    monkeypatch.setattr(app_templates, "check_rate_limit", AsyncMock(side_effect=RuntimeError("redis down")))
    # Must NOT raise.
    await app_templates._enforce_rate_limit(org_id=42)


# ---------------------------------------------------------------------------
# PATCH authorization
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_patch_by_non_owner_non_admin_returns_403(monkeypatch):
    """REQ-TEMPLATES-CRUD-E2: non-owner + non-admin PATCH → 403."""
    from app.api import app_templates

    monkeypatch.setattr(
        app_templates,
        "_get_caller_org",
        AsyncMock(return_value=_mock_caller(role="user", zitadel_user_id="user-1")),
    )
    monkeypatch.setattr(app_templates, "_enforce_rate_limit", AsyncMock())
    template = _mock_template(created_by="user-2")  # someone else's template
    monkeypatch.setattr(app_templates, "_get_template_or_404", AsyncMock(return_value=template))

    body = app_templates.TemplatePatch(name="New Name")
    with pytest.raises(HTTPException) as exc:
        await app_templates.update_template(slug="test", body=body, credentials=MagicMock(), db=MagicMock())

    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_patch_happy_path_flushes_and_refreshes_before_commit(monkeypatch):
    """Regression: `PortalTemplate.updated_at` has `onupdate=func.now()`, which
    SQLAlchemy expires after flush. Without a refresh, `_template_out` later
    triggers a lazy SELECT that fires outside the greenlet context and raises
    `MissingGreenlet` (observed in production 2026-04-24 as HTTP 500 on
    PATCH /api/app/templates/klantenservice2).

    This test locks the flush → refresh → commit ordering so a future cleanup
    cannot re-introduce the same bug.
    """
    from app.api import app_templates

    monkeypatch.setattr(
        app_templates,
        "_get_caller_org",
        AsyncMock(return_value=_mock_caller(role="admin", zitadel_user_id="user-1")),
    )
    monkeypatch.setattr(app_templates, "_enforce_rate_limit", AsyncMock())
    monkeypatch.setattr(app_templates, "invalidate_templates", AsyncMock())
    template = _mock_template(created_by="user-1", scope="org", slug="klantenservice")
    monkeypatch.setattr(app_templates, "_get_template_or_404", AsyncMock(return_value=template))

    call_order: list[str] = []
    db = MagicMock()
    db.flush = AsyncMock(side_effect=lambda: call_order.append("flush"))

    async def _refresh(obj):
        call_order.append("refresh")
        # Simulate server-side updated_at being re-populated.
        obj.updated_at = datetime(2026, 4, 24, 14, 0, 0)

    db.refresh = AsyncMock(side_effect=_refresh)
    db.commit = AsyncMock(side_effect=lambda: call_order.append("commit"))

    body = app_templates.TemplatePatch(name="Klantenservice 2")
    out = await app_templates.update_template(slug="klantenservice", body=body, credentials=MagicMock(), db=db)

    # flush MUST precede refresh MUST precede commit, so the refresh SELECT
    # runs inside the tenant-scoped transaction.
    assert call_order == ["flush", "refresh", "commit"], call_order
    # Response carries the new slug derived from the renamed template.
    assert out.slug == "klantenservice-2"


@pytest.mark.asyncio
async def test_patch_promoting_personal_to_org_requires_admin(monkeypatch):
    """REQ-TEMPLATES-CRUD-E1 applies on PATCH too: promoting to scope='org' needs admin."""
    from app.api import app_templates

    monkeypatch.setattr(
        app_templates,
        "_get_caller_org",
        AsyncMock(return_value=_mock_caller(role="user", zitadel_user_id="user-1")),
    )
    monkeypatch.setattr(app_templates, "_enforce_rate_limit", AsyncMock())
    template = _mock_template(created_by="user-1", scope="personal")  # owner trying to promote
    monkeypatch.setattr(app_templates, "_get_template_or_404", AsyncMock(return_value=template))

    body = app_templates.TemplatePatch(scope="org")
    with pytest.raises(HTTPException) as exc:
        await app_templates.update_template(slug="test", body=body, credentials=MagicMock(), db=MagicMock())

    assert exc.value.status_code == 403


# ---------------------------------------------------------------------------
# Personal visibility in GET detail
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_personal_hides_from_non_owner_non_admin(monkeypatch):
    """REQ-TEMPLATES-CRUD-S1: non-owner non-admin sees 404 for personal template."""
    from app.api import app_templates

    monkeypatch.setattr(
        app_templates,
        "_get_caller_org",
        AsyncMock(return_value=_mock_caller(role="user", zitadel_user_id="user-1")),
    )
    template = _mock_template(created_by="user-2", scope="personal")
    monkeypatch.setattr(app_templates, "_get_template_or_404", AsyncMock(return_value=template))

    with pytest.raises(HTTPException) as exc:
        await app_templates.get_template(slug="test", credentials=MagicMock(), db=MagicMock())

    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_get_personal_visible_to_admin(monkeypatch):
    """REQ-TEMPLATES-CRUD-S1: admin sees every personal template in their org."""
    from app.api import app_templates

    monkeypatch.setattr(
        app_templates,
        "_get_caller_org",
        AsyncMock(return_value=_mock_caller(role="admin", zitadel_user_id="admin-1")),
    )
    template = _mock_template(created_by="user-2", scope="personal")
    monkeypatch.setattr(app_templates, "_get_template_or_404", AsyncMock(return_value=template))

    out = await app_templates.get_template(slug="test", credentials=MagicMock(), db=MagicMock())

    assert out.scope == "personal"
    assert out.created_by == "user-2"
