"""Tests for the app KB stats-summary endpoint."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.conftest import make_perms


def _make_perms() -> object:
    return make_perms(role="admin", user_id="user-1", org_id=1)


def _result_all(rows: list[object]) -> MagicMock:
    result = MagicMock()
    result.all.return_value = rows
    return result


def _result_scalars(rows: list[object]) -> MagicMock:
    result = MagicMock()
    result.scalars.return_value.all.return_value = rows
    return result


@pytest.mark.asyncio
async def test_stats_summary_counts_processing_uploads_before_ingest_artifact_exists() -> None:
    """A 202-accepted upload must count as a bron while docling is processing."""

    from app.api.app_knowledge_bases import knowledge_bases_stats_summary

    org = MagicMock()
    org.id = 1
    org.zitadel_org_id = "zitadel-org-1"

    kb = MagicMock()
    kb.id = 42
    kb.slug = "chemie"

    db = AsyncMock()
    db.execute.side_effect = [
        _result_scalars([kb]),  # visible KBs
        _result_all([]),  # portal connectors
        _result_all([(42, 1)]),  # visible kb_uploads rows
        _result_all([]),  # retrieval gaps
        _result_all([]),  # usage rows
    ]

    with (
        patch("app.api.app_knowledge_bases._load_org_or_500", new=AsyncMock(return_value=org)),
        patch("app.api.app_knowledge_bases._qdrant_count_for_kb", new=AsyncMock(return_value=0)),
        patch(
            "app.api.app_knowledge_bases.knowledge_ingest_client.get_chunks_summary",
            new=AsyncMock(return_value=({}, {"chemie": 0})),
        ),
    ):
        result = await knowledge_bases_stats_summary(perms=_make_perms(), db=db)

    stats = result.stats["chemie"]
    assert stats.bronnen == 1
    assert stats.chunks == 0
