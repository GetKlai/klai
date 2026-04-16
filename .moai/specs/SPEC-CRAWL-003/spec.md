# SPEC-CRAWL-003: Connector Auth-Expiry Detection — Three-Layer Content Quality Guardrails

**Status:** Planned
**Priority:** High
**Created:** 2026-04-16
**Revised:** 2026-04-16 (threshold calibration + module path correction)

---

## Problem

The webcrawler connector (`klai-connector`) syncs pages from auth-protected wikis into Qdrant,
but it has no way to detect when authentication silently breaks mid-lifecycle or when a crawl
captures non-content (login walls, Cloudflare challenges, maintenance pages). The result is
contaminated knowledge bases that pass every existing quality check — the ingest pipeline is
happy to embed and store "Log in when you want to read this article" as if it were real content.

Concrete incident that motivated this SPEC: in the `voys-help-notion` KB (Redcactus connector),
the admin's `wiki.redcactus.cloud` session cookies expired between connector setup and later syncs.
86 of 233 (37%) indexed pages are now identical login-wall boilerplate instead of article content.
No alarm fired. The only reason we noticed is that the user observed a degradation in retrieval
quality — not any connector metric.

The failure generalises to three distinct modes, none of which today's skip-heuristics catch:

1. **Session-wide auth loss** — cookies expire between syncs, so every crawled page returns
   the same login-wall HTML. Every page has non-trivial length, so the existing "empty markdown"
   skip does not fire.
2. **Per-page auth** — general auth works, but a subset of pages require additional permissions.
   Those pages silently return the login wall while the rest of the sync looks healthy.
3. **Silent content degradation** — any future scenario where the crawler captures non-content
   at scale (Cloudflare challenge pages, rate-limit blocks, scheduled-maintenance notices,
   CDN error templates) produces the same invisible failure.

The root issue: the connector trusts `crawl4ai` to return "content" and has no oracle for "is
this content real?" We need three complementary layers of detection, each cheap enough to run
on every sync, each catching a different failure mode.

---

## Goal

Add three independent detection layers to the webcrawler adapter and sync engine:

1. **Layer A — Canary fingerprint (pre-sync, fail-fast).** Before any content is crawled or
   written, re-crawl a known-good reference page and verify its content fingerprint still
   matches the one captured at connector setup. A drifted fingerprint aborts the entire sync
   before a single chunk reaches Qdrant.
2. **Layer B — Per-page login indicator (fail-fast per page).** Let connector config declare
   a CSS selector that proves a page was rendered in an authenticated context (e.g.
   `.logged-in-user-menu`). Pages that don't render that selector are dropped as `auth_walled`
   during `_process_results()`, not stored.
3. **Layer C — Post-sync boilerplate-ratio metric (observability, soft-fail).** After
   extraction, compute a SimHash-based fingerprint per page. If more than 15% of pages fall
   into a single near-duplicate cluster, mark the sync as `quality_status=degraded` and log a
   structured alert. Valid pages are still committed — this layer is a safety net, not a gate.

Each layer is opt-in: connectors without the new config fields behave exactly as today.

---

## Non-Goals

This SPEC explicitly does not include:

- **Admin UI changes.** New config fields are backend-first. The frontend SPEC for capturing
  `canary_url`, `canary_fingerprint`, and `login_indicator_selector` in the connector setup
  wizard is a separate work item.
- **Playwright `storage_state` / auto-refresh login.** Recovering from expired cookies without
  admin re-authentication is a future SPEC. This SPEC only *detects* auth expiry; admin must
  still paste fresh cookies to resolve it.
- **Retroactive cleanup** of the 86 login-wall chunks already in `voys-help-notion`. That's a
  one-off ops task (re-sync after cookie refresh + optional Qdrant filter-delete), not
  SPEC-worthy.
- **Alert delivery beyond structured logs.** Slack / email / PagerDuty integration for
  `quality_status=degraded` events builds on the existing product-events and logging pipeline
  (see `.claude/rules/klai/infra/observability.md`) in a follow-up SPEC.
- **Full re-architecture of sync-result schemas.** `quality_status` is additive; existing
  consumers of `SyncRun` continue to work.

---

## Environment

- **klai-connector:** Python 3.13, FastAPI, httpx, structlog, async throughout
  (see `.claude/rules/klai/projects/python-services.md`)
- **Crawler backend:** Crawl4AI REST API at `http://crawl4ai:11235`, v0.8.6
  - Supports `wait_for` CSS selector for fail-fast per-page auth
  - `after_goto` / `on_page_context_created` hooks already used for cookie injection
    (see SPEC-CRAWL-002)
- **DB:** PostgreSQL via SQLAlchemy 2.0 async
  - `connector.sync_runs` table (klai-connector DB) — existing model extended with
    `quality_status`
  - `portal_connectors.config` JSONB (portal DB) — new optional fields, surfaced to the
    connector via `PortalClient.get_connector_config()`
- **Logging:** structlog with `setup_logging()` + `RequestContextMiddleware`
  (see `.claude/rules/klai/projects/portal-logging-py.md`)
- **New dependency:** `trafilatura>=2.0` in `klai-connector/pyproject.toml` for SimHash-based
  content fingerprinting
