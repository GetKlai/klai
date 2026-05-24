"""Partner API router.

SPEC-API-001: External partner endpoints under /partner/v1/*.
Authenticated via partner API keys (Bearer pk_live_...).
"""

from __future__ import annotations

import asyncio
import json
import re
import uuid
from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

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
from app.core.permissions import assert_platform_unlocked
from app.models.knowledge_bases import PortalKnowledgeBase
from app.models.portal import PortalOrg
from app.models.widgets import Widget, WidgetKbAccess
from app.services.events import emit_event
from app.services.partner_chat import (
    _citation_source_metadata_from_chunks,
    _citation_source_urls_from_chunks,
    _source_urls_from_chunks,
    chat_completion_non_streaming,
    chat_completion_streaming,
    retrieve_context,
)
from app.services.partner_rate_limit import check_rate_limit
from app.services.quality_scorer import schedule_quality_update
from app.services.redis_client import get_redis_pool
from app.services.retrieval_log import find_correlated_log, write_retrieval_log
from app.services.widget_audit import (
    hash_audit_value,
    record_widget_turn,
    session_key_from_token,
)
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


async def _widget_system_prompt(auth: PartnerAuthContext, db: AsyncSession) -> str | None:
    """Return the admin-configured widget prompt for widget JWT calls.

    When ``widget_config.template_slug`` is set, the linked Template's
    ``prompt_text`` is appended after the widget-local system_prompt.
    Lets admins reuse named prompts across widgets without copying.
    """
    if not str(auth.key_id).startswith("wgt_"):
        return None
    result = await db.execute(
        select(Widget.widget_config).where(
            Widget.widget_id == auth.key_id,
            Widget.org_id == auth.org_id,
        )
    )
    config = result.scalar_one_or_none() or {}
    if not isinstance(config, dict):
        return None
    base = config.get("system_prompt")
    base_str = base.strip() if isinstance(base, str) else ""

    template_slug = config.get("template_slug")
    template_text = ""
    if isinstance(template_slug, str) and template_slug:
        # Local import to avoid a top-level dependency between partner.py
        # (widget runtime) and the templates module (admin domain).
        from app.models.templates import PortalTemplate

        t_result = await db.execute(
            select(PortalTemplate.prompt_text).where(
                PortalTemplate.slug == template_slug,
                PortalTemplate.org_id == auth.org_id,
            )
        )
        t_text = t_result.scalar_one_or_none()
        if isinstance(t_text, str):
            template_text = t_text.strip()

    parts = [p for p in (base_str, template_text) if p]
    return "\n\n".join(parts) if parts else None


async def _audit_streaming_wrapper(
    inner: AsyncGenerator[bytes],
    *,
    widget_id: str,
    org_id: int,
    session_key: str,
    loaded_origin: str | None = None,
) -> AsyncGenerator[bytes]:
    """Tee the SSE stream, capture composed text + sources, log the
    assistant turn once the generator completes.

    Parsing is best-effort — anything we can't decode is just yielded
    through. The audit never blocks or alters the user's response.
    """
    composed_text: list[str] = []
    composed_sources: list[dict] = []
    try:
        async for chunk in inner:
            try:
                text_part, src_part = _parse_audit_sse_chunk(chunk)
                if text_part:
                    composed_text.append(text_part)
                if src_part:
                    composed_sources = src_part
            except Exception:
                # Best-effort audit parsing — never break the user's chat.
                logger.debug("widget_audit_sse_parse_skipped", exc_info=True)
            yield chunk
    finally:
        final_text = "".join(composed_text).strip()
        if final_text:
            task = asyncio.create_task(
                record_widget_turn(
                    widget_id=widget_id,
                    org_id=org_id,
                    session_key=session_key,
                    role="assistant",
                    content=final_text,
                    sources=composed_sources or None,
                    loaded_origin=loaded_origin,
                )
            )
            _pending.add(task)
            task.add_done_callback(_pending.discard)


