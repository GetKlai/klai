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
from pydantic import BaseModel, Field, ValidationError
from redis.exceptions import RedisError
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
from app.core.database import AsyncSessionLocal, get_db, set_tenant
from app.core.permissions import assert_platform_unlocked
from app.models.knowledge_bases import PortalKnowledgeBase
from app.models.portal import PortalOrg
from app.models.widgets import Widget, WidgetKbAccess
from app.services.events import emit_event
from app.services.partner_chat import (
    chat_completion_non_streaming,
    chat_completion_streaming,
    openai_chat_completion_non_streaming,
    openai_chat_completion_streaming,
    retrieve_context,
    safety_refusal_response,
    safety_refusal_stream,
    widget_input_safety_violation,
)
from app.services.partner_rate_limit import check_rate_limit, check_weighted_rate_limit
from app.services.partner_sse import (
    _extract_assistant_text_and_sources,
    _parse_audit_sse_chunk,
)
from app.services.partner_support import (
    _mapping,
    _message_payload,
    _session_payload,
)
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
from app.services.widget_theme import _merge_css_variables

logger = structlog.get_logger()

# Hold references to fire-and-forget tasks to prevent GC (same pattern as partner_dependencies)
_pending: set[asyncio.Task] = set()  # type: ignore[type-arg]

router = APIRouter(prefix="/partner/v1", tags=["Partner API"])

_ALLOWED_MODELS = {"klai-primary", "klai-fast"}
_OPENAI_COMPATIBLE_MODELS = {"klai-primary", "klai-fast", "klai-large"}
_OPENAI_COMPATIBLE_MODEL_ALIASES = {
    "gpt-4o-mini": "klai-fast",
    "gpt-4o": "klai-primary",
    "gpt-4.1-mini": "klai-fast",
    "gpt-4.1": "klai-primary",
}
_OPENAI_COMPATIBLE_ACCEPTED_MODELS = _OPENAI_COMPATIBLE_MODELS | set(_OPENAI_COMPATIBLE_MODEL_ALIASES)
_KNOWLEDGE_CHAT_TRIGGER_FIELDS = {
    "knowledge",
    "knowledge_base_ids",
    "page_context",
    "web_search",
    "web_search_query",
}
_WIDGET_CLIENT_SESSION_RE = re.compile(r"^[A-Za-z0-9_-]{16,80}$")
_HUBSPOT_HANDOFF_DEV_TENANT_SLUG = "getklai"
_HUBSPOT_HANDOFF_DEV_ORIGIN = "https://getklai.getklai.com"
_MAX_WEB_SEARCH_QUERY_CHARS = 512
_OPENAI_COMPAT_MAX_BODY_BYTES = 131_072
_OPENAI_COMPAT_DEFAULT_MAX_TOKENS = 2048
_OPENAI_COMPAT_MAX_TOKENS = 4096
_OPENAI_COMPAT_MAX_N = 1
_OPENAI_COMPAT_FORWARD_FIELDS = {
    "frequency_penalty",
    "logit_bias",
    "logprobs",
    "max_completion_tokens",
    "max_tokens",
    "messages",
    "model",
    "n",
    "parallel_tool_calls",
    "presence_penalty",
    "reasoning_effort",
    "response_format",
    "seed",
    "stop",
    "stream",
    "stream_options",
    "temperature",
    "tool_choice",
    "tools",
    "top_logprobs",
    "top_p",
    "user",
}
_OPENAI_RESPONSES_UNSUPPORTED_FIELDS = {
    "background",
    "conversation",
    "context_management",
    "include",
    "max_tool_calls",
    "metadata",
    "previous_response_id",
    "prompt",
    "prompt_cache_key",
    "prompt_cache_retention",
    "reasoning",
    "service_tier",
    "store",
    "stream_options",
    "truncation",
}


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


def _openai_compatible_model_payload() -> list[dict[str, Any]]:
    # Advertise only the canonical klai-* models. The gpt-* aliases stay
    # ACCEPTED as input (convenience for drop-in clients) but are not listed as
    # klai-owned models — doing so misrepresents a Mistral-backed model as GPT.
    model_ids = sorted(_OPENAI_COMPATIBLE_MODELS)
    return [_openai_compatible_model_entry(model) for model in model_ids]


def _openai_compatible_model_entry(model: str) -> dict[str, Any]:
    if model in _OPENAI_COMPATIBLE_MODELS:
        owned_by = "klai"
    elif model in _OPENAI_COMPATIBLE_MODEL_ALIASES:
        owned_by = "klai-alias"
    else:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": {"type": "not_found", "message": f"Model '{model}' not found"}},
        )
    return {"id": model, "object": "model", "created": 1_735_689_600, "owned_by": owned_by}


