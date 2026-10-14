"""Partner API router.

SPEC-API-001: External partner endpoints under /partner/v1/*.
Authenticated via partner API keys (Bearer pk_live_...).
"""

from __future__ import annotations

import asyncio
import re
import time
import uuid
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from typing import Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request, status
from klai_chat_prompts.kb_modes import build_template_instructions_block
from klai_chat_prompts.language import identify_text_language, resolve_conversation_language
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import Response, StreamingResponse

from app.api.partner_dependencies import (
    PartnerAuthContext,
    get_partner_key,
    require_permission,
    validate_kb_access,
)
from app.api.partner_feedback import router as feedback_router
from app.api.partner_hubspot import router as hubspot_router
from app.api.partner_openai_compat import (
    _OPENAI_COMPAT_MAX_BODY_BYTES,
    _general_passthrough,
    _is_librechat_title_request,
    _message_text,
    _openai_compatible_chat_completions_from_body,
    _openai_compatible_enabled,
    _openai_compatible_request_body,
    _openai_error,
    _uses_knowledge_chat,
)
from app.api.partner_openai_compat import router as openai_compat_router
from app.api.partner_support_sessions import router as support_sessions_router
from app.api.partner_widget_config import router as widget_config_router
from app.api.widget_public import WIDGET_PUBLIC_CORS
from app.core.config import settings
from app.core.database import get_db
from app.models.knowledge_bases import PortalKnowledgeBase
from app.models.widgets import Widget
from app.services import case_file
from app.services.chat_attachments import process_chat_attachments
from app.services.chat_profile import ChatProfile, resolve_chat_profile
from app.services.chat_safety import (
    _last_user_message,
    context_safety_violation,
    fixed_reply_response,
    fixed_reply_stream,
    safety_refusal_response,
    safety_refusal_stream,
    widget_input_safety_violation,
)
from app.services.internal_turn import InternalInputs, internal_turn
from app.services.partner_chat import chat_completion_non_streaming, chat_completion_streaming
from app.services.pasted_correspondence import detect_pasted_correspondence
from app.services.prompt_templates import internal_turn_settings
from app.services.retrieval_context import KnowledgeTurn
from app.services.web_search import build_web_results_block, search_web, web_results_as_chunks
from app.services.widget_audit import (
    hash_audit_value,
    last_case,
    patch_answer_signals,
    record_internal_turn,
    record_widget_turn,
    session_key_from_token,
)
from app.services.widget_reply import AnswerUpstreamError, Reply, error_frames, reply_body, reply_frames
from app.services.widget_turn import TurnInputs, widget_turn

logger = structlog.get_logger()

# Hold references to fire-and-forget tasks to prevent GC (same pattern as partner_dependencies)
_pending: set[asyncio.Task] = set()  # type: ignore[type-arg]

router = APIRouter(prefix="/partner/v1", tags=["Partner API"])

