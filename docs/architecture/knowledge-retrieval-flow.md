# Knowledge Retrieval Flow: How Chat with Knowledge Works

> Engineering reference for the full retrieval pipeline — from user preference to LLM context injection.
> Verified against `klai-portal/`, `klai-retrieval-api/`, and `deploy/litellm/` — April 2026 (updated 2026-05-06 post retrieval-coupling audit; updated 2026-06-08 post doc-vs-code drift audit).
>
> **2026-06-08 update.** Two large changes landed since 2026-05-06 and are reflected below:
> (1) `deploy/litellm/klai_knowledge.py` was decomposed (commit `dd4225695`, 1614→~1250 lines) into ~15 sibling `klai_kb_*.py` modules + a `klai_llm_safety/` package; this doc references `klai_knowledge.py` as the hook entrypoint and the new modules where a specific responsibility moved. (2) `klai-retrieval-api/retrieve.py` was decomposed (commit `91e8db29b`, 929→759 lines) into `retrieval_api/api/{retrieve,ranking,page_context,...}.py`. A **Strict / Open KB-only answer-policy** layer (6 prompt modes, fail-closed) also shipped — see the new section in Part 1.
>
> Places where this doc describes a *more capable* design than the code currently implements (the retrieval gate, the router LLM fallback) carry an **Intended vs. current** callout pointing to [`product-gaps-backlog.md`](product-gaps-backlog.md).
>
> For how knowledge is *stored* (ingestion, chunking, embedding), see
> [knowledge-ingest-flow.md](knowledge-ingest-flow.md).
>
> For the **strategic roadmap** (how this stack will evolve to be production-grade) and the **RAGAS evaluation harness** that measures every change, see [retrieval-improvements-roadmap.md](retrieval-improvements-roadmap.md).

---

## The big picture

When a user sends a chat message with knowledge enabled, Klai doesn't just forward the
message to an AI model. Before the model sees a single word, the system quietly finds the
most relevant pieces of knowledge, assembles them into context, and prepends them to the
conversation — all within the same round-trip, invisible to the user.

The key insight is that this happens *inside* LiteLLM, the proxy that sits between
the consumer and the actual language model. Three consumer classes enter the same
pipeline with three different credentials:

| Consumer | Entry point | Credential |
|---|---|---|
| **LibreChat tenant** (in-portal) | LibreChat container → LiteLLM | Team key (metadata: `org_id`) |
| **Partner API client** (server-to-server) | `api.getklai.com/partner/v1/chat/completions` → LiteLLM | `pk_live_...` Bearer token (SHA-256 lookup in `partner_api_keys`) |
| **Embedded chat widget** (external website) | `api.getklai.com/partner/v1/chat/completions` → LiteLLM | JWT session token (signed with `WIDGET_JWT_SECRET`, obtained from `/partner/v1/widget-config`) |

All three converge on `KlaiKnowledgeHook`. LiteLLM intercepts the request, enriches it with knowledge, and only then forwards the enriched request to the model.

```
User types a message (LibreChat | Partner API consumer | chat widget on external site)
        │
        ▼
  LiteLLM receives POST /v1/chat/completions (or /partner/v1/chat/completions)
        │
        ▼
  KlaiKnowledgeHook intercepts (before reaching the model)
        │
        ├──▶ Is this message trivial? (greeting, "ok", "thanks") → skip, pass through
        │
        ├──▶ Resolve identity: org_id + kb_ids (+ user_id for LibreChat only)
        │       widget / Partner API → kb_ids from credential; no ChatConfigBar
        │       LibreChat            → KB preferences (cached, ~30s propagation lag)
        │
        ├──▶ Fetch rules (strict guardrails) + templates (response scaffolds) for org/KB
        │
        ├──▶ Multi-KB taxonomy lookup (parallel) — trees + binary coverage map
        │       (Redis-cached at hook layer, single retrieval-api roundtrip,
        │       SPEC-RAG-TAXONOMY-001 multi-KB)
        │
        ├──▶ Combined query rewrite + taxonomy classify (single klai-fast call)
        │       (resolve pronouns + classify into the merged taxonomy nodes
        │       across all in-scope KBs, anti-hallucination guard against
        │       union of valid IDs, SPEC-RAG-QUERY-REWRITE-001 + SPEC-RAG-TAXONOMY-001)
        │
        ├──▶ POST to retrieval-api → returns ranked knowledge chunks
        │         │
        │         ├── Coreference resolution (retrieval-api side, internal pronoun pass)
        │         ├── Generate embeddings (dense + sparse, parallel)
        │         ├── Retrieval gate (is KB retrieval even needed?)
        │         ├── Hybrid vector search in Qdrant (3-leg RRF) + parallel graph search (FalkorDB)
        │         │   (chunks carry document_summary + context_prefix from
        │         │    SPEC-RAG-CONTEXTUAL-001 — Anthropic-pattern contextual retrieval)
        │         ├── Apply taxonomy_node_ids filter (when classifier returned IDs
        │         │   AND in-scope KB has coverage)
        │         ├── Source-aware selection (mentioned / diversify mode, SPEC-KB-021)
        │         ├── Reranking (cross-encoder scores each chunk against the query)
        │         ├── Parent expansion — expand each top-K child to its parent chunk
        │         │   text via parent_chunk_id (SPEC-RAG-PARENT-CHILD-001)
        │         ├── Quality score boost (feedback signals from Qdrant payload)
        │         └── Return top-K chunks (parent text where expansion succeeded)
        │
        ├──▶ Write retrieval log to Redis (fire-and-forget, for feedback correlation)
        │
        ├──▶ Build context block = rules + templates + retrieved chunks
        │       inject into system message
        │       widget: system prompt is grounded-KB-only (no general knowledge)
        │
        └──▶ Enriched request → language model → streaming answer → user
```

