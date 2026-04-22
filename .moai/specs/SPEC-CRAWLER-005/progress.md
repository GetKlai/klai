## SPEC-CRAWLER-005 Progress

- Started: 2026-04-22
- Development mode: TDD (RED-GREEN-REFACTOR)
- Harness level: standard
- Branch: feature/SPEC-CRAWLER-005

### Phase log

- Phase 0.9: language detection → Python (uv, pytest, ruff, pyright)
- Phase 0.95: scale-based mode → Full Pipeline (≥10 files, 3 domains: ingest + retrieval + docs)
- Phase 1.6: acceptance criteria registered as pending tasks (see tasks.md)
- Phase 1.7: scaffolding deferred (all target files exist except new tests + `app/util/payload.py`)
- Phase 1.8: MX context map — `run_crawl_job` has `@MX:ANCHOR AuthWallDetected`, no WARN on interleave site

### Implementation order

Fases uitgevoerd in SPEC-plan volgorde: 1 → 2 → 3 → 4 → 5 → 6 → 7.
Geen pauze tussen fases (dev-fase, direct merge naar main).
