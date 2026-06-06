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
    chat_completion_non_streaming,
    chat_completion_streaming,
    retrieve_context,
    safety_refusal_response,
    safety_refusal_stream,
    widget_input_safety_violation,
)
from app.services.partner_rate_limit import check_rate_limit
from app.services.quality_scorer import schedule_quality_update
from app.services.redis_client import get_redis_pool
from app.services.retrieval_log import find_correlated_log, write_retrieval_log
from app.services.web_search import build_web_results_block, search_web, web_results_as_chunks
from app.services.widget_audit import (
    hash_audit_value,
    record_widget_turn,
    session_key_from_token,
)
from app.services.widget_auth import generate_session_token, origin_allowed
from app.services.widget_handoff import (
    get_active_handoff_session_id,
    list_visible_handoff_messages,
    send_handoff_visitor_message,
    start_hubspot_handoff,
)

logger = structlog.get_logger()

# Hold references to fire-and-forget tasks to prevent GC (same pattern as partner_dependencies)
_pending: set[asyncio.Task] = set()  # type: ignore[type-arg]

router = APIRouter(prefix="/partner/v1", tags=["Partner API"])

_ALLOWED_MODELS = {"klai-primary", "klai-fast"}
_WIDGET_CLIENT_SESSION_RE = re.compile(r"^[A-Za-z0-9_-]{16,80}$")
_HUBSPOT_HANDOFF_DEV_TENANT_SLUG = "getklai"
_HUBSPOT_HANDOFF_DEV_ORIGIN = "https://getklai.getklai.com"
_MAX_WEB_SEARCH_QUERY_CHARS = 512


# ---------------------------------------------------------------------------
# Pydantic request models
# ---------------------------------------------------------------------------


class PageContext(BaseModel):
    url: str = Field(..., min_length=1, max_length=2048)
    path: str = Field(..., min_length=1, max_length=512)
    title: str | None = Field(default=None, max_length=512)
    referrer: str | None = Field(default=None, max_length=2048)
    excerpt: str | None = Field(default=None, max_length=2000)


class KnowledgeOptions(BaseModel):
    enabled: bool = True
    query: str | None = None
    knowledge_base_ids: list[int] | None = None
    top_k: int | None = Field(default=None, ge=1, le=50)
    include_sources: bool = True


class ChatCompletionsRequest(BaseModel):
    messages: list[dict] = Field(..., min_length=1)
    model: str = "klai-primary"
    stream: bool = True
    temperature: float = 0.7
    knowledge_base_ids: list[int] | None = None
    page_context: PageContext | None = None
    knowledge: KnowledgeOptions | None = None
    # Opt-in live web search. When true, portal-api queries the same self-hosted
    # SearXNG instance the chat surfaces use, then injects the top results into
    # the system prompt for this turn. Default off so existing callers are
    # unaffected and search only runs when explicitly requested. Gated per key
    # by the ``web_search`` permission; never runs for public widget keys.
    web_search: bool = False
    # Optional explicit query for the web search. Integrations should pass a
    # concise, natural-language query here (e.g. the ticket subject) — it makes
    # a far better keyword query than ``knowledge.query``, which is tuned for KB
    # embedding retrieval and is often a long labelled blob that returns nothing
    # from a keyword engine. Falls back to the last user message when omitted.
    web_search_query: str | None = Field(default=None, max_length=_MAX_WEB_SEARCH_QUERY_CHARS)


class PartnerFeedbackRequest(BaseModel):
    message_id: str
    conversation_id: str | None = None
    rating: Literal["thumbsUp", "thumbsDown"]
    text: str | None = None
    tag: str | None = None


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


class PartnerKnowledgeRequest(BaseModel):
    kb_id: int
    title: str | None = None
    content: str = Field(..., max_length=10_485_760)
    source_type: str = "partner_api"
    content_type: str = "text/plain"


class HandoffTranscriptMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(..., min_length=1, max_length=4000)


class StartHubSpotHandoffRequest(BaseModel):
    summary: str | None = Field(default=None, max_length=4000)
    visitor_name: str | None = Field(default=None, max_length=120)
    visitor_email: str | None = Field(default=None, max_length=254)
    messages: list[HandoffTranscriptMessage] = Field(default_factory=list, max_length=50)


class SendHubSpotHandoffMessageRequest(BaseModel):
    content: str = Field(..., min_length=1, max_length=10000)
    visitor_name: str | None = Field(default=None, max_length=120)


class HubSpotHandoffResponse(BaseModel):
    id: int
    status: str
    integration_thread_id: str
    hubspot_conversations_thread_id: str | None = None


class HubSpotHandoffMessageResponse(BaseModel):
    id: int | None
    handoff_session_id: int
    hubspot_message_id: str | None = None


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


async def _widget_page_context_enabled(auth: PartnerAuthContext, db: AsyncSession) -> bool:
    """Return whether this widget may send current-page context."""
    if not str(auth.key_id).startswith("wgt_"):
        return False
    result = await db.execute(
        select(Widget.widget_config).where(
            Widget.widget_id == auth.key_id,
            Widget.org_id == auth.org_id,
        )
    )
    config = result.scalar_one_or_none() or {}
    return bool(config.get("page_context_enabled")) if isinstance(config, dict) else False


def _citation_runtime_options(
    trusted_sources: list[dict[str, Any]],
    *,
    is_widget_chat: bool,
) -> tuple[set[str], dict[int, str], dict[str, dict[str, str]], Literal["links", "markers"]]:
    """Return citation inputs for backend-managed document-level citations.

    Both widget and partner API chat use the shared deterministic selector.
    The legacy link sanitizer still exists for direct helper coverage and
    rollback, but this endpoint no longer asks the model to choose visible
    citation links.
    """
    _ = trusted_sources, is_widget_chat
    return set(), {}, {}, "markers"


async def _audit_streaming_wrapper(
    inner: AsyncGenerator[bytes],
    *,
    widget_id: str,
    session_key: str,
    loaded_origin: str | None = None,
    is_preview: bool = False,
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
                    session_key=session_key,
                    role="assistant",
                    content=final_text,
                    sources=composed_sources or None,
                    loaded_origin=loaded_origin,
                    is_preview=is_preview,
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


def _support_user_hash(auth: PartnerAuthContext, hubspot_user_id: str | None) -> str:
    """Stable private key for one support rep inside one integration session."""
    raw_user = hubspot_user_id or "unknown"
    return hash_audit_value(f"{auth.org_id}:{auth.key_id}:hubspot:{raw_user}") or "unknown"


def _mapping(row: Any) -> dict[str, Any]:
    if row is None:
        return {}
    if isinstance(row, dict):
        return row
    if hasattr(row, "_mapping"):
        return dict(row._mapping)
    try:
        return dict(row)
    except (TypeError, ValueError):
        return {}


def _isoformat(value: Any) -> str | None:
    if value is None:
        return None
    return str(value.isoformat())


def _message_payload(row: Any) -> dict[str, Any]:
    data = _mapping(row)
    return {
        "id": str(data.get("id")),
        "role": data.get("role"),
        "content": data.get("content"),
        "draft_body": data.get("draft_body"),
        "sources": data.get("sources") or [],
        "model_alias": data.get("model_alias"),
        "completion_id": data.get("completion_id"),
        "sequence": data.get("sequence"),
        "created_at": _isoformat(data.get("created_at")),
    }


def _session_payload(row: Any, messages: list[dict[str, Any]]) -> dict[str, Any]:
    data = _mapping(row)
    return {
        "id": str(data.get("id")),
        "integration_type": data.get("integration_type"),
        "hubspot_portal_id": data.get("hubspot_portal_id"),
        "hubspot_ticket_id": data.get("hubspot_ticket_id"),
        "contact_id": data.get("contact_id"),
        "subject": data.get("subject_snapshot"),
        "status": data.get("status"),
        "message_count": data.get("message_count") or len(messages),
        "created_at": _isoformat(data.get("created_at")),
        "updated_at": _isoformat(data.get("updated_at")),
        "last_message_at": _isoformat(data.get("last_message_at")),
        "messages": messages,
    }


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


def _widget_safety_block_response(
    request: ChatCompletionsRequest,
    auth: PartnerAuthContext,
) -> Response | dict[str, Any] | None:
    if not str(auth.key_id).startswith("wgt_"):
        return None
    safety_reason = widget_input_safety_violation(request.messages)
    if not safety_reason:
        return None
    logger.warning(
        "partner_chat_widget_input_blocked",
        org_id=auth.org_id,
        widget_id=auth.key_id,
        stage="widget_input",
        reason=safety_reason,
    )
    last_user_msg = next(
        (str(m.get("content", "")) for m in reversed(request.messages) if m.get("role") == "user"),
        "",
    )
    if request.stream:
        return StreamingResponse(
            content=safety_refusal_stream(last_user_msg),
            media_type="text/event-stream",
        )
    return safety_refusal_response(model=request.model, query=last_user_msg)


def _validate_chat_request(request: ChatCompletionsRequest) -> None:
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
    if not any(m.get("role") == "user" for m in request.messages):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": {"type": "invalid_request", "message": "Messages must contain at least one user message"}},
        )


