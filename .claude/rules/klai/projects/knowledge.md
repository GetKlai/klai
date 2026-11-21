---
paths:
  - "klai-knowledge-ingest/**"
  - "klai-connector/**"
  - "klai-retrieval-api/**"
  - "klai-portal/backend/app/services/answer_compose.py"
  - "klai-portal/backend/app/services/widget_write.py"
  - "klai-libs/chat-prompts/**"
---
# Knowledge Domain Patterns

## asyncpg tenant GUCs are connection-local (HIGH)

`app.current_org_id` belongs to one PostgreSQL connection, not an asyncpg pool. Code inside `tenant_scoped_connection(org_id)` must issue SQL through the yielded `conn`, or open its own scoped connection at the leaf. Holding one scoped connection while a nested helper calls `get_pool()` or `pool.acquire()` does not carry the GUC and fails against FORCE-RLS tables.

Review every new knowledge-schema SQL path for connection threading. The current contract is implemented in `klai-knowledge-ingest/knowledge_ingest/db.py` and exercised by `tests/test_db_rls_wiring.py`.

## Receiver changes require caller changes (CRIT)

When an internal endpoint adds a required header, field, scope, or identity rule, find and update every active caller in the same change. Receiver-only tests prove only the receiver. For `/retrieve`, callers must send the exact identity headers expected by retrieval-api, including `X-Caller-Service` where required.

Search the whole repository for endpoint literals and URL settings, then keep both:

- caller-side tests that assert the outbound headers/body;
- receiver-side contract tests using a real caller-shaped request.

Do not silently convert authentication or contract failures into empty knowledge context. Preserve explicit logging and user-visible degradation where the calling surface provides it.

## Shared adapter fields are cross-service contracts (HIGH)

Before deleting or moving shared adapter types or fields such as `DocumentRef`,
`ImageRef`, or `content_fingerprint`, search every producer and consumer across
connector and knowledge-ingest. A field's original purpose does not establish
its current ownership: sync, image upload, deduplication, and ingest paths may
all depend on the same shape. Change producer and consumer tests together.

## chat system prompt — two locations, never duplicate (HIGH)

System-prompt content for chat lives in **two** canonical files,
never two copies of the same content. Modifying one without considering
the other has been the recurring failure mode of
SPEC-RAG-MULTILINGUAL-CHAT-001.

| Location | What lives here | Used by |
|---|---|---|
| `klai-libs/chat-prompts/klai_chat_prompts/__init__.py` (`GROUNDED_CHAT_SYSTEM_PROMPT`) | The shared multilingual base prompt: detect substantive language, three guards, no translator disclaimers, [n] citation rules | imported by path B (`knowledge_prompts.py`) |
| `klai-portal/backend/app/services/retrieval_context.py::_build_system_prompt` | Adds the chunks-as-context block to the shared base | path B — Widget + Partner API |

**Why this matters:** chat traffic in Klai used to take concurrent paths.
Today there is one: (B) portal-api `/partner/v1/chat/completions` →
LiteLLM → Mistral, which also serves the internal chat (LibreChat) since
13 October 2026. Path A (LibreChat → LiteLLM hook) was removed on
21 October 2026; path C (retrieval-api `POST /chat`, with its own
`synthesis.py` prompt wiring) was removed in November 2026 after 30 days
without a call. A new chat surface is a new path: import the prompt, never
copy it. v1.0/v1.1 of the SPEC landed
multilingual on B + C only, leaving A still hardcoded NL — that
mistake cost a full audit cycle to detect.

**Prevention:**
- Before changing chat behaviour: ask which of the paths you're
  touching. If the answer isn't immediately obvious from the file
  name, you don't yet understand the change — re-read this entry.
- Never inline a copy of `GROUNDED_CHAT_SYSTEM_PROMPT` content in a
  new chat surface. Import `from klai_chat_prompts import
  GROUNDED_CHAT_SYSTEM_PROMPT`.
- Never duplicate a Klai Kennisbank / ANTWOORDFORMAAT / Klai Templates
  / KB-unavailable block outside `klai-libs/chat-prompts`.
