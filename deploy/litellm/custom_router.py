"""
Context-aware model routing for LiteLLM proxy.

Intercepts Klai chat requests via pre_call_hook and routes based on
conversation context:

  Tool call history detected         →  klai-large    (mistral-large, agentic/MCP flows)
  Long user message detected         →  klai-large    (complex analytical request)
  Web search content detected        →  klai-fast     (mistral-small, speed for synthesis)
  Default                            →  klai-primary  (mistral-small, normal chat)

Scope: klai-primary is routed for LibreChat/chat traffic. Explicit klai-fast,
klai-large, and klai-medium calls are only provider-assembled when they carry a
chat-origin signal (LibreChat user or Klai chat metadata). Internal background
services (Graphiti, enrichment, batch pipelines) use direct aliases without chat
metadata and bypass provider context assembly.

Detection:
  - Tool calls: any message with role="tool" → agentic flow, needs strong reasoning
  - Long user message: last user message > USER_MESSAGE_THRESHOLD tokens → complex
    analytical request. Counts only role="user" messages so KB chunks and system
    prompts injected by klai_knowledge_hook do not trigger false positives.
  - Web search: any message with >= MIN_SEARCH_URLS URLs → scraped content injection
  - Hard ceiling: token count > SEARCH_TOKEN_THRESHOLD → fast (safety net)

Note: LibreChat web search injects scraped content as message *content* (not as
tool call results), so the two signals are distinct with no overlap.
"""

import logging
import re
from litellm.integrations.custom_logger import CustomLogger
import litellm
from klai_context import KlaiContextOrchestrator

# URL count in a single message that indicates scraped web content
MIN_SEARCH_URLS = 3

# Hard token ceiling fallback (safety net, not primary signal)
SEARCH_TOKEN_THRESHOLD = 3000

# Last user message token count above this → complex analytical request → klai-large.
# ~300 tokens ≈ 225 words; casual chat stays well below 80 tokens.
USER_MESSAGE_THRESHOLD = 300

_URL_RE = re.compile(r"https?://\S+")
logger = logging.getLogger("custom_router")
KLAI_CHAT_MODELS = frozenset(
    {"klai-primary", "klai-fast", "klai-large", "klai-medium"}
)
_KLAI_CONTEXT_ORCHESTRATOR = KlaiContextOrchestrator()


def _has_tool_calls(messages: list) -> bool:
    """Return True if messages contain tool call results (agentic/MCP flow)."""
    for msg in messages:
        if msg.get("role") == "tool":
            return True
    return False


def _looks_like_search(messages: list) -> bool:
    """Return True if messages appear to contain LibreChat web search results."""
    for msg in messages:
        content = msg.get("content") or ""
        if isinstance(content, list):
            content = " ".join(
                part.get("text", "") for part in content if isinstance(part, dict)
            )
        if not isinstance(content, str):
            continue
        if len(_URL_RE.findall(content)) >= MIN_SEARCH_URLS:
            return True
    return False


def _context_meta_has_tool_history(metadata: dict) -> bool:
    context_meta = metadata.get("_klai_context_meta")
    if not isinstance(context_meta, dict):
        return False
    return bool(
        context_meta.get("omitted_tool_messages")
        or context_meta.get("omitted_tool_content_parts")
    )


def _is_chat_context_request(requested_model: object, data: dict, metadata: dict) -> bool:
    if requested_model == "klai-primary":
        return True
    return bool(
        data.get("user")
        or metadata.get("_klai_kb_meta")
        or metadata.get("_klai_context_meta")
        or metadata.get("_klai_router_meta")
    )


def _merge_context_meta(
    previous_context_meta: object,
    next_context_meta: dict,
) -> dict:
    previous = previous_context_meta if isinstance(previous_context_meta, dict) else {}
    merged = {
        **previous,
        **next_context_meta,
        "pre_router_meta": previous,
    }
    merged["omitted_tool_messages"] = int(previous.get("omitted_tool_messages") or 0) + int(
        next_context_meta.get("omitted_tool_messages") or 0
    )
    merged["omitted_tool_content_parts"] = int(
        previous.get("omitted_tool_content_parts") or 0
    ) + int(next_context_meta.get("omitted_tool_content_parts") or 0)
    # The knowledge hook and router can both see the same active tool turn.
    # Keep the observed count without double-counting a single provider request.
    for counter in (
        "active_tool_results_converted",
        "active_tool_results_preserved",
        "active_tool_calls_preserved",
        "empty_active_tool_results",
        "trailing_assistant_repaired",
    ):
        merged[counter] = max(
            int(previous.get(counter) or 0),
            int(next_context_meta.get(counter) or 0),
        )
    merged["reason_codes"] = list(
        dict.fromkeys(
            [
                *list(previous.get("reason_codes") or []),
                *list(next_context_meta.get("reason_codes") or []),
            ]
        )
    )
    return merged