- **Existing SPECs this builds on:**
  - SPEC-CRAWL-002 — Two-phase BFS + extraction split in webcrawler adapter
  - SPEC-KB-IMAGE-001 — Recent image-extraction refactor (commits `ddfac8c1`, `363441be`,
    `51b7150b`) that defined the current `_process_results()` shape

---

## Requirements

### Data Model

**REQ-1: New optional fields on portal connector config (webcrawler only)**
The portal `connectors.config` JSONB SHALL accept three new optional fields when
`source_type == "webcrawler"`:
- `canary_url: str | None` — absolute URL of a known-good reference page. When set, must be
  within `base_url` + `path_prefix`.
- `canary_fingerprint: str | None` — SimHash (hex string) of the canary page captured at
  connector setup.
- `login_indicator_selector: str | None` — CSS selector whose presence on a page proves the
  render happened in an authenticated context (e.g. `.logged-in-user-menu`, `a[href*=logout]`).

All three fields are optional. Absence of any field disables that layer for the connector.
The portal is the source of truth; klai-connector reads these via
`PortalClient.get_connector_config()` without any local persistence.

**REQ-2: New `quality_status` column on `connector.sync_runs`**
The `SyncRun` SQLAlchemy model SHALL add a `quality_status` column:
- Type: `String(20)`, nullable (backward-compat for historical rows)
- Allowed values: `"healthy"`, `"degraded"`, `"failed"`, or `NULL`
- Default for new rows: `"healthy"` when sync completes normally, overridden by detection
  layers as described below
- Migration: Alembic revision in `klai-connector/alembic/versions/` adds the column with
  default `NULL` for existing rows — no backfill

**REQ-3: `error_details` JSONB extension for canary failures**
When Layer A aborts a sync, `SyncRun.error_details` SHALL contain a single entry with shape:
```json
{
  "reason": "canary_mismatch",
  "canary_url": "<url>",
  "expected_fingerprint": "<hex>",
  "actual_fingerprint": "<hex>",
  "similarity": 0.42
}
```
No per-page error records are written in this case — the sync never reached per-page
extraction.

### Layer A — Canary fingerprint check (pre-sync)

**REQ-4: Canary check runs before Phase 1 (BFS discovery)**
WHEN `canary_url` AND `canary_fingerprint` are both set in connector config, the adapter
SHALL perform a canary check BEFORE `list_documents()` begins BFS discovery (REQ from
SPEC-CRAWL-002). Execution order:
1. Fetch `canary_url` via `POST /crawl` (single-URL sync crawl), injecting cookies from
   config via the existing `_build_cookie_hooks()` mechanism.
2. Compute SimHash fingerprint of the returned markdown via a new
   `_compute_content_fingerprint(markdown: str) -> str` helper (trafilatura-backed; see REQ-11).
3. Compare against stored `canary_fingerprint` using SimHash Hamming-distance similarity
   (score in `[0.0, 1.0]`).

**REQ-5: Canary mismatch aborts the sync**
IF canary similarity is strictly less than **0.80**, THEN the adapter SHALL:
- Raise a new `CanaryMismatchError(similarity: float, expected: str, actual: str)` exception
- The sync orchestrator SHALL catch this exception, write `SyncRun.status = "auth_error"`,
  `SyncRun.quality_status = "failed"`, populate `SyncRun.error_details` per REQ-3, and commit
- NO documents are crawled or written to Qdrant in this sync run
- A single structured log event is emitted at `error` level:
  `logger.error("Sync aborted: canary fingerprint mismatch", connector_id=..., canary_url=...,
  similarity=..., expected=..., actual=...)`

**REQ-6: Canary check is skipped when config incomplete**
WHEN `canary_url` is missing OR `canary_fingerprint` is missing, the adapter SHALL skip
Layer A entirely and proceed directly to BFS discovery. No log event is emitted for this case
(silence preserves backward compat).

**REQ-7: Canary check honours existing timeouts**
The canary crawl SHALL use the same `page_timeout` and `cache_mode: "bypass"` as regular
extraction crawls and SHALL NOT cache its response across sync runs (every sync does a fresh
canary check).

### Layer B — Per-page login indicator

**REQ-8: `login_indicator_selector` is passed to Crawl4AI `wait_for` during extraction**
WHEN `login_indicator_selector` is set, the adapter SHALL include it in the `wait_for`
parameter of the Phase 2 (extraction) `CrawlerRunConfig`, combined with the existing
word-count `wait_for` such that the final `wait_for` requires BOTH conditions (login
indicator present AND page has body text). Concretely: prepend a `css:` form of the selector
to the existing JS predicate, or use Crawl4AI's multi-condition `wait_for` syntax
— whichever the current Crawl4AI 0.8.6 REST API supports.

**REQ-9: Pages that fail `wait_for` are counted and skipped, not errored**
IF Crawl4AI returns `result.success == False` for a URL during Phase 2 AND
`login_indicator_selector` was set, THEN:
- The page is treated as auth-walled: excluded from the returned `DocumentRef` list
- A per-connector counter `auth_walled_count` is incremented
- NO per-page error is appended to `SyncRun.error_details` for auth-walled pages
  (to avoid error_details explosion when cookies expire)
- A single structured log event summarising the count is emitted at the end of Phase 2:
  `logger.warning("Pages dropped due to missing login indicator",
  connector_id=..., auth_walled_count=..., total_urls=...)`
  — one log per sync, not one per page.

