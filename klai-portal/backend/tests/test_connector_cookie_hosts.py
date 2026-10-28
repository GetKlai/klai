"""Cookies per sign-in host for web_crawler connectors.

A site that signs in through a second host (SSO/OAuth) needs that host's
cookies too. A cookie may only name the base host or a detected sign-in host,
and no response may carry a cookie value.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from klai_image_storage.url_guard import _reset_dns_cache

from app.api.app_knowledge_bases import AuthChainRequest, auth_chain
from app.api.connectors import (
    ConnectorCredentialMetadataOut,
    _cookie_identities_from_credentials,
    _validate_connector_config,
)

_SECRET = "cookie-value-that-must-not-leak"


@pytest.fixture(autouse=True)
def _stub_dns_resolver():
    _reset_dns_cache()
    with patch("klai_image_storage.url_guard._resolve_blocking", return_value=("93.184.216.34",)):
        yield


def _config(*cookies: dict, hosts: list[str] | None = None) -> dict:
    config: dict = {"base_url": "https://app.example.com", "cookies": list(cookies)}
    if hosts is not None:
        config["auth_chain_hosts"] = hosts
    return config


def _idp_cookie() -> dict:
    return {
        "name": "idp_session",
        "value": _SECRET,
        "domain": "idp.example.com",
        "expires": "2027-01-01T00:00:00+00:00",
    }


def test_a_cookie_for_a_detected_sign_in_host_is_accepted_and_stored_as_json() -> None:
    validated = _validate_connector_config("web_crawler", _config(_idp_cookie(), hosts=["idp.example.com"]))
    stored = validated["cookies"][0]
    assert stored["domain"] == "idp.example.com"
    assert stored["expires"] == "2027-01-01T00:00:00+00:00"
    assert (stored["secure"], stored["httpOnly"], stored["path"]) == (True, False, "/")


def test_a_cookie_for_a_host_that_is_not_a_sign_in_host_is_rejected_with_422() -> None:
    with pytest.raises(HTTPException) as excinfo:
        _validate_connector_config("web_crawler", _config(_idp_cookie()))
    assert excinfo.value.status_code == 422
    assert "idp_session" in str(excinfo.value.detail)
    assert _SECRET not in str(excinfo.value.detail)


def test_cookies_for_the_base_host_keep_working_with_or_without_a_domain() -> None:
    validated = _validate_connector_config(
        "web_crawler",
        _config(
            {"name": "legacy", "value": "x"},
            {"name": "exported", "value": "y", "domain": ".example.com"},
            {"name": "exact", "value": "z", "domain": "app.example.com"},
        ),
    )
    assert [c["name"] for c in validated["cookies"]] == ["legacy", "exported", "exact"]


def test_a_bare_public_suffix_is_not_a_parent_domain() -> None:
    with pytest.raises(HTTPException):
        _validate_connector_config("web_crawler", _config({"name": "a", "value": "b", "domain": ".com"}))


def test_a_sign_in_host_must_be_a_host_name() -> None:
    with pytest.raises(HTTPException) as excinfo:
        _validate_connector_config("web_crawler", _config(hosts=["idp.example.com/oauth"]))
    assert excinfo.value.status_code == 422


def test_saved_cookie_metadata_never_carries_a_value() -> None:
    credentials = {"cookies": [{**_idp_cookie(), "path": "/", "secure": True}]}
    out = ConnectorCredentialMetadataOut(cookies=_cookie_identities_from_credentials(credentials))
    assert out.cookies[0].domain == "idp.example.com"
    assert _SECRET not in out.model_dump_json()


@pytest.mark.asyncio
async def test_auth_chain_route_returns_the_hosts_knowledge_ingest_found() -> None:
    perms = MagicMock(org_id=8)
    org = MagicMock(zitadel_org_id="org-1")
    with (
        patch("app.api.app_knowledge_bases._get_kb_or_404", new=AsyncMock()),
        patch("app.api.app_knowledge_bases.require_connector_manage_access", new=AsyncMock()),
        patch("app.api.app_knowledge_bases._load_org_or_500", new=AsyncMock(return_value=org)),
        patch(
            "app.api.app_knowledge_bases.knowledge_ingest_client.auth_chain",
            new=AsyncMock(return_value=["idp.example.com"]),
        ) as call,
    ):
        result = await auth_chain("kb", AuthChainRequest(url="https://app.example.com"), perms, MagicMock())
    assert result.hosts == ["idp.example.com"]
    call.assert_awaited_once_with(url="https://app.example.com", org_id="org-1")


@pytest.mark.asyncio
async def test_auth_chain_route_fails_loudly_when_knowledge_ingest_is_down() -> None:
    with (
        patch("app.api.app_knowledge_bases._get_kb_or_404", new=AsyncMock()),
        patch("app.api.app_knowledge_bases.require_connector_manage_access", new=AsyncMock()),
        patch("app.api.app_knowledge_bases._load_org_or_500", new=AsyncMock(return_value=MagicMock())),
        patch(
            "app.api.app_knowledge_bases.knowledge_ingest_client.auth_chain",
            new=AsyncMock(side_effect=RuntimeError("down")),
        ),
        pytest.raises(HTTPException) as excinfo,
    ):
        await auth_chain("kb", AuthChainRequest(url="https://app.example.com"), MagicMock(org_id=8), MagicMock())
    assert excinfo.value.status_code == 502
