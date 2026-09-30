"""SPEC-KNOWLEDGE-ESCALATION-001 §4.2 — admin ticket settings per widget.

Endpoint functions are called directly with a mocked DB, like
tests/test_admin_widgets_integration.py; HubSpot is served by respx.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
import respx
from conftest import make_perms
from fastapi import HTTPException
from helpers import FakeResult, setup_db

from app.api.admin_widgets import (
    TicketSettingsRequest,
    TicketTarget,
    get_ticket_settings,
    put_ticket_settings,
)
from app.services.secrets import portal_secrets

API = "https://api.hubapi.com"
KEY = "pat-eu1-00000000-synthetic"
WIDGET = SimpleNamespace(id="5112f9ad-3768-4b76-9a13-5b74e9165bc3", org_id=1)
TARGET = TicketTarget(key="sales", label="Sales", pipeline_id="0", stage_id="1")


def _perms():
    return make_perms(role="admin", user_id="user-1", org_id=1, platform_unlocked_features=["widgets"])


def _body(
    *, service_key: str | None = KEY, target: TicketTarget = TARGET, portal_id: int | None = 12345
) -> TicketSettingsRequest:
    return TicketSettingsRequest(service_key=service_key, hubspot_portal_id=portal_id, targets=[target])


def _db(settings: object | None = None) -> AsyncMock:
    db = AsyncMock()
    db.add = MagicMock()
    setup_db(
        db,
        [
            FakeResult([WIDGET]),
            FakeResult([settings] if settings else []),
            FakeResult([7]),  # caller's portal_users.id
        ],
    )
    return db


def _mock_account_info(status_code: int = 403, portal_id: int = 12345) -> respx.Route:
    """Default 403: the tenant key today has no `oauth` scope."""
    return respx.get(f"{API}/account-info/v3/details").mock(
        return_value=httpx.Response(status_code, json={"portalId": portal_id, "uiDomain": "app-eu1.hubspot.com"})
    )


def _mock_pipelines(status_code: int = 200) -> respx.Route:
    return respx.get(f"{API}/crm/v3/pipelines/tickets").mock(
        return_value=httpx.Response(
            status_code,
            json={"results": [{"id": "0", "label": "Support", "stages": [{"id": "1", "label": "Nieuw"}]}]},
        )
    )


@pytest.mark.asyncio
@respx.mock
async def test_put_rejects_an_invalid_key_with_invalid_service_key() -> None:
    _mock_account_info(401)
    _mock_pipelines(401)
    db = _db()

    with pytest.raises(HTTPException) as info:
        await put_ticket_settings(widget_id=WIDGET.id, body=_body(), perms=_perms(), db=db)

    assert info.value.status_code == 422
    assert info.value.detail == "invalid_service_key"
    db.add.assert_not_called()


@pytest.mark.asyncio
@respx.mock
async def test_put_rejects_an_unknown_stage() -> None:
    _mock_account_info()
    _mock_pipelines()
    db = _db()
    unknown = TicketTarget(key="sales", label="Sales", pipeline_id="0", stage_id="999")

    with pytest.raises(HTTPException) as info:
        await put_ticket_settings(widget_id=WIDGET.id, body=_body(target=unknown), perms=_perms(), db=db)

    assert info.value.status_code == 422
    assert info.value.detail == "unknown_pipeline_or_stage"
    db.add.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("service_key", [None, "  "])
async def test_put_without_a_key_and_nothing_stored_is_422(service_key: str | None) -> None:
    with pytest.raises(HTTPException) as info:
        await put_ticket_settings(widget_id=WIDGET.id, body=_body(service_key=service_key), perms=_perms(), db=_db())

    assert info.value.status_code == 422
    assert info.value.detail == "service_key_required"


@pytest.mark.asyncio
@respx.mock
async def test_put_with_a_blank_key_keeps_the_stored_one() -> None:
    _mock_account_info()
    pipelines = _mock_pipelines()
    stored = SimpleNamespace(
        service_key_encrypted=portal_secrets.encrypt(KEY),
        hubspot_portal_id=1,
        hubspot_ui_domain=None,
        targets=[],
        updated_at=None,
    )

    await put_ticket_settings(widget_id=WIDGET.id, body=_body(service_key=""), perms=_perms(), db=_db(stored))

    assert pipelines.calls.last.request.headers["Authorization"] == f"Bearer {KEY}"
    assert portal_secrets.decrypt(stored.service_key_encrypted) == KEY
    assert stored.hubspot_portal_id == 12345


@pytest.mark.asyncio
@respx.mock
async def test_put_takes_portal_id_and_ui_domain_from_account_info() -> None:
    _mock_account_info(200, portal_id=4455)
    pipelines = _mock_pipelines()
    db = _db()

    result = await put_ticket_settings(widget_id=WIDGET.id, body=_body(portal_id=None), perms=_perms(), db=db)

    assert pipelines.called
    stored = db.add.call_args.args[0]
    assert stored.service_key_encrypted != KEY.encode()
    assert portal_secrets.decrypt(stored.service_key_encrypted) == KEY
    assert (stored.hubspot_portal_id, stored.hubspot_ui_domain, stored.org_id) == (4455, "app-eu1.hubspot.com", 1)
    assert result.model_dump() == {
        "configured": True,
        "hubspot_portal_id": 4455,
        "portal_id_source": "hubspot",
        "targets": [TARGET.model_dump()],
    }
    assert KEY not in result.model_dump_json()


@pytest.mark.asyncio
@respx.mock
async def test_put_without_account_info_access_needs_the_portal_id() -> None:
    _mock_account_info(403)
    _mock_pipelines()
    db = _db()

    with pytest.raises(HTTPException) as info:
        await put_ticket_settings(widget_id=WIDGET.id, body=_body(portal_id=None), perms=_perms(), db=db)

    assert info.value.status_code == 422
    assert info.value.detail == "portal_id_required"
    db.add.assert_not_called()


@pytest.mark.asyncio
@respx.mock
async def test_put_without_account_info_access_saves_the_entered_portal_id_as_manual() -> None:
    _mock_account_info(403)
    _mock_pipelines()
    db = _db()

    result = await put_ticket_settings(widget_id=WIDGET.id, body=_body(portal_id=12345), perms=_perms(), db=db)

    stored = db.add.call_args.args[0]
    assert (stored.hubspot_portal_id, stored.hubspot_ui_domain) == (12345, None)
    assert (result.hubspot_portal_id, result.portal_id_source) == (12345, "manual")
    db.commit.assert_awaited()


@pytest.mark.asyncio
@respx.mock
async def test_put_with_a_portal_id_other_than_the_keys_account_is_account_mismatch() -> None:
    """The key decides which account the tickets land in; an entered id that
    disagrees would make every ticket link point at another account."""
    _mock_account_info(200, portal_id=4455)
    _mock_pipelines()
    db = _db()

    with pytest.raises(HTTPException) as info:
        await put_ticket_settings(widget_id=WIDGET.id, body=_body(portal_id=12345), perms=_perms(), db=db)

    assert info.value.status_code == 422
    assert info.value.detail == "account_mismatch"
    db.add.assert_not_called()


@pytest.mark.asyncio
async def test_get_never_contains_the_key() -> None:
    settings = SimpleNamespace(
        service_key_encrypted=portal_secrets.encrypt(KEY),
        hubspot_portal_id=12345,
        hubspot_ui_domain="app-eu1.hubspot.com",
        targets=[TARGET.model_dump()],
    )

    result = await get_ticket_settings(widget_id=WIDGET.id, perms=_perms(), db=_db(settings))

    assert result.configured is True
    assert result.portal_id_source == "hubspot"
    assert result.targets == [TARGET]
    assert KEY not in result.model_dump_json()
    assert "service_key" not in result.model_dump()


def test_request_validation_at_the_boundary() -> None:
    with pytest.raises(ValueError):
        TicketSettingsRequest(hubspot_portal_id=12345, targets=[TARGET, TARGET])
    with pytest.raises(ValueError):
        TicketSettingsRequest(hubspot_portal_id=0, targets=[TARGET])
    with pytest.raises(ValueError):
        TicketTarget(key="Sales Team", label="Sales", pipeline_id="0", stage_id="1")
