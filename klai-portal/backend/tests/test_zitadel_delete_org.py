"""Unit tests for SPEC-INFRA-TENANT-DELETE-001 Phase 5 — ZitadelClient.delete_org().

Uses respx to mock the Zitadel HTTP surface without a real network connection.
The fixture is taken from conftest.py's re-export of respx_zitadel
(originally defined in auth_test_helpers).
"""

from __future__ import annotations

import httpx
import pytest
import respx

from app.core.config import settings
from app.services.zitadel import zitadel

_ORG_ID = "org-abc-123"
_DELETE_PATH = "/management/v1/orgs"


@pytest.fixture
def respx_zitadel_local():
    """Local fixture: mock the Zitadel base URL."""
    with respx.mock(base_url=settings.zitadel_base_url, assert_all_called=False) as router:
        yield router


class TestDeleteOrg:
    @pytest.mark.asyncio
    async def test_200_returns_none(self, respx_zitadel_local) -> None:
        """A 200 OK response means deletion succeeded — method returns None."""
        respx_zitadel_local.delete(_DELETE_PATH).mock(return_value=httpx.Response(200))
        result = await zitadel.delete_org(_ORG_ID)
        assert result is None

    @pytest.mark.asyncio
    async def test_200_sends_correct_org_id_header(self, respx_zitadel_local) -> None:
        """The x-zitadel-orgid header must match the org_id argument."""
        route = respx_zitadel_local.delete(_DELETE_PATH).mock(return_value=httpx.Response(200))
        await zitadel.delete_org(_ORG_ID)
        request = route.calls.last.request
        assert request.headers.get("x-zitadel-orgid") == _ORG_ID

    @pytest.mark.asyncio
    async def test_404_is_idempotent_returns_none(self, respx_zitadel_local) -> None:
        """404 means org is already absent — idempotent, should return None."""
        respx_zitadel_local.delete(_DELETE_PATH).mock(return_value=httpx.Response(404))
        result = await zitadel.delete_org(_ORG_ID)
        assert result is None

    @pytest.mark.asyncio
    async def test_500_raises_http_status_error(self, respx_zitadel_local) -> None:
        """Any non-2xx non-404 response must propagate as HTTPStatusError."""
        respx_zitadel_local.delete(_DELETE_PATH).mock(
            return_value=httpx.Response(500, json={"message": "internal error"})
        )
        with pytest.raises(httpx.HTTPStatusError):
            await zitadel.delete_org(_ORG_ID)

    @pytest.mark.asyncio
    async def test_403_raises_http_status_error(self, respx_zitadel_local) -> None:
        """403 Forbidden must propagate — likely a PAT permissions issue."""
        respx_zitadel_local.delete(_DELETE_PATH).mock(return_value=httpx.Response(403))
        with pytest.raises(httpx.HTTPStatusError):
            await zitadel.delete_org(_ORG_ID)
