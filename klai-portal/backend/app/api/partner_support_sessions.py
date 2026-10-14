"""Partner support-session routes: create, read, append to and rate a support session."""

from __future__ import annotations

import json
import uuid
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.partner_dependencies import (
    PartnerAuthContext,
    get_partner_key,
    require_permission,
)
from app.core.database import get_db
from app.services.partner_support import (
    _mapping,
    _message_payload,
    _session_payload,
)
from app.services.widget_audit import (
    hash_audit_value,
)

router = APIRouter()


class PartnerSupportSessionRequest(BaseModel):
    integration_type: Literal["hubspot_email_support"] = "hubspot_email_support"
    hubspot_portal_id: str = Field(..., min_length=1, max_length=64)
    hubspot_ticket_id: str = Field(..., min_length=1, max_length=64)
    hubspot_user_id: str | None = Field(default=None, max_length=128)
    contact_id: str | None = Field(default=None, max_length=64)
    subject: str | None = Field(default=None, max_length=2048)
    content: str | None = Field(default=None, max_length=12000)
    metadata: dict[str, Any] | None = None


class PartnerSupportMessageRequest(BaseModel):
    role: Literal["agent", "assistant", "system"]
    content: str = Field(..., min_length=1, max_length=20000)
    draft_body: str | None = Field(default=None, max_length=20000)
    sources: list[dict[str, Any]] | None = None
    model_alias: str | None = Field(default=None, max_length=64)
    completion_id: str | None = Field(default=None, max_length=128)


class PartnerSupportFeedbackRequest(BaseModel):
    message_id: str
    rating: Literal["thumbsUp", "thumbsDown"]
    text: str | None = None
    tag: str | None = None


def _support_user_hash(auth: PartnerAuthContext, hubspot_user_id: str | None) -> str:
    """Stable private key for one support rep inside one integration session."""
    raw_user = hubspot_user_id or "unknown"
    return hash_audit_value(f"{auth.org_id}:{auth.key_id}:hubspot:{raw_user}") or "unknown"


async def _fetch_support_messages(
    db: AsyncSession,
    *,
    session_id: str,
    org_id: int,
    limit: int = 50,
) -> list[dict[str, Any]]:
    result = await db.execute(
        text(
            """
            SELECT id, role, content, draft_body, sources, model_alias,
                   completion_id, sequence, created_at
            FROM partner_support_messages
            WHERE session_id = CAST(:session_id AS uuid)
              AND org_id = :org_id
            ORDER BY sequence ASC
            LIMIT :limit
            """
        ),
        {"session_id": session_id, "org_id": org_id, "limit": limit},
    )
    rows = result.fetchall() if hasattr(result, "fetchall") else []
    return [_message_payload(row) for row in rows]


async def _get_support_session_row(
    db: AsyncSession,
    *,
    session_id: str,
    auth: PartnerAuthContext,
) -> Any:
    try:
        uuid.UUID(str(session_id))
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": {"type": "not_found", "message": "Support session not found"}},
        ) from exc

    result = await db.execute(
        text(
            """
            SELECT id, integration_type, hubspot_portal_id, hubspot_ticket_id,
                   contact_id, subject_snapshot, content_snapshot, status,
                   message_count, created_at, updated_at, last_message_at
            FROM partner_support_sessions
            WHERE id = CAST(:session_id AS uuid)
              AND org_id = :org_id
              AND partner_api_key_id = CAST(:partner_api_key_id AS uuid)
            """
        ),
        {
            "session_id": session_id,
            "org_id": auth.org_id,
            "partner_api_key_id": auth.key_id,
        },
    )
    row = result.first() if hasattr(result, "first") else result.one_or_none()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": {"type": "not_found", "message": "Support session not found"}},
        )
    return row


# ---------------------------------------------------------------------------
# Partner support sessions
# ---------------------------------------------------------------------------