async def _openai_compatible_request_body(http_request: Request) -> dict[str, Any]:
    content_length = http_request.headers.get("content-length")
    if content_length is not None:
        try:
            if int(content_length) > _OPENAI_COMPAT_MAX_BODY_BYTES:
                raise HTTPException(
                    status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                    detail={
                        "error": {
                            "type": "invalid_request",
                            "message": "Request body too large",
                        }
                    },
                )
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"error": {"type": "invalid_request", "message": "Content-Length must be an integer"}},
            ) from exc

    chunks: list[bytes] = []
    total = 0
    async for chunk in http_request.stream():
        total += len(chunk)
        if total > _OPENAI_COMPAT_MAX_BODY_BYTES:
            raise HTTPException(
                status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                detail={
                    "error": {
                        "type": "invalid_request",
                        "message": "Request body too large",
                    }
                },
            )
        chunks.append(chunk)

    raw = b"".join(chunks)
    if not raw:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": {"type": "invalid_request", "message": "Request body must be valid JSON"}},
        )
    try:
        body = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": {"type": "invalid_request", "message": "Request body must be valid JSON"}},
        ) from exc
    if not isinstance(body, dict):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": {"type": "invalid_request", "message": "Request body must be a JSON object"}},
        )
    return body


def _validated_openai_compatible_body(body: dict[str, Any]) -> dict[str, Any]:
    forwarded = {field: body[field] for field in _OPENAI_COMPAT_FORWARD_FIELDS if field in body}
    requested_model = forwarded.get("model") or "klai-primary"
    model = _OPENAI_COMPATIBLE_MODEL_ALIASES.get(requested_model, requested_model)
    if model not in _OPENAI_COMPATIBLE_MODELS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": {
                    "type": "invalid_request",
                    "message": f"Model must be one of: {', '.join(sorted(_OPENAI_COMPATIBLE_ACCEPTED_MODELS))}",
                }
            },
        )
    forwarded["model"] = model

    messages = forwarded.get("messages")
    if not isinstance(messages, list) or not messages:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": {"type": "invalid_request", "message": "Messages must be a non-empty array"}},
        )

    n = forwarded.get("n", 1)
    if not isinstance(n, int) or isinstance(n, bool) or n < 1 or n > _OPENAI_COMPAT_MAX_N:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": {"type": "invalid_request", "message": "n must be 1"}},
        )

    for field in ("max_tokens", "max_completion_tokens"):
        value = forwarded.get(field)
        if value is None:
            continue
        if not isinstance(value, int) or isinstance(value, bool) or value < 1:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"error": {"type": "invalid_request", "message": f"{field} must be a positive integer"}},
            )
        if value > _OPENAI_COMPAT_MAX_TOKENS:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "error": {
                        "type": "invalid_request",
                        "message": f"{field} may not exceed {_OPENAI_COMPAT_MAX_TOKENS}",
                    }
                },
            )
    if forwarded.get("max_tokens") is None and forwarded.get("max_completion_tokens") is None:
        forwarded["max_tokens"] = _OPENAI_COMPAT_DEFAULT_MAX_TOKENS

    return forwarded


def _openai_error(status_code: int, message: str, error_type: str = "invalid_request") -> HTTPException:
    return HTTPException(
        status_code=status_code,
        detail={"error": {"type": error_type, "message": message}},
    )


def _uses_knowledge_chat(body: dict[str, Any]) -> bool:
    knowledge = body.get("knowledge")
    if "knowledge" in body and not (isinstance(knowledge, dict) and knowledge.get("enabled") is False):
        return True
    return any(field in body for field in _KNOWLEDGE_CHAT_TRIGGER_FIELDS - {"knowledge"})


def _openai_compatible_enabled(auth: PartnerAuthContext) -> bool:
    return bool(auth.permissions.get("general_chat"))


def _parse_knowledge_chat_request(body: dict[str, Any]) -> ChatCompletionsRequest:
    try:
        return ChatCompletionsRequest.model_validate(body)
    except ValidationError as exc:
        first_error = exc.errors()[0] if exc.errors() else {}
        loc = ".".join(str(part) for part in first_error.get("loc", []))
        msg = str(first_error.get("msg") or "Invalid request")
        detail = f"{loc}: {msg}" if loc else msg
        raise _openai_error(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            f"Invalid knowledge chat request: {detail}",
        ) from exc


