from __future__ import annotations

import inspect
from datetime import UTC, date, datetime

import pytest
from fastapi import HTTPException, status

from app.api.admin import platform_stats
from tests.conftest import make_perms


def _platform_perms():
    return make_perms(role="admin", org_id=1, org_slug="getklai", is_platform_admin=True)


async def _noop_audit(*_args, **_kwargs):
    return None


def _response_keys(value):
    if isinstance(value, dict):
        for key, child in value.items():
            yield key
            yield from _response_keys(child)
    elif isinstance(value, list):
        for child in value:
            yield from _response_keys(child)


def _empty_portal_overview():
    return {
        "total_events": 0,
        "knowledge_queries": 0,
        "knowledge_uploads": 0,
        "meetings_started": 0,
        "problem_reports": 0,
        "active_users": 0,
        "active_tenants": 0,
    }


def test_usage_window_returns_exact_calendar_day_count(monkeypatch):
    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 7, 7, 12, 30, tzinfo=tz)

    monkeypatch.setattr(platform_stats, "datetime", FixedDateTime)

    start, end = platform_stats._window("30d")

    assert start == datetime(2026, 6, 8, 0, 0, tzinfo=UTC)
    assert end == datetime(2026, 7, 8, 0, 0, tzinfo=UTC)
    assert (end.date() - start.date()).days == 30


def test_tenant_row_uses_none_for_missing_litellm_mapping():
    row = {
        "org_id": 8,
        "name": "Voys",
        "slug": "voys",
        "plan": "knowledge",
        "billing_status": "pending",
        "litellm_team_id": None,
        "knowledge_queries": 260,
        "active_users": 11,
        "total_events": 262,
        "last_activity_at": None,
    }

    result = platform_stats._tenant_row(row, True, None)

    assert result.api_requests is None
    assert result.total_tokens is None
    assert result.spend_usd is None


def test_tenant_row_uses_zero_for_mapped_team_without_usage():
    row = {
        "org_id": 8,
        "name": "Voys",
        "slug": "voys",
        "plan": "knowledge",
        "billing_status": "pending",
        "litellm_team_id": "team-voys",
        "knowledge_queries": 0,
        "active_users": 0,
        "total_events": 0,
        "last_activity_at": None,
    }

    result = platform_stats._tenant_row(row, True, None)

    assert result.api_requests == 0
    assert result.successful_requests == 0
    assert result.failed_requests == 0
    assert result.total_tokens == 0
    assert result.spend_usd == 0.0


def test_daily_points_zero_fill_and_litellm_none_when_unavailable():
    start = datetime(2026, 7, 5, tzinfo=UTC)
    end = datetime(2026, 7, 8, tzinfo=UTC)
    product_rows = [
        {"day": date(2026, 7, 6), "events": 3, "knowledge_queries": 2},
    ]

    points = platform_stats._daily_points(
        start,
        end,
        product_rows,
        {},
        include_litellm=False,
    )

    assert [p.date for p in points] == [
        date(2026, 7, 5),
        date(2026, 7, 6),
        date(2026, 7, 7),
    ]
    assert [p.knowledge_queries for p in points] == [0, 2, 0]
    assert all(p.api_requests is None for p in points)


def test_sum_litellm_includes_all_teams_for_overview():
    result = platform_stats._sum_litellm(
        {
            "team-a": {
                "api_requests": 2,
                "successful_requests": 1,
                "failed_requests": 1,
                "total_tokens": 100,
                "spend_usd": 0.1,
            },
            "team-b": {
                "api_requests": 3,
                "successful_requests": 3,
                "failed_requests": 0,
                "total_tokens": 200,
                "spend_usd": 0.2,
            },
        }
    )

    assert result == {
        "api_requests": 5,
        "successful_requests": 4,
        "failed_requests": 1,
        "total_tokens": 300,
        "spend_usd": pytest.approx(0.3),
    }


@pytest.mark.asyncio
async def test_litellm_team_totals_keeps_blank_team_id_for_overview(monkeypatch):
    async def fake_rows(_sql, _params):
        return (
            True,
            True,
            [{"team_id": None, "api_requests": 7, "successful_requests": 6, "failed_requests": 1}],
        )

    monkeypatch.setattr(platform_stats, "_litellm_rows", fake_rows)

    _, available, rows = await platform_stats._litellm_team_totals(
        datetime(2026, 7, 1, tzinfo=UTC),
        datetime(2026, 7, 2, tzinfo=UTC),
    )

    assert available is True
    assert platform_stats._sum_litellm(rows)["api_requests"] == 7


@pytest.mark.asyncio
async def test_litellm_unconfigured_is_explicit(monkeypatch):
    monkeypatch.setattr(platform_stats, "litellm_analytics_configured", lambda: False)

    configured, available, rows = await platform_stats._litellm_rows("SELECT 1", {})

    assert configured is False
    assert available is False
    assert rows == []


@pytest.mark.asyncio
async def test_usage_routes_use_platform_admin_dependency():
    expected_paths = {"/platform/usage/overview", "/platform/usage/tenants", "/platform/usage/tenants/{org_id}"}
    routes = [route for route in platform_stats.router.routes if getattr(route, "path", None) in expected_paths]

    assert {route.path for route in routes} == expected_paths

    for route in routes:
        dependency = inspect.signature(route.endpoint).parameters["perms"].default.dependency
        assert await dependency(perms=_platform_perms())
        with pytest.raises(HTTPException) as exc:
            await dependency(perms=make_perms(role="admin", org_id=8, org_slug="voys", is_platform_admin=False))
        assert exc.value.status_code == status.HTTP_403_FORBIDDEN


