"""Characterization snapshots for `billing.py` admin gate.

SPEC-PORTAL-RBAC-REFACTOR-001 Pre-phase. Pins the current behaviour of the
two admin-gated billing endpoints (`create_mandate`, `cancel_subscription`)
against the role matrix:

  - admin                          → gate passes (no 401/403)
  - personal/company/kb_manager/group_manager → 403 from `_require_admin`
  - unauthenticated                → 401 from `_get_caller_org`

The other three endpoints in `billing.py` (`mock_complete`, `billing_status`,
`invoice_portal`) only require authentication, not admin, so they are out
of scope for this snapshot.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.api.billing import (
    MandateRequest,
    cancel_subscription,
    create_mandate,
)
from tests.helpers import make_request
from tests.role_matrix_helpers import (
    NON_ADMIN_ROLES,
    assert_admin_passes_gate,
    assert_role_blocked_at_gate,
    assert_unauthenticated_blocked,
    make_db_mock,
)

_MODULE = "app.api.billing"


def _mandate_body() -> MandateRequest:
    return MandateRequest(
        plan="knowledge",
        billing_cycle="monthly",
        seats=2,
        address="Teststraat 1",
        zipcode="1011AA",
        city="Amsterdam",
        country="NL",
    )


def _moneybird_mock() -> AsyncMock:
    """Stand-in for the MoneybirdService dependency."""
    m = AsyncMock()
    m.create_contact = AsyncMock(return_value={"id": "mc-9001"})
    m.get_mandate_url = AsyncMock(return_value="https://example.com/mandate")
    m.cancel_subscription = AsyncMock()
    return m


# ---------------------------------------------------------------------------
# create_mandate — POST /api/billing/mandate
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_mandate_admin_passes_gate() -> None:
    await assert_admin_passes_gate(
        create_mandate,
        _MODULE,
        request=make_request(method="POST", path="/api/billing/mandate"),
        body=_mandate_body(),
        credentials=MagicMock(credentials="tok"),
        db=make_db_mock(),
        moneybird=_moneybird_mock(),
    )


@pytest.mark.parametrize("role", NON_ADMIN_ROLES)
@pytest.mark.asyncio
async def test_create_mandate_non_admin_blocked(role: str) -> None:
    await assert_role_blocked_at_gate(
        create_mandate,
        _MODULE,
        role,
        request=make_request(method="POST", path="/api/billing/mandate"),
        body=_mandate_body(),
        credentials=MagicMock(credentials="tok"),
        db=make_db_mock(),
        moneybird=_moneybird_mock(),
    )


@pytest.mark.asyncio
async def test_create_mandate_unauthenticated() -> None:
    await assert_unauthenticated_blocked(
        create_mandate,
        _MODULE,
        request=make_request(method="POST", path="/api/billing/mandate"),
        body=_mandate_body(),
        credentials=MagicMock(credentials="tok"),
        db=make_db_mock(),
        moneybird=_moneybird_mock(),
    )


# ---------------------------------------------------------------------------
# cancel_subscription — POST /api/billing/cancel
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cancel_subscription_admin_passes_gate() -> None:
    await assert_admin_passes_gate(
        cancel_subscription,
        _MODULE,
        credentials=MagicMock(credentials="tok"),
        db=make_db_mock(),
        moneybird=_moneybird_mock(),
    )


@pytest.mark.parametrize("role", NON_ADMIN_ROLES)
@pytest.mark.asyncio
async def test_cancel_subscription_non_admin_blocked(role: str) -> None:
    await assert_role_blocked_at_gate(
        cancel_subscription,
        _MODULE,
        role,
        credentials=MagicMock(credentials="tok"),
        db=make_db_mock(),
        moneybird=_moneybird_mock(),
    )


@pytest.mark.asyncio
async def test_cancel_subscription_unauthenticated() -> None:
    await assert_unauthenticated_blocked(
        cancel_subscription,
        _MODULE,
        credentials=MagicMock(credentials="tok"),
        db=make_db_mock(),
        moneybird=_moneybird_mock(),
    )