def _parse_audit_sse_chunk(chunk: bytes) -> tuple[str | None, list[dict] | None]:
    """Pull ``delta.content`` text and ``delta.sources`` list out of one
    SSE ``data: …\\n\\n`` block. Returns (text, sources) — either side
    may be None when the chunk doesn't carry that field."""
    text_part: str | None = None
    src_part: list[dict] | None = None
    for raw in chunk.split(b"\n"):
        if not raw.startswith(b"data: "):
            continue
        payload = raw[6:].strip()
        if payload in (b"", b"[DONE]"):
            continue
        try:
            event = json.loads(payload)
        except json.JSONDecodeError:
            continue
        delta = (event.get("choices") or [{}])[0].get("delta") or {}
        if isinstance(delta.get("content"), str):
            text_part = (text_part or "") + delta["content"]
        srcs = delta.get("sources")
        if isinstance(srcs, list):
            src_part = [s for s in srcs if isinstance(s, dict)]
    return text_part, src_part


def _extract_assistant_text_and_sources(
    result: Any,
) -> tuple[str, list[dict] | None]:
    """Pull the assistant message + sources out of a non-streaming
    chat-completions result (a dict or Pydantic-shaped object)."""
    payload: dict[str, Any]
    if hasattr(result, "model_dump"):
        payload = result.model_dump()
    elif isinstance(result, dict):
        payload = result
    else:
        return "", None
    choices = payload.get("choices") or []
    if not choices:
        return "", None
    message = choices[0].get("message") or {}
    content = str(message.get("content") or "")
    sources_raw = message.get("sources")
    sources = [s for s in sources_raw if isinstance(s, dict)] if isinstance(sources_raw, list) else None
    return content, sources


