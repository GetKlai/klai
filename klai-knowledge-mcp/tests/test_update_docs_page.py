"""Tests for the update_docs_page MCP tool.

The tool is intentionally update-only: it preflights the Klai Docs page with
GET, then PUTs the full replacement body with a SHA. A missing page must not
turn into an accidental create.
"""

from __future__ import annotations

import re
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests._helpers import allow_verify_result


def _make_ctx(headers: dict | None = None) -> MagicMock:
    ctx = MagicMock()
    ctx.request_context.request.headers = headers or {}
    return ctx


_VALID_HEADERS = {
    "x-user-id": "user1",
    "x-org-id": "org1",
    "x-org-slug": "testorg",
    "x-internal-secret": "test-secret",
}


def _response(status_code: int, body: dict | list | None = None, text: str = "") -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.text = text
    if body is None:
        resp.json.side_effect = ValueError("no json")
    else:
        resp.json.return_value = body
    return resp


class TestDocsSourceTools:
    @pytest.mark.asyncio
    async def test_list_docs_kbs_returns_accessible_kbs_with_verified_org(self) -> None:
        from main import list_docs_kbs

        verified = allow_verify_result(
            user_id="VERIFIED-USER", org_id="VERIFIED-ORG", org_slug="acme"
        )
        captured: dict[str, object] = {}

        with (
            patch("main._asserter.verify", new_callable=AsyncMock, return_value=verified),
            patch("main.httpx.AsyncClient") as mock_client_cls,
        ):
            list_resp = _response(
                200,
                [
                    {"slug": "docs", "name": "Docs", "visibility": "private", "kb_type": "org"},
                    {"slug": "handbook", "name": "Handbook", "visibility": "public"},
                ],
            )

            async def fake_get(url: str, headers: dict[str, str] | None = None) -> MagicMock:
                captured["url"] = url
                captured["headers"] = headers or {}
                return list_resp

            mock_client = AsyncMock()
            mock_client.get = AsyncMock(side_effect=fake_get)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client_cls.return_value = mock_client

            result = await list_docs_kbs(ctx=_make_ctx(_VALID_HEADERS))

        assert result == [
            {"slug": "docs", "name": "Docs", "visibility": "private", "kb_type": "org"},
            {"slug": "handbook", "name": "Handbook", "visibility": "public", "kb_type": None},
        ]
        assert captured["url"] == "http://docs-app:3000/api/orgs/acme/kbs"
        headers = captured["headers"]
        assert isinstance(headers, dict)
        assert headers["X-User-ID"] == "VERIFIED-USER"
        assert headers["X-Org-ID"] == "VERIFIED-ORG"

    @pytest.mark.asyncio
    async def test_list_docs_pages_returns_page_index(self) -> None:
        from main import list_docs_pages

        with (
            patch(
                "main._asserter.verify",
                new_callable=AsyncMock,
                return_value=allow_verify_result(org_slug="acme"),
            ),
            patch("main.httpx.AsyncClient") as mock_client_cls,
        ):
            list_resp = _response(200, [{"slug": "docs", "name": "Docs"}])
            page_index_resp = _response(
                200,
                [
                    {
                        "id": "11111111-2222-4333-8444-555555555555",
                        "slug": "handbook/process",
                        "title": "Process",
                        "icon": "doc",
                    }
                ],
            )

            mock_client = AsyncMock()
            mock_client.get = AsyncMock(side_effect=[list_resp, page_index_resp])
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client_cls.return_value = mock_client

            result = await list_docs_pages(ctx=_make_ctx(_VALID_HEADERS), kb_name="docs")

        assert result == [
            {
                "id": "11111111-2222-4333-8444-555555555555",
                "slug": "handbook/process",
                "title": "Process",
                "icon": "doc",
                "kb_name": "docs",
            }
        ]
        assert mock_client.get.await_args_list[1].args[0] == (
            "http://docs-app:3000/api/orgs/acme/kbs/docs/page-index"
        )

    @pytest.mark.asyncio
    async def test_get_docs_page_returns_exact_content_and_sha(self) -> None:
        from main import get_docs_page

        with (
            patch(
                "main._asserter.verify",
                new_callable=AsyncMock,
                return_value=allow_verify_result(org_slug="acme"),
            ),
            patch("main.httpx.AsyncClient") as mock_client_cls,
        ):
            list_resp = _response(200, [{"slug": "docs", "name": "Docs"}])
            page_resp = _response(
                200,
                {
                    "frontmatter": {"title": "Process", "tags": ["ops"]},
                    "content": "# Process\n\nExact body",
                    "sha": "pagesha",
                },
            )

            mock_client = AsyncMock()
            mock_client.get = AsyncMock(side_effect=[list_resp, page_resp])
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client_cls.return_value = mock_client

            result = await get_docs_page(
                page_path="handbook/process",
                ctx=_make_ctx(_VALID_HEADERS),
                kb_name="docs",
            )

        assert result["kb_name"] == "docs"
        assert result["page_path"] == "handbook/process"
        assert result["title"] == "Process"
        assert result["content"] == "# Process\n\nExact body"
        assert result["sha"] == "pagesha"

    @pytest.mark.asyncio
    async def test_create_docs_page_preflights_then_creates_with_idempotency_key(self) -> None:
        from main import create_docs_page

        captured: dict[str, object] = {}

        with (
            patch(
                "main._asserter.verify",
                new_callable=AsyncMock,
                return_value=allow_verify_result(
                    user_id="VERIFIED-USER", org_id="VERIFIED-ORG", org_slug="acme"
                ),
            ),
            patch("main.httpx.AsyncClient") as mock_client_cls,
        ):
            list_resp = _response(200, [{"slug": "docs", "name": "Docs"}])
            not_found_resp = _response(404, {"error": "Not found"})
            put_resp = _response(201, {"page": {"slug": "inbox/new-page"}})

            mock_client = AsyncMock()
            mock_client.get = AsyncMock(side_effect=[list_resp, not_found_resp])

            async def fake_put(
                url: str, json: dict | None = None, headers: dict[str, str] | None = None
            ) -> MagicMock:
                captured["url"] = url
                captured["json"] = json or {}
                captured["headers"] = headers or {}
                return put_resp

            mock_client.put = AsyncMock(side_effect=fake_put)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client_cls.return_value = mock_client

            result = await create_docs_page(
                title="New Page",
                content="body",
                ctx=_make_ctx(_VALID_HEADERS),
                kb_name="docs",
                page_path="inbox/new-page",
            )

        assert "Aangemaakt" in result
        assert captured["url"] == "http://docs-app:3000/api/orgs/acme/kbs/docs/pages/inbox/new-page"
        headers = captured["headers"]
        assert isinstance(headers, dict)
        assert headers["X-User-ID"] == "VERIFIED-USER"
        assert headers["X-Org-ID"] == "VERIFIED-ORG"
        assert "Idempotency-Key" in headers
        body = captured["json"]
        assert isinstance(body, dict)
        assert body["title"] == "New Page"
        assert body["content"] == "body"
        assert body["frontmatter"]["created_by"] == "VERIFIED-USER"

    @pytest.mark.asyncio
    async def test_create_docs_page_existing_page_does_not_overwrite(self) -> None:
        from main import create_docs_page

        with (
            patch(
                "main._asserter.verify", new_callable=AsyncMock, return_value=allow_verify_result()
            ),
            patch("main.httpx.AsyncClient") as mock_client_cls,
        ):
            list_resp = _response(200, [{"slug": "docs", "name": "Docs"}])
            existing_resp = _response(
                200,
                {"frontmatter": {"title": "Existing"}, "content": "old", "sha": "oldsha"},
            )

            mock_client = AsyncMock()
            mock_client.get = AsyncMock(side_effect=[list_resp, existing_resp])
            mock_client.put = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client_cls.return_value = mock_client

            result = await create_docs_page(
                title="Existing",
                content="new",
                ctx=_make_ctx(_VALID_HEADERS),
                kb_name="docs",
                page_path="existing",
            )

        assert "already exists" in result
        mock_client.put.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_create_docs_page_auto_path_uses_next_free_suffix(self) -> None:
        from main import create_docs_page

        captured: dict[str, object] = {}

        with (
            patch(
                "main._asserter.verify",
                new_callable=AsyncMock,
                return_value=allow_verify_result(
                    user_id="VERIFIED-USER", org_id="VERIFIED-ORG", org_slug="acme"
                ),
            ),
            patch("main.httpx.AsyncClient") as mock_client_cls,
        ):
            list_resp = _response(200, [{"slug": "docs", "name": "Docs"}])
            existing_resp = _response(
                200,
                {"frontmatter": {"title": "New Page"}, "content": "old", "sha": "oldsha"},
            )
            not_found_resp = _response(404, {"error": "Not found"})
            put_resp = _response(201, {"page": {"slug": "inbox/new-page-2"}})

            mock_client = AsyncMock()
            mock_client.get = AsyncMock(side_effect=[list_resp, existing_resp, not_found_resp])

            async def fake_put(
                url: str, json: dict | None = None, headers: dict[str, str] | None = None
            ) -> MagicMock:
                captured["url"] = url
                captured["json"] = json or {}
                captured["headers"] = headers or {}
                return put_resp

            mock_client.put = AsyncMock(side_effect=fake_put)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client_cls.return_value = mock_client

            result = await create_docs_page(
                title="New Page",
                content="body",
                ctx=_make_ctx(_VALID_HEADERS),
                kb_name="docs",
            )

        assert "Aangemaakt" in result
        assert "inbox/new-page-2" in result
        assert (
            captured["url"] == "http://docs-app:3000/api/orgs/acme/kbs/docs/pages/inbox/new-page-2"
        )


