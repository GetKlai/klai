"""
PostgreSQL artifact tracking for knowledge-ingest.
"""

import json
import time
import uuid

from knowledge_ingest.db import get_pool

_SENTINEL = 253402300800  # 9999-12-31 — sentinel value for "still active"


async def get_active_content_hash(org_id: str, kb_slug: str, path: str) -> str | None:
    """Return the content_hash of the current active artifact for this path, or None."""
    pool = await get_pool()
    row = await pool.fetchval(
        """
        SELECT content_hash
        FROM knowledge.artifacts
        WHERE org_id = $1 AND kb_slug = $2 AND path = $3
          AND belief_time_end = $4
        ORDER BY created_at DESC
        LIMIT 1
        """,
        org_id,
        kb_slug,
        path,
        _SENTINEL,
    )
    return row


async def create_artifact(
    org_id: str,
    kb_slug: str,
    path: str,
    provenance_type: str,
    assertion_mode: str,
    synthesis_depth: int,
    confidence: str | None,
    belief_time_start: int,
    belief_time_end: int,
    user_id: str | None = None,
    content_type: str = "unknown",
    extra: dict | None = None,
    content_hash: str | None = None,
) -> str:
    """Create a knowledge artifact record. Returns the artifact UUID."""
    artifact_id = str(uuid.uuid4())
    now = int(time.time())
    pool = await get_pool()
    extra_json = json.dumps(extra) if extra else "{}"
    await pool.execute(
        """
        INSERT INTO knowledge.artifacts
          (id, org_id, user_id, kb_slug, path,
           provenance_type, assertion_mode,
           synthesis_depth, confidence,
           belief_time_start, belief_time_end,
           content_type, extra, content_hash,
           created_at)
        VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15)
        """,
        artifact_id,
        org_id,
        user_id,
        kb_slug,
        path,
        provenance_type,
        assertion_mode,
        synthesis_depth,
        confidence,
        belief_time_start,
        belief_time_end,
        content_type,
        extra_json,
        content_hash,
        now,
    )
    return artifact_id


def _personal_slug(user_id: str) -> str:
    """Build the per-user personal KB slug: ``personal-{user_id}``."""
    return f"personal-{user_id}"


async def list_personal_artifacts(
    org_id: str,
    user_id: str,
    limit: int = 50,
    offset: int = 0,
) -> list[dict]:
    """List active personal artifacts for a user, newest first."""
    pool = await get_pool()
    rows = await pool.fetch(
        """
        SELECT id, path, assertion_mode, created_at
        FROM knowledge.artifacts
        WHERE org_id = $1 AND user_id = $2
          AND kb_slug = $3
          AND belief_time_end = $4
        ORDER BY created_at DESC
        LIMIT $5 OFFSET $6
        """,
        org_id,
        user_id,
        _personal_slug(user_id),
        _SENTINEL,
        limit,
        offset,
    )
    return [dict(r) for r in rows]


async def count_personal_artifacts(org_id: str, user_id: str) -> int:
    """Count active personal artifacts for a user."""
    pool = await get_pool()
    row = await pool.fetchval(
        """
        SELECT COUNT(*)
        FROM knowledge.artifacts
        WHERE org_id = $1 AND user_id = $2
          AND kb_slug = $3
          AND belief_time_end = $4
        """,
        org_id,
        user_id,
        _personal_slug(user_id),
        _SENTINEL,
    )
    return row or 0


async def get_personal_artifact(
    artifact_id: str,
    org_id: str,
    user_id: str,
) -> dict | None:
    """Get a single active personal artifact, or None if not found / wrong user."""
    pool = await get_pool()
    row = await pool.fetchrow(
        """
        SELECT id, path
        FROM knowledge.artifacts
        WHERE id = $1 AND org_id = $2 AND user_id = $3
          AND kb_slug = $4
          AND belief_time_end = $5
        """,
        artifact_id,
        org_id,
        user_id,
        _personal_slug(user_id),
        _SENTINEL,
    )
    return dict(row) if row else None


async def soft_delete_artifact(org_id: str, kb_slug: str, path: str) -> None:
    """Set belief_time_end = now for all active artifacts matching this path."""
    now = int(time.time())
    pool = await get_pool()
    await pool.execute(
        """
        UPDATE knowledge.artifacts
        SET belief_time_end = $1
        WHERE org_id = $2 AND kb_slug = $3 AND path = $4
          AND belief_time_end = $5
        """,
        now,
        org_id,
        kb_slug,
        path,
        _SENTINEL,
    )


