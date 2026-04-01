# Research: Link Graph Signals in RAG Systems

> Pure technical research — system-agnostic. Applicable to any RAG system built on a vector store + knowledge graph.
>
> Date: 2026-04-01

## Assumed input

```
crawled_pages: url, content_hash, raw_markdown
page_links:    from_url, to_url, link_text
```

Pages are chunked into ~500-token segments, embedded (dense + sparse), and stored in a vector store. A knowledge graph (property graph DB) runs in parallel.

---

## Q1: Link Metadata in Vector Store Payloads

### What to store per chunk

**Fields with clear value:**
- `source_url` — essential for deduplication and attribution, universally stored
- `links_to: [url]` (outbound) — for low-cardinality pages (up to ~20 links), store as payload array; enables 1-hop forward expansion via payload filter without a graph DB call
- `incoming_link_count: int` — preferred over a full `linked_from` list; enables authority boosting at negligible payload cost
- `content_hash` — enables idempotent re-ingestion
- `title`, `section_heading`, `crawl_timestamp`

**Avoid in the payload:**
- `linked_from: [url]` as a full array. Every new crawled page that links to this one requires updating all existing chunks of the target — an O(n) write fan-out. Full reverse adjacency belongs in the graph DB where edges are first-class citizens.

### Staleness trade-off

Outbound links (`links_to`) update when page A is recrawled — manageable. Inbound counts require scanning all pages that link to a target on any change — batch refresh on a schedule is the right model, not real-time.

**Key reference:** Lettria production case study (100M+ vectors in Qdrant + Neo4j): vector store carries scalar metadata and provenance fields; graph carries all relational structure.

---

## Q2: Link Expansion at Retrieval Time

### Evidence on hop depth

**HopRAG (arXiv:2502.12442, 2025):** Ablated 1–4 hops across three datasets.
- 4 hops is optimal on their benchmarks
- Retrieval saturates rapidly: by hop 5, average queue size drops to 1.23 vertices
- Gains vs dense retrieval baseline: **+76.78% answer accuracy, +65.07% retrieval F1**
- Uses a "Helpfulness" metric combining textual similarity + traversal arrival frequency to prune over-expanded contexts

**SAGE (arXiv:2602.16964, 2025):** 1-hop expansion only — **+5.7 recall points at k=20** on OTT-QA. Gain is larger at constrained k.

**HippoRAG (NeurIPS 2024):** Does not expand hops explicitly — runs Personalized PageRank over the full graph, which implicitly aggregates multi-hop neighborhoods in a single pass. Outperforms ColBERTv2 by **up to 20%** on 2WikiMultiHopQA.

### When expansion hurts vs. helps

From arXiv:2506.05690 (2025):
- Global GraphRAG: 83.1% Evidence Recall but only 78.8% Context Relevance — expansion retrieves more but with noise; prompt sizes can reach 4×10⁴ tokens (44× standard RAG)
- **Simple factual lookups:** standard vector RAG wins — lower noise, lower cost
- **Multi-hop reasoning and summarization:** graph expansion wins decisively

**Practical hop guidance:**
- 1-hop forward: safe for most corpora, modest recall gain, low noise risk
- 2-hop: appropriate for documentation with deliberate cross-referencing (wikis, manuals)
- 3+ hops: use PPR instead of BFS — BFS without pruning floods context
- Backward expansion: more dangerous than forward; high-authority hub pages attract many inbound links and can dominate retrieved context; cap or downweight using `incoming_link_count`

### Payload filter vs. graph DB traversal speed

**Qdrant payload filter** (1-hop forward, `links_to` stored and indexed): sub-200ms P95 at 100M vectors. No graph DB call needed.

**Graph DB traversal** (Neo4j): 5–30ms in co-located setup, but adds a network round-trip and operational complexity.

**Recommendation:** Store outbound links in the payload for 1-hop forward expansion. Use the graph DB for backward traversal and 2+ hop reasoning — maintaining reverse adjacency lists in a vector store payload is write-expensive.

---

## Q3: Feeding Explicit Links into a Property Graph

### Node type architecture

The dominant pattern (Neo4j, LlamaIndex, Lettria) uses **separate, labeled node types**:
- `Document` nodes — one per crawled page
- `Chunk` nodes — ~500-token segments, `PART_OF` Document, `NEXT`/`PREVIOUS` sequence edges
- `Entity` nodes — LLM-extracted, typed (Person, Organization, Concept...) connected via `MENTIONS` from Chunk nodes

**Do not merge page nodes and entity nodes.** Document/Chunk nodes carry raw text for LLM context injection; Entity nodes are traversal waypoints. Merging conflates retrieval granularity (paragraph) with knowledge granularity (concept).

### Avoiding duplication between explicit links and extracted triples

Use **distinct relationship types**:
- `[:LINKS_TO]` — explicit hyperlinks (Document → Document)
- `[:RELATES_TO {via: "worked_with"}]` — extracted semantic triples (Entity → Entity)

This allows:
- Structural queries using only `[:LINKS_TO]` edges for context expansion
- Semantic queries using extracted triples for entity-level reasoning
- Deduplication by checking edge existence before insertion

### PPR vs. BFS behavior on different edge types

**PPR on explicit link edges:** Follows the information architecture of the corpus — pages frequently linked get high stationary probability, mirroring their authority. Combines naturally with semantic edges in a single PPR pass if they share node space.

