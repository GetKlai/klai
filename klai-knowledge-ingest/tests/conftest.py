"""Shared pytest fixtures for knowledge-ingest tests.

SPEC-SEC-011: ``KNOWLEDGE_INGEST_SECRET`` is now a required environment
variable — ``Settings()`` raises ``ValidationError`` when it is missing.
We set a test value before any ``knowledge_ingest`` module is imported so
``config.py``'s module-level ``settings = Settings()`` succeeds during
test collection. The real production deploy injects this via SOPS.

SPEC-TI-003-FOLLOWUP-001: ``tenant_scoped_connection`` now does
``pool.acquire()`` and yields the connection to callers. The mock pool
fixture exposes ``acquire()`` as an async context manager yielding a
mock connection so unit tests that exercise the FastAPI app via
``TestClient`` -- and therefore go through ``tenant_scoped_connection``
-- still work without a real Postgres.
"""

from __future__ import annotations

import os
import sys
import types

os.environ.setdefault("KNOWLEDGE_INGEST_SECRET", "test-secret-value-123")
os.environ.setdefault("PORTAL_INTERNAL_TOKEN", "test-portal-internal-token-456")  # SEC-014
# SPEC-SEC-VALIDATOR-COVERAGE-001 REQ-12 — gitea webhook HMAC secret.
os.environ.setdefault("GITEA_WEBHOOK_SECRET", "test-gitea-webhook-secret-789")
# Default ``enrichment_enabled = True`` makes the FastAPI lifespan spawn a
# real Procrastinate worker, which fails in the test environment (no libpq).
# Force the kill-switch off via env var so every fresh ``Settings()``
# instantiation -- including ones triggered by test files that pop
# ``knowledge_ingest.config`` from ``sys.modules`` -- starts with the worker
# disabled. Tests that *want* to exercise the enrichment path patch
# ``settings.enrichment_enabled = True`` themselves.
os.environ.setdefault("ENRICHMENT_ENABLED", "false")


# ---------------------------------------------------------------------------
# Procrastinate stub (see audit-2026-05-06 finding: module-level stubs in
# individual test files left an INCOMPLETE ``procrastinate`` module in
# ``sys.modules``. Subsequent tests that triggered ``WorkerLifecycle`` (via the
# default ``settings.enrichment_enabled = True``) cascaded with
# ``AttributeError: module 'procrastinate' has no attribute 'PsycopgConnector'``.
#
# The fix: install a *complete* stub here at conftest import time (before any
# test module loads). The stub covers every attribute production code reads
# (``PsycopgConnector``, ``App``, ``RetryStrategy``, ``BaseRetryStrategy``,
# ``JobContext``, ``RetryDecision``, plus ``exceptions.AlreadyEnqueued``).
# Individual test files keep their own ``_install_procrastinate_stub()``
# helpers as no-ops because they all guard with
# ``if "procrastinate" in sys.modules: return``.
# ---------------------------------------------------------------------------


class _AlreadyEnqueued(Exception):
    """Stub for procrastinate.exceptions.AlreadyEnqueued."""


def _install_procrastinate_stub() -> None:
    if "procrastinate" in sys.modules:
        return
    exceptions_mod = types.ModuleType("procrastinate.exceptions")
    exceptions_mod.AlreadyEnqueued = _AlreadyEnqueued  # type: ignore[attr-defined]

    pkg = types.ModuleType("procrastinate")
    pkg.exceptions = exceptions_mod  # type: ignore[attr-defined]

    # Production code does decorator chains like
    # ``@procrastinate_app.task(queue=..., retry=procrastinate.RetryStrategy(...))``
    # where ``procrastinate_app = procrastinate.App(connector=connector)``.
    # ``MagicMock`` lets us absorb arbitrary attribute / call chains without
    # forcing each test that imports a module-level decorated task to mock
    # everything by hand.
    from unittest.mock import MagicMock  # local import: only needed here

    pkg.PsycopgConnector = MagicMock(name="PsycopgConnector")  # type: ignore[attr-defined]
    pkg.App = MagicMock(name="App")  # type: ignore[attr-defined]
    pkg.RetryStrategy = MagicMock(name="RetryStrategy")  # type: ignore[attr-defined]
    pkg.BaseRetryStrategy = type("BaseRetryStrategy", (), {})  # type: ignore[attr-defined]
    pkg.JobContext = MagicMock(name="JobContext")  # type: ignore[attr-defined]
    pkg.RetryDecision = MagicMock(name="RetryDecision")  # type: ignore[attr-defined]

    # ``procrastinate.testing`` exposes ``InMemoryConnector`` -- the eval
    # suite imports it to exercise queueing-lock semantics without a real
    # database. Provide a placeholder so the import succeeds.
    testing_mod = types.ModuleType("procrastinate.testing")
    testing_mod.InMemoryConnector = MagicMock(name="InMemoryConnector")  # type: ignore[attr-defined]
    pkg.testing = testing_mod  # type: ignore[attr-defined]

    sys.modules["procrastinate"] = pkg
    sys.modules["procrastinate.exceptions"] = exceptions_mod
    sys.modules["procrastinate.testing"] = testing_mod


