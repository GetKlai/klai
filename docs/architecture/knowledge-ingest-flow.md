# Knowledge Ingestion & Retrieval: How It Works

> Engineering reference for the running system on core-01.
> Verified against `klai-knowledge-ingest/knowledge_ingest/` and `klai-retrieval-api/` — March 2026 (updated 2026-03-31).
>
> For the research backing these design decisions, see
> [knowledge-system-fundamentals.md](knowledge-system-fundamentals.md).
> For evidence-weighted scoring and assertion mode weights research, see
> [Evidence-Weighted Knowledge Retrieval: Research Synthesis](../research/README.md).

---

## The big picture

### Three ways to store knowledge — and why we use all three

There are three fundamentally different ways to store and retrieve information, and they
answer different questions.

**A relational database (PostgreSQL)** stores structured facts: who created this document,
when was it ingested, what is the visibility setting, what is the artifact ID. It answers
exact lookups — "give me all documents for org X ingested after date Y." It cannot answer
"find me something conceptually similar to this question."

**A vector database (Qdrant)** converts text into a point in a high-dimensional space via
an embedding model. Texts with similar meaning end up close together. This answers
semantic questions — "find chunks that are conceptually relevant to what the user just
asked" — even when the exact words don't match. It cannot tell you *how* concepts relate
to each other.

**A knowledge graph (FalkorDB + Graphiti)** stores entities and the relationships between
them: "Jan works at Voys", "Voys uses platform X", "platform X has known issue Y". It
answers traversal questions — "what is connected to this concept, and how?" — that neither
SQL nor vector search can express naturally.

Klai uses all three:

| What | Product | Answers |
|---|---|---|
| Structured metadata | PostgreSQL | Exact lookups, artifact tracking, access control |
| Semantic search | Qdrant | "Find relevant content for this question" |
| Relationship traversal | FalkorDB (via Graphiti) | "What is connected to this?" |

In practice, most retrieval today is Qdrant-only. The graph layer (FalkorDB) is
implemented but gated behind a feature flag — it runs in production only when
`settings.graphiti_enabled` is on.

---

### How a piece of knowledge moves through the system

When a user saves a page, Klai doesn't just store the text — it *prepares* it for
retrieval. That means breaking it into chunks, generating AI context prefixes,
synthesizing hypothetical questions, and computing multiple embedding vectors. Each chunk
ends up with up to three search indexes in Qdrant (what it says, what questions it
answers, what keywords it contains), plus an entry in the knowledge graph.

None of this happens in real-time. The document lands in Qdrant within seconds (immediately
searchable with basic vectors), and the enrichment catches up in the background. Later,
when a user sends a chat message, the system quietly retrieves the most relevant chunks
and injects them into the model's context before it even starts generating — the user
never has to ask.

```
Where content comes from         What happens to it            Where it lives
────────────────────────         ──────────────────            ──────────────
User saves a KB page      ──┐
GitHub repo syncs         ──┼──▶  knowledge-ingest  ─────────▶  Qdrant (semantic)
Webcrawler indexes site   ──┘     (parse, chunk,                 PostgreSQL (metadata)
Meeting transcript saved  ──┐      embed, enrich)
                              │         │
                              │         └──▶ Graphiti/FalkorDB (relationships)
                              │
When the user chats:
User message ──▶ LiteLLM hook ──▶ retrieval-api ──▶ top chunks injected into context
              (automatic,          (Qdrant + graph
               transparent)         + reranker)
```

---

## Part 1: Where content comes from

### 1.1 The knowledge base editor (klai-docs → Gitea webhook)

The editor (BlockNote in klai-portal) **auto-saves** — there is no explicit save button.
Every keystroke resets a 1.5-second debounce timer; when the user pauses for 1.5s the
page is saved. On navigating away, any pending timer is flushed immediately.

Each save commits to Gitea, which fires a webhook to `knowledge-ingest`. Gitea and the
knowledge layer are **deliberately decoupled**: the webhook does not run the ingest
pipeline immediately. Instead it schedules a debounced ingest task.

**Debounced ingest (live as of 2026-03-27):** When the webhook fires, the handler defers
a Procrastinate task (`ingest-kb` queue) scheduled `INGEST_DEBOUNCE_SECONDS` into the
future (default: 180 s = 3 minutes), with
`queueing_lock="gitea:{org_id}:{kb_slug}:{path}"`. Procrastinate's `UNIQUE INDEX` on
`(queueing_lock, status='todo')` ensures at most one pending task per document: every
subsequent save while the task is still scheduled raises `AlreadyEnqueued`, which is
caught and silently dropped. When the task eventually runs, it fetches the **current**
version of the document from Gitea (not the content at queue time), so the knowledge layer
always receives the final version.

```
User pauses typing for 1.5s (auto-save debounce in klai-docs)
  → serializes to HTML + YAML frontmatter
  → commits to Gitea (one repo per KB, named org-{slug}/{kb_slug})
  → Gitea webhook → POST /ingest/v1/webhook/gitea
       ↓
  defer ingest_from_gitea task, schedule_in=3min, queueing_lock=gitea:org:kb:path
       ↓ (all intermediate saves → AlreadyEnqueued, silently dropped)
       ↓ 3 minutes after the last save:
  task executes → fetch latest content from Gitea → ingest_document()
```

**Content-hash dedup (safety net):** `ingest_document` computes SHA-256 of the incoming
content and compares it to the stored hash on `knowledge.artifacts`. If the content
matches what was last ingested (e.g., a bulk sync re-sending unchanged pages), the full
pipeline is skipped and the request returns `{"status": "skipped"}`. The hash is stored
on every new artifact so subsequent runs can detect no-ops.

**Page deletes** are still processed immediately — no debounce. Qdrant vectors and the
artifact record are removed as soon as the webhook fires.

The `org_id` (the Zitadel organization ID) is read from the Gitea organization's
*description* field. This is a naming convention: whenever a Klai org is provisioned, its
Zitadel org ID is written into the Gitea org description so the webhook handler can
resolve it without a separate lookup.

On page delete: all Qdrant vectors for that document path are removed.

### 1.2 External sources via klai-connector

klai-connector is a separate service that syncs external content into the knowledge pipeline
on a schedule or on-demand.

**GitHub repos (live):** klai-connector authenticates as a GitHub App installation, lists
all files, and skips syncs when the repository tree SHA hasn't changed since the last run.
This means a large repo with no changes costs almost nothing. Supported files: `.md`,
`.txt`, `.pdf`, `.docx`, `.rst`, `.html`, `.csv`.

