"""SPEC-PRIVACY-QUERY-SHADOW-001 Unit 3 — shadow-store writer behaviour.

Verifies the fire-and-forget contract: drops are counted, missing pool is
handled gracefully, and a successful write delegates to asyncpg.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest


@pytest.mark.asyncio
async def test_write_shadow_drops_when_no_pool(monkeypatch):
    from retrieval_api.metrics import telemetry_shadow_drop_total
    from retrieval_api.services import telemetry as tlm

    monkeypatch.setattr("retrieval_api.services.telemetry.get_pool", lambda: None)

    before = telemetry_shadow_drop_total.labels(reason="no_pool")._value.get()  # type: ignore[attr-defined]
    tlm.write_shadow(
        request_id="req-1",
        org_id="org-1",
        embedding=[0.1, 0.2, 0.3],
        features={"tokens": 5, "lang": "nl"},
        band="medium",
        chunk_ids=["c1", "c2"],
        reranker_top1=0.7,
    )
    # Yield control so the scheduled task runs.
    await asyncio.sleep(0)
    after = telemetry_shadow_drop_total.labels(reason="no_pool")._value.get()  # type: ignore[attr-defined]
    assert after >= before + 1


@pytest.mark.asyncio
async def test_write_shadow_calls_pool_execute_on_success(monkeypatch):
    from retrieval_api.services import telemetry as tlm

    fake_pool = AsyncMock()
    fake_pool.execute = AsyncMock(return_value=None)
    monkeypatch.setattr("retrieval_api.services.telemetry.get_pool", lambda: fake_pool)

    tlm.write_shadow(
        request_id="req-2",
        org_id="org-2",
        embedding=[1.0] * 4,
        features={"tokens": 7, "lang": "en"},
        band="high",
        chunk_ids=["c1"],
        reranker_top1=0.9,
    )
    # Let the scheduled task run.
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    fake_pool.execute.assert_awaited_once()
    call_args = fake_pool.execute.await_args.args
    assert "INSERT INTO telemetry.query_shadow" in call_args[0]
    # Args: SQL, request_id, org_id, vector_text, features_json, band, chunk_ids, reranker_top1
    assert call_args[1] == "req-2"
    assert call_args[2] == "org-2"
    assert call_args[3].startswith("[")  # pgvector text literal
    # features json
    assert "tokens" in call_args[4]


@pytest.mark.asyncio
async def test_write_shadow_db_error_counts_drop(monkeypatch):
    from retrieval_api.metrics import telemetry_shadow_drop_total
    from retrieval_api.services import telemetry as tlm

    fake_pool = AsyncMock()
    fake_pool.execute = AsyncMock(side_effect=RuntimeError("connection refused"))
    monkeypatch.setattr("retrieval_api.services.telemetry.get_pool", lambda: fake_pool)

    before = telemetry_shadow_drop_total.labels(reason="db_error")._value.get()  # type: ignore[attr-defined]
    tlm.write_shadow(
        request_id="req-3",
        org_id="org-3",
        embedding=[0.5, 0.5],
        features={"tokens": 3},
        band="low",
        chunk_ids=[],
        reranker_top1=None,
    )
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    after = telemetry_shadow_drop_total.labels(reason="db_error")._value.get()  # type: ignore[attr-defined]
    assert after >= before + 1


def test_format_vector_handles_none_and_list() -> None:
    from retrieval_api.services.telemetry import _format_vector

    assert _format_vector(None) is None
    assert _format_vector([0.1, 0.2]) == "[0.1,0.2]"
    # Verify the format string strips trailing zeros (more compact wire size).
    assert _format_vector([1.0, 2.5e-3]) == "[1,0.0025]"
