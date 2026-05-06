---
id: SPEC-INGEST-RECONCILE-001
version: "0.2.0"
status: approved
created: 2026-05-06
updated: 2026-05-06
author: Mark Vletter
priority: high
related:
  - SPEC-TAXONOMY-V2-001-FOLLOWUP-001 (downstream consumer — taxonomy bootstrap quality depends on coverage)
  - SPEC-CRAWLER-005 (link-graph two-phase pattern; this SPEC fixes the discovery-stage gap that surfaced via that work)
  - SPEC-CRAWLER-004 (web_crawler delegated path — the asyncio.gather supplement bug lives here)
  - SPEC-CRAWLER-006 (introduced documents_short_skipped logging — this SPEC promotes it to a column)
  - SPEC-RAG-REBUILD-KB-001 (rebuild_kb consumes per-URL outcomes for selective refetch decisions)
---

# SPEC-INGEST-RECONCILE-001: Coverage-complete + observable connector ingestion

## Summary

Two empirical bugs surfaced on 2026-05-06 reveal the same root pattern: connector ingestion silently drops documents at unobserved stages. This SPEC fixes the bugs, surfaces the drops to operators, and prevents the class from recurring via a CI-enforced lint rule.

The scope is **deliberately bounded**: we leverage crawl4ai's existing multi-URL bulk endpoint instead of building parallel orchestration, and we extend the existing `sync_runs` row instead of adding new tables. An earlier draft (v0.1) proposed a full 4-stage pipeline with two new tables and a 5-phase rollout — that draft was rewritten after evidence showed crawl4ai already provides the orchestration we were about to build (see "Rejected alternatives" below).

## Motivation — empirical evidence

### Bug A: Voys Help NL crawler ingested 41 of 208 sitemap pages

`help.voys.nl/sitemap.xml` exposes 208 URLs. `knowledge.crawled_pages` for that connector contains 41. The 167 missing pages disappeared in a custom `asyncio.gather(*[crawl_page(u) ...])` supplement loop introduced by commit `b0895e1b` (4 april 2026, "remove sitemap seeding" → "add sitemap as post-supplement").

The supplement loop has three observable defects:
- No `Semaphore` → 167 parallel calls overwhelm crawl4ai's Playwright pool, mass timeouts.
- No success-vs-failure counter → 167 attempted, 0 added, no log distinguishes "all failed" from "skipped".
- URL dedup is exact-string match → trailing-slash and fragment variants miss dedup.

`crawl_jobs.pages_total = 41` and `status = completed`. Operator has no signal anything went wrong.

**Why this is a regression** — before commit `b0895e1b`, sitemap URLs were BFS *seeds*, not post-supplements. crawl4ai BFS-traversed all 208 directly. The refactor was conceptually cleaner but introduced a custom orchestration layer that turned out to be wrong.

### Bug B: Voys Notion sync ingested 79 of 120 syncable pages

`client.search()` returns 120 syncable pages. `connector.sync_runs` records `documents_total=120, documents_ok=120, documents_failed=0`. `knowledge.artifacts` contains 79. The 41 missing pages are dropped by the 50-character minimum-length filter in `klai-connector/app/services/sync_engine.py:333-339`.

The drop counter `documents_short_skipped` IS already implemented (sync_engine.py:159, 337, 490) but only reaches a structlog event — it is never persisted to the `sync_runs` row. So `documents_ok=120` is **arithmetically wrong**: it counts "submitted to ingest" not "persisted as artifact". The 41-page gap is invisible everywhere except in 5-day-retention structlog.

**Why this is a generalised problem** — the same drop class affects Confluence empty pages, MS Docs placeholder pages, GitHub binary blobs. Every adapter has its own short-content filter. None of them surface to the user.

### Cross-cutting consequence — taxonomy traceability

Today a taxonomy proposal carries 5 sample_titles + cluster size in its `payload` JSONB. There is no way to expand "where does NetSapiens en compatibele telefoonsystemen come from?" into the full document set, the discovery source per document, or per-document failure reasons. This is the operator question that surfaced during SPEC-TAXONOMY-V2-001-FOLLOWUP-001 close-out.

### Online research — what mature ETL systems do (and what we adopt)

Singer.io, Apache Nutch, Scrapy, Airbyte, OpenLineage all surface the same patterns:

- **Nutch**: per-URL fetch-status enum (`fetched`, `not_fetched`, `gone`, `redir_temp`, ...). We adopt this as `FetchReasonCode`.
- **Scrapy**: `item_dropped` signal with stable `reason` field; `StatsCollector` aggregates per-reason counts. We adopt this as `PersistSkipReason` + JSONB aggregation.
- **Singer**: catalog/discovery JSON contract. We **don't adopt** the wire protocol (JSON over stdin/stdout doesn't fit our async Python stack) but we adopt the IDEA that discovery output is a first-class artifact, not implicit state.
- **Airbyte**: per-record state. We **don't adopt** — full migration cost is multiple weeks for orchestration we already get from crawl4ai + Procrastinate; their per-record observability is no better than ours-with-this-SPEC.
- **OpenLineage**: standardised lineage events. We **don't adopt as runtime dependency** — emit-shim is a future 50-line addition once we have a downstream consumer (DataHub instance, Marquez deployment).

## What WE DO (4 changes) and why

### Fix 1 — Replace BFS-with-post-supplement by sitemap+BFS UNION via crawl4ai's bulk endpoint

**Decision**: discover the candidate URL set via `union(sitemap_xml_urls, BFS_traversal_urls_from_homepage)` BEFORE the fetch phase begins, then submit the union to crawl4ai's `POST /crawl` bulk endpoint.

**Rationale** — three pieces of evidence:

1. crawl4ai REST exposes `POST /crawl` with `urls: array<string>` (verified via openapi.json on the production container). It is the multi-URL primitive with built-in concurrency dispatch (server-side `MemoryAdaptiveDispatcher` with `max_session_permit=10` default).
2. Per-URL `result.success` and `result.error_message` are returned in the response — no need for our own per-URL outcome tracking.
3. The current `crawl_site` uses `POST /crawl/job` (server-side BFS) followed by a custom `asyncio.gather` for sitemap supplement. This duplicates dispatch logic crawl4ai already provides server-side.

**Why this is forward, not backward (vs the pre-`b0895e1b` "sitemap-as-seed")**: pre-`b0895e1b` fed sitemap URLs as BFS seeds — crawl4ai then walked links from each, multiplying duplicate fetches. The new union approach feeds them as the FULL candidate list (no further BFS expansion past the homepage seed), so we get coverage WITHOUT duplicate work.

**What this rejects**: building our own `asyncio.Semaphore` + custom outcome capture. Wrong layer of abstraction — crawl4ai owns the dispatcher.

### Fix 2 — Persist `documents_short_skipped` (and reason-coded skips) in `sync_runs`

**Decision**: add JSONB column `connector.sync_runs.skip_reasons` containing `{reason_code: count}`. Populate from existing structlog event in `sync_engine._execute_sync`. Correct `documents_ok` arithmetic: `documents_ok = documents_persisted` (NOT `submitted_to_ingest`).

**Rationale**:
- `documents_short_skipped` is already counted in `sync_engine.py` — only the persistence is missing.
- A JSONB `skip_reasons` field generalises beyond Notion: same column captures `auth_wall_detected`, `dedupe_content_hash_match`, `excluded_by_kb_config`, etc. for ALL connectors.
- One column + one alembic migration + ~20 lines of sync_engine arithmetic. No new tables.
- The portal UI's existing connector-detail view already reads `documents_total/ok/failed` — adding a `skip_reasons` panel is a small frontend change.

**What this rejects**: a separate `fetch_outcomes` table for per-document forensic queries. Voys-scale (~500 docs/sync) does not justify the table overhead. If a tenant later needs forensic queries on individual rejected docs, that's a follow-up SPEC. JSONB aggregates suffice for the operator's "how many dropped, why" question today.

### Fix 3 — Stable reason-code registry + per-URL outcome capture in `crawl_jobs`

**Decision**: define two enums in `klai-knowledge-ingest/knowledge_ingest/reason_codes.py` (and a copy in klai-connector):

- `FetchReasonCode`: `success` | `http_4xx` | `http_5xx` | `timeout` | `dns_error` | `connection_error` | `auth_error` | `parse_error` | `rate_limited` | `unknown_exception`
- `PersistSkipReason`: `content_too_short` | `auth_wall_detected` | `dedupe_content_hash_match` | `dedupe_raw_html_hash_match` | `non_text_content` | `excluded_by_kb_config` | `taxonomy_classify_failed`

Per-URL outcomes from crawl4ai's `/crawl` response are written as JSONB on `knowledge.crawl_jobs.fetch_outcomes` (new column, JSONB array of `{url, reason_code, status_code, content_length}`). Reconciliation in `sync_runs.skip_reasons` JSONB is computed by aggregating both.

