---
id: SPEC-INGEST-LOGIN-WALL-DETECT-001
version: "1.1"
status: superseded
superseded_by: SPEC-INGEST-LOGIN-WALL-DETECT-002
created: 2026-05-06
updated: 2026-05-06
author: Mark Vletter
priority: high
issue_number: TBD
related_specs:
  - SPEC-INGEST-LOGIN-WALL-DETECT-002 (replaces phrase-detector with SimHash near-duplicate clustering)
  - SPEC-CRAWLER-004 (authenticated login-indicator guard — complementary)
  - SPEC-KB-014 (gap detection — complementary, fires on low reranker scores)
  - SPEC-KB-015 (quality_boost — extended here with hard floor filter)
  - SPEC-INGEST-QUEUE-SEPARATION-001 (queue infrastructure for backfill task)
---

> **Superseded by SPEC-INGEST-LOGIN-WALL-DETECT-002.**
>
> The phrase-substring detector defined here was found to FP at 2.6% on
> production voys/support content (4 NL + 1 EN). v2 replaces the detector
> with SimHash near-duplicate clustering — same caller signature, same
> mode flags, but the underlying detection mechanism targets templating
> structure instead of phrasing. See `../SPEC-INGEST-LOGIN-WALL-DETECT-002/`
> for the redesign and rationale.

## HISTORY

| Version | Date | Author | Change |
|---------|------|--------|--------|
| 1.0 | 2026-05-06 | Mark Vletter | Initial draft after voys/support-KB redcactus retrieval incident — 150/422 (35%) crawled pages were anonymous-crawl login-stubs surfacing in retrieval with reranker scores 0.71–0.84. |
| 1.1 | 2026-05-06 | Mark Vletter | Marked superseded by SPEC-INGEST-LOGIN-WALL-DETECT-002 after the production canary on voys/support exposed v1's substring-matching detector as fundamentally wrong (FP rate 2.6% on legitimate content). v2 replaces the detector logic with SimHash near-duplicate clustering; mode flags and caller signature are preserved. |

---

# SPEC-INGEST-LOGIN-WALL-DETECT-001: Anonymous-crawl login-wall detection at ingest

## Context

### Incident summary (2026-05-06)

Voys user `mark.vletter@voys.nl` (org `368884765035593759`, KB `support`) asked
*"Hoe stel ik RedCactus in met HubSpot?"*. The chat answered *"Ik kan dit niet
vinden in de kennisbank"* despite the support-KB containing 368 RedCactus pages.

Investigation traced the call (request_id `bb0cbb0a-e7b4-414d-adf6-b7e98d187251`):

- Retrieval returned 5 chunks with reranker scores `[0.84, 0.80, 0.80, 0.75, 0.71]`
- All 5 chunks came from `wiki.redcactus.cloud/nl/crm-software/HubSpot` and
  `/hubspot-embedded`
- Each chunk's `text` contained variants of *"If you want to read this article,
  you will have to log in with your Red Cactus account"* — the page is gated
  behind authentication on the source site
- `quality_score = 0.5` (hard-coded default in `qdrant_store.py`)
- The crawl was anonymous (no cookies); crawl4ai received `success=True` because
  the public landing page rendered without error
- Mistral in `kb_narrow=true` mode correctly refused to fabricate an answer

**Production scale** (verified via `knowledge.crawled_pages`):

| Tenant | Total crawled pages | Login-walled | Ratio |
|---|---|---|---|
| voys | 422 | 150 | 35.5% |
| getklai | 5 | 1 | 20.0% |

A login-wall page is one whose `raw_markdown` contains canonical login-redirect
phrases ("have to log in", "log in to read", "log in with your X account").

### Why this slipped past existing safeguards

Klai already has `SPEC-CRAWLER-004 Fase B` — an authenticated login-indicator
guard. Flow:
1. Crawl is configured with cookies via `klai-libs/connector-credentials`
2. `detect_login_indicator_via_llm()` finds a DOM element only visible to
   logged-in users (logout link, user menu)
3. `build_crawl_config(login_indicator_selector=…)` injects
   `&& !document.querySelector('<selector>')` into wait_for JS