def _estimate_openai_input_tokens(body: dict[str, Any]) -> int:
    """Conservative preflight token estimate for abuse limits.

    Exact provider tokenization is model-specific. For a cheap fail-fast guard,
    estimate from serialized OpenAI messages/tools/response_format and round up.
    """
    payload = {
        "messages": body.get("messages", []),
        "tools": body.get("tools"),
        "tool_choice": body.get("tool_choice"),
        "response_format": body.get("response_format"),
    }
    serialized = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return max(1, (len(serialized) + 3) // 4)


def _reserved_openai_output_tokens(body: dict[str, Any]) -> int:
    values = [value for value in (body.get("max_tokens"), body.get("max_completion_tokens")) if value is not None]
    return int(max(values) if values else _OPENAI_COMPAT_DEFAULT_MAX_TOKENS)


def _responses_text_from_content(content: object) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        raise _openai_error(status.HTTP_400_BAD_REQUEST, "Responses content must be a string or an array")

    parts: list[str] = []
    for part in content:
        if isinstance(part, str):
            parts.append(part)
            continue
        if not isinstance(part, dict):
            raise _openai_error(status.HTTP_400_BAD_REQUEST, "Responses content parts must be objects")
        part_type = part.get("type")
        if part_type in {"input_text", "output_text", "text"} and isinstance(part.get("text"), str):
            parts.append(part["text"])
            continue
        raise _openai_error(
            status.HTTP_400_BAD_REQUEST,
            f"Responses content part type '{part_type}' is not supported by this text endpoint",
        )
    return "\n".join(part for part in parts if part)


def _responses_input_to_messages(body: dict[str, Any]) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    instructions = body.get("instructions")
    if instructions is not None:
        if not isinstance(instructions, str):
            raise _openai_error(status.HTTP_400_BAD_REQUEST, "instructions must be a string")
        if instructions.strip():
            messages.append({"role": "system", "content": instructions})

    if "input" not in body:
        raise _openai_error(status.HTTP_400_BAD_REQUEST, "input is required")
    input_value = body["input"]
    if isinstance(input_value, str):
        messages.append({"role": "user", "content": input_value})
        return messages
    if not isinstance(input_value, list) or not input_value:
        raise _openai_error(status.HTTP_400_BAD_REQUEST, "input must be a string or a non-empty array")

    for item in input_value:
        if isinstance(item, str):
            messages.append({"role": "user", "content": item})
            continue
        if not isinstance(item, dict):
            raise _openai_error(status.HTTP_400_BAD_REQUEST, "input items must be strings or objects")

        item_type = item.get("type")
        if item_type in {None, "message"}:
            role = item.get("role", "user")
            if role not in {"system", "developer", "user", "assistant", "tool"}:
                raise _openai_error(status.HTTP_400_BAD_REQUEST, f"input message role '{role}' is not supported")
            chat_role = "system" if role == "developer" else role
            message: dict[str, Any] = {
                "role": chat_role,
                "content": _responses_text_from_content(item.get("content", "")),
            }
            if chat_role == "tool" and isinstance(item.get("tool_call_id"), str):
                message["tool_call_id"] = item["tool_call_id"]
            messages.append(message)
            continue

        if item_type == "function_call_output":
            call_id = item.get("call_id") or item.get("tool_call_id")
            if not isinstance(call_id, str):
                raise _openai_error(status.HTTP_400_BAD_REQUEST, "function_call_output requires call_id")
            output = item.get("output", "")
            messages.append({"role": "tool", "tool_call_id": call_id, "content": str(output)})
            continue

        raise _openai_error(status.HTTP_400_BAD_REQUEST, f"Responses input item type '{item_type}' is not supported")

    return messages


def _responses_format_to_chat_response_format(body: dict[str, Any]) -> dict[str, Any] | None:
    text_options = body.get("text")
    if text_options is None:
        return None
    if not isinstance(text_options, dict):
        raise _openai_error(status.HTTP_400_BAD_REQUEST, "text must be an object")
    text_format = text_options.get("format")
    if text_format is None:
        return None
    if not isinstance(text_format, dict):
        raise _openai_error(status.HTTP_400_BAD_REQUEST, "text.format must be an object")

    format_type = text_format.get("type")
    if format_type == "text":
        return None
    if format_type == "json_object":
        return {"type": "json_object"}
    if format_type == "json_schema":
        json_schema = {
            key: text_format[key] for key in ("name", "description", "schema", "strict") if key in text_format
        }
        if not json_schema.get("name") or not isinstance(json_schema.get("schema"), dict):
            raise _openai_error(status.HTTP_400_BAD_REQUEST, "text.format json_schema requires name and schema")
        return {"type": "json_schema", "json_schema": json_schema}
    raise _openai_error(status.HTTP_400_BAD_REQUEST, f"text.format type '{format_type}' is not supported")


def _responses_tools_to_chat_tools(body: dict[str, Any]) -> list[dict[str, Any]] | None:
    tools = body.get("tools")
    if tools is None:
        return None
    if not isinstance(tools, list):
        raise _openai_error(status.HTTP_400_BAD_REQUEST, "tools must be an array")

    chat_tools: list[dict[str, Any]] = []
    for tool in tools:
        if not isinstance(tool, dict):
            raise _openai_error(status.HTTP_400_BAD_REQUEST, "tools entries must be objects")
        tool_type = tool.get("type")
        if tool_type != "function":
            raise _openai_error(
                status.HTTP_400_BAD_REQUEST,
                f"Responses hosted tool type '{tool_type}' is not supported by this endpoint",
            )
        if isinstance(tool.get("function"), dict):
            chat_tools.append({"type": "function", "function": tool["function"]})
            continue
        name = tool.get("name")
        if not isinstance(name, str) or not name:
            raise _openai_error(status.HTTP_400_BAD_REQUEST, "function tools require a name")
        function: dict[str, Any] = {"name": name}
        if isinstance(tool.get("description"), str):
            function["description"] = tool["description"]
        parameters = tool.get("parameters")
        function["parameters"] = parameters if isinstance(parameters, dict) else {"type": "object", "properties": {}}
        chat_tools.append({"type": "function", "function": function})
    return chat_tools


def _responses_tool_choice_to_chat_tool_choice(body: dict[str, Any]) -> object | None:
    if "tool_choice" not in body:
        return None
    tool_choice = body["tool_choice"]
    if isinstance(tool_choice, str):
        if tool_choice not in {"auto", "none", "required"}:
            raise _openai_error(status.HTTP_400_BAD_REQUEST, f"tool_choice '{tool_choice}' is not supported")
        return tool_choice
    if (
        isinstance(tool_choice, dict)
        and tool_choice.get("type") == "function"
        and isinstance(tool_choice.get("name"), str)
    ):
        return {"type": "function", "function": {"name": tool_choice["name"]}}
    raise _openai_error(status.HTTP_400_BAD_REQUEST, "tool_choice must be auto, none, required, or a function choice")


def _responses_to_chat_body(body: dict[str, Any]) -> dict[str, Any]:
    unsupported = sorted(field for field in _OPENAI_RESPONSES_UNSUPPORTED_FIELDS if field in body)
    if unsupported:
        raise _openai_error(
            status.HTTP_400_BAD_REQUEST,
            f"Responses field(s) not supported by this stateless endpoint: {', '.join(unsupported)}",
        )
    if _uses_knowledge_chat(body):
        raise _openai_error(
            status.HTTP_400_BAD_REQUEST,
            "Klai knowledge extensions are supported on /chat/completions, not /responses",
        )
    if body.get("stream") is True and "tools" in body:
        raise _openai_error(
            status.HTTP_400_BAD_REQUEST,
            "Streaming function tool calls are not supported on /responses; use stream=false",
        )

    chat_body: dict[str, Any] = {
        "model": body.get("model") or "klai-primary",
        "messages": _responses_input_to_messages(body),
        "stream": bool(body.get("stream", False)),
    }
    passthrough_fields = {
        "frequency_penalty",
        "logit_bias",
        "parallel_tool_calls",
        "presence_penalty",
        "seed",
        "stop",
        "temperature",
        "top_logprobs",
        "top_p",
        "user",
    }
    for field in passthrough_fields:
        if field in body:
            chat_body[field] = body[field]
    safety_identifier = body.get("safety_identifier")
    if safety_identifier is not None:
        if not isinstance(safety_identifier, str) or not safety_identifier.strip():
            raise _openai_error(status.HTTP_400_BAD_REQUEST, "safety_identifier must be a non-empty string")
        chat_body["user"] = safety_identifier
    if "max_output_tokens" in body:
        chat_body["max_tokens"] = body["max_output_tokens"]
    if response_format := _responses_format_to_chat_response_format(body):
        chat_body["response_format"] = response_format
    if tools := _responses_tools_to_chat_tools(body):
        chat_body["tools"] = tools
    if (tool_choice := _responses_tool_choice_to_chat_tool_choice(body)) is not None:
        chat_body["tool_choice"] = tool_choice
    return chat_body


def _chat_completion_text(chat_result: dict[str, Any]) -> str:
    choices = chat_result.get("choices")
    if not isinstance(choices, list) or not choices:
        return ""
    first = choices[0]
    if not isinstance(first, dict):
        return ""
    message = first.get("message")
    if not isinstance(message, dict):
        return ""
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return _responses_text_from_content(content)
    return ""


def _responses_usage_from_chat_usage(chat_usage: object) -> dict[str, Any] | None:
    """Translate a Chat Completions ``usage`` block to the Responses usage shape.

    The OpenAI Responses API uses ``input_tokens``/``output_tokens`` with required
    ``*_details`` sub-objects — NOT Chat Completions' ``prompt_tokens``/
    ``completion_tokens``. Passing the chat shape through makes the SDK's
    ``ResponseUsage`` parse every canonical field to ``None`` (broken metering).
    """
    if not isinstance(chat_usage, dict):
        return None
    prompt = chat_usage.get("prompt_tokens")
    completion = chat_usage.get("completion_tokens")
    total = chat_usage.get("total_tokens")
    if prompt is None and completion is None and total is None:
        return None
    input_tokens = int(prompt or 0)
    output_tokens = int(completion or 0)
    total_tokens = int(total if total is not None else input_tokens + output_tokens)
    prompt_details = chat_usage.get("prompt_tokens_details")
    cached_tokens = prompt_details.get("cached_tokens", 0) if isinstance(prompt_details, dict) else 0
    return {
        "input_tokens": input_tokens,
        "input_tokens_details": {"cached_tokens": int(cached_tokens or 0)},
        "output_tokens": output_tokens,
        "output_tokens_details": {"reasoning_tokens": 0},
        "total_tokens": total_tokens,
    }


def _responses_object(
    *,
    response_id: str,
    model: str,
    status: str,
    output: list[dict[str, Any]],
    output_text: str,
    created_at: int,
    usage: dict[str, Any] | None,
) -> dict[str, Any]:
    """Build a Responses ``Response`` object carrying every SDK-required field.

    ``parallel_tool_calls``, ``tool_choice`` and ``tools`` are required by the
    SDK's ``Response`` model; omitting them makes those typed attributes parse to
    ``None`` on the client. The remaining keys are common Optionals included so a
    client reading them gets sensible values rather than surprising ``None``s.
    """
    return {
        "id": response_id,
        "object": "response",
        "created_at": created_at,
        "status": status,
        "model": model,
        "output": output,
        "output_text": output_text,
        "usage": usage,
        "parallel_tool_calls": True,
        "tool_choice": "auto",
        "tools": [],
        "instructions": None,
        "metadata": {},
        "temperature": None,
        "top_p": None,
        "max_output_tokens": None,
        "previous_response_id": None,
        "error": None,
        "incomplete_details": None,
    }


def _responses_payload_from_chat_result(chat_result: dict[str, Any], *, requested_model: str) -> dict[str, Any]:
    output_text = _chat_completion_text(chat_result)
    message_id = f"msg_{uuid.uuid4().hex}"
    response_id = str(chat_result.get("id") or f"resp_{uuid.uuid4().hex}")
    model = str(chat_result.get("model") or requested_model)
    output: list[dict[str, Any]] = []
    if output_text:
        output.append(
            {
                "id": message_id,
                "type": "message",
                "status": "completed",
                "role": "assistant",
                "content": [{"type": "output_text", "text": output_text, "annotations": []}],
            }
        )

    choices = chat_result.get("choices")
    if isinstance(choices, list) and choices and isinstance(choices[0], dict):
        message = choices[0].get("message")
        if isinstance(message, dict) and isinstance(message.get("tool_calls"), list):
            output.extend(_responses_function_calls_from_chat_tool_calls(message["tool_calls"]))
    if not output:
        output.append(
            {
                "id": message_id,
                "type": "message",
                "status": "completed",
                "role": "assistant",
                "content": [{"type": "output_text", "text": "", "annotations": []}],
            }
        )

    return _responses_object(
        response_id=response_id,
        model=model,
        status="completed",
        output=output,
        output_text=output_text,
        created_at=int(datetime.now(UTC).timestamp()),
        usage=_responses_usage_from_chat_usage(chat_result.get("usage")),
    )


def _responses_function_calls_from_chat_tool_calls(tool_calls: list[object]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for tool_call in tool_calls:
        if not isinstance(tool_call, dict):
            continue
        function = tool_call.get("function")
        if not isinstance(function, dict):
            continue
        call_id = str(tool_call.get("id") or f"call_{uuid.uuid4().hex}")
        output.append(
            {
                "type": "function_call",
                "id": call_id,
                "call_id": call_id,
                "name": function.get("name") or "",
                "arguments": function.get("arguments") or "{}",
                "status": "completed",
            }
        )
    return output


def _responses_sse(event: str, payload: dict[str, Any]) -> bytes:
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n".encode()


def _chat_sse_delta_text(chunk: bytes) -> str:
    text_parts: list[str] = []
    for raw_event in chunk.decode("utf-8", errors="ignore").split("\n\n"):
        for line in raw_event.splitlines():
            if not line.startswith("data:"):
                continue
            payload_text = line.removeprefix("data:").strip()
            if not payload_text or payload_text == "[DONE]":
                continue
            try:
                payload = json.loads(payload_text)
            except json.JSONDecodeError:
                continue
            choices = payload.get("choices")
            if not isinstance(choices, list) or not choices:
                continue
            first = choices[0]
            if not isinstance(first, dict):
                continue
            delta = first.get("delta")
            if isinstance(delta, dict) and isinstance(delta.get("content"), str):
                text_parts.append(delta["content"])
    return "".join(text_parts)


async def _responses_stream_from_chat_response(
    chat_response: StreamingResponse, *, requested_model: str
) -> AsyncGenerator[bytes]:
    response_id = f"resp_{uuid.uuid4().hex}"
    message_id = f"msg_{uuid.uuid4().hex}"
    created_at = int(datetime.now(UTC).timestamp())
    full_text: list[str] = []
    sequence = 0

    def event(name: str, **fields: Any) -> bytes:
        # Every Responses streaming event MUST carry ``type`` (the SDK's union
        # discriminator) and a monotonic ``sequence_number`` in its JSON payload,
        # else the SDK drops the event (type=None) and the high-level
        # ``responses.stream()`` helper raises.
        nonlocal sequence
        payload = {"type": name, "sequence_number": sequence, **fields}
        sequence += 1
        return _responses_sse(name, payload)

    def response_obj(status: str, output: list[dict[str, Any]]) -> dict[str, Any]:
        return _responses_object(
            response_id=response_id,
            model=requested_model,
            status=status,
            output=output,
            output_text="".join(full_text),
            created_at=created_at,
            usage=None,
        )

    message_in_progress = {
        "id": message_id,
        "type": "message",
        "status": "in_progress",
        "role": "assistant",
        "content": [],
    }
    yield event("response.created", response=response_obj("in_progress", []))
    yield event("response.in_progress", response=response_obj("in_progress", []))
    yield event("response.output_item.added", output_index=0, item=message_in_progress)
    yield event(
        "response.content_part.added",
        item_id=message_id,
        output_index=0,
        content_index=0,
        part={"type": "output_text", "text": "", "annotations": []},
    )
    async for chunk in chat_response.body_iterator:
        if not isinstance(chunk, bytes):
            chunk = str(chunk).encode()
        if delta := _chat_sse_delta_text(chunk):
            full_text.append(delta)
            yield event(
                "response.output_text.delta",
                item_id=message_id,
                output_index=0,
                content_index=0,
                delta=delta,
                logprobs=[],
            )
    output_text = "".join(full_text)
    yield event(
        "response.output_text.done",
        item_id=message_id,
        output_index=0,
        content_index=0,
        text=output_text,
        logprobs=[],
    )
    final_part = {"type": "output_text", "text": output_text, "annotations": []}
    yield event(
        "response.content_part.done",
        item_id=message_id,
        output_index=0,
        content_index=0,
        part=final_part,
    )
    completed_message = {
        "id": message_id,
        "type": "message",
        "status": "completed",
        "role": "assistant",
        "content": [final_part],
    }
    yield event("response.output_item.done", output_index=0, item=completed_message)
    yield event("response.completed", response=response_obj("completed", [completed_message]))
    yield b"data: [DONE]\n\n"


async def _enforce_openai_compatible_usage_limits(
    *,
    auth: PartnerAuthContext,
    body: dict[str, Any],
) -> None:
    input_tokens = _estimate_openai_input_tokens(body)
    if input_tokens > settings.partner_openai_max_input_tokens:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": {
                    "type": "invalid_request",
                    "message": f"estimated input tokens may not exceed {settings.partner_openai_max_input_tokens}",
                }
            },
        )

    redis_pool = await get_redis_pool()
    if redis_pool is None:
        logger.warning("partner_openai_rate_limit_unavailable", org_id=auth.org_id, key_id=str(auth.key_id))
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"error": {"type": "service_unavailable", "message": "Rate limit service unavailable"}},
        )

    try:
        rpm_allowed, rpm_retry_after = await check_rate_limit(
            redis_pool,
            f"openai:{auth.key_id}",
            settings.partner_openai_rpm_limit,
        )
    except (RedisError, ConnectionError, OSError) as exc:
        logger.warning(
            "partner_openai_rate_limit_unavailable",
            org_id=auth.org_id,
            key_id=str(auth.key_id),
            exc_info=True,
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"error": {"type": "service_unavailable", "message": "Rate limit service unavailable"}},
        ) from exc
    if not rpm_allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={"error": {"type": "rate_limit_error", "message": "OpenAI-compatible chat rate limit exceeded"}},
            headers={"Retry-After": str(rpm_retry_after)},
        )

    token_cost = input_tokens + _reserved_openai_output_tokens(body)
    try:
        tpm_allowed, tpm_retry_after = await check_weighted_rate_limit(
            redis_pool,
            f"openai_tpm:{auth.key_id}",
            token_cost,
            settings.partner_openai_tpm_limit,
        )
    except (RedisError, ConnectionError, OSError) as exc:
        logger.warning(
            "partner_openai_rate_limit_unavailable",
            org_id=auth.org_id,
            key_id=str(auth.key_id),
            exc_info=True,
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"error": {"type": "service_unavailable", "message": "Rate limit service unavailable"}},
        ) from exc
    if not tpm_allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={"error": {"type": "rate_limit_error", "message": "OpenAI-compatible token rate limit exceeded"}},
            headers={"Retry-After": str(tpm_retry_after)},
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


