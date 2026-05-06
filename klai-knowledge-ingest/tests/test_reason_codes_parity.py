"""SPEC-INGEST-RECONCILE-001 AC-9 — symmetric parity test (knowledge-ingest side).

Mirrors :mod:`klai-connector/tests/test_reason_codes_parity.py`. The two
service copies of ``reason_codes.py`` MUST stay aligned: a value added
to one without the other turns into either a silent dashboard category
(if the connector writes a key knowledge-ingest doesn't recognise) or a
Postgres CHECK violation at runtime (if knowledge-ingest writes a key
connector's migration 009 doesn't allow).

The connector-side test fails when run from the connector test suite;
this test fails when run from knowledge-ingest. Either entry point in
CI catches drift — there is no "tested only by the other guy" gap.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

from knowledge_ingest.reason_codes import (
    FETCH_REASON_VALUES as KI_FETCH,
)
from knowledge_ingest.reason_codes import (
    PERSIST_SKIP_REASON_VALUES as KI_PERSIST,
)


def _load_connector_reason_codes():
    """Import the klai-connector copy from the sibling service path."""
    repo_root = Path(__file__).resolve().parents[2]
    target = repo_root / "klai-connector" / "app" / "reason_codes.py"
    if not target.exists():  # pragma: no cover — repo layout invariant
        pytest.skip(f"klai-connector reason_codes.py not found at {target}")
    spec = importlib.util.spec_from_file_location("_connector_reason_codes", target)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["_connector_reason_codes"] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_fetch_reason_values_match_connector_copy() -> None:
    connector = _load_connector_reason_codes()
    assert KI_FETCH == connector.FETCH_REASON_VALUES, (
        "FetchReasonCode drift between klai-knowledge-ingest and klai-connector. "
        f"Knowledge-ingest-only: {KI_FETCH - connector.FETCH_REASON_VALUES}; "
        f"Connector-only: {connector.FETCH_REASON_VALUES - KI_FETCH}"
    )


def test_persist_skip_reason_values_match_connector_copy() -> None:
    connector = _load_connector_reason_codes()
    assert KI_PERSIST == connector.PERSIST_SKIP_REASON_VALUES, (
        "PersistSkipReason drift between klai-knowledge-ingest and klai-connector. "
        f"Knowledge-ingest-only: {KI_PERSIST - connector.PERSIST_SKIP_REASON_VALUES}; "
        f"Connector-only: {connector.PERSIST_SKIP_REASON_VALUES - KI_PERSIST}"
    )
