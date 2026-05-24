# SPEC-SEC-CROSS-TENANT-FOLLOWUP-001 — Research Notes

Status: draft
Created: 2026-05-24
Purpose: Codebase-context summary that informs the SPEC. This file references existing artifacts rather than duplicating their content.

## 1. Primary input documents

Reference these documents in the order listed; do not duplicate their content here.

| Document | What it provides |
|---|---|
| `reports/audit-cross-tenant-2026-05-24/report.md` | The 44-finding security audit synthesised from three parallel `klai-security-audit` agents. Source of truth for every Finding-ID (B-1, A-2, C-1, etc.) cited in spec.md. |
| `.moai/specs/SPEC-SEC-CROSS-TENANT-2026-05/plan.md` | The remediation SPEC that landed via PR #672. Reference template for style; closes findings A-1, A-8, B-3 (partial), C-2, C-5. |
| `reports/audit-tenant-isolation-2026-05-05/standards.md` | Klai tenant-isolation standards (Cat-A vs Cat-D RLS, `cross_org_session`, `tenant_scoped_session`, WITH CHECK discipline). REQ-3 cites § 1 directly. |
| `.claude/rules/klai/pitfalls/process-rules.md` | Pitfall catalog. Directly relevant: `rls-policy-shape-must-match-lifespan-assert`, `rls-with-check-blocks-migration-update`, `alembic-cannot-drop-non-portal_api-tables`, `bind-mount-without-sync-workflow`, `validator-env-parity`, `multi-layer-gate-audit-all-sides`, `claim-emission-vs-claim-consumption`, `alembic-multi-pr-head-split`. |
| `.claude/rules/klai/projects/portal-permissions.md` | Five-Layer Model documentation. Establishes the `require_platform_unlocked(feature)` / `assert_platform_unlocked(org, feature)` pattern used by REQ-1 + REQ-13. |
| `.claude/rules/klai/projects/portal-backend.md` | `tenant_scoped_session`, `cross_org_session`, pool-GUC pollution rules, and the canonical **deprovisioning state-machine** (16-step idempotent orchestrator) that REQ-4 mirrors. |
| `.moai/specs/SPEC-PLATFORM-ADMIN-001/spec.md` | Stylistic precedent for cross-tenant-affecting Klai SPECs. Naming, section headers, [HARD] markers. |

## 2. Topology — which Klai services this SPEC touches

This SPEC is **single-repo dominant** with two cross-repo dependencies:

### Within `klai-portal`

- **Backend (`klai-portal/backend/`):** 18 of 19 REQs.
  - Modified: `app/api/partner.py`, `app/api/partner_dependencies.py`, `app/api/admin_widgets.py`, `app/api/admin/platform_manage.py`, `app/api/admin/users.py`, `app/core/permissions.py`, `app/services/widget_auth.py`, `app/services/widget_audit.py`, `app/services/zitadel.py`, `app/services/provisioning/infrastructure.py`, `app/main.py`.
  - New: `app/services/user_deletion_orchestrator.py`, `app/services/user_deletion_steps.py`, `app/services/widget_messages_retention.py`, `app/services/provisioning/_slug_guard.py`.
  - New alembic migrations: 5-7 (REQ-2, REQ-3, REQ-4 schema, REQ-8 CHECK, REQ-15 column, REQ-16 soft-delete, REQ-18 CHECK).

- **Frontend (`klai-portal/frontend/`):** 2 of 19 REQs.
  - Modified: `src/routes/admin/widgets/_components/tabs/ActivityTab.tsx` (REQ-9), `src/routes/admin/widgets/_components/tabs/EmbedTab.tsx` + `new.tsx` (REQ-2 UI toggle + default origin).

### Cross-repo dependencies (out of this repo)

- **`klai-infra`:** REQ-17 (Caddy `frame-ancestors 'none'` for `/bot/*`) AND REQ-19 (crawl4ai DNS or sidecar egress proxy).
- **`klai-mailer`:** REQ-2 customer-communication email template.

### Services NOT touched by this SPEC

