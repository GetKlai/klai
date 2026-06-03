"""Security regressions for internal connector config materialization."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

_PLACEHOLDER_INTERNAL = "placeholder-internal-value"  # nosec
_PLACEHOLDER_TOKEN = "placeholder-token-value"  # nosec


def _request() -> MagicMock:
    request = MagicMock()
    request.headers = {"Authorization": f"Bearer {_PLACEHOLDER_INTERNAL}"}
    return request


def _connector(*, connector_type: str = "confluence", config: dict, encrypted_credentials: bytes | None) -> MagicMock:
    connector = MagicMock()
    connector.id = "conn-confluence-1"
    connector.kb_id = 123
    connector.org_id = 77
    connector.connector_type = connector_type
    connector.config = config
    connector.schedule = None
    connector.is_enabled = True
    connector.allowed_assertion_modes = None
    connector.created_by = "zitadel-user-1"
    connector.encrypted_credentials = encrypted_credentials
    return connector


def _row(connector: MagicMock) -> MagicMock:
    kb = MagicMock()
    kb.slug = "kb-main"
    org = MagicMock()
    org.zitadel_org_id = "200000000000000001"
    result = MagicMock()
    result.one_or_none.return_value = (connector, kb, org)
    return result


class TestInternalConnectorConfigSecurity:
    @pytest.mark.parametrize(
        "connector_type,config",
        [
            (
                "confluence",
                {
                    "base_url": "https://example.atlassian.net/wiki",
                    "email": "admin@example.com",
                    "api_token": _PLACEHOLDER_TOKEN,
                },
            ),
            (
                "airtable",
                {
                    "base_id": "app123456789",
                    "table_names": ["Customers"],
                    "api_key": _PLACEHOLDER_TOKEN,
                },
            ),
        ],
    )
    @pytest.mark.asyncio
    async def test_plaintext_connector_secret_is_rejected(self, connector_type: str, config: dict) -> None:
        from app.api.internal import get_connector_config

        connector = _connector(
            connector_type=connector_type,
            config=config,
            encrypted_credentials=None,
        )
        db = AsyncMock()
        db.get = AsyncMock(return_value=connector)
        db.execute = AsyncMock(return_value=_row(connector))

        with (
            patch("app.api.internal.settings") as mock_settings,
            patch("app.api.internal.set_tenant", new=AsyncMock()),
            patch("app.api.internal._audit_internal_call", new=AsyncMock()),
        ):
            mock_settings.internal_secret = _PLACEHOLDER_INTERNAL

            with pytest.raises(HTTPException) as exc_info:
                await get_connector_config(
                    connector_id="conn-confluence-1",
                    request=_request(),
                    db=db,
                )

            assert exc_info.value.status_code == 500
            assert exc_info.value.detail == {"error_code": "connector_plaintext_secret_detected"}

    @pytest.mark.asyncio
    async def test_encrypted_confluence_api_token_is_merged_for_internal_consumer(self) -> None:
        from app.api.internal import get_connector_config

        connector = _connector(
            config={
                "base_url": "https://example.atlassian.net/wiki",
                "email": "admin@example.com",
            },
            encrypted_credentials=b"ENCRYPTED",
        )
        db = AsyncMock()
        db.get = AsyncMock(return_value=connector)
        db.execute = AsyncMock(return_value=_row(connector))

        with (
            patch("app.api.internal.settings") as mock_settings,
            patch("app.api.internal.set_tenant", new=AsyncMock()),
            patch("app.api.internal._audit_internal_call", new=AsyncMock()),
            patch("app.api.internal.credential_store") as mock_store,
        ):
            mock_settings.internal_secret = _PLACEHOLDER_INTERNAL
            mock_store.decrypt_credentials = AsyncMock(return_value={"api_token": _PLACEHOLDER_TOKEN})

            result = await get_connector_config(
                connector_id="conn-confluence-1",
                request=_request(),
                db=db,
            )

            assert result.config["api_token"] == _PLACEHOLDER_TOKEN
            assert connector.config == {
                "base_url": "https://example.atlassian.net/wiki",
                "email": "admin@example.com",
            }

    @pytest.mark.parametrize(
        "connector_type,config",
        [
            (
                "confluence",
                {
                    "base_url": "https://example.atlassian.net/wiki",
                    "email": "admin@example.com",
                },
            ),
            (
                "airtable",
                {
                    "base_id": "app123456789",
                    "table_names": ["Customers"],
                },
            ),
        ],
    )
    @pytest.mark.asyncio
    async def test_required_encrypted_credentials_missing_is_rejected(self, connector_type: str, config: dict) -> None:
        from app.api.internal import get_connector_config

        connector = _connector(
            connector_type=connector_type,
            config=config,
            encrypted_credentials=None,
        )
        db = AsyncMock()
        db.get = AsyncMock(return_value=connector)
        db.execute = AsyncMock(return_value=_row(connector))

        with (
            patch("app.api.internal.settings") as mock_settings,
            patch("app.api.internal.set_tenant", new=AsyncMock()),
            patch("app.api.internal._audit_internal_call", new=AsyncMock()),
        ):
            mock_settings.internal_secret = _PLACEHOLDER_INTERNAL

            with pytest.raises(HTTPException) as exc_info:
                await get_connector_config(
                    connector_id="conn-confluence-1",
                    request=_request(),
                    db=db,
                )

            assert exc_info.value.status_code == 500
            assert exc_info.value.detail == {"error_code": "connector_required_credentials_missing"}
