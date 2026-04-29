"""
Tests for Zitadel email-based user lookup helpers.

Bug context: Zitadel's loginName field stores emails with the original case
the user signed up with (e.g. "Steven@getklai.com"). The search query
defaulted to TEXT_QUERY_METHOD_EQUALS which is case-sensitive, so a user
typing the lower-case form on the password-reset form was silently treated
as "unknown email" and the endpoint returned 204 without sending a mail.

Both find_user_id_by_email (password reset, invite, internal language lookup)
and find_user_by_email (login, MFA) MUST use TEXT_QUERY_METHOD_EQUALS_IGNORE_CASE.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest


def _zitadel_client_with_mocked_http(result: list[dict]):
    """Construct a ZitadelClient with a mocked _http returning the given result list."""
    from app.services.zitadel import ZitadelClient

    client = ZitadelClient.__new__(ZitadelClient)
    mock_resp = MagicMock()
    mock_resp.json.return_value = {"result": result}
    mock_resp.raise_for_status = MagicMock()

    mock_http = AsyncMock()
    mock_http.post = AsyncMock(return_value=mock_resp)
    client._http = mock_http
    return client, mock_http


class TestFindUserIdByEmailCaseInsensitive:
    """find_user_id_by_email must match emails case-insensitively."""

    @pytest.mark.asyncio
    async def test_uses_equals_ignore_case_method(self) -> None:
        """The Zitadel query MUST use TEXT_QUERY_METHOD_EQUALS_IGNORE_CASE.

        With TEXT_QUERY_METHOD_EQUALS, a user whose loginName is
        "Steven@getklai.com" is silently not found when typing
        "steven@getklai.com" — exactly the bug we are fixing.
        """
        client, mock_http = _zitadel_client_with_mocked_http([{"userId": "u-123"}])

        await client.find_user_id_by_email("steven@getklai.com")

        mock_http.post.assert_awaited_once()
        _args, kwargs = mock_http.post.call_args
        sent_query = kwargs["json"]["queries"][0]["loginNameQuery"]
        assert sent_query["method"] == "TEXT_QUERY_METHOD_EQUALS_IGNORE_CASE"
        assert sent_query["loginName"] == "steven@getklai.com"

    @pytest.mark.asyncio
    async def test_returns_user_id_when_found(self) -> None:
        client, _ = _zitadel_client_with_mocked_http([{"userId": "u-123"}])

        result = await client.find_user_id_by_email("anyone@example.com")

        assert result == "u-123"

    @pytest.mark.asyncio
    async def test_returns_none_when_not_found(self) -> None:
        client, _ = _zitadel_client_with_mocked_http([])

        result = await client.find_user_id_by_email("ghost@example.com")

        assert result is None


class TestFindUserByEmailCaseInsensitive:
    """find_user_by_email (used by login + MFA) must match case-insensitively."""

    @pytest.mark.asyncio
    async def test_uses_equals_ignore_case_method(self) -> None:
        client, mock_http = _zitadel_client_with_mocked_http(
            [
                {
                    "userId": "u-1",
                    "details": {"resourceOwner": "org-1"},
                }
            ]
        )

        await client.find_user_by_email("steven@getklai.com")

        mock_http.post.assert_awaited_once()
        _args, kwargs = mock_http.post.call_args
        sent_query = kwargs["json"]["queries"][0]["loginNameQuery"]
        assert sent_query["method"] == "TEXT_QUERY_METHOD_EQUALS_IGNORE_CASE"

    @pytest.mark.asyncio
    async def test_returns_user_id_and_org_id(self) -> None:
        client, _ = _zitadel_client_with_mocked_http(
            [
                {
                    "userId": "u-1",
                    "details": {"resourceOwner": "org-1"},
                }
            ]
        )

        result = await client.find_user_by_email("user@example.com")

        assert result == ("u-1", "org-1")

    @pytest.mark.asyncio
    async def test_returns_none_when_not_found(self) -> None:
        client, _ = _zitadel_client_with_mocked_http([])

        result = await client.find_user_by_email("ghost@example.com")

        assert result is None
