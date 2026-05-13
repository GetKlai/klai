# Acceptance Criteria — SPEC-INGEST-LOGIN-WALL-DETECT-001

All scenarios use Gherkin Given/When/Then format, grouped per requirement.
Baseline configuration: voys tenant (org_id `368884765035593759`), KB `support`,
unless noted otherwise. Negative-case fixtures live in
`klai-knowledge-ingest/tests/fixtures/auth_walls/` (positive) and
`tests/fixtures/clean_pages/` (negative).

---

## REQ-01 — Pure detector function

### AC-01.1: Detector is importable and pure

```gherkin
Given the package klai-knowledge-ingest is installed in the test environment
When test code imports detect_anonymous_auth_wall from
     knowledge_ingest.utils.auth_wall_detector
Then the import succeeds without instantiating any clients or pools
  And calling the function with a fixed input produces a deterministic output
  And running it 1000 times consecutively produces no log lines, no I/O, and
      no metric emissions
```

### AC-01.2: Detector p99 latency

```gherkin
Given a 100 KB markdown string sampled from a real walled page
When detect_anonymous_auth_wall is called 10000 times
Then the 99th percentile execution time is below 1 millisecond
  And the 50th percentile execution time is below 0.2 milliseconds
```

### AC-01.3: fit_markdown takes precedence

```gherkin
Given a CrawlResult with raw_markdown containing "log in to read" 6 times
  And fit_markdown containing 800 words of clean tutorial content with no
      login phrases
When detect_anonymous_auth_wall(raw_markdown, fit_markdown=fit_markdown)
     is called
Then the result is None (false-positive guard activates because fit_markdown
     is clean)
```

---

## REQ-02 — Detection patterns

### AC-02.1: Canonical English phrase match

```gherkin
Given a markdown string containing "you will have to log in with your Red
      Cactus account"
When the detector is called with no fit_markdown override
Then the returned AuthWallSignal has pattern="canonical_phrase_en"
  And evidence contains the matched substring
  And confidence is >= 0.9
```

### AC-02.2: Dutch phrase match

```gherkin
Given a markdown string containing "U dient in te loggen om verder te gaan"
When the detector is called
Then the returned AuthWallSignal has pattern="canonical_phrase_nl"
  And evidence contains the matched substring (case-preserved for clarity)
```

### AC-02.2b: German phrase explicitly NOT matched

```gherkin
Given a markdown string containing "Bitte melden Sie sich an, um fortzufahren"
  And no English or Dutch login phrases are present
  And no other detection conditions match
When the detector is called
Then the result is None
  And the test documents that DE coverage is intentionally deferred
```

### AC-02.3: Login-redirect URL density

```gherkin
Given a markdown string with no canonical login phrases
  But containing 7 occurrences of "redirect_to=" in different anchor hrefs
When the detector is called
Then the returned AuthWallSignal has pattern="redirect_density"
  And evidence is the count "7 redirect_to= occurrences"
```

### AC-02.4: Login-link repetition

```gherkin
Given a markdown string containing 6 anchors with href ending in /login
  And the same href "/login?redirect_to=/article/123" appears 6 times
When the detector is called
Then the returned AuthWallSignal has pattern="login_link_repetition"
  And evidence is the repeated href and count
```

### AC-02.5: Content-to-login ratio

```gherkin
Given a markdown string with 80 non-login words
  And 4 login anchors
When the detector is called
Then the returned AuthWallSignal has pattern="content_login_ratio"
  And evidence is "80 content words, 4 login anchors"
```

### AC-02.6: False-positive guard — auth documentation page (WEAK signals only)

```gherkin
Given a markdown string with 800 words explaining "How sign-in works in
      our app" as a tutorial
  And the page contains 6 anchors pointing to /login as navigation chrome
  And NO canonical phrase from Condition A is present
When the detector is called
Then the result is None
  And no false positive is emitted
  And the guard's reasoning is "weak_signal_with_clean_content"
```

### AC-02.6b: Canonical phrase fires regardless of content length

```gherkin
Given a markdown string with 3243 content words (real RedCactus HubSpot
      page captured 2026-05-06)
  And the canonical phrase "you will have to log in with your" appears
      twice
  And no other conditions match
When the detector is called
Then the returned AuthWallSignal has pattern="canonical_phrase_en"
  And the FP-guard does NOT veto (Condition A is a strong signal)
  And confidence is >= 0.9
```

### AC-02.7: Real RedCactus HubSpot fixture flags positive

```gherkin
Given the captured raw_markdown of
      https://wiki.redcactus.cloud/nl/crm-software/HubSpot
      stored at tests/fixtures/auth_walls/redcactus_hubspot.md
When detect_anonymous_auth_wall is called with that markdown
Then the result is a non-None AuthWallSignal
  And confidence is >= 0.9
```

### AC-02.8: Real voys help page does not flag

```gherkin
Given the captured raw_markdown of
      https://help.voys.nl/article/cancellation-procedure
      stored at tests/fixtures/clean_pages/voys_help_cancellation.md
When detect_anonymous_auth_wall is called with that markdown
Then the result is None
```