_ALLOWED_MODELS = {"klai-primary", "klai-fast"}
# General-passthrough-only fields: ChatCompletionsRequest (the knowledge-path
# Pydantic model) silently drops unknown fields, so a partner sending these
# alongside a knowledge field would get HTTP 200 with their schema quietly
# ignored instead of an error. Fail loudly instead — see
# canonical_chat_completions. ``tools``/``tool_choice`` used to be here too;
# the knowledge path now forwards them (see ChatCompletionsRequest.tools),
# so they are no longer passthrough-only.
_PASSTHROUGH_ONLY_FIELDS = {
    "parallel_tool_calls",
    "prompt_cache_key",
    "response_format",
}
_MAX_WEB_SEARCH_QUERY_CHARS = 512
# LibreChat sends attachments base64-encoded inside the body: a PDF at the
# 20 MB conversion limit (chat_pdf_max_bytes) is about 27 MB encoded, plus the
# conversation. Only internal-chat keys get this ceiling.
_INTERNAL_CHAT_MAX_BODY_BYTES = 32 * 1024 * 1024


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
    # the system prompt for this turn. Default off. Honoured on the internal
    # chat only (gated by the key's ``web_search`` permission); refused with a 400 elsewhere.
    web_search: bool = False
    # Optional explicit query for the web search. Integrations should pass a
    # concise, natural-language query here (e.g. the ticket subject) — it makes
    # a far better keyword query than ``knowledge.query``, which is tuned for KB
    # embedding retrieval and is often a long labelled blob that returns nothing
    # from a keyword engine. Falls back to the last user message when omitted.
    web_search_query: str | None = Field(default=None, max_length=_MAX_WEB_SEARCH_QUERY_CHARS)
    # Per-turn visitor consent for the helpdesk widget's broad mode: when this
    # turn's help-article retrieval comes up empty or weak, answer from general
    # domain knowledge instead of refusing — labelled as general knowledge,
    # never citing help articles, and never for company-specific claims (the
    # boundary lives in the prompt profile). Honoured on every widget and
    # partner turn, which all run widget_turn; the internal chat ignores it.
    # Retrieval always runs first regardless, so knowledge gaps keep being
    # recorded even when a broad answer follows.
    broad_mode: bool = False
    # Widget feedback addressing: the widget client generates one random hex
    # id per assistant turn and sends it here; the audit writer stores it on
    # the assistant ``widget_messages`` row, and POST /partner/v1/widget/
    # feedback later addresses the turn with it. Ignored for partner API keys
    # (their chat turns are not audited to widget_messages), so partner
    # behaviour is unchanged.
    widget_turn_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{16,64}$")
    # Contact details from the widget's pre-chat step (collect_user_info), sent
    # on every turn of that conversation. They are written to the conversation
    # audit row and never forwarded to the model: the answer does not depend on
    # who is asking, and keeping them out of ``messages`` keeps
    # first_user_query the real question. Ignored for partner API keys, whose
    # turns are not audited. Same limits as the HubSpot handoff request.
    visitor_name: str | None = Field(default=None, max_length=120)
    visitor_email: str | None = Field(default=None, max_length=254)
    # Internal chat only; a widget or partner key gets a 400 (chat_completions).
    # Forwarded to LiteLLM unmodified except for a Strict-KB profile, which
    # strips web-search tools (see widget_write.write_reply). Client-side
    # tool execution: portal never calls a tool itself, it only relays the
    # model's tool_calls deltas and accepts the tool-role results back.
    tools: list[dict[str, Any]] | None = None
    tool_choice: Any | None = None


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


async def _widget_off_topic(auth: PartnerAuthContext, db: AsyncSession) -> tuple[str, str]:
    """Return this widget's (subjects it does not answer, reply to give instead).

    Both empty means the feature is off, which is every widget until an admin
    fills them in. Same shape and same widget-JWT restriction as
    :func:`_widget_page_context_enabled`.
    """
    if not str(auth.key_id).startswith("wgt_"):
        return "", ""
    result = await db.execute(
        select(Widget.widget_config).where(
            Widget.widget_id == auth.key_id,
            Widget.org_id == auth.org_id,
        )
    )
    config = result.scalar_one_or_none() or {}
    if not isinstance(config, dict):
        return "", ""
    subjects = config.get("off_topic_subjects")
    reply = config.get("off_topic_reply")
    return (
        subjects.strip() if isinstance(subjects, str) else "",
        reply.strip() if isinstance(reply, str) else "",
    )


async def _widget_tone_register(auth: PartnerAuthContext, db: AsyncSession) -> str:
    """Return this widget's customer-facing register: ``"restrained"`` (default)
    or ``"expressive"``.

    Same shape as :func:`_widget_page_context_enabled`, same restriction to
    widget JWT callers. The value selects between the two SUPPORT profiles;
    anything unrecognised falls back to
    ``"restrained"``, so a hand-edited or stale widget_config can never
    silently switch a live widget to the expressive voice.
    """
    if not str(auth.key_id).startswith("wgt_"):
        return "restrained"
    result = await db.execute(
        select(Widget.widget_config).where(
            Widget.widget_id == auth.key_id,
            Widget.org_id == auth.org_id,
        )
    )
    config = result.scalar_one_or_none() or {}
    register = config.get("tone_register") if isinstance(config, dict) else None
    return register if register == "expressive" else "restrained"


