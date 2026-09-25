"""
Stable prompt_cache_key injection for Mistral prompt caching.

Mistral bills a request's shared-prefix tokens at 10% of the standard input
price when the request carries a `prompt_cache_key` that matches an earlier
request sharing the same prefix. Cache blocks are 64 tokens (a shorter
prefix never hits) and a hit is reported in
`usage.prompt_tokens_details.cached_tokens`. Mistral's own backend also
caches some repeated-prefix requests without any key at all (measured
2026-09-25 against the production proxy: a second identical call with no
`prompt_cache_key` in the body still showed `cached_tokens > 0`) -- the key
is a routing hint that raises the hit *rate* under concurrent, multi-shard
traffic, not a strict precondition for any hit to occur.
(docs.mistral.ai/api/endpoint/chat, docs.mistral.ai/studio-api/conversations/
advanced/prompt-caching, read 2026-09-25.)

One central pre_call_hook, not per-caller changes: every klai-* alias (chat
via portal-api/LibreChat, Graphiti extraction in klai-knowledge-ingest,
portal-api judge/grounding/paraphrase calls) already goes through this one
proxy, so adding the key here once covers all of them.

Why the key MUST go through `data["extra_body"]`, not `data["prompt_cache_key"]`:
verified against the installed litellm==1.96.2 source and against a mocked
outbound HTTP capture (both `litellm.acompletion` directly and
`litellm.Router.acompletion`, the call the proxy itself makes) --

  - A bare top-level `prompt_cache_key` kwarg is dropped before it even
    reaches Mistral-specific code, regardless of `drop_params` or
    `allowed_openai_params`. `litellm.main.completion()`'s own
    `optional_param_args` dict (the hard-coded set of kwargs it forwards to
    `get_optional_params`) has no entry for `prompt_cache_key`, and
    `get_non_default_completion_params()` excludes it from the catch-all
    `non_default_params` precisely BECAUSE it is a recognized
    `OPENAI_CHAT_COMPLETION_PARAMS` name -- so it never reaches
    `get_optional_params` at all, and MistralConfig (which also does not
    list `prompt_cache_key` in `get_supported_openai_params()`) never gets
    a chance to drop or keep it. This is an upstream gap: the param is
    schema-recognized but not yet wired into `completion()`'s forwarding
    list, and it applies identically via `litellm.completion()`,
    `litellm.acompletion()`, and `litellm.Router.acompletion()` (verified
    for all three).
  - `extra_body`, by contrast, is not a recognized OpenAI param name, so it
    survives `get_non_default_completion_params()` intact, and
    `add_provider_specific_params_to_optional_params()`'s catch-all (taken
    for "mistral", which is not in `litellm.constants.
    openai_compatible_providers`) copies it straight into `optional_params`
    -- bypassing the whole `_check_valid_arg` / `drop_params` gate, which
    only inspects recognized param names. `llm_http_handler.py` then pops
    `extra_body` back out of `optional_params` and merges its keys into the
    final request dict (`data = {**data, **extra_body}`), so
    `prompt_cache_key` lands at the top level of the JSON actually sent to
    Mistral -- exactly the shape Mistral's API expects.

Guarded by tests/test_klai_prompt_cache.py so a litellm bump that finally
wires `prompt_cache_key` into `completion()`, or that closes the `extra_body`
passthrough, goes red instead of silently breaking caching again.

Cache key derivation: sha256 of (final routed model, concatenated
system-role message content). The system prompt is the stable,
repeated-verbatim prefix for every caller here (KB system prompt, Graphiti
extraction instructions, portal-api judge/grounding/paraphrase templates);
everything after it (retrieved chunks, conversation turns) varies per
request and does not need to match the key for a hit -- Mistral matches on
the actual byte prefix regardless of which key is presented, so the key only
needs to be stable, not exhaustive over the whole prompt.

Registered last in `litellm_settings.callbacks` (config.yaml) so the key is
derived from whatever `klai_pii_enforce` produced as the final system-role
content (its own pre_call_hook can mask entities in-place), not a pre-mask
draft that would never match what is actually sent.

Known gap, out of scope for this change: LiteLLM's own spend accounting
does not bill cached tokens at Mistral's 10% rate for these models --
`cache_read_input_token_cost` is absent from every mistral-small-2603 /
mistral-medium-3.5 / mistral-large-2512 entry in litellm's pricing map, so
`generic_cost_per_token()` prices `cached_tokens` at $0 (confirmed by
litellm's own startup warning: "cache cost fields will default to 0. To
track cache cost, add cache_creation_input_token_cost and
cache_read_input_token_cost to model_info"). Internal spend tracking will
under-report actual Mistral spend once hits start happening; fixing it means
adding `model_info` overrides to config.yaml's `model_list`, a separate,
narrowly-scoped change.
"""

from __future__ import annotations

import hashlib
import logging

from litellm.integrations.custom_logger import CustomLogger

logger = logging.getLogger("klai_prompt_cache")


def _message_text(message: dict) -> str:
    content = message.get("content") or ""
    if isinstance(content, list):
        return " ".join(part.get("text", "") for part in content if isinstance(part, dict))
    return content if isinstance(content, str) else ""


def _stable_prefix(messages: list) -> str:
    """Concatenate every system-role message, in order.

    The system role is what every caller here (KB chat, Graphiti, portal-api
    judge/grounding/paraphrase) keeps byte-stable across repeated calls; user
    and assistant turns vary per request and are deliberately excluded.
    """
    return "\n".join(_message_text(m) for m in messages if m.get("role") == "system")


class PromptCacheKeyInjector(CustomLogger):
    async def async_pre_call_hook(self, user_api_key_dict, cache, data, call_type):
        if call_type not in ("completion", "acompletion"):
            return data

        messages = data.get("messages") or []
        prefix = _stable_prefix(messages)
        if not prefix:
            return data

        extra_body = data.get("extra_body")
        if extra_body is None:
            extra_body = {}
            data["extra_body"] = extra_body
        elif not isinstance(extra_body, dict):
            # A caller already set a non-dict extra_body; do not clobber it.
            return data

        model = data.get("model") or ""
        digest = hashlib.sha256(f"{model}\n{prefix}".encode()).hexdigest()[:32]
        cache_key = f"klai-{digest}"
        extra_body["prompt_cache_key"] = cache_key
        # WARNING: the container only ships WARNING+ to VictoriaLogs (see
        # custom_router.py's klai_router_final_model line for the same
        # constraint) -- this is the only place a cache-key assignment is
        # visible without reading Mistral's own usage.prompt_tokens_details.
        logger.warning("klai_prompt_cache_key_set model=%s cache_key=%s", model, cache_key)
        return data


prompt_cache_key_injector = PromptCacheKeyInjector()