Everything between "user sends message" and "model starts generating" happens in well
under a second on a warm cache. The retrieval step itself typically takes 300–500ms.

---

## Identity verification on every `/retrieve` call

retrieval-api authenticates and **verifies the body identity** on every call before
running any pipeline work. Two trust gates are layered:

1. **Service auth** — either `Authorization: Bearer <jwt>` (Zitadel-issued service
   JWT with the `klai:internal:retrieval:query` scope) OR `X-Internal-Secret` matching
   the rotation-bounded shared secret. Every internal-secret caller MUST also send
   `X-Caller-Service: <known-service>`.
2. **Body-vs-claim verification** — the `(claimed_user_id, claimed_org_id)` tuple
   from the request body is verified against portal-api's
   `/internal/identity/verify`. Three branches:

   | Claim shape | Verification |
   |---|---|
   | Real Zitadel user (`claimed_user_id` is a Zitadel sub) | Active membership lookup against `portal_users` |
   | Bearer JWT forwarded | JWT signature + `sub == claimed_user_id` AND `resourceowner == claimed_org_id` |
   | **Partner API** synthetic identity (`claimed_user_id="partner:<key_id>"`) | `partner_api_keys` lookup; key's owning `org_id` must map to `claimed_org_id`. **Restricted to `caller_service="portal-api"`.** Other callers presenting the prefix get `partner_key_not_found`. |

The verified tuple is pinned on `request.state.verified_caller`. `emit_event` for
`knowledge.queried` reads from this pin — never from the body — so a tampered body
cannot poison `product_events` (SPEC-SEC-IDENTITY-ASSERT-001 REQ-6).

The partner-key branch (F2 fix-forward, audit retrieval-coupling-2026-05-06)
intentionally lives in portal-api's `identity_verifier`, not in retrieval-api itself.
An earlier in-process bypass in retrieval-api was removed because it allowed an
attacker holding `X-Internal-Secret` to pin any `(partner:<key>, victim_org)` tuple
without portal verification — collapsing the layered defense for partner traffic.

---

## Part 1: User preferences — what each setting does

### The ChatConfigBar

> Renamed from `KBScopeBar` → `ChatConfigBar`. The component is
> `klai-portal/frontend/src/routes/app/_components/ChatConfigBar.tsx`
> ("Superdock-style config bar above the LibreChat iframe"); there is no
> `KBScopeBar.tsx` in the tree. It is backed by the same
> `/api/app/account/kb-preference` endpoint and the same fields
> (`kb_retrieval_enabled`, `kb_personal_enabled`, `kb_slugs_filter`,
> `kb_narrow`, plus `active_template_ids`).

The knowledge settings bar sits above the LibreChat iframe in the portal. It controls
four things. Each change is saved immediately to the database and propagates to the
retrieval layer within about 30 seconds (the length of the LiteLLM cache TTL).

**The ChatConfigBar applies to LibreChat only.** Partner API keys and embedded chat widgets are scope-locked at credential creation: their `kb_ids` whitelist is stored on `partner_api_keys` (for API keys) or in the JWT payload (for widgets), and cannot be widened at runtime. They also never query personal scope. `kb_retrieval_enabled` is implicitly always `true` for these consumers — they exist specifically to answer from knowledge.

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
which header is prepended to the knowledge context block. The header text is produced by
`kb_chunks_present_header(kb_narrow)` in `deploy/litellm/klai_kb_answer_policy.py` and is
**English-structured and language-neutral** (SPEC-RAG-MULTILINGUAL-CHAT-001) — the model
answers in the user's detected language. The old hardcoded-Dutch strings no longer exist.

**Narrow mode (`kb_narrow=true`) — exact header injected:**
```
[Klai Knowledge Base — answer strictly using only the sources below. Do not use
general knowledge beyond these sources. If the answer is not present, say so plainly
in the user's detected language (e.g. 'I cannot find this in the knowledge base' /
'Dat staat niet in de kennisbank' / 'Das steht nicht in der Wissensdatenbank').]
```

**Broad mode (default, `kb_narrow=false`) — exact header injected:**
```
[Klai Knowledge Base — use this as supplementary context for your answer. You may
complement it with your general knowledge.]
```

The header sits at the top of the model's system message, above any other instructions.
The model reads it as a hard constraint on how to use the provided context.

> **`kb_narrow` is the surface of a deeper answer-policy.** Narrow/broad is no longer
> just a header-text swap. It feeds a Strict/Open answer-policy state machine (6 prompt
> modes) that, in strict mode, never bypasses the retrieval gate, strips web-search tool
> content, and emits a deterministic refusal when there is no citable evidence or chat
> settings are unreachable. See the new **Strict / Open answer policy** section below.

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

### Strict / Open answer policy (6 prompt modes)

Since 2026-05-07 the narrow/broad toggle drives a small answer-policy state machine
rather than only a header swap. The mode is resolved per request and recorded in
`_klai_kb_meta` for the post-call citation guard. Defined in
`deploy/litellm/klai_kb_chat_mode.py` (`ChatRetrievalPromptMode`) and enforced in
`deploy/litellm/klai_kb_answer_policy.py`:

