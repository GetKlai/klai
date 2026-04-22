# Acceptance Criteria — SPEC-CRAWLER-005

All scenarios in Gherkin Given/When/Then format, grouped per requirement
module. Baseline for live scenarios: Voys tenant (zitadel_org_id
`368884765035593759`), KB `support`, connector "Voys Help NL"
(`414d4f82-f702-4ff2-abd4-c5ce38ae7d61`, `max_pages=20`).

---

## REQ-CRAWLER-005-01 — Two-phase pipeline

### AC-01.1: `_build_link_graph` runs before every ingest

```gherkin
Given a mocked crawl returning 5 CrawlResult objects with
      result.links.internal populated
When run_crawl_job is invoked
Then pg_store.upsert_page_links is called exactly 5 times
  And all 5 calls happen before the first call to _ingest_crawl_result
  And the 5 calls' from_url values equal the URLs of the 5 results
```

### AC-01.2: `_ingest_crawl_result` no longer touches page_links

```gherkin
Given a single CrawlResult with result.links.internal = [{"href": "...", "text": "..."}]
When _ingest_crawl_result is invoked directly
Then pg_store.upsert_page_links is NOT called from within _ingest_crawl_result
```

### AC-01.3: Late pages see the full graph

```gherkin
Given 5 pages A, B, C, D, E crawled with the link set
      A->B, A->C, B->C, C->D, D->E, E->A
  And _build_link_graph has upserted every page_links row
When _ingest_crawl_result processes page A (the FIRST ingested page)
Then link_graph.get_anchor_texts(A) returns anchor texts from E's link to A
  And link_graph.get_incoming_count(A) returns 1 (E links to A)
  And link_graph.get_outbound_urls(A) returns [B, C]
```

---

## REQ-CRAWLER-005-02 — Qdrant payload completeness

### AC-02.1: incoming_link_count final at ingest time

```gherkin
Given a completed 5-page cross-linked synthetic crawl
When the Qdrant chunks are read back after enrichment finishes
Then every chunk has an integer incoming_link_count
  And no chunk required a post-crawl set_payload call to reach that state
  And the values match link_graph.compute_incoming_counts() output
```

### AC-02.2: anchor_texts non-empty on pages with inbound links

```gherkin
Given a page P with 3 internal pages linking to it with anchor text
When the Qdrant chunks for P are read
Then each chunk's payload contains a non-empty anchor_texts list
  And the list content is a permutation of those 3 anchor texts
```

### AC-02.3: links_to capped at 20

```gherkin
Given a page P with 35 outbound internal links
When the Qdrant chunks for P are read
Then each chunk's payload contains links_to with exactly 20 URLs
  And no URL in links_to is duplicated
```

### AC-02.4: Empty == absent, by convention

```gherkin
Given a leaf page L with no inbound internal links
When the Qdrant chunks for L are read
Then anchor_texts is absent from each chunk's payload
  And the retrieval-api payload_list helper returns [] for anchor_texts
```

---

## REQ-CRAWLER-005-03 — chunk_type on crawl chunks

### AC-03.1: LLM-classified chunks carry chunk_type

```gherkin
Given a crawl chunk whose LLM enrichment returned
      chunk_type="procedural"
When enrich_document_bulk completes and upserts the chunk
Then the Qdrant payload contains chunk_type="procedural"
```

### AC-03.2: Invalid LLM response is logged and recovered

```gherkin
Given a crawl chunk whose LLM enrichment returned chunk_type=""
When enrich_document_bulk processes the chunk
Then a warning log fires with artifact_id, chunk_index, and
     a raw_llm_response preview of max 200 chars
  And a retry or fallback produces a valid chunk_type
  And the Qdrant payload contains a valid chunk_type value
```

### AC-03.3: Voys baseline threshold

```gherkin
Given the consolidated pipeline re-crawls help.voys.nl (20 pages)
When enrichment finishes for every page
Then >= 80% of Qdrant crawl chunks have chunk_type in
     {procedural, conceptual, reference, warning, example}
```