async def get_episode_ids(org_id: str, kb_slug: str) -> list[str]:
    """Return Graphiti episode UUIDs for all artifacts in a KB.

    Reads the graphiti_episode_id from the extra JSON field before deletion.
    Excludes the 'no-chunks' sentinel (artifacts with no text content).
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT extra::jsonb->>'graphiti_episode_id' AS episode_id
               FROM knowledge.artifacts
               WHERE org_id = $1 AND kb_slug = $2
                 AND extra IS NOT NULL
                 AND extra::jsonb->>'graphiti_episode_id' IS NOT NULL""",
            org_id,
            kb_slug,
        )
    return [r["episode_id"] for r in rows if r["episode_id"] != "no-chunks"]


async def delete_kb(org_id: str, kb_slug: str) -> None:
    """Hard-delete all PostgreSQL records for a knowledge base.

    Removes: artifacts, artifact_entities, derivations, embedding_queue,
    kb_config, crawl_jobs — all scoped to (org_id, kb_slug).

    Does NOT delete knowledge.entities: entities are org-scoped and may be
    shared across multiple KBs within the same org.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            # Nullify self-references first to avoid FK violations when deleting artifacts
            await conn.execute(
                "UPDATE knowledge.artifacts SET superseded_by = NULL"
                " WHERE org_id = $1 AND kb_slug = $2",
                org_id,
                kb_slug,
            )
            await conn.execute(
                """DELETE FROM knowledge.embedding_queue WHERE artifact_id IN (
                     SELECT id FROM knowledge.artifacts WHERE org_id = $1 AND kb_slug = $2
                   )""",
                org_id,
                kb_slug,
            )
            await conn.execute(
                """DELETE FROM knowledge.artifact_entities WHERE artifact_id IN (
                     SELECT id FROM knowledge.artifacts WHERE org_id = $1 AND kb_slug = $2
                   )""",
                org_id,
                kb_slug,
            )
            await conn.execute(
                """DELETE FROM knowledge.derivations WHERE child_id IN (
                     SELECT id FROM knowledge.artifacts WHERE org_id = $1 AND kb_slug = $2
                   ) OR parent_id IN (
                     SELECT id FROM knowledge.artifacts WHERE org_id = $1 AND kb_slug = $2
                   )""",
                org_id,
                kb_slug,
            )
            await conn.execute(
                "DELETE FROM knowledge.artifacts WHERE org_id = $1 AND kb_slug = $2",
                org_id,
                kb_slug,
            )
            await conn.execute(
                "DELETE FROM knowledge.kb_config WHERE org_id = $1 AND kb_slug = $2",
                org_id,
                kb_slug,
            )
            await conn.execute(
                "DELETE FROM knowledge.crawl_jobs WHERE org_id = $1 AND kb_slug = $2",
                org_id,
                kb_slug,
            )
            await conn.execute(
                "DELETE FROM knowledge.crawled_pages WHERE org_id = $1 AND kb_slug = $2",
                org_id,
                kb_slug,
            )
            await conn.execute(
                "DELETE FROM knowledge.page_links WHERE org_id = $1 AND kb_slug = $2",
                org_id,
                kb_slug,
            )


async def get_connector_episode_ids(org_id: str, kb_slug: str, connector_id: str) -> list[str]:
    """Return Graphiti episode UUIDs for artifacts ingested by a specific connector."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT extra::jsonb->>'graphiti_episode_id' AS episode_id
               FROM knowledge.artifacts
               WHERE org_id = $1 AND kb_slug = $2
                 AND extra IS NOT NULL
                 AND extra::jsonb->>'source_connector_id' = $3
                 AND extra::jsonb->>'graphiti_episode_id' IS NOT NULL""",
            org_id,
            kb_slug,
            connector_id,
        )
    return [r["episode_id"] for r in rows if r["episode_id"] != "no-chunks"]


async def delete_connector_artifacts(org_id: str, kb_slug: str, connector_id: str) -> int:
    """Hard-delete all PostgreSQL artifact records for a specific connector.

    Follows the same cascade order as delete_kb():
    nullify self-references → embedding_queue → artifact_entities → derivations →
    crawled_pages (by URL) → page_links (by URL) → artifacts.

    crawled_pages + page_links have no connector_id column (legacy schema), so we
    scope them via the artifact path-URL set BEFORE deleting artifacts. Covers the
    cleanup-gap discovered during SPEC-CRAWLER-005 Fase 6: re-ingest would otherwise
    skip all pages as dedup-"unchanged" via content_hash.

    Returns the number of artifacts deleted.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                """UPDATE knowledge.artifacts SET superseded_by = NULL
                   WHERE superseded_by IN (
                     SELECT id FROM knowledge.artifacts
                     WHERE org_id = $1 AND kb_slug = $2
                       AND extra IS NOT NULL
                       AND extra::jsonb->>'source_connector_id' = $3
                   )""",
                org_id,
                kb_slug,
                connector_id,
            )
            await conn.execute(
                """DELETE FROM knowledge.embedding_queue WHERE artifact_id IN (
                     SELECT id FROM knowledge.artifacts
                     WHERE org_id = $1 AND kb_slug = $2
                       AND extra IS NOT NULL
                       AND extra::jsonb->>'source_connector_id' = $3
                   )""",
                org_id,
                kb_slug,
                connector_id,
            )
            await conn.execute(
                """DELETE FROM knowledge.artifact_entities WHERE artifact_id IN (
                     SELECT id FROM knowledge.artifacts
                     WHERE org_id = $1 AND kb_slug = $2
                       AND extra IS NOT NULL
                       AND extra::jsonb->>'source_connector_id' = $3
                   )""",
                org_id,
                kb_slug,
                connector_id,
            )
            await conn.execute(
                """DELETE FROM knowledge.derivations WHERE child_id IN (
                     SELECT id FROM knowledge.artifacts
                     WHERE org_id = $1 AND kb_slug = $2
                       AND extra IS NOT NULL
                       AND extra::jsonb->>'source_connector_id' = $3
                   ) OR parent_id IN (
                     SELECT id FROM knowledge.artifacts
                     WHERE org_id = $1 AND kb_slug = $2
                       AND extra IS NOT NULL
                       AND extra::jsonb->>'source_connector_id' = $3
                   )""",
                org_id,
                kb_slug,
                connector_id,
            )
            # SPEC-CRAWLER-005 Fase 6 follow-up: scrub crawled_pages + page_links
            # for URLs owned by this connector. Scoped via the artifact path-URL
            # set (web_crawler/crawl adapters write artifacts with path=URL).
            # Must run BEFORE the artifacts DELETE so the URL set is still
            # reachable. Other connectors in the same KB remain untouched — their
            # URLs don't appear in this connector's artifact set.
            await conn.execute(
                """DELETE FROM knowledge.crawled_pages
                   WHERE org_id = $1 AND kb_slug = $2 AND url IN (
                     SELECT path FROM knowledge.artifacts
                     WHERE org_id = $1 AND kb_slug = $2
                       AND extra IS NOT NULL
                       AND extra::jsonb->>'source_connector_id' = $3
                   )""",
                org_id,
                kb_slug,
                connector_id,
            )
            await conn.execute(
                """DELETE FROM knowledge.page_links
                   WHERE org_id = $1 AND kb_slug = $2 AND (
                     from_url IN (
                       SELECT path FROM knowledge.artifacts
                       WHERE org_id = $1 AND kb_slug = $2
                         AND extra IS NOT NULL
                         AND extra::jsonb->>'source_connector_id' = $3
                     ) OR to_url IN (
                       SELECT path FROM knowledge.artifacts
                       WHERE org_id = $1 AND kb_slug = $2
                         AND extra IS NOT NULL
                         AND extra::jsonb->>'source_connector_id' = $3
                     )
                   )""",
                org_id,
                kb_slug,
                connector_id,
            )
            result = await conn.fetchval(
                """WITH deleted AS (
                     DELETE FROM knowledge.artifacts
                     WHERE org_id = $1 AND kb_slug = $2
                       AND extra IS NOT NULL
                       AND extra::jsonb->>'source_connector_id' = $3
                     RETURNING id
                   ) SELECT COUNT(*) FROM deleted""",
                org_id,
                kb_slug,
                connector_id,
            )
    return int(result or 0)


