# Link-Graph Signals — Applied to Klai Ingest & Retrieval

> Applies findings from `link-graph-rag.md` to the specific stack:
> Qdrant (klai_knowledge) + Graphiti/FalkorDB + klai-retrieval-api + klai-knowledge-ingest.
>
> Prerequisite: SPEC-CRAWL-001 complete (provides `source_url` in artifact `extra`).
> Assumes post-CRAWL-001 tables: `knowledge.crawled_pages`, `knowledge.page_links`.
>
> Date: 2026-04-01

---

## What the stack already does (baseline)

| Signal | Where | How |
|---|---|---|
| Entity PageRank | Qdrant `entity_pagerank_max` payload | `graph.compute_entity_pagerank()` after every episode; stored via `qdrant_store.set_entity_graph_data()` |
| Semantic graph search | Graphiti PPR over entity/relationship triples | `graph_search.search()` in parallel with Qdrant, RRF-merged |
| Hybrid vector search | `klai_knowledge`: 3-leg RRF (vector_chunk + vector_questions + vector_sparse) | `search._search_knowledge()` |
| Contextual enrichment | HyPE questions + context prefix | `enrichment.enrich_chunks()`, stored in payload and embedded as `vector_questions` |

**What the existing entity PageRank is NOT:** it is PageRank over LLM-extracted semantic entities (persons, orgs, concepts), not over the hyperlink graph. The two signals are complementary.

---

## Overlap analysis: what Graphiti already covers vs. what links add

| Need | Graphiti already covers | Link graph adds |
|---|---|---|
| Multi-hop reasoning | Semantic PPR over entity-relation triples | Structural page-to-page traversal (different graph) |
| Authority signal | Hebbian edge weight (reinforcement by re-mention) | Hyperlink in-degree (human editorial signal) |
| Vocabulary coverage | Entity names and relation text | Anchor text (how other pages *describe* this page) |
| 1-hop context expansion | Not applicable — graph returns entity facts, not page chunks | Forward-link expansion returns full page content chunks |

**Conclusion:** No overlap that matters. Graphiti answers "what entities are related to this query". Link expansion answers "what full pages does this relevant page point to". Anchor text answers "what vocabulary do other pages use to describe this page". These are additive signals.

---

## What NOT to build (yet)

**Do not feed `page_links` edges into Graphiti via `add_episode()`.**
- `add_episode()` has no API for injecting explicit edges — we'd need raw Cypher on FalkorDB
- Mixing `[:LINKS_TO]` structural edges with Graphiti's `[:RELATES_TO]` semantic edges in the same graph requires careful node-type design that is non-trivial to retrofit
- The payload-filter approach covers 1-hop forward expansion without touching Graphiti at all
- Revisit as Phase 3 only if multi-hop recall is measurably insufficient after Phase 1-2

**Do not implement PPR over the link graph yet.** The `entity_pagerank_max` field already provides entity-level authority. Link-based in-degree count (`incoming_link_count`) covers the authority signal at negligible cost. Full structural PPR is Phase 3.

**Do not store `linked_from: list[url]` in Qdrant payload.** Every new crawled page that links to a target would require updating all chunks of that target — O(n) write fan-out. Store only `incoming_link_count: int`, batch-refreshed after each crawl run.

---

## Design

### Phase 1 — Ingest-side enrichment (zero retrieval-pipeline change)

**Goal:** Store link signals in Qdrant payload and augment `enriched_text` with anchor text at embed time. Pure ingest changes, retrieval pipeline untouched.

#### 1a. New Qdrant payload fields

In `qdrant_store.upsert_enriched_chunks()`, the `extra_payload` dict is already passed through to all chunk points. Two new fields flow via this mechanism:

```python
# extra_payload additions (set by ingest route/task before calling upsert):
extra_payload["source_url"] = "https://docs.example.com/guide"  # from SPEC-CRAWL-001
extra_payload["links_to"] = ["https://…/page-a", "https://…/page-b"]   # outbound
extra_payload["incoming_link_count"] = 7                                  # inbound count
```

