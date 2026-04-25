"""Partner CORS tests — SPEC-SEC-CORS-001 REQ-2, REQ-3.

Tests AC-9, AC-10, AC-11.

Strategy: test via the actual partner.py handler functions (same approach as
test_widget_config.py) rather than a full ASGI integration. This avoids the
Redis + DB setup required for a full TestClient run while still exercising the
CORS header logic in the handlers and the PartnerCORSMiddleware.

AC-11 tests the get_partner_key dependency directly to confirm it rejects
cookie-only requests.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.api.partner import widget_config, widget_config_preflight

# ---------------------------------------------------------------------------
# Shared fixtures (mirror test_widget_config.py)
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
            "allowed_origins": ["https://customer.example"],
            "title": "Chat",
            "welcome_message": "Hello!",
            "css_variables": {},
        }
    )
    rate_limit_rpm: int = 60
    last_used_at: datetime | None = None
    created_at: datetime = field(default_factory=lambda: datetime(2026, 1, 1, tzinfo=UTC))
    created_by: str = "test-user"


@dataclass
class FakeOrg:
    id: int = 42
    zitadel_org_id: str = "zitadel-org-123"


def _make_request(
    origin: str | None = "https://customer.example",
    headers_extra: dict[str, str] | None = None,
) -> MagicMock:
    req = MagicMock()
    h: dict[str, str] = {}
    if origin is not None:
        h["origin"] = origin
    if headers_extra:
        h.update(headers_extra)
    req.headers = h
    return req


def _make_db_chain(
    widget: FakeWidget | None, org: FakeOrg | None, kb_ids: list[int]
) -> AsyncMock:
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


# ---------------------------------------------------------------------------
# AC-9: Widget origin allowed WITHOUT Access-Control-Allow-Credentials
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_partner_cors_widget_origin_no_credentials() -> None:
    """AC-9: GET /partner/v1/widget-config from customer.example echoes ACAO
    but MUST NOT include Access-Control-Allow-Credentials: true.

    REQ-2.2 — widget traffic never uses BFF cookies; credentials mode is 'omit'.
    """
    widget = FakeWidget()
    org = FakeOrg()
    db = _make_db_chain(widget, org, [1, 2])
    request = _make_request(origin="https://customer.example")

    with (
        patch("app.api.partner.settings") as mock_settings,
        patch("app.api.partner.set_tenant", new=AsyncMock()),
        patch("app.api.partner.generate_session_token", return_value="fake.jwt.token"),
    ):
        mock_settings.widget_jwt_secret = "shared-secret"

        response = await widget_config(id=widget.widget_id, request=request, db=db)

    assert response.status_code == 200

    acao = response.headers.get("access-control-allow-origin", "")
    acac = response.headers.get("access-control-allow-credentials", "")
    vary = response.headers.get("vary", "")

    assert acao == "https://customer.example", (
        f"ACAO must echo https://customer.example, got {acao!r} (AC-9)"
    )
    assert acac.lower() != "true", (
        f"ACAC must NOT be true for widget endpoint, got {acac!r} (AC-9 / REQ-2.2)"
    )
    assert "origin" in vary.lower(), (
        f"Vary must include Origin for cache correctness, got {vary!r} (AC-9 / REQ-2.3)"
    )


@pytest.mark.asyncio
async def test_partner_cors_preflight_no_credentials() -> None:
    """AC-9 preflight: OPTIONS /partner/v1/widget-config from customer.example
    echoes ACAO but NOT ACAC.

    REQ-2.2 — preflight handler must not set Access-Control-Allow-Credentials.
    """
    widget = FakeWidget()
    db = AsyncMock()

    widget_result = MagicMock()
    widget_result.scalar_one_or_none = MagicMock(return_value=widget)
    db.execute = AsyncMock(return_value=widget_result)

    request = _make_request(origin="https://customer.example")

    response = await widget_config_preflight(
        id=widget.widget_id, request=request, db=db
    )

    assert response.status_code == 204

    acao = response.headers.get("access-control-allow-origin", "")
    acac = response.headers.get("access-control-allow-credentials", "")

    assert acao == "https://customer.example", (
        f"ACAO must echo origin in preflight, got {acao!r} (AC-9 preflight)"
    )
    assert acac.lower() != "true", (
        f"ACAC must NOT be true in preflight, got {acac!r} (AC-9 / REQ-2.2)"
    )


# ---------------------------------------------------------------------------
# AC-10: Unlisted origin returns 403 + no ACAO
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_partner_cors_blocks_unlisted_origin() -> None:
    """AC-10: GET /partner/v1/widget-config from evil.example returns 403
    and does NOT echo ACAO.

    REQ-2.1 — origin not in widget's allowed_origins list.
    """
    widget = FakeWidget()  # allowed_origins = ["https://customer.example"]
    db = AsyncMock()

    widget_result = MagicMock()
    widget_result.scalar_one_or_none = MagicMock(return_value=widget)
    db.execute = AsyncMock(return_value=widget_result)

    request = _make_request(origin="https://evil.example")

    with (
        patch("app.api.partner.settings") as mock_settings,
    ):
        mock_settings.widget_jwt_secret = "shared-secret"

        response = await widget_config(id=widget.widget_id, request=request, db=db)

    assert response.status_code == 403, (
        f"Expected 403 for unlisted origin, got {response.status_code} (AC-10)"
    )
    assert b"Origin not allowed" in response.body, (
        "Response body must contain 'Origin not allowed' (AC-10)"
    )

    acao = response.headers.get("access-control-allow-origin", "")
    assert acao != "https://evil.example", (
        f"ACAO must NOT echo evil.example, got {acao!r} (AC-10)"
    )
    assert acao != "*", "ACAO must NOT be wildcard (AC-10)"


# ---------------------------------------------------------------------------
# AC-11: BFF session cookie is rejected on partner endpoint (no Bearer token)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bff_cookie_rejected_on_partner_endpoint() -> None:
    """AC-11: POST /partner/v1/chat/completions with ONLY a BFF cookie returns 401.

    REQ-3.1 — partner endpoints do NOT accept BFF session cookies as auth.
    The get_partner_key dependency rejects missing Bearer tokens with 401.
    """
    from fastapi import HTTPException

    from app.api.partner_dependencies import get_partner_key

    # Simulate a request with ONLY a cookie header, no Authorization
    request = MagicMock()
    request.headers = {"cookie": "klai_bff_session=some-valid-sid"}
    request.state = MagicMock()

    db = AsyncMock()

    with pytest.raises(HTTPException) as exc_info:
        await get_partner_key(
            request=request,
            db=db,
        )

    assert exc_info.value.status_code == 401, (
        f"Expected 401 for cookie-only partner request, "
        f"got {exc_info.value.status_code} (AC-11)"
    )