def _write_widget_record(turn: KnowledgeTurn | None, **fields: Any) -> None:
    """One record per widget turn, written at once; its signals completed when the later check is done.

    The check on a reply written from chosen passages runs after the reply
    went out (``turn.grounding_check``, partner_chat.py). The row is written
    first so the transcript keeps its order under a quick follow-up and the
    feedback endpoint finds the turn; the signals it was written with are
    then replaced by the completed ones (``patch_answer_signals``).
    """

    async def write() -> None:
        message_id = await record_widget_turn(**fields)
        if message_id is None or turn is None or turn.grounding_check is None or fields.get("answer_signals") is None:
            return
        try:
            await turn.grounding_check
        except Exception:
            logger.warning("widget_later_check_failed", exc_info=True)
        await patch_answer_signals(
            widget_id=fields["widget_id"], message_id=message_id, answer_signals=fields["answer_signals"]
        )

    task = asyncio.create_task(write())
    _pending.add(task)
    task.add_done_callback(_pending.discard)


def _write_internal_record(org_id: int, answer_signals: dict[str, Any], turn: KnowledgeTurn) -> None:
    """One record per internal turn, written once the turn's own grounding check is done.

    Signals only, never text: LibreChat keeps the conversation. An Open turn's
    check runs after the reply went out (``turn.grounding_check``), so the
    record waits for it rather than being written without its outcome.
    """

    answer_signals["sub_questions"] = len(turn.sub_queries)

    async def write() -> None:
        if turn.grounding_check is not None:
            await turn.grounding_check
        await record_internal_turn(org_id=org_id, answer_signals=answer_signals)

    task = asyncio.create_task(write())
    _pending.add(task)
    task.add_done_callback(_pending.discard)


async def _internal_record_streaming_wrapper(
    inner: AsyncGenerator[bytes], *, org_id: int, answer_signals: dict[str, Any], turn: KnowledgeTurn
) -> AsyncGenerator[bytes]:
    """Pass the stream through, then write the turn's record, also when the client left early."""
    try:
        async for chunk in inner:
            yield chunk
    finally:
        _write_internal_record(org_id, answer_signals, turn)


def _input_safety_block_response(
    request: ChatCompletionsRequest,
    auth: PartnerAuthContext,
    profile: ChatProfile,
) -> Response | dict[str, Any] | None:
    """Refuse a turn whose latest user message, or any tool result, is unsafe.

    Widget and internal turns are checked (the internal chat had both checks in
    the LiteLLM hook); partner-API traffic stays unchecked as before. Tool
    results only exist on the internal surface, where the client runs tools.
    """
    if profile.surface == "partner":
        return None
    safety_reason = widget_input_safety_violation(request.messages)
    if not safety_reason and profile.surface == "internal":
        safety_reason = next(
            (
                reason
                for m in request.messages
                if m.get("role") == "tool"
                and isinstance(m.get("content"), str)
                and (reason := context_safety_violation(m["content"], query=_last_user_message(request.messages) or ""))
            ),
            None,
        )
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
    system_prompt: str,
) -> tuple[str, list[dict], str | None]:
    """Append live web results to the prompt and return them as evidence chunks.

    Opt-in per request (``request.web_search``) on an internal chat turn, gated
    by the key's ``web_search`` permission; a widget or partner turn runs
    ``widget_turn``, which never searches the web. Fail-open:
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


# Included here rather than at the bottom so the OpenAPI document keeps
# /models and /responses ahead of /chat/completions, as before the split.
router.include_router(openai_compat_router)


@router.post("/chat/completions", response_model=None, openapi_extra=WIDGET_PUBLIC_CORS)
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

    A key with ``internal_chat`` (LibreChat) is routed by its resolved profile,
    not by body fields: title prompts go to the passthrough, every other turn
    to the knowledge path, tools included.
    """
    max_bytes = (
        _INTERNAL_CHAT_MAX_BODY_BYTES if auth.permissions.get("internal_chat") else _OPENAI_COMPAT_MAX_BODY_BYTES
    )
    body = await _openai_compatible_request_body(http_request, max_bytes=max_bytes)
    profile = await resolve_chat_profile(db, auth, body.get("user"))
    if profile.surface == "internal":
        if _is_librechat_title_request(body.get("messages")):
            # The title prompt carries the conversation, so it is PII-masked
            # for the employee's org like every other internal call.
            return await _general_passthrough(body, auth=auth, delegated_org_id=auth.zitadel_org_id)
        return await chat_completions(
            request=_parse_knowledge_chat_request(body),
            http_request=http_request,
            auth=auth,
            db=db,
            profile=profile,
        )

    if _openai_compatible_enabled(auth) and not _uses_knowledge_chat(body):
        return await _openai_compatible_chat_completions_from_body(body, auth=auth)

    offending_fields = sorted(_PASSTHROUGH_ONLY_FIELDS & body.keys())
    if offending_fields:
        fields_str = ", ".join(offending_fields)
        raise _openai_error(
            status.HTTP_400_BAD_REQUEST,
            f"{fields_str} is/are only supported on the general passthrough path. "
            "Remove knowledge fields (knowledge_base_ids, page_context, web_search, "
            'web_search_query; knowledge: {"enabled": false} is allowed) to use them, '
            "or drop the field(s) for knowledge-grounded chat.",
        )

    knowledge_request = _parse_knowledge_chat_request(body)
    return await chat_completions(
        request=knowledge_request,
        http_request=http_request,
        auth=auth,
        db=db,
        profile=profile,
    )


