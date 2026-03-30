# SPEC-KB-004: Knowledge Schema Integration

> Status: COMPLETED (2026-03-26)
> Author: Mark Vletter (design) + Claude (SPEC)
> Builds on: SPEC-KB-002 (ingest pipeline), SPEC-KB-003 (app layer)
> Architecture reference: `docs/architecture/klai-knowledge-architecture.md` §3, §5.2
> Created: 2026-03-25

---

## What exists today

`knowledge-ingest` chunks, embeds, and upserts directly to Qdrant. `pg_store.py` is a no-op stub. PostgreSQL `knowledge.*` tables are created (migration `001_knowledge_schema.sql`) and empty.

Qdrant points carry: `org_id`, `kb_slug`, `path`, `user_id`, `text`, `chunk_index`, `title`, `source_type`, `tags`, `provenance_type`, `confidence`, `source_note`.

**The problem:** Qdrant point IDs are random UUIDs with no link to PostgreSQL. Structured metadata (provenance, assertion mode, synthesis depth, temporal validity) lives in YAML frontmatter only — it is never indexed or queryable. There is no way to ask "show me all factual claims from a specific source" or "what did we believe about X before a given date."

---

## What this SPEC builds

Wire the ingest pipeline into `knowledge.artifacts`. After this SPEC:

- Every ingested document creates one `knowledge.artifacts` record with its frontmatter metadata
- Every Qdrant chunk carries an `artifact_id` payload field linking back to that record
- Retrieve responses include structured metadata (provenance_type, assertion_mode, synthesis_depth, confidence)
- Deleting a document soft-deletes the artifact record (sets `belief_time_end`) and hard-deletes from Qdrant
- The foundation is laid for filtering, invalidation, and supersession (not built here)

---

## Design decisions

### D1: One artifact per document, not per chunk

`knowledge.artifacts` represents a logical knowledge item (a page/document), not a physical chunk. A document with 12 chunks creates one artifact record and 12 Qdrant points, all carrying the same `artifact_id` payload field.

Rationale: provenance, assertion mode, and temporal validity are properties of the knowledge claim (the document), not of a text fragment. Chunk-level granularity adds storage overhead and complexity with no retrieval benefit at current scale.

### D2: Frontmatter drives artifact fields; safe defaults for documents without frontmatter

If frontmatter contains knowledge model fields, use them. If not, default:

| Field | Default |
|---|---|
| `provenance_type` | `observed` |
| `assertion_mode` | `factual` |
| `synthesis_depth` | `0` |
| `confidence` | `NULL` |
| `belief_time_start` | ingest time (Unix epoch) |
| `belief_time_end` | `253402300800` (sentinel = active) |

Documents ingested via `source_type: connector` default `synthesis_depth: 0` (raw capture). Documents ingested via `source_type: docs` (human-authored in editor) default `synthesis_depth: 4` (published curated artifact). These are overridden by explicit frontmatter values.

### D3: Synchronous PostgreSQL write — no queue worker yet

At current scale (<1,000 documents per org) write to PostgreSQL and Qdrant synchronously within the ingest call. The `embedding_queue` table is NOT used yet — it is scaffolding for a future background worker when write throughput demands it. Remove the stub entirely; add it back with real implementation when the throughput case exists.

### D4: org_id is stored as TEXT in knowledge.artifacts

The existing migration defines `org_id UUID NOT NULL`. This is wrong: `knowledge-ingest` identifies tenants by Zitadel org ID, which is an 18-digit integer string (e.g., `"362757920133283846"`), not a UUID. Portal integer PKs are internal to portal-api and not available to knowledge-ingest.

A migration is required to change `org_id` and `user_id` to `TEXT`. This is a non-breaking change (tables are empty).

### D5: Artifact ID is a new UUID, not the Gitea page UUID

The `id` field in YAML frontmatter is the stable page UUID used by klai-docs for wikilinks and cross-references. The `knowledge.artifacts.id` is the database primary key for the knowledge record.

These are kept separate because:
- Klai-docs IDs are set by the editor and may not be present on all pages (optional field)
- A single klai-docs page can be re-ingested multiple times (e.g., after edits), creating a new artifact version while preserving the old one
- Coupling them would make supersession logic fragile

The `klai_docs_id` (the frontmatter `id` field, if present) is stored as a TEXT payload field in Qdrant for cross-referencing, but it is not the artifact's primary key.

---

## Migration required

**002_knowledge_schema_fix.sql** — change `org_id` / `user_id` to `TEXT`:

```sql
-- Migration: 002_knowledge_schema_fix.sql
-- Changes org_id and user_id from UUID to TEXT to match Zitadel ID format.
-- Safe: knowledge.artifacts is empty at time of this migration.

ALTER TABLE knowledge.artifacts ALTER COLUMN org_id TYPE TEXT;
ALTER TABLE knowledge.artifacts ALTER COLUMN user_id TYPE TEXT;
ALTER TABLE knowledge.entities  ALTER COLUMN org_id TYPE TEXT;
```

