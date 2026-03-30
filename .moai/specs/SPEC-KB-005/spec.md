# SPEC-KB-005: Contextual Retrieval and HyPE Enrichment

> Status: COMPLETED (2026-03-26)
> Author: Mark Vletter (design) + Claude (SPEC)
> Builds on: SPEC-KB-004 (knowledge schema), SPEC-KB-002 (ingest pipeline)
> Architecture reference: `docs/architecture/klai-knowledge-architecture.md` SS4.2
> Created: 2026-03-26

---

## What exists today

`knowledge-ingest` chunks a document (via `chunker.chunk_markdown`), embeds the raw chunk text (via `embedder.embed` calling TEI/BGE-M3 dense), and upserts to Qdrant with the original chunk text in the `text` payload field. There is no enrichment step between chunking and embedding.

The current pipeline in `ingest.py`:
```
content --> chunk_markdown --> embed(raw chunks) --> upsert_chunks(raw text + vectors)
```

Retrieval quality depends entirely on the embedding similarity between the user's query and the raw chunk text. Chunks that lack context ("The return period is 30 days") perform poorly when the query does not use the same vocabulary as the chunk.

---

## What this SPEC builds

Two complementary enrichment techniques in a single LLM call inserted between chunking and embedding:

1. **Contextual prefix generation** -- each chunk gets a 1-2 sentence prefix situating it within the parent document.
2. **HyPE (Hypothetical Prompt Embeddings)** -- the same LLM call generates 3-5 questions that the chunk answers. For depth 0-1 chunks (raw transcripts, connector imports, unstructured PDFs), these questions are embedded as a second named vector. For depth 3-4 chunks (curated KB articles, published help center content), questions are stored but not embedded.

After this SPEC, the ingest pipeline becomes:
```
content --> chunk_markdown --> LLM enrichment (1 combined call) --> embed --> upsert
```

The single LLM call returns:
```json
{"context_prefix": "...", "questions": ["...", "...", "...", "..."]}
```

The enrichment is async (enqueued via Procrastinate, does not block the ingest HTTP response), configurable per org via database config, and degrades gracefully to raw embedding when the LLM is unavailable.

---

## Design decisions

### D1: Enrichment runs through internal LiteLLM proxy, not direct API

All enrichment LLM calls go through the existing LiteLLM proxy at `http://litellm:4000` using the `klai-fast` model alias. This provides:
- Model routing (Mistral Small API during ramp-up, self-hosted at scale)
- Rate limiting and cost tracking via existing LiteLLM infrastructure
- No new API keys or direct vendor SDK dependencies in knowledge-ingest

### D2: Contextual prefix is prepended to chunk text before embedding

The enriched chunk stored in Qdrant is `"{context_prefix}\n\n{original_text}"`. The original text is preserved separately in a `text_original` payload field so retrieval can display the clean text to the user while embedding benefits from the context.

Why not store only the prefix separately: the embedding must operate on the combined text. Splitting them at embed time adds complexity for no benefit.

### D3: Dual-Index Fusion with named vectors (Qdrant)

Two named vectors on every point in the collection:

- **`vector_chunk`**: embedding of `"{context_prefix}\n\n{original_text}"` -- populated for ALL chunks
- **`vector_questions`**: embedding of the aggregated questions (all questions concatenated into one string, then embedded) -- populated ONLY for synthesis_depth 0-1 chunks

Named vectors with partial population is a first-class Qdrant feature (v1.2.0+). Points without `vector_questions` are automatically excluded from that HNSW graph. No storage penalty for empty slots.

**Why Dual-Index Fusion instead of separate points per question:**

Separate points (5x growth) are qualitatively inferior: individual question vectors are more noise-prone than an aggregated representation. Concatenation of all questions into one embedding degrades quality by 9-12% nDCG@10 compared to Dual-Index Fusion (Doc2Query++, arXiv:2510.09557). Dual-Index Fusion consistently outperforms both alternatives on all tested datasets.