async def _partner_kb_scope(
    request: ChatCompletionsRequest, auth: PartnerAuthContext, db: AsyncSession
) -> tuple[bool, list[str]]:
    """Whether this widget/partner turn retrieves, and from which KB slugs (fail-closed)."""
    knowledge = request.knowledge
    retrieval_enabled = knowledge.enabled if knowledge is not None else True
    requested_kb_ids = (
        knowledge.knowledge_base_ids
        if knowledge is not None and knowledge.knowledge_base_ids is not None
        else request.knowledge_base_ids
    )
    # SPEC-PARTNER-KB-SCOPE-001: an explicit empty list is ambiguous — it must
    # never be treated the same as "no scope requested" (which falls back to
    # the key's configured KBs). validate_kb_access() cannot distinguish an
    # explicit [] from a zero-access key, so reject it here before that call.
    if retrieval_enabled and requested_kb_ids is not None and len(requested_kb_ids) == 0:
        raise _openai_error(
            status.HTTP_400_BAD_REQUEST,
            "knowledge_base_ids is empty, which is ambiguous. Omit the field to use "
            "this key's configured knowledge bases, or set knowledge.enabled=false to "
            "skip retrieval entirely.",
        )
    kb_ids = validate_kb_access(auth, requested_kb_ids)
    if retrieval_enabled and not kb_ids:
        # No requested ids fell back to the key's/widget's own KB access,
        # which is empty. Never let this silently widen to every KB in the
        # org — fail closed instead. Message is intentionally generic (never
        # reveal KB existence/names).
        raise _openai_error(
            status.HTTP_403_FORBIDDEN,
            "This API key has no knowledge base access configured for chat",
            error_type="permission_error",
        )

    # 5. Translate kb_ids -> kb_slugs
    kb_slugs = await _resolve_kb_slugs(kb_ids, auth.org_id, db)
    if retrieval_enabled and kb_ids:
        if not kb_slugs:
            # Every authorized/requested KB id failed to resolve (e.g. a
            # deleted KB). Fail closed rather than letting retrieve_context
            # silently widen scope by omitting kb_slugs entirely.
            raise _openai_error(
                status.HTTP_403_FORBIDDEN,
                "This API key has no knowledge base access configured for chat",
                error_type="permission_error",
            )
        if len(kb_slugs) < len(kb_ids):
            logger.warning(
                "partner_kb_ids_unresolved",
                org_id=auth.org_id,
                unresolved_kb_ids=len(kb_ids) - len(kb_slugs),
            )
    return retrieval_enabled, kb_slugs