class TestUpdateDocsPage:
    @pytest.mark.asyncio
    async def test_updates_existing_page_with_verified_headers_and_sha(self) -> None:
        from main import update_docs_page

        verified = allow_verify_result(
            user_id="VERIFIED-USER", org_id="VERIFIED-ORG", org_slug="acme"
        )
        captured: dict[str, object] = {}

        with (
            patch("main._asserter.verify", new_callable=AsyncMock, return_value=verified),
            patch("main.httpx.AsyncClient") as mock_client_cls,
        ):
            list_resp = _response(200, [{"slug": "docs", "name": "Docs KB"}])
            get_page_resp = _response(
                200,
                {
                    "frontmatter": {"title": "Existing Title"},
                    "content": "old content",
                    "sha": "oldsha",
                },
            )
            put_resp = _response(200, {"ok": True, "sha": "newsha"})

            mock_client = AsyncMock()
            mock_client.get = AsyncMock(side_effect=[list_resp, get_page_resp])

            async def fake_put(
                url: str, json: dict | None = None, headers: dict[str, str] | None = None
            ) -> MagicMock:
                captured["put_url"] = url
                captured["put_json"] = json or {}
                captured["put_headers"] = headers or {}
                return put_resp

            mock_client.put = AsyncMock(side_effect=fake_put)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client_cls.return_value = mock_client

            result = await update_docs_page(
                page_path="handbook/process",
                content="new content",
                ctx=_make_ctx(
                    {
                        **_VALID_HEADERS,
                        "x-user-id": "spoofed-user",
                        "x-org-id": "spoofed-org",
                    }
                ),
                kb_name="docs",
                derived_from=["11111111-2222-4333-8444-555555555555"],
            )

        assert "Bijgewerkt" in result
        assert "newsha" in result
        assert captured["put_url"] == (
            "http://docs-app:3000/api/orgs/acme/kbs/docs/pages/handbook/process"
        )

        put_headers = captured["put_headers"]
        assert isinstance(put_headers, dict)
        assert put_headers["X-User-ID"] == "VERIFIED-USER"
        assert put_headers["X-Org-ID"] == "VERIFIED-ORG"
        assert "Idempotency-Key" not in put_headers

        put_json = captured["put_json"]
        assert isinstance(put_json, dict)
        assert put_json["title"] == "Existing Title"
        assert put_json["content"] == "new content"
        assert put_json["sha"] == "oldsha"
        assert put_json["frontmatter"]["derived_from"] == ["11111111-2222-4333-8444-555555555555"]

    @pytest.mark.asyncio
    async def test_missing_page_does_not_create(self) -> None:
        from main import update_docs_page

        with (
            patch(
                "main._asserter.verify", new_callable=AsyncMock, return_value=allow_verify_result()
            ),
            patch("main.httpx.AsyncClient") as mock_client_cls,
        ):
            list_resp = _response(200, [{"slug": "docs", "name": "Docs KB"}])
            missing_resp = _response(404, {"error": "Not found"})

            mock_client = AsyncMock()
            mock_client.get = AsyncMock(side_effect=[list_resp, missing_resp])
            mock_client.put = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client_cls.return_value = mock_client

            result = await update_docs_page(
                page_path="missing/page",
                content="new content",
                ctx=_make_ctx(_VALID_HEADERS),
                kb_name="docs",
            )

        assert "not found" in result.lower()
        mock_client.put.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_invalid_page_path_rejected_before_http(self) -> None:
        from main import update_docs_page

        with (
            patch(
                "main._asserter.verify", new_callable=AsyncMock, return_value=allow_verify_result()
            ),
            patch("main.httpx.AsyncClient") as mock_client_cls,
        ):
            result = await update_docs_page(
                page_path="docs/%2e%2e/secret",
                content="new content",
                ctx=_make_ctx(_VALID_HEADERS),
                kb_name="docs",
            )

        assert result == "Error: page_path contains invalid path components."
        mock_client_cls.assert_not_called()

    @pytest.mark.asyncio
    async def test_upstream_error_body_is_not_returned_to_user(self) -> None:
        from main import update_docs_page

        bad_upstream_body = (
            'Internal server error: {"Authorization": "Bearer docs-secret", '
            '"reason": "write-failed"}'
        )

        with (
            patch(
                "main._asserter.verify", new_callable=AsyncMock, return_value=allow_verify_result()
            ),
            patch("main.httpx.AsyncClient") as mock_client_cls,
        ):
            list_resp = _response(200, [{"slug": "docs", "name": "Docs KB"}])
            get_page_resp = _response(
                200,
                {
                    "frontmatter": {"title": "Existing Title"},
                    "content": "old content",
                    "sha": "oldsha",
                },
            )
            put_resp = _response(500, None, bad_upstream_body)

            mock_client = AsyncMock()
            mock_client.get = AsyncMock(side_effect=[list_resp, get_page_resp])
            mock_client.put = AsyncMock(return_value=put_resp)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client_cls.return_value = mock_client

            result = await update_docs_page(
                page_path="handbook/process",
                content="new content",
                ctx=_make_ctx(_VALID_HEADERS),
                kb_name="docs",
            )

        assert "docs-secret" not in result
        assert "Authorization" not in result
        assert "Bearer" not in result
        assert re.match(
            r"^Error updating docs: upstream returned HTTP \d+\. Request ID: [0-9a-f-]+\. .+$",
            result,
        ), result

    @pytest.mark.asyncio
    async def test_sha_conflict_fails_loudly(self) -> None:
        from main import update_docs_page

        with (
            patch(
                "main._asserter.verify", new_callable=AsyncMock, return_value=allow_verify_result()
            ),
            patch("main.httpx.AsyncClient") as mock_client_cls,
        ):
            list_resp = _response(200, [{"slug": "docs", "name": "Docs KB"}])
            get_page_resp = _response(
                200,
                {
                    "frontmatter": {"title": "Existing Title"},
                    "content": "old content",
                    "sha": "oldsha",
                },
            )
            conflict_resp = _response(409, {"detail": {"error": "SHA conflict", "sha": "fresh"}})

            mock_client = AsyncMock()
            mock_client.get = AsyncMock(side_effect=[list_resp, get_page_resp])
            mock_client.put = AsyncMock(return_value=conflict_resp)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client_cls.return_value = mock_client

            result = await update_docs_page(
                page_path="handbook/process",
                content="new content",
                ctx=_make_ctx(_VALID_HEADERS),
                kb_name="docs",
                expected_sha="stale",
            )

        assert result.startswith("Error:")
        assert "changed while updating" in result


