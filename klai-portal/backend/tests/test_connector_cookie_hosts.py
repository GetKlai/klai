"""Cookie domains for web_crawler connectors.

A cookie may only name a domain that covers the base host, and no response
may carry a cookie value.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi import HTTPException
from klai_image_storage.url_guard import _reset_dns_cache

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


def _config(*cookies: dict) -> dict:
    return {"base_url": "https://app.example.com", "cookies": list(cookies)}


def _idp_cookie() -> dict:
    return {
        "name": "idp_session",
        "value": _SECRET,
        "domain": "idp.example.com",
        "expires": "2027-01-01T00:00:00+00:00",
    }


def test_a_cookie_for_the_base_host_is_stored_as_json() -> None:
    validated = _validate_connector_config("web_crawler", _config({**_idp_cookie(), "domain": "app.example.com"}))
    stored = validated["cookies"][0]
    assert stored["domain"] == "app.example.com"
    assert stored["expires"] == "2027-01-01T00:00:00+00:00"
    assert (stored["secure"], stored["httpOnly"], stored["path"]) == (True, False, "/")


def test_a_cookie_for_another_host_is_rejected_with_422() -> None:
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


def test_a_parent_domain_without_a_leading_dot_is_host_only_and_does_not_cover_the_base_host() -> None:
    # The crawler never sends such a cookie to app.example.com, so the wizard
    # must not accept it as one.
    with pytest.raises(HTTPException) as excinfo:
        _validate_connector_config(
            "web_crawler", _config({"name": "parent", "value": "made-up", "domain": "example.com"})
        )
    assert excinfo.value.status_code == 422


def test_saved_cookie_metadata_never_carries_a_value() -> None:
    credentials = {"cookies": [{**_idp_cookie(), "path": "/", "secure": True}]}
    out = ConnectorCredentialMetadataOut(cookies=_cookie_identities_from_credentials(credentials))
    assert out.cookies[0].domain == "idp.example.com"
    assert _SECRET not in out.model_dump_json()
