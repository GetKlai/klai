"""
KlaiKnowledgeHook — LiteLLM pre-call hook that enriches LibreChat messages
with relevant organizational knowledge from the Klai Knowledge Service.

Mount into LiteLLM container at /app/custom/ and set PYTHONPATH=/app/custom.
Configure in config.yaml:
  litellm_settings:
    callbacks:
      - klai_knowledge.klai_knowledge_hook

Authorization is fail-closed: any user without a verified knowledge entitlement
receives no KB injection. If the portal authorization endpoint is unreachable,
injection is silently skipped (WARNING logged).

KB-context presence is signalled to downstream hooks via data["_klai_kb_meta"].
The custom_router uses this to prevent model downgrade for KB-enriched requests.
"""

import asyncio
import logging
import os
import time
from typing import Any

import httpx
import litellm

# SPEC-RAG-MULTILINGUAL-CHAT-001 Phase 4 (REQ-10): the language-detection
# foundation that this hook prepends to every LibreChat system message,
# matching what synthesis.py (path C) and partner_chat.py (path B) already
# do. Imported from the vendored single-file copy at
# deploy/litellm/klai_chat_prompts.py — see that file's docstring for the
# vendoring rationale. Drift vs the canonical klai-libs/chat-prompts is
# enforced by deploy/litellm/tests/test_klai_chat_prompts_drift.py.
#
# Note (re-introduced 2026-05-07 after the inline-hotfix detour): the
# original Phase 4 commit imported this and broke production because the
# old ``Deploy LiteLLM hooks`` workflow used ``docker compose restart
# litellm``, which silently ignores new bind-mounts. The cleanup PR that
# restored this import ALSO switched the workflow to
# ``/opt/klai/scripts/compose-up.sh litellm`` (= ``docker compose up -d
# --remove-orphans litellm``) so new mounts are picked up automatically —
# matching every other klai service deploy.
from klai_context import (
    ACTIVE_ATTACHMENT_CONTEXT_PLACEHOLDER as _ACTIVE_ATTACHMENT_CONTEXT_PLACEHOLDER,
    HISTORY_BUDGET_CONTEXT_PLACEHOLDER as _HISTORY_BUDGET_CONTEXT_PLACEHOLDER,
    KlaiContextOrchestrator,
    STALE_ATTACHMENT_CONTEXT_PLACEHOLDER as _STALE_ATTACHMENT_CONTEXT_PLACEHOLDER,
)
from klai_citations import (
    evidence_pack_items_as_chunks,
    trusted_sources_from_evidence_pack,
)
from klai_kb_confidence_policy import (
    LOW_CONFIDENCE_INJECTION_DISABLED as _LOW_CONFIDENCE_INJECTION_DISABLED,
    LOW_CONFIDENCE_INJECTION_TEXT as _LOW_CONFIDENCE_INJECTION_TEXT,
    LOW_CONFIDENCE_OPEN_CONTEXT_TEXT as _LOW_CONFIDENCE_OPEN_CONTEXT_TEXT,
    has_direct_evidence_for_query as _has_direct_evidence_for_query,
    low_confidence_query_tokens as _low_confidence_query_tokens,
    should_apply_low_confidence_injection as _should_apply_low_confidence_injection,
)
from klai_kb_citation_render import (
    KbCitationRenderStats as _KbCitationRenderStats,
    compose_non_streaming_kb_response as _compose_non_streaming_kb_response,
    compose_streaming_kb_response as _compose_streaming_kb_response,
    log_kb_citation_render as _log_kb_citation_render,
)
from klai_kb_answer_policy import (
    KbAnswerPolicy,
    USER_PROVIDED_CONTENT_SCOPE as _USER_PROVIDED_CONTENT_SCOPE,
    compose_general_chat_prefix as _compose_general_chat_prefix,
    compose_kb_mode_chat_prefix as _compose_kb_mode_chat_prefix,
    compose_libre_chat_prefix as _compose_libre_chat_prefix,
    compose_meta_chat_prefix as _compose_meta_chat_prefix,
    compose_open_kb_chat_prefix as _compose_open_kb_chat_prefix,
    has_user_provided_content_context as _has_user_provided_content_context,
    kb_chunks_present_header as _kb_chunks_present_header,
    kb_retrieval_failure_notice as _kb_retrieval_failure_notice,
    kb_zero_chunks_notice as _kb_zero_chunks_notice,
    strict_kb_unavailable_message as _strict_kb_unavailable_message,
    settings_unavailable_message as _settings_unavailable_message,
)
from klai_chat_prompts import (
    no_citable_sources_message as _no_citable_sources_message,
)
from klai_chat_attachments import (
    process_chat_attachments as _process_chat_attachments,
)
from klai_kb_context_prompt import (
    build_kb_context_prompt as _build_kb_context_prompt,
)
from klai_kb_system_prompt import (
    build_template_instructions_block as _build_template_instructions_block,
    prepend_system_prefix as _prepend_system_prefix,
)
from klai_kb_chat_mode import (
    prompt_mode_is_unavailable as _prompt_mode_is_unavailable,
)
from klai_kb_query_rewrite import (
    KLAI_TAXONOMY_COVERAGE_THRESHOLD,
    TAXONOMY_ENABLED,
    _QUERY_REWRITE_PROMPT,
    _QUERY_REWRITE_AND_CLASSIFY_PROMPT,
    fetch_taxonomy_coverage as _fetch_taxonomy_coverage,
    fetch_taxonomy_trees as _fetch_taxonomy_trees,
    flatten_trees as _flatten_trees,
    format_history_for_rewrite as _format_history_for_rewrite,
    format_taxonomy_for_prompt as _format_taxonomy_for_prompt,
    rewrite_and_classify as _rewrite_and_classify,
    rewrite_query as _rewrite_query,
)
from klai_kb_request_context import (
    META_QUERY_PATTERNS as _META_QUERY_PATTERNS,
    RETRIEVE_HISTORY_API_CONTENT_LIMIT_CHARS,
    RETRIEVE_HISTORY_MAX_CONTENT_CHARS,
    RETRIEVE_HISTORY_OMISSION_MARKER,
    active_tool_result_contexts as _active_tool_result_contexts,
    build_conversation_history as _build_conversation_history,
    clip_retrieval_history_content as _clip_retrieval_history_content,
    general_runtime_capabilities_block as _general_runtime_capabilities_block,
    is_meta_query as _is_meta_query,
    is_title_generation_request as _is_title_generation_request,
    is_trivial as _is_trivial,
    last_user_message as _last_user_message,
    message_text as _message_text,
    request_has_web_search as _request_has_web_search,
    request_metadata as _request_metadata,
    sanitize_assistant_history_messages as _sanitize_assistant_history_messages,
    strip_klai_backend_footer_from_text as _strip_klai_backend_footer_from_text,
    strip_web_search_tools as _strip_web_search_tools,
    truthy as _truthy,
)
from klai_kb_safety_filter import (
    filter_evidence_pack_for_chunks as _filter_evidence_pack_for_chunks,
    filter_trusted_sources_for_chunks as _filter_trusted_sources_for_chunks,
)
from klai_litellm_response import (
    split_stream_footer_from_stop_item as _split_stream_footer_from_stop_item,
    stream_item_has_finish_reason as _stream_item_has_finish_reason,
)
from klai_kb_render_policy import (
    KB_RENDER_MODE_DETERMINISTIC_NON_STREAMING as _KB_RENDER_MODE_DETERMINISTIC_NON_STREAMING,
    KB_RENDER_MODE_LEGACY_STREAMING_GUARD as _KB_RENDER_MODE_LEGACY_STREAMING_GUARD,
    KB_RENDER_MODE_STREAMING_GUARD as _KB_RENDER_MODE_STREAMING_GUARD,
    KbCitationRenderStrategy,
    is_streaming_kb_render_mode as _is_streaming_kb_render_mode,
    resolve_kb_render_mode as _resolve_kb_render_mode,
    select_kb_render_strategy as _select_kb_render_strategy_for_mode,
)
from klai_kb_scope_policy import (
    build_retrieve_body as _build_retrieve_body,
    resolve_chat_retrieval_policy as _resolve_chat_retrieval_policy,
    resolve_kb_taxonomy_decision as _resolve_kb_taxonomy_decision,
)
from klai_kb_traceability import (
    kb_labels_from_chunks as _kb_labels_from_chunks,
    kb_labels_used_by_sources as _kb_labels_used_by_sources,
    kb_scope_mode as _kb_scope_mode,
)
from klai_llm_safety import (
    SafetyDecision,
    SafetyPhase,
)
from klai_kb_llm_safety import (
    LLM_SAFETY_LITELLM_MODE,
    check_llm_safety as _check_llm_safety,
    chunk_safety_text as _chunk_safety_text,
    llm_safety_enabled as _llm_safety_enabled,
    llm_safety_enforces as _llm_safety_enforces,
    llm_safety_short_circuit as _llm_safety_short_circuit,
)
from klai_kb_portal_client import (
    get_kb_feature as _get_kb_feature,
    get_templates as _get_templates,
    retrieve as _retrieve,
)