In `qdrant_store.ensure_collection()`, add payload indexes for the two new filterable fields:

```python
for field in (
    "org_id", "kb_slug", "artifact_id", "content_type", "user_id",
    "entity_uuids",
    "source_url",          # NEW — keyword, for forward expansion filter
):
    if field not in indexed_fields:
        await client.create_payload_index(COLLECTION, field_name=field, field_schema="keyword")

for field in ("incoming_link_count",):   # NEW — integer, for authority score filter
    if field not in indexed_fields:
        await client.create_payload_index(COLLECTION, field_name=field, field_schema="integer")
```

**Sync strategy for `incoming_link_count`:**
- Set at first ingest from `page_links` table (count of rows where `to_url = source_url`)
- When a new page is crawled, a background task recomputes counts for all URLs that new page links to
- Update via `client.set_payload()` scoped by `source_url` — same pattern as `set_entity_graph_data()`

New function needed in `qdrant_store.py`:

```python
async def update_link_counts(
    org_id: str,
    kb_slug: str,
    url_to_count: dict[str, int],   # {source_url: incoming_link_count}
) -> None:
    """Batch-update incoming_link_count for changed URLs after a crawl run."""
    client = get_client()
    for url, count in url_to_count.items():
        await client.set_payload(
            COLLECTION,
            payload={"incoming_link_count": count},
            points=Filter(must=[
                FieldCondition(key="org_id", match=MatchValue(value=org_id)),
                FieldCondition(key="kb_slug", match=MatchValue(value=kb_slug)),
                FieldCondition(key="source_url", match=MatchValue(value=url)),
            ]),
        )
```

#### 1b. New module: `link_graph.py`

New file in `klai-knowledge-ingest/knowledge_ingest/link_graph.py`:

```python
"""Link graph queries against knowledge.page_links for ingest-side enrichment."""

async def get_outbound_urls(
    url: str, org_id: str, kb_slug: str, pool
) -> list[str]:
    """Return all URLs that `url` links to."""
    rows = await pool.fetch(
        "SELECT to_url FROM knowledge.page_links "
        "WHERE org_id=$1 AND kb_slug=$2 AND from_url=$3",
        org_id, kb_slug, url,
    )
    return [r["to_url"] for r in rows]


async def get_anchor_texts(
    url: str, org_id: str, kb_slug: str, pool
) -> list[str]:
    """Return anchor text strings from all pages that link to `url`."""
    rows = await pool.fetch(
        "SELECT link_text FROM knowledge.page_links "
        "WHERE org_id=$1 AND kb_slug=$2 AND to_url=$3 AND link_text IS NOT NULL",
        org_id, kb_slug, url,
    )
    return [r["link_text"] for r in rows if r["link_text"].strip()]


async def get_incoming_count(
    url: str, org_id: str, kb_slug: str, pool
) -> int:
    row = await pool.fetchrow(
        "SELECT COUNT(*) AS cnt FROM knowledge.page_links "
        "WHERE org_id=$1 AND kb_slug=$2 AND to_url=$3",
        org_id, kb_slug, url,
    )
    return int(row["cnt"]) if row else 0


async def compute_incoming_counts(
    org_id: str, kb_slug: str, pool
) -> dict[str, int]:
    """Return {url: incoming_link_count} for all pages in a KB."""
    rows = await pool.fetch(
        "SELECT to_url, COUNT(*) AS cnt FROM knowledge.page_links "
        "WHERE org_id=$1 AND kb_slug=$2 GROUP BY to_url",
        org_id, kb_slug,
    )
    return {r["to_url"]: int(r["cnt"]) for r in rows}
```

#### 1c. Anchor text in enrichment

In `enrichment_tasks.py`, `_enrich_document()`, after building `enriched_text`:

