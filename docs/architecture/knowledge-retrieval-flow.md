# Knowledge Retrieval Flow: How Chat with Knowledge Works

> Engineering reference for the full retrieval pipeline — from user preference to LLM context injection.
> Verified against `klai-portal/`, `klai-retrieval-api/`, and `deploy/litellm/` — April 2026 (updated 2026-04-16).
>
> For how knowledge is *stored* (ingestion, chunking, embedding), see
> [knowledge-ingest-flow.md](knowledge-ingest-flow.md).

---

## The big picture

When a user sends a chat message with knowledge enabled, Klai doesn't just forward the
message to an AI model. Before the model sees a single word, the system quietly finds the
most relevant pieces of knowledge, assembles them into context, and prepends them to the
conversation — all within the same round-trip, invisible to the user.

The key insight is that this happens *inside* LiteLLM, the proxy that sits between
LibreChat and the actual language model. LibreChat sends a standard chat request.
LiteLLM intercepts it, enriches it with knowledge, and only then forwards the enriched
request to the model. The model has no idea this happened — it just receives a richer
conversation and produces a better-grounded answer.

```
User types a message in LibreChat
        │
        ▼
  LibreChat sends POST /v1/chat/completions to LiteLLM
        │
        ▼
  KlaiKnowledgeHook intercepts (before reaching the model)
        │
        ├──▶ Is this message trivial? (greeting, "ok", "thanks") → skip, pass through
        │
        ├──▶ Fetch user's KB preferences (cached, ~30s propagation lag)
        │
        ├──▶ POST to retrieval-api → returns ranked knowledge chunks
        │         │
        │         ├── Coreference resolution (resolve pronouns)
        │         ├── Generate embeddings (dense + sparse, parallel)
        │         ├── Retrieval gate (is KB retrieval even needed?)
        │         ├── Hybrid vector search in Qdrant (+ optional graph search)
        │         ├── Reranking (cross-encoder scores each chunk against the query)
        │         ├── Quality score boost (feedback signals from Qdrant payload)
        │         └── Return top-K chunks
        │
        ├──▶ Write retrieval log to Redis (fire-and-forget, for feedback correlation)
        │
        ├──▶ Build context block from chunks + inject into system message
        │
        └──▶ Enriched request → language model → streaming answer → user
```

Everything between "user sends message" and "model starts generating" happens in well
under a second on a warm cache. The retrieval step itself typically takes 300–500ms.

---

## Part 1: User preferences — what each setting does

### The KBScopeBar

The knowledge settings bar sits above the LibreChat iframe in the portal. It controls
four things. Each change is saved immediately to the database and propagates to the
retrieval layer within about 30 seconds (the length of the LiteLLM cache TTL).

---

### Setting 1: Knowledge base on/off

**Simple:** The master switch. When off, the AI answers purely from its training
knowledge, without consulting any of your documents.

**Technical:** Stored as `kb_retrieval_enabled` (bool, default `true`) on `portal_users`.
When `false`, the LiteLLM hook exits at the feature-gate check — no retrieval call is
made, no context is injected, the request passes through unmodified.

---

### Setting 2: Personal KB

