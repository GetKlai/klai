# Finding 7 research: missing UNIQUE constraint on active artifacts

## Code/schema verification

### Constraints on `knowledge.artifacts`

Baseline migration `0001_baseline.py` defines the following for `knowledge.artifacts`:

**Primary key** (line 344-347):
```sql
ALTER TABLE ONLY knowledge.artifacts ADD CONSTRAINT artifacts_pkey PRIMARY KEY (id);
```
Single-column UUID primary key only.

**Check constraints** (lines 122-139, inside `CREATE TABLE`):
- `artifacts_assertion_mode_check` — validates `assertion_mode` enum values
- `artifacts_confidence_check` — validates `confidence` enum values
- `artifacts_provenance_type_check` — validates `provenance_type` enum values
- `artifacts_synthesis_depth_check` — validates `synthesis_depth` range

**No UNIQUE constraint exists on `knowledge.artifacts`.** The UNIQUE constraint block (lines 461-481) only covers two tables:
- `crawled_pages_uniq` on `(org_id, kb_slug, url)`
- `page_links_uniq` on `(org_id, kb_slug, from_url, to_url)`

`knowledge.artifacts` is absent from this block.

### Indexes on `knowledge.artifacts`

Lines 573-588 create four indexes on the artifacts table:

```
idx_artifacts_active          ON artifacts USING btree (belief_time_end)
idx_artifacts_active_path     ON artifacts USING btree (org_id, kb_slug, path, belief_time_end)
idx_artifacts_org_id          ON artifacts USING btree (org_id)
idx_artifacts_org_kb_path     ON artifacts USING btree (org_id, kb_slug, path)
idx_artifacts_user_id         ON artifacts USING btree (user_id) WHERE (user_id IS NOT NULL)
```

`idx_artifacts_org_kb_path` and `idx_artifacts_active_path` both cover `(org_id, kb_slug, path)` — but both are plain `btree`, not `UNIQUE`. Nothing prevents two rows with identical `(org_id, kb_slug, path)` from existing with `belief_time_end = _SENTINEL`.

### Soft-delete mechanism

The sentinel value is defined in `knowledge_ingest/pg_store.py` line 11:
```python
_SENTINEL = 253402300800  # 9999-12-31 — sentinel value for "still active"
```

An artifact is **active** when `belief_time_end = _SENTINEL` (exact equality, not `>= now()`).

`soft_delete_artifact` (lines 160-176) sets `belief_time_end = int(time.time())` for all rows matching `(org_id, kb_slug, path, belief_time_end = _SENTINEL)`:

```sql
UPDATE knowledge.artifacts
SET belief_time_end = $1          -- unix timestamp of now()
WHERE org_id = $2 AND kb_slug = $3 AND path = $4
  AND belief_time_end = $5        -- _SENTINEL (253402300800)
```

`get_active_content_hash` (lines 14-31) reads with the same filter:
```sql
SELECT content_hash FROM knowledge.artifacts
WHERE org_id = $1 AND kb_slug = $2 AND path = $3
  AND belief_time_end = $4        -- _SENTINEL
ORDER BY created_at DESC LIMIT 1
```

`create_artifact` (lines 34-81) performs a plain `INSERT INTO knowledge.artifacts (...)` with no conflict handling. There is no `ON CONFLICT`, no advisory lock, no transaction wrapping the read-check-delete-insert sequence.

### Tests for concurrent ingest of the same path

A search across `klai-knowledge-ingest/tests/` finds no test exercising two genuinely concurrent HTTP requests for the same `(org_id, kb_slug, path)`:

- `test_enrichment_dedup.py::test_two_ingests_same_path_only_one_enrichment` — tests that the Procrastinate `AlreadyEnqueued` exception is caught when a second *sequential* `ingest_document()` call is made for the same path. This covers enrichment queue deduplication, not the DB-level artifact uniqueness race.
- `test_ingest_content_hash_dedup.py` — tests the early-exit code path when content hash matches, also sequential.
- `test_ingest_enrichment_dedup.py` — tests `queueing_lock` deduplication in Procrastinate, sequential.

No integration test uses `asyncio.gather` or concurrent connections to produce two overlapping `ingest_document` calls for the same path against a real database.