**Why selective application (depth 0-1 only for `vector_questions`):**

HyPE bridges a vocabulary gap between user queries and chunk content. When that gap is already small (curated KB articles at depth 3-4), expansion adds noise and degrades retrieval (EACL 2024 Findings). This is a QUALITY decision, not a storage decision:

- **depth 0-1**: raw transcripts, connector imports, unstructured PDFs -- vocabulary gap is large, HyPE helps
- **depth 3-4**: curated KB articles, published help center content -- gap already small, HyPE hurts

**Known limitation (calibration note):** `synthesis_depth` is a KB-biased proxy for vocabulary gap. In the current klai corpus (predominantly KB articles), depth correlates well with vocabulary accessibility. As the corpus broadens to include technical documentation, research papers, or domain-specific content at depth 3-4, this assumption may not hold -- a technical specification at depth 4 can have a large vocabulary gap that HyPE would help bridge. The long-term signal is content-type (via the adapter that produced the artifact), not synthesis_depth. The `knowledge.org_config.extra_config` JSONB field is intentionally available to add per-org or per-content-type threshold overrides without a schema migration.

### D4: Enrichment via Procrastinate task queue, not asyncio.create_task

The ingest endpoint enqueues a Procrastinate job and returns immediately (same user-facing behavior as before). Procrastinate is a PostgreSQL-backed async task queue that reuses the existing PG database -- no new infrastructure required.

Two named queues:
- **`enrich-interactive`**: single-doc uploads; always drains first (higher priority)
- **`enrich-bulk`**: crawl/import jobs

Worker runs as a separate process alongside the FastAPI app.

**Transactional enqueue**: the Procrastinate job is created in the same DB transaction as the artifact insert. If the transaction rolls back, no ghost enrichment job is left behind.

Flow:
1. `ingest_document()` inserts artifact, chunks, embeds raw text, upserts to Qdrant (immediate)
2. In the same transaction, enqueues a Procrastinate task on the appropriate queue
3. Procrastinate worker picks up the task, generates contextual prefix + questions via LLM
4. Worker embeds enriched text into `vector_chunk`; if synthesis_depth <= 1, also embeds aggregated questions into `vector_questions`
5. Worker upserts enriched vectors (overwriting the raw chunk points)

If the enrichment task fails, the raw vectors remain in Qdrant. The document is searchable immediately, just without enrichment benefit. Procrastinate handles retries (configurable, default: 2 retries with exponential backoff).

### D5: Per-org enrichment config in PostgreSQL, not env vars

Per-org configuration is stored in a sparse override table:

```sql
CREATE TABLE knowledge.org_config (
    org_id              TEXT PRIMARY KEY,
    enrichment_enabled  BOOLEAN,        -- NULL = use global default (true)
    extra_config        JSONB NOT NULL DEFAULT '{}',
    updated_at          BIGINT NOT NULL
);
```

- **Sparse**: only orgs with non-default config have a row. Global default is enrichment enabled.
- **In-process cache**: `cachetools.TTLCache(maxsize=20_000, ttl=60)` to avoid a DB query on every ingest.
- **Cache invalidation**: a PostgreSQL trigger on `knowledge.org_config` fires `NOTIFY org_config_changed, '<org_id>'`. The application listens on this channel and evicts the specific org from the TTL cache on any change.
- **Emergency override**: the `ENRICHMENT_ENABLED` env var remains as a global kill switch. When `false`, no enrichment runs for any org regardless of the database config.

**Enrichment applies to all synthesis depths for contextual prefix**: Contextual Retrieval does not increase storage (one vector per chunk regardless), so there is no reason to gate it by synthesis depth. The selective-by-depth logic applies only to `vector_questions` population (HyPE), where depth 0-1 chunks get the additional vector and depth 3-4 chunks do not.

Migration: `deploy/postgres/migrations/004_knowledge_org_config.sql` (to be created during implementation).

### D6: One combined LLM call per chunk with structured JSON output