def _message_text(content: object) -> str:
    """Extract plain text from a message ``content`` that may be a string or an
    OpenAI-style list of parts (``[{"type": "text", "text": "…"}, …]``)."""
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts = [
            part["text"].strip()
            for part in content
            if isinstance(part, dict)
            and part.get("type") == "text"
            and isinstance(part.get("text"), str)
            and part["text"].strip()
        ]
        return " ".join(parts).strip()
    return ""


def _clean_web_query(value: str | None) -> str | None:
    cleaned = re.sub(r"\s+", " ", value or "").strip()
    if not cleaned:
        return None
    return cleaned[:_MAX_WEB_SEARCH_QUERY_CHARS]


def _resolve_web_query(request: ChatCompletionsRequest) -> str | None:
    """Pick the query to send to the web search.

    Deliberately does NOT use ``knowledge.query``: that field is tuned for KB
    embedding retrieval and is often a long labelled blob (ticket fields, recent
    comments, …) that a keyword engine like SearXNG returns nothing for. Order:

    1. ``web_search_query`` — explicit, concise query from the integration.
    2. the last user message — the natural-language question being asked
       (handles multimodal content arrays, not just plain strings).
    """
    explicit = _clean_web_query(request.web_search_query)
    if explicit:
        return explicit
    for message in reversed(request.messages):
        if message.get("role") != "user":
            continue
        if text := _clean_web_query(_message_text(message.get("content"))):
            return text
    return None


