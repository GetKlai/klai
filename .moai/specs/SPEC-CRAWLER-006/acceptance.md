# Acceptance Criteria — SPEC-CRAWLER-006

Baseline: Voys tenant (`zitadel_org_id 368884765035593759`), KB `support`,
connector "Redcactus" (`fdde0c1e-7a31-4810-9906-d3e032b3a815`).

---

## REQ-CRAWLER-006-01 — Fire-and-forget enqueue

### AC-01.1: Enqueue returns immediately with RUNNING status

```gherkin
Given a web_crawler connector with valid config
  And knowledge-ingest /crawl/sync returns 200 with job_id "abc123"
When _run_web_crawler_delegation is invoked
Then sync_run.cursor_state["remote_job_id"] equals "abc123"
  And sync_run.status equals SyncStatus.RUNNING
  And the function returns within 5 seconds
  And no call is made to crawl_sync_status
```

### AC-01.2: Enqueue HTTP error fails the sync_run

```gherkin
Given knowledge-ingest /crawl/sync returns 500
When _run_web_crawler_delegation is invoked
Then sync_run.status equals SyncStatus.FAILED
  And error_details[0].error equals "http_500"
  And error_details[0].service equals "knowledge-ingest"
```

### AC-01.3: SSRF rejection still fails before enqueue

```gherkin
Given the persisted config holds an SSRF-blocked base_url
When _run_web_crawler_delegation is invoked
Then no call is made to /crawl/sync
  And sync_run.status equals SyncStatus.FAILED
  And error_details[0].error equals "ssrf_blocked_persisted_url"
```

---

## REQ-CRAWLER-006-02 — No synchronous wait

### AC-02.1: Poll loop is gone

```gherkin
Given the codebase at HEAD
When grep -n "_WEB_CRAWLER_POLL_TIMEOUT_S\|poll_interval = self._WEB_CRAWLER" runs
  inside klai-connector/app/services/sync_engine.py
Then no matches are returned
```

### AC-02.2: No call to crawl_sync_status from sync_engine

```gherkin
Given the codebase at HEAD
When grep -n "crawl_sync_status" runs inside klai-connector/app/services/sync_engine.py
Then no matches are returned
```

---

## REQ-CRAWLER-006-03 — No cancel on timeout path

### AC-03.1: No call to crawl_sync_cancel from sync_engine

```gherkin
Given the codebase at HEAD
When grep -n "crawl_sync_cancel" runs inside klai-connector/app/services/sync_engine.py
Then no matches are returned
```

---

## REQ-CRAWLER-006-04 — Live status resolution

### AC-04.1: Running run resolves live progress

```gherkin
Given a sync_run with status=RUNNING and remote_job_id="abc123"
  And knowledge-ingest /crawl/sync/abc123/status returns
      {"status":"running","pages_done":42,"pages_total":500,"error":null}
When portal-api requests the sync_run via the connector internal
     proxy endpoint
Then the response shape includes
    {"status":"running","pages_done":42,"pages_total":500}
  And the local sync_run row is unchanged
```

### AC-04.2: Completed remote terminalizes the local row

```gherkin
Given a sync_run with status=RUNNING and remote_job_id="abc123"
  And knowledge-ingest /crawl/sync/abc123/status returns
      {"status":"completed","pages_done":368,"pages_total":368,"error":null}
When portal-api requests the sync_run
Then the local sync_run row is updated to
    status=COMPLETED, documents_ok=368, documents_total=368
  And error_details is NULL
  And a product_event of type "connector.sync_completed" is emitted
     exactly once for this sync_run
```

### AC-04.3: Failed remote terminalizes the local row

```gherkin
Given a sync_run with status=RUNNING and remote_job_id="abc123"
  And knowledge-ingest returns
      {"status":"failed","pages_done":17,"pages_total":500,"error":"timeout_per_page"}
When portal-api requests the sync_run
Then the local sync_run row is updated to
    status=FAILED, documents_ok=17, documents_failed=483
  And error_details[0].error equals "timeout_per_page"
  And error_details[0].service equals "knowledge-ingest"
```

### AC-04.4: Live resolution failure surfaces a flag

```gherkin
Given a sync_run with status=RUNNING and remote_job_id="abc123"
  And knowledge-ingest /crawl/sync/abc123/status raises ConnectError
When portal-api requests the sync_run
Then the response shape includes
    {"status":"running","live_resolution_failed":true}
  And no log entry at ERROR level is emitted
     (warning is acceptable)
```