**Binary file parsing in klai-connector:** Plain-text formats (`.md`, `.txt`, `.rst`,
`.csv`) are decoded directly. Binary formats (`.pdf`, `.docx`, `.html`) are parsed via
**Unstructured.io** (`unstructured.partition.auto`) inside `klai-connector/app/services/
parser.py`. The parsed plain text is then forwarded to `knowledge-ingest` via
`POST /ingest/v1/document`. Maximum file size: 50 MB.

**Web crawls (live):** the crawl wizard uses Crawl4AI (Playwright-backed) with two endpoints:

- `POST /ingest/v1/crawl/preview` — crawl wizard preview; returns `fit_markdown`, `word_count`,
  and any `warnings` (e.g. `navigation_detected`, `low_word_count`) without ingesting.
- `POST /ingest/v1/crawl` — full ingest; fetches URL via crawl4ai (Playwright, JS rendering),
  deduplicates via dual-hash, and ingests via the standard pipeline.

**Smart pipeline switching (SPEC-CRAWL-001):** the pipeline is chosen based on whether a
CSS selector is available:

| Condition | JS chrome removal | `excluded_tags` | `css_selector` | Pruning filter |
|---|---|---|---|---|
| No selector | `_JS_REMOVE_CHROME` (semantic tags only) | nav, footer, header, aside, script, style | — | Yes (0.45) |
| Selector present (user or stored) | disabled | `[]` | selector value | Yes (0.45) |

**Domain selector storage:** resolved selectors are persisted per `(domain, org_id)` in
`knowledge.crawl_domains`. On subsequent crawls for the same domain, the stored selector is
reused automatically. User-provided selectors always win over stored or AI-detected ones.

**AI-assisted selector detection:** when a preview crawl yields fewer than 100 words and no
selector was used, the system extracts a DOM summary (top 25 elements by word count) and sends
it to `klai-fast` to identify the main content selector. If the AI selector produces ≥ 100 words
on a re-crawl, it is stored in `crawl_domains` with `selector_source='ai'`. Otherwise the
original result is returned with a `low_word_count` warning.

**Crawl registry (SPEC-CRAWLER-002):** two tables in the `knowledge` schema persist crawl
state per `(org_id, kb_slug, url)`:

- `crawled_pages` — URL registry: `raw_html_hash` (SHA-256 of raw HTML), `content_hash`
  (SHA-256 of extracted markdown), `raw_markdown`, `crawled_at`. Used for deduplication and
  as a content cache for future re-ingest without re-crawling.
- `page_links` — link graph: `from_url → to_url` pairs with `link_text`, extracted from
  `result.links['internal']` by crawl4ai. Used by SPEC-CRAWLER-003 for link-graph
  retrieval enrichment: anchor text augmentation (vocabulary bridging in `enriched_text`),
  `incoming_link_count` authority boost, and 1-hop forward expansion in the retrieval API.

**Dual-hash deduplication:** both `crawl_url` and the bulk crawler skip ingest in two stages:
1. `raw_html_hash` unchanged → skip everything (JS/tracking noise ignored)
2. `raw_html_hash` changed but `content_hash` unchanged → update raw hash only, skip re-ingest
3. Both changed → full re-ingest and update both hashes

The bulk crawler fetches all hashes for a crawl job in a single batch query
(`get_crawled_page_hashes`) to avoid N+1 round-trips. On KB deletion, both `crawled_pages`
and `page_links` rows are cleaned up in `delete_kb()`.

**Source URL in artifacts:** every crawled artifact stores `{"source_url": "..."}` in
`knowledge.artifacts.extra`, enabling traceability back to the origin page.

**Navigation contamination detection:** the preview endpoint scores link density in the returned
markdown; if >35% of lines are link-only and the first 25 lines are >45% link-only, a
`navigation_detected` warning is returned so the user can add a selector.

**Planned connectors:**
- Google Drive — on the roadmap
- Microsoft SharePoint / OneDrive — on the roadmap
- Notion — on the roadmap
- Manual file upload (like Focus) — on the roadmap; users will be able to upload a PDF or
  document directly into a knowledge base, without needing a connected source

Both current adapters call the same `POST /ingest/v1/document` endpoint that direct uploads
use. A `source_ref` (`owner/repo:branch:path`) is used as a deduplication key so
re-syncing the same content updates rather than duplicates.

### 1.3 Meeting transcripts via scribe-api

After a meeting is processed by the Vexa bot and whisper-server, scribe-api sends the
summarized transcript to knowledge-ingest — **but only if the user has enabled knowledge
saving** for their account. This is opt-in.

```
Vexa bot captures audio → whisper-server transcribes
  → scribe-api summarizes
  → POST /ingest/v1/document (content_type: meeting_transcript or 1on1_transcript)
```

The `content_type` drives how the transcript is chunked and enriched — transcripts get
smaller chunks and always get HyPE (see below), because the vocabulary gap between how
people speak in meetings and how they ask follow-up questions is large.

---

## Part 2: What happens inside knowledge-ingest

The pipeline has two phases. In the first phase (synchronous, completes in seconds), the
document is parsed, chunked, and given a basic search index. In the second phase
(asynchronous, via a task queue), each chunk is enriched with AI-generated context and
additional search signals. The document is searchable immediately; it just gets *better*
as enrichment completes.

### Content profiles: one profile per document type

Before any processing begins, the pipeline selects a *content profile* based on
`content_type`. Profiles encode domain knowledge about how different content should be
handled — a PDF technical manual and a meeting transcript have very different chunk sizes,
context window strategies, and enrichment needs.

| content_type | HyPE | Context strategy | Max chunk size |
|---|---|---|---|
| `kb_article` | Conditional (synthesis_depth ≤ 1) | first_n | 500 tokens (2000 chars) |
| `pdf_document` | Always | front_matter | 800 tokens (3200 chars) |
| `meeting_transcript` | Always | rolling_window | 400 tokens (1600 chars) |
| `1on1_transcript` | Always | rolling_window | 300 tokens (1200 chars) |
| `email_thread` | Conditional (depth ≤ 1) | most_recent | 500 tokens (2000 chars) |
| `unknown` | Never | first_n | 500 tokens (2000 chars) |

If no `content_type` is set, the pipeline uses `unknown` — no enrichment, basic chunking.

### Phase 1: Immediate (synchronous)