---

## Current behavior

### The race window in detail

The `ingest_document` function in `knowledge_ingest/routes/ingest.py` executes the following sequence without any enclosing transaction or lock (lines 274-435):

```
Step A  get_active_content_hash(org_id, kb_slug, path)   → SELECT ... WHERE belief_time_end = SENTINEL
Step B  [content hash comparison — may return early]
Step C  [chunking, embedding, taxonomy — 200ms-2000ms wall clock]
Step D  soft_delete_artifact(org_id, kb_slug, path)       → UPDATE ... SET belief_time_end = now()
Step E  create_artifact(...)                              → INSERT ...
```

Steps A and D+E are three separate, uncoordinated database round-trips. Between them sits a multi-second CPU- and I/O-intensive pipeline (chunking, LLM calls, embedding model calls, taxonomy classification).

### Concurrent scenario leading to duplicate active rows

Given two concurrent requests R1 and R2 for identical `(org_id="org1", kb_slug="kb-main", path="docs/page.md")` with different or identical content:

| Time | R1 | R2 |
|------|----|----|
| t0 | `get_active_content_hash` → NULL (no active row) | |
| t1 | | `get_active_content_hash` → NULL (same result, no active row yet) |
| t2 | chunking + embedding (1-2 s) | chunking + embedding (1-2 s, runs in parallel) |
| t3 | `soft_delete_artifact` → UPDATE 0 rows (nothing to delete) | |
| t4 | `create_artifact` → INSERT row A with `belief_time_end = SENTINEL` | |
| t5 | | `soft_delete_artifact` → UPDATE 0 rows (row A was inserted by R1, **but R2 read at t1 before that**; however soft_delete IS idempotent against the state at t5, so it DOES close row A) |
| t6 | | `create_artifact` → INSERT row B with `belief_time_end = SENTINEL` |

At t6, the state is: row A has `belief_time_end = int(time.time())` (closed by R2 at t5) and row B is active. **Net result: one active row** — in this specific interleaving.

However, in a tighter race where both processes reach step D simultaneously:

| Time | R1 | R2 |
|------|----|----|
| t0 | `get_active_content_hash` → NULL | |
| t1 | | `get_active_content_hash` → NULL |
| t2 | `soft_delete_artifact` → UPDATE 0 (nothing) | |
| t2 | | `soft_delete_artifact` → UPDATE 0 (nothing, also nothing exists) |
| t3 | `create_artifact` → INSERT row A (SENTINEL) | |
| t3 | | `create_artifact` → INSERT row B (SENTINEL) | |

Both rows land with `belief_time_end = SENTINEL`. **Two active artifacts for the same path.** No database constraint prevents this.

If an existing active row IS present (re-ingest scenario):

| Time | R1 | R2 |
|------|----|----|
| t0 | `get_active_content_hash` → old_hash (≠ new_hash, proceeds) | |
| t1 | | `get_active_content_hash` → old_hash (≠ new_hash, proceeds) |
| t2-t4 | embedding pipeline ... | embedding pipeline ... |
| t5 | `soft_delete_artifact` → closes row A | |
| t6 | `create_artifact` → INSERT row B (SENTINEL) | |
| t7 | | `soft_delete_artifact` → closes row B (just created by R1!) | |
| t8 | | `create_artifact` → INSERT row C (SENTINEL) | |

Result: row B is immediately soft-deleted, row C is the sole active row. Net result: correct count (one active) but R1's artifact is ghosted — its Qdrant vectors are orphaned because R2's enrichment Procrastinate job will enqueue with row C's artifact_id, and the Qdrant upsert for row B's chunks never happens (or is overwritten). The Qdrant `queueing_lock` for enrichment (`{org_id}:{kb_slug}:{path}`) does prevent double enrichment, but only for the second job — R1's artifact still wrote chunks that are now stale.

The **highest-probability duplicate active rows scenario** is a simultaneous first-ever ingest of a document (no prior active artifact exists) under parallel crawl/sync workers.

---

## Industry standard (2026)

### Postgres partial unique index

The canonical Postgres solution for "uniqueness on active rows only" is a partial unique index:

