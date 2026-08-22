from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def test_derives_title_from_stored_h1_for_numeric_article_id():
    from scripts.backfill_document_titles import title_update_from_row

    update = title_update_from_row(
        {
            "id": "0f7d44ca-e32d-45b4-91af-834270a87d3f",
            "path": "https://support.ascendcloud.com/app/articles/detail/a_id/15937",
            "extra": json.dumps(
                {
                    "title": "15937",
                    "source_url": "https://support.ascendcloud.com/app/articles/detail/a_id/15937",
                    "document_text": "<h1>Configure call forwarding</h1>\n\nInstructions.",
                }
            ),
        }
    )

    assert update is not None
    assert update.artifact_id == "0f7d44ca-e32d-45b4-91af-834270a87d3f"
    assert update.title == "Configure call forwarding"


def test_leaves_numeric_title_when_stored_content_has_no_better_source():
    from scripts.backfill_document_titles import title_update_from_row

    update = title_update_from_row(
        {
            "id": "0f7d44ca-e32d-45b4-91af-834270a87d3f",
            "path": "https://support.ascendcloud.com/app/articles/detail/a_id/15937",
            "extra": {
                "title": "15937",
                "source_url": "https://support.ascendcloud.com/app/articles/detail/a_id/15937",
                "document_text": "Instructions without a heading.",
            },
        }
    )

    assert update is None


@pytest.mark.asyncio
async def test_collect_title_updates_selects_only_active_artifacts():
    from knowledge_ingest import pg_store
    from scripts.backfill_document_titles import collect_title_updates

    conn = MagicMock()
    conn.fetch = AsyncMock(return_value=[])
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=conn)
    ctx.__aexit__ = AsyncMock(return_value=False)

    with patch(
        "scripts.backfill_document_titles.tenant_scoped_connection",
        return_value=ctx,
    ):
        assert await collect_title_updates("org-1") == ([], 0)

    sql, *args = conn.fetch.await_args.args
    assert "belief_time_end = $2" in sql
    assert args == ["org-1", pg_store._SENTINEL]


@pytest.mark.asyncio
async def test_run_updates_qdrant_before_artifact_title_for_resume_safety():
    from scripts.backfill_document_titles import TitleUpdate, run

    events: list[str] = []
    qdrant_client = MagicMock()

    async def set_qdrant_payload(**kwargs):
        events.append("qdrant")

    qdrant_client.set_payload = AsyncMock(side_effect=set_qdrant_payload)
    conn = MagicMock()
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=conn)
    ctx.__aexit__ = AsyncMock(return_value=False)

    async def update_artifact_extra(*args):
        events.append("postgres")

    with (
        patch(
            "scripts.backfill_document_titles.collect_title_updates",
            new_callable=AsyncMock,
            return_value=(
                [
                    TitleUpdate(
                        artifact_id="0f7d44ca-e32d-45b4-91af-834270a87d3f",
                        title="Configure call forwarding",
                    )
                ],
                0,
            ),
        ),
        patch(
            "scripts.backfill_document_titles.qdrant_store.get_client",
            return_value=qdrant_client,
        ),
        patch(
            "scripts.backfill_document_titles.tenant_scoped_connection",
            return_value=ctx,
        ),
        patch(
            "scripts.backfill_document_titles.pg_store.update_artifact_extra",
            new_callable=AsyncMock,
            side_effect=update_artifact_extra,
        ) as update_pg,
    ):
        result = await run("org-1", dry_run=False)

    assert result == 0
    assert events == ["qdrant", "postgres"]
    assert qdrant_client.set_payload.await_args.kwargs["payload"] == {
        "title": "Configure call forwarding"
    }
    points_filter = qdrant_client.set_payload.await_args.kwargs["points"]
    assert [(condition.key, condition.match.value) for condition in points_filter.must] == [
        ("org_id", "org-1"),
        ("artifact_id", "0f7d44ca-e32d-45b4-91af-834270a87d3f"),
    ]
    update_pg.assert_awaited_once_with(
        conn,
        "0f7d44ca-e32d-45b4-91af-834270a87d3f",
        {"title": "Configure call forwarding"},
    )


@pytest.mark.asyncio
async def test_dry_run_reports_updates_without_opening_either_write_store():
    from scripts.backfill_document_titles import TitleUpdate, run

    with (
        patch(
            "scripts.backfill_document_titles.collect_title_updates",
            new_callable=AsyncMock,
            return_value=(
                [
                    TitleUpdate(
                        artifact_id="0f7d44ca-e32d-45b4-91af-834270a87d3f",
                        title="Configure call forwarding",
                    )
                ],
                0,
            ),
        ),
        patch("scripts.backfill_document_titles.qdrant_store.get_client") as get_client,
        patch("scripts.backfill_document_titles.tenant_scoped_connection") as tenant_conn,
    ):
        result = await run("org-1", dry_run=True)

    assert result == 0
    get_client.assert_not_called()
    tenant_conn.assert_not_called()
