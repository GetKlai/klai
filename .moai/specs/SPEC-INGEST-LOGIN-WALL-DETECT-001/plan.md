# Implementation Plan — SPEC-INGEST-LOGIN-WALL-DETECT-001

Phased rollout: prevention first, then backfill the existing pollution, then
verify with a real re-crawl test, then add the retrieval-side defence.
Last phase is the authenticated re-crawl as a separate effort outside this SPEC.

Methodology: TDD per project convention. Each phase has a failing test first,
then implementation, then refactor.

Worktree: `git worktree add ../klai-login-wall -b feature/login-wall-detect main`
before any edits (multi-file change, > 5 tool calls expected).

---

## Phase A — Detector function + golden fixtures

**Files**:
- `klai-knowledge-ingest/knowledge_ingest/utils/auth_wall_detector.py` (new)
- `klai-knowledge-ingest/tests/test_auth_wall_detector.py` (new)
- `klai-knowledge-ingest/tests/fixtures/auth_walls/` (new dir, 4-5 fixtures)
- `klai-knowledge-ingest/tests/fixtures/clean_pages/` (new dir, 2-3 negative
  fixtures)

**Steps**:
1. Capture real markdown samples to fixtures (RED):
   - `redcactus_hubspot.md` — fetch from
     `knowledge.crawled_pages WHERE url LIKE '%redcactus%/HubSpot' LIMIT 1`
   - `redcactus_hubspot_embedded.md` — same but `/hubspot-embedded`
   - `confluence_login_required.md` — synthetic but realistic (login-redirect
     pattern from Atlassian Confluence)
   - `wordpress_login_redirect.md` — synthetic with `wp-login.php?redirect_to=`
   - `notion_private_page.md` — synthetic, public-page-with-login-CTA from
     Notion
   - `voys_help_cancellation.md` — clean (negative case)
   - `auth_documentation_tutorial.md` — clean (false-positive guard case —
     a tutorial that legitimately discusses "log in with your account")
   - `de_only_login.md` — German-only login page (negative case, documents
     known gap per AC-02.2b — DE-only without redirect_to= should not be
     flagged in v1; fix in follow-up SPEC if a DE tenant onboards)
2. Write failing tests for AC-01, AC-02 (RED)
3. Implement `detect_anonymous_auth_wall` with all 4 conditions + FP guard
   (GREEN)
4. Benchmark: AC-01.2 latency assertion (REFACTOR if needed)
5. `AuthWallSignal` dataclass

Exit criteria: All AC-01 + AC-02 tests pass, mypy clean, ruff clean.

---

## Phase B — Integration in `_ingest_crawl_result`

**Files**:
- `klai-knowledge-ingest/knowledge_ingest/adapters/crawler.py` (modify)
- `klai-knowledge-ingest/knowledge_ingest/qdrant_store.py` (modify — accept
  `quality_score` param + `metadata` extras)
- `klai-knowledge-ingest/knowledge_ingest/config.py` (modify — read env vars)
- `klai-knowledge-ingest/tests/test_ingest_login_wall_integration.py` (new)

**Steps**:
1. Add `AnonymousAuthWallDetected` exception class (parallel to
   `AuthWallDetected`)
2. Add config reading for `KLAI_INGEST_LOGIN_WALL_DETECT_ENABLED` and
   `_MODE`, fail-safe to `audit_only` on invalid value (AC-05.2)
3. Modify `_ingest_crawl_result`: detector call between dedup-check and
   chunking, branch on mode
4. Modify `qdrant_store` to accept optional `quality_score_override` and
   `extra_metadata` (used only by `degrade` mode)
5. Tests for AC-03.1, AC-03.2, AC-03.3, AC-05.1, AC-05.2

Exit criteria: All AC-03 + AC-05 tests pass.

---

## Phase C — `run_crawl_job` BFS handling + Alembic

**Files**:
- `klai-knowledge-ingest/knowledge_ingest/adapters/crawler.py` (modify
  `run_crawl_job`)
- `klai-knowledge-ingest/alembic/versions/XXXX_add_crawl_jobs_error_summary.py`
  (new)
- `klai-knowledge-ingest/knowledge_ingest/db/crawl_jobs.py` (modify)
- `klai-knowledge-ingest/tests/test_crawler_anonymous_auth_wall.py` (new)

**Alembic migration**:
- ADD COLUMN `error_summary jsonb NULL`
- ADD VALUE `failed_partial` to `crawl_job_status` enum (use
  `ALTER TYPE ... ADD VALUE IF NOT EXISTS`)
- Per `klai-knowledge-ingest/alembic/env.py`, schema-isolated alembic_version
  in `knowledge` schema

**Steps**:
1. Migration first (RED — code references nonexistent column will fail)
2. Update `run_crawl_job`: catch `AnonymousAuthWallDetected`, append to
   `auth_wall_pages` list, continue iteration
3. After BFS complete: write `error_summary` and decide
   `succeeded` vs `failed_partial`
4. Tests for AC-04.1, AC-04.2, AC-04.3, AC-04.4 (regression on AC-04.4)

Exit criteria: All AC-04 tests pass; existing `test_crawler_login_indicator.py`
still green (no regression on authenticated path).

---

## Phase D — Backfill task

**Files**:
- `klai-knowledge-ingest/knowledge_ingest/backfill_tasks.py` (new — new file
  per SPEC-INGEST-QUEUE-SEPARATION-001 conventions)
- `klai-knowledge-ingest/knowledge_ingest/queues.py` (modify — register
  task module if needed; verify the existing `enrich-bulk` queue accepts
  this task per the semgrep rule in
  `rules/knowledge_ingest_queue_constants.yml`)
- `klai-knowledge-ingest/tests/test_backfill_login_walls.py` (new)