---

## REQ-03 — Reject behaviour

### AC-03.1: Default mode rejects with typed exception

```gherkin
Given KLAI_INGEST_LOGIN_WALL_DETECT_MODE is unset (defaults to "reject")
  And a CrawlResult for a known walled URL with success=True
  And login_indicator_selector=None
When _ingest_crawl_result is called
Then AnonymousAuthWallDetected is raised with attributes:
       url == result.url
       signal.pattern == matched pattern
  And no Qdrant points are inserted for that URL
  And no row is inserted into knowledge.crawled_pages for that URL
```

### AC-03.2: Degrade mode ingests with quality_score=0

```gherkin
Given KLAI_INGEST_LOGIN_WALL_DETECT_MODE="degrade"
  And a CrawlResult for a known walled URL with success=True
When _ingest_crawl_result is called
Then no exception is raised
  And the resulting Qdrant points have payload.quality_score == 0.0
  And payload.metadata.ingest_warning == "login_wall_detected"
  And one INFO log line is emitted with event="login_wall_degrade"
```

### AC-03.3: Audit-only mode ingests unchanged

```gherkin
Given KLAI_INGEST_LOGIN_WALL_DETECT_MODE="audit_only"
  And a CrawlResult for a known walled URL with success=True
When _ingest_crawl_result is called
Then no exception is raised
  And the resulting Qdrant points have payload.quality_score == 0.5
      (existing default)
  And one WARN log line is emitted with event="login_wall_detected"
```

---

## REQ-04 — Caller behaviour (BFS continuity)

### AC-04.1: Anonymous wall does NOT halt BFS

```gherkin
Given crawl_site returns three CrawlResults:
       page_A (clean), page_B (anonymous-walled), page_C (clean)
  And login_indicator_selector is None (anonymous crawl)
  And mode is "reject"
When run_crawl_job processes them
Then page_A is ingested
  And page_B raises AnonymousAuthWallDetected and is skipped
  And page_C IS still ingested (BFS continues, unlike SPEC-CRAWLER-004
      authenticated halt)
  And the job's auth_wall_pages list contains exactly one entry for page_B
```

### AC-04.2: Job summary written when walls detected

```gherkin
Given a crawl job ingested 5 pages and skipped 3 walled pages
When run_crawl_job's finally block executes
Then crawl_jobs.error_summary contains JSON
       {"login_walls_skipped": 3, "sample_urls": ["url1", "url2", "url3"]}
  And crawl_jobs.status == "succeeded" (because >= 1 page ingested)
```

### AC-04.3: Job marked failed_partial when zero pages ingested

```gherkin
Given a crawl job skipped 10 walled pages and ingested 0 pages
When run_crawl_job's finally block executes
Then crawl_jobs.status == "failed_partial"
  And error_summary contains login_walls_skipped >= 10
  And the job does not raise to the caller
```

### AC-04.4: Authenticated halt path is unchanged

```gherkin
Given login_indicator_selector="#login-form"
  And crawl4ai returns success=False for one of the pages
When run_crawl_job processes them
Then AuthWallDetected is raised
  And BFS halts (existing SPEC-CRAWLER-004 behaviour preserved)
  And error_summary is not written (different code path)
  And status == "failed", error == "auth_wall_detected: #login-form"
```

---

## REQ-05 — Configuration

### AC-05.1: Disabled flag short-circuits detector

```gherkin
Given KLAI_INGEST_LOGIN_WALL_DETECT_ENABLED="false"
  And a CrawlResult for a known walled URL
When _ingest_crawl_result is called
Then detect_anonymous_auth_wall is NOT called
  And the page is ingested normally with quality_score=0.5
  And no log line about login walls is emitted
```

### AC-05.2: Invalid mode falls back safely

```gherkin
Given KLAI_INGEST_LOGIN_WALL_DETECT_MODE="invalid_value"
When the module is imported
Then a WARN log is emitted with event="login_wall_detector_config_invalid"
  And the effective mode is "audit_only" (fail-safe per non-functional
      reliability)
  And the application starts successfully
```

---

## REQ-06 — Backfill task

### AC-06.1: Backfill detects and deletes walled pages

```gherkin
Given the support KB for org_id "368884765035593759" contains 150 pages
      whose raw_markdown matches the detector
  And those pages have corresponding Qdrant points in the klai_knowledge
      collection
When backfill_detect_login_walls is invoked with that org_id and kb_slug
Then the task returns {"processed": 422, "flagged": 150,
                       "qdrant_deleted": >= 150}
  And the corresponding Qdrant points are removed (verified by counting
      points where payload.path matches one of the 150 URLs)
  And crawled_pages.content_hash for those rows equals "__login_wall_purged__"
  And the task completes within 60 seconds for a 422-page tenant
```

### AC-06.2: Backfill is idempotent

```gherkin
Given backfill_detect_login_walls already ran successfully for a tenant
  And no new pages were ingested since
When backfill_detect_login_walls is invoked again with the same args
Then the task returns {"processed": <original>, "flagged": 0,
                       "qdrant_deleted": 0}
  And no Qdrant deletes are issued (early-skip on placeholder hash)
```

