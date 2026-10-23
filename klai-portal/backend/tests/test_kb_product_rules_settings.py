"""KB product rules through the KB settings API (knowledge-base plan, step 7)."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import ValidationError

from app.models.knowledge_bases import PortalKnowledgeBase
from tests.conftest import make_perms


def _kb() -> PortalKnowledgeBase:
    return PortalKnowledgeBase(
        id=7,
        org_id=101,
        name="Help",
        slug="help",
        description=None,
        created_at=datetime(2026, 10, 1, tzinfo=UTC),
        created_by="uid-owner",
        visibility="internal",
        docs_enabled=False,
        gitea_repo_slug=None,
        owner_type="org",
        owner_user_id=None,
        default_org_role="viewer",
        product_rules=[],
    )


@pytest.mark.asyncio
async def test_product_rules_round_trip_through_the_kb_update():
    from app.api.app_knowledge_bases import AppKBUpdateRequest, update_knowledge_base

    kb = _kb()
    db = AsyncMock()
    rules = [{"prefix": " help.example.com/orbit ", "product": "Orbit"}, {"prefix": "zephyr-", "product": "Zephyr"}]
    with (
        patch("app.api.app_knowledge_bases._load_org_or_500", AsyncMock(return_value=MagicMock())),
        patch("app.api.app_knowledge_bases._get_kb_or_404", AsyncMock(return_value=kb)),
        patch("app.api.app_knowledge_bases._require_owner", AsyncMock()),
    ):
        out = await update_knowledge_base(
            "help",
            AppKBUpdateRequest(product_rules=rules),
            perms=make_perms(org_id=101, user_id="uid-owner"),
            db=db,
        )

    expected = [{"prefix": "help.example.com/orbit", "product": "Orbit"}, {"prefix": "zephyr-", "product": "Zephyr"}]
    assert kb.product_rules == expected
    assert [rule.model_dump() for rule in out.product_rules] == expected
    db.commit.assert_awaited_once()


@pytest.mark.parametrize(
    "rules",
    [
        [{"prefix": f"p{n}", "product": "Orbit"} for n in range(101)],  # more than 100 rules
        [{"prefix": "", "product": "Orbit"}],  # an empty prefix would match everything
        [{"prefix": "orbit", "product": "x" * 65}],  # a label longer than 64 characters
        [{"prefix": "orbit", "product": "Orbit", "extra": 1}],  # an unknown field
    ],
)
def test_product_rules_are_validated_at_the_boundary(rules):
    from app.api.app_knowledge_bases import AppKBUpdateRequest

    with pytest.raises(ValidationError):
        AppKBUpdateRequest(product_rules=rules)
