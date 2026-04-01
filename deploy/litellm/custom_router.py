"""
Context-aware model routing for LiteLLM proxy.

Intercepts klai-primary requests via pre_call_hook and routes based on
conversation context:

  Tool call history detected         →  klai-large    (mistral-large, agentic/MCP flows)
  Long user message detected         →  klai-large    (complex analytical request)
  Web search content detected        →  klai-fast     (mistral-small, speed for synthesis)
  Default                            →  klai-primary  (mistral-small, normal chat)

Scope: LibreChat/chat traffic only. Internal background services (Graphiti,
enrichment, batch pipelines) use klai-fast directly, which bypasses this router
entirely — the hook returns early for any model != "klai-primary".

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

import re
from litellm.integrations.custom_logger import CustomLogger
import litellm

# URL count in a single message that indicates scraped web content
MIN_SEARCH_URLS = 3

# Hard token ceiling fallback (safety net, not primary signal)
SEARCH_TOKEN_THRESHOLD = 3000

# Last user message token count above this → complex analytical request → klai-large.
# ~300 tokens ≈ 225 words; casual chat stays well below 80 tokens.
USER_MESSAGE_THRESHOLD = 300

_URL_RE = re.compile(r"https?://\S+")


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


class TokenRouter(CustomLogger):
    async def async_pre_call_hook(self, user_api_key_dict, cache, data, call_type):
        if data.get("model") != "klai-primary":
            return data

        messages = data.get("messages") or []
        if not messages:
            return data

        try:
            # Agentic/MCP flow: tool call history → mistral-large
            if _has_tool_calls(messages):
                data["model"] = "klai-large"
                return data

            # Long user message → complex analytical request → mistral-large.
            # Only the last user message is counted so KB chunks injected by
            # klai_knowledge_hook (role=system/assistant) don't trigger this.
            last_user = next(
                (m for m in reversed(messages) if m.get("role") == "user"), None
            )
            if last_user:
                user_tokens = litellm.token_counter(
                    model="mistral/mistral-small-latest",
                    messages=[last_user],
                )
                if user_tokens > USER_MESSAGE_THRESHOLD:
                    data["model"] = "klai-large"
                    return data

            # Web search: scraped content → klai-fast
            if _looks_like_search(messages):
                data["model"] = "klai-fast"
                return data

            # KB-context present → never downgrade regardless of token count.
            # KB chunks are compact and pre-ranked; the safety-net is for scraped
            # web content, not knowledge base context.
            if data.get("metadata", {}).get("_klai_kb_meta"):
                return data

            # Safety net: very long context without tool calls → klai-fast
            token_count = litellm.token_counter(
                model="mistral/mistral-small-latest",
                messages=messages,
            )
            if token_count > SEARCH_TOKEN_THRESHOLD:
                data["model"] = "klai-fast"
        except Exception:
            pass

        return data


token_router = TokenRouter()