async def insert_artifact_image_refs(
    artifact_id: str,
    image_keys: list[tuple[str, str]],
) -> None:
    """Record (artifact, s3_key, content_hash) bookkeeping rows.

    SPEC-CONNECTOR-DELETE-LIFECYCLE-001 REQ-06.2.

    Called once per artifact at ingest-time, after the artifact row has
    been inserted. Each tuple is ``(s3_key, content_hash)``. Idempotent:
    duplicate (artifact_id, s3_key) pairs are silently merged via
    ``ON CONFLICT DO NOTHING`` so that re-ingest of the same content
    doesn't trip the primary-key constraint.

    Empty ``image_keys`` is a no-op.
    """
    if not image_keys:
        return
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.executemany(
            """
            INSERT INTO knowledge.artifact_images (artifact_id, s3_key, content_hash)
            VALUES ($1::uuid, $2, $3)
            ON CONFLICT (artifact_id, s3_key) DO NOTHING
            """,
            [(artifact_id, key, content_hash) for key, content_hash in image_keys],
        )


async def get_orphan_image_keys_for_connector(
    org_id: str, kb_slug: str, connector_id: str
) -> list[str]:
    """Return S3 keys that will become orphan when this connector's artifacts are deleted.

    SPEC-CONNECTOR-DELETE-LIFECYCLE-001 REQ-06.3. Refcount check on
    ``content_hash``: a key is "orphan" iff its content_hash is NOT
    referenced by any artifact OUTSIDE the deleted set. Same key might
    be referenced by another connector in another KB sharing the SHA256
    content; in that case we leave it in place.

    Must be called BEFORE ``delete_connector_artifacts`` because the FK
    CASCADE on ``artifact_images`` will remove the rows we need to query.
    Returns an empty list if no images exist for this connector.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT DISTINCT ai.s3_key
            FROM knowledge.artifact_images ai
            JOIN knowledge.artifacts a ON a.id = ai.artifact_id
            WHERE a.org_id = $1
              AND a.kb_slug = $2
              AND a.extra IS NOT NULL
              AND a.extra::jsonb->>'source_connector_id' = $3
              AND NOT EXISTS (
                  SELECT 1
                  FROM knowledge.artifact_images other_ai
                  JOIN knowledge.artifacts other_a ON other_a.id = other_ai.artifact_id
                  WHERE other_ai.content_hash = ai.content_hash
                    AND (
                        other_a.org_id != $1
                        OR other_a.kb_slug != $2
                        OR other_a.extra IS NULL
                        OR other_a.extra::jsonb->>'source_connector_id' IS DISTINCT FROM $3
                    )
              )
            """,
            org_id,
            kb_slug,
            connector_id,
        )
    return [r["s3_key"] for r in rows]


