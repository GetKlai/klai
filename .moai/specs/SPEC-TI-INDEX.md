# SPEC-TI Index — Tenant Isolation Fix Cyclus 2026-05-05/06

Index van alle SPECs die uit de audit-tenant-isolation-2026-05-05 zijn ontstaan.

## SPECs

| ID | Title | Findings | Priority | Worktree | PR |
|---|---|---|---|---|---|
| SPEC-TI-001 | retry_provisioning platform-admin gate | C-2 | CRIT | `klai-retry-prov-gate` | [#373](https://github.com/GetKlai/klai/pull/373) |
| SPEC-TI-002 | RLS rollout connector schema | A-7 | HIGH | `klai-connector-rls` | TBD |
| SPEC-TI-003 | RLS knowledge schema + identity-assertion | A-8, A-13 | HIGH | `klai-knowledge-rls` | TBD |
| SPEC-TI-004 | RLS research schema + multi-org auth | A-10, A-11, A-12 | HIGH | `klai-research-rls` | TBD |
| SPEC-TI-005 | portal-api RLS hygiëne batch | A-1..A-6 | HIGH | `klai-portal-rls-hygiene` | TBD |
| SPEC-TI-006 | Webhook replay-protection adoption | C-9, C-10 | HIGH | `klai-webhook-replay` | TBD |
| SPEC-TI-007 | Gitea webhook fail-closed + tenant-spoof | C-1 | HIGH | `klai-gitea-harden` | TBD |
| SPEC-TI-008 | retrieval-api router fix | B-1 | HIGH | `klai-router-fix` | TBD |
| SPEC-TI-009 | Garage KB-image auth-proxy | B-4 | MED | `klai-garage-proxy` | TBD |
| SPEC-TI-010 | Cleanup batch (15 findings, 3 sub-pakketten) | B-2, B-5..B-10, A-9, C-3..C-8, C-11 | MED+LOW | 3 worktrees | TBD |

## Documentatie

- Audit-rapport: `reports/audit-tenant-isolation-2026-05-05/report.md`
- Coverage matrix: `reports/audit-tenant-isolation-2026-05-05/coverage-matrix.md`
- Volgorde + prioriteit: `reports/audit-tenant-isolation-2026-05-05/next-steps.md`
- **Standards (mandatory reading voor agents):** `reports/audit-tenant-isolation-2026-05-05/standards.md`

## Volgorde

**Fase 1** (sequential): SPEC-TI-001 (in flight, PR #373) — wacht op CI green + merge.
**Fase 2** (parallel, 4 worktrees): SPEC-TI-002, -003, -004, -005.
**Fase 3** (parallel, 4 worktrees): SPEC-TI-006, -007, -008, -009.
**Fase 4** (parallel, 3 worktrees): SPEC-TI-010 sub-A, sub-B, sub-C.
**Fase 5**: RESULTS.md eind-rapport.