_install_procrastinate_stub()


# Imports below need the env vars above — keep this order to allow the
# module-level ``settings = Settings()`` call in ``knowledge_ingest.config``
# to succeed under the SPEC-SEC-011 / SEC-014 validators.
from contextlib import asynccontextmanager  # noqa: E402
from unittest.mock import AsyncMock, MagicMock, patch  # noqa: E402

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

# Default header that lets existing TestClient requests pass through the
# SPEC-SEC-011 ``InternalSecretMiddleware`` without each test having to set
# it explicitly. Matches the value seeded into the process environment above.
_INTERNAL_HEADER = {"X-Internal-Secret": os.environ["KNOWLEDGE_INGEST_SECRET"]}


def _make_mock_conn():
    """Return a mock asyncpg.Connection that returns benign defaults.

    Tests that need specific behaviour patch the pg_store helper directly
    (e.g. ``patch("knowledge_ingest.pg_store.create_artifact", AsyncMock(...))``)
    rather than reaching through this stub.
    """
    conn = MagicMock()
    conn.execute = AsyncMock(return_value=None)
    conn.executemany = AsyncMock(return_value=None)
    conn.fetch = AsyncMock(return_value=[])
    conn.fetchrow = AsyncMock(return_value=None)
    conn.fetchval = AsyncMock(return_value=None)

    # Transaction context manager (used by pg_store helpers like delete_kb).
    @asynccontextmanager
    async def _tx():
        yield None

    conn.transaction = MagicMock(side_effect=_tx)
    return conn


def _make_mock_pool():
    """Return a mock asyncpg pool that yields a mock conn from acquire().

    Mirrors the asyncpg.Pool surface that ``tenant_scoped_connection`` and
    ``cross_org_admin_connection`` exercise: ``async with pool.acquire() as
    conn`` must yield something the caller can ``execute`` / ``fetch`` /
    ``fetchval`` against without raising.
    """
    pool = MagicMock()
    pool.execute = AsyncMock(return_value=None)
    pool.fetch = AsyncMock(return_value=[])
    pool.fetchval = AsyncMock(return_value=None)
    pool.fetchrow = AsyncMock(return_value=None)
    pool.close = AsyncMock(return_value=None)

    conn = _make_mock_conn()

    @asynccontextmanager
    async def _acquire():
        yield conn

    pool.acquire = MagicMock(side_effect=_acquire)
    # Expose the conn so tests can assert against it if needed.
    pool._mock_conn = conn  # type: ignore[attr-defined]
    return pool


@pytest.fixture
def mock_pool():
    return _make_mock_pool()


def make_pg_store_mock():
    """All-async mock for the ``knowledge_ingest.pg_store`` module.

    Returns an :class:`AsyncMock` with ``spec=pg_store`` so every helper
    (existing AND any future addition) is auto-mocked as an async no-op.
    Use this in new tests instead of ``MagicMock()`` plus manual per-method
    ``AsyncMock`` assignments — that pattern silently breaks every time a
    new pg_store helper is added (e.g.
    ``update_crawled_page_simhash`` from SPEC-INGEST-LOGIN-WALL-DETECT-002).
    Existing tests are not retroactively migrated; the helper is opt-in.
    """
    import knowledge_ingest.pg_store as _pg_store_module

    return AsyncMock(spec=_pg_store_module)


@pytest.fixture
def pg_store_mock():
    return make_pg_store_mock()


