"""Unit tests for SPEC-INFRA-TENANT-DELETE-001 Phase 6 — MoneybirdClient.

Uses respx to mock the Moneybird HTTP surface. Tests cover the two
deprovisioning methods (stop_subscription, archive_contact) across the
three scenarios per method: 200/success, 404/idempotent, 500/error.

Also covers fail-closed construction when settings are missing.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from app.services.moneybird_client import _MONEYBIRD_API_ROOT, MoneybirdClient

_ADMIN_ID = "480855402911630899"
_TOKEN = "test-moneybird-token"
_BASE_URL = f"{_MONEYBIRD_API_ROOT}/{_ADMIN_ID}"
_SUBSCRIPTION_ID = "sub-111"
_CONTACT_ID = "con-222"


@pytest.fixture
def moneybird_settings(monkeypatch):
    """Patch settings so MoneybirdClient constructs without error."""
    monkeypatch.setattr("app.services.moneybird_client.settings.moneybird_admin_id", _ADMIN_ID)
    monkeypatch.setattr("app.services.moneybird_client.settings.moneybird_api_token", _TOKEN)


@pytest.fixture
def client(moneybird_settings) -> MoneybirdClient:
    return MoneybirdClient()


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


class TestMoneybirdClientConstruction:
    def test_raises_when_admin_id_empty(self, monkeypatch) -> None:
        monkeypatch.setattr("app.services.moneybird_client.settings.moneybird_admin_id", "")
        monkeypatch.setattr("app.services.moneybird_client.settings.moneybird_api_token", _TOKEN)
        with pytest.raises(ValueError, match="MONEYBIRD_ADMIN_ID"):
            MoneybirdClient()

    def test_raises_when_token_empty(self, monkeypatch) -> None:
        monkeypatch.setattr("app.services.moneybird_client.settings.moneybird_admin_id", _ADMIN_ID)
        monkeypatch.setattr("app.services.moneybird_client.settings.moneybird_api_token", "")
        with pytest.raises(ValueError, match="MONEYBIRD_API_TOKEN"):
            MoneybirdClient()

    def test_raises_when_admin_id_whitespace_only(self, monkeypatch) -> None:
        monkeypatch.setattr("app.services.moneybird_client.settings.moneybird_admin_id", "   ")
        monkeypatch.setattr("app.services.moneybird_client.settings.moneybird_api_token", _TOKEN)
        with pytest.raises(ValueError, match="MONEYBIRD_ADMIN_ID"):
            MoneybirdClient()

    def test_base_url_derived_from_admin_id(self, moneybird_settings) -> None:
        c = MoneybirdClient()
        assert c._base_url == _BASE_URL


# ---------------------------------------------------------------------------
# stop_subscription
# ---------------------------------------------------------------------------


class TestStopSubscription:
    """Per Moneybird API: DELETE /recurring_sales_invoices/{id} stops billing
    (destroys the recurring template if no invoices exist, deactivates if they do).
    There is no PATCH-based 'frequency_type=stopped' endpoint — the original
    PATCH-with-body implementation was a guess and would 404.
    """

    @pytest.mark.asyncio
    async def test_204_returns_none(self, client) -> None:
        with respx.mock(base_url=_BASE_URL) as mock:
            mock.delete(f"/recurring_sales_invoices/{_SUBSCRIPTION_ID}").mock(return_value=httpx.Response(204))
            result = await client.stop_subscription(_SUBSCRIPTION_ID)
        assert result is None

    @pytest.mark.asyncio
    async def test_200_returns_none(self, client) -> None:
        """Some Moneybird responses return 200 instead of 204 — both = success."""
        with respx.mock(base_url=_BASE_URL) as mock:
            mock.delete(f"/recurring_sales_invoices/{_SUBSCRIPTION_ID}").mock(
                return_value=httpx.Response(200, json={"id": _SUBSCRIPTION_ID})
            )
            result = await client.stop_subscription(_SUBSCRIPTION_ID)
        assert result is None

    @pytest.mark.asyncio
    async def test_uses_delete_method_with_no_body(self, client) -> None:
        with respx.mock(base_url=_BASE_URL) as mock:
            route = mock.delete(f"/recurring_sales_invoices/{_SUBSCRIPTION_ID}").mock(return_value=httpx.Response(204))
            await client.stop_subscription(_SUBSCRIPTION_ID)
        # Verify exactly one DELETE call with empty body
        assert len(route.calls) == 1
        assert route.calls.last.request.method == "DELETE"
        assert route.calls.last.request.content == b""

    @pytest.mark.asyncio
    async def test_404_is_idempotent(self, client) -> None:
        with respx.mock(base_url=_BASE_URL) as mock:
            mock.delete(f"/recurring_sales_invoices/{_SUBSCRIPTION_ID}").mock(return_value=httpx.Response(404))
            result = await client.stop_subscription(_SUBSCRIPTION_ID)
        assert result is None

    @pytest.mark.asyncio
    async def test_500_raises_http_status_error(self, client) -> None:
        with respx.mock(base_url=_BASE_URL) as mock:
            mock.delete(f"/recurring_sales_invoices/{_SUBSCRIPTION_ID}").mock(return_value=httpx.Response(500))
            with pytest.raises(httpx.HTTPStatusError):
                await client.stop_subscription(_SUBSCRIPTION_ID)

    @pytest.mark.asyncio
    async def test_422_raises_http_status_error(self, client) -> None:
        """Non-404 client errors also propagate."""
        with respx.mock(base_url=_BASE_URL) as mock:
            mock.delete(f"/recurring_sales_invoices/{_SUBSCRIPTION_ID}").mock(
                return_value=httpx.Response(422, json={"error": "invalid"})
            )
            with pytest.raises(httpx.HTTPStatusError):
                await client.stop_subscription(_SUBSCRIPTION_ID)


# ---------------------------------------------------------------------------
# archive_contact
# ---------------------------------------------------------------------------


class TestArchiveContact:
    @pytest.mark.asyncio
    async def test_200_returns_none(self, client) -> None:
        with respx.mock(base_url=_BASE_URL) as mock:
            mock.patch(f"/contacts/{_CONTACT_ID}").mock(return_value=httpx.Response(200, json={"id": _CONTACT_ID}))
            result = await client.archive_contact(_CONTACT_ID)
        assert result is None

    @pytest.mark.asyncio
    async def test_200_sends_archived_true(self, client) -> None:
        with respx.mock(base_url=_BASE_URL) as mock:
            route = mock.patch(f"/contacts/{_CONTACT_ID}").mock(return_value=httpx.Response(200))
            await client.archive_contact(_CONTACT_ID)
        import json

        body = json.loads(route.calls.last.request.content)
        assert body["contact"]["archived"] is True

    @pytest.mark.asyncio
    async def test_404_is_idempotent(self, client) -> None:
        with respx.mock(base_url=_BASE_URL) as mock:
            mock.patch(f"/contacts/{_CONTACT_ID}").mock(return_value=httpx.Response(404))
            result = await client.archive_contact(_CONTACT_ID)
        assert result is None

    @pytest.mark.asyncio
    async def test_500_raises_http_status_error(self, client) -> None:
        with respx.mock(base_url=_BASE_URL) as mock:
            mock.patch(f"/contacts/{_CONTACT_ID}").mock(return_value=httpx.Response(500))
            with pytest.raises(httpx.HTTPStatusError):
                await client.archive_contact(_CONTACT_ID)

    @pytest.mark.asyncio
    async def test_403_raises_http_status_error(self, client) -> None:
        with respx.mock(base_url=_BASE_URL) as mock:
            mock.patch(f"/contacts/{_CONTACT_ID}").mock(return_value=httpx.Response(403))
            with pytest.raises(httpx.HTTPStatusError):
                await client.archive_contact(_CONTACT_ID)
