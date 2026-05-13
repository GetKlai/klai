"""
R7 tests -- free-email enforcement (AC-2) + auto_join_admin_notification template.
(SPEC-AUTH-009 R7 / C7.1 / C7.2 / C7.3)

AC-2 is primarily covered by test_primary_domain.py (R1).
This module adds targeted smoke tests that confirm:
- is_free_email_provider() blocks gmail during signup (AC-2 integration check)
- notify_auto_join_admin uses the auto_join_admin_notification template (R7)
- the auto_join_admin_notification schema is registered in klai-mailer
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Path from tests/test_r7...py to klai-mailer root:
#   test file -> tests -> backend -> klai-portal -> klai-auth-009 -> klai-mailer
_MAILER_ROOT = Path(__file__).parent.parent.parent.parent / "klai-mailer"


class TestFreeEmailSignupConfirmed:
    """Confirm AC-2 coverage is wired in the signup flow (C7.1)."""

    @pytest.mark.asyncio
    async def test_free_email_raises_400_on_signup(self) -> None:
        """C7.1: Signup with gmail raises HTTP 400 (delegates to is_free_email_provider)."""
        from fastapi import HTTPException

        from app.api.signup import SignupRequest, signup

        body = SignupRequest(
            email="someone@gmail.com",
            password="VerySecure1234!",  # 15 chars -- satisfies strength policy
            first_name="Test",
            last_name="User",
            company_name="AcmeCorp",
            preferred_language="nl",
        )

        with patch("app.api.signup.zitadel") as mock_z:
            mock_z.create_user_with_password = AsyncMock(return_value={"userId": "uid1"})
            db = AsyncMock()
            db.add = MagicMock()
            with pytest.raises(HTTPException) as exc_info:
                await signup(body=body, background_tasks=MagicMock(), db=db)
        assert exc_info.value.status_code == 400

    def test_free_email_provider_detection(self) -> None:
        """C7.1: is_free_email_provider correctly identifies free vs corporate email."""
        from app.services.domain_validation import is_free_email_provider

        assert is_free_email_provider("gmail.com") is True
        assert is_free_email_provider("outlook.com") is True
        assert is_free_email_provider("voys.nl") is False
        assert is_free_email_provider("company.com") is False


class TestAutoJoinAdminNotification:
    """R7: auto_join_admin_notification mailer template is wired correctly."""

    @pytest.mark.asyncio
    async def test_notify_auto_join_admin_uses_correct_template(self) -> None:
        """R7: notify_auto_join_admin POSTs to mailer with template=auto_join_admin_notification."""
        captured: list[dict] = []

        async def _mock_post(url, *, headers, json, **kwargs):  # type: ignore[misc]
            captured.append(json)
            mock_response = MagicMock()
            mock_response.status_code = 200
            return mock_response

        with patch("app.services.notifications.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client.post = _mock_post
            mock_client_cls.return_value = mock_client

            with patch("app.services.notifications.settings") as mock_settings:
                mock_settings.mailer_url = "http://mailer"
                mock_settings.internal_secret = "secret"

                from app.services.notifications import notify_auto_join_admin

                await notify_auto_join_admin(
                    email="user@voys.nl",
                    display_name="Test User",
                    domain="voys.nl",
                    org_id=1,
                    admin_email="admin@voys.nl",
                )

        assert len(captured) == 1
        assert captured[0]["template"] == "auto_join_admin_notification"
        assert captured[0]["to"] == "admin@voys.nl"
        assert captured[0]["variables"]["domain"] == "voys.nl"
        assert captured[0]["variables"]["admin_email"] == "admin@voys.nl"

    def test_auto_join_admin_notification_template_files_exist(self) -> None:
        """R7: auto_join_admin_notification template files exist in klai-mailer/theme/internal/."""
        theme_dir = _MAILER_ROOT / "theme" / "internal"
        nl_template = theme_dir / "auto_join_admin_notification.nl.html.j2"
        en_template = theme_dir / "auto_join_admin_notification.en.html.j2"
        assert nl_template.exists(), f"NL template not found: {nl_template}"
        assert en_template.exists(), f"EN template not found: {en_template}"

    def test_auto_join_admin_notification_schema_registered(self) -> None:
        """R7: auto_join_admin_notification is in klai-mailer schemas.py TEMPLATE_SCHEMAS."""
        schemas_path = _MAILER_ROOT / "app" / "schemas.py"
        assert schemas_path.exists(), f"schemas.py not found at {schemas_path}"
        content = schemas_path.read_text(encoding="utf-8")
        assert "auto_join_admin_notification" in content, (
            "auto_join_admin_notification not registered in TEMPLATE_SCHEMAS"
        )
        assert "AutoJoinAdminNotificationVars" in content, (
            "AutoJoinAdminNotificationVars class not defined in schemas.py"
        )