@router.get("/models")
async def openai_compatible_models(
    auth: PartnerAuthContext = Depends(get_partner_key),
) -> dict[str, Any]:
    """List models available through the OpenAI-compatible partner passthrough."""
    require_permission(auth, "general_chat")
    return {"object": "list", "data": _openai_compatible_model_payload()}


@router.get("/models/{model}")
async def openai_compatible_model(
    model: str,
    auth: PartnerAuthContext = Depends(get_partner_key),
) -> dict[str, Any]:
    """Retrieve a model available through the OpenAI-compatible partner passthrough."""
    require_permission(auth, "general_chat")
    return _openai_compatible_model_entry(model)


async def _openai_compatible_chat_completions_from_body(
    body: dict[str, Any],
    *,
    auth: PartnerAuthContext,
) -> Response | dict[str, Any]:
    require_permission(auth, "general_chat")
    validated_body = _validated_openai_compatible_body(body)
    await _enforce_openai_compatible_usage_limits(auth=auth, body=validated_body)
    if bool(validated_body.get("stream", False)):
        return await openai_chat_completion_streaming(validated_body, settings, org_id=auth.org_id)
    return await openai_chat_completion_non_streaming(validated_body, settings, org_id=auth.org_id)


async def openai_compatible_chat_completions(
    http_request: Request,
    auth: PartnerAuthContext = Depends(get_partner_key),
) -> Response | dict[str, Any]:
    """Internal request-shaped helper for the canonical chat route tests."""
    body = await _openai_compatible_request_body(http_request)
    return await _openai_compatible_chat_completions_from_body(body, auth=auth)


