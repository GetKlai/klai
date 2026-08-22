"""Replace stored bare article-id titles with deterministic document titles.

Crawler pages historically used the final URL path segment when Crawl4AI's
indexable Markdown had no ``# `` heading. URLs shaped like
``/app/articles/detail/a_id/15937`` therefore stored ``15937`` as the title in
both Qdrant chunk payloads and ``knowledge.artifacts.extra``.

This backfill reads the stored document body and source URL, applies the same
title precedence as new ingest, then updates both stores. It makes no network
or LLM calls.

Usage (in a knowledge-ingest container):

    docker exec klai-core-knowledge-ingest-1 \
        python -m scripts.backfill_document_titles --org-id <org_id> [--dry-run]

Idempotent and resume-safe: only numeric stored titles are candidates, and
Qdrant is updated before Postgres so an interrupted row remains selectable on
the next run.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from dataclasses import dataclass
from typing import Any

import structlog
from qdrant_client.models import FieldCondition, Filter, MatchValue

from knowledge_ingest import pg_store, qdrant_store
from knowledge_ingest.db import tenant_scoped_connection
from knowledge_ingest.routes.ingest import _extract_title

logger = structlog.get_logger()


@dataclass(frozen=True)
class TitleUpdate:
    artifact_id: str
    title: str


def title_update_from_row(row: Any) -> TitleUpdate | None:
    try:
        extra = json.loads(row["extra"]) if isinstance(row["extra"], str) else row["extra"]
    except (TypeError, ValueError):
        return None
    if not isinstance(extra, dict):
        return None

    current_title = extra.get("title")
    if not isinstance(current_title, str) or not current_title.strip().isdigit():
        return None
    source_url = extra.get("source_url")
    title_path = source_url if isinstance(source_url, str) and source_url.strip() else row["path"]
    document_text = extra.get("document_text")
    content = document_text if isinstance(document_text, str) else ""
    title = _extract_title(content, str(title_path), current_title)
    if title.isdigit() or title == current_title.strip():
        return None
    return TitleUpdate(artifact_id=str(row["id"]), title=title)


async def collect_title_updates(org_id: str) -> tuple[list[TitleUpdate], int]:
    updates: list[TitleUpdate] = []
    skipped = 0
    async with tenant_scoped_connection(org_id) as conn:
        rows = await conn.fetch(
            """
            SELECT id, path, extra
            FROM knowledge.artifacts
            WHERE org_id = $1
              AND belief_time_end = $2
              AND extra->>'title' ~ '^[0-9]+$'
            ORDER BY id
            """,
            org_id,
            pg_store._SENTINEL,
        )
    for row in rows:
        update = title_update_from_row(row)
        if update is None:
            skipped += 1
        else:
            updates.append(update)
    return updates, skipped


async def run(org_id: str, dry_run: bool) -> int:
    updates, skipped = await collect_title_updates(org_id)
    logger.info(
        "backfill_document_titles_plan",
        org_id=org_id,
        updates=len(updates),
        skipped_without_better_title=skipped,
        dry_run=dry_run,
    )
    if dry_run or not updates:
        return 0

    client = qdrant_store.get_client()
    async with tenant_scoped_connection(org_id) as conn:
        for update in updates:
            await client.set_payload(
                collection_name=qdrant_store.COLLECTION,
                payload={"title": update.title},
                points=Filter(
                    must=[
                        FieldCondition(key="org_id", match=MatchValue(value=org_id)),
                        FieldCondition(
                            key="artifact_id",
                            match=MatchValue(value=update.artifact_id),
                        ),
                    ]
                ),
            )
            await pg_store.update_artifact_extra(
                conn,
                update.artifact_id,
                {"title": update.title},
            )

    logger.info(
        "backfill_document_titles_complete",
        org_id=org_id,
        updated=len(updates),
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--org-id", required=True, help="Zitadel org id (one tenant per run)")
    parser.add_argument("--dry-run", action="store_true", help="Report the plan, change nothing")
    args = parser.parse_args()
    return asyncio.run(run(args.org_id, args.dry_run))


if __name__ == "__main__":
    raise SystemExit(main())
