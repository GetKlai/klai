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

The memory also decides how a crawl runs (``CrawlMemoryState.whole_site``):
when it is empty or the last recorded crawl tried every address, the crawl
runs and purges exactly as without memory and then replaces the memory with
its frontier; otherwise it rotates through the remembered addresses, oldest
wait first, and never retires anything.
"""

from __future__ import annotations

from typing import Any

import asyncpg

from knowledge_ingest.crawl_checkpoint import ConnectionFactory


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
        """The addresses earlier crawls of this connector discovered."""
        async with self.connection_factory() as conn:
            rows = await conn.fetch(
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
        return [dict(row) for row in rows]

    async def record(
        self,
        conn: asyncpg.Connection,
        rows: list[dict[str, Any]],
        forget: list[str],
        *,
        replace: bool,
    ) -> None:
        """Record one crawl on the caller's fenced connection, in one transaction.

        Tried addresses share one ``now()``. ``first_seen_at`` is set when a
        row is first written and never changed. A whole-site crawl
        (``replace``) makes the memory exactly tonight's frontier: rows for
        addresses it no longer holds are deleted, and an address it did not
        try loses its earlier try. A rotation crawl adds to the memory: an
        address that was only seen keeps its earlier try and ``forget`` keys
        are deleted. A crawl that held nothing (cancelled before its start
        page) records nothing.
        """
        if not rows and not forget:
            return
        async with conn.transaction():
            if replace:
                await conn.execute(
                    """
                    DELETE FROM knowledge.crawl_url_memory
                    WHERE org_id = $1 AND connector_id = $2
                      AND NOT (canonical_url = ANY($3::text[]))
                    """,
                    self.org_id,
                    self.connector_id,
                    [row["canonical_url"] for row in rows],
                )
            elif forget:
                await conn.execute(
                    """
                    DELETE FROM knowledge.crawl_url_memory
                    WHERE org_id = $1 AND connector_id = $2
                      AND canonical_url = ANY($3::text[])
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
                    depth = CASE WHEN $8 THEN EXCLUDED.depth
                                 ELSE LEAST(m.depth, EXCLUDED.depth) END,
                    last_visited_at = CASE WHEN $8 THEN EXCLUDED.last_visited_at
                                           ELSE COALESCE(EXCLUDED.last_visited_at,
                                                         m.last_visited_at) END
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
                        replace,
                    )
                    for row in rows
                ],
            )