Run: `docker exec -i klai-core-postgres-1 psql -U klai -d klai < 002_knowledge_schema_fix.sql`

---

## Changes to `knowledge-ingest`

### New: `db.py` — asyncpg connection pool

```python
# knowledge_ingest/db.py
import asyncpg
from knowledge_ingest.config import settings

_pool: asyncpg.Pool | None = None

async def get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        # Convert SQLAlchemy DSN to asyncpg format
        dsn = settings.postgres_dsn.replace("postgresql+asyncpg://", "postgresql://")
        _pool = await asyncpg.create_pool(dsn, min_size=2, max_size=10)
    return _pool

async def close_pool() -> None:
    global _pool
    if _pool:
        await _pool.close()
        _pool = None
```

Wire into `app.py` lifespan: open pool on startup, close on shutdown.

### Rewrite: `pg_store.py`

Replace the stub with real writes:

```python
# knowledge_ingest/pg_store.py
import time
import uuid
from knowledge_ingest.db import get_pool

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
    klai_docs_id: str | None = None,
) -> str:
    """Create a knowledge artifact record. Returns the artifact UUID."""
    artifact_id = str(uuid.uuid4())
    now = int(time.time())
    pool = await get_pool()
    await pool.execute(
        """
        INSERT INTO knowledge.artifacts
          (id, org_id, user_id, provenance_type, assertion_mode,
           synthesis_depth, confidence, belief_time_start, belief_time_end, created_at)
        VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)
        """,
        artifact_id, org_id, user_id, provenance_type, assertion_mode,
        synthesis_depth, confidence, belief_time_start, belief_time_end, now,
    )
    return artifact_id


async def soft_delete_artifact(org_id: str, kb_slug: str, path: str) -> None:
    """Set belief_time_end = now for all active artifacts for this path."""
    now = int(time.time())
    pool = await get_pool()
    # Artifact records don't store kb_slug/path — use Qdrant as the index for that.
    # This is called after Qdrant delete, so we use a dedicated lookup column.
    # See D5: artifact records store path + kb_slug for exactly this purpose.
    await pool.execute(
        """
        UPDATE knowledge.artifacts
        SET belief_time_end = $1
        WHERE org_id = $2 AND kb_slug = $3 AND path = $4
          AND belief_time_end = 253402300800
        """,
        now, org_id, kb_slug, path,
    )
```

> **Schema addition required:** Add `kb_slug TEXT NOT NULL DEFAULT ''` and `path TEXT NOT NULL DEFAULT ''` columns to `knowledge.artifacts`. These are needed for the soft-delete path (invalidating the correct artifact when a document is removed). Add as migration **003**.

### Updated: `ingest.py` — parse frontmatter and call pg_store

In `ingest_document()`, after chunking, before embedding:

1. Parse knowledge model fields from frontmatter (see helper below)
2. Call `pg_store.create_artifact()` → get `artifact_id`
3. Pass `artifact_id` to `qdrant_store.upsert_chunks()`
4. Remove the stub `pg_store.record_ingest()` call

**Frontmatter parser** (add to `ingest.py`):

```python
import time

_SENTINEL = 253402300800  # 9999-12-31

def _parse_knowledge_fields(content: str, source_type: str | None) -> dict:
    """Extract knowledge model fields from YAML frontmatter. Returns defaults if absent."""
    defaults = {
        "provenance_type": "observed",
        "assertion_mode": "factual",
        "synthesis_depth": 4 if source_type == "docs" else 0,
        "confidence": None,
        "belief_time_start": int(time.time()),
        "belief_time_end": _SENTINEL,
        "klai_docs_id": None,
    }
    if not content.startswith("---"):
        return defaults
    end = content.find("\n---", 3)
    if end == -1:
        return defaults
    try:
        fm = yaml.safe_load(content[3:end])
        if not isinstance(fm, dict):
            return defaults
    except Exception:
        return defaults

    result = dict(defaults)
    if fm.get("provenance_type") in ("observed","extracted","synthesized","revised"):
        result["provenance_type"] = fm["provenance_type"]
    if fm.get("assertion_mode") in ("factual","procedural","quoted","belief","hypothesis"):
        result["assertion_mode"] = fm["assertion_mode"]
    if isinstance(fm.get("synthesis_depth"), int) and 0 <= fm["synthesis_depth"] <= 4:
        result["synthesis_depth"] = fm["synthesis_depth"]
    if fm.get("confidence") in ("high","medium","low"):
        result["confidence"] = fm["confidence"]
    if isinstance(fm.get("belief_time_start"), str):
        try:
            from datetime import datetime, timezone
            result["belief_time_start"] = int(
                datetime.fromisoformat(fm["belief_time_start"]).replace(tzinfo=timezone.utc).timestamp()
            )
        except Exception:
            pass
    if fm.get("id") and isinstance(fm["id"], str):
        result["klai_docs_id"] = fm["id"]
    return result
```

