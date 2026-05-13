"""
Tests for SPEC-PORTAL-PROFILES-001 Phase 1.5: get_effective_capabilities.

Phase 1.5 changes (SPEC v0.2.0):
  - get_effective_capabilities now returns role_caps ∩ plan_caps.
  - core / professional plans now include kb.connectors.
  - complete plan has the full capability set.
  - personal / company roles on complete-plan only get kb.connectors (role is floor).
  - kb_manager / group_manager / admin on complete-plan get all capabilities.
  - Admin bypass is intentional and preserved (gets complete-tier regardless of plan).
  - kb.advanced removed from PLAN_LIMITS["knowledge"].capabilities.
"""

import sys
import types
from unittest.mock import AsyncMock, MagicMock

import pytest


def _ensure_redis_mocked() -> None:
    """Ensure redis and related modules are mocked so internal.py can be imported."""
    if "redis" not in sys.modules or not hasattr(sys.modules["redis"], "asyncio"):
        redis_mod = types.ModuleType("redis")
        redis_asyncio = types.ModuleType("redis.asyncio")
        redis_exceptions = types.ModuleType("redis.exceptions")
        redis_exceptions.RedisError = Exception  # type: ignore[attr-defined]
        redis_mod.asyncio = redis_asyncio  # type: ignore[attr-defined]
        redis_mod.exceptions = redis_exceptions  # type: ignore[attr-defined]
        redis_asyncio.Redis = MagicMock  # type: ignore[attr-defined]
        sys.modules["redis"] = redis_mod
        sys.modules["redis.asyncio"] = redis_asyncio
        sys.modules["redis.exceptions"] = redis_exceptions

    if "bson" not in sys.modules or not hasattr(sys.modules["bson"], "ObjectId"):
        bson_mod = types.ModuleType("bson")
        bson_mod.ObjectId = MagicMock  # type: ignore[attr-defined]
        bson_errors = types.ModuleType("bson.errors")
        bson_errors.InvalidId = Exception  # type: ignore[attr-defined]
        bson_mod.errors = bson_errors  # type: ignore[attr-defined]
        sys.modules["bson"] = bson_mod
        sys.modules["bson.errors"] = bson_errors

    if "motor" not in sys.modules:
        motor_mod = types.ModuleType("motor")
        motor_asyncio = types.ModuleType("motor.motor_asyncio")
        motor_asyncio.AsyncIOMotorClient = MagicMock  # type: ignore[attr-defined]
        motor_mod.motor_asyncio = motor_asyncio  # type: ignore[attr-defined]
        sys.modules["motor"] = motor_mod
        sys.modules["motor.motor_asyncio"] = motor_asyncio

    sys.modules.pop("app.api.internal", None)


def _make_db_mock() -> AsyncMock:
    db = AsyncMock()
    db.add = MagicMock()
    return db


def _make_db_with_user(plan: str, role: str = "personal") -> AsyncMock:
    mock_db = _make_db_mock()
    mock_org = MagicMock()
    mock_org.plan = plan
    mock_user = MagicMock()
    mock_user.role = role
    mock_user.org_id = 1
    mock_result = MagicMock()
    mock_result.one_or_none.return_value = (mock_user, mock_org)
    mock_db.execute.return_value = mock_result
    return mock_db