### AC-06.3: Backfill respects tenant isolation

```gherkin
Given two tenants, voys and getklai, both have walled pages
When backfill_detect_login_walls is invoked for voys only
Then no getklai pages are inspected
  And no getklai Qdrant points are deleted
  And the Postgres queries set app.current_org_id to voys's org_id only
```

---

## REQ-07 — Retrieval-side hard floor

### AC-07.1: Floor filters degraded chunks

```gherkin
Given a Qdrant chunk with payload.quality_score=0.0 in the support KB
  And a query whose reranker would otherwise rank that chunk top-1
When retrieve.py runs the full pipeline with KLAI_RETRIEVAL_QUALITY_FLOOR=0.05
Then the chunk is excluded from the served top_k
  And retrieval_decision_record.quality_floor_filtered >= 1
  And the chunk's ID is logged at DEBUG level
```

### AC-07.2: Floor preserves neutral-quality chunks

```gherkin
Given a Qdrant chunk with payload.quality_score=0.5 (existing default)
When retrieve.py runs with KLAI_RETRIEVAL_QUALITY_FLOOR=0.05
Then the chunk is NOT filtered
  And it appears in the served results based on its rerank score
```

### AC-07.3: Floor is configurable per-deployment

```gherkin
Given KLAI_RETRIEVAL_QUALITY_FLOOR is set to "0.6" in a non-production env
When retrieve.py runs
Then chunks with quality_score < 0.6 are filtered (e.g., 0.5 default chunks)
  And the change does not require a code deploy
```

---

## REQ-08 — Observability

### AC-08.1: Counter increments per detection

```gherkin
Given KLAI_INGEST_LOGIN_WALL_DETECT_MODE="reject"
  And 3 walled pages are processed in a single crawl job
When the job completes
Then klai_ingest_login_wall_detected_total{org_id="368884765035593759",
     kb_slug="support", mode="reject"} has increased by exactly 3
```

### AC-08.2: Retrieval-floor counter

```gherkin
Given a query that surfaces 2 chunks with quality_score=0.0 before filtering
When the retrieve endpoint completes
Then klai_retrieval_quality_floor_filtered_total{org_id=...} has increased
     by exactly 2
```

### AC-08.3: Detector latency histogram populated

```gherkin
Given the detector runs at least 100 times in a 1-minute window
When `histogram_quantile(0.99, rate(klai_ingest_login_wall_detector_latency_ms_bucket[1m]))`
     is queried
Then the result is below 1.0 (millisecond)
```

### AC-08.4: High-walled-job alert fires

```gherkin
Given a single crawl job processed 100 pages
  And login_walls_skipped == 25 (25%)
When the alert evaluation runs
Then the critical alert "knowledge-ingest: high login-wall ratio" enters
     firing state
  And the alert payload includes org_id, kb_slug, and the ratio
```

---

## REQ-09 — Tenant isolation

### AC-09.1: Qdrant delete filter contains org_id

```gherkin
Given the backfill task is implementing point deletion
When the developer attempts to delete points using only path or kb_slug
Then the semgrep rule from .github/workflows/tenant-isolation-review.yml
     fails the PR
  And the rule message references "FieldCondition(key='org_id', ...)"
```

### AC-09.2: Backfill query sets RLS context

```gherkin
Given the backfill task is reading knowledge.crawled_pages
When the implementing code is reviewed
Then every SELECT/UPDATE on knowledge.* is preceded by a SET LOCAL
     app.current_org_id = $1 (or equivalent helper)
  And the backfill cannot return rows from a different org under any code path
```

---

## REQ-10 — Re-crawl smoke test

### AC-10.1: End-to-end smoke test passes

```gherkin
Given the implementation is deployed to staging (or production with rollback
      plan)
  And mode="reject" is active
When a single-URL crawl is triggered for
     https://wiki.redcactus.cloud/nl/crm-software/HubSpot
Then the resulting crawl_jobs row has
       error_summary.login_walls_skipped == 1
       status in ("succeeded" if other URLs ingested, else "failed_partial")
  And no new Qdrant points reference that URL
```

### AC-10.2: Original failing query produces honest answer

```gherkin
Given backfill_detect_login_walls has run for voys/support
  And the smoke-test re-crawl in AC-10.1 has run
When the LiteLLM call equivalent to "Hoe stel ik RedCactus in met HubSpot?"
     fires for voys/mark.vletter@voys.nl
Then retrieval-api logs show either:
       reranker_scores_top5 all < 0.4 AND gap_type="hard"
     OR
       chunks served are NOT from wiki.redcactus.cloud
  And the chat response does not fabricate setup steps
```

### AC-10.3: Production metrics confirm cleanup

```gherkin
Given backfill_detect_login_walls has been run for voys/support
When 7 days have passed
Then klai_ingest_login_wall_detected_total{org_id="voys's org"} has not
     incremented during normal scheduled re-crawls (because the URLs were
     already purged and re-crawls now reject at ingest time)
  And the Grafana panel shows zero login-wall detections for voys
```
