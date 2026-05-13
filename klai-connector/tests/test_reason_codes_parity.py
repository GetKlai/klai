"""SPEC-INGEST-RECONCILE-001 AC-9 — connector / knowledge-ingest enum parity.

The two services hold independent copies of the reason-code enums to
avoid an import-time dependency between deployables (rationale:
SPEC §"Stable reason-code registry"). Drift is dangerous:

- A reason added to one side but missing from the other lets the
  Postgres CHECK constraint reject what the other side writes.
- A typo on either side becomes a silent dashboard category — the
  exact failure mode this SPEC is designed to prevent.

This test compares enum value sets between the two copies and FAILS
loudly on any divergence. It runs as part of the connector test suite
because the connector is the authoritative writer of skip_reasons; the
mirror test in knowledge-ingest is symmetric.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

from app.reason_codes import FETCH_REASON_VALUES as CONNECTOR_FETCH
from app.reason_codes import PERSIST_SKIP_REASON_VALUES as CONNECTOR_PERSIST


def _load_knowledge_ingest_reason_codes():
    """Import the knowledge-ingest copy from the sibling service path."""
    repo_root = Path(__file__).resolve().parents[2]
    target = repo_root / "klai-knowledge-ingest" / "knowledge_ingest" / "reason_codes.py"
    if not target.exists():  # pragma: no cover — repo layout invariant
        pytest.skip(f"knowledge-ingest reason_codes.py not found at {target}")
    spec = importlib.util.spec_from_file_location("_ki_reason_codes", target)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["_ki_reason_codes"] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_fetch_reason_values_match_across_services() -> None:
    ki = _load_knowledge_ingest_reason_codes()
    assert CONNECTOR_FETCH == ki.FETCH_REASON_VALUES, (
        "FetchReasonCode drift between klai-connector and klai-knowledge-ingest. "
        f"Connector-only: {CONNECTOR_FETCH - ki.FETCH_REASON_VALUES}; "
        f"Knowledge-ingest-only: {ki.FETCH_REASON_VALUES - CONNECTOR_FETCH}"
    )


def test_persist_skip_reason_values_match_across_services() -> None:
    ki = _load_knowledge_ingest_reason_codes()
    assert CONNECTOR_PERSIST == ki.PERSIST_SKIP_REASON_VALUES, (
        "PersistSkipReason drift between klai-connector and klai-knowledge-ingest. "
        f"Connector-only: {CONNECTOR_PERSIST - ki.PERSIST_SKIP_REASON_VALUES}; "
        f"Knowledge-ingest-only: {ki.PERSIST_SKIP_REASON_VALUES - CONNECTOR_PERSIST}"
    )


def test_persist_skip_reason_values_match_migration_check_constraint() -> None:
    """SPEC AC-10: migration's CHECK constraint and the enum stay aligned.

    A reason added to the enum without a follow-up migration would still
    pass at write time on dev DBs (no CHECK enforcement) but fail in
    production (where the CHECK is in force) — the worst kind of
    asymmetric bug. Pin both ends here.
    """
    repo_root = Path(__file__).resolve().parents[2]
    migration = (
        repo_root
        / "klai-connector"
        / "alembic"
        / "versions"
        / "009_sync_runs_skip_reasons.py"
    )
    assert migration.exists(), f"Migration 009 missing at {migration}"
    text = migration.read_text(encoding="utf-8")
    for reason in CONNECTOR_PERSIST:
        # The literal lives in the ``_ALLOWED_SKIP_REASONS`` tuple
        # (double-quoted Python source). At runtime the migration's
        # f-string composes it into single-quoted SQL. We accept either
        # form so the test stays robust to source-style refactors.
        assert (f'"{reason}"' in text) or (f"'{reason}'" in text), (
            f"PersistSkipReason value '{reason}' missing from migration 009 "
            "CHECK constraint allowed-set. Add it to _ALLOWED_SKIP_REASONS."
        )