4. If the selector matches (= session expired mid-crawl), wait_for times out,
   crawl4ai returns `success=False`, `AuthWallDetected` halts BFS

This guard does NOT cover the redcactus case because:
- The redcactus crawl was anonymous from the start (no cookies, no
  login_indicator)
- `wiki.redcactus.cloud/nl/crm-software/HubSpot` returns HTTP 200 with a
  rendered HTML page that *contains* "log in to read" text but is otherwise
  identical to a regular page (word_count > 50, success=True)
- Crawl4ai had no signal that the content was a stub

### Retrieval-side gap

`klai-retrieval-api/retrieval_api/quality_boost.py` applies `quality_score`
multiplicatively only when `feedback_count >= 3` (cold-start guard). Until 3
users vote thumbs-down on a chunk, `quality_score` has zero effect on ranking.
A login-walled chunk with `quality_score=0.0` would still surface at the top of
results — the system has no hard floor.

### Goal

Detect login-stub pages **at ingest time, before chunking and Qdrant insert**,
without requiring authentication. Add a defence-in-depth floor at retrieval
time so a missed detection at ingest cannot pollute results indefinitely.
Provide a backfill task to clean up the 151 already-ingested walled pages.

---

## Scope

### In scope

1. New pure detector function
   `knowledge_ingest/utils/auth_wall_detector.py::detect_anonymous_auth_wall()`
   with golden-file tests covering RedCactus, Confluence, WordPress, Notion
   public-vs-private, plus negative cases (auth-documentation pages).
2. Integration in `knowledge_ingest/adapters/crawler.py::_ingest_crawl_result`
   that runs the detector before chunking when no `login_indicator_selector`
   is configured.
3. New typed exception `AnonymousAuthWallDetected` parallel to existing
   `AuthWallDetected`. Caller behaviour differs: anonymous detection skips the
   single page and continues BFS (vs. session-expiry halt in authenticated
   mode).
4. Three operating modes via env config:
   `reject` (skip page entirely, default), `degrade` (ingest with
   `quality_score=0.0` for audit trail), `audit_only` (log only, no impact).
5. Procrastinate backfill task `backfill_detect_login_walls(org_id, kb_slug)`
   that scans existing `knowledge.crawled_pages`, deletes matching Qdrant
   chunks, and resets `content_hash` so a future re-crawl re-evaluates.
6. Hard quality-score floor in retrieval-api: filter chunks with
   `quality_score < KLAI_RETRIEVAL_QUALITY_FLOOR` (default `0.05`) after
   reranker, before `quality_boost`. Logged in `retrieval_decision_record` as
   `quality_floor_filtered`.
7. Prometheus metrics: `klai_ingest_login_wall_detected_total`,
   `klai_retrieval_quality_floor_filtered_total`. Grafana panel in the existing
   knowledge-ingest dashboard.
8. Critical alert when a single crawl job has > 20% login-wall ratio (likely
   misconfigured connector or vendor changed login model).
9. Re-crawl smoke test against
   `https://wiki.redcactus.cloud/nl/crm-software/HubSpot` to verify end-to-end
   detection.

### Out of scope (What NOT to Build)

- No authenticated re-crawl of `wiki.redcactus.cloud`. That requires Red Cactus
  credentials and is a separate connector-config exercise tracked under a
  follow-up issue.
- No changes to `SPEC-CRAWLER-004` authenticated login-indicator path —
  this SPEC is purely additive.
- No LLM-based content classification at ingest. The detector is rule-based for
  determinism, sub-millisecond latency, and zero external dependency.
- No changes to chunking/embedding pipeline — detector runs strictly before
  chunking.
- No changes to `quality_boost` formula — only adding a hard floor as a
  separate gate.
- No retroactive notification to users whose past chats may have been
  contaminated. Out-of-band data-quality communication, not a code change.
- No new Qdrant collection or Postgres table.
- No portal UI surface for "X pages were rejected as login-walls" in this
  SPEC. Operator-visible only via Grafana for now; a future SPEC may surface
  to the connector wizard.

---

## Requirements (EARS)

### REQ-INGEST-LOGIN-WALL-DETECT-001-01 — Pure detector function