@router.post("/chat/completions")
async def chat_completions(
    request: ChatCompletionsRequest,
    http_request: Request,
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

    # 6. Retrieve context.
    # F2 (audit retrieval-coupling-2026-05-06): pass synthetic partner_user_id
    # so retrieval-api pins verified_caller and emits the
    # `knowledge.queried` product_event with the correct (org, partner-key)
    # tuple. Matches the existing convention used at line ~205 below for
    # write_retrieval_log.
    #
    # 2026-05-12 HOTFIX (chat-widget-launch-day): the F2 claim only resolves
    # correctly when ``auth.key_id`` is a partner_api_keys UUID. Widget-driven
    # calls authenticate via the widgets table (separate domain — see
    # klai-portal/backend/app/models/widgets.py) and ``auth.key_id`` carries a
    # ``wgt_<hex>`` identifier that is NOT a UUID. Forwarding it as
    # ``partner:<wgt_id>`` made retrieval-api's identity-assert call portal-api's
    # ``_resolve_partner_key_org_slug``, which SELECTed against
    # ``partner_api_keys.id`` (uuid column) and asyncpg raised DataError →
    # portal-api 5xx → SDK collapsed to ``portal_unreachable`` → 403 on
    # /retrieve → widget chat showed "Er ging iets mis". Only forward the
    # claim when it is parseable as a UUID; the widget path falls back to
    # tenant-only verification at retrieval-api (already correct via the
    # X-Internal-Secret + caller_service:portal-api contract). Product-event
    # tagging on widget retrieval reverts to None until a dedicated
    # ``evidence:"widget_key"`` path exists in identity-assert (follow-up
    # SPEC). See pitfalls/process-rules.md → retrieve-caller-service-header-mismatch.
    partner_user_id: str | None
    try:
        uuid.UUID(str(auth.key_id))
        partner_user_id = f"partner:{auth.key_id}"
    except (ValueError, AttributeError, TypeError):
        partner_user_id = None
    widget_system_prompt = await _widget_system_prompt(auth, db)
    is_widget_chat = str(auth.key_id).startswith("wgt_")

    try:
        chunks, system_prompt = await retrieve_context(
            org_id=auth.org_id,
            zitadel_org_id=auth.zitadel_org_id,
            kb_slugs=kb_slugs,
            messages=request.messages,
            settings=settings,
            partner_user_id=partner_user_id,
            widget_system_prompt=widget_system_prompt,
            backend_managed_citations=is_widget_chat,
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

    # 7b. Widget audit-trail: log the user turn immediately, and the
    # assistant turn once the response is composed. Fire-and-forget so
    # an audit hiccup never breaks the chat. SPEC-WIDGET-ACTIVITY-001.
    audit_widget_id: str | None = None
    audit_session_key: str | None = None
    audit_ip_hash: str | None = None
    audit_ua_hash: str | None = None
    if is_widget_chat:
        widget_uuid_row = (
            await db.execute(select(Widget.id).where(Widget.widget_id == str(auth.key_id)))
        ).scalar_one_or_none()
        if widget_uuid_row is not None:
            audit_widget_id = str(widget_uuid_row)
            bearer = http_request.headers.get("authorization", "")
            raw_token = bearer.removeprefix("Bearer ").strip()
            audit_session_key = session_key_from_token(raw_token)
            audit_ip_hash = hash_audit_value(http_request.client.host if http_request.client else None)
            audit_ua_hash = hash_audit_value(http_request.headers.get("user-agent"))
            last_user_msg = next(
                (str(m.get("content", "")) for m in reversed(request.messages) if m.get("role") == "user"),
                "",
            )
            if audit_session_key and last_user_msg:
                task = asyncio.create_task(
                    record_widget_turn(
                        widget_id=audit_widget_id,
                        org_id=auth.org_id,
                        session_key=audit_session_key,
                        role="user",
                        content=last_user_msg,
                        ip_hash=audit_ip_hash,
                        user_agent_hash=audit_ua_hash,
                        loaded_origin=http_request.headers.get("origin") or None,
                    )
                )
                _pending.add(task)
                task.add_done_callback(_pending.discard)

    audit_ready = is_widget_chat and audit_widget_id is not None and audit_session_key is not None

    # 8. Streaming or non-streaming
    if request.stream:
        citation_source_urls = _citation_source_urls_from_chunks(chunks)
        citation_source_metadata = _citation_source_metadata_from_chunks(chunks)
        streaming_gen = chat_completion_streaming(
            messages=request.messages,
            model=request.model,
            temperature=request.temperature,
            system_prompt=system_prompt,
            settings=settings,
            org_id=auth.org_id,
            allowed_source_urls=set(citation_source_urls.values()) or _source_urls_from_chunks(chunks),
            citation_source_urls=citation_source_urls,
            citation_source_metadata=citation_source_metadata,
            citation_chunks=chunks,
            citation_output="markers" if is_widget_chat else "links",
        )
        if audit_ready:
            streaming_gen = _audit_streaming_wrapper(
                streaming_gen,
                widget_id=audit_widget_id,  # type: ignore[arg-type]
                org_id=auth.org_id,
                session_key=audit_session_key,  # type: ignore[arg-type]
                loaded_origin=http_request.headers.get("origin") or None,
            )
        return StreamingResponse(
            content=streaming_gen,
            media_type="text/event-stream",
        )

    # Non-streaming
    citation_source_urls = _citation_source_urls_from_chunks(chunks)
    citation_source_metadata = _citation_source_metadata_from_chunks(chunks)
    result = await chat_completion_non_streaming(
        messages=request.messages,
        model=request.model,
        temperature=request.temperature,
        system_prompt=system_prompt,
        settings=settings,
        org_id=auth.org_id,
        allowed_source_urls=set(citation_source_urls.values()) or _source_urls_from_chunks(chunks),
        citation_source_urls=citation_source_urls,
        citation_source_metadata=citation_source_metadata,
        citation_chunks=chunks,
        citation_output="markers" if is_widget_chat else "links",
    )
    if audit_ready:
        assistant_text, assistant_sources = _extract_assistant_text_and_sources(result)
        if assistant_text:
            task = asyncio.create_task(
                record_widget_turn(
                    widget_id=audit_widget_id,  # type: ignore[arg-type]
                    org_id=auth.org_id,
                    session_key=audit_session_key,  # type: ignore[arg-type]
                    role="assistant",
                    content=assistant_text,
                    sources=assistant_sources,
                    loaded_origin=http_request.headers.get("origin") or None,
                )
            )
            _pending.add(task)
            task.add_done_callback(_pending.discard)
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
# Widget CORS header builder
#
# SPEC-SEC-CORS-001 REQ-2.2 — partner widget endpoints SHALL NEVER set
# Access-Control-Allow-Credentials: true. Widget traffic authenticates via
# Bearer JWT in the Authorization header; cookies are not involved. The
# helper centralises this contract so the GET and OPTIONS handlers can
# never drift apart.
# ---------------------------------------------------------------------------


_HEX_COLOR_RE = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")


def _hex_to_rgb(hex_color: str) -> tuple[int, int, int]:
    """Convert ``#RRGGBB`` or ``#RGB`` to an (r, g, b) tuple of 0-255 ints."""
    h = hex_color.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def _readable_text_color(primary_hex: str) -> str:
    """Return ``#191918`` for light primaries and ``#ffffff`` for dark
    ones using the WCAG-relative-luminance formula.

    Picked thresholds match the WCAG 2.x AA cutoff (~0.179): primaries
    brighter than that get the dark Klai foreground; darker primaries
    get pure white. Without this the bubble icon, send-arrow, and user
    message text inherit ``--klai-primary-text-color: var(--klai-text-color)``
    (dark) on every brand colour — illegible the moment an admin picks a
    dark hex like #2b32fd or any deep brown.
    """

    def _channel(c: int) -> float:
        s = c / 255
        return s / 12.92 if s <= 0.03928 else ((s + 0.055) / 1.055) ** 2.4

    r, g, b = _hex_to_rgb(primary_hex)
    lum = 0.2126 * _channel(r) + 0.7152 * _channel(g) + 0.0722 * _channel(b)
    return "#191918" if lum > 0.179 else "#ffffff"


def _merge_css_variables(widget_config: dict) -> dict[str, str]:
    """Translate stored widget-config fields into CSS custom properties
    the embed script (klai-chat.js) applies inside its Shadow DOM.

    The admin "Brand kleur" field is stored as ``primary_color`` (a hex
    string). The widget script only reads ``css_variables`` for per-widget
    overrides — without this translation step the configured brand colour
    silently never reaches the widget. Any keys already in
    ``css_variables`` win over the derived ones so a power-user can still
    override granularly.

    Beside the colour itself we derive ``--klai-primary-text-color`` from
    its luminance so the icon / arrow / user-message text rendered on top
    of the primary surface stays legible on any brand hex the admin picks.

    Validation: ``primary_color`` must match ``#RRGGBB`` or ``#RGB``.
    Anything else (empty, invalid, attempted CSS injection) is dropped
    silently so a malformed admin field can never poison the stylesheet.
    """
    css_vars: dict[str, str] = {}
    primary = widget_config.get("primary_color")
    if isinstance(primary, str) and _HEX_COLOR_RE.match(primary):
        css_vars["--klai-primary-color"] = primary
        css_vars["--klai-primary-text-color"] = _readable_text_color(primary)

    overrides = widget_config.get("css_variables") or {}
    if isinstance(overrides, dict):
        for key, value in overrides.items():
            if isinstance(key, str) and isinstance(value, str):
                css_vars[key] = value
    return css_vars


def _widget_cors_headers(origin: str, *, preflight: bool) -> dict[str, str]:
    """Build the CORS response headers for /partner/v1/widget-config.

    The actual server-side origin gate is the per-widget `allowed_origins`
    check upstream of this call (see ``origin_allowed`` in
    ``app.services.widget_auth``). This helper only constructs the response
    headers once that gate has approved the request.

    Parameters
    ----------
    origin:
        The request Origin header value, already validated against the
        widget's allowed_origins list. The caller MUST NOT pass an
        unvalidated origin — that is the upstream check's responsibility.
    preflight:
        When True, includes the preflight-only headers (Allow-Methods,
        Allow-Headers, Max-Age). When False, returns the minimal set for
        an actual response.
    """
    headers: dict[str, str] = {
        "Access-Control-Allow-Origin": origin,
        "Vary": "Origin",
    }
    if preflight:
        headers["Access-Control-Allow-Methods"] = "GET, OPTIONS"
        headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
        headers["Access-Control-Max-Age"] = "86400"
    return headers


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
    # @MX:REASON: Origin check is UX-only (see docstring §"Security model");
    # the actual security boundary is the HS256 JWT session_token. Token TTL 1h;
    # no sensitive data returned. SPEC-SEC-HYGIENE-001 REQ-23.
    # @MX:SPEC: SPEC-WIDGET-001 REQ-2 + SPEC-SEC-HYGIENE-001 REQ-23

    SPEC-WIDGET-002: Public endpoint, no API key required.
    - Looks up widget by widget_id (id param) in the widgets table
    - Validates Origin header against allowed_origins (UX-gating only — see below)
    - Generates HS256 JWT session token (1 hour TTL)
    - Returns CORS headers for matched origin (never *)

    Security model (SPEC-SEC-HYGIENE-001 REQ-23.1):

    The ``Origin`` header check is **UX-only, not a security boundary.**
    Auditors flag this finding repeatedly because non-browser clients
    (curl, custom integrations) can spoof the ``Origin`` header — yes,
    they can, and that is fine, because:

    - The primary identifier is ``widget_id`` (the URL ``id`` query
      parameter). It is a public, opaque identifier.
    - Downstream security (chat completions, KB retrieval) is enforced
      by the HS256 JWT ``session_token`` returned in the response body.
      The token carries ``wgt_id``, ``org_id``, and the allowed
      ``kb_ids`` — it is the actual access-control mechanism.
    - A non-browser client that spoofs ``Origin`` receives the same
      scoped session_token any other browser would receive for that
      widget. They cannot escalate privilege; they can only obtain a
      token that grants access to exactly the KBs the widget owner has
      already published.
    - ``allowed_origins`` therefore controls **browser embedding
      behaviour** (which origins may render the widget iframe), not
      API access control.

    Asymmetric signing (ES256/EdDSA) is the structural fix and is
    tracked separately; until that lands, REQ-24 derives per-tenant
    HS256 keys via HKDF so a single secret leak does not let an attacker
    forge tokens cross-tenant.

    Error codes:
        404 - widget_id not found
        403 - missing or disallowed Origin (UX gate)
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

    # REQ-7 (Finding B-4): per-widget mint rate-limit BEFORE DB lookup.
    # @MX:NOTE: [AUTO] Rate-limit key is widget_mint:{id} (public widget_id from URL param).
    # Limit is 10/min per widget to prevent unbounded LLM-token drain via the public mint path.
    # @MX:SPEC: SPEC-SEC-CROSS-TENANT-FOLLOWUP-001 REQ-7
    redis = get_redis_pool()
    allowed, retry_after = await check_rate_limit(redis, f"widget_mint:{id}", limit_per_minute=10, window_seconds=60)
    if not allowed:
        return Response(
            content='{"detail":"Rate limit exceeded"}',
            status_code=429,
            media_type="application/json",
            headers={"Retry-After": str(retry_after)},
        )

    # Look up widget by public widget_id (SPEC-WIDGET-002: own table)
    result = await db.execute(select(Widget).where(Widget.widget_id == id))
    widget_row = result.scalar_one_or_none()

    if widget_row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Widget not found")

    # Validate Origin header
    origin = request.headers.get("origin", "")
    widget_config_data = widget_row.widget_config or {}
    allowed_origins = widget_config_data.get("allowed_origins", [])

    if not origin or not origin_allowed(origin, allowed_origins, allow_any_origin=widget_row.allow_any_origin):
        return Response(
            content='{"detail":"Origin not allowed"}',
            status_code=403,
            media_type="application/json",
        )

    # Load org and set tenant BEFORE KB access query (ensures RLS context is active)
    org_result = await db.execute(select(PortalOrg).where(PortalOrg.id == widget_row.org_id))
    org = org_result.scalar_one_or_none()
    if org is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Widget not found")

    # REQ-1 (Finding B-1): existence-non-disclosure — surface as 404 not 403.
    # @MX:ANCHOR: [AUTO] Platform-unlock gate on widget_config public endpoint
    # @MX:REASON: Fencing 'widgets' in enabled_addons must also block the public mint path;
    # admin-UI gate alone leaves deployed widgets draining LLM tokens for locked tenants.
    # @MX:SPEC: SPEC-SEC-CROSS-TENANT-FOLLOWUP-001 REQ-1
    try:
        assert_platform_unlocked(org, "widgets")
    except Exception:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Widget not found") from None

    await set_tenant(db, org.id)

    # Load KB access for this widget (after RLS tenant is set)
    kb_result = await db.execute(select(WidgetKbAccess).where(WidgetKbAccess.widget_id == widget_row.id))
    kb_rows = kb_result.scalars().all()
    kb_ids = [row.kb_id for row in kb_rows]

    # Generate session token. SPEC-SEC-HYGIENE-001 REQ-24.4: pass tenant_slug
    # so the signing key is HKDF-derived per tenant — a single-secret leak no
    # longer lets an attacker forge tokens across tenants.
    session_token = generate_session_token(
        wgt_id=widget_row.widget_id,
        org_id=widget_row.org_id,
        kb_ids=kb_ids,
        secret=settings.widget_jwt_secret,
        tenant_slug=org.slug,
    )

    expires_at = datetime.now(UTC) + timedelta(hours=1)

    body = {
        "title": widget_config_data.get("title", "") or widget_row.name,
        "welcome_message": widget_config_data.get("welcome_message", ""),
        "css_variables": _merge_css_variables(widget_config_data),
        "chat_endpoint": "/partner/v1/chat/completions",
        "session_token": session_token,
        "session_expires_at": expires_at.isoformat(),
        # TWD-style additions: chips on empty state, white-label
        # disclaimer toggle. system_prompt stays server-side only.
        "conversation_starters": widget_config_data.get("conversation_starters", []),
        "hide_disclaimer": widget_config_data.get("hide_disclaimer", False),
        # Name + description drive the TWD-pattern header (avatar +
        # title + subtitle) and the empty-state hero.
        "name": widget_row.name,
        "description": widget_row.description or "",
        "primary_color": widget_config_data.get("primary_color", "#fcaa2d"),
        # Display toggles: widget renders the sources block / meta line
        # under each assistant message based on these flags.
        "show_sources": widget_config_data.get("show_sources", True),
        "show_meta": widget_config_data.get("show_meta", False),
    }

    return Response(
        content=json.dumps(body),
        status_code=200,
        media_type="application/json",
        headers=_widget_cors_headers(origin, preflight=False),
    )


@router.get("/public-bot-config")
async def public_bot_config(
    id: str,
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Public bot share-link endpoint — no Origin check.

    Powers the share-link bot page at /bot/{widget_id}. Anyone with the
    widget_id (a public, opaque identifier) can fetch the same payload
    as /widget-config without going through the browser-embed origin
    gate. Downstream security is the HS256 JWT session_token, identical
    to the embed flow.
    """
    if not settings.widget_jwt_secret:
        return Response(
            content='{"detail":"Widget authentication not configured"}',
            status_code=503,
            media_type="application/json",
        )

    # REQ-7 (Finding B-4): per-widget mint rate-limit BEFORE DB lookup.
    # @MX:NOTE: [AUTO] Same rate-limit as widget_config — isolates public share-link
    # mint path from the embed mint path with separate per-widget keys.
    # @MX:SPEC: SPEC-SEC-CROSS-TENANT-FOLLOWUP-001 REQ-7
    redis = get_redis_pool()
    allowed, retry_after = await check_rate_limit(redis, f"widget_mint:{id}", limit_per_minute=10, window_seconds=60)
    if not allowed:
        return Response(
            content='{"detail":"Rate limit exceeded"}',
            status_code=429,
            media_type="application/json",
            headers={"Retry-After": str(retry_after)},
        )

    result = await db.execute(select(Widget).where(Widget.widget_id == id))
    widget_row = result.scalar_one_or_none()
    if widget_row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Widget not found")

    widget_config_data = widget_row.widget_config or {}
    if not widget_row.public_share_enabled:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Widget not found")

    org_result = await db.execute(select(PortalOrg).where(PortalOrg.id == widget_row.org_id))
    org = org_result.scalar_one_or_none()
    if org is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Widget not found")

    # REQ-1 (Finding B-1): existence-non-disclosure — surface as 404 not 403.
    # @MX:ANCHOR: [AUTO] Platform-unlock gate on public_bot_config endpoint
    # @MX:REASON: Same as widget_config — disabled tenant still has live share links;
    # 404 avoids leaking widget existence for locked tenants.
    # @MX:SPEC: SPEC-SEC-CROSS-TENANT-FOLLOWUP-001 REQ-1
    try:
        assert_platform_unlocked(org, "widgets")
    except Exception:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Widget not found") from None

    await set_tenant(db, org.id)

    kb_result = await db.execute(select(WidgetKbAccess).where(WidgetKbAccess.widget_id == widget_row.id))
    kb_ids = [row.kb_id for row in kb_result.scalars().all()]

    session_token = generate_session_token(
        wgt_id=widget_row.widget_id,
        org_id=widget_row.org_id,
        kb_ids=kb_ids,
        secret=settings.widget_jwt_secret,
        tenant_slug=org.slug,
    )
    expires_at = datetime.now(UTC) + timedelta(hours=1)
    body = {
        "title": widget_config_data.get("title", ""),
        "welcome_message": widget_config_data.get("welcome_message", ""),
        "css_variables": _merge_css_variables(widget_config_data),
        "chat_endpoint": "/partner/v1/chat/completions",
        "session_token": session_token,
        "session_expires_at": expires_at.isoformat(),
        "conversation_starters": widget_config_data.get("conversation_starters", []),
        "hide_disclaimer": widget_config_data.get("hide_disclaimer", False),
        "primary_color": widget_config_data.get("primary_color", "#fcaa2d"),
        "show_sources": widget_config_data.get("show_sources", True),
        "show_meta": widget_config_data.get("show_meta", False),
        "name": widget_row.name,
        "description": widget_row.description or "",
    }
    return Response(
        content=json.dumps(body),
        status_code=200,
        media_type="application/json",
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

    @MX:WARN: DB read precedes the origin allowlist check. A cross-origin
    attacker probing this endpoint with rotating origins causes one DB hit
    per attempt. Browsers cache rejected preflights only briefly so the
    cost can compound under sustained probing.
    @MX:REASON: Origin validation must read ``widget.allowed_origins`` from
    the DB; we cannot decide without that read. A future SPEC may add a
    ``widget_id -> allowed_origins`` cache (60s TTL in Redis) — see PR #180
    follow-up. Preserved as pre-existing pattern under SPEC-SEC-CORS-001
    minimal-changes scope.
    @MX:SPEC: SPEC-WIDGET-001
    """
    result = await db.execute(select(Widget).where(Widget.widget_id == id))
    widget_row = result.scalar_one_or_none()

    if widget_row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Widget not found")

    origin = request.headers.get("origin", "")
    widget_config_data = widget_row.widget_config or {}
    allowed_origins = widget_config_data.get("allowed_origins", [])

    if not origin or not origin_allowed(origin, allowed_origins, allow_any_origin=widget_row.allow_any_origin):
        return Response(status_code=204)

    return Response(
        status_code=204,
        headers=_widget_cors_headers(origin, preflight=True),
    )