```sql
CREATE UNIQUE INDEX uq_artifacts_active_path
    ON knowledge.artifacts (org_id, kb_slug, path)
    WHERE belief_time_end = 253402300800;
```

PostgreSQL enforces this at write time: two rows with identical `(org_id, kb_slug, path)` and `belief_time_end = 253402300800` cannot coexist. The second concurrent `INSERT` will raise `UniqueViolation` (SQLSTATE 23505). Soft-deleted rows (where `belief_time_end` is a unix timestamp < sentinel) are excluded from the index and can be duplicated freely.

This is explicitly supported by the PostgreSQL partial index documentation (section 11.8): "Partial unique indexes enforce uniqueness among rows satisfying the predicate." The predicate must be a constant expression; equality on a bigint sentinel qualifies.

**Limitation:** the sentinel value (`253402300800`) is hardcoded in the predicate. This is not a problem as long as the sentinel is a project constant — as it is here (`_SENTINEL` in `pg_store.py`). If the sentinel ever changes, the index predicate must be updated in a migration.

### Bitemporal data UNIQUE patterns

The artifacts table implements a simplified bitemporal model: `belief_time_start` / `belief_time_end` form a semi-open interval `[start, end)`. The active row has `end = SENTINEL` (effective open end).

In standard temporal databases (SQL:2011 `PERIOD FOR SYSTEM_TIME`, PostgreSQL temporal extensions via `pg_bitemporal`), uniqueness on current rows is enforced with EXCLUSION constraints on time ranges using GiST indexes. Example:

```sql
CREATE EXTENSION IF NOT EXISTS btree_gist;

ALTER TABLE knowledge.artifacts
    ADD CONSTRAINT no_overlap_active
    EXCLUDE USING gist (
        org_id WITH =,
        kb_slug WITH =,
        path WITH =,
        int8range(belief_time_start, belief_time_end) WITH &&
    );
```

This prevents any two active rows from having overlapping time intervals. For the sentinel pattern, the partial unique index is simpler and avoids the GiST overhead.

### Advisory locks

PostgreSQL session-level advisory locks (`pg_advisory_lock(key)`) can serialize the read-check-delete-insert sequence. The lock key is typically a hash of `(org_id, kb_slug, path)`:

```sql
SELECT pg_advisory_lock(hashtext(org_id || ':' || kb_slug || ':' || path));
-- ... read, soft-delete, insert ...
SELECT pg_advisory_unlock(hashtext(org_id || ':' || kb_slug || ':' || path));
```

Advisory locks are released at session end, so leaks on crash are recovered automatically. Transaction-level advisory locks (`pg_advisory_xact_lock`) are released on COMMIT/ROLLBACK, cleaner for connection-pooled environments (PgBouncer).

Advisory locks require all callers to agree to acquire the lock before reading — if any caller bypasses the lock, the race re-opens. The partial unique index enforces at the DB level regardless of caller discipline.

### Serializable transactions

Setting the transaction isolation level to `SERIALIZABLE` forces Postgres to detect read-write conflicts:

```python
async with pool.acquire() as conn:
    async with conn.transaction(isolation="serializable"):
        stored_hash = await conn.fetchval("SELECT ...")
        if stored_hash == new_hash:
            return  # no change
        await conn.execute("UPDATE ... SET belief_time_end = now()")
        await conn.execute("INSERT INTO ...")
```

Serializable isolation detects that the two concurrent transactions both read the same "no active artifact" fact and both try to insert — one will raise `SerializationFailure` (SQLSTATE 40001), which the caller must retry. This works but adds retry complexity and higher lock overhead across the database. The partial unique index is simpler.

### SELECT FOR UPDATE

Wrapping the initial `get_active_content_hash` in `SELECT ... FOR UPDATE` locks the returned row (if any). This prevents the re-ingest race (R1 and R2 both read the existing row, one wins the lock), but does not help the first-ever ingest race (no row exists to lock).

Combined with INSERT ON CONFLICT, a complete pattern is:

```sql
-- Step 1: try to lock the existing active row
SELECT id FROM knowledge.artifacts
WHERE org_id = $1 AND kb_slug = $2 AND path = $3
  AND belief_time_end = 253402300800
FOR UPDATE SKIP LOCKED;

-- Step 2: soft-delete and insert, knowing no other transaction
-- holds the lock on an active row for this path
```

`SKIP LOCKED` prevents deadlock but can cause a caller to skip its update if another holds the lock. This pattern is complex and still does not prevent the first-ever insert race.

### INSERT ON CONFLICT as atomic upsert

With the partial unique index in place, `INSERT ON CONFLICT` becomes available:

```sql
INSERT INTO knowledge.artifacts (id, org_id, kb_slug, path, belief_time_end, ...)
VALUES (...)
ON CONFLICT (org_id, kb_slug, path)
WHERE belief_time_end = 253402300800
DO UPDATE SET
    belief_time_end = EXCLUDED.belief_time_end,
    content_hash = EXCLUDED.content_hash,
    ...
```

This atomically either creates or updates the active artifact in a single round-trip, eliminating the separate soft-delete step and the race window entirely.

---

## Fix recommendations

### Option 1: Partial unique index (minimal change, recommended)

Add a new migration `0002_artifacts_active_unique.py`:

```sql
CREATE UNIQUE INDEX uq_artifacts_active_path
    ON knowledge.artifacts (org_id, kb_slug, path)
    WHERE belief_time_end = 253402300800;
```

**Migration considerations:**
- The index creation must complete before any concurrent writes land two active rows. On production, use `CREATE UNIQUE INDEX CONCURRENTLY` to avoid an exclusive lock (safe with Alembic via `op.execute()`).
- If duplicate active rows already exist in production (detectable with `SELECT org_id, kb_slug, path, COUNT(*) FROM knowledge.artifacts WHERE belief_time_end = 253402300800 GROUP BY 1,2,3 HAVING COUNT(*) > 1`), the concurrent index build will fail with a uniqueness violation. Those duplicates must be cleaned up first (soft-delete all but the most recently created row).
- Alembic `downgrade()` should `DROP INDEX CONCURRENTLY uq_artifacts_active_path`.

**Application change:** none required for the constraint itself. The application should catch `asyncpg.exceptions.UniqueViolationError` on `create_artifact` and either retry or treat the second write as a no-op (the enrichment `queueing_lock` already handles the downstream deduplication).

### Option 2: Atomic read-delete-insert in a single transaction (deeper change)

Wrap steps A, D, E in a single database transaction:

```python
async with pool.acquire() as conn:
    async with conn.transaction():
        stored_hash = await conn.fetchval(
            "SELECT content_hash FROM knowledge.artifacts "
            "WHERE org_id=$1 AND kb_slug=$2 AND path=$3 "
            "  AND belief_time_end=$4 FOR UPDATE",
            org_id, kb_slug, path, _SENTINEL,
        )
        if stored_hash == content_hash:
            return {"status": "skipped", ...}
        await conn.execute("UPDATE ... SET belief_time_end=now() ...")
        artifact_id = await conn.fetchval("INSERT ... RETURNING id", ...)
```

`FOR UPDATE` with no existing row does not lock anything — the first-ever ingest race still exists. This option must be combined with the partial unique index to be safe. Option 1 alone is sufficient.

### Option 3: Atomic upsert with INSERT ON CONFLICT (architectural change)

Requires the partial unique index from Option 1 plus a rewrite of `soft_delete_artifact` + `create_artifact` into a single upsert that:
1. Inserts the new artifact row.
2. On conflict (an active row for the same path already exists), updates `content_hash`, `belief_time_start`, `content_type`, `extra` in place.

This eliminates the separate soft-delete step, the "ghosted R1 artifact" scenario, and the race window simultaneously. The downside is that the artifact `id` changes on re-ingest (currently guaranteed because each re-ingest generates a new UUID), which may break downstream artifact-id references in Qdrant payloads and `derivations` rows. Would require auditing all artifact_id consumers before adopting.

**Recommendation:** implement Option 1 (partial unique index + `UniqueViolationError` catch in `create_artifact`) as a near-term migration. Defer Option 3 until artifact-id stability requirements are clarified.

---

## Risk assessment

### Likelihood