The system SHALL provide a pure synchronous function in
`klai-knowledge-ingest/knowledge_ingest/utils/auth_wall_detector.py`:

```python
def detect_anonymous_auth_wall(
    markdown: str,
    *,
    fit_markdown: str | None = None,
    url: str | None = None,
) -> AuthWallSignal | None
```

Where `AuthWallSignal` is a dataclass with fields `pattern: str`,
`evidence: list[str]`, `confidence: float`. The function MUST be:

- Side-effect free (no I/O, no logging)
- Deterministic (same input → same output)
- Sub-millisecond on inputs up to 100 KB markdown
- Independent of the crawl4ai client and adapter modules (testable in isolation)

When `fit_markdown` is provided it takes precedence as the primary content
source (stripped of chrome). `markdown` (= `raw_markdown`) is used as a
fallback and to evaluate redirect-density patterns that fit_markdown may have
stripped.

### REQ-INGEST-LOGIN-WALL-DETECT-001-02 — Detection patterns

The detector SHALL flag a page as auth-walled when ANY of the following
conditions match AND the false-positive guard does not veto:

**Condition A — Canonical phrase match** (case-insensitive):
- English: "log in to read", "sign in to continue", "you will have to log in",
  "log in with your", "this article requires authentication",
  "please sign in to view"
- Dutch: "u dient in te loggen", "log in om dit te lezen",
  "meld u aan om verder te gaan"

Coverage limited to EN + NL based on current tenant base (voys, getklai —
both NL-primary). Login-wall pages on most CMS platforms (Confluence,
WordPress, MediaWiki, Notion) emit at least an English fallback string even
on non-English locales. Adding additional languages is on-demand per future
tenant onboarding, not preemptively.

**Condition B — Login-redirect URL density**:
The substring `redirect_to=` appears ≥ 5 times in `markdown`.

**Condition C — Login-link repetition**:
A single href containing `/login` or `/sign-in` or `/auth/` appears ≥ 5 times.

**Condition D — Content-to-login ratio**:
Word count of text outside login-link anchor scopes < 100 AND number of
login-link anchors ≥ 3.

**Signal strength**: Condition A is a STRONG signal — the listed phrases have
no legitimate non-walled use (e.g., "you will have to log in with your X
account" cannot reasonably appear in tutorial or product content; it is
specific to gated articles). Conditions B, C, D are WEAK signals — high
keyword density alone may match legitimate pages with many login links in
chrome.

**False-positive guard** (applies to WEAK signals only): If only B/C/D match
AND the page contains ≥ 500 non-login content words OR fit_markdown is clean
of all four conditions, the detector returns None. The guard does NOT
override Condition A — a single canonical-phrase match flags the page
regardless of surrounding content volume.

Rationale: real-world walled pages (verified against captured RedCactus
fixtures) often contain 3000+ words of template chrome (product catalogs,
navigation, footer text) plus the canonical login phrase exactly once or
twice. Length-based heuristics alone miss them; canonical phrases catch them
deterministically.

### REQ-INGEST-LOGIN-WALL-DETECT-001-03 — Reject behaviour

When `_ingest_crawl_result` processes a `CrawlResult` with `success=True` AND
`login_indicator_selector is None` AND `detect_anonymous_auth_wall(...)` returns
a non-None signal:

- In mode `reject` (default): The function SHALL raise
  `AnonymousAuthWallDetected(url, signal)` before any Qdrant or Postgres write.
- In mode `degrade`: The function SHALL continue ingestion but pass
  `quality_score=0.0` to `qdrant_store` (overriding the hard-coded 0.5) AND
  set `metadata.ingest_warning='login_wall_detected'` in the Qdrant payload AND
  emit a `INFO` log with `event="login_wall_degrade"`.
- In mode `audit_only`: The function SHALL ingest unchanged but emit a `WARN`
  log with `event="login_wall_detected"` and the matching pattern.

### REQ-INGEST-LOGIN-WALL-DETECT-001-04 — Caller behaviour (BFS continuity)

`run_crawl_job` SHALL handle `AnonymousAuthWallDetected` differently from
`AuthWallDetected`:

- `AuthWallDetected` (existing): Halt BFS, mark job `failed`,
  `error='auth_wall_detected: {selector}'`. (Unchanged.)
