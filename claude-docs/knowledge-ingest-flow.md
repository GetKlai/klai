# Knowledge Ingestion & Retrieval: How It Works

> Engineering reference for the running system on core-01.
> Verified against `deploy/knowledge-ingest/knowledge_ingest/` and `retrieval-api/` — March 2026 (updated 2026-03-27).
>
> For the research backing these design decisions, see
> [klai-mono/claude-docs/knowledge-system-fundamentals.md](../../../klai-mono/claude-docs/knowledge-system-fundamentals.md).

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

**Web crawls (live):** uses Crawl4AI with sitemap awareness for deep site crawls.

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

| content_type | HyPE | Context strategy | Chunk size |
|---|---|---|---|
| `kb_article` | Conditional (synthesis_depth ≤ 1) | first_n | 300–500 tokens |
| `pdf_document` | Always | front_matter | 400–800 tokens |
| `meeting_transcript` | Always | rolling_window | 150–400 tokens |
| `1on1_transcript` | Always | rolling_window | 100–300 tokens |
| `email_thread` | Conditional (depth ≤ 1) | most_recent | 200–500 tokens |
| `unknown` | Never | first_n | 500 tokens |

If no `content_type` is set, the pipeline uses `unknown` — no enrichment, basic chunking.

### Phase 1: Immediate (synchronous)

**Step 1 — Parse and chunk.** The raw file is sent to **docling-serve**, a self-hosted
wrapper around Docling's `HybridChunker`. It splits the document at natural boundaries
(headings, paragraphs, tables) into chunks of the size defined by the content profile.
Chunking is token-aware rather than character-aware, so chunks don't cut off mid-sentence.

**Step 2 — Embed and store raw vectors.** Each chunk is sent to **TEI** (Text Embeddings
Inference) running **BGE-M3** to produce a 1024-dimensional dense vector. These raw
embeddings are immediately upserted into Qdrant as `vector_chunk`. The document is now
searchable.

**Step 3 — Enqueue enrichment.** The main request enqueues an async task via Procrastinate
(a PostgreSQL-backed task queue) and returns to the caller. Two queues separate
priorities: `enrich-interactive` for user-triggered saves (should feel fast), and
`enrich-bulk` for background syncs (can wait).

**Step 4 — Feed the knowledge graph (fire-and-forget).** If `settings.graphiti_enabled`
is on, an `asyncio.create_task()` fires the document to Graphiti/FalkorDB as a "episode".
The main request doesn't wait for this and doesn't fail if it errors. Graphiti extracts
entities and relationships from the text and stores them in a graph database (FalkorDB),
enabling relationship-based queries that pure vector search can't do (e.g. "what does
person X work on, and what decisions are connected to those projects?").

### Phase 2: Async enrichment (Procrastinate worker)

The enrichment worker picks up tasks from the queue and runs the following for each
chunk in the document.

**Why enrich at all?** Raw text retrieval has two systematic problems: (1) chunks lose
context when isolated ("the meeting discussed three options" — which meeting? which
options?), and (2) users search with questions while documents contain answers (vocabulary
gap). Enrichment addresses both.

**Step A — Contextual Retrieval prefix.**
A single LLM call (via the LiteLLM proxy, model `klai-primary`) takes each chunk plus
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
(bge-reranker-v2-m3 on CPU). The reranker applies a cross-attention model that scores
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
an `assertion_mode` label (`factual`, `procedural`, `belief`, `hypothesis`, `quoted`).

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
  → docling-serve extracts text
  → TEI embeds chunks (BGE-M3 dense)
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

**Web mode** uses SearXNG (self-hosted search) to find URLs, fetches them via docling,
embeds the text on-the-fly, and combines with notebook chunks. Whether web mode works
well in practice depends on SearXNG's availability and docling's ability to extract clean
text from the fetched pages.

---

## Service map (deployed on core-01)

| Service | Role |
|---|---|
| `knowledge-ingest` | Ingest pipeline: parse, chunk, embed, enqueue enrichment |
| `retrieval-api` | Retrieval endpoint (SPEC-KB-008) — replaces deprecated /knowledge/v1/retrieve |
| `procrastinate-worker` | Async enrichment worker (queues: enrich-interactive, enrich-bulk) |
| `qdrant` | Vector store — `klai_knowledge` collection, 3 named vectors per chunk |
| `tei` | BGE-M3 dense embeddings (1024-dim) |
| `bge-m3-sparse` | BGE-M3 sparse (SPLADE-style) embeddings sidecar |
| `docling-serve` | Document parsing and token-aware chunking |
| `infinity-reranker` | bge-reranker-v2-m3 on CPU — shared with LibreChat webSearch |
| `litellm` | LLM proxy + KlaiKnowledgeHook pre-call filter |
| `librechat-{slug}` | Per-tenant chat container |
| `gitea` | Git store for human-authored KB pages |
| `falkordb` | Graph database for Graphiti knowledge graph |
| `klai-knowledge-mcp` | MCP server for explicit knowledge saves from LibreChat |
| `klai-connector` | External source sync: GitHub repos, web crawls |
| `research-api` | Klai Focus backend — Qdrant `klai_focus` collection |

---

## What is not yet built

| Feature | Why it's deferred |
|---|---|
| MCP read tools (semantic search via tool call) | V1 covers saves only |
| Helpdesk transcript adapter | Interface with whisper-server not decided |
