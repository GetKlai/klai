"""
Characterization tests for provisioning orchestrator.

Tests provision_tenant availability, _ProvisionState, _caddy_lock,
and rollback logic.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.fixture(autouse=True)
def _mock_settings():
    """Provide deterministic settings for all tests."""
    import app.services.provisioning.orchestrator  # noqa: F401

    with patch("app.services.provisioning.orchestrator.settings") as mock:
        mock.domain = "getklai.com"
        mock.caddy_tenants_path = "/tmp/test-caddy-tenants"  # noqa: S108
        mock.caddy_container_name = "klai-core-caddy-1"
        mock.litellm_master_key = "test-litellm-master"
        mock.mongo_root_password = "test-mongo-pw"
        mock.redis_password = "test-redis-pw"
        yield mock


class TestCharacterizeProvisionTenantImport:
    """Test that provision_tenant is importable from the current path."""

    def test_provision_tenant_importable(self):
        from app.services.provisioning import provision_tenant

        assert callable(provision_tenant)

    def test_provision_tenant_is_async(self):
        from app.services.provisioning import provision_tenant

        assert asyncio.iscoroutinefunction(provision_tenant)


class TestCharacterizeProvisionState:
    """Characterization tests for _ProvisionState dataclass."""

    def test_default_values(self):
        from app.services.provisioning import _ProvisionState

        state = _ProvisionState()
        assert state.slug == ""
        assert state.zitadel_app_id == ""
        assert state.litellm_team_id == ""
        assert state.env_file_path == ""
        assert state.container_started is False
        assert state.caddy_written is False
        assert state.mongo_user_created is False
        assert state.mongo_user_slug == ""

    def test_custom_values(self):
        from app.services.provisioning import _ProvisionState

        state = _ProvisionState(slug="acme", container_started=True)
        assert state.slug == "acme"
        assert state.container_started is True

    def test_is_dataclass(self):
        from dataclasses import fields

        from app.services.provisioning import _ProvisionState

        field_names = {f.name for f in fields(_ProvisionState)}
        assert "slug" in field_names
        assert "zitadel_app_id" in field_names
        assert "container_started" in field_names
        assert "caddy_written" in field_names
        assert "mongo_user_created" in field_names


class TestCharacterizeCaddyLock:
    """Characterization tests for _caddy_lock module-level lock."""

    def test_caddy_lock_exists(self):
        from app.services.provisioning import _caddy_lock

        assert isinstance(_caddy_lock, asyncio.Lock)

    def test_caddy_lock_is_module_level_singleton(self):
        from app.services.provisioning import _caddy_lock as lock1
        from app.services.provisioning import _caddy_lock as lock2

        assert lock1 is lock2


class TestCompensators:
    """SPEC-PROV-001 M3 — compensator functions (replacing the old _rollback).

    Compensators are now individual functions registered on an AsyncExitStack.
    These tests cover the two behaviours that the old _rollback test guaranteed:
    1. An empty state results in no side effects.
    2. A populated state's caddy compensator removes the tenant caddyfile.
    """

    @pytest.mark.asyncio()
    async def test_compensate_caddy_is_noop_when_not_written(self):
        """caddy_written=False means no side effects."""
        from app.services.provisioning.orchestrator import (
            _compensate_caddy,
            _ProvisionState,
        )

        state = _ProvisionState(slug="acme", caddy_written=False)
        await _compensate_caddy(state)  # must not raise

    @pytest.mark.asyncio()
    async def test_compensate_caddy_removes_file_when_written(self, tmp_path):
        """caddy_written=True removes the tenant caddyfile and reloads."""
        from app.services.provisioning.orchestrator import (
            _compensate_caddy,
            _ProvisionState,
        )

        tenant_file = tmp_path / "acme.caddyfile"
        tenant_file.write_text("test")

        state = _ProvisionState(slug="acme", caddy_written=True)

        with (
            patch("app.services.provisioning.orchestrator.settings") as mock_settings,
            patch("app.services.provisioning.orchestrator._reload_caddy"),
        ):
            mock_settings.caddy_tenants_path = str(tmp_path)
            await _compensate_caddy(state)

        assert not tenant_file.exists()


class TestSeedDefaultTemplatesNonFatal:
    """SPEC-CHAT-TEMPLATES-CLEANUP-001: provisioning step 6b contract.

    REQ-TEMPLATES-SEED-E2: any exception from the seeder must be logged
    and swallowed, so broader provisioning keeps going.
    """

    @pytest.mark.asyncio
    async def test_happy_path_calls_seeder_and_commits(self):
        from app.services.provisioning.orchestrator import _seed_default_templates_non_fatal

        db = MagicMock()
        db.commit = AsyncMock()

        with patch(
            "app.services.default_templates.ensure_default_templates",
            AsyncMock(return_value=4),
        ) as seed:
            await _seed_default_templates_non_fatal(org_id=42, db=db)

        seed.assert_awaited_once_with(42, "system", db)
        db.commit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_seeder_raises_are_swallowed_not_propagated(self):
        """Exception in ensure_default_templates must NOT abort provisioning."""
        from app.services.provisioning.orchestrator import _seed_default_templates_non_fatal

        db = MagicMock()
        db.commit = AsyncMock()

        with patch(
            "app.services.default_templates.ensure_default_templates",
            AsyncMock(side_effect=RuntimeError("transient db blip")),
        ):
            # Must not raise.
            await _seed_default_templates_non_fatal(org_id=42, db=db)

    @pytest.mark.asyncio
    async def test_commit_raises_are_swallowed(self):
        """Commit failure after a successful seed is also non-fatal."""
        from app.services.provisioning.orchestrator import _seed_default_templates_non_fatal

        db = MagicMock()
        db.commit = AsyncMock(side_effect=RuntimeError("commit failed"))

        with patch(
            "app.services.default_templates.ensure_default_templates",
            AsyncMock(return_value=4),
        ):
            # Must not raise.
            await _seed_default_templates_non_fatal(org_id=42, db=db)