- `AnonymousAuthWallDetected` (new): Continue BFS, append URL + signal to a
  job-level `auth_wall_pages: list[dict]`, increment a per-job counter.

After BFS completes, when `len(auth_wall_pages) > 0`:
- Write a structured field `crawl_jobs.error_summary` (new column,
  Alembic migration required) containing
  `{"login_walls_skipped": N, "sample_urls": [...up to 10...]}` as JSON.
- Job status remains `succeeded` if any page ingested; transitions to
  `failed_partial` (new enum value) if 0 pages ingested AND > 0 walls skipped.

### REQ-INGEST-LOGIN-WALL-DETECT-001-05 — Configuration

Two env vars added to `klai-knowledge-ingest`:

| Variable | Default | Purpose |
|---|---|---|
| `KLAI_INGEST_LOGIN_WALL_DETECT_ENABLED` | `true` | Global on/off switch |
| `KLAI_INGEST_LOGIN_WALL_DETECT_MODE` | `reject` | One of `reject`, `degrade`, `audit_only` |

Mode semantics:
- `reject` (default, production): skip the page entirely, no Qdrant/Postgres
  write. This is the production target — single code path, no degraded chunks
  to maintain.
- `audit_only` (canary, hours not days): used for the first deploy of a new
  detector revision to measure false-positive rate against live traffic for a
  few hours before promoting to `reject`.
- `degrade` (edge case): tenants who require an audit trail of what was
  flagged. Not part of the standard rollout. Retains the chunk in Qdrant with
  `quality_score=0.0`, where the retrieval-floor (REQ-07) excludes it from
  serving.

Configuration is read once at module import; changes require a container
restart. SOPS-encrypted `.env` files are the source of truth.

### REQ-INGEST-LOGIN-WALL-DETECT-001-06 — Backfill task

A new Procrastinate task SHALL be implemented:

```python
@task(queue=Queues.ENRICH_BULK)
async def backfill_detect_login_walls(org_id: str, kb_slug: str) -> dict
```

The task is **operator-triggered only** (CLI invocation). It is NOT
automatically enqueued on deploy. Rationale: backfill races against any
concurrently-running crawl for the same `(org_id, kb_slug)`, and operators
need explicit visibility into Qdrant-delete counts before the next scheduled
crawl re-ingests. CLI lives at
`python -m knowledge_ingest.backfill_tasks --org <slug> --kb <kb_slug>`.

Behaviour:
1. Stream `knowledge.crawled_pages` rows for `(org_id, kb_slug)` in batches
   of 100 (use `LIMIT 100 OFFSET N` keyset pagination)
2. For each page, run `detect_anonymous_auth_wall(raw_markdown)`
3. For each detected page:
   - DELETE Qdrant points where `payload.path == page.url AND payload.org_id ==
     page.org_id AND payload.kb_slug == page.kb_slug` (use Qdrant
     `delete_points(filter=...)`)
   - UPDATE `crawled_pages SET content_hash = '__login_wall_purged__'` so the
     next crawl detects "stored != current" and re-ingests (which will then
     hit the new ingest-time detector)
4. Return `{"processed": N, "flagged": M, "qdrant_deleted": K}`
5. Emit one `INFO` log per batch with progress

The task MUST be idempotent (running twice yields the same result; the second
run finds the placeholder hash and skips).

### REQ-INGEST-LOGIN-WALL-DETECT-001-07 — Retrieval-side hard floor

`klai-retrieval-api/retrieval_api/api/retrieve.py` SHALL filter chunks where
`payload.quality_score < KLAI_RETRIEVAL_QUALITY_FLOOR` (default `0.05`)
immediately after the reranker step, before `quality_boost`.

The filter:
- Default threshold `0.05` keeps `quality_score=0.5` (= no signal) chunks but
  removes `quality_score=0.0` (= explicitly degraded) chunks
- Records the count of filtered chunks in `retrieval_decision_record` as
  `quality_floor_filtered`
- Records the chunk IDs in a `DEBUG` log line for diagnostics
- Threshold is configurable via env var per-deployment

### REQ-INGEST-LOGIN-WALL-DETECT-001-08 — Observability

