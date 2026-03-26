# SPEC-KB-007: Sparse Vectors en Hybrid Search

> Status: DRAFT (2026-03-26)
> Author: Mark Vletter (design) + Claude (SPEC)
> Builds on: SPEC-KB-005 (named vectors, RRF fusion), SPEC-KB-006 (FlagEmbedding sidecar deployment, content-type profiles)
> Architecture reference: `claude-docs/klai-knowledge-architecture.md` SS4.3
> Created: 2026-03-26

---

## What exists today

After KB-006 is implemented, the `klai_knowledge_v2` collection holds two named vectors per point:

- **`vector_chunk`**: BGE-M3 dense (1024-dim) of the enriched chunk text -- always populated
- **`vector_questions`**: BGE-M3 dense (1024-dim) of aggregated HyPE questions -- conditionally populated per content-type profile

The `bge-m3-sparse` FlagEmbedding sidecar is deployed as a Docker service (port 8001, internal network only, from KB-006 D12). A `sparse_embedder.py` client module exists. However, neither the ingest pipeline nor the retrieval pipeline is wired to the sidecar yet -- KB-006 captured the design decision and deployed the service; the collection schema change, ingest wiring, and retrieval integration are the scope of this SPEC.

Retrieval in `portal/backend/app/api/knowledge.py` issues a two-leg Qdrant prefetch (`vector_chunk` + `vector_questions`) fused via RRF.

**The gap**: dense-only retrieval systematically misses exact lexical matches for terms that appear infrequently in general text:

- Internal product names ("HelloDialog", "Voys CloudPBX")
- Error codes and ticket numbers ("ERR_AUTH_503", "KLAI-4712")
- Employee names, abbreviations, and internal jargon

BGE-M3 dense distributes the meaning of these terms across many dimensions rather than preserving surface form. Sparse retrieval -- which assigns high weights to rare but discriminative tokens -- recovers these cases structurally.

---

## What this SPEC builds

Two additions to the existing pipeline:

1. **Sparse vector at ingest time**: every chunk receives a `vector_sparse` named vector via the FlagEmbedding sidecar. Universal -- no content-type exceptions.

2. **Hybrid retrieval**: query embeds in parallel (dense + sparse), then submits a three-leg Qdrant prefetch fused via RRF.

The Qdrant collection is created fresh after KB-006 with all three vectors in the schema from the start. No migration of existing data -- all current data is test data.

After this SPEC, every Qdrant point carries:

| Named vector | Type | Content | When populated |
|---|---|---|---|
| `vector_chunk` | Dense 1024-dim | BGE-M3 dense of enriched text | Always |
| `vector_questions` | Dense 1024-dim | Aggregated HyPE questions | Conditional (KB-005/KB-006) |
| `vector_sparse` | Sparse ~250k-dim | BGE-M3 sparse token weights | Always (this SPEC) |

---

## Design decisions

### D1: BGE-M3 sparse over BM25

BM25 does not produce a vector -- it is a scoring function over an inverted index. It cannot participate in Qdrant's named-vector prefetch + RRF fusion architecture. Using BM25 alongside dense retrieval would require a separate keyword search leg returning different point IDs, and application-side join logic to merge results. That is infrastructure klai does not have and does not want to build.

BGE-M3 sparse produces a `SparseVector(indices, values)` that Qdrant handles natively via `SparseVectorParams`. It uses the same model weights as the existing TEI dense service -- no new model downloads. The choice is primarily architectural: one unified Qdrant query instead of two systems.

**Infrastructure note**: the FlagEmbedding sidecar is specified in KB-006 D12 but not yet in `deploy/docker-compose.yml`. This SPEC adds the service definition. The model weights are shared with the existing `tei-models` volume -- the only addition is a new container.

**Performance note**: the fundamentals doc (Bevinding 14) shows hybrid dense+sparse outperforms BM25-only by +21% nDCG@10. There is no direct BGE-M3 sparse vs. BM25 benchmark in the research. The decision is architectural, not a performance claim against BM25.