async def _maybe_apply_web_search(
    *,
    request: ChatCompletionsRequest,
    auth: PartnerAuthContext,
    is_widget_chat: bool,
    system_prompt: str,
) -> tuple[str, list[dict], str | None]:
    """Append live web results to the prompt and return them as evidence chunks.

    Opt-in per request (``request.web_search``), gated by the key's
    ``web_search`` permission, and never run for public widget keys. Fail-open:
    if search returns nothing or errors, the original prompt, an empty chunk
    list and ``None`` are returned so the answer still goes out without web context.

    Returns ``(system_prompt, web_chunks, web_query)``. The chunks are passed to
    the chat completion as a SEPARATE ``web_chunks`` tier (not merged with KB
    chunks), and ``web_query`` is the concise query they were retrieved for — the
    composer validates web sources against it (not the KB ``knowledge.query``
    blob, which would reject relevant web sources). The composer tags web sources
    ``origin: "web"``; without this tier a web-grounded answer with no KB chunks
    would be stripped to the "no citable sources" refusal.
    """
    if not request.web_search:
        return system_prompt, [], None
    if is_widget_chat:
        logger.debug("partner_web_search_ignored_widget", org_id=auth.org_id)
        return system_prompt, [], None

    require_permission(auth, "web_search")  # raises 403 if the key lacks it

    web_query = _resolve_web_query(request)
    web_results = await search_web(web_query, settings=settings) if web_query else []
    if not web_results:
        logger.warning("partner_web_search_empty", org_id=auth.org_id, key_id=str(auth.key_id))
        return system_prompt, [], None

    logger.info(
        "partner_web_search_used",
        org_id=auth.org_id,
        key_id=str(auth.key_id),
        result_count=len(web_results),
    )
    enriched_prompt = f"{system_prompt}\n{build_web_results_block(web_results)}"
    return enriched_prompt, web_results_as_chunks(web_results), web_query


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

    # 2-3. Model and messages validation.
    _validate_chat_request(request)
    if safety_response := _widget_safety_block_response(request, auth):
        return safety_response
    is_widget_chat = str(auth.key_id).startswith("wgt_")

    # 4. Validate KB access. ``knowledge`` is a Klai extension on top of the
    # OpenAI-compatible request shape. Top-level knowledge_base_ids remains
    # supported for existing partner clients.
    knowledge = request.knowledge
    requested_kb_ids = (
        knowledge.knowledge_base_ids
        if knowledge is not None and knowledge.knowledge_base_ids is not None
        else request.knowledge_base_ids
    )
    kb_ids = validate_kb_access(auth, requested_kb_ids)

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
    page_context_enabled = await _widget_page_context_enabled(auth, db) if is_widget_chat else False
    page_context = (
        request.page_context.model_dump(exclude_none=True) if page_context_enabled and request.page_context else None
    )

    try:
        chunks, system_prompt, trusted_sources = await retrieve_context(
            org_id=auth.org_id,
            zitadel_org_id=auth.zitadel_org_id,
            kb_slugs=kb_slugs,
            messages=request.messages,
            settings=settings,
            partner_user_id=partner_user_id,
            widget_system_prompt=widget_system_prompt,
            page_context=page_context,
            backend_managed_citations=True,
            retrieval_query=knowledge.query if knowledge is not None else None,
            top_k=knowledge.top_k if knowledge is not None and knowledge.top_k is not None else 8,
            retrieval_enabled=knowledge.enabled if knowledge is not None else True,
        )
    except (httpx.TimeoutException, httpx.ReadTimeout) as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={"error": {"type": "upstream_error", "message": "Retrieval service timeout"}},
        ) from exc

    # 6b. Optional live web search (opt-in per request, gated per API key,
    #     never for public widget keys). Web results are a SEPARATE citation
    #     tier from the knowledge base: they are passed alongside the KB chunks
    #     (never merged) so the composer keeps KB and web apart, tags each
    #     source with its origin, and only refuses when both tiers are empty.
    system_prompt, web_chunks, web_query = await _maybe_apply_web_search(
        request=request,
        auth=auth,
        is_widget_chat=is_widget_chat,
        system_prompt=system_prompt,
    )

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
            audit_session_key = getattr(auth, "session_key", None) or session_key_from_token(raw_token)
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
                        session_key=audit_session_key,
                        role="user",
                        content=last_user_msg,
                        ip_hash=audit_ip_hash,
                        user_agent_hash=audit_ua_hash,
                        loaded_origin=http_request.headers.get("origin") or None,
                        is_preview=getattr(auth, "is_preview", False),
                    )
                )
                _pending.add(task)
                task.add_done_callback(_pending.discard)

    audit_ready = is_widget_chat and audit_widget_id is not None and audit_session_key is not None
    (
        allowed_source_urls,
        citation_source_urls,
        citation_source_metadata,
        citation_output,
    ) = _citation_runtime_options(trusted_sources, is_widget_chat=is_widget_chat)

    # 8. Streaming or non-streaming
    if request.stream:
        streaming_gen = chat_completion_streaming(
            messages=request.messages,
            model=request.model,
            temperature=request.temperature,
            system_prompt=system_prompt,
            settings=settings,
            org_id=auth.org_id,
            allowed_source_urls=allowed_source_urls,
            citation_source_urls=citation_source_urls,
            citation_source_metadata=citation_source_metadata,
            citation_chunks=chunks,
            web_chunks=web_chunks,
            web_query=web_query,
            trusted_sources=trusted_sources,
            citation_output=citation_output,
            source_query=knowledge.query if knowledge is not None else None,
            emit_sources=knowledge.include_sources if knowledge is not None else True,
            page_context=page_context,
        )
        if audit_ready:
            streaming_gen = _audit_streaming_wrapper(
                streaming_gen,
                widget_id=audit_widget_id,  # type: ignore[arg-type]
                session_key=audit_session_key,  # type: ignore[arg-type]
                loaded_origin=http_request.headers.get("origin") or None,
                is_preview=getattr(auth, "is_preview", False),
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
        org_id=auth.org_id,
        allowed_source_urls=allowed_source_urls,
        citation_source_urls=citation_source_urls,
        citation_source_metadata=citation_source_metadata,
        citation_chunks=chunks,
        web_chunks=web_chunks,
        web_query=web_query,
        trusted_sources=trusted_sources,
        citation_output=citation_output,
        source_query=knowledge.query if knowledge is not None else None,
        page_context=page_context,
    )
    if knowledge is not None and not knowledge.include_sources:
        for choice in result.get("choices") or []:
            message = choice.get("message") if isinstance(choice, dict) else None
            if isinstance(message, dict):
                message.pop("sources", None)
    if audit_ready:
        assistant_text, assistant_sources = _extract_assistant_text_and_sources(result)
        if assistant_text:
            task = asyncio.create_task(
                record_widget_turn(
                    widget_id=audit_widget_id,  # type: ignore[arg-type]
                    session_key=audit_session_key,  # type: ignore[arg-type]
                    role="assistant",
                    content=assistant_text,
                    sources=assistant_sources,
                    loaded_origin=http_request.headers.get("origin") or None,
                    is_preview=getattr(auth, "is_preview", False),
                )
            )
            _pending.add(task)
            task.add_done_callback(_pending.discard)
    return result


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


