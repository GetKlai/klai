"""Real-PostgreSQL proof that a replacement claim actually excludes.

``claim_replacement_slot`` exists because a plain SELECT cannot make
check-and-claim one step: two requests both find nothing pending, both
insert, both ingest under the same document key, and the winner becomes
whichever docling task finishes last — possibly the file the user picked
FIRST. The fix is only worth anything if the lock really is exclusive
across concurrent transactions, and no stubbed session can show that: a
mock returns whatever it was told to.

These run against a REAL PostgreSQL and are skipped unless
``RLS_TEST_DATABASE_URL`` is set. CI runs them in the postgres lane
(``pytest -m postgres``).

Local run::

    docker run --rm -d --name claim-test -e POSTGRES_PASSWORD=test \
        -p 55440:5432 postgres:16
    RLS_TEST_DATABASE_URL=postgresql+asyncpg://postgres:test@localhost:55440/postgres \
        uv run pytest tests/test_kb_uploads_claim_postgres.py -m postgres -q
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app.services.kb_uploads_repo import replacement_lock_key

pytestmark = pytest.mark.postgres


@pytest.fixture
async def engine() -> AsyncIterator[AsyncEngine]:
    dsn = os.environ.get("RLS_TEST_DATABASE_URL", "")
    if not dsn:
        pytest.skip("RLS_TEST_DATABASE_URL not set — real-PostgreSQL claim tests skipped")
    eng = create_async_engine(dsn, pool_size=4, max_overflow=0)
    try:
        yield eng
    finally:
        await eng.dispose()


@pytest.mark.asyncio
async def test_a_second_claim_on_the_same_source_is_refused(engine: AsyncEngine) -> None:
    """The whole point: concurrent claims on one source cannot both win."""
    key = replacement_lock_key(42, "file:sha256:contended")

    async with engine.connect() as first, engine.connect() as second:
        await first.begin()
        await second.begin()

        first_got = (await first.execute(select(func.pg_try_advisory_xact_lock(key)))).scalar_one()
        second_got = (await second.execute(select(func.pg_try_advisory_xact_lock(key)))).scalar_one()

        assert first_got is True
        assert second_got is False, "two replacements would race on one document key"


@pytest.mark.asyncio
async def test_a_different_source_is_not_blocked(engine: AsyncEngine) -> None:
    """The claim is per source, not a global replace mutex."""
    mine = replacement_lock_key(42, "file:sha256:mine")
    theirs = replacement_lock_key(42, "file:sha256:theirs")

    async with engine.connect() as first, engine.connect() as second:
        await first.begin()
        await second.begin()

        assert (await first.execute(select(func.pg_try_advisory_xact_lock(mine)))).scalar_one()
        assert (await second.execute(select(func.pg_try_advisory_xact_lock(theirs)))).scalar_one()


@pytest.mark.asyncio
async def test_the_claim_is_released_when_the_transaction_ends(engine: AsyncEngine) -> None:
    """Transaction-scoped, so a crashed request cannot wedge a source.

    A session-scoped lock would survive on a pooled connection and leave the
    source unreplaceable until the process restarted.
    """
    key = replacement_lock_key(42, "file:sha256:released")

    async with engine.connect() as conn:
        await conn.begin()
        assert (await conn.execute(select(func.pg_try_advisory_xact_lock(key)))).scalar_one()
        await conn.rollback()

    async with engine.connect() as other:
        await other.begin()
        assert (await other.execute(select(func.pg_try_advisory_xact_lock(key)))).scalar_one(), (
            "the lock outlived its transaction"
        )
        await other.rollback()


@pytest.mark.asyncio
async def test_the_key_fits_the_bigint_the_lock_takes(engine: AsyncEngine) -> None:
    """A key outside int8 would raise at runtime, not at import."""
    key = replacement_lock_key(2**31, "file:sha256:" + "f" * 64)
    assert -(2**63) <= key < 2**63

    async with engine.connect() as conn:
        await conn.begin()
        assert (await conn.execute(select(func.pg_try_advisory_xact_lock(key)))).scalar_one()
        await conn.rollback()