class TestGetEffectiveCapabilities:
    """get_effective_capabilities returns role_caps ∩ plan_caps."""

    @pytest.mark.asyncio
    async def test_personal_on_core_has_kb_connectors(self) -> None:
        """personal role + core plan → kb.connectors (basic cap is in both)."""
        from app.api.dependencies import get_effective_capabilities

        caps = await get_effective_capabilities(user_id="u", db=_make_db_with_user("chat", "personal"))
        assert "kb.connectors" in caps

    @pytest.mark.asyncio
    async def test_personal_on_core_lacks_kb_connectors_external(self) -> None:
        """personal role + core plan → no kb.connectors.external (role blocks)."""
        from app.api.dependencies import get_effective_capabilities

        caps = await get_effective_capabilities(user_id="u", db=_make_db_with_user("chat", "personal"))
        assert "kb.connectors.external" not in caps

    @pytest.mark.asyncio
    async def test_personal_on_complete_has_only_kb_connectors(self) -> None:
        """personal role + complete plan → only kb.connectors (role is the floor)."""
        from app.api.dependencies import get_effective_capabilities

        caps = await get_effective_capabilities(user_id="u", db=_make_db_with_user("knowledge", "personal"))
        assert caps == {"kb.connectors"}

    @pytest.mark.asyncio
    async def test_company_on_complete_has_only_kb_connectors(self) -> None:
        """company role + complete plan → only kb.connectors (role is the floor)."""
        from app.api.dependencies import get_effective_capabilities

        caps = await get_effective_capabilities(user_id="u", db=_make_db_with_user("knowledge", "company"))
        assert caps == {"kb.connectors"}

    @pytest.mark.asyncio
    async def test_kb_manager_on_complete_has_full_caps(self) -> None:
        """kb_manager role + complete plan → all capabilities."""
        from app.api.dependencies import get_effective_capabilities

        caps = await get_effective_capabilities(user_id="u", db=_make_db_with_user("knowledge", "kb_manager"))
        expected = {"kb.connectors", "kb.connectors.external", "kb.create_org", "kb.members", "kb.taxonomy", "kb.gaps"}
        assert caps == expected

    @pytest.mark.asyncio
    async def test_kb_manager_on_core_has_only_kb_connectors(self) -> None:
        """kb_manager role + core plan → only kb.connectors (plan is the ceiling)."""
        from app.api.dependencies import get_effective_capabilities

        caps = await get_effective_capabilities(user_id="u", db=_make_db_with_user("chat", "kb_manager"))
        assert caps == {"kb.connectors"}

    @pytest.mark.asyncio
    async def test_kb_manager_on_professional_has_only_kb_connectors(self) -> None:
        """kb_manager role + professional plan → only kb.connectors (plan ceiling)."""
        from app.api.dependencies import get_effective_capabilities

        caps = await get_effective_capabilities(user_id="u", db=_make_db_with_user("chat", "kb_manager"))
        assert caps == {"kb.connectors"}

    @pytest.mark.asyncio
    async def test_admin_on_core_gets_complete_tier(self) -> None:
        """Admin bypass: admin always gets complete-tier capabilities, regardless of plan."""
        from app.api.dependencies import get_effective_capabilities

        caps = await get_effective_capabilities(user_id="u", db=_make_db_with_user("chat", "admin"))
        expected = {"kb.connectors", "kb.connectors.external", "kb.create_org", "kb.members", "kb.taxonomy", "kb.gaps"}
        assert caps == expected

    @pytest.mark.asyncio
    async def test_unknown_user_returns_empty(self) -> None:
        from app.api.dependencies import get_effective_capabilities

        mock_db = _make_db_mock()
        mock_result = MagicMock()
        mock_result.one_or_none.return_value = None
        mock_db.execute.return_value = mock_result

        caps = await get_effective_capabilities(user_id="ghost", db=mock_db)
        assert caps == set()

    @pytest.mark.asyncio
    async def test_unknown_plan_falls_back_to_core(self) -> None:
        """Unknown plan falls back to core; core has kb.connectors."""
        from app.api.dependencies import get_effective_capabilities

        caps = await get_effective_capabilities(user_id="u", db=_make_db_with_user("enterprise_unknown", "kb_manager"))
        assert "kb.connectors" in caps
        assert "kb.connectors.external" not in caps

    @pytest.mark.asyncio
    async def test_complete_plan_no_longer_returns_kb_advanced(self) -> None:
        """kb.advanced was removed from PLAN_LIMITS[complete] in SPEC v0.2.0."""
        from app.api.dependencies import get_effective_capabilities

        caps = await get_effective_capabilities(user_id="u", db=_make_db_with_user("knowledge", "kb_manager"))
        assert "kb.advanced" not in caps


