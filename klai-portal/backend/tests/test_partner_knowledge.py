"""Tests for POST /partner/v1/knowledge.

SPEC-API-001 TASK-011:
- knowledge_append permission missing -> 403
- KB not in scope -> 403
- KB in scope but only read level -> 403
- Content > 10MB -> 413 (Pydantic validation)
- Happy path proxies to ingest-api and returns mapped response
"""

from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException
from helpers import FakeKB, FakeResult, make_partner_auth


def _make_auth(**kwargs):
    """make_partner_auth with knowledge_append:True as default for this module."""
    kwargs.setdefault("permissions", {"chat": True, "feedback": True, "knowledge_append": True})
    kwargs.setdefault("kb_access", {10: "read_write"})
    return make_partner_auth(**kwargs)


@pytest.mark.asyncio
async def test_knowledge_append_permission_missing():
    """knowledge_append permission not set -> 403."""
    from app.api.partner import PartnerKnowledgeRequest, append_knowledge

    auth = _make_auth(
        permissions={"chat": True, "feedback": True, "knowledge_append": False},
        kb_access={10: "read_write"},
    )
    req = PartnerKnowledgeRequest(kb_id=10, content="Some content")

    with pytest.raises(HTTPException) as exc_info:
        await append_knowledge(request=req, auth=auth, db=AsyncMock())
    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_kb_not_in_scope():
    """KB not in key's scope -> 403."""
    from app.api.partner import PartnerKnowledgeRequest, append_knowledge

    req = PartnerKnowledgeRequest(kb_id=99, content="Some content")  # 99 not in scope

    with pytest.raises(HTTPException) as exc_info:
        await append_knowledge(request=req, auth=_make_auth(), db=AsyncMock())
    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_kb_read_only_returns_403():
    """KB in scope but only read level -> 403."""
    from app.api.partner import PartnerKnowledgeRequest, append_knowledge

    req = PartnerKnowledgeRequest(kb_id=10, content="Some content")

    with pytest.raises(HTTPException) as exc_info:
        await append_knowledge(request=req, auth=_make_auth(kb_access={10: "read"}), db=AsyncMock())
    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_content_too_large_returns_validation_error():
    """Content > 10MB -> rejected by Pydantic max_length."""
    from pydantic import ValidationError

    from app.api.partner import PartnerKnowledgeRequest

    with pytest.raises(ValidationError):
        PartnerKnowledgeRequest(
            kb_id=10,
            content="x" * (10_485_760 + 1),
        )


@pytest.mark.asyncio
async def test_happy_path_proxies_to_ingest():
    """Happy path: proxies to ingest-api and returns mapped response."""
    from app.api.partner import PartnerKnowledgeRequest, append_knowledge

    fake_kb = FakeKB(id=10, name="KB Alpha", slug="kb-alpha", org_id=42)
    db = AsyncMock()
    db.execute = AsyncMock(return_value=FakeResult(rows=[fake_kb]))

    req = PartnerKnowledgeRequest(
        kb_id=10,
        title="My Document",
        content="Document content here",
        source_type="partner_api",
        content_type="text/plain",
    )

    ingest_response = {
        "artifact_id": "art-123",
        "chunks_created": 3,
        "status": "ingested",
    }

    with patch("app.services.partner_knowledge.ingest_knowledge", return_value=ingest_response) as mock_ingest:
        result = await append_knowledge(request=req, auth=_make_auth(), db=db)

    assert result["knowledge_id"] == "art-123"
    assert result["chunks_created"] == 3
    assert result["status"] == "ingested"

    mock_ingest.assert_called_once()
    call_kwargs = mock_ingest.call_args[1]
    assert call_kwargs["kb_slug"] == "kb-alpha"
    assert call_kwargs["zitadel_org_id"] == "zit-org-42"
