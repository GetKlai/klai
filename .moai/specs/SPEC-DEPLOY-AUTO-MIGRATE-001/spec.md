# SPEC-DEPLOY-AUTO-MIGRATE-001: Auto-migrate on container start — remaining 5 services

## Status
STUB — filed as follow-up from SPEC-SEC-AUDIT-2026-04 C5.

## Background
SPEC-SEC-AUDIT-2026-04 C5 fixed the `scribe-deploy-no-alembic` pattern for scribe-api
by adding `klai-scribe/scribe-api/entrypoint.sh` (mirrors portal-api's entrypoint.sh
from SPEC-CHAT-TEMPLATES-CLEANUP-001). The same pattern must be applied to the remaining
5 services that have their own alembic migrations but still launch via `CMD uvicorn ...`.

See `.claude/rules/klai/pitfalls/process-rules.md#scribe-deploy-no-alembic` for the
canonical pitfall description and the canonical entrypoint.sh template.

## Services in scope

| Service | Dockerfile CMD | Has alembic? | Priority |
|---|---|---|---|
| klai-connector | `CMD uvicorn …` | Check pyproject.toml | High |
| klai-mailer | `CMD uvicorn …` | Check pyproject.toml | High |
| klai-knowledge-ingest | `CMD uvicorn …` | Check pyproject.toml | High |
| klai-retrieval-api | `CMD uvicorn …` | Check pyproject.toml | High |
| klai-knowledge-mcp | `CMD python main.py` | Check if uses alembic | Medium |

Note: Only services with actual alembic migration directories need the entrypoint.
Before implementing, verify each service has `alembic/` directory and `alembic.ini`.
Services without alembic (no DB schema) can skip.

## Acceptance criteria

For each in-scope service that has alembic migrations:

- [ ] `entrypoint.sh` created at `<service-root>/entrypoint.sh` mirroring the portal-api/scribe-api pattern
- [ ] `Dockerfile` updated: COPY + chmod + ENTRYPOINT (remove CMD)
- [ ] CI workflow does NOT need a manual `docker exec alembic upgrade head` step
- [ ] Service boots and migrations apply on `docker compose up -d`
- [ ] Pitfall table in `process-rules.md` updated to YES for each fixed service

## Implementation pattern

Copy from `klai-scribe/scribe-api/entrypoint.sh`. Adjust:
- Port number (8020 for scribe, 8040 for connector, etc. — check existing CMD)
- Module path (`app.main:app` — check existing CMD)

## Out of scope
- portal-api (already fixed)
- scribe-api (fixed by SPEC-SEC-AUDIT-2026-04 C5)

## References
- Canonical entrypoint: `klai-portal/backend/entrypoint.sh`
- scribe-api entrypoint: `klai-scribe/scribe-api/entrypoint.sh`
- Pitfall: `.claude/rules/klai/pitfalls/process-rules.md#scribe-deploy-no-alembic`
- Shared launcher: `scripts/uvicorn-launch.sh` (SPEC-SEC-WEBHOOK-001 REQ-6)