**Rationale**:
- Stable enum prevents free-form-string pollution in dashboards. A new reason requires the value to land in the enum AND in a Postgres CHECK constraint — mechanical guard against typo-introduced silent reasons.
- Per-URL outcomes on `crawl_jobs` give us the "show all source documents with their fetch status" affordance for taxonomy traceability — without a separate table.
- JSONB-on-existing-row is cheap to migrate. Voys-scale: 200-500 entries × ~80 bytes = 16-40KB per crawl_jobs row. Indexable via Postgres jsonb_path_ops if needed.

**What this rejects**: separate `fetch_outcomes` table. Same rationale as Fix 2 — Voys-scale doesn't need it; future tenants can drive a follow-up SPEC if forensic queries become latency-bound.

### Fix 4 — CI-enforced lint rule against unbounded `asyncio.gather` on crawl_page

**Decision**: add ast-grep rule `rules/no-unbounded-gather-crawl-page.yml` that fails CI on `asyncio.gather(*[crawl_page(u) ...])` patterns without surrounding `Semaphore` context.

**Rationale**:
- The supplement bug (Fix 1) is the third recurrence of the same anti-pattern at Klai (SPEC-CRAWLER-005 link-graph batch had a related issue, SPEC-CRAWLER-006 web_crawler counters too). Pattern: "fan out async work, ignore individual outcomes via `return_exceptions=True`, lose 90% silently".
- Mechanical guard prevents reintroduction. Adding a new place that legitimately needs unbounded gather requires the author to either add a semaphore or explicitly suppress the rule with comment + reason — both high-friction to do mindlessly.
- ~15 lines of YAML; runs in existing per-service CI workflows.

**What this rejects**: relying on code review. Per `pitfalls/process-rules.md::adversarial-at-high-confidence`, prompt-based rules achieve ~70% compliance vs hooks/lints at ~100%. Mechanical enforcement is the only durable answer.

## What WE EXPLICITLY DO NOT DO (and why)

### Not adding `discovery_candidates` table

**Rejected** because: crawl4ai's `/crawl` response already returns per-URL outcomes. We would be normalising data we receive in JSON into a table and joining it back — pure overhead. JSONB on `crawl_jobs.fetch_outcomes` covers the same use cases at Voys-scale. If a tenant emerges with 100k+ pages and forensic-query latency becomes an issue, promote to table in a follow-up. Don't pre-build for hypothetical scale.

### Not adding `fetch_outcomes` table

**Rejected** for the same reason as `discovery_candidates`. Two new tables for two new aggregation views (per-fetch-reason, per-skip-reason) when both can be derived from JSONB on existing rows.

### Not changing `BaseAdapter.list_documents` contract

**Rejected** because: only `web_crawler` and `notion` are observably broken. The other 5 adapters (GitHub, Confluence, Airtable, GoogleDrive, MSdocs) work today. Forcing a contract change on all 7 adapters multiplies blast radius for two specific bugs. The reason-code registry + sync_engine arithmetic correction (Fix 2) generalises to all adapters without contract change — each adapter calls `record_skip(reason_code)` from inside its existing flow.

### Not implementing OpenLineage emit

**Rejected** because: no downstream consumer exists at Klai today (no DataHub, no Marquez, no Atlan). Emit-without-consumer is dead code. When a consumer arrives, ~50 lines of glue around the new `sync_runs.skip_reasons` data covers it. Future SPEC-RECONCILE-002 territory.

### Not implementing per-tenant retention policy on `fetch_outcomes`

**Rejected** because: at Voys-scale (200-500 outcomes per sync × ~weekly syncs × ~30 connectors org-wide = ~15-75MB/year of JSONB across all rows). Negligible. Default keep-everything is fine until someone actually exceeds storage thresholds — at which point a separate SPEC defines TTL.

### Not implementing retry-failed-fetches admin endpoint

**Rejected** for SPEC-RECONCILE-002 future scope. The reason-code design enables retry (filter `fetch_outcomes` by `reason_code IN ('http_5xx', 'timeout')`) but the actual retry orchestration (exponential backoff, max attempts per candidate, idempotency) is a separate week of work. Don't include in this SPEC's scope.

### Not implementing real-time alerts on `fetch_failed_ratio > 5%`

**Rejected**. Once `sync_runs.skip_reasons` exists, Grafana dashboards can be authored from existing PortalPostgres datasource without new alerting infrastructure. Operator-driven, post-hoc.