def _partner_user_id(auth: PartnerAuthContext) -> str | None:
    # F2 (audit retrieval-coupling-2026-05-06): pass synthetic partner_user_id
    # so retrieval-api pins verified_caller and emits the
    # `knowledge.queried` product_event with the correct (org, partner-key)
    # tuple. Matches the existing convention used in chat_completions for
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
    try:
        uuid.UUID(str(auth.key_id))
    except (ValueError, AttributeError, TypeError):
        return None
    return f"partner:{auth.key_id}"


async def _attachment_error_reply(request: ChatCompletionsRequest) -> Any:
    """Turn the latest user turn's PDF attachments into text, or return the reply that says why one failed."""
    attachment_language = resolve_conversation_language(request.messages).language
    attachment_result = await process_chat_attachments(request.messages, language=attachment_language)
    if attachment_result.user_visible_error is not None:
        if request.stream:
            return StreamingResponse(
                content=fixed_reply_stream(attachment_result.user_visible_error),
                media_type="text/event-stream",
            )
        return fixed_reply_response(model=request.model, message=attachment_result.user_visible_error)
    if attachment_result.processed_count:
        request.messages = attachment_result.messages
    return None


@dataclass
class _WidgetAudit:
    """Who a widget turn is recorded for; empty for other callers and for a widget key with no widgets row."""

    widget_id: str | None = None
    session_key: str | None = None
    ip_hash: str | None = None
    ua_hash: str | None = None

    @property
    def ready(self) -> bool:
        return self.widget_id is not None and self.session_key is not None


async def _widget_audit(auth: PartnerAuthContext, http_request: Request, db: AsyncSession) -> _WidgetAudit:
    # Widget audit identity, resolved BEFORE retrieval. The gap event the
    # pad schedules inside retrieve_context must be able to point back at the
    # conversation the question came from, and (widget_id, session_key) is the
    # pair the audit writer keys on (SPEC-KNOWLEDGE-ACTIVITY-001 §4.5). Values
    # and exclusions are unchanged from when this block ran after retrieval:
    # non-widget traffic and a widget key with no widgets row still get no
    # audit at all. SPEC-WIDGET-ACTIVITY-001.
    widget_uuid_row = (
        await db.execute(select(Widget.id).where(Widget.widget_id == str(auth.key_id)))
    ).scalar_one_or_none()
    if widget_uuid_row is None:
        return _WidgetAudit()
    bearer = http_request.headers.get("authorization", "")
    raw_token = bearer.removeprefix("Bearer ").strip()
    return _WidgetAudit(
        widget_id=str(widget_uuid_row),
        session_key=getattr(auth, "session_key", None) or session_key_from_token(raw_token),
        ip_hash=hash_audit_value(http_request.client.host if http_request.client else None),
        ua_hash=hash_audit_value(http_request.headers.get("user-agent")),
    )


def _record_user_turn(
    audit: _WidgetAudit, request: ChatCompletionsRequest, http_request: Request, auth: PartnerAuthContext
) -> asyncio.Task[int | None] | None:
    # The user turn is written before retrieval so the gap task can wait for
    # the conversation row instead of racing it (first-turn gaps used to lose
    # their provenance). Still fire-and-forget: the chat never waits on it.
    if audit.widget_id is None:
        return None
    last_user_msg = next(
        (str(m.get("content", "")) for m in reversed(request.messages) if m.get("role") == "user"),
        "",
    )
    if not audit.session_key or not last_user_msg:
        return None
    task = asyncio.create_task(
        record_widget_turn(
            widget_id=audit.widget_id,
            session_key=audit.session_key,
            role="user",
            content=last_user_msg,
            ip_hash=audit.ip_hash,
            user_agent_hash=audit.ua_hash,
            # The visitor's question language. The row keeps the newest
            # non-NULL value (COALESCE(EXCLUDED, existing) in the UPSERT);
            # the exact per-answer language lives in answer_signals.
            language_detected=identify_text_language(last_user_msg) or None,
            loaded_origin=http_request.headers.get("origin") or None,
            is_preview=getattr(auth, "is_preview", False),
            visitor_name=request.visitor_name,
            visitor_email=request.visitor_email,
        )
    )
    _pending.add(task)
    task.add_done_callback(_pending.discard)
    return task


