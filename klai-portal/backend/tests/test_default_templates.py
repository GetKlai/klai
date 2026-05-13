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


@pytest.mark.asyncio
async def test_ensure_sets_tenant_context_before_count_and_insert():
    """Regression guard for 2026-05-13 incident on org_id=10 (e2e tenant).

    portal_templates uses a Cat-D strict RLS policy:
    USING (org_id = NULLIF(current_setting('app.current_org_id', true), '')::int).
    Postgres uses USING as WITH CHECK when no explicit WITH CHECK clause
    is defined, so every INSERT requires the app.current_org_id GUC to
    match the row's org_id. If the GUC is empty (NULLIF -> NULL -> int
    coercion -> NULL; org_id = NULL -> NULL -> fails WITH CHECK) the
    INSERT raises 'new row violates row-level security policy'.

    The orchestrator calls ensure_default_knowledge_bases (which sets
    the tenant GUC) just before this seed. In production on 2026-05-13
    that GUC was not visible to the seed's INSERTs — the e2e tenant
    ended up with zero default templates and the warning
    'default_templates_seeding_failed' fired with InsufficientPrivilegeError.

    Defense in depth: ensure_default_templates MUST set the tenant
    context itself, independent of caller order or upstream commit
    timing. This test pins that contract via call-order inspection of
    db.execute — the FIRST statement on the session must be set_config
    for app.current_org_id, BEFORE any SELECT count or INSERT.
    """
    from app.services import default_templates

    db = MagicMock()
    db.execute = AsyncMock(return_value=_count_result(0))
    db.add = MagicMock()
    db.flush = AsyncMock()
    db.rollback = AsyncMock()

    await default_templates.ensure_default_templates(org_id=99, created_by="sys", db=db)

    assert db.execute.await_count >= 2, (
        f"expected at least 2 execute() calls (set_config + COUNT); got {db.execute.await_count}"
    )
    first_call_sql = str(db.execute.call_args_list[0].args[0])
    assert "set_config" in first_call_sql and "app.current_org_id" in first_call_sql, (
        "ensure_default_templates must set app.current_org_id BEFORE any "
        f"SELECT/INSERT for defense-in-depth against caller-context leaks. "
        f"First execute() statement was: {first_call_sql!r}"
    )


@pytest.mark.asyncio
async def test_ensure_set_tenant_passes_correct_org_id():
    """The defensive set_tenant call must use the helper's org_id parameter,
    not some other value (no off-by-one regressions where the GUC is set
    to the caller's org instead of the new tenant's org).
    """
    from app.services import default_templates

    db = MagicMock()
    db.execute = AsyncMock(return_value=_count_result(0))
    db.add = MagicMock()
    db.flush = AsyncMock()
    db.rollback = AsyncMock()

    await default_templates.ensure_default_templates(org_id=777, created_by="sys", db=db)

    first_call = db.execute.call_args_list[0]
    # set_tenant binds the org_id via :org_id parameter.
    params = first_call.args[1] if len(first_call.args) > 1 else first_call.kwargs.get("parameters", {})
    assert params == {"org_id": "777"}, (
        f"set_tenant should be invoked with the helper's org_id (777), got params={params!r}"
    )
