# Finding 4 research: document_text duplication

## Code verification

### Storage location 1 — PostgreSQL `artifacts.extra->>'document_text'`

**CONFIRMED.** `/klai-knowledge-ingest/knowledge_ingest/routes/ingest.py` lines 414–419:

```python
# SPEC-RAG-REBUILD-KB-001 follow-up: persist the document body on
# the artifact row so rebuild_kb can replay against the original
# source instead of reconstructing from Qdrant chunks.
if req.content:
    pg_extra["document_text"] = req.content
```

This is the intentional primary copy, used by `rebuild_tasks.py` (line 242:
`document_text: str | None = extra.get("document_text")`). When absent, rebuild
falls back to reconstructing from Qdrant chunk text (lossy: frontmatter dropped,
overlap boundaries left duplicated). PG is the authoritative store for this field.

### Storage location 2 — Procrastinate `procrastinate_jobs.args`

**CONFIRMED.** `ingest.py` lines 547–551:

```python
await task_fn.configure(...).defer_async(
    org_id=req.org_id,
    ...
    document_text=req.content,   # full body serialised into JSON job args
    ...
    extra_payload=extra_payload, # extra_payload also contains document_text (see below)
)
```

`document_text` is passed explicitly as a top-level job argument AND is also
embedded inside `extra_payload` (which itself is also serialised as a job arg),
resulting in the document body appearing twice in the `procrastinate_jobs.args`
JSONB column per enrichment job row.

`enrichment_tasks.py` consumes `document_text` directly (lines 184, 221, 236,
330) for LLM enrichment and summary generation, and never reads it from Qdrant.

### Storage location 3 — Qdrant payload (per chunk)

**CONFIRMED — and confirmed unused on read.**

`ingest.py` lines 463–472 build `extra_payload`:

```python
extra_payload: dict = {"title": title, "artifact_id": artifact_id}
if req.content:
    extra_payload["document_text"] = req.content   # full body injected here
```

This dict is passed to `qdrant_store.upsert_chunks(extra_payload=extra_payload)`.
Inside `upsert_chunks` (qdrant_store.py lines 173–174):

```python
if extra_payload:
    base_payload.update(extra_payload)   # document_text merges into every point
```

Every chunk of the document therefore receives `document_text` as a payload field.

**Read-side filter excludes it entirely.** `_ALLOWED_METADATA_FIELDS` (qdrant_store.py
lines 363–369) does not include `document_text` or `document_summary`:

```python
_ALLOWED_METADATA_FIELDS = frozenset({
    "title", "kb_slug", "chunk_index", "created_at",
    "source_type", "source_connector_id", "source_ref", "visibility",
    "tags", "provenance_type", "confidence",
    "artifact_id", "content_type", "valid_from", "valid_until", "ingested_at",
    "assertion_mode",
})
```

The `search()` function (lines 443–455) returns:
```python
"metadata": {k: v for k, v in (p.payload or {}).items() if k in _ALLOWED_METADATA_FIELDS}
```

`document_text` is stored in every chunk's Qdrant payload, never returned to any
caller, and is never used for filtering or indexing.

### `document_summary` also stored but filtered out

`enrichment_tasks.py` line 325 adds `document_summary` to `extra_payload` after
LLM generation:
```python
extra_payload["document_summary"] = document_summary_val
```

This flows into `upsert_enriched_chunks(extra_payload=extra_payload)` (line 421),
which calls `base_payload.update(extra_payload)` (qdrant_store.py line 244),
putting `document_summary` on every enriched chunk point. It is also excluded from
`_ALLOWED_METADATA_FIELDS` and never read from Qdrant by any consumer. The
retrieval-api codebase contains zero references to `document_text` or
`document_summary` — confirmed by grep with no output.

### rebuild_kb reads from PG, not Qdrant

**CONFIRMED.** `rebuild_tasks.py` lines 237–255: reads `document_text` from
`artifact["extra"]` (PG JSONB), and only falls back to reconstructing from
Qdrant chunk text when the PG field is absent (legacy artifacts pre-dating the
`document_text` persistence). Qdrant's copy is never consulted by rebuild.


## Current behavior

### Per-document storage cost breakdown