async def get_alive_episode_uuids_for_org(org_id: str) -> set[str]:
    """Return every Graphiti episode UUID still referenced by an artifact for this org.

    Read from ``knowledge.artifacts.extra->>'graphiti_episode_id'`` —
    this is where the ingest pipeline stores the FalkorDB ``Episodic.uuid``
    after a successful ``graph_module.ingest_episode``. The org-wide
    janitor uses the result to compute which FalkorDB episodes are no
    longer referenced and therefore orphan.

    Excludes the ``no-chunks`` sentinel that artifacts use when an
    article had no extractable text.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT extra::jsonb->>'graphiti_episode_id' AS episode_uuid
              FROM knowledge.artifacts
             WHERE org_id = $1
               AND extra IS NOT NULL
               AND extra::jsonb->>'graphiti_episode_id' IS NOT NULL
            """,
            org_id,
        )
    return {r["episode_uuid"] for r in rows if r["episode_uuid"] != "no-chunks"}


async def get_active_image_hashes_for_kb(org_id: str, kb_slug: str) -> set[str]:
    """Return content_hashes still referenced by any artifact in a KB.

    SPEC-CONNECTOR-DELETE-LIFECYCLE-001 janitor support. The Garage
    cleanup janitor calls this AFTER ``delete_connector_artifacts`` to
    work out which S3 keys still have a referencing artifact_image row
    for this KB. Keys whose hash is NOT in this set are orphan and safe
    to delete from S3.

    Returns an empty set when the KB has no images / no artifacts.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT DISTINCT ai.content_hash
              FROM knowledge.artifact_images ai
              JOIN knowledge.artifacts a ON a.id = ai.artifact_id
             WHERE a.org_id = $1 AND a.kb_slug = $2
            """,
            org_id,
            kb_slug,
        )
    return {r["content_hash"] for r in rows}


