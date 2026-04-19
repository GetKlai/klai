---
id: SPEC-SEC-009
version: 0.1.0
status: draft
created: 2026-04-19
updated: 2026-04-19
author: Mark Vletter
priority: low
---

# SPEC-SEC-009: SERVERS.md Documentation Drift

## HISTORY

### v0.1.0 (2026-04-19)
- Initial draft from Fase 3 security audit (post-Caddy-verify)
- Sourced from `.moai/audit/04-3-prework-caddy.md` Caddy verify FINDINGS table
- Roadmap context: `.moai/audit/99-fix-roadmap.md` § SEC-009

---

## Goal

`klai-infra/SERVERS.md` accurately reflects every publicly-routed service in `/opt/klai/caddy/Caddyfile`. The Caddy state section becomes a reviewable single-source-of-truth inventory of public routes, their target services, auth layers, rate limits, and whether each service is Docker-internal only or internet-exposed.

This is a documentation-only fix. It codifies the reality already discovered by the audit; it does not change auth, routing, or infrastructure behavior. The hardening work triggered by the same findings (F-017, F-018, F-020, F-022) lives in SEC-008.

## Success Criteria

- Every route in `/opt/klai/caddy/Caddyfile` is listed in SERVERS.md with its target service, auth layer, rate limit, and internal-vs-public marker
- SERVERS.md explicitly marks retrieval-api, knowledge-ingest, klai-mailer, klai-knowledge-mcp, PostgreSQL, Qdrant, FalkorDB, and Ollama as Docker-internal only (not exposed via Caddy)
- The route table appears in the same format in `.claude/rules/klai/infra/servers.md` and/or `.claude/rules/klai/platform/caddy.md` if present, so rules context matches repo documentation
- A maintenance note in SERVERS.md instructs contributors to keep the Caddy state section in sync with `/opt/klai/caddy/Caddyfile` on every Caddy change, with a pointer to `.moai/audit/04-3-prework-caddy.md` as the verified reference snapshot
- Reviewers can answer "is X publicly reachable?" from SERVERS.md alone without SSHing to core-01

---

## Environment

- Scope: `klai-infra` repo only
- Files touched: `klai-infra/SERVERS.md` (primary), optional mirror in `.claude/rules/klai/infra/servers.md` or `.claude/rules/klai/platform/caddy.md`
- No service code, no Caddy config, no compose, no SOPS changes
- Markdown-only PR

## Assumptions

- The route table in `.moai/audit/04-3-prework-caddy.md` § "Caddy verify — FINDINGS" is a faithful snapshot of the live `/opt/klai/caddy/Caddyfile` as of 2026-04-19
- No auth-layer changes will land before this SPEC merges; if they do, this SPEC's table is refreshed rather than the SPEC re-scoped
- `klai-infra/SERVERS.md` remains the primary operational reference for server state; the Caddy state section owns the public route inventory
- Contributors have push access to `klai-infra`; no GitHub policy changes needed

---

## Out of Scope

- SEC-008 hardening work (connector public-exposure review, dev-env allowlisting, vexa-bot-manager auth audit, docs-app auth audit) — this SPEC only updates docs to match reality; it does NOT change auth or expose anything new
- Automated enforcement (pre-commit or CI that fails when Caddyfile changes without a SERVERS.md diff) — mentioned as a future option in plan.md step 4, not required for this SPEC
- Docker network inventory updates beyond what is already in SERVERS.md § "Docker networks" — the existing network section stays as-is
- Changes to `.claude/rules/klai/infra/servers.md` beyond mirroring the Caddy table, if that file already exists
- Any Caddy config edits, auth additions, rate-limit changes, or route removals

---

## Findings Addressed

Documentation side of the Caddy-drift findings from `.moai/audit/04-3-prework-caddy.md`:

| Finding | Route | What SERVERS.md currently shows | What SERVERS.md MUST show |
|---|---|---|---|
| **F-017** | `connector.getklai.com` → klai-connector:8200 | Absent from Caddy state (SERVERS.md implies klai-connector is internal only via `klai-net`) | Listed as PUBLIC with auth: Zitadel introspection + portal bypass, rate-limit 60/min/IP |
| **F-018** | `dev.getklai.com` → portal-api-dev:8010 + dev SPA | Absent | Listed as PUBLIC with auth: Zitadel (same app as prod — to verify in SEC-008), no explicit rate limit |
| **F-019** | `*.getklai.com/kb-images/*` → garage:3902 | Absent | Listed as PUBLIC, anonymous reads by design (browser image access) |
| **F-020** | `*.getklai.com/bots/*` → vexa-bot-manager:8000 | Absent | Listed as PUBLIC, auth UNKNOWN (pending SEC-008 audit), rate-limit 10/min/IP |
| **F-021** | `logs-ingest.getklai.com` → victorialogs:9428 | Absent | Listed as PUBLIC, token-gated (single-secret) |
| **F-022** | `*.getklai.com/docs/*` → docs-app:3010 | Absent | Listed as PUBLIC, auth UNKNOWN (pending SEC-008 audit), rate-limit 60/min/IP |

Audit source of truth for the table content: `.moai/audit/04-3-prework-caddy.md` § "Caddy verify — FINDINGS" (table between lines 66 and 86).

---

## Requirements

### REQ-1: Complete and Accurate Caddy State Section

The system SHALL present the full public route inventory in a single table in `klai-infra/SERVERS.md`.

**REQ-1.1:** The Caddy state section in SERVERS.md SHALL list every route present in `/opt/klai/caddy/Caddyfile` as of 2026-04-19, sourced from `.moai/audit/04-3-prework-caddy.md` § "Caddy verify — FINDINGS".

**REQ-1.2:** Each row in the route table SHALL include the following columns: route (hostname + path pattern), target service (container:port), auth layer (human-readable description of the auth mechanism), rate limit, and comment (finding reference or note).

**REQ-1.3:** The route table SHALL include a column or explicit marker distinguishing "public" (reachable from internet via Caddy) from "internal" (Docker-network only). Routes that appear in the Caddyfile are "public" by definition; internal-only services are listed in a separate subsection immediately below.

**REQ-1.4:** The following routes from F-017 through F-022 SHALL be present in the table with the auth and rate-limit values from the audit snapshot: `connector.getklai.com`, `dev.getklai.com` (and its SPA fallback), `*.getklai.com/kb-images/*`, `*.getklai.com/bots/*`, `logs-ingest.getklai.com`, `*.getklai.com/docs/*`.

**REQ-1.5:** The existing routes already present in SERVERS.md (auth, chat, chat-{slug}, errors, grafana, llm health, scribe, research, api, partner, signup/billing, catch-all SPA) SHALL remain with the same auth and rate-limit text, updated only where the audit shows a discrepancy.

**REQ-1.6:** The "UNKNOWN" auth markers for F-020 (`/bots/*`) and F-022 (`/docs/*`) SHALL be preserved verbatim in the table until SEC-008 resolves them, with a comment column entry referencing "see SEC-008".

### REQ-2: Internal-vs-Public Boundary Documentation

The system SHALL make the Docker-internal-only boundary explicit and auditable from SERVERS.md alone.

**REQ-2.1:** SERVERS.md SHALL contain a subsection (immediately below the public route table) titled "Docker-internal only (NOT exposed via Caddy)" listing at minimum: retrieval-api, knowledge-ingest, klai-mailer, klai-knowledge-mcp, PostgreSQL, Qdrant, FalkorDB, Ollama, LibreChat-tenants other than those explicitly exposed via `chat-{slug}.getklai.com`.

**REQ-2.2:** The subsection SHALL note that these services are reachable only from other containers on their respective Docker networks (with cross-reference to the existing "Docker networks" subsection in SERVERS.md).

**REQ-2.3:** A maintenance warning block SHALL appear at the top of the Caddy state section stating that this table must be updated in the same PR as any `/opt/klai/caddy/Caddyfile` change, and that mismatched public routes are a security risk (drift led to F-017 being unknown to reviewers until the 2026-04-19 audit).

**REQ-2.4:** The maintenance warning SHALL reference `.moai/audit/04-3-prework-caddy.md` as the verified 2026-04-19 snapshot that reviewers can diff against when validating the current table.

**REQ-2.5:** IF `.claude/rules/klai/infra/servers.md` or `.claude/rules/klai/platform/caddy.md` contains a Caddy route table, THEN that table SHALL be updated to match the new SERVERS.md table byte-for-byte (or removed in favor of a pointer to SERVERS.md) so rules context cannot drift from repo documentation.