# ---------------------------------------------------------------------------
# Edge cases: get/list/create/update error paths + tool registration
# ---------------------------------------------------------------------------


class TestDocsToolsEdgeCases:
    @pytest.mark.asyncio
    async def test_get_docs_page_404_raises_tool_error(self) -> None:
        from mcp.server.fastmcp.exceptions import ToolError

        from main import get_docs_page

        with (
            patch(
                "main._asserter.verify", new_callable=AsyncMock, return_value=allow_verify_result()
            ),
            patch("main.httpx.AsyncClient") as mock_client_cls,
        ):
            list_resp = _response(200, [{"slug": "docs", "name": "Docs"}])
            missing_resp = _response(404, {"error": "Not found"})

            mock_client = AsyncMock()
            mock_client.get = AsyncMock(side_effect=[list_resp, missing_resp])
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client_cls.return_value = mock_client

            with pytest.raises(ToolError) as excinfo:
                await get_docs_page(
                    page_path="missing/page",
                    ctx=_make_ctx(_VALID_HEADERS),
                    kb_name="docs",
                )

        assert "not found" in str(excinfo.value).lower()

    @pytest.mark.asyncio
    async def test_get_docs_page_invalid_kb_name_rejected_before_http(self) -> None:
        from mcp.server.fastmcp.exceptions import ToolError

        from main import get_docs_page

        with (
            patch(
                "main._asserter.verify", new_callable=AsyncMock, return_value=allow_verify_result()
            ),
            patch("main.httpx.AsyncClient") as mock_client_cls,
        ):
            with pytest.raises(ToolError) as excinfo:
                await get_docs_page(
                    page_path="docs/page",
                    ctx=_make_ctx(_VALID_HEADERS),
                    kb_name="bad/slug",
                )

        assert "invalid characters" in str(excinfo.value).lower()
        mock_client_cls.assert_not_called()

    @pytest.mark.asyncio
    async def test_list_docs_pages_multi_kb_ambiguity_raises(self) -> None:
        from mcp.server.fastmcp.exceptions import ToolError

        from main import list_docs_pages

        with (
            patch(
                "main._asserter.verify", new_callable=AsyncMock, return_value=allow_verify_result()
            ),
            patch("main.httpx.AsyncClient") as mock_client_cls,
        ):
            list_resp = _response(
                200,
                [
                    {"slug": "docs", "name": "Docs"},
                    {"slug": "handbook", "name": "Handbook"},
                ],
            )

            mock_client = AsyncMock()
            mock_client.get = AsyncMock(side_effect=[list_resp])
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client_cls.return_value = mock_client

            with pytest.raises(ToolError) as excinfo:
                await list_docs_pages(ctx=_make_ctx(_VALID_HEADERS))

        msg = str(excinfo.value)
        assert "Meerdere kennisbanken" in msg
        assert "docs" in msg and "handbook" in msg

    @pytest.mark.asyncio
    async def test_create_docs_page_invalid_derived_from_rejected_before_http(self) -> None:
        from main import create_docs_page

        with (
            patch(
                "main._asserter.verify", new_callable=AsyncMock, return_value=allow_verify_result()
            ),
            patch("main.httpx.AsyncClient") as mock_client_cls,
        ):
            result = await create_docs_page(
                title="Doc",
                content="body",
                ctx=_make_ctx(_VALID_HEADERS),
                kb_name="docs",
                page_path="inbox/doc",
                derived_from=["not-a-uuid"],
            )

        assert result == "Error: derived_from must contain only UUID strings"
        mock_client_cls.assert_not_called()

    @pytest.mark.asyncio
    async def test_create_docs_page_upstream_500_body_is_not_returned_to_user(self) -> None:
        from main import create_docs_page

        bad_upstream_body = (
            'Internal server error: {"Authorization": "Bearer docs-secret", '
            '"reason": "write-failed"}'
        )

        with (
            patch(
                "main._asserter.verify", new_callable=AsyncMock, return_value=allow_verify_result()
            ),
            patch("main.httpx.AsyncClient") as mock_client_cls,
        ):
            list_resp = _response(200, [{"slug": "docs", "name": "Docs"}])
            not_found_resp = _response(404, {"error": "Not found"})
            put_resp = _response(500, None, bad_upstream_body)

            mock_client = AsyncMock()
            mock_client.get = AsyncMock(side_effect=[list_resp, not_found_resp])
            mock_client.put = AsyncMock(return_value=put_resp)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client_cls.return_value = mock_client

            result = await create_docs_page(
                title="Doc",
                content="body",
                ctx=_make_ctx(_VALID_HEADERS),
                kb_name="docs",
                page_path="inbox/doc",
            )

        assert "docs-secret" not in result
        assert "Authorization" not in result
        assert "Bearer" not in result
        assert re.match(
            r"^Error creating docs page: upstream returned HTTP \d+\. Request ID: [0-9a-f-]+\. .+$",
            result,
        ), result

    @pytest.mark.asyncio
    async def test_update_docs_page_empty_expected_sha_rejected_before_http(self) -> None:
        from main import update_docs_page

        with (
            patch(
                "main._asserter.verify", new_callable=AsyncMock, return_value=allow_verify_result()
            ),
            patch("main.httpx.AsyncClient") as mock_client_cls,
        ):
            result = await update_docs_page(
                page_path="docs/page",
                content="new content",
                ctx=_make_ctx(_VALID_HEADERS),
                kb_name="docs",
                expected_sha="   ",
            )

        assert result == "Error: expected_sha must be a non-empty string when provided."
        mock_client_cls.assert_not_called()