- klai-knowledge-mcp, klai-knowledge-ingest, klai-retrieval-api, klai-connector, klai-scribe, klai-focus / research-api, klai-mailer (beyond template add).

Per the audit report § 7 "Audit completeness", these services were read in scope but their findings are deferred to other SPECs.

## 3. Klai pattern references used

| Pattern | Where it lives | Used by REQ |
|---|---|---|
| `assert_platform_unlocked(org, feature)` imperative | `klai-portal/backend/app/core/permissions.py:425` | REQ-1 |
| `require_platform_unlocked(feature)` FastAPI dep | `klai-portal/backend/app/core/permissions.py:402` | REQ-13 |
| `tenant_scoped_session(org_id)` | `klai-portal/backend/app/core/database.py` | REQ-10, REQ-14 |
| `cross_org_session()` (admin-only, RLS bypass) | `klai-portal/backend/app/core/database.py` | REQ-3 testing, REQ-14 widget→org lookup |
| Cat-D RLS template (USING + explicit WITH CHECK) | `reports/audit-tenant-isolation-2026-05-05/standards.md` § 1 | REQ-3 |
| `check_rate_limit(redis_pool, key_id, limit, window)` | `klai-portal/backend/app/services/partner_rate_limit.py:21` | REQ-7 |
| 16-step idempotent orchestrator with retries | `klai-portal/backend/app/services/provisioning/deprovisioning_orchestrator.py` | REQ-4 |
| Lifespan-registered background worker | `klai-portal/backend/app/main.py` + `bot_poller.py` | REQ-8 retention worker |
| Chunked DELETE for retention | `klai-portal/backend/app/services/recording_cleanup.py` | REQ-8 |
| `@MX:ANCHOR` for high-fan_in functions | per `.claude/rules/moai/workflow/mx-tag-protocol.md` | spec.md § 12 mx_plan |
| Post-deploy SQL pattern (klai-owned tables) | `klai-portal/backend/alembic/versions/post_deploy_a4f72e913c8b_widget_conversations_rls.sql` | REQ-3 |
| Cross-org-by-design markers | per standards.md § 4 | REQ-14 (widget→org lookup needs `cross_org_session` rationale) |
| Zitadel `grant_user_role` / `lock_user` | `klai-portal/backend/app/services/zitadel.py` | REQ-5, REQ-12 |

## 4. Post-PR-#672 verification notes

The audit report (dated 2026-05-24) was synthesised from agents that read `origin/main` BEFORE PR #672 merged. Some findings were closed by #672 (commit `13e07bea`); the audit notes which.

I verified the **still-open** state of each cited file:line against `origin/main` at commit `f8ee7826` (current head):

