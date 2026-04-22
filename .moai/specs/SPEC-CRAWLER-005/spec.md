---
id: SPEC-CRAWLER-005
version: "1.1"
status: implemented
created: 2026-04-22
updated: 2026-04-22
author: Mark Vletter
priority: high
issue_number: 110
---

## HISTORY

| Version | Date | Author | Change |
|---------|------|--------|--------|
| 1.0 | 2026-04-22 | Mark Vletter | Initial draft from SPEC-CRAWLER-004 Fase E findings on Voys `support` |
| 1.1 | 2026-04-22 | Mark Vletter | Implemented. Fase 1-4 + Fase 5 skeleton shipped to `main`. Fase 6 (live Playwright on Voys `support`) is the prod verification — run post-deploy. Fase 7 docs + pitfall entries committed. |

---

# SPEC-CRAWLER-005: Two-phase crawl pipeline — complete link-graph + chunk_type on every Qdrant chunk

## Context

SPEC-CRAWLER-004 consolidated the crawl pipeline into `knowledge-ingest` and the Fase E smoketest on Voys `support` (20 pages, 167 Qdrant chunks) exposed three payload fields missing on every crawl chunk:

- `anchor_texts`: 0 / 167 chunks have the key (stored as None/missing, not even empty list)
- `links_to`: 0 / 167 chunks have the key
- `chunk_type`: 0 / 167 chunks have the key
- `incoming_link_count`: 43 / 167 populated via the existing post-crawl batch-update; the other 124 miss it

These are pre-existing bugs in the knowledge-ingest crawl adapter, not introduced by SPEC-CRAWLER-004. Three tests on main already fail because of this:

- `tests/test_crawl_link_fields.py::test_crawl_url_populates_link_fields`
- `tests/test_crawl_link_fields.py::test_crawl_url_caps_links_to_at_20`
- `tests/test_crawl_link_fields.py::test_crawl_url_graceful_degradation_on_link_graph_error`

### Root cause 1 — single-loop graph + content ingest

`knowledge-ingest/adapters/crawler.py::run_crawl_job` processes pages in one sequential loop where each iteration does both graph-building (`pg_store.upsert_page_links`) AND content ingest (`link_graph.get_anchor_texts` → `ingest_document`). When page P is processed, only pages crawled earlier in the loop exist in `knowledge.page_links`, so `get_anchor_texts(P)` returns a partial set. The first page always gets `anchor_texts=[]`; later pages get a monotonically growing but still incomplete view.

A post-crawl pass (`link_graph.compute_incoming_counts` → `qdrant_store.update_link_counts`) patches `incoming_link_count` via `set_payload` — but **only that one field**. There is no equivalent pass for `anchor_texts` or `links_to`. Additionally that pass races with the enrichment Procrastinate queue: if enrichment hasn't upserted a chunk yet when `set_payload` fires, the filter matches nothing and the chunk stays at `incoming_link_count: 0` (or missing). This explains the 43/167 hit rate.

### Root cause 2 — Qdrant empty-list stripping

Qdrant's upsert API strips empty-list payload keys. Even if ingest writes `anchor_texts: []` the key disappears from the stored point. Reading back returns `KeyError` / `None`. Retrieval code that does `payload["anchor_texts"]` breaks; callers must use `.get("anchor_texts") or []`.

### Root cause 3 — chunk_type absent on crawl chunks

`enrichment.py` calls the LLM with a prompt that explicitly asks for `chunk_type` as an enum {procedural, conceptual, reference, warning, example} and the Pydantic model types it as `Literal[...]`. Yet on Voys crawl chunks the field is missing. Either the LLM call fails silently, the parser falls back to empty-string which line `if getattr(ec, "chunk_type", ""):` skips, or the crawl's `synthesis_depth=1` path hits a branch that does not run the classifier. Needs diagnosis.

### Goal

Refactor `run_crawl_job` into two clean phases so every graph-derived Qdrant field is accurate at first write, eliminate the post-crawl band-aid, and fix `chunk_type` on crawl chunks.

---

## Scope

### In scope