**REQ-10: `auth_walled_count` is persisted as sync metric**
`SyncRun.error_details` SHALL contain exactly one summary entry when `auth_walled_count > 0`:
```json
{ "reason": "auth_walled_pages", "count": <int> }
```
This is the sole exception to the "no per-page errors for auth-walled pages" rule — one
rollup record, not N per-page records.

### Layer C — Post-sync boilerplate-ratio metric

**REQ-11: `_compute_content_fingerprint()` helper**
A new module-level function `compute_content_fingerprint(markdown: str) -> str` SHALL be
added to a new module `klai-connector/app/services/content_fingerprint.py`. It SHALL:
- Strip markdown to plain text via `trafilatura.extract()` on an HTML wrapper, or via a
  regex pre-pass — whichever trafilatura's documented API for raw markdown prefers (see
  References). Small pages (<20 words after stripping) MUST return an empty fingerprint
  string `""`.
- Compute a 64-bit SimHash over word shingles (default n-gram size: 4 words, as used by
  trafilatura's built-in deduplication helpers).
- Return the SimHash as a zero-padded hex string (16 chars).

**REQ-12: Per-page fingerprint stored during `_process_results()`**
Inside `_process_results()`, for every successfully extracted page, the adapter SHALL compute
`_compute_content_fingerprint(markdown)` and attach it to the `DocumentRef` as an in-memory
attribute `content_fingerprint: str` (no DB storage — consumed only by Layer C). Pages whose
fingerprint is `""` (too short) are skipped by Layer C analysis.

**REQ-13: Boilerplate-ratio analysis after sync**
After `list_documents()` returns, the sync engine SHALL group pages by fingerprint
similarity:
- For ≤200 pages: pairwise Hamming-distance comparison, threshold **0.95** similarity
  (= Hamming distance ≤ 3 on 64-bit SimHash). This is the Google/Manku standard for
  near-duplicate detection in web crawling — see Threshold Rationale below.
- For >200 pages: SimHash-LSH with band size 8, rows 8 (standard parameters), using the
  same 0.95 similarity threshold.
- Any cluster containing **more than 15% of total pages** is flagged as a boilerplate
  cluster.

IF any boilerplate cluster is detected, THEN:
- `SyncRun.quality_status` is set to `"degraded"` (not `"failed"` — valid pages still
  commit).
- `SyncRun.status` remains `"completed"` (backward-compat; existing consumers keep
  working).
- A single structured log event is emitted at `warning` level:
  `logger.warning("Sync quality degraded: boilerplate cluster detected",
  connector_id=..., cluster_size=<n>, cluster_ratio=<float>, total_pages=<n>,
  sample_fingerprint=<hex>)` — one log event per sync run (REQ-16).
- Valid pages (not in the boilerplate cluster) ARE still written to Qdrant.

**REQ-14: Layer C is automatic, not opt-in**
Unlike Layers A and B, Layer C requires no connector config field. Every sync with **≥30
pages** runs the boilerplate check. Syncs with <30 pages are exempt (too small to produce
a statistically meaningful 15% ratio — a 2–3-page "cluster" in a 20-page sync is noise).

### Logging and Observability

**REQ-15: Structured product event on `quality_status` transitions**
WHEN `quality_status` changes from `"healthy"` to `"degraded"` or `"failed"` on a sync run,
the sync engine SHALL emit a single `knowledge.sync_quality_degraded` product event via
the existing product-events pipeline (see `.claude/rules/klai/infra/observability.md`,
"Product events"). Event payload:
```json
{
  "event_type": "knowledge.sync_quality_degraded",
  "org_id": "<uuid>",
  "connector_id": "<uuid>",
  "sync_run_id": "<uuid>",
  "quality_status": "degraded" | "failed",
  "reason": "canary_mismatch" | "boilerplate_cluster" | "auth_walled_pages",
  "metric": <float or int>
}
```

**REQ-16: Bounded log output per layer**
Each detection mechanism SHALL emit a bounded number of structured log entries per sync
run. Specifically:
- **Layer A:** one `error` log on canary mismatch; no log when canary passes.
- **Layer B:** one `warning` log with aggregated `auth_walled_count` (not N per-page logs).
- **Layer C:** always emit a single `warning`-level summary log whenever ≥1 boilerplate
  cluster is detected, followed by detail logs for at most the **top 3 largest clusters**.
  Summary + top-3 is the canonical shape — never fewer-than-summary, never more-than-top-3.

**REQ-17: Layer C logging shape — summary plus top-3 detail**
WHEN Layer C detects one or more boilerplate clusters, the sync engine SHALL emit:

1. Exactly ONE summary log at `warning` level:
   ```python
   logger.warning(
       "Sync quality degraded: boilerplate clusters detected",
       connector_id=...,
       cluster_count=<int>,            # total clusters found
       pages_in_clusters=<int>,        # union of all cluster members
       largest_cluster_ratio=<float>,  # max cluster size / total pages
       total_pages=<int>,
   )
   ```
2. Up to THREE detail logs at `warning` level (one per cluster, largest first):
   ```python
   logger.warning(
       "Boilerplate cluster detail",
       connector_id=...,
       cluster_rank=<1|2|3>,           # 1 = largest
       cluster_size=<int>,
       cluster_ratio=<float>,
       sample_fingerprint=<hex>,
       sample_urls=<list[str] up to 3>,
   )
   ```

Clusters beyond rank 3 are omitted from detail logs but still counted in the summary's
`cluster_count`. This shape gives operators (a) a one-shot "yes/no + how bad" signal in
the summary and (b) actionable forensics for the worst offenders, without ever exploding
log volume under pathological conditions.

### Backward Compatibility

**REQ-18: Connectors without new config fields behave identically to today**
A connector whose config has NONE of `canary_url`, `canary_fingerprint`,
`login_indicator_selector` SHALL produce sync runs whose behaviour is byte-for-byte
identical to pre-SPEC behaviour, with `quality_status = "healthy"` on success (or
`"failed"` on crawl failure). No new log events are emitted.

**REQ-19: Historical `sync_runs` rows remain queryable**
The `quality_status` column migration MUST NOT backfill values into existing rows.
Historical rows keep `quality_status = NULL`, and all downstream consumers
(VictoriaLogs dashboards, portal UI, metrics queries) MUST treat `NULL` as
equivalent to `"healthy"` for display purposes only — never for alerting.

### Dependencies

**REQ-20: `trafilatura` dependency pinned to stable release**
`trafilatura>=2.0,<3.0` SHALL be added to `klai-connector/pyproject.toml`. The version
constraint follows trafilatura's semver (2.x is the current stable line, see References).
The adapter SHALL import trafilatura lazily — top-level `import trafilatura` is acceptable
since it's a required dep, but all calls MUST be behind helper functions so a future swap
to a lighter SimHash library is a one-file change.

---

## Data Model Diff

### `connector.sync_runs` (klai-connector DB)

| Column | Before | After |
|---|---|---|
| `id` | UUID PK | unchanged |
| `connector_id` | UUID | unchanged |
| `status` | VARCHAR(20) | unchanged (still `"running"` \| `"completed"` \| `"failed"` \| `"auth_error"` \| `"pending"`) |
| `started_at`, `completed_at` | TIMESTAMPTZ | unchanged |
| `documents_total`, `documents_ok`, `documents_failed` | INTEGER | unchanged |
| `bytes_processed` | BIGINT | unchanged |
| `error_details` | JSONB | unchanged shape; NEW allowed `reason` values: `"canary_mismatch"`, `"auth_walled_pages"` |
| `cursor_state` | JSONB | unchanged |
| **`quality_status`** | — | **NEW** VARCHAR(20), nullable, one of `"healthy"` \| `"degraded"` \| `"failed"` \| `NULL` |

Alembic migration:

```python
# klai-connector/alembic/versions/XXXX_add_sync_run_quality_status.py
def upgrade():
    op.add_column(
        "sync_runs",
        sa.Column("quality_status", sa.String(20), nullable=True),
        schema="connector",
    )

def downgrade():
    op.drop_column("sync_runs", "quality_status", schema="connector")
```

No index: `quality_status` is low-cardinality and queried alongside `connector_id` which is
already indexed.

### `portal_connectors.config` JSONB (portal DB)

Portal DB schema is unchanged — it's already JSONB. Three optional fields are added to the
application-level schema validated in `klai-portal/backend/app/routers/connectors.py`:

```python
class WebcrawlerConfig(BaseModel):
    # existing fields (unchanged)
    base_url: str
    path_prefix: str | None = None
    max_pages: int = 200
    max_depth: int = 3
    content_selector: str | None = None
    cookies: list[CookieEntry] | None = None

    # NEW (SPEC-CRAWL-003) — all optional, safe defaults
    canary_url: str | None = None
    canary_fingerprint: str | None = None
    login_indicator_selector: str | None = None
```

Validation rules (enforced portal-side, not in klai-connector):
- IF `canary_url` is set, `canary_fingerprint` MUST also be set (and vice versa) — XOR is a
  config error. 422 on save.
- IF `canary_url` is set, it MUST start with `base_url` + (`path_prefix` if set).
- `canary_fingerprint` MUST match regex `^[0-9a-f]{16}$` (16-char hex).
- `login_indicator_selector` MUST be a syntactically valid CSS selector. Light validation:
  non-empty, no script-tag characters.

---

## Acceptance Criteria

### AC-1: Backward-compat — no config fields, no new behaviour
**Given** a connector with NONE of `canary_url`, `canary_fingerprint`,
`login_indicator_selector` in its config
**When** `list_documents()` is invoked
**Then** the behaviour is identical to pre-SPEC (SPEC-CRAWL-002 applies unchanged)
**And** the resulting `SyncRun` has `quality_status = "healthy"` if sync completes normally
**And** no Layer A/B/C-specific log events are emitted.

### AC-2: Canary mismatch aborts sync before any write to Qdrant
**Given** a connector with `canary_url = "https://wiki.example.com/known-page"` and
`canary_fingerprint = "abc123..." ` configured
**And** cookies that are expired (the live canary page now returns a login wall)
**When** sync starts
**Then** the canary crawl completes, fingerprint similarity is computed to be < 0.80
**And** `CanaryMismatchError` is raised
**And** the sync aborts before Phase 1 (BFS) runs
**And** the resulting `SyncRun` has `status = "auth_error"`, `quality_status = "failed"`,
  `error_details = [{"reason": "canary_mismatch", ...}]`
**And** ZERO new chunks are written to Qdrant during this sync
**And** exactly one structured `error`-level log is emitted with
  key `"Sync aborted: canary fingerprint mismatch"`.

### AC-3: Canary pass allows sync to proceed
**Given** a connector with valid cookies and canary fingerprint similarity ≥ 0.80
**When** sync starts
**Then** the canary check passes silently (no log)
**And** Phase 1 (BFS) proceeds per SPEC-CRAWL-002.

### AC-4: Login-indicator selector is forwarded to Crawl4AI
**Given** a connector with `login_indicator_selector = ".logged-in-user-menu"` set
**When** Phase 2 extraction runs
**Then** the `CrawlerRunConfig` params passed to `/crawl` include a `wait_for` clause that
  combines the login-indicator CSS selector with the existing word-count JS condition
**And** pages matching `result.success == False` are excluded from the `DocumentRef` list
**And** `auth_walled_count` reflects the number of such skips.

### AC-5: Auth-walled pages produce one summary log, not per-page logs
**Given** 50 of 100 crawled pages fail the login indicator `wait_for`
**When** Phase 2 completes
**Then** exactly ONE `warning`-level log is emitted for the batch:
  `"Pages dropped due to missing login indicator"` with `auth_walled_count=50,
  total_urls=100`
**And** `SyncRun.error_details` contains exactly one entry:
  `{"reason": "auth_walled_pages", "count": 50}`
**And** `SyncRun.documents_ok = 50` (the passing pages still commit).

### AC-6: Boilerplate-ratio detection flags degraded sync
**Given** a sync that returns 233 pages, 86 of which have near-identical content
  (all within Hamming distance ≤ 3 of the same SimHash, similarity ≥ 0.95)
**When** Layer C analysis runs after `list_documents()`
**Then** a boilerplate cluster of 86 pages is detected (86 / 233 ≈ 37% > 15%)
**And** `SyncRun.quality_status = "degraded"`
**And** `SyncRun.status = "completed"` (valid pages still commit)
**And** the 147 non-boilerplate pages ARE written to Qdrant
**And** exactly one summary `warning`-level log is emitted with key
  `"Sync quality degraded: boilerplate clusters detected"` and `cluster_count=1`
**And** exactly one detail `warning`-level log is emitted with key
  `"Boilerplate cluster detail"` and `cluster_rank=1`, `cluster_size=86`.

### AC-7: Layer C skipped for small syncs
**Given** a sync returning 25 pages (below the 30-page threshold in REQ-14)
**When** the sync completes
**Then** Layer C does NOT run
**And** `SyncRun.quality_status = "healthy"` regardless of content similarity
**And** no Layer C log is emitted.

### AC-8: `content_fingerprint` helper handles short input
**Given** a page whose markdown strips to fewer than 20 words
**When** `_compute_content_fingerprint(markdown)` is called
**Then** the function returns an empty string `""`
**And** that page is excluded from Layer C cluster analysis.

### AC-9: Migration is reversible and safe on live DB
**Given** an existing `connector.sync_runs` table with 10,000 historical rows
**When** the Alembic upgrade runs
**Then** the `quality_status` column is added with `NULL` on all existing rows
**And** `downgrade()` removes the column without data loss warnings
**And** no index rebuild is triggered.

### AC-10: Product event emitted on quality transition
**Given** a sync where Layer A raises `CanaryMismatchError`
**When** the sync engine handles the exception
**Then** a `knowledge.sync_quality_degraded` product event is emitted
  with `quality_status="failed"` and `reason="canary_mismatch"`.

### AC-11: Multiple boilerplate clusters emit summary + top-3 detail logs
**Given** Layer C detects 5 distinct boilerplate clusters in a single sync
**When** Layer C logs the result
**Then** exactly ONE summary `warning` log is emitted with key
  `"Sync quality degraded: boilerplate clusters detected"` and fields
  `cluster_count=5, pages_in_clusters=<int>, largest_cluster_ratio=<float>, total_pages=<int>`
**And** exactly THREE detail `warning` logs are emitted with key
  `"Boilerplate cluster detail"` for the top 3 largest clusters (`cluster_rank=1|2|3`)
**And** NO detail logs are emitted for clusters 4 and 5 (they are counted in the summary
  but not detailed).

### AC-12: Config XOR validation rejects half-configured canary
**Given** a portal API request to update a webcrawler connector config with
  `canary_url` set but `canary_fingerprint` missing
**When** the portal validates the config
**Then** the request is rejected with HTTP 422
**And** the error message names the missing field.

---

## Implementation Notes

### Files to Change

**klai-connector (primary):**
- `klai-connector/app/adapters/webcrawler.py` — Layers A, B, and fingerprint computation
  in `_process_results()`. Builds on SPEC-CRAWL-002's two-phase structure.
- `klai-connector/app/services/content_fingerprint.py` — NEW module for
  `compute_content_fingerprint()` and `find_boilerplate_clusters()`. Kept separate from
  `webcrawler.py` because Layer C runs in the sync orchestrator, not the adapter.
  Located in `app/services/` alongside existing helpers (`image_utils.py`, `parser.py`,
  `sync_images.py`, `s3_storage.py`, `portal_client.py`, `sync_engine.py`) — there is no
  `app/lib/` directory in this codebase.
- `klai-connector/app/sync_engine.py` (or wherever the sync orchestration lives — discover
  exact filename during `/run` phase) — Layer C invocation + `quality_status` assignment +
  product-event emission.
- `klai-connector/app/models/sync_run.py` — add `quality_status: Mapped[str | None]`
  column mapping (see Data Model Diff).
- `klai-connector/alembic/versions/XXXX_add_sync_run_quality_status.py` — NEW migration.
- `klai-connector/pyproject.toml` — add `trafilatura>=2.0,<3.0`.
- `klai-connector/tests/adapters/test_webcrawler.py` — tests per Test Plan below.
- `klai-connector/tests/services/test_content_fingerprint.py` — NEW (mirrors `app/services/` layout).
- `klai-connector/tests/test_sync_engine_quality.py` — NEW, Layer C integration tests.

**klai-portal (config schema + validation only, no UI):**
- `klai-portal/backend/app/routers/connectors.py` — extend `WebcrawlerConfig` Pydantic
  model with three new optional fields + XOR validator (REQ-20 acceptance).
- `klai-portal/backend/tests/routers/test_connectors.py` — tests for the XOR validator.

### Exception Hierarchy

```python
# klai-connector/app/adapters/webcrawler.py
class CanaryMismatchError(Exception):
    """Raised when canary page fingerprint drifts below similarity threshold."""

    def __init__(self, similarity: float, expected: str, actual: str,
                 canary_url: str) -> None:
        self.similarity = similarity
        self.expected = expected
        self.actual = actual
        self.canary_url = canary_url
        super().__init__(
            f"Canary mismatch for {canary_url}: similarity={similarity:.2f} "
            f"(threshold 0.80), expected={expected[:8]}..., actual={actual[:8]}..."
        )
```

The sync engine catches `CanaryMismatchError` specifically (narrower than `Exception`) and
translates it into `SyncRun.status = "auth_error"` + `quality_status = "failed"` +
`error_details` per REQ-3.

### Fingerprint Comparison

SimHash similarity from Hamming distance over 64-bit fingerprints:

```python
def _similarity_from_hamming(a_hex: str, b_hex: str) -> float:
    """Return 1.0 - (hamming_distance / 64) for 64-bit SimHashes."""
    a = int(a_hex, 16)
    b = int(b_hex, 16)
    hamming = bin(a ^ b).count("1")
    return 1.0 - (hamming / 64.0)
```

Threshold mapping on 64-bit SimHash:
- **Canary (Layer A): ≥ 0.80 = pass** (Hamming ≤ 12). Tolerant because legitimate article
  edits (new section, updated timestamps, version numbers) can drift 5–10 bits without
  indicating auth failure. Auth-walled content vs real article is typically < 0.40
  similarity, so 0.80 is a wide safety band against false positives.
- **Boilerplate cluster (Layer C): ≥ 0.95 = same cluster** (Hamming ≤ 3). Google/Manku
  standard for web-crawl near-duplicate detection on 8B+ pages, also the default used by
  `text-dedup` (`bit_diff=3`). Stricter than Layer A because we're detecting identical
  login-wall / Cloudflare / maintenance templates, not tolerating legitimate content
  drift.

See "Threshold Rationale" section below for sources. Tune after first-week telemetry if
false-positive rate > 5% (SPEC amendment required — these are not config knobs).

### Canary Crawl Reuses Existing Plumbing

The canary crawl is a single-URL variant of Phase 2's `_crawl_pages_sync()`. Avoid
duplicating cookie/hook plumbing — pass `urls=[canary_url]` through the same helper:

```python
async def _crawl_canary(
    self,
    canary_url: str,
    config: dict[str, Any],
    cookies: list[dict[str, Any]] | None,
) -> str:
    """Return content fingerprint of canary page, or raise on fetch failure."""
    page_params = self._build_page_crawl_params(config)
    cache: dict[str, str] = {}
    refs = await self._crawl_pages_sync(
        urls=[canary_url],
        crawl_params=page_params,
        cache=cache,
        base_url=config["base_url"],
        cookies=cookies,
    )
    if not refs or canary_url not in cache:
        # Network failure, 404, etc. — treat as canary failure too.
        raise CanaryMismatchError(
            similarity=0.0, expected="", actual="", canary_url=canary_url,
        )
    return compute_content_fingerprint(cache[canary_url])
```

### Logging Signature

All logs follow `.claude/rules/klai/projects/portal-logging-py.md`: structured kwargs, never
string concatenation. Context vars (`connector_id`, `org_id`) come from
`RequestContextMiddleware` — do not re-bind manually inside the adapter.

Example:
```python
logger.error(
    "Sync aborted: canary fingerprint mismatch",
    canary_url=canary_url,
    similarity=similarity,
    expected=expected[:8],
    actual=actual[:8],
)
```

### Package Layout for `content_fingerprint.py`

```python
# klai-connector/app/services/content_fingerprint.py
from __future__ import annotations
import trafilatura
from trafilatura.deduplication import Simhash  # trafilatura's own SimHash impl

def compute_content_fingerprint(markdown: str) -> str:
    """Return 16-char hex SimHash of markdown content, or '' if too short."""
    ...

def similarity(a_hex: str, b_hex: str) -> float: ...

def find_boilerplate_clusters(
    fingerprints: list[tuple[str, str]],  # [(url, fingerprint), ...]
    ratio_threshold: float = 0.15,
    similarity_threshold: float = 0.95,  # Google/Manku near-duplicate standard
) -> list[list[str]]:
    """Return list of clusters (list of URLs) exceeding ratio_threshold.

    Clusters are returned sorted by size descending (largest first) so callers
    can directly slice the top-3 for detail logging per REQ-17.
    """
    ...
```

Keeping Layer C logic in a dedicated module makes it unit-testable without spinning up the
adapter, and leaves room for reuse by other connectors (notion, google_drive) in future
SPECs if they hit similar "silently garbage content" failure modes.

---

## Threshold Rationale

The three numeric thresholds in this SPEC (canary similarity 0.80, cluster similarity 0.95,
minimum-pages 30) are picked deliberately, not arbitrarily. Each serves a different
statistical job and is backed by literature or operational reasoning.

### Canary similarity 0.80 (Layer A)

**Purpose:** "Did authentication silently break between setup and this sync?"

**Expected effect size:** auth-walled content versus the real article typically shares
< 40% of tokens. The login-wall page differs from the reference article in roughly 30–50
bits on a 64-bit SimHash. True mismatch similarity is well below 0.50.

**Chosen threshold 0.80** (Hamming distance ≤ 12) gives a wide safety band against
false positives from legitimate content drift — new sections, version-number bumps, fixed
typos, updated last-edited timestamps embedded in the markdown. An article being edited
between setup and next sync should not abort the sync; only true content-replacement (auth
wall) should.

**Not 0.95** because that would make the canary fire every time an admin updates the
reference article. Not 0.50 because that would miss degradation where the login wall
shares some boilerplate with the original page.

### Cluster similarity 0.95 (Layer C)

**Purpose:** "Are many of the pages in this sync the exact same boilerplate dressed in
different URLs?"

**Industry standard.** [Manku, Jain & Das Sarma (Google 2007) "Detecting Near-Duplicates
for Web Crawling"](https://research.google.com/pubs/archive/33026.pdf) validates **k = 3
differing bits on 64-bit SimHash** as the near-duplicate threshold at 8-billion-page
scale, which equals similarity (64 − 3) / 64 ≈ **0.953**. The `text-dedup` library
defaults to `bit_diff = 3` for the same reason. Trafilatura's own deduplication helpers
use comparable strictness.

**Why stricter than canary:** Layer C is detecting *identical templates* (login walls,
Cloudflare challenges, maintenance notices). Those pages share >99% of their tokens. We
don't want to accidentally cluster articles that share a sidebar — 0.95 is strict enough
to only fire on true duplicates.

**Not 0.85:** at 0.85 (Hamming ≤ 10), articles that share navigation, breadcrumbs, and
"see also" sections would cluster together and produce constant false positives.

### Minimum sample size 30 pages (Layer C)

**Purpose:** "Do we have enough pages to call a 15% cluster statistically meaningful?"

**Reasoning:** 15% of N must be a non-trivial number of pages for the signal to exceed
noise. Under 20 pages, even a 2-page cluster is 10–15% and triggers false alarms. The
[PLOS One streaming-cluster study (Ramos et al. 2024)](https://pmc.ncbi.nlm.nih.gov/articles/PMC10878511/)
confirms cluster-size variability significantly impacts cluster-evolution detection at
small N.

**Chosen minimum 30:** 15% × 30 = 4.5 pages, so the smallest detectable cluster is 5
pages. That's enough to rule out coincidental overlap from shared footers while still
letting Layer C fire on small-but-credible signals.

**Not 20:** too permissive — a 3-page cluster looks significant at 15% of 20 but is
usually noise. **Not 50:** too conservative — many initial syncs during connector
bootstrapping will have 30–50 pages and deserve Layer C coverage.

### Ratio threshold 15% (Layer C)

**Chosen:** pragmatic. The motivating incident had 86/233 = 37% contamination. Legitimate
overlap from shared boilerplate (sidebars, breadcrumbs) typically stays below 10% at
0.95-similarity. 15% is the midpoint — strict enough to catch the incident class, loose
enough to tolerate genuine overlap.

This is the one threshold where we expect to tune after first-week telemetry. If false
positives accumulate on wiki-style sites with heavy shared layout, raise to 20%. If real
auth-wall incidents slip through, lower to 10%.

---

## Rollout / Migration

### Deploying the SPEC

1. **Merge + deploy klai-connector first.** The code paths are fully backward-compatible:
   connectors without new config fields behave exactly as today. No connector breaks on
   rollout.
2. **Run the Alembic migration** during the klai-connector deploy. Migration is pure DDL
   (add nullable column), no lock escalation, safe on live DB.
3. **Deploy klai-portal** with the new Pydantic `WebcrawlerConfig` fields. Existing
   connectors continue to load (new fields default to `None`).
4. **Opt in existing connectors** via the portal admin UI (separate SPEC) OR directly via
   SQL for the one-off Redcactus case:
   ```sql
   UPDATE portal_connectors
   SET config = config || jsonb_build_object(
     'canary_url', 'https://wiki.redcactus.cloud/help/known-good-article',
     'canary_fingerprint', '<hex computed offline>',
     'login_indicator_selector', '.logged-in-user-menu'
   )
   WHERE id = '<redcactus_connector_id>';
   ```
5. **Monitor** VictoriaLogs for `service:klai-connector AND
   message:"Sync quality degraded*"` after the first few sync runs to catch Layer C
   false-positives (wiki pages that legitimately share boilerplate e.g. sidebars that
   survived `PruningContentFilter`). Tune the 15% ratio threshold and 0.95 similarity
   threshold in a follow-up SPEC if false-positive rate > 5%.

### Capturing the Initial Canary Fingerprint

For the first few connectors, admins can capture the fingerprint via a one-off script:

```bash
# scripts/compute_canary_fingerprint.py
python -c "
import asyncio, sys
from app.services.content_fingerprint import compute_content_fingerprint
from app.adapters.webcrawler import WebCrawlerAdapter
# ... minimal harness to crawl one URL and print fingerprint ...
"
```

Once the admin UI SPEC lands, the fingerprint is captured automatically during connector
setup: admin enters `canary_url`, portal triggers a one-off crawl, stores the returned
fingerprint in `canary_fingerprint`. No user-visible fingerprint field.

### Rolling Back

- Revert klai-connector deploy: connectors automatically resume pre-SPEC behaviour because
  the config fields are optional and Layer C is internally gated.
- Run Alembic downgrade to drop `quality_status` column. No data loss: historical rows
  never had meaningful values.

---

## Test Plan

| Test | What to verify |
|---|---|
| `test_canary_check_passes_when_similarity_high` | similarity=0.95, sync proceeds, no error log |
| `test_canary_check_aborts_when_similarity_low` | similarity=0.42 raises `CanaryMismatchError`, sync engine records `status="auth_error"`, `quality_status="failed"` |
| `test_canary_check_skipped_when_config_missing` | no canary fields, BFS runs immediately, no canary log |
| `test_canary_check_xor_validation` | portal rejects config with canary_url only |
| `test_login_indicator_appended_to_wait_for` | Phase 2 params contain combined wait_for clause |
| `test_login_indicator_skips_failed_pages` | 50/100 `result.success=False` → 50 DocumentRefs, one summary log |
| `test_login_indicator_populates_auth_walled_count` | error_details contains one `{"reason": "auth_walled_pages", "count": 50}` entry |
| `test_compute_content_fingerprint_returns_hex` | standard markdown → 16-char hex |
| `test_compute_content_fingerprint_short_input_empty` | <20 words → `""` |
| `test_compute_content_fingerprint_deterministic` | same markdown twice → same hex |
| `test_similarity_identical_fingerprints` | hamming=0 → 1.0 |
| `test_similarity_inverted_fingerprints` | hamming=64 → 0.0 |
| `test_find_boilerplate_clusters_detects_login_wall_cluster` | 86/233 identical → returns cluster of 86 URLs |
| `test_find_boilerplate_clusters_ignores_small_clusters` | 5/100 identical (<15%) → no cluster returned |
| `test_layer_c_sets_quality_status_degraded` | integration: sync with 86/233 boilerplate → `quality_status="degraded"`, `status="completed"`, 147 valid pages committed |
| `test_layer_c_skipped_for_small_sync` | 25-page sync (below 30-page threshold) → Layer C not invoked, `quality_status="healthy"` |
| `test_layer_c_emits_summary_plus_top_3_details` | 5 clusters → one summary + exactly 3 detail logs, clusters 4 & 5 only in summary |
| `test_quality_status_column_migration_up_down` | Alembic up adds column, down drops it, existing rows survive |
| `test_product_event_emitted_on_degradation` | `knowledge.sync_quality_degraded` row in `product_events` table after degraded sync |
| `test_backward_compat_no_config_no_behaviour_change` | full integration: connector with no new fields → sync output identical to pre-SPEC baseline |

Coverage target: 85%+ per `.claude/rules/moai/languages/python.md`. New modules
(`content_fingerprint.py`) aim for 95%+ since they're pure functions with no I/O.

---

## References

- Trafilatura deduplication (SimHash): https://trafilatura.readthedocs.io/en/latest/deduplication.html
- Manku, Jain & Das Sarma (Google 2007) "Detecting Near-Duplicates for Web Crawling" — canonical k=3 / 64-bit SimHash threshold: https://research.google.com/pubs/archive/33026.pdf
- PLOS One (Ramos et al. 2024) "Least sample size for detecting changes in cluster solutions of streaming datasets" — minimum-N reasoning for Layer C: https://pmc.ncbi.nlm.nih.gov/articles/PMC10878511/
- `text-dedup` library defaults (`bit_diff=3` for SimHash): https://github.com/ChenghaoMou/text-dedup
- Crawl4AI hooks & auth detection: https://docs.crawl4ai.com/advanced/hooks-auth/
- Crawl4AI `wait_for` CSS / JS modes: https://docs.crawl4ai.com/api/parameters/ (search "wait_for")
- SPEC-CRAWL-002 (two-phase BFS + extraction) — the adapter structure this SPEC hooks into
- SPEC-KB-IMAGE-001 — recent `_process_results()` refactor (commits `ddfac8c1`, `363441be`, `51b7150b`)
- `.claude/rules/klai/infra/observability.md` — product-events + VictoriaLogs
- `.claude/rules/klai/projects/portal-logging-py.md` — structlog conventions
- `.claude/rules/klai/projects/knowledge.md` — Procrastinate enrichment passthrough warning (relevant if `content_fingerprint` is ever promoted from in-memory to Qdrant payload in a future SPEC)
- Incident context: `voys-help-notion` KB, Redcactus connector — 86/233 pages silently replaced by `"Log in when you want to read this article"` boilerplate between connector setup and second sync.
