"""Partner API client (SPEC-SLACK-001 §4).

Slack-free on purpose (§11): a Teams adapter should be able to reuse it as is.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, Literal

import httpx
import structlog
from log_utils import sanitize_response_body

logger = structlog.get_logger()

CHAT_TIMEOUT_S = 120.0
DEFAULT_TIMEOUT_S = 10.0


class PartnerError(Exception):
    def __init__(self, message: str, *, status: int | None, request_id: str) -> None:
        super().__init__(message)
        self.status = status
        self.request_id = request_id


class PartnerAuthError(PartnerError):
    """401/403: the key is revoked, or the requested scope fell outside it (``error_type``)."""

    def __init__(self, message: str, *, status: int, request_id: str, error_type: str | None):
        super().__init__(message, status=status, request_id=request_id)
        self.error_type = error_type


class PartnerRateLimited(PartnerError):
    def __init__(self, *, retry_after: int | None, request_id: str) -> None:
        super().__init__("partner api rate limited", status=429, request_id=request_id)
        self.retry_after = retry_after


class PartnerUnavailable(PartnerError):
    """5xx, timeout, connect error, or a body we cannot parse."""


@dataclass(frozen=True)
class Source:
    title: str
    url: str | None
    label: str | None = None


@dataclass(frozen=True)
class ChatResult:
    text: str
    sources: list[Source]
    completion_id: str | None
    raw: dict[str, Any] = field(repr=False)


@dataclass(frozen=True)
class KnowledgeBase:
    id: int
    name: str
    slug: str
    access_level: str


def _retry_after(response: httpx.Response) -> int | None:
    try:
        return int(response.headers["Retry-After"])
    except (KeyError, ValueError):
        return None


def _error_type(response: httpx.Response) -> str | None:
    try:
        body = response.json()
    except ValueError:
        return None
    detail = body.get("detail", body) if isinstance(body, dict) else None
    error = detail.get("error") if isinstance(detail, dict) else None
    return error.get("type") if isinstance(error, dict) else None


class PartnerClient:
    def __init__(
        self, base_url: str, api_key: str, *, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._transport = transport

    async def _request(
        self, method: str, path: str, *, json: dict[str, Any] | None = None, timeout: float
    ) -> httpx.Response:
        request_id = str(uuid.uuid4())
        headers = {"Authorization": f"Bearer {self._api_key}", "X-Request-ID": request_id}
        with structlog.contextvars.bound_contextvars(request_id=request_id):
            try:
                async with httpx.AsyncClient(
                    base_url=self._base_url, timeout=timeout, transport=self._transport
                ) as client:
                    response = await client.request(method, path, json=json, headers=headers)
            except httpx.HTTPError as exc:
                logger.exception("partner_api_unreachable", path=path, url=self._base_url)
                raise PartnerUnavailable(
                    f"partner api unreachable: {type(exc).__name__}",
                    status=None,
                    request_id=request_id,
                ) from exc

            if response.is_success:
                return response

            logger.error(
                "partner_api_error",
                path=path,
                status=response.status_code,
                body=sanitize_response_body(response, [self._api_key]),
            )
            if response.status_code in (401, 403):
                raise PartnerAuthError(
                    "partner api refused the key",
                    status=response.status_code,
                    request_id=request_id,
                    error_type=_error_type(response),
                )
            if response.status_code == 429:
                raise PartnerRateLimited(retry_after=_retry_after(response), request_id=request_id)
            raise PartnerUnavailable(
                f"partner api returned {response.status_code}",
                status=response.status_code,
                request_id=request_id,
            )

    async def list_knowledge_bases(self) -> list[KnowledgeBase]:
        response = await self._request(
            "GET", "/partner/v1/knowledge-bases", timeout=DEFAULT_TIMEOUT_S
        )
        return [
            KnowledgeBase(
                id=int(kb["id"]), name=kb["name"], slug=kb["slug"], access_level=kb["access_level"]
            )
            for kb in response.json()
        ]

    async def chat(self, messages: list[dict[str, str]], kb_ids: list[int] | None) -> ChatResult:
        """One non-streaming answer. ``kb_ids=None`` searches every KB the key allows."""
        knowledge: dict[str, Any] = {"include_sources": True}
        if kb_ids is not None:
            knowledge["knowledge_base_ids"] = kb_ids
        body = {
            "model": "klai-primary",
            "stream": False,
            "messages": messages,
            "knowledge": knowledge,
        }
        response = await self._request(
            "POST", "/partner/v1/chat/completions", json=body, timeout=CHAT_TIMEOUT_S
        )
        try:
            raw = response.json()
            message = raw["choices"][0]["message"]
            text = message.get("content") or ""
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            logger.exception("partner_api_unparseable_completion")
            raise PartnerUnavailable(
                "unparseable completion",
                status=response.status_code,
                request_id=response.request.headers["X-Request-ID"],
            ) from exc
        sources = [
            Source(
                title=s.get("title") or s.get("url") or "", url=s.get("url"), label=s.get("label")
            )
            for s in message.get("sources") or []
            if isinstance(s, dict)
        ]
        return ChatResult(text=text, sources=sources, completion_id=raw.get("id"), raw=raw)

    async def feedback(
        self,
        message_id: str,
        conversation_id: str,
        rating: Literal["thumbsUp", "thumbsDown"],
        text: str | None = None,
    ) -> None:
        body: dict[str, Any] = {
            "message_id": message_id,
            "conversation_id": conversation_id,
            "rating": rating,
        }
        if text:
            body["text"] = text
        await self._request("POST", "/partner/v1/feedback", json=body, timeout=DEFAULT_TIMEOUT_S)