### Updated: `qdrant_store.py` — add `artifact_id` to payload

In `upsert_chunks()`, add `artifact_id` parameter and include it in `base_payload`:

```python
async def upsert_chunks(
    org_id: str,
    kb_slug: str,
    path: str,
    chunks: list[str],
    vectors: list[list[float]],
    artifact_id: str,           # NEW
    extra_payload: dict | None = None,
    user_id: str | None = None,
) -> None:
    base_payload = {
        "org_id": org_id, "kb_slug": kb_slug, "path": path,
        "artifact_id": artifact_id,  # NEW
    }
    ...
```

Also add `artifact_id` to the Qdrant payload index in `ensure_collection()`:

```python
await client.create_payload_index(COLLECTION, field_name="artifact_id", field_schema="keyword")
```

Add `artifact_id` to `_ALLOWED_METADATA_FIELDS` so it is returned in search results.

### Updated: `retrieve.py` — enrich results with PostgreSQL metadata

After Qdrant search returns results, extract distinct `artifact_id` values from payloads and fetch the corresponding artifact records from PostgreSQL. Merge into the response.

```python
# After Qdrant search, collect artifact IDs
artifact_ids = list({r["metadata"].get("artifact_id") for r in results if r["metadata"].get("artifact_id")})

if artifact_ids:
    pool = await pg_store.get_pool()  # or import from db
    rows = await pool.fetch(
        "SELECT id, provenance_type, assertion_mode, synthesis_depth, confidence, "
        "       belief_time_start, belief_time_end "
        "FROM knowledge.artifacts WHERE id = ANY($1::text[])",
        artifact_ids,
    )
    artifact_meta = {str(r["id"]): dict(r) for r in rows}
    for chunk in results:
        aid = chunk["metadata"].get("artifact_id")
        if aid and aid in artifact_meta:
            chunk["metadata"].update(artifact_meta[aid])
```

### Updated: `models.py` — extend ChunkResult

Add knowledge model fields to `ChunkResult`:

```python
class ChunkResult(BaseModel):
    text: str
    source: str
    score: float
    metadata: dict = {}
    artifact_id: str | None = None
    provenance_type: str | None = None
    assertion_mode: str | None = None
    synthesis_depth: int | None = None
    confidence: str | None = None
```

---

## Migrations summary

| Migration | File | What |
|---|---|---|
| 001 | `deploy/postgres/migrations/001_knowledge_schema.sql` | Existing — creates all tables |
| 002 | `deploy/postgres/migrations/002_knowledge_schema_fix.sql` | Change `org_id`/`user_id` to TEXT |
| 003 | `deploy/postgres/migrations/003_knowledge_artifacts_path.sql` | Add `kb_slug TEXT`, `path TEXT` columns to `knowledge.artifacts` |

---

## What is NOT in scope

| Item | Where it goes |
|---|---|
| `knowledge.entities` / entity extraction | Future SPEC — requires GLiNER or spaCy pipeline |
| `knowledge.derivations` (provenance DAG) | Future SPEC — requires multi-source synthesis workflow |
| `embedding_queue` background worker | Future SPEC — when write throughput demands async path |
| Superseded_by logic | Future SPEC — requires version management UI |
| Gap detection | Future SPEC — requires sufficient query volume first |
| Filtering retrieve by assertion_mode / synthesis_depth | Can be added as a small follow-on; not needed to ship this SPEC |

---

## Acceptance criteria

| # | Criterion |
|---|---|
| AC-1 | `POST /ingest/v1/document` creates one row in `knowledge.artifacts` with correct provenance_type, assertion_mode, synthesis_depth from frontmatter (or defaults) |
| AC-2 | All Qdrant chunks for that document carry the same `artifact_id` payload matching the PostgreSQL row |
| AC-3 | `POST /knowledge/v1/retrieve` response includes `artifact_id`, `provenance_type`, `assertion_mode`, `synthesis_depth`, `confidence` per chunk |
| AC-4 | `DELETE /ingest/v1/kb` or document removal sets `belief_time_end = now` in `knowledge.artifacts` (soft delete) |
| AC-5 | Re-ingesting an existing document (e.g., after a Gitea push) creates a NEW artifact row; the previous row's `belief_time_end` is set to now |
| AC-6 | Documents without knowledge model frontmatter ingest without error and receive correct defaults |
| AC-7 | `confidence` in `("high","medium","low")` from frontmatter is stored correctly; invalid values fall back to NULL |
| AC-8 | Migration 002 applied: `knowledge.artifacts.org_id` accepts `"362757920133283846"` (Zitadel string ID) without error |
| AC-9 | Existing tests pass; no regression on ingest or retrieve endpoints |
| AC-10 | `asyncpg` connection pool is closed cleanly on service shutdown (no "connection leak" warnings in logs) |