Each chunk gets a single LLM call that returns structured JSON:

```json
{"context_prefix": "...", "questions": ["...", "...", "...", "..."]}
```

- **Document context: first 2,000 tokens** of document content (after frontmatter stripping) -- not the full document.
- Rationale: Mistral Small performs better on focused short contexts; the first 2-3 pages contain enough context for accurate prefix generation; 6x cheaper per call compared to a 12,000-token context.
- **Known limitation (content-type note):** First-N tokens works well for documents where context is front-loaded (reports, articles, manuals). For **transcripts** and **email threads**, the most relevant context is often mid-document or the most recent message, not the first lines. When these content types are introduced via adapters, the `enrichment.py` module should select document context by adapter/content-type rather than always slicing the first 2,000 tokens. This is out of scope for KB-005 (current corpus is document-first) but should be addressed before transcripts or email threads are ingested at scale.
- Structured output via Pydantic schema + JSON mode.
- Prompt in Dutch: the model follows the document language naturally, but a Dutch prompt produces better Dutch output for the predominantly Dutch knowledge base.
- The `questions` field is used for `vector_questions` embedding (depth 0-1 chunks) and stored in payload for all chunks.

### D7: Batch LLM calls with semaphore

Contextual prefix generation requires document context per chunk, so it is one LLM call per chunk. The batching opportunity is at the HTTP level: use `asyncio.gather` with a semaphore to parallelize LLM calls (max 5 concurrent per ingest to avoid overwhelming LiteLLM).

| Step | Input | Max output | Estimated tokens/chunk |
|---|---|---|---|
| Combined call | First 2,000 tokens of document + chunk text | 300 tokens (prefix + questions JSON) | ~2,200 input + 300 output |

For a 10-chunk document: ~25,000 input tokens + 3,000 output tokens total. At Mistral Small pricing this is negligible. At self-hosted the cost is compute time only.

### D8: Collection migration for named vectors

The existing `klai_knowledge` collection uses a single unnamed (default) vector. This SPEC requires two named vectors (`vector_chunk` and `vector_questions`). Qdrant does not support adding named vectors to an existing collection with a default vector.

**Migration path:**
1. Create a new collection `klai_knowledge_v2` with both named vectors configured (same dimensions as current: BGE-M3 1024-dim dense)
2. Re-index all existing documents through the enrichment pipeline (backfill script)
3. Switch the application to read from `klai_knowledge_v2`
4. Drop the old `klai_knowledge` collection after validation

This is a one-time migration that doubles as the backfill for enriching existing documents.

---

## Changes to `knowledge-ingest`

### New: `enrichment.py` -- LLM enrichment service

```python
# knowledge_ingest/enrichment.py
from pydantic import BaseModel

class EnrichmentResult(BaseModel):
    context_prefix: str
    questions: list[str]

ENRICHMENT_PROMPT = """
Documenttitel: {title}
Pad: {path}

<document>
{document_text}
</document>

<chunk>
{chunk_text}
</chunk>

Genereer een JSON-object met:
- "context_prefix": een zin van 1-2 regels die deze chunk plaatst binnen het document
  (welk document, welk onderwerp, welke sectie).
- "questions": 3-5 vragen die deze chunk beantwoordt. De vragen moeten natuurlijke
  zoekopdrachten zijn die een gebruiker zou typen.

Antwoord ALLEEN met geldig JSON.
"""
```

Functions:
- `async def enrich_chunk(document_text: str, chunk_text: str, title: str, path: str) -> EnrichmentResult | None`
- `async def enrich_chunks(document_text: str, chunks: list[str], title: str, path: str) -> list[EnrichedChunk]`

Each function calls LiteLLM proxy via `httpx.AsyncClient` with `response_format` set to JSON mode. Returns `None` on failure (timeout, HTTP error, parse error). Timeout: 15 seconds per LLM call. Document text is truncated to the first 2,000 tokens after frontmatter stripping.