async def _previous_case(request: ChatCompletionsRequest, auth: PartnerAuthContext, audit: _WidgetAudit) -> dict | None:
    # Help widget: the case stored with the previous assistant turn
    # (case_file.py). A read that fails costs this turn its case and nothing
    # else. Only on a follow-up, and only when the stored turn is this
    # conversation's last answer: one session token serves every
    # conversation the widget starts in it.
    if not audit.ready or not case_file.is_follow_up(request.messages):
        return None
    try:
        stored = await last_case(widget_id=audit.widget_id, session_key=audit.session_key)  # type: ignore[arg-type]
    except Exception:
        stored = None
        logger.warning("partner_chat_previous_case_unavailable", org_id=auth.org_id, exc_info=True)
    return case_file.stored_case_for(request.messages, stored)


def _record_fields(
    audit: _WidgetAudit, request: ChatCompletionsRequest, http_request: Request, auth: PartnerAuthContext
) -> dict[str, Any]:
    return {
        "widget_id": audit.widget_id,
        "session_key": audit.session_key,
        "loaded_origin": http_request.headers.get("origin") or None,
        "is_preview": getattr(auth, "is_preview", False),
        "turn_id": request.widget_turn_id,
    }


def _sent_body(result: dict, request: ChatCompletionsRequest) -> dict:
    """The non-streaming body as the caller asked for it."""
    knowledge = request.knowledge
    if knowledge is not None and not knowledge.include_sources:
        for choice in result.get("choices") or []:
            message = choice.get("message") if isinstance(choice, dict) else None
            if isinstance(message, dict):
                message.pop("sources", None)
    return result


async def _replayed(frames: list[bytes]) -> AsyncGenerator[bytes]:
    for frame in frames:
        yield frame


def _reply_response(reply: Reply, request: ChatCompletionsRequest, record: dict[str, Any] | None) -> Any:
    """Send a widget or internal reply the way the request asked for it, and record a widget turn."""
    knowledge = request.knowledge
    include_sources = knowledge.include_sources if knowledge is not None else True
    if record is not None and reply.text:
        # Recorded once, from the Reply itself, as the visitor gets it: the
        # write starts before the first frame and runs beside the sending.
        _write_widget_record(
            reply.turn,
            **record,
            role="assistant",
            content=reply.text,
            sources=(reply.sources or None) if include_sources else None,
            answer_signals=reply.signals or None,
        )
    if request.stream:
        frames = _replayed(reply_frames(reply, include_sources=include_sources))
        return StreamingResponse(content=frames, media_type="text/event-stream")
    return reply_body(reply, model=request.model, include_sources=include_sources)


def _upstream_error_response(error: AnswerUpstreamError, request: ChatCompletionsRequest) -> Any:
    if not request.stream:
        raise error
    # Once streaming has started a failure cannot become a 502, so a streamed
    # request gets the error as a frame (see _sse_error_frame).
    return StreamingResponse(content=_replayed(error_frames(error)), media_type="text/event-stream")