async def artifact_exists(artifact_id: str) -> bool:
    """SPEC-CONNECTOR-DELETE-LIFECYCLE-001 REQ-07: existence-guard helper.

    Returns True iff a row in ``knowledge.artifacts`` matches the given
    UUID. Used by ``ingest_graphiti_episode`` to short-circuit when the
    artifact was deleted (typically by the connector purge orchestrator)
    between enqueue and dequeue. The graphiti task has no
    ``source_connector_id`` arg, so artifact-presence is the canonical
    signal here.

    Fail-closed: any DB error returns False so the caller aborts.
    """
    if not artifact_id:
        return False
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT 1 FROM knowledge.artifacts WHERE id = $1::uuid",
                artifact_id,
            )
        return row is not None
    except Exception:
        return False


async def delete_connector_crawl_jobs(org_id: str, kb_slug: str, connector_id: str) -> int:
    """Hard-delete crawl_jobs rows owned by a specific connector.

    knowledge.crawl_jobs has no native ``connector_id`` column — every row
    nests it inside the ``config`` JSONB blob (set by web_crawler/crawler
    adapters at job-create time). Filter on
    ``config->>'connector_id'`` so we only nuke this connector's history,
    leaving any other connector's job rows in the same KB untouched.

    Counterpart to ``delete_connector_artifacts``. Without this, every
    connector delete left an audit trail of orphan crawl_jobs that the
    UI cannot reach but that the next deployment Sentry alert / dashboard
    audit treats as live history. Returns the number of rows deleted.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        result = await conn.fetchval(
            """WITH deleted AS (
                 DELETE FROM knowledge.crawl_jobs
                 WHERE org_id = $1
                   AND kb_slug = $2
                   AND config IS NOT NULL
                   AND config->>'connector_id' = $3
                 RETURNING id
               ) SELECT COUNT(*) FROM deleted""",
            org_id,
            kb_slug,
            connector_id,
        )
    return int(result or 0)


async def upsert_crawled_page(
    org_id: str,
    kb_slug: str,
    url: str,
    raw_html_hash: str | None,
    content_hash: str,
    raw_markdown: str,
    crawled_at: int,
) -> None:
    """Insert or update a crawled page record (URL dedup registry + raw content cache).

    Stores both raw_html_hash (pre-extraction) and content_hash (post-extraction)
    to support dual-hash deduplication — see migration 012 for the skip logic.
    """
    pool = await get_pool()
    await pool.execute(
        """
        INSERT INTO knowledge.crawled_pages
            (org_id, kb_slug, url, raw_html_hash, content_hash, raw_markdown, crawled_at)
        VALUES ($1, $2, $3, $4, $5, $6, $7)
        ON CONFLICT (org_id, kb_slug, url)
        DO UPDATE SET
            raw_html_hash = EXCLUDED.raw_html_hash,
            content_hash  = EXCLUDED.content_hash,
            raw_markdown  = EXCLUDED.raw_markdown,
            crawled_at    = EXCLUDED.crawled_at
        """,
        org_id,
        kb_slug,
        url,
        raw_html_hash,
        content_hash,
        raw_markdown,
        crawled_at,
    )


# PageHashes = (raw_html_hash, content_hash) — either may be None for legacy rows
PageHashes = tuple[str | None, str | None]


async def get_crawled_page_stored(org_id: str, kb_slug: str, url: str) -> PageHashes | None:
    """Return (raw_html_hash, content_hash) for this URL, or None if not yet crawled."""
    pool = await get_pool()
    row = await pool.fetchrow(
        "SELECT raw_html_hash, content_hash FROM knowledge.crawled_pages "
        "WHERE org_id = $1 AND kb_slug = $2 AND url = $3",
        org_id,
        kb_slug,
        url,
    )
    return (row["raw_html_hash"], row["content_hash"]) if row else None


async def get_crawled_page_hashes(
    org_id: str,
    kb_slug: str,
    urls: list[str],
) -> dict[str, PageHashes]:
    """Return {url: (raw_html_hash, content_hash)} for all known URLs (single query)."""
    if not urls:
        return {}
    pool = await get_pool()
    rows = await pool.fetch(
        "SELECT url, raw_html_hash, content_hash FROM knowledge.crawled_pages "
        "WHERE org_id = $1 AND kb_slug = $2 AND url = ANY($3::text[])",
        org_id,
        kb_slug,
        urls,
    )
    return {row["url"]: (row["raw_html_hash"], row["content_hash"]) for row in rows}


async def upsert_page_links(
    org_id: str,
    kb_slug: str,
    from_url: str,
    links: list[dict],
) -> None:
    """Upsert outgoing links for from_url in a single batch round-trip."""
    from urllib.parse import urljoin

    rows = []
    for link in links:
        href = link.get("href", "")
        if not href:
            continue
        rows.append(
            (
                org_id,
                kb_slug,
                from_url,
                urljoin(from_url, href),
                (link.get("text", "") or "")[:500],
            )
        )
    if not rows:
        return
    pool = await get_pool()
    await pool.executemany(
        """
        INSERT INTO knowledge.page_links
            (org_id, kb_slug, from_url, to_url, link_text)
        VALUES ($1, $2, $3, $4, $5)
        ON CONFLICT (org_id, kb_slug, from_url, to_url)
        DO UPDATE SET link_text = EXCLUDED.link_text
        """,
        rows,
    )


async def get_page_episode_ids(org_id: str, kb_slug: str, path: str) -> list[str]:
    """Return Graphiti episode UUIDs for artifacts matching a specific page path.

    Like get_episode_ids() but scoped to a single page. Used during page deletion
    to clean up Graphiti graph nodes before soft-deleting the artifact.
    """
    pool = await get_pool()
    rows = await pool.fetch(
        """SELECT extra::jsonb->>'graphiti_episode_id' AS episode_id
           FROM knowledge.artifacts
           WHERE org_id = $1 AND kb_slug = $2 AND path = $3
             AND extra IS NOT NULL
             AND extra::jsonb->>'graphiti_episode_id' IS NOT NULL""",
        org_id,
        kb_slug,
        path,
    )
    return [r["episode_id"] for r in rows if r["episode_id"] != "no-chunks"]


async def cleanup_page_metadata(org_id: str, kb_slug: str, path: str) -> None:
    """Hard-delete metadata records (derivations, artifact_entities, embedding_queue)
    for all artifacts matching this page path.

    Must be called BEFORE soft_delete_artifact to avoid FK issues.
    Follows the same pattern as delete_kb() but scoped to a single page.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            # Nullify self-references first to avoid FK violations
            await conn.execute(
                """UPDATE knowledge.artifacts SET superseded_by = NULL
                   WHERE superseded_by IN (
                     SELECT id FROM knowledge.artifacts
                     WHERE org_id = $1 AND kb_slug = $2 AND path = $3
                   )""",
                org_id,
                kb_slug,
                path,
            )
            await conn.execute(
                """DELETE FROM knowledge.embedding_queue WHERE artifact_id IN (
                     SELECT id FROM knowledge.artifacts
                     WHERE org_id = $1 AND kb_slug = $2 AND path = $3
                   )""",
                org_id,
                kb_slug,
                path,
            )
            await conn.execute(
                """DELETE FROM knowledge.artifact_entities WHERE artifact_id IN (
                     SELECT id FROM knowledge.artifacts
                     WHERE org_id = $1 AND kb_slug = $2 AND path = $3
                   )""",
                org_id,
                kb_slug,
                path,
            )
            await conn.execute(
                """DELETE FROM knowledge.derivations WHERE child_id IN (
                     SELECT id FROM knowledge.artifacts
                     WHERE org_id = $1 AND kb_slug = $2 AND path = $3
                   ) OR parent_id IN (
                     SELECT id FROM knowledge.artifacts
                     WHERE org_id = $1 AND kb_slug = $2 AND path = $3
                   )""",
                org_id,
                kb_slug,
                path,
            )


async def update_artifact_extra(artifact_id: str, extra_patch: dict) -> None:
    """Merge extra_patch into knowledge.artifacts.extra (JSONB merge, AC-2)."""
    pool = await get_pool()
    await pool.execute(
        """
        UPDATE knowledge.artifacts
        SET extra = COALESCE(extra::jsonb, '{}'::jsonb) || $1::jsonb
        WHERE id = $2
        """,
        json.dumps(extra_patch),
        artifact_id,
    )
