"""Tests for SPEC-TI-010B finding B-10: Redis namespace flush on deprovision.

Before the fix, _flush_redis_tenant_keys only flushed configs:{slug}:* keys.
After the fix it also flushes the following namespaces keyed on zitadel_org_id:
  - templates:{zitadel_org_id}:*
  - kb_ver:{zitadel_org_id}:*
  - kb_feature:{zitadel_org_id}:*
  - connector_rl:read:{zitadel_org_id}
  - connector_rl:write:{zitadel_org_id}
  - rl:{zitadel_org_id}:*
  - templates_rl:{zitadel_org_id}

Uses the same _make_state() helper pattern as test_deprovisioning_steps.py.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest


def _make_state(**overrides) -> SimpleNamespace:
    defaults = {
        "db": MagicMock(),
        "org_id": 42,
        "slug": "acme",
        "zitadel_org_id": "362757920133283846",
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _make_redis_mock(keys_by_pattern: dict[str, list[str]] | None = None) -> MagicMock:
    """Return a sync Redis mock whose scan_iter yields keys per pattern."""
    keys_by_pattern = keys_by_pattern or {}
    mock_redis = MagicMock()
    mock_redis.close = MagicMock()
    mock_redis.unlink.return_value = 1

    def _scan_iter(match: str, count: int = 100):
        return iter(keys_by_pattern.get(match, []))

    mock_redis.scan_iter = MagicMock(side_effect=_scan_iter)
    return mock_redis


class TestFlushRedisTenantKeysB10:
    """Asserts that all expected namespaces are flushed on deprovision."""

    @pytest.mark.asyncio
    async def test_flushes_templates_namespace(self) -> None:
        """templates:{zitadel_org_id}:* keys must be deleted on deprovision."""
        state = _make_state()
        zid = state.zitadel_org_id
        pattern = f"templates:{zid}:*"
        mock_redis = _make_redis_mock({pattern: [f"templates:{zid}:user-1", f"templates:{zid}:user-2"]})

        with (
            patch("app.services.provisioning.deprovisioning_steps.settings") as mock_settings,
            patch("redis.Redis", return_value=mock_redis),
        ):
            mock_settings.redis_host = "localhost"
            mock_settings.redis_port = 6379
            mock_settings.redis_password = None
            from app.services.provisioning.deprovisioning_steps import _flush_redis_tenant_keys

            await _flush_redis_tenant_keys(state)

        # scan_iter must have been called with the templates pattern
        patterns_scanned = [call.kwargs.get("match") or call.args[0] for call in mock_redis.scan_iter.call_args_list]
        assert pattern in patterns_scanned, f"templates pattern not scanned; got: {patterns_scanned}"
        # unlink must have been called (because mock returned 2 keys)
        mock_redis.unlink.assert_called()

    @pytest.mark.asyncio
    async def test_flushes_kb_ver_namespace(self) -> None:
        """kb_ver:{zitadel_org_id}:* keys must be deleted on deprovision."""
        state = _make_state()
        zid = state.zitadel_org_id
        pattern = f"kb_ver:{zid}:*"
        mock_redis = _make_redis_mock({pattern: [f"kb_ver:{zid}:user-x"]})

        with (
            patch("app.services.provisioning.deprovisioning_steps.settings") as mock_settings,
            patch("redis.Redis", return_value=mock_redis),
        ):
            mock_settings.redis_host = "localhost"
            mock_settings.redis_port = 6379
            mock_settings.redis_password = None
            from app.services.provisioning.deprovisioning_steps import _flush_redis_tenant_keys

            await _flush_redis_tenant_keys(state)

        patterns_scanned = [call.kwargs.get("match") or call.args[0] for call in mock_redis.scan_iter.call_args_list]
        assert pattern in patterns_scanned, f"kb_ver pattern not scanned; got: {patterns_scanned}"

    @pytest.mark.asyncio
    async def test_flushes_kb_feature_namespace(self) -> None:
        """kb_feature:{zitadel_org_id}:* keys must be deleted on deprovision."""
        state = _make_state()
        zid = state.zitadel_org_id
        pattern = f"kb_feature:{zid}:*"
        mock_redis = _make_redis_mock({pattern: [f"kb_feature:{zid}:user-y:3"]})

        with (
            patch("app.services.provisioning.deprovisioning_steps.settings") as mock_settings,
            patch("redis.Redis", return_value=mock_redis),
        ):
            mock_settings.redis_host = "localhost"
            mock_settings.redis_port = 6379
            mock_settings.redis_password = None
            from app.services.provisioning.deprovisioning_steps import _flush_redis_tenant_keys

            await _flush_redis_tenant_keys(state)

        patterns_scanned = [call.kwargs.get("match") or call.args[0] for call in mock_redis.scan_iter.call_args_list]
        assert pattern in patterns_scanned, f"kb_feature pattern not scanned; got: {patterns_scanned}"

    @pytest.mark.asyncio
    async def test_flushes_rate_limiter_namespace(self) -> None:
        """rl:{zitadel_org_id}:* keys must be deleted on deprovision."""
        state = _make_state()
        zid = state.zitadel_org_id
        pattern = f"rl:{zid}:*"
        mock_redis = _make_redis_mock({pattern: [f"rl:{zid}:window1"]})

        with (
            patch("app.services.provisioning.deprovisioning_steps.settings") as mock_settings,
            patch("redis.Redis", return_value=mock_redis),
        ):
            mock_settings.redis_host = "localhost"
            mock_settings.redis_port = 6379
            mock_settings.redis_password = None
            from app.services.provisioning.deprovisioning_steps import _flush_redis_tenant_keys

            await _flush_redis_tenant_keys(state)

        patterns_scanned = [call.kwargs.get("match") or call.args[0] for call in mock_redis.scan_iter.call_args_list]
        assert pattern in patterns_scanned, f"rl pattern not scanned; got: {patterns_scanned}"

    @pytest.mark.asyncio
    async def test_flushes_connector_rl_exact_keys(self) -> None:
        """connector_rl:read/write:{zitadel_org_id} must be UNLINK-ed as exact keys."""
        state = _make_state()
        zid = state.zitadel_org_id
        mock_redis = _make_redis_mock()  # no scan hits

        with (
            patch("app.services.provisioning.deprovisioning_steps.settings") as mock_settings,
            patch("redis.Redis", return_value=mock_redis),
        ):
            mock_settings.redis_host = "localhost"
            mock_settings.redis_port = 6379
            mock_settings.redis_password = None
            from app.services.provisioning.deprovisioning_steps import _flush_redis_tenant_keys

            await _flush_redis_tenant_keys(state)

        # The exact keys are sent in one UNLINK call together with templates_rl
        all_unlink_calls = mock_redis.unlink.call_args_list
        all_unlinkable = [key for call in all_unlink_calls for key in call.args]
        assert f"connector_rl:read:{zid}" in all_unlinkable, (
            f"connector_rl:read:{zid} not in unlink calls: {all_unlinkable}"
        )
        assert f"connector_rl:write:{zid}" in all_unlinkable, (
            f"connector_rl:write:{zid} not in unlink calls: {all_unlinkable}"
        )
        assert f"templates_rl:{zid}" in all_unlinkable, f"templates_rl:{zid} not in unlink calls: {all_unlinkable}"

    @pytest.mark.asyncio
    async def test_all_expected_scan_patterns_are_covered(self) -> None:
        """Parametrized: every expected namespace must appear in the scan_iter calls."""
        state = _make_state()
        zid = state.zitadel_org_id
        slug = state.slug

        expected_patterns = [
            f"configs:{slug}:*",
            f"templates:{zid}:*",
            f"kb_ver:{zid}:*",
            f"kb_feature:{zid}:*",
            f"rl:{zid}:*",
        ]

        mock_redis = _make_redis_mock()

        with (
            patch("app.services.provisioning.deprovisioning_steps.settings") as mock_settings,
            patch("redis.Redis", return_value=mock_redis),
        ):
            mock_settings.redis_host = "localhost"
            mock_settings.redis_port = 6379
            mock_settings.redis_password = None
            from app.services.provisioning.deprovisioning_steps import _flush_redis_tenant_keys

            await _flush_redis_tenant_keys(state)

        patterns_scanned = {call.kwargs.get("match") or call.args[0] for call in mock_redis.scan_iter.call_args_list}
        for expected in expected_patterns:
            assert expected in patterns_scanned, (
                f"Pattern '{expected}' not flushed on deprovision. Scanned: {patterns_scanned}"
            )

    @pytest.mark.asyncio
    async def test_idempotent_when_no_keys_exist(self) -> None:
        """When no keys exist for any namespace, no unlink should be called via scan."""
        state = _make_state()
        mock_redis = _make_redis_mock()  # all patterns return empty

        with (
            patch("app.services.provisioning.deprovisioning_steps.settings") as mock_settings,
            patch("redis.Redis", return_value=mock_redis),
        ):
            mock_settings.redis_host = "localhost"
            mock_settings.redis_port = 6379
            mock_settings.redis_password = None
            from app.services.provisioning.deprovisioning_steps import _flush_redis_tenant_keys

            await _flush_redis_tenant_keys(state)

        # The exact-key unlink is still called once (for connector_rl + templates_rl)
        # but no scan-derived unlinks should occur.
        # Total unlink calls: at most 1 (the exact keys batch)
        assert mock_redis.unlink.call_count <= 1
