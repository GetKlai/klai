"""Conftest for eval tests.

The eval tests use procrastinate.testing.InMemoryConnector to exercise the
real queueing_lock machinery without a live database. On Windows dev the
system libpq is absent, so procrastinate's top-level __init__ fails when it
imports PsycopgConnector. We stub the psycopg / psycopg_pool packages before
procrastinate is imported so the InMemoryConnector path remains exercisable.

This stub must be installed before any test module that uses procrastinate
is collected, hence the conftest location.
"""

from __future__ import annotations

import sys
import types
from unittest.mock import MagicMock


def _stub_psycopg_if_absent() -> None:
    """Install lightweight psycopg stubs when the real library is not present.

    Idempotent — no-op if psycopg already imported successfully.
    """
    if "psycopg" in sys.modules:
        # Already loaded (either real or previously stubbed) — leave as-is.
        return

    psycopg_pkg = types.ModuleType("psycopg")
    psycopg_pkg.__path__ = []  # type: ignore[attr-defined]
    psycopg_pkg.__package__ = "psycopg"
    psycopg_pkg.Connection = MagicMock()  # type: ignore[attr-defined]
    psycopg_pkg.AsyncConnection = MagicMock()  # type: ignore[attr-defined]
    sys.modules["psycopg"] = psycopg_pkg

    for sub in ["pq", "rows", "abc", "adapt", "errors", "types", "types.json"]:
        m = types.ModuleType(f"psycopg.{sub}")
        m.__package__ = "psycopg"  # type: ignore[attr-defined]
        sys.modules[f"psycopg.{sub}"] = m

    pool_mod = types.ModuleType("psycopg_pool")
    pool_mod.AsyncConnectionPool = MagicMock()  # type: ignore[attr-defined]
    pool_mod.ConnectionPool = MagicMock()  # type: ignore[attr-defined]
    sys.modules["psycopg_pool"] = pool_mod


_stub_psycopg_if_absent()
