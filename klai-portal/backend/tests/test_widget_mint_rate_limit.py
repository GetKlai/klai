"""Per-widget + per-client mint rate-limit on /partner/v1/widget-config and /public-bot-config.

REQ-7 (Finding B-4, SPEC-SEC-CROSS-TENANT-FOLLOWUP-001):
- widget-config must rate-limit before DB lookup: per client IP (20/min) then
  per widget abuse ceiling (120/min). The ceiling used to be 10/min, which was
  a traffic cap shared by every visitor of a widget (help.voys.nl 429s).
- public-bot-config must apply the same two layers before DB lookup: the
  client bucket widget_mint_ip:{id}:{ip} is shared with the embed path, so a
  share-link hammerer can never burn more of the 120/min widget_mint:{id}
  ceiling than its own IP share (20/min) and lock out embed visitors.
- 429 is returned with Retry-After header when a limit fires.

AC7.1, AC7.2, AC7.3 from acceptance.md.

# @MX:NOTE: [AUTO] Tests patch app.services.partner_rate_limit.check_rate_limit
# where only the wiring is under test; the acceptance tests run the REAL limiter
# on the fakeredis pool from conftest so multi-client traffic is actually admitted.
# @MX:SPEC: SPEC-SEC-CROSS-TENANT-FOLLOWUP-001 REQ-7
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

WIDGET_ID = "wgt_abcdef1234567890abcdef1234567890abcdef12"

# ---------------------------------------------------------------------------
# Shared fakes (mirrors test_widget_platform_unlock.py)
# ---------------------------------------------------------------------------


@dataclass
class FakeWidget:
    id: str = "widget-uuid-1"
    org_id: int = 42
    name: str = "Test widget"
    description: str | None = None
    widget_id: str = "wgt_abcdef1234567890abcdef1234567890abcdef12"
    widget_config: dict = field(
        default_factory=lambda: {
            "allowed_origins": ["https://example.com"],
            "title": "Chat",
            "welcome_message": "Hello!",
            "css_variables": {},
        }
    )
    public_share_enabled: bool = True
    allow_any_origin: bool = True  # allow any origin so origin check doesn't interfere
    rate_limit_rpm: int = 60
    last_used_at: datetime | None = None
    created_at: datetime = field(default_factory=lambda: datetime(2026, 1, 1, tzinfo=UTC))
    created_by: str = "test-user"


@dataclass
class FakeOrg:
    id: int = 42
    zitadel_org_id: str = "zitadel-org-123"
    slug: str = "test"
    platform_unlocked_features: list = field(default_factory=lambda: ["widgets"])
    enabled_addons: list = field(default_factory=list)


def _make_db_mock(widget_row=None, org_row=None):
    """Build a mock AsyncSession that returns the given rows in sequence."""
    db = AsyncMock()
    db.add = MagicMock()

    widget_result = MagicMock()
    widget_result.scalar_one_or_none.return_value = widget_row or FakeWidget()

    org_result = MagicMock()
    org_result.scalar_one_or_none.return_value = org_row or FakeOrg()

    kb_result = MagicMock()
    kb_result.scalars.return_value.all.return_value = []

    db.execute = AsyncMock(side_effect=[widget_result, org_result, kb_result])
    return db


def _make_request(origin: str = "https://example.com", caller_ip: str = "203.0.113.5"):
    req = MagicMock()
    req.headers = {"origin": origin}
    # resolve_caller_ip() reads request.client.host — uvicorn fills it from the
    # Caddy-validated X-Forwarded-For in production.
    req.client.host = caller_ip
    return req


@contextmanager
def _mint_stubs() -> Any:
    """Stub everything around the mint handler EXCEPT the real rate limiter,
    so acceptance tests drive app.services.partner_rate_limit.check_rate_limit
    against the fakeredis pool (fake_redis fixture)."""
    with (
        patch("app.api.partner.settings") as mock_settings,
        patch("app.api.partner.set_tenant", new_callable=AsyncMock),
        patch("app.api.partner.generate_session_token", return_value="tok"),
        patch("app.api.partner.assert_platform_unlocked"),
    ):
        mock_settings.widget_jwt_secret = "test-secret"
        yield


# ---------------------------------------------------------------------------
# Acceptance (real limiter, one minute, one widget):
# reported symptom — the 11th visitor of help.voys.nl got 429.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_twelve_mints_from_twelve_clients_all_succeed(fake_redis):
    """12 distinct client IPs minting the same widget within a minute all get 200.

    Regression for the reported traffic-cap: the per-widget number must never
    be a shared-by-all-visitors 10/min (REQ-7 ceiling is 120/min now)."""
    from app.api.partner import widget_config

    with _mint_stubs():
        for n in range(12):
            request = _make_request(caller_ip=f"203.0.113.{n + 1}")
            response = await widget_config(id=WIDGET_ID, request=request, db=_make_db_mock())
            assert response.status_code == 200, f"visitor {n + 1} got {response.status_code}"


@pytest.mark.asyncio
async def test_one_client_hammering_one_widget_stops_at_per_client_limit(fake_redis):
    """A single client IP is capped at 20 mints/min (REQ-7 abuse bound).

    The 21st mint from the same IP is 429 while other clients are unaffected."""
    from app.api.partner import widget_config

    with _mint_stubs():
        hammerer = _make_request(caller_ip="198.51.100.7")
        for n in range(20):
            response = await widget_config(id=WIDGET_ID, request=hammerer, db=_make_db_mock())
            assert response.status_code == 200, f"mint {n + 1} from one client got 429"
        response = await widget_config(id=WIDGET_ID, request=hammerer, db=_make_db_mock())
        assert response.status_code == 429
        # A different client is not punished for the hammerer's volume:
        bystander = _make_request(caller_ip="198.51.100.8")
        bystander_response = await widget_config(id=WIDGET_ID, request=bystander, db=_make_db_mock())
        assert bystander_response.status_code == 200


@pytest.mark.asyncio
async def test_widget_ceiling_stops_many_distinct_clients(fake_redis):
    """The per-widget ceiling (120/min, REQ-7) is the backstop: 121 distinct
    client IPs within a minute → the 121st is 429 even though no client hit
    its own per-IP limit (simulates an IP-rotating botnet)."""
    from app.api.partner import widget_config

    with _mint_stubs():
        for n in range(120):
            request = _make_request(caller_ip=f"198.18.0.{n}")
            response = await widget_config(id=WIDGET_ID, request=request, db=_make_db_mock())
            assert response.status_code == 200, f"mint {n + 1} got {response.status_code}"
        last = _make_request(caller_ip="198.18.1.0")
        response = await widget_config(id=WIDGET_ID, request=last, db=_make_db_mock())
        assert response.status_code == 429


@pytest.mark.asyncio
async def test_share_link_hammerer_stops_at_20_and_embed_visitor_is_unaffected(fake_redis):
    """Review finding 2: the share-link path enforces the same per-client layer.

    One client hammering public-bot-config is stopped at 20/min; because the
    per-client bucket is shared with the embed path, it can never take more
    than its own IP share of the 120/min widget_mint:{id} ceiling. A
    widget-config visitor of the same widget keeps minting."""
    from app.api.partner import public_bot_config, widget_config

    with _mint_stubs():
        hammerer = _make_request(caller_ip="198.51.100.9")
        for n in range(20):
            response = await public_bot_config(id=WIDGET_ID, request=hammerer, db=_make_db_mock())
            assert response.status_code == 200, f"share-link mint {n + 1} from one client got {response.status_code}"
        response = await public_bot_config(id=WIDGET_ID, request=hammerer, db=_make_db_mock())
        assert response.status_code == 429
        embed_visitor = _make_request(caller_ip="198.51.100.11")
        visitor_response = await widget_config(id=WIDGET_ID, request=embed_visitor, db=_make_db_mock())
        assert visitor_response.status_code == 200


# ---------------------------------------------------------------------------
# Observability: a 429 says which limit fired; raw visitor IPs are not logged
# (widget-audit privacy convention: visitor IPs go out as hash_audit_value).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_per_client_429_logs_limit_and_hashed_ip_without_raw_ip(fake_redis):
    from app.api.partner import widget_config

    caller_ip = "198.51.100.42"
    with _mint_stubs(), patch("app.api.partner.logger") as mock_logger:
        request = _make_request(caller_ip=caller_ip)
        for _ in range(20):
            await widget_config(id=WIDGET_ID, request=request, db=_make_db_mock())
        response = await widget_config(id=WIDGET_ID, request=request, db=_make_db_mock())

    assert response.status_code == 429
    events = [c for c in mock_logger.warning.call_args_list if c.args and c.args[0] == "widget_mint_rate_limited"]
    assert len(events) == 1
    kw = events[0].kwargs
    assert kw["limit"] == "client"
    assert kw["widget_id"] == WIDGET_ID
    assert kw["ip_hash"]
    assert caller_ip not in str(events)


@pytest.mark.asyncio
async def test_widget_ceiling_429_logs_widget_limit():
    """Per-widget ceiling rejection names limit="widget" with the widget_id."""
    from app.api.partner import widget_config

    db = _make_db_mock()
    request = _make_request()

    with (
        patch("app.api.partner.settings") as mock_settings,
        patch("app.api.partner.get_redis_pool") as mock_get_redis,
        patch("app.api.partner.check_rate_limit", new_callable=AsyncMock) as mock_rl,
        patch("app.api.partner.logger") as mock_logger,
        patch("app.api.partner.set_tenant", new_callable=AsyncMock),
    ):
        mock_settings.widget_jwt_secret = "test-secret"
        mock_get_redis.return_value = AsyncMock()
        # per-IP layer passes, per-widget ceiling rejects
        mock_rl.side_effect = [(True, 0), (False, 7)]

        response = await widget_config(id=WIDGET_ID, request=request, db=db)

    assert response.status_code == 429
    assert response.headers["Retry-After"] == "7"
    events = [c for c in mock_logger.warning.call_args_list if c.args and c.args[0] == "widget_mint_rate_limited"]
    assert len(events) == 1
    assert events[0].kwargs["limit"] == "widget"
    assert events[0].kwargs["widget_id"] == WIDGET_ID
    # Review finding 5: the widget ceiling carries ip_hash like the client event.
    assert events[0].kwargs["ip_hash"]
    assert request.client.host not in str(events)


# ---------------------------------------------------------------------------
# AC7.2: 429 returned on widget-config when a rate limit fires
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_widget_config_429_when_client_limit_fires():
    """widget-config returns 429 with Retry-After when a limit fires (AC7.2).

    The per-client bucket is checked first, so the first rejection is the
    client layer; Retry-After must still propagate."""
    from app.api.partner import widget_config

    db = _make_db_mock()
    request = _make_request()

    with (
        patch("app.api.partner.settings") as mock_settings,
        patch("app.api.partner.get_redis_pool") as mock_get_redis,
        patch("app.api.partner.check_rate_limit", new_callable=AsyncMock) as mock_rl,
        patch("app.api.partner.logger"),
        patch("app.api.partner.set_tenant", new_callable=AsyncMock),
        patch("app.api.partner.generate_session_token", return_value="tok"),
    ):
        mock_settings.widget_jwt_secret = "test-secret"
        mock_redis = AsyncMock()
        mock_get_redis.return_value = mock_redis
        # Simulate rate limit exceeded: allowed=False, retry_after=45
        mock_rl.return_value = (False, 45)

        response = await widget_config(id=WIDGET_ID, request=request, db=db)

    assert response.status_code == 429
    assert "Retry-After" in response.headers
    assert response.headers["Retry-After"] == "45"
    # A browser on the customer's site must be able to read the 429: without
    # the origin echo it only sees an opaque CORS error and never the
    # Retry-After (help.voys.nl, 2026-09-10).
    assert response.headers["Access-Control-Allow-Origin"] == "https://example.com"
    assert response.headers["Access-Control-Expose-Headers"] == "Retry-After"
    assert "access-control-allow-credentials" not in {k.lower() for k in response.headers}


@pytest.mark.asyncio
async def test_public_bot_config_429_when_rate_limit_fires():
    """public-bot-config returns 429 with Retry-After when rate limit exceeded (AC7.2)."""
    from app.api.partner import public_bot_config

    db = _make_db_mock()
    request = _make_request()

    with (
        patch("app.api.partner.settings") as mock_settings,
        patch("app.api.partner.get_redis_pool") as mock_get_redis,
        patch("app.api.partner.check_rate_limit", new_callable=AsyncMock) as mock_rl,
        patch("app.api.partner.logger"),
        patch("app.api.partner.set_tenant", new_callable=AsyncMock),
        patch("app.api.partner.generate_session_token", return_value="tok"),
    ):
        mock_settings.widget_jwt_secret = "test-secret"
        mock_redis = AsyncMock()
        mock_get_redis.return_value = mock_redis
        # Simulate rate limit exceeded (first layer checked: per client)
        mock_rl.return_value = (False, 30)

        response = await public_bot_config(id=WIDGET_ID, request=request, db=db)

    assert response.status_code == 429
    assert "Retry-After" in response.headers
    assert response.headers["Retry-After"] == "30"


@pytest.mark.asyncio
async def test_429_includes_retry_after_header():
    """429 response always includes Retry-After header with positive seconds (AC7.2)."""
    from app.api.partner import widget_config

    db = _make_db_mock()
    request = _make_request()

    with (
        patch("app.api.partner.settings") as mock_settings,
        patch("app.api.partner.get_redis_pool") as mock_get_redis,
        patch("app.api.partner.check_rate_limit", new_callable=AsyncMock) as mock_rl,
        patch("app.api.partner.logger"),
        patch("app.api.partner.set_tenant", new_callable=AsyncMock),
    ):
        mock_settings.widget_jwt_secret = "test-secret"
        mock_redis = AsyncMock()
        mock_get_redis.return_value = mock_redis
        mock_rl.return_value = (False, 60)

        response = await widget_config(id=WIDGET_ID, request=request, db=db)

    assert response.status_code == 429
    retry_after = int(response.headers["Retry-After"])
    assert retry_after > 0


# ---------------------------------------------------------------------------
# AC7.1: 200 returned when within limit
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_widget_config_200_within_limit():
    """widget-config returns 200 when rate limit is not exceeded (AC7.1)."""
    from app.api.partner import widget_config

    db = _make_db_mock()
    request = _make_request()

    with (
        patch("app.api.partner.settings") as mock_settings,
        patch("app.api.partner.get_redis_pool") as mock_get_redis,
        patch("app.api.partner.check_rate_limit", new_callable=AsyncMock) as mock_rl,
        patch("app.api.partner.set_tenant", new_callable=AsyncMock),
        patch("app.api.partner.generate_session_token", return_value="tok"),
        patch("app.api.partner.assert_platform_unlocked"),
    ):
        mock_settings.widget_jwt_secret = "test-secret"
        mock_redis = AsyncMock()
        mock_get_redis.return_value = mock_redis
        mock_rl.return_value = (True, 0)

        response = await widget_config(id=WIDGET_ID, request=request, db=db)

    assert response.status_code == 200


# ---------------------------------------------------------------------------
# AC7.3: Separate widgets have independent rate limit keys
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rate_limit_keys_scope_widget_and_client():
    """The per-widget ceiling key AND the per-client key (widget + caller IP) are
    both used, and the client layer is checked first (AC7.3)."""
    from app.api.partner import widget_config

    widget_id = WIDGET_ID
    db = _make_db_mock()
    request = _make_request(caller_ip="203.0.113.5")

    with (
        patch("app.api.partner.settings") as mock_settings,
        patch("app.api.partner.get_redis_pool") as mock_get_redis,
        patch("app.api.partner.check_rate_limit", new_callable=AsyncMock) as mock_rl,
        patch("app.api.partner.set_tenant", new_callable=AsyncMock),
        patch("app.api.partner.generate_session_token", return_value="tok"),
        patch("app.api.partner.assert_platform_unlocked"),
    ):
        mock_settings.widget_jwt_secret = "test-secret"
        mock_redis = AsyncMock()
        mock_get_redis.return_value = mock_redis
        mock_rl.return_value = (True, 0)

        await widget_config(id=widget_id, request=request, db=db)

    keys = [c.args[1] for c in mock_rl.call_args_list]
    assert keys[0] == f"widget_mint_ip:{widget_id}:203.0.113.5"
    assert keys[1] == f"widget_mint:{widget_id}"


@pytest.mark.asyncio
async def test_widget_ceiling_is_120_per_minute():
    """Per-widget ceiling is 120/min (raised from 10 for REQ-7/B-4: 10/min was a
    traffic cap on legitimate visitors, 120/min is the abuse backstop)."""
    from app.api.partner import widget_config

    widget_id = WIDGET_ID
    db = _make_db_mock()
    request = _make_request()

    with (
        patch("app.api.partner.settings") as mock_settings,
        patch("app.api.partner.get_redis_pool") as mock_get_redis,
        patch("app.api.partner.check_rate_limit", new_callable=AsyncMock) as mock_rl,
        patch("app.api.partner.set_tenant", new_callable=AsyncMock),
        patch("app.api.partner.generate_session_token", return_value="tok"),
        patch("app.api.partner.assert_platform_unlocked"),
    ):
        mock_settings.widget_jwt_secret = "test-secret"
        mock_redis = AsyncMock()
        mock_get_redis.return_value = mock_redis
        mock_rl.return_value = (True, 0)

        await widget_config(id=widget_id, request=request, db=db)

    calls = {c.args[1]: c for c in mock_rl.call_args_list}
    assert calls[f"widget_mint:{widget_id}"].kwargs.get("limit_per_minute") == 120
    assert calls[f"widget_mint_ip:{widget_id}:203.0.113.5"].kwargs.get("limit_per_minute") == 20
