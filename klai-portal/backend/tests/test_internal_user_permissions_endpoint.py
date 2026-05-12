"""SPEC-PORTAL-RBAC-REFACTOR-001 REQ-19 — internal endpoint that returns
serialised ``UserPermissions`` for the given Zitadel user.

Pinned behaviour:
- Invalid bearer → HTTP 401 (delegated to ``_require_internal_token``).
- Unknown user → HTTP 404 with ``error_code=user_not_found``.
- Happy path → HTTP 200 with all 12 fields of the dataclass plus
  ``provisioning_status`` (the 13th-field carry-over for the
  deprovisioning gate).
- Frozensets serialise as sorted lists.
- ``ProfileRole`` enums serialise as their ``.value`` string.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException, status

from app.core.permissions import UserPermissions
from app.core.profiles import Capability, ProfileRole


def _fake_perms(
    *,
    role: ProfileRole = ProfileRole.ADMIN,
    plan: str = "knowledge",
    addons: tuple[str, ...] = (),
    platform_unlocks: tuple[str, ...] = (),
    is_platform_admin: bool = False,
    provisioning_status: str = "active",
) -> UserPermissions:
    # SPEC-PORTAL-EXTENSIONS-UNIFY-001: enabled_addons folded into
    # platform_unlocked_features. The `addons=` kwarg is kept for test
    # readability but merges into the unified set.
    return UserPermissions(
        user_id="zit-user-1",
        org_id=42,
        org_slug="voys",
        role=role,
        plan=plan,
        platform_unlocked_features=frozenset(platform_unlocks) | frozenset(addons),
        effective_role=role,
        effective_capabilities=frozenset({Capability.KB_CONNECTORS}),
        effective_products=frozenset({"chat"}),
        effective_kb_limits=MagicMock(),  # not asserted by serialiser
        is_platform_admin=is_platform_admin,
        provisioning_status=provisioning_status,
    )


class TestGetUserPermissionsEndpoint:
    @pytest.mark.asyncio
    async def test_invalid_bearer_returns_401_before_db(self, monkeypatch):
        """``_require_internal_token`` raises 401 → handler MUST NOT touch DB."""
        from app.api import internal

        monkeypatch.setattr(
            internal,
            "_require_internal_token",
            AsyncMock(side_effect=HTTPException(status_code=401, detail="Unauthorized")),
        )

        db = MagicMock()
        db.execute = AsyncMock()

        with pytest.raises(HTTPException) as exc:
            await internal.get_user_permissions(
                zitadel_user_id="zit-user-1",
                request=MagicMock(),
                db=db,
            )
        assert exc.value.status_code == status.HTTP_401_UNAUTHORIZED
        db.execute.assert_not_called()

    @pytest.mark.asyncio
    async def test_unknown_user_returns_404(self, monkeypatch):
        from app.api import internal

        monkeypatch.setattr(internal, "_require_internal_token", AsyncMock())
        monkeypatch.setattr(internal, "_audit_internal_call", AsyncMock())
        monkeypatch.setattr(internal, "resolve_user_permissions", AsyncMock(return_value=None))

        with pytest.raises(HTTPException) as exc:
            await internal.get_user_permissions(
                zitadel_user_id="ghost",
                request=MagicMock(),
                db=AsyncMock(),
            )
        assert exc.value.status_code == status.HTTP_404_NOT_FOUND
        assert exc.value.detail["error_code"] == "user_not_found"

    @pytest.mark.asyncio
    async def test_happy_path_returns_serialised_permissions(self, monkeypatch):
        """All 13 fields make it through; frozensets sort; enums become strings."""
        from app.api import internal

        perms = _fake_perms(
            role=ProfileRole.KB_MANAGER,
            plan="knowledge",
            addons=("scribe", "docs"),
            platform_unlocks=("widgets", "custom_mcps"),
            is_platform_admin=False,
            provisioning_status="active",
        )

        monkeypatch.setattr(internal, "_require_internal_token", AsyncMock())
        monkeypatch.setattr(internal, "_audit_internal_call", AsyncMock())
        monkeypatch.setattr(internal, "resolve_user_permissions", AsyncMock(return_value=perms))

        result = await internal.get_user_permissions(
            zitadel_user_id="zit-user-1",
            request=MagicMock(),
            db=AsyncMock(),
        )

        assert result.user_id == "zit-user-1"
        assert result.org_id == 42
        assert result.org_slug == "voys"
        assert result.role == "kb_manager"
        assert result.effective_role == "kb_manager"
        assert result.plan == "knowledge"
        # Frozenset → sorted list invariant. Unified set contains both the
        # legacy "addons" and the explicit platform-unlocks (SPEC-PORTAL-
        # EXTENSIONS-UNIFY-001 merged them on 2026-05-12).
        assert result.platform_unlocked_features == ["custom_mcps", "docs", "scribe", "widgets"]
        # Capability enum → string.
        assert result.effective_capabilities == ["kb.connectors"]
        assert result.effective_products == ["chat"]
        assert result.is_platform_admin is False
        assert result.provisioning_status == "active"

    @pytest.mark.asyncio
    async def test_deprovisioning_status_is_carried_through(self, monkeypatch):
        """REQ-19 callers (MCP) need the provisioning_status field to make
        the same tenant_deleting decision portal-api makes locally."""
        from app.api import internal

        perms = _fake_perms(provisioning_status="deprovisioning")

        monkeypatch.setattr(internal, "_require_internal_token", AsyncMock())
        monkeypatch.setattr(internal, "_audit_internal_call", AsyncMock())
        monkeypatch.setattr(internal, "resolve_user_permissions", AsyncMock(return_value=perms))

        result = await internal.get_user_permissions(
            zitadel_user_id="zit-user-1",
            request=MagicMock(),
            db=AsyncMock(),
        )
        assert result.provisioning_status == "deprovisioning"


class TestMcpRoleNotifier:
    """SPEC-PORTAL-RBAC-REFACTOR-001 REQ-14 + REQ-18 sender side."""

    @pytest.mark.asyncio
    async def test_fire_and_forget_does_not_block_caller(self):
        """``fire_role_change_notification`` MUST schedule a background task
        and return immediately, never awaiting the cross-service hop."""
        import asyncio

        from app.services.mcp_role_notifier import fire_role_change_notification

        with patch("app.services.mcp_role_notifier._notify_role_change_inner") as mock_inner:
            event = asyncio.Event()

            async def _slow(_user_id: str) -> None:
                await event.wait()

            mock_inner.side_effect = _slow

            # If fire-and-forget were broken, this would block until event.set().
            fire_role_change_notification("zit-user-1")
            # Yield control once so the task starts.
            await asyncio.sleep(0)

            # Still scheduled but not done.
            mock_inner.assert_called_once_with("zit-user-1")
            event.set()
            # Drain pending tasks before exit so pytest doesn't warn.
            await asyncio.sleep(0)

    @pytest.mark.asyncio
    async def test_inner_post_uses_internal_secret_header(self):
        """REQ-14: the cross-service notify call MUST present
        ``X-Internal-Secret`` (not OAuth) — the receiver validates against
        ``PORTAL_INTERNAL_SECRET`` via constant-time compare."""
        from app.services.mcp_role_notifier import _notify_role_change_inner

        captured: dict = {}

        class _FakeResponse:
            status_code = 200

            def json(self):
                return {"notified": 0}

        class _FakeAsyncClient:
            def __init__(self, *_a, **_kw):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_):
                return None

            async def post(self, url, json=None, headers=None):
                captured["url"] = url
                captured["headers"] = headers
                captured["json"] = json
                return _FakeResponse()

        with (
            patch(
                "app.services.mcp_role_notifier.settings.internal_secret",
                "shared-secret",
            ),
            patch(
                "app.services.mcp_role_notifier.settings.knowledge_mcp_url",
                "http://klai-knowledge-mcp:8080",
            ),
            patch("app.services.mcp_role_notifier.httpx.AsyncClient", _FakeAsyncClient),
            patch(
                "app.services.mcp_role_notifier.get_trace_headers",
                MagicMock(return_value={}),
            ),
        ):
            await _notify_role_change_inner("zit-user-1")

        assert captured["url"] == "http://klai-knowledge-mcp:8080/internal/notify-role-change"
        assert captured["headers"]["X-Internal-Secret"] == "shared-secret"
        assert captured["json"] == {"user_id": "zit-user-1"}

    @pytest.mark.asyncio
    async def test_inner_post_skips_when_secret_unset(self):
        """Empty secret → fail-safe no-op (warns, never sends an unauth'd call)."""
        from app.services.mcp_role_notifier import _notify_role_change_inner

        with (
            patch("app.services.mcp_role_notifier.settings.internal_secret", ""),
            patch(
                "app.services.mcp_role_notifier.httpx.AsyncClient",
                MagicMock(side_effect=AssertionError("MUST NOT be called")),
            ),
        ):
            # Should NOT raise (the mock would fail the assertion if hit).
            await _notify_role_change_inner("zit-user-1")