```python
# Anchor text augmentation — appended after enriched_text, before embedding.
# Anchor texts describe how other pages refer to this page, bridging vocabulary gaps.
anchor_texts: list[str] = extra_payload.pop("anchor_texts", []) if extra_payload else []
anchor_block = " | ".join(dict.fromkeys(anchor_texts))  # deduplicate, preserve order

for ec in enriched_chunks:
    if anchor_block:
        ec.enriched_text = f"{ec.enriched_text}\n\nAnder pagina's noemen deze pagina: {anchor_block}"
```

This runs BEFORE the embedding step, so the anchor text is baked into `vector_chunk` and `vector_sparse`. It does not change the stored `text` (original) or `context_prefix` — only `text_enriched`, which drives the dense + sparse vectors.

#### 1d. Ingest route: populate link fields before enqueueing

In the crawl/ingest route that fires after a page is ingested (post SPEC-CRAWL-001), before dispatching the enrichment task:

```python
pool = await get_pool()
source_url = extra_payload.get("source_url")
if source_url:
    links_to = await link_graph.get_outbound_urls(source_url, org_id, kb_slug, pool)
    anchor_texts = await link_graph.get_anchor_texts(source_url, org_id, kb_slug, pool)
    incoming_count = await link_graph.get_incoming_count(source_url, org_id, kb_slug, pool)
    extra_payload["links_to"] = links_to
    extra_payload["anchor_texts"] = anchor_texts
    extra_payload["incoming_link_count"] = incoming_count
```

After each crawl run completes (all pages crawled), fire a batch job to refresh `incoming_link_count` for all affected pages:

```python
url_to_count = await link_graph.compute_incoming_counts(org_id, kb_slug, pool)
await qdrant_store.update_link_counts(org_id, kb_slug, url_to_count)
```

---

### Phase 2 — 1-hop forward expansion in retrieval-api

**Goal:** After initial search + RRF merge, fetch chunks from pages that seed chunks link to, then pass the expanded pool to the reranker. The reranker decides what's actually relevant.

#### 2a. Return `source_url` and `links_to` from `_search_knowledge()`

In `search.py`, `_search_knowledge()` result dict — add two new fields:

```python
return [
    {
        "chunk_id": str(r.id),
        "text": r.payload.get("text", ""),
        "score": r.score,
        "artifact_id": r.payload.get("artifact_id"),
        "content_type": r.payload.get("content_type"),
        "context_prefix": r.payload.get("context_prefix"),
        "scope": r.payload.get("scope"),
        "valid_at": r.payload.get("valid_at"),
        "invalid_at": r.payload.get("invalid_at"),
        "ingested_at": r.payload.get("ingested_at"),
        "assertion_mode": r.payload.get("assertion_mode"),
        "entity_pagerank_max": r.payload.get("entity_pagerank_max"),
        "source_url": r.payload.get("source_url"),          # NEW
        "links_to": r.payload.get("links_to", []),          # NEW
        "incoming_link_count": r.payload.get("incoming_link_count", 0),  # NEW
    }
    for r in result.points
]
```

#### 2b. New function: `fetch_chunks_by_urls()` in `search.py`