**Step 1 — Parse and chunk.** The content arrives as plain text (already decoded by the
caller for Gitea pages and connector text files). Binary files (PDF, DOCX, HTML) are
parsed upstream in **klai-connector** via Unstructured.io's `partition.auto` — not in
knowledge-ingest itself.

Chunking is done by a custom `chunker.py` inside knowledge-ingest:
1. **Heading split** — the document is first split at H1/H2/H3 headings
   (`^(#{1,3})\s+(.+)$`). Each section keeps its heading prepended so chunks are
   self-contained.
2. **Size split** — sections that are still larger than `chunk_size` (default 1500
   **characters**, roughly 300–400 tokens for BGE-M3) are further split at paragraph
   boundaries (`\n\n`) or sentence boundaries (`. `).
3. **Overlap** — consecutive chunks share a 200-character tail/head overlap to prevent
   answers from falling between chunks.

The content profile defines `chunk_tokens_max` per document type. The chunker converts
this to characters (`tokens × 4`) and uses it as `chunk_size`. Effective chunk sizes:

| content_type | chunk_tokens_max | chunk_size (chars) |
|---|---|---|
| `1on1_transcript` | 300 | 1200 |
| `meeting_transcript` | 400 | 1600 |
| `kb_article` | 500 | 2000 |
| `email_thread` | 500 | 2000 |
| `unknown` | 500 | 2000 |
| `pdf_document` | 800 | 3200 |

**Step 2 — Embed and store raw vectors.** Each chunk is sent to **TEI** (HuggingFace
text-embeddings-inference, OpenAI-compatible `/v1/embeddings` endpoint) running **BGE-M3**
to produce a 1024-dimensional dense vector. These raw embeddings are immediately upserted into Qdrant as `vector_chunk`.
The document is now searchable.

**Step 3 — Enqueue enrichment.** The main request enqueues two async tasks via
Procrastinate (a PostgreSQL-backed task queue) and returns to the caller. Three queues
manage priorities:
- `enrich-interactive` — user-triggered saves (should feel fast)
- `enrich-bulk` — background connector syncs (can wait)
- `graphiti-bulk` — knowledge graph ingestion (lowest priority)

The worker processes queues in drain order: `ingest-kb → enrich-interactive → enrich-bulk
→ graphiti-bulk`.

**Step 4 — Feed the knowledge graph (async).** If `settings.graphiti_enabled` is on, a
Procrastinate task on the `graphiti-bulk` queue defers the document to Graphiti/FalkorDB
as an "episode". The main request doesn't wait for this and doesn't fail if it errors.

### Phase 2: Async enrichment (Procrastinate worker)

The enrichment worker picks up tasks from the queue and runs the following for each
chunk in the document.

**Why enrich at all?** Raw text retrieval has two systematic problems: (1) chunks lose
context when isolated ("the meeting discussed three options" — which meeting? which
options?), and (2) users search with questions while documents contain answers (vocabulary
gap). Enrichment addresses both.

**Step A — Contextual Retrieval prefix.**
A single LLM call (via the LiteLLM proxy, model `klai-fast`) takes each chunk plus
surrounding context from the document and generates:
- A 1–2 sentence *context prefix* that situates the chunk: "This excerpt is from the Q4
  roadmap doc and describes the decision to migrate from X to Y."
- 3–5 *hypothetical questions* the chunk would answer.

The context strategy (from the profile) determines which surrounding text goes into the
prompt: `first_n` uses the document's opening paragraphs, `rolling_window` uses nearby
chunks, `front_matter` uses YAML metadata, and `most_recent` uses the most recent chunks
(useful for email threads where the latest message is most relevant).

If the LLM call fails, the chunk falls back to its original text — enrichment failure
never blocks a chunk from being retrievable.

**Step B — Dense re-embedding.**
The enriched text (`context_prefix + "\n\n" + original_chunk`) is re-embedded via TEI.
This replaces the raw `vector_chunk` stored in Phase 1.

**Step C — Sparse embedding.**
`sparse_embedder.py` calls a BGE-M3 sidecar that produces SPLADE-style *sparse* vectors.
Sparse vectors capture exact keyword matches that dense embeddings (which operate in a
continuous semantic space) can miss. A user searching for a product version number or an
exact acronym benefits from sparse retrieval. This runs in parallel with Step B.

**Step D — HyPE vectors (if enabled for this content type).**
The questions from Step A are joined into a single string and embedded. This produces
`vector_questions` — a vector that represents "what this chunk answers" rather than "what
this chunk says". At query time, a user's question will match `vector_questions` much more
reliably than it would match the dense content embedding, because both are *questions*
(same distributional space).

This is the **Dual-Index Fusion** pattern: one chunk, two dense vectors plus one sparse.
The result is that queries can match via meaning (dense chunk), via question-answer
alignment (dense questions), or via keywords (sparse). All three are fused at query time.

**Step E — Full Qdrant upsert.**
The enriched point is upserted with up to three named vectors:

| Named vector | What it encodes | Present when |
|---|---|---|
| `vector_chunk` | Dense embedding of `context_prefix + chunk text` | Always |
| `vector_questions` | Dense embedding of joined hypothetical questions | HyPE enabled for this chunk |
| `vector_sparse` | Sparse (SPLADE) embedding | Enrichment succeeded |

Payload per point includes: `org_id`, `kb_slug`, `path`, `artifact_id`, `content_type`,
`visibility`, `valid_from`, `valid_until`, `text` (original), `text_enriched`,
`context_prefix`, `questions`, `chunk_index`, `user_id`.

**Tenant isolation** is enforced by `org_id`: it's a payload index with `is_tenant: true`,
and every search includes a mandatory `must: org_id = X` filter. All tenants share a
single `klai_knowledge` collection — there's no collection-per-tenant. This keeps ops
simple (one index to manage, one backup, no provisioning step per org) while still
isolating data at query time.

### Phase 3: Knowledge graph ingestion (Graphiti / FalkorDB)

After the Qdrant upsert, the `graphiti-bulk` Procrastinate task runs `ingest_episode()`.
This is the deepest enrichment phase — it builds a traversable knowledge graph on top of
the vector index.

**Three types of objects in the graph:**

**EpisodicNode** — the document itself as a time-stamped event. Every ingested artifact
becomes an `EpisodicNode` with the full text, a `valid_from` timestamp, and a
`belief_time_end` sentinel (`253402300800` = year 9999) that marks it as currently active.
When a document is re-ingested, the old EpisodicNode gets `belief_time_end = now()` (soft
delete) and a fresh one is created. This temporal layering means the graph holds a full
history of what the system "believed" at any point in time.