| Finding | Original line | Verified line on current main | Status |
|---|---|---|---|
| B-1 (no `assert_platform_unlocked` on partner.py endpoints) | partner.py:750-948 | Still applies; line 911 has `public_share_enabled` check (#672) but NO platform-unlock gate | OPEN — REQ-1 |
| B-2 (`if not allowed_origins: return True`) | widget_auth.py:170-171 | Still present at widget_auth.py:171 | OPEN — REQ-2 |
| B-3 (`/public-bot-config` no-auth token-mint) | partner.py:885 | Partially closed by #672 via `public_share_enabled` flag (line 911), but rate-limit (REQ-7) + Origin gate still missing | PARTIAL — REQ-7 covers rate-limit; full Origin gate deferred to REQ-2 (origin enforcement after default-deny) |
| C-1 (portal_templates RLS no WITH CHECK) | 34d8f876ffbf migration | Migration is already deployed; new delta migration required per REQ-3 | OPEN — REQ-3 |
| A-2 (external state destroyed pre-commit in hard-delete) | platform_manage.py:267-343 | Lines shifted to 267-376; #672 added self-protection (A-1) BUT NOT order-of-operations fix | OPEN — REQ-4 |
| A-4 (Zitadel role-grant sync) | platform_manage.py:120-172 | Still applies; PR #672 mentions in CT-02 but addresses MCP-notifier, NOT Zitadel-grant sync | OPEN — REQ-5 |
| A-6 (suspend is informational only) | platform_manage.py:180-216 | Still applies; PR #672 does not touch suspend semantics | OPEN — REQ-12 |
| B-6 (admin activity endpoints missing platform-unlock) | admin_widgets.py:491-668 | Lines shifted to 498/558/618; still no `require_platform_unlocked` on activity endpoints | OPEN — REQ-13 |
| B-7 (record_widget_turn accepts caller org_id) | widget_audit.py:63-160 | Still present at lines 63-167; signature unchanged | OPEN — REQ-14 |
| B-14 (widget DELETE CASCADE wipes audit) | admin_widgets.py:409-434 | Still applies; CASCADE is in post_deploy SQL | OPEN — REQ-16 |

No false positives in the audit. The line-shifts above are minor (within ±10 lines of cited values).

## 5. Risk profile of REQs

| Risk class | REQs | Justification |
|---|---|---|
| **Breaking change with negligible external impact** | REQ-2 | Default-deny on `allowed_origins=[]` flips behavior for every widget without explicit origins. Production cohort check on 2026-05-24 returned 2 widgets, both inside Klai's own `getklai` tenant — 0 external customers impacted. 7-day communication window dropped in favor of automated CI cohort gate (`scripts/verify_widget_cohort.sh` invoked from `portal-api.yml`); the deploy is blocked if `impacted_widgets > 5` OR any external tenant slug appears. Migration's automated branches pick the safe default per row. |
| **Schema change with klai-owned table (post-deploy SQL)** | REQ-3 | `portal_templates` owned by `klai` superuser; can't `op.execute` from alembic; pattern enforced by `alembic-cannot-drop-non-portal_api-tables` pitfall. |
| **Schema change with portal_api-owned table (alembic upgrade)** | REQ-2, REQ-8, REQ-15, REQ-16, REQ-18 | All additive DDL in `upgrade()`; safe per `rls-with-check-blocks-migration-update` (no row-writes in upgrade). |
| **Large code refactor** | REQ-4 | State-machine refactor; mirror existing `deprovisioning_orchestrator`. Hot-cut (no feature flag) — pattern is proven, user-delete frequency is low (<5/week at Klai scale), git revert is the rollback plan. 48h post-deploy monitoring via Grafana product_events. |
| **Hot-path performance** | REQ-12 (status check on every authenticated request) | Single Python comparison on already-loaded row; negligible. |
| **Cross-repo dependency** | REQ-17 (klai-infra Caddy), REQ-19 (klai-infra crawl4ai DNS) | Separate PRs; track in this SPEC's deployment checklist. |
| **Surgical** | REQ-1, REQ-5, REQ-6, REQ-7, REQ-9, REQ-10, REQ-11, REQ-13, REQ-14 | Small, additive code changes; low risk. |

## 6. Methodology and quality conventions

This SPEC follows:

- **TDD per `.moai/config/sections/quality.yaml`:** `development_mode: tdd`. RED-GREEN-REFACTOR per REQ.
- **Documentation language: English** per `.moai/config/sections/language.yaml`: `documentation: en`.
- **Conversation language: NL** per the same config (orchestrator chat with user is in Dutch; SPEC artifacts are English).
- **Code comments: English** per same config (`code_comments: en`).
- **No emojis** in SPEC content per moai-constitution.
- **No AskUserQuestion** per Klai project rule `.claude/rules/klai/no-ask-user-question.md` — all user interaction is plain markdown chat.
- **Conventional commits** with Finding-ID cross-link (e.g. `feat(widgets): platform-unlock on public endpoints (Finding B-1)`).
- **Trust 5 quality framework** per `.claude/rules/moai/core/moai-constitution.md`.

## 7. Out-of-scope items + rationale

Repeating spec.md § 3 for self-containment of this research artifact:

| Finding | Why out of scope |
|---|---|
| B-10 (LLM prompt-injection mitigation + admin KB-picker UX warning) | Cross-cuts LLM-policy, retrieval, and admin-UX boundaries; deserves dedicated security/UX SPEC. |
| B-13 (asymmetric widget JWT signing ES256/EdDSA + master-key rotation) | Intrusive cryptographic rework; own infra SPEC. |
| B-15 (`widget_id` rotation UX with grace window) | Best bundled with B-13. |
| B-18 (`system_prompt` admin-side injection-pattern validation) | Review-policy issue, not code. |
| B-8, B-12, B-16, B-17, B-20 (LOW widget items) | Defense-in-depth; future LOW-bundle SPEC. |
| A-9, A-10, A-11..A-15 (LOW platform-admin items) | Defense-in-depth; future LOW-bundle SPEC. |
| C-6 (poller error-branching) | Operationally low-impact; future. |
| C-7 (per-tenant HKDF `KNOWLEDGE_INGEST_SECRET`) | Cryptographic rework; future infra SPEC. |
| C-8 (`sso_cookie_key` validator) | Covered by existing `validator-env-parity` pattern; small follow-up. |
| C-9 (Zitadel 403-vs-404 logging) | Operational signal-clarity; future. |

## 8. Resolved decisions

All 5 originally-surfaced open questions resolved by the orchestrator on 2026-05-24. Full decision rationale in `plan.md § 10`. Summary:

1. **REQ-2 communication-window — DROPPED.** Production cohort = 2 widgets, both in `getklai` tenant. Automated CI cohort gate (`scripts/verify_widget_cohort.sh` invoked from `.github/workflows/portal-api.yml`) replaces the 7-day window; no human pre-merge inspection.
2. **REQ-4 — HOT-CUT.** No feature flag. Proven pattern + low frequency + git revert as rollback.
3. **REQ-17 / REQ-19 — DECOUPLED.** Cross-repo tracked as `cross_repo_dependency`; SPEC does not block on klai-infra merge.
4. **REQ-12 — INLINE.** `user.status` check in `_resolve_caller_with_options` (no Redis cache); the row is already SELECTed per request.
5. **REQ-8 — 90d + env-var.** No unified Klai retention policy; `WIDGET_MESSAGES_RETENTION_DAYS` env-var for tuning.

## 8.1 Cohort verification (REQ-2)

Direct production PostgreSQL query on 2026-05-24 via `ssh core-01 docker exec klai-core-postgres-1 psql`:

| Metric | Count |
|---|---|
| Total widgets | 5 |
| Widgets with empty `allowed_origins` | 2 |
| Widgets impacted by REQ-2 (empty origins + `public_share_enabled=false`) | 2 |
| Distinct tenants with widgets | 2 (`getklai`, `nerds-37376105`) |
| Distinct tenants impacted | 1 (`getklai`) |

Per-tenant breakdown: `getklai` has 4 widgets (2 impacted); `nerds-37376105` has 1 widget (0 impacted). All impacted widgets are internal to Klai — no external customers affected by the REQ-2 breaking change. The originally-scoped 7-day customer-communication window is replaced by an automated CI cohort gate (`scripts/verify_widget_cohort.sh` + a step in `.github/workflows/portal-api.yml`). The gate re-runs the cohort SQL on every deploy and blocks the migration if `impacted_widgets > 5` OR any external tenant slug appears.

## 9. References

External (no fetch required; cited for reader):
- EARS format paper: https://alistairmavin.com/ears/
- PostgreSQL RLS docs: https://www.postgresql.org/docs/current/ddl-rowsecurity.html
- OWASP A01:2021 Broken Access Control: https://owasp.org/Top10/A01_2021-Broken_Access_Control/

Internal (read-back if context lost):
- `reports/audit-cross-tenant-2026-05-24/report.md`
- `.moai/specs/SPEC-SEC-CROSS-TENANT-2026-05/plan.md`
- `reports/audit-tenant-isolation-2026-05-05/standards.md`
- `.claude/rules/klai/pitfalls/process-rules.md`
- `.claude/rules/klai/projects/portal-permissions.md`
- `.claude/rules/klai/projects/portal-backend.md`

End of research.md.
