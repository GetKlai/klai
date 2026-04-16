"""Partner API router.

SPEC-API-001: External partner endpoints under /partner/v1/*.
Authenticated via partner API keys (Bearer pk_live_...).
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from typing import Literal

import httpx
import structlog
from fastapi import APIRouter, Depends, HTTPException, Request, status
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
from app.core.database import get_db, set_tenant
from app.models.knowledge_bases import PortalKnowledgeBase
from app.models.partner_api_keys import PartnerAPIKey, PartnerApiKeyKbAccess
from app.models.portal import PortalOrg
from app.services.events import emit_event
from app.services.partner_chat import (
    chat_completion_non_streaming,
    chat_completion_streaming,
    retrieve_context,
)
from app.services.quality_scorer import schedule_quality_update
from app.services.redis_client import get_redis_pool
from app.services.retrieval_log import find_correlated_log, write_retrieval_log
from app.services.widget_auth import generate_session_token, origin_allowed

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

    # Tenant is set by get_partner_key after key lookup (connection pinned by get_db)
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
    ]


# ---------------------------------------------------------------------------
# POST /partner/v1/chat/completions  (TASK-008 + TASK-009)
# ---------------------------------------------------------------------------


async def _resolve_kb_slugs(kb_ids: list[int], org_id: int, db: AsyncSession) -> list[str]:
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
            detail={
                "error": {
                    "type": "invalid_request",
                    "message": f"Model must be one of: {', '.join(sorted(_ALLOWED_MODELS))}",
                }
            },
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


# ---------------------------------------------------------------------------
# GET /partner/v1/widget-config  (SPEC-WIDGET-001 Task 2)
# Public endpoint — NO auth dependency
# ---------------------------------------------------------------------------


@router.get("/widget-config")
async def widget_config(
    id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Return widget bootstrap configuration and a short-lived session token.

    # @MX:WARN: [AUTO] Public endpoint — no authentication required
    # @MX:REASON: Origin validated via origin_allowed(); token TTL 1h; no sensitive data returned
    # @MX:SPEC: SPEC-WIDGET-001 REQ-2

    SPEC-WIDGET-001 REQ-2: Public endpoint, no API key required.
    - Looks up widget by widget_id (id param) with integration_type='widget'
    - Validates Origin header against allowed_origins (fail-closed)
    - Generates HS256 JWT session token (1 hour TTL)
    - Returns CORS headers for matched origin (never *)

    Error codes:
        404 - widget_id not found or integration_type != 'widget'
        403 - missing or disallowed Origin
        503 - WIDGET_JWT_SECRET not configured
    """
    # Check JWT secret is configured
    if not settings.widget_jwt_secret:
        logger.warning("widget_jwt_secret_not_configured")
        return Response(
            content='{"detail":"Widget authentication not configured"}',
            status_code=503,
            media_type="application/json",
        )

    # Look up widget key by widget_id and integration_type='widget'
    result = await db.execute(
        select(PartnerAPIKey).where(
            PartnerAPIKey.widget_id == id,
            PartnerAPIKey.integration_type == "widget",
            PartnerAPIKey.active.is_(True),
        )
    )
    key_row = result.scalar_one_or_none()

    if key_row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Widget not found")

    # Validate Origin header
    origin = request.headers.get("origin", "")
    widget_config_data = key_row.widget_config or {}
    allowed_origins = widget_config_data.get("allowed_origins", [])

    if not origin or not origin_allowed(origin, allowed_origins):
        return Response(
            content='{"detail":"Origin not allowed"}',
            status_code=403,
            media_type="application/json",
        )

    # Load org and set tenant BEFORE KB access query (ensures RLS context is active)
    org_result = await db.execute(select(PortalOrg).where(PortalOrg.id == key_row.org_id))
    org = org_result.scalar_one_or_none()
    if org is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Widget not found")
    await set_tenant(db, org.id)

    # Load KB access for this key (after RLS tenant is set)
    kb_result = await db.execute(
        select(PartnerApiKeyKbAccess).where(PartnerApiKeyKbAccess.partner_api_key_id == key_row.id)
    )
    kb_rows = kb_result.scalars().all()
    kb_ids = [row.kb_id for row in kb_rows]

    # Generate session token
    session_token = generate_session_token(
        wgt_id=key_row.widget_id or id,
        org_id=key_row.org_id,
        kb_ids=kb_ids,
        secret=settings.widget_jwt_secret,
    )

    expires_at = datetime.now(UTC) + timedelta(hours=1)

    body = {
        "title": widget_config_data.get("title", ""),
        "welcome_message": widget_config_data.get("welcome_message", ""),
        "css_variables": widget_config_data.get("css_variables", {}),
        "chat_endpoint": "/partner/v1/chat/completions",
        "session_token": session_token,
        "session_expires_at": expires_at.isoformat(),
    }

    headers = {
        "Access-Control-Allow-Origin": origin,
        "Access-Control-Allow-Credentials": "true",
        "Vary": "Origin",
    }

    return Response(
        content=json.dumps(body),
        status_code=200,
        media_type="application/json",
        headers=headers,
    )


@router.options("/widget-config")
async def widget_config_preflight(
    id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Handle OPTIONS preflight for widget-config CORS.

    SPEC-WIDGET-001: Return 204 with CORS headers for valid origins.
    Returns CORS headers without verifying JWT secret (preflight only).
    """
    result = await db.execute(
        select(PartnerAPIKey).where(
            PartnerAPIKey.widget_id == id,
            PartnerAPIKey.integration_type == "widget",
            PartnerAPIKey.active.is_(True),
        )
    )
    key_row = result.scalar_one_or_none()

    if key_row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Widget not found")

    origin = request.headers.get("origin", "")
    widget_config_data = key_row.widget_config or {}
    allowed_origins = widget_config_data.get("allowed_origins", [])

    if not origin or not origin_allowed(origin, allowed_origins):
        return Response(status_code=204)

    headers = {
        "Access-Control-Allow-Origin": origin,
        "Access-Control-Allow-Methods": "GET, OPTIONS",
        "Access-Control-Allow-Headers": "Content-Type, Authorization",
        "Access-Control-Allow-Credentials": "true",
        "Access-Control-Max-Age": "86400",
        "Vary": "Origin",
    }

    return Response(status_code=204, headers=headers)
