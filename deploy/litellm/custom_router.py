"""
Token-based model routing for LiteLLM proxy.

Intercepts klai-primary requests via pre_call_hook and routes based on
input token count:

  <= TOKEN_THRESHOLD  →  klai-primary  (mistral-small, for short/simple prompts)
  >  TOKEN_THRESHOLD  →  klai-fast     (open-mistral-nemo, for web search / long context)

Web search always produces >600 tokens (system prompt + scraped content chunks).
Normal chat is typically <200 tokens.
"""

from litellm.integrations.custom_logger import CustomLogger
import litellm

TOKEN_THRESHOLD = 600


class TokenRouter(CustomLogger):
    async def async_pre_call_hook(self, user_api_key_dict, cache, data, call_type):
        if data.get("model") != "klai-primary":
            return data

        messages = data.get("messages") or []
        if not messages:
            return data

        try:
            token_count = litellm.token_counter(
                model="mistral/mistral-small-latest",
                messages=messages,
            )
            if token_count > TOKEN_THRESHOLD:
                data["model"] = "klai-fast"
        except Exception:
            pass

        return data


token_router = TokenRouter()
