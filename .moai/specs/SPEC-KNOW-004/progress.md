## SPEC-KNOW-004 Progress

- Started: 2026-03-26
- Phase 1 complete: Execution plan goedgekeurd
- Phase 2 complete: DDD implementatie (manager-ddd) — 11 bestanden, 11 tests passing
- Phase 2.5 complete: TRUST 5 validatie PASS
- Phase 3 complete: Commit 64ad758 op feature/zod-reranker
- Sync complete: SPEC status → completed, PR aangemaakt
- Post-sync bug fixes (2026-03-26):
  - c85bbae: fix lazy auto-create personal KB row on first access (portal-backend)
  - aa53fbe: fix GHCR auth on core-01 (CI deploy workflow)
  - d51949b: add libpq5 to knowledge-ingest Docker image
  - cc0d591: use PsycopgConnector (procrastinate 2.x compatibility)
  - 86cd889: use libpq key=value DSN for procrastinate psycopg3
  - Playwright verified: /app/knowledge/personal werkt end-to-end
