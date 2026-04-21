# Research — SPEC-SEC-009

This SPEC is documentation-only. No new research is required.

## Source of truth

The verified snapshot of `/opt/klai/caddy/Caddyfile` from 2026-04-19 lives in:

- **`.moai/audit/04-3-prework-caddy.md`** § "Caddy verify — FINDINGS" — the route table between lines 66 and 86, and the "NIET publiek" line 86 listing Docker-internal services.

That table is the authoritative input for the new SERVERS.md Caddy state section. Copy it verbatim (column schema and all 15 rows), then add the internal/public column and comment annotations per spec.md REQ-1.2 and REQ-1.3.

## Context already gathered by the audit

Audit Fase 3 (Tenant isolation) already did the legwork this SPEC needs:

- `.moai/audit/04-tenant-isolation.md` — enumerates F-001 through F-016 (tenant-isolation findings)
- `.moai/audit/04-3-prework-caddy.md` — enumerates F-017 through F-022 (Caddy-exposure findings, some overlap with F-009 upgrade)
- `.moai/audit/99-fix-roadmap.md` § SEC-009 — confirms this work is P3 trivial-fix with scope "doc update" and names the six findings in scope: F-017, F-018, F-019, F-020, F-021, F-022

No new SSH access, no live Caddyfile re-read, no service inspection is needed. If the Caddyfile has changed since 2026-04-19, the implementer verifies the delta and updates spec.md accordingly before merging — but that is a maintenance step, not research.

## Related SPECs (for context, not dependency)

- **SPEC-SEC-008** (P1) owns the actual hardening of F-017 (connector public-exposure), F-018 (dev-env allowlisting), F-020 (vexa-bot-manager auth audit), F-022 (docs-app auth audit). SEC-009 is strictly the docs side; it does not wait on SEC-008 and does not change behavior that SEC-008 will later change.
- **SPEC-API-001** uses the same SERVERS.md format for reference when documenting Caddy routes for the partner API. Consistency is preserved by keeping the column schema identical.

## What is NOT researched

- Live Caddyfile inspection (already done 2026-04-19 by audit)
- Actual auth behavior of connector, bots, docs (deferred to SEC-008)
- Whether dev-env uses a separate Zitadel app (open item in SEC-008 parking lot)
- Docker network membership of individual services (already documented in SERVERS.md § "Docker networks")

Implementation can start immediately from the audit table. No blockers.
