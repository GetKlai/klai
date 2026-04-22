# Implementation Plan — SPEC-CRAWLER-005

## Overview

Split `run_crawl_job` into two discrete phases so `anchor_texts`,
`links_to`, and `incoming_link_count` are correct on Qdrant chunks at
ingest time — no post-crawl `set_payload` band-aid. Separately diagnose
and fix the `chunk_type` drop on crawl chunks. Retrieval-side code
formalises the "absent key == empty list" convention.

Every phase is an independently-revertable commit, CI-green in
isolation, with its own unit tests. Integration + Playwright E2E land
in a single later phase so we verify behaviour on real Voys data before
closing the SPEC.

---

## Reference Implementation Anchors

| Concept | Reference |
|---------|-----------|
| Current interleaved loop | `klai-knowledge-ingest/knowledge_ingest/adapters/crawler.py::run_crawl_job` (post Fase B: L64-L150) |
| Post-crawl batch-update | `adapters/crawler.py` lines calling `link_graph.compute_incoming_counts` + `qdrant_store.update_link_counts` |
| Link-graph queries (unchanged, correct) | `knowledge_ingest/link_graph.py::{get_outbound_urls, get_anchor_texts, get_incoming_count}` |
| `pg_store.upsert_page_links` | `knowledge_ingest/pg_store.py::upsert_page_links` |
| Enrichment classifier | `knowledge_ingest/enrichment.py::EnrichmentResult.chunk_type` + the LLM prompt at the top of that file |
| Current broken test baseline | `klai-knowledge-ingest/tests/test_crawl_link_fields.py` (3 failing on main as of SPEC-CRAWLER-004 branch) |

---

## Technology Stack

Python 3.12, asyncpg, Qdrant async client, Procrastinate, httpx,
pytest-asyncio, ruff, pyright. No new runtime deps.

---

## Phase Breakdown

### Fase 1 — Phase-split crawler.py (core refactor)

**Goal:** `run_crawl_job` calls `_build_link_graph` then per-page ingest.
Every link-graph field is correct at first Qdrant upsert.

**Tasks:**

1. Add `_build_link_graph(results: list[CrawlResult], org_id: str,
   kb_slug: str, pool: asyncpg.Pool)` to `adapters/crawler.py`. It
   iterates results, collects `result.links.internal`, calls
   `pg_store.upsert_page_links(from_url=result.url, links=internal)`
   for each. No Qdrant, no LLM, no ingest.
2. In `run_crawl_job`, after `crawl_site` returns and the success-count
   is updated on `knowledge.crawl_jobs`, call `_build_link_graph(...)`.
3. Remove the `if result.links: ... upsert_page_links` block from
   `_ingest_crawl_result`.
4. Remove the `try: from knowledge_ingest import link_graph,
   qdrant_store ... update_link_counts(...)` block from `run_crawl_job`.
5. Add deprecation docstrings to
   `link_graph.compute_incoming_counts` and
   `qdrant_store.update_link_counts` pointing at
   `SPEC-CRAWLER-005 REQ-05.1`. Do not delete — allows an admin-only
   repair script later.
6. Unit tests:
   - `tests/test_build_link_graph.py` — given 3 mocked `CrawlResult`
     objects with overlapping `links.internal`, assert
     `upsert_page_links` is called for each result with exactly the
     expected link sets and that no other side effect fires.
   - `tests/test_crawler_link_fields_complete.py` — patch `crawl_site`
     to return 5 cross-linked pages (A→B, A→C, B→C, C→D, D→A); patch
     `ingest_document` to capture `req.extra`; run `run_crawl_job`;
     assert that every captured `extra` contains the complete
     `anchor_texts`, `links_to` (capped at 20), and
     `incoming_link_count` for its page, including page A which under
     the old pipeline got `anchor_texts=[]`.

**Estimated size:** ~120 LOC refactor + ~250 LOC tests.

**Risks:**

- `pg_store.upsert_page_links` is idempotent (uses an ON CONFLICT-style
  upsert). Running it in a tight loop of 200 pages must stay under
  15 s end-to-end — confirm with a timing assertion in the unit test.
- Moving the upsert before ingest means if Phase 2 fails per-page, the
  page_links graph is still there. Resumable — acceptable.

### Fase 2 — Fix pre-existing `test_crawl_link_fields.py`

**Goal:** Three currently-failing tests pass. They cover
`POST /ingest/v1/crawl` (single-URL), not the bulk loop. We restore
them first so the regression gate has something to lean on.

**Tasks:**

1. Run the tests, capture the actual failure (it's already identified
   in the SPEC-CRAWLER-004 baseline as pre-existing; diagnosis goes in
   commit message).
2. Fix `routes/crawl.py::crawl_url` if there is a real bug; more likely
   only the mock wiring drifted.
3. Do not loosen assertions — the tests must verify the SAME behaviour
   they originally aimed at (link fields populated in
   `ingest_req.extra`).

