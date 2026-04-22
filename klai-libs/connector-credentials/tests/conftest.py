"""Shared fixtures for ConnectorCredentialStore tests."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

import pytest

from connector_credentials import ConnectorCredentialStore

if TYPE_CHECKING:
    from collections.abc import Iterable


def make_store(hex_key: str | None = None) -> ConnectorCredentialStore:
    """Create a store with a random valid KEK unless hex_key is given."""
    if hex_key is None:
        hex_key = os.urandom(32).hex()
    return ConnectorCredentialStore(hex_key)


def make_db_returning(rows: Iterable[object]) -> AsyncMock:
    """Mock an AsyncSession where each db.execute() returns the next row queue entry.

    Each element in ``rows`` is what ``result.first()`` (or ``.all()`` / ``.scalar_one_or_none()``)
    should return. Non-SELECT statements (UPDATE) consume a slot too; pass None for those.
    """
    db = AsyncMock()
    db.add = MagicMock()
    db.flush = AsyncMock()

    row_queue = list(rows)

    async def fake_execute(_stmt: object, _params: object | None = None) -> MagicMock:
        result = MagicMock()
        payload = row_queue.pop(0) if row_queue else None
        result.first = MagicMock(return_value=payload)
        result.scalar_one_or_none = MagicMock(return_value=payload)
        # For .all() results we expect the payload itself to be a list
        result.all = MagicMock(return_value=payload if isinstance(payload, list) else [])
        return result

    db.execute = AsyncMock(side_effect=fake_execute)
    return db


@pytest.fixture()
def store() -> ConnectorCredentialStore:
    return make_store()


@pytest.fixture()
def db() -> AsyncMock:
    """Empty AsyncMock — tests set execute behaviour themselves when needed."""
    mock = AsyncMock()
    mock.add = MagicMock()
    mock.flush = AsyncMock()
    return mock
