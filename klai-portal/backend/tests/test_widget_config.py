"""Tests for widget-config endpoint — SPEC-WIDGET-002.

Covers the core flow:
- GET /partner/v1/widget-config with valid wgt_id + allowed origin → 200 + JWT
- Unknown wgt_id → 404
- Disallowed origin → 403
- Empty allowed_origins list → 200 (open by default; admin opt-in lockdown)
- Missing JWT secret in settings → 503
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.api.partner import widget_config


@dataclass
class FakeWidget:
    """Simulates a Widget row."""

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
            "system_prompt": "Use a friendly support tone.",
            "css_variables": {},
        }
    )
    allow_any_origin: bool = False
    public_share_enabled: bool = False
    rate_limit_rpm: int = 60
    last_used_at: datetime | None = None
    created_at: datetime = field(default_factory=lambda: datetime(2026, 1, 1, tzinfo=UTC))
    created_by: str = "test-user"


@dataclass
class FakeOrg:
    id: int = 42
    zitadel_org_id: str = "zitadel-org-123"
    # SPEC-SEC-HYGIENE-001 REQ-24.4: slug is read by partner.py to derive
    # the per-tenant widget JWT signing key (HKDF). Test patches
    # generate_session_token directly, so the actual value here is not
    # signature-relevant — but it must exist to satisfy the call site.
    slug: str = "test"
    # SPEC-SEC-CROSS-TENANT-FOLLOWUP-001 REQ-1 (Finding B-1): partner endpoints
    # call assert_platform_unlocked(org, "widgets"). Default the fake to
    # "widgets unlocked" so happy-path tests still pass; tests that exercise
    # the locked-tenant path override this field per-instance.
    platform_unlocked_features: list = field(default_factory=lambda: ["widgets"])


def _make_request(origin: str | None = "https://example.com") -> MagicMock:
    request = MagicMock()
    request.headers = {"origin": origin} if origin else {}
    request.query_params = {}
    return request


def _make_db_chain(widget: FakeWidget | None, org: FakeOrg | None, kb_ids: list[int]) -> AsyncMock:
    """Build an AsyncMock db that returns widget, org, then kb_access rows in sequence."""
    db = AsyncMock()

    widget_result = MagicMock()
    widget_result.scalar_one_or_none = MagicMock(return_value=widget)

    org_result = MagicMock()
    org_result.scalar_one_or_none = MagicMock(return_value=org)

    kb_result = MagicMock()
    kb_scalars = MagicMock()
    kb_rows = [MagicMock(kb_id=kb_id) for kb_id in kb_ids]
    kb_scalars.all = MagicMock(return_value=kb_rows)
    kb_result.scalars = MagicMock(return_value=kb_scalars)

    db.execute = AsyncMock(side_effect=[widget_result, org_result, kb_result])
    return db


@pytest.mark.asyncio
async def test_widget_config_happy_path():
    """Valid wgt_id + allowed origin returns 200 with session token."""
    widget = FakeWidget()
    org = FakeOrg()
    db = _make_db_chain(widget, org, [1, 2])
    request = _make_request("https://example.com")

    with (
        patch("app.api.partner.settings") as mock_settings,
        patch("app.api.partner.get_redis_pool"),
        patch("app.api.partner.check_rate_limit", new_callable=AsyncMock, return_value=(True, 0)),
        patch("app.api.partner.set_tenant", new=AsyncMock()),
        patch("app.api.partner.generate_session_token", return_value="fake.jwt.token"),
    ):
        mock_settings.widget_jwt_secret = "shared-secret"

        response = await widget_config(id=widget.widget_id, request=request, db=db)

    assert response.status_code == 200
    body = response.body.decode()
    assert '"session_token": "fake.jwt.token"' in body
    assert '"title": "Chat"' in body
    assert '"page_context_enabled": false' in body
    assert "system_prompt" not in body


@pytest.mark.asyncio
async def test_widget_config_returns_page_context_enabled():
    widget = FakeWidget()
    widget.widget_config["page_context_enabled"] = True
    org = FakeOrg()
    db = _make_db_chain(widget, org, [1])
    request = _make_request("https://example.com")

    with (
        patch("app.api.partner.settings") as mock_settings,
        patch("app.api.partner.get_redis_pool"),
        patch("app.api.partner.check_rate_limit", new_callable=AsyncMock, return_value=(True, 0)),
        patch("app.api.partner.set_tenant", new=AsyncMock()),
        patch("app.api.partner.generate_session_token", return_value="fake.jwt.token"),
    ):
        mock_settings.widget_jwt_secret = "shared-secret"

        response = await widget_config(id=widget.widget_id, request=request, db=db)

    assert json.loads(response.body.decode())["page_context_enabled"] is True


@pytest.mark.asyncio
async def test_widget_config_hubspot_handoff_visible_only_for_getklai_origin():
    widget = FakeWidget()
    widget.widget_config["allowed_origins"] = ["https://getklai.getklai.com"]
    widget.widget_config["integrations"] = {
        "hubspot": {
            "status": "connected",
            "channel_account_id": "3307400689",
        }
    }
    org = FakeOrg(slug="getklai")
    db = _make_db_chain(widget, org, [1])
    request = _make_request("https://getklai.getklai.com")

    with (
        patch("app.api.partner.settings") as mock_settings,
        patch("app.api.partner.get_redis_pool"),
        patch("app.api.partner.check_rate_limit", new_callable=AsyncMock, return_value=(True, 0)),
        patch("app.api.partner.set_tenant", new=AsyncMock()),
        patch("app.api.partner.generate_session_token", return_value="fake.jwt.token"),
    ):
        mock_settings.widget_jwt_secret = "shared-secret"

        response = await widget_config(id=widget.widget_id, request=request, db=db)

    assert json.loads(response.body.decode())["handoff"]["hubspot"]["enabled"] is True


@pytest.mark.asyncio
async def test_widget_config_hubspot_handoff_hidden_for_non_getklai_tenant():
    widget = FakeWidget()
    widget.widget_config["allowed_origins"] = ["https://getklai.getklai.com"]
    widget.widget_config["integrations"] = {
        "hubspot": {
            "status": "connected",
            "channel_account_id": "3307400689",
        }
    }
    org = FakeOrg(slug="voys")
    db = _make_db_chain(widget, org, [1])
    request = _make_request("https://getklai.getklai.com")

    with (
        patch("app.api.partner.settings") as mock_settings,
        patch("app.api.partner.get_redis_pool"),
        patch("app.api.partner.check_rate_limit", new_callable=AsyncMock, return_value=(True, 0)),
        patch("app.api.partner.set_tenant", new=AsyncMock()),
        patch("app.api.partner.generate_session_token", return_value="fake.jwt.token"),
    ):
        mock_settings.widget_jwt_secret = "shared-secret"

        response = await widget_config(id=widget.widget_id, request=request, db=db)

    assert json.loads(response.body.decode())["handoff"]["hubspot"]["enabled"] is False


@pytest.mark.asyncio
async def test_widget_config_passes_valid_client_session_id_to_token():
    widget = FakeWidget()
    org = FakeOrg()
    db = _make_db_chain(widget, org, [1])
    request = _make_request("https://example.com")
    request.headers["x-klai-widget-session-id"] = "client-session_1234567890"

    with (
        patch("app.api.partner.settings") as mock_settings,
        patch("app.api.partner.get_redis_pool"),
        patch("app.api.partner.check_rate_limit", new_callable=AsyncMock, return_value=(True, 0)),
        patch("app.api.partner.set_tenant", new=AsyncMock()),
        patch("app.api.partner.generate_session_token", return_value="fake.jwt.token") as generate_token,
    ):
        mock_settings.widget_jwt_secret = "shared-secret"

        await widget_config(id=widget.widget_id, request=request, db=db)

    assert generate_token.call_args.kwargs["session_id"] == "client-session_1234567890"


@pytest.mark.asyncio
async def test_widget_config_keeps_legacy_query_session_id_fallback():
    widget = FakeWidget()
    org = FakeOrg()
    db = _make_db_chain(widget, org, [1])
    request = _make_request("https://example.com")
    request.query_params = {"session_id": "client-session_legacy123"}

    with (
        patch("app.api.partner.settings") as mock_settings,
        patch("app.api.partner.get_redis_pool"),
        patch("app.api.partner.check_rate_limit", new_callable=AsyncMock, return_value=(True, 0)),
        patch("app.api.partner.set_tenant", new=AsyncMock()),
        patch("app.api.partner.generate_session_token", return_value="fake.jwt.token") as generate_token,
    ):
        mock_settings.widget_jwt_secret = "shared-secret"

        await widget_config(id=widget.widget_id, request=request, db=db)

    assert generate_token.call_args.kwargs["session_id"] == "client-session_legacy123"


@pytest.mark.asyncio
async def test_widget_config_missing_jwt_secret():
    """503 when WIDGET_JWT_SECRET is not configured."""
    db = AsyncMock()
    request = _make_request()

    with patch("app.api.partner.settings") as mock_settings:
        mock_settings.widget_jwt_secret = ""
        response = await widget_config(id="wgt_any", request=request, db=db)

    assert response.status_code == 503


@pytest.mark.asyncio
async def test_widget_config_unknown_widget_id():
    """404 when the wgt_id does not exist in widgets table."""
    db = _make_db_chain(None, None, [])
    request = _make_request()

    with (
        patch("app.api.partner.settings") as mock_settings,
        patch("app.api.partner.get_redis_pool"),
        patch("app.api.partner.check_rate_limit", new_callable=AsyncMock, return_value=(True, 0)),
    ):
        mock_settings.widget_jwt_secret = "shared-secret"
        with pytest.raises(Exception) as exc_info:
            await widget_config(id="wgt_does_not_exist", request=request, db=db)

    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_widget_config_disallowed_origin():
    """403 when the Origin header is not in allowed_origins."""
    widget = FakeWidget()
    db = _make_db_chain(widget, None, [])
    request = _make_request("https://evil.example.com")

    with (
        patch("app.api.partner.settings") as mock_settings,
        patch("app.api.partner.get_redis_pool"),
        patch("app.api.partner.check_rate_limit", new_callable=AsyncMock, return_value=(True, 0)),
    ):
        mock_settings.widget_jwt_secret = "shared-secret"
        response = await widget_config(id=widget.widget_id, request=request, db=db)

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_widget_config_empty_allowed_origins_denied_by_default():
    """403 when allowed_origins is empty AND allow_any_origin is False.

    SPEC-SEC-CROSS-TENANT-FOLLOWUP-001 REQ-2 (Finding B-2) flipped the
    default from open-to-the-world to deny. The previous behavior was a
    CRIT: any newly-created widget was embeddable on any phishing site.
    Admins opt into open-origin mode explicitly via allow_any_origin=True.
    """
    org = FakeOrg(slug="acme")
    widget = FakeWidget(
        widget_config={
            "allowed_origins": [],
            "title": "",
            "welcome_message": "",
            "system_prompt": "",
            "css_variables": {},
        },
        # allow_any_origin defaults to False — explicit for readability.
        allow_any_origin=False,
    )
    db = _make_db_chain(widget, org, [])
    request = _make_request("https://example.com")

    with (
        patch("app.api.partner.settings") as mock_settings,
        patch("app.api.partner.get_redis_pool"),
        patch("app.api.partner.check_rate_limit", new_callable=AsyncMock, return_value=(True, 0)),
        patch("app.api.partner.set_tenant", new=AsyncMock()),
        patch("app.api.partner.generate_session_token", return_value="fake.jwt.token"),
    ):
        mock_settings.widget_jwt_secret = "shared-secret"
        response = await widget_config(id=widget.widget_id, request=request, db=db)

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_widget_config_empty_allowed_origins_allowed_when_allow_any_origin_true():
    """200 when allowed_origins is empty AND allow_any_origin is True.

    Counterpart to test_widget_config_empty_allowed_origins_denied_by_default:
    the explicit opt-in restores the "load anywhere" behavior for widgets
    that legitimately need it (public chatbots embedded on third-party sites).
    """
    org = FakeOrg(slug="acme")
    widget = FakeWidget(
        widget_config={
            "allowed_origins": [],
            "title": "",
            "welcome_message": "",
            "system_prompt": "",
            "css_variables": {},
        },
        allow_any_origin=True,
    )
    db = _make_db_chain(widget, org, [])
    request = _make_request("https://example.com")

    with (
        patch("app.api.partner.settings") as mock_settings,
        patch("app.api.partner.get_redis_pool"),
        patch("app.api.partner.check_rate_limit", new_callable=AsyncMock, return_value=(True, 0)),
        patch("app.api.partner.set_tenant", new=AsyncMock()),
        patch("app.api.partner.generate_session_token", return_value="fake.jwt.token"),
    ):
        mock_settings.widget_jwt_secret = "shared-secret"
        response = await widget_config(id=widget.widget_id, request=request, db=db)

    assert response.status_code == 200


# ---------------------------------------------------------------------------
# origin_allowed unit tests (wildcard support)
# ---------------------------------------------------------------------------


def test_origin_allowed_exact_match():
    """Exact origin match works."""
    from app.services.widget_auth import origin_allowed

    assert origin_allowed("https://example.com", ["https://example.com"])
    assert not origin_allowed("https://evil.com", ["https://example.com"])


def test_origin_allowed_wildcard_subdomain():
    """Wildcard *.domain matches subdomains but not bare domain."""
    from app.services.widget_auth import origin_allowed

    assert origin_allowed("https://app.example.com", ["https://*.example.com"])
    assert origin_allowed("https://test.example.com", ["https://*.example.com"])
    assert not origin_allowed("https://example.com", ["https://*.example.com"])
    assert not origin_allowed("https://evil-example.com", ["https://*.example.com"])
    assert not origin_allowed("https://example.com.evil.test", ["https://*.example.com"])
    assert not origin_allowed("https://evil.com", ["https://*.example.com"])


def test_origin_allowed_combined():
    """Exact + wildcard together cover bare domain and all subdomains."""
    from app.services.widget_auth import origin_allowed

    origins = ["https://getklai.com", "https://*.getklai.com"]
    assert origin_allowed("https://getklai.com", origins)
    assert origin_allowed("https://voys.getklai.com", origins)
    assert origin_allowed("https://test.getklai.com", origins)
    assert not origin_allowed("https://evil.com", origins)


def test_origin_allowed_empty_list_denied_by_default():
    """Empty list returns False unless allow_any_origin=True.

    SPEC-SEC-CROSS-TENANT-FOLLOWUP-001 REQ-2 (Finding B-2): the pre-2026-05-24
    behavior (empty = open) was a CRIT — any new widget was a CSRF/exfil target
    on phishing sites. Default is now deny; admins must opt in via the
    allow_any_origin kwarg (backed by the widgets.allow_any_origin column).
    """
    from app.services.widget_auth import origin_allowed

    assert not origin_allowed("https://example.com", [])
    assert not origin_allowed("https://anything.else.com", [])
    # Explicit opt-in flips the default back for legitimate use cases.
    assert origin_allowed("https://example.com", [], allow_any_origin=True)
    assert origin_allowed("https://anything.else.com", [], allow_any_origin=True)


def test_origin_allowed_trailing_slash():
    """Trailing slashes are stripped before comparison."""
    from app.services.widget_auth import origin_allowed

    assert origin_allowed("https://example.com/", ["https://example.com"])
    assert origin_allowed("https://example.com", ["https://example.com/"])


def test_origin_allowed_rejects_scheme_and_port_mismatch():
    """Origin matching must preserve scheme and explicit port boundaries."""
    from app.services.widget_auth import origin_allowed

    assert not origin_allowed("http://app.example.com", ["https://*.example.com"])
    assert origin_allowed("https://app.example.com:8443", ["https://*.example.com:8443"])
    assert not origin_allowed("https://app.example.com:9443", ["https://*.example.com:8443"])


@pytest.mark.asyncio
async def test_public_bot_config_rejects_when_share_disabled():
    """Public bot config is off by default, even if the widget exists."""
    from app.api.partner import public_bot_config

    widget = FakeWidget()
    db = _make_db_chain(widget, None, [])

    with (
        patch("app.api.partner.settings") as mock_settings,
        patch("app.api.partner.get_redis_pool"),
        patch("app.api.partner.check_rate_limit", new_callable=AsyncMock, return_value=(True, 0)),
    ):
        mock_settings.widget_jwt_secret = "shared-secret"
        with pytest.raises(Exception) as exc_info:
            await public_bot_config(id=widget.widget_id, db=db)

    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_public_bot_config_returns_token_when_share_enabled():
    """Public bot config returns a session token only after explicit enablement."""
    from app.api.partner import public_bot_config

    org = FakeOrg()
    widget = FakeWidget(
        public_share_enabled=True,
        widget_config={
            "allowed_origins": [],
            "title": "Public",
            "welcome_message": "",
            "system_prompt": "",
            "css_variables": {},
        },
    )
    db = _make_db_chain(widget, org, [10])

    with (
        patch("app.api.partner.settings") as mock_settings,
        patch("app.api.partner.get_redis_pool"),
        patch("app.api.partner.check_rate_limit", new_callable=AsyncMock, return_value=(True, 0)),
        patch("app.api.partner.set_tenant", new=AsyncMock()),
        patch("app.api.partner.generate_session_token", return_value="public.jwt.token"),
    ):
        mock_settings.widget_jwt_secret = "shared-secret"
        response = await public_bot_config(id=widget.widget_id, db=db)

    assert response.status_code == 200
    assert '"session_token": "public.jwt.token"' in response.body.decode()
