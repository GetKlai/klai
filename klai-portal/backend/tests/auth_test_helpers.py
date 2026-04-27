"""Shared test helpers for the auth.py endpoint test modules.

Extracted from ``tests/test_auth_mfa_fail_closed.py`` per
SPEC-SEC-AUTH-COVERAGE-001 REQ-5.6 so that future test files for the
remaining 13 in-scope auth endpoints (TOTP setup/confirm, passkey x 2,
email_otp x 3, verify_email, IDP intent/callback x 4, password reset/set,
sso_complete) can import the same fixtures and factory functions.

Naming convention:
- ``_make_<thing>`` → request-body / DB-mock factory
- ``_capture_events(captured, event)`` → filter on captured structlog events
- ``_audit_emit_patches`` → suppress audit + analytics side effects
- ``respx_zitadel`` → respx fixture mounted on settings.zitadel_base_url

All factories use sensible defaults. Each test passes only the fields it
cares about; the rest fall back to safe placeholders.
"""

from __future__ import annotations

import hashlib
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import respx
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import LoginRequest
from app.core.config import settings
from app.models.portal import PortalOrg, PortalUser

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_TEST_EMAIL = "alice@acme.com"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def respx_zitadel():
    """Mock the Zitadel HTTP surface against ``settings.zitadel_base_url``.

    Uses ``assert_all_called=False`` so individual scenarios can omit endpoints
    that should not be hit (e.g. asserting ``/v2/sessions`` received zero calls).
    """
    with respx.mock(base_url=settings.zitadel_base_url, assert_all_called=False) as router:
        yield router


# ---------------------------------------------------------------------------
# Request body factories
# ---------------------------------------------------------------------------


def _make_login_body(email: str = _TEST_EMAIL) -> LoginRequest:
    return LoginRequest(
        email=email,
        password="correct-horse-battery-staple",
        auth_request_id="ar-mfa-fc-1",
    )


# ---------------------------------------------------------------------------
# Hash + payload helpers
# ---------------------------------------------------------------------------


def _expected_email_hash(email: str = _TEST_EMAIL) -> str:
    return hashlib.sha256(email.lower().encode("utf-8")).hexdigest()


def _session_ok() -> dict[str, Any]:
    return {"sessionId": "sess-abc", "sessionToken": "tok-xyz"}


# ---------------------------------------------------------------------------
# Async DB-session mock factory
# ---------------------------------------------------------------------------


def _make_db_mock(
    *,
    portal_user_org_id: int | None = 10,
    portal_user_zitadel_id: str = "uid-req",
    org_mfa_policy: str | None = "required",
    scalar_side_effect: Exception | None = None,
    get_side_effect: Exception | None = None,
) -> AsyncMock:
    """Return an ``AsyncMock(spec=AsyncSession)`` wired for MFA-lookup tests.

    - ``portal_user_org_id=None`` => ``db.scalar`` returns ``None`` (user not in portal).
    - ``scalar_side_effect`` => ``db.scalar`` raises (DB lookup failure).
    - ``org_mfa_policy=None`` => ``db.get`` returns ``None`` (org missing).
    - ``get_side_effect`` => ``db.get`` raises (org fetch failure).
    """
    db = AsyncMock(spec=AsyncSession)
    db.add = MagicMock()  # SQLAlchemy Session.add is sync; AsyncMock would coerce it

    if scalar_side_effect is not None:
        db.scalar = AsyncMock(side_effect=scalar_side_effect)
    elif portal_user_org_id is None:
        db.scalar = AsyncMock(return_value=None)
    else:
        portal_user = MagicMock(spec=PortalUser)
        portal_user.org_id = portal_user_org_id
        portal_user.zitadel_user_id = portal_user_zitadel_id
        db.scalar = AsyncMock(return_value=portal_user)

    if get_side_effect is not None:
        db.get = AsyncMock(side_effect=get_side_effect)
    elif org_mfa_policy is None:
        db.get = AsyncMock(return_value=None)
    else:
        org = MagicMock(spec=PortalOrg)
        org.id = portal_user_org_id or 10
        org.mfa_policy = org_mfa_policy
        db.get = AsyncMock(return_value=org)

    return db


# ---------------------------------------------------------------------------
# Side-effect suppression
# ---------------------------------------------------------------------------


def _audit_emit_patches() -> tuple[Any, Any]:
    """Suppress audit + analytics side effects without mocking the zitadel module.

    Used by every endpoint test to silence ``audit.log_event`` and
    ``emit_event`` calls so tests focus on the endpoint's own observability
    surface (structured events) rather than re-asserting audit-log behaviour.
    """
    return (
        patch("app.api.auth.audit.log_event", AsyncMock()),
        patch("app.api.auth.emit_event", MagicMock()),
    )


# ---------------------------------------------------------------------------
# Captured-event filters
# ---------------------------------------------------------------------------


def _capture_events(captured: list[dict[str, Any]], event_name: str) -> list[dict[str, Any]]:
    """Return only the records with ``event == event_name`` from a structlog capture.

    Generalisation of the original ``_mfa_events`` filter — accepts any event
    name so test files for non-MFA endpoints can use the same helper.
    """
    return [e for e in captured if e.get("event") == event_name]


def _mfa_events(captured: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Backward-compatible filter for the ``mfa_check_failed`` event.

    Existing ``test_auth_mfa_fail_closed.py`` callers use this name. New
    test files SHOULD prefer ``_capture_events(captured, "<event>")``.
    """
    return _capture_events(captured, "mfa_check_failed")
