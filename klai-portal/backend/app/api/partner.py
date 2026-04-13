"""Partner API router.

SPEC-API-001: External partner endpoints under /partner/v1/*.
Authenticated via partner API keys (Bearer pk_live_...).
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Literal

import httpx
import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import Response, StreamingResponse

from app.api.partner_dependencies import (
    PartnerAuthContext,
    get_partner_key,
    require_permission,
    validate_kb_access,
)
from app.core.config import settings
from app.core.database import get_db
from app.models.knowledge_bases import PortalKnowledgeBase
from app.services.events import emit_event
from app.services.partner_chat import (
    chat_completion_non_streaming,
    chat_completion_streaming,
    retrieve_context,
)
from app.services.quality_scorer import schedule_quality_update
from app.services.redis_client import get_redis_pool
from app.services.retrieval_log import find_correlated_log, write_retrieval_log

logger = structlog.get_logger()

# Hold references to fire-and-forget tasks to prevent GC (same pattern as partner_dependencies)
_pending: set[asyncio.Task] = set()  # type: ignore[type-arg]

router = APIRouter(prefix="/partner/v1", tags=["Partner API"])

_ALLOWED_MODELS = {"klai-primary", "klai-fast"}


# ---------------------------------------------------------------------------
# Pydantic request models
# ---------------------------------------------------------------------------


class ChatCompletionsRequest(BaseModel):
    messages: list[dict] = Field(..., min_length=1)
    model: str = "klai-primary"
    stream: bool = True
    temperature: float = 0.7
    knowledge_base_ids: list[int] | None = None


class PartnerFeedbackRequest(BaseModel):
    message_id: str
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
# GET /partner/v1/knowledge-bases
# ---------------------------------------------------------------------------


@router.get("/knowledge-bases")
async def list_knowledge_bases(
    auth: PartnerAuthContext = Depends(get_partner_key),
    db: AsyncSession = Depends(get_db),
) -> list[dict]:
    """List knowledge bases the partner key has access to.

    REQ-4.1: Requires chat OR knowledge_append permission.
    Returns id, name, slug, access_level for each accessible KB.
    """
    # Permission: chat OR knowledge_append
    if not auth.permissions.get("chat") and not auth.permissions.get("knowledge_append"):
        require_permission(auth, "chat")  # will raise 403

    if not auth.kb_access:
        return []

    kb_ids = list(auth.kb_access.keys())

    result = await db.execute(
        select(PortalKnowledgeBase).where(
            PortalKnowledgeBase.id.in_(kb_ids),
            PortalKnowledgeBase.org_id == auth.org_id,
        )
    )
    kbs = result.scalars().all()

    return [
        {
            "id": kb.id,
            "name": kb.name,
            "slug": kb.slug,
            "access_level": auth.kb_access[kb.id],
        }
        for kb in kbs
        if kb.id in auth.kb_access
    ]


# ---------------------------------------------------------------------------
# POST /partner/v1/chat/completions  (TASK-008 + TASK-009)
# ---------------------------------------------------------------------------


async def _resolve_kb_slugs(
    kb_ids: list[int], org_id: int, db: AsyncSession
) -> list[str]:
    """Translate integer KB IDs to slug strings via DB lookup."""
    result = await db.execute(
        select(PortalKnowledgeBase).where(
            PortalKnowledgeBase.id.in_(kb_ids),
            PortalKnowledgeBase.org_id == org_id,
        )
    )
    kbs = result.scalars().all()
    return [kb.slug for kb in kbs]


@router.post("/chat/completions")
async def chat_completions(
    request: ChatCompletionsRequest,
    auth: PartnerAuthContext = Depends(get_partner_key),
    db: AsyncSession = Depends(get_db),
):
    """Chat completions with RAG context from knowledge bases.

    TASK-008: Non-streaming path.
    TASK-009: Streaming SSE path.
    """
    # 1. Permission check
    require_permission(auth, "chat")

    # 2. Model validation
    if request.model not in _ALLOWED_MODELS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": {"type": "invalid_request", "message": f"Model must be one of: {', '.join(sorted(_ALLOWED_MODELS))}"}},
        )

    # 3. Messages validation: at least one user message
    has_user_msg = any(m.get("role") == "user" for m in request.messages)
    if not has_user_msg:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": {"type": "invalid_request", "message": "Messages must contain at least one user message"}},
        )

    # 4. Validate KB access
    kb_ids = validate_kb_access(auth, request.knowledge_base_ids)

    # 5. Translate kb_ids -> kb_slugs
    kb_slugs = await _resolve_kb_slugs(kb_ids, auth.org_id, db)

    # 6. Retrieve context
    try:
        chunks, system_prompt = await retrieve_context(
            org_id=auth.org_id,
            zitadel_org_id=auth.zitadel_org_id,
            kb_slugs=kb_slugs,
            messages=request.messages,
            settings=settings,
        )
    except (httpx.TimeoutException, httpx.ReadTimeout) as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={"error": {"type": "upstream_error", "message": "Retrieval service timeout"}},
        ) from exc

    # 7. Fire retrieval log async
    chunk_ids = [c.get("chunk_id", "") for c in chunks if c.get("chunk_id")]
    reranker_scores = [c.get("reranker_score", 0.0) for c in chunks if c.get("reranker_score") is not None]

    task = asyncio.create_task(
        write_retrieval_log(
            org_id=auth.org_id,
            user_id=f"partner:{auth.key_id}",
            chunk_ids=chunk_ids,
            reranker_scores=reranker_scores,
            query_resolved="",
            embedding_model_version="",
            retrieved_at=datetime.now(UTC),
        )
    )
    _pending.add(task)
    task.add_done_callback(_pending.discard)

    # 8. Streaming or non-streaming
    if request.stream:
        streaming_gen = chat_completion_streaming(
            messages=request.messages,
            model=request.model,
            temperature=request.temperature,
            system_prompt=system_prompt,
            settings=settings,
        )
        return StreamingResponse(
            content=streaming_gen,
            media_type="text/event-stream",
        )

    # Non-streaming
    result = await chat_completion_non_streaming(
        messages=request.messages,
        model=request.model,
        temperature=request.temperature,
        system_prompt=system_prompt,
        settings=settings,
    )
    return result


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
    idem_key = f"partner_fb:{request.message_id}"
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
            (org_id, message_id, rating, tag, feedback_text,
             chunk_ids, correlated, occurred_at)
            VALUES (:org_id, :message_id, :rating, :tag,
                    :feedback_text, :chunk_ids, :correlated, NOW())
        """),
        {
            "org_id": auth.org_id,
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
