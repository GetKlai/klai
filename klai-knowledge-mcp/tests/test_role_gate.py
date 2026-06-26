"""SPEC-PORTAL-RBAC-REFACTOR-001 Phase 4 — role gate tests.

Covers:
  - _role_at_least unit tests (4C)
  - save_org_knowledge gate: "personal" denied, "company" allowed (4E)
  - create_docs_page gate: "personal" denied, "company" allowed (4E)

Strategy: patch ``main._identify_request`` with an ``AsyncMock`` that
returns a fake identity with the desired ``effective_role``.  This avoids
spinning up portal-api, Redis, or any external service while still exercising
the full gate code path inside each tool.
"""

from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Minimal fake identity that matches _VerifiedIdentity's public interface.
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _FakeIdentity:
    user_id: str = "user1"
    org_id: str = "1"
    org_slug: str = "testorg"
    client_id: str | None = None
    effective_role: str = "unknown"


def _make_ctx(headers: dict | None = None) -> MagicMock:
    ctx = MagicMock()
    ctx.request_context.request.headers = headers or {}
    return ctx


# ---------------------------------------------------------------------------
# Unit tests: _role_at_least
# ---------------------------------------------------------------------------


class TestRoleAtLeast:
    def test_unknown_is_below_personal(self) -> None:
        from main import _role_at_least

        assert _role_at_least("unknown", "personal") is False

    def test_personal_meets_personal(self) -> None:
        from main import _role_at_least

        assert _role_at_least("personal", "personal") is True

    def test_personal_below_company(self) -> None:
        from main import _role_at_least

        assert _role_at_least("personal", "company") is False

    def test_company_meets_company(self) -> None:
        from main import _role_at_least

        assert _role_at_least("company", "company") is True

    def test_admin_meets_company(self) -> None:
        from main import _role_at_least

        assert _role_at_least("admin", "company") is True

    def test_kb_manager_meets_company(self) -> None:
        from main import _role_at_least

        assert _role_at_least("kb_manager", "company") is True

    def test_unknown_minimum_always_denied(self) -> None:
        # "unknown" minimum maps to 0 via .get(minimum, 0) — but actual
        # "unknown" maps to -1, so it is still denied.
        from main import _role_at_least

        assert _role_at_least("unknown", "unknown") is False

    def test_unrecognised_role_is_denied(self) -> None:
        from main import _role_at_least

        assert _role_at_least("superuser", "personal") is False


# ---------------------------------------------------------------------------
# Integration tests: save_org_knowledge role gate
# ---------------------------------------------------------------------------


class TestSaveOrgKnowledgeRoleGate:
    """Role gate in save_org_knowledge must deny sub-company roles (4E)."""

    @pytest.mark.asyncio
    async def test_personal_role_is_denied(self) -> None:
        from main import save_org_knowledge

        ctx = _make_ctx()
        identity = _FakeIdentity(effective_role="personal")
        with patch("main._identify_request", new_callable=AsyncMock, return_value=identity):
            result = await save_org_knowledge(
                title="Test",
                content="body",
                assertion_mode="factual",
                tags=[],
                ctx=ctx,
            )
        assert result.startswith("Error:")
        assert "rol" in result.lower()  # Dutch error message

    @pytest.mark.asyncio
    async def test_unknown_role_is_denied(self) -> None:
        from main import save_org_knowledge

        ctx = _make_ctx()
        identity = _FakeIdentity(effective_role="unknown")
        with patch("main._identify_request", new_callable=AsyncMock, return_value=identity):
            result = await save_org_knowledge(
                title="Test",
                content="body",
                assertion_mode="factual",
                tags=[],
                ctx=ctx,
            )
        assert result.startswith("Error:")

    @pytest.mark.asyncio
    async def test_company_role_passes_gate(self) -> None:
        """company role must pass the gate; downstream _save_to_ingest is mocked."""
        from main import save_org_knowledge

        ctx = _make_ctx()
        identity = _FakeIdentity(effective_role="company")
        with (
            patch("main._identify_request", new_callable=AsyncMock, return_value=identity),
            patch("main._save_to_ingest", new_callable=AsyncMock, return_value=True),
        ):
            result = await save_org_knowledge(
                title="Test",
                content="body",
                assertion_mode="factual",
                tags=[],
                ctx=ctx,
            )
        # Success path returns a confirmation string, not an error.
        assert not result.startswith("Error:")

    @pytest.mark.asyncio
    async def test_admin_role_passes_gate(self) -> None:
        from main import save_org_knowledge

        ctx = _make_ctx()
        identity = _FakeIdentity(effective_role="admin")
        with (
            patch("main._identify_request", new_callable=AsyncMock, return_value=identity),
            patch("main._save_to_ingest", new_callable=AsyncMock, return_value=True),
        ):
            result = await save_org_knowledge(
                title="Admin save",
                content="body",
                assertion_mode="factual",
                tags=[],
                ctx=ctx,
            )
        assert not result.startswith("Error:")


