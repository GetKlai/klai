# Task Decomposition — SPEC-CRAWLER-006

| Task ID | Description | Requirement | Dependencies | Planned Files | Status |
|---------|-------------|-------------|--------------|---------------|--------|
| T-001 | RED: failing test `test_run_web_crawler_delegation_fire_and_forget` | REQ-01.1, REQ-02.1, REQ-02.2 | - | klai-connector/tests/services/test_sync_engine_web_crawler.py | pending |
| T-002 | RED: failing test `test_no_cancel_path` | REQ-03.1 | - | klai-connector/tests/services/test_sync_engine_web_crawler.py | pending |
| T-003 | GREEN: rewrite `_run_web_crawler_delegation` to fire-and-forget | REQ-01, REQ-02, REQ-03 | T-001, T-002 | klai-connector/app/services/sync_engine.py | pending |
| T-004 | GREEN: remove unused class constants `_WEB_CRAWLER_POLL_INTERVAL_S` / `_WEB_CRAWLER_POLL_TIMEOUT_S` | REQ-02.1 | T-003 | klai-connector/app/services/sync_engine.py | pending |
| T-005 | RED: failing test for live status resolver (running case) | REQ-04.1 | - | klai-connector/tests/services/test_sync_run_resolver.py | pending |
| T-006 | RED: failing test for resolver terminalizing completed/failed | REQ-04.2, REQ-04.3 | T-005 | klai-connector/tests/services/test_sync_run_resolver.py | pending |
| T-007 | RED: failing test for resolver fallback on knowledge-ingest down | REQ-04.4 | T-005 | klai-connector/tests/services/test_sync_run_resolver.py | pending |
| T-008 | GREEN: add `SyncRunResolver` service that wraps live resolution | REQ-04 | T-005, T-006, T-007 | klai-connector/app/services/sync_run_resolver.py | pending |
| T-009 | RED: failing tests for 30s cache (hit + expiry) | REQ-05.1, REQ-05.2 | T-008 | klai-connector/tests/services/test_sync_run_resolver.py | pending |
| T-010 | GREEN: add per-job_id 30s TTL cache to resolver | REQ-05 | T-009 | klai-connector/app/services/sync_run_resolver.py | pending |
| T-011 | Wire SyncRunResolver into the existing portal-facing endpoints (sync_runs list + detail) | REQ-04 | T-010 | klai-connector/app/routes/sync_runs.py | pending |
| T-012 | RED: failing reaper tests (terminal, 404, still-running, 7d force-fail) | REQ-06.1-06.4 | - | klai-connector/tests/services/test_sync_run_reaper.py | pending |
| T-013 | GREEN: add `SyncRunReaper` running every 5 min as FastAPI lifespan task | REQ-06 | T-012 | klai-connector/app/services/sync_run_reaper.py, klai-connector/app/main.py | pending |
| T-014 | Add settings `SYNC_REAPER_TICK_S=300`, `SYNC_REAPER_FINALIZE_AFTER_H=24`, `SYNC_REAPER_FORCE_FAIL_AFTER_D=7` | REQ-06 | T-013 | klai-connector/app/core/config.py, klai-infra/core-01/.env.sops | pending |
| T-015 | RED: alembic migration test on test_db with seeded sync_runs + crawl_jobs | REQ-07.1, REQ-07.2, REQ-07.3 | - | klai-connector/tests/migrations/test_007_backfill_poll_timeout.py | pending |
| T-016 | GREEN: alembic migration `00X_backfill_poll_timeout_sync_runs.py` | REQ-07 | T-015 | klai-connector/alembic/versions/...py | pending |
| T-017 | Create `connector.sync_run_corrections` audit table in same migration | REQ-07.1 | T-015 | klai-connector/alembic/versions/...py | pending |
| T-018 | Verify Voys/Redcactus row is corrected after migration runs on prod | REQ-07.1 | T-016 (deployed) | (post-deploy verification) | pending |
| T-019 | Frontend: derive badge variant from sync_run shape including live fields | REQ-08.1, REQ-08.2, REQ-08.3, REQ-08.4 | T-011 (deployed) | klai-portal/frontend/src/features/connectors/SyncStatusBadge.tsx | pending |
| T-020 | Frontend: add progress bar component for crawler running state | REQ-08.1 | T-019 | klai-portal/frontend/src/features/connectors/SyncProgressBar.tsx | pending |
| T-021 | Playwright E2E: full Voys/Redcactus sync flow (REQ-04 + REQ-08) | REQ-04, REQ-08 | T-019, T-020 (deployed to staging) | klai-portal/e2e/connectors-redcactus-sync.spec.ts | pending |
| T-022 | Update `docs/architecture/knowledge-ingest-flow.md` Part 3 (delegation diagram) | - | T-003 | docs/architecture/knowledge-ingest-flow.md | pending |
| T-023 | Add pitfall entry: "two-writers anti-pattern across service boundaries" | - | T-003 | .claude/rules/klai/pitfalls/process-rules.md | pending |
| T-024 | Update SPEC-CRAWLER-004 frontmatter to mark REQ-03.4 (poll loop) as superseded by SPEC-CRAWLER-006 | - | T-003 | .moai/specs/SPEC-CRAWLER-004/spec.md | pending |
| T-025 | Update SPEC-WORKER-LANES-001 frontmatter to mark REQ-3 (best-effort cancel) as superseded by SPEC-CRAWLER-006 | - | T-003 | .moai/specs/SPEC-WORKER-LANES-001/spec.md | pending |
| T-026 | Set SPEC frontmatter status=implemented after T-001..T-025 | - | T-001..T-025 | .moai/specs/SPEC-CRAWLER-006/spec.md | pending |

## Execution order

1. **Backend logic** (T-001..T-014): in-process change to klai-connector. Can land in one PR.
2. **Backfill** (T-015..T-018): separate PR after backend lands. Migration runs once on deploy; verification is post-deploy.
3. **Frontend** (T-019..T-021): separate PR. Depends on backend exposing the live shape.
4. **Docs + supersedes** (T-022..T-026): final cleanup PR.

## Notes

- T-003 deletes ~80 lines of poll loop and replaces with ~10 lines of fire-and-forget. The diff is net-negative.
- T-008 + T-010 add ~150 lines for the resolver, mostly cache plumbing and tests.
- T-013 adds the reaper (~80 lines) — runs as a `lifespan` startup task with `asyncio.create_task` + cancel on shutdown. Idempotent; survives restarts.
- T-016 is the most operationally risky task. Run it in dry-run mode first
  (log only, no UPDATE) to validate the corrected row count before committing.
