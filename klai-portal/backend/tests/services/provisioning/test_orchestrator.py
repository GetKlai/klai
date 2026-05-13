"""
Characterization tests for provisioning orchestrator.

Tests provision_tenant availability, _ProvisionState, _caddy_lock,
and rollback logic.
"""

import asyncio
import contextlib
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


class TestProvisionUsesDBSlug:
    """SPEC-INFRA-TENANT-DELETE-003 Bug 2 — orchestrator must use the slug
    committed by signup.py (org.slug), not regenerate via _slugify_unique.

    Bug history: prior code ran
        slug = _slugify_unique(org.name, existing_slugs)
    which produced a DIFFERENT slug than the one signup.py wrote to
    portal_orgs.slug (e.g. "e2e" vs "e2e-37271947" — the difference is
    that signup uses _to_slug() with a zitadel-id suffix for uniqueness,
    while _slugify_unique drops the suffix when no active collision exists).
    Provisioned resources used the orchestrator slug; the DB held a
    different slug; deprovisioning queried by DB slug → missed the
    actual resources → orphans.
    """

    @pytest.mark.asyncio
    async def test_orchestrator_uses_org_slug_not_regenerated(self) -> None:
        """`_provision` must read slug directly from org.slug — verified by
        injecting a sentinel org with name='Different Name' and
        slug='committed-slug', running the entry-state branch, and asserting
        the `provisioning_tenant_start` log carries `committed-slug`.
        """
        from app.services.provisioning.orchestrator import _provision

        captured_log_slug: dict[str, object] = {}

        def _capture_logger():
            real_logger = __import__("structlog").get_logger()
            orig_info = real_logger.info

            def _capture(event, **kwargs):
                if event == "provisioning_tenant_start":
                    captured_log_slug["slug"] = kwargs.get("slug")
                return orig_info(event, **kwargs)

            real_logger.info = _capture
            return real_logger

        # Build a SimpleNamespace-style org that returns the sentinel slug
        # but a different name (so a regenerator would produce a different slug).
        org = MagicMock()
        org.id = 99
        org.name = "Different Name"
        org.slug = "committed-slug-x42"
        org.provisioning_status = "pending"
        org.deleted_at = None
        org.zitadel_org_id = "zit-org-99"
        org.mcp_servers = None
        org.litellm_team_key = None

        # Result-stubs for two SELECT calls before the slug-line:
        #   1) SELECT PortalOrg WHERE id=org_id → scalar_one() → org
        #   2) SELECT slug FROM portal_orgs WHERE deleted_at IS NULL — no longer
        #      called after the Bug 2 fix; we still mock it harmlessly.
        org_result = MagicMock()
        org_result.scalar_one = MagicMock(return_value=org)
        slugs_result = MagicMock()
        slugs_result.fetchall = MagicMock(return_value=[("other-slug",), ("committed-slug-x42",)])

        # Fail early at the first orchestrator step that needs network so we
        # can isolate the slug-read assertion without mocking 16 steps.
        with (
            patch(
                "app.services.provisioning.orchestrator.zitadel.create_librechat_oidc_app",
                new=AsyncMock(side_effect=RuntimeError("STOP — fail after slug read")),
            ),
            patch("app.services.provisioning.orchestrator.transition_state", new=AsyncMock()),
            patch("app.services.provisioning.orchestrator.pin_session", new=AsyncMock()),
            patch("app.services.provisioning.orchestrator.logger") as mock_logger,
        ):
            db = AsyncMock()
            db.execute = AsyncMock(side_effect=[org_result, slugs_result])

            mock_logger.info = MagicMock()
            mock_logger.warning = MagicMock()
            mock_logger.exception = MagicMock()
            mock_logger.error = MagicMock()

            with contextlib.suppress(Exception):
                # Expect STOP after slug-read (mocked downstream step raises).
                await _provision(99, db)

            # Find the provisioning_tenant_start log call
            start_calls = [
                c for c in mock_logger.info.call_args_list if c.args and c.args[0] == "provisioning_tenant_start"
            ]
            assert start_calls, "provisioning_tenant_start was not logged"
            kwargs = start_calls[0].kwargs
            assert kwargs.get("slug") == "committed-slug-x42", (
                f"orchestrator must use org.slug='committed-slug-x42'; "
                f"got slug={kwargs.get('slug')!r} (likely from _slugify_unique regenerator)"
            )