1. New helper `_build_link_graph(results, org_id, kb_slug, pool)` in
   `klai-knowledge-ingest/knowledge_ingest/adapters/crawler.py`. Upserts all
   `page_links` rows before any call to `_ingest_crawl_result`. Pure SQL, no
   LLM, no Qdrant.
2. Restructure `run_crawl_job` to: `crawl_site` → `_build_link_graph` →
   `for result in results: _ingest_crawl_result`. Removes the interleaved
   per-page upsert + ingest.
3. Remove `_ingest_crawl_result`'s inline `pg_store.upsert_page_links` call
   (now owned by Phase 1).
4. Remove the post-crawl `compute_incoming_counts` + `update_link_counts`
   block from `run_crawl_job` (redundant — ingest-time values are now
   final).
5. Mark `qdrant_store.update_link_counts` and
   `link_graph.compute_incoming_counts` as deprecated repair utilities
   (keep the functions, add docstrings, no production caller).
6. Diagnose + fix `chunk_type` population on crawl chunks
   (`knowledge_ingest/enrichment.py` or the bulk-enrichment wiring). The
   fix must cover `synthesis_depth=1` which is the default for bulk crawls.
7. Document the "absent key == empty list" convention for link-graph
   fields. Add a `payload_list(payload, key) -> list` helper in
   `klai-retrieval-api` (or shared util) and switch every reader to it.
8. Fix the three failing tests in `test_crawl_link_fields.py`. They
   exercise the single-URL `POST /ingest/v1/crawl` endpoint (not
   `run_crawl_job`), so the fix there is an independent patch to
   `routes/crawl.py::crawl_url` that uses the same completion contract
   (extra_payload fully populated — which it already does for the
   single-URL path, so the tests likely need mock realignment).
9. Update `docs/architecture/knowledge-ingest-flow.md` § Part 2 Phase 1
   Step 1 with the two-phase description.

### Out of scope (What NOT to Build)

- NO changes to `klai-connector` or `klai-portal/backend` — consumers
  call the existing `/ingest/v1/crawl/sync` endpoint from
  SPEC-CRAWLER-004 Fase D and see no change.
- NO changes to the Qdrant `klai_knowledge` collection schema or any
  existing payload field.
- NO redefinition of `chunk_type` semantics (SPEC-KB-021) — only fix the
  wiring so it is populated on crawl chunks.
- NO backfill of existing broken chunks — a re-sync (handled by the
  frontend via the connector) refills them.
- NO new Qdrant indexes for the graph fields.
- NO new Procrastinate queues; reuse `enrich-bulk` and `ingest-kb`.
- NO per-field `set_payload` fallback logic; if Phase 1 is correct there
  is no reason to touch the payload after enrichment.

---

## Requirements (EARS)

### REQ-CRAWLER-005-01 — Two-phase pipeline separation

**REQ-01.1 (Ubiquitous).** `run_crawl_job` shall upsert every
`knowledge.page_links` row produced by the crawl before ingesting any
page via `_ingest_crawl_result`.

**REQ-01.2 (Ubiquitous).** A new `_build_link_graph(results, org_id,
kb_slug, pool)` helper in `adapters/crawler.py` shall be the only caller
of `pg_store.upsert_page_links` during a bulk crawl.

**REQ-01.3 (Ubiquitous).** `_ingest_crawl_result` shall not call
`pg_store.upsert_page_links` directly.

**REQ-01.4 (Event-driven).** When `run_crawl_job` calls Phase 2 on page
P, `link_graph.get_anchor_texts(P)` and `link_graph.get_outbound_urls(P)`
and `link_graph.get_incoming_count(P)` shall all return the final values
for P across all crawled pages — not a partial view.

### REQ-CRAWLER-005-02 — Qdrant payload completeness at ingest time

**REQ-02.1 (Ubiquitous).** After a completed `run_crawl_job`, every
Qdrant crawl chunk shall have `incoming_link_count` set to a final
non-negative integer. No post-crawl `set_payload` pass shall be
required to reach that state.

**REQ-02.2 (Ubiquitous).** After a completed `run_crawl_job` on N pages
with K internal links, every Qdrant crawl chunk whose page has at least
one inbound link shall have a non-empty `anchor_texts` list in payload.

