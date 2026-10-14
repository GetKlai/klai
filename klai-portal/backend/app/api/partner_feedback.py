"""Partner and widget feedback routes, and the /knowledge append route."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import Response

from app.api.partner_dependencies import (
    PartnerAuthContext,
    get_partner_key,
    require_permission,
    validate_kb_access,
)
from app.api.widget_public import WIDGET_PUBLIC_CORS
from app.core.config import settings
from app.core.database import get_db
from app.models.knowledge_bases import PortalKnowledgeBase
from app.services.events import emit_event
from app.services.partner_rate_limit import check_rate_limit
from app.services.quality_scorer import schedule_quality_update
from app.services.redis_client import get_redis_pool
from app.services.retrieval_log import find_correlated_log

logger = structlog.get_logger()

router = APIRouter()


class PartnerFeedbackRequest(BaseModel):
    message_id: str
    conversation_id: str | None = None
    rating: Literal["thumbsUp", "thumbsDown"]
    text: str | None = None
    tag: str | None = None


class PartnerKnowledgeRequest(BaseModel):
    kb_id: int
    title: str | None = None
    content: str = Field(..., max_length=10_485_760)
    source_type: str = "partner_api"
    content_type: str = "text/plain"


# ---------------------------------------------------------------------------
# POST /partner/v1/feedback  (TASK-010)
# ---------------------------------------------------------------------------


@router.post("/feedback", status_code=201)
async def submit_feedback(
    request: PartnerFeedbackRequest,
    auth: PartnerAuthContext = Depends(get_partner_key),
    db: AsyncSession = Depends(get_db),
):
    """Process feedback from partner API.

    Follows the pattern from app/api/internal.py:post_kb_feedback
    but adapted for partner auth (no librechat_tenant_id).
    """
    # 1. Permission check
    require_permission(auth, "feedback")

    # 2. Idempotency check
    redis_pool = await get_redis_pool()
    conversation_id = request.conversation_id or f"partner:{auth.key_id}"
    idem_key = f"partner_fb:{conversation_id}:{request.message_id}"
    if redis_pool:
        existing = await redis_pool.get(idem_key)
        if existing:
            return Response(status_code=200)

    # 3. Time-window correlation with retrieval log
    correlated_log = await find_correlated_log(
        org_id=auth.org_id,
        user_id=f"partner:{auth.key_id}",
        message_created_at=datetime.now(UTC),
    )

    chunk_ids = correlated_log["chunk_ids"] if correlated_log else []
    correlated = correlated_log is not None

    # 4. Insert feedback event via raw SQL (RLS table)
    await db.execute(
        text("""
            INSERT INTO portal_feedback_events
            (org_id, conversation_id, message_id, rating, tag, feedback_text,
             chunk_ids, correlated, occurred_at)
            VALUES (:org_id, :conversation_id, :message_id, :rating, :tag,
                    :feedback_text, :chunk_ids, :correlated, NOW())
        """),
        {
            "org_id": auth.org_id,
            "conversation_id": conversation_id,
            "message_id": request.message_id,
            "rating": request.rating,
            "tag": request.tag,
            "feedback_text": request.text,
            "chunk_ids": chunk_ids or None,
            "correlated": correlated,
        },
    )
    await db.commit()

    # 5. Set idempotency key
    if redis_pool:
        try:
            await redis_pool.set(idem_key, "1", ex=3600)
        except Exception:
            logger.warning("partner_feedback_idem_key_set_failed", exc_info=True)

    # 6. Schedule Qdrant quality update if correlated
    if correlated and chunk_ids:
        schedule_quality_update(chunk_ids, request.rating, auth.org_id)

    # 7. Emit product event
    emit_event(
        "knowledge.feedback",
        org_id=auth.org_id,
        properties={
            "rating": request.rating,
            "correlated": correlated,
            "chunk_count": len(chunk_ids),
            "source": "partner_api",
        },
    )

    return {"ok": True}


# ---------------------------------------------------------------------------
# POST /partner/v1/widget/feedback  (widget thumbs up/down)
# ---------------------------------------------------------------------------

# Separate sliding window from the per-widget 60/min auth bucket in
# _auth_via_session_token — feedback must not be able to starve the chat
# path, and chat volume must not silently absorb (or block) ratings.
_WIDGET_FEEDBACK_RATE_LIMIT_PER_MINUTE = 12


class WidgetFeedbackRequest(BaseModel):
    # Same shape as ChatCompletionsRequest.widget_turn_id — the client-
    # generated identifier stored on the assistant widget_messages row.
    turn_id: str = Field(..., pattern=r"^[0-9a-f]{16,64}$")
    # None withdraws the rating again (second click on the active button).
    rating: Literal["thumbsUp", "thumbsDown"] | None = None


@router.post("/widget/feedback", openapi_extra=WIDGET_PUBLIC_CORS)
async def submit_widget_feedback(
    request: WidgetFeedbackRequest,
    auth: PartnerAuthContext = Depends(get_partner_key),
    db: AsyncSession = Depends(get_db),
):
    """Record a visitor thumbs rating on one assistant answer of the
    caller's own widget conversation.

    Why a dedicated endpoint instead of opening POST /partner/v1/feedback
    to widget tokens: that endpoint is built for authenticated partner
    integrations — it correlates retrieval logs by time window, inserts
    into portal_feedback_events, schedules Qdrant quality updates, and
    emits product events. Flipping ``feedback: True`` in the widget
    session permissions would expose that whole side-effect pipeline to
    every anonymous browser holding a widget JWT — a new abuse surface —
    and its addressing model (caller-supplied free-form message_id) does
    not match widget audit rows at all. Partner API-key behaviour on
    /partner/v1/feedback stays byte-identical. Here the mutation is one
    scoped UPDATE on widget_messages, addressed by the turn id the chat
    request already carried.

    Tenant scoping: conversation + org are taken from the verified JWT
    (session_key + wgt_id), never from the request body. A turn_id from
    another visitor's conversation or another org simply matches no row
    → 404 (existence-non-disclosing, and RLS Cat-D on widget_messages /
    widget_conversations is the second layer).
    """
    # 1. Widget sessions only. Partner keys are rejected here explicitly:
    #    auth.permissions cannot express "widget-only" (a partner key row
    #    with feedback=False and a widget JWT both lack the permission),
    #    and widget JWTs always carry a session_key while key auth never does.
    if not str(auth.key_id).startswith("wgt_") or not auth.session_key:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"error": {"type": "permission_error", "message": "Insufficient permissions"}},
        )

    # 2. Per-session sliding-window rate limit (app/services/partner_rate_limit).
    redis_pool = await get_redis_pool()
    if redis_pool is not None:
        allowed, retry_after = await check_rate_limit(
            redis_pool,
            f"widget_feedback:{auth.key_id}:{auth.session_key}",
            limit_per_minute=_WIDGET_FEEDBACK_RATE_LIMIT_PER_MINUTE,
        )
        if not allowed:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail={"error": {"type": "rate_limit_error", "message": "Rate limit exceeded"}},
                headers={"Retry-After": str(retry_after)},
            )

    # 3. Stamp the rating on the assistant row of THIS session's conversation.
    #    Raw text() SQL follows the widget_audit convention (RLS-guarded
    #    tables avoid ORM implicit RETURNING). rowcount==0 means the turn id
    #    matched no assistant row in the caller's own conversation/org —
    #    including foreign turn ids replayed from another tenant.
    result = await db.execute(
        text(
            """
            UPDATE widget_messages AS m
               SET rating = :rating
              FROM widget_conversations AS c
             WHERE c.id = m.conversation_id
               AND m.role = 'assistant'
               AND m.turn_id = :turn_id
               AND m.org_id = :org_id
               AND c.org_id = :org_id
               AND c.session_key = :session_key
               AND c.widget_id = (
                   SELECT w.id FROM widgets AS w
                    WHERE w.widget_id = :wgt_id AND w.org_id = :org_id
               )
            """
        ),
        {
            "rating": request.rating,
            "turn_id": request.turn_id,
            "org_id": auth.org_id,
            "session_key": auth.session_key,
            "wgt_id": str(auth.key_id),
        },
    )
    if result.rowcount == 0:  # type: ignore[attr-defined]
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": {"type": "not_found", "message": "Feedback target not found"}},
        )
    await db.commit()

    return {"ok": True, "rating": request.rating}


# ---------------------------------------------------------------------------
# POST /partner/v1/knowledge  (TASK-011)
# ---------------------------------------------------------------------------


@router.post("/knowledge", status_code=201)
async def append_knowledge(
    request: PartnerKnowledgeRequest,
    auth: PartnerAuthContext = Depends(get_partner_key),
    db: AsyncSession = Depends(get_db),
):
    """Append content to a knowledge base via ingest-api.

    Append-only: no update or delete operations.
    """
    from app.services.partner_knowledge import ingest_knowledge

    # 1. Permission check
    require_permission(auth, "knowledge_append")

    # 2. Validate KB access with read_write level
    validate_kb_access(auth, [request.kb_id], required_level="read_write")

    # 3. Translate kb_id -> kb_slug
    result = await db.execute(
        select(PortalKnowledgeBase).where(
            PortalKnowledgeBase.id == request.kb_id,
            PortalKnowledgeBase.org_id == auth.org_id,
        )
    )
    kb = result.scalar_one_or_none()
    if kb is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"error": {"type": "permission_error", "message": "Insufficient permissions"}},
        )

    # 4. Call ingest service
    ingest_result = await ingest_knowledge(
        org_id=auth.org_id,
        zitadel_org_id=auth.zitadel_org_id,
        kb_slug=kb.slug,
        title=request.title,
        content=request.content,
        source_type=request.source_type,
        content_type=request.content_type,
        settings=settings,
    )

    # 5. Return mapped response
    return {
        "knowledge_id": ingest_result.get("artifact_id"),
        "chunks_created": ingest_result.get("chunks_created"),
        "status": ingest_result.get("status", "ingested"),
    }