**Steps**:
1. Implement `backfill_detect_login_walls(org_id, kb_slug)`:
   - Set RLS context via existing helper
   - Stream `crawled_pages` in batches, run detector
   - On match: Qdrant `delete_points` with `Filter.must=[
       FieldCondition(key="org_id", match=MatchValue(value=org_id)),
       FieldCondition(key="kb_slug", match=MatchValue(value=kb_slug)),
       FieldCondition(key="path", match=MatchValue(value=page.url))]`
   - UPDATE `crawled_pages SET content_hash = '__login_wall_purged__'`
2. Idempotency check: skip pages where `content_hash =
   '__login_wall_purged__'`
3. Tests for AC-06.1, AC-06.2, AC-06.3, AC-09.1, AC-09.2

**Exit criteria**: All AC-06 + AC-09 tests pass.

**Operator-facing artifact**: a small CLI `python -m
knowledge_ingest.backfill_tasks --org voys --kb support` that enqueues
the Procrastinate task. Documented in
`docs/runbooks/login-wall-backfill.md` (new).

---

## Phase E — Retrieval-side hard floor

**Files**:
- `klai-retrieval-api/retrieval_api/api/retrieve.py` (modify)
- `klai-retrieval-api/retrieval_api/config.py` (modify)
- `klai-retrieval-api/tests/test_quality_floor.py` (new)

**Steps**:
1. Add `KLAI_RETRIEVAL_QUALITY_FLOOR` config (default `0.05`)
2. After reranker, before `quality_boost`:
   `filtered = [c for c in reranked if c.payload.quality_score >= floor]`
3. Record `quality_floor_filtered = len(reranked) - len(filtered)` in
   decision_record
4. Tests for AC-07.1, AC-07.2, AC-07.3

Exit criteria: All AC-07 tests pass.

---

## Phase F — Observability

**Files**:
- `klai-knowledge-ingest/knowledge_ingest/metrics.py` (modify or new)
- `klai-retrieval-api/retrieval_api/metrics.py` (modify)
- `klai-infra/grafana/dashboards/knowledge-ingest.json` (modify)
- `klai-infra/alerting/rules/knowledge-ingest.yml` (modify)

**Steps**:
1. Define Prometheus metrics per REQ-08
2. Wire into detector + retrieval flow
3. Add Grafana panel via dashboard JSON
4. Add alerting rule with `for: 5m` (avoid flapping on small jobs)
5. Tests for AC-08.1, AC-08.2, AC-08.3
6. Manual test in staging for AC-08.4 (alert firing)

Exit criteria: All AC-08 tests pass + dashboard panel visible.

---

## Phase G — Production rollout (canary → reject → backfill)

Sequence (no code changes, ops only):

1. Deploy with `KLAI_INGEST_LOGIN_WALL_DETECT_MODE=audit_only`. Window: hours,
   not days. Inspect Grafana counter for detection rate matching the ~35%
   expected from voys's existing data. Sample-check 20 detections for false
   positives against fixtures.
2. If FP-rate < 1% on canary sample → switch directly to
   `KLAI_INGEST_LOGIN_WALL_DETECT_MODE=reject`. Skipping `degrade` is
   intentional: simpler code path, no walled chunks left behind for the
   retrieval-floor to clean up.
3. Run `python -m knowledge_ingest.backfill_tasks --org voys --kb support`
   (cleans 150 pages from Qdrant). Manual operator command, idempotent.
4. Verify via re-running the original failing query. AC-10.2.
5. Run backfill for `getklai/*` (1 page). Same CLI.

If FP-rate is unexpectedly high in step 1, fall back to `audit_only` and
iterate on the detector before continuing.

---

## Phase H — Re-crawl smoke test

Final acceptance per REQ-10. Two ways to verify:

**Option 1 — Production re-crawl (lower risk after Phase G)**:
- Trigger single-URL re-crawl on
  `https://wiki.redcactus.cloud/nl/crm-software/HubSpot`
- Verify `crawl_jobs.error_summary` populated (AC-10.1)
- Run the original LLM query end-to-end (AC-10.2)

**Option 2 — Sacrificial test page**:
- Set up a short-lived crawl source (e.g., the WordPress fixture URL pattern
  on a test domain) and run the full pipeline
- Same verification, isolated from production data

Document outcome in the sync-phase PR description.

---

## Decisions resolved during SPEC drafting

These were initially listed as open questions but resolved before annotation:

1. **Mode default = `reject`** with `audit_only` as canary-mode (hours, not
   days). `degrade` remains in the codebase as an edge-case mode for tenants
   wanting an audit-trail, but it is NOT part of the standard rollout. One
   code path in production, one set of mental models for ops.
2. **`failed_partial` enum**: standard pattern in this codebase, no special
   review needed. Alembic `ALTER TYPE ADD VALUE IF NOT EXISTS` per the
   convention in `klai-knowledge-ingest/alembic/env.py`.
3. **Backfill = CLI-only**, no auto-on-deploy. Avoids race with running
   crawls; operator gets visible delete counts before next crawl cycle.
4. **Retrieval floor activates immediately at threshold 0.05**, no shadow
   mode. The floor only filters chunks with `quality_score == 0.0` — chunks
   that someone explicitly degraded. Default 0.5 chunks pass through. Risk of
   filtering legitimate content is zero because no current code path produces
   `quality_score < 0.05` other than the new degrade mode itself.
5. **Languages = EN + NL only.** Voys and getklai are both NL-primary. Most
   CMS login walls emit English fallback strings even on non-English locales
   (Confluence, WordPress, MediaWiki, Notion). Add languages on tenant-
   onboarding demand, not preemptively.

## Open questions for plan-phase annotation

None remaining. Ready for /moai run.