`EnrichedChunk` dataclass:
```python
@dataclass
class EnrichedChunk:
    original_text: str
    enriched_text: str        # context_prefix + "\n\n" + original_text
    context_prefix: str
    questions: list[str]      # embedded as vector_questions for depth 0-1; stored in payload for all
```

### New: `enrichment_tasks.py` -- Procrastinate task definitions

```python
# knowledge_ingest/enrichment_tasks.py
import procrastinate

app = procrastinate.App(connector=procrastinate.PsycopgConnector())

@app.task(queue="enrich-interactive", retry=2)
async def enrich_document_interactive(
    org_id: str,
    kb_slug: str,
    path: str,
    document_text: str,
    chunks: list[str],
    title: str,
    artifact_id: str,
    user_id: str | None,
    extra_payload: dict,
    synthesis_depth: int,
) -> None:
    """Enrich chunks for a single-doc upload (high priority)."""
    await _enrich_document(org_id, kb_slug, path, document_text, chunks, title, artifact_id, user_id, extra_payload, synthesis_depth)

@app.task(queue="enrich-bulk", retry=2)
async def enrich_document_bulk(
    # same signature
) -> None:
    """Enrich chunks for crawl/import jobs (lower priority)."""
    await _enrich_document(...)
```

The `_enrich_document` function:
1. Calls `enrichment.enrich_chunks()` with semaphore (max 5 concurrent LLM calls)
2. For each enriched chunk: embed the `enriched_text` as `vector_chunk`
3. If `synthesis_depth <= 1`: concatenate all questions into one string, embed as `vector_questions`
4. Upsert to Qdrant with named vectors and payload fields (`text_original`, `text_enriched`, `context_prefix`, `questions`)

### New: `org_config.py` -- per-org configuration

```python
# knowledge_ingest/org_config.py
import cachetools
from asyncio import get_event_loop

_cache: cachetools.TTLCache = cachetools.TTLCache(maxsize=20_000, ttl=60)

async def is_enrichment_enabled(org_id: str) -> bool:
    """Check if enrichment is enabled for this org. Uses cache + DB + env override."""
    if not settings.enrichment_enabled:  # global kill switch
        return False
    if org_id in _cache:
        return _cache[org_id]
    row = await db.fetchrow("SELECT enrichment_enabled FROM knowledge.org_config WHERE org_id = $1", org_id)
    enabled = row["enrichment_enabled"] if row and row["enrichment_enabled"] is not None else True
    _cache[org_id] = enabled
    return enabled
```

A PostgreSQL trigger + LISTEN/NOTIFY evicts specific orgs from cache on change.

### Updated: `config.py` -- new settings

```python
# Add to Settings:
litellm_url: str = "http://litellm:4000"
litellm_api_key: str = ""  # LiteLLM API key for enrichment calls
enrichment_enabled: bool = True  # global kill switch (env var emergency override)
enrichment_model: str = "klai-fast"
enrichment_timeout: float = 15.0
enrichment_max_concurrent: int = 5
enrichment_max_document_tokens: int = 2000
```

### Updated: `ingest.py` -- enqueue Procrastinate task

In `ingest_document()`, after the existing `qdrant_store.upsert_chunks()` call:

```python
# After existing upsert (raw vectors already in Qdrant)
if await org_config.is_enrichment_enabled(req.org_id):
    task_fn = (
        enrichment_tasks.enrich_document_interactive
        if req.source_type == "upload"
        else enrichment_tasks.enrich_document_bulk
    )
    await task_fn.defer_async(
        org_id=req.org_id,
        kb_slug=req.kb_slug,
        path=req.path,
        document_text=req.content,
        chunks=texts,
        title=title,
        artifact_id=artifact_id,
        user_id=req.user_id,
        extra_payload=extra_payload,
        synthesis_depth=req.synthesis_depth,
    )
```

The `defer_async` call is part of the same DB transaction as the artifact insert -- transactional enqueue.

