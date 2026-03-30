"""Tests for SPEC-TAXONOMY-001: assertion_mode taxonomy alignment in knowledge-mcp.

RED phase: these tests define the expected behavior for the new 6-value taxonomy.
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


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


def _valid_ctx():
    return _make_ctx({
        "x-user-id": "user1",
        "x-org-id": "org1",
        "x-org-slug": "testorg",
        "x-internal-secret": "test-secret",
    })


class TestAssertionModeType:
    """The AssertionMode Literal and VALID_ASSERTION_MODES frozenset must exist."""

    def test_valid_assertion_modes_has_six_values(self, _patch_env):
        from main import VALID_ASSERTION_MODES

        assert VALID_ASSERTION_MODES == frozenset(
            {"fact", "claim", "speculation", "procedural", "quoted", "unknown"}
        )

    def test_assertion_mode_literal_exists(self, _patch_env):
        from main import AssertionMode
        from typing import get_args

        args = set(get_args(AssertionMode))
        assert args == {"fact", "claim", "speculation", "procedural", "quoted", "unknown"}


class TestAssertionModeValidation:
    """Invalid assertion_mode must return an error, not silently fallback."""

    @pytest.mark.asyncio
    async def test_invalid_assertion_mode_returns_error(self, _patch_env):
        from main import save_personal_knowledge

        ctx = _valid_ctx()
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
    @pytest.mark.parametrize("mode", ["fact", "claim", "speculation", "procedural", "quoted", "unknown"])
    async def test_valid_mode_accepted(self, _patch_env, mode):
        from main import save_personal_knowledge

        ctx = _valid_ctx()
        with patch("main._save_to_ingest", new_callable=AsyncMock, return_value=True):
            result = await save_personal_knowledge(
                title="Test",
                content="content",
                assertion_mode=mode,
                tags=["test"],
                ctx=ctx,
            )
        assert "Error" not in result


class TestMissingAssertionModeDefaultsToUnknown:
    """When assertion_mode is None/missing, default to 'unknown'."""

    @pytest.mark.asyncio
    async def test_none_assertion_mode_defaults_to_unknown(self, _patch_env):
        from main import save_personal_knowledge

        ctx = _valid_ctx()
        captured_mode = {}

        async def _capture_ingest(org_id, kb_slug, title, content, assertion_mode, tags, source_note, user_id=None):
            captured_mode["value"] = assertion_mode
            return True

        with patch("main._save_to_ingest", side_effect=_capture_ingest):
            # Pass empty string to simulate missing/empty assertion_mode
            await save_personal_knowledge(
                title="Test",
                content="content",
                assertion_mode="",
                tags=["test"],
                ctx=ctx,
            )

        assert captured_mode.get("value") == "unknown"
