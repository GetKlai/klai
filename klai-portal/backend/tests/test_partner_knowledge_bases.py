"""Tests for GET /partner/v1/knowledge-bases.

SPEC-API-001 REQ-4.1:
- Returns only KBs in auth.kb_access, joined with PortalKnowledgeBase
- KBs not in scope are absent from response
- Requires chat OR knowledge_append permission
"""

from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from helpers import FakeKB, FakeResult, make_partner_auth


@pytest.mark.asyncio
async def test_list_knowledge_bases_returns_only_scoped():
    """Only KBs in auth.kb_access are returned; out-of-scope KBs absent."""
    from app.api.partner import list_knowledge_bases

    auth = make_partner_auth(kb_access={10: "read", 20: "read_write"})
    fake_kbs = [
        FakeKB(id=10, name="KB Alpha", slug="kb-alpha", org_id=42),
        FakeKB(id=20, name="KB Beta", slug="kb-beta", org_id=42),
    ]

    db = AsyncMock()
    db.execute = AsyncMock(return_value=FakeResult(rows=fake_kbs))

    result = await list_knowledge_bases(auth=auth, db=db)

    assert len(result) == 2
    kb_ids = {kb["id"] for kb in result}
    assert kb_ids == {10, 20}

    for kb in result:
        if kb["id"] == 10:
            assert kb["access_level"] == "read"
            assert kb["name"] == "KB Alpha"
            assert kb["slug"] == "kb-alpha"
        elif kb["id"] == 20:
            assert kb["access_level"] == "read_write"


@pytest.mark.asyncio
async def test_list_knowledge_bases_requires_chat_or_knowledge_append():
    """Neither chat nor knowledge_append permission -> 403."""
    from app.api.partner import list_knowledge_bases

    auth = make_partner_auth(
        permissions={"chat": False, "feedback": True, "knowledge_append": False},
        kb_access={10: "read"},
    )

    with pytest.raises(HTTPException) as exc_info:
        await list_knowledge_bases(auth=auth, db=AsyncMock())
    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_list_knowledge_bases_allows_knowledge_append_only():
    """knowledge_append permission alone is sufficient (no chat needed)."""
    from app.api.partner import list_knowledge_bases

    auth = make_partner_auth(
        permissions={"chat": False, "feedback": False, "knowledge_append": True},
        kb_access={10: "read"},
    )
    fake_kbs = [FakeKB(id=10, name="KB Alpha", slug="kb-alpha", org_id=42)]

    db = AsyncMock()
    db.execute = AsyncMock(return_value=FakeResult(rows=fake_kbs))

    result = await list_knowledge_bases(auth=auth, db=db)
    assert len(result) == 1


@pytest.mark.asyncio
async def test_list_knowledge_bases_empty_access_returns_empty():
    """Key with no KB access returns empty list without hitting the DB."""
    from app.api.partner import list_knowledge_bases

    auth = make_partner_auth(kb_access={})

    db = AsyncMock()
    result = await list_knowledge_bases(auth=auth, db=db)

    assert result == []
    db.execute.assert_not_called()