class TokenRouter(CustomLogger):
    async def async_pre_call_hook(self, user_api_key_dict, cache, data, call_type):
        if call_type not in ("completion", "acompletion"):
            return data

        requested_model = data.get("model")
        if requested_model not in KLAI_CHAT_MODELS:
            return data

        messages = data.get("messages") or []
        if not messages:
            return data

        metadata = data.setdefault("metadata", {})
        should_apply_provider_context = _is_chat_context_request(
            requested_model,
            data,
            metadata,
        )
        final_model = requested_model
        route_reason = "explicit_model"
        last_user_tokens: int | None = None
        total_tokens: int | None = None
        try:
            if requested_model == "klai-primary":
                # Agentic/MCP flow: tool call history → mistral-large. The
                # knowledge hook may already have stripped provider-unsafe tool
                # roles, so also honor its retained context metadata.
                if _has_tool_calls(messages) or _context_meta_has_tool_history(metadata):
                    final_model = "klai-large"
                    route_reason = "tool_history"
                else:
                    # Long user message → complex analytical request → klai-large.
                    # Only the last user message is counted so KB chunks injected by
                    # klai_knowledge_hook (role=system/assistant) don't trigger this.
                    last_user = next(
                        (m for m in reversed(messages) if m.get("role") == "user"), None
                    )
                    if last_user:
                        last_user_tokens = litellm.token_counter(
                            model="mistral/mistral-small-latest",
                            messages=[last_user],
                        )
                    if last_user_tokens and last_user_tokens > USER_MESSAGE_THRESHOLD:
                        final_model = "klai-large"
                        route_reason = "long_user_message"
                    elif _looks_like_search(messages):
                        final_model = "klai-fast"
                        route_reason = "web_search_content"
                    elif metadata.get("_klai_kb_meta"):
                        final_model = requested_model
                        route_reason = "kb_context"
                    else:
                        # Safety net: very long context without tool calls → klai-fast
                        total_tokens = litellm.token_counter(
                            model="mistral/mistral-small-latest",
                            messages=messages,
                        )
                        if total_tokens > SEARCH_TOKEN_THRESHOLD:
                            final_model = "klai-fast"
                            route_reason = "long_context_safety_net"
                        else:
                            route_reason = "default"
        except Exception:
            final_model = requested_model
            route_reason = "routing_error"

        data["model"] = final_model
        metadata["_klai_router_meta"] = {
            "requested_model": requested_model,
            "final_model": final_model,
            "route_reason": route_reason,
            "last_user_tokens": last_user_tokens,
            "total_tokens": total_tokens,
            "provider_context_phase": "post_router",
            "provider_context_applied": should_apply_provider_context,
        }

        if not should_apply_provider_context:
            return data

        try:
            context_result = _KLAI_CONTEXT_ORCHESTRATOR.assemble(
                data.get("messages"),
                requested_model=requested_model,
                final_model=final_model,
                token_counter=litellm.token_counter,
            )
            data["messages"] = context_result.messages
            metadata["_klai_context_meta"] = _merge_context_meta(
                metadata.get("_klai_context_meta"),
                context_result.meta,
            )
            logger.info(
                "klai_router_final_model requested_model=%s final_model=%s "
                "route_reason=%s model_profile=%s token_budget_applied=%s "
                "omitted_history_messages=%d omitted_tool_messages=%d",
                requested_model,
                final_model,
                route_reason,
                context_result.meta["model_profile"],
                context_result.meta["token_budget_applied"],
                context_result.meta["omitted_history_messages"],
                metadata["_klai_context_meta"]["omitted_tool_messages"],
            )
        except Exception as exc:
            metadata["_klai_router_meta"]["provider_context_applied"] = False
            metadata["_klai_router_meta"]["provider_context_error"] = type(exc).__name__
            logger.warning("klai_provider_context_assembly_failed error=%s", exc)

        return data


token_router = TokenRouter()
