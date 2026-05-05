---
id: SPEC-LOGGING-EXTRACT-001
version: "0.1.0"
status: draft
created: 2026-05-05
updated: 2026-05-05
author: Mark Vletter
priority: medium
related:
  - SPEC-CODEBASE-AUDIT-001 (parent, Cluster J)
---

# SPEC-LOGGING-EXTRACT-001: Extract `setup_logging()` + `RequestContextMiddleware` naar `klai-libs/log-utils`

## Summary

8 services dupliceren een 80-123 LOC `logging_setup.py` met dezelfde `setup_logging()` + `RequestContextMiddleware` shape. Per `reports/audit-2026-05-04/maintainability-duplication-naming.md` 5.2: ~560 LOC dedup-kans. Plus standardize `uvicorn.access` op INFO+healthcheck-filter (mailer pattern, lessons learned uit 4-h outage).

## Motivation

- DRY: één canonical implementation in `klai-libs/log-utils/log_utils/structlog_setup.py`
- Consistency: alle services krijgen `RequestContextMiddleware` + `_HealthCheckAccessFilter` zoals mailer (na 4-h outage 2026-04-29)
- Mailer is precedent: `INFO + healthcheck-filter` voorkomt dat unhandled exceptions onzichtbaar worden in `docker logs`

## Scope

### In scope

1. **`klai-libs/log-utils/log_utils/structlog_setup.py`** (nieuwe module):
   - `setup_logging(service_name: str)` — structlog config + ProcessorFormatter + uvicorn.access INFO + `_HealthCheckAccessFilter`
   - `RequestContextMiddleware` (FastAPI middleware) — bind `request_id`, `org_id` op contextvars; respect `X-Request-ID` header van Caddy
   - Re-exports in `log_utils/__init__.py`
   - Unit tests in `klai-libs/log-utils/tests/test_structlog_setup.py`

2. **Per service migratie** (8 services):
   - klai-portal/backend
   - klai-knowledge-ingest
   - klai-retrieval-api
   - klai-knowledge-mcp (mocht het wel/niet hebben — zie audit)
   - klai-connector
   - klai-mailer (al canonical, verwijder eigen kopie als shared)
   - klai-scribe/scribe-api
   - klai-scribe/whisper-server
   - klai-focus/research-api
   
   Per service:
   - Add `klai-log-utils` als path-dep in pyproject.toml `[tool.uv.sources]` (al voor portal/connector/mcp/scribe; uitbreiden naar overige)
   - Vervang lokale `logging_setup.py` import door `from log_utils.structlog_setup import setup_logging, RequestContextMiddleware`
   - Verwijder lokale `logging_setup.py` file
   - Verifieer service-startup logs nog hetzelfde JSON-format produceren

3. **Naming conventie fix** (uit audit 5.5):
   - portal-api: rename `LoggingContextMiddleware` → `RequestContextMiddleware` (matcht rest)

### Out of scope

- klai-libs naming-prefix fix (`log_utils` → `klai_log_utils`) — separate SPEC die ALLE klai-libs prefix-consistent maakt
- Cross-service trace-correlation header changes — al in place via `app.trace.get_trace_headers()`

## Acceptance criteria

1. `klai-libs/log-utils` heeft `structlog_setup` module met >85% coverage
2. Alle 8 services importeren `setup_logging` + `RequestContextMiddleware` uit klai-libs
3. Per service: `docker logs <ctr> 2>&1 | head -5` toont identiek JSON-format
4. Smoke-test: één request met `X-Request-ID: <uuid>` toont propagation in logs over alle services
5. Geen lokale `logging_setup.py` files meer

## Risks

| Risk | Mitigatie |
|---|---|
| Path-dep build context faalt voor services die nu niet repo-root build (mailer, focus) | Eerst migrate naar repo-root build context (al onderdeel van separate maintainability SPEC) — tijdelijk: kopieer log-utils inhoud per service |
| Healthcheck-filter regression | Per service smoke-test `curl /health` + verify logs |
| Test-fixtures verwachten lokale `logging_setup` path | Update test-imports in elke service |

## References

- `reports/audit-2026-05-04/maintainability-duplication-naming.md` (5.2)
- `reports/audit-2026-05-04/pattern-divergence.md` (rij 11-13)
- `klai-mailer/app/logging_setup.py` — canonical met `_HealthCheckAccessFilter`
- `pitfalls/process-rules.md::redis-url-password-must-be-parsed-manually` — uitleg waarom mailer access-logs INFO zijn (4-h outage)