**Simple:** Your personal notebooks (documents you've created yourself) can be included
alongside your organisation's shared knowledge bases. Toggle this off to search only the
shared company knowledge.

**Technical:** Stored as `kb_personal_enabled` (bool, default `true`) on `portal_users`.
Translated into the retrieval scope:

```python
scope = "both" if kb_personal_enabled else "org"
```

When scope is `"org"`, Qdrant filters by `org_id` only. When scope is `"both"`, the
filter allows chunks where `user_id == current_user` (personal) *or* where the document
is not private (org). Importantly, if you have a KB slug filter active, personal chunks
always bypass it — your own documents are always included, regardless of which org KBs
you've selected.

---

### Setting 3: Select specific knowledge bases

**Simple:** You can limit search to one or more specific knowledge bases instead of
searching everything your organisation has. Useful when you want focused answers — for
example, only from your HR policy documents, not from the entire company wiki.

**Technical:** Stored as `kb_slugs_filter` (PostgreSQL `ARRAY(String(128))`, nullable).
`null` means "all org KBs". An empty array is automatically normalised to `null` by both
the frontend and backend — there is no semantic distinction.

When a filter is active, the retrieval request includes:
```json
{ "kb_slugs": ["hr-policy", "onboarding"] }
```

In Qdrant, this becomes a `MatchAny` filter on the `kb_slug` payload field. Chunks from
other knowledge bases are excluded before scoring begins.

**Validation:** The backend verifies every submitted slug actually belongs to the caller's
organisation. Submitting a slug from another org returns `400 Bad Request` with the list
of invalid slugs.

**Stale slug auto-healing:** If a knowledge base is deleted after a user has it in their
filter, the frontend detects on load that the stored slug no longer exists. It
automatically sends a PATCH to reset the filter to `null` — no user action needed.

---

### Setting 4: Narrow mode ("Only knowledge base")

**Simple:** Normally the AI can combine your documents with its own training knowledge.
Narrow mode turns that off — the AI must answer only from your documents. If the answer
isn't there, it says so explicitly. Good for compliance situations where you want answers
traceable to specific sources.

**Technical:** Stored as `kb_narrow` (bool, default `false`) on `portal_users`. Controls
which header is prepended to the knowledge context block:

**Narrow mode — exact text injected:**
```
[Klai Kennisbank — beantwoord uitsluitend op basis van onderstaande bronnen.
Gebruik geen algemene kennis buiten deze bronnen.
Staat het antwoord er niet in? Zeg dan: 'Ik kan dit niet vinden in de kennisbank.']
```

**Broad mode (default) — exact text injected:**
```
[Klai Kennisbank — gebruik dit als aanvullende context bij je antwoord.
Je mag dit aanvullen met je algemene kennis.]
```

These instructions are written in Dutch and sit at the top of the model's system message,
above any other instructions. The model reads them as a hard constraint on how to use the
provided context.

---

### How preference changes propagate

**Simple:** Changes take effect within about 30 seconds. The tooltip on the narrow mode
checkbox says this explicitly.

**Technical:** Preferences are cached in two layers inside LiteLLM:

| Cache layer | Key | TTL | Purpose |
|---|---|---|---|
| Version pointer | `kb_ver:{org_id}:{user_id}` | 30 seconds | Detects that preferences changed |
| Feature data | `kb_feature:{org_id}:{user_id}:{version}` | 5 minutes | Full preference state for a known version |

Every successful PATCH to `/api/app/account/kb-preference` increments `kb_pref_version`
on the database row. The version pointer uses a 30-second TTL, so within that window,
LiteLLM will re-fetch the version number from the portal and discover it has changed.
Old feature data remains in cache but becomes unreachable — the version it was keyed
against is no longer the current version.

---

## Part 2: From message to chunks — the retrieval pipeline

The retrieval API (`klai-retrieval-api`) is a standalone service that owns the complete
search pipeline. The LiteLLM hook calls it with a query and gets back a ranked list of
text chunks. Everything below happens inside that service.

---

### Step 1: Coreference resolution

**Simple:** Conversation is context-dependent. "What did he say about it?" only makes
sense if you know who "he" is and what "it" refers to. This step rewrites the user's
query to be fully self-contained, so the search engine can find the right documents
without needing to understand the conversation history itself.

**Technical:** The query, combined with the last three conversation turns (six messages),
is sent to `klai-fast` with the following system prompt:

> *You are a coreference resolver. Given a conversation history and the latest user
> query, rewrite the query so it is fully standalone — all pronouns and references
> resolved. Return ONLY the rewritten query, nothing else. Keep the same language as the
> input query. If no rewriting is needed, return the original query unchanged.*

Temperature: `0.0` — deterministic output. Timeout: 3 seconds. On timeout or error, the
original query is used unchanged.

The result is `query_resolved` — this is what gets embedded and searched. The original
query is never used for vector search.

---

### Step 2: Embeddings (dense + sparse, in parallel)

**Simple:** To search by meaning rather than exact words, we convert the query into a
list of numbers that represents its semantic content. We actually generate two such
representations simultaneously — one that captures meaning, one that captures keywords.

**Technical:** Two embedding calls are made in parallel:

**Dense vector** — `POST http://172.18.0.1:7997/v1/embeddings` (service: **TEI**, port 7997 on gpu-01)
Model: `BAAI/bge-m3`. Produces a high-dimensional float vector representing semantic
meaning. Texts with similar meaning end up geometrically close; synonyms and paraphrases
are neighbours.

**Sparse vector** — `POST http://172.18.0.1:8001/embed_sparse_batch` (service: **bge-m3-sparse**, port 8001 on gpu-01)
Produces a sparse vector of (token-index, weight) pairs — effectively a weighted keyword
representation (BM25-style). This captures exact term matches that dense search can miss.

Both are used in the Qdrant search in step 4. If the sparse sidecar is unreachable (5s
timeout), retrieval continues with dense-only search.

---

### Step 3: The retrieval gate

**Simple:** Not every question needs a knowledge base lookup. "How do I write a for-loop
in Python?" has nothing to do with your company documents. The gate detects this and
skips retrieval entirely — saving latency and not polluting the model's context with
irrelevant chunks.

**Technical:** The gate compares the query's dense vector against a set of reference
vectors loaded from `data/gate_reference.jsonl` (queries that are known to need
retrieval). It computes:

```
margin = cosine_similarity(query, top_1_reference) - cosine_similarity(query, top_2_reference)
```

If `margin > 0.1` (configurable via `RETRIEVAL_GATE_THRESHOLD`), the query is too
distinct from any known retrieval-worthy query pattern, and retrieval is bypassed. The
hook receives `retrieval_bypassed: true` and injects nothing into the model's context.

When bypassed, the metadata `gate_bypassed: true` is attached to the request so
downstream hooks can observe the decision.

---

### Step 4: Hybrid search in Qdrant

**Simple:** Qdrant is the database that holds all the knowledge chunks as vectors. We
search it three ways at once: by semantic meaning, by the questions each chunk answers,
and by exact keywords. The results are merged into a single ranked list.

**Technical:** Against the `klai_knowledge` collection, a three-leg prefetch query is
executed:

```
Leg 1: Dense query on "vector_chunk"     — what the chunk says
Leg 2: Dense query on "vector_questions" — what questions this chunk can answer (HyDE)
Leg 3: Sparse query on "vector_sparse"   — keyword overlap
```

Each leg fetches `max(candidates × 4, 20)` candidates (typically 240 with `candidates=60`).
The three result sets are merged via **Reciprocal Rank Fusion**:

```
rrf_score = 1 / (k + rank + 1)    where k = 60
```

Duplicate chunks (same `chunk_id` appearing in multiple legs) have their scores summed.
The merged list is re-sorted by combined score before proceeding.

**Filters applied at query time:**

All scopes:
- `org_id == request.org_id` — tenant isolation, always enforced
- `invalid_at` not set OR `invalid_at > now()` — bi-temporal validity

Scope `"org"` or `"both"`:
- Visibility: `visibility != "private"` OR `user_id == request.user_id` (private documents
  are only visible to their owner, even within the same org)

KB slug filter (when active):
- `kb_slug IN [requested slugs]`
- Exception: when scope is `"both"`, personal chunks (`user_id == request.user_id`)
  bypass the slug filter and are always included

**Parallel graph search (when `graphiti_enabled`):**
FalkorDB/Graphiti runs a graph traversal in parallel with the Qdrant search. It resolves
named entities in the query and traverses relationships to find conceptually connected
chunks. Timeout: 5 seconds. Results are merged with Qdrant results using the same RRF
formula before reranking.

---

### Step 5: Reranking

**Simple:** The vector search finds broadly relevant chunks quickly, but it's not precise
enough on its own. Reranking takes the top 20 candidates and scores each one carefully
against the actual query — comparing the full meaning of both the question and the
document chunk. This step makes a big difference in which chunks end up at the top.

**Technical:** The top 20 candidates (from the merged Qdrant + graph results) are sent
to a cross-encoder model running on **Infinity** (port 7998 on gpu-01, distinct from TEI
at 7997 — see [GPU inference services](knowledge-ingest-flow.md#gpu-inference-services--why-three-separate-services-exist)):

```
POST http://172.18.0.1:7998/v1/rerank
{
  "model": "bge-reranker-v2-m3",
  "query": "<query_resolved>",
  "documents": ["<chunk_text_1>", "<chunk_text_2>", ...],
  "top_n": <top_k>
}
```

The cross-encoder processes (query, document) pairs jointly — unlike embedding models
which encode query and document separately. This gives much more accurate relevance
scores at the cost of being slower. It is only applied to the top 20 candidates, not the
full 60, to stay within latency budget.

Timeout: 30 seconds. On failure, the top-K Qdrant results are returned unranked.

---

### Step 5b: Quality score boost (SPEC-KB-015)

**Simple:** Chunks that users have previously rated helpful get a small ranking boost.
Chunks rated unhelpful get a small penalty. This makes the knowledge base self-improving
over time — popular, useful answers rise; outdated or irrelevant ones sink.

**Technical:** After reranking, `quality_boost()` reads two payload fields from each Qdrant
result:

- `quality_score` — running average of thumbs up/down signals, initialized at `0.5` (neutral)
- `feedback_count` — total number of feedback events on this chunk

```python
boosted_score = rrf_score * (1 + 0.2 * (quality_score - 0.5))
```

The boost is only applied when `feedback_count >= 3` (cold-start guard). Below this
threshold, chunks rank purely on retrieval score. The threshold is 3 rather than the
statistically ideal 5–10 because Klai's per-org user pool is small; see SPEC-KB-015
§Design notes for full rationale.

At maximum signal (quality_score = 1.0 or 0.0), the adjustment is ±10% of the RRF score
— intentionally conservative to avoid letting feedback dominate over semantic relevance.

Results are re-sorted by the boosted score.

**Feedback loop:** After the retrieval-api responds, the LiteLLM hook fires a retrieval
log to `portal-api /internal/v1/retrieval-log` (fire-and-forget). This log is stored in
Redis (1-hour TTL). When the user later clicks 👍 or 👎 on the AI response, LibreChat
forwards the feedback to `portal-api /internal/v1/kb-feedback`, which correlates it with
the retrieval log and updates the Qdrant payload. See
[knowledge-ingest-flow.md — Self-learning feedback loop](knowledge-ingest-flow.md#self-learning-feedback-loop-spec-kb-015)
for the full picture.

---

### Step 5c: Source-aware selection (SPEC-KB-021)

**Simple:** When an org has multiple knowledge bases, Klai ensures that the top-K results
are not monopolized by a single source. This step enforces source diversity while respecting
the user's query intent — if they explicitly mention a source by name, that source gets
priority.

**Technical:** After quality boost, `source_aware_select()` applies two filters in sequence:

**1. Mention and gate detection:**
- If the `query_resolved` contains a substring match (lowercase) of any `kb_slug` longer
  than 3 characters, that source is "mentioned" and gets priority.
- Alternatively, if the router (see below) has selected specific sources, those are
  "selected" and get priority.

**2. Diversity enforcement:**
- If a source is mentioned or selected: allocate all remaining slots to chunks from that
  source(s), sorted by reranker score descending.
- Otherwise ("diversify" mode): greedily select chunks sorted by reranker score, with a
  hard limit of `max_per_source` (default: 2) chunks per `source_label`. When a source hits
  its quota, skip to the next highest-scoring chunk from a different source. If fewer than
  `top_k` results remain after quota enforcement, fill remaining slots with the
  highest-scoring chunks regardless of source (fallback fill).

The `source_label` field (computed during ingestion, see
[knowledge-ingest-flow.md — Source-label and source-aware enrichment](knowledge-ingest-flow.md#step-d5--source-label-and-source-aware-enrichment-spec-kb-021))
is read from each chunk's Qdrant payload.

**Router as a pre-search signal (SPEC-KB-021):**
Before executing `hybrid_search`, if the user has not specified `kb_slugs` (i.e., they
are not filtering manually) and the org has ≥ 4 knowledge bases, a three-layer router
is invoked:

| Layer | Method | Input |
|-------|--------|-------|
| Layer 1 | Keyword gate | Pre-computed `{brand_term → kb_slug}` map from KB name + description |
| Layer 2 | Semantic margin | Cosine similarity between `query_vector` and pre-computed centroids per source |
| Layer 3 | LLM fallback | (Optional) Route via `klai-fast` with 500ms timeout if Layer 1+2 are inconclusive |

The router's decision is **not** applied as a hard filter to the Qdrant query. Instead,
it signals which sources *might* be relevant, and is passed to `source_aware_select` as
the `router_selected` parameter. The search still retrieves candidates from all sources;
the router influence is applied in the diversity step, not the search step. This allows
semantic relevance to trump router signal when appropriate.

Router centroids are pre-computed as the mean vector of the top-10 chunk embeddings per
source. They are cached in memory with a TTL (default: 10 minutes) and refreshed on-demand
when the org's KB catalog changes (new KB added, description updated).

**Decision record logging (SPEC-KB-021):**
Every retrieval request logs the following to `RetrieveMetadata` for observability:
- `source_aware_mode`: "mentioned" | "diversify" (which diversity strategy was used)
- `router_layer_used`: "keyword" | "semantic" | "llm" | "skipped" (which layer fired, if any)
- `router_decision`: list of selected `kb_slug` values, or None if no router selection
- `router_margin`: cosine margin value from Layer 2, or None if Layer 2 didn't run
- `quota_applied`: bool (whether source quota affected the final result)
- `quota_per_source_counts`: dict mapping `kb_slug` to count of chunks in final result

These fields enable post-retrieval analysis: which sources does the router recommend vs.
which the diversity algorithm selects vs. which actually end up in the top-K.

The final `top_k` chunks (default: 5) are returned to the LiteLLM hook.

---

### Step 6: Evidence tier scoring (shadow mode)

**Simple:** This is a work-in-progress layer that will eventually sort results by how
*confidently* each chunk makes its claims — a direct assertion beats a vague statement.
For now it runs silently and logs the scores without affecting what gets shown.

**Technical:** Each chunk is classified into an evidence tier: `assertion` > `fact` >
`general`. Scores are attached to `evidence_tier_metadata` on each chunk. The environment
variable `EVIDENCE_SHADOW_MODE` (default: `"true"`) controls whether these scores
re-order the results. When shadow mode is disabled, results are served in a U-shape
pattern (highest-confidence + lowest-confidence chunks first, mid-confidence last) to
give the model the strongest anchors at the boundaries of its context window.

---

## Part 3: From chunks to context — the injection step

Back in the LiteLLM hook, with a list of scored chunks in hand.

---

### Gap detection

Before building context, the hook classifies the retrieval result:

| Gap type | Condition | Consequence |
|---|---|---|
| **Hard gap** | No chunks returned | Gap event fired; no injection |
| **Soft gap** | All `reranker_score < 0.4` (or `dense_score < 0.35` if no reranker) | Gap event fired; injection still happens |
| **Success** | At least one chunk above threshold | Normal injection |

Gap events are sent fire-and-forget to the portal (`POST /internal/v1/gap-events`) for
coverage analytics — to see which questions the knowledge base cannot answer.

---

### Building the context block

The chunks are formatted into a structured context block. The header depends on narrow
mode; the rest is the same:

```
[Klai Kennisbank — gebruik dit als aanvullende context bij je antwoord.
Je mag dit aanvullen met je algemene kennis.]    ← broad mode (default)

### Titel van het document  [org]
Tekst van de eerste chunk.

### Iets uit mijn notebook  [persoonlijk]
Tekst van een persoonlijk chunk.

[Einde kennisbank-context]
```

The `[persoonlijk]` / `[org]` label comes from the `scope` field on each chunk.
If a chunk has no title, the fallback is `Kennisbank`.

---

### Injecting into the system message

The context block is prepended to the model's system message:

- If a system message already exists: the context block is placed *before* it
- If no system message exists: a new one is inserted at position 0 in the messages array

The model receives the enriched conversation and generates an answer grounded in the
injected context.

```
messages sent to the model:
┌─────────────────────────────────────────────────────────────┐
│ system: [Klai Kennisbank — ...]                             │
│         ### Bron 1  [org]                                   │
│         <chunk text>                                        │
│         ...                                                 │
│         [Einde kennisbank-context]                          │
│                                                             │
│         <original system message, if any>                   │
├─────────────────────────────────────────────────────────────│
│ user:   <conversation history turn 1>                       │
│ assistant: <conversation history turn 1>                    │
│ ...                                                         │
├─────────────────────────────────────────────────────────────│
│ user:   <current message>                                   │
└─────────────────────────────────────────────────────────────┘
```

---

## Part 4: What actually changes per user action

This section maps each concrete action in the UI to its effect on the pipeline.

### Turning knowledge off

```
kb_retrieval_enabled = false
→ LiteLLM hook exits at feature gate
→ No retrieval call made
→ Model answers from training knowledge only
```

### Turning personal KB off

```
kb_personal_enabled = false
→ scope = "org" (was "both")
→ Qdrant filter: org_id only, no user_id visibility bypass
→ Personal notebooks invisible to search
→ Your documents never appear in answers
```

### Selecting specific knowledge bases

```
kb_slugs_filter = ["hr-policy"]
→ Retrieval request includes kb_slugs: ["hr-policy"]
→ Qdrant filter: kb_slug IN ["hr-policy"]
→ Chunks from all other KBs excluded before scoring
→ Exception: if personal KB is on, your personal chunks still appear
```

### Deselecting all knowledge bases

```
Frontend sends kb_slugs_filter = []
→ Backend normalises [] to null
→ No slug filter applied
→ All org KBs included (same as no filter)
```

### Turning narrow mode on

```
kb_narrow = true
→ System prompt header changes to:
  "beantwoord uitsluitend op basis van onderstaande bronnen..."
→ Model is instructed to say "Ik kan dit niet vinden in de kennisbank"
  if the answer is not in the retrieved chunks
→ Propagation lag: up to 30 seconds (cache TTL)
```

---

## Part 5: Trivial messages

Not every message triggers retrieval. The hook checks the last user message before
doing anything else:

- **Length < 8 characters** → skip (any very short message)
- **Matches trivial regex** → skip

The exact pattern (case-insensitive):
```
ok, okay, oke, oké, ja, nee, yes, no, bedankt, thanks, thank you,
dank je, dank u, graag, np, prima, goed, good, sure, hmm, ah, oh,
begrepen, understood, clear, got it, doei, bye, hoi, hallo, hello, hi
```

Trailing punctuation and whitespace are ignored. "Ok!" and "Oké." are both trivial.

---

## Reference: configuration values

| Variable | Default | Purpose |
|---|---|---|
| `KNOWLEDGE_RETRIEVE_URL` | (required) | URL of the retrieval API |
| `KNOWLEDGE_RETRIEVE_TOP_K` | `5` | Chunks to inject per request |
| `KNOWLEDGE_RETRIEVE_TIMEOUT` | `3.0` | Retrieval API timeout (seconds) |
| `KLAI_GAP_SOFT_THRESHOLD` | `0.4` | Reranker score below which gap is "soft" |
| `KLAI_GAP_DENSE_THRESHOLD` | `0.35` | Dense score fallback for gap detection |
| `RETRIEVAL_GATE_ENABLED` | `true` | Enable/disable the retrieval gate |
| `RETRIEVAL_GATE_THRESHOLD` | `0.1` | Cosine margin threshold for gate bypass |
| `retrieval_candidates` | `60` | Raw candidates fetched from Qdrant |
| `reranker_candidates` | `20` | Top-N sent to cross-encoder |
| `EVIDENCE_SHADOW_MODE` | `true` | Log evidence tiers without reordering results |
| `graphiti_enabled` | `true` | Include FalkorDB graph search (parallel) |
| `graph_search_timeout` | `5.0` | FalkorDB search timeout (seconds) |
| `coreference_timeout` | `3.0` | Coreference LLM call timeout (seconds) |
| `reranker_timeout` | `30.0` | Cross-encoder timeout (seconds) |
| `coreference_model` | `klai-fast` | Model tier for coreference resolution |
| `synthesis_model` | `klai-primary` | Model tier for answer generation |

---

## Reference: key files

| Component | File | What it does |
|---|---|---|
| KB preferences (model) | `klai-portal/backend/app/models/portal.py` | `PortalUser` model, all five KB fields |
| KB preferences (API) | `klai-portal/backend/app/api/app_account.py` | `GET`/`PATCH /api/app/account/kb-preference` |
| KB feature (internal) | `klai-portal/backend/app/api/internal.py` | `GET /internal/v1/users/{id}/feature/knowledge` |
| KB scope bar (UI) | `klai-portal/frontend/src/routes/app/_components/KBScopeBar.tsx` | The four-toggle preference bar |
| LiteLLM hook | `deploy/litellm/klai_knowledge.py` | `KlaiKnowledgeHook` — intercepts and enriches requests |
| Retrieval pipeline | `klai-retrieval-api/retrieval_api/api/retrieve.py` | The seven-step retrieval pipeline |
| Coreference | `klai-retrieval-api/retrieval_api/services/coreference.py` | Pronoun resolution via `klai-fast` |
| Embeddings | `klai-retrieval-api/retrieval_api/services/tei.py` | Dense + sparse embedding via BGE-M3 |
| Qdrant search | `klai-retrieval-api/retrieval_api/services/search.py` | Hybrid three-leg RRF search |
| Reranker | `klai-retrieval-api/retrieval_api/services/reranker.py` | Cross-encoder reranking via BGE-reranker-v2-m3 |
| Retrieval gate | `klai-retrieval-api/retrieval_api/services/gate.py` | Cosine margin bypass check |
| Config | `klai-retrieval-api/retrieval_api/config.py` | All configurable values and defaults |