**Decision: BGE-M3 sparse via FlagEmbedding sidecar, with an explicit docker-compose service definition in this SPEC.**

### D2: Sparse vectors are universal -- no content-type exceptions

The vocabulary gap that sparse retrieval addresses exists in all content types:

- KB articles mention product names and ticket numbers
- Meeting transcripts reference participants by exact name
- PDF documents contain model numbers, standards codes, and abbreviations
- Crawled pages reference URLs and domain-specific acronyms

Sparse retrieval adds ~640 bytes per point (~80 non-zero entries × 8 bytes each) and negligible query latency. There is no quality reason to gate it by content type or synthesis depth.

**Decision: populate `vector_sparse` for ALL chunks. No profile-based exception.**

### D3: SparseVectorParams -- on_disk=False

BGE-M3 sparse produces approximately 80 non-zero entries per chunk out of a ~250k vocabulary. Qdrant stores the sparse index as an inverted index in RAM (`on_disk=False`) or on disk (`on_disk=True`).

RAM footprint at `on_disk=False`: N chunks × 80 entries × 8 bytes = N × 640 bytes.

| Corpus size | Sparse index RAM |
|---|---|
| 10k chunks | 6 MB |
| 100k chunks | 64 MB |
| 1M chunks | 640 MB |

core-01 currently has 48 GB RAM with <4 GB used by Qdrant. At 1M chunks, the sparse index uses 640 MB -- within acceptable limits.

Qdrant collection configuration:

```python
sparse_vectors_config={
    "vector_sparse": SparseVectorParams(
        index=SparseIndexParams(on_disk=False)
    )
}
```

`on_disk=True` is available as a runtime fallback if RAM becomes a constraint. The `on_disk` setting is exposed as a configurable setting in `config.py` so it can be changed without code modification.

### D4: Pure RRF now; weighted RRF when signal exists

RRF formula: `score = Σ 1 / (60 + rank_i)` across all prefetch legs. Parameter-free -- requires no training data. Each leg votes via its rank; ties are broken by the RRF constant (60).

Weighted RRF (or convex combination of normalized scores) can yield higher precision once you have ~200+ labeled queries. Without labels, manual weighting risks overfitting to anecdotes and introduces a tuning burden.

**Current state**: the klai corpus has no labeled query evaluation data.

**Decision: ship with pure RRF across three legs.**

`retrieve.py` accepts an optional `sparse_weight: float` parameter (default `None` = pure RRF). When set to a float between 0 and 1, retrieval switches to a weighted convex combination of normalized dense and sparse scores. This allows controlled A/B testing without a further code change.

**Transition criteria for weighted RRF** -- switch when ALL of the following are true:
- ≥200 queries have explicit user quality signal (thumbs-up/down in portal)
- Recall@5 measured per leg on the labeled test set shows one leg materially outperforms the other on identifiable query types
- Accuracy gain justifies the added tuning and maintenance surface

### D5: Collection created fresh with three-vector schema

All current Qdrant data is test data. After KB-006, the collection is dropped and recreated with all three named vectors in the schema from the start:

```python
client.recreate_collection(
    collection_name="klai_knowledge_v2",
    vectors_config={
        "vector_chunk":     VectorParams(size=1024, distance=Distance.COSINE),
        "vector_questions": VectorParams(size=1024, distance=Distance.COSINE),
    },
    sparse_vectors_config={
        "vector_sparse": SparseVectorParams(
            index=SparseIndexParams(on_disk=False)
        )
    }
)
```

No backfill script. No partial upsert logic. No version checks. Every point ingested after this SPEC carries all three vectors from day one.

### D6: Sparse embedding at query time -- parallel with dense, same sidecar

BGE-M3 is a symmetric model: the same weights produce query and document sparse vectors. No separate query encoder.