### Not migrating existing `sync_runs` rows

**Rejected**. Existing rows keep `skip_reasons = NULL`. UI handles absent-field case (shows old `documents_total/ok/failed` only). No backfill — operationally pointless and migration-risky.

## Acceptance criteria

### Crawl-discovery (Fix 1)

1. **AC-1 (Event-driven)** — When `crawl_site` is invoked for a connector with no `path_prefix` config, the system shall fetch sitemap.xml AND seed BFS from `start_url`, take the union of the URL sets (deduplicated by canonicalised URL), and submit to crawl4ai's `POST /crawl` bulk endpoint as the candidate list.

2. **AC-2 (Ubiquitous)** — The union shall be capped at `max_pages` (existing config). When the union exceeds `max_pages`, sitemap URLs take priority over BFS-discovered URLs (sitemap is the site owner's stated truth; BFS is best-effort).

3. **AC-3 (Unwanted behavior)** — If sitemap.xml fetch fails (404, timeout, malformed XML), the system shall fall back to BFS-only discovery AND log `crawl_discovery_sitemap_unavailable` at warning level. The crawl MUST NOT fail.

4. **AC-4 (Ubiquitous)** — Each candidate URL shall produce one entry in `crawl_jobs.fetch_outcomes` JSONB with shape `{url, reason_code, status_code, content_length}`. Reason codes use the `FetchReasonCode` enum. Successful fetches use `reason_code: "success"`.

5. **AC-5 (Empirical)** — A live trigger of the crawler against `voys/support` (after this SPEC ships) SHALL produce ≥ 200 entries in `fetch_outcomes` and ≥ 180 with `reason_code: "success"`. The remaining outcomes shall be classified by reason (not lost).

### Persist-stage observability (Fix 2)

6. **AC-6 (Ubiquitous)** — `connector.sync_runs.skip_reasons` (new JSONB column, default `{}`) shall be populated by `sync_engine._execute_sync` with `{reason_code: count}` covering ALL persist-stage drops.

7. **AC-7 (Ubiquitous)** — `documents_ok` shall be redefined as `documents_persisted = documents_total - documents_failed - sum(skip_reasons.values())`. Existing API consumers continue reading `documents_ok` and get the corrected value.

8. **AC-8 (Empirical)** — A live trigger of the Notion connector against `voys/support` SHALL produce `sync_runs.skip_reasons.content_too_short ≥ 30` (the existing 41 short pages), AND `documents_ok ≤ 80` (corrected arithmetic).

### Reason-code registry (Fix 3)

9. **AC-9 (Ubiquitous)** — `knowledge_ingest/reason_codes.py` shall define `FetchReasonCode` and `PersistSkipReason` as Python `StrEnum`. The same enums (or a string-equivalent copy) shall exist in klai-connector.

10. **AC-10 (Ubiquitous)** — A Postgres CHECK constraint on `connector.sync_runs.skip_reasons` shall validate that all keys are members of `PersistSkipReason`. Violation raises at INSERT/UPDATE time, not at read.

11. **AC-11 (Ubiquitous)** — A Postgres CHECK constraint on `crawl_jobs.fetch_outcomes` shall validate that all `reason_code` entries are members of `FetchReasonCode`.

### Bug-prevention (Fix 4)

12. **AC-12 (Ubiquitous)** — A new ast-grep rule `rules/no-unbounded-gather-crawl-page.yml` shall fail CI on patterns matching `asyncio.gather(*[crawl_page(...) ...])` outside a `Semaphore` context. Existing call sites (post-Fix-1 there should be none) are migrated, not exempted.

### Non-functional

13. **AC-13** — End-to-end Voys/support crawl (501 docs scale) SHALL complete in ≤ 90s after Fix 1 (current is 33s for the broken path; the bulk-`/crawl` path is comparable or faster due to crawl4ai server-side concurrency).

14. **AC-14** — `sync_runs.skip_reasons` JSONB write overhead per sync run SHALL be ≤ 5ms (single UPDATE statement at end of sync; existing pattern).

### UI surface

15. **AC-15 (Optional feature)** — Where the portal connector-detail page renders sync-run results, the `skip_reasons` JSONB shall be displayed as a breakdown panel ("41 short content, 0 auth-wall, 0 dedupe-match"). Implementation in a follow-up frontend PR; backend support is in this SPEC.

### MX tags

16. **AC-16** — `crawl_site`, `_execute_sync`, and `record_skip` shall carry `@MX:NOTE` annotations referencing this SPEC. The reason-code enums get `@MX:ANCHOR` (high fan-in expected).

## Technical approach

### Fix 1 — `crawl_site` rewrite

```python
async def crawl_site(start_url, selector=None, max_pages=200, ...):
    # Discovery — union, not sequence
    sitemap_urls = await _fetch_sitemap_urls(start_url)  # existing helper
    bfs_seed_urls = await _bfs_discover(start_url, max_depth=1)  # NEW: shallow BFS just to get homepage links
    candidates = _dedupe_urls(sitemap_urls | bfs_seed_urls)[:max_pages]

    # Fetch — single bulk call to crawl4ai with built-in concurrency
    response = await client.post(
        f"{settings.crawl4ai_api_url}/crawl",
        json={
            "urls": candidates,
            "crawler_config": build_crawl_config(selector, ...),
            # crawl4ai's MemoryAdaptiveDispatcher kicks in server-side
        },
    )

    # Per-URL outcome capture
    outcomes = []
    for r in response.json()["results"]:
        outcomes.append({
            "url": r["url"],
            "reason_code": _classify_outcome(r),  # success | http_5xx | timeout | ...
            "status_code": r.get("status_code"),
            "content_length": len(r.get("html", "")),
        })

    return crawl_results, outcomes
```

`_dedupe_urls` normalises (lowercase scheme/host, strip trailing slash, strip fragments) before set-union.

### Fix 2 — sync_engine arithmetic + JSONB write

```python
# In sync_engine._execute_sync, AFTER the document loop:
skip_reasons = {
    "content_too_short": documents_short_skipped,
    # ...future reasons added here
}
documents_persisted = documents_total - documents_failed - sum(skip_reasons.values())

# Single UPDATE
await self._sync_runs.update_run(
    sync_run_id,
    documents_total=documents_total,
    documents_ok=documents_persisted,  # CORRECTED
    documents_failed=documents_failed,
    skip_reasons=skip_reasons,  # NEW JSONB column
)
```

### Schema migration (alembic, knowledge-ingest + connector)

```sql
-- knowledge-ingest
ALTER TABLE knowledge.crawl_jobs ADD COLUMN fetch_outcomes JSONB DEFAULT '[]'::jsonb;
ALTER TABLE knowledge.crawl_jobs ADD CONSTRAINT fetch_outcomes_valid_reasons
    CHECK (jsonb_typeof(fetch_outcomes) = 'array');
-- (per-element CHECK on reason_code is application-side because Postgres jsonb CHECK is awkward)

-- connector
ALTER TABLE connector.sync_runs ADD COLUMN skip_reasons JSONB DEFAULT '{}'::jsonb;
ALTER TABLE connector.sync_runs ADD CONSTRAINT skip_reasons_valid_keys
    CHECK (
        skip_reasons = '{}'::jsonb OR
        (SELECT bool_and(key IN (
            'content_too_short', 'auth_wall_detected', 'dedupe_content_hash_match',
            'dedupe_raw_html_hash_match', 'non_text_content', 'excluded_by_kb_config',
            'taxonomy_classify_failed'
        )) FROM jsonb_object_keys(skip_reasons) AS key)
    );
```

(The CHECK constraint syntax is illustrative; the exact form depends on Postgres-version SQL features. The constraint moves to a function-based CHECK if the inline form is rejected.)

## Phased rollout

Two phases, not five:

- **Phase F1 (one PR)**: alembic migrations + `reason_codes.py` + `sync_engine` arithmetic correction + `crawl_jobs.fetch_outcomes` write path. Behind feature flag `RECONCILE_ENABLED` (default `True` in dev, `False` in prod for one release cycle, then flipped).
- **Phase F2 (one PR)**: rewrite `crawl_site` with sitemap+BFS union + bulk `/crawl` call. Empirical validation gate: trigger against `voys/support`, AC-5 + AC-8 must pass live.

Plus one independent PR for the ast-grep rule (Fix 4). Three PRs total.

## Risks

| Risk | Mitigation |
|---|---|
| crawl4ai `/crawl` bulk endpoint behaviour differs from documented (timeouts, partial responses) | Phase F2 validation gate catches this empirically before main merge. Fallback: keep `/crawl/job` path available behind feature flag, switch back. |
| `MemoryAdaptiveDispatcher` defaults (max_session_permit=10) too aggressive for a free tier or too conservative for power tenants | Expose as setting `crawl4ai_max_session_permit` (default 10). Per-tenant overrides as a follow-up if needed. |
| `documents_short_skipped` arithmetic correction breaks downstream clients reading `documents_ok` | Search across portal-frontend + retrieval-api + any other repo for `documents_ok` consumers. Verify each handles the corrected (smaller) value. |
| Postgres CHECK constraint on JSONB rejects legitimate edge cases | Keep CHECK simple (key membership only); no value-shape validation. Application owns shape correctness. |
| ast-grep rule false-positives on legitimate unbounded-gather usage elsewhere | Rule scoped to `klai-knowledge-ingest/` and pattern matches only `crawl_page` arg specifically. Other `gather` usage unaffected. |
| `sync_runs.skip_reasons` JSONB grows unbounded per row | Bounded by enum size (~7 reasons today). Even if 1000s of skip events per sync, JSONB stores `{reason: count}` not per-event log. KB-scale per row. |

## References

### Empirical evidence

- `reports/taxonomy-v2.1-evaluation-2026-05-06/comparison.md` — Phase C of FOLLOWUP-001, where the silent-drop pattern surfaced
- Live diagnostic: 208 sitemap URLs vs 41 ingested for help.voys.nl (verified via `_fetch_sitemap_urls` in container 2026-05-06)
- Live diagnostic: 120 Notion pages via `client.search()` vs 79 in `knowledge.artifacts` (verified via Notion API in container 2026-05-06)
- crawl4ai REST `/crawl` accepts `urls: array<string>` (verified via openapi.json on production container 2026-05-06)

### Online research (the systems we evaluated)

- [crawl4ai multi-URL crawling](https://docs.crawl4ai.com/advanced/multi-url-crawling/) — `arun_many()` + dispatcher (we use the REST equivalent `/crawl`)
- [Apache Nutch fetch status taxonomy](https://nutch.apache.org/) — source of `FetchReasonCode` shape
- [Scrapy item_dropped signal](https://docs.scrapy.org/en/latest/topics/signals.html#scrapy.signals.item_dropped) — source of `PersistSkipReason` pattern
- [Singer specification](https://www.singer.io/) — evaluated and **not adopted** (wire protocol, not runtime)
- [Airbyte connector framework](https://docs.airbyte.com/connector-development) — evaluated and **not adopted** (multi-week migration cost for orchestration we already get from crawl4ai + Procrastinate)

### Klai SPEC context

- `.moai/specs/SPEC-TAXONOMY-V2-001-FOLLOWUP-001/spec.md` — parent SPEC; this SPEC fixes the data-coverage gap that made V2.2 less effective than possible
- `.moai/specs/SPEC-CRAWLER-005/` — the link-graph two-phase pattern (similar silent-drop class, fixed there at Fase 1 level)
- `.moai/specs/SPEC-CRAWLER-006/` — added `documents_short_skipped` logging; this SPEC promotes it to a column
- `.claude/rules/klai/projects/knowledge.md::HDBSCAN on raw high-dim embeddings fails` — sibling pitfall captured during FOLLOWUP-001

### Klai code surfaces touched

- `klai-knowledge-ingest/knowledge_ingest/crawl4ai_client.py::crawl_site` — Fix 1 rewrite
- `klai-connector/app/services/sync_engine.py::_execute_sync` — Fix 2 arithmetic + JSONB write (lines 159, 337, 490)
- `klai-knowledge-ingest/knowledge_ingest/reason_codes.py` — NEW (Fix 3)
- `klai-knowledge-ingest/knowledge_ingest/alembic/versions/<new>.py` — schema migration
- `klai-connector/alembic/versions/<new>.py` — schema migration
- `rules/no-unbounded-gather-crawl-page.yml` — NEW (Fix 4)

### Rejected alternative — v0.1 of this SPEC

Earlier draft proposed a 4-stage pipeline (`discovery → fetch → persist → reconcile`) with two new tables (`discovery_candidates`, `fetch_outcomes`), a `BaseAdapter` contract change touching 7 adapters, a 5-phase feature-flag rollout, and 9 PRs. **Rejected after evidence showed crawl4ai's `/crawl` REST endpoint already provides per-URL outcomes with built-in concurrency dispatch**, removing the need for ~70% of the proposed orchestration. The remaining ~30% (reason-code registry, JSONB persistence of skip counts, ast-grep prevention) is what this v0.2 implements.

The lesson captured in the rewrite: research industry patterns AND check whether the libraries already in the stack expose those patterns natively before designing a new layer.
