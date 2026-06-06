"""Regression-guard for the kb_slugs_filter `[]` preservation contract.

The PATCH /api/app/account/kb-preference endpoint historically collapsed
``kb_slugs_filter: []`` to ``None`` in `_normalise`-style code. This made
the tri-state contract (None=all, []=none, [..]=subset) impossible to
express — when a user turned off the LAST org KB the client sent ``[]``,
the server stored ``None``, and the next render flipped every collection
back to "on".

This test pins the contract: an explicit empty list MUST round-trip as
``[]``, not ``None``.

Pure unit test of `patch_kb_preference`. Mocks the AsyncSession and the
auth dependency; no real DB or network involved.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from tests.conftest import make_perms


class _FakeUser:
    def __init__(self) -> None:
        self.kb_retrieval_enabled = True
        self.kb_personal_enabled = True
        self.kb_slugs_filter: list[str] | None = None
        self.kb_narrow = False
        self.kb_pref_version = 0
        self.active_template_ids: list[int] | None = None
        self.librechat_user_id = ""


class _FakeOrg:
    id = 42


def _empty_query_result() -> MagicMock:
    """Empty SQLAlchemy result — no rows, used to allow validation to pass."""
    rows: list = []
    res = MagicMock()
    res.__iter__ = lambda self: iter(rows)
    return res


@pytest.mark.asyncio
async def test_empty_kb_slugs_filter_round_trips_as_empty_list(monkeypatch):
    """``kb_slugs_filter: []`` must be stored verbatim, never collapsed to None.

    See pitfalls/process-rules.md → ``kb-slugs-filter-empty-list-collapse``
    once that lands. The frontend's ``ChatConfigBar.toggleSlug`` comment
    explicitly warns "DO NOT collapse empty to null"; this test pins the
    server contract that matches.
    """
    from app.api.app_account import KBPreferencePatch, patch_kb_preference

    fake_user = _FakeUser()
    fake_user.kb_slugs_filter = ["existing-kb"]  # non-empty before

    db = AsyncMock()
    db.commit = AsyncMock()
    # Validation step is bypassed because the empty list short-circuits
    # the validation branch (no slugs → no DB lookup needed).
    db.execute = AsyncMock(return_value=_empty_query_result())

    monkeypatch.setattr(
        "app.api.app_account._load_caller_user",
        AsyncMock(return_value=fake_user),
    )
    # Skip the fire-and-forget Redis cache invalidation in tests.
    monkeypatch.setattr(
        "app.api.app_account.invalidate_kb_cache",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        "app.api.app_account.invalidate_templates",
        AsyncMock(return_value=None),
    )

    body = KBPreferencePatch(kb_slugs_filter=[])
    result = await patch_kb_preference(body=body, perms=make_perms(role="admin", user_id="sub", org_id=42), db=db)

    # The stored value MUST be `[]`, not `None`. This is the core contract.
    assert fake_user.kb_slugs_filter == [], (
        "kb_slugs_filter=[] MUST be preserved verbatim. The earlier "
        "collapse-to-None reintroduced the very bug the frontend "
        "comment in ChatConfigBar.toggleSlug warns about: turning off "
        "the last org KB silently re-enables every collection."
    )
    # Response MUST also reflect [] so the client doesn't see None and
    # render every collection as active again.
    assert result.kb_slugs_filter == []


@pytest.mark.asyncio
async def test_null_kb_slugs_filter_remains_null(monkeypatch):
    """``None`` is the explicit "all org KBs" sentinel and must survive."""
    from app.api.app_account import KBPreferencePatch, patch_kb_preference

    fake_user = _FakeUser()
    fake_user.kb_slugs_filter = ["a", "b"]  # non-null before

    db = AsyncMock()
    db.commit = AsyncMock()
    db.execute = AsyncMock(return_value=_empty_query_result())

    monkeypatch.setattr(
        "app.api.app_account._load_caller_user",
        AsyncMock(return_value=fake_user),
    )
    monkeypatch.setattr(
        "app.api.app_account.invalidate_kb_cache",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        "app.api.app_account.invalidate_templates",
        AsyncMock(return_value=None),
    )

    body = KBPreferencePatch(kb_slugs_filter=None)
    # Force "kb_slugs_filter" into model_fields_set so the endpoint sees the
    # explicit None (not "field omitted"). Pydantic v2: pass through model_dump.
    body = KBPreferencePatch.model_validate({"kb_slugs_filter": None})
    result = await patch_kb_preference(body=body, perms=make_perms(role="admin", user_id="sub", org_id=42), db=db)

    assert fake_user.kb_slugs_filter is None
    assert result.kb_slugs_filter is None