At query time, `sparse_embedder.embed_sparse(query)` and `embedder.embed_one(query)` are dispatched in parallel via `asyncio.gather`. The dense embed goes to TEI (port 8080); the sparse embed goes to the FlagEmbedding sidecar (port 8001). Both return before the Qdrant query is submitted.

If the sparse sidecar is unavailable at query time, the `vector_sparse` prefetch leg is dropped and retrieval degrades to two-leg RRF (`vector_chunk` + `vector_questions`) with a warning log. The query does not fail.

---

## Changes to `knowledge-ingest`

### Updated: `config.py` -- sparse sidecar settings

```python
# Add to Settings:
sparse_sidecar_url: str = "http://bge-m3-sparse:8001"
sparse_sidecar_timeout: float = 5.0        # fail fast; dense is always the safety net
sparse_sidecar_batch_size: int = 64
sparse_index_on_disk: bool = False         # D3: set True to move sparse index to disk
```

### Updated: `sparse_embedder.py` -- batch embed function

KB-006 defined `embed_sparse(text: str) -> SparseVector`. This SPEC adds the batch variant used at ingest time:

```python
# knowledge_ingest/sparse_embedder.py

from qdrant_client.models import SparseVector

async def embed_sparse(text: str) -> SparseVector | None:
    """Embed a single text. Returns None on sidecar failure."""
    result = await embed_sparse_batch([text])
    return result[0]

async def embed_sparse_batch(texts: list[str]) -> list[SparseVector | None]:
    """
    Embed a list of texts via the FlagEmbedding sidecar.

    Returns list of SparseVector (or None for failed items).
    Sends texts in batches of settings.sparse_sidecar_batch_size.
    Never raises -- sidecar failures produce None entries with a WARNING log.

    Each SparseVector contains:
    - indices: list[int]  -- token IDs with non-zero weight
    - values: list[float] -- corresponding weights
    Typical length: ~80 non-zero entries per chunk.
    """
```

Response format from the FlagEmbedding sidecar (from KB-006 D12):
```json
{"indices": [101, 2038, ...], "values": [0.42, 0.18, ...]}
```

Mapped to `qdrant_client.models.SparseVector(indices=[...], values=[...])`.

### Updated: `enrichment_tasks.py` -- sparse embed alongside dense

In `_enrich_document()`, after computing `chunk_vectors` (dense), call sparse embed in parallel:

```python
# After enrichment, embed dense and sparse in parallel
chunk_vectors, sparse_vectors = await asyncio.gather(
    embedder.embed_batch([ec.enriched_text for ec in enriched_chunks]),
    sparse_embedder.embed_sparse_batch([ec.enriched_text for ec in enriched_chunks]),
)

# question_vectors: existing KB-005/KB-006 logic unchanged
question_vectors = await _embed_questions(enriched_chunks, profile, synthesis_depth)

await qdrant_store.upsert_enriched_chunks(
    ...
    chunk_vectors=chunk_vectors,
    question_vectors=question_vectors,
    sparse_vectors=sparse_vectors,   # NEW
    ...
)
```

Structured log entry added per document:
```python
logger.info(
    "sparse_embed_complete",
    artifact_id=artifact_id,
    chunk_count=len(enriched_chunks),
    sparse_success_count=sum(1 for sv in sparse_vectors if sv is not None),
    sparse_embed_ms=int((time.monotonic() - t0) * 1000),
)
```

### Updated: `qdrant_store.py` -- sparse vector in upsert

```python
async def upsert_enriched_chunks(
    org_id: str,
    kb_slug: str,
    path: str,
    enriched_chunks: list,
    chunk_vectors: list[list[float]],
    question_vectors: list[list[float] | None],
    sparse_vectors: list[SparseVector | None],    # NEW
    artifact_id: str,
    extra_payload: dict | None = None,
    user_id: str | None = None,
) -> None:
```