# SPEC-MCP-RETRIEVAL-001 Phase 1: telemetry helpers moved out of this file
# into ``klai-libs/retrieval-telemetry/`` so klai-knowledge-mcp's new
# ``search_knowledge`` tool can emit identical retrieval-log + gap-event
# payloads. The vendored single-file copy at
# ``deploy/litellm/klai_retrieval_telemetry.py`` mirrors the canonical
# ``klai-libs/retrieval-telemetry/klai_retrieval_telemetry/_emit.py``.
# Drift is enforced by ``test_klai_retrieval_telemetry_drift.py``.
#
# We bind to underscore-prefixed module locals to preserve the existing
# call sites (``_classify_gap``, ``_fire_gap_event``, ``_fire_retrieval_log``)
# without a touch-every-call-site refactor. ``caller_client_id`` defaults
# to ``None`` for all LibreChat-path emits, preserving the wire shape.
from klai_retrieval_telemetry import (
    classify_gap as _classify_gap,
)
from klai_retrieval_telemetry import (
    fire_gap_event as _fire_gap_event,
)
from klai_retrieval_telemetry import (
    fire_retrieval_log as _fire_retrieval_log,
)
from litellm.integrations.custom_logger import CustomLogger

logger = logging.getLogger(__name__)

__all__ = [
    "KbAnswerPolicy",
    "_USER_PROVIDED_CONTENT_SCOPE",
    "_ACTIVE_ATTACHMENT_CONTEXT_PLACEHOLDER",
    "_HISTORY_BUDGET_CONTEXT_PLACEHOLDER",
    "_STALE_ATTACHMENT_CONTEXT_PLACEHOLDER",
    "_compose_libre_chat_prefix",
    "_compose_open_kb_chat_prefix",
    "_compose_general_chat_prefix",
    "_compose_meta_chat_prefix",
    "_compose_kb_mode_chat_prefix",
    "_has_user_provided_content_context",
    "_kb_chunks_present_header",
    "_kb_retrieval_failure_notice",
    "_kb_zero_chunks_notice",
    "_KB_RENDER_MODE_STREAMING_GUARD",
    "_KB_RENDER_MODE_LEGACY_STREAMING_GUARD",
    "_KB_RENDER_MODE_DETERMINISTIC_NON_STREAMING",
    "KLAI_KB_CHAT_RENDER_MODE",
    "KbCitationRenderStrategy",
    "_resolve_kb_render_mode",
    "_is_streaming_kb_render_mode",
    "_select_kb_render_strategy",
    "_format_history_for_rewrite",
    "_format_taxonomy_for_prompt",
    "_flatten_trees",
    "_rewrite_query",
    "_QUERY_REWRITE_PROMPT",
    "_QUERY_REWRITE_AND_CLASSIFY_PROMPT",
    "_LOW_CONFIDENCE_INJECTION_TEXT",
    "_LOW_CONFIDENCE_OPEN_CONTEXT_TEXT",
    "_LOW_CONFIDENCE_INJECTION_DISABLED",
    "_low_confidence_query_tokens",
    "_has_direct_evidence_for_query",
    "_should_apply_low_confidence_injection",
    "_META_QUERY_PATTERNS",
    "RETRIEVE_HISTORY_API_CONTENT_LIMIT_CHARS",
    "RETRIEVE_HISTORY_MAX_CONTENT_CHARS",
    "RETRIEVE_HISTORY_OMISSION_MARKER",
    "_active_tool_result_contexts",
    "_build_conversation_history",
    "_clip_retrieval_history_content",
    "_general_runtime_capabilities_block",
    "_is_meta_query",
    "_is_title_generation_request",
    "_is_trivial",
    "_last_user_message",
    "_message_text",
    "_request_has_web_search",
    "_request_metadata",
    "_sanitize_assistant_history_messages",
    "_strip_klai_backend_footer_from_text",
    "_truthy",
    "_process_chat_attachments",
    "klai_knowledge_hook",
]

PORTAL_API_URL = os.getenv("PORTAL_API_URL", "http://portal-api:8000")

RETRIEVE_TIMEOUT = float(os.getenv("KNOWLEDGE_RETRIEVE_TIMEOUT", "3.0"))
# SPEC-RAG-LOW-CONFIDENCE-ABSTAIN-001 REQ-4: top_k raised from 5 to 20.
# Anthropic Contextual Retrieval finding ("top-20 chunks proved more
# effective than top-5 or top-10"). The reranker already produces 20
# candidates server-side (reranker_candidates=20) so this only changes
# how many chunks are forwarded to the LLM, not how many are scored.
RETRIEVE_TOP_K = int(os.getenv("KNOWLEDGE_RETRIEVE_TOP_K", "20"))
KLAI_GAP_SOFT_THRESHOLD = float(os.getenv("KLAI_GAP_SOFT_THRESHOLD", "0.4"))
KLAI_GAP_DENSE_THRESHOLD = float(os.getenv("KLAI_GAP_DENSE_THRESHOLD", "0.35"))
PORTAL_RETRIEVAL_LOG_URL = os.getenv(
    "PORTAL_RETRIEVAL_LOG_URL", f"{PORTAL_API_URL}/internal/v1/retrieval-log"
)
EMBEDDING_MODEL_VERSION = os.getenv("EMBEDDING_MODEL_VERSION", "bge-m3-v1")
KB_IMAGES_BASE_URL = os.getenv("KB_IMAGES_BASE_URL", "https://getklai.getklai.com")
_STREAM_LINK_GUARD_TAIL_CHARS = 16
KLAI_KB_CHAT_RENDER_MODE = _resolve_kb_render_mode(
    os.getenv("KLAI_KB_CHAT_RENDER_MODE")
)