async def chat_completions(
    request: ChatCompletionsRequest,
    http_request: Request,
    auth: PartnerAuthContext = Depends(get_partner_key),
    db: AsyncSession = Depends(get_db),
    profile: ChatProfile | None = None,
):
    """Chat completions with RAG context from knowledge bases.

    TASK-008: Non-streaming path.
    TASK-009: Streaming SSE path.
    """
    turn_started = time.perf_counter()
    # 1. Permission check
    require_permission(auth, "chat")
    # canonical_chat_completions always passes the profile; direct callers get
    # the one their key resolves to. From here on the profile, not the key, is
    # what the generation functions receive.
    if profile is None:
        profile = await resolve_chat_profile(db, auth, None)
    structlog.contextvars.bind_contextvars(chat_surface=profile.surface, chat_kb_mode=profile.kb_mode)

    # 2-3. Model and messages validation.
    _validate_chat_request(request)
    # Tools reach the model's prompt, so only the internal chat offers them: a
    # widget visitor is anonymous, and a partner key runs the widget's turn,
    # which writes its reply without a tool step.
    if profile.surface != "internal" and (request.tools or request.tool_choice is not None):
        raise _openai_error(status.HTTP_400_BAD_REQUEST, "tools are not supported on widget and partner keys")
    # The same holds for live web results: the widget's turn reads the help
    # articles only, so a request that asks for the web gets an error, not a
    # reply that quietly did without.
    if profile.surface != "internal" and request.web_search:
        raise _openai_error(status.HTTP_400_BAD_REQUEST, "web_search is not supported on widget and partner keys")

    # 3a. PDF attachments in the latest user turn become text before anything
    # reads the messages. Not for the widget: its visitors are anonymous and
    # its UI has no upload, so a widget key must not be a public route into
    # docling. The language is decided on the unmodified messages, so a Dutch
    # PDF never overrules an English question.
    if profile.surface != "widget" and (attachment_reply := await _attachment_error_reply(request)):
        return attachment_reply

    if safety_response := _input_safety_block_response(request, auth, profile):
        return safety_response
    is_widget_chat = str(auth.key_id).startswith("wgt_")

    # 4. Validate KB access. ``knowledge`` is a Klai extension on top of the
    # OpenAI-compatible request shape. Top-level knowledge_base_ids remains
    # supported for existing partner clients.
    knowledge = request.knowledge
    if profile.surface == "internal":
        # The employee's own KB settings, resolved into the profile, scope the
        # turn; the internal key's kb_access plays no part. No slugs means every
        # org KB, which retrieval-api scopes by the key's org and the employee.
        retrieval_enabled, kb_slugs = True, list(profile.kb_slugs or [])
    else:
        retrieval_enabled, kb_slugs = await _partner_kb_scope(request, auth, db)

    # 6. Retrieve context.
    partner_user_id = _partner_user_id(auth)
    widget_system_prompt = await _widget_system_prompt(auth, db)
    page_context_enabled = await _widget_page_context_enabled(auth, db) if is_widget_chat else False
    off_topic_subjects, off_topic_reply = await _widget_off_topic(auth, db) if is_widget_chat else ("", "")
    tone_register = await _widget_tone_register(auth, db) if is_widget_chat else "restrained"
    page_context = (
        request.page_context.model_dump(exclude_none=True) if page_context_enabled and request.page_context else None
    )

    audit = await _widget_audit(auth, http_request, db) if is_widget_chat else _WidgetAudit()
    user_turn_write = _record_user_turn(audit, request, http_request, auth)
    # What the turn's assistant record is written with; None when nothing is recorded.
    record = _record_fields(audit, request, http_request, auth) if audit.ready else None

    # SPEC-PARTNER-KB-SCOPE-001 invariant: when retrieval is enabled,
    # kb_slugs must never be empty here — retrieve_context() only sends
    # kb_slugs to retrieval-api when the list is truthy, and an empty list
    # is silently treated as "no KB restriction" (widens to every KB the
    # org can see). The 400/403 guards above must have already fired for
    # every empty-scope case by this point. The internal surface is the
    # exception by design: its empty list is the employee's "every org KB".
    assert profile.surface == "internal" or not retrieval_enabled or kb_slugs, (
        "chat_completions: retrieve_context must not be called with empty kb_slugs while retrieval is enabled"
    )

    # Retrieval certainty of this answer: retrieve_context writes the band, the
    # completion pad fills the rest in-process, and we hand the dict to the audit
    # write, so the widget visitor's response never carries it. Only wired to the
    # audit trail when it will read it.
    # @MX:SPEC: SPEC-KNOWLEDGE-ACTIVITY-001 §4.1
    answer_signals: dict[str, Any] = {}
    # Pasted third-party correspondence (one-chat-pipeline slice 3, moved from
    # klai_pasted_correspondence.py): conversation-wide by design so a
    # follow-up turn still gets the epistemic contract while the
    # correspondence stays in context. Threaded into retrieve_context so
    # every return path (including the no-retrieval early returns) builds
    # the same system prompt a later fan-out/clarify gate (slice 4/5) must
    # also skip on, the way klai_knowledge.py does today.
    pasted_correspondence = detect_pasted_correspondence(request.messages)

    if profile.surface != "internal":
        # Every widget and partner turn runs as the flow of chat-quality-history-and-plan.md §5.
        turn = TurnInputs(
            request=request,
            auth=auth,
            settings=settings,
            profile=profile,
            kb_slugs=kb_slugs,
            retrieval_enabled=retrieval_enabled,
            partner_user_id=partner_user_id,
            widget_system_prompt=widget_system_prompt,
            page_context=page_context,
            tone_register=tone_register,
            off_topic_subjects=off_topic_subjects,
            off_topic_reply=off_topic_reply,
            previous_case=await _previous_case(request, auth, audit),
            audit_widget_id=audit.widget_id,
            audit_session_key=audit.session_key,
            user_turn_write=user_turn_write,
            audit_ready=audit.ready,
            pasted_correspondence=pasted_correspondence,
            answer_signals=answer_signals,
            turn_started=turn_started,
        )
        try:
            reply = await widget_turn(turn)
        except AnswerUpstreamError as error:
            return _upstream_error_response(error, request)
        return _reply_response(reply, request, record)

    # The internal chat runs the same flow (internal_turn.py). A Strict reply
    # comes back held and composed; an Open or general one is streamed live
    # below, because employees read the tokens as they arrive.
    templates_block, telemetry_level = "", "shadow"
    if profile.user_id is not None:
        instructions, telemetry_level = await internal_turn_settings(db, auth.org_id, profile.user_id)
        templates_block = build_template_instructions_block(instructions)
    try:
        outcome = await internal_turn(
            InternalInputs(
                request=request,
                auth=auth,
                settings=settings,
                profile=profile,
                templates_block=templates_block,
                telemetry_level=telemetry_level,
                pasted_correspondence=pasted_correspondence,
                answer_signals=answer_signals,
                turn_started=turn_started,
            )
        )
    except AnswerUpstreamError as error:
        return _upstream_error_response(error, request)
    if isinstance(outcome, Reply):
        _write_internal_record(auth.org_id, outcome.signals, outcome.turn)
        return _reply_response(outcome, request, None)
    chunks, system_prompt, trusted_sources, knowledge_turn, turn_timing = outcome

    system_prompt, web_chunks, web_query = await _maybe_apply_web_search(
        request=request,
        auth=auth,
        system_prompt=system_prompt,
    )

    # 8. Streaming or non-streaming; the turn's signals go to its own record.
    generation: dict[str, Any] = {
        "messages": request.messages,
        "model": request.model,
        "temperature": request.temperature,
        "system_prompt": system_prompt,
        "settings": settings,
        "org_id": auth.org_id,
        "citation_chunks": chunks,
        "web_chunks": web_chunks,
        "web_query": web_query,
        "trusted_sources": trusted_sources,
        "source_query": knowledge.query if knowledge is not None else None,
        "answer_signals": answer_signals,
        "turn_timing": turn_timing,
        "profile": profile,
        "tools": request.tools,
        "tool_choice": request.tool_choice,
        # Every model call of the turn runs on LiteLLM's master key, so the org
        # travels with it for LiteLLM to apply the tenant's PII policy.
        "delegated_org_id": auth.zitadel_org_id,
        "knowledge_turn": knowledge_turn,
        "pasted_correspondence": pasted_correspondence,
    }
    if request.stream:
        streaming_gen = _internal_record_streaming_wrapper(
            chat_completion_streaming(
                **generation, emit_sources=knowledge.include_sources if knowledge is not None else True
            ),
            org_id=auth.org_id,
            answer_signals=answer_signals,
            turn=knowledge_turn,
        )
        return StreamingResponse(content=streaming_gen, media_type="text/event-stream")

    result = await chat_completion_non_streaming(**generation)
    _write_internal_record(auth.org_id, answer_signals, knowledge_turn)
    return _sent_body(result, request)


router.include_router(support_sessions_router)
router.include_router(hubspot_router)
router.include_router(feedback_router)
router.include_router(widget_config_router)