# ---------------------------------------------------------------------------
# Widget human handoff endpoints
# ---------------------------------------------------------------------------


def _require_widget_auth(auth: PartnerAuthContext) -> None:
    if not str(auth.key_id).startswith("wgt_") or not auth.session_key:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"error": {"type": "permission_error", "message": "Widget session required"}},
        )


@router.post("/widget-handoffs/hubspot/start", response_model=HubSpotHandoffResponse)
async def start_widget_hubspot_handoff(
    request: StartHubSpotHandoffRequest,
    auth: PartnerAuthContext = Depends(get_partner_key),
    db: AsyncSession = Depends(get_db),
) -> HubSpotHandoffResponse:
    _require_widget_auth(auth)
    try:
        result = await start_hubspot_handoff(
            db,
            org_id=auth.org_id,
            widget_public_id=str(auth.key_id),
            session_key=auth.session_key or "",
            summary=request.summary,
            visitor_name=request.visitor_name,
            visitor_email=request.visitor_email,
            messages=[message.model_dump() for message in request.messages],
        )
    except Exception as exc:
        logger.exception("widget_hubspot_handoff_start_failed", org_id=auth.org_id, widget_id=str(auth.key_id))
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={"error": {"type": "handoff_error", "message": "Could not start HubSpot handoff"}},
        ) from exc
    return HubSpotHandoffResponse(**result)


