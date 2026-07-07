"""Tests for SPEC-TAXONOMY-001 (revised): assertion_mode taxonomy in knowledge-mcp.

The 6-value vocabulary uses the original DB-flavoured names + ``unknown``,
matching the live ``artifacts_assertion_mode_check`` constraint and the
``VALID_ASSERTION_MODES`` set in ``klai-knowledge-ingest``. See the
Realignment Note in ``.moai/specs/SPEC-TAXONOMY-001/spec.md`` for why
DD-1's ``fact/claim/speculation`` rename was reverted.

Identity verification (SPEC-SEC-IDENTITY-ASSERT-001 REQ-2) sits in front of
every save_* tool. These tests mock ``main._asserter.verify`` with an
allow-result so the assertion_mode validation path is actually reached.
Without the mock the tests pass through ``_ERR_IDENTITY_REJECTED`` and
silently never exercise the taxonomy logic.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests._helpers import allow_verify_result


def _make_ctx(headers: dict | None = None):
    ctx = MagicMock()
    ctx.request_context.request.headers = headers or {}
    return ctx


@pytest.fixture(autouse=True)
def _patch_env(monkeypatch):
    monkeypatch.setenv("KLAI_DOCS_API_BASE", "http://docs-app:3000")
    monkeypatch.setenv("DOCS_INTERNAL_SECRET", "docs-secret")
    monkeypatch.setenv("KNOWLEDGE_INGEST_URL", "http://knowledge-ingest:8000")
    monkeypatch.setenv("KNOWLEDGE_INGEST_SECRET", "test-secret")
    monkeypatch.setenv("PORTAL_API_URL", "http://portal-api:8010")
    monkeypatch.setenv("PORTAL_INTERNAL_SECRET", "portal-test-secret")


def _valid_ctx():
    return _make_ctx(
        {
            "x-user-id": "user1",
            "x-org-id": "org1",
            "x-org-slug": "testorg",
            "x-internal-secret": "test-secret",
        }
    )


class TestAssertionModeType:
    """The AssertionMode Literal and VALID_ASSERTION_MODES frozenset must exist."""

    def test_valid_assertion_modes_has_six_values(self, _patch_env):
        from main import VALID_ASSERTION_MODES

        assert VALID_ASSERTION_MODES == frozenset(
            {"factual", "belief", "hypothesis", "procedural", "quoted", "unknown"}
        )

    def test_assertion_mode_literal_exists(self, _patch_env):
        from typing import get_args

        from main import AssertionMode

        args = set(get_args(AssertionMode))
        assert args == {
            "factual",
            "belief",
            "hypothesis",
            "procedural",
            "quoted",
            "unknown",
        }


class TestAssertionModeValidation:
    """Invalid assertion_mode must return an error, not silently fallback."""

    @pytest.mark.asyncio
    async def test_invalid_assertion_mode_returns_error(self, _patch_env):
        from main import save_personal_knowledge

        ctx = _valid_ctx()
        with patch(
            "main._asserter.verify", new_callable=AsyncMock, return_value=allow_verify_result()
        ):
            result = await save_personal_knowledge(
                title="Test",
                content="content",
                assertion_mode="invalid_mode",
                tags=["test"],
                ctx=ctx,
            )
        # Must return an error string, not silently fallback
        assert "Error" in result or "invalid" in result.lower()

    @pytest.mark.asyncio
    async def test_old_note_value_returns_error(self, _patch_env):
        """The old 'note' value is no longer valid and should error."""
        from main import save_personal_knowledge

        ctx = _valid_ctx()
        with patch(
            "main._asserter.verify", new_callable=AsyncMock, return_value=allow_verify_result()
        ):
            result = await save_personal_knowledge(
                title="Test",
                content="content",
                assertion_mode="note",
                tags=["test"],
                ctx=ctx,
            )
        assert "Error" in result or "invalid" in result.lower()


class TestAssertionModeValidValues:
    """All 6 valid assertion_mode values must be accepted."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "mode",
        ["factual", "belief", "hypothesis", "procedural", "quoted", "unknown"],
    )
    async def test_valid_mode_accepted(self, _patch_env, mode):
        from main import save_personal_knowledge

        ctx = _valid_ctx()
        with (
            patch(
                "main._asserter.verify", new_callable=AsyncMock, return_value=allow_verify_result()
            ),
            patch("main._save_to_ingest", new_callable=AsyncMock, return_value=True),
        ):
            result = await save_personal_knowledge(
                title="Test",
                content="content",
                assertion_mode=mode,
                tags=["test"],
                ctx=ctx,
            )
        assert "Error" not in result
        # Positive assertion: the success path must actually be reached.
        assert "Opgeslagen" in result


class TestMissingAssertionModeDefaultsToUnknown:
    """When assertion_mode is None/missing, default to 'unknown'."""

    @pytest.mark.asyncio
    async def test_none_assertion_mode_defaults_to_unknown(self, _patch_env):
        from main import save_personal_knowledge

        ctx = _valid_ctx()
        captured_mode = {}

        async def _capture_ingest(
            org_id,
            kb_slug,
            title,
            content,
            assertion_mode,
            tags,
            source_note,
            user_id=None,
        ):
            captured_mode["value"] = assertion_mode
            return True

        with (
            patch(
                "main._asserter.verify", new_callable=AsyncMock, return_value=allow_verify_result()
            ),
            patch("main._save_to_ingest", side_effect=_capture_ingest),
        ):
            # Pass empty string to simulate missing/empty assertion_mode
            await save_personal_knowledge(
                title="Test",
                content="content",
                assertion_mode="",
                tags=["test"],
                ctx=ctx,
            )

        assert captured_mode.get("value") == "unknown"


class TestIngestPayload:
    @pytest.mark.asyncio
    async def test_save_to_ingest_sends_frontmatter_without_capability_hint(self, _patch_env):
        from main import _save_to_ingest

        captured = {}

        class _Response:
            status_code = 201

        class _Client:
            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return None

            async def post(self, url, json, headers):
                captured["url"] = url
                captured["json"] = json
                captured["headers"] = headers
                return _Response()

        with patch("main.httpx.AsyncClient", return_value=_Client()):
            ok = await _save_to_ingest(
                org_id="org1",
                kb_slug="personal-user1",
                title="Test",
                content="Remember this",
                assertion_mode="belief",
                tags=["product", "roadmap"],
                source_note="user said so",
                user_id="user1",
            )

        assert ok is True
        payload = captured["json"]
        assert payload["source_type"] == "mcp"
        assert payload["content_type"] == "kb_article"
        assert "allowed_assertion_modes" not in payload
        assert payload["user_id"] == "user1"
        assert payload["content"].startswith("---\nassertion_mode: belief\n")
        assert 'tags: ["product", "roadmap"]' in payload["content"]
        assert 'source_note: "user said so"' in payload["content"]

    def test_content_frontmatter_is_not_nested(self, _patch_env):
        from main import _content_with_knowledge_frontmatter

        content = """---
title: Existing
tags: [old]
---
# Heading
Body"""

        result = _content_with_knowledge_frontmatter(
            content=content,
            assertion_mode="factual",
            tags=["new"],
            source_note=None,
        )

        assert result.count("\n---") == 1
        assert result.startswith("---\nassertion_mode: factual\n")
        assert "title: Existing" not in result
        assert "# Heading\nBody" in result