---

## REQ-CRAWLER-005-04 — Empty-list convention helper

### AC-04.1: payload_list handles every input shape

```gherkin
Given a payload {"anchor_texts": ["a", "b"]}
When payload_list(payload, "anchor_texts") is called
Then the return value is ["a", "b"]

Given a payload {"anchor_texts": None}
When payload_list(payload, "anchor_texts") is called
Then the return value is []

Given a payload without the key
When payload_list(payload, "anchor_texts") is called
Then the return value is []

Given a payload where anchor_texts is a non-list value
When payload_list(payload, "anchor_texts") is called
Then the return value is []
```

### AC-04.2: Every retrieval-api reader uses payload_list

```gherkin
Given klai-retrieval-api grepped for "anchor_texts" | "links_to" |
      "image_urls" in its app/ directory
When the occurrences are inspected after this SPEC lands
Then every read of these keys goes through payload_list (or an
     equivalent helper wrapping the same semantics)
```

---

## REQ-CRAWLER-005-05 — Removed / deprecated code

### AC-05.1: No production caller of update_link_counts remains

```gherkin
Given ripgrep "update_link_counts" across klai-knowledge-ingest/
When the results are inspected
Then the only matches are in the function definition,
     its deprecation docstring,
     and (optionally) repair scripts under a clearly-marked admin/ dir
```

### AC-05.2: compute_incoming_counts gets the same treatment

```gherkin
Given ripgrep "compute_incoming_counts" across klai-knowledge-ingest/
When the results are inspected
Then no call from run_crawl_job remains
```

---

## REQ-CRAWLER-005-06 — Regression

### AC-06.1: Three pre-existing tests pass

```gherkin
Given tests/test_crawl_link_fields.py unchanged assertions
When uv run pytest tests/test_crawl_link_fields.py is executed
Then all three tests that failed on main now pass
```

### AC-06.2: No knowledge-ingest regression

```gherkin
Given the full knowledge-ingest suite ignoring
      test_adapters_scribe_chunking.py (pre-existing collection error)
When uv run pytest is executed
Then the passing count is >= 405 (402 baseline + 3 restored tests)
  And the failing count is exactly the 16 documented pre-existing
     failures (test_knowledge_fields.py + test_enrichment_dedup.py +
     test_ingest_debounce.py + test_assertion_mode_taxonomy.py +
     test_clustering.py + test_crawl_link_fields.py original trio
     → the trio now passes)
```

---

## REQ-CRAWLER-005-07 — Integration test

### AC-07.1: Docker-compose integration crawl passes

```gherkin
Given docker-compose with postgres + qdrant + knowledge-ingest
      + stub crawl4ai server serving 4 cross-linked HTML pages
When tests/integration/test_crawl_sync_end_to_end.py is run with
     RUN_INTEGRATION=1
Then the Qdrant chunks for all 4 pages have
     source_type=crawl, source_label set, anchor_texts correct,
     links_to correct, incoming_link_count correct
  And the test completes in < 60 s
```

---

## REQ-CRAWLER-005-08 — Playwright E2E on Voys `support`

### AC-08.1: Fresh sync produces complete payload

```gherkin
Given the Voys `support` KB has been reset via the portal UI
  And the help.voys.nl connector is triggered via "Sync now" in the UI
When the sync reaches status=completed in the UI
Then connector.sync_runs row status=completed, documents_ok=20
  And cursor_state.remote_job_id is a valid UUID
  And knowledge.crawl_jobs[remote_job_id].status=completed
  And knowledge.crawled_pages has exactly 20 rows
  And knowledge.page_links has >= 50 rows
  And Qdrant chunk count (filter org+kb+source_type=crawl) is 140-200
  And 10 randomly-sampled chunks ALL have
      source_type=crawl, source_label=help.voys.nl,
      source_domain=help.voys.nl, non-empty anchor_texts OR
      absent (for orphan leaf pages), non-empty links_to OR absent,
      integer incoming_link_count
  And >= 80% of sampled chunks have chunk_type in the enum set
  And hub pages (incoming_link_count > 0) have incoming_link_count > 0
```