```python
async def fetch_chunks_by_urls(
    urls: list[str],
    request: RetrieveRequest,
    limit: int,
) -> list[dict]:
    """Fetch chunks whose source_url is in `urls` — for 1-hop forward expansion.

    Uses a payload filter on the indexed source_url field.
    Returns chunk dicts in the same shape as _search_knowledge() results,
    with score=0.0 (expansion chunks are scored by the reranker, not by vector similarity).
    """
    if not urls:
        return []
    client = _get_client()
    scope_conditions = _scope_filter(request)
    query_filter = Filter(must=[
        *scope_conditions,
        FieldCondition(key="source_url", match=MatchAny(any=urls)),
        _invalid_at_filter(),
    ])
    try:
        result = await asyncio.wait_for(
            client.scroll(
                collection_name=settings.qdrant_collection,
                scroll_filter=query_filter,
                limit=limit,
                with_payload=True,
                with_vectors=False,
            ),
            timeout=3.0,
        )
    except Exception as exc:
        logger.warning("link_expand_failed", error=str(exc))
        return []

    points, _ = result
    return [
        {
            "chunk_id": str(p.id),
            "text": p.payload.get("text", ""),
            "score": 0.0,
            "artifact_id": p.payload.get("artifact_id"),
            "content_type": p.payload.get("content_type"),
            "context_prefix": p.payload.get("context_prefix"),
            "scope": p.payload.get("scope"),
            "source_url": p.payload.get("source_url"),
            "links_to": p.payload.get("links_to", []),
            "incoming_link_count": p.payload.get("incoming_link_count", 0),
            "valid_at": None,
            "invalid_at": None,
            "ingested_at": p.payload.get("ingested_at"),
            "assertion_mode": p.payload.get("assertion_mode"),
            "entity_pagerank_max": p.payload.get("entity_pagerank_max"),
        }
        for p in points
    ]
```

#### 2c. Link expansion + authority boost in `retrieve.py`

After the existing RRF merge step (line ~106), before reranking:

```python
# 4b. Link expansion — fetch chunks from pages linked by top seed results
if req.scope != "notebook" and settings.link_expand_enabled:
    seed_chunks = raw_results[:settings.link_expand_seed_k]   # e.g. top 10
    expansion_urls: list[str] = []
    seen_urls: set[str] = set()
    for chunk in seed_chunks:
        for url in chunk.get("links_to", []):
            if url and url not in seen_urls:
                expansion_urls.append(url)
                seen_urls.add(url)

    if expansion_urls:
        t_expand = time.perf_counter()
        expansion_chunks = await search.fetch_chunks_by_urls(
            expansion_urls[:settings.link_expand_max_urls],  # cap, e.g. 30
            req,
            limit=settings.link_expand_candidates,           # e.g. 20
        )
        expand_ms = (time.perf_counter() - t_expand) * 1000
        step_latency_seconds.labels(step="link_expand").observe(expand_ms / 1000)

        if expansion_chunks:
            # Deduplicate: don't add chunks already in raw_results
            existing_ids = {r["chunk_id"] for r in raw_results}
            new_chunks = [c for c in expansion_chunks if c["chunk_id"] not in existing_ids]
            raw_results = raw_results + new_chunks
            logger.debug(
                "link_expand",
                seed_chunks=len(seed_chunks),
                expansion_urls=len(expansion_urls),
                new_chunks=len(new_chunks),
            )

# 4c. Authority score boost from incoming_link_count
# Applied as a small additive modifier BEFORE reranking so the reranker sees boosted scores.
# Weight is configurable (default 0.05 — modest, does not override semantic relevance).
if settings.link_authority_boost > 0:
    import math
    for chunk in raw_results:
        count = chunk.get("incoming_link_count") or 0
        if count > 0:
            chunk["score"] = chunk["score"] + settings.link_authority_boost * math.log1p(count)
```

#### 2d. New settings in `config.py` (retrieval-api)

```python
link_expand_enabled: bool = True
link_expand_seed_k: int = 10          # how many top chunks to extract links from
link_expand_max_urls: int = 30        # cap on outbound URLs to expand
link_expand_candidates: int = 20      # max chunks returned from expansion
link_authority_boost: float = 0.05    # weight for log(1+incoming_link_count) score modifier
```

All four default to safe values: expansion is on but capped, authority boost is small. Set `link_expand_enabled=false` to disable without code change.

---

### Phase 3 — Structural PPR (only if Phase 2 is insufficient)

Only build if RAGAS evaluation after Phase 2 shows multi-hop recall still insufficient.

**What it adds:** FalkorDB `Document` nodes (separate from `Entity` nodes) with `[:LINKS_TO]` edges between them. PageRank over this structural graph gives a different authority signal than entity PageRank. PPR seeded from query-relevant pages propagates authority through the link graph.

**How:**

