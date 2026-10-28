"""In-memory stand-ins for Slack, the repository and the Partner API. All values are synthetic."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import httpx
from slack_sdk.errors import SlackApiError

KEY = "pk_live_synthetic_key_0001"
KBS = [
    {"id": 1, "name": "Handboek", "slug": "handboek", "access_level": "read"},
    {"id": 2, "name": "Support", "slug": "support", "access_level": "read"},
    {"id": 3, "name": "Productinfo", "slug": "productinfo", "access_level": "read"},
]


def completion(text: str = "Het verlof is 25 dagen [1].", sources: int = 2) -> dict[str, Any]:
    return {
        "id": "chatcmpl-synthetic-1",
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": text,
                    "sources": [
                        {
                            "label": str(i),
                            "title": f"Document {i}",
                            "url": f"https://docs.example.com/{i}",
                        }
                        for i in range(1, sources + 1)
                    ],
                }
            }
        ],
    }


class FakeSlack:
    """Records every Web API call as (method, kwargs); ``errors[method]`` makes it fail."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.errors: dict[str, str] = {}
        self.users: dict[str, dict[str, Any]] = {}
        self.replies: list[dict[str, Any]] = []
        self._ts = 0

    def __getattr__(self, method: str) -> Any:
        async def call(**kwargs: Any) -> dict[str, Any]:
            self.calls.append((method, kwargs))
            if method in self.errors:
                raise SlackApiError(method, {"ok": False, "error": self.errors[method]})
            if method == "users_info":
                return {
                    "ok": True,
                    "user": {"id": kwargs["user"], **self.users.get(kwargs["user"], {})},
                }
            if method == "conversations_replies":
                return {"ok": True, "messages": self.replies}
            if method == "views_open":
                return {"ok": True, "view": {"id": "V1"}}
            self._ts += 1
            return {"ok": True, "ts": f"1700000000.{self._ts:06d}"}

        return call

    def named(self, method: str) -> list[dict[str, Any]]:
        return [kwargs for name, kwargs in self.calls if name == method]


@dataclass
class Store:
    """The slice of ``app.repo`` the conversation code uses."""

    link: Any = field(
        default_factory=lambda: SimpleNamespace(
            org_name="Voorbeeld BV",
            key_id=42,
            linked_at=datetime(2026, 1, 1, tzinfo=UTC),
            revoked_at=None,
        )
    )
    channel_defaults: dict[tuple[str, str], list[int]] = field(default_factory=dict)
    thread_scopes: dict[tuple[str, str, str], list[int]] = field(default_factory=dict)
    turns: list[SimpleNamespace] = field(default_factory=list)

    REPO_FUNCTIONS = (
        "get_link",
        "get_channel_default",
        "set_channel_default",
        "list_channel_defaults",
        "get_thread_scope",
        "set_thread_scope",
        "record_turn",
        "list_turns",
    )

    async def get_link(self, _s: object, _team: str) -> Any:
        return self.link

    async def get_channel_default(self, _s: object, team: str, channel: str) -> list[int] | None:
        return self.channel_defaults.get((team, channel))

    async def set_channel_default(
        self, _s: object, team: str, channel: str, kb_ids: list[int] | None, set_by: str
    ) -> None:
        if kb_ids:
            self.channel_defaults[(team, channel)] = kb_ids
        else:
            self.channel_defaults.pop((team, channel), None)

    async def list_channel_defaults(self, _s: object, team: str) -> dict[str, list[int]]:
        return {c: ids for (t, c), ids in self.channel_defaults.items() if t == team}

    async def get_thread_scope(
        self, _s: object, team: str, channel: str, ts: str
    ) -> list[int] | None:
        return self.thread_scopes.get((team, channel, ts))

    async def set_thread_scope(
        self, _s: object, team: str, channel: str, ts: str, kb_ids: list[int] | None, set_by: str
    ) -> None:
        if kb_ids:
            self.thread_scopes[(team, channel, ts)] = kb_ids
        else:
            self.thread_scopes.pop((team, channel, ts), None)

    async def record_turn(self, _s: object, **kwargs: Any) -> None:
        self.turns.append(SimpleNamespace(**kwargs))

    async def list_turns(self, _s: object, team: str, channel: str, ts: str) -> list[Any]:
        return [
            turn
            for turn in self.turns
            if (turn.team_id, turn.channel_id, turn.thread_ts) == (team, channel, ts)
        ]


@dataclass
class Partner:
    """Partner API behind an httpx MockTransport."""

    kbs: list[dict[str, Any]] = field(default_factory=lambda: list(KBS))
    chat_responses: list[httpx.Response] = field(default_factory=list)
    requests: list[httpx.Request] = field(default_factory=list)

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url.path == "/partner/v1/knowledge-bases":
            return httpx.Response(200, json=self.kbs)
        if request.url.path == "/partner/v1/feedback":
            return httpx.Response(200, json={"ok": True})
        if self.chat_responses:
            return self.chat_responses.pop(0)
        return httpx.Response(200, json=completion())

    def bodies(self, path: str) -> list[dict[str, Any]]:
        return [json.loads(r.content) for r in self.requests if r.url.path == path]

    def chats(self) -> list[dict[str, Any]]:
        return self.bodies("/partner/v1/chat/completions")