**Estimated size:** ≤ 40 LOC, diagnosis-dependent.

### Fase 3 — chunk_type diagnose + fix

**Goal:** ≥ 80% of crawl chunks on `help.voys.nl` get a valid
`chunk_type`.

**Tasks:**

1. Diagnose the drop. First check (in order of likelihood):
   - LLM response parsing: is the JSON sometimes returned with
     `chunk_type=""` or a value outside the `Literal` set? Confirm by
     adding a structured `crawl_chunk_type_drop` log with
     `artifact_id`, `chunk_index`, `raw_llm_response[:200]`, and run a
     small crawl (3 pages) locally against the LLM.
   - Pydantic validation failure: does the `EnrichmentResult` model
     reject the LLM response, causing a fall-through to the default
     `chunk_type: str = ""` on `EnrichedChunk`?
   - Line 275 gating: `if getattr(ec, "chunk_type", ""):` — is
     `ec.chunk_type` set but empty because of upstream failure?
2. Implement the minimum viable fix. Options:
   - Retry the LLM call once with a strengthened prompt if `chunk_type`
     is invalid.
   - Fall back to a cheap heuristic (e.g., first-line keyword match on
     the chunk text) when the LLM value is empty.
   - Prefer classifier-retry; heuristic only as backstop.
3. Add REQ-03.2 logging at `warning` on invalid/empty classification.
4. New test `tests/test_chunk_type_crawl.py`:
   - Mock the LLM to return a valid response — assert `chunk_type`
     flows into the Qdrant upsert payload.
   - Mock the LLM to return an empty `chunk_type` — assert the warning
     log fires with the required fields AND the fallback sets a valid
     `chunk_type`.

**Estimated size:** ~60 LOC fix + ~180 LOC tests.

### Fase 4 — Empty-list convention helper

**Goal:** One shared helper for every reader; Qdrant's key-stripping is
documented and tolerated.

**Tasks:**

1. Create `klai-retrieval-api/app/util/payload.py`:
   ```python
   def payload_list(payload: Mapping[str, Any], key: str) -> list:
       value = payload.get(key)
       return list(value) if isinstance(value, list) else []
   ```
2. Switch every reader of `anchor_texts`, `links_to`, `image_urls` in
   `klai-retrieval-api/` to `payload_list`. Grep to enumerate.
3. Unit test `tests/test_payload_util.py` covering: present list,
   missing key, None, non-list value (returns `[]` — protects against
   future regressions).
4. Update `docs/architecture/knowledge-ingest-flow.md` § Part 2 with a
   one-line note: "Qdrant strips empty-list payload keys on upsert —
   retrieval-api uses `payload_list()` so key-absent and empty-list are
   interchangeable."

**Estimated size:** ~40 LOC helper + ~80 LOC call-site rewrites + ~60
LOC tests.

### Fase 5 — Integration test (docker-compose)

**Goal:** End-to-end verification inside a throwaway compose stack,
without the real crawl4ai / LLM.

**Tasks:**

1. Add a `tests/integration/test_crawl_sync_end_to_end.py` that uses
   `docker-compose.test.yml` (already used by repo) to bring up
   `postgres + qdrant + knowledge-ingest`, stub crawl4ai with a
   FastAPI fixture server returning 4 pre-canned HTML pages with
   cross-links and `media.images`.
2. POST to `/ingest/v1/crawl/sync` with a fixture `connector_id`.
3. Poll `/ingest/v1/crawl/sync/{job_id}/status` until `completed`.
4. Assert Qdrant chunks for all 4 pages have:
   `source_type=crawl`, `source_label` set, `anchor_texts` populated
   for pages with inbound links, `links_to` populated for pages with
   outbound links, `incoming_link_count` set correctly,
   `image_urls` when fixtures had images.
5. Integration test runs gated by env flag `RUN_INTEGRATION=1` so CI
   speed stays reasonable.

**Estimated size:** ~300 LOC incl. fixtures.

### Fase 6 — Playwright E2E on Voys `support`

**Goal:** Proof in production data. Frontend-driven, no backend
shortcuts.

**Driver:** agent via Playwright MCP (Brave persistent profile
`~/.claude/mcp-brave-profile`).

**Tasks (agent steps):**

1. Navigate to `https://my.getklai.com`, login as admin.
2. Switch to Voys tenant (`368884765035593759`).
3. Open admin Knowledge Base view for KB `support`.
4. Click "Delete all artifacts" (or equivalent reset flow), confirm.
   Agent waits for the UI state that confirms cleanup fired.
5. Navigate to the `help.voys.nl` web_crawler connector detail page.
6. Click "Sync now".
7. Wait for `status=completed` in the UI.

**Verification by agent (SSH + SQL + Qdrant queries on core-01):**

- `connector.sync_runs` row: status=completed, documents_ok=20,
  `cursor_state.remote_job_id` non-null.
