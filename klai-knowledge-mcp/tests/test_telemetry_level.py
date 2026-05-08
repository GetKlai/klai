"""SPEC-PRIVACY-QUERY-SHADOW-001 Unit 5 — knowledge-mcp telemetry forwarding.

The MCP path is privacy-by-default: every /retrieve body MUST carry
``telemetry_level: "shadow"``. This is intentional and conservative —
third-party MCP traffic (Claude Desktop / Cursor / ChatGPT) is treated
more strictly than first-party LibreChat traffic. Operators who need
'full'-mode debug visibility can flip the kb_feature toggle for their
LibreChat path; the MCP path stays in shadow until a future SPEC adds
a per-tenant lookup.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from tests._helpers import allow_verify_result


def _ctx() -> MagicMock:
    ctx = MagicMock()
    ctx.request_context.request.headers = {
        "x-user-id": "user1",
        "x-org-id": "org1",
        "x-org-slug": "testorg",
        "x-internal-secret": "test-secret",
    }
    return ctx


def _make_resp(chunks: list[dict] | None = None) -> MagicMock:
    resp = MagicMock()
    resp.status_code = 200
    resp.json = MagicMock(
        return_value={
            "query_resolved": "test",
            "retrieval_bypassed": False,
            "chunks": chunks or [],
        }
    )
    resp.raise_for_status = MagicMock()
    return resp


@pytest.mark.asyncio
async def test_mcp_retrieve_body_carries_shadow_telemetry_level() -> None:
    """REQ-4: every MCP /retrieve body has telemetry_level='shadow'."""
    from main import search_knowledge

    captured_body: dict = {}

    async def _capture_post(*args, **kwargs):
        captured_body.update(kwargs.get("json") or {})
        return _make_resp([])

    with (
        patch(
            "main._asserter.verify",
            new_callable=AsyncMock,
            return_value=allow_verify_result(),
        ),
        patch.object(
            httpx.AsyncClient,
            "post",
            new=AsyncMock(side_effect=_capture_post),
        ),
        patch("main.fire_retrieval_log"),
        patch("main.fire_gap_event"),
    ):
        await search_knowledge(query="hoe stel ik vakantie aan?", ctx=_ctx(), top_k=5)

    assert captured_body.get("telemetry_level") == "shadow"
