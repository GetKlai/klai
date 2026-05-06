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


def _swap_in_real_procrastinate() -> None:
    """Replace the parent conftest's procrastinate stub with the real
    package. The eval suite needs the genuine
    ``procrastinate.testing.InMemoryConnector`` to drive queueing-lock
    semantics; the parent stub only exposes attribute placeholders. With
    psycopg already stubbed (above), the real procrastinate import does
    not need a libpq backend.

    Importantly: eagerly re-import real procrastinate **here** so that
    every module collected after this point sees ``procrastinate`` in
    ``sys.modules``. Test files like ``test_extra_payload_contract.py``
    install their own *partial* stub at module-level guarded by
    ``if "procrastinate" in sys.modules: return`` -- if we just pop the
    entry without forcing the real load, the next test module's guard
    sees an empty slot and races to install a partial stub that lacks
    ``App``, breaking the eval tests downstream.
    """
    sys.modules.pop("procrastinate", None)
    sys.modules.pop("procrastinate.exceptions", None)
    sys.modules.pop("procrastinate.testing", None)
    # Force the real import now; the per-file guards in test_*.py modules
    # will skip on the next pytest pass because the entry is populated.
    import procrastinate
    import procrastinate.exceptions
    import procrastinate.testing  # noqa: F401


_stub_psycopg_if_absent()
_swap_in_real_procrastinate()
