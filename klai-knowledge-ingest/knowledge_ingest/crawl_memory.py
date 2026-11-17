"""Per-connector memory of the addresses a web crawl knows and when it last visited them.

A crawl that cannot visit everything (page limit, attempt bound, a site that
slows us down or blocks us) must continue the next night where it stopped.
Artifact rows cannot tell: an unchanged page creates no new version. Earlier
``crawl_jobs.fetch_outcomes`` could, but a source that needs sixty nights for
one round would have to read sixty jobs' outcome lists on every crawl and
would lose its place as soon as old job rows are pruned. This table holds one
row per address instead, written once at the end of each crawl.
"""

from __future__ import annotations

from typing import Any

from knowledge_ingest.crawl_checkpoint import ConnectionFactory
from knowledge_ingest.crawl_url_policy import canonicalise_url
from knowledge_ingest.pg_store import _SENTINEL

# A stored page with no memory row (stored before this table existed) joins the
# frontier like a sitemap address: depth 1, never visited.
_STORED_PAGE_DEPTH = 1


class PostgresCrawlMemory:
    """``CrawlMemory`` for one connector, in ``knowledge.crawl_url_memory``."""

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
        """Remembered addresses plus the connector's stored pages."""
        async with self.connection_factory() as conn:
            remembered = await conn.fetch(
                """
                SELECT canonical_url, url, depth,
                       extract(epoch FROM last_visited_at)::float8 AS last_visited_at,
                       last_reason_code
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
        rows = {row["canonical_url"]: dict(row) for row in remembered}
        for row in stored:
            url = str(row["url"])
            canonical = canonicalise_url(url)
            if url.startswith(("http://", "https://")) and canonical not in rows:
                rows[canonical] = {
                    "canonical_url": canonical,
                    "url": url,
                    "depth": _STORED_PAGE_DEPTH,
                    "last_visited_at": None,
                    "last_reason_code": None,
                }
        return list(rows.values())

    async def save(self, rows: list[dict[str, Any]]) -> None:
        """Record tonight's visits; one transaction, so they share one ``now()``."""
        async with self.connection_factory() as conn:
            async with conn.transaction():
                await conn.executemany(
                    """
                    INSERT INTO knowledge.crawl_url_memory AS m (
                        org_id, connector_id, canonical_url, url, depth,
                        last_visited_at, last_reason_code
                    ) VALUES (
                        $1, $2, $3, $4, $5,
                        CASE WHEN $6 THEN now() END,
                        CASE WHEN $6 THEN $7 END
                    )
                    ON CONFLICT (org_id, connector_id, canonical_url) DO UPDATE SET
                        url = EXCLUDED.url,
                        depth = LEAST(m.depth, EXCLUDED.depth),
                        last_visited_at = COALESCE(EXCLUDED.last_visited_at, m.last_visited_at),
                        last_reason_code = CASE
                            WHEN EXCLUDED.last_visited_at IS NULL THEN m.last_reason_code
                            ELSE EXCLUDED.last_reason_code
                        END
                    """,
                    [
                        (
                            self.org_id,
                            self.connector_id,
                            row["canonical_url"],
                            row["url"],
                            row["depth"],
                            row["visited"],
                            row["reason_code"],
                        )
                        for row in rows
                    ],
                )