**BFS on extracted triples:** Dense, many spurious relations — BFS at 2+ hops explodes into noise. This is why HippoRAG uses PPR rather than BFS even on a purely semantic graph — PPR's damping factor (α) controls expansion radius implicitly.

---

## Q4: Authority Scoring from Link Structure

### Research evidence

**HippoRAG (NeurIPS 2024):** PPR with query entities as personalization seeds, not a static PageRank prior. "Node specificity" modulates seed probability by inverse document frequency — rare entities get higher seed weight. State of the art for multi-hop retrieval.

**HippoRAG 2 (ICML 2025, arXiv:2502.14802):** +7% over SOTA embedding models on associative memory tasks, still PPR-based.

No recent RAG paper uses raw static PageRank as a primary retrieval signal.

### At which stage to apply authority scoring

| Stage | Mechanism | Best for |
|---|---|---|
| Pre-retrieval filter | Filter orphan pages (`incoming_link_count = 0`) | Removing genuinely isolated/low-quality content |
| Score modifier | Add `log(1 + incoming_link_count) × weight` to similarity score | Gentle authority boost without overriding semantic relevance |
| Post-retrieval rerank | Full PPR over graph | Multi-hop queries where authority-flow matters |

### PPR vs. static PageRank

**PPR is strictly better for retrieval:** it is query-seeded, finding authority relative to query entry points, not globally. Research confirms: replacing PPR's personalization vector with uniform distribution causes up to 8.7% drop in Hit@1.

**Static PageRank:** useful only as a corpus-level precomputed quality prior, not as a retrieval signal.

**When in-degree authority hurts:** on factual single-hop lookups where the answer is on a lightly-linked but specific leaf page, authority boost will push hub/index pages above the answer.

**Practical incremental build:**
Start with `log(1 + incoming_link_count)` as a small additive boost (cheap, no graph query). Graduate to query-time PPR when multi-hop recall becomes the bottleneck.

---

## Q5: Anchor Text as a Retrieval Signal

### The IR foundation

Anchor text indexing is a foundational classical IR technique. The core insight: pages often fail to self-describe using the vocabulary their users search with. The IBM homepage famously did not contain the word "computer" — but thousands of linking pages did. Anchor text bridges the vocabulary mismatch between author terminology and user query language. This is the same insight motivating HyDE (hypothetical document embeddings) in modern RAG.

No recent RAG paper directly evaluates `anchor_texts` as a named payload field. The RAG literature has reproduced this concept via HyDE, contextual chunk enrichment (Anthropic), and LightRAG's dual-level retrieval.

### Implementation pattern

During crawl, for each `(from_url, to_url, link_text)` triple:

```
append link_text to anchor_texts[to_url]
```

At index time, for each chunk of `to_url`:

```
augmented_text = chunk_text + " " + " ".join(deduplicated(anchor_texts[to_url]))
sparse_field = augmented_text           # BM25 gains vocabulary coverage
dense_embedding = embed(augmented_text) # or embed separately and pool
payload["anchor_texts"] = anchor_texts[to_url]  # for inspection/debug
```

**Staleness:** Same profile as `incoming_link_count` — depends on linking pages being recrawled. Handle with periodic batch refresh.

---

## Summary: Incremental Build Order

| Signal | Where to store | When to compute | Phase |
|---|---|---|---|
| `links_to: [url]` | Vector store payload (indexed) | At crawl/index time | 1 |
| `incoming_link_count: int` | Vector store payload (indexed) | Batch, after each crawl run | 1 |
| `anchor_texts: [str]` | Payload + embedded in chunk text | At index time from crawl data | 1 |
| Forward expansion (1-hop) | Payload filter query | At retrieval time | 1 |
| Backward expansion | Graph DB traversal | At retrieval time | 2 |
| Explicit `[:LINKS_TO]` edges | Property graph DB | At crawl/index time | 2 |
| Authority boost (in-degree) | Score modifier on retrieval | At retrieval time | 2 |
| PPR (Personalized PageRank) | Graph DB traversal | At retrieval time | 3 |

---

## Key Sources

| Paper / Resource | Year | Relevance |
|---|---|---|
| HippoRAG (arXiv:2405.14831) | NeurIPS 2024 | PPR-based GraphRAG, +20% on 2WikiMultiHopQA |
| HippoRAG 2 (arXiv:2502.14802) | ICML 2025 | +7% over SOTA on associative memory |
| HopRAG (arXiv:2502.12442) | 2025 | Multi-hop ablations, 4-hop optimal, +76% accuracy |
| SAGE (arXiv:2602.16964) | 2025 | Structure-aware 1-hop expansion, +5.7 recall |
| When to Use Graphs in RAG (arXiv:2506.05690) | 2025 | When graphs hurt vs. help, noise analysis |
| Beyond Nearest Neighbors (arXiv:2507.19715) | 2025 | Graph-augmented vector search |
| Lettria case study | 2024 | 100M+ vector Qdrant + Neo4j production |
| Microsoft GraphRAG | 2024 | LexicalGraphConfig, local/global search |
| LightRAG (arXiv:2410.05779) | 2024 | Dual-level retrieval |
| Stanford IR Book (anchor text chapter) | Classic | Foundation for anchor text as IR signal |