def _sanitize_upstream_body(body: str, *, max_len: int = 200) -> str:
    """Redact local service secrets before logging an upstream error body."""
    if not body:
        return ""
    safe = body
    secret_values = (
        os.getenv("PORTAL_INTERNAL_SECRET", ""),
        os.getenv("RETRIEVAL_INTERNAL_SECRET", ""),
    )
    for secret in sorted({s for s in secret_values if len(s) >= 8}, key=len, reverse=True):
        safe = safe.replace(secret, "<redacted>")
    return safe[:max_len]


def _select_kb_render_strategy(original_stream: object) -> KbCitationRenderStrategy:
    return _select_kb_render_strategy_for_mode(
        original_stream,
        configured_mode=KLAI_KB_CHAT_RENDER_MODE,
    )


_KLAI_CONTEXT_ORCHESTRATOR = KlaiContextOrchestrator()

# @MX:NOTE: classify_gap, fire_gap_event, fire_retrieval_log moved to
# klai-libs/retrieval-telemetry/ (SPEC-MCP-RETRIEVAL-001 Phase 1). Imports
# at the top of this module rebind them to the underscore-prefixed names
# every call site below already uses, so the move is invisible to callers.
# The threshold / URL env vars (KLAI_GAP_SOFT_THRESHOLD,
# KLAI_GAP_DENSE_THRESHOLD, EMBEDDING_MODEL_VERSION,
# PORTAL_RETRIEVAL_LOG_URL) are read inside the lib's _default_config()
# helper at call time, so the env-rotation behaviour stays identical.


def _split_if_rendered_stop_item(
    item: object, stats: "_KbCitationRenderStats"
) -> object | None:
    if stats.rendered_messages and _stream_item_has_finish_reason(item):
        return _split_stream_footer_from_stop_item(item)
    return None


def _should_assemble_provider_context(data: dict[str, Any]) -> bool:
    """Return whether this request is user-facing chat traffic.

    Internal services can call explicit Klai model aliases for their own
    provider-native tool loops. Provider normalization belongs to chat traffic
    only; otherwise the knowledge hook undercuts the router's explicit bypass.
    """
    metadata = _request_metadata(data)
    return bool(
        data.get("user")
        or metadata.get("_klai_kb_meta")
        or metadata.get("_klai_context_meta")
        or metadata.get("_klai_router_meta")
    )


