"""Tests for case-normalisation of ``userName`` / ``username`` fields at
user-creation time in ``app.services.zitadel``.

Sibling-bug coverage for the 2026-04-30 case-sensitive-loginName regression.

Background:
    PR #235 fixed ``create_session_with_password`` to pass the canonical
    ``userId`` instead of the user-typed email. That fix made login
    case-insensitive at the SESSION layer. But the underlying
    user-creation paths still wrote the userName / loginName field with
    whatever case the operator typed. A user invited as
    ``Steven@getklai.com`` got that exact value as their loginName,
    which bit Steven on 2026-04-30. New users created via these paths
    going forward are normalised to lowercase so the data layer cannot
    re-introduce the case-mismatch class regardless of what later
    consumers do with loginName.

The display ``email`` field keeps original case (used in outgoing mail
headers — preserves the inviter's intended capitalisation for greeting
purposes).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest


def _zitadel_client_with_mocked_http(response_body: dict | None = None):
    """Construct a ZitadelClient with a mocked _http returning the given body."""
    from app.services.zitadel import ZitadelClient

    client = ZitadelClient.__new__(ZitadelClient)
    mock_resp = MagicMock()
    mock_resp.json.return_value = response_body or {"userId": "u-1"}
    mock_resp.raise_for_status = MagicMock()

    mock_http = AsyncMock()
    mock_http.post = AsyncMock(return_value=mock_resp)
    client._http = mock_http
    return client, mock_http


class TestCreateHumanUserUsernameLowercased:
    """``create_human_user`` MUST lowercase the userName even when the
    caller typed mixed case."""

    @pytest.mark.asyncio
    async def test_username_lowercased_when_email_mixed_case(self) -> None:
        client, mock_http = _zitadel_client_with_mocked_http()

        await client.create_human_user(
            org_id="org-1",
            email="Steven@GetKlai.COM",
            first_name="Steven",
            last_name="Buurma",
            password="x",
        )

        _args, kwargs = mock_http.post.call_args
        body = kwargs["json"]
        assert body["userName"] == "steven@getklai.com"

    @pytest.mark.asyncio
    async def test_email_field_preserves_original_case(self) -> None:
        """Display email keeps original case for outgoing-mail headers."""
        client, mock_http = _zitadel_client_with_mocked_http()

        await client.create_human_user(
            org_id="org-1",
            email="Steven@GetKlai.COM",
            first_name="S",
            last_name="B",
            password="x",
        )

        _args, kwargs = mock_http.post.call_args
        assert kwargs["json"]["email"]["email"] == "Steven@GetKlai.COM"

    @pytest.mark.asyncio
    async def test_already_lowercase_email_passes_through(self) -> None:
        client, mock_http = _zitadel_client_with_mocked_http()

        await client.create_human_user(
            org_id="org-1",
            email="lower@example.com",
            first_name="L",
            last_name="C",
            password="x",
        )

        _args, kwargs = mock_http.post.call_args
        assert kwargs["json"]["userName"] == "lower@example.com"


class TestInviteUserUsernameLowercased:
    """``invite_user`` MUST lowercase the userName even when the inviting
    admin typed mixed case."""

    @pytest.mark.asyncio
    async def test_username_lowercased_when_email_mixed_case(self) -> None:
        client, mock_http = _zitadel_client_with_mocked_http()

        await client.invite_user(
            org_id="org-1",
            email="NewHire@Acme.IO",
            first_name="New",
            last_name="Hire",
        )

        _args, kwargs = mock_http.post.call_args
        body = kwargs["json"]
        assert body["userName"] == "newhire@acme.io"

    @pytest.mark.asyncio
    async def test_email_field_preserves_original_case(self) -> None:
        client, mock_http = _zitadel_client_with_mocked_http()

        await client.invite_user(
            org_id="org-1",
            email="NewHire@Acme.IO",
            first_name="N",
            last_name="H",
        )

        _args, kwargs = mock_http.post.call_args
        assert kwargs["json"]["email"]["email"] == "NewHire@Acme.IO"

    @pytest.mark.asyncio
    async def test_send_codes_flag_unchanged(self) -> None:
        """Regression: the lowercase rename must not break the invite-mail
        send-codes flag."""
        client, mock_http = _zitadel_client_with_mocked_http()

        await client.invite_user(
            org_id="org-1",
            email="anyone@example.com",
            first_name="A",
            last_name="N",
        )

        _args, kwargs = mock_http.post.call_args
        assert kwargs["json"]["sendCodes"] is True


class TestCreateZitadelUserFromIdpUsernameLowercased:
    """``create_zitadel_user_from_idp`` MUST lowercase the username even
    when the IDP-provided email has mixed case. Auto-provisioned IDP
    users land on the same case-insensitive footing as manually-created
    humans."""

    @pytest.mark.asyncio
    async def test_username_lowercased_when_idp_email_mixed_case(self) -> None:
        client, mock_http = _zitadel_client_with_mocked_http(response_body={"userId": "u-idp-1"})

        intent_data = {
            "idpInformation": {
                "idpId": "google-idp",
                "userId": "google-uid-123",
                "userName": "google-username",
                "rawInformation": {
                    "User": {
                        "given_name": "Mixed",
                        "family_name": "Case",
                        "email": "Mixed.Case@Example.IO",
                    }
                },
            }
        }

        await client.create_zitadel_user_from_idp(intent_data, org_id="org-1")

        _args, kwargs = mock_http.post.call_args
        body = kwargs["json"]
        assert body["username"] == "mixed.case@example.io"

    @pytest.mark.asyncio
    async def test_idp_email_preserved_for_display(self) -> None:
        """The display email keeps original case (matches what the IDP
        actually returned)."""
        client, mock_http = _zitadel_client_with_mocked_http(response_body={"userId": "u-1"})

        intent_data = {
            "idpInformation": {
                "idpId": "google-idp",
                "userId": "google-uid-123",
                "rawInformation": {
                    "User": {
                        "given_name": "M",
                        "family_name": "C",
                        "email": "Mixed.Case@Example.IO",
                    }
                },
            }
        }

        await client.create_zitadel_user_from_idp(intent_data, org_id="org-1")

        _args, kwargs = mock_http.post.call_args
        assert kwargs["json"]["email"]["email"] == "Mixed.Case@Example.IO"
