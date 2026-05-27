"""Tests for partner support-session persistence endpoints."""

from dataclasses import dataclass
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from helpers import make_partner_auth


@dataclass
class FakeDbResult:
    rows: list

    def first(self):
        return self.rows[0] if self.rows else None

    def one_or_none(self):
        return self.first()

    def fetchall(self):
        return self.rows


def _db_with_results(results: list[FakeDbResult]):
    db = AsyncMock()
    calls = 0

    async def _execute(*args, **kwargs):
        nonlocal calls
        index = min(calls, len(results) - 1)
        calls += 1
        return results[index]

    db.execute = AsyncMock(side_effect=_execute)
    db.commit = AsyncMock()
    return db


def _auth():
    auth = make_partner_auth()
    auth.key_id = "22222222-2222-2222-2222-222222222222"
    return auth


@pytest.mark.asyncio
async def test_create_support_session_upserts_and_returns_messages():
    from app.api.partner import PartnerSupportSessionRequest, create_support_session

    session_id = "11111111-1111-1111-1111-111111111111"
    db = _db_with_results(
        [
            FakeDbResult(
                [
                    {
                        "id": session_id,
                        "integration_type": "hubspot_email_support",
                        "hubspot_portal_id": "5604529",
                        "hubspot_ticket_id": "123",
                        "contact_id": "456",
                        "subject_snapshot": "Vraag",
                        "status": "active",
                        "message_count": 0,
                    }
                ]
            ),
            FakeDbResult([]),
        ]
    )

    result = await create_support_session(
        request=PartnerSupportSessionRequest(
            hubspot_portal_id="5604529",
            hubspot_ticket_id="123",
            hubspot_user_id="42",
            contact_id="456",
            subject="Vraag",
            content="Tickettekst",
        ),
        auth=_auth(),
        db=db,
    )

    assert result["id"] == session_id
    assert result["messages"] == []
    db.commit.assert_called_once()


@pytest.mark.asyncio
async def test_append_support_message_requires_existing_session():
    from app.api.partner import PartnerSupportMessageRequest, append_support_message

    db = _db_with_results([FakeDbResult([])])

    with pytest.raises(HTTPException) as exc:
        await append_support_message(
            session_id="11111111-1111-1111-1111-111111111111",
            request=PartnerSupportMessageRequest(role="agent", content="Korter graag"),
            auth=_auth(),
            db=db,
        )

    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_append_support_message_persists_next_sequence():
    from app.api.partner import PartnerSupportMessageRequest, append_support_message

    session_id = "11111111-1111-1111-1111-111111111111"
    db = _db_with_results(
        [
            FakeDbResult([{"id": session_id, "message_count": 2}]),
            FakeDbResult(
                [
                    {
                        "id": 99,
                        "role": "assistant",
                        "content": "Concept",
                        "draft_body": "Concept",
                        "sources": [],
                        "model_alias": "klai-primary",
                        "completion_id": "chatcmpl-1",
                        "sequence": 2,
                    }
                ]
            ),
            FakeDbResult([]),
        ]
    )

    result = await append_support_message(
        session_id=session_id,
        request=PartnerSupportMessageRequest(
            role="assistant",
            content="Concept",
            draft_body="Concept",
            sources=[],
            model_alias="klai-primary",
            completion_id="chatcmpl-1",
        ),
        auth=_auth(),
        db=db,
    )

    assert result["id"] == "99"
    assert result["sequence"] == 2
    db.commit.assert_called_once()