For each chunk, the Qdrant point's `vectors` dict gains `"vector_sparse": sparse_vectors[i]` when non-None. Points where `sparse_vectors[i]` is None (sidecar unavailable for that item) omit the key -- they are automatically excluded from the sparse prefetch leg at query time via Qdrant's partial population semantics.

### Updated: `retrieve.py` -- three-leg hybrid query

Changes to `portal/backend/app/api/knowledge.py` (or wherever `retrieve.py` lives):

```python
async def search(
    query: str,
    org_id: str,
    kb_slug: str,
    top_k: int = 10,
    content_type_filter: str | None = None,
    sparse_weight: float | None = None,  # None = pure RRF (default, no training data needed)
                                          # float: weighted convex combination -- only activate
                                          # once ≥200 labeled queries exist (see D4).
                                          # There are no good default weights without corpus data.
) -> list[SearchResult]:
    # Embed dense and sparse in parallel
    dense_vec, sparse_vec = await asyncio.gather(
        embedder.embed_one(query),
        sparse_embedder.embed_sparse(query),
    )

    prefetch = [
        Prefetch(query=dense_vec, using="vector_chunk",     limit=20),
        Prefetch(query=dense_vec, using="vector_questions", limit=20),
    ]
    if sparse_vec is not None:
        prefetch.insert(1, Prefetch(query=sparse_vec, using="vector_sparse", limit=20))
    else:
        logger.warning("sparse_sidecar_unavailable_at_query", query_prefix=query[:50])

    results = client.query_points(
        collection_name=settings.qdrant_collection,
        prefetch=prefetch,
        query=FusionQuery(fusion=Fusion.RRF),
        limit=top_k,
        query_filter=_build_filter(org_id, kb_slug, content_type_filter),
    )
    return [SearchResult.from_qdrant(p) for p in results.points]
```

The `sparse_weight` parameter is accepted but unused until weighted RRF is activated (D4). When it becomes active, the implementation replaces `FusionQuery(fusion=Fusion.RRF)` with a score-normalized weighted sum.

### New: PostgreSQL migration `007_knowledge_noop.sql`

No PostgreSQL schema changes in this SPEC. The migration file is a placeholder to maintain the numbered migration sequence:

```sql
-- Migration: 007_knowledge_noop.sql
-- KB-007: Sparse vectors and hybrid search. All changes are in Qdrant and application code.
-- No PostgreSQL schema changes required.
SELECT 1;
```

---

## What is NOT in scope

| Item | Why not now |
|---|---|
| Weighted RRF / convex combination | Requires labeled query evaluation data (D4); deferred until signal exists |
| Sparse vectors for HyPE questions | Questions are matched via `vector_questions` dense; sparse targets exact-match recovery on primary chunk text |
| ColBERT-style late interaction | Higher recall than dual-encoder but requires token-level storage (~100x point size); deferred per cost constraints |
| Sparse-only retrieval mode | No use case identified; hybrid always dominates sparse-only on recall |
| Reranker after hybrid fusion | Separate concern, planned for KB-008 |

---

## Acceptance criteria

