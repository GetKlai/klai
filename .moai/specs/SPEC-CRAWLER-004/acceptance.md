# Acceptance Criteria — SPEC-CRAWLER-004

All scenarios in Gherkin Given/When/Then format, grouped per requirement module.
Baseline configuration for all scenarios: Voys tenant (org_id
`368884765035593759`), KB `support` (kb_id 42), connector "Voys Help NL"
(id `414d4f82-f702-4ff2-abd4-c5ce38ae7d61`, `max_pages=20`) unless noted
otherwise.

---

## REQ-CRAWLER-004-01 — Shared Credentials Library

### AC-01.1: Round-trip encryption

```gherkin
Given the shared library klai-libs/connector-credentials is installed
  And the ENCRYPTION_KEY env var contains a valid 64-char hex string
  And a PortalOrg row exists with connector_dek_enc IS NULL
When ConnectorCredentialStore.encrypt_credentials is called with
     connector_type="web_crawler", config={"cookies": [...two cookies...]}
Then the function returns (encrypted_blob, stripped_config)
  And the stripped_config does not contain the "cookies" key
  And calling decrypt_credentials on the same blob returns the original cookies list
  And portal_orgs.connector_dek_enc is no longer NULL
```

### AC-01.2: Cross-org DEK isolation

```gherkin
Given two different orgs each have their own DEK
  And org A has encrypted connector credentials
When org B attempts to decrypt org A's blob using org B's DEK
Then an AES-GCM authentication tag mismatch error is raised
  And no partial plaintext is returned
```

### AC-01.3: Cross-service usability

```gherkin
Given the shared library is listed as a path dependency in
      klai-portal/backend, klai-connector, and klai-knowledge-ingest
When each service's pytest suite is run in isolation
Then every test that imports ConnectorCredentialStore resolves the symbol
     from the shared lib (not from the old klai-portal path)
  And no two services embed a diverged copy of AESGCMCipher
```

### AC-01.4: Missing ENCRYPTION_KEY fails loudly

```gherkin
Given a service attempts to construct ConnectorCredentialStore without
      ENCRYPTION_KEY being set
When the service starts
Then startup fails with a ValueError mentioning "ENCRYPTION_KEY"
  And no partial decrypt path is reachable
```

---

## REQ-CRAWLER-004-02 — Crawl pipeline feature parity

### AC-02.1: Image extraction with Cloudflare srcset debris filtered

```gherkin
Given a crawl4ai result for https://help.voys.nl/index with media.images
      containing srcset fragments "quality=90", "fit=scale-down" alongside
      real URLs
When knowledge-ingest/adapters/crawler.py:_ingest_crawl_result processes the page
Then only valid URLs are attempted for download
  And no HTTP request is made to https://help.voys.nl/quality=90 or
      https://help.voys.nl/fit=scale-down
  And the resulting extra["image_urls"] contains no duplicates
  And the uploaded images land in S3 under bucket kb-images at path
      368884765035593759/images/support/{sha256}.{ext}
```

### AC-02.2: Partial image failure does not block ingest

```gherkin
Given a page with 10 valid image URLs and 2 URLs that return 404
When the crawl adapter processes the page
Then 10 images are uploaded to S3
  And 2 warnings are logged with the failing URLs
  And the page's artifact is upserted successfully
  And no retry loop is triggered for the 404 URLs
```

### AC-02.3: Login_indicator triggers hard fail

```gherkin
Given a crawl-config with login_indicator="#login-form"
  And the crawled page HTML contains a <form id="login-form"> element
When _ingest_crawl_result processes the page
Then an AuthWallDetected exception propagates to run_crawl_job
  And knowledge.crawl_jobs.status becomes "failed"
  And knowledge.crawl_jobs.error starts with "auth_wall_detected:"
  And no artifact row is created for this URL
  And no Qdrant point is upserted
  And the BFS discovery is halted (no further URLs attempted for this run)
```

### AC-02.4: Qdrant payload field completeness

```gherkin
Given a completed sync of https://help.voys.nl via the consolidated pipeline
When a random chunk is fetched from Qdrant collection klai_knowledge with
     filter org_id=368884765035593759 AND kb_slug=support
Then the payload contains source_type="crawl"
  And the payload contains source_label="help.voys.nl"
  And the payload contains source_domain="help.voys.nl"
  And the payload contains a non-None chunk_type in
      {procedural, conceptual, reference, warning, example}
  And the payload contains a list anchor_texts (possibly empty)
  And the payload contains an integer incoming_link_count >= 0
  And the payload contains a list links_to (possibly empty)
```

---

## REQ-CRAWLER-004-03 — Bulk-sync endpoint and delegation

### AC-03.1: Endpoint happy path