@router.post("/widget-handoffs/hubspot/messages", response_model=HubSpotHandoffMessageResponse)
async def send_widget_hubspot_handoff_message(
    request: SendHubSpotHandoffMessageRequest,
    auth: PartnerAuthContext = Depends(get_partner_key),
    db: AsyncSession = Depends(get_db),
) -> HubSpotHandoffMessageResponse:
    _require_widget_auth(auth)
    try:
        result = await send_handoff_visitor_message(
            db,
            org_id=auth.org_id,
            widget_public_id=str(auth.key_id),
            session_key=auth.session_key or "",
            content=request.content,
            visitor_name=request.visitor_name,
        )
    except RuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"error": {"type": "handoff_error", "message": "No active HubSpot handoff"}},
        ) from exc
    except Exception as exc:
        logger.exception("widget_hubspot_handoff_message_failed", org_id=auth.org_id, widget_id=str(auth.key_id))
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={"error": {"type": "handoff_error", "message": "Could not send HubSpot handoff message"}},
        ) from exc
    return HubSpotHandoffMessageResponse(**result)


@router.get("/widget-handoffs/hubspot/events")
async def stream_widget_hubspot_handoff_events(
    last_event_id: int = 0,
    auth: PartnerAuthContext = Depends(get_partner_key),
    db: AsyncSession = Depends(get_db),
) -> StreamingResponse:
    _require_widget_auth(auth)
    handoff_session_id = await get_active_handoff_session_id(
        db,
        org_id=auth.org_id,
        widget_public_id=str(auth.key_id),
        session_key=auth.session_key or "",
    )
    if handoff_session_id is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": {"type": "handoff_error", "message": "No active HubSpot handoff"}},
        )

    async def _events() -> AsyncGenerator[bytes]:
        seen_id = last_event_id
        for message in await list_visible_handoff_messages(
            db,
            handoff_session_id=handoff_session_id,
            after_id=last_event_id,
        ):
            seen_id = max(seen_id, int(message["id"]))
            yield f"id: {message['id']}\nevent: message\ndata: {json.dumps(message)}\n\n".encode()

        redis = await get_redis_pool()
        if redis is None:
            while True:
                await asyncio.sleep(15)
                yield b": heartbeat\n\n"

        pubsub = redis.pubsub()
        await pubsub.subscribe(f"widget_handoff:{handoff_session_id}")
        try:
            while True:
                event = await pubsub.get_message(ignore_subscribe_messages=True, timeout=15)
                if event is None:
                    yield b": heartbeat\n\n"
                    continue
                raw_data = event.get("data")
                if not isinstance(raw_data, str):
                    continue
                try:
                    payload = json.loads(raw_data)
                except json.JSONDecodeError:
                    continue
                event_id = int(payload.get("id") or 0)
                if event_id and event_id <= seen_id:
                    continue
                if event_id:
                    seen_id = event_id
                yield f"id: {event_id}\nevent: message\ndata: {json.dumps(payload)}\n\n".encode()
        finally:
            await pubsub.unsubscribe(f"widget_handoff:{handoff_session_id}")
            await pubsub.aclose()

    return StreamingResponse(_events(), media_type="text/event-stream")


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
    if widget_config.get("theme") == "dark":
        css_vars.update(
            {
                "--klai-text-color": "#fffef2",
                "--klai-text-muted": "#fffef299",
                "--klai-background-color": "#191918",
                "--klai-card-color": "#27251f",
                "--klai-border-color": "#3a3831",
            }
        )

    if widget_config.get("widget_position") == "left":
        css_vars["--klai-widget-left"] = "20px"
        css_vars["--klai-widget-right"] = "auto"
    elif widget_config.get("widget_position") == "right":
        css_vars["--klai-widget-left"] = "auto"
        css_vars["--klai-widget-right"] = "20px"

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