**EntityNode** — extracted entities within the document. Graphiti's LLM step extracts
named entities (people, products, projects, decisions, organisations) from each chunk and
creates or updates `EntityNode` objects in FalkorDB. If the entity was seen in a previous
document, its existing node is reused (entity resolution by name + embedding similarity).

**Edge** — relationships between entities. Each co-occurrence or explicit relationship
found in the text creates or strengthens a `RELATES_TO` edge between two `EntityNode`
objects. The label is a short phrase extracted by the LLM ("works at", "decided on",
"depends on").

**Hebbian reinforcement** — every time two entities co-occur in a new document, the edge
weight between them is incremented: `SET r.weight = COALESCE(r.weight, 0) + 1`. Edges
that keep appearing across many documents become stronger. This mirrors Hebb's rule
("neurons that fire together wire together") — frequently co-mentioned concepts end up
more tightly connected in the graph.

**PageRank** — after every Graphiti ingest batch, a PageRank algorithm runs over the
entity graph (`CALL algo.pageRank('Entity', 'RELATES_TO')`). Entities with many
connections (and connections to other well-connected entities) get higher scores. These
scores are written back to **Qdrant** as `entity_pagerank_max` in the chunk payload,
allowing the retrieval layer to optionally boost chunks that mention highly-central
entities.

**Rate limiting** — Graphiti makes internal LLM calls (entity extraction, relationship
labeling). Mistral's API has a 1 req/s rate limit. `knowledge_ingest/graph.py` wraps all
Graphiti LLM calls with a `_TokenBucketLimiter(rate=settings.graphiti_llm_rps)` transport
(default 1.0 req/s). On rate-limit error: backs off 30s then 60s. On other errors: backs
off 1s then 2s. Three retries total before the episode is dropped (with a warning log).

---

## Part 3: How knowledge reaches the user

### 3.1 The automatic path: LiteLLM pre-call hook

This is the main retrieval path. Every chat message in LibreChat passes through LiteLLM,
which runs `KlaiKnowledgeHook` as a pre-call hook before forwarding to the model.

The hook is invisible to the user. It runs within the configured timeout (default 3s),
quietly injects relevant chunks as a system message prefix, and passes the augmented
request to the model. If anything fails — portal unreachable, retrieval timeout, no
results — the request passes through unchanged. The chat never breaks.

```
User sends message
  → LiteLLM hook extracts the last user message as the query
  → Trivial check: skip if < 8 chars or matches greeting/ack pattern
  → Reads org_id from team-scoped API key metadata (master key → skip silently)
  → Reads user_id from the "user" field (missing → skip)
  → Portal feature-gate + KB scope: GET /internal/v1/users/{user_id}/feature/knowledge
    Two-level cache: kb_ver:{org}:{user} pointer (30s TTL) + kb_feature:...:{version} data (300s)
    Cache invalidates within 30s of any KBScopeBar change (version pointer expires first)
    Fail-closed on entitlement error; fail-open on preference errors (preserves retrieval)
  → Pre-step skip: if kb_retrieval_enabled=false → return data unchanged (no retrieval call)
  → scope = "both" if kb_personal_enabled=true (default), "org" if false
  → POST to retrieval-api with scope, top_k, conversation_history (last 6 turns)
    Optional: kb_slugs=[...] if kb_slugs_filter is set (restricts to named org KBs only)
  → Gap detection (SPEC-KB-014): classify result as hard (no chunks) or soft (all scores < 0.4)
    If gap detected: fire-and-forget POST to /internal/v1/gap-events
  → If retrieval_bypassed=true: skip injection, set _klai_kb_meta (gate_bypassed=true)
  → Inject chunks as system message prefix with [org] / [persoonlijk] labels
  → Set data["_klai_kb_meta"] for downstream hooks (custom_router uses this)
  → Model gets the augmented request
```

**Authorization is fail-closed.** If `PORTAL_INTERNAL_SECRET` is not set, or if the
portal endpoint is unreachable, the hook returns data unchanged — no KB injection. This
prevents knowledge leaking to unauthorized users but means a misconfigured deployment
silently degrades rather than errors loudly.

**`scope=both` in one request.** The hook sends a single request to retrieval-api with
`scope=both`. The retrieval-api handles the fan-out to personal and org scopes internally
and returns chunks labelled by scope. Personal chunks appear under `[persoonlijk]`,
org chunks under `[org]`.

**Personal scope:** Personal saves via the MCP tool go directly to the ingest pipeline
(not via Gitea) and are indexed in Qdrant with `kb_slug="personal"` and `user_id`. The
retrieval-api filters personal scope by `user_id` so users only see their own saves.

**Why team-scoped keys?** Each LibreChat container has a LiteLLM key scoped to its team,
with `org_id` in the key's metadata. This means the hook knows which org's knowledge to
search without any runtime configuration — it's baked into the key. Containers
provisioned with the master key simply skip retrieval (no `org_id` in metadata).

### 3.2 The retrieval-api (SPEC-KB-008)

The hook calls **`retrieval-api`**, a separate deployed service (`retrieval-api:8040`).
`KNOWLEDGE_RETRIEVE_URL` **must** be set in the LiteLLM container environment — the hook
raises `RuntimeError` at startup if it is missing. In production this is set to
`http://retrieval-api:8040/retrieve` via docker-compose.

The `/knowledge/v1/retrieve` endpoint in `knowledge-ingest` has been **removed** (issue #51).
The old code-level fallback URL in the hook that pointed to it has also been removed.

**Retrieval scopes:** The retrieval-api request model has a `scope` field:

| Scope | Searches | Requires |
|---|---|---|
| `org` | All KBs in the org | `org_id` |
| `personal` | User's personal KB only | `org_id` + `user_id` |
| `both` | Personal + org | `org_id` + `user_id` |
| `notebook` | Focus notebook (Qdrant `klai_focus`) | `org_id` + `notebook_id` |
| `broad` | Focus notebook + org KB | `org_id` + `notebook_id` |

The LiteLLM hook fires a single request with `scope=both`. The retrieval-api handles the
fan-out to personal and org scopes internally and returns chunks labelled by scope.

**Multiple knowledge bases and visibility:** An org can have multiple KBs (each with a
`kb_slug`). Each KB has a `visibility` field (`public` | `internal` | `private`) stored
as a Qdrant payload field and in the `knowledge.kb_config` PostgreSQL table (with TTL
cache). Visibility is enforced at retrieval time in `_scope_filter()`: for `org` and
`both` scopes, chunks with `visibility="private"` are excluded unless the requesting
`user_id` matches the chunk's `user_id` (the owning user can still see their own private
chunks). Personal scope skips this check — it's already restricted to a single user.