### AC-08.2: No plaintext cookies in logs (REQ-05.4 still holds)

```gherkin
Given the sync ran with the Voys connector (no cookies in this case,
      but the contract is general)
When docker logs of klai-connector + knowledge-ingest are grepped for
     any previously-captured plaintext cookie substring of >= 30 chars
Then zero matches are returned
```

### AC-08.3: Second sync is fully deduped

```gherkin
Given AC-08.1 passed
When "Sync now" is clicked a second time via the UI
Then the sync completes with documents_ok=20 (or 0 if the engine
     short-circuits)
  And knowledge.crawled_pages still has exactly 20 rows
  And docker logs contain 20 "crawl_skipped_unchanged" entries
  And Qdrant chunk count is unchanged
  And no new Procrastinate enrich_document_bulk task is enqueued
     for any of the 20 paths
```

---

## Edge Cases

### EC-1: Page with only inbound links, no outbound

```gherkin
Given a leaf page L that is linked to from 3 pages but has no
      internal outbound links of its own
When run_crawl_job finishes
Then Qdrant chunks for L have a non-empty anchor_texts
  And Qdrant chunks for L have no links_to key (absent, == empty)
  And Qdrant chunks for L have incoming_link_count = 3
```

### EC-2: Page with only outbound links, no inbound

```gherkin
Given a root page R with 5 outbound internal links and 0 inbound
When run_crawl_job finishes
Then Qdrant chunks for R have a 5-element links_to list
  And Qdrant chunks for R have no anchor_texts key (absent, == empty)
  And Qdrant chunks for R have incoming_link_count = 0
```

### EC-3: Orphan crawl (page in crawl set but link graph is empty)

```gherkin
Given a single-page crawl with no internal links at all
When run_crawl_job finishes
Then knowledge.page_links is empty for this KB
  And Qdrant chunk for the page has no anchor_texts, no links_to
  And incoming_link_count = 0
```

### EC-4: chunk_type retry exhausts

```gherkin
Given a chunk whose LLM call keeps returning empty chunk_type
When retries are exhausted (e.g. 1 retry attempted)
Then the chunk is written to Qdrant with a fallback chunk_type
     (default="reference" until AC-03 approves a different default)
  And a warning log captures the fallback path
```

### EC-5: Concurrent KB delete during Fase 6 E2E

```gherkin
Given the agent starts the Voys support delete via the UI
  And an unrelated admin triggers a second operation on the same KB
When both operations settle
Then the agent's sync verification in AC-08.1 still passes
     (exactly 20 crawled_pages, regardless of interleaving)
  And no orphan row or duplicate sync_run is left behind
```

---

## Quality Gate Criteria

| Gate | Threshold | Evidence |
|------|-----------|----------|
| Unit test coverage (new modules) | >= 85% | `pytest --cov` on `_build_link_graph` + chunk_type fix + payload_list |
| Regression test suite | 100% pass on all current-passing tests | Full pytest on klai-knowledge-ingest, klai-connector, klai-retrieval-api |
| Ruff + pyright strict | 0 errors on every touched file | `uv run ruff check` and `uv run pyright` (or pyright strict where already enabled) |
| Voys baseline chunk count | 140-200 crawl chunks for 20 URLs | Qdrant count query after Fase 6 re-sync |
| Voys `chunk_type` coverage | >= 80% | Sampled Qdrant query after Fase 6 |
| Log redaction | 0 plaintext cookie hits | grep on 10-min log window after Fase 6 sync |
| page_links row count | >= 50 for 20 Voys pages | Postgres count query after Fase 6 |
| Second-sync dedup | 20x `crawl_skipped_unchanged` | docker logs query after second Fase 6 sync |
