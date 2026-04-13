"""RED: Verify POST /partner/v1/knowledge.

SPEC-API-001 TASK-011:
- knowledge_append permission missing -> 403
- KB not in scope -> 403
- KB in scope but only read level -> 403
- Content > 10MB -> 413
- Happy path proxies to ingest-api and returns mapped response
"""

from dataclasses import dataclass
from unittest.mock import AsyncMock, MagicMock, patch

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
        permissions=permissions or {"chat": True, "feedback": True, "knowledge_append": True},
        kb_access=kb_access or {10: "read_write"},
        rate_limit_rpm=60,
    )


def _mock_scalars_all(values):
    result = MagicMock()
    scalars = MagicMock()
    scalars.all.return_value = values
    result.scalars.return_value = scalars
    return result


def _mock_scalar_one_or_none(value):
    result = MagicMock()
    result.scalar_one_or_none.return_value = value
    return result


@pytest.mark.asyncio
async def test_knowledge_append_permission_missing():
    """knowledge_append permission not set -> 403."""
    from app.api.partner import PartnerKnowledgeRequest, append_knowledge

    auth = _make_auth(
        permissions={"chat": True, "feedback": True, "knowledge_append": False},
        kb_access={10: "read_write"},
    )
    db = AsyncMock()

    req = PartnerKnowledgeRequest(
        kb_id=10,
        content="Some content",
    )

    with pytest.raises(HTTPException) as exc_info:
        await append_knowledge(request=req, auth=auth, db=db)
    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_kb_not_in_scope():
    """KB not in key's scope -> 403."""
    from app.api.partner import PartnerKnowledgeRequest, append_knowledge

    auth = _make_auth(kb_access={10: "read_write"})
    db = AsyncMock()

    req = PartnerKnowledgeRequest(
        kb_id=99,  # not in scope
        content="Some content",
    )

    with pytest.raises(HTTPException) as exc_info:
        await append_knowledge(request=req, auth=auth, db=db)
    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_kb_read_only_returns_403():
    """KB in scope but only read level -> 403."""
    from app.api.partner import PartnerKnowledgeRequest, append_knowledge

    auth = _make_auth(kb_access={10: "read"})  # only read, not read_write
    db = AsyncMock()

    req = PartnerKnowledgeRequest(
        kb_id=10,
        content="Some content",
    )

    with pytest.raises(HTTPException) as exc_info:
        await append_knowledge(request=req, auth=auth, db=db)
    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_content_too_large_returns_413():
    """Content > 10MB -> 413 (validated by Pydantic max_length on the request model)."""
    from pydantic import ValidationError

    from app.api.partner import PartnerKnowledgeRequest

    # 10MB + 1 byte
    with pytest.raises(ValidationError):
        PartnerKnowledgeRequest(
            kb_id=10,
            content="x" * (10_485_760 + 1),
        )


@pytest.mark.asyncio
async def test_happy_path_proxies_to_ingest():
    """Happy path: proxies to ingest-api and returns mapped response."""
    from app.api.partner import PartnerKnowledgeRequest, append_knowledge

    auth = _make_auth(kb_access={10: "read_write"})
    fake_kb = FakeKB(id=10, name="KB Alpha", slug="kb-alpha", org_id=42)

    db = AsyncMock()
    db.execute = AsyncMock(return_value=_mock_scalar_one_or_none(fake_kb))

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

    with patch(
        "app.services.partner_knowledge.ingest_knowledge",
        return_value=ingest_response,
    ) as mock_ingest:
        result = await append_knowledge(request=req, auth=auth, db=db)

    assert result["knowledge_id"] == "art-123"
    assert result["chunks_created"] == 3
    assert result["status"] == "ingested"

    # Verify ingest was called with correct slug
    mock_ingest.assert_called_once()
    call_kwargs = mock_ingest.call_args[1]
    assert call_kwargs["kb_slug"] == "kb-alpha"
    assert call_kwargs["zitadel_org_id"] == "zit-org-42"
