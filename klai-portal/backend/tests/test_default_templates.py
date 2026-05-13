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
    assert db.execute.await_count == 2


@pytest.mark.asyncio
async def test_sets_tenant_context_before_counting_templates():
    from app.services import default_templates

    db = MagicMock()
    db.execute = AsyncMock(return_value=_count_result(4))
    db.add = MagicMock()
    db.flush = AsyncMock()

    await default_templates.ensure_default_templates(org_id=42, created_by="sys", db=db)

    first_statement = str(db.execute.await_args_list[0].args[0])
    assert "set_config('app.current_org_id'" in first_statement


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


def test_klantenservice_default_is_language_agnostic():
    """Regression guard for the SPEC-RAG-MULTILINGUAL-CHAT-001 cleanup
    (commit a0d72cea, 2026-05-07).

    The "Klantenservice" seed template originally pinned the model to
    Dutch via "Antwoord altijd in het Nederlands". That instruction
    overrode the multilingual ``GROUNDED_CHAT_SYSTEM_PROMPT`` whenever
    a tenant had this template active. The cleanup removed that line
    and replaced it with a language-agnostic phrasing. This test pins
    both ends of the contract so a future template tweak cannot
    silently re-introduce the regression.
    """
    from app.services.default_templates import DEFAULT_TEMPLATES

    klantenservice = next(t for t in DEFAULT_TEMPLATES if t["slug"] == "klantenservice")
    prompt = klantenservice["prompt_text"]

    # Negative: the legacy NL-pinning string must NOT appear.
    assert "Antwoord altijd in het Nederlands" not in prompt, (
        "Klantenservice default seed must remain language-agnostic. See commit a0d72cea + post_deploy_e44f9da674fe.sql."
    )
    # Positive: the new wording explicitly mirrors the user's question
    # language. This anchor lets a future reviewer trace the rule back.
    assert "in dezelfde taal als de vraag van de gebruiker" in prompt


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