**REQ-02.3 (Ubiquitous).** Every Qdrant crawl chunk whose page has at
least one outbound internal link shall have a non-empty `links_to` list
in payload (capped at 20 URLs, preserving the current behaviour).

**REQ-02.4 (Ubiquitous).** When a page has no inbound or no outbound
internal links, the corresponding list field shall be absent from the
Qdrant payload (empty-list == absent, per convention documented in
REQ-04).

### REQ-CRAWLER-005-03 — chunk_type on crawl chunks

**REQ-03.1 (Ubiquitous).** When a crawl chunk is enriched via
`enrich_document_bulk` (`synthesis_depth=1`), the Qdrant payload shall
contain a `chunk_type` field with a value in
{`procedural`, `conceptual`, `reference`, `warning`, `example`} for
≥ 80% of chunks on a help-center style crawl (help.voys.nl baseline of
20 pages).

**REQ-03.2 (Unwanted behaviour).** If the LLM classifier returns an
empty or invalid `chunk_type`, the enrichment pipeline shall log a
warning with `artifact_id`, `chunk_index`, and the raw LLM response
preview and continue — it shall not silently drop the chunk.

**REQ-03.3 (Ubiquitous).** The diagnosis of why `chunk_type` was missing
on Voys crawl chunks shall be captured in the SPEC's research.md or in
the commit message of the fix, including the specific code path
(prompt, parser, schema validation, or synthesis_depth gating) that
caused the drop.

### REQ-CRAWLER-005-04 — Empty-list convention

**REQ-04.1 (Ubiquitous).** Any retrieval-side code reading
`anchor_texts`, `links_to`, or any other list-valued Qdrant payload
field shall treat key-absent as equivalent to empty list, via a single
shared `payload_list(payload, key) -> list` helper.

**REQ-04.2 (Ubiquitous).** The helper shall live in
`klai-retrieval-api/app/util/payload.py` (or similar) and every existing
reader of link-graph fields shall switch to it in this SPEC.

**REQ-04.3 (Ubiquitous).** `docs/architecture/knowledge-ingest-flow.md`
§ Part 2 shall document the empty-list convention with a one-line note
pointing to the helper.

### REQ-CRAWLER-005-05 — Removed / deprecated code

**REQ-05.1 (Ubiquitous).** The call sequence
`link_graph.compute_incoming_counts` → `qdrant_store.update_link_counts`
in `run_crawl_job` shall be removed. The two functions themselves shall
remain as deprecated repair utilities with explicit docstrings pointing
at this SPEC.

**REQ-05.2 (Ubiquitous).** No production code path shall call
`update_link_counts` after this SPEC lands. CI ruff rule or pytest
assertion shall guard against reintroduction.

### REQ-CRAWLER-005-06 — Regression + test coverage

**REQ-06.1 (Ubiquitous).** The three pre-existing failing tests in
`tests/test_crawl_link_fields.py` shall pass.

**REQ-06.2 (Ubiquitous).** No regression in the ≥ 402 currently passing
tests in `klai-knowledge-ingest` and the ≥ 237 passing tests in
`klai-connector`.

**REQ-06.3 (Ubiquitous).** A new unit test
`tests/test_build_link_graph.py` shall verify that
`_build_link_graph` upserts every expected `page_links` row before the
first `_ingest_crawl_result` call.

**REQ-06.4 (Ubiquitous).** A new integration-style test
`tests/test_crawler_link_fields_complete.py` shall simulate a 5-page
cross-linking crawl and assert that every ingested page's
`extra_payload` (captured at `ingest_document` time) contains a
correctly populated `anchor_texts`, `links_to`, `incoming_link_count` —
verifying the Phase 2 ordering fix.

**REQ-06.5 (Ubiquitous).** A new test
`tests/test_chunk_type_crawl.py` shall verify that a mocked
`enrich_document_bulk` with an LLM response containing a valid
`chunk_type` produces a Qdrant upsert whose payload carries that
`chunk_type`.

---

## Affected Files

### klai-knowledge-ingest (refactor)

