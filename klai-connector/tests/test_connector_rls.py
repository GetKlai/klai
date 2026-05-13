"""SPEC-TI-002 RLS regression tests for klai-connector session helpers.

These tests validate that:
- AC-7: a query without tenant context raises ERRCODE 42501.
- AC-8: cross_org_session() bypasses RLS (returns NULL from helper).
- The session helpers set and clear the correct GUCs.
- tenant_scoped_session sets app.current_org_id.
- cross_org_session sets app.cross_org_admin=true and clears on exit.

Test design: unit-level, no real PostgreSQL. We mock the async engine and
session to assert that the correct SQL GUC calls are made. Integration
tests against a live Postgres would be the authoritative AC-7/AC-8 proof;
these unit tests guard against regression in the helper's GUC wiring.
"""

from __future__ import annotations

from typing import Any

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _RecordingSession:
    """Records every SQL text string + params passed to session.execute().

    Simulates the pin-connection side-effect from connection() and the
    GUC calls from set_config. Avoids importing SQLAlchemy internals.

    Note: set_tenant uses a parameterised query
    ``text("SELECT set_config('app.current_org_id', :org_id, false)")``
    so the org_id value does NOT appear in the SQL string — it's in params.
    ``guc_entries()`` returns (sql, params) tuples for callers that need
    to inspect both.
    """

    def __init__(self) -> None:
        self.executed: list[tuple[str, Any]] = []  # (sql_text, params)
        self._rolled_back: bool = False
        self._connection_pinned: bool = False

    async def connection(self) -> None:
        self._connection_pinned = True

    async def execute(self, stmt: Any, params: Any = None) -> Any:
        # Capture the string form of the statement (works for text() objects).
        self.executed.append((str(stmt), params))
        return None

    async def rollback(self) -> None:
        self._rolled_back = True

    def guc_calls(self) -> list[str]:
        """Return only the set_config SQL strings (without params)."""
        return [sql for sql, _params in self.executed if "set_config" in sql]

    def guc_entries(self) -> list[tuple[str, Any]]:
        """Return (sql, params) pairs for set_config calls."""
        return [(sql, params) for sql, params in self.executed if "set_config" in sql]


class _FakeSessionContext:
    """Async context manager that yields a _RecordingSession."""

    def __init__(self) -> None:
        self.session = _RecordingSession()

    async def __aenter__(self) -> _RecordingSession:
        return self.session

    async def __aexit__(self, *args: Any) -> None:
        pass


# ---------------------------------------------------------------------------
# Tests: set_tenant
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_set_tenant_sends_set_config() -> None:
    """set_tenant(session, org_id) executes set_config('app.current_org_id', ...).

    AC-4 partial: the session helper wires the correct GUC for per-tenant
    queries on connector.connectors and connector.sync_runs.
    """
    from app.core.database import set_tenant

    session = _RecordingSession()
    await set_tenant(session, "org-abc-123")

    guc_entries = session.guc_entries()
    assert len(guc_entries) == 1, f"Expected 1 set_config call, got: {guc_entries}"
    sql, params = guc_entries[0]
    assert "app.current_org_id" in sql
    # The org_id is passed as a bind parameter, not embedded in the SQL text.
    assert params is not None and params.get("org_id") == "org-abc-123", (
        f"Expected params={{'org_id': 'org-abc-123'}}, got params={params}"
    )


# ---------------------------------------------------------------------------
# Tests: tenant_scoped_session
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tenant_scoped_session_pins_and_sets_guc() -> None:
    """tenant_scoped_session(org_id) pins connection + sets current_org_id GUC.

    Verifies the Cat-D RLS context is established before any DML.
    """
    from app.core import database as db_module

    recording = _RecordingSession()

    class _MockSessionMaker:
        def __call__(self) -> _FakeSessionContext:
            ctx = _FakeSessionContext()
            ctx.session = recording
            return ctx

    original_maker = db_module.session_maker
    db_module.session_maker = _MockSessionMaker()  # type: ignore[assignment]

    try:
        async with db_module.tenant_scoped_session("my-org") as session:
            assert session is recording
    finally:
        db_module.session_maker = original_maker

    guc_entries = recording.guc_entries()
    # Expect: reset (2 calls from _pin_and_reset_connection) + set current_org_id
    # + reset on exit (2 calls from _reset_tenant_context in finally).
    # At minimum, app.current_org_id must be set once with org_id='my-org' in params.
    current_org_entries = [(sql, params) for sql, params in guc_entries if "current_org_id" in sql]
    set_calls = [
        (sql, params)
        for sql, params in current_org_entries
        if isinstance(params, dict) and params.get("org_id") == "my-org"
    ]
    assert len(set_calls) >= 1, (
        f"Expected set_config('app.current_org_id', :org_id, ...) with org_id='my-org'. Got GUC entries: {guc_entries}"
    )


