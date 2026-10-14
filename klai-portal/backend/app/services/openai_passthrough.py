"""The general OpenAI-compatible passthrough to LiteLLM, streaming and non-streaming.

Used by partner keys with ``general_chat``; no retrieval, no knowledge prompt.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any

import httpx
import structlog
from fastapi import HTTPException, status
from fastapi.responses import JSONResponse, StreamingResponse

from app.core.config import Settings
from app.services.chat_sse import _sse_error_frame
from app.services.litellm_delegation import with_delegated_org, with_feature_tag
from app.trace import get_trace_headers

logger = structlog.get_logger()


def _with_openai_passthrough_metadata(
    body: dict[str, Any], *, org_id: int | str | None = None, delegated_org_id: str | None = None
) -> dict[str, Any]:
    """Mark portal-proxied OpenAI-compatible calls so LiteLLM hooks stay transparent.

    Also translates the OpenAI-style top-level ``prompt_cache_key`` into
    ``extra_body`` so LiteLLM delivers it to Mistral (LiteLLM's mistral chat
    transformation drops the top-level field under ``drop_params: true``).
    The translation OVERWRITES ``extra_body`` — caller-supplied ``extra_body``
    is never merged or forwarded.

    The cache key is namespaced per tenant (``org:{org_id}:{key}``) before
    forwarding. All Klai tenants share a single upstream Mistral API key, so
    forwarding a partner-supplied key verbatim would let two different orgs
    collide on the same cache entry — a cross-tenant timing/billing oracle
    (an attacker holding a victim's exact prompt prefix could infer via
    ``usage.prompt_tokens_details.cached_tokens`` whether it was recently
    sent by someone else). Namespacing is invisible to partners — they keep
    sending their own short key. ``org_id=None`` still gets the literal
    ``org:none:`` prefix — an un-namespaced key is never forwarded.
    """
    forwarded = dict(body)
    forwarded["metadata"] = {"_klai_openai_passthrough": True}
    with_delegated_org(forwarded, delegated_org_id)
    with_feature_tag(forwarded, "portal:partner-openai-passthrough")
    prompt_cache_key = forwarded.pop("prompt_cache_key", None)
    if prompt_cache_key is not None:
        namespace = org_id if org_id is not None else "none"
        forwarded["extra_body"] = {"prompt_cache_key": f"org:{namespace}:{prompt_cache_key}"}
    return forwarded


def _cache_usage_fields(response_json: Any) -> tuple[int | None, int]:
    """Extract prompt/cached token counts for cache-usage telemetry.

    Never raises — malformed or missing usage data yields safe defaults so
    this telemetry-only helper can never break the response to the caller.
    """
    try:
        usage = response_json.get("usage") if isinstance(response_json, dict) else None
        if not isinstance(usage, dict):
            return None, 0
        prompt_tokens = usage.get("prompt_tokens")
        details = usage.get("prompt_tokens_details")
        cached_tokens = details.get("cached_tokens") if isinstance(details, dict) else None
        if not isinstance(cached_tokens, int):
            cached_tokens = 0
        return prompt_tokens, cached_tokens
    except Exception:
        return None, 0


def _openai_passthrough_litellm_key(settings: Settings) -> str:
    key = settings.litellm_general_chat_key.strip()
    if not key:
        logger.error("partner_openai_general_chat_key_missing")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"error": {"type": "service_unavailable", "message": "General chat key is not configured"}},
        )
    if key == settings.litellm_master_key.strip():
        logger.error("partner_openai_general_chat_key_matches_master")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"error": {"type": "service_unavailable", "message": "General chat key is not configured"}},
        )
    return key


def _passthrough_key(settings: Settings, delegated_org_id: str | None) -> str:
    # LiteLLM honours a delegated org only on the master key, so an internal
    # call that must be PII-masked for its org cannot use the general chat key.
    return settings.litellm_master_key if delegated_org_id else _openai_passthrough_litellm_key(settings)


def _json_response_from_upstream(resp: httpx.Response) -> JSONResponse:
    try:
        content = resp.json()
    except ValueError:
        content = {
            "error": {
                "type": "upstream_error",
                "message": resp.text or "Chat service error",
            }
        }
    return JSONResponse(status_code=resp.status_code, content=content)


async def _close_openai_stream(client: httpx.AsyncClient, stream: Any) -> None:
    try:
        await stream.__aexit__(None, None, None)
    finally:
        await client.aclose()


async def _proxy_openai_stream(
    *,
    client: httpx.AsyncClient,
    stream: Any,
    resp: httpx.Response,
    org_id: int | str | None,
    chat_url: str,
) -> AsyncGenerator[bytes]:
    try:
        async for chunk in resp.aiter_bytes():
            if chunk:
                yield chunk
    except httpx.TransportError:
        logger.warning("partner_openai_chat_upstream_unreachable", org_id=org_id, target=chat_url, exc_info=True)
        yield _sse_error_frame("Chat service unavailable")
        yield b"data: [DONE]\n\n"
    finally:
        await _close_openai_stream(client, stream)


async def openai_chat_completion_non_streaming(
    request_body: dict[str, Any],
    settings: Settings,
    *,
    org_id: int | str | None = None,
    delegated_org_id: str | None = None,
) -> dict[str, Any] | JSONResponse:
    """Forward an OpenAI-compatible chat completion request to LiteLLM unchanged.

    This is the generic partner passthrough path. It deliberately skips Klai KB
    retrieval, citation composition, source filtering, and prompt injection.
    """
    chat_url = f"{settings.litellm_base_url}/v1/chat/completions"
    api_key = _passthrough_key(settings, delegated_org_id)
    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(
                chat_url,
                json=_with_openai_passthrough_metadata(request_body, org_id=org_id, delegated_org_id=delegated_org_id),
                headers={
                    "Authorization": f"Bearer {api_key}",
                    **get_trace_headers(),
                },
            )
            if 400 <= resp.status_code < 500:
                return _json_response_from_upstream(resp)
            resp.raise_for_status()
            response_json = resp.json()
            prompt_tokens, cached_tokens = _cache_usage_fields(response_json)
            logger.info(
                "partner_openai_cache_usage",
                org_id=org_id,
                cache_key_present="prompt_cache_key" in request_body,
                prompt_tokens=prompt_tokens,
                cached_tokens=cached_tokens,
            )
            return response_json
    except httpx.TransportError as exc:
        logger.warning(
            "partner_openai_chat_upstream_unreachable",
            org_id=org_id,
            target=chat_url,
            exc_info=True,
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={"error": {"type": "upstream_error", "message": "Chat service unavailable"}},
        ) from exc
    except httpx.HTTPStatusError as exc:
        logger.warning(
            "partner_openai_chat_upstream_error",
            org_id=org_id,
            status_code=exc.response.status_code,
            exc_info=True,
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={"error": {"type": "upstream_error", "message": "Chat service error"}},
        ) from exc


async def openai_chat_completion_streaming(
    request_body: dict[str, Any],
    settings: Settings,
    *,
    org_id: int | str | None = None,
    delegated_org_id: str | None = None,
) -> StreamingResponse | JSONResponse:
    """Proxy LiteLLM's OpenAI-compatible SSE stream without buffering or rewriting."""
    chat_url = f"{settings.litellm_base_url}/v1/chat/completions"
    api_key = _passthrough_key(settings, delegated_org_id)
    client: httpx.AsyncClient | None = None
    stream = None
    try:
        client = httpx.AsyncClient(timeout=120.0)
        stream = client.stream(
            "POST",
            chat_url,
            json=_with_openai_passthrough_metadata(request_body, org_id=org_id, delegated_org_id=delegated_org_id),
            headers={
                "Authorization": f"Bearer {api_key}",
                **get_trace_headers(),
            },
        )
        resp = await stream.__aenter__()
        if 400 <= resp.status_code < 500:
            response = _json_response_from_upstream(resp)
            await _close_openai_stream(client, stream)
            return response
        resp.raise_for_status()
        return StreamingResponse(
            _proxy_openai_stream(client=client, stream=stream, resp=resp, org_id=org_id, chat_url=chat_url),
            media_type=resp.headers.get("content-type", "text/event-stream"),
        )
    except httpx.TransportError as exc:
        if client is not None and stream is not None:
            await _close_openai_stream(client, stream)
        elif client is not None:
            await client.aclose()
        logger.warning("partner_openai_chat_upstream_unreachable", org_id=org_id, target=chat_url, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={"error": {"type": "upstream_error", "message": "Chat service unavailable"}},
        ) from exc
    except httpx.HTTPStatusError as exc:
        if client is not None and stream is not None:
            await _close_openai_stream(client, stream)
        elif client is not None:
            await client.aclose()
        logger.warning(
            "partner_openai_chat_upstream_error",
            org_id=org_id,
            status_code=exc.response.status_code,
            exc_info=True,
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={"error": {"type": "upstream_error", "message": "Chat service error"}},
        ) from exc
