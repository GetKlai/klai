---
paths:
  - "klai-knowledge-ingest/**"
  - "klai-connector/**"
---
# Knowledge Domain Patterns

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

## notion_client v2 — databases.query() removed (MED)

`notion_client` v2 removed `databases.query()`. The only available search API is
`client.search()`, which returns all pages the integration can access — it cannot
be filtered by `database_ids` at the client level.

The `database_ids` config field is stored and surfaced in the UI (SPEC-KB-019) but
does not filter API results. Future filtering must be applied post-fetch (compare
`parent.database_id` against the stored list), not via an SDK call.

**Rule:** Never assume `notion_client` has a database-scoped query method. Filter by `database_id` in Python after fetching all search results.
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
