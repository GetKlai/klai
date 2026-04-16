"""RED: Verify Partner API router skeleton + GET /partner/v1/knowledge-bases.

SPEC-API-001 REQ-4.1:
- Returns only KBs in auth.kb_access, joined with PortalKnowledgeBase
- KBs not in scope are absent from response
- Requires chat OR knowledge_append permission
"""

from dataclasses import dataclass
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException


@dataclass
class FakeKB:
    """Mimics a PortalKnowledgeBase row."""

    id: int
    name: str
    slug: str
    org_id: int


def _make_auth(permissions: dict | None = None, kb_access: dict | None = None):
    """Create a PartnerAuthContext for testing."""
    from app.api.partner_dependencies import PartnerAuthContext

    return PartnerAuthContext(
        key_id="key-uuid-1",
        org_id=42,
        zitadel_org_id="zit-org-42",
        permissions=permissions or {"chat": True, "feedback": True, "knowledge_append": False},
        kb_access=kb_access or {10: "read", 20: "read_write"},
        rate_limit_rpm=60,
    )


def _mock_scalars_all(values):
    result = MagicMock()
    scalars = MagicMock()
    scalars.all.return_value = values
    result.scalars.return_value = scalars
    return result


@pytest.mark.asyncio
async def test_list_knowledge_bases_returns_only_scoped():
    """Only KBs in auth.kb_access are returned; out-of-scope KBs absent."""
    from app.api.partner import list_knowledge_bases

    auth = _make_auth(kb_access={10: "read", 20: "read_write"})

    # DB has 3 KBs in org, but key only has access to 2
    fake_kbs = [
        FakeKB(id=10, name="KB Alpha", slug="kb-alpha", org_id=42),
        FakeKB(id=20, name="KB Beta", slug="kb-beta", org_id=42),
    ]

    db = AsyncMock()
    db.execute = AsyncMock(return_value=_mock_scalars_all(fake_kbs))

    result = await list_knowledge_bases(auth=auth, db=db)

    assert len(result) == 2
    kb_ids = {kb["id"] for kb in result}
    assert kb_ids == {10, 20}

    # Check access_level is included
    for kb in result:
        if kb["id"] == 10:
            assert kb["access_level"] == "read"
            assert kb["name"] == "KB Alpha"
            assert kb["slug"] == "kb-alpha"
        elif kb["id"] == 20:
            assert kb["access_level"] == "read_write"


@pytest.mark.asyncio
async def test_list_knowledge_bases_requires_chat_or_knowledge_append():
    """Permission check: needs chat OR knowledge_append."""
    from app.api.partner import list_knowledge_bases

    # Neither chat nor knowledge_append -> should raise 403
    auth = _make_auth(
        permissions={"chat": False, "feedback": True, "knowledge_append": False},
        kb_access={10: "read"},
    )

    db = AsyncMock()

    with pytest.raises(HTTPException) as exc_info:
        await list_knowledge_bases(auth=auth, db=db)
    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_list_knowledge_bases_allows_knowledge_append_permission():
    """knowledge_append permission alone is sufficient."""
    from app.api.partner import list_knowledge_bases

    auth = _make_auth(
        permissions={"chat": False, "feedback": False, "knowledge_append": True},
        kb_access={10: "read"},
    )

    fake_kbs = [FakeKB(id=10, name="KB Alpha", slug="kb-alpha", org_id=42)]

    db = AsyncMock()
    db.execute = AsyncMock(return_value=_mock_scalars_all(fake_kbs))

    result = await list_knowledge_bases(auth=auth, db=db)
    assert len(result) == 1


@pytest.mark.asyncio
async def test_list_knowledge_bases_empty_access():
    """Key with no KB access returns empty list."""
    from app.api.partner import list_knowledge_bases

    auth = _make_auth(kb_access={})

    db = AsyncMock()
    db.execute = AsyncMock(return_value=_mock_scalars_all([]))

    result = await list_knowledge_bases(auth=auth, db=db)
    assert result == []
