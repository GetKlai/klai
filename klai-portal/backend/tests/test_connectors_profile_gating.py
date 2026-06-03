"""
Integration tests for SPEC-PORTAL-PROFILES-001 Phase 1.5b G2: connector type x role x plan gating matrix.
"""

import sys
from types import ModuleType
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from tests.conftest import make_perms

_stub_mod = ModuleType("connector_credentials")
_stub_mod.SENSITIVE_FIELDS = {}  # type: ignore[attr-defined]
_stub_mod.ConnectorCredentialStore = MagicMock  # type: ignore[attr-defined]
_stub_cipher = ModuleType("connector_credentials.cipher")
_stub_cipher.AESGCMCipher = MagicMock  # type: ignore[attr-defined]
sys.modules.setdefault("connector_credentials", _stub_mod)
sys.modules.setdefault("connector_credentials.cipher", _stub_cipher)


def _make_org(plan: str = "chat") -> MagicMock:
    org = MagicMock()
    org.plan = plan
    org.id = 1
    org.slug = "test-org"
    org.zitadel_org_id = "zitadel-org-1"
    return org


def _make_user(role: str = "personal") -> MagicMock:
    user = MagicMock()
    user.role = role
    user.zitadel_user_id = f"user-{role}"
    return user


def _make_kb(owner_type: str = "user", slug: str = "my-kb") -> MagicMock:
    kb = MagicMock()
    kb.id = 1
    kb.slug = slug
    kb.owner_type = owner_type
    return kb


def _make_out(ctype: str = "notion") -> MagicMock:
    out = MagicMock()
    out.connector_type = ctype
    return out


def _make_body(ctype: str = "url") -> MagicMock:
    """Mock ConnectorCreateRequest to bypass ConnectorType Literal.

    url/upload are basic types allowed for all roles but absent from the ConnectorType Literal.
    """
    body = MagicMock()
    body.connector_type = ctype
    body.allowed_assertion_modes = None
    body.config = {}
    body.content_type = None
    body.schedule = None
    body.name = "test-connector"
    return body