In `graph.py`, new function (direct FalkorDB Cypher, bypassing `add_episode()`):

```python
async def upsert_page_links(
    org_id: str,
    kb_slug: str,
    links: list[tuple[str, str]],   # [(from_url, to_url), ...]
) -> None:
    graphiti = _get_graphiti()
    driver = graphiti.driver.clone(org_id)
    for from_url, to_url in links:
        await driver.execute_query(
            "MERGE (a:Document {url: $from_url, group_id: $org_id}) "
            "MERGE (b:Document {url: $to_url, group_id: $org_id}) "
            "MERGE (a)-[:LINKS_TO]->(b)",
            from_url=from_url, to_url=to_url, org_id=org_id,
        )
```

Document nodes use a distinct label (`Document`) to avoid collision with `Entity` nodes. Structural PageRank runs over `Document` / `LINKS_TO` independently from entity PageRank.

---

## Files changed

### klai-knowledge-ingest

| File | Change |
|---|---|
| `qdrant_store.py` | `ensure_collection()`: add `source_url` (keyword) + `incoming_link_count` (integer) payload indexes; new `update_link_counts()` function |
| `enrichment_tasks.py` | `_enrich_document()`: read `anchor_texts` from `extra_payload`, append anchor block to `ec.enriched_text` before embedding |
| `link_graph.py` | **NEW** — `get_outbound_urls()`, `get_anchor_texts()`, `get_incoming_count()`, `compute_incoming_counts()` |
| crawl/ingest route | Before dispatching enrichment task: populate `links_to`, `anchor_texts`, `incoming_link_count` in `extra_payload` from `page_links` |

### klai-retrieval-api

| File | Change |
|---|---|
| `services/search.py` | `_search_knowledge()`: add `source_url`, `links_to`, `incoming_link_count` to result dict; new `fetch_chunks_by_urls()` function |
| `api/retrieve.py` | After RRF merge: 1-hop forward expansion + authority score boost; new log fields; metrics step `link_expand` |
| `config.py` | Four new settings: `link_expand_enabled`, `link_expand_seed_k`, `link_expand_max_urls`, `link_expand_candidates`, `link_authority_boost` |

---

## Phased rollout

| Phase | Scope | Risk | Expected gain |
|---|---|---|---|
| **1** | Ingest only — new payload fields + anchor text augmentation | Zero retrieval risk; only affects newly-ingested or re-ingested crawled pages | Improved sparse/BM25 recall for vocabulary-mismatched queries |
| **2** | Retrieval — 1-hop expansion + authority boost | Low — expansion capped at 20 chunks, reranker filters noise; `link_expand_enabled=false` disables instantly | +5-10% recall on multi-section queries (matches SAGE benchmark) |
| **3** | Structural PPR via FalkorDB | Higher — requires FalkorDB schema changes | Only if Phase 2 is insufficient |

**Validation gate between Phase 1 and 2:** Run RAGAS on a crawled KB before and after Phase 1. Check whether recall@20 increases (anchor text signal). If negligible, skip Phase 2.

---

## Open questions before writing the SPEC

1. **`page_links` populated when?** The current SPEC-CRAWL-001 stores `source_url` in `artifact.extra` but does not create `page_links`. Does a subsequent crawl SPEC capture the link graph, or does this SPEC own that table?

2. **Re-ingest existing crawled pages?** Phase 1 fields are only populated for newly ingested pages. Do we backfill existing Qdrant chunks, or only enrich new crawls? Backfill requires a one-time job that queries `page_links` and calls `update_link_counts()` + re-runs enrichment tasks.

3. **`links_to` payload size limit.** Pages with 100+ outbound links generate large payload arrays. Set a cap (e.g., top 20 by... what order?) or store only canonical outbound links (no fragment, same-domain only)?

4. **Anchor text staleness.** When page A is recrawled and its link text to page B changes, do we re-enrich page B's chunks? This requires tracking which target-page chunks need re-embedding when a linking page changes.
