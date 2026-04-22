# SPEC-CRAWLER-005 — Compact

Two-phase crawl pipeline in knowledge-ingest so every Qdrant crawl
chunk has correct `anchor_texts`, `links_to`, `incoming_link_count`,
and `chunk_type` at first write. No post-crawl `set_payload` band-aid.

## Problem (one line)

Voys `support` smoketest: 167 crawl chunks, 0 have anchor_texts /
links_to / chunk_type keys, 43 have incoming_link_count. Three
pre-existing `test_crawl_link_fields.py` tests confirm this is not
caused by SPEC-CRAWLER-004.

## Root cause (one paragraph)

`run_crawl_job` interleaves `upsert_page_links` with `ingest_document`
in a single loop. Pages processed early read an incomplete graph from
`knowledge.page_links`, so `get_anchor_texts` returns `[]`. A
post-crawl pass patches `incoming_link_count` via `set_payload` but
races with enrichment. Qdrant strips empty-list keys on upsert, so
even `anchor_texts=[]` disappears. `chunk_type` is dropped separately
by the enrichment LLM path on crawl chunks.

## Requirements

### REQ-01 — Two-phase pipeline

- **01.1**: `run_crawl_job` upserts every `page_links` row before any
  `_ingest_crawl_result` call.
- **01.2**: New `_build_link_graph(results, org_id, kb_slug, pool)`
  helper owns all `upsert_page_links` calls during a bulk crawl.
- **01.3**: `_ingest_crawl_result` does not call
  `pg_store.upsert_page_links`.
- **01.4**: `link_graph.get_*(P)` returns the final values for P
  across all crawled pages when Phase 2 processes P.

### REQ-02 — Qdrant payload completeness at ingest time

- **02.1**: `incoming_link_count` is final on every chunk at first
  write. No post-crawl `set_payload` required.
- **02.2**: `anchor_texts` is populated on chunks whose page has
  inbound internal links.
- **02.3**: `links_to` is populated (capped at 20) on chunks whose
  page has outbound internal links.
- **02.4**: Pages with no inbound/outbound links have the
  corresponding field absent — empty-list == absent, by convention.

### REQ-03 — chunk_type

- **03.1**: >= 80% of Voys `support` crawl chunks have `chunk_type`
  in {procedural, conceptual, reference, warning, example}.
- **03.2**: Empty/invalid LLM response logs a warning with
  `artifact_id`, `chunk_index`, `raw_llm_response[:200]` and falls
  back to a valid classification.
- **03.3**: Diagnosis root cause goes into commit message or research.md.

### REQ-04 — Empty-list convention

- **04.1**: New `payload_list(payload, key) -> list` helper treats
  key-absent, None, and non-list values as `[]`.
- **04.2**: Every retrieval-api reader of `anchor_texts`/`links_to`/
  `image_urls` uses the helper.
- **04.3**: `knowledge-ingest-flow.md` § Part 2 documents the
  convention.

### REQ-05 — Removed / deprecated code

- **05.1**: `run_crawl_job` no longer calls
  `compute_incoming_counts` + `update_link_counts`.
- **05.2**: The two functions remain with deprecation docstrings;
  no production caller.

### REQ-06 — Regression

- **06.1**: The 3 failing `test_crawl_link_fields.py` tests pass.
- **06.2**: knowledge-ingest passing >= 405. klai-connector passing
  >= 237. No new regressions.
- **06.3**: `test_build_link_graph.py` (new) verifies Phase 1
  ordering.
- **06.4**: `test_crawler_link_fields_complete.py` (new) verifies
  5-page cross-linked ingest writes full fields.
- **06.5**: `test_chunk_type_crawl.py` (new) verifies chunk_type end-
  to-end through the enrichment path.

## Acceptance (key scenarios)

Full Gherkin in `acceptance.md`. Summary:

- AC-01.1 `_build_link_graph` runs before first ingest
- AC-01.3 late pages see full graph (first-page has anchor_texts)
- AC-02.1 `incoming_link_count` final without post-crawl pass
- AC-02.3 `links_to` capped at 20
- AC-02.4 leaf pages have absent `anchor_texts` (== empty by helper)
- AC-03.3 `chunk_type` on >= 80% of Voys chunks
- AC-04.1 `payload_list` handles every input shape
- AC-05.1 no production caller of `update_link_counts`
- AC-06.1 3 pre-existing tests pass
- AC-07.1 docker-compose integration crawl passes
- AC-08.1 Voys Playwright E2E: 20 pages, > 50 links, 140-200 chunks,
  complete payload
- AC-08.3 second sync: 20x `crawl_skipped_unchanged`, zero duplicates

Quality gates:
- >= 85% unit coverage on new modules
- 0 ruff/pyright errors on touched files
- Voys baseline: 140-200 crawl chunks, >= 80% chunk_type, zero
  plaintext cookie leakage

## Files

### klai-knowledge-ingest (refactor)

- `knowledge_ingest/adapters/crawler.py` — new `_build_link_graph`,
  `run_crawl_job` two-phase, remove post-crawl batch-update,
  `_ingest_crawl_result` no longer upserts page_links
- `knowledge_ingest/enrichment.py` — fix chunk_type drop; add
  structured warning logs; retry/fallback
- `knowledge_ingest/link_graph.py` — deprecation docstring on
  `compute_incoming_counts`
- `knowledge_ingest/qdrant_store.py` — deprecation docstring on
  `update_link_counts`
- `knowledge_ingest/routes/crawl.py` — unblock the 3 failing
  single-URL crawl tests (mock realignment likely)

### klai-knowledge-ingest (new tests)

- `tests/test_build_link_graph.py` (new)
- `tests/test_crawler_link_fields_complete.py` (new)
- `tests/test_chunk_type_crawl.py` (new)
- `tests/integration/test_crawl_sync_end_to_end.py` (new, env-gated)
- `tests/test_crawl_link_fields.py` — 3 tests restored

### klai-retrieval-api

- `app/util/payload.py` (new)
- every reader of `anchor_texts`/`links_to`/`image_urls` switches to
  `payload_list`

### docs

- `docs/architecture/knowledge-ingest-flow.md` § Part 2 — two-phase
  diagram + empty-list convention note
- `.claude/rules/klai/projects/knowledge.md` — new pitfall entry

## Exclusions (What NOT to Build)

- NO changes to klai-connector or klai-portal/backend
- NO Qdrant schema changes
- NO chunk_type semantics redefinition — only wire it
- NO backfill of existing broken chunks; re-sync via frontend refills
- NO new Procrastinate queues
- NO new Qdrant payload indexes

## Constraints

- Each fase independently committable + revertable
- No breaking changes for live consumers (klai-connector delegation
  from SPEC-CRAWLER-004 Fase D continues to work)
- ruff + pyright strict on every touched file
- E2E verified via Playwright + DB queries, not just unit tests
- Clean git history; small focused commits

## References

- SPEC-CRAWLER-004 (pipeline consolidation — Fase E findings drove this)
- SPEC-CRAWLER-003 (link-graph R9-R12)
- SPEC-KB-021 (chunk_type classification)
- Pre-existing failing tests: `tests/test_crawl_link_fields.py`
- `docs/architecture/knowledge-ingest-flow.md` § Part 2 Phase 1 Step 1
- `.claude/rules/klai/projects/knowledge.md` — Procrastinate
  enrichment passthrough pitfall