Assumptions for a 100 KB markdown document with 50 chunks, after enrichment:

| Storage location | Copies | Size per copy | Total |
|---|---|---|---|
| PG `artifacts.extra` (JSONB) | 1 | ~100 KB raw + ~30% JSONB overhead = ~130 KB | **~130 KB** |
| `procrastinate_jobs.args` (JSONB) — `document_text` arg | 1 | ~100 KB + JSONB overhead | ~130 KB |
| `procrastinate_jobs.args` (JSONB) — inside `extra_payload` arg | 1 | ~100 KB + JSONB overhead | ~130 KB |
| Qdrant payload (per chunk × 50) | 50 | ~100 KB × 50 | **~5 MB** |
| Qdrant `document_summary` (per enriched chunk × 50) | 50 | ~1–3 KB × 50 | ~50–150 KB |

**Dominant waste: ~5 MB in Qdrant per 100 KB document, 0% of which is ever read.**

The Procrastinate duplication (~260 KB extra) is transient: job rows accumulate
in `procrastinate_jobs` until they are vacuumed away, but each run also holds two
full body copies instead of one (explicit arg + same field inside `extra_payload`).

### Usage summary per copy

| Copy | Who writes | Who reads | Necessary? |
|---|---|---|---|
| PG `artifacts.extra['document_text']` | `ingest.py` | `rebuild_tasks.py` | YES |
| Procrastinate job arg `document_text` | `ingest.py` | `enrichment_tasks.py` | YES (consumed, then discarded) |
| Procrastinate job arg inside `extra_payload['document_text']` | `ingest.py` | No consumer reads it from `extra_payload` | NO — duplicate of above |
| Qdrant payload `document_text` per chunk | `qdrant_store.upsert_chunks/upsert_enriched_chunks` | Nothing, filtered out on read | NO |
| Qdrant payload `document_summary` per chunk | `qdrant_store.upsert_enriched_chunks` (via `extra_payload`) | Nothing, filtered out on read | NO |

### Scale projection

At 100 000 documents averaging 50 KB body with 30 chunks each:

- Qdrant waste from `document_text`: 100 000 × 50 KB × 30 = **~150 GB**
- Qdrant waste from `document_summary` (~2 KB × 30 chunks): **~6 GB**
- Total dead payload in Qdrant: **~156 GB**

This is stored either in RAM (InMemory payload mode) or RocksDB on disk (OnDisk
mode). Either way it is scanned during upsert and occupies real disk blocks — and
for InMemory mode it directly inflates the RAM footprint of the Qdrant process.


## Industry standard (2026)

### Canonical raw-document storage patterns for RAG

**Pattern A — Object storage (S3 / MinIO / Garage) as the source of truth**

Used by: AWS-native RAG stacks, Haystack's `DocumentStore` with blob backend,
LlamaIndex's `SimpleObjectNodeParser` + S3 object index.

The raw document (PDF, markdown, HTML) is stored once in object storage under a
content-addressed key (SHA256 or UUID). The vector database chunk payload holds
only `doc_key` (the pointer), `chunk_text`, `chunk_index`, and lightweight
metadata. When retrieval needs the full document (rebuild, re-chunking), it fetches
the object by key. Cost: ~$0.023/GB/month (S3 Standard) vs Qdrant RAM/SSD.

This is the dominant pattern for documents > 10 KB and for rebuild scenarios
because it decouples storage scaling from vector index scaling.

**Pattern B — RDBMS `text` column as the source of truth**

Used by: pgvector-based stacks (LangChain + pgvector, Supabase AI), production
setups that co-locate embeddings and source text in PostgreSQL.

One `documents` table with `id`, `content TEXT`, `embedding vector(N)`. Retrieval
joins on `document_id` to fetch original text. Latency is a single SQL lookup.
This is already the model klai uses for `artifacts.extra->>'document_text'`.

**Pattern C — Chunk payload stores the chunk text only, not the full document**

This is the universal minimum for all mainstream frameworks:

- **LangChain** `VectorStore.add_documents()`: stores `Document.page_content`
  (chunk text) and `Document.metadata` (source path, page number, etc.) but never
  duplicates the full parent document body onto every child chunk.
