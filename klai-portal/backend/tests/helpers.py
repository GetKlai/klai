"""Shared test helpers for Klai backend tests.

Import directly in test files:

    from helpers import FakeResult, FakeKB, make_partner_auth, make_request, setup_db
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from starlette.requests import Request

# ---------------------------------------------------------------------------
# Generic DB result mock
# ---------------------------------------------------------------------------


class FakeResult:
    """Mimics all common SQLAlchemy async result access patterns.

    Use setup_db() for multi-query flows.
    Use as a direct return_value for single-query endpoints:

        db.execute = AsyncMock(return_value=FakeResult(rows=[my_row]))
    """

    def __init__(self, rows: list | None = None, scalar_value=None) -> None:
        self._rows = rows or []
        self._scalar_value = scalar_value

    def scalars(self) -> MagicMock:
        mock = MagicMock()
        mock.all.return_value = self._rows
        return mock

    def scalar_one_or_none(self):
        return self._rows[0] if self._rows else None

    def one_or_none(self):
        """Return the single row (typically a tuple) or None.

        SQLAlchemy 2.0 distinction: ``scalar_one_or_none()`` returns the first
        column of the first row; ``one_or_none()`` returns the whole row.
        Tests that mock multi-column SELECT (e.g. ``select(PortalOrg, PortalUser)``)
        expect ``one_or_none()`` to return the full tuple as the test stored it.
        """
        return self._rows[0] if self._rows else None

    def scalar(self):
        return self._scalar_value

    def fetchall(self) -> list:
        return self._rows

    def __iter__(self):
        return iter(self._rows)


def setup_db(mock_db: AsyncMock, results: list[FakeResult]) -> None:
    """Configure mock_db.execute to return results in sequence.

    Results are consumed in order; the last result is repeated for any
    additional calls beyond the list length.  This mirrors the real DB
    behaviour where every execute() call is independent.
    """
    call_count = 0

    async def _execute(*args, **kwargs):
        nonlocal call_count
        idx = min(call_count, len(results) - 1)
        call_count += 1
        return results[idx] if results else FakeResult()

    mock_db.execute = AsyncMock(side_effect=_execute)


# ---------------------------------------------------------------------------
# Partner API fakes
# ---------------------------------------------------------------------------


@dataclass
class FakeKB:
    """Mimics a PortalKnowledgeBase ORM row."""

    id: int
    name: str
    slug: str
    org_id: int


def make_partner_auth(
    permissions: dict | None = None,
    kb_access: dict | None = None,
):
    """Return a PartnerAuthContext with sensible test defaults.

    Override permissions or kb_access as needed per test:

        make_partner_auth(permissions={"chat": True, "knowledge_append": True, "general_chat": False})
        make_partner_auth(kb_access={10: "read_write"})
    """
    from app.api.partner_dependencies import PartnerAuthContext

    return PartnerAuthContext(
        key_id="key-uuid-1",
        org_id=42,
        zitadel_org_id="zit-org-42",
        permissions=permissions or {"chat": True, "feedback": True, "knowledge_append": False, "general_chat": False},
        kb_access=kb_access if kb_access is not None else {10: "read", 20: "read_write"},
        rate_limit_rpm=60,
    )


# ---------------------------------------------------------------------------
# Synthetic Starlette Request for tests that call FastAPI handlers directly
# ---------------------------------------------------------------------------


def make_request(
    *,
    method: str = "POST",
    path: str = "/",
    headers: dict[str, str] | None = None,
    client: tuple[str, int] | None = ("127.0.0.1", 12345),
) -> Request:
    """Build a Starlette ``Request`` suitable for in-process handler calls.

    Real FastAPI routing injects a ``Request`` per call. Tests that bypass the
    router (``await login(...)`` style) need an equivalent. SPEC-SEC-SESSION-001
    REQ-1.1 made ``request`` a required parameter on ``/auth/login`` so the UA
    hash + IP-subnet snapshot can be captured.

    Defaults to ``127.0.0.1:12345`` so ``resolve_caller_ip`` returns a parseable
    address without test setup.
    """
    scope: dict[str, Any] = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": method,
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "scheme": "http",
        "server": ("testserver", 80),
        "headers": [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()],
        "client": client,
    }
    return Request(scope)


def selection_quoting_first_passage(body: dict) -> dict:
    """A selection step reply that quotes the first line of passage 1 from the request it answers.

    The default for an LLM double: a knowledge turn whose selection points at
    no sentence is "not found" (widget_turn.mark_knowledge_turn_without_evidence),
    so a test about what comes after the selection needs one that chose.
    """
    content = body["messages"][1]["content"]
    first_line = content.split("Passages:\n[1] ", 1)[1].split("\n", 2)[1]
    return {
        "need": "",
        "parts": [{"ask": "vraag", "evidence": [{"passage": 1, "quote": first_line}]}],
        "must_ask_variant": False,
        "question": "",
    }


async def streamed_reply_frames(*, include_sources: bool = True, **kwargs: Any) -> list[bytes]:
    """Every SSE frame of a reply the one writer streams, as an internal Open or general turn sends it."""
    from app.services.widget_reply import streamed_frames
    from app.services.widget_write import write_reply

    reply = await write_reply(stream=True, include_sources=include_sources, **kwargs)
    return [frame async for frame in streamed_frames(reply, include_sources=include_sources)]


async def held_reply_body(*, include_sources: bool = True, **kwargs: Any) -> dict:
    """The completion body of a reply the one writer holds, as a non-streaming request gets it."""
    from app.services.widget_reply import reply_body
    from app.services.widget_write import write_reply

    reply = await write_reply(include_sources=include_sources, **kwargs)
    return reply_body(reply, model=kwargs["model"], include_sources=include_sources)