| Mode | When | Behaviour |
|---|---|---|
| `general` | KB retrieval off | No KB context; normal chat |
| `open_kb` | KB on, `kb_narrow=false`, chunks found | Supplementary-context header; model may use general knowledge |
| `strict_kb` | KB on, `kb_narrow=true`, chunks found | Strict header; answer only from sources |
| `strict_no_kb` | `kb_narrow=true`, **no KB selected** | Deterministic notice; no general-knowledge fallback |
| `open_unavailable` | `kb_narrow=false`, chat settings unreachable (cold cache) | Degrades to a stated-uncertainty answer |
| `strict_unavailable` | `kb_narrow=true`, chat settings unreachable | Deterministic refusal — **fail closed** |

The **strict-mode contract** (commits `f45d98eeb`, `318cad715`, `2ba0f5740`,
`947603eea`, `2a1bf209e`):

- The retrieval gate is **never** bypassed in strict mode (`retrieve.py` sets
  `gate_skipped_reason=strict_mode` so a strict request always runs retrieval).
- Web-search tool content is **stripped** from context so the answer cannot leak from
  the web in strict mode.
- When retrieval returns no citable evidence, the hook emits a **deterministic refusal**
  (`kb_zero_chunks_notice`) instead of letting the model answer from general knowledge.
- When chat settings are unreachable (cold cache / portal down), strict mode **refuses
  honestly** (`strict_kb_unavailable_message` / `settings_unavailable_message`) rather
  than silently degrading to open behaviour.

The deterministic refusal builders live in `klai_kb_answer_policy.py`
(`strict_kb_unavailable_message`, `settings_unavailable_message`,
`kb_zero_chunks_notice`).

---

## Part 2: From message to chunks — the retrieval pipeline

The retrieval API (`klai-retrieval-api`) is a standalone service that owns the complete
search pipeline. The LiteLLM hook calls it with a query and gets back a ranked list of
text chunks.

Before the call to `/retrieve`, the hook itself does two things: it **rewrites the query
and classifies it into the multi-KB taxonomy in a single LLM round-trip**. After the
chunks come back, the retrieval API also runs **parent expansion** — replacing each
matched child chunk with its larger parent for better LLM context. The original
"coreference resolution" step inside retrieval-api is still there as an internal pass.

---

### Step 0: Hook-side rewrite + taxonomy classify (single klai-fast call)

**Simple:** Two pieces of preparation happen in the LiteLLM hook *before* it calls
the search engine. First, "What did he say about it?" gets rewritten into a fully
self-contained question. Second, the question gets categorised into one of the customer's
knowledge-base topic tags (when the customer has a curated taxonomy). Both happen in a
single AI call to keep the latency overhead near zero.

**Technical:** The hook fires three small lookups in parallel before retrieval:

1. **Multi-KB taxonomy trees + coverage map.** A single GET to
   `retrieval-api/internal/v1/taxonomy/trees?kb_slugs=a&kb_slugs=b&...` returns
   `{kb_slug: [node, ...]}` for every in-scope KB. A second parallel GET to
   `/internal/v1/taxonomy/coverage` returns `{kb_slug: 0.0|1.0}` — a binary signal
   marking which KBs have a curated taxonomy. Both are Redis-cached at the hook layer
   (TTL 300s, deterministic key sorted on `kb_slugs`) so high-traffic chats don't keep
   re-fetching. Capped at 5 KBs in scope; above that, taxonomy is skipped fail-open.
   See `SPEC-RAG-TAXONOMY-001`.

2. **Combined rewrite + classify.** The hook makes a single `klai-fast` call (Mistral
   Small) with both the conversation history (last 4 turns) AND the merged taxonomy
   trees from KBs that meet the coverage threshold. The model returns:

   ```json
   { "rewritten_query": "<self-contained question>",
     "taxonomy_node_ids": [12, 18] }
   ```

   Anti-hallucination guard: returned IDs are filtered against the *union* of valid IDs
   across all provided KBs (taxonomy node IDs are globally unique on
   `portal_taxonomy_nodes`, so cross-KB collisions are impossible). When no KB has
   coverage, the call falls back to a plain rewrite-only prompt — the classifier path
   simply isn't invoked. See `SPEC-RAG-QUERY-REWRITE-001` (REQ-5: zero added roundtrip)
   and `SPEC-RAG-TAXONOMY-001`.

3. **Filter decision.** The hook then decides whether to attach `taxonomy_node_ids` to
   the `/retrieve` request body. The filter applies iff (a) at least one in-scope KB has
   coverage above `KLAI_TAXONOMY_COVERAGE_THRESHOLD` (default 0.30, i.e. has at least one
   taxonomy node), AND (b) the classifier returned at least one valid node ID. Otherwise
   the filter is skipped fail-open and retrieval runs with the standard org/KB scope
   filter only.

The whole hook-side rewrite + classify path is fail-open: any LLM timeout, any malformed
JSON, any retrieval-api error during the taxonomy lookup logs a warning and falls back to
the raw user query without the taxonomy filter. The chat keeps working — just without the
narrowing.

Tenants that haven't curated their taxonomy yet (Voys-support, today): every query logs
`taxonomy_classify ... skip_reason=all_kbs_low_coverage` and the filter is never applied.
The rewrite path still runs for them.

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

> **Caller-pre-resolved skip.** For the LibreChat path the hook already rewrites the query
> in Step 0 (`klai_kb_query_rewrite`) and passes both the resolved query and the
> pre-rewrite `raw_query` to retrieval-api. retrieval-api **skips** its own coreference
> call when the caller already resolved it (commit `79f23a34c`), so coreference does not
> run twice for chat traffic. It still runs for callers that submit a raw query (e.g. the
> eval harness).

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