### Updated: `qdrant_store.py` -- support named vectors and enriched upserts

New function for enriched upsert using named vectors:

```python
async def upsert_enriched_chunks(
    org_id: str,
    kb_slug: str,
    path: str,
    enriched_chunks: list,  # list[EnrichedChunk]
    chunk_vectors: list[list[float]],         # vector_chunk embeddings
    question_vectors: list[list[float] | None],  # vector_questions embeddings (None for depth 3-4)
    artifact_id: str,
    extra_payload: dict | None = None,
    user_id: str | None = None,
) -> None:
```

This function:
1. Deletes existing points for this path (same as current behavior)
2. Creates one point per enriched chunk with named vectors:
   - `vector_chunk`: always populated (enriched text embedding)
   - `vector_questions`: populated only when `question_vectors[i]` is not None (depth 0-1 chunks)
3. Payload fields: `text` (original), `text_enriched`, `context_prefix`, `questions`

### Updated: `retrieve.py` -- Dual-Index Fusion retrieval

At query time, use Qdrant prefetch to search both named vectors and fuse results via RRF:

```python
results = client.query_points(
    collection_name="klai_knowledge",
    prefetch=[
        models.Prefetch(query=query_vector, using="vector_chunk", limit=20),
        models.Prefetch(query=query_vector, using="vector_questions", limit=20),
    ],
    query=models.FusionQuery(fusion=models.Fusion.RRF),
    limit=top_k,
)
```

When `vector_questions` is not populated on a point, Qdrant automatically excludes it from the `vector_questions` prefetch leg. No application-level filtering needed.

### New: `migrate_collection.py` -- collection migration script

Script to create the new collection with named vectors and re-index existing documents:

```python
# Create new collection with named vectors
client.create_collection(
    collection_name="klai_knowledge_v2",
    vectors_config={
        "vector_chunk": models.VectorParams(size=1024, distance=models.Distance.COSINE),
        "vector_questions": models.VectorParams(size=1024, distance=models.Distance.COSINE),
    },
)
```

The migration script iterates all existing artifacts, runs them through the enrichment pipeline, and upserts to the new collection. After validation, the application switches to `klai_knowledge_v2`.

---

## What is NOT in scope

| Item | Why not now |
|---|---|
| Sparse embeddings (FlagEmbedding) | Deferred per SS0 -- TEI dense-only until >1K docs |
| Re-enrichment of existing documents | Handled by the collection migration script (D8); not part of the ongoing ingest code path |
| Enrichment for personal KB or transcripts | GDPR: enrichment sends content to LLM API. Org KB content is allowed (SS13.6); personal/transcript content is not until legal basis established |
| Reranker integration | Separate concern (SS7); this SPEC only improves the embedding quality |

---

## Acceptance criteria

