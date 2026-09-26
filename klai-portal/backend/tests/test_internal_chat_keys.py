"""The internal-chat key is minted once per org, stored hashed, and replaced only on request."""

from __future__ import annotations

import hashlib
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from helpers import FakeResult, setup_db

from app.services.internal_chat_keys import INTERNAL_CHAT_RATE_LIMIT_RPM, mint_internal_chat_key


def _db(existing: list) -> AsyncMock:
    db = AsyncMock()
    db.add = MagicMock()
    setup_db(db, [FakeResult(existing)])
    return db


@pytest.mark.asyncio
async def test_first_mint_returns_the_plaintext_and_stores_only_its_hash():
    db = _db([])

    plaintext = await mint_internal_chat_key(db, 7)

    assert plaintext is not None and plaintext.startswith("pk_live_")
    row = db.add.call_args.args[0]
    assert row.key_hash == hashlib.sha256(plaintext.encode()).hexdigest()
    assert plaintext not in {row.name, row.key_prefix, row.key_hash, row.created_by}
    assert row.org_id == 7
    assert row.permissions == {"chat": True, "general_chat": True, "internal_chat": True}
    assert row.rate_limit_rpm == INTERNAL_CHAT_RATE_LIMIT_RPM
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_second_mint_reports_the_existing_key_and_changes_nothing():
    db = _db([SimpleNamespace(id="key-1", key_hash="0" * 64)])

    assert await mint_internal_chat_key(db, 7) is None

    db.add.assert_not_called()
    db.delete.assert_not_awaited()
    db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_rotate_replaces_the_existing_key_in_one_commit():
    old = SimpleNamespace(id="key-1", key_hash="0" * 64)
    db = _db([old])

    plaintext = await mint_internal_chat_key(db, 7, rotate=True)

    assert plaintext is not None
    db.delete.assert_awaited_once_with(old)
    assert db.add.call_args.args[0].key_hash == hashlib.sha256(plaintext.encode()).hexdigest()
    db.commit.assert_awaited_once()