@router.post("/responses", response_model=None)
async def openai_compatible_responses(
    http_request: Request,
    auth: PartnerAuthContext = Depends(get_partner_key),
) -> Response | dict[str, Any]:
    """OpenAI Responses-compatible text endpoint backed by the general passthrough.

    This is a stateless adapter over LiteLLM chat completions. It supports the
    common Responses text surface and function tools, and explicitly rejects
    stateful/background/hosted-tool features instead of pretending they work.
    """
    require_permission(auth, "general_chat")
    responses_body = await _openai_compatible_request_body(http_request)
    chat_body = _validated_openai_compatible_body(_responses_to_chat_body(responses_body))
    await _enforce_openai_compatible_usage_limits(auth=auth, body=chat_body)

    requested_model = str(responses_body.get("model") or "klai-primary")
    if bool(chat_body.get("stream", False)):
        upstream_response = await openai_chat_completion_streaming(chat_body, settings, org_id=auth.org_id)
        if isinstance(upstream_response, StreamingResponse):
            return StreamingResponse(
                _responses_stream_from_chat_response(upstream_response, requested_model=requested_model),
                media_type="text/event-stream",
            )
        return upstream_response

    upstream_result = await openai_chat_completion_non_streaming(chat_body, settings, org_id=auth.org_id)
    if isinstance(upstream_result, Response):
        return upstream_result
    return _responses_payload_from_chat_result(upstream_result, requested_model=requested_model)


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