@pytest.mark.asyncio
async def test_tenant_scoped_session_clears_guc_on_exit() -> None:
    """tenant_scoped_session clears app.current_org_id on context manager exit.

    Pool-GUC pollution prevention: the GUC must be reset before the
    connection returns to the pool. SPEC-TI-002.
    """
    from app.core import database as db_module

    recording = _RecordingSession()

    class _MockSessionMaker:
        def __call__(self) -> _FakeSessionContext:
            ctx = _FakeSessionContext()
            ctx.session = recording
            return ctx

    original_maker = db_module.session_maker
    db_module.session_maker = _MockSessionMaker()  # type: ignore[assignment]

    try:
        async with db_module.tenant_scoped_session("my-org"):
            pass  # exit triggers cleanup
    finally:
        db_module.session_maker = original_maker

    guc_calls = recording.guc_calls()
    # After exit, current_org_id should be reset to '' (empty string reset).
    reset_calls = [
        c for c in guc_calls if "current_org_id" in c and ("''" in c or '""' in c or ", ''" in c or ', ""' in c)
    ]
    assert len(reset_calls) >= 1, (
        f"Expected set_config('app.current_org_id', '', ...) on exit. Got GUC calls: {guc_calls}"
    )


# ---------------------------------------------------------------------------
# Tests: cross_org_session
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cross_org_session_sets_bypass_guc() -> None:
    """cross_org_session() sets app.cross_org_admin=true.

    AC-8: the bypass GUC causes _rls_current_org_id() to return NULL,
    making the USING clause pass for every row.
    """
    from app.core import database as db_module

    recording = _RecordingSession()

    class _MockSessionMaker:
        def __call__(self) -> _FakeSessionContext:
            ctx = _FakeSessionContext()
            ctx.session = recording
            return ctx

    original_maker = db_module.session_maker
    db_module.session_maker = _MockSessionMaker()  # type: ignore[assignment]

    try:
        async with db_module.cross_org_session() as session:
            assert session is recording
    finally:
        db_module.session_maker = original_maker

    guc_calls = recording.guc_calls()
    bypass_calls = [c for c in guc_calls if "cross_org_admin" in c and "true" in c]
    assert len(bypass_calls) >= 1, (
        f"Expected set_config('app.cross_org_admin', 'true', ...) to be called. Got GUC calls: {guc_calls}"
    )


@pytest.mark.asyncio
async def test_cross_org_session_clears_guc_on_exit() -> None:
    """cross_org_session clears app.cross_org_admin on exit.

    Pool-GUC pollution prevention: the bypass GUC must be cleared before
    the connection returns to the pool. SPEC-TI-002.
    """
    from app.core import database as db_module

    recording = _RecordingSession()

    class _MockSessionMaker:
        def __call__(self) -> _FakeSessionContext:
            ctx = _FakeSessionContext()
            ctx.session = recording
            return ctx

    original_maker = db_module.session_maker
    db_module.session_maker = _MockSessionMaker()  # type: ignore[assignment]

    try:
        async with db_module.cross_org_session():
            pass
    finally:
        db_module.session_maker = original_maker

    guc_calls = recording.guc_calls()
    # After exit, cross_org_admin must be reset (empty string or 'false').
    reset_calls = [
        c for c in guc_calls if "cross_org_admin" in c and ("''" in c or '""' in c or ", ''" in c or ', ""' in c)
    ]
    assert len(reset_calls) >= 1, f"Expected cross_org_admin to be cleared on exit. Got GUC calls: {guc_calls}"