The system SHALL expose Prometheus metrics:

| Metric | Type | Labels | Description |
|---|---|---|---|
| `klai_ingest_login_wall_detected_total` | Counter | `org_id`, `kb_slug`, `mode` | Login-wall detections at ingest |
| `klai_retrieval_quality_floor_filtered_total` | Counter | `org_id` | Chunks filtered by retrieval-side floor |
| `klai_ingest_login_wall_detector_latency_ms` | Histogram | — | Detector execution time |

A new Grafana panel SHALL be added to the knowledge-ingest dashboard
visualizing weekly login-wall detection counts per tenant, broken down by mode.

A new alert rule SHALL fire critical when:
`(login_walls_detected / total_pages_in_job) > 0.20` for any single crawl job.
Rationale: > 20% indicates a misconfigured connector (missing credentials) or
the source site changed its auth model.

### REQ-INGEST-LOGIN-WALL-DETECT-001-09 — Tenant isolation

The detector and backfill task MUST honour tenant isolation:
- Backfill task accepts `(org_id, kb_slug)` as required arguments; no
  cross-tenant scanning
- Qdrant `delete_points` filter MUST include `org_id` AND `kb_slug` AND `path`
  conditions; deletion by `path` alone is FORBIDDEN
- All Postgres queries MUST set `app.current_org_id` via the standard RLS
  helper before SELECT/UPDATE
- Per the tenant-isolation review standards
  (`reports/audit-tenant-isolation-2026-05-05/standards.md`), any new
  Qdrant operation MUST include `org_id` in `Filter.must` — checked by the
  semgrep rule in `.github/workflows/tenant-isolation-review.yml`

### REQ-INGEST-LOGIN-WALL-DETECT-001-10 — Re-crawl smoke test

After implementation, an end-to-end smoke test SHALL be executed against
production data (read-only or against a sacrificial test page):

1. Trigger `POST /ingest/v1/crawl/sync` for
   `https://wiki.redcactus.cloud/nl/crm-software/HubSpot` (single-URL crawl)
2. Verify the resulting `crawl_jobs` row contains `error_summary` with
   `login_walls_skipped >= 1`
3. Verify no new Qdrant points were inserted for that URL
4. Run the original failing query *"Hoe stel ik RedCactus in met HubSpot?"*
   via the LiteLLM proxy with voys credentials
5. Verify retrieval-api log shows either zero chunks served (gap_type=hard) or
   chunks from a different (non-walled) source
6. Document the test outcome in the sync-phase PR

---

## Non-functional requirements

### Performance
- Detector latency: p99 < 1 ms on 100 KB markdown
- Backfill throughput: ≥ 100 pages/second (limited by Qdrant delete API)
- Retrieval floor filter: p99 < 1 ms additional latency

### Security
- No new authentication paths introduced
- All env vars treated as configuration, never as secrets
- Tenant isolation enforced via existing RLS + Qdrant filter helpers (see
  REQ-09)

### Reliability
- Detector failure (e.g., regex compilation error on cold start) MUST fail-safe
  to `audit_only` mode: log error, ingest as-is. Never block all crawls due to
  detector bug.
- Backfill task MUST be re-runnable; partial failure during a batch leaves the
  remainder for the next run (idempotent via `__login_wall_purged__`
  placeholder hash).

### Observability
- All detector outcomes (positive/negative/error) emit structured logs with
  `request_id` propagation
- Prometheus metrics granular enough to alert on per-tenant anomalies
- Grafana panel sufficient to spot a misconfigured connector within one crawl
  cycle

---

## Success criteria

1. The 150 voys + 1 getklai login-walled pages are removed from Qdrant.
2. The query *"Hoe stel ik RedCactus in met HubSpot?"* in voys's chat returns
   either no chunks (honest) or chunks from non-walled sources.
3. New crawls of `wiki.redcactus.cloud/*` without credentials produce zero
   ingested chunks and a `failed_partial` job row.
4. False-positive rate < 1% measured against a sample of 100 known-good pages
   from voys's existing successful retrievals.
5. Detector latency p99 < 1 ms.
6. Grafana panel shows zero login-wall detections from voys after backfill +
   for 7 consecutive days.
