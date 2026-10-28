"""portal-api internal Slack API client (SPEC-SLACK-001 §7.2, §8 target state)."""

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
    """The credential is refused: 404 on ``link`` (unknown or non-slack key), 401 on
    ``link_oauth`` (link token invalid or expired)."""


class LinkFeatureLocked(PortalError):
    """403: the org lacks the ``slack`` platform unlock."""


class LinkConflict(PortalError):
    """409: ``org_already_linked``, ``team_already_linked`` or (``link_oauth``) ``token_used``."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class PortalUnavailable(PortalError):
    """Connect error, timeout, 5xx, or any status the contract does not define."""


@dataclass(frozen=True)
class LinkResult:
    org_id: int
    org_name: str
    key_id: str
    kb_ids: list[int]


@dataclass(frozen=True)
class OAuthLinkResult(LinkResult):
    """``link_oauth``: the portal minted the key itself, so it hands it over once."""

    api_key: str
    return_url: str


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
        body = _link_body(response, invalid_status=404)
        return LinkResult(
            org_id=int(body["org_id"]),
            org_name=str(body["org_name"]),
            key_id=str(body["key_id"]),
            kb_ids=[int(kb_id) for kb_id in body["kb_ids"]],
        )

    async def link_oauth(
        self, token: str, team_id: str, team_name: str, slack_user_id: str
    ) -> OAuthLinkResult:
        """Portal-driven link: the portal checks its signed link token and mints the key."""
        response = await self._request(
            "POST",
            "/internal/slack/link-oauth",
            json={
                "token": token,
                "team_id": team_id,
                "team_name": team_name,
                "slack_user_id": slack_user_id,
            },
            redact=token,
        )
        body = _link_body(response, invalid_status=401)
        return OAuthLinkResult(
            org_id=int(body["org_id"]),
            org_name=str(body["org_name"]),
            key_id=str(body["key_id"]),
            kb_ids=[int(kb_id) for kb_id in body["kb_ids"]],
            api_key=str(body["api_key"]),
            return_url=str(body["return_url"]),
        )

    async def unlink(self, team_id: str) -> None:
        response = await self._request("DELETE", f"/internal/slack/link/{team_id}")
        if not response.is_success:
            raise PortalUnavailable(f"unlink returned {response.status_code}")


def _link_body(response: httpx.Response, *, invalid_status: int) -> dict[str, Any]:
    if response.status_code == invalid_status:
        raise LinkKeyInvalid
    if response.status_code == 403:
        raise LinkFeatureLocked
    if response.status_code == 409:
        raise LinkConflict(_conflict_code(response))
    if response.status_code != 200:
        raise PortalUnavailable(f"{response.request.url.path} returned {response.status_code}")
    return response.json()


def _conflict_code(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return "unknown"
    # FastAPI wraps an HTTPException detail as {"detail": {...}}; accept the bare shape too.
    detail = body.get("detail", body) if isinstance(body, dict) else None
    code = detail.get("code") if isinstance(detail, dict) else None
    return code if isinstance(code, str) else "unknown"
