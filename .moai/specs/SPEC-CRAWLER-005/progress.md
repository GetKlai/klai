## SPEC-CRAWLER-005 Progress

- Started: 2026-04-22
- Status: **implemented** (Fase 1-5 + 7 landed on `main`; Fase 6 deferred to live prod verification)
- Development mode: TDD (RED-GREEN-REFACTOR)
- Harness level: standard

### Commits on main

| Fase | Commit | Summary |
|------|--------|---------|
| SPEC annotation | c81a0cfa | `.moai/specs/SPEC-CRAWLER-005/` files |
| Fase 1 | f8c4875b | Two-phase crawl pipeline (`_build_link_graph` + refactor) |
| Fase 2 | 481b5a31 | Restore 3 `test_crawl_link_fields.py` tests |
| Fase 3 | bcbfe836 | `chunk_type` retry + fallback in `enrichment.py` |
| Fase 4 | c3221af3 | `payload_list` helper + retrieval-api sweep |
| Fase 5 | 64925167 | Integration test skeleton (env-gated, full harness follow-up) |
| Fase 7 | 719ee439 | Docs + pitfall entries + SPEC status=implemented |
| Fase 7 wrap | (this commit) | progress.md final commit table + deferred items |

### Test delta

- New: `test_build_link_graph.py` (4), `test_crawler_link_fields_complete.py` (2),
  `test_chunk_type_crawl.py` (6), `test_payload_util.py` (10), integration skeleton (1 skipped)
- Updated: `test_crawl_link_fields.py` (3 restored), `test_crawler_link_fields.py` (1 rewritten),
  `test_page_links.py` (1 rewritten), `test_enrichment.py` (1 rewritten)

### Deferred

- **Fase 6** — Live Playwright E2E on Voys `support`. Depends on production deploy of the
  new `main`. Destructive (deletes KB artifacts, re-syncs). Must be user-triggered after
  deploy is verified.
- **Fase 5 full harness** — docker-compose orchestration for in-process stub-crawl4ai
  test. Skeleton shipped; full fixture server + compose config is follow-up.

### Phase log

- Phase 0.9: language detection → Python (uv, pytest, ruff, pyright)
- Phase 0.95: scale-based mode → Full Pipeline (≥10 files, 3 domains: ingest + retrieval + docs)
- Phase 1.6: acceptance criteria registered as pending tasks (see tasks.md)
- Phase 1.8: MX context map — `run_crawl_job` already had `@MX:ANCHOR AuthWallDetected`

### Incidents during execution

- Branch auto-switched multiple times between `feature/SPEC-CRAWLER-005` /
  `feature/SPEC-KB-IMAGE-002` / `main` due to a parallel Claude session sharing the same
  working directory. User stopped the parallel process. Final wrap-up committed from a
  dedicated git worktree (`.claude/worktrees/spec-005-wrap-up`) to isolate the branch
  from further concurrent motion.