```gherkin
Given knowledge-ingest is running with the new endpoint deployed
  And a web_crawler connector exists in portal_connectors
  And X-Internal-Secret header matches the configured secret
When POST /ingest/v1/crawl/sync is called with a valid CrawlSyncRequest body
Then the response status is 202
  And the response body has shape {"job_id": str, "status": "queued"}
  And a new row exists in knowledge.crawl_jobs with status="pending"
  And the Procrastinate queue has one new task with name "run_crawl"
  And the response is returned within 500 ms (p95)
```

### AC-03.2: Endpoint auth is enforced

```gherkin
Given the new endpoint is deployed
When POST /ingest/v1/crawl/sync is called WITHOUT X-Internal-Secret
Then the response status is 401
  And no Procrastinate task is enqueued
  And no crawl_jobs row is created
```

### AC-03.3: Endpoint rejects unknown connector_id

```gherkin
Given the new endpoint is deployed
  And the internal secret is valid
When POST /ingest/v1/crawl/sync is called with a connector_id that does
     not exist in portal_connectors
Then the response status is 404
  And the response body includes "connector_not_found"
  And no task is enqueued
```

### AC-03.4: klai-connector delegation round-trip

```gherkin
Given klai-connector is running with the delegation path enabled
  And a web_crawler connector exists with a valid scheduled sync
When the portal UI "Sync now" button is clicked
Then klai-connector creates a sync_run row with status="running"
  And klai-connector sends exactly ONE HTTP POST to /ingest/v1/crawl/sync
  And the request body contains connector_id (no plaintext cookies)
  And klai-connector stores the returned job_id in
      sync_runs.cursor_state.remote_job_id
  And klai-connector polls /status every 5 seconds until completion
  And when knowledge-ingest finishes, klai-connector closes sync_run with
      status="completed" and documents_ok from the remote response
```

### AC-03.5: Delegation failure mode

```gherkin
Given klai-connector's delegation path is active
  And knowledge-ingest is unreachable (docker stop)
When klai-connector attempts POST /ingest/v1/crawl/sync
Then klai-connector catches the ConnectError after the configured timeout
  And sync_run.status becomes "failed"
  And sync_run.error.details contains "service: knowledge-ingest"
  And no retry is attempted within the same sync cycle
  And a sync_failed product_event is emitted exactly once
```

---

## REQ-CRAWLER-004-04 — Removal of the duplicate pipeline

### AC-04.1: Source tree is clean

```gherkin
Given Fase F has landed on main
When grep -r "WebCrawlerAdapter" klai-connector/ is executed
Then zero matches are returned
  And `find klai-connector/ -name "webcrawler.py"` returns empty
  And `find klai-connector/ -name "content_fingerprint.py"` returns empty
```

### AC-04.2: BaseAdapter contract is minimal

```gherkin
Given Fase F has landed
When klai-connector/app/adapters/base.py is inspected
Then DocumentRef has no "images" attribute
  And DocumentRef has no "content_fingerprint" attribute
  And the ImageRef class does not exist in that file
  And the three remaining attributes (path, ref, size, content_type,
      source_ref, source_url, last_edited) are the only public fields
```

### AC-04.3: Dispatch route is correct

```gherkin
Given a sync request arrives for a connector with connector_type="web_crawler"
When klai-connector/app/services/sync_engine.py dispatches the work
Then no call to adapters.registry.get("web_crawler") is made
  And the dispatch goes directly to the delegation path via CrawlSyncClient
```

### AC-04.4: Regression surface is untouched

```gherkin
Given Fase F has landed
When klai-connector's full pytest suite runs with `uv run pytest tests/`
Then every test for GitHub, Notion, Google Drive, and MS Docs adapters passes
  And every test for sync_engine (non-web parts) passes
  And the removed webcrawler tests are gone (no ImportError, tests deleted)
  And ruff + pyright find zero dangling imports of ImageRef / WebCrawlerAdapter
```

---

## REQ-CRAWLER-004-05 — Validation, smoketest, documentation

### AC-05.1: Voys support smoketest — all dimensions green

```gherkin
Given Fase D has landed and Fase E is being executed
  And the Voys support KB has been reset (artifacts soft-deleted, Qdrant purged,
      connector last_sync_at=NULL)
When the sync is triggered via the portal UI
Then within 5 minutes the sync_run status is "completed"
  And portal_connectors.last_sync_documents_ok == 20
  And the number of Qdrant points for this KB is between 140 and 200
  And for 10 sampled points: source_type="crawl",
      source_label="help.voys.nl", source_domain="help.voys.nl"
  And for hub page chunks (path="index.md"): incoming_link_count > 0
  And for ≥ 80% of points: chunk_type ∈ {procedural, conceptual, reference,
      warning, example}
  And the number of "Image download failed" log entries = 0
```

### AC-05.2: Dual-hash dedup engages on re-sync