The race window exists whenever two ingest workers process the same `(org_id, kb_slug, path)` concurrently. Callers that can trigger this:

1. **Parallel crawl workers** — `klai-knowledge-ingest/knowledge_ingest/crawl_tasks.py` dispatches multiple `ingest_document` calls via Procrastinate. If two crawl jobs for different pages share the same path (unlikely by URL design) or if a crawl retries a previously in-flight page, the race opens.
2. **klai-connector sync workers** — `klai-connector` calls the `/ingest` HTTP endpoint per document. If a connector has two simultaneous sync runs active for the same KB (e.g. a manual "sync now" triggered while a scheduled sync is running), they can both submit the same document path.
3. **Personal KB upload** — users uploading duplicate filenames concurrently (rare, but the route is unauthenticated at the document level).
4. **`rebuild_kb` task** — `SPEC-RAG-REBUILD-KB-001` replay reads from `pg_extra["document_text"]` and re-ingests each artifact. If triggered on a KB while a live sync is also running, the same path can be processed by both.

The most realistic trigger is a connector with two sync runs overlapping, which is not prevented by the current connector state guard (`connector_is_active` only checks for `deleting` state, not `already_syncing`).

### Impact

**DB level:** two `knowledge.artifacts` rows with `belief_time_end = SENTINEL` for the same `(org_id, kb_slug, path)`. Queries using `LIMIT 1 ORDER BY created_at DESC` return the newer row correctly, but queries without the limit (e.g. bulk KB operations, delete by path) process both rows, doubling the work and potentially leaving orphaned Qdrant chunks for the older of the two rows.

**Qdrant level:** each artifact_id gets its own set of Qdrant points. With two active artifact_ids for the same path, retrieval will return duplicate chunks (same content, different metadata). The `queueing_lock` in Procrastinate prevents double enrichment tasks, but only for the second ingest call — if the first enrichment completes before the second `ingest_document` call arrives, the second will enqueue its own enrichment job and produce a second set of Qdrant points for the same content.

**Severity:** Medium. Not a data loss event (document content is present). Creates retrieval quality degradation (duplicate chunks inflate scores for documents that have concurrent ingests) and bloat in both PostgreSQL and Qdrant. The scenario requires concurrent writes for the same path, which is uncommon in single-tenant KB usage but becomes probable at scale or during `rebuild_kb`.

---

## References

- [PostgreSQL 18 Documentation: Partial Indexes (section 11.8)](https://www.postgresql.org/docs/current/indexes-partial.html)
- [PostgreSQL 18 Documentation: CREATE INDEX — partial index syntax](https://www.postgresql.org/docs/current/sql-createindex.html)
- [PostgreSQL 18 Documentation: Explicit Locking — advisory locks](https://www.postgresql.org/docs/current/explicit-locking.html)
- [Soft Delete and Unique Constraint — practical partial index patterns](https://gusiol.medium.com/soft-delete-and-unique-constraint-da94b41cff62)
- [Using Unique Database Fields with Soft Deletes — partial index techniques](https://medium.com/@BBreyten/using-unique-fields-and-soft-deletes-fe37e7c47ce3)
- [Postgres: Building concurrently safe upsert queries](https://devandchill.com/posts/2020/02/postgres-building-concurrently-safe-upsert-queries/)
- [Using PostgreSQL advisory locks to avoid race conditions — FireHydrant](https://firehydrant.com/blog/using-advisory-locks-to-avoid-race-conditions-in-rails/)
- [How to Handle Race Conditions in PostgreSQL Functions](https://oneuptime.com/blog/post/2026-01-25-postgresql-race-conditions/view)
- [Bitemporal Tables in PostgreSQL — pg_bitemporal](https://github.com/scalegenius/pg_bitemporal)
- [Temporal Extensions — PostgreSQL wiki](https://wiki.postgresql.org/wiki/Temporal_Extensions)
- [(Bi)Temporal Tables, PostgreSQL and SQL Standard](https://hdombrovskaya.wordpress.com/2024/05/05/3937/)
- [SQLAlchemy community discussion: soft delete with unique constraint](https://github.com/sqlalchemy/sqlalchemy/discussions/10152)
