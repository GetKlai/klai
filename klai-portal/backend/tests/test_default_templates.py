"""Tests for app.services.default_templates.

Covers SPEC-CHAT-TEMPLATES-001 REQ-TEMPLATES-SEED:
- Idempotency via row-count check (second call is no-op).
- Exactly 4 defaults inserted on first call.
- Slugs match `{klantenservice, formeel, creatief, samenvatter}`.
- Defaults use scope="org" and created_by="system".
- Any DB exception is swallowed (non-fatal).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest


def _count_result(n: int) -> MagicMock:
    r = MagicMock()
    r.scalar = MagicMock(return_value=n)
    return r


@pytest.mark.asyncio
async def test_first_call_inserts_exactly_four_defaults():
    from app.services import default_templates

    db = MagicMock()
    db.execute = AsyncMock(return_value=_count_result(0))
    db.add = MagicMock()
    db.flush = AsyncMock()
    db.rollback = AsyncMock()

    inserted = await default_templates.ensure_default_templates(org_id=42, created_by="sys", db=db)

    assert inserted == 4
    assert db.add.call_count == 4


@pytest.mark.asyncio
async def test_second_call_is_no_op():
    from app.services import default_templates

    db = MagicMock()
    db.execute = AsyncMock(return_value=_count_result(4))  # already seeded
    db.add = MagicMock()
    db.flush = AsyncMock()

    inserted = await default_templates.ensure_default_templates(org_id=42, created_by="sys", db=db)

    assert inserted == 0
    db.add.assert_not_called()
    db.flush.assert_not_called()


@pytest.mark.asyncio
async def test_defaults_use_org_scope_and_system_created_by():
    from app.services import default_templates

    db = MagicMock()
    db.execute = AsyncMock(return_value=_count_result(0))
    added = []
    db.add = MagicMock(side_effect=added.append)
    db.flush = AsyncMock()
    db.rollback = AsyncMock()

    await default_templates.ensure_default_templates(org_id=42, created_by="system", db=db)

    assert len(added) == 4
    for tpl in added:
        assert tpl.scope == "org"
        assert tpl.created_by == "system"
        assert tpl.org_id == 42


def test_defaults_constant_has_expected_slugs():
    from app.services.default_templates import DEFAULT_TEMPLATES

    slugs = {t["slug"] for t in DEFAULT_TEMPLATES}
    assert slugs == {"klantenservice", "formeel", "creatief", "samenvatter"}


def test_defaults_constant_has_non_empty_prompt_text():
    from app.services.default_templates import DEFAULT_TEMPLATES

    for tpl in DEFAULT_TEMPLATES:
        assert tpl["prompt_text"].strip(), f"{tpl['slug']} has empty prompt_text"
        # All under the CHECK constraint limit.
        assert len(tpl["prompt_text"]) <= 8000


@pytest.mark.asyncio
async def test_exception_is_swallowed_and_rolled_back():
    """REQ-TEMPLATES-SEED: non-fatal — exceptions don't propagate."""
    from app.services import default_templates

    db = MagicMock()
    db.execute = AsyncMock(side_effect=RuntimeError("boom"))
    db.rollback = AsyncMock()

    # Must NOT raise.
    inserted = await default_templates.ensure_default_templates(org_id=42, created_by="sys", db=db)
    assert inserted == 0
    db.rollback.assert_awaited_once()