> **Intended vs. current ([`GAP-RETR-01`](product-gaps-backlog.md)).** The gate above
> describes the *intended* behaviour. In production it never bypasses anything:
> `gate.should_bypass()` loads its reference vectors from
> `retrieval_api/data/gate_reference.jsonl`, which **does not exist** in the repo (the
> `data/` dir contains only `.gitkeep`) and is never generated at build or deploy. With
> no reference corpus, `should_bypass()` returns `(False, None)` unconditionally —
> `RETRIEVAL_GATE_ENABLED` defaults `true` but the gate is inert, so *every* query
> (trivial or not) runs the full embed + hybrid + rerank pipeline. A generator script
> (`scripts/generate_gate_reference.py`) exists but is manual-only. Closing the gap =
> commit a curated `gate_reference.jsonl` or run the generator at deploy.

---

### Step 4: Hybrid search in Qdrant

**Simple:** Qdrant is the database that holds all the knowledge chunks as vectors. We
search it three ways at once: by semantic meaning, by the questions each chunk answers,
and by exact keywords. The results are merged into a single ranked list.

> Each chunk in Qdrant is *contextually enriched* per the Anthropic-pattern
> retrieval (`SPEC-RAG-CONTEXTUAL-001`). Two payload fields ride along with the
> chunk text and improve embedding quality at ingest time:
> - `document_summary` — one short summary per artifact, generated once at
>   ingest and shared across every child chunk.
> - `context_prefix` — a per-chunk one-liner placing the chunk in its document
>   context. Embedded together with the chunk text.
>
> The reranker (Step 5) sees `context_prefix + text` — the same shape stored in
> Qdrant — so reranker scoring stays calibrated to the embedded representation.

**Technical:** Against the `klai_knowledge` collection, a three-leg prefetch query is
executed:

```
Leg 1: Dense query on "vector_chunk"     — what the chunk says
Leg 2: Dense query on "vector_questions" — what questions this chunk can answer (HyDE)
Leg 3: Sparse query on "vector_sparse"   — keyword overlap
```

> **Raw-query legs (rewrite-resilience).** When the query was rewritten (Step 0/1), the
> search fuses **two additional legs** on the user's *pre-rewrite* `raw_query` — a dense
> `vector_chunk` leg and a sparse leg — so literal terms the rewrite dropped still match.
> And when `graphiti_enabled` (live in prod), a **graph leg** from FalkorDB joins the
> fusion. So the "three-leg" framing is really dense + questions/HyDE + sparse (+ raw-query
> dense + raw-query sparse when rewritten) (+ graph), all RRF-merged.

Each leg fetches `max(candidates × 4, 20)` candidates (typically 240 with `candidates=60`).
The result sets are merged via **Reciprocal Rank Fusion**:

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

**Parallel graph search:**
FalkorDB/Graphiti runs a graph traversal in parallel with the Qdrant search, resolving
named entities in the query and traversing relationships to find conceptually connected
chunks. Results are merged with Qdrant results using the same RRF formula before
reranking. Timeout: 5 seconds. `GRAPHITI_ENABLED=true` on `retrieval-api` in production.

---

### Step 4b: Link expansion + authority boost (SPEC-CRAWLER-003)

**Simple:** When a top-ranked chunk links to other documents, those linked documents
are pulled in as extra candidates. Documents that many other documents link to get a
small ranking boost — the same logic that made PageRank work for the early web.

**Technical:** Two separate mechanisms run after the initial Qdrant + graph search,
before reranking:

1. **1-hop link expansion** — for the top `link_expand_seed_k=10` chunks, the
   `links_to` payload is mined for outbound URLs (capped at `link_expand_max_urls=30`).
   `fetch_chunks_by_urls()` then scrolls Qdrant for chunks whose `source_url` matches
   one of those URLs (`link_expand_candidates=20` cap), tagging each newly-added chunk
   with an internal `_link_expanded=True` flag. Score starts at 0.0 — they earn their
   way into the top-K through the authority boost and reranker.

2. **Authority boost** — every chunk (seed or expanded) gets `score +=
   link_authority_boost * log(1 + incoming_link_count)`. Default
   `link_authority_boost=0.05`; a chunk with 100 incoming links gets ~+0.23 score
   uplift.

**Instrumentation (F3 phase 1, audit retrieval-coupling-2026-05-06):** the
`retrieval_decision_record` log entry carries a `link_expand` block with `seed_k`,
`candidate_urls`, `expanded_added`, `expanded_in_top_k`, `expanded_top_k_chunk_ids`,
`seed_in_top_k`, `served_top_k`. This lets us measure how often expanded chunks
actually survive into the served top-K vs. dying at the reranker cut-off — Phase 2
(RRF migration vs. recalibrate boost vs. disable) waits on ~7 days of this data.

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

### Step 5d: Parent expansion (SPEC-RAG-PARENT-CHILD-001)

**Simple:** When ingest chunks a document it actually creates two layers — small *child*
chunks (good for matching, bad for context because they cut sentences) and large *parent*
chunks (good for context, too noisy for matching). The retrieval engine matches on
children to stay precise, then swaps each match for its parent text before sending the
result to the LLM. The model sees broader context without the matching step getting
diluted.

**Technical:** Each Qdrant child chunk carries a `parent_chunk_id` payload field
referencing a row in PostgreSQL `knowledge.parent_chunks`. After Step 5c, retrieval-api
runs a single batched lookup:

```sql
SELECT id, text FROM knowledge.parent_chunks WHERE id = ANY($1::bigint[])
```

For each top-K child whose `parent_chunk_id` resolves, the chunk's `text` field is
replaced with the parent's `text` and `is_parent_text` is set to `true` on the
`ChunkResult`. Children without a `parent_chunk_id` (legacy artifacts ingested before
SPEC-RAG-PARENT-CHILD-001 landed, or artifacts where rebuild_kb hasn't yet propagated the
linkage) fall back to their own chunk text — fail-open.

The reranker (Step 5) still scores against the *child* text — that's where matching
precision lives. Parent expansion only changes what the LLM ultimately reads.

Backfill for legacy artifacts: `rebuild_kb_inline(org_id, kb_slug)` reconstructs document
text from existing Qdrant chunks (lossy but workable), re-chunks with parent-child
chunking, and re-upserts to Qdrant with the new `parent_chunk_id` linkage. See the
operator runbook in `docs/runbooks/rag-quality.md` and `klai-knowledge-ingest/
knowledge_ingest/rebuild_tasks.py`.

---

### Step 6: Evidence tier scoring (shadow mode)

**Simple:** A work-in-progress layer that re-weights results by source quality, recency,
and graph centrality before optionally reordering for the LLM. It runs silently today —
the weighted scores are computed and logged, but the flat reranker order is what gets
served. A nightly RAGAS A/B will decide whether to activate it, recalibrate, or
decommission.

**Technical:** Implemented in
[`evidence_tier.apply()`](../../klai-retrieval-api/retrieval_api/services/evidence_tier.py).
Each chunk is multiplied by four weights along independent dimensions, gated by
per-dimension feature flags:

```
final_score = reranker_score
            * content_type_weight       # EVIDENCE_CONTENT_TYPE_ENABLED
            * assertion_mode_weight     # EVIDENCE_ASSERTION_MODE_ENABLED (flat 1.00 in v1)
            * temporal_decay            # EVIDENCE_TEMPORAL_DECAY_ENABLED
            * pagerank_weight           # EVIDENCE_PAGERANK_ENABLED
```

| Weight | Source | Default values |
|---|---|---|
| `content_type_weight` | Per-chunk `content_type` payload (set at ingest) | `kb_article=1.00`, `pdf_document=0.90`, `meeting_transcript=0.80`, `1on1_transcript=0.80`, `graph_edge=0.70`, `web_crawl=0.65`, `unknown=0.55` |
| `assertion_mode_weight` | Per-chunk `assertion_mode` payload | All flat at 1.00 in v1 — plumbing only. SPEC-EVIDENCE-002 governs activation. |
| `temporal_decay` | Chunk `ingested_at` age | `<30d=1.00`, `30-180d=0.95`, `180-365d=0.90`, `>365d=0.85` |
| `pagerank_weight` | Per-chunk `entity_pagerank_max` from FalkorDB | `1 + 0.20 * log1p(pagerank * 100)` — capped ~+25% for hub entities |