The portal writes KB visibility to `knowledge-ingest` (`PATCH /ingest/v1/kb/visibility`)
on KB create and on every visibility change. `ingest_document()` reads the KB's current
visibility from `kb_config` and attaches it to every chunk at ingest time, so the Qdrant
filter is always effective.

`RetrieveRequest` also accepts an optional `kb_slugs: list[str]` parameter. When set,
`_scope_filter()` adds a `MatchAny` condition on the `kb_slug` payload field, restricting
results to the specified KBs only. The LiteLLM hook passes `kb_slugs` when the user has
an active `kb_slugs_filter` set via the KBScopeBar (SPEC-KB-013). When `kb_slugs_filter`
is null (the default), all org KBs are searched. Callers such as Focus can also pass it
to scope retrieval to a specific KB.

**The retrieval pipeline:**

**1. Coreference resolution.** The query is passed through a coreference resolver that
expands pronouns and references using conversation history. "What did we decide about
that?" becomes something more specific before embedding.

**2. Embed the query.** Dense (TEI/BGE-M3) and sparse embeddings are computed in parallel.

**3. Qdrant search + Graphiti in parallel.** Qdrant runs 3-leg RRF fusion (or 2-leg
fallback). If `settings.graphiti_enabled`, a Graphiti graph search fires concurrently
and is RRF-merged with the Qdrant results.

**4. Rerank.** The top candidates are reranked by `infinity-reranker`
(bge-reranker-v2-m3 on GPU — gpu-01 via SSH tunnel at 172.18.0.1:7998). The reranker applies a cross-attention model that scores
each (query, chunk) pair more precisely than vector distance alone. Top 5–10 survive.

**Why not just use vector search directly?** Each retrieval signal captures something
different. Dense search finds semantically similar text. Question vectors find content
that directly answers the query. Sparse search finds keyword matches. The reranker adds
a final precision pass. Together they reduce retrieval failures significantly compared to
dense-only search.

### 3.3 Gap detection (SPEC-KB-014)

After every retrieval call, the LiteLLM hook classifies the result before injecting
chunks into context. If the result looks like a knowledge gap, a lightweight event is
fired to the portal — asynchronously, without blocking the chat response.

**Two gap types:**

| Type | Condition | Meaning |
|---|---|---|
| `hard` | Zero chunks returned | The knowledge base has nothing on this topic |
| `soft` | Chunks returned but all scores < 0.4 | Results exist but confidence is too low |

**The async event flow:**

```
Retrieval result received in LiteLLM hook
  → Classify: hard (no chunks) or soft (all scores < 0.4)?
  → If gap: fire-and-forget POST /internal/v1/gap-events (portal-api, internal token)
    { org_id, query, gap_type, nearest_kb_slug, scores }
  → Continue: inject whatever chunks exist (or skip injection if hard gap)
```

The event POST is non-blocking. If the portal is unreachable, the gap is silently
dropped — retrieval quality is unaffected.

**Storage:** `portal_retrieval_gaps` table in PostgreSQL, with `org_id`, `query`,
`gap_type`, `nearest_kb_slug` (populated when a soft gap has a closest matching KB),
`scores`, and a `created_at` timestamp. Rows are retained for 90 days and then
automatically purged.

**The `/app/gaps` dashboard** (admin-only):

- Grouped table of recent gap queries, filterable by gap type (hard/soft) and period (default 30d)
- Each row shows the user's query and whether it was a hard or soft gap
- **Action buttons per row:**
  - Soft gap (nearest KB known): PlusCircle icon → navigates directly to that KB's editor
  - Hard gap (no KB): BookOpen icon → opens an inline KB picker select; selecting a KB navigates to its editor
- Summary card on the knowledge index page shows gap count for the last 7 days
- KB detail page shows a 7d gap count metric tile per KB

The intended workflow: an admin reviews the gaps dashboard periodically, identifies
recurring unanswered questions, and uses the action button to jump directly into the KB
editor to write or update the relevant content.

### 3.4 The explicit path: klai-knowledge-mcp

LibreChat tenants also have access to `klai-knowledge-mcp` as an MCP tool server. Unlike
the hook (which runs automatically for every message), MCP tools are explicitly invoked
by the model when it decides to save something to the user's personal knowledge base.

The V1 tool is `save_to_personal_kb`. The model can save text with a title, tags, and
an `assertion_mode` label (`factual`, `belief`, `hypothesis`, `procedural`, `quoted`,
`unknown`).

**Write path:** MCP server → `POST /ingest/v1/document` (knowledge-ingest) → Qdrant
`klai_knowledge` collection with `kb_slug="personal"` and `user_id`. Personal saves are
immediately searchable via the `scope=both` path in the LiteLLM hook.

---

## Part 4: Tenant provisioning (how a new org gets knowledge)

When a new tenant is provisioned:

1. **Zitadel** — OIDC client created
2. **LiteLLM team key** — scoped key with `metadata: {org_id}` and allowed models
   `["klai-primary", "klai-fast", "klai-large"]`
3. **LibreChat container** — started with the team key and org slug in its `.env`
4. **Personal KB** — klai-docs creates a Gitea repo and registers the webhook with
   `knowledge-ingest` so new pages are automatically indexed

The team key is the thread that connects provisioning to retrieval: it carries `org_id`,
which scopes every Qdrant search to the correct tenant's content.

---

## Part 5: Klai Focus

Klai Focus (research-api) is a personal research assistant where users upload documents
into notebooks. Focus shares the same retrieval-api and Qdrant infrastructure as the org
knowledge base, but stores its vectors in a **separate Qdrant collection** (`klai_focus`)
rather than `klai_knowledge`.

Focus vectors were previously stored in PostgreSQL with pgvector. The pgvector embedding
column was dropped on 2026-03-26 (migration `0003_drop_embedding_column`) — vectors now
live in Qdrant.

**Ingest:**
```
User uploads to Focus notebook
  → docling-serve extracts text (PDF, DOCX, HTML, URLs)
  → TEI embeds chunks (BGE-M3 dense, 1024-dim)
  → stored in Qdrant klai_focus collection
  → PostgreSQL research.chunks tracks metadata (no embedding column)
```

**Three chat modes** (all live):