@pytest.fixture(autouse=True)
def _mock_db_helpers(request):
    """Autouse: replace tenant_scoped_connection + cross_org_admin_connection
    with no-op async context managers yielding a mock conn.

    SPEC-TI-003-FOLLOWUP-001 turned every pg_store / kb_config / org_config
    call into a ``conn``-passing call, and the route / task entry points
    now wrap each request in ``tenant_scoped_connection``. Tests that hit
    the FastAPI app via TestClient would otherwise need a real Postgres
    just to satisfy the helper's ``pool.acquire()`` call. This fixture
    intercepts both helpers so tests can run without one.

    Tests that exercise the real helper (``test_db_rls_wiring.py``) opt out
    by adding the ``no_mock_db_helpers`` marker, e.g.::

        @pytest.mark.no_mock_db_helpers
        async def test_real_postgres_thing(): ...
    """
    if request.node.get_closest_marker("no_mock_db_helpers"):
        yield
        return

    @asynccontextmanager
    async def _fake_tenant(org_id: str):
        yield _make_mock_conn()

    @asynccontextmanager
    async def _fake_admin():
        yield _make_mock_conn()

    # Reset the module-level pool global so a prior test that triggered
    # the real get_pool() does not leak a half-connected pool into this
    # test's patched view.
    try:
        import knowledge_ingest.db as _db_mod

        _db_mod._pool = None
    except Exception:  # noqa: S110
        pass

    targets = [
        "knowledge_ingest.db.tenant_scoped_connection",
        "knowledge_ingest.routes.ingest.tenant_scoped_connection",
        "knowledge_ingest.routes.crawl.tenant_scoped_connection",
        "knowledge_ingest.routes.personal.tenant_scoped_connection",
        "knowledge_ingest.routes.kb_sources.tenant_scoped_connection",
        "knowledge_ingest.crawl_tasks.tenant_scoped_connection",
        "knowledge_ingest.rebuild_tasks.tenant_scoped_connection",
        "knowledge_ingest.enrichment_tasks.tenant_scoped_connection",
        "knowledge_ingest.connector_cleanup.tenant_scoped_connection",
    ]
    admin_targets = [
        "knowledge_ingest.db.cross_org_admin_connection",
        "knowledge_ingest.backfill.cross_org_admin_connection",
        "knowledge_ingest.enrichment_tasks.cross_org_admin_connection",
    ]

    patchers = []
    for target in targets:
        try:
            p = patch(target, _fake_tenant)
            p.start()
            patchers.append(p)
        except (ImportError, AttributeError, ModuleNotFoundError):
            # Module not yet imported by this test session; harmless.
            continue
    for target in admin_targets:
        try:
            p = patch(target, _fake_admin)
            p.start()
            patchers.append(p)
        except (ImportError, AttributeError, ModuleNotFoundError):
            continue

    try:
        yield
    finally:
        for p in patchers:
            try:
                p.stop()
            except RuntimeError:
                pass


@pytest.fixture(autouse=True)
def _reset_shared_klai_fast_limiter():
    """Reset the process-wide klai-fast token bucket before each test.

    ``knowledge_ingest.llm_throttle.shared_klai_fast_limiter()`` is a lazy
    singleton by design (one bucket per production process). Left
    unreset across the test suite, its default burst capacity (10)
    would be exhausted after the first ~10 tests that exercise a real
    ``_call_litellm``/``_call_llm`` path, and every test after that
    would pay the 1/0.6s pacing delay -- silently multiplying suite
    runtime without any test asserting on it. Resetting to ``None``
    gives each test a fresh, full-burst bucket.
    """
    import knowledge_ingest.llm_throttle as _llm_throttle_mod

    _llm_throttle_mod._shared_limiter = None
    yield
    _llm_throttle_mod._shared_limiter = None


@pytest.fixture
def client(mock_pool):
    """Test client with auth middleware.

    Patches qdrant and db pool to skip startup, and injects the default
    ``X-Internal-Secret`` header so pre-existing tests (written before
    SPEC-SEC-011) continue to exercise the happy-path flows without
    modification. Auth-focused tests that must exercise the 401 path should
    build their own TestClient instead.
    """
    with (
        patch("knowledge_ingest.qdrant_store.ensure_collection", new_callable=AsyncMock),
        patch("knowledge_ingest.db.get_pool", new_callable=AsyncMock, return_value=mock_pool),
        patch("knowledge_ingest.db.close_pool", new_callable=AsyncMock),
        patch("knowledge_ingest.config.settings.enrichment_enabled", False),
    ):
        from knowledge_ingest.app import app

        with TestClient(app, raise_server_exceptions=False) as c:
            c.headers.update(_INTERNAL_HEADER)
            yield c
