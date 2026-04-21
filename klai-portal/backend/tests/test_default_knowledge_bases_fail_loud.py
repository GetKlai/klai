"""Regression tests for tenant provisioning fail-loud behaviour.

Pins the invariant that downstream helpers called during provisioning
(`ensure_default_knowledge_bases`) raise on failure instead of swallowing
exceptions with a warning log. Silent swallow was the root cause of the
2026-04-16 Voys incident where the tenant ended up with zero rows in
portal_knowledge_bases and zero rows in portal_groups, but
provisioning_status='ready'.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.exc import IntegrityError

from app.services import default_knowledge_bases as dkb


@pytest.mark.asyncio
async def test_ensure_default_kbs_raises_when_org_kb_creation_fails(monkeypatch):
    """If create_default_org_kb raises, the exception must bubble up.

    Previously `ensure_default_knowledge_bases` wrapped everything in a
    try/except and logged a warning — the tenant was silently marked
    ready with no default KBs.
    """
    db = MagicMock()
    db.execute = AsyncMock()  # for set_config
    db.commit = AsyncMock()
    db.rollback = AsyncMock()

    async def _raising_create_org_kb(*args, **kwargs):
        raise IntegrityError("simulated RLS block", None, Exception("rls block"))

    monkeypatch.setattr(dkb, "create_default_org_kb", _raising_create_org_kb)
    monkeypatch.setattr(
        dkb,
        "create_default_personal_kb",
        AsyncMock(side_effect=AssertionError("should not reach personal kb")),
    )

    with pytest.raises(IntegrityError):
        await dkb.ensure_default_knowledge_bases(org_id=42, user_id="u1", db=db)

    # No silent swallow: commit must NOT have run
    db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_ensure_default_kbs_raises_when_personal_kb_creation_fails(monkeypatch):
    db = MagicMock()
    db.execute = AsyncMock()
    db.commit = AsyncMock()
    db.rollback = AsyncMock()

    monkeypatch.setattr(dkb, "create_default_org_kb", AsyncMock())

    async def _raising_create_personal_kb(*args, **kwargs):
        raise RuntimeError("simulated RLS block on personal kb")

    monkeypatch.setattr(dkb, "create_default_personal_kb", _raising_create_personal_kb)

    with pytest.raises(RuntimeError, match="personal kb"):
        await dkb.ensure_default_knowledge_bases(org_id=42, user_id="u1", db=db)

    db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_ensure_default_kbs_commits_on_success(monkeypatch):
    db = MagicMock()
    db.execute = AsyncMock()
    db.commit = AsyncMock()

    monkeypatch.setattr(dkb, "create_default_org_kb", AsyncMock())
    monkeypatch.setattr(dkb, "create_default_personal_kb", AsyncMock())

    await dkb.ensure_default_knowledge_bases(org_id=42, user_id="u1", db=db)

    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_ensure_default_kbs_calls_set_tenant_before_inserts(monkeypatch):
    """set_tenant must run before the KB helpers, otherwise INSERTs hit RLS."""
    call_order: list[str] = []

    async def _set_tenant(session, org_id: int) -> None:
        call_order.append(f"set_tenant:{org_id}")

    async def _create_org_kb(*args, **kwargs):
        call_order.append("create_org_kb")

    async def _create_personal_kb(*args, **kwargs):
        call_order.append("create_personal_kb")

    db = MagicMock()
    db.execute = AsyncMock()
    db.commit = AsyncMock()

    monkeypatch.setattr(dkb, "set_tenant", _set_tenant)
    monkeypatch.setattr(dkb, "create_default_org_kb", _create_org_kb)
    monkeypatch.setattr(dkb, "create_default_personal_kb", _create_personal_kb)

    await dkb.ensure_default_knowledge_bases(org_id=99, user_id="u1", db=db)

    assert call_order == ["set_tenant:99", "create_org_kb", "create_personal_kb"]