| Mode | What it searches | Use case |
|---|---|---|
| `narrow` | Notebook only (`scope=notebook` via retrieval-api) | Search your own uploads |
| `broad` | Notebook + org KB (`scope=broad` via retrieval-api) | Search uploads and company knowledge together |
| `web` | Notebook + SearXNG live web search | Search uploads and the web |

In `broad` mode, retrieval-api runs parallel Qdrant searches on both `klai_focus` and
`klai_knowledge`, merges the results by score, and returns combined chunks. The
research-api then picks the appropriate system prompt based on whether KB results were
actually found.

**Web mode** uses SearXNG (self-hosted search) to find URLs, fetches and parses them via
**docling-serve** (`convert_url`), embeds the text on-the-fly, and combines with notebook chunks. Whether web mode works
well in practice depends on SearXNG's availability and docling's ability to extract clean
text from the fetched pages.

---

## Service map (core-01 + gpu-01)

| Service | Role |
|---|---|
| `knowledge-ingest` | Ingest pipeline: chunk, embed, enqueue enrichment, graph ingestion |
| `retrieval-api` | Retrieval endpoint (SPEC-KB-008) — replaces deprecated /knowledge/v1/retrieve |
| `procrastinate-worker` | Async enrichment worker (queues: enrich-interactive, enrich-bulk, graphiti-bulk) |
| `qdrant` | Vector store — `klai_knowledge` collection, 3 named vectors per chunk |
| `tei` | TEI (text-embeddings-inference) — BGE-M3 dense embeddings (1024-dim, OpenAI-compatible `/v1/embeddings`) — gpu-01 via SSH tunnel at 172.18.0.1:7997 |
| `bge-m3-sparse` | BGE-M3 sparse embeddings sidecar (FlagEmbedding) — gpu-01 via SSH tunnel at 172.18.0.1:8001 |
| `infinity-reranker` | bge-reranker-v2-m3 on GPU (gpu-01 via SSH tunnel at 172.18.0.1:7998) — shared with LibreChat webSearch |
| `litellm` | LLM proxy + KlaiKnowledgeHook pre-call filter |
| `librechat-{slug}` | Per-tenant chat container |
| `gitea` | Git store for human-authored KB pages |
| `falkordb` | Graph database for Graphiti knowledge graph |
| `klai-knowledge-mcp` | MCP server for explicit knowledge saves from LibreChat |
| `klai-connector` | External source sync: GitHub repos, web crawls — uses Unstructured.io for binary parsing |
| `docling-serve` | Document parsing voor Focus (uploads + URL-fetching in web mode) |
| `research-api` | Klai Focus backend — Qdrant `klai_focus` collection |

---

## GPU inference services — why three separate services exist

All three services run on **gpu-01** and are tunneled to **core-01** via autossh. They serve
completely different roles and cannot be consolidated — each requires a different model,
a different API, and is optimised for a different task.

| Service | Port | Tunnel endpoint | Model | API | Role |
|---|---|---|---|---|---|
| **TEI** (text-embeddings-inference) | 7997 | `172.18.0.1:7997` | BAAI/bge-m3 | `/v1/embeddings` (OpenAI-compatible) | Dense vector embeddings (1024-dim float) |
| **Infinity** (reranker) | 7998 | `172.18.0.1:7998` | bge-reranker-v2-m3 | `/v1/rerank` | Cross-encoder reranking — scores (query, doc) pairs jointly |
| **bge-m3-sparse** | 8001 | `172.18.0.1:8001` | BAAI/bge-m3 (FlagEmbedding) | `/embed_sparse_batch` | Sparse vector embeddings — SPLADE-style (token-index, weight) pairs |

**Why TEI and Infinity are separate:**

TEI (embedding) and Infinity (reranking) represent two different stages in the retrieval
pipeline, and the split is a deliberate latency/quality trade-off:

- TEI runs over the **entire dataset** on every search. You cannot afford a slow model here —
  it needs to embed query and chunks independently so Qdrant can do fast approximate nearest-
  neighbour search across hundreds of thousands of vectors.
- Infinity is a **cross-encoder**: it sees query and document together in a single forward pass,
  which is much more accurate but also proportionally slower. It is only applied to the top-20
  candidates that TEI already selected — never to the full dataset.

The reason we use both instead of just one: TEI alone misses nuance (a chunk can look
semantically similar but not actually answer the question). Infinity alone is too slow to run
across the full index. Two-stage is the only way to get both recall and precision within an
acceptable latency budget.

Both happen to use the OpenAI `/v1/embeddings` API format for their inputs, which caused
historical confusion in code comments — but they serve completely different purposes.

**Why TEI and bge-m3-sparse are separate:**
- TEI produces **dense** vectors (all 1024 dimensions have values) — good for semantic similarity.
- bge-m3-sparse produces **sparse** vectors (only a few hundred token indices have non-zero weights)
  — good for exact keyword matching (BM25-style). These complement each other in hybrid search.
- The dense and sparse models are both based on BAAI/bge-m3 but require different inference code
  (TEI for dense, FlagEmbedding for sparse), hence separate services.

**Config variable names:**
- `TEI_URL` (env) / `tei_url` (Python settings) → `http://172.18.0.1:7997`
- `INFINITY_RERANKER_URL` (env) / `infinity_reranker_url` (Python settings) → `http://172.18.0.1:7998`
- `SPARSE_SIDECAR_URL` (env) / `sparse_sidecar_url` (Python settings) → `http://172.18.0.1:8001`

---

## What is not yet built

| Feature | Why it's deferred |
|---|---|
| MCP read tools (semantic search via tool call) | V1 covers saves only |
| Helpdesk transcript adapter | Interface with whisper-server not decided |
| Assertion mode active in retrieval | See research below |
| ~~Content profile chunk sizes wired to chunker~~ | Fixed 2026-03-31: `chunk_tokens_max` from profile now passed to `chunker.py` (`tokens * 4` → chars) |
| Docling migration voor binary parsing in klai-connector | Unstructured.io huidig; Docling sneller en nauwkeuriger voor digitale PDFs. Usecase verschilt: Unstructured beter voor gescande/handgeschreven docs. Tracked voor evaluatie. |

---

## Part 6: Assertion modes — research findings and implementation guidance

> Compiled: 2026-03-28. Updated: 2026-03-30.
> Status: Research complete. Recommendation: **start with flat weights, measure, then tune.**
> Full research synthesis: [Evidence-Weighted Knowledge Retrieval](../research/README.md)
> Detailed weights analysis: [Assertion Mode Weights](../research/assertion-modes/assertion-mode-weights.md)