@router.post("/support-sessions", status_code=201)
async def create_support_session(
    request: PartnerSupportSessionRequest,
    auth: PartnerAuthContext = Depends(get_partner_key),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Create or restore a support assistant session for an integration ticket.

    This endpoint intentionally does not call the model. It persists the
    integration session around the generic chat-completions API.
    """
    require_permission(auth, "chat")
    try:
        uuid.UUID(str(auth.key_id))
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"error": {"type": "permission_error", "message": "Support sessions require a partner API key"}},
        ) from exc

    user_hash = _support_user_hash(auth, request.hubspot_user_id)
    metadata_json = json.dumps(request.metadata or {})
    result = await db.execute(
        text(
            """
            INSERT INTO partner_support_sessions
                (org_id, partner_api_key_id, integration_type,
                 hubspot_portal_id, hubspot_ticket_id, hubspot_user_id_hash,
                 contact_id, subject_snapshot, content_snapshot,
                 session_metadata, updated_at)
            VALUES
                (:org_id, CAST(:partner_api_key_id AS uuid), :integration_type,
                 :hubspot_portal_id, :hubspot_ticket_id, :hubspot_user_id_hash,
                 :contact_id, :subject_snapshot, :content_snapshot,
                 CAST(:session_metadata AS jsonb), NOW())
            ON CONFLICT (
                org_id, partner_api_key_id, integration_type,
                hubspot_portal_id, hubspot_ticket_id, hubspot_user_id_hash
            ) DO UPDATE
                SET updated_at = NOW(),
                    contact_id = COALESCE(EXCLUDED.contact_id, partner_support_sessions.contact_id),
                    subject_snapshot = COALESCE(EXCLUDED.subject_snapshot, partner_support_sessions.subject_snapshot),
                    content_snapshot = COALESCE(EXCLUDED.content_snapshot, partner_support_sessions.content_snapshot),
                    session_metadata = COALESCE(EXCLUDED.session_metadata, partner_support_sessions.session_metadata)
            RETURNING id, integration_type, hubspot_portal_id, hubspot_ticket_id,
                      contact_id, subject_snapshot, status, message_count,
                      created_at, updated_at, last_message_at
            """
        ),
        {
            "org_id": auth.org_id,
            "partner_api_key_id": auth.key_id,
            "integration_type": request.integration_type,
            "hubspot_portal_id": request.hubspot_portal_id,
            "hubspot_ticket_id": request.hubspot_ticket_id,
            "hubspot_user_id_hash": user_hash,
            "contact_id": request.contact_id,
            "subject_snapshot": request.subject,
            "content_snapshot": request.content,
            "session_metadata": metadata_json,
        },
    )
    row = result.first() if hasattr(result, "first") else result.one_or_none()
    await db.commit()
    session_id = str(_mapping(row).get("id"))
    messages = await _fetch_support_messages(db, session_id=session_id, org_id=auth.org_id)
    return _session_payload(row, messages)


@router.get("/support-sessions/{session_id}")
async def get_support_session(
    session_id: str,
    auth: PartnerAuthContext = Depends(get_partner_key),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    require_permission(auth, "chat")
    row = await _get_support_session_row(db, session_id=session_id, auth=auth)
    messages = await _fetch_support_messages(db, session_id=session_id, org_id=auth.org_id)
    return _session_payload(row, messages)


@router.post("/support-sessions/{session_id}/messages", status_code=201)
async def append_support_message(
    session_id: str,
    request: PartnerSupportMessageRequest,
    auth: PartnerAuthContext = Depends(get_partner_key),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    require_permission(auth, "chat")
    try:
        uuid.UUID(str(session_id))
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": {"type": "not_found", "message": "Support session not found"}},
        ) from exc

    session_result = await db.execute(
        text(
            """
            SELECT id, message_count
            FROM partner_support_sessions
            WHERE id = CAST(:session_id AS uuid)
              AND org_id = :org_id
              AND partner_api_key_id = CAST(:partner_api_key_id AS uuid)
            FOR UPDATE
            """
        ),
        {
            "session_id": session_id,
            "org_id": auth.org_id,
            "partner_api_key_id": auth.key_id,
        },
    )
    session_row = session_result.first() if hasattr(session_result, "first") else session_result.one_or_none()
    if session_row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": {"type": "not_found", "message": "Support session not found"}},
        )

    sequence = int(_mapping(session_row).get("message_count") or 0)
    insert_result = await db.execute(
        text(
            """
            INSERT INTO partner_support_messages
                (session_id, org_id, role, content, draft_body, sources,
                 model_alias, completion_id, sequence)
            VALUES
                (CAST(:session_id AS uuid), :org_id, :role, :content, :draft_body,
                 CAST(:sources AS jsonb), :model_alias, :completion_id, :sequence)
            RETURNING id, role, content, draft_body, sources, model_alias,
                      completion_id, sequence, created_at
            """
        ),
        {
            "session_id": session_id,
            "org_id": auth.org_id,
            "role": request.role,
            "content": request.content,
            "draft_body": request.draft_body,
            "sources": json.dumps(request.sources or []),
            "model_alias": request.model_alias,
            "completion_id": request.completion_id,
            "sequence": sequence,
        },
    )
    row = insert_result.first() if hasattr(insert_result, "first") else insert_result.one_or_none()
    await db.execute(
        text(
            """
            UPDATE partner_support_sessions
            SET message_count = message_count + 1,
                last_message_at = NOW(),
                updated_at = NOW()
            WHERE id = CAST(:session_id AS uuid)
              AND org_id = :org_id
            """
        ),
        {"session_id": session_id, "org_id": auth.org_id},
    )
    await db.commit()
    return _message_payload(row)


@router.post("/support-sessions/{session_id}/feedback", status_code=201)
async def submit_support_feedback(
    session_id: str,
    request: PartnerSupportFeedbackRequest,
    auth: PartnerAuthContext = Depends(get_partner_key),
    db: AsyncSession = Depends(get_db),
) -> dict[str, bool]:
    require_permission(auth, "feedback")
    await _get_support_session_row(db, session_id=session_id, auth=auth)
    await db.execute(
        text(
            """
            INSERT INTO portal_feedback_events
                (org_id, conversation_id, message_id, rating, tag,
                 feedback_text, chunk_ids, correlated, occurred_at)
            VALUES
                (:org_id, :conversation_id, :message_id, :rating, :tag,
                 :feedback_text, NULL, false, NOW())
            ON CONFLICT (message_id, conversation_id) DO UPDATE
                SET rating = EXCLUDED.rating,
                    tag = EXCLUDED.tag,
                    feedback_text = EXCLUDED.feedback_text,
                    occurred_at = NOW()
            """
        ),
        {
            "org_id": auth.org_id,
            "conversation_id": session_id,
            "message_id": request.message_id,
            "rating": request.rating,
            "tag": request.tag,
            "feedback_text": request.text,
        },
    )
    await db.commit()
    return {"ok": True}
