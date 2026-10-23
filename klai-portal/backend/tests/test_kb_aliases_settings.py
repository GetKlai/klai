"""KB aliases through the KB settings API (knowledge-base plan, step 6)."""

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
        aliases=[],
    )


@pytest.mark.asyncio
async def test_aliases_round_trip_through_the_kb_update():
    from app.api.app_knowledge_bases import AppKBUpdateRequest, update_knowledge_base

    kb = _kb()
    db = AsyncMock()
    with (
        patch("app.api.app_knowledge_bases._load_org_or_500", AsyncMock(return_value=MagicMock())),
        patch("app.api.app_knowledge_bases._get_kb_or_404", AsyncMock(return_value=kb)),
        patch("app.api.app_knowledge_bases._require_owner", AsyncMock()),
    ):
        out = await update_knowledge_base(
            "help",
            AppKBUpdateRequest(aliases=[[" Web client ", "Windows app"], ["Fax", "e-fax"]]),
            perms=make_perms(org_id=101, user_id="uid-owner"),
            db=db,
        )

    assert kb.aliases == [["Web client", "Windows app"], ["Fax", "e-fax"]]
    assert out.aliases == [["Web client", "Windows app"], ["Fax", "e-fax"]]
    db.commit.assert_awaited_once()


@pytest.mark.parametrize(
    "aliases",
    [
        [["a", "b"]] * 51,  # more than 50 groups
        [["Web client", "x" * 65]],  # a term longer than 64 characters
        [["Web client", "Windows app"], ["windows APP", "desktop app"]],  # a term twice
        [["Web client"]],  # a group of one says nothing
    ],
)
def test_alias_list_is_validated_at_the_boundary(aliases):
    from app.api.app_knowledge_bases import AppKBUpdateRequest

    with pytest.raises(ValidationError):
        AppKBUpdateRequest(aliases=aliases)
