"""SPEC-MCP-RETRIEVAL-001 Phase 3: search_knowledge MCP tool tests.

Covers all 14 testcases T-1..T-14 from
``.moai/specs/SPEC-MCP-RETRIEVAL-001/plan.md`` § "Test matrix".

The tool is a thin wrapper around retrieval-api ``/retrieve`` that:
  - identifies the caller via the existing dispatcher (LibreChat or OAuth)
  - clamps top_k to [1, 15]
  - posts to retrieval-api with a 3.0s timeout
  - returns a list[dict] of chunks with title/source_url/text/score/scope
  - fires retrieval-log + (optional) gap-event telemetry tagged with the
    OAuth client_id when the caller is an OAuth client
  - raises ToolError on retrieval-api 4xx/5xx/timeout

These tests stay pure-unit: the retrieval-api HTTP call and all telemetry
emits are mocked. Cross-tenant RLS isolation is verified at the
retrieval-api layer (out of scope here); we only verify that org_id is
forwarded correctly.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from tests._helpers import allow_verify_result

# ─── ctx + mock builders ──────────────────────────────────────────────────


def _make_ctx(headers: dict[str, str] | None = None) -> MagicMock:
    ctx = MagicMock()
    ctx.request_context.request.headers = headers or {}
    return ctx


def _librechat_ctx() -> MagicMock:
    """ctx that triggers the LibreChat dispatcher pad."""
    return _make_ctx(
        {
            "x-user-id": "user1",
            "x-org-id": "org1",
            "x-org-slug": "testorg",
            "x-internal-secret": "test-secret",
        }
    )


def _oauth_ctx(token: str = "klai_mcp_oauth_test_token_123") -> MagicMock:  # noqa: S107 — test fixture token
    """ctx that triggers the OAuth dispatcher pad."""
    return _make_ctx({"authorization": f"Bearer {token}"})


def _make_retrieve_response(
    chunks: list[dict[str, Any]] | None = None, status: int = 200
) -> MagicMock:
    """Build a fake httpx.Response for retrieval-api /retrieve."""
    resp = MagicMock()
    resp.status_code = status
    resp.json = MagicMock(
        return_value={
            "query_resolved": "test",
            "retrieval_bypassed": False,
            "chunks": chunks or [],
        }
    )
    if status >= 400:
        resp.raise_for_status = MagicMock(
            side_effect=httpx.HTTPStatusError(
                f"HTTP {status}",
                request=MagicMock(),
                response=resp,
            )
        )
    else:
        resp.raise_for_status = MagicMock()
    return resp


def _chunk(
    title: str = "Sample doc",
    source_url: str | None = "https://example.com/doc",
    text: str = "Sample content",
    reranker_score: float | None = 0.9,
    scope: str = "org",
    chunk_id: str = "chunk-1",
) -> dict[str, Any]:
    return {
        "chunk_id": chunk_id,
        "title": title,
        "source_url": source_url,
        "text": text,
        "reranker_score": reranker_score,
        "scope": scope,
        "metadata": {"kb_slug": "support"},
    }


# ─── Tool surface basics ──────────────────────────────────────────────────


class TestToolSurface:
    """The tool must be importable as an @mcp.tool function from main."""

    def test_search_knowledge_function_exists(self) -> None:
        from main import search_knowledge

        assert callable(search_knowledge)

    @pytest.mark.asyncio
    async def test_tool_description_contains_citation_guidance(self) -> None:
        """REQ-1 + REQ-15: tool-description carries the citation rule.

        FastMCP stores the description on the registered Tool object,
        not on the wrapped function. Source via ``mcp.list_tools()`` —
        same path the MCP host LLM consumes.
        """
        import main

        tools = await main.mcp.list_tools()
        descriptions = [t.description or "" for t in tools if t.name == "search_knowledge"]
        assert descriptions, "search_knowledge must be registered as an MCP tool"
        joined = descriptions[0].lower()
        # Must contain the source_url citation rule
        assert "source_url" in joined or "source url" in joined
        assert "never invent" in joined or "do not invent" in joined


# ─── T-1, T-13: Happy path with OAuth + caller_client_id ──────────────────


class TestOAuthHappyPath:
    @pytest.mark.asyncio
    async def test_oauth_path_returns_chunks_with_expected_shape(self) -> None:
        from klai_identity_assert.mcp_token_client import McpTokenVerifyResult

        from main import search_knowledge

        ctx = _oauth_ctx()
        chunks_in = [_chunk(chunk_id=f"c{i}") for i in range(3)]

        with (
            patch(
                "main._mcp_token_asserter.verify",
                new_callable=AsyncMock,
                return_value=McpTokenVerifyResult.allow(
                    user_id="zit-user-1",
                    org_id="42",
                    org_slug="acme",
                    scopes=("mcp:knowledge",),
                    resource_uri="https://mcp.getklai.com",
                    client_id="claude-desktop",
                ),
            ),
            patch(
                "httpx.AsyncClient.post",
                new_callable=AsyncMock,
                return_value=_make_retrieve_response(chunks_in),
            ),
        ):
            result = await search_knowledge(query="how do I X?", ctx=ctx, top_k=5)

        assert isinstance(result, list)
        assert len(result) == 3
        for item in result:
            assert set(item.keys()) >= {"title", "source_url", "text", "score", "scope"}

    @pytest.mark.asyncio
    async def test_oauth_path_fires_retrieval_log_with_client_id(self) -> None:
        """REQ-9 + AC-3: telemetry labelled with caller_client_id."""
        from klai_identity_assert.mcp_token_client import McpTokenVerifyResult

        from main import search_knowledge

        ctx = _oauth_ctx()

        with (
            patch(
                "main._mcp_token_asserter.verify",
                new_callable=AsyncMock,
                return_value=McpTokenVerifyResult.allow(
                    user_id="zit-user-1",
                    org_id="42",
                    org_slug="acme",
                    scopes=("mcp:knowledge",),
                    resource_uri="https://mcp.getklai.com",
                    client_id="claude-desktop",
                ),
            ),
            patch(
                "httpx.AsyncClient.post",
                new_callable=AsyncMock,
                return_value=_make_retrieve_response([_chunk()]),
            ),
            patch("main.fire_retrieval_log") as mock_log,
        ):
            await search_knowledge(query="how do I X?", ctx=ctx, top_k=5)

        # fire_retrieval_log called with caller_client_id="claude-desktop"
        mock_log.assert_called_once()
        kwargs = mock_log.call_args.kwargs
        assert kwargs.get("caller_client_id") == "claude-desktop"


# ─── T-2: LibreChat path uses caller_client_id=None ───────────────────────


class TestLibreChatPath:
    @pytest.mark.asyncio
    async def test_librechat_path_works_and_omits_client_id(self) -> None:
        """REQ-9 + AC-4: LibreChat path passes caller_client_id=None."""
        from main import search_knowledge

        ctx = _librechat_ctx()

        with (
            patch(
                "main._asserter.verify",
                new_callable=AsyncMock,
                return_value=allow_verify_result(),
            ),
            patch(
                "httpx.AsyncClient.post",
                new_callable=AsyncMock,
                return_value=_make_retrieve_response([_chunk()]),
            ),
            patch("main.fire_retrieval_log") as mock_log,
        ):
            result = await search_knowledge(query="q", ctx=ctx, top_k=3)

        assert len(result) == 1
        assert mock_log.call_args.kwargs.get("caller_client_id") is None


# ─── T-3, T-11: Empty / non-error edge cases ──────────────────────────────


class TestEmptyResults:
    @pytest.mark.asyncio
    async def test_empty_chunks_returns_empty_list_and_fires_gap(self) -> None:
        """REQ-19 + AC-11: empty chunks = legitimate [], gap-event fires."""
        from main import search_knowledge

        ctx = _librechat_ctx()

        with (
            patch(
                "main._asserter.verify",
                new_callable=AsyncMock,
                return_value=allow_verify_result(),
            ),
            patch(
                "httpx.AsyncClient.post",
                new_callable=AsyncMock,
                return_value=_make_retrieve_response([]),
            ),
            patch("main.fire_retrieval_log"),
            patch("main.fire_gap_event") as mock_gap,
        ):
            result = await search_knowledge(query="q", ctx=ctx)

        assert result == []
        mock_gap.assert_called_once()
        assert mock_gap.call_args.kwargs.get("gap_type") == "hard"


class TestTelemetryFailureSwallowed:
    @pytest.mark.asyncio
    async def test_telemetry_post_failure_does_not_break_tool(self) -> None:
        """REQ-20 + AC-9: telemetry failure swallowed."""
        from main import search_knowledge

        ctx = _librechat_ctx()

        with (
            patch(
                "main._asserter.verify",
                new_callable=AsyncMock,
                return_value=allow_verify_result(),
            ),
            patch(
                "httpx.AsyncClient.post",
                new_callable=AsyncMock,
                return_value=_make_retrieve_response([_chunk()]),
            ),
            patch(
                "main.fire_retrieval_log",
                side_effect=RuntimeError("simulated telemetry failure"),
            ),
        ):
            # MUST NOT raise — telemetry-failure is non-fatal
            result = await search_knowledge(query="q", ctx=ctx)

        assert len(result) == 1


# ─── T-4, T-5, T-6: ToolError on retrieval failure ────────────────────────


class TestRetrievalFailures:
    @pytest.mark.asyncio
    async def test_503_raises_tool_error(self) -> None:
        """REQ-18 + AC-5."""
        from mcp.server.fastmcp.exceptions import ToolError

        from main import search_knowledge

        ctx = _librechat_ctx()

        with (
            patch(
                "main._asserter.verify",
                new_callable=AsyncMock,
                return_value=allow_verify_result(),
            ),
            patch(
                "httpx.AsyncClient.post",
                new_callable=AsyncMock,
                return_value=_make_retrieve_response([], status=503),
            ),
        ):
            with pytest.raises(ToolError):
                await search_knowledge(query="q", ctx=ctx)

    @pytest.mark.asyncio
    async def test_4xx_raises_tool_error(self) -> None:
        """REQ-17 + AC-12."""
        from mcp.server.fastmcp.exceptions import ToolError

        from main import search_knowledge

        ctx = _librechat_ctx()

        with (
            patch(
                "main._asserter.verify",
                new_callable=AsyncMock,
                return_value=allow_verify_result(),
            ),
            patch(
                "httpx.AsyncClient.post",
                new_callable=AsyncMock,
                return_value=_make_retrieve_response([], status=400),
            ),
        ):
            with pytest.raises(ToolError):
                await search_knowledge(query="q", ctx=ctx)

    @pytest.mark.asyncio
    async def test_timeout_raises_tool_error(self) -> None:
        """REQ-18 + AC-5: TimeoutException maps to ToolError."""
        from mcp.server.fastmcp.exceptions import ToolError

        from main import search_knowledge

        ctx = _librechat_ctx()

        with (
            patch(
                "main._asserter.verify",
                new_callable=AsyncMock,
                return_value=allow_verify_result(),
            ),
            patch(
                "httpx.AsyncClient.post",
                new_callable=AsyncMock,
                side_effect=httpx.TimeoutException("simulated timeout"),
            ),
        ):
            with pytest.raises(ToolError):
                await search_knowledge(query="q", ctx=ctx)


# ─── T-7, T-8, T-9: top_k clamping ────────────────────────────────────────


class TestTopKClamping:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "input_top_k,expected",
        [(20, 15), (16, 15), (15, 15), (8, 8), (1, 1), (0, 1), (-5, 1), (-100, 1)],
    )
    async def test_clamps_top_k_to_range(self, input_top_k: int, expected: int) -> None:
        """REQ-12 + AC-6."""
        from main import search_knowledge

        ctx = _librechat_ctx()
        captured: dict[str, Any] = {}

        async def _capture_post(self: Any, url: str, **kwargs: Any) -> MagicMock:
            captured["url"] = url
            captured["json"] = kwargs.get("json")
            return _make_retrieve_response([])

        with (
            patch(
                "main._asserter.verify",
                new_callable=AsyncMock,
                return_value=allow_verify_result(),
            ),
            patch.object(httpx.AsyncClient, "post", new=_capture_post),
        ):
            await search_knowledge(query="q", ctx=ctx, top_k=input_top_k)

        assert captured["json"]["top_k"] == expected


# ─── T-10: Identity verify failure propagates ─────────────────────────────


class TestIdentityFailurePropagation:
    @pytest.mark.asyncio
    async def test_oauth_invalid_token_propagates_error(self) -> None:
        """REQ-2 + AC-17: invalid OAuth token = identification failure.

        The dispatcher raises ``main._IdentificationFailed`` on a denied
        verify; the tool must not silently swallow it into an empty list.
        We assert the exception is the dispatcher's specific class so a
        future refactor that unwraps it accidentally would fail loudly.
        """
        from klai_identity_assert.mcp_token_client import McpTokenVerifyResult

        import main

        ctx = _oauth_ctx()

        with patch(
            "main._mcp_token_asserter.verify",
            new_callable=AsyncMock,
            return_value=McpTokenVerifyResult.deny("token_revoked"),
        ):
            with pytest.raises(main._IdentificationFailed):
                await main.search_knowledge(query="q", ctx=ctx)


# ─── Query length clamp ───────────────────────────────────────────────────


class TestQueryLengthClamp:
    """Defense-in-depth: a runaway 100k-char query gets truncated to 2000
    rather than slamming retrieval-api with the full payload.
    """

    @pytest.mark.asyncio
    async def test_query_under_limit_passes_through_unchanged(self) -> None:
        from main import search_knowledge

        ctx = _librechat_ctx()
        captured: dict[str, Any] = {}

        async def _capture_post(self: Any, url: str, **kwargs: Any) -> MagicMock:
            captured["json"] = kwargs.get("json")
            return _make_retrieve_response([])

        with (
            patch(
                "main._asserter.verify",
                new_callable=AsyncMock,
                return_value=allow_verify_result(),
            ),
            patch.object(httpx.AsyncClient, "post", new=_capture_post),
        ):
            await search_knowledge(query="short query", ctx=ctx)

        assert captured["json"]["query"] == "short query"

    @pytest.mark.asyncio
    async def test_query_over_2000_chars_is_truncated(self) -> None:
        from main import search_knowledge

        ctx = _librechat_ctx()
        captured: dict[str, Any] = {}

        async def _capture_post(self: Any, url: str, **kwargs: Any) -> MagicMock:
            captured["json"] = kwargs.get("json")
            return _make_retrieve_response([])

        long_query = "x" * 5000
        with (
            patch(
                "main._asserter.verify",
                new_callable=AsyncMock,
                return_value=allow_verify_result(),
            ),
            patch.object(httpx.AsyncClient, "post", new=_capture_post),
        ):
            await search_knowledge(query=long_query, ctx=ctx)

        assert len(captured["json"]["query"]) == 2000
        assert len(captured["json"]["raw_query"]) == 2000


# ─── Outbound auth header regression guard ────────────────────────────────


class TestRetrievalSecretUsedForUpstream:
    """Regression guard: retrieval-api validates against its own secret
    (RETRIEVAL_API_INTERNAL_SECRET in SOPS), NOT KNOWLEDGE_INGEST_SECRET.
    Using the wrong env-var name silently 401s every search_knowledge call
    in production.
    """

    @pytest.mark.asyncio
    async def test_retrieval_internal_secret_is_used_in_outbound_header(self) -> None:
        from main import search_knowledge

        ctx = _librechat_ctx()
        captured: dict[str, Any] = {}

        async def _capture_post(self: Any, url: str, **kwargs: Any) -> MagicMock:
            captured["headers"] = kwargs.get("headers", {})
            return _make_retrieve_response([])

        with (
            patch(
                "main._asserter.verify",
                new_callable=AsyncMock,
                return_value=allow_verify_result(),
            ),
            patch.object(httpx.AsyncClient, "post", new=_capture_post),
            patch("main.RETRIEVAL_INTERNAL_SECRET", "retrieval-specific-secret"),
        ):
            await search_knowledge(query="q", ctx=ctx)

        assert captured["headers"]["X-Internal-Secret"] == "retrieval-specific-secret"
        # Ensure we are NOT sending the knowledge-ingest secret (different service)
        assert captured["headers"]["X-Internal-Secret"] != "test-secret"

    @pytest.mark.asyncio
    async def test_caller_service_header_is_knowledge_mcp(self) -> None:
        """AC-20: SPEC-SEC-IDENTITY-ASSERT-001 REQ-4.2 guard."""
        from main import search_knowledge

        ctx = _librechat_ctx()
        captured: dict[str, Any] = {}

        async def _capture_post(self: Any, url: str, **kwargs: Any) -> MagicMock:
            captured["headers"] = kwargs.get("headers", {})
            return _make_retrieve_response([])

        with (
            patch(
                "main._asserter.verify",
                new_callable=AsyncMock,
                return_value=allow_verify_result(),
            ),
            patch.object(httpx.AsyncClient, "post", new=_capture_post),
        ):
            await search_knowledge(query="q", ctx=ctx)

        assert captured["headers"]["X-Caller-Service"] == "knowledge-mcp"


# ─── T-12: org_id forwarding (cross-tenant smoke) ─────────────────────────


class TestOrgIdForwarding:
    @pytest.mark.asyncio
    async def test_request_body_carries_verified_org_id(self) -> None:
        """AC-7: forward verified.org_id to retrieval-api, not body-supplied."""
        from main import search_knowledge

        ctx = _librechat_ctx()
        captured: dict[str, Any] = {}

        async def _capture_post(self: Any, url: str, **kwargs: Any) -> MagicMock:
            captured["url"] = url
            captured["json"] = kwargs.get("json")
            return _make_retrieve_response([])

        with (
            patch(
                "main._asserter.verify",
                new_callable=AsyncMock,
                return_value=allow_verify_result(org_id="42"),
            ),
            patch.object(httpx.AsyncClient, "post", new=_capture_post),
        ):
            await search_knowledge(query="q", ctx=ctx)

        assert captured["json"]["org_id"] == "42"
        assert "/retrieve" in captured["url"]


# ─── Save-tools regression (T-14) lives in existing tests/ — no new test
# needed. The save_personal_knowledge / save_org_knowledge / save_to_docs
# suites in tests/ continue to pass under the Phase 1+2 changes.
