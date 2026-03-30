# Knowledge System — Verified Implementation Details

> Last verified against code: 2026-03-27
> Key source files: klai-knowledge-ingest/knowledge_ingest/, klai-retrieval-api/retrieval_api/

## Ingest pipeline (two phases)

**Phase 1 (synchronous, seconds):**
1. Content profile selected by `content_type` (see content_profiles.py)
2. docling-serve chunks the document (token-aware, structure-preserving)
3. TEI embeds raw chunks → upsert to Qdrant as `vector_chunk` (document immediately searchable)
4. Procrastinate enrichment task enqueued (non-blocking): `enrich-interactive` for user saves, `enrich-bulk` for connectors
5. Graphiti episode fired as `asyncio.create_task` (fire-and-forget, gated by `settings.graphiti_enabled`)

**Phase 2 (async, Procrastinate worker):**
1. Per-org gate: `org_config.is_enrichment_enabled()` — can skip enrichment per org
2. LLM call (klai-primary) → `{"context_prefix": "...", "questions": [...]}`  
   Context strategy from profile: first_n / rolling_window / front_matter / most_recent
3. Dense re-embed enriched text (context_prefix + chunk) → replaces `vector_chunk`
4. Sparse embed via bge-m3-sparse sidecar → `vector_sparse`
5. If HyPE enabled: embed joined questions string → `vector_questions`
6. Full upsert with up to 3 named vectors

## Qdrant schema (klai_knowledge)

- **`vector_chunk`**: always present after enrichment
- **`vector_questions`**: only if HyPE enabled for this chunk
- **`vector_sparse`**: only if sparse embedding succeeded
- Tenant isolation: `org_id` payload index with `is_tenant: true`, mandatory filter on every search
- Visibility: stored as payload field but NOT enforced at retrieval time (known gap, no issue filed yet)

## Retrieval-api scopes (RetrieveRequest.scope)

- `org` — all KBs in org (filter: org_id only)
- `personal` — user's personal KB (filter: org_id + user_id)
- `both` — personal + org
- `notebook` — Focus notebook in klai_focus collection
- `broad` — Focus + org KB (parallel Qdrant searches, merged by score)

**Important:** scope=org searches ALL KBs in the org. There is NO per-KB filtering at retrieval time. `kb_slugs` existed in the deprecated endpoint but is not in the new retrieval-api request model.

## Retrieval pipeline (retrieval-api)

1. Coreference resolution (expands pronouns using conversation history)
2. Dense + sparse embed in parallel
3. Qdrant hybrid search (3-leg RRF: vector_chunk + vector_questions + vector_sparse; 2-leg fallback)
4. Graphiti graph search in parallel (gated by settings.graphiti_enabled), RRF-merged with Qdrant
5. Rerank via infinity-reranker (bge-reranker-v2-m3 CPU)

## LiteLLM hook (KlaiKnowledgeHook)

- Fires two parallel retrieval calls when user_id present: scope=org + scope=personal
- Personal chunks rendered first under `[Persoonlijke kennis]` header
- 2s hard timeout; all failures degrade gracefully (request passes through unchanged)
- org_id read from LiteLLM team key metadata (not from request)
- user_id read from LiteLLM `data["user"]` field (set by LibreChat if present)

## Gitea webhook org_id convention

- Gitea org description field = Zitadel org ID (set at provisioning time)
- Repo naming: `org-{slug}/{kb_slug}`
- Webhook handler reads org_id from Gitea org description via API lookup

## Auto-save debounce

- klai-portal editor auto-saves every 1.5s of inactivity (scheduleSave, $kbSlug.tsx:349-351)
- Each save → Gitea commit → webhook → full ingest cycle including Procrastinate enqueue
- No content-hash dedup at ingest level yet → potential for many enrichment tasks per session
- Fix: Procrastinate queueing_lock per (org_id, kb_slug, path) — see issue #50

## Focus

- Vectors in Qdrant klai_focus collection (migrated from pgvector 2026-03-26)
- research-api calls retrieval-api via retrieval_client.py for all retrieval
- Three modes: narrow (notebook only), broad (notebook + org KB), web (narrow + SearXNG)
- Broad mode is LIVE (not deferred)
- Web mode is LIVE but reliability depends on SearXNG availability

## Known gaps (not yet in issues)

- KB visibility not enforced at retrieval (all org KBs always searched together)
- Per-KB scoping removed when moving from deprecated endpoint to retrieval-api

## Personal KB indexing (implemented 2026-03-27)

- `save_personal_knowledge` MCP posts directly to `/ingest/v1/document` with `user_id` set
- `ingest_document` passes `user_id` to `upsert_chunks` → stored in Qdrant point payload
- `user_id` payload index added to `ensure_collection()` (idempotent, applies to existing collections)
- LiteLLM hook uses `scope="both"` + `user_id` → retrieval-api returns personal chunks filtered by user_id
- Isolation: personal chunks only returned when requesting `user_id` matches stored `user_id`