class TestRequireCapability:
    """require_capability dependency raises 403 when caller lacks the capability."""

    @pytest.mark.asyncio
    async def test_personal_on_core_has_kb_connectors(self) -> None:
        """personal + core -> kb.connectors passes (basic cap is in both)."""
        from app.api.dependencies import require_capability

        dep = require_capability("kb.connectors")
        await dep(user_id="u", db=_make_db_with_user("chat", "personal"))

    @pytest.mark.asyncio
    async def test_personal_on_complete_lacks_kb_members(self) -> None:
        """personal + complete -> 403 for kb.members (role blocks)."""
        from fastapi import HTTPException

        from app.api.dependencies import require_capability

        dep = require_capability("kb.members")
        with pytest.raises(HTTPException) as exc_info:
            await dep(user_id="u", db=_make_db_with_user("knowledge", "personal"))
        assert exc_info.value.status_code == 403

    @pytest.mark.asyncio
    async def test_kb_manager_on_complete_has_kb_members(self) -> None:
        from app.api.dependencies import require_capability

        dep = require_capability("kb.members")
        await dep(user_id="u", db=_make_db_with_user("knowledge", "kb_manager"))

    @pytest.mark.asyncio
    async def test_kb_manager_on_core_lacks_kb_connectors_external(self) -> None:
        """kb_manager + core -> 403 for kb.connectors.external (plan blocks)."""
        from fastapi import HTTPException

        from app.api.dependencies import require_capability

        dep = require_capability("kb.connectors.external")
        with pytest.raises(HTTPException) as exc_info:
            await dep(user_id="u", db=_make_db_with_user("chat", "kb_manager"))
        assert exc_info.value.status_code == 403

    @pytest.mark.asyncio
    async def test_admin_on_core_has_all_capabilities(self) -> None:
        """Admin bypass: admin on core plan passes any capability check."""
        from app.api.dependencies import require_capability

        for cap in ["kb.connectors", "kb.connectors.external", "kb.members", "kb.taxonomy", "kb.gaps"]:
            dep = require_capability(cap)
            await dep(user_id="u", db=_make_db_with_user("chat", "admin"))

    @pytest.mark.asyncio
    async def test_personal_on_core_lacks_kb_gaps(self) -> None:
        """personal + core -> 403 for kb.gaps (role blocks)."""
        from fastapi import HTTPException

        from app.api.dependencies import require_capability

        dep = require_capability("kb.gaps")
        with pytest.raises(HTTPException) as exc_info:
            await dep(user_id="u", db=_make_db_with_user("chat", "personal"))
        assert exc_info.value.status_code == 403

    @pytest.mark.asyncio
    async def test_personal_on_core_lacks_kb_taxonomy(self) -> None:
        """personal + core -> 403 for kb.taxonomy (role blocks)."""
        from fastapi import HTTPException

        from app.api.dependencies import require_capability

        dep = require_capability("kb.taxonomy")
        with pytest.raises(HTTPException) as exc_info:
            await dep(user_id="u", db=_make_db_with_user("chat", "personal"))
        assert exc_info.value.status_code == 403


class TestUserProductsResponseCapabilities:
    """UserProductsResponse includes capabilities field."""

    def test_user_products_response_source_has_capabilities_field(self) -> None:
        import re
        from pathlib import Path

        source = (Path(__file__).parent.parent / "app" / "api" / "internal.py").read_text()
        assert re.search(r"capabilities:\s*list\[str\]\s*=\s*\[\]", source), (
            "UserProductsResponse in internal.py must declare 'capabilities: list[str] = []'"
        )

    def test_user_products_response_source_calls_get_effective_capabilities(self) -> None:
        from pathlib import Path

        source = (Path(__file__).parent.parent / "app" / "api" / "internal.py").read_text()
        assert "get_effective_capabilities" in source, (
            "get_user_products in internal.py must call get_effective_capabilities"
        )

    def test_user_products_response_source_imports_get_effective_capabilities(self) -> None:
        from pathlib import Path

        source = (Path(__file__).parent.parent / "app" / "api" / "internal.py").read_text()
        assert "from app.api.dependencies import get_effective_capabilities" in source, (
            "internal.py must import get_effective_capabilities from app.api.dependencies"
        )


class TestInternalGetUserProductsWithCapabilities:
    """Internal /users/{id}/products endpoint returns capabilities."""

    def test_get_user_products_source_returns_sorted_capabilities(self) -> None:
        from pathlib import Path

        source = (Path(__file__).parent.parent / "app" / "api" / "internal.py").read_text()
        assert "sorted(capabilities)" in source, (
            "get_user_products must return sorted(capabilities) for deterministic output"
        )

    def test_get_user_products_response_includes_capabilities_in_return(self) -> None:
        from pathlib import Path

        source = (Path(__file__).parent.parent / "app" / "api" / "internal.py").read_text()
        assert "capabilities=sorted(capabilities)" in source, (
            "get_user_products must pass capabilities= to UserProductsResponse"
        )