### What assertion modes are

The knowledge architecture defines six assertion modes per artifact: `factual`,
`procedural`, `quoted`, `belief`, `hypothesis`, `unknown`. These are one of three metadata
axes (alongside provenance type and synthesis depth) described in the architecture doc §3.2.

### Current implementation status

| Component | Status |
|---|---|
| DB schema (`knowledge.artifacts.assertion_mode`, enum) | Done |
| Ingest parsing (from YAML frontmatter, default `unknown`) | Done |
| Valid set: `{factual, belief, hypothesis, procedural, quoted, unknown}` | Done |
| Migration dict: handles old → new name variants in frontmatter | Done |
| Frontend UI (add-connector page assertion mode multi-select) | Done |
| HyPE classification | Not built — enrichment does not classify assertion mode |
| Qdrant payload | Not included — cannot filter or weight by assertion mode |
| Retrieval API response | Not returned — stripped from results |
| Reranker weighting | Not built |

**Summary:** Storage and ingest parsing exist. The entire consumption side (retrieval,
reranking, generation context) ignores assertion mode.

### Industry landscape

No production RAG system tags chunks with epistemic type labels and uses them as a
retrieval signal. The building blocks exist separately:

| Building block | What it does | Maturity |
|---|---|---|
| Certainty classifiers | Classify scholarly assertions into 3 certainty categories | 89.2% accuracy on biomedical text (Prieto et al. 2020) |
| Nanopublications | RDF-based atomic assertions with provenance graphs | 10M+ published, biomedical domain |
| TrustGraph | Per-triple confidence via RDF-star reification | Production platform, open source |
| RAG+ | Separates declarative from procedural knowledge into two corpora | Research prototype (arXiv 2025) |
| MDKeyChunker | Single LLM call extracts 7 metadata fields per chunk | March 2026 preprint |

Nobody has assembled epistemic type labeling + metadata-enriched retrieval +
confidence-weighted reranking into a single production pipeline.

### Evidence: metadata enrichment improves retrieval

These studies demonstrate that adding structured metadata to chunks improves RAG retrieval
quality. None use epistemic type labels specifically.

| Study | What metadata | Key result |
|---|---|---|
| Mishra et al. 2025 | Content type (procedural/conceptual/reference/warning/example) | +12.5% precision on AWS S3 docs |
| Multi-Meta-RAG (ICTERI 2024) | Source publication + date as pre-filters | +8-26% answer accuracy (PaLM +25.6%, GPT-4 +8.2%) |
| Anthropic Contextual Retrieval 2024 | Chunk-level context summary prepended before embedding | -67% top-20 retrieval failure rate |
| Financial QA (arXiv 2025) | Thematic clusters, entities, anticipated questions | +35% F1; but chunk expansion increased hallucination from 14.7% to 22.2% |
| Legal RAG (arXiv 2026) | Document name, jurisdiction, local summaries | -84% document mismatch on MAUD; minimal improvement on PrivacyQA |
| MEGA-RAG (Frontiers 2025) | Multi-source provenance + conflict detection | 35-60% error reduction in hybrid architectures |
| SELF-RAG (ICLR 2024 Oral) | Runtime relevance/support assessment via reflection tokens | +22-30% citation precision |

### Evidence gap: epistemic type labels specifically

No study tests fact/hypothesis/opinion labels on chunks and measures their impact on
retrieval quality. The closest work:

- **Mishra et al.** classifies content *type* (tutorial, reference, FAQ) — structural, not
  epistemic. Does not distinguish "stated as fact" from "stated as hypothesis."
- **SELF-RAG** performs epistemic assessment at runtime (is this passage relevant? does it
  support the claim?) but does not pre-label chunks.
- **Medical RAG literature** shows corpus-level source type selection matters (clinical
  guidelines outperform abstracts) but operates at document level, not chunk level.
- **Legal RAG** uses document metadata (parties, jurisdiction) which partially overlaps
  with epistemic context but is structural.

**The gap:** Strong evidence that metadata enrichment improves retrieval. No evidence that
*epistemic type* metadata specifically improves retrieval over other metadata types. The
hypothesis is plausible by analogy but untested.

### Classification feasibility

**Human agreement by taxonomy size:**

| Categories | Agreement | Source |
|---|---|---|
| 5 levels (absolute/high/moderate/low/uncertain) | ~67% (kappa 0.51) | Rubin 2007, NYT corpus |
| 3 levels | ~91% | Prieto et al. 2020, biomedical corpus |
| 2 levels (certain/uncertain) | ~84% | Prieto et al. 2020 |

Five categories is too fine-grained for reliable classification. Three is workable.

**LLM zero-shot on fact vs. opinion (61,514 claims, 30 languages):**

| Model | Facts correct | Opinions correct |
|---|---|---|
| GPT-4o | 65% | 79% |
| LLaMA 3.1 8B | 57% | 67% |
| Mixtral 8x7B | 43% | 61% |

Facts are systematically harder to classify than opinions — the opposite of what you want
for safety (misclassifying a hypothesis as a fact is the most dangerous error).

Note: this study measured fact-*checking* (is this claim true?), not fact-*typing* (is this
stated as a fact or as a hypothesis?). Fact-typing relies on linguistic markers and is more
tractable, but no benchmark exists for business documents.

**Fine-tuned models** outperform zero-shot by 5-69 percentage points depending on task
granularity. Prieto's 89.2% was achieved with a modest 5-layer neural network on 3,221
examples.

**Practical path if we proceed:**

1. Use 3 categories: `assertion` (factual + quoted), `speculation` (hypothesis + belief),
   `procedure` (procedural). Human agreement is dramatically better at 3 than 5.
2. Piggyback on HyPE — the enrichment step already calls an LLM per chunk. MDKeyChunker
   (2026) demonstrates multi-field extraction in a single call. Adding assertion mode as an
   additional field is marginal prompt engineering.
3. Confidence gating — only apply the label when classifier confidence exceeds a threshold
   (e.g. 85%). Below that, default to unclassified.

### Error asymmetry

| Misclassification | Risk |
|---|---|
| Hypothesis labeled as fact | **HIGH** — user trusts unverified information |
| Fact labeled as hypothesis | **MEDIUM** — user seeks unnecessary verification |
| Procedure labeled as fact | **MEDIUM** — user misses that action is required |