@router.post("/chat/completions", response_model=None)
async def canonical_chat_completions(
    http_request: Request,
    auth: PartnerAuthContext = Depends(get_partner_key),
    db: AsyncSession = Depends(get_db),
) -> Response | dict[str, Any]:
    """OpenAI SDK-compatible chat endpoint with Klai knowledge extensions.

    The canonical OpenAI path defaults to general model passthrough when the
    partner key has ``general_chat`` and the request does not opt into Klai
    knowledge features. Supplying ``knowledge``/KB/web-search fields keeps the
    existing knowledge-grounded Klai behavior.
    """
    body = await _openai_compatible_request_body(http_request)
    if _openai_compatible_enabled(auth) and not _uses_knowledge_chat(body):
        return await _openai_compatible_chat_completions_from_body(body, auth=auth)

    knowledge_request = _parse_knowledge_chat_request(body)
    return await chat_completions(
        request=knowledge_request,
        http_request=http_request,
        auth=auth,
        db=db,
    )


async def chat_completions(  # noqa: C901
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
    except httpx.HTTPStatusError as exc:
        logger.warning(
            "partner_chat_retrieval_upstream_status_error",
            org_id=auth.org_id,
            status_code=exc.response.status_code if exc.response is not None else None,
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={"error": {"type": "upstream_error", "message": "Retrieval service error"}},
        ) from exc
    except httpx.RequestError as exc:
        logger.warning(
            "partner_chat_retrieval_upstream_request_error",
            org_id=auth.org_id,
            error_type=type(exc).__name__,
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={"error": {"type": "upstream_error", "message": "Retrieval service unavailable"}},
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


def _request_origin(request: Request | None) -> str | None:
    headers = getattr(request, "headers", None)
    if headers is None or not hasattr(headers, "get"):
        return None
    value = headers.get("origin") or headers.get("Origin")
    return value if isinstance(value, str) and value else None


def _hubspot_handoff_forbidden() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail={"error": {"type": "permission_error", "message": "HubSpot handoff is not enabled for this widget"}},
    )


async def _require_hubspot_widget_handoff_enabled(
    *,
    db: AsyncSession,
    auth: PartnerAuthContext,
    request: Request | None,
) -> None:
    _require_widget_auth(auth)
    if _request_origin(request) != _HUBSPOT_HANDOFF_DEV_ORIGIN:
        raise _hubspot_handoff_forbidden()

    result = await db.execute(
        select(Widget, PortalOrg)
        .join(PortalOrg, PortalOrg.id == Widget.org_id)
        .where(
            Widget.widget_id == str(auth.key_id),
            Widget.org_id == auth.org_id,
            Widget.deleted_at.is_(None),
        )
    )
    row = result.first()
    if row is None:
        raise _hubspot_handoff_forbidden()

    widget_row = row[0]
    org = row[1]
    widget_config_data = widget_row.widget_config or {}
    if not _hubspot_handoff_enabled_for_widget(
        org=org,
        widget_config_data=widget_config_data,
        origin=_request_origin(request),
    ):
        raise _hubspot_handoff_forbidden()


@router.post("/widget-handoffs/hubspot/start", response_model=HubSpotHandoffResponse)
async def start_widget_hubspot_handoff(
    http_request: Request,
    request: StartHubSpotHandoffRequest,
    auth: PartnerAuthContext = Depends(get_partner_key),
    db: AsyncSession = Depends(get_db),
) -> HubSpotHandoffResponse:
    await _require_hubspot_widget_handoff_enabled(db=db, auth=auth, request=http_request)
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
    http_request: Request,
    request: SendHubSpotHandoffMessageRequest,
    auth: PartnerAuthContext = Depends(get_partner_key),
    db: AsyncSession = Depends(get_db),
) -> HubSpotHandoffMessageResponse:
    await _require_hubspot_widget_handoff_enabled(db=db, auth=auth, request=http_request)
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
    request: Request,
    last_event_id: int = 0,
) -> StreamingResponse:
    async with AsyncSessionLocal() as db:
        auth = await get_partner_key(request=request, db=db)
        await _require_hubspot_widget_handoff_enabled(db=db, auth=auth, request=request)
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

    channel = f"widget_handoff:{handoff_session_id}"

    async def _events() -> AsyncGenerator[bytes]:
        seen_id = last_event_id
        redis = await get_redis_pool()
        pubsub: Any | None = None
        try:
            if redis is not None:
                pubsub = redis.pubsub()
                await pubsub.subscribe(channel)

            async with AsyncSessionLocal() as replay_db:
                replay_messages = await list_visible_handoff_messages(
                    replay_db,
                    handoff_session_id=handoff_session_id,
                    after_id=last_event_id,
                )

            for message in replay_messages:
                seen_id = max(seen_id, int(message["id"]))
                yield f"id: {message['id']}\nevent: message\ndata: {json.dumps(message)}\n\n".encode()

            if pubsub is None:
                while True:
                    await asyncio.sleep(15)
                    yield b": heartbeat\n\n"

            assert pubsub is not None
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
            if pubsub is not None:
                await pubsub.unsubscribe(channel)
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
        headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization, X-Klai-Widget-Session-Id"
        headers["Access-Control-Max-Age"] = "86400"
    return headers


def _widget_client_session_id(request: Request | None) -> str | None:
    if request is None:
        return None
    headers = getattr(request, "headers", None)
    if headers is not None and hasattr(headers, "get"):
        header_value = headers.get("x-klai-widget-session-id") or headers.get("X-Klai-Widget-Session-Id")
        if isinstance(header_value, str) and header_value:
            return header_value if _WIDGET_CLIENT_SESSION_RE.fullmatch(header_value) else None
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