| # | Criterion | EARS pattern |
|---|---|---|
| AC-1 | **When** a document is ingested with enrichment enabled for that org, **then** each chunk receives a contextual prefix and the Qdrant point's `vector_chunk` is the embedding of `"{prefix}\n\n{original_text}"` | Event-driven |
| AC-2 | **When** a document is ingested with enrichment enabled and `synthesis_depth <= 1`, **then** the LLM response's 3-5 questions are concatenated and embedded as `vector_questions` on the Qdrant point | Event-driven |
| AC-3 | **When** a document is ingested with enrichment enabled and `synthesis_depth > 1`, **then** the questions are stored in the `questions` payload field but `vector_questions` is NOT populated on the Qdrant point | Event-driven |
| AC-4 | **When** the enrichment LLM call fails or times out (>15s), **then** the system falls back to raw chunk embedding, logs a warning including org_id, path, and error detail, and does not fail the ingest | Unwanted behavior |
| AC-5 | **When** the enrichment LLM returns an unparseable response (invalid JSON, missing fields), **then** that chunk retains its raw embedding and a warning is logged | Unwanted behavior |
| AC-6 | The ingest endpoint **shall** return the response (status, chunk count, artifact_id) without waiting for enrichment to complete. Enrichment runs as a Procrastinate background task. | Ubiquitous |
| AC-7 | **When** `ENRICHMENT_ENABLED=false` (env var), **then** no LLM calls are made and ingest behaves identically to the current pipeline | State-driven |
| AC-8 | **When** an org has `enrichment_enabled = false` in `knowledge.org_config`, **then** enrichment is skipped for that org only; other orgs are enriched normally | State-driven |
| AC-9 | The enrichment worker **shall** limit concurrent LLM calls to `ENRICHMENT_MAX_CONCURRENT` (default: 5) per ingest operation | Ubiquitous |
| AC-10 | **When** a single-doc upload and a bulk import are both queued, **then** the `enrich-interactive` queue drains before `enrich-bulk` | Event-driven |
| AC-11 | **When** the Procrastinate task fails after retries, **then** the raw vectors remain in Qdrant and a structured error is logged (org_id, artifact_id, error) | Unwanted behavior |
| AC-12 | **When** a query is executed, **then** retrieval uses Qdrant prefetch with RRF fusion across both `vector_chunk` and `vector_questions` named vectors | Event-driven |
| AC-13 | **When** a point does not have `vector_questions` populated, **then** Qdrant automatically excludes it from the `vector_questions` prefetch leg without application-level filtering | Unwanted behavior |
| AC-14 | The collection migration script **shall** create a new collection with both named vectors, re-index all existing documents through the enrichment pipeline, and provide a switchover mechanism | Ubiquitous |
| AC-15 | Existing tests pass; no regression on ingest or retrieve endpoints when enrichment is disabled | Ubiquitous |

---

## Validation approach

### Measuring retrieval quality improvement

1. **Build a test set**: Collect 50-100 real queries from LiteLLM hook logs (queries that users actually asked). For each query, manually identify the correct source document/chunk from the KB.

2. **Baseline measurement**: Run the test queries against the current pipeline (raw embeddings). Record Recall@5 and MRR@5 (Mean Reciprocal Rank at top 5 results).

3. **Enriched measurement (Contextual Retrieval only)**: Re-ingest the same documents with enrichment enabled but `vector_questions` disabled. Run the same test queries. Record Recall@5 and MRR@5.

4. **Enriched measurement (Contextual Retrieval + HyPE)**: Re-ingest with full enrichment including `vector_questions` for depth 0-1 chunks. Run the same test queries. Record Recall@5 and MRR@5.

5. **Compare**: Measure whether HyPE adds measurable improvement over Contextual Retrieval alone. The enrichment is worth keeping if Recall@5 improves by >10% or MRR@5 improves by >0.05. If HyPE does not add value beyond Contextual Retrieval on the klai corpus, disable `vector_questions` population (the architecture doc SS4.2 calibration note applies).

6. **Dutch language validation**: The test set must include Dutch queries against Dutch content. The contextual prefix and questions will be generated in the document's language (Dutch prompt encourages this). Verify that `klai-fast` produces coherent Dutch prefixes and questions.

### Operational monitoring

- Log enrichment success/failure rate per org (structured logging: `enrichment_status`, `org_id`, `chunks_enriched`, `chunks_failed`)
- Track enrichment latency per document (time from Procrastinate task pickup to enriched upsert completion)
- Monitor Procrastinate queue depth for `enrich-interactive` and `enrich-bulk`
- Alert if enrichment failure rate exceeds 20% over a 1-hour window
- Track `vector_questions` population rate (percentage of chunks with both named vectors populated)

---

## Implementation Notes (sync 2026-03-26)

**Implemented as specified. Six post-merge fix commits were needed to stabilise the Procrastinate integration.**

### Wat werkte precies als SPEC beschreven