```gherkin
Given AC-05.1 has passed
  And the sync has just completed
When a second sync is triggered with no content changes upstream
Then knowledge.crawled_pages still has exactly 20 rows (no duplicates)
  And the logs contain "crawl_skipped_unchanged" for each of the 20 URLs
  And no new Qdrant upsert happens (point count unchanged)
  And no enrichment LLM calls are made (Procrastinate enrich-bulk
      succeeded counter is unchanged)
```

### AC-05.3: Link graph populated

```gherkin
Given AC-05.1 has passed
When knowledge.page_links is queried for from_url LIKE 'https://help.voys.nl%'
Then the row count is > 50
  And every row has a non-empty from_url AND to_url
  And ≥ 90% of rows have non-empty link_text
  And for the chunks of the index.md page, anchor_texts in Qdrant is non-empty
```

### AC-05.4: Redcactus login_indicator guard works

```gherkin
Given a Redcactus connector exists in the Voys tenant with valid cookies
  And the cookies are encrypted via the shared credentials library
When cookies are deliberately corrupted (DB update on encrypted_credentials)
     AND a sync is triggered
Then sync_run.status becomes "failed"
  And sync_run.error.error_type == "auth_wall_detected"
  And no new artifact rows are created
  And no new Qdrant points are upserted
```

### AC-05.5: No plaintext cookies in logs

```gherkin
Given a full sync is in progress for a connector with cookies
When docker logs of klai-connector and knowledge-ingest are captured for the
     duration of the sync
Then no log line contains a substring that matches a known cookie value
      (compared against the decrypted cookie list captured in-test)
  And no product_events row for this sync has plaintext cookies
  And no error message or stack trace includes plaintext cookies
```

### AC-05.6: Documentation updated

```gherkin
Given Fase G has landed
When docs/architecture/knowledge-ingest-flow.md is read
Then § Part 1.2 describes the delegation flow (klai-connector posts to
     /ingest/v1/crawl/sync, knowledge-ingest owns the crawl pipeline)
  And § Part 2 Step 1 notes that image extraction for crawled content happens
     in knowledge-ingest (not klai-connector)
  And § Part 4 mentions the shared connector-credentials library
  And no sentence in the doc refers to klai-connector as "the crawl pipeline"
```

---

## Edge Cases

### EC-1: Very slow crawl (Procrastinate task runs > 30 min)

```gherkin
Given a web_crawler config with max_pages=2000 and a slow target
When the Procrastinate task takes longer than klai-connector's poll timeout (30 min)
Then klai-connector marks sync_run as "timeout"
  And the remote job_id is preserved in cursor_state
  And a later /moai fix or admin retry can resume status polling
  And the Procrastinate task itself is NOT cancelled (it may still complete)
```

### EC-2: Cookie DEK rotation mid-sync

```gherkin
Given a sync is in progress and has already decrypted cookies at task start
When an operator rotates the org's DEK via the shared lib's rotate_kek
Then the currently-running sync continues with the in-memory cookies
  And the next sync picks up the new DEK automatically
  And no sync is left in an inconsistent state
```

### EC-3: Duplicate sync trigger during delegation

```gherkin
Given a sync_run with status="running" exists for a connector
When the portal UI "Sync now" button is clicked a second time
Then klai-connector rejects the trigger with a 409 Conflict
  And no duplicate call is made to /ingest/v1/crawl/sync
  And no duplicate crawl_jobs row is created
```

### EC-4: Shared lib version drift at import time

```gherkin
Given one service has a stale compiled wheel of connector-credentials
When that service starts
Then startup fails with an import or API-mismatch error
  And the service does not silently fall back to a duplicate embedded copy
```

### EC-5: GitHub connector sync during Fase C rollout

```gherkin
Given Fase C is deployed but Fase D is not yet landed
When a GitHub connector sync runs
Then the sync uses the unchanged adapter path in klai-connector
  And does not touch /ingest/v1/crawl/sync
  And all existing tests pass
  And no behaviour changes for Klasse 1 connectors
```

---

## Quality Gate Criteria

| Gate | Threshold | Evidence |
|------|-----------|----------|
| Unit test coverage (new code) | ≥ 85% | `pytest --cov` reports on new modules |
| Regression test suite | 100% pass | Full pytest on klai-connector, knowledge-ingest, klai-portal |
| Ruff lint | 0 errors | `uv run ruff check` all repos |
| Pyright strict | 0 errors in touched files | `uv run pyright` on modified + new files |
| Endpoint p95 latency (Fase C) | < 500 ms | Locally timed with synthetic request |
| Smoketest chunk count | 140-200 chunks for 20 URLs | Qdrant count query |
| Log redaction (REQ-05.4) | 0 plaintext cookie hits | grep -E on 5-minute log window during sync |
| Credential DEK round-trip | 100% (no auth tag failures) | AC-01.1 and AC-01.2 |
