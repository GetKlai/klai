"""RED: Tests for SPEC-WIDGET-001 Task 2 - Widget bootstrap endpoint.

Tests cover:
- GET /partner/v1/widget-config: public endpoint returning JWT session token
- Origin validation (exact match on scheme+host+port)
- JWT session token generation and validation
- CORS headers on widget-config endpoint
- Chat endpoint with session token auth
- Error cases: missing id, unknown wgt_id, wrong integration_type, bad origin, empty origins
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import jwt
import pytest

# ---------------------------------------------------------------------------
# Fakes & helpers
# ---------------------------------------------------------------------------


@dataclass
class FakeWidgetKey:
    """Simulates a PartnerAPIKey row with widget integration type."""

    id: str = "wgt-key-uuid-1"
    org_id: int = 42
    name: str = "Widget Key"
    key_hash: str = "hash-abc"
    permissions: dict = field(default_factory=lambda: {"chat": True, "feedback": True, "knowledge_append": False})
    rate_limit_rpm: int = 60
    active: bool = True
    integration_type: str = "widget"
    widget_id: str = "wgt_abcdef1234567890abcdef1234567890abcdef12"
    widget_config: dict = field(
        default_factory=lambda: {
            "allowed_origins": ["https://example.com"],
            "title": "Chat",
            "welcome_message": "Hello!",
            "css_variables": {},
        }
    )
    last_used_at: datetime | None = None
    created_at: datetime = field(default_factory=lambda: datetime(2026, 1, 1, tzinfo=UTC))
    created_by: str = "test-user"


@dataclass
class FakeApiKey:
    """Simulates a PartnerAPIKey row with API (non-widget) integration type."""

    id: str = "api-key-uuid-1"
    org_id: int = 42
    name: str = "API Key"
    key_hash: str = "hash-def"
    permissions: dict = field(default_factory=lambda: {"chat": True})
    rate_limit_rpm: int = 60
    active: bool = True
    integration_type: str = "api"
    widget_id: str | None = None
    widget_config: dict | None = None
    last_used_at: datetime | None = None
    created_at: datetime = field(default_factory=lambda: datetime(2026, 1, 1, tzinfo=UTC))
    created_by: str = "test-user"


@dataclass
class FakeOrg:
    id: int = 42
    zitadel_org_id: str = "zit-org-42"


_WIDGET_JWT_SECRET = "test-widget-secret-for-unit-tests-32-bytes"


def _make_request(origin: str | None = "https://example.com") -> MagicMock:
    """Create a mock Request with optional Origin header."""
    request = MagicMock()
    if origin:
        request.headers = {"origin": origin}
    else:
        request.headers = {}
    return request


# ---------------------------------------------------------------------------
# widget_auth service: generate_session_token
# ---------------------------------------------------------------------------


def test_generate_session_token_returns_jwt():
    """SPEC-WIDGET-001: generate_session_token() returns a valid HS256 JWT."""
    from app.services.widget_auth import generate_session_token

    token = generate_session_token(
        wgt_id="wgt_abc123",
        org_id=42,
        kb_ids=[1, 2],
        secret=_WIDGET_JWT_SECRET,
    )

    assert isinstance(token, str)
    payload = jwt.decode(token, _WIDGET_JWT_SECRET, algorithms=["HS256"])
    assert payload["wgt_id"] == "wgt_abc123"
    assert payload["org_id"] == 42
    assert payload["kb_ids"] == [1, 2]
    assert "exp" in payload


def test_generate_session_token_ttl_is_one_hour():
    """SPEC-WIDGET-001: session token expires in 1 hour."""
    from app.services.widget_auth import generate_session_token

    before = int(time.time())
    token = generate_session_token(
        wgt_id="wgt_abc123",
        org_id=42,
        kb_ids=[],
        secret=_WIDGET_JWT_SECRET,
    )
    after = int(time.time())

    payload = jwt.decode(token, _WIDGET_JWT_SECRET, algorithms=["HS256"])
    exp = payload["exp"]

    assert before + 3600 <= exp <= after + 3600


def test_generate_session_token_uses_hs256():
    """SPEC-WIDGET-001: session token is signed with HS256."""
    from app.services.widget_auth import generate_session_token

    token = generate_session_token(
        wgt_id="wgt_abc123",
        org_id=42,
        kb_ids=[],
        secret=_WIDGET_JWT_SECRET,
    )

    header = jwt.get_unverified_header(token)
    assert header["alg"] == "HS256"


def test_expired_session_token_raises_on_decode():
    """SPEC-WIDGET-001: expired token cannot be decoded."""
    # Create a token that expired 10 seconds ago
    payload = {
        "wgt_id": "wgt_abc123",
        "org_id": 42,
        "kb_ids": [],
        "exp": int(time.time()) - 10,
    }
    expired_token = jwt.encode(payload, _WIDGET_JWT_SECRET, algorithm="HS256")

    with pytest.raises(jwt.ExpiredSignatureError):
        jwt.decode(expired_token, _WIDGET_JWT_SECRET, algorithms=["HS256"])


# ---------------------------------------------------------------------------
# origin_allowed helper
# ---------------------------------------------------------------------------


def test_origin_allowed_exact_match():
    """SPEC-WIDGET-001: exact match on scheme+host+port returns True."""
    from app.services.widget_auth import origin_allowed

    assert origin_allowed("https://example.com", ["https://example.com"]) is True


def test_origin_allowed_different_scheme():
    """SPEC-WIDGET-001: http vs https are different origins."""
    from app.services.widget_auth import origin_allowed

    assert origin_allowed("http://example.com", ["https://example.com"]) is False


def test_origin_allowed_different_subdomain():
    """SPEC-WIDGET-001: subdomains are different origins."""
    from app.services.widget_auth import origin_allowed

    assert origin_allowed("https://sub.example.com", ["https://example.com"]) is False


def test_origin_allowed_different_port():
    """SPEC-WIDGET-001: different port is a different origin."""
    from app.services.widget_auth import origin_allowed

    assert origin_allowed("https://example.com:8080", ["https://example.com"]) is False


def test_origin_allowed_empty_list():
    """SPEC-WIDGET-001: empty allowed_origins list returns False (fail-closed)."""
    from app.services.widget_auth import origin_allowed

    assert origin_allowed("https://example.com", []) is False


def test_origin_allowed_multiple_origins():
    """SPEC-WIDGET-001: origin matches if in the allowed list."""
    from app.services.widget_auth import origin_allowed

    allowed = ["https://example.com", "https://other.com"]
    assert origin_allowed("https://other.com", allowed) is True
    assert origin_allowed("https://evil.com", allowed) is False


# ---------------------------------------------------------------------------
# GET /partner/v1/widget-config — success case
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_widget_config_success():
    """SPEC-WIDGET-001: valid wgt_id + valid origin returns 200 with session token."""
    from app.api.partner import widget_config

    fake_key = FakeWidgetKey()
    fake_org = FakeOrg()
    db = AsyncMock()
    db.add = MagicMock()

    from tests.helpers import FakeResult

    db.execute = AsyncMock(
        side_effect=[
            FakeResult(rows=[fake_key]),  # widget key lookup
            FakeResult(rows=[]),  # kb access rows
            FakeResult(rows=[fake_org]),  # org lookup
        ]
    )

    request = _make_request("https://example.com")

    with (
        patch("app.api.partner.settings") as mock_settings,
        patch("app.services.widget_auth.generate_session_token", return_value="test-jwt-token"),
    ):
        mock_settings.widget_jwt_secret = _WIDGET_JWT_SECRET

        response = await widget_config(id=fake_key.widget_id, request=request, db=db)

    import json

    body = json.loads(response.body)
    assert response.status_code == 200
    assert "session_token" in body
    assert "session_expires_at" in body
    assert "title" in body
    assert "welcome_message" in body
    assert "chat_endpoint" in body
    # pk_live_ must never appear in widget response
    assert "pk_live_" not in str(body)


@pytest.mark.asyncio
async def test_widget_config_returns_cors_header_for_valid_origin():
    """SPEC-WIDGET-001: valid origin gets Access-Control-Allow-Origin header."""
    from app.api.partner import widget_config

    fake_key = FakeWidgetKey()
    fake_org = FakeOrg()
    db = AsyncMock()
    db.add = MagicMock()

    from tests.helpers import FakeResult

    db.execute = AsyncMock(
        side_effect=[
            FakeResult(rows=[fake_key]),
            FakeResult(rows=[]),
            FakeResult(rows=[fake_org]),
        ]
    )

    request = _make_request("https://example.com")

    with (
        patch("app.api.partner.settings") as mock_settings,
        patch("app.services.widget_auth.generate_session_token", return_value="test-jwt-token"),
    ):
        mock_settings.widget_jwt_secret = _WIDGET_JWT_SECRET

        response = await widget_config(id=fake_key.widget_id, request=request, db=db)

    assert response.status_code == 200
    assert response.headers.get("access-control-allow-origin") == "https://example.com"


# ---------------------------------------------------------------------------
# GET /partner/v1/widget-config — error cases
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_widget_config_missing_id_returns_422():
    """SPEC-WIDGET-001: missing id query param returns 422 via FastAPI validation."""
    # This is enforced by FastAPI query param validation — no id param = 422
    # We verify the route signature requires id (not Optional)
    import inspect

    from app.api.partner import widget_config

    sig = inspect.signature(widget_config)
    assert "id" in sig.parameters
    param = sig.parameters["id"]
    # id has no default = required
    assert param.default is inspect.Parameter.empty


@pytest.mark.asyncio
async def test_widget_config_unknown_wgt_id_returns_404():
    """SPEC-WIDGET-001: unknown wgt_id returns 404."""
    from fastapi import HTTPException

    from app.api.partner import widget_config

    db = AsyncMock()
    db.add = MagicMock()

    from tests.helpers import FakeResult

    db.execute = AsyncMock(return_value=FakeResult(rows=[]))

    request = _make_request("https://example.com")

    with (
        patch("app.api.partner.settings") as mock_settings,
    ):
        mock_settings.widget_jwt_secret = _WIDGET_JWT_SECRET

        with pytest.raises(HTTPException) as exc:
            await widget_config(id="wgt_nonexistent", request=request, db=db)

    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_widget_config_api_type_returns_404():
    """SPEC-WIDGET-001: api-type key lookup returns 404 (query filters on integration_type='widget')."""
    from fastapi import HTTPException

    from app.api.partner import widget_config

    db = AsyncMock()
    db.add = MagicMock()

    from tests.helpers import FakeResult

    # DB lookup finds nothing because query filters on integration_type='widget'
    db.execute = AsyncMock(return_value=FakeResult(rows=[]))

    request = _make_request("https://example.com")

    with patch("app.api.partner.settings") as mock_settings:
        mock_settings.widget_jwt_secret = _WIDGET_JWT_SECRET

        with pytest.raises(HTTPException) as exc:
            await widget_config(id="wgt_abcdef", request=request, db=db)

    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_widget_config_missing_origin_returns_403():
    """SPEC-WIDGET-001: missing Origin header returns 403."""
    import json

    from app.api.partner import widget_config

    fake_key = FakeWidgetKey()
    db = AsyncMock()
    db.add = MagicMock()

    from tests.helpers import FakeResult

    db.execute = AsyncMock(return_value=FakeResult(rows=[fake_key]))

    request = _make_request(origin=None)  # no Origin header

    with patch("app.api.partner.settings") as mock_settings:
        mock_settings.widget_jwt_secret = _WIDGET_JWT_SECRET

        response = await widget_config(id=fake_key.widget_id, request=request, db=db)

    assert response.status_code == 403
    assert json.loads(response.body)["detail"] == "Origin not allowed"


@pytest.mark.asyncio
async def test_widget_config_invalid_origin_returns_403():
    """SPEC-WIDGET-001: origin not in allowed_origins returns 403."""
    import json

    from app.api.partner import widget_config

    fake_key = FakeWidgetKey()
    db = AsyncMock()
    db.add = MagicMock()

    from tests.helpers import FakeResult

    db.execute = AsyncMock(return_value=FakeResult(rows=[fake_key]))

    request = _make_request("https://evil.com")

    with patch("app.api.partner.settings") as mock_settings:
        mock_settings.widget_jwt_secret = _WIDGET_JWT_SECRET

        response = await widget_config(id=fake_key.widget_id, request=request, db=db)

    assert response.status_code == 403
    assert json.loads(response.body)["detail"] == "Origin not allowed"


@pytest.mark.asyncio
async def test_widget_config_empty_allowed_origins_returns_403():
    """SPEC-WIDGET-001: empty allowed_origins list returns 403 (fail-closed)."""
    from app.api.partner import widget_config

    fake_key = FakeWidgetKey()
    fake_key.widget_config = {
        "allowed_origins": [],
        "title": "Chat",
        "welcome_message": "Hello",
        "css_variables": {},
    }
    db = AsyncMock()
    db.add = MagicMock()

    from tests.helpers import FakeResult

    db.execute = AsyncMock(return_value=FakeResult(rows=[fake_key]))

    request = _make_request("https://example.com")

    with patch("app.api.partner.settings") as mock_settings:
        mock_settings.widget_jwt_secret = _WIDGET_JWT_SECRET

        response = await widget_config(id=fake_key.widget_id, request=request, db=db)

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_widget_config_no_jwt_secret_returns_503():
    """SPEC-WIDGET-001: missing WIDGET_JWT_SECRET returns 503."""
    from app.api.partner import widget_config

    db = AsyncMock()
    db.add = MagicMock()
    request = _make_request("https://example.com")

    with patch("app.api.partner.settings") as mock_settings:
        mock_settings.widget_jwt_secret = ""  # empty = not configured

        response = await widget_config(id="wgt_anything", request=request, db=db)

    assert response.status_code == 503


# ---------------------------------------------------------------------------
# OPTIONS preflight
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_widget_config_options_preflight_returns_204():
    """SPEC-WIDGET-001: OPTIONS preflight returns 204 with CORS headers."""
    from app.api.partner import widget_config_preflight

    fake_key = FakeWidgetKey()
    db = AsyncMock()
    db.add = MagicMock()

    from tests.helpers import FakeResult

    db.execute = AsyncMock(return_value=FakeResult(rows=[fake_key]))

    request = _make_request("https://example.com")

    with patch("app.api.partner.settings"):
        response = await widget_config_preflight(id=fake_key.widget_id, request=request, db=db)

    assert response.status_code == 204
    assert "access-control-allow-origin" in response.headers


# ---------------------------------------------------------------------------
# Chat endpoint with session token auth
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_chat_endpoint_accepts_session_token():
    """SPEC-WIDGET-001: chat endpoint accepts valid JWT session token."""
    from app.api.partner_dependencies import get_partner_key

    # Build a valid session token
    payload = {
        "wgt_id": "wgt_abcdef1234567890abcdef1234567890abcdef12",
        "org_id": 42,
        "kb_ids": [1, 2],
        "exp": int(time.time()) + 3600,
    }
    token = jwt.encode(payload, _WIDGET_JWT_SECRET, algorithm="HS256")

    request = MagicMock()
    request.headers = {"authorization": f"Bearer {token}"}

    fake_org = FakeOrg()
    db = AsyncMock()
    db.add = MagicMock()

    from tests.helpers import FakeResult

    db.execute = AsyncMock(return_value=FakeResult(rows=[fake_org]))

    with (
        patch("app.api.partner_dependencies.set_tenant", new_callable=AsyncMock),
        patch("app.api.partner_dependencies.settings") as mock_settings,
    ):
        mock_settings.widget_jwt_secret = _WIDGET_JWT_SECRET

        auth = await get_partner_key(request=request, db=db)

    assert auth.org_id == 42
    assert auth.kb_access == {1: "read", 2: "read"}


@pytest.mark.asyncio
async def test_chat_endpoint_rejects_expired_session_token():
    """SPEC-WIDGET-001: chat endpoint rejects expired JWT session token with 401."""
    from fastapi import HTTPException

    from app.api.partner_dependencies import get_partner_key

    payload = {
        "wgt_id": "wgt_abcdef1234567890abcdef1234567890abcdef12",
        "org_id": 42,
        "kb_ids": [1, 2],
        "exp": int(time.time()) - 10,  # expired
    }
    token = jwt.encode(payload, _WIDGET_JWT_SECRET, algorithm="HS256")

    request = MagicMock()
    request.headers = {"authorization": f"Bearer {token}"}

    db = AsyncMock()

    with patch("app.api.partner_dependencies.settings") as mock_settings:
        mock_settings.widget_jwt_secret = _WIDGET_JWT_SECRET

        with pytest.raises(HTTPException) as exc:
            await get_partner_key(request=request, db=db)

    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_chat_endpoint_rejects_invalid_jwt_secret():
    """SPEC-WIDGET-001: chat endpoint rejects JWT signed with wrong secret."""
    from fastapi import HTTPException

    from app.api.partner_dependencies import get_partner_key

    payload = {
        "wgt_id": "wgt_abcdef1234567890abcdef1234567890abcdef12",
        "org_id": 42,
        "kb_ids": [],
        "exp": int(time.time()) + 3600,
    }
    token = jwt.encode(payload, "wrong-secret-that-is-long-enough-for-hmac", algorithm="HS256")

    request = MagicMock()
    request.headers = {"authorization": f"Bearer {token}"}

    db = AsyncMock()

    with patch("app.api.partner_dependencies.settings") as mock_settings:
        mock_settings.widget_jwt_secret = _WIDGET_JWT_SECRET

        with pytest.raises(HTTPException) as exc:
            await get_partner_key(request=request, db=db)

    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_existing_pk_live_auth_rejected_for_non_pk_live_non_jwt():
    """SPEC-WIDGET-001: non-pk_live_ non-JWT token returns 401."""
    from fastapi import HTTPException

    from app.api.partner_dependencies import get_partner_key

    # A bearer token that doesn't start with pk_live_ and isn't a valid JWT
    request = MagicMock()
    request.headers = {"authorization": "Bearer not-a-jwt-or-pk-live"}

    db = AsyncMock()

    with patch("app.api.partner_dependencies.settings") as mock_settings:
        mock_settings.widget_jwt_secret = _WIDGET_JWT_SECRET

        with pytest.raises(HTTPException) as exc:
            await get_partner_key(request=request, db=db)

    assert exc.value.status_code == 401