class TestDocsToolRegistration:
    @pytest.mark.asyncio
    async def test_all_docs_tools_are_registered(self) -> None:
        import main

        tools = await main.mcp.list_tools()
        names = {t.name for t in tools}
        for expected in (
            "list_docs_kbs",
            "list_docs_pages",
            "get_docs_page",
            "create_docs_page",
            "update_docs_page",
        ):
            assert expected in names, f"{expected} must be registered as an MCP tool"
        assert "save_to_docs" not in names


# ---------------------------------------------------------------------------
# Spec B/C adversarial: suffix exhaustion + expected_sha override
# ---------------------------------------------------------------------------


class TestDocsSpecAdversarial:
    @pytest.mark.asyncio
    async def test_create_docs_page_exhausts_suffixes_fails_clean_without_put(self) -> None:
        """Geen page_path + 25 bestaande slugs: failt netjes, geen PUT."""
        from main import create_docs_page

        with (
            patch(
                "main._asserter.verify",
                new_callable=AsyncMock,
                return_value=allow_verify_result(org_slug="acme"),
            ),
            patch("main.httpx.AsyncClient") as mock_client_cls,
        ):
            list_resp = _response(200, [{"slug": "docs", "name": "Docs"}])
            existing_resp = _response(
                200, {"frontmatter": {"title": "Taken"}, "content": "x", "sha": "s"}
            )
            # 1 KB-list GET + 25 candidate GETs, every candidate already exists.
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(side_effect=[list_resp] + [existing_resp] * 25)
            mock_client.put = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client_cls.return_value = mock_client

            result = await create_docs_page(
                title="Taken",
                content="body",
                ctx=_make_ctx(_VALID_HEADERS),
                kb_name="docs",
            )

        assert result.startswith("Error:")
        assert "could not find an unused docs page path" in result
        mock_client.put.assert_not_awaited()
        # Exactly the 25 candidate probes were made (plus the KB-list fetch).
        assert mock_client.get.await_count == 26

    @pytest.mark.asyncio
    async def test_update_docs_page_expected_sha_overrides_preflight_sha(self) -> None:
        """expected_sha override: PUT must use the caller SHA, not the preflight SHA."""
        from main import update_docs_page

        captured: dict[str, object] = {}

        with (
            patch(
                "main._asserter.verify",
                new_callable=AsyncMock,
                return_value=allow_verify_result(org_slug="acme"),
            ),
            patch("main.httpx.AsyncClient") as mock_client_cls,
        ):
            list_resp = _response(200, [{"slug": "docs", "name": "Docs"}])
            get_page_resp = _response(
                200,
                {"frontmatter": {"title": "T"}, "content": "old", "sha": "preflight-sha"},
            )
            put_resp = _response(200, {"ok": True, "sha": "newsha"})

            mock_client = AsyncMock()
            mock_client.get = AsyncMock(side_effect=[list_resp, get_page_resp])

            async def fake_put(
                url: str, json: dict | None = None, headers: dict[str, str] | None = None
            ) -> MagicMock:
                captured["json"] = json or {}
                return put_resp

            mock_client.put = AsyncMock(side_effect=fake_put)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client_cls.return_value = mock_client

            result = await update_docs_page(
                page_path="handbook/process",
                content="new content",
                ctx=_make_ctx(_VALID_HEADERS),
                kb_name="docs",
                expected_sha="caller-sha",
            )

        assert "Bijgewerkt" in result
        put_json = captured["json"]
        assert isinstance(put_json, dict)
        assert put_json["sha"] == "caller-sha"
