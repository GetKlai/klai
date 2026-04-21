"""
Tests for admin domain CRUD endpoints (SPEC-AUTH-006 R3).

Covers:
- GET /api/admin/domains
- POST /api/admin/domains
- DELETE /api/admin/domains/{id}
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException


def _mock_org(org_id: int = 1) -> MagicMock:
    org = MagicMock()
    org.id = org_id
    return org


def _mock_admin_user() -> MagicMock:
    user = MagicMock()
    user.role = "admin"
    return user


def _mock_member_user() -> MagicMock:
    user = MagicMock()
    user.role = "member"
    return user


class TestListDomains:
    """GET /api/admin/domains returns org's allowed domains."""

    @pytest.mark.asyncio
    async def test_returns_domains_for_org(self) -> None:
        from app.api.admin.domains import list_domains

        domain_mock = MagicMock()
        domain_mock.id = 1
        domain_mock.domain = "acme.nl"
        domain_mock.created_at = "2026-04-16"
        domain_mock.created_by = "user-123"

        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [domain_mock]

        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(return_value=mock_result)

        mock_credentials = MagicMock()

        with patch("app.api.admin.domains._get_caller_org") as mock_get_org:
            mock_get_org.return_value = ("user-123", _mock_org(), _mock_admin_user())
            response = await list_domains(credentials=mock_credentials, db=mock_db)

        assert len(response.domains) == 1
        assert response.domains[0].domain == "acme.nl"


class TestAddDomain:
    """POST /api/admin/domains adds a new allowed domain."""

    @pytest.mark.asyncio
    async def test_rejects_free_email_provider(self) -> None:
        from app.api.admin.domains import AddDomainRequest, add_domain

        mock_db = AsyncMock()
        mock_credentials = MagicMock()

        body = AddDomainRequest(domain="gmail.com")

        with (
            patch("app.api.admin.domains._get_caller_org") as mock_get_org,
            pytest.raises(HTTPException) as exc_info,
        ):
            mock_get_org.return_value = ("user-123", _mock_org(), _mock_admin_user())
            await add_domain(body=body, credentials=mock_credentials, db=mock_db)

        assert exc_info.value.status_code == 400

    @pytest.mark.asyncio
    async def test_rejects_invalid_domain_format(self) -> None:
        from app.api.admin.domains import AddDomainRequest, add_domain

        mock_db = AsyncMock()
        mock_credentials = MagicMock()

        body = AddDomainRequest(domain="not-a-domain")

        with (
            patch("app.api.admin.domains._get_caller_org") as mock_get_org,
            pytest.raises(HTTPException) as exc_info,
        ):
            mock_get_org.return_value = ("user-123", _mock_org(), _mock_admin_user())
            await add_domain(body=body, credentials=mock_credentials, db=mock_db)

        assert exc_info.value.status_code == 400

    @pytest.mark.asyncio
    async def test_non_admin_rejected(self) -> None:
        from app.api.admin.domains import AddDomainRequest, add_domain

        mock_db = AsyncMock()
        mock_credentials = MagicMock()

        body = AddDomainRequest(domain="acme.nl")

        with (
            patch("app.api.admin.domains._get_caller_org") as mock_get_org,
            pytest.raises(HTTPException) as exc_info,
        ):
            mock_get_org.return_value = ("user-123", _mock_org(), _mock_member_user())
            await add_domain(body=body, credentials=mock_credentials, db=mock_db)

        assert exc_info.value.status_code == 403


class TestDeleteDomain:
    """DELETE /api/admin/domains/{id} removes domain."""

    @pytest.mark.asyncio
    async def test_non_admin_rejected(self) -> None:
        from app.api.admin.domains import delete_domain

        mock_db = AsyncMock()
        mock_credentials = MagicMock()

        with (
            patch("app.api.admin.domains._get_caller_org") as mock_get_org,
            pytest.raises(HTTPException) as exc_info,
        ):
            mock_get_org.return_value = ("user-123", _mock_org(), _mock_member_user())
            await delete_domain(domain_id=1, credentials=mock_credentials, db=mock_db)

        assert exc_info.value.status_code == 403