- `knowledge.crawl_jobs[remote_job_id]`: status=completed,
  `pages_done = pages_total`.
- `knowledge.crawled_pages`: exactly 20 rows for
  `org_id=368884765035593759 AND kb_slug='support'`.
- `knowledge.page_links`: ≥ 50 rows.
- Qdrant chunk count via filter
  `org_id+kb_slug+source_type=crawl`: 140-200.
- For 10 randomly-sampled chunks:
  - `source_type=crawl`, `source_label=help.voys.nl`,
    `source_domain=help.voys.nl`
  - `chunk_type` in {procedural, conceptual, reference, warning,
    example} on ≥ 80% of them
  - `anchor_texts` non-empty on chunks whose page has inbound links
  - `links_to` non-empty on chunks whose page has outbound links
  - `incoming_link_count` is an int
- Hub pages (`path LIKE '%index%'` or `incoming_link_count > 0`):
  `incoming_link_count > 0` holds.
- `docker logs --since 10m klai-core-klai-connector-1
  klai-core-knowledge-ingest-1 2>&1 | grep -iE "<known cookie value
  prefix>"` returns zero matches (REQ-05.4 from SPEC-CRAWLER-004
  continues to hold).

**Second sync (dedup):**

- Click "Sync now" again.
- Assert `knowledge.crawled_pages` still 20 rows.
- Assert `docker logs` contains `crawl_skipped_unchanged` 20×.
- Assert Qdrant count unchanged.

**Rollback point:** if any Fase 6 assertion fails, revert Fase 1 commit
to restore the old single-loop behaviour. Re-open the SPEC.

### Fase 7 — Docs + pre-existing pitfall capture

**Goal:** Knowledge rot prevention.

**Tasks:**

1. Update `docs/architecture/knowledge-ingest-flow.md` § Part 2 Phase 1
   Step 1 with a short two-phase diagram.
2. Add an entry to `.claude/rules/klai/pitfalls/process-rules.md` or
   `.claude/rules/klai/projects/knowledge.md`:
   "Graph-first, content-second — never interleave SQL graph building
   with per-row Qdrant ingest when the graph influences the ingest
   payload. Pipeline A's pre-SPEC-CRAWLER-005 code violated this and
   cost us the Voys smoketest."
3. Set SPEC frontmatter `status: completed`.

**Estimated size:** ~200 LOC doc changes.

---

## MX Tag Plan

High fan_in targets requiring `@MX:ANCHOR`:

- `adapters/crawler.py::run_crawl_job` — behavioural anchor, ordering
  contract with Phase 1.
- `adapters/crawler.py::_build_link_graph` — new invariant boundary.
- `enrichment.py::EnrichmentResult.chunk_type` — cross-SPEC contract.

Danger-zone targets requiring `@MX:WARN`:

- `qdrant_store.update_link_counts` — deprecated; warn any future
  caller to use a full re-sync instead.
- `link_graph.compute_incoming_counts` — deprecated.

---

## Risk Analysis and Mitigation

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| `_build_link_graph` slow on 200-page crawls | L | M | Time-assert in unit test; keep `upsert_page_links` batched if already batched, or introduce ONE multi-row INSERT in a follow-up |
| `chunk_type` fix needs LLM retry — extra latency | M | L | Only retry on invalid response; log retry rate, tune threshold if retries > 5% |
| Deprecating `update_link_counts` breaks external scripts | L | M | Keep function, add docstring. Leave a TODO to remove post next quarter |
| Playwright KB delete flow differs in UI than expected | M | M | First pass: agent takes snapshots, confirms element refs before clicking. If delete flow doesn't exist, open a follow-up issue and use a backend endpoint one-off |
| LLM rate limits during Fase 6 sync | M | L | Reuse existing enrichment rate-limit config; monitor logs |
| Integration docker-compose flakiness | M | L | Env-gated; not on critical CI path |

---

## Estimated Effort

- Fase 1: 1 commit (refactor + unit tests)
- Fase 2: 1 commit (test fix)
- Fase 3: 1 commit (chunk_type fix)
- Fase 4: 1 commit (payload helper + switch consumers)
- Fase 5: 1 commit (integration test, env-gated)
- Fase 6: 0 commits (live E2E via Playwright)
- Fase 7: 1 commit (docs + status=completed)

Total: ~6 commits. Each revertable. No multi-commit dependencies across
unrelated scopes.

---

## Open Questions

1. Is the Voys `support` KB the right fixture for Fase 6, or should we
   provision a throwaway tenant? → default: use Voys since Mark owns
   it and has a clean baseline. Escalate if reset UI is missing.
2. Should `update_link_counts` be moved into a dedicated `admin.py`
   repair module now, or left in-place with a deprecated docstring? →
   leave in-place; rename / move is out of scope here.
3. Does `chunk_type` retry need a circuit-breaker per org? → no; one
   retry per chunk is bounded; enrichment is queue-bound anyway.
