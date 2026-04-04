"""
Characterization tests for provisioning orchestrator.

Tests provision_tenant availability, _ProvisionState, _caddy_lock,
and rollback logic.
"""

import asyncio
from unittest.mock import patch

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


class TestCharacterizeRollback:
    """Characterization tests for _rollback function."""

    def test_rollback_is_async(self):
        from app.services.provisioning import _rollback

        assert asyncio.iscoroutinefunction(_rollback)

    @pytest.mark.asyncio()
    async def test_rollback_empty_state_is_noop(self):
        """Rollback with default state should do nothing and not raise."""
        from app.services.provisioning import _ProvisionState, _rollback

        state = _ProvisionState()
        # Should not raise any exceptions
        await _rollback(state)

    @pytest.mark.asyncio()
    async def test_rollback_caddy_written_removes_file(self, tmp_path):
        """Rollback removes the caddy file when caddy_written is True."""
        from app.services.provisioning import _ProvisionState, _rollback

        tenant_file = tmp_path / "acme.caddyfile"
        tenant_file.write_text("test")

        state = _ProvisionState(slug="acme", caddy_written=True)

        with (
            patch("app.services.provisioning.orchestrator.settings") as mock_settings,
            patch("app.services.provisioning.orchestrator._reload_caddy"),
        ):
            mock_settings.caddy_tenants_path = str(tmp_path)
            await _rollback(state)

        assert not tenant_file.exists()