- A module lives where its only consumer is. Move it to `klai-libs/chat-prompts`
  only in the PR that adds the second consumer: the vendored-copy pattern doubles
  every line, never pay that for a speculative consumer. A new dependency in the
  LiteLLM container is class L (needs Mark's yes).
- The CI lint `scripts/lint-no-duplicate-chat-prompt.sh` enforces both
  rules. It checks two anchor sets — the GROUNDED prompt anchors and the
  KB prefix anchors (both canonical: `klai-libs/chat-prompts`).
- Do NOT re-introduce explicit language allow-lists in either
  location ("if the user writes Dutch", "if the user writes
  English"). The base prompt detects any language the LLM
  understands; the hook prefix and the chunks-context block must
  remain language-neutral. Hand-curated language lists break every
  non-listed language.
- bge-m3 retrieval is already polyglot — never add a query-translation
  layer ahead of retrieval.

**Detection in production:** `event:chat_synthesis_complete` events in
VictoriaLogs carry `query_language_detected`,
`response_language_detected`, `language_correctness`, and
`chunks_injected` (added 2026-07-07; `None` on call sites that cannot
determine the count). A drop in language-correctness rate is the
signal that a prompt location drifted — correlate with recent PRs.

**Coverage closed (2026-09-15):** all three paths emit
`chat_synthesis_complete` with the same field names and meanings. Path A took
longest not because it lacked a detector — `klai_conversation_language.py` has
been vendored in that container all along — but because the emit was never
written. It now logs one JSON object per line from
`klai_kb_citation_render._emit_chat_synthesis_complete`. On every path the query
side measures the visitor's own text, never the conversation target; logging our
own target under a field named `*_detected` would make the metric confirm itself.
Since paths A and C were removed, the one emitter is the portal-api function at
`klai-portal/backend/app/services/chat_prompting.py:274` (`_emit_language_correctness_log`).

**One identifier, by decision (2026-09-15):** `lingua` was removed from
klai-portal and klai-retrieval-api. It was only ever the telemetry detector,
it disagreed with the identifier that steers the prompt on most short
utterances, and it cannot reach path A at all (no sdist on PyPI, only ~170 MB
wheels). Do NOT reintroduce a second detector "as a cross-check": a second
library is a second guess, not ground truth. The independent check is the
LABELLED fixture in `klai-retrieval-api/evaluation/test_queries_cross_lingual.json`,
where the expected language is a field, not a detection.

**See:** SPEC-RAG-MULTILINGUAL-CHAT-001 (HISTORY v1.2 has the
post-merge audit that uncovered the three-paths confusion),
`docs/architecture/knowledge-ingest-flow.md` § Multilingual chat,
`docs/runbooks/multilingual-chat-observability.md`,
`klai-retrieval-api/evaluation/cross_lingual_runner.py` (cross-lingual
eval).

## connector_id must thread through the crawl pipeline (HIGH)

When a pipeline writes both Qdrant chunks and `knowledge.artifacts`, the
`source_connector_id` field must be set on every write, or connector-delete is
a silent no-op at the Qdrant + artifact layer.

**Why:** `qdrant_store.delete_connector` + `pg_store.delete_connector_artifacts` both
filter on `source_connector_id`. In the original `knowledge-ingest` crawl pipeline,
the `connector_id` arg was accepted by the Procrastinate task (`crawl_tasks.run_crawl`)
but **not forwarded** to `run_crawl_job` or `_ingest_crawl_result`. Every crawl chunk
was stored with `source_connector_id=None`. Delete UI clicks looked like they worked
(portal deleted its row), but 161 Qdrant chunks + 20 artifacts + 20 crawled_pages + 32
page_links stayed behind. A second sync then short-circuited via content-hash dedup.
Cost: half a day of SPEC-CRAWLER-005 Fase 6 debugging.

**Prevention:**
- Any pipeline that writes data scoped to a connector MUST forward `connector_id` from
  the entry point (Procrastinate task arg or HTTP route) all the way through to the
  write sites (`extra["source_connector_id"] = connector_id` in ingest.py).
- Review call chain before touching `delete_connector_*` functions: what populates
  `source_connector_id`? If anything in the chain drops the arg, the delete will be
  a silent no-op.
- SPEC-CRAWLER-005 fix is in `klai-knowledge-ingest/knowledge_ingest/adapters/crawler.py`
  (run_crawl_job + _ingest_crawl_result now accept `connector_id`) and
  `klai-knowledge-ingest/knowledge_ingest/crawl_tasks.py` (passes it through).

## Connector-delete cleanup must cover every store (HIGH)

Deleting a connector via the portal UI is a cross-service operation. Every data
layer must be cleaned or the next re-ingest hits dedup, produces zero new work,
and an audit trail of orphan rows lingers forever:

| Layer | Cleaned by |
|---|---|
| Qdrant chunks | `qdrant_store.delete_connector` (filters on `source_connector_id`) |
| FalkorDB Graphiti episodes/entities | `graph_module.delete_kb_episodes` (per-episode UUIDs from `pg_store.get_connector_episode_ids`) |
| `knowledge.artifacts` | `pg_store.delete_connector_artifacts` (filters on `extra::jsonb->>'source_connector_id'`) |
| `knowledge.crawled_pages` + `knowledge.page_links` | `pg_store.delete_connector_artifacts` (scoped via artifact URL set — added post-SPEC-CRAWLER-005) |
| `knowledge.crawl_jobs` | `pg_store.delete_connector_crawl_jobs` (filters on `config->>'connector_id'` — JSONB, no native column) |
| `connector.sync_runs` | `klai_connector_client.delete_sync_runs` (interim app-level call until SPEC-CONNECTOR-CLEANUP-001 REQ-04 lands the cross-schema FK with `ON DELETE CASCADE`) |
| Object storage `{org}/images/{kb_slug}/...` | **NOT YET CLEANED PER CONNECTOR**: image keys carry no `connector_id`, so per-connector cleanup needs an artifact↔image-key tracking table (separate SPEC). KB delete (`delete_kb_route`) and organisation deprovisioning (`_delete_kb_images`) delete the whole `{org}/images/{kb_slug}/` or `{org}/images/` prefix through `ImageStore.delete_images` (`klai-libs/image-storage/klai_image_storage/storage.py:176-205`), but a partial KB still grows orphan images proportionally to the deleted connector, and deleting a single document does not remove its images. |

**Prevention:** when adding a new data layer that is connector-scoped, immediately
wire it into `delete_connector_artifacts` (or the Qdrant equivalent), add the call
to `delete_connector_route` in `klai-knowledge-ingest/knowledge_ingest/routes/ingest.py`
(or the portal-side delete handler if the data lives in another schema), AND write a
regression test that does: insert → delete connector → assert rows == 0 across all
stores.

**Audit (2026-04-30):** verified live on Voys via UI delete. Six of seven stores
auto-clean on the current code path (after the 2026-04-30 fix). Garage images
remain a known gap — see SPEC-CONNECTOR-CLEANUP-001 follow-up notes.

## Single-document delete: replaced keeps history, deleted is gone (HIGH)

A replaced version (re-ingest of changed content) stays as a closed row linked by
`superseded_by`. A deleted document must leave nothing: every single-document delete
path calls `document_purge.purge_documents(conn, org_id, head_ids)`, which closes the
active rows, walks `superseded_by` back to every version, deletes graph episodes and
Qdrant points, then hard-deletes the rows (artifacts incl. `document_text`,
`parent_chunks`, `artifact_images`, `embedding_queue`, `artifact_entities`,
`derivations`, `crawled_pages`, `page_links`). Never add a delete path that only closes
the row. The real-PostgreSQL proof is `tests/integration/test_document_purge_postgres.py`;
a new delete path gets a case there.

## Derived work belongs to the document version, not to a run (HIGH)

Enrichment (`enrich-interactive`, `enrich-bulk`) and graph (`graphiti-bulk`)
jobs carry `artifact_id` as their work item and nothing that ties them to a
sync or crawl run. A job stops only when its document version is no longer
current (the artifact is replaced or deleted) or its connector is being
deleted or is gone (`portal_connectors.state`). A new sync or crawl run never
cancels, aborts or fences them: in 2026-11 tying them to the run id lost 353
enrichment and 387 graph jobs of ~11,300, because the newer run skipped the
unchanged pages and nothing queued the work again.

A job ends in exactly one of three ways: it did the work (`succeeded`); its
document is no longer current or its connector is going away, in which case it
raises procrastinate's `JobAborted` through `enrichment_tasks._abandon` (status
`aborted`, never retried, logged with `outcome=abandoned`); or it could not do
the work or could not tell whether it may write (a failed artifact or
connector lookup included), in which case it raises, is retried, and ends
`failed` when retries run out. Never return early without having done the work.

The document version says how each product ended, through
`derived_state.set_product_state` (keys `enrichment_state` / `graph_state`,
`_reason`, `_state_at` in `artifacts.extra`): `pending` when queued, `done`,
`not_applicable` with a reason (policy skip, navigation page, switched off), or
`failed` with a reason. Work that was needed and not done is `failed` and the
job ends `failed`; an abandoned job sets nothing. A cancelled job (worker
shutdown, user abort) is always retried, whatever its attempt count, so it never
ends with the document pending. `set_product_state` is the only writer and
enforces the legal moves in one UPDATE on the current row: to `pending` from
anything (queueing is the deliberate act), and from `pending` or no state to
`done`, `failed` or `not_applicable`; everything else is refused and logged
(`derived_state_transition_refused`). Write outcomes through it, never by
merging state keys into `extra`. Every reader goes through
`derived_state.product_state`, which derives the state of rows written before
it existed and says `unknown` where the old fields cannot tell, never `done`.
Queue through `derived_state.queue_derived_work`: a defer that fails after the
row is committed marks the product `failed (queue_unavailable)` and the
request raises, because a retry of the request finds the content unchanged and
queues nothing; ingest first marks the raw text `synced` and tries every
product. The move to `failed` logs `derived_product_failed` once at
error level (alert of the same name, runbook `docs/runbooks/derived-product-failed.md`);
graph refresh holds a document that failed for a reason a new job would repeat
(`no_stored_text`, `quarantined` for the current writer build), so a re-sync
does not alert again. A stuck job is covered by a live job holding its queueing
lock when that job builds the same product of the same artifact.
Nothing scans for or re-queues failed or missing work; re-queueing is a manual,
approved run. `index_status` means only whether the version's raw text is in
the vector index (`pending` while written or rewritten by a reindex, `synced`,
`failed` when that write failed, `deleting`),
for every source alike; it says nothing about enrichment or the graph.

Every writer checks the artifact is active and its connector is active before
expensive work and again before each irreversible write (Qdrant upsert, each
graph part); a part written after the document stopped being current is
removed again before the job stops.

Run-level ownership is for crawl runs only. A crawl job carries
`resource_key` (`connector:{org}:{kb}:{connector}:{generation}`); a new crawl
run moves `knowledge.connector_resource_fences` to its generation and cancels
the older run's job on `crawl-jobs` only, and the crawler checks the fence per
page (<=5 s cache) so a running older crawl stops. Connector delete marks the
fences deleting, cancels crawl jobs by resource key, and cancels the live
enrichment and graph jobs of the connector's documents by `artifact_id`.
Cancellation uses `abort=True, delete_job=False`; never query or count the
legacy `aborting` status.

## Audit a run before it writes; a per-page verdict is not enough (CRIT)

In 2026-11 a nightly crawl that ran without its login replaced 203 pages with
versions missing 19% of their words. Logged out, the site left out protected
sections without any login marker, so every per-page guard passed, and the
run-level verdicts were computed after the pages had been written. No guard
compared a new version with the stored one; hash inequality decided everything.

- `run_crawl_job` judges the whole run in `_judge_run_before_write` before the
  first write: session lost (saved cookies and any walled page), wall-dominant
  (anonymous crawls), and a content regression against the stored text
  (`content_retention.retention`). A run that fails the audit writes nothing.
- `ingest_document` holds a version from an automated source that keeps under
  half of the stored words until the same version arrives again on the next
  run (12 to 36 hours). Uploads are never held.
- A held or failed document must be offered again: keep it out of the sync
  run's `synced_refs` and leave `crawled_pages` untouched, or the hold never ends.
- A verdict that only sets the job status is not a guard. A guard that can stop
  a write sits before the write loop, and its thresholds carry the measured
  distribution in the comment next to the constant.

**Rule:** before adding a path that replaces stored knowledge, name what
compares the new version with the stored one. Hash inequality is not a
comparison.

## Graph-first, content-second for bulk crawls (HIGH)

When a graph-lookup feeds the payload of a per-row write, build the whole graph BEFORE
the first row is written. Never interleave graph upserts with per-row ingest when the
ingest reads from that graph.

**Why:** `run_crawl_job` originally upserted each page's `knowledge.page_links` row
inside `_ingest_crawl_result`. The first page processed saw an empty graph, so
`link_graph.get_anchor_texts(P)` returned `[]`, and since Qdrant strips empty-list keys
on upsert, `anchor_texts` was silently absent for the first N pages. A post-crawl
`compute_incoming_counts + update_link_counts` pass tried to patch it up with a second
write, but that races with Procrastinate enrichment (which deletes + re-inserts chunks
from `extra_payload`) and the repair work gets thrown away. Net result on the Voys
support smoketest: 0 of 167 crawl chunks had `anchor_texts` or `links_to`. Cost: an
entire SPEC (SPEC-CRAWLER-005) to untangle.

**Prevention:** Two-phase `run_crawl_job` — `_build_link_graph(results, ...)` first,
per-page ingest second. Think of graph state as an invariant that must hold before
any row that reads from it gets processed. See
`knowledge_ingest/adapters/crawler.py` and
`docs/architecture/knowledge-ingest-flow.md` § Part 2.

## HDBSCAN on raw high-dim embeddings fails (HIGH)

Density-based clustering (HDBSCAN) on raw 1024-dim bge-m3 embeddings is unreliable in practice — the curse of dimensionality makes "density" meaningless. Empirical: V2 bootstrap on `voys/support` (501 docs) gave 2 huge clusters + 26.5% outliers instead of an expected 5-15. **Always UMAP-reduce to 5-15 dimensions before HDBSCAN** (defaults: `n_components=10`, `n_neighbors=15`, UMAP metric `cosine`, HDBSCAN metric `euclidean` post-UMAP). Synthetic test fixtures hide this — only real bge-m3 exposes it. See `reports/taxonomy-v2-baseline-2026-05-06/baseline.md` and SPEC-TAXONOMY-V2-001-FOLLOWUP-001.

## Qdrant empty-list == absent (MED)

Qdrant strips empty-list payload keys (`[]`) on upsert. A page with no inbound links
has `anchor_texts` *absent* from its stored payload — not `[]`. Any reader that
checks `payload["anchor_texts"]` crashes; any reader that reads without a default
sees `None`. Both shapes mean the same thing.

**Prevention:** Every retrieval-api reader of list-shaped payload keys goes through
`retrieval_api/util/payload.py::payload_list(payload, key)` which returns `[]` for
key-absent, `None`, and non-list values. Matches the storage contract. SPEC-CRAWLER-005
REQ-04.

## crawl4ai DOM selectors
- Never use `[class*="sidebar"]` or other substring CSS selectors in JS removal scripts.
- Use only semantic element selectors (`nav`, `header`, `aside`) and ARIA roles.

## notion_client — no database-scoped query, and the parent shape moved (MED)

`notion_client` v2 removed `databases.query()`, and `client.search()` — the API
the adapter uses — cannot be filtered by `database_id` at the client level. That
is still true on v3.

The `database_ids` config field is therefore applied as a post-fetch filter in
`NotionAdapter._search_all_pages`, comparing `parent.database_id` against the
stored list. That is a deliberate choice, not the only option: v3 does expose
`databases.retrieve()` and `data_sources.query()`, so a database ID could be
resolved to its data sources and queried per data source. That costs one extra
API call per database and a second code path, which the current volumes do not
justify.

Since 2026-04-05 the connector runs `notion-client` 3.x (arrived transitively via
`notion-sync-lib`, so no major-version PR). v3 defaults to `Notion-Version:
2025-09-03`, and in that API version a page inside a database reports
`parent.type == "data_source_id"` instead of `"database_id"` — while still
carrying the `database_id` key. Code that filtered on `parent.type` dropped every
page and the sync completed with zero documents and no error. Fixed 2026-09-10 by
matching on `parent.database_id` alone, which is correct for both shapes.

A `database_ids` entry is whatever was pasted into the portal. Until 2026-11-12
it was compared verbatim, so a link (or an undashed ID) matched nothing and one
connector completed 19 runs at zero documents. `_extract_config` now reduces
every entry to the dashed ID, and `_require_databases` retrieves each database
before the search, so the link of a page, or of a database not shared with the
integration, fails the sync with Notion's error instead of completing empty.

**Rule:** Never assume `notion_client` has a database-scoped query method, and
never key off `parent.type` — match on `parent.database_id`. When a Notion
behaviour looks wrong, check the default `Notion-Version` of the installed
client first: it moves with the SDK, not with our code.

## Connector source deletions depend on a complete listing (HIGH)

`sync_engine` deletes a stored ref that a listing no longer contains,
but only when `adapter.listing_is_complete(connector)` is True for that run. An
adapter that swallows an error per item, stops at a cap, or reads a change feed
must answer False, or every document it did not see is deleted from the KB.

**Rule:** In `list_documents`, let any error that is not a definite "gone" (Notion
`object_not_found`, Drive/Graph 404) raise; call `self._record_listing(connector,
complete=...)` on every return path, False whenever a cap or truncation could
have cut the listing. Table and guards: `docs/architecture/knowledge-ingest-flow.md`
"Source deletions".
- Spot-check `raw_words` on a known-good page after any crawl config change.

## crawl4ai usage
- **Both klai-knowledge-ingest and klai-connector**: HTTP REST API client to `http://crawl4ai:11235` — `POST /crawl` (sync), processes markdown results.
- Crawl4AI runs as a shared Docker container with Playwright; neither service has a local browser install.
- Connector uses `POST /crawl` with batches of up to 100 URLs (sitemap supplement strategy).
- No other services use crawl4ai. Firecrawl is a separate service used by the chat application only.

## Multi-layer data threading in retrieval results

**When:** Adding a new field (e.g. `source_ref`, `source_connector_id`) that must flow from Qdrant all the way to the frontend.

Every layer is a separate serialization boundary. Dropping a field at any single layer silently loses it downstream — no error, no warning.

Full chain for retrieval results:

1. Qdrant payload → `search.py` dict (`_search_knowledge`, `fetch_chunks_by_urls`)
2. `ChunkResult` model fields (add as `Optional`)
3. `retrieve.py` endpoint — pass fields into `ChunkResult(...)` constructor
4. HTTP JSON response (automatic if Pydantic fields are set)
5. `retrieval_client.py` `_to_chunk()` — populate metadata dict
6. `retrieval.py` `extract_citations()` — read from metadata, build final value
7. Frontend `Citation` interface — add `url?: string | null`

**Rule:** When adding a retrieval field, trace all 7 layers before assuming it works. Test end-to-end with a real Qdrant document that has the field set.

## Connector ID as citation anchor

**When:** Deciding whether to build a connector-specific URL (e.g. Notion page link) for a cited source.

Web crawl sources have `source_url` but no `source_connector_id`. KB connector sources have both `source_ref` (page UUID) and `source_connector_id`. Use the presence of `source_connector_id` as the signal.

```python
if metadata.get("source_ref") and metadata.get("source_connector_id"):
    uuid_clean = metadata["source_ref"].replace("-", "")
    url = f"https://notion.so/{uuid_clean}"
```

**Rule:** Only build connector-specific URLs when BOTH `source_ref` AND `source_connector_id` are present. This prevents false matches with web crawl chunks.

## Notion page URL format

**When:** Building a clickable Notion link from a page UUID stored in `source_ref`.

Notion's canonical URL format strips dashes from the UUID:

```
https://notion.so/<uuid-without-dashes>
```

Example: UUID `550e8400-e29b-41d4-a716-446655440000` becomes `https://notion.so/550e8400e29b41d4a716446655440000`.

**Rule:** Always call `.replace("-", "")` on the UUID before appending to `https://notion.so/`.

## Incremental cursor reset for connectors (MED)

When debugging a connector that syncs 0 results, the incremental cursor is often the cause.

All pages in the source may predate `last_synced_at` stored in `connector.sync_runs.cursor_state`. The connector skips them as "already seen."

**Prevention:** Check and reset the cursor:

```sql
-- Check what the cursor holds
SELECT cursor_state FROM connector.sync_runs
WHERE connector_id = '<connector_id>'
ORDER BY created_at DESC LIMIT 1;

-- Reset to force full re-sync
UPDATE connector.sync_runs
SET cursor_state = NULL
WHERE connector_id = '<connector_id>';
```

**Rule:** When a connector returns 0 results after initial setup, always check `cursor_state` before debugging the adapter logic.

## Embedding pipeline (knowledge-ingest)
1. Chunking: 1500 chars, 200-char overlap
2. Dense embeddings via TEI (gpu-01, port 7997, BAAI/bge-m3, batch size 32, timeout 120s)
3. Sparse embeddings via bge-m3-sparse (gpu-01, port 8001)
4. Store in Qdrant: hybrid dense + sparse + metadata
5. Retrieval: query → dense + BM25 sparse → rerank top-20 via Infinity (gpu-01, port 7998) → top-10 to LLM

## Procrastinate enrichment passthrough (CRIT)

Any metadata field set during initial ingest will be silently deleted if it is not also included in `extra_payload` before the Procrastinate job is enqueued.

**Why:** The enrichment worker receives only the serialized `extra_payload` dict and calls `upsert_enriched_chunks(extra_payload=extra_payload)`. It does not have access to the original ingest call's local variables. The enrichment job deletes all existing chunks and re-inserts them from `extra_payload` — so anything absent from that dict vanishes.

**Prevention:** When adding a new metadata field to ingest, always add it to `extra_payload` in `ingest.py` before `defer_async`. Verify by checking the enriched Qdrant point after a full ingest cycle, not just the initial write. Pattern in `ingest.py`:

```python
extra_payload["content_label"] = content_label  # must be here, not just as explicit param
await job.defer_async(extra_payload=extra_payload, ...)
```

## Qdrant skip-if-present index test (MED)

When adding a new field to the `ensure_collection()` keyword index loop, the test that covers "skip when all fields are already indexed" will fail.

**Why:** The test mocks all pre-existing indexed fields as an `all_fields` set. The new field is not in that set, so `create_payload_index` is called for it — but the test asserts it was never called.

**Prevention:** After adding a new Qdrant payload index field, search for a test that mocks "all fields already indexed" (look for `all_fields` set construction) and add the new field name to that set.

## Dual-path parameter anti-pattern (MED)

A function grows explicit keyword parameters that are only used by one call path (e.g., direct test callers) while pipeline callers pass the same data through `extra_payload`. This results in dead parameters in the signature.

**Why:** When a function is called both directly (tests, simple cases) and via a pipeline (Procrastinate enrichment), two data channels emerge. Over time the explicit params diverge from what the pipeline actually uses.

**Prevention:** Use `extra_payload` as the single channel for pipeline-specific metadata. Explicit params are only appropriate for direct/test callers where `extra_payload` is not in use. When a function accumulates params that are unused in one call path, remove them and consolidate to one channel. Review API boundaries when adding parameters that are only reachable from one code path.

## Portal→ingest auth header: always X-Internal-Secret (HIGH)

When portal calls a knowledge-ingest endpoint, the correct header is `X-Internal-Secret` (checked by `InternalSecretMiddleware` on every request). Using `x-internal-token` instead results in a silent 401 — especially dangerous in fire-and-forget calls where there is no error propagation.

**Why:** knowledge-ingest has two separate auth mechanisms that look similar:
1. `InternalSecretMiddleware` (app-level) — checks `X-Internal-Secret` on every request. Used for portal→ingest calls.
2. `_verify_internal_token()` (per-route helper) — checks `x-internal-token`. Used for ingest→portal calls.

The agent saw `_verify_internal_token` in the taxonomy routes and copied that header name for the outbound portal call — wrong direction, wrong header.

**Prevention:** Before wiring any portal→ingest HTTP call, check `InternalSecretMiddleware` in `knowledge_ingest/middleware.py` to confirm the exact header name. Never infer it from per-route helpers.

## Content-addressed storage for images (SHA256)

**When:** Storing extracted images (or any binary content) in S3/Garage with tenant-scoped paths.

Use SHA256 hash of the file content as the object key. This provides automatic deduplication — the same image extracted from multiple documents is stored once.

```python
content_hash = hashlib.sha256(image_bytes).hexdigest()
key = f"{org_id}/{content_hash}.{ext}"
```

Combined with `filetype` library for magic-bytes MIME detection (zero C dependencies, unlike Pillow), this gives a lightweight image storage pipeline with no duplicate writes.

**Rule:** Use content hash as S3 key for binary assets. Use `filetype` (not Pillow) for MIME detection when you only need type identification.

## Extra JSONB passthrough to downstream services

**When:** Adding new metadata fields that must flow through the connector to knowledge-ingest to Qdrant.

The `extra` JSONB dict on connector ingest requests flows through automatically to Qdrant payload via `extra_payload.update(req.extra)` in knowledge-ingest. No code change needed in knowledge-ingest for new metadata fields — just set them in the connector's `extra` dict.

**Rule:** For new metadata fields originating in the connector, add them to the `extra` dict. They will appear in Qdrant payload without touching knowledge-ingest code. But see "Procrastinate enrichment passthrough" — fields must also be in `extra_payload` before `defer_async`.

## locals() for if/elif branch variable capture (MED)

Using `locals().get("node")` after an if/elif chain to retrieve a variable set in only one branch bypasses type checking (pyright cannot track it), hides control flow, and breaks on rename.

**Prevention:** Declare `_result: SomeType | None = None` before the if/elif chain, assign inside each branch, and read `_result` after. Never use `locals()` for cross-branch variable access.

## Shared downstream budgets, not caller-local limiters (HIGH)

A Procrastinate queue controls ownership and delivery; it does not pace calls to
a downstream API. All callers sharing one LiteLLM alias or external quota must
acquire the same process-wide limiter, and retries must use backoff. Independent
per-module limiters add their offered throughput and can exceed the shared budget
while each appears correct in isolation.

For `klai-fast`, route chat calls through
`knowledge_ingest/llm_throttle.py::shared_klai_fast_limiter()`. Keep a drift-guard
test that fails when a new `chat/completions` call bypasses that limiter, and alert
on sustained 429/error events rather than relying on queue health.

The rate is derived from the `klai-ingest` alias limits and the measured call
size, with the derivation next to `litellm_klai_fast_rps` in `config.py`;
`tests/test_llm_throttle.py` pins its share of the limits, so a changed rate or
a changed alias limit fails until the other is revisited. A job that waits on
an in-process semaphore (the graph's episode semaphore) gets its own worker
lane sized to that semaphore: in a shared lane it holds a slot while it waits,
and on 12 November 2026 that made enrichment wait 132 minutes (median) for 0.2
minutes of work.

## Configuration requires a production reader

When adding a knowledge setting or threshold, identify the production code that
reads it and prove an observable behavior change. A field threaded through task
arguments or accepted by a remote API can remain inert when the real dispatcher
uses another branch. Before deleting an apparently orphaned engine, check whether
the missing piece is its trigger rather than the engine itself.