class KlaiKnowledgeHook(CustomLogger):
    async def async_pre_call_hook(self, user_api_key_dict, cache, data, call_type):
        if call_type not in ("completion", "acompletion"):
            return data
        if _truthy(_request_metadata(data).get("_klai_openai_passthrough")):
            return data

        messages = _sanitize_assistant_history_messages(data.get("messages", []))
        query = _last_user_message(messages)
        data["messages"] = messages
        context_meta: dict[str, Any] | None = None
        should_assemble_provider_context = _should_assemble_provider_context(data)
        if should_assemble_provider_context:
            profile = _KLAI_CONTEXT_ORCHESTRATOR.profile_for(
                data.get("model", "klai-primary")
            )
            attachment_result = await _process_chat_attachments(
                messages,
                query=query,
                token_counter=getattr(litellm, "token_counter", None),
                token_counter_model=profile.token_counter_model,
            )
            data.setdefault("metadata", {})["_klai_chat_attachment_meta"] = attachment_result.meta
            if attachment_result.user_visible_error is not None:
                data["mock_response"] = attachment_result.user_visible_error
                return data
            if attachment_result.processed_count:
                messages = attachment_result.messages
                data["messages"] = messages
                logger.warning(
                    "chat_pdf_attachment_processed attachments=%d extracted_chars=%d extracted_tokens_estimate=%d",
                    attachment_result.processed_count,
                    attachment_result.meta["chat_pdf_extracted_chars"],
                    attachment_result.meta["chat_pdf_extracted_tokens_estimate"],
                )
            context_result = _KLAI_CONTEXT_ORCHESTRATOR.assemble(
                messages,
                requested_model=data.get("model", "klai-primary"),
                apply_history_budget=False,
            )
            messages = context_result.messages
            context_meta = context_result.meta
            data["messages"] = messages
            data.setdefault("metadata", {})["_klai_context_meta"] = context_meta
        if not query or _is_trivial(query):
            return data

        # org_id lives in LiteLLM team key metadata
        metadata = getattr(user_api_key_dict, "metadata", {}) or {}
        org_id = metadata.get("org_id")
        if not org_id:
            # Master key usage — no org scope available, skip silently
            return data

        # librechat_user_id = LibreChat MongoDB ObjectId sent as the "user" field.
        # We only use this against portal-api endpoints that explicitly accept
        # the LibreChat ID (kb_feature, templates). Everything that asks
        # "which Zitadel user is this?" — including retrieval-api's
        # /retrieve identity-verify path and the personal-KB qdrant filter —
        # gets the resolved zitadel_user_id from the kb_feature response.
        librechat_user_id = data.get("user", "")
        if not librechat_user_id:
            return data

        user_provided_content_context = _has_user_provided_content_context(
            messages, query
        )
        data["messages"] = messages
        if context_meta is not None:
            data.setdefault("metadata", {})["_klai_context_meta"] = context_meta

        normalized_user_text_part_messages = (
            context_meta["normalized_user_text_part_messages"]
            if context_meta is not None
            else 0
        )
        if normalized_user_text_part_messages:
            logger.warning(
                "librechat_user_text_part_messages_normalized org_id=%s user_id=%s normalized=%d",
                org_id,
                librechat_user_id,
                normalized_user_text_part_messages,
            )
        safety_metadata = data.setdefault("metadata", {})
        # Input safety scans ONLY the latest user turn — never the assistant's
        # prior answers and never the full history. Rescanning the whole
        # conversation meant a single earlier grey-area turn (or a prior
        # refusal that named the blocked topics) poisoned every later message,
        # so innocent follow-ups got refused. Assistant output is the model's
        # own text and is covered separately, not by the INPUT gate.
        input_safety_decision = _check_llm_safety(
            phase=SafetyPhase.INPUT,
            text=query,
            query=query,
            org_id=org_id,
            user_id=librechat_user_id,
            metadata=safety_metadata,
        )
        if (
            input_safety_decision is not None
            and not input_safety_decision.allowed
            and _llm_safety_enforces()
        ):
            return _llm_safety_short_circuit(
                data, query=query, decision=input_safety_decision
            )

        last_tool_context_decision: SafetyDecision | None = None
        for index, tool_context in enumerate(_active_tool_result_contexts(messages)):
            tool_context_decision = _check_llm_safety(
                phase=SafetyPhase.CONTEXT,
                text=tool_context,
                query=query,
                org_id=org_id,
                user_id=librechat_user_id,
                metadata=safety_metadata,
                chunk_id=f"active_tool_result:{index}",
            )
            if tool_context_decision is not None and not tool_context_decision.allowed:
                last_tool_context_decision = tool_context_decision
        if last_tool_context_decision is not None and _llm_safety_enforces():
            return _llm_safety_short_circuit(
                data,
                query=query,
                decision=last_tool_context_decision,
            )

        if _is_title_generation_request(messages):
            logger.info(
                "title_generation_request_detected org_id=%s user_id=%s",
                org_id,
                librechat_user_id,
            )
            return data

        # SPEC-CHAT-TEMPLATES-001 REQ-TEMPLATES-HOOK: fetch active templates
        # before the KB path so they apply on EVERY downstream branch —
        # including early returns when KB retrieval is skipped or fails.
        # Fail-open: empty list on any portal-api error.
        template_instructions = await _get_templates(org_id, librechat_user_id, cache)
        templates_block = _build_template_instructions_block(template_instructions)

        # Meta-question early-return (Voys "Meldingen" incident, 2026-05-12).
        # When the user asks ABOUT Klai itself ("wat kan ik hier?", "what is
        # Klai?", "how does this chat work?") rather than asking a content
        # question, skip retrieval entirely and inject META_CHAT_SYSTEM_PROMPT
        # so the model gives a plain capability-description answer without
        # fabricating features or grasping at tangential KB chunks.
        #
        # Sits AFTER _is_trivial / org_id / librechat_user_id checks so it
        # only fires inside a valid path-A request. Sits BEFORE the KB
        # feature flag because a user without KB entitlement asking "what
        # is Klai?" still deserves a real answer — not the generic libre
        # prefix that promises KB grounding.
        #
        # Templates still apply so admins can extend the canned capability
        # description per org.
        if _is_meta_query(query):
            _prepend_system_prefix(messages, _compose_meta_chat_prefix(templates_block))
            data["messages"] = messages
            logger.info(
                "meta_query_detected org_id=%s user_id=%s query=%r",
                org_id,
                librechat_user_id,
                query[:80],
            )
            return data

        # Feature, KB scope, identity, and request-mode policy.
        feature = await _get_kb_feature(librechat_user_id, org_id, cache)

        if feature.get("settings_unavailable"):
            # Truly-cold path: portal unreachable AND no cached settings, so we
            # don't know the user's mode (Strict/Open). Refuse deterministically
            # via mock_response (model bypassed) instead of silently giving a
            # general answer that would break a Strict user's KB-only promise.
            logger.warning(
                "kb_settings_unavailable_refusal org_id=%s user_id=%s",
                org_id,
                librechat_user_id,
            )
            data["mock_response"] = _settings_unavailable_message(query)
            return data

        chat_retrieval_policy = _resolve_chat_retrieval_policy(feature)

        # Strict mode (kb_narrow=True) is KB-only. The web-search tool is a
        # LibreChat affordance the KB hook does not otherwise gate; strip it
        # here so "web is not an answer source in Strict" is code-enforced
        # rather than left to the model obeying a prompt notice. (Web results
        # LibreChat injects as plain message content are a separate,
        # frontend-gated concern — see strip_web_search_tools.)
        if chat_retrieval_policy.kb_narrow:
            web_tools_removed = _strip_web_search_tools(data)
            if web_tools_removed:
                logger.info(
                    "strict_mode_web_tools_stripped org_id=%s user_id=%s removed=%d",
                    org_id,
                    librechat_user_id,
                    web_tools_removed,
                )

        if chat_retrieval_policy.prompt_mode == "strict_no_kb":
            if chat_retrieval_policy.user_visible_failure_reason is None:
                raise RuntimeError("strict KB chat retrieval policy has no reason")
            # Deterministic refusal. Strict mode with no KB in scope has nothing
            # to ground on, so bypass the model entirely via ``mock_response``
            # instead of injecting a prompt notice and trusting the model to
            # refuse. A prompt-only notice let a non-compliant model answer from
            # general knowledge — exactly the Strict-KB-only leak this prevents.
            logger.info(
                "strict_no_kb_deterministic_refusal org_id=%s user_id=%s reason=%s",
                org_id,
                librechat_user_id,
                chat_retrieval_policy.user_visible_failure_reason,
            )
            data["mock_response"] = _no_citable_sources_message(query)
            return data
        if chat_retrieval_policy.prompt_mode == "general":
            _prepend_system_prefix(
                messages,
                _compose_general_chat_prefix(
                    _general_runtime_capabilities_block(data),
                    templates_block,
                ),
            )
            data["messages"] = messages
            return data
        if _prompt_mode_is_unavailable(chat_retrieval_policy.prompt_mode):
            failure_reason = (
                chat_retrieval_policy.user_visible_failure_reason or "kb-unavailable"
            )
            if failure_reason == "identity-resolve-failed":
                logger.error(
                    "KlaiKnowledgeHook: portal returned no zitadel_user_id for "
                    "librechat_user_id=%s org_id=%s — failing loud",
                    librechat_user_id,
                    org_id,
                )
            if chat_retrieval_policy.kb_narrow:
                # Strict + KB unavailable: deterministic refusal, model
                # bypassed. Do NOT inject a prompt notice and trust the model to
                # refuse — return the canned strict-unavailable message directly.
                logger.info(
                    "strict_unavailable_deterministic_refusal "
                    "org_id=%s user_id=%s reason=%s",
                    org_id,
                    librechat_user_id,
                    failure_reason,
                )
                data["mock_response"] = _strict_kb_unavailable_message(query)
                return data
            # Open + KB unavailable: keep the prompt notice so the model answers
            # from general knowledge WITH an explicit warning — that is the Open
            # contract, not a leak.
            kb_unavailable_notice = _kb_retrieval_failure_notice(
                False,
                failure_reason,
            )
            prefix = _compose_kb_mode_chat_prefix(
                False,
                templates_block,
                kb_unavailable_notice,
            )
            _prepend_system_prefix(messages, prefix)
            data["messages"] = messages
            return data

        # SPEC-SEC-IDENTITY-ASSERT-001 follow-up: retrieval-api forwards
        # claimed_user_id to portal-api /internal/identity/verify which
        # matches against PortalUser.zitadel_user_id. Sending the LibreChat
        # ObjectId here lands on `reason: "no_membership"` and a 403
        # `identity_assertion_failed` — observed in prod 2026-05-05 once
        # the missing-X-Caller-Service header bug was fixed.
        #
        # From here on we use `user_id` (= Zitadel sub) for everything that
        # leaves this process: retrieval-api request body, gap-events,
        # retrieval-log, _klai_kb_meta. Personal-KB qdrant chunks are
        # stamped with the same Zitadel sub at ingest time
        # (klai-portal/backend/app/api/knowledge.py:172-204) so the
        # personal-scope filter matches.
        user_id = chat_retrieval_policy.user_id
        scope_decision = chat_retrieval_policy.scope_decision
        if (
            user_id is None
            or scope_decision is None
            or scope_decision.scope is None
            or not chat_retrieval_policy.should_retrieve
        ):
            raise RuntimeError("KB chat retrieval policy continued without retrieval inputs")
        kb_narrow = chat_retrieval_policy.kb_narrow
        scope = scope_decision.scope

        conversation_history = _build_conversation_history(messages)

        # SPEC-RAG-TAXONOMY-001 (multi-KB): fetch taxonomy trees + coverage for
        # every KB the request is scoped to in a single retrieval-api roundtrip,
        # Redis-cached. v1 was first-KB-only; v2 merges trees across KBs and
        # uses per-KB coverage so a mixed-coverage request still benefits from
        # the KBs that *do* have a curated taxonomy.
        #
        # Scope rules:
        #   * `scope_decision.kbs_in_scope == []` means either "all org KBs" or
        #     personal-only. In both cases the client does not know an explicit
        #     org-KB set, so taxonomy is skipped (no_kbs_in_scope). Fail-open:
        #     retrieval-api still applies its org/scope filters.
        #   * personal-only scope ("personal") → no org KBs → skip taxonomy.
        kbs_in_scope: list[str] = scope_decision.kbs_in_scope or []
        kb_scope_mode = _kb_scope_mode(
            scope=scope,
            kb_slugs_for_request=scope_decision.kb_slugs_for_request,
        )
        taxonomy_trees: dict[str, list[dict]] = {}
        taxonomy_coverage_map: dict[str, float] = {}
        if kbs_in_scope and TAXONOMY_ENABLED and scope in ("org", "both"):
            taxonomy_trees, taxonomy_coverage_map = await asyncio.gather(
                _fetch_taxonomy_trees(org_id, kbs_in_scope, cache),
                _fetch_taxonomy_coverage(org_id, kbs_in_scope, cache),
            )

        # Filter to KBs that meet the coverage threshold. The classifier sees
        # ONLY trees from these KBs so it cannot return IDs from a KB without
        # curated taxonomy (REQ-1+2: low-coverage KBs run unfiltered).
        kbs_with_coverage: list[str] = [
            slug
            for slug in kbs_in_scope
            if taxonomy_coverage_map.get(slug, 0.0) >= KLAI_TAXONOMY_COVERAGE_THRESHOLD
        ]
        trees_for_classify: dict[str, list[dict]] = {
            slug: taxonomy_trees.get(slug, []) for slug in kbs_with_coverage
        }

        # SPEC-RAG-QUERY-REWRITE-001 + SPEC-RAG-TAXONOMY-001:
        # Combined rewrite + classify in a single LLM call (REQ-5 zero added roundtrip).
        # When trees_for_classify is empty, falls back to plain rewrite.
        # Anti-hallucination guard inside _rewrite_and_classify filters IDs to
        # the union of all valid IDs across the provided KBs (REQ-4).
        # Fail-open: any failure returns (raw_query, [], meta_with_skip_reason).
        (
            rewritten_query,
            classified_node_ids,
            rewrite_meta,
        ) = await _rewrite_and_classify(query, conversation_history, trees_for_classify)
        # SPEC-PRIVACY-QUERY-SHADOW-001 REQ-6: gate raw query content out of
        # the query_rewrite log line in 'off' / 'shadow' mode. Operators keep
        # full-shape diagnostics (timing, was_changed, skip reason) but never
        # see literal customer text.
        telemetry_level = feature.get("telemetry_level", "shadow")
        try:
            # REQ-OBS-03: ``skipped`` IS the skip reason (empty when the
            # rewrite ran); ``prompt_variant`` distinguishes plain/classify.
            if telemetry_level == "full":
                logger.info(
                    "query_rewrite org_id=%s user_id=%s raw_query=%r rewritten_query=%r "
                    "rewrite_ms=%d was_changed=%s skipped=%s prompt_variant=%s",
                    org_id,
                    user_id,
                    query,
                    rewritten_query,
                    rewrite_meta.get("rewrite_ms", 0),
                    rewrite_meta.get("was_changed", False),
                    rewrite_meta.get("skipped", ""),
                    rewrite_meta.get("prompt_variant", ""),
                )
            else:
                logger.info(
                    "query_rewrite_metadata org_id=%s user_id=%s "
                    "rewrite_ms=%d was_changed=%s skipped=%s prompt_variant=%s",
                    org_id,
                    user_id,
                    rewrite_meta.get("rewrite_ms", 0),
                    rewrite_meta.get("was_changed", False),
                    rewrite_meta.get("skipped", ""),
                    rewrite_meta.get("prompt_variant", ""),
                )
        except Exception:
            # Logging itself must never abort the hook (REQ-2 fail-open).
            pass

        # SPEC-RAG-TAXONOMY-001 REQ-1+2: inject taxonomy filter iff at least
        # one in-scope KB has coverage AND the classifier returned >= 1 valid
        # node ID. node IDs are globally unique across portal_taxonomy_nodes,
        # so retrieval-api's ANY-of filter is collision-free across KBs.
        taxonomy_decision = _resolve_kb_taxonomy_decision(
            taxonomy_enabled=TAXONOMY_ENABLED,
            kbs_in_scope=kbs_in_scope,
            kbs_with_coverage=kbs_with_coverage,
            classified_node_ids=classified_node_ids,
        )

        # REQ-7: log taxonomy_classify event on every classify invocation.
        if TAXONOMY_ENABLED and kbs_in_scope:
            try:
                coverage_repr = ",".join(
                    f"{slug}={taxonomy_coverage_map.get(slug, 0.0):.2f}"
                    for slug in kbs_in_scope
                )
                logger.info(
                    "taxonomy_classify org_id=%s kb_slugs=%s coverage=%s "
                    "kbs_with_coverage=%s classified_node_ids=%s "
                    "was_applied=%s skip_reason=%s",
                    org_id,
                    ",".join(kbs_in_scope),
                    coverage_repr,
                    ",".join(kbs_with_coverage),
                    classified_node_ids,
                    taxonomy_decision.applied,
                    taxonomy_decision.skip_reason,
                )
            except Exception:
                pass

        retrieve_body = _build_retrieve_body(
            rewritten_query=rewritten_query,
            raw_query=query,
            org_id=org_id,
            user_id=user_id,
            top_k=RETRIEVE_TOP_K,
            conversation_history=conversation_history,
            telemetry_level=telemetry_level,
            scope_decision=scope_decision,
            taxonomy_applied=taxonomy_decision.applied,
            classified_node_ids=classified_node_ids,
        )

        # Internal-secret auth + X-Caller-Service; end-user identity in the
        # body is verified by retrieval-api against portal. See ``_retrieve``.
        t0 = time.monotonic()
        retrieval_failure: str | None = None
        retrieval_request_id: str | None = None
        result: dict[str, Any] = {}
        try:
            async with httpx.AsyncClient(timeout=RETRIEVE_TIMEOUT) as client:
                resp = await _retrieve(client, retrieve_body)
                retrieval_request_id = resp.headers.get("X-Request-ID")
                resp.raise_for_status()
                result = resp.json()
        except httpx.HTTPStatusError as exc:
            retrieval_request_id = exc.response.headers.get("X-Request-ID")
            # @MX:WARN: 4xx from /retrieve is a config error, not a transient
            # failure. Surface to the user — silent-degrade hid the
            # caller-service-header regression in production for 7 days.
            # @MX:REASON: SPEC-SEC-IDENTITY-ASSERT-001 Phase D + the
            # `retrieve-caller-service-header-mismatch` pitfall.
            body_snippet = _sanitize_upstream_body(
                getattr(exc.response, "text", ""),
                max_len=200,
            )
            logger.error(
                "KlaiKnowledgeHook: retrieval HTTP %d — body=%r — failing loud",
                exc.response.status_code,
                body_snippet,
            )
            retrieval_failure = f"HTTP {exc.response.status_code}"
        except Exception as exc:
            # Network errors / timeouts: still log error (was warning), but
            # surface in chat so a sustained outage is impossible to miss.
            logger.error("KlaiKnowledgeHook: retrieval failed (%s) — failing loud", exc)
            retrieval_failure = type(exc).__name__

        if retrieval_failure is not None:
            # SPEC-RAG-MULTILINGUAL-CHAT-001 Phase 4 (REQ-10): English
            # instruction to the model; the warning the model emits is in the
            # user's detected language thanks to GROUNDED_CHAT_SYSTEM_PROMPT.
            kb_unavailable_notice = _kb_retrieval_failure_notice(
                kb_narrow, retrieval_failure
            )
            prefix = _compose_kb_mode_chat_prefix(
                kb_narrow, templates_block, kb_unavailable_notice
            )
            _prepend_system_prefix(messages, prefix)
            data["messages"] = messages
            original_stream = data.get("stream")
            render_strategy = _select_kb_render_strategy(original_stream)
            if kb_narrow and render_strategy.force_non_streaming:
                data["stream"] = False
            answer_policy = KbAnswerPolicy(
                state="retrieval_failure",
                prompt_mode=chat_retrieval_policy.prompt_mode,
                user_provided_content_context=user_provided_content_context,
            )
            data.setdefault("metadata", {})["_klai_kb_meta"] = answer_policy.to_kb_meta(
                org_id=org_id,
                user_id=user_id,
                user_query=query,
                retrieval_ms=int((time.monotonic() - t0) * 1000),
                no_citable_sources=bool(kb_narrow),
                no_citable_reason="retrieval_failure" if kb_narrow else None,
                no_citable_message=_strict_kb_unavailable_message(query)
                if kb_narrow
                else None,
                original_stream=original_stream,
                render_mode=render_strategy.mode,
                retrieval_failure=retrieval_failure,
                retrieval_request_id=retrieval_request_id,
                kb_scope_mode=kb_scope_mode,
                kbs_in_scope=kbs_in_scope,
            )
            if kb_narrow:
                data["mock_response"] = _strict_kb_unavailable_message(query)
            return data

        retrieval_ms = int((time.monotonic() - t0) * 1000)

        if kb_narrow and result.get("retrieval_bypassed"):
            logger.error(
                "KlaiKnowledgeHook: strict KB retrieval returned retrieval_bypassed=True "
                "org_id=%s user_id=%s — failing closed",
                org_id,
                user_id,
            )
            strict_bypass_failure = "strict_retrieval_bypassed"
            kb_unavailable_notice = _kb_retrieval_failure_notice(
                kb_narrow, strict_bypass_failure
            )
            prefix = _compose_kb_mode_chat_prefix(
                kb_narrow, templates_block, kb_unavailable_notice
            )
            _prepend_system_prefix(messages, prefix)
            data["messages"] = messages
            original_stream = data.get("stream")
            render_strategy = _select_kb_render_strategy(original_stream)
            if render_strategy.force_non_streaming:
                data["stream"] = False
            answer_policy = KbAnswerPolicy(
                state="retrieval_failure",
                prompt_mode=chat_retrieval_policy.prompt_mode,
                user_provided_content_context=user_provided_content_context,
            )
            data.setdefault("metadata", {})["_klai_kb_meta"] = answer_policy.to_kb_meta(
                org_id=org_id,
                user_id=user_id,
                user_query=query,
                retrieval_ms=retrieval_ms,
                no_citable_sources=True,
                no_citable_reason=strict_bypass_failure,
                no_citable_message=_strict_kb_unavailable_message(query),
                original_stream=original_stream,
                render_mode=render_strategy.mode,
                retrieval_failure=strict_bypass_failure,
                retrieval_request_id=retrieval_request_id,
                kb_scope_mode=kb_scope_mode,
                kbs_in_scope=kbs_in_scope,
            )
            data["mock_response"] = _strict_kb_unavailable_message(query)
            return data

        # If the retrieval-gate determined no KB context is needed, skip injection.
        # Multilingual foundation still applies — REQ-10.
        if result.get("retrieval_bypassed"):
            _prepend_system_prefix(
                messages, _compose_kb_mode_chat_prefix(kb_narrow, templates_block)
            )
            data["messages"] = messages
            answer_policy = KbAnswerPolicy(
                state="gate_bypassed",
                prompt_mode=chat_retrieval_policy.prompt_mode,
                user_provided_content_context=user_provided_content_context,
            )
            data.setdefault("metadata", {})["_klai_kb_meta"] = answer_policy.to_kb_meta(
                org_id=org_id,
                user_id=user_id,
                retrieval_ms=retrieval_ms,
                gate_bypassed=True,
                retrieval_request_id=retrieval_request_id,
                kb_scope_mode=kb_scope_mode,
                kbs_in_scope=kbs_in_scope,
            )
            return data

        chunks = result.get("chunks", [])
        raw_chunks = chunks if isinstance(chunks, list) else []
        kbs_with_results = _kb_labels_from_chunks(raw_chunks)
        evidence_pack = result.get("evidence_pack")
        has_evidence_pack = isinstance(evidence_pack, dict)
        if not has_evidence_pack:
            logger.error(
                "retrieval_response_missing_evidence_pack org_id=%s user_id=%s chunks=%d",
                org_id,
                user_id,
                len(chunks) if isinstance(chunks, list) else 0,
            )
            _prepend_system_prefix(
                messages, _compose_kb_mode_chat_prefix(kb_narrow, templates_block)
            )
            data["messages"] = messages
            original_stream = data.get("stream")
            render_strategy = _select_kb_render_strategy(original_stream)
            if render_strategy.force_non_streaming:
                data["stream"] = False
            answer_policy = KbAnswerPolicy(
                state="missing_evidence_pack",
                prompt_mode=chat_retrieval_policy.prompt_mode,
                user_provided_content_context=user_provided_content_context,
            )
            data.setdefault("metadata", {})["_klai_kb_meta"] = answer_policy.to_kb_meta(
                org_id=org_id,
                user_id=user_id,
                user_query=query,
                retrieval_ms=retrieval_ms,
                confidence_band=result.get("confidence_band"),
                no_citable_sources=True,
                no_citable_reason="missing_evidence_pack",
                original_stream=original_stream,
                render_mode=render_strategy.mode,
                retrieval_request_id=retrieval_request_id,
                kb_scope_mode=kb_scope_mode,
                kbs_in_scope=kbs_in_scope,
                kbs_with_results=kbs_with_results,
            )
            if kb_narrow and not user_provided_content_context:
                data["mock_response"] = _no_citable_sources_message(query)
            return data
        evidence_chunks = evidence_pack_items_as_chunks(evidence_pack)
        trusted_sources = trusted_sources_from_evidence_pack(evidence_pack)
        context_chunks = evidence_chunks
        safety_metadata = data.setdefault("metadata", {})
        if _llm_safety_enabled():
            safe_context_chunks: list[dict] = []
            blocked_chunk_ids: list[object] = []
            last_block_decision: SafetyDecision | None = None
            for chunk in context_chunks:
                context_safety_decision = _check_llm_safety(
                    phase=SafetyPhase.CONTEXT,
                    text=_chunk_safety_text(chunk),
                    query=query,
                    org_id=org_id,
                    user_id=user_id,
                    metadata=safety_metadata,
                    chunk_id=chunk.get("chunk_id"),
                )
                if context_safety_decision is None or context_safety_decision.allowed:
                    safe_context_chunks.append(chunk)
                    continue
                blocked_chunk_ids.append(chunk.get("chunk_id"))
                last_block_decision = context_safety_decision
            if blocked_chunk_ids:
                logger.error(
                    "llm_safety_litellm_context_chunks_dropped mode=%s org_id=%s user_id=%s blocked=%d kept=%d chunk_ids=%s",
                    LLM_SAFETY_LITELLM_MODE,
                    org_id,
                    user_id,
                    len(blocked_chunk_ids),
                    len(safe_context_chunks),
                    blocked_chunk_ids,
                )
            if not safe_context_chunks and context_chunks and _llm_safety_enforces():
                logger.error(
                    "llm_safety_litellm_all_context_blocked mode=%s org_id=%s user_id=%s total_blocked=%d reason=%s",
                    LLM_SAFETY_LITELLM_MODE,
                    org_id,
                    user_id,
                    len(blocked_chunk_ids),
                    last_block_decision.reason if last_block_decision else "unknown",
                )
                return _llm_safety_short_circuit(
                    data, query=query, decision=last_block_decision
                )
            context_chunks = safe_context_chunks
            if blocked_chunk_ids:
                trusted_sources = _filter_trusted_sources_for_chunks(
                    trusted_sources,
                    context_chunks,
                )
                evidence_pack = _filter_evidence_pack_for_chunks(
                    evidence_pack,
                    context_chunks,
                )
        # SPEC-RAG-LOW-CONFIDENCE-ABSTAIN-001 REQ-2: confidence band drives the
        # anti-hallucination injection. None on bypass paths (fail-open).
        confidence_band: str | None = result.get("confidence_band")
        low_confidence_inject = _should_apply_low_confidence_injection(
            confidence_band,
            user_query=query,
            evidence_chunks=context_chunks,
        )

        # --- Gap detection (KB-014) ---
        gap_type = _classify_gap(chunks)
        if gap_type is not None and org_id and user_id:
            _fire_gap_event(
                org_id=org_id,
                user_id=user_id,
                query_text=query,
                gap_type=gap_type,
                chunks=chunks,
                retrieval_ms=retrieval_ms,
                taxonomy_node_ids=retrieve_body.get("taxonomy_node_ids") or None,
            )

        # --- Retrieval log (SPEC-KB-015-01) ---
        chunk_ids = [c.get("chunk_id") for c in chunks if c.get("chunk_id")]
        reranker_scores = [c.get("reranker_score") or 0.0 for c in chunks]
        if chunk_ids and not result.get("retrieval_bypassed"):
            _fire_retrieval_log(org_id, user_id, chunk_ids, reranker_scores, query)

        if (
            context_chunks
            and kb_narrow
            and low_confidence_inject
            and not user_provided_content_context
        ):
            # Strict + weak/tangential chunks is not a prompt problem. Bypass
            # the model before it can answer from general knowledge.
            logger.warning(
                "strict_low_confidence_deterministic_refusal "
                "org_id=%s user_id=%s confidence_band=%s chunks_injected=%d",
                org_id,
                user_id,
                confidence_band,
                len(context_chunks),
            )
            original_stream = data.get("stream")
            render_strategy = _select_kb_render_strategy(original_stream)
            if render_strategy.force_non_streaming:
                data["stream"] = False
            answer_policy = KbAnswerPolicy(
                state="chunks_present",
                prompt_mode=chat_retrieval_policy.prompt_mode,
                user_provided_content_context=user_provided_content_context,
                low_confidence_inject=low_confidence_inject,
            )
            data.setdefault("metadata", {})["_klai_kb_meta"] = answer_policy.to_kb_meta(
                org_id=org_id,
                user_id=user_id,
                user_query=query,
                retrieval_ms=retrieval_ms,
                chunks_injected=len(context_chunks),
                chunk_ids=[
                    c.get("chunk_id") for c in context_chunks if c.get("chunk_id")
                ],
                citation_chunks=context_chunks,
                trusted_sources=trusted_sources,
                evidence_pack=evidence_pack if isinstance(evidence_pack, dict) else None,
                citable_sources_count=len(trusted_sources),
                confidence_band=confidence_band,
                no_citable_sources=True,
                no_citable_reason="strict_low_confidence_no_direct_evidence",
                original_stream=original_stream,
                render_mode=render_strategy.mode,
                retrieval_request_id=retrieval_request_id,
                kb_scope_mode=kb_scope_mode,
                kbs_in_scope=kbs_in_scope,
                kbs_with_results=kbs_with_results,
            )
            data["mock_response"] = _no_citable_sources_message(query)
            return data

        if not context_chunks:
            # Zero chunks: inject a mode-aware "zero results" header so
            # the ChatConfigBar Modus toggle (kb_narrow) actually drives
            # behaviour. Without this branch's awareness of kb_narrow,
            # the user's Strict/Open choice was silently ignored when
            # retrieval came up empty:
            #
            #   - Strict + zero chunks USED to fall through to
            #     ``_compose_libre_chat_prefix(templates_block)``. That
            #     gave GROUNDED_CHAT_SYSTEM_PROMPT alone, which softly
            #     discourages general-knowledge fallback but does NOT
            #     command an explicit "this isn't in your KB" reply.
            #     Mistral Small would sometimes refuse, sometimes hedge
            #     with general knowledge anyway — breaking the Strict
            #     popover promise.
            #
            #   - Open + zero chunks hit the SAME generic prefix, which
            #     forbids general-knowledge fallback. That contradicted
            #     the Open popover promise ("may complement with general
            #     knowledge"); the user got "Dat staat niet in de
            #     kennisbank" even when they explicitly wanted the model
            #     to answer from general knowledge with a disclaimer.
            #
            # The per-mode header below makes the two paths deterministic.
            # See ``TestKlaiKnowledgeHookZeroChunksMode`` for the contract.
            # Multilingual foundation still applies — REQ-10.
            empty_kb_header = _kb_zero_chunks_notice(kb_narrow)
            _prepend_system_prefix(
                messages,
                _compose_kb_mode_chat_prefix(
                    kb_narrow, templates_block, empty_kb_header
                ),
            )
            data["messages"] = messages
            if has_evidence_pack:
                original_stream = data.get("stream")
                render_strategy = _select_kb_render_strategy(original_stream)
                if render_strategy.force_non_streaming:
                    data["stream"] = False
                answer_policy = KbAnswerPolicy(
                    state="zero_chunks",
                    prompt_mode=chat_retrieval_policy.prompt_mode,
                    user_provided_content_context=user_provided_content_context,
                    low_confidence_inject=low_confidence_inject,
                )
                data.setdefault("metadata", {})["_klai_kb_meta"] = (
                    answer_policy.to_kb_meta(
                        org_id=org_id,
                        user_id=user_id,
                        user_query=query,
                        retrieval_ms=retrieval_ms,
                        evidence_pack=evidence_pack
                        if isinstance(evidence_pack, dict)
                        else None,
                        confidence_band=confidence_band,
                        # Strict mode (kb_narrow=True) MUST refuse deterministically
                        # when there are no citable sources; trusting the model's
                        # system-prompt instruction to refuse risks a hallucinated
                        # general-knowledge answer leaking through. We force the
                        # post-call renderer to replace the model output with the
                        # language-aware canned refusal from
                        # ``klai_chat_prompts.no_citable_sources_message``.
                        #
                        # Broad mode (kb_narrow=False) lets the model answer from
                        # general knowledge, so leave the flag False — the post-call
                        # guard ``not trusted_sources and not force_no_citable and
                        # not citation_chunks`` short-circuits and the streamed
                        # tokens reach the client unchanged.
                        no_citable_sources=bool(kb_narrow),
                        no_citable_reason=(
                            evidence_pack.get("no_citable_reason")
                            if isinstance(evidence_pack, dict)
                            else None
                        ),
                        original_stream=original_stream,
                        render_mode=render_strategy.mode,
                        retrieval_request_id=retrieval_request_id,
                        kb_scope_mode=kb_scope_mode,
                        kbs_in_scope=kbs_in_scope,
                        kbs_with_results=kbs_with_results,
                    )
                )
                if kb_narrow and not user_provided_content_context:
                    data["mock_response"] = _no_citable_sources_message(query)
            return data

        # SPEC-RAG-MULTILINGUAL-CHAT-001 Phase 4 (REQ-10): English instructions
        # to the model. The user-facing answer is in the user's detected
        # language via GROUNDED_CHAT_SYSTEM_PROMPT prepended at the call site.
        context_prompt = _build_kb_context_prompt(
            kb_narrow=kb_narrow,
            context_chunks=context_chunks,
            trusted_sources=trusted_sources,
            templates_block=templates_block,
            images_base_url=KB_IMAGES_BASE_URL,
            low_confidence_inject=low_confidence_inject,
            low_confidence_injection_disabled=_LOW_CONFIDENCE_INJECTION_DISABLED,
            low_confidence_strict_text=_LOW_CONFIDENCE_INJECTION_TEXT,
            low_confidence_open_text=_LOW_CONFIDENCE_OPEN_CONTEXT_TEXT,
        )
        if context_prompt.low_confidence_injection_applied:
            # NOTE: warning-level (not info) is deliberate. The litellm
            # container's root logger filters info-level emits from
            # non-uvicorn modules (verified 2026-05-08 via VictoriaLogs:
            # zero info-level klai_knowledge events visible despite
            # the code path being reached). Other warning-level events
            # in this module (`KlaiKnowledgeHook: jwt rejected ...`,
            # `query_rewrite_and_classify_failed`) DO surface, so the
            # injection event uses the same level for parity with
            # observable hook events. Lets operators query
            # `service:litellm AND _msg:"low_confidence_injection_applied"`
            # for incident triage on a per-request_id basis.
            logger.warning(
                "low_confidence_injection_applied org_id=%s confidence_band=%s chunks_injected=%d",
                org_id,
                confidence_band,
                len(context_chunks),
            )
        context_block = context_prompt.context_block

        # SPEC-CHAT-TEMPLATES-001 REQ-TEMPLATES-HOOK-E1: templates → KB → existing.
        # SPEC-RAG-MULTILINGUAL-CHAT-001 Phase 4 (REQ-10): GROUNDED_CHAT_SYSTEM_PROMPT
        # leads — same contract as paths B (partner_chat) and C (retrieval-api /chat).
        #
        # Note 2026-05-27: ``templates_block`` is appended inside the context
        # builder after [ANSWER FORMAT] and before chunks. We therefore pass
        # only the built context block to the mode prefix here; passing
        # templates_block again would duplicate it.
        prefix = _compose_kb_mode_chat_prefix(kb_narrow, context_block)
        _prepend_system_prefix(messages, prefix)
        data["messages"] = messages
        original_stream = data.get("stream")
        render_strategy = _select_kb_render_strategy(original_stream)
        if render_strategy.force_non_streaming:
            data["stream"] = False
        answer_policy = KbAnswerPolicy(
            state="chunks_present",
            prompt_mode=chat_retrieval_policy.prompt_mode,
            user_provided_content_context=user_provided_content_context,
            low_confidence_inject=low_confidence_inject,
        )
        # Signal KB injection to downstream hooks (e.g. custom_router, post-call logger)
        # Stored in data["metadata"] so it is never forwarded to the LLM provider.
        data.setdefault("metadata", {})["_klai_kb_meta"] = answer_policy.to_kb_meta(
            org_id=org_id,
            user_id=user_id,
            user_query=query,
            retrieval_ms=retrieval_ms,
            chunks_injected=len(context_chunks),
            chunk_ids=[c.get("chunk_id") for c in context_chunks if c.get("chunk_id")],
            allowed_source_urls=context_prompt.allowed_source_urls,
            allowed_image_urls=context_prompt.allowed_image_urls,
            citation_source_urls=context_prompt.citation_source_urls,
            citation_chunks=context_chunks,
            trusted_sources=trusted_sources,
            evidence_pack=evidence_pack if isinstance(evidence_pack, dict) else None,
            citable_sources_count=len(trusted_sources),
            confidence_band=confidence_band,
            original_stream=original_stream,
            render_mode=render_strategy.mode,
            retrieval_request_id=retrieval_request_id,
            kb_scope_mode=kb_scope_mode,
            kbs_in_scope=kbs_in_scope,
            kbs_with_results=kbs_with_results,
            kbs_used_as_sources=_kb_labels_used_by_sources(
                trusted_sources,
                evidence_pack,
                raw_chunks,
            ),
        )
        return data

    async def async_post_call_success_hook(self, data, user_api_key_dict, response):
        kb_meta = data.get("metadata", {}).get("_klai_kb_meta")
        if kb_meta and not kb_meta.get("gate_bypassed"):
            stats = _compose_non_streaming_kb_response(response, kb_meta)
            _log_kb_citation_render(logger, kb_meta, stats, stream=False)
            logger.info(
                "KB injection: org=%s user=%s chunks=%d retrieval_ms=%d",
                kb_meta["org_id"],
                kb_meta["user_id"],
                kb_meta["chunks_injected"],
                kb_meta["retrieval_ms"],
            )
        return response

    async def async_post_call_streaming_iterator_hook(
        self, user_api_key_dict, response, request_data
    ):
        kb_meta = request_data.get("metadata", {}).get("_klai_kb_meta")
        if (
            not kb_meta
            or kb_meta.get("gate_bypassed")
            or not _is_streaming_kb_render_mode(kb_meta.get("render_mode"))
        ):
            async for item in response:
                yield item
            return

        pending_item = None
        async for item in response:
            if pending_item is not None:
                yield pending_item
            pending_item = item
            stats = _compose_streaming_kb_response(item, kb_meta)
            _log_kb_citation_render(logger, kb_meta, stats, stream=True)
            footer_item = _split_if_rendered_stop_item(item, stats)
            if footer_item is not None:
                yield footer_item
        if pending_item is not None and not kb_meta.get(
            "_citation_stream_sources_appended"
        ):
            stats = _compose_streaming_kb_response(
                pending_item, kb_meta, flush_stream=True
            )
            _log_kb_citation_render(logger, kb_meta, stats, stream=True)
            footer_item = _split_if_rendered_stop_item(pending_item, stats)
            if footer_item is not None:
                yield footer_item
        if pending_item is not None:
            yield pending_item

    async def async_post_call_failure_hook(self, *args, **kwargs):
        pass


# Module-level instance (some LiteLLM versions require this form)
klai_knowledge_hook = KlaiKnowledgeHook()