Conservative strategy: when uncertain, label toward `speculation`. A false conservative
label degrades user experience slightly. A false confident label degrades trust
fundamentally.

### User-facing display: evidence says no

- **CHI 2024:** Showing AI confidence does NOT improve task accuracy. Users adjust behavior
  based on confidence but don't make better decisions.
- **ACL 2024 (Zhou et al.):** Overconfident epistemic markers cause lasting trust damage
  that persists even after the model returns to being well-calibrated.
- **ACL 2025 (Liu et al.):** LLMs cannot reliably produce calibrated epistemic markers.
  "Fairly confident" does not correlate with actual correctness across domains.

If assertion modes are activated, they should be **internal ranking signals only**, not
exposed to end users as labels.

### Research conclusions (2026-03-30)

The [evidence-weighted knowledge research programme](../research/README.md) investigated four scoring dimensions (content type, assertion mode, temporal decay, cross-source corroboration) across 50+ papers. Key conclusions for assertion modes:

1. **Evidence-weighted retrieval works in principle.** RA-RAG: +51% accuracy; TREC Health: +60% MAP; BayesRAG: +20% Recall@20. But these studies use source credibility or corroboration — not epistemic type labels. The concept of assertion mode as a retrieval signal is **novel and untested**.

2. **Start with flat weights (all 1.00).** The equal-weighting literature (Einhorn & Hogarth 1975, Graefe 2013) strongly shows flat weights outperform intuitive expert weights when empirical calibration data doesn't exist. See [Assertion Mode Weights](../research/assertion-modes/assertion-mode-weights.md).

3. **Maximum safe weight spread: 0.20** for an ~85% accurate classifier. Wider spreads cause more harm from misclassification than benefit from correct classification. The previously proposed 0.30 spread (in the implementation plan) was revised to 0.10 based on the weights research.

4. **Multiplicative compounding risk.** With 4 scoring dimensions at ~85% accuracy each, 48% of chunks will have at least one misclassified dimension. This is why assertion mode weights should start flat and only be activated after empirical validation.

5. **Never show to users.** CHI 2024: confidence indicators don't improve task accuracy. ACL 2024: overconfident epistemic markers cause lasting trust damage. ACL 2025: LLMs can't produce calibrated epistemic markers. Assertion mode is an **internal ranking signal only**.

6. **Corroboration scoring: deferred.** Three prerequisites must be met first: near-duplicate detection (SemHash), source-level grouping (`source_document_id`), and entity resolution validation (>90% precision, >85% recall). See [Corroboration Scoring](../research/corroboration/corroboration-scoring.md).

**Evaluation protocol before activating weights:** 150 test queries (50 curated + 100 RAGAS-synthetic), Context Precision + NDCG@10 + Faithfulness metrics, Wilcoxon signed-rank paired tests, shadow scoring before cutover. See [RAG Evaluation Framework](../research/evaluation/rag-evaluation-framework.md).

### Remaining open questions

1. **Does assertion mode improve retrieval on Klai's data?** Still untested. The research confirms the concept is plausible by analogy but the specific combination is novel. Needs the A/B evaluation described above.

2. **Does the 3-category taxonomy fit Klai's content mix?** Needs a 200-sample evaluation.
   Hand-label 200 chunks, measure inter-annotator agreement. If below 80%, the categories
   need refinement.

3. **Can the HyPE prompt classify assertion mode reliably?** Needs prompt engineering +
   evaluation. MDKeyChunker (2026) demonstrates multi-field extraction is feasible.

4. **MCP taxonomy alignment** is needed regardless of the weights decision. The current `{fact,
   claim, note}` → `{factual, procedural, quoted, belief, hypothesis}` gap creates data
   quality issues. The 3-category grouping for retrieval (`assertion`, `speculation`, `procedure`) should inform the MCP mapping.

### Sources

**Metadata enrichment in RAG:**
- Mishra et al. (2025). Enterprise Knowledge Retrieval with LLM-Generated Metadata. [emergentmind.com](https://www.emergentmind.com/topics/meta-rag-framework)
- Poliakov & Shvai (2024). Multi-Meta-RAG. ICTERI 2024. [arXiv:2406.13213](https://arxiv.org/abs/2406.13213)
- Anthropic (2024). Contextual Retrieval. [anthropic.com](https://www.anthropic.com/news/contextual-retrieval)
- Metadata-Driven RAG for Financial QA (2025). [arXiv:2510.24402](https://arxiv.org/html/2510.24402v1)
- Maniyar et al. (2026). Legal RAG with Metadata-Enriched Pipelines. [arXiv:2603.19251](https://arxiv.org/abs/2603.19251)
- Xu et al. (2025). MEGA-RAG. Frontiers in Public Health. [frontiersin.org](https://www.frontiersin.org/journals/public-health/articles/10.3389/fpubh.2025.1635381/full)
- Asai et al. (2024). Self-RAG. ICLR 2024 Oral. [arXiv:2310.11511](https://arxiv.org/abs/2310.11511)

**Epistemic classification:**
- Rubin (2007). Epistemic Modality Annotation. NAACL 2007. [ACL Anthology](https://aclanthology.org/N07-2036/)
- Prieto et al. (2020). Classification of Scholarly Assertion Certainty. [PeerJ](https://peerj.com/articles/8871/)
- Aicher et al. (2025). Facts are Harder Than Opinions. [arXiv:2506.03655](https://arxiv.org/abs/2506.03655)
- MDKeyChunker (2026). Single-Call LLM Enrichment for RAG. [arXiv:2603.23533](https://arxiv.org/abs/2603.23533)

**User-facing confidence:**
- CHI 2024. Human Self-Confidence Calibration in AI-Assisted Decision Making. [ACM DL](https://dl.acm.org/doi/10.1145/3613904.3642780)
- Zhou et al. (2024). Overconfident Epistemic Markers. ACL 2024. [ACL Anthology](https://aclanthology.org/2024.acl-long.198.pdf)
- Liu et al. (2025). Epistemic Markers in Confidence Estimation. ACL 2025. [ACL Anthology](https://aclanthology.org/2025.acl-short.18/)

**Epistemic building blocks:**
- Nanopublication Guidelines. [nanopub.net](https://nanopub.net/guidelines/working_draft/)
- TrustGraph. [trustgraph.ai](https://trustgraph.ai/guides/key-concepts/context-graphs/)
- Gilda et al. (2026). First Principles Framework for Epistemic Status Tracking. [arXiv:2601.21116](https://arxiv.org/abs/2601.21116)
