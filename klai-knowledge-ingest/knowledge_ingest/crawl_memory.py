"""Per-connector memory of the addresses a web crawl knows and when it last tried them.

A crawl that cannot visit everything (page limit, attempt bound, a site that
slows us down or blocks us) must continue the next night where it stopped.
Artifact rows cannot tell: an unchanged page creates no new version. Earlier
``crawl_jobs.fetch_outcomes`` could, but a source that needs sixty nights for
one round would have to read sixty jobs' outcome lists on every crawl and
would lose its place as soon as old job rows are pruned. This table holds one
row per address instead. ``crawl_site`` only reads it; ``run_crawl_job``
records a run inside its guarded completion, once the run's pages were
accepted and written.
"""

from __future__ import annotations

from typing import Any

import asyncpg

from knowledge_ingest.crawl_checkpoint import ConnectionFactory
from knowledge_ingest.pg_store import _SENTINEL


class PostgresCrawlMemory:
    """The crawl memory of one connector, in ``knowledge.crawl_url_memory``."""

    def __init__(
        self,
        connection_factory: ConnectionFactory,
        *,
        org_id: str,
        kb_slug: str,
        connector_id: str,
    ) -> None:
        self.connection_factory = connection_factory
        self.org_id = org_id
        self.kb_slug = kb_slug
        self.connector_id = connector_id

    async def load(self) -> list[dict[str, Any]]:
        """Remembered addresses, then the connector's stored pages.

        A stored page comes back without a key or depth; the ledger
        canonicalises it on the crawl's host and, when the memory has no row
        for it, places it at ``max_depth``.
        """
        async with self.connection_factory() as conn:
            remembered = await conn.fetch(
                """
                SELECT canonical_url, url, depth,
                       extract(epoch FROM first_seen_at)::float8 AS first_seen_at,
                       extract(epoch FROM last_visited_at)::float8 AS last_visited_at
                FROM knowledge.crawl_url_memory
                WHERE org_id = $1 AND connector_id = $2
                ORDER BY canonical_url
                """,
                self.org_id,
                self.connector_id,
            )
            stored = await conn.fetch(
                """
                SELECT DISTINCT COALESCE(extra::jsonb->>'source_url', path) AS url
                FROM knowledge.artifacts
                WHERE org_id = $1
                  AND kb_slug = $2
                  AND belief_time_end = $4
                  AND extra IS NOT NULL
                  AND extra::jsonb->>'source_connector_id' = $3
                ORDER BY url
                """,
                self.org_id,
                self.kb_slug,
                self.connector_id,
                _SENTINEL,
            )
        return [dict(row) for row in remembered] + [
            {
                "canonical_url": None,
                "url": url,
                "depth": None,
                "first_seen_at": None,
                "last_visited_at": None,
            }
            for row in stored
            if (url := str(row["url"])).startswith(("http://", "https://"))
        ]

    async def record(
        self, conn: asyncpg.Connection, rows: list[dict[str, Any]], forget: list[str]
    ) -> None:
        """Record one crawl on the caller's fenced connection, in one transaction.

        Tried addresses share one ``now()``; an address that was only seen
        keeps its earlier try. ``first_seen_at`` is set when a row is first
        written and never changed. ``forget`` keys are deleted.
        """
        if not rows and not forget:
            return
        async with conn.transaction():
            await conn.execute(
                """
                DELETE FROM knowledge.crawl_url_memory
                WHERE org_id = $1 AND connector_id = $2 AND canonical_url = ANY($3::text[])
                """,
                self.org_id,
                self.connector_id,
                forget,
            )
            await conn.executemany(
                """
                INSERT INTO knowledge.crawl_url_memory AS m (
                    org_id, kb_slug, connector_id, canonical_url, url, depth,
                    first_seen_at, last_visited_at
                ) VALUES ($1, $2, $3, $4, $5, $6, now(), CASE WHEN $7 THEN now() END)
                ON CONFLICT (org_id, connector_id, canonical_url) DO UPDATE SET
                    url = EXCLUDED.url,
                    depth = LEAST(m.depth, EXCLUDED.depth),
                    last_visited_at = COALESCE(EXCLUDED.last_visited_at, m.last_visited_at)
                """,
                [
                    (
                        self.org_id,
                        self.kb_slug,
                        self.connector_id,
                        row["canonical_url"],
                        row["url"],
                        row["depth"],
                        row["visited"],
                    )
                    for row in rows
                ],
            )