# ---------------------------------------------------------------------------
# Integration tests: update_docs_page role gate
# ---------------------------------------------------------------------------


class TestUpdateDocsPageRoleGate:
    """Role gate in update_docs_page must deny sub-company roles."""

    @pytest.mark.asyncio
    async def test_personal_role_is_denied(self) -> None:
        from main import update_docs_page

        ctx = _make_ctx()
        identity = _FakeIdentity(effective_role="personal")
        with patch("main._identify_request", new_callable=AsyncMock, return_value=identity):
            result = await update_docs_page(
                page_path="docs/page",
                content="doc body",
                ctx=ctx,
                kb_name="docs",
            )
        assert result.startswith("Error:")
        assert "rol" in result.lower()

    @pytest.mark.asyncio
    async def test_unknown_role_is_denied(self) -> None:
        from main import update_docs_page

        ctx = _make_ctx()
        identity = _FakeIdentity(effective_role="unknown")
        with patch("main._identify_request", new_callable=AsyncMock, return_value=identity):
            result = await update_docs_page(
                page_path="docs/page",
                content="doc body",
                ctx=ctx,
                kb_name="docs",
            )
        assert result.startswith("Error:")

    @pytest.mark.asyncio
    async def test_company_role_passes_gate(self) -> None:
        import httpx

        from main import update_docs_page

        ctx = _make_ctx()
        identity = _FakeIdentity(effective_role="company")

        with (
            patch("main._identify_request", new_callable=AsyncMock, return_value=identity),
            patch(
                "httpx.AsyncClient",
                side_effect=httpx.RequestError("no server"),
            ),
        ):
            result = await update_docs_page(
                page_path="docs/page",
                content="doc body",
                ctx=ctx,
                kb_name="docs",
            )

        assert "rol" not in result.lower()


class TestCreateDocsPageRoleGate:
    """Role gate in create_docs_page must deny sub-company roles."""

    @pytest.mark.asyncio
    async def test_personal_role_is_denied(self) -> None:
        from main import create_docs_page

        ctx = _make_ctx()
        identity = _FakeIdentity(effective_role="personal")
        with patch("main._identify_request", new_callable=AsyncMock, return_value=identity):
            result = await create_docs_page(
                title="Doc title",
                content="doc body",
                ctx=ctx,
                kb_name="docs",
                page_path="docs/page",
            )
        assert result.startswith("Error:")
        assert "rol" in result.lower()

    @pytest.mark.asyncio
    async def test_company_role_passes_gate(self) -> None:
        import httpx

        from main import create_docs_page

        ctx = _make_ctx()
        identity = _FakeIdentity(effective_role="company")

        with (
            patch("main._identify_request", new_callable=AsyncMock, return_value=identity),
            patch(
                "httpx.AsyncClient",
                side_effect=httpx.RequestError("no server"),
            ),
        ):
            result = await create_docs_page(
                title="Doc title",
                content="doc body",
                ctx=ctx,
                kb_name="docs",
                page_path="docs/page",
            )

        assert "rol" not in result.lower()