class TestCreateConnectorProfilePlanMatrix:
    """create_connector enforces role x plan connector-type gating (G1 + G2)."""

    @pytest.mark.asyncio
    async def test_kb_manager_complete_plan_notion_allowed(self) -> None:
        """kb_manager on complete plan may create external (notion) connectors."""
        from app.api.connectors import ConnectorCreateRequest, create_connector

        kb = _make_kb()
        out = _make_out("notion")

        db = AsyncMock()
        db.add = MagicMock()
        body = ConnectorCreateRequest(
            connector_type="notion",
            config={},
            name="test-connector",
        )

        with (
            patch("app.api.connectors._get_kb_with_owner_check", new_callable=AsyncMock, return_value=kb),
            patch(
                "app.api.connectors.get_effective_capabilities",
                new_callable=AsyncMock,
                return_value={"kb.connectors", "kb.connectors.external"},
            ),
            patch("app.api.connectors._validate_connector_config", return_value={}),
            patch("app.api.connectors._auto_fill_canary_fingerprint", return_value=None),
            patch("app.api.connectors.credential_store", None),
            patch("app.api.connectors._connector_out", return_value=out),
            patch("app.api.connectors.emit_event"),
        ):
            result = await create_connector(
                kb_slug="my-kb",
                body=body,
                perms=make_perms(role="kb_manager", plan="knowledge", org_id=1),
                db=db,
            )

        assert result is out

    @pytest.mark.asyncio
    async def test_kb_manager_core_plan_notion_blocked(self) -> None:
        """kb_manager on core plan may NOT create external (notion) connectors -> 403."""
        from app.api.connectors import ConnectorCreateRequest, create_connector

        kb = _make_kb()

        db = AsyncMock()
        body = ConnectorCreateRequest(
            connector_type="notion",
            config={},
            name="test-connector",
        )

        caps_mock = AsyncMock(return_value={"kb.connectors"})

        with (
            patch("app.api.connectors._get_kb_with_owner_check", new_callable=AsyncMock, return_value=kb),
            patch("app.api.connectors.get_effective_capabilities", caps_mock),
        ):
            with pytest.raises(HTTPException) as exc_info:
                await create_connector(
                    kb_slug="my-kb",
                    body=body,
                    perms=make_perms(role="kb_manager", plan="chat", org_id=1),
                    db=db,
                )

        assert exc_info.value.status_code == 403
        assert exc_info.value.detail["error_code"] == "external_connectors_require_complete_plan"
        caps_mock.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_personal_complete_plan_notion_blocked_by_role(self) -> None:
        """personal role may NOT create external connectors on complete plan -> 403 (role gate)."""
        from app.api.connectors import ConnectorCreateRequest, create_connector

        kb = _make_kb()

        db = AsyncMock()
        body = ConnectorCreateRequest(
            connector_type="notion",
            config={},
            name="test-connector",
        )

        caps_mock = AsyncMock(return_value={"kb.connectors", "kb.connectors.external"})

        with (
            patch("app.api.connectors._get_kb_with_owner_check", new_callable=AsyncMock, return_value=kb),
            patch("app.api.connectors.get_effective_capabilities", caps_mock),
        ):
            with pytest.raises(HTTPException) as exc_info:
                await create_connector(
                    kb_slug="my-kb",
                    body=body,
                    perms=make_perms(role="personal", plan="knowledge", org_id=1),
                    db=db,
                )

        assert exc_info.value.status_code == 403
        assert exc_info.value.detail == "connector_not_allowed_for_profile"
        # Role gate fires before plan gate: capabilities must not be queried
        caps_mock.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_personal_complete_plan_url_allowed(self) -> None:
        """personal role on complete plan may create url (basic) connectors -> 200."""
        from app.api.connectors import create_connector

        kb = _make_kb()
        out = _make_out("url")

        db = AsyncMock()
        db.add = MagicMock()
        body = _make_body("url")

        caps_mock = AsyncMock(return_value={"kb.connectors", "kb.connectors.external"})

        with (
            patch("app.api.connectors._get_kb_with_owner_check", new_callable=AsyncMock, return_value=kb),
            patch("app.api.connectors.get_effective_capabilities", caps_mock),
            patch("app.api.connectors._validate_connector_config", return_value={}),
            patch("app.api.connectors._auto_fill_canary_fingerprint", return_value=None),
            patch("app.api.connectors.credential_store", None),
            patch("app.api.connectors._connector_out", return_value=out),
            patch("app.api.connectors.emit_event"),
        ):
            result = await create_connector(
                kb_slug="my-kb",
                body=body,
                perms=make_perms(role="personal", plan="knowledge", org_id=1),
                db=db,
            )

        assert result is out
        # Basic type: plan ceiling check must not be triggered
        caps_mock.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_kb_manager_core_plan_url_allowed(self) -> None:
        """kb_manager on core plan may create url (basic) connectors -> 200."""
        from app.api.connectors import create_connector

        kb = _make_kb()
        out = _make_out("url")

        db = AsyncMock()
        db.add = MagicMock()
        body = _make_body("url")

        caps_mock = AsyncMock(return_value={"kb.connectors"})

        with (
            patch("app.api.connectors._get_kb_with_owner_check", new_callable=AsyncMock, return_value=kb),
            patch("app.api.connectors.get_effective_capabilities", caps_mock),
            patch("app.api.connectors._validate_connector_config", return_value={}),
            patch("app.api.connectors._auto_fill_canary_fingerprint", return_value=None),
            patch("app.api.connectors.credential_store", None),
            patch("app.api.connectors._connector_out", return_value=out),
            patch("app.api.connectors.emit_event"),
        ):
            result = await create_connector(
                kb_slug="my-kb",
                body=body,
                perms=make_perms(role="kb_manager", plan="chat", org_id=1),
                db=db,
            )

        assert result is out
        # Basic type: plan ceiling check must not be triggered
        caps_mock.assert_not_awaited()