- **LlamaIndex** `TextNode`: stores `node.text` (chunk) and `node.metadata`
  (relationships to parent document node, source file). The parent `Document` node
  holds the full text separately from its children.
- **Haystack** `Document`: `content` is the chunk text; `meta["source"]` is a
  pointer. Full document reconstruction is done by the `DocumentStore.get_documents_by_id`
  API, not by concatenating chunk payloads.

In all three frameworks, **the full document body is never stored per-chunk in the
vector payload**. The chunk payload stores only the chunk text and metadata pointers.

### Qdrant payload size guidance

Qdrant has no hard limit on payload size (confirmed in GitHub discussion #3934).
However:

- **InMemory payload mode** (the default): all payload fields are loaded into RAM
  at service startup. Large per-chunk bodies directly inflate the Qdrant process
  memory footprint.
- **OnDisk payload mode** (`on_disk_payload: true`): payload is stored in RocksDB.
  Indexed fields remain in RAM; unindexed fields (like `document_text`) are read
  from disk only on demand. Since `document_text` is never returned by the search
  path, it consumes disk space with zero read benefit.
- Qdrant's own guidance for large text payloads (abstracts, full text) is to use
  `on_disk_payload: true` — not to eliminate the duplication.
- The search function filters results through `_ALLOWED_METADATA_FIELDS` before
  returning, so the unindexed `document_text` blocks are written to storage on
  every upsert and deserialized on every payload merge, but never returned.

### Cost comparison: PG text vs Qdrant payload vs S3

| Store | Cost model | GB-scale cost | Rebuild latency |
|---|---|---|---|
| PostgreSQL JSONB | Server RAM + SSD (self-hosted) | Included in existing infra | Single row lookup, fast |
| Qdrant InMemory payload | Server RAM (expensive) | High: full body loaded in RAM per chunk × N chunks | Never used for rebuild |
| Qdrant OnDisk payload | SSD/HDD via RocksDB | Lower than RAM, but still N-times amplification per doc | Never used for rebuild |
| Garage / MinIO object storage | ~$0.02–0.03/GB/month | Cheapest at scale | Requires HTTP fetch per doc |


## Fix recommendations

Ranked by impact and implementation risk:

### Rank 1 (immediate, zero risk): Strip `document_text` and `document_summary` from Qdrant payload before upsert

**Change:** In `ingest.py`, strip `document_text` from `extra_payload` before
passing it to `qdrant_store.upsert_chunks()`. In `enrichment_tasks.py`, strip
`document_summary` (and `document_text` if present) from `extra_payload` before
calling `qdrant_store.upsert_enriched_chunks()`.

```python
# Before passing to qdrant_store:
_QDRANT_STRIP_KEYS = {"document_text", "document_summary", "document_language"}
qdrant_payload = {k: v for k, v in extra_payload.items() if k not in _QDRANT_STRIP_KEYS}
await qdrant_store.upsert_chunks(..., extra_payload=qdrant_payload)
```

**Impact:** Eliminates ~5 MB waste per 100 KB doc in Qdrant. At 100 k docs this
reclaims ~150 GB of Qdrant storage (or RAM in InMemory mode). No rebuild path is
affected (rebuild reads from PG). No retrieval path is affected (`_ALLOWED_METADATA_FIELDS`
already excluded these fields). The enrichment task continues to receive both fields
via explicit function arguments, unaffected.

**Risk:** Very low. The fields are already invisible to all read consumers. The
only risk is a test that asserts these keys are present in Qdrant points — any
such test is testing dead weight and should be updated.

### Rank 2 (medium effort): Remove `document_text` from `extra_payload` before Procrastinate enqueue (eliminate intra-job duplication)

`extra_payload` is serialised as a separate job argument alongside the explicit
`document_text` arg. The `document_text` key inside `extra_payload` is never
consumed by `enrichment_tasks._run_enrichment()` — it reads the top-level arg.
Removing it from `extra_payload` before `defer_async` eliminates one full copy
from `procrastinate_jobs.args`.

```python
proc_payload = {k: v for k, v in extra_payload.items() if k != "document_text"}
await task_fn.configure(...).defer_async(document_text=req.content, extra_payload=proc_payload, ...)
```

**Risk:** Low. Verify no enrichment consumer reads `document_text` from
`extra_payload` (currently confirmed: all consumers take it as an explicit param).

### Rank 3 (long-term, optional): Move PG `artifacts.extra['document_text']` to object storage (Garage/MinIO)

**Context:** The PG JSONB copy is the only copy that serves a real purpose
(rebuild). At scale, large JSONB values in `artifacts.extra` can bloat the
PostgreSQL relation and slow full-row reads and vacuums.

**Alternative:** Store the raw document body in the existing Garage S3 cluster
under a content-addressed key (`{org_id}/{content_hash}.raw`). Store only the key
in `artifacts.extra['raw_doc_key']`. `rebuild_tasks.py` fetches the object when
needed.

**When this becomes worthwhile:** When `artifacts.extra` row sizes routinely
exceed ~100 KB or PostgreSQL table bloat from large JSONB values becomes
measurable. At current document volumes (< 500 K artifacts) the PG copy is not
the bottleneck and the object-store migration adds operational complexity (Garage
availability dependency for rebuild, presigned URL lifetime management). Defer
until PG bloat is confirmed by `pg_relation_size('knowledge.artifacts')`.


## Risk assessment

### Current risk level: Medium, trending High

The Qdrant waste is passive today — it does not corrupt data or affect retrieval
quality. The risk escalates with document volume:

| Document count | Avg body 50 KB | 50 chunks/doc | Qdrant dead payload |
|---|---|---|---|
| 10 000 | 50 KB | 50 | ~25 GB |
| 100 000 | 50 KB | 50 | ~250 GB |
| 1 000 000 | 50 KB | 50 | ~2.5 TB |

**InMemory payload mode** (default Qdrant): this waste lives in RAM. A Qdrant node
with 32 GB RAM and 100 K docs at 50 KB average would be spending ~250 GB RAM
capacity on dead payload — impossible without massive vertical scaling. This alone
would force `on_disk_payload: true`.

**OnDisk payload mode**: the waste is on SSD/HDD (cheaper), but each upsert still
serialises and deserialises the full body payload JSON. For a 100-chunk doc, a
re-enrichment run writes 100 × 100 KB = 10 MB to RocksDB for fields that will
never be read. This adds measurable I/O latency to every enrichment cycle.

**Secondary risk:** the `_ALLOWED_METADATA_FIELDS` allowlist is a read-side guard,
not a write-side guard. Any future code that calls `qdrant_store.search()` and
bypasses the allowlist filter (e.g., `client.retrieve()` called directly, or
`fetch_chunks_by_urls` if it has a different filter) would expose raw document
bodies in API responses. Removing the fields at write time eliminates this
latent exposure class permanently.

**Trigger for Rank 1 fix:** immediately — no scale threshold required. The fix is
zero-risk and the benefit is a direct function of current document volume.

**Trigger for Rank 3 fix:** when `SELECT pg_relation_size('knowledge.artifacts') / 1024 / 1024`
exceeds ~5 GB or when average `extra` JSONB column size exceeds 200 KB (query:
`SELECT avg(octet_length(extra::text)) FROM knowledge.artifacts`).


## References

- [Qdrant payload size discussion (no hard limit, on_disk recommendation)](https://github.com/orgs/qdrant/discussions/3934)
- [Qdrant Storage documentation (InMemory vs OnDisk payload)](https://qdrant.tech/documentation/manage-data/storage/)
- [Qdrant Capacity Planning](https://qdrant.tech/documentation/guides/capacity-planning/)
- [LlamaIndex Vector Store documentation (TextNode / Document separation)](https://docs.llamaindex.ai/en/stable/module_guides/storing/vector_stores/)
- [AWS RAG with S3 (object storage as raw document store)](https://aws.amazon.com/blogs/storage/building-self-managed-rag-applications-with-amazon-eks-and-amazon-s3-vectors/)
- [Production RAG storage best practices 2026](https://lushbinary.com/blog/rag-retrieval-augmented-generation-production-guide/)
- [RAG storage solutions overview 2026](https://fast.io/resources/best-storage-solutions-rag-pipelines/)