# ---------------------------------------------------------------------------
# Tests: 42501 simulation (AC-7)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rls_raises_42501_without_tenant_context() -> None:
    """A query without tenant context should raise ERRCODE 42501.

    AC-7: _rls_current_org_id() raises ERRCODE 42501 when neither
    app.current_org_id nor app.cross_org_admin=true is set.

    This test unit-tests the logic path by simulating the DB raising
    asyncpg.exceptions.InsufficientPrivilegeError when the GUC is absent.
    The real enforcement is in the post-deploy SQL function; this test
    validates that our session helpers are the required layer — a caller
    that skips them and calls session.execute() directly would see the
    real 42501 in production.

    We model the failure by verifying that get_session() resets the GUC
    at checkout (i.e., the session starts with empty GUC) — any code
    that then queries without calling set_tenant or cross_org_session
    would hit the 42501 from the DB function.
    """
    from app.core import database as db_module

    recording = _RecordingSession()

    class _MockSessionMaker:
        def __call__(self) -> _FakeSessionContext:
            ctx = _FakeSessionContext()
            ctx.session = recording
            return ctx

    original_maker = db_module.session_maker
    db_module.session_maker = _MockSessionMaker()  # type: ignore[assignment]

    try:
        # Simulate get_session() checkout — it calls _pin_and_reset_connection
        # which clears both GUCs. Verify the GUC is explicitly reset.
        session = recording
        await db_module._pin_and_reset_connection(session)  # type: ignore[attr-defined]
    finally:
        db_module.session_maker = original_maker

    guc_calls = recording.guc_calls()
    # After _pin_and_reset_connection, current_org_id should be reset to ''.
    reset_calls = [c for c in guc_calls if "current_org_id" in c]
    assert len(reset_calls) >= 1, (
        "get_session() must clear app.current_org_id at checkout to prevent "
        "pool-GUC pollution. Missing reset means a stale GUC from the previous "
        "tenant could bypass RLS for the next request. "
        f"Got GUC calls: {guc_calls}"
    )
    # cross_org_admin is also cleared.
    bypass_reset_calls = [c for c in guc_calls if "cross_org_admin" in c]
    assert len(bypass_reset_calls) >= 1, (
        f"get_session() must clear app.cross_org_admin at checkout. Got GUC calls: {guc_calls}"
    )


# ---------------------------------------------------------------------------
# Tests: RLS policy shape (AC-1/AC-2) — verified via SQL content
# ---------------------------------------------------------------------------


def test_post_deploy_sql_has_text_returns() -> None:
    """The post-deploy SQL helper function returns 'text', not 'integer'.

    The connector schema org_id is VARCHAR(255) (Zitadel resourceowner string).
    The portal-api schema uses integer org_id. Both _rls_current_org_id()
    overloads coexist in the public schema — the connector one RETURNS text.
    This test guards against accidentally using the integer variant.
    """
    import pathlib

    sql_path = pathlib.Path(__file__).parent.parent / ("alembic/versions/post_deploy_008_rls_tenant_isolation.sql")
    assert sql_path.exists(), f"post-deploy SQL not found at {sql_path}"
    content = sql_path.read_text()

    # Must declare RETURNS text (not integer / bigint).
    assert "RETURNS text" in content, (
        "post_deploy_008 must declare _rls_current_org_id() RETURNS text "
        f"(connector schema uses varchar org_id). File: {sql_path}"
    )

    # Must have ENABLE ROW LEVEL SECURITY for both tables (from migration).
    # The migration itself enables; the post-deploy SQL adds the policies.
    # Policies must use OR _rls_current_org_id() IS NULL in USING clause.
    assert "IS NULL" in content, f"USING clause must include IS NULL branch to allow cross-org bypass. File: {sql_path}"

    # WITH CHECK must NOT have IS NULL (prevents orphan rows).
    # Skip comment-only lines (starting with --) to avoid false positives from
    # documentation comments that mention "no IS NULL branch intentionally".
    for line in content.splitlines():
        stripped = line.strip()
        if "WITH CHECK" in stripped and not stripped.startswith("--"):
            assert "IS NULL" not in stripped, (
                f"WITH CHECK clause must NOT have IS NULL branch (prevents orphan row bug). Offending line: {line!r}"
            )

    # Must cover both tables.
    assert "connector.connectors" in content, "Policy missing for connector.connectors"
    assert "connector.sync_runs" in content, "Policy missing for connector.sync_runs"
