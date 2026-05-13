"""SPEC-PRIVACY-QUERY-SHADOW-001 Unit 6 — gap-events + retrieval-log gating.

Pure unit tests with mocked AsyncSession + `set_tenant`. Verifies the
three-mode gating contract for the two portal-api telemetry endpoints:

- /internal/v1/gap-events:
  * off    → skipped, no PortalRetrievalGap.add()
  * shadow → row inserted with query_text='[REDACTED:shadow]'
  * full   → row inserted with literal query_text (legacy behaviour)

- /internal/v1/retrieval-log:
  * off    → skipped, no write_retrieval_log call
  * shadow → write_retrieval_log called with query_resolved=""
  * full   → write_retrieval_log called with literal query_resolved
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class _FakeOrg:
    def __init__(self, telemetry_level: str = "shadow") -> None:
        self.id = 42
        self.zitadel_org_id = "zit-org-1"
        self.telemetry_level = telemetry_level


def _scalar_result(value: object) -> MagicMock:
    res = MagicMock()
    res.scalar_one_or_none.return_value = value
    return res


def _build_gap_payload() -> dict:
    return {
        "org_id": "zit-org-1",
        "user_id": "user-1",
        "query_text": "Hoe stel ik vakantie aan?",
        "gap_type": "soft",
        "top_score": 0.42,
        "nearest_kb_slug": "support",
        "chunks_retrieved": 0,
        "retrieval_ms": 120,
        "taxonomy_node_ids": None,
        "caller_client_id": None,
    }


def _build_log_payload() -> dict:
    return {
        "org_id": "zit-org-1",
        "user_id": "user-1",
        "chunk_ids": ["c1", "c2"],
        "reranker_scores": [0.7, 0.5],
        "query_resolved": "Hoe stel ik vakantie aan in het systeem?",
        "embedding_model_version": "bge-m3",
        "retrieved_at": datetime.now(UTC),
        "caller_client_id": None,
    }


@pytest.mark.asyncio
async def test_gap_events_skipped_in_off_mode() -> None:
    """REQ-8: 'off' returns 200 but inserts nothing."""
    from app.api.internal import GapEventIn, create_gap_event

    org = _FakeOrg(telemetry_level="off")
    db = AsyncMock()
    db.execute = AsyncMock(return_value=_scalar_result(org))
    db.add = MagicMock()
    db.commit = AsyncMock()

    payload = GapEventIn(**_build_gap_payload())
    request = MagicMock()
    request.headers = {"x-internal-secret": "test-secret"}

    with (
        patch("app.api.internal._require_internal_token", AsyncMock()),
        patch("app.api.internal.set_tenant", AsyncMock()),
        patch("app.api.internal._audit_internal_call", AsyncMock()),
    ):
        result = await create_gap_event(payload, request, db)

    assert result["ok"] is True
    assert result.get("skipped") == "telemetry_off"
    db.add.assert_not_called()
    db.commit.assert_not_called()


@pytest.mark.asyncio
async def test_gap_events_redacts_in_shadow_mode() -> None:
    """REQ-8: 'shadow' inserts a row with query_text='[REDACTED:shadow]'."""
    from app.api.internal import GapEventIn, create_gap_event

    org = _FakeOrg(telemetry_level="shadow")
    db = AsyncMock()
    db.execute = AsyncMock(return_value=_scalar_result(org))
    captured: list = []
    db.add = MagicMock(side_effect=lambda obj: captured.append(obj))
    db.commit = AsyncMock()

    payload = GapEventIn(**_build_gap_payload())
    request = MagicMock()
    request.headers = {"x-internal-secret": "test-secret"}

    with (
        patch("app.api.internal._require_internal_token", AsyncMock()),
        patch("app.api.internal.set_tenant", AsyncMock()),
        patch("app.api.internal._audit_internal_call", AsyncMock()),
    ):
        await create_gap_event(payload, request, db)

    assert len(captured) == 1
    gap = captured[0]
    assert gap.query_text == "[REDACTED:shadow]"
    # Other fields preserved.
    assert gap.gap_type == "soft"
    assert gap.top_score == 0.42


@pytest.mark.asyncio
async def test_gap_events_keeps_text_in_full_mode() -> None:
    """REQ-8: 'full' inserts a row with literal query_text (legacy)."""
    from app.api.internal import GapEventIn, create_gap_event

    org = _FakeOrg(telemetry_level="full")
    db = AsyncMock()
    db.execute = AsyncMock(return_value=_scalar_result(org))
    captured: list = []
    db.add = MagicMock(side_effect=lambda obj: captured.append(obj))
    db.commit = AsyncMock()

    payload = GapEventIn(**_build_gap_payload())
    request = MagicMock()
    request.headers = {"x-internal-secret": "test-secret"}

    with (
        patch("app.api.internal._require_internal_token", AsyncMock()),
        patch("app.api.internal.set_tenant", AsyncMock()),
        patch("app.api.internal._audit_internal_call", AsyncMock()),
        # Avoid the async classification side-effect path
        patch("app.api.internal.asyncio.create_task"),
    ):
        await create_gap_event(payload, request, db)

    assert len(captured) == 1
    assert captured[0].query_text == "Hoe stel ik vakantie aan?"


@pytest.mark.asyncio
async def test_retrieval_log_skipped_in_off_mode() -> None:
    """REQ-9: 'off' returns 200 but does not call write_retrieval_log."""
    from app.api.internal import RetrievalLogIn, post_retrieval_log

    org = _FakeOrg(telemetry_level="off")
    db = AsyncMock()
    db.execute = AsyncMock(return_value=_scalar_result(org))

    payload = RetrievalLogIn(**_build_log_payload())
    request = MagicMock()
    request.headers = {"x-internal-secret": "test-secret"}

    with (
        patch("app.api.internal._require_internal_token", AsyncMock()),
        patch("app.api.internal._audit_internal_call", AsyncMock()),
        patch("app.services.retrieval_log.write_retrieval_log", AsyncMock()) as mock_write,
    ):
        result = await post_retrieval_log(payload, request, db)

    assert result["ok"] is True
    assert result.get("skipped") == "telemetry_off"
    mock_write.assert_not_called()


@pytest.mark.asyncio
async def test_retrieval_log_redacts_query_resolved_in_shadow_mode() -> None:
    """REQ-9: 'shadow' calls write_retrieval_log with query_resolved=''."""
    from app.api.internal import RetrievalLogIn, post_retrieval_log

    org = _FakeOrg(telemetry_level="shadow")
    db = AsyncMock()
    db.execute = AsyncMock(return_value=_scalar_result(org))

    payload = RetrievalLogIn(**_build_log_payload())
    request = MagicMock()
    request.headers = {"x-internal-secret": "test-secret"}

    with (
        patch("app.api.internal._require_internal_token", AsyncMock()),
        patch("app.api.internal._audit_internal_call", AsyncMock()),
        patch("app.services.retrieval_log.write_retrieval_log", AsyncMock()) as mock_write,
    ):
        await post_retrieval_log(payload, request, db)

    mock_write.assert_awaited_once()
    kwargs = mock_write.await_args.kwargs
    assert kwargs["query_resolved"] == ""
    # Other fields preserved.
    assert kwargs["chunk_ids"] == ["c1", "c2"]
    assert kwargs["reranker_scores"] == [0.7, 0.5]


@pytest.mark.asyncio
async def test_retrieval_log_keeps_query_resolved_in_full_mode() -> None:
    """REQ-9: 'full' calls write_retrieval_log with literal query_resolved."""
    from app.api.internal import RetrievalLogIn, post_retrieval_log

    org = _FakeOrg(telemetry_level="full")
    db = AsyncMock()
    db.execute = AsyncMock(return_value=_scalar_result(org))

    payload_data = _build_log_payload()
    payload = RetrievalLogIn(**payload_data)
    request = MagicMock()
    request.headers = {"x-internal-secret": "test-secret"}

    with (
        patch("app.api.internal._require_internal_token", AsyncMock()),
        patch("app.api.internal._audit_internal_call", AsyncMock()),
        patch("app.services.retrieval_log.write_retrieval_log", AsyncMock()) as mock_write,
    ):
        await post_retrieval_log(payload, request, db)

    mock_write.assert_awaited_once()
    kwargs = mock_write.await_args.kwargs
    assert kwargs["query_resolved"] == payload_data["query_resolved"]