---

## REQ-CRAWLER-006-05 — Live resolution caching

### AC-05.1: Repeated reads within 30s hit the cache

```gherkin
Given a sync_run with status=RUNNING and remote_job_id="abc123"
When portal-api requests the sync_run twice within 30 seconds
Then knowledge-ingest /crawl/sync/abc123/status is called exactly once
```

### AC-05.2: Cache expires after 30s

```gherkin
Given a sync_run with status=RUNNING
When portal-api requests it at t=0 and t=31s
Then knowledge-ingest /crawl/sync/<job>/status is called exactly twice
```

---

## REQ-CRAWLER-006-06 — Reaper for orphan running rows

### AC-06.1: Reaper finalizes a terminal remote

```gherkin
Given a sync_run with status=RUNNING, started_at older than 24h
  And knowledge-ingest returns status=completed for its remote_job_id
When the reaper tick runs
Then the sync_run is updated to status=COMPLETED with the remote counts
```

### AC-06.2: Reaper marks 404 as remote_job_lost

```gherkin
Given a sync_run with status=RUNNING, started_at older than 24h
  And knowledge-ingest returns 404 for its remote_job_id
When the reaper tick runs
Then the sync_run is updated to status=FAILED
  And error_details[0].error equals "remote_job_lost"
```

### AC-06.3: Reaper leaves still-running rows alone (under 7d)

```gherkin
Given a sync_run with status=RUNNING, started_at 25h ago
  And knowledge-ingest returns status=running
When the reaper tick runs
Then the sync_run row is unchanged
```

### AC-06.4: Reaper force-fails after 7 days

```gherkin
Given a sync_run with status=RUNNING, started_at 8 days ago
  And knowledge-ingest returns status=running
When the reaper tick runs
Then the sync_run is updated to status=FAILED
  And error_details[0].error equals "remote_job_stuck"
```

---

## REQ-CRAWLER-006-07 — Backfill of historical timeouts

### AC-07.1: Voys/Redcactus 2026-05-01 row is corrected

```gherkin
Given the production sync_run at id=<2026-05-01 12:03 row>
  with status=failed, error.error="web_crawler_poll_timeout",
       cursor_state.remote_job_id="4adc7afd-9436-4752-bfce-d780accfdb55"
  And knowledge.crawl_jobs row 4adc7afd has status=completed
       and pages_done=368
When the backfill alembic migration runs
Then the sync_run is updated to status=completed, documents_ok=368
  And error_details is NULL
  And an audit row is inserted in connector.sync_run_corrections
     with original_status=failed, new_status=completed
```

### AC-07.2: Genuinely failed crawls are left alone

```gherkin
Given a sync_run with status=failed, error.error="web_crawler_poll_timeout"
  And knowledge.crawl_jobs row for its remote_job_id has status=failed
When the backfill alembic migration runs
Then the sync_run row is unchanged
  And no audit row is inserted
```

### AC-07.3: Missing crawl_jobs row leaves sync_run alone

```gherkin
Given a sync_run with status=failed, error.error="web_crawler_poll_timeout"
  And no row exists in knowledge.crawl_jobs for its remote_job_id
When the backfill alembic migration runs
Then the sync_run row is unchanged
  And a structured warning is logged with sync_run id
```

---

## REQ-CRAWLER-006-08 — Frontend live progress

### AC-08.1: Running web_crawler shows progress bar

```gherkin
Given the user is on /app/knowledge/support/edit-connector/fdde0c1e-...
  And the most recent sync_run is in status=running with pages_done=42, pages_total=500
When the page renders
Then the badge text matches "Bezig — 42/500 pagina's"
  And a progress bar is rendered at 8% width
```

### AC-08.2: Completed run shows count

```gherkin
Given the most recent sync_run is in status=completed with documents_ok=368
When the page renders
Then the badge text matches "Voltooid — 368 pagina's"
```

### AC-08.3: Failed run shows reason

```gherkin
Given the most recent sync_run is in status=failed with error.error="timeout_per_page"
When the page renders
Then the badge text matches "Mislukt — timeout_per_page"
```

### AC-08.4: Non-crawler connectors unchanged

```gherkin
Given a notion connector with sync_run.status=running
When the page renders
Then the badge text matches the existing pattern
     (no progress bar; this SPEC adds rendering only for web_crawler)
```
