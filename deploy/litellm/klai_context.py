"""Klai provider-context orchestration for Mistral-backed chat.

LibreChat owns UI conversation state. Klai owns the provider-boundary contract:
which messages are safe and useful to send to Mistral, which history is retained
for retrieval/query rewriting, and which metadata explains those choices.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any


STALE_ATTACHMENT_CONTEXT_PLACEHOLDER = (
    "[Earlier uploaded document content omitted from model context. Use the "
    "latest user question and retrieved knowledge-base context instead.]"
)
HISTORY_BUDGET_CONTEXT_PLACEHOLDER = (
    "[Earlier conversation turns omitted from model context because they "
    "exceeded Klai's provider context budget. Use the latest user question, "
    "recent turns, and retrieved knowledge-base context instead.]"
)
STALE_LIBRECHAT_UPLOAD_PREFIX = "Attached document(s):"


@dataclass(frozen=True)
class MistralModelProfile:
    """Budget profile for a Klai model alias backed by Mistral."""

    alias: str
    upstream_model: str
    context_window_chars_estimate: int
    history_budget_chars: int
    output_reserve_chars: int


@dataclass(frozen=True)
class ContextAssemblyResult:
    messages: object
    meta: dict[str, Any]


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        return default


def _default_profiles() -> dict[str, MistralModelProfile]:
    shared_budget = os.getenv("KLAI_CONTEXT_HISTORY_BUDGET_CHARS")
    primary_history_budget = _env_int("KLAI_CONTEXT_PRIMARY_HISTORY_BUDGET_CHARS", 24000)
    fast_history_budget = _env_int("KLAI_CONTEXT_FAST_HISTORY_BUDGET_CHARS", 16000)
    large_history_budget = _env_int("KLAI_CONTEXT_LARGE_HISTORY_BUDGET_CHARS", 48000)
    if shared_budget is not None:
        override = _env_int("KLAI_CONTEXT_HISTORY_BUDGET_CHARS", primary_history_budget)
        primary_history_budget = override
        fast_history_budget = override
        large_history_budget = override

    return {
        "klai-primary": MistralModelProfile(
            alias="klai-primary",
            upstream_model="mistral-small-2603",
            context_window_chars_estimate=768000,
            history_budget_chars=primary_history_budget,
            output_reserve_chars=24000,
        ),
        "klai-fast": MistralModelProfile(
            alias="klai-fast",
            upstream_model="mistral-small-2603",
            context_window_chars_estimate=768000,
            history_budget_chars=fast_history_budget,
            output_reserve_chars=16000,
        ),
        "klai-large": MistralModelProfile(
            alias="klai-large",
            upstream_model="mistral-large-2512",
            context_window_chars_estimate=768000,
            history_budget_chars=large_history_budget,
            output_reserve_chars=48000,
        ),
    }


def _text_from_text_parts(content: object) -> str | None:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return None

    texts: list[str] = []
    for part in content:
        if not isinstance(part, dict):
            return None
        if part.get("type") != "text" or not isinstance(part.get("text"), str):
            return None
        texts.append(part["text"])
    return "\n".join(texts)


def _last_user_index(messages: list[object]) -> int | None:
    return next(
        (
            index
            for index in range(len(messages) - 1, -1, -1)
            if isinstance(messages[index], dict) and messages[index].get("role") == "user"
        ),
        None,
    )


def _is_stale_upload_text(text: str) -> bool:
    return text.lstrip().startswith(STALE_LIBRECHAT_UPLOAD_PREFIX)


class KlaiContextOrchestrator:
    """Build the Mistral/retrieval context view for one LiteLLM request.

    In the current LiteLLM callback order this runs before ``custom_router`` on
    the LibreChat path, so the model profile is based on the requested alias,
    not the final routed alias. The profile metadata is still useful for direct
    ``klai-fast``/``klai-large`` calls and for Phase 2 router integration.
    """

    def __init__(
        self,
        profiles: dict[str, MistralModelProfile] | None = None,
        *,
        default_profile: str = "klai-primary",
    ) -> None:
        self._profiles = profiles or _default_profiles()
        self._default_profile = default_profile

    def profile_for(self, requested_model: object) -> MistralModelProfile:
        alias = requested_model if isinstance(requested_model, str) else ""
        return self._profiles.get(alias) or self._profiles[self._default_profile]

    def assemble(
        self,
        messages: object,
        *,
        requested_model: object = "klai-primary",
        normalize_content: bool = True,
        apply_history_budget: bool = True,
    ) -> ContextAssemblyResult:
        profile = self.profile_for(requested_model)
        meta: dict[str, Any] = {
            "version": "v1",
            "orchestrator": "klai_context",
            "provider": "mistral",
            "requested_model": requested_model if isinstance(requested_model, str) else "",
            "profile_selection_phase": "pre_router_litellm_callback",
            "model_profile": profile.alias,
            "upstream_model": profile.upstream_model,
            "context_window_chars_estimate": profile.context_window_chars_estimate,
            "history_budget_chars": profile.history_budget_chars,
            "history_budget_applied": apply_history_budget,
            "output_reserve_chars": profile.output_reserve_chars,
            "normalized_text_part_messages": 0,
            "normalized_user_text_part_messages": 0,
            "stale_attachment_placeholders": 0,
            "omitted_history_messages": 0,
            "kept_history_chars": 0,
            "unknown_message_shapes": 0,
            "unknown_content_shapes": 0,
            "reason_codes": [],
        }
        if not isinstance(messages, list):
            meta["reason_codes"].append("messages_not_list")
            return ContextAssemblyResult(messages=messages, meta=meta)

        last_user_index = _last_user_index(messages)
        if last_user_index is None:
            meta["reason_codes"].append("no_user_message")
            return ContextAssemblyResult(messages=messages, meta=meta)

        normalized_messages: list[object] = []
        for index, message in enumerate(messages):
            if not isinstance(message, dict):
                normalized_messages.append(message)
                meta["unknown_message_shapes"] += 1
                continue

            role = message.get("role")
            next_message = message
            if normalize_content and role in ("user", "assistant"):
                text_content = _text_from_text_parts(message.get("content"))
                if text_content is None:
                    meta["unknown_content_shapes"] += 1
                elif text_content != message.get("content"):
                    next_message = {**message, "content": text_content}
                    meta["normalized_text_part_messages"] += 1
                    if role == "user":
                        meta["normalized_user_text_part_messages"] += 1

                if (
                    index != last_user_index
                    and role == "user"
                    and isinstance(next_message.get("content"), str)
                    and _is_stale_upload_text(next_message["content"])
                ):
                    next_message = {
                        **next_message,
                        "content": STALE_ATTACHMENT_CONTEXT_PLACEHOLDER,
                    }
                    meta["stale_attachment_placeholders"] += 1

            normalized_messages.append(next_message)

        if not apply_history_budget:
            return ContextAssemblyResult(messages=normalized_messages, meta=meta)

        budget = max(profile.history_budget_chars, 0)
        kept_chars = 0
        omitted_history_indexes: set[int] = set()
        history_indexes = [
            index
            for index in range(0, last_user_index)
            if isinstance(normalized_messages[index], dict)
            and normalized_messages[index].get("role") in ("user", "assistant")
        ]
        for index in reversed(history_indexes):
            message = normalized_messages[index]
            if not isinstance(message, dict) or message.get("role") not in ("user", "assistant"):
                continue
            content = message.get("content")
            if not isinstance(content, str):
                continue
            if kept_chars + len(content) > budget:
                omitted_history_indexes.update(
                    history_index
                    for history_index in history_indexes
                    if history_index <= index
                )
                break
            kept_chars += len(content)

        meta["kept_history_chars"] = kept_chars
        if not omitted_history_indexes:
            return ContextAssemblyResult(messages=normalized_messages, meta=meta)

        assembled: list[object] = []
        placeholder_inserted = False
        for index, message in enumerate(normalized_messages):
            if index in omitted_history_indexes:
                if not placeholder_inserted:
                    assembled.append(
                        {
                            "role": "system",
                            "content": HISTORY_BUDGET_CONTEXT_PLACEHOLDER,
                        }
                    )
                    placeholder_inserted = True
                continue
            assembled.append(message)

        meta["omitted_history_messages"] = len(omitted_history_indexes)
        meta["reason_codes"].append("history_budget_exceeded")
        return ContextAssemblyResult(messages=assembled, meta=meta)
