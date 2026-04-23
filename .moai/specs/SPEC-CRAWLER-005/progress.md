## SPEC-CRAWLER-005 Progress

- Started: 2026-04-22
- Closed: 2026-04-23
- Status: **implemented + verified** (Fase 1-7 on `main`, Fase 6 live E2E on Voys `support` passed)
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

- ~~**Fase 6** — Live Playwright E2E on Voys `support`.~~ → **COMPLETED 2026-04-23**, see
  "Fase 6 verification" below.
- **Fase 5 full harness** — docker-compose orchestration for in-process stub-crawl4ai
  test. Skeleton shipped; full fixture server + compose config is follow-up.

### Fase 6 verification (2026-04-23 post-deploy)

**Setup**: Voys tenant `368884765035593759`, KB `support`, connector "Voys Help NL" via
portal UI, `max_pages=20`. Executed via Playwright MCP.

**Baseline before SPEC fixes** (167 chunks from pre-SPEC crawl):
- 0/167 chunks had `anchor_texts`
- 0/167 had `links_to`
- 0/167 had `chunk_type`
- 43/167 had `incoming_link_count`

**Post-fresh-sync** (after all SPEC-CRAWLER-005 fixes deployed, 161 chunks):
- 100% `source_type="crawl"` (was `"connector"`, fixed in commit `66ea2d0c`)
- 100% `source_connector_id` populated (was `None`, fixed in commit `66ea2d0c`)
- 100% `incoming_link_count` set
- 100% valid `chunk_type` after enrichment (procedural 81, reference 38, conceptual 22,
  warning 19, example 1)
- 60% `links_to` non-empty (pages with outbound links, capped at 20)
- 26% `anchor_texts` non-empty (pages with inbound links within the 20-page set)
- 55% `image_urls` populated (content-addressed SHA256 paths to Garage S3)

**Delete-cleanup verification** (connector delete via portal UI, post bug-fix deploy):
- Before delete: 161 Qdrant chunks, 20 artifacts, 20 crawled_pages, 32 page_links
- After delete: **0 chunks, 0 artifacts, 0 crawled_pages, 0 page_links**
- Remaining: 1 `sync_runs` row without matching `portal_connectors` parent — scoped for
  SPEC-CONNECTOR-CLEANUP-001 REQ-04 (new FK with ON DELETE CASCADE)

### Follow-up commits (post-Fase-6 bug-fixes)

| Commit | Purpose |
|--------|---------|
| `04dc434c` | Cleanup gap fix: `pg_store.delete_connector_artifacts` now also scrubs crawled_pages + page_links scoped by artifact URL set |
| `66ea2d0c` | `source_connector_id` threading + `source_type="crawl"` (was `"connector"`) — crawl chunks now match the Qdrant/artifact delete filter |

### Related follow-up SPECs

- `SPEC-CONNECTOR-CLEANUP-001` — legacy `connector.connectors` drop + missing FK
- `SPEC-CONNECTOR-SCHEDULING-001` (draft, blocked on CLEANUP-001) — reimplement cron scheduling

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
