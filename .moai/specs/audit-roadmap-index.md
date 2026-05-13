# SPEC-CODEBASE-AUDIT-001 — fix-SPEC roadmap index

Geconsolideerd uit `reports/audit-2026-05-04/roadmap.md` (15 fix-SPECs in 4 waves).
Stub-files zijn aangemaakt voor de hoogste-prioriteit 7. Resterende 8 zijn nog te
schrijven via `manager-spec` agent op basis van `synthesis.md` clusters.

## Created stubs (✅)

| SPEC | Cluster | Wave | Status |
|---|---|---|---|
| SPEC-SEC-VALIDATOR-COVERAGE-001 | A — Settings & validators | 1 | draft |
| SPEC-SEC-PORTAL-RLS-001 | C — RLS coverage | 1 | draft |
| SPEC-SEC-AUTH-HARDENING-001 | D — Auth-flow hardening | 1 | draft |
| SPEC-SEC-EDGE-CSP-001 | E — Edge security | 1 | in-progress (PR #313 deels) |
| SPEC-INFRA-TENANT-DELETE-002 | F — GDPR/PII | 1 | draft |
| SPEC-RESTORE-001 | M — Operations | 2 | draft |
| SPEC-INGEST-ALEMBIC-001 | I — DB schema | 2 | draft |
| SPEC-LOGGING-EXTRACT-001 | J — Maintainability | 3 | draft |

## Already in-flight or done

| Item | Status |
|---|---|
| Wave-0 cleanup (~1966 LOC delete) | PR #310 |
| FRONTEND_URL host-allowlist validator (Adv 3+4) | PR #310 |
| klai-docs rehype-sanitize XSS fix | PR #313 |
| knowledge-ingest header drift (Cluster G TP-1) | PR #314 |

## To-be-stubbed (later via manager-spec)

| SPEC | Cluster | Wave |
|---|---|---|
| SPEC-INFRA-REDIS-SPLIT-001 (al ergens gedraft) | B | 1 |
| SPEC-DB-SCHEMA-HARMONIZATION-001 | I | 2 |
| SPEC-CI-COVERAGE-001 | K | 2 |
| SPEC-API-VERSION-001 (+ SPEC-INGEST-HEADER-DRIFT-001 closed via PR #314) | G | 3 |
| SPEC-MAINTAINABILITY-REFACTOR-001 (top-5 F-grade functies) | J | 3 |
| SPEC-CONNECTOR-CLEANUP-001 EXTEND (al draft) | H | 3 |
| SPEC-CODEBASE-CLEANUP-001 (rest van dead code) | H | 3 |
| SPEC-COVERAGE-CONFIG-001 + SPEC-COVERAGE-CRITICAL-MODULES-001 + SPEC-CONTRACT-TESTS-001 | K | 4 |
| SPEC-DEPS-CONSOLIDATION-001 | N | 4 |
| SPEC-OBS-METRICS-001 | M | 4 |
| SPEC-DOCS-DRIFT-001 | L | 4 |

## Cross-references

- Master roadmap: `reports/audit-2026-05-04/roadmap.md`
- Findings synthesis: `reports/audit-2026-05-04/synthesis.md`
- Parent audit SPEC: `.moai/specs/SPEC-CODEBASE-AUDIT-001/spec.md` (v0.7.0, completed)
