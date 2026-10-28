"""portal-api internal Slack API client (SPEC-SLACK-001 §7.2)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

import httpx
import structlog
from log_utils import sanitize_response_body

logger = structlog.get_logger()

TIMEOUT_S = 10.0


class PortalError(Exception):
    pass


class LinkKeyInvalid(PortalError):
    """404: unknown key, or not a slack key (portal answers both with one message)."""


class LinkFeatureLocked(PortalError):
    """403: the org lacks the ``slack`` platform unlock."""


class LinkConflict(PortalError):
    """409: ``org_already_linked`` or ``team_already_linked``."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class PortalUnavailable(PortalError):
    """Connect error, timeout, 5xx, or any status the contract does not define."""


@dataclass(frozen=True)
class LinkResult:
    org_id: int
    org_name: str
    key_id: int
    kb_ids: list[int]


class PortalClient:
    def __init__(
        self,
        base_url: str,
        internal_secret: str,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._secret = internal_secret
        self._transport = transport

    async def _request(
        self, method: str, path: str, *, json: dict[str, Any] | None = None, redact: str = ""
    ) -> httpx.Response:
        request_id = str(uuid.uuid4())
        headers = {"Authorization": f"Bearer {self._secret}", "X-Request-ID": request_id}
        with structlog.contextvars.bound_contextvars(request_id=request_id):
            try:
                async with httpx.AsyncClient(
                    base_url=self._base_url, timeout=TIMEOUT_S, transport=self._transport
                ) as client:
                    response = await client.request(method, path, json=json, headers=headers)
            except httpx.HTTPError as exc:
                logger.exception("portal_api_unreachable", path=path, url=self._base_url)
                raise PortalUnavailable(type(exc).__name__) from exc
            if not response.is_success:
                logger.error(
                    "portal_api_error",
                    path=path,
                    status=response.status_code,
                    body=sanitize_response_body(response, [self._secret, redact]),
                )
            return response

    async def link(
        self, api_key: str, team_id: str, team_name: str, slack_user_id: str
    ) -> LinkResult:
        response = await self._request(
            "POST",
            "/internal/slack/link",
            json={
                "api_key": api_key,
                "team_id": team_id,
                "team_name": team_name,
                "slack_user_id": slack_user_id,
            },
            redact=api_key,
        )
        if response.status_code == 404:
            raise LinkKeyInvalid
        if response.status_code == 403:
            raise LinkFeatureLocked
        if response.status_code == 409:
            raise LinkConflict(_conflict_code(response))
        if response.status_code != 200:
            raise PortalUnavailable(f"link returned {response.status_code}")
        body = response.json()
        return LinkResult(
            org_id=int(body["org_id"]),
            org_name=str(body["org_name"]),
            key_id=int(body["key_id"]),
            kb_ids=[int(kb_id) for kb_id in body["kb_ids"]],
        )

    async def unlink(self, team_id: str) -> None:
        response = await self._request("DELETE", f"/internal/slack/link/{team_id}")
        if not response.is_success:
            raise PortalUnavailable(f"unlink returned {response.status_code}")


def _conflict_code(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return "unknown"
    # FastAPI wraps an HTTPException detail as {"detail": {...}}; accept the bare shape too.
    detail = body.get("detail", body) if isinstance(body, dict) else None
    code = detail.get("code") if isinstance(detail, dict) else None
    return code if isinstance(code, str) else "unknown"
