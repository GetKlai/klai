# Klai docs

Shared documentation for the Klai monorepo. Keep this directory focused on
human-readable product, architecture, runbook, research, and historical records.
Agent operating rules live in `.claude/rules/klai/`, not here.

## Active references

| Directory | Contents |
|-----------|----------|
| `architecture/` | Platform-wide architecture and engineering references. The `knowledge-*-flow.md` docs are the most operationally specific references for the running knowledge system. |
| `runbooks/` | Operational procedures for deploys, incidents, local development, tenant/user lifecycle, retrieval quality, security gates, telemetry, and integrations. |
| `privacy/` | Telemetry and DPA documentation. |
| `setup/` | Developer setup notes that do not belong to a single subproject. |
| `testing/` | Test-suite planning and coverage strategy. |
| `gtm/` | Go-to-market notes. |

## Research and history

| Directory / file | Contents |
|------------------|----------|
| `research/` | Research synthesis and implementation planning. Useful as rationale; not the source of truth when it conflicts with `architecture/` engineering references or live code. |
| `audit-ingest-pipeline-2026-05-06/` | Historical ingest pipeline audit with findings and supporting research. Keep for traceability. |
| `retros/` | Incident and process retrospectives. |
| `specs/` | Archived SPEC documents. These are retained for context, not as proof that the described work is still current. |
| `knowledge-retrieval-low-confidence-abstain-2026-05-08.md` | Historical design/debugging note for low-confidence retrieval behaviour. |

## Maintenance rules

- Prefer updating an existing runbook or engineering reference over adding a new
  standalone note.
- If a document is superseded, say so at the top and link to the replacement.
- Do not add generated screenshots, Playwright traces, or local `.DS_Store`
  files under `docs/`.
- Before deleting tracked docs, verify references with `rg` and preserve
  migration/audit history when it explains shipped behaviour.
