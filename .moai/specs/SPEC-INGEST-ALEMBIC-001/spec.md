---
id: SPEC-INGEST-ALEMBIC-001
version: "0.1.0"
status: draft
created: 2026-05-05
updated: 2026-05-05
author: Mark Vletter
priority: high
related:
  - SPEC-CODEBASE-AUDIT-001 (parent, Cluster I)
  - SPEC-CONNECTOR-DELETE-LIFECYCLE-001 (precedent met `knowledge.artifact_images`)
---

# SPEC-INGEST-ALEMBIC-001: Bootstrap Alembic in knowledge-ingest

## Summary

knowledge-ingest is **niet alembic-managed** — alleen één raw `migrations/001_crawl_domains.sql` + runtime DDL via `pg_store`. Per `reports/audit-2026-05-04/db-schema-consistency.md` TP-8: schema-evolutie loopt impliciet via portal-api alembic OF runtime, geen migration history, geen rollback. Audit-gat: fresh `docker compose down -v` zou ingest niet starten zonder manuele bootstrap-DDL.

## Motivation

Per `pitfalls/process-rules.md::scribe-deploy-no-alembic` (HIGH): elke service met DDL nodig heeft een entrypoint met `alembic upgrade head`. knowledge-ingest is de laatste service met dit gat.

## Scope

### In scope

1. **Bootstrap alembic dir** in knowledge-ingest:
   - `alembic.ini` mirroring portal-api/connector pattern
   - `alembic/env.py` met dual-mode online/offline
   - `alembic/script.py.mako`
   - `alembic/versions/0001_baseline.py` met:
     - `op.execute("CREATE SCHEMA IF NOT EXISTS knowledge")`
     - Alle huidige `knowledge.*` tabellen (extracted via `pg_dump --schema-only --schema=knowledge` van prod)
     - Plus content van bestaande `migrations/001_crawl_domains.sql`
2. **`klai-knowledge-ingest/entrypoint.sh`** mirror van klai-connector:
   ```bash
   #!/bin/bash
   set -eu
   export PYTHONPATH=.
   alembic upgrade head
   exec /app/scripts/uvicorn-launch.sh knowledge_ingest.app:app --host 0.0.0.0 --port 8000
   ```
3. **Dockerfile update**: COPY entrypoint.sh + ENTRYPOINT
4. **Stamp prod als baseline** (deploy-step, niet code):
   ```bash
   ssh core-01 "docker exec klai-core-knowledge-ingest-1 alembic stamp 0001_baseline"
   ```
5. **Cleanup**: verwijder `klai-knowledge-ingest/migrations/001_crawl_domains.sql`
6. **CI guard**: extend `rules/no-alembic-without-entrypoint.yml` ast-grep om knowledge-ingest mee te nemen

### Future scope (apart SPEC)

- Schema-ownership migreren: portal-api stopt met `knowledge.*` tabellen aanmaken; knowledge-ingest neemt het over

## Acceptance criteria

1. Fresh dev-stack (`docker compose down -v && docker compose up -d`) start knowledge-ingest succesvol — schema bootstrap automatisch
2. `alembic stamp` op prod werkt zonder DDL-uitvoering
3. ruff check clean op alembic/* files
4. ast-grep `no-alembic-without-entrypoint` rule fired niet meer voor knowledge-ingest
5. Smoke-test: trigger nieuwe migration on top → werkt via entrypoint pre-flight

## Risks

| Risk | Mitigatie |
|---|---|
| `alembic stamp` op prod faalt | First test op staging; verify `alembic current` post-stamp |
| Conflicten met portal-api alembic die historisch ook `knowledge.*` schreef | Audit pre-baseline of portal-api migraties referenties hebben naar `knowledge.*`; eventueel coordinated migration |
| Entrypoint deploy hangt op import error | Pre-validate met `alembic upgrade head --sql` lokaal |

## References

- `reports/audit-2026-05-04/db-schema-consistency.md` (TP-8)
- `reports/audit-2026-05-04/deep-pass-knowledge-ingest.md` (sectie 4)
- `klai-connector/entrypoint.sh` — canonical pattern
- `.claude/rules/klai/pitfalls/process-rules.md::scribe-deploy-no-alembic`