@pytest.mark.asyncio
async def test_usage_endpoints_emit_audit_events(monkeypatch):
    audits: list[tuple[int | None, platform_stats.UsageRange]] = []

    async def fake_audit(_perms, *, org_id, range_name):
        audits.append((org_id, range_name))

    async def fake_overview(_start, _end):
        return _empty_portal_overview()

    async def fake_tenants(_start, _end):
        return []

    async def fake_detail(_org_id, _start, _end):
        return (
            {
                "org_id": 8,
                "name": "Voys",
                "slug": "voys",
                "litellm_team_id": None,
                "active_users": 0,
                "last_activity_at": None,
            },
            [],
            [],
        )

    async def fake_litellm_totals(_start, _end):
        return False, False, {}

    monkeypatch.setattr(platform_stats, "_audit_usage", fake_audit)
    monkeypatch.setattr(platform_stats, "_portal_overview", fake_overview)
    monkeypatch.setattr(platform_stats, "_portal_tenants", fake_tenants)
    monkeypatch.setattr(platform_stats, "_portal_tenant_detail", fake_detail)
    monkeypatch.setattr(platform_stats, "_litellm_team_totals", fake_litellm_totals)
    monkeypatch.setattr(platform_stats, "litellm_analytics_configured", lambda: False)

    await platform_stats.platform_usage_overview(range="7d", perms=_platform_perms())
    await platform_stats.platform_usage_tenants(range="7d", perms=_platform_perms())
    await platform_stats.platform_usage_tenant_detail(org_id=8, range="7d", perms=_platform_perms())

    assert audits == [(None, "7d"), (None, "7d"), (8, "7d")]


@pytest.mark.asyncio
async def test_tenant_detail_reports_unmapped_litellm_without_false_zeroes(monkeypatch):
    async def fake_detail(_org_id, _start, _end):
        return (
            {
                "org_id": 8,
                "name": "Voys",
                "slug": "voys",
                "litellm_team_id": None,
                "active_users": 1,
                "last_activity_at": datetime(2026, 7, 6, 12, tzinfo=UTC),
            },
            [{"day": date(2026, 7, 6), "events": 3, "knowledge_queries": 2}],
            [{"event_type": "knowledge.queried", "count": 2}],
        )

    async def unexpected_litellm(*_args, **_kwargs):
        raise AssertionError("unmapped tenants must not query LiteLLM team analytics")

    monkeypatch.setattr(platform_stats, "_audit_usage", _noop_audit)
    monkeypatch.setattr(platform_stats, "_portal_tenant_detail", fake_detail)
    monkeypatch.setattr(platform_stats, "_litellm_daily", unexpected_litellm)
    monkeypatch.setattr(platform_stats, "_litellm_models", unexpected_litellm)
    monkeypatch.setattr(platform_stats, "litellm_analytics_configured", lambda: True)

    result = await platform_stats.platform_usage_tenant_detail(org_id=8, range="30d", perms=_platform_perms())

    assert result.litellm_configured is True
    assert result.litellm_mapped is False
    assert result.litellm_available is False
    assert result.model_breakdown is None
    assert all(point.api_requests is None for point in result.daily)
    assert result.event_type_breakdown == [platform_stats.EventTypeCount(event_type="knowledge.queried", count=2)]
    keys = set(_response_keys(result.model_dump()))
    assert "user_id" not in keys
    assert "properties" not in keys


@pytest.mark.asyncio
async def test_tenant_detail_reports_litellm_error_when_configured_but_unavailable(monkeypatch):
    async def fake_detail(_org_id, _start, _end):
        return (
            {
                "org_id": 8,
                "name": "Voys",
                "slug": "voys",
                "litellm_team_id": "team-voys",
                "active_users": 0,
                "last_activity_at": None,
            },
            [],
            [],
        )

    async def fake_daily(_team_id, _start, _end):
        return True, False, {}

    async def fake_models(_team_id, _start, _end):
        return True, False, []

    monkeypatch.setattr(platform_stats, "_audit_usage", _noop_audit)
    monkeypatch.setattr(platform_stats, "_portal_tenant_detail", fake_detail)
    monkeypatch.setattr(platform_stats, "_litellm_daily", fake_daily)
    monkeypatch.setattr(platform_stats, "_litellm_models", fake_models)
    monkeypatch.setattr(platform_stats, "litellm_analytics_configured", lambda: True)

    result = await platform_stats.platform_usage_tenant_detail(org_id=8, range="30d", perms=_platform_perms())

    assert result.litellm_configured is True
    assert result.litellm_mapped is True
    assert result.litellm_available is False
    assert result.model_breakdown is None
    assert all(point.api_requests is None for point in result.daily)


@pytest.mark.asyncio
async def test_tenant_detail_404_propagates(monkeypatch):
    async def missing_detail(_org_id, _start, _end):
        raise HTTPException(status_code=404, detail="Organisatie niet gevonden")

    monkeypatch.setattr(platform_stats, "_audit_usage", _noop_audit)
    monkeypatch.setattr(platform_stats, "_portal_tenant_detail", missing_detail)

    with pytest.raises(HTTPException) as exc:
        await platform_stats.platform_usage_tenant_detail(org_id=404, range="30d", perms=_platform_perms())

    assert exc.value.status_code == status.HTTP_404_NOT_FOUND