| # | Criterion | EARS pattern |
|---|---|---|
| AC-1 | **When** a chunk is ingested (any `content_type`), **then** `vector_sparse` is computed via the FlagEmbedding sidecar and stored as a sparse named vector on the Qdrant point | Event-driven |
| AC-2 | **When** the FlagEmbedding sidecar is unreachable at ingest time, **then** the chunk is upserted with `vector_chunk` (and optionally `vector_questions`) only; a `WARNING` log entry is written; ingest does not fail | Unwanted behavior |
| AC-3 | The `klai_knowledge_v2` collection **shall** be created with `vector_sparse` in its `sparse_vectors_config` (`SparseVectorParams(index=SparseIndexParams(on_disk=False))`) alongside the two dense named vectors | Ubiquitous |
| AC-4 | **When** a retrieval query is executed and the sparse sidecar is available, **then** the system calls dense encoder and sparse sidecar in parallel and submits a three-leg Qdrant prefetch (`vector_chunk`, `vector_sparse`, `vector_questions`) fused via RRF | Event-driven |
| AC-5 | **When** the sparse sidecar is unreachable at query time, **then** retrieval falls back to two-leg RRF (`vector_chunk` + `vector_questions`) with a `WARNING` log; the query does not fail | Unwanted behavior |
| AC-6 | **When** a point has no `vector_sparse` (sidecar-failure during ingest), **then** it is automatically excluded from the `vector_sparse` prefetch leg without application-level filtering | Unwanted behavior |
| AC-7 | The `search()` function **shall** accept an optional `sparse_weight: float` parameter; when `None`, uses pure RRF; the parameter is plumbed through but has no behavioral effect until weighted RRF is activated | Ubiquitous |
| AC-8 | A retrieval query for an exact internal term (product name, ticket number, error code) **shall** return the matching chunk in the top-3 results on the exact-match test set (see Validation approach) | Ubiquitous |
| AC-9 | **When** a chunk is ingested, **then** `sparse_embed_ms`, `chunk_count`, `sparse_success_count`, and `artifact_id` are present in the structured log entry | Ubiquitous |
| AC-10 | The `sparse_index_on_disk` setting **shall** be configurable via environment variable without code change; changing it requires a collection recreation to take effect (documented in config.py) | Ubiquitous |
| AC-11 | Existing tests pass; no regression on ingest or retrieve endpoints when the sparse sidecar is not running (`SPARSE_SIDECAR_URL` set to an unreachable address) | Ubiquitous |

---

## Validation approach

### Exact-match retrieval test

Build a test set of 20 queries containing exact internal terms -- product names ("HelloDialog", "CloudPBX"), known abbreviations, error codes, and employee names used in existing KB articles. For each query, manually identify the expected source chunk.

Measure Recall@3 with:
1. Dense-only (two-leg RRF: `vector_chunk` + `vector_questions`) -- baseline
2. Hybrid (three-leg RRF: + `vector_sparse`) -- this SPEC

Target: exact-match Recall@3 improves by ≥15 percentage points on the jargon test set.

### General retrieval regression test

Reuse the KB-005 test set (50-100 real user queries). Verify Recall@5 and MRR@5 do not degrade after adding the sparse leg. A decrease of more than 2% MRR@5 is a signal to investigate the sparse leg's influence on general queries (consider adding a content-type filter or temporary sparse weight reduction for investigation).

### Operational monitoring

- Sparse sidecar availability rate (target: >99.9% of ingest calls succeed)
- Sparse embed latency at ingest: p95 target < 100ms per batch of 64
- Sparse embed latency at query: p95 target < 50ms (single text)
- Alert if sparse sidecar is unreachable for >5 minutes continuously (retrieval degrades to two-leg)

---

## Beslissingen voor review

Twee punten die voor implementatie besloten moeten worden:

**D4 -- Pure RRF vs weighted RRF: wanneer start je met meten?**
De SPEC kiest pure RRF als startpunt en definieert transitiecriteria (≥200 gelabelde queries). Maar er is nog geen mechanisme in de portal om kwaliteitssignalen te verzamelen (thumbs-up/down per antwoord). Vraag: moet feedback-collectie worden geprioriteerd als prereq voor weighted RRF, en zo ja, hoort dat in KB-008 (reranker) of als een eerder losse SPEC?

**D3 -- Sparse index on_disk bij groei**
De SPEC stelt `on_disk=False` in en maakt het configureerbaar. Het wijzigen ervan vereist een collectie-recreatie (niet triviaal in productie). Bij 500k chunks is de sparse index ~320 MB -- ruim binnen budget. Vraag: is dat voor jou voldoende, of wil je nu al nadenken over een upgrade-pad voor het geval we naar 1M+ gaan?