def _widget_client_session_id(request: Request | None) -> str | None:
    if request is None:
        return None
    query_params = getattr(request, "query_params", None)
    if query_params is None or not hasattr(query_params, "get"):
        return None
    value = query_params.get("session_id")
    if not isinstance(value, str) or not value:
        return None
    return value if _WIDGET_CLIENT_SESSION_RE.fullmatch(value) else None


def _hubspot_handoff_enabled_for_widget(
    *,
    org: PortalOrg,
    widget_config_data: dict[str, Any],
    origin: str | None = None,
) -> bool:
    if org.slug != _HUBSPOT_HANDOFF_DEV_TENANT_SLUG:
        return False
    if origin is not None and origin != _HUBSPOT_HANDOFF_DEV_ORIGIN:
        return False
    integrations = widget_config_data.get("integrations")
    if not isinstance(integrations, dict):
        return False
    hubspot = integrations.get("hubspot")
    return bool(
        isinstance(hubspot, dict) and hubspot.get("status") == "connected" and hubspot.get("channel_account_id")
    )


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
    redis = await get_redis_pool()
    if redis is not None:
        allowed, retry_after = await check_rate_limit(
            redis, f"widget_mint:{id}", limit_per_minute=10, window_seconds=60
        )
        if not allowed:
            return Response(
                content='{"detail":"Rate limit exceeded"}',
                status_code=429,
                media_type="application/json",
                headers={"Retry-After": str(retry_after)},
            )

    # Look up widget by public widget_id (SPEC-WIDGET-002: own table)
    # REQ-16: soft-deleted widgets are 404 to the public/partner endpoints.
    result = await db.execute(select(Widget).where(Widget.widget_id == id, Widget.deleted_at.is_(None)))
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
        session_id=_widget_client_session_id(request),
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
        "theme": widget_config_data.get("theme", "light"),
        "collect_user_info": widget_config_data.get("collect_user_info", False),
        "widget_position": widget_config_data.get("widget_position", "right"),
        # Display toggles: widget renders the sources block / meta line
        # under each assistant message based on these flags.
        "show_sources": widget_config_data.get("show_sources", True),
        "show_meta": widget_config_data.get("show_meta", False),
        "page_context_enabled": widget_config_data.get("page_context_enabled", False),
        "handoff": {
            "hubspot": {
                "enabled": _hubspot_handoff_enabled_for_widget(
                    org=org,
                    widget_config_data=widget_config_data,
                    origin=origin,
                )
            }
        },
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
    redis = await get_redis_pool()
    if redis is not None:
        allowed, retry_after = await check_rate_limit(
            redis, f"widget_mint:{id}", limit_per_minute=10, window_seconds=60
        )
        if not allowed:
            return Response(
                content='{"detail":"Rate limit exceeded"}',
                status_code=429,
                media_type="application/json",
                headers={"Retry-After": str(retry_after)},
            )

    # REQ-16: soft-deleted widgets are 404 to the public/partner endpoints.
    result = await db.execute(select(Widget).where(Widget.widget_id == id, Widget.deleted_at.is_(None)))
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
        "theme": widget_config_data.get("theme", "light"),
        "collect_user_info": widget_config_data.get("collect_user_info", False),
        "widget_position": widget_config_data.get("widget_position", "right"),
        "show_sources": widget_config_data.get("show_sources", True),
        "show_meta": widget_config_data.get("show_meta", False),
        "page_context_enabled": widget_config_data.get("page_context_enabled", False),
        "name": widget_row.name,
        "description": widget_row.description or "",
        "handoff": {
            "hubspot": {
                "enabled": _hubspot_handoff_enabled_for_widget(
                    org=org,
                    widget_config_data=widget_config_data,
                )
            }
        },
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
    # REQ-16: soft-deleted widgets are 404 to the public/partner endpoints.
    result = await db.execute(select(Widget).where(Widget.widget_id == id, Widget.deleted_at.is_(None)))
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
