"""SPEC-TI-003-FOLLOWUP-001 — RLS wiring regression tests.

Two complementary tests cover the contract introduced by this SPEC:

* **AC-5** (concurrent isolation): two ``tenant_scoped_connection`` blocks
  for different orgs run on real Postgres at the same time. Each block
  reads back ``app.current_org_id`` via ``SELECT current_setting(...)`` and
  must see its own value -- proves that the GUC is connection-local and
  that the helper does not bleed between concurrent acquirers.

* **AC-6** (type-time + run-time guard): calling a refactored pg_store
  function without the leading ``conn`` argument raises ``TypeError`` at
  run-time. The accompanying ``# type: ignore`` comment is the only place
  in the test file that suppresses pyright/mypy -- in production code the
  same call will fail the type checker, which is the structural contract
  AC-6 protects.

The integration test is gated behind ``RUN_INTEGRATION=1`` so the default
fast suite stays mock-only. CI can opt in by setting the env var and
providing ``POSTGRES_DSN`` pointing at a database where post-deploy SQL
has been applied (see
``alembic/versions/post_deploy_dd1b439a57d0.sql``).
"""

from __future__ import annotations

import asyncio
import os

import pytest

_REQUIRES_INTEGRATION = os.getenv("RUN_INTEGRATION") != "1"


# ---------------------------------------------------------------------------
# AC-5: concurrent tenant_scoped_connection blocks see their own GUC.
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    _REQUIRES_INTEGRATION,
    reason=(
        "Integration test requires a real Postgres reachable via "
        "POSTGRES_DSN. Run with RUN_INTEGRATION=1."
    ),
)
@pytest.mark.asyncio
async def test_concurrent_tenant_scoped_connections_no_guc_bleed() -> None:
    """SPEC-TI-003-FOLLOWUP-001 AC-5.

    Two parallel ``tenant_scoped_connection`` blocks for different orgs
    must each read back THEIR OWN ``app.current_org_id`` -- proves that
    the GUC is pinned per connection and survives concurrent acquirers
    from the same pool.
    """
    from knowledge_ingest.db import close_pool, tenant_scoped_connection

    org_a = "rls-test-org-a-2026-05-06"
    org_b = "rls-test-org-b-2026-05-06"

    a_observed: list[str] = []
    b_observed: list[str] = []

    async def _read_back(org_id: str, observed: list[str]) -> None:
        async with tenant_scoped_connection(org_id) as conn:
            # SELECT inside the block must see the GUC the helper set.
            value = await conn.fetchval("SELECT current_setting('app.current_org_id', true)")
            observed.append(value or "")
            # Sleep briefly so both blocks overlap in time -- if they
            # somehow shared a connection the second SET would clobber
            # the first.
            await asyncio.sleep(0.05)
            value_after_sleep = await conn.fetchval(
                "SELECT current_setting('app.current_org_id', true)"
            )
            observed.append(value_after_sleep or "")

    try:
        await asyncio.gather(
            _read_back(org_a, a_observed),
            _read_back(org_b, b_observed),
        )
    finally:
        await close_pool()

    assert a_observed == [org_a, org_a], (
        f"Expected org A's block to see its own GUC twice; got {a_observed!r}. "
        "Possible regression: tenant_scoped_connection no longer pins the GUC "
        "per-connection, or the pool returned the same physical conn to both "
        "blocks (which the asyncpg pool does not do, but worth diagnosing)."
    )
    assert b_observed == [org_b, org_b], (
        f"Expected org B's block to see its own GUC twice; got {b_observed!r}."
    )


@pytest.mark.skipif(
    _REQUIRES_INTEGRATION,
    reason=(
        "Integration test requires a real Postgres reachable via "
        "POSTGRES_DSN. Run with RUN_INTEGRATION=1."
    ),
)
@pytest.mark.asyncio
async def test_tenant_scoped_connection_resets_guc_on_exit() -> None:
    """SPEC-TI-003-FOLLOWUP-001 AC-5 follow-on.

    After the ``async with tenant_scoped_connection`` block exits the
    pinned conn is returned to the pool. The next acquire from that pool
    must NOT see the previous tenant's GUC -- otherwise a follow-up
    request reusing the same physical conn would silently inherit the
    wrong tenant context.
    """
    from knowledge_ingest.db import close_pool, get_pool, tenant_scoped_connection

    org_id = "rls-test-org-reset-2026-05-06"

    try:
        async with tenant_scoped_connection(org_id) as conn:
            inside = await conn.fetchval("SELECT current_setting('app.current_org_id', true)")
            assert inside == org_id

        # Acquire raw pool conn and inspect the GUC: it should be empty
        # because the helper reset it on exit.
        pool = await get_pool()
        async with pool.acquire() as raw_conn:
            after = await raw_conn.fetchval("SELECT current_setting('app.current_org_id', true)")
            assert after in (None, ""), (
                f"GUC not reset on exit -- pool returned a conn carrying "
                f"app.current_org_id={after!r} from a prior tenant. This is "
                f"the pool-GUC pollution pitfall the helper guards against."
            )
    finally:
        await close_pool()


# ---------------------------------------------------------------------------
# AC-6: pg_store functions require conn at call-time.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pg_store_function_without_conn_raises_typeerror() -> None:
    """SPEC-TI-003-FOLLOWUP-001 AC-6.

    Calling a refactored pg_store helper without the leading ``conn``
    argument must raise ``TypeError`` at run-time. The matching
    ``# type: ignore[call-arg]`` below is the only place we suppress the
    type checker -- in production code the same shape fails pyright/mypy
    BEFORE the code runs, which is the structural contract this AC
    protects.
    """
    from knowledge_ingest import pg_store

    # ``create_artifact`` is one of the 26 functions reshaped by AC-1.
    # The current signature is:
    #     async def create_artifact(conn, org_id, kb_slug, path, ...)
    # Calling it with org_id as the first positional binds org_id to the
    # ``conn`` parameter, leaving the actual ``org_id`` keyword unfilled.
    # Both pyright and a runtime call see that as a missing-argument /
    # type error.
    with pytest.raises(TypeError):
        await pg_store.create_artifact(  # type: ignore[call-arg]
            org_id="dummy-org",
            kb_slug="dummy-kb",
            path="dummy/path.md",
            provenance_type="observed",
            assertion_mode="unknown",
            synthesis_depth=0,
            confidence=None,
            belief_time_start=0,
            belief_time_end=0,
        )


def test_pg_store_module_does_not_import_get_pool() -> None:
    """SPEC-TI-003-FOLLOWUP-001 AC-2 follow-on.

    A static guard against future regressions: pg_store must not import
    ``get_pool`` (or call ``pool.acquire``) at all. The module only ever
    runs SQL via the caller-supplied ``conn``. Re-introducing the pool
    import in pg_store is the exact regression the SPEC closed.
    """
    from pathlib import Path

    src = Path(__file__).parent.parent / "knowledge_ingest" / "pg_store.py"
    text = src.read_text(encoding="utf-8")
    assert "from knowledge_ingest.db import get_pool" not in text, (
        "pg_store.py re-introduced get_pool. AC-2 forbids pool.acquire() inside "
        "pg_store -- caller MUST hand down the GUC-pinned conn."
    )
    assert "get_pool()" not in text, (
        "pg_store.py calls get_pool(). AC-2 forbids pool acquisition inside this module."
    )
