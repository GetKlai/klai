"""
Web-search-aware model routing for LiteLLM proxy.

Intercepts klai-primary requests via pre_call_hook and routes based on
whether the messages contain LibreChat web search content:

  Web search detected  →  klai-fast     (open-mistral-nemo, fast/cheap for large context)
  Normal conversation  →  klai-primary  (mistral-small, stronger for tool use / agents)

Web search detection heuristics (any one triggers fast routing):
  1. A message contains >= MIN_SEARCH_URLS URLs (https://) — scraped content chunks
  2. Total token count exceeds SEARCH_TOKEN_THRESHOLD (very long = search context)

Normal agentic / MCP conversations stay on mistral-small regardless of length,
because nemo (12B) is too weak for multi-step tool orchestration.
"""

import re
from litellm.integrations.custom_logger import CustomLogger
import litellm

# URL count in a single message that indicates scraped web content
MIN_SEARCH_URLS = 3

# Hard ceiling: if somehow we get here without URLs, very long is still search
SEARCH_TOKEN_THRESHOLD = 3000

_URL_RE = re.compile(r"https?://\S+")


def _looks_like_search(messages: list) -> bool:
    """Return True if messages appear to contain LibreChat web search results."""
    for msg in messages:
        content = msg.get("content") or ""
        if isinstance(content, list):
            # Multipart content (text + images etc.)
            content = " ".join(
                part.get("text", "") for part in content if isinstance(part, dict)
            )
        if not isinstance(content, str):
            continue
        url_count = len(_URL_RE.findall(content))
        if url_count >= MIN_SEARCH_URLS:
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
            if _looks_like_search(messages):
                data["model"] = "klai-fast"
                return data

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
