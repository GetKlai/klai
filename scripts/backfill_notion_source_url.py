"""
Backfill source_url for existing Notion chunks in Qdrant klai_knowledge.

Scans for chunks with content_type='notion_page' and no source_url set,
then sets source_url = https://notion.so/<source_ref_no_dashes>.

Run inside knowledge-ingest container:
  docker exec klai-knowledge-ingest-1 python /app/scripts/backfill_notion_source_url.py
"""
import asyncio
import os

from qdrant_client import AsyncQdrantClient
from qdrant_client.models import FieldCondition, Filter, MatchValue

COLLECTION = os.getenv("QDRANT_COLLECTION", "klai_knowledge")
QDRANT_URL = os.getenv("QDRANT_URL", "http://qdrant:6333")
QDRANT_API_KEY = os.getenv("QDRANT_API_KEY", "")
BATCH_SIZE = 100


async def backfill() -> None:
    client = AsyncQdrantClient(
        url=QDRANT_URL,
        api_key=QDRANT_API_KEY or None,
    )

    scroll_filter = Filter(
        must=[
            FieldCondition(key="content_type", match=MatchValue(value="notion_page")),
        ]
    )

    updated = 0
    skipped = 0
    offset = None

    print(f"Scanning {COLLECTION} for Notion chunks without source_url...")

    while True:
        results, next_offset = await client.scroll(
            collection_name=COLLECTION,
            scroll_filter=scroll_filter,
            limit=BATCH_SIZE,
            offset=offset,
            with_payload=True,
            with_vectors=False,
        )

        if not results:
            break

        for point in results:
            source_ref = point.payload.get("source_ref", "")
            existing_url = point.payload.get("source_url", "")

            if existing_url:
                skipped += 1
                continue

            if not source_ref:
                print(f"  SKIP {point.id}: no source_ref")
                skipped += 1
                continue

            source_url = f"https://notion.so/{source_ref.replace('-', '')}"
            await client.set_payload(
                collection_name=COLLECTION,
                payload={"source_url": source_url},
                points=[point.id],
            )
            updated += 1

        print(f"  Processed batch: {updated} updated, {skipped} skipped so far")

        if next_offset is None:
            break
        offset = next_offset

    print(f"\nDone. Updated: {updated}, Skipped (already had source_url or no source_ref): {skipped}")


if __name__ == "__main__":
    asyncio.run(backfill())
