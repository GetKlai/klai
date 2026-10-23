"""OpenAI-compatible partner surface: /models, /responses and the general passthrough.

The Responses adapter, request-body validation and token usage limits for keys
with ``general_chat`` live here; the knowledge chat path stays in ``app.api.partner``.
"""

from __future__ import annotations

import json
import re
import uuid
from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from typing import Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request, status
from redis.exceptions import RedisError
from starlette.responses import Response, StreamingResponse

from app.api.partner_dependencies import (
    PartnerAuthContext,
    get_partner_key,
    require_permission,
)
from app.core.config import settings
from app.services.openai_passthrough import openai_chat_completion_non_streaming, openai_chat_completion_streaming
from app.services.partner_rate_limit import check_rate_limit, check_weighted_rate_limit
from app.services.redis_client import get_redis_pool

logger = structlog.get_logger()

router = APIRouter()
_OPENAI_COMPATIBLE_MODELS = {"klai-primary", "klai-fast", "klai-large"}
_OPENAI_COMPATIBLE_MODEL_ALIASES = {
    "gpt-4o-mini": "klai-fast",
    "gpt-4o": "klai-primary",
    "gpt-4.1-mini": "klai-fast",
    "gpt-4.1": "klai-primary",
}
_OPENAI_COMPATIBLE_ACCEPTED_MODELS = _OPENAI_COMPATIBLE_MODELS | set(_OPENAI_COMPATIBLE_MODEL_ALIASES)
# The whole of LibreChat's title request: its fixed completion prompt around a
# "User: ...\nAI: ..." transcript, sent as the only message. Read from
# @librechat/agents title.cjs and run.cjs in LibreChat v0.8.7 (agents 3.2.46)
# and the pinned v0.8.8 (agents 4.0.1), where "completion" is the default
# titleMethod and librechat.yaml sets no titlePrompt. The earlier keyword match
# also took an employee's own message that named a title and a chat, and the
# title path answers with the general model whatever the mode.
_LIBRECHAT_TITLE_PROMPT_RE = re.compile(
    r"Provide a concise, 5-word-or-less title for the conversation, using title case conventions\. "
    r"Only return the title itself\.\n\nConversation:\nUser: [\s\S]*\nAI: [\s\S]*"
)
_OPENAI_COMPAT_MAX_BODY_BYTES = 131_072
_OPENAI_COMPAT_DEFAULT_MAX_TOKENS = 2048
_OPENAI_COMPAT_MAX_TOKENS = 4096
_OPENAI_COMPAT_MAX_N = 1
_OPENAI_COMPAT_MAX_PROMPT_CACHE_KEY_CHARS = 256
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
    "prompt_cache_key",
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


async def _openai_compatible_request_body(
    http_request: Request, *, max_bytes: int = _OPENAI_COMPAT_MAX_BODY_BYTES
) -> dict[str, Any]:
    content_length = http_request.headers.get("content-length")
    if content_length is not None:
        try:
            if int(content_length) > max_bytes:
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
        if total > max_bytes:
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

    if "prompt_cache_key" in forwarded:
        prompt_cache_key = forwarded["prompt_cache_key"]
        if (
            not isinstance(prompt_cache_key, str)
            or not prompt_cache_key
            or len(prompt_cache_key) > _OPENAI_COMPAT_MAX_PROMPT_CACHE_KEY_CHARS
        ):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "error": {
                        "type": "invalid_request",
                        "message": (
                            "prompt_cache_key must be a non-empty string of at most "
                            f"{_OPENAI_COMPAT_MAX_PROMPT_CACHE_KEY_CHARS} characters"
                        ),
                    }
                },
            )

    return forwarded


def _openai_error(status_code: int, message: str, error_type: str = "invalid_request") -> HTTPException:
    return HTTPException(
        status_code=status_code,
        detail={"error": {"type": error_type, "message": message}},
    )


def _uses_knowledge_chat(body: dict[str, Any]) -> bool:
    """Route to the knowledge (RAG) path iff a trigger field's VALUE means "yes".

    Presence alone is no longer enough (breaking change, approved 2026-08-13
    — see docs/runbooks/partner-chat-threading.md "Routing reminder"):

    - ``knowledge``: present and not ``None``, unless it is a dict with
      ``enabled`` exactly ``False``. A non-dict non-null value still
      triggers so it fails loudly in the knowledge path's Pydantic
      validation instead of being silently dropped by the passthrough
      allowlist.
    - ``knowledge_base_ids``: present and not ``None``. An explicit empty
      list ``[]`` remains a trigger so the knowledge path's "ambiguous
      empty list" 400 guard (SPEC-PARTNER-KB-SCOPE-001) keeps firing
      instead of silently falling through to passthrough.
    - ``page_context``: present and not ``None``. A non-dict non-null
      value still triggers so it fails loudly downstream.
    - ``web_search``: truthy.
    - ``web_search_query``: a non-empty string, or any other non-null
      value (non-string non-null values still trigger so knowledge-path
      validation rejects them loudly).
    """
    knowledge = body.get("knowledge")
    if "knowledge" in body and knowledge is not None:
        if not (isinstance(knowledge, dict) and knowledge.get("enabled") is False):
            return True

    if "knowledge_base_ids" in body and body["knowledge_base_ids"] is not None:
        return True

    if "page_context" in body and body["page_context"] is not None:
        return True

    if body.get("web_search"):
        return True

    web_search_query = body.get("web_search_query")
    if web_search_query is not None and web_search_query != "":
        return True

    return False


def _openai_compatible_enabled(auth: PartnerAuthContext) -> bool:
    return bool(auth.permissions.get("general_chat"))


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


def _is_librechat_title_request(messages: object) -> bool:
    if not isinstance(messages, list) or len(messages) != 1:
        return False
    (message,) = messages
    return (
        isinstance(message, dict)
        and message.get("role") == "user"
        and isinstance(message.get("content"), str)
        and _LIBRECHAT_TITLE_PROMPT_RE.fullmatch(message["content"]) is not None
    )


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
    return await _general_passthrough(body, auth=auth)


async def _general_passthrough(
    body: dict[str, Any], *, auth: PartnerAuthContext, delegated_org_id: str | None = None
) -> Response | dict[str, Any]:
    validated_body = _validated_openai_compatible_body(body)
    await _enforce_openai_compatible_usage_limits(auth=auth, body=validated_body)
    if bool(validated_body.get("stream", False)):
        return await openai_chat_completion_streaming(
            validated_body, settings, org_id=auth.org_id, delegated_org_id=delegated_org_id
        )
    return await openai_chat_completion_non_streaming(
        validated_body, settings, org_id=auth.org_id, delegated_org_id=delegated_org_id
    )


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