After scoring, chunks are reordered into a **U-shape** (`_order_for_llm`): strongest
chunk at position 0, second-strongest at the last position, mid-strength chunks
clustered in the middle. This mitigates "Lost in the Middle" (Liu et al. 2023,
[arXiv:2307.03172](https://arxiv.org/abs/2307.03172)) — long-context LLMs historically
showed >30% performance degradation when the strongest evidence sat in the middle of
the prompt. Whether this still holds for modern frontier LLMs is part of what the
RAGAS A/B will measure.

**Shadow-mode contract:** `EVIDENCE_SHADOW_MODE=true` (default) computes the
weighted/U-shape order, logs both orders side-by-side as `shadow_eval`, and serves the
**flat reranker order**. The CPU cost (`copy.deepcopy(reranked) + apply()`) is paid on
every request.

**Activation path (SPEC-EVIDENCE-001-FOLLOWUP-001):** the shadow mode has been the
default since 2026-03-30. RAGAS infrastructure landed 2026-05-05 (#369). The follow-up
SPEC sets a 30-day deadline to either:

1. Activate (5%/50%/100% staged rollout with auto-revert on quality regression),
2. Activate `evidence_tier_temporal_only` (temporal decay isolated; the dimension with
   the strongest theoretical justification),
3. Decommission entirely (remove the `evidence_tier.apply()` call + payload fields), or
4. Retain-flags-off (`EVIDENCE_SHADOW_MODE=disabled` becomes the new default — stops
   the shadow CPU cost while preserving code for future revival).

The RAGAS A/B uses three `RAG_EVAL_VARIANT` values (`baseline`, `evidence_tier_full`,
`evidence_tier_temporal_only`) over 7 consecutive days. Decision criteria: ≥+0.02 on
RAGAS Context Precision AND Faithfulness with Wilcoxon `p<0.05` against baseline.

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

### Templates injection (SPEC-CHAT-TEMPLATES-001)

Before the retrieval chunks are formatted, the hook fetches the active prompt templates for the calling (org, user) pair and prepends them to the system message.

- **Templates** — reusable prompt scaffolds. Configured per org (admin-only) or per user (personal scope). Users pick 0+ active templates via `active_template_ids` on their PortalUser. The hook fetches `/internal/templates/effective?zitadel_org_id=...&librechat_user_id=...` and injects each template's `prompt_text` in the order the user specified.
- **Cache**: 30 s TTL per `(org, user)` in the LiteLLM in-process cache. Redis DEL from portal-api CRUD + `active_template_ids` PATCH invalidates pre-emptively.
- **Fail-open**: timeout or 5xx from portal-api → `templates_degraded` warning, chat continues without template injection.
- **No per-KB scoping in v1**: templates are either org-wide or personal. KB-binding is explicitly deferred.

Ordering in the final system message: **templates → KB context (below) → any pre-existing system message.**

### LLM safety guardrails (SPEC-CHAT-GUARDRAILS-001 — shipped)

> Corrected 2026-06-08. The earlier "planned `klai-pii` (Presidio + GLiNER)
> microservice" description was never built. SPEC-CHAT-GUARDRAILS-001 **shipped**
> (commits `78e84c395`, `7cf628658`, `210f1d723`) as an **LLM-based** safety layer, not a
> Presidio/GLiNER PII service. There is no `klai-pii` service and no `app_rules.py` in the
> repo.

Guardrails are implemented in `klai-libs/llm-safety` and `deploy/litellm/klai_llm_safety/`
(`policy.py`, `openai_moderation.py`, `refusals.py`, `models.py`, `providers.py`) — an
OpenAI-moderation-style input/output check wired into the LiteLLM hook (and into
retrieval-api coreference). The input check is scoped to the latest user turn and produces
a neutral refusal when content is blocked. It is integrated into the hook flow, not a
separate microservice the request is proxied through.

### Building the context block

The chunks are formatted into a structured context block by
`build_kb_context_prompt()` in `deploy/litellm/klai_kb_context_prompt.py`. The header
depends on narrow mode (LibreChat) or consumer class (widget / Partner API always use
grounded-KB-only). The block is **English-structured / language-neutral** (the old Dutch
markers are gone) and ends with `[End knowledge base context]` followed by a
`KB_LANGUAGE_REMINDER` line that tells the model to answer in the user's language, not the
language of the source chunks (SPEC-RAG-MULTILINGUAL-CHAT-001). Illustrative shape:

```
[Klai Knowledge Base — use this as supplementary context for your answer. You may
complement it with your general knowledge.]    ← broad mode (default, LibreChat)

<KB ANSWER FORMAT instructions>
<active templates, if any>

<rendered evidence chunks with [n] citation markers>

[End knowledge base context]
[LANGUAGE REMINDER] ... respond in the language of the user's most recent question ...
```

Widget responses never include personal-scope chunks — widgets are org-scope only. Their system prompt also differs: grounded-KB-only framing (no general knowledge), and snarkdown is used for markdown rendering in the widget.

Each chunk's org-vs-personal scope comes from the `scope` field on the chunk (see
`render_evidence_context` in `deploy/litellm/klai_kb_citation_render.py`).

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
│ system: [Klai Knowledge Base — ...]                         │
│         <rendered evidence chunks with [n] markers>         │
│         ...                                                 │
│         [End knowledge base context]                        │
│         [LANGUAGE REMINDER] ...                             │
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
| `KNOWLEDGE_RETRIEVE_TOP_K` | `20` | Chunks requested per call (raised from `5` by SPEC-RAG-LOW-CONFIDENCE-ABSTAIN-001 REQ-4) |
| `top_k` (retrieve request) | `8` | retrieval-api request-model default, bounded `[1,50]` (SPEC-SEC-010) |
| `QUERY_REWRITE_MODEL` | `mistral-small-2603` | Hook-side query-rewrite + taxonomy-classify model (was the `klai-fast` alias; now env-configurable) |
| `KNOWLEDGE_RETRIEVE_TIMEOUT` | `3.0` | Retrieval API timeout (seconds) |
| `KLAI_GAP_SOFT_THRESHOLD` | `0.4` | Reranker score below which gap is "soft" |
| `KLAI_GAP_DENSE_THRESHOLD` | `0.35` | Dense score fallback for gap detection |
| `RETRIEVAL_GATE_ENABLED` | `true` | Enable/disable the retrieval gate |
| `RETRIEVAL_GATE_THRESHOLD` | `0.1` | Cosine margin threshold for gate bypass |
| `retrieval_candidates` | `60` | Raw candidates fetched from Qdrant |
| `reranker_candidates` | `20` | Top-N sent to cross-encoder |
| `EVIDENCE_SHADOW_MODE` | `true` | Compute weighted score + U-shape order, log as `shadow_eval`, serve flat reranker order. Activation gated by SPEC-EVIDENCE-001-FOLLOWUP-001 (RAGAS A/B + 30-day deadline). |
| `EVIDENCE_CONTENT_TYPE_ENABLED` | `true` | Per-dimension flag for content_type weights. |
| `EVIDENCE_TEMPORAL_DECAY_ENABLED` | `true` | Per-dimension flag for temporal decay. |
| `EVIDENCE_PAGERANK_ENABLED` | `true` | Per-dimension flag for entity_pagerank_max boost. |
| `link_expand_enabled` | `true` | 1-hop link expansion + authority boost (SPEC-CRAWLER-003). |
| `link_expand_seed_k` | `10` | Top-N raw chunks whose `links_to` payload is mined. |
| `link_expand_max_urls` | `30` | Cap on URLs collected from seed chunks per request. |
| `link_expand_candidates` | `20` | Cap on Qdrant scroll results when fetching link-expanded chunks. |
| `link_authority_boost` | `0.05` | Coefficient on `log(1 + incoming_link_count)` authority boost. |
| `graphiti_enabled` | `true` (both `retrieval-api` and `knowledge-ingest`) | Include FalkorDB graph search (parallel with Qdrant 3-leg RRF). |
| `graph_search_timeout` | `5.0` | FalkorDB search timeout (seconds) |
| `coreference_timeout` | `3.0` | Coreference LLM call timeout (seconds) |
| `reranker_timeout` | `30.0` | Cross-encoder timeout (seconds) |
| `coreference_model` | `klai-fast` | Model tier for coreference resolution |
| `synthesis_model` | `klai-primary` | Model tier for answer generation |
| `WIDGET_JWT_SECRET` | (required) | HS256 signing secret for widget session tokens (portal-api env). Rotating it invalidates all live widget sessions. |
| `WIDGET_SESSION_TTL_SECONDS` | `3600` | Widget JWT lifetime (1 hour). Widget auto-refreshes on 401. |

---

## Quality measurement (SPEC-RAG-EVAL-001, shipped 2026-05-05)

The retrieval pipeline above is exercised nightly by a RAGAS-based evaluation harness that writes per-query metrics to `knowledge.rag_eval_results`. Every retrieval-improvement SPEC (CONTEXTUAL-001 / QUERY-REWRITE-001 / PARENT-CHILD-001 / TAXONOMY-001) is measured by setting `RAG_EVAL_VARIANT=<experiment>` and comparing the result rows against the `baseline` rows.

```
                    ┌─────────────────────────────┐
                    │  evaluate_retrieval_quality │
                    │  _nightly (Procrastinate)   │
                    └─────────────┬───────────────┘
                                  │
                ┌─────────────────┼─────────────────┐
                ▼                 ▼                 ▼
       Load YAML suite     Call /retrieve      klai-fast judge
       (chat, knowledge_   (X-Internal-       (4 RAGAS metrics:
       org)               Secret auth)        precision, recall,
                                              faithfulness,
                                              answer_relevance)
                                  │
                                  ▼
                  knowledge.rag_eval_results (one row/query)
                                  │
                                  ▼
                  Grafana → "RAG quality (RAGAS metrics)"
                  Alert  → rag_eval_faithfulness_low (HIGH)
```

| Component | File / Path |
|---|---|
| Procrastinate task | `klai-knowledge-ingest/knowledge_ingest/eval/ragas_runner.py` (`evaluate_retrieval_quality_nightly`) |
| Suite YAMLs | `klai-knowledge-ingest/knowledge_ingest/eval/suites/{chat,knowledge_org}.yaml` (60 hand-curated Voys queries) |
| Storage | `knowledge.rag_eval_results` (migration `deploy/postgres/migrations/014_rag_eval_results.sql`) |
| Ad-hoc CLI | `python -m knowledge_ingest.eval --suite chat --variant <name>` |
| Grafana dashboard | `deploy/grafana/provisioning/dashboards/rag-quality.json` |
| Alert rule | `deploy/grafana/provisioning/alerting/rag-eval-rules.yaml` |
| Triage runbook | [docs/runbooks/rag-quality.md](../runbooks/rag-quality.md) |

**Voys baseline → post-stack measurements (2026-05-05, chat suite, n=30):**

| Metric | `baseline-v4` (pre-stack) | `post_pr_abcdefg_v1` (full Tier 1+2 live) | Δ |
|---|---|---|---|
| `context_precision` | 0.231 | **0.372** | +0.141 (+61%) |
| `context_recall` | 0.253 | **0.642** | +0.389 (+154%) |
| `faithfulness` | NaN (judge truncation) | **0.812** | first measurable |
| `answer_relevance` | 0.706 | 0.711 | +0.005 |

The eval-harness calls retrieval-api directly and bypasses the LiteLLM hook, so query
rewriting and taxonomy classifying (which live in the hook) are NOT measured by the
numbers above. Their effect shows up only on real chat traffic. The +61% precision /
+154% recall / first-measurable faithfulness come from contextual-retrieval chunks +
parent-child expansion + the rebuild_kb backfill alone. Detailed roadmap closing
snapshot: [docs/architecture/retrieval-improvements-roadmap.md](retrieval-improvements-roadmap.md).

**Multi-tenant by design:** every query in a suite YAML carries its own `org_zitadel_id`. v1 ships with Voys-only suites. Adding additional tenants post-launch is a YAML drop-in — no service split, no per-tenant deployment.

---

## Reference: key files

| Component | File | What it does |
|---|---|---|
| KB preferences (model) | `klai-portal/backend/app/models/portal.py` | `PortalUser` model, all five KB fields |
| KB preferences (API) | `klai-portal/backend/app/api/app_account.py` | `GET`/`PATCH /api/app/account/kb-preference` |
| KB feature (internal) | `klai-portal/backend/app/api/internal.py` | `GET /internal/v1/users/{id}/feature/knowledge` |
| Chat config bar (UI) | `klai-portal/frontend/src/routes/app/_components/ChatConfigBar.tsx` | The preference bar (LibreChat only); was `KBScopeBar.tsx` |
| LiteLLM hook (entrypoint) | `deploy/litellm/klai_knowledge.py` | `KlaiKnowledgeHook` — orchestrates; delegates to the `klai_kb_*` modules below |
| ↳ scope policy | `deploy/litellm/klai_kb_scope_policy.py` | `build_retrieve_body` / `resolve_kb_retrieval_scope` (preferences → retrieve body) |
| ↳ query rewrite | `deploy/litellm/klai_kb_query_rewrite.py` | Hook-side rewrite + taxonomy classify (`QUERY_REWRITE_MODEL`) |
| ↳ answer policy | `deploy/litellm/klai_kb_answer_policy.py` | 6 prompt modes, deterministic refusals, `kb_chunks_present_header` |
| ↳ chat mode | `deploy/litellm/klai_kb_chat_mode.py` | `ChatRetrievalPromptMode` enum (general / open_kb / strict_kb / …) |
| ↳ context block | `deploy/litellm/klai_kb_context_prompt.py` | `build_kb_context_prompt` + `KB_LANGUAGE_REMINDER` |
| ↳ citation render | `deploy/litellm/klai_kb_citation_render.py` | `compose_(non_)streaming_kb_response`, `render_evidence_context`, **Bronnen** section |
| ↳ LLM safety | `deploy/litellm/klai_llm_safety/` + `klai-libs/llm-safety` | SPEC-CHAT-GUARDRAILS-001 moderation (input/output) |
| Retrieval pipeline | `klai-retrieval-api/retrieval_api/api/retrieve.py` | The seven-step retrieval pipeline |
| Coreference | `klai-retrieval-api/retrieval_api/services/coreference.py` | Pronoun resolution via `klai-fast` |
| Embeddings | `klai-retrieval-api/retrieval_api/services/tei.py` | Dense + sparse embedding via BGE-M3 |
| Qdrant search | `klai-retrieval-api/retrieval_api/services/search.py` | Hybrid three-leg RRF search |
| Source-aware select | `klai-retrieval-api/retrieval_api/services/diversity.py` | `source_aware_select()` — `mentioned` / `diversify` mode + `STOP_WORDS`. Called from `retrieve.py` step 5c. |
| Evidence tier | `klai-retrieval-api/retrieval_api/services/evidence_tier.py` | `apply()` + `_order_for_llm()` U-shape; content_type / temporal / pagerank weights. Shadow-mode default. |
| Parent text swap | `klai-retrieval-api/retrieval_api/services/parent_lookup.py` | SPEC-RAG-PARENT-CHILD-001 — batch-fetches `knowledge.parent_chunks` rows for chunks with `parent_chunk_id`. |
| Quality boost | `klai-retrieval-api/retrieval_api/quality_boost.py` | SPEC-KB-015 — `feedback_count >= 3` cold-start gate, ±10% boost. |
| Identity verify (portal) | `klai-portal/backend/app/services/identity_verifier.py` | `verify_identity_claim()` — JWT / membership / `partner:<key_id>` branches. |
| Identity asserter (lib) | `klai-libs/identity-assert/klai_identity_assert/` | Consumer-side cache + retry around `/internal/identity/verify`. |
| F2 audit ref | `.moai/audits/retrieval-coupling-2026-05-06/findings/F2-...md` | Why partner-key verification lives portal-side (not in retrieval-api). |
| Router (signal) | `klai-retrieval-api/retrieval_api/services/router.py` | Keyword + semantic-centroid signal for source-aware select (SPEC-KB-021) |
| Graph search | `klai-retrieval-api/retrieval_api/services/graph_search.py` | FalkorDB/Graphiti parallel traversal, RRF-merged with Qdrant results |
| Reranker | `klai-retrieval-api/retrieval_api/services/reranker.py` | Cross-encoder reranking via BGE-reranker-v2-m3 on gpu-01 (Infinity) |
| Retrieval gate | `klai-retrieval-api/retrieval_api/services/gate.py` | Cosine margin bypass check |
| Partner API auth | `klai-portal/backend/app/api/partner_dependencies.py` | `get_partner_key` — branches on token shape (`pk_live_*` → SHA-256 lookup; else JWT decode) |
| Widget auth | `klai-portal/backend/app/services/widget_auth.py` | `generate_session_token` — HS256 JWT signed with `WIDGET_JWT_SECRET`, 1h TTL |
| Widget admin | `klai-portal/backend/app/api/admin_widgets.py` | CRUD on `widgets` table (SPEC-WIDGET-002) |
| Widget bundle | `klai-widget/src/main.ts` | SolidJS entry point; bootstrap via `/partner/v1/widget-config` |
| Templates | `klai-portal/backend/app/api/app_templates.py` | CRUD (SPEC-CHAT-TEMPLATES-001); resolved via `/internal/templates/effective` in the LiteLLM hook. |
| LLM safety (shipped) | `klai-libs/llm-safety` + `deploy/litellm/klai_llm_safety/` | SPEC-CHAT-GUARDRAILS-001 — LLM-moderation guardrails (no `klai-pii`/`app_rules.py`). |
| Config | `klai-retrieval-api/retrieval_api/config.py` | All configurable values and defaults |
| RAGAS eval harness | `klai-knowledge-ingest/knowledge_ingest/eval/ragas_runner.py` | `run_evaluation()` + `evaluate_retrieval_quality_nightly` Procrastinate task |
| RAGAS suite loader | `klai-knowledge-ingest/knowledge_ingest/eval/suite_loader.py` | YAML schema validator + `Suite` / `SuiteQuery` dataclasses |
| RAGAS retrieval client | `klai-knowledge-ingest/knowledge_ingest/eval/retrieval_client.py` | Calls `/retrieve` with `X-Internal-Secret`; fail-open on errors (REQ-3) |
| RAGAS judge client | `klai-knowledge-ingest/knowledge_ingest/eval/judge_client.py` | klai-fast for answer generation + 4 RAGAS metrics |
| RAGAS storage | `klai-knowledge-ingest/knowledge_ingest/eval/store.py` | asyncpg helper `insert_eval_row()` |
| RAGAS suite YAMLs | `klai-knowledge-ingest/knowledge_ingest/eval/suites/{chat,knowledge_org}.yaml` | 30 queries each, mix-tagged for SPEC-target stratification |
