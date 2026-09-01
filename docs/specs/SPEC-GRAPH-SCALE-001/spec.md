---
id: SPEC-GRAPH-SCALE-001
version: "0.1.0"
status: draft (research complete, implementation not started)
created: 2026-08-26
updated: 2026-08-26
author: Claude (Fable), commissioned by Mark Vletter
priority: high
related:
  - SPEC-KB-011 (introduced graphiti-core; the stub-deps lesson in its history is why graphiti imports stay guarded)
  - SPEC-RAG-GRAPH-CITE-002 (episode naming; unaffected by this SPEC, but its rename path is a consumer of the graph this SPEC keeps intact during migration)
  - GetKlai/klai#1148 (canonical-English entity names across knowledge bases — the reason sub-tenant partitioning is a real trade-off, not a free lever)
  - GetKlai/klai#1214 (edge fulltext O(hits × entities) fix in klai-libs/graphiti-compat — precedent for the patch mechanism this SPEC extends)
roadmap: docs/architecture/retrieval-improvements-roadmap.md
---

# HISTORY

| Version | Date       | Author | Change |
|---------|------------|--------|--------|
| 0.1.4   | 2026-09-01 | Claude (Fable) | REQ-5 rolled out to production. `GRAPH_ANN_ENABLED=true` in compose for knowledge-ingest + retrieval-api; `scripts/verify_graph_ann.py` (new) created the vector indexes and recall-gated them per tenant ON THE SERVER against live data — 42 graphs scanned, 19 non-empty verified, all passed the 0.95 mean-recall gate (largest tenant, 30,236 edges: edge recall 0.985 / node 0.997; index 1.7 ms vs 45.6 ms scan server-side). Live in-situ measurement in the running retrieval-api container: the patched edge candidate search returns in **~34 ms median** on the largest tenant vs 616–1,703 ms `graph_search_ms` observed on the scan path the same morning. Empty tenant graphs are skipped by the script; their indexes arrive automatically via `ensure_database_initialized` on first ingest (one harmless once-per-process fallback warning until then). Remaining: REQ-6 re-measurement of the estimator constants once real post-ANN ingest throughput exists, plus the 0.1.3 checklist items. Process note recorded in project memory: the REQ-3 spike's local-Mac copy of production data violated convention — all spike work from now on runs on the server; the local copy was deleted 2026-09-01. |
| 0.1.3   | 2026-09-01 | Claude (Fable) | REQ-4 implemented (Codex/Sol implementation, Claude Opus review, operator verification against a live tenant-graph copy). **Mechanism amendment to REQ-4/AC-4**: implemented as module-level patches of `search_utils.edge_similarity_search`/`node_similarity_search` in `klai-libs/graphiti-compat` (the #1214 precedent), NOT via `driver.search_interface` — setting the interface object reroutes EVERY search function (fulltext, bfs, episode, community) through it, forcing re-dispatch machinery for functions we do not want to change; the module patch replaces exactly the two scans that carry the quadratic cost. AC-4's `search_interface`-consulted pin is replaced by: (a) a rebind-coverage test pinning the three graphiti importers that exist in 0.29.3 (`search.search`, `search_utils`, `node_operations`) — NOT a derived set, so a graphiti bump adding a fourth importer passes CI while that call site silently keeps the scan; deriving the set is on REQ-5's checklist, (b) flag-off byte-identical tests, (c) live verification on a production-graph copy that the patched path executes the `db.idx.vector.*` procedures (performed 2026-09-01; REQ-5's shadow run re-verifies per tenant). Post-review hardening: the fast paths FALL BACK to the original brute-force scan on any query error (a missing/still-building index otherwise silently drops the retrieval graph leg and loses ingest episodes during the index-build window — Opus findings #1/#2), logged once per database. Review leftovers (medium/low), recorded here as REQ-5's pre-rollout checklist rather than fixed: `GRAPH_ANN_ENABLED` has no deploy/compose wiring yet (blocking for any flag flip); the fallback catch is indiscriminate (a timeout also falls back to the very scan being avoided) and unmemoised, and its warning fires once per database per process — add discrimination/re-log/metric before relying on it operationally; the index-CREATION path still propagates unknown errors into every add_episode (only reads have a fallback); eager-import half of the rebind mechanism; node-path test coverage; rebind-coverage test hardcodes three importers; dimension-constant triplication (1024 in three places); efRuntime left at default 10 while production k is 60-80 — REQ-5's shadow run must measure recall at the real k; k≤0 guard; `:Entity` endpoint constraint dropped on the fast path (no such edge exists today). |
| 0.1.2   | 2026-09-01 | Claude (Fable) | REQ-3 spike executed — **verdict: option 1a (FalkorDB native vector index)**. On a local copy of the largest tenant graph (FalkorDB v4.20.3): index top-10 ~1 ms vs 50 ms brute force at 30k edges and vs 172 ms at 100k (flat scaling, 0 empty results); the #1287 empty-results failure mode did NOT reproduce — 0/720 empty under sequential ingest-pattern writes, 22,020 concurrent queries with 0 empty/0 errors against a racing writer; score = cosine distance (no #525 bug), graphiti's own `(2−d)/2` converts it. Qdrant sidecar reference: ANN fast (3 ms) but the fetch-by-uuid hop back into FalkorDB is itself an O(E) scan (181 ms) — 1b demoted to fallback. Full data: `spike-req3.md`. AC-3 satisfied; REQ-4 unblocked. |
| 0.1.1   | 2026-08-26 | Claude (Fable) | REQ-1 + REQ-2 implemented; post-review amendments. (1) The edge ceiling is derived from the scan TAIL, not the average: timeouts were observed from ~22k edges under the 1000 ms cap while the average scan there was ~343 ms, so the ceiling formula gains a `graph_scan_tail_factor` (default 3.0): `0.6 × timeout / (15.6 µs × 3)` ≈ 64k edges — consistent with §1's ~110k-edges-at-5-s wall, where the earlier average-based formula (~192k) was not. (2) REQ-2 landed as `FALKORDB_ARGS="MAX_QUEUED_QUERIES 25 TIMEOUT 5000 RESULTSET_SIZE 10000"` — exactly the runtime-proven setting; `TIMEOUT_DEFAULT`/`TIMEOUT_MAX` were deliberately NOT used because they would newly cap write queries, untested behavior (operator handoff note 2026-08-26: 15/30/60 s were tried and made throughput worse; graph now at ~30k edges steady state). (3) The live-ingest scale-warning hook is fire-and-forget with socket + coroutine deadlines, and treats an empty COUNT result as an error, after a Sol review found the original inline await could block the episode semaphore indefinitely on a half-dead FalkorDB connection. (4) Review leftovers deliberately not fixed: none ≥ high; see PR discussion. |
| 0.1.0   | 2026-08-26 | Claude (Fable), commissioned by Mark Vletter | Initial draft. Research-only: production measurements were taken beforehand on the live Voys graph (provided as input, not re-measured); code paths verified against graphiti-core 0.29.3 source and the klai repositories; external claims verified against FalkorDB docs/source, the getzep/graphiti issue tracker, and the entity-resolution literature. No code changes. |

---

# SPEC-GRAPH-SCALE-001: Knowledge-graph construction cost is quadratic in corpus size

## Summary

Klai builds a per-tenant knowledge graph with graphiti-core 0.29.3 on FalkorDB
v4.20.3. For every episode it ingests, graphiti resolves the newly extracted
entities and edges against the **entire existing tenant graph** using
brute-force cosine scans in Cypher (`vec.cosineDistance()` over every
`RELATES_TO.fact_embedding` and every `Entity.name_embedding`). Per-episode
cost therefore grows linearly with graph size, and total build cost grows
**quadratically with source-text volume**. The largest tenant (6.6M characters
of source text → ~26,400 edges) already takes ~20 hours to rebuild and began
hitting FalkorDB's 1,000 ms query timeout at ~22,000 edges. A tenant with ~2×
the source text could not complete a rebuild inside a weekend; at ~10× the
build would take on the order of **weeks** and fail on timeouts long before
finishing — while today the system would happily start that build and go
silent for days.

This SPEC establishes the scaling law in source-text volume, ranks the levers
with evidence, and specifies the work: (1) a cheap, separable **pre-flight
estimate that refuses infeasible builds loudly**, (2) persisting the FalkorDB
timeout raise that currently reverts on restart, (3) a **spike** deciding
between FalkorDB's native HNSW vector index and Klai's existing Qdrant as the
ANN candidate generator — the native index has a documented correctness risk
under interleaved read/write — and (4) routing graphiti's candidate searches
through that ANN index via graphiti's official `SearchInterface` extension
point, delivered through the existing `klai-libs/graphiti-compat` patch
mechanism. No fork, no framework swap, no per-knowledge-base partitioning.

Everything in this document is labelled **[measured]** (taken on the live
system before this SPEC), **[verified in source]** (read directly from
graphiti-core 0.29.3 / FalkorDB source / this repository),
**[cited]** (published material, linked), or **[judgement]** (our inference —
each one names what would settle it).

## Motivation

### What is measured — the input, not re-derived here

Largest tenant (Voys), Dutch knowledge bases only **[measured]**:

    source text            6,593,861 characters across 726 documents
    average document       9,082 characters
    edges produced         ~17,400 new (~2,600 edges per million characters)
    total graph            ~26,400 edges, ~8,000 entities, ~960 episodes
    full rebuild           ~20 hours, sequential
    throughput             88 documents/hour on a near-empty graph,
                           degrading to 22–34/hour at 26,000 edges

Query timings on the live graph at 18,031 edges **[measured]**:

    pattern match only, no cosine                    200 ms
    cosine over 2,000 edges                          3.2 ms
    full graphiti edge-similarity query              281 ms
    node equivalent (Entity.name_embedding, ~8k)     172 ms

FalkorDB's effective query timeout is 1,000 ms; at ~22,000 edges these queries
began exceeding it and episodes started failing. The timeout was raised to
5,000 ms **at runtime only** — `deploy/docker-compose.yml` has no `command:`
or `FALKORDB_ARGS` for the `falkordb` service, so the raise reverts on every
container restart **[measured; compose verified in source
`deploy/docker-compose.yml:1889`]**. The 1,000 ms cap is not the module
default (which is 0 = unbounded): the official Docker image bakes
`TIMEOUT 1000` into its own `FALKORDB_ARGS`, and that image-level `TIMEOUT`
caps **read queries only** — writes complete while the reads feeding them are
killed, which is exactly the "ingest reports success, resolution quietly
degrades" shape **[cited:
[FalkorDB/FalkorDB#1826](https://github.com/FalkorDB/FalkorDB/issues/1826)]**.

Creating FalkorDB vector indexes on `Entity.name_embedding` and
`RELATES_TO.fact_embedding` made **no measurable difference** and they were
dropped again **[measured]**. This is expected, not a fluke — see "Why the
index changed nothing" below.

### Where the quadratic term lives — the per-episode query census

Read directly from graphiti-core 0.29.3 **[verified in source]**:

- **Node resolution.** `add_episode` → `resolve_extracted_nodes` →
  `_semantic_candidate_search`
  (`graphiti_core/utils/maintenance/node_operations.py:418`): **one**
  brute-force `node_similarity_search` per extracted entity, wanting only the
  top `NODE_DEDUP_CANDIDATE_LIMIT = 15` candidates above cosine 0.6 — but
  computing the cosine against **every** `Entity.name_embedding` in the tenant
  graph to find them.
- **Edge resolution.** `resolve_extracted_edges`
  (`graphiti_core/utils/maintenance/edge_operations.py:392-418`): **two**
  hybrid searches per extracted edge (related-edges + invalidation-candidates,
  both `EDGE_HYBRID_SEARCH_RRF`), each containing a brute-force
  `edge_similarity_search` over **every** `RELATES_TO.fact_embedding`
  (`search_utils.py:291`, the exact query quoted in the problem statement),
  wanting only the top `RELEVANT_SCHEMA_LIMIT = 10`.
- The edge **fulltext** leg of those hybrid searches is served by FalkorDB's
  RediSearch index and is already fixed: the O(hits × entities) re-match
  defect was patched in `klai-libs/graphiti-compat` (140+ s → 2.99 ms,
  GetKlai/klai#1214). The similarity leg is what remains.

So each episode issues on the order of *(entities extracted)* node scans plus
*2 × (edges extracted)* edge scans. With Voys averages (~18 edges and ~10
entities per episode — the ~2,600 edges/Mchar density over ~9 kchar
documents), that is roughly **45–50 full-graph scans per episode**
**[judgement — the extracted-per-episode counts are estimated from aggregate
totals; `graphiti_episode_ingested` logs `entity_count`/`edge_count` per
episode and would give the exact distribution]**.

Per-scan cost from the measured timings: 281 ms at 18,031 edges ≈ **15.6 µs
per edge** (the 200 ms "pattern match only" component is itself a scan of
every edge, so the whole 281 ms scales with edge count, not just the cosine
part); 172 ms over ~8,000 nodes ≈ **21.5 µs per node** **[derived from
measured]**.

### Why the vector index changed nothing

`vec.cosineDistance()` is a plain scalar function in FalkorDB — its own source
documents it as the shared math used both by the Cypher function *and* by
per-result scoring "when materialising KNN results from a vector index", i.e.
they share arithmetic, not an execution path. The **only** way to engage the
HNSW index is the procedure call `db.idx.vector.queryNodes(...)` /
`db.idx.vector.queryRelationships(...)`; there is no planner rewrite that
turns an `ORDER BY vec.cosineDistance(...) LIMIT k` into an index scan
**[cited: [FalkorDB vector-index
docs](https://docs.falkordb.com/cypher/indexing/vector-index.html),
[vec_distance.rs](https://raw.githubusercontent.com/FalkorDB/FalkorDB/main/graph/src/runtime/vec_distance.rs)]**.
graphiti never calls those procedures — `get_vector_cosine_func_query()`
hardcodes the scalar function for every driver, and
`FalkorDriver.build_indices_and_constraints` creates only range and fulltext
indexes **[verified in source]**. Creating an index the queries never touch
is exactly a no-op, as observed.

This is not a FalkorDB-vs-Neo4j gap: the Neo4j path in graphiti `main` is
equally brute-force. Neo4j HNSW support was merged upstream (PR #859,
2025-08), silently removed a week later (PR #894), and the re-proposal
(issue #1793 with 41–45 s → 232–1,456 ms measurements on a 48k-node graph;
draft PR #1796) has had no maintainer response. The equivalent FalkorDB PR
(#1335, claiming ~3,000×) has been open unmerged for five months **[cited:
[#1793](https://github.com/getzep/graphiti/issues/1793),
[#1796](https://github.com/getzep/graphiti/pull/1796),
[#1335](https://github.com/getzep/graphiti/pull/1335)]**. Zep's own paper and
positioning are conversation-memory-shaped — many small per-user graphs, DMR
conversations of ~60 messages — which is why a corpus-scale wall does not
surface in their evaluations **[cited:
[arXiv:2501.13956](https://arxiv.org/abs/2501.13956)]**.

## 1. The scaling law, in source-text volume

Model: total sequential build time `T(C) = a·C + b·C²` for `C` in millions of
characters. The linear term is per-document extraction (LLM calls, embedding,
the configured 10 s inter-episode delay); the quadratic term is the resolution
scans, whose per-episode cost grows with the edge count that earlier documents
created.

Calibration **[derived from measured]**:

- Near-empty throughput 88 docs/h → 41 s/document; at ~110 docs per million
  characters this gives **a ≈ 1.25 h per Mchar**.
- The measured 20 h total at C = 6.6 then fixes **b ≈ 0.27 h per Mchar²**.
- Cross-check from the bottom up: ~46 scans/episode × 15.6 µs/edge at the
  build's average edge count predicts ~6 h of scan time for the Voys rebuild —
  the same order but smaller than the ~12 h the calibrated quadratic term
  implies. The gap (factor ~2–3) is real degradation the pure scan model does
  not capture: timeout-and-retry loops once queries crossed 1,000 ms, and
  LLM dedup prompts that grow with candidate count are the two candidates
  **[judgement — an instrumented probe run (REQ-6) attributes it; the
  calibrated model is anchored to the measured 20 h either way]**.

Projections (sequential, current code, per tenant) **[derived]**:

| Source text | vs Voys | Predicted build | Verdict |
|---|---|---|---|
| 6.6M chars | 1× | ~20 h (calibration point) | painful, done once |
| 10M chars | 1.5× | ~40 h | last size a weekend covers |
| 13M chars | 2× | ~2.7 days | operationally unacceptable |
| 20M chars | 3× | ~5.5 days | infeasible |
| 66M chars | 10× | ~7 weeks | absurd — and it fails first (below) |

The pure-quadratic extrapolation in the problem statement (~10× text ≈ ~100×
time) and this calibrated model (~60×) agree on the order; the difference is
the linear term's share.

**The hard wall arrives before the wall-clock wall.** Query latency is linear
in edge count (~15.6 µs/edge), so at the tail: failures began at ~22,000 edges
under the 1,000 ms cap. Under the runtime-raised 5,000 ms cap the same tail
crosses at roughly 5× the edge count, ~110,000 edges ≈ **28–42M characters**
(the range reflects total-graph density 4,000/Mchar vs new-edge density
2,600/Mchar) **[derived]**. And because the 5,000 ms setting is not
persisted, any FalkorDB container restart snaps the wall back to ~22,000
edges — **fractionally above the largest existing tenant** **[measured +
verified in source]**.

**Threshold to refuse (REQ-1).** Refuse a full build whose predicted time
exceeds an operator budget (default 48 h). Under the current constants that is
**C ≈ 11M characters (~29,000 predicted edges, ~1.7× the largest tenant)**
**[judgement — the 48 h budget is a policy choice; the formula, not the
number, is what the pre-flight encodes, so the threshold moves automatically
when REQ-6 re-measures the constants after the ANN fix]**. Independently,
refuse when the predicted final edge count would push the per-scan tail past
the configured FalkorDB timeout (edge count ≳ 0.6 × timeout / (15.6 µs ×
tail factor 3) ≈ 64k edges at 5 s — the tail factor because failures were
observed at ~22k edges where the *average* scan was only ~343 ms).

## 2. The levers, ranked

**Lever 1 — index-based ANN for candidate search (do this).** Every
brute-force scan exists to find a top-10/15 candidate set: this is
approximate-nearest-neighbour blocking implemented as an exact linear scan.
Replacing the scan with an ANN index removes the quadratic term structurally —
per-episode cost stops depending on graph size. Evidence of magnitude:
graphiti #1793 measured ~500× per query at 48k nodes on Neo4j HNSW; FalkorDB
PR #1335 claims ~3,000×; HNSW query cost is near-O(log N) vs O(N·d)
**[cited]**. Post-fix build time collapses to the linear term, ~1.25 h/Mchar
sequential (66M chars ≈ 83 h sequential — and now parallelizable via the
existing `--concurrency` dial, because the graph engine stops being the
shared bottleneck; the LiteLLM budget becomes the ceiling). Two sub-options —
**the choice is a spike, not an opinion** (REQ-3):

- **1a. FalkorDB native vector index**, queried via
  `db.idx.vector.queryRelationships('RELATES_TO', 'fact_embedding', k, …)` /
  `queryNodes('Entity', 'name_embedding', k, …)`. Simplest topology — no new
  stores, index lives with the data. Two documented risks: (i) a community PR
  doing exactly this (graphiti #1287) was **withdrawn by its own author**
  after measuring 83% empty/wrong HNSW results under interleaved read/write
  (391 of 472 queries empty in a 42-episode build), tied to the still-open
  [FalkorDB#716](https://github.com/FalkorDB/FalkorDB/issues/716) and
  hnswlib's documented lack of search/insert synchronization **[cited]**;
  (ii) FalkorDB vector queries support **no property filtering** — over-fetch
  and post-filter is the only pattern **[cited: docs + discussion #633]**.
  Risk (ii) is mostly moot for Klai: each tenant is its own FalkorDB
  database, so `group_id` is uniform within any query's scope; the one
  filtered search (`SearchFilters(edge_uuids=…)` on the related-edges pass)
  targets edges between one specific node pair and can stay a direct indexed
  `MATCH` instead of ANN. Risk (i) is the spike's whole reason to exist:
  Klai's writes are serialized (`GRAPHITI_MAX_CONCURRENT=1`) but reads and
  writes still interleave within a build, and #1287's failure mode was
  measured on a different version/configuration than v4.20.3.
- **1b. Qdrant as external candidate generator.** Klai already runs Qdrant
  with HNSW at 1024 dimensions for chunk retrieval — proven at exactly the
  vector shape needed (bge-m3), with none of FalkorDB's HNSW concurrency
  history, and Qdrant's published benchmarks cover the closest analog to our
  dimensionality (1M × 1536-dim) **[cited]**. Shape: mirror
  `fact_embedding`/`name_embedding` into per-tenant sidecar collections at
  episode-save time; candidate search = Qdrant top-k → fetch by uuid from
  FalkorDB. Costs: dual-write consistency — **every** delete path in
  `knowledge_ingest/graph.py` (`delete_kb_episodes`,
  `sweep_orphan_episodes_org_wide`, `delete_orphan_episodes_for_artifact_ids`,
  `wipe_org_graph`) must also clean the sidecar, and the connector-delete
  matrix in the knowledge rules gains a row; plus new collections to
  provision and monitor. External support for the pattern: two-stage
  ANN-candidate-then-exact-resolve is the standard shape in the ER literature
  (SC-Block: 99.5% recall at k = 100–200 on 100k–2M record sets, candidate
  sets halved vs competing blockers, brute force "computationally infeasible"
  at scale) **[cited: [arXiv:2303.03132](https://arxiv.org/abs/2303.03132)]**,
  though no published writeup names the exact "vector DB blocks, graph DB
  stores" combination — that gap is real, not a search miss.

**Lever 2 — blocking / candidate narrowing.** In this codebase, lever 2 *is*
lever 1: graphiti already narrows to top-10/15 candidates — the literature's
"blocking is 10× more important than matching" is an argument for making that
narrowing cheap (ANN), not for adding another narrowing stage. The only
independent variant — dropping or scoping the second (invalidation-candidates)
search per edge — halves edge scans at best while changing edge-invalidation
semantics. Not worth the semantic risk for a 2× on a term lever 1 removes
entirely **[judgement]**.

**Lever 3 — pre-flight estimate + refusal.** Makes nothing faster; converts a
multi-day silent failure into an immediate loud one. Cheapest item in this
SPEC, zero dependencies, do it first (REQ-1).

**Lever 4 — batch corpus construction as a separate path.** Upstream
`add_episode_bulk` does **not** avoid the cost: it batches extraction but
still runs `resolve_extracted_nodes`/`resolve_extracted_edges` per episode
against the live graph through the same brute-force searches **[verified in
source]**, returns no episode UUIDs (incompatible with the per-artifact
bookkeeping `backfill.py` depends on), and has open LLM-shape fragility
issues in its dedup path (#882, #879) **[cited]**. A Klai-built batch
pipeline (extract-all fan-out → global resolution → bulk load, the
GraphRAG/LightRAG shape) becomes interesting only if post-lever-1
measurements show resolution still dominating — the external evidence says
extraction, not resolution, dominates once blocking is cheap (58% of GraphRAG
indexing tokens are extraction) **[cited]**. Extraction fan-out itself needs
no new pipeline: `backfill.py --concurrency` already exists and the LLM
budget is the ceiling. Deferred, with the decision gate named (REQ-6).

**Lever 5 — reducing what enters the graph.** Already partially exercised
(navigation-page skip saves ~26 LLM calls per page; meta-fact suppression;
content-hash dedup). Further tightening trims constants, not the exponent.
Worth doing opportunistically, never the answer **[judgement]**.

**Lever 6 — partitioning below the tenant.** Rejected as a primary lever:
Klai just made entity names canonical English (#1148) precisely so entities
join **across** knowledge bases; per-KB partitions would fragment "Device"
into one node per KB and undo that. Also unnecessary if lever 1 lands: ANN
query cost grows logarithmically, so tenant-sized indexes are comfortable at
any projected corpus. Keep in the back pocket only for a pathological tenant,
priced honestly as "the graph stops joining across KBs" **[judgement]**.

## 3. Reaching FalkorDB's vector index from graphiti — concretely

**No fork is needed.** Two mechanisms, both compatible with the existing
runtime-patch precedent:

1. **`driver.search_interface`** — graphiti 0.29.3 ships an official
   extension point
   (`graphiti_core/driver/search_interface/search_interface.py`): every
   search function in `search_utils` consults it first
   (`edge_similarity_search` at `search_utils.py:301`,
   `node_similarity_search` at `:664`, the fulltext variants likewise)
   **[verified in source]**. A `KlaiFalkorSearchInterface` implementing
   `edge_similarity_search` / `node_similarity_search` (and delegating the
   rest) can be assigned to the driver at client construction in
   `_get_graphiti()` — both in knowledge-ingest and retrieval-api.
2. The existing `klai-libs/graphiti-compat` module-patch mechanism, which
   already rewrites `edge_fulltext_search` the same way (#1214).

Option 1 is the primary path (it is API, not monkey-patching); the compat
package is where the implementation lives either way, next to the patches it
extends. The maintainer response on
[#1229](https://github.com/getzep/graphiti/issues/1229) signals upstream
intent to solve this via per-backend driver refactoring, so an upstream
contribution is plausible later but must not be the plan of record — the
FalkorDB HNSW PR has sat unmerged for five months.

**The query change** (edge case; node case is symmetric on
`Entity.name_embedding`):

```cypher
-- today (search_utils.edge_similarity_search, FalkorDB branch):
MATCH (n:Entity)-[e:RELATES_TO]->(m:Entity)
WHERE e.group_id IN $group_ids
WITH DISTINCT e, n, m,
     (2 - vec.cosineDistance(e.fact_embedding, vecf32($search_vector)))/2 AS score
WHERE score > $min_score
ORDER BY score DESC LIMIT $limit

-- proposed:
CALL db.idx.vector.queryRelationships(
    'RELATES_TO', 'fact_embedding', $k, vecf32($search_vector))
YIELD relationship AS e, score
WITH e, startNode(e) AS n, endNode(e) AS m, score
-- score→similarity conversion per the index's similarityFunction;
-- residual SearchFilters applied here as post-filter over the k results
ORDER BY score_converted DESC
LIMIT $limit
```

with `$k` over-fetched relative to `$limit` (e.g. 4×) to absorb post-filter
loss, and the index created per tenant database as

```cypher
CREATE VECTOR INDEX FOR ()-[e:RELATES_TO]->() ON (e.fact_embedding)
OPTIONS {dimension: 1024, similarityFunction: 'cosine'}
CREATE VECTOR INDEX FOR (n:Entity) ON (n.name_embedding)
OPTIONS {dimension: 1024, similarityFunction: 'cosine'}
```

hooked into the compat layer's `ensure_database_initialized` (the per-tenant
init the clone patch already owns) **[cited: FalkorDB docs; verified in
source for the hook point]**. Two things the spike must smoke-test rather
than assume: the `score` semantics of `queryRelationships` on v4.20.3
(distance vs similarity, and the historical always-0 bug
[FalkorDB#525](https://github.com/FalkorDB/FalkorDB/issues/525), closed), and
recall correctness under Klai's ingest pattern (the #1287 failure mode).

If the spike rejects the native index, the identical `SearchInterface` seam
takes the Qdrant implementation (option 1b) — the seam is the deliverable;
the backend is a decision behind it.

## 4. Batch pipeline: not now, and here is the gate

Covered under lever 4. Recommendation **[judgement]**: keep incremental
ingest as the only write path. A separate corpus-construction pipeline
(extraction fan-out → global resolution → bulk load) costs a second write
path with its own dedup semantics, invalidation story, resume/bookkeeping
machinery, and drift risk against live ingest — the repo's own history
(reconciliation SPECs, orphan sweeps) shows what parallel write paths cost.
What it would buy — cheap global resolution — lever 1 buys without a second
path. Revisit only if, after REQ-4 lands, REQ-6's re-measurement shows
resolution still >30% of per-episode wall-clock at the largest tenant.

## 5. Migration for existing tenants

The decisive property: **lever 1 requires no re-extraction and no graph
rebuild**. The embeddings already sit on every edge and node; only the lookup
changes. Retrieval keeps working against the same graph throughout.

Per tenant (largest first, since it proves the point):

1. Create the two vector indexes on the live tenant database (online; ~26k +
   ~8k vectors — the docs' memory formula puts 1M × 1024-dim at roughly 4 GB,
   so tenant-sized indexes are trivial) **[cited: docs formula]**.
2. Deploy the `SearchInterface` implementation behind a feature flag
   (`GRAPH_ANN_ENABLED`, default off), in **both** knowledge-ingest and
   retrieval-api — retrieval-api constructs its own graphiti client and
   applies the compat layer independently (`graph_search.py:31`), so a
   knowledge-ingest-only rollout would leave the read side on brute force
   **[verified in source]**.
3. Shadow-verify: with the flag off, run both paths side by side on a sample
   of live queries per tenant; compare candidate sets (ANN recall vs exact
   top-k) and latencies. Gate: recall ≥ 0.95 on top-10 against the exact
   scan, no empty-result anomalies across a full ingest of ≥ 40 episodes (the
   #1287 failure mode showed within ~5).
4. Flip the flag per tenant; watch `graphiti_episode_ingested.ingest_ms` and
   graph-leg latencies in VictoriaLogs.
5. Rollback is the flag: brute force remains correct (only slow) at every
   existing tenant's size.

Independently and immediately (REQ-2): persist the timeout raise in
`deploy/docker-compose.yml` via `FALKORDB_ARGS`, because until then a
container restart re-arms the 1,000 ms wall just above the largest tenant's
current edge count.

## 6. Pre-flight estimate — refuse loudly, before spending days

Today `backfill.py` starts any build and goes quiet; the only "estimate" is
the operator noticing the rate line decay. Specified in REQ-1; deliberately
separable and first.

## Requirements

### REQ-1 — Pre-flight build estimator with loud refusal

A shared estimator in knowledge-ingest:
`estimate_graph_build(total_chars, current_edge_count) -> {predicted_hours,
predicted_final_edges, refusal: str | None}`, with the model
`T = a·C + b·C²` and constants (`a`, `b`, edges-per-Mchar, per-scan µs/edge,
budget hours, timeout ms) in `config.py` settings — constants are data, not
code, so REQ-6's re-measurement is a config change.

- `backfill.py` MUST call it before processing (it already counts artifacts
  and can sum `length()` of chunk text; corpus chars come from the same
  Qdrant scroll it already performs) and MUST refuse — non-zero exit, one
  unmissable log line naming predicted hours, predicted edges, the threshold,
  and this SPEC id — when `predicted_hours > budget` **or**
  `predicted_final_edges` would push the per-scan tail past the configured
  FalkorDB timeout. `--force` overrides with an explicit
  "operator override" log line.
- The estimator MUST be importable by the live ingest path for a
  warning-level log (not refusal) when a tenant's cumulative corpus crosses
  50% of threshold — connectors grow corpora gradually; the operator should
  hear about the wall before a rebuild is needed.
- Fail loudly, per the repo's prime directive: no silent fallback, no
  best-effort start.

### REQ-2 — Persist the FalkorDB timeout configuration

`deploy/docker-compose.yml` `falkordb` service gains explicit
`FALKORDB_ARGS` carrying the operationally-decided `TIMEOUT`/`TIMEOUT_DEFAULT`
/`TIMEOUT_MAX` values (the current runtime state is 5,000 ms), replacing the
image's baked-in 1,000 ms read cap. The values and their rationale are
documented in the compose comment, including that image-level `TIMEOUT` caps
reads only (FalkorDB#1826). Deployment follows the deploy-compose skill's
boundaries.

### REQ-3 — ANN backend spike (decision gate for REQ-4)

A bounded spike (target: days, not weeks) on a copy of the largest tenant's
graph, producing a written verdict:

- FalkorDB v4.20.3 native path: create both vector indexes; replay ≥ 40
  episodes of real ingest; measure per-query latency, `queryRelationships`
  score semantics, and — the deciding measurement — **recall/emptiness of
  index results interleaved with writes** (the #1287 failure mode: >80%
  empty/wrong under mixed read/write on other versions).
- Qdrant sidecar path: same replay with candidates served from a mirrored
  collection; measure latency including the extra hop, and enumerate the
  delete-path surface that dual-writing adds.
- Verdict picks 1a or 1b with numbers. If both fail (native index incorrect
  AND sidecar latency unacceptable), the SPEC's fallback ordering is: keep
  brute force + REQ-1 refusal as the operating envelope, and escalate the
  FalkorDB correctness finding upstream — that outcome must be reported, not
  papered over.

### REQ-4 — ANN candidate search via SearchInterface

Implement the chosen backend as a `SearchInterface` in
`klai-libs/graphiti-compat`, covering `edge_similarity_search` and
`node_similarity_search` (fulltext stays on the existing patched path;
`edge_uuids`-filtered pair lookups stay as direct indexed MATCH). Wired in
both knowledge-ingest (`_get_graphiti`) and retrieval-api
(`graph_search._get_graphiti`), behind `GRAPH_ANN_ENABLED` (default off
until REQ-5 verifies). Index creation joins the per-tenant
`ensure_database_initialized` path (1a) or collection provisioning joins
tenant setup (1b). Tests pin: the interface is actually consulted (a
graphiti bump that drops the `search_interface` hook must fail CI, in the
spirit of the existing reach-pinning tests), over-fetch + post-filter
semantics, and — for 1b — every delete path in `graph.py` cleaning the
sidecar.

### REQ-5 — Shadow verification and per-tenant rollout

The migration sequence of §5: shadow comparison with recall ≥ 0.95 on top-10
vs the exact scan, ≥ 40-episode ingest without empty-result anomalies, flag
flip per tenant starting with the largest, rollback = flag. No period without
graph retrieval at any point (the graph is never rebuilt or dropped).

### REQ-6 — Re-measure and republish the scaling constants

After REQ-4/5 on the largest tenant: an instrumented probe run measuring
per-episode wall-clock split (LLM vs graph queries vs delay) at three graph
sizes, updating the REQ-1 constants and the refusal threshold, and recording
in this SPEC's HISTORY: the new sustainable corpus ceiling, and the lever-4
gate verdict (resolution share of per-episode time — >30% reopens the batch
pipeline question).

## Acceptance criteria

- **AC-1** (REQ-1) Unit: estimator returns the calibrated values for the
  Voys inputs (6.6M chars → ~20 h ± tolerance); refusal fires above the
  configured budget and above the timeout-derived edge ceiling; `--force`
  logs the override. Integration: `backfill.py` against a fixture corpus
  above threshold exits non-zero before any episode is ingested.
- **AC-2** (REQ-2) The compose file pins `FALKORDB_ARGS`; after a container
  recreate, `GRAPH.CONFIG GET` shows the intended values (verified on the
  server per the deploy runbook, not assumed from the file).
- **AC-3** (REQ-3) The spike report exists with all three measurements
  (latency, score semantics, interleaved-write recall) and a named verdict.
- **AC-4** (REQ-4) With the flag on, `GRAPH.EXPLAIN` of a candidate search
  shows `ProcedureCall | db.idx.vector.queryRelationships` (1a) or the
  Qdrant client is invoked (1b) — and with the flag off, byte-identical
  behaviour to today. CI fails if `driver.search_interface` is no longer
  consulted by the pinned graphiti version.
- **AC-5** (REQ-5) Shadow-run artifact per migrated tenant: recall ≥ 0.95
  top-10, zero empty-result anomalies over ≥ 40 episodes, before/after
  `ingest_ms` distributions.
- **AC-6** (REQ-6) Updated constants land in config with the probe data
  linked from this SPEC's HISTORY; the pre-flight threshold changes
  accordingly without code changes.

## Non-goals

- No separate batch/corpus-construction pipeline (lever 4 — gated on REQ-6's
  measurement, not built here).
- No partitioning below the tenant (lever 6 — conflicts with #1148's
  cross-KB entity joins).
- No graphiti fork and no framework swap (GraphRAG/LightRAG/iText2KG were
  evaluated as patterns, not replacements: none fit a per-tenant FalkorDB +
  incremental-sync stack without adopting their storage layers).
- No change to extraction quality levers (language policy, meta-fact rules,
  skip policy) — they are #1148's domain and orthogonal.
- No upstreaming as the plan of record (a contribution can follow once the
  Klai implementation is proven; upstream responsiveness on this problem
  space is currently near-zero).

## Risks

- **FalkorDB HNSW correctness under interleaved read/write** — the central
  risk of path 1a; documented at 83% failure in another setup, unmeasured on
  v4.20.3 with Klai's serialized writes. Fully absorbed by REQ-3 before any
  production wiring; path 1b exists precisely because this risk may confirm.
- **ANN recall vs exact scan changes resolution behaviour** — a candidate the
  exact scan would have found at rank 14 may be missed, changing an
  entity-merge decision. Bounded by the shadow-run recall gate (≥ 0.95
  top-10) and by the fact that resolution is already LLM-mediated and
  approximate; the 0.6 cosine floor and top-15 cap mean marginal candidates
  were already noise-dominated **[judgement]**.
- **Dual-write drift (1b only)** — sidecar collections diverging from the
  graph on partial failures. Mitigation: the delete-path test matrix in
  REQ-4, plus a periodic count-comparison in the existing orphan-sweep
  machinery.
- **The calibration transfers imperfectly to other tenants** — 2,600
  edges/Mchar is Voys's density; a different corpus type (transcripts,
  contracts) may extract differently. The pre-flight uses the tenant's own
  current edge count where available and the constants are per-config
  adjustable; REQ-6 revisits after the first non-Voys large tenant.
- **Model risk in the threshold** — the 3–5× gap between the bottom-up scan
  model and measured degradation is attributed by hypothesis, not data. The
  threshold is calibrated to the measured 20 h endpoint, so the refusal is
  conservative in the direction that matters (refusing too early costs an
  override; refusing too late costs days).

## Not established — and what would settle each

1. FalkorDB v4.20.3 vector-index correctness under Klai's actual ingest
   pattern — settled by REQ-3's replay.
2. `queryRelationships` score semantics on v4.20.3 (and whether the
   historical always-0 defect is fully gone) — settled by a one-hour smoke
   test inside REQ-3.
3. Exact attribution of the throughput-degradation gap (retries vs LLM prompt
   growth) — settled by REQ-6's instrumented probe.
4. Extracted entities/edges per episode distribution (the census multiplier)
   — settled by aggregating existing `graphiti_episode_ingested` log fields
   in VictoriaLogs; no new instrumentation needed.
5. Published production evidence for LLM KG construction at 10M+ chars with
   disclosed methods and costs — a genuine gap in the public record (Diffbot
   publishes scale without method; GraphRAG publishes cost without ER
   detail); nothing to wait for.
6. Whether upstream will take a vector-index contribution — unknowable from
   outside; the design deliberately does not depend on it.
7. Published HNSW benchmarks at exactly 1024 dims — nearest analogs are
   128–1536-dim results; REQ-3 produces our own numbers at our exact shape.