- `knowledge_ingest/adapters/crawler.py` — new `_build_link_graph`
  helper; `run_crawl_job` restructured into two phases;
  `_ingest_crawl_result` loses the inline `upsert_page_links`; removes
  the post-crawl `compute_incoming_counts` + `update_link_counts` block.
- `knowledge_ingest/enrichment.py` — fix the code path that dropped
  `chunk_type` on crawl chunks (details determined during run phase).
- `knowledge_ingest/link_graph.py` — `compute_incoming_counts` gets
  deprecation docstring (function body unchanged).
- `knowledge_ingest/qdrant_store.py` — `update_link_counts` gets
  deprecation docstring.
- `knowledge_ingest/routes/crawl.py` — single-URL `crawl_url` endpoint
  may need mock/test realignment for the three failing tests.

### klai-knowledge-ingest (new tests)

- `tests/test_build_link_graph.py` (new)
- `tests/test_crawler_link_fields_complete.py` (new)
- `tests/test_chunk_type_crawl.py` (new)
- `tests/test_crawl_link_fields.py` — 3 tests restored to passing

### klai-retrieval-api (new helper + consumer switch)

- `klai-retrieval-api/app/util/payload.py` (new) — `payload_list(p, k)`
- every reader of `anchor_texts` / `links_to` in retrieval-api routed
  through `payload_list`.

### docs

- `docs/architecture/knowledge-ingest-flow.md` — § Part 2 updated with
  two-phase description + empty-list convention note.

---

## Delta Markers (brownfield)

### [DELTA] klai-knowledge-ingest crawler.py

- [EXISTING] `run_crawl_job` single-loop per-page upsert + ingest.
- [MODIFY] `run_crawl_job` two-phase: `_build_link_graph` → per-page ingest.
- [REMOVE] post-crawl `compute_incoming_counts` + `update_link_counts`
  block in `run_crawl_job`.
- [NEW] `_build_link_graph` helper.

### [DELTA] klai-knowledge-ingest enrichment.py

- [EXISTING] Bulk enrichment that sometimes drops `chunk_type` at
  `synthesis_depth=1`.
- [MODIFY] Fix the dropped classifier path; add structured warning log
  on invalid/empty LLM `chunk_type`.

### [DELTA] klai-retrieval-api

- [NEW] `app/util/payload.py::payload_list`
- [MODIFY] every site that reads `payload["anchor_texts"]` or
  `payload.get("anchor_texts", [])` switches to `payload_list`.

---

## Acceptance Summary

Full Gherkin scenarios in `acceptance.md`. Key gates:

1. `_build_link_graph` upserts every expected row before any ingest (unit test)
2. 5-page synthetic cross-linked crawl yields all fields populated at ingest-time (unit test)
3. 3 pre-existing `test_crawl_link_fields.py` tests pass
4. Integration test (docker-compose) on a 4-page fixture server passes full field verification
5. Playwright E2E on Voys `support` (delete + re-sync via UI): 20 pages, > 50 page_links, 140-200 chunks, all with `anchor_texts` / `links_to` / `incoming_link_count` / `chunk_type` populated
6. Second sync logs `crawl_skipped_unchanged` 20×, Qdrant chunk count unchanged
7. Log grep: zero plaintext cookie leakage (REQ-05.4 from SPEC-CRAWLER-004 continues to hold)
8. Regression: klai-knowledge-ingest ≥ 405 passing, klai-connector ≥ 237 passing

---

## References

- SPEC-CRAWLER-004 (pipeline consolidation; Fase E findings drove this SPEC)
- SPEC-CRAWLER-003 (link-graph fields, R9-R12)
- SPEC-KB-021 (chunk_type classification)
- Commits from SPEC-CRAWLER-004: `5dca107e` Fase 0, `6cbb67d1` Fase A, `00c2a87d` Fase B, `12e99358` Fase C, `f9139f7f` Fase D, `95992807` REQ-05.4 fix
- Pre-existing failing tests: `klai-knowledge-ingest/tests/test_crawl_link_fields.py`
- `docs/architecture/knowledge-ingest-flow.md` § Part 2 Phase 1 Step 1
