"""Shared test helpers for Klai backend tests.

Import directly in test files:

    from helpers import FakeResult, FakeKB, make_partner_auth, setup_db
"""

from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import AsyncMock, MagicMock

# ---------------------------------------------------------------------------
# Generic DB result mock
# ---------------------------------------------------------------------------


class FakeResult:
    """Mimics all common SQLAlchemy async result access patterns.

    Use setup_db() for multi-query flows.
    Use as a direct return_value for single-query endpoints:

        db.execute = AsyncMock(return_value=FakeResult(rows=[my_row]))
    """

    def __init__(self, rows: list | None = None, scalar_value=None) -> None:
        self._rows = rows or []
        self._scalar_value = scalar_value

    def scalars(self) -> MagicMock:
        mock = MagicMock()
        mock.all.return_value = self._rows
        return mock

    def scalar_one_or_none(self):
        return self._rows[0] if self._rows else None

    def scalar(self):
        return self._scalar_value

    def fetchall(self) -> list:
        return self._rows

    def __iter__(self):
        return iter(self._rows)


def setup_db(mock_db: AsyncMock, results: list[FakeResult]) -> None:
    """Configure mock_db.execute to return results in sequence.

    Results are consumed in order; the last result is repeated for any
    additional calls beyond the list length.  This mirrors the real DB
    behaviour where every execute() call is independent.
    """
    call_count = 0

    async def _execute(*args, **kwargs):
        nonlocal call_count
        idx = min(call_count, len(results) - 1)
        call_count += 1
        return results[idx] if results else FakeResult()

    mock_db.execute = AsyncMock(side_effect=_execute)


# ---------------------------------------------------------------------------
# Partner API fakes
# ---------------------------------------------------------------------------


@dataclass
class FakeKB:
    """Mimics a PortalKnowledgeBase ORM row."""

    id: int
    name: str
    slug: str
    org_id: int


def make_partner_auth(
    permissions: dict | None = None,
    kb_access: dict | None = None,
):
    """Return a PartnerAuthContext with sensible test defaults.

    Override permissions or kb_access as needed per test:

        make_partner_auth(permissions={"chat": True, "knowledge_append": True})
        make_partner_auth(kb_access={10: "read_write"})
    """
    from app.api.partner_dependencies import PartnerAuthContext

    return PartnerAuthContext(
        key_id="key-uuid-1",
        org_id=42,
        zitadel_org_id="zit-org-42",
        permissions=permissions or {"chat": True, "feedback": True, "knowledge_append": False},
        kb_access=kb_access if kb_access is not None else {10: "read", 20: "read_write"},
        rate_limit_rpm=60,
    )