All core modules landed as designed: `enrichment.py` (single combined LLM call per chunk, Dutch prompt, structured JSON output), `enrichment_tasks.py` (two named queues `enrich-interactive` / `enrich-bulk`), `org_config.py` (TTLCache + PG NOTIFY eviction), named vectors `vector_chunk` + `vector_questions` in Qdrant, Dual-Index Fusion via RRF prefetch, and the one-time `migrate_collection.py` script. The ingest endpoint returns immediately — enrichment is fully non-blocking.

### Pitfall: Procrastinate 2.x API-breuk

The SPEC assumed Procrastinate 1.x patterns. After the feat merged, six fix commits were required:

1. **`d51949b`** — `add libpq5 to Docker image` — psycopg3 requires the native libpq C library; it is not bundled in the python:3.12-slim base image. Container crashed on startup without it.

2. **`cc0d591`** — `use PsycopgConnector for procrastinate 2.x` — Procrastinate 2.x dropped `AiopgConnector` in favour of `PsycopgConnector` (psycopg3). The feat commit used the old connector class.

3. **`50be7df`** — `update to procrastinate 2.x worker API` — Worker startup API changed between 1.x and 2.x. The old `App.run_worker()` call signature no longer exists.

4. **`86cd889`** — `use libpq key=value DSN for procrastinate psycopg3` — `PsycopgConnector` does not accept a standard `postgresql://` URI; it requires a libpq key=value connection string (`host=... dbname=... password=...`).

5. **`0c76d7f`** — `pass kwargs={} to PsycopgConnector pool` — Pool factory signature requires an explicit `kwargs` dict when using `open_async`; omitting it caused a TypeError at worker start.

6. **`cba692c`** — `single-quote libpq password to handle base64 trailing '='` — Passwords generated by `openssl rand -base64` can end with `=`. In a libpq key=value string, the `=` must be inside single quotes or it truncates the value. This silently broke authentication.

7. **`b8433250`** — `replace hand-written procrastinate schema with official 2.x schema` — The hand-written `procrastinate_fetch_job` SQL function in migration 004 had the wrong signature (3 args, `SETOF` return) vs what Procrastinate 2.15.0 expects (1 arg `target_queue_names`, returns a single `procrastinate_jobs` row). Every worker poll failed with `ConnectorException`. Fixed by replacing the entire procrastinate section with the official `schema.sql` from Procrastinate 2.15.0.

**Lesson:** When adding a new PostgreSQL-backed task queue, always pull the official migration SQL from the library's own repository rather than hand-writing the schema. Procrastinate ships `schema.sql` in its package; use it directly.

### Beslissing: document context first-2000-tokens scope bevestigd

D6 (first 2,000 tokens as document context window) was validated during implementation. Mistral Small via `klai-fast` produces consistent Dutch contextual prefixes and relevant questions at this context size. The known limitation for transcripts/email threads (where context is mid-document) is documented in D6 and remains out of scope.

### Collection migration (D8) — v2 nog niet live

The `klai_knowledge_v2` collection and migration script are implemented but the one-time migration has not yet been executed. The application still reads from `klai_knowledge` (single unnamed vector). Switch to enriched retrieval by running `scripts/migrate_collection.py` and setting `QDRANT_COLLECTION=klai_knowledge_v2`. This is an operational step, not a code gap.

### Commits

- `6a2e88a` — feat(knowledge): contextual retrieval + HyPE enrichment (SPEC-KB-005) — original branch commit
- `eb5151e` — feat(knowledge): contextual retrieval + HyPE enrichment (SPEC-KB-005) (#31) — merged to main via PR #31
- `d51949b` — fix: add libpq5 to Docker image
- `cc0d591` — fix: use PsycopgConnector for procrastinate 2.x
- `50be7df` — fix: update to procrastinate 2.x worker API
- `86cd889` — fix: use libpq key=value DSN for procrastinate psycopg3
- `0c76d7f` — fix: pass kwargs={} to PsycopgConnector pool
- `cba692c` — fix: single-quote libpq password to handle base64 trailing '='
- `b843325` — fix(migrations): replace hand-written procrastinate schema with official 2.x schema
