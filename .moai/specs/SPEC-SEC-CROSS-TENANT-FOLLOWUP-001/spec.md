---
id: SPEC-SEC-CROSS-TENANT-FOLLOWUP-001
version: 0.1.0
status: draft
created: 2026-05-24
updated: 2026-05-24
author: platform/backend
priority: P0
issue_number: 0
---

# SPEC-SEC-CROSS-TENANT-FOLLOWUP-001 — Remaining cross-tenant security hardening after PR #672

## HISTORY

| Version | Date | Author | Changes |
|---|---|---|---|
| 0.1.0 | 2026-05-24 | platform/backend | Initial draft from cross-tenant audit + PR #672 follow-up. Verification against `origin/main` at commit `f8ee7826` confirms: B-1 (no `assert_platform_unlocked` on partner endpoints) still open; B-2 (`if not allowed_origins: return True` at `widget_auth.py:171`) still open; B-3 partially addressed by `public_share_enabled` check (`partner.py:911`), but rate-limit + Origin gate still missing; B-13/C-1 confirmed open. PR #672 introduced `_platform: UserPermissions = Depends(require_platform_unlocked("widgets"))` on admin CRUD routes at lines 196/262/294/333/374/420 of `admin_widgets.py` but NOT on activity-tab routes at lines 498/558/618 — REQ-13 still applies. |
| 0.2.0 | 2026-05-24 | orchestrator | Resolved all 5 open questions after production-data check + Klai-pattern review. (1) REQ-2 customer-communication window dropped — production cohort query returned 2 widgets, both inside the `getklai` tenant itself (0 external customers impacted); replaced by automated CI cohort gate (deploy pipeline runs the cohort SQL via SSH and aborts if impacted_widgets > 5 OR any external tenant slug appears). (2) REQ-4 hot-cut chosen over feature-flag — `deprovisioning_orchestrator` pattern is proven, user-delete frequency is low (<5/week at Klai scale), git revert is the rollback plan; feature-flag adds parallel-path complexity without real benefit. (3) REQ-17/REQ-19 cross-repo dependency decoupled — SPEC closes when all klai-portal code REQs land; klai-infra PRs tracked as `cross_repo_dependency` with explicit PR-link cross-reference. (4) REQ-12 inline `user.status` check chosen — `_resolve_caller_with_options` already SELECTs the row; Redis cache adds invalidation edge-cases for a handful of suspended users. (5) REQ-8 retention 90d default with `WIDGET_MESSAGES_RETENTION_DAYS` env-var — no unified Klai retention policy in code; 90d defensible per GDPR legitimate interest. |
| 0.2.1 | 2026-05-24 | orchestrator | Removed all "operator manual inspect" language per project principle (Klai works fully autonomously; no manual gates between deploy steps). REQ-2 cohort verification is enforced by an automated GitHub Actions step in `.github/workflows/portal-api.yml` that SSHes to core-01, runs `scripts/verify_widget_cohort.sh`, and aborts the deploy if `impacted_widgets > 5 OR impacted_tenants includes any slug outside the platform_org list`. If the deploy aborts, REQ-2 is split into a follow-up SPEC with the standard 7-day comm protocol. No human pre-merge inspection required — the migration's automated branches handle the safe defaults per row. |

## 1. Problem

On 2026-05-24 a three-agent adversarial security audit identified 44 cross-tenant findings across the platform-admin console (Slice A), widgets feature (Slice B), and auth/provisioning/connector slice (Slice C). The audit report lives at `reports/audit-cross-tenant-2026-05-24/report.md`.

PR #672 (`13e07bea harden cross-tenant security paths`, merged 2026-05-24) closed approximately seven of the forty-four findings: A-1 (self-protection on hard-delete), A-8 (orphan-rollback for create-tenant owner), B-3 (`public_share_enabled` flag now gates `/public-bot-config` access), C-2 (rollback of Zitadel user on invite-mail failure), C-5 (OAuth state binds `org_id`). It partially addressed A-4 (Zitadel role-grant sync still missing), A-5 (audit-row for partial failure missing), and A-6 (suspend still informational; no Zitadel lock).

This SPEC closes the remaining items: two of three CRITs (B-1 platform-unlock gate not enforced on public widget endpoints; B-2 empty `allowed_origins` defaults to open-to-the-world), one HIGH (C-1 `portal_templates` RLS migration missing explicit WITH CHECK clause), plus a tail of MED/HIGH items grouped into five modules. Production cohort check (2026-05-24) confirms REQ-2's "breaking change" affects only 2 widgets, both inside Klai's own `getklai` tenant — 0 external customers impacted. The originally-planned 7-day customer-communication window for REQ-2 is replaced by an automated CI cohort gate (GitHub Actions step SSHes to core-01, runs the cohort SQL, aborts the deploy if `impacted_widgets > 5` OR any external tenant slug appears). The data-migration itself picks the safe default per row automatically; no human pre-merge inspection. All 5 originally-open questions are RESOLVED — see `plan.md § 10`.

## 2. Goal

Close the structural and exploitable cross-tenant security gaps that survive PR #672, in priority order: P0 widget hardening (B-1, B-2, REQ-3 RLS) first, then P1 platform-admin destructive-operation refactoring (REQ-4 state machine), then P2 audit-trail integrity and defense-in-depth (REQ-9..REQ-19). All requirements derive from a numbered Finding-ID in the audit report for full traceability.

## 3. Non-goals (Exclusions — What NOT to Build)

The following findings are out of scope for this SPEC and either belong to follow-up SPECs or are accepted-risk LOW items:

- **B-10** (LLM prompt-injection mitigation + admin KB-picker UX warning): deserves its own security/UX SPEC because it crosses LLM-policy, retrieval, and admin-UX boundaries.
- **B-13** (asymmetric widget JWT signing ES256/EdDSA + master-key rotation): own infra SPEC; intrusive cryptographic rework.
- **B-15** (`widget_id` rotation UX with grace window): can be bundled with B-13.
- **B-18** (`system_prompt` admin-side injection-pattern validation): review-policy issue, not code.
- LOW items: B-8 (KB error-message enumeration), B-12 (conversation-messages widget_id subquery), B-16 (`_widget_cors_headers` re-validation), B-17 / B-20 (widget-preview.html hardcoded URL + CSP), A-9..A-15 (audit-ordering, identity-lookup-failed flag, slug uniqueness), C-6 (poller error-branching), C-7 (per-tenant HKDF `KNOWLEDGE_INGEST_SECRET`), C-8 (`sso_cookie_key` validator — covered by existing `validator-env-parity` pattern but not this SPEC), C-9 (Zitadel 403-vs-404 logging). Pick up in next refactor pass or a LOW-bundle SPEC.
- **Implementation code changes**: this SPEC defines requirements only. Code changes happen in the Run phase against `manager-tdd`.

## 4. Findings Covered

| REQ | Finding | Severity | Module | Primary files |
|---|---|---|---|---|
| REQ-1 | B-1 | P0 CRIT | M1 Widgets | `klai-portal/backend/app/api/partner.py`, `klai-portal/backend/app/api/partner_dependencies.py` |
| REQ-2 | B-2 | P0 CRIT | M1 Widgets | `klai-portal/backend/app/services/widget_auth.py`, `klai-portal/backend/app/models/widgets.py`, alembic migration + post-deploy SQL, frontend Embed UI (cohort = 2 widgets internal — no mailer) |
| REQ-3 | C-1 | P0 HIGH | M5 Infra | `klai-portal/backend/alembic/versions/` (new migration), `klai-portal/backend/tests/` |
| REQ-4 | A-2 | P1 HIGH | M2 PlatformAdmin | `klai-portal/backend/app/api/admin/platform_manage.py`, new `app/services/user_deletion_orchestrator.py` |
| REQ-5 | A-4 | P1 HIGH | M2 PlatformAdmin | `klai-portal/backend/app/api/admin/platform_manage.py`, `klai-portal/backend/app/api/admin/users.py`, `klai-portal/backend/app/services/zitadel.py` |
| REQ-6 | A-7 | P1 MED | M2 PlatformAdmin | `klai-portal/backend/app/api/admin/platform_manage.py` |
| REQ-7 | B-4 | P1 HIGH | M1 Widgets | `klai-portal/backend/app/api/partner.py` |
| REQ-8 | B-5 | P1 HIGH | M1 Widgets | `klai-portal/backend/app/services/widget_audit.py`, alembic migration, new `klai-portal/backend/app/services/widget_messages_retention.py` |
| REQ-9 | B-9 | P1 MED | M4 AuditTrail | `klai-portal/frontend/src/routes/admin/widgets/_components/tabs/ActivityTab.tsx` |
| REQ-10 | A-3 | P2 MED | M2 PlatformAdmin | `klai-portal/backend/app/api/admin/platform_manage.py` |
| REQ-11 | A-5 | P2 MED | M2 PlatformAdmin | depends on REQ-4 |
| REQ-12 | A-6 | P2 MED | M3 AdminAuthZ | `klai-portal/backend/app/core/permissions.py`, `klai-portal/backend/app/api/admin/platform_manage.py`, `klai-portal/backend/app/services/zitadel.py` |
| REQ-13 | B-6 | P2 MED | M3 AdminAuthZ | `klai-portal/backend/app/api/admin_widgets.py` |
| REQ-14 | B-7 | P2 HIGH | M4 AuditTrail | `klai-portal/backend/app/services/widget_audit.py`, `klai-portal/backend/app/api/partner.py` |
| REQ-15 | B-11 | P2 MED | M4 AuditTrail | `klai-portal/backend/app/api/admin_widgets.py`, `klai-portal/backend/app/services/widget_audit.py`, alembic migration |
| REQ-16 | B-14 | P2 MED | M4 AuditTrail | `klai-portal/backend/app/api/admin_widgets.py`, alembic migration |
| REQ-17 | B-19 | P2 MED | M5 Infra | klai-infra Caddy config (cross-repo) |
| REQ-18 | C-3 | P2 MED | M5 Infra | `klai-portal/backend/app/services/provisioning/infrastructure.py`, new `_slug_guard.py`, alembic CHECK constraint |
| REQ-19 | C-4 | P2 MED | M5 Infra | `deploy/docker-compose.yml` or klai-infra DNS config |

---

## 5. Module 1 — Public Widget Hardening

Four requirements (P0 + P1) addressing the critical widget-feature exposures. CC-1 (universal phishing-site bot hijack) chain is broken by REQ-2 alone; CC-2 (admin-account takeover via stored XSS) chain is broken by REQ-9 alone; REQ-1 + REQ-7 + REQ-8 add defense-in-depth.

### REQ-1 — Public widget endpoints SHALL enforce platform-unlock gate

**Finding:** B-1 (CRIT). Klai-staff who disable `widgets` in `enabled_addons` for an abusive tenant only fence the admin UI; deployed widgets keep draining LLM-tokens and KB-context.

**EARS:**

WHEN a caller hits `/partner/v1/widget-config?id=<widget_id>`, THE SYSTEM SHALL after resolving `widget.org_id` call `await assert_platform_unlocked(org, "widgets")` before generating any session token.

WHEN a caller hits `/partner/v1/public-bot-config?id=<widget_id>`, THE SYSTEM SHALL after resolving `widget.org_id` call `await assert_platform_unlocked(org, "widgets")` before generating any session token.

WHEN `_auth_via_session_token` in `partner_dependencies.py` resolves a widget-JWT branch, THE SYSTEM SHALL call `await assert_platform_unlocked(org, "widgets")` after the widget row is loaded and before the chat-handler runs.

IF `assert_platform_unlocked` raises 403, THEN THE SYSTEM SHALL surface the failure as HTTP 404 on `/widget-config` and `/public-bot-config` to preserve existence-non-disclosure, and as 403 on the chat-path (where the JWT already identifies the widget).

**Files:** `klai-portal/backend/app/api/partner.py` lines 750-948; `klai-portal/backend/app/api/partner_dependencies.py` `_auth_via_session_token`.

**Pattern reference:** mirror `_platform: UserPermissions = Depends(require_platform_unlocked("widgets"))` at `klai-portal/backend/app/api/admin_widgets.py:196`. Because partner endpoints do not go through `get_caller` (they resolve their own org via the widget row), use the imperative `assert_platform_unlocked(org, "widgets")` helper at `klai-portal/backend/app/core/permissions.py:425` after the widget row is loaded.

### REQ-2 — Empty `allowed_origins` SHALL default-deny; explicit "allow any origin" toggle SHALL be required

**Finding:** B-2 (CRIT, BREAKING). `origin_allowed(origin, [])` returns `True` (`widget_auth.py:171`), so every newly created widget is embeddable anywhere on the internet.

**EARS:**

WHEN `origin_allowed(origin, allowed_origins)` is called with `allowed_origins` empty AND `widget.allow_any_origin` is False, THE SYSTEM SHALL return False (deny).

WHEN `origin_allowed(origin, allowed_origins)` is called with `allowed_origins` empty AND `widget.allow_any_origin` is True, THE SYSTEM SHALL return True (allow).

WHEN an admin POSTs to `/api/widgets` without specifying `allowed_origins`, THE SYSTEM SHALL persist `allowed_origins=["https://<tenant_subdomain>.getklai.com"]` automatically.

WHEN an admin enables "Allow all origins" in the EmbedTab UI, THE SYSTEM SHALL set `allow_any_origin=true` on the widget row and display a warning message "Conversations from any website will be attributed to this widget — only enable for public chatbots."

WHEN the alembic migration runs, THE SYSTEM SHALL add column `widgets.allow_any_origin BOOLEAN NOT NULL DEFAULT false` AND column `widget_conversations.loaded_origin TEXT NULL` so post-deploy operators can see which origin a conversation came from.

WHEN the data-migration runs against existing widgets, THE SYSTEM SHALL apply the following rules per row:
- IF `widget_config->>'allowed_origins'` is non-empty THEN keep as-is.
- IF `widget_config->>'allowed_origins'` is empty/missing AND `public_share_enabled=true` THEN set `allow_any_origin=true` AND emit audit event `widget.allow_any_origin_migrated` with `reason=public_share_enabled`.
- IF `widget_config->>'allowed_origins'` is empty/missing AND `public_share_enabled=false` THEN set `allowed_origins=["https://<tenant_subdomain>.getklai.com"]` AND emit audit event `widget.allow_any_origin_migrated` with `reason=tenant_subdomain_default`.

WHEN `record_widget_turn` writes a conversation row, THE SYSTEM SHALL include the request's `Origin` header in `widget_conversations.loaded_origin` (truncated to 200 chars, NULL if header missing).

**Files:**
- `klai-portal/backend/app/services/widget_auth.py:170-180` (`origin_allowed` accepts new `allow_any_origin: bool` parameter or reads from the widget row passed in)
- `klai-portal/backend/app/models/widgets.py` (add `allow_any_origin` column; add `loaded_origin` column on `widget_conversations`)
- `klai-portal/backend/app/api/admin_widgets.py` (UI toggle endpoint + create-widget default origin)
- `klai-portal/backend/app/services/widget_audit.py` (`record_widget_turn` accepts + persists `loaded_origin`)
- `klai-portal/backend/app/api/partner.py` (chat-path passes `Origin` header to `record_widget_turn`)
- new alembic migration `<rev>_widget_allow_any_origin_and_loaded_origin.py` (DDL only — additive in `upgrade()`) plus `post_deploy_<rev>.sql` for the data migration
- `klai-portal/frontend/src/routes/admin/widgets/_components/tabs/EmbedTab.tsx` and `new.tsx` (UI toggle + warning copy)
- `scripts/verify_widget_cohort.sh` — invoked by the CI cohort gate (see below); runs the cohort SQL and exits non-zero if the deploy should abort
- `.github/workflows/portal-api.yml` — new step that SSHes to core-01 and runs the verification script; runs after CI tests pass and before the deploy job

**Automated CI cohort gate (replaces 7-day customer-communication window):** production cohort verified on 2026-05-24 at 2 widgets, both inside Klai's own `getklai` tenant (verified via direct PostgreSQL query against `klai-core-postgres-1`). The deploy pipeline (GitHub Actions step in `portal-api.yml`) SHALL re-run the cohort verification query before applying the post-deploy SQL. If `impacted_widgets > 5` OR `impacted_tenants` contains any slug outside the platform-org list (`settings.platform_org_slug`, default `getklai`), the deploy step SHALL exit non-zero and the post-deploy SQL SHALL NOT run; REQ-2 then requires a follow-up SPEC with the standard 7-day customer-communication protocol (mailer template + in-app banner re-introduced). The data-migration itself picks the safe default per row automatically (see the WHEN clauses above) — no human inspection between the cohort gate and the migration.

**Cohort verification query (driven by `scripts/verify_widget_cohort.sh`):**

```sql
SELECT
  COUNT(*) FILTER (
    WHERE jsonb_array_length(COALESCE(widget_config->'allowed_origins', '[]'::jsonb)) = 0
      AND public_share_enabled = false
  ) AS impacted_widgets,
  COUNT(DISTINCT org_id) FILTER (
    WHERE jsonb_array_length(COALESCE(widget_config->'allowed_origins', '[]'::jsonb)) = 0
      AND public_share_enabled = false
  ) AS impacted_tenants
FROM widgets;
```

### REQ-7 — Public widget endpoints SHALL enforce per-widget rate-limit

**Finding:** B-4 (HIGH). Mint endpoints today have only Caddy per-IP rate limiting (120 rpm), which is distributable across IPs; without per-widget caps an attacker can drain unbounded LLM tokens via the public chat path.

**EARS:**

WHEN `/partner/v1/widget-config` is called, THE SYSTEM SHALL call `check_rate_limit(redis_pool, f"widget_mint:{widget_id}", limit_per_minute=10, window_seconds=60)` BEFORE the DB lookup.

WHEN `/partner/v1/public-bot-config` is called, THE SYSTEM SHALL call the same rate-limit check before the DB lookup.

IF the rate-limit check returns `(allowed=False, retry_after_seconds=X)`, THEN THE SYSTEM SHALL return HTTP 429 with header `Retry-After: <X>`.

**Files:** `klai-portal/backend/app/api/partner.py:750-948`. Reuse `check_rate_limit` from `klai-portal/backend/app/services/partner_rate_limit.py` (signature `(redis_pool, key_id, limit_per_minute, window_seconds=60)` already matches).

### REQ-8 — Widget message content SHALL be length-capped and retention-bounded

**Finding:** B-5 (HIGH). No length limit on `widget_messages.content`, no retention. 10KB × 60 rpm × 30 days ≈ 26 GB per widget per month — disk-fill DoS for the shared Postgres cluster.

**EARS:**

WHEN `record_widget_turn` writes content, THE SYSTEM SHALL clamp content to 10000 chars (`content[:10000]`) before the INSERT.

WHEN the alembic migration runs, THE SYSTEM SHALL add `CHECK (LENGTH(content) <= 10000)` constraint to `widget_messages.content` (DDL in `upgrade()`, no row-write — RLS-safe per `rls-with-check-blocks-migration-update` pitfall).

WHEN the new background worker `widget_messages_retention.py` runs, THE SYSTEM SHALL every 24 hours execute `DELETE FROM widget_messages WHERE created_at < NOW() - INTERVAL ':retention_days days'` in chunks of 10000 rows, with `retention_days` configurable via `WIDGET_MESSAGES_RETENTION_DAYS` env var (default 90).

WHEN the retention worker completes a run, THE SYSTEM SHALL emit audit event `widget_messages.retention_deleted` with `deleted_count` and `chunk_count`.

**Files:**
- `klai-portal/backend/app/services/widget_audit.py` (clamp at INSERT)
- new alembic migration `<rev>_widget_messages_length_check.py`
- new `klai-portal/backend/app/services/widget_messages_retention.py`
- `klai-portal/backend/app/main.py` (lifespan: register the retention worker as a background task)
- `klai-portal/backend/app/core/config.py` (Settings field `widget_messages_retention_days: int = 90`)

---

## 6. Module 2 — Platform-Admin Destructive Operations

Five requirements (P1 + P2) refactoring the platform-admin hard-delete and role-change flows so they survive partial failures with auditable, idempotent state.

### REQ-4 — Platform user-delete SHALL be a state machine mirroring deprovisioning_orchestrator

**Finding:** A-2 (HIGH). `platform_delete_user` calls `docs_client.deprovision_kb` + `knowledge_ingest_client.delete_kb` (external HTTP calls with side-effects on Gitea/Qdrant/Garage) BEFORE the DB-session commit. If Zitadel.remove_user then 502s, portal-DB rolls back but external state is already destroyed.

**EARS:**

WHEN `platform_delete_user` is called, THE SYSTEM SHALL refactor the delete sequence into a state machine that mirrors the `deprovisioning_orchestrator` pattern at `klai-portal/backend/app/services/provisioning/deprovisioning_orchestrator.py`.

WHEN the state machine runs, THE SYSTEM SHALL execute steps in the following order:
1. Zitadel-remove (cheap to undo via re-invite if external-state deletes succeed)
2. External KB deletes (Qdrant / Garage / knowledge-ingest / docs-app per `kb_offboarding._do_delete`)
3. portal_users DELETE in the same DB transaction as the audit-row write

WHEN any step fails, THE SYSTEM SHALL write `portal_users.deletion_status = 'failed_partial'` AND `failure_reason JSONB` AND `last_attempted_step TEXT` to a new state-tracking table (or extend `portal_users` if simpler).

WHEN a partial failure occurs, THE SYSTEM SHALL emit audit event `platform_admin.user_delete_partial_failure` with `step` (one of: zitadel_remove | external_kb_delete | portal_db_delete), `kbs_deleted_externally`, `api_keys_revoked`, `mcp_tokens_revoked`, `zitadel_identity_deleted`, `db_user_deleted`.

WHEN an operator hits the new endpoint `POST /api/admin/platform/users/{zitadel_user_id}/retry-delete`, THE SYSTEM SHALL restart the state machine from scratch (each step is idempotent — already-deleted resources are skipped harmlessly).

**Files:**
- refactor of `klai-portal/backend/app/api/admin/platform_manage.py:267-376` (`platform_delete_user`)
- new `klai-portal/backend/app/services/user_deletion_orchestrator.py` (mirror of `deprovisioning_orchestrator.py` structure)
- new step module `klai-portal/backend/app/services/user_deletion_steps.py`
- new alembic migration adding `portal_users.deletion_status`, `portal_users.failure_reason`, `portal_users.last_attempted_step` (or new `portal_user_deletion_state` table)
- new endpoint `POST /api/admin/platform/users/{zitadel_user_id}/retry-delete` in `platform_manage.py`

### REQ-5 — Role changes SHALL sync Zitadel `org:owner` grant

**Finding:** A-4 (HIGH). `_ZITADEL_ROLE_BY_PORTAL_ROLE` maps `admin → org:owner` but `platform_update_role` mutates `portal_users.role` without promoting/demoting the Zitadel grant. For services that read the JWT claim directly (klai-retrieval-api, klai-connector), the effective role diverges until the JWT expires.

**EARS:**

WHEN `platform_update_role` (or `users.py::update_user_role`) commits a role change to or from "admin", THE SYSTEM SHALL after the DB commit invoke a Zitadel API call:
- ON promotion to admin: `await zitadel.grant_user_role(org_id=settings_zitadel_portal_org_id(), user_id=zitadel_user_id, role_key="org:owner")`
- ON demotion from admin: equivalent "remove grant" Zitadel API call (add to `app/services/zitadel.py` if not present)

IF the Zitadel call fails, THEN THE SYSTEM SHALL log audit event `platform_admin.role_change_zitadel_desync` with `db_role`, `target_zitadel_role`, AND surface `zitadel_sync_failed=true` in the response audit details, BUT SHALL NOT rollback the DB commit.

WHEN the same logic is applied in `users.py::update_user_role`, THE SYSTEM SHALL not duplicate the Zitadel call (use a shared helper `_sync_zitadel_role_grant(zitadel_user_id, new_role)` in `app/services/zitadel.py`).

**Files:**
- `klai-portal/backend/app/api/admin/platform_manage.py:120-172` (`platform_update_role`)
- `klai-portal/backend/app/api/admin/users.py::update_user_role`
- `klai-portal/backend/app/services/zitadel.py` (add `grant_user_role`, `remove_user_role`, `_sync_zitadel_role_grant` helpers)

### REQ-6 — Partial Zitadel-failure paths in invite + create-tenant SHALL emit audit events

**Finding:** A-7 (MED). Failures at Zitadel-call sites in `platform_invite` and `platform_create_tenant` raise 502 with `logger.exception` (goes to VictoriaLogs, 30-day retention) but no `log_event` (audit-table, permanent). Bulk-invite probing leaves no audit trace.

**EARS:**

WHEN any Zitadel call inside `platform_invite` fails, THE SYSTEM SHALL emit `log_event(action="platform_admin.invite_<step>_failed", details={...})` with `step` (one of: zitadel_invite | send_invite_code | grant_role | db_commit | rollback_zitadel_user), `target_email`, `target_org_id`, `error` (truncated to 200 chars).

WHEN any Zitadel call inside `platform_create_tenant` fails, THE SYSTEM SHALL emit `log_event(action="platform_admin.create_tenant_<step>_failed", details={...})` with the same `step` shape plus `target_org_slug`.

WHEN the emission itself fails (DB session aborted), THE SYSTEM SHALL fall back to `logger.exception("platform_admin_audit_emit_failed", original_event=...)` so the failure remains visible in VictoriaLogs.

**Pattern:** wrap each external call in a `try / finally` so audit-emit always runs (mirror `kb_offboarding._do_delete` audit-event pattern).

**Files:** `klai-portal/backend/app/api/admin/platform_manage.py:378-417` (invite), `:488-559` (create-tenant). Tests: extend `klai-portal/backend/tests/test_platform_admin_manage.py`.

### REQ-10 — `platform_create_tenant` user-insert SHALL use `tenant_scoped_session`

**Finding:** A-3 (MED). `await set_tenant(db, org_row.id)` on the request-scoped session from `Depends(get_db)`. Today safe because `get_db`'s `finally:` resets the GUC, but a precedent that future reads/writes between `set_tenant` and `commit` would land on the wrong tenant. Not conforming to standards.md § 3.

**EARS:**

WHEN `platform_create_tenant` performs the new owner-user INSERT, THE SYSTEM SHALL execute it inside a separate `tenant_scoped_session(org_row.id)` block instead of `await set_tenant(db, org_row.id)` on the request-scoped session.

**Pattern reference:** mirror `platform_invite` at line 420 of the same file.

**Files:** `klai-portal/backend/app/api/admin/platform_manage.py:531-559`.

### REQ-11 — Partial-failure audit-trail SHALL describe attempted steps

**Finding:** A-5 (MED). After REQ-4 refactor, the audit-row for `platform_admin.user_delete_partial_failure` MUST contain enough detail for operators to recover the user.

**EARS:**

WHEN the state machine from REQ-4 detects partial failure, THE SYSTEM SHALL invoke `log_event(action="platform_admin.user_delete_partial_failure", details={attempted_step, kbs_deleted_externally, api_keys_revoked, mcp_tokens_revoked, zitadel_identity_deleted, db_user_deleted})`.

**Dependency:** depends on REQ-4 implementation (state machine introduces the `attempted_step` value).

---

## 7. Module 3 — Admin AuthZ Tightening

Two requirements (P2) closing audit-gaps in suspended-user authentication and missing platform-unlock checks on admin activity-tab endpoints.

### REQ-12 — Suspended users SHALL be denied authentication

**Finding:** A-6 (MED). `platform_suspend` sets `portal_users.status = "suspended"` but `_resolve_caller_with_options` and `get_caller` do not check status. Status-badge is theatre — suspended user keeps exfiltrating data via valid bearer tokens.

**EARS:**

WHEN `_resolve_caller_with_options` (at `klai-portal/backend/app/core/permissions.py:160-206`) resolves a caller, THE SYSTEM SHALL check `portal_users.status == "suspended"` AND if so return HTTP 403 `{"error_code": "user_suspended"}` (not 401, to distinguish from auth-token-invalid).

WHEN `platform_suspend` is called, THE SYSTEM SHALL after the DB commit invoke `await zitadel.lock_user(org_id=settings_zitadel_portal_org_id(), user_id=zitadel_user_id)`.

WHEN `platform_reactivate` is called, THE SYSTEM SHALL after the DB commit invoke `await zitadel.unlock_user(org_id=settings_zitadel_portal_org_id(), user_id=zitadel_user_id)`.

IF the Zitadel lock/unlock call fails, THEN THE SYSTEM SHALL log audit event `platform_admin.suspend_zitadel_desync` AND surface `zitadel_sync_failed=true` in the response.

**Files:**
- `klai-portal/backend/app/core/permissions.py` (status check in resolver)
- `klai-portal/backend/app/api/admin/platform_manage.py:180-260` (suspend/reactivate)
- `klai-portal/backend/app/services/zitadel.py` (add `lock_user` / `unlock_user` methods if missing)

### REQ-13 — Admin widget activity endpoints SHALL enforce platform-unlock gate

**Finding:** B-6 (MED). `/conversations`, `/conversations/{conv_id}`, `/stats` skip the platform-unlock check. Admin of a revoked tenant can still read conversation logs for widgets that already exist. Known pattern `multi-layer-gate-audit-all-sides`.

**EARS:**

WHEN an admin hits `GET /api/widgets/{widget_id}/conversations`, THE SYSTEM SHALL include `Depends(require_platform_unlocked("widgets"))` in the route's dependencies.

WHEN an admin hits `GET /api/widgets/{widget_id}/conversations/{conv_id}`, THE SYSTEM SHALL include `Depends(require_platform_unlocked("widgets"))`.

WHEN an admin hits `GET /api/widgets/{widget_id}/stats`, THE SYSTEM SHALL include `Depends(require_platform_unlocked("widgets"))`.

**Files:** `klai-portal/backend/app/api/admin_widgets.py` lines 498, 558, 618.

**Dependency:** consistent with REQ-1's use of `assert_platform_unlocked` on the public partner endpoints (same widgets feature; same `require_platform_unlocked("widgets")` pattern).

---

## 8. Module 4 — Audit-Trail Integrity

Four requirements (P1 + P2) closing audit-trail integrity holes: scheme-allowlist on rendered URLs (XSS), server-side org_id derivation in audit writes, preview-conversation flagging, and soft-delete for widget audit-preservation.

### REQ-9 — ActivityTab source URLs SHALL be filtered by scheme allowlist

**Finding:** B-9 (MED). React 18+ logs a warning but still navigates on `javascript:` URI. Prompt-injection by visitor → "Reply with source pointing to javascript:alert(document.cookie)" → admin reviews → JS in admin-session context on `my.getklai.com`. Exploit chain CC-2.

**EARS:**

WHEN ActivityTab.tsx renders a source URL in the conversation viewer, THE SYSTEM SHALL filter `s.url`: only `http://` or `https://` schemes may render as `<a href>`; all other schemes (`javascript:`, `data:`, `file:`, `vbscript:`, scheme-less, mailto:, tel:, etc.) SHALL render as plain text.

WHEN the URL passes the scheme check, THE SYSTEM SHALL render `<a href={s.url} target="_blank" rel="noopener noreferrer">`.

**Files:** `klai-portal/frontend/src/routes/admin/widgets/_components/tabs/ActivityTab.tsx:324-340`.

**Test:** vitest unit test asserting `javascript:alert(1)` → plain text element (no `href` attribute), `https://example.com` → anchor element with `href`.

### REQ-14 — `record_widget_turn` SHALL derive `org_id` server-side from widget row

**Finding:** B-7 (HIGH). `record_widget_turn(widget_id, org_id, ...)` accepts org_id from the caller. Today safe because `_auth_via_session_token` via HKDF-binding prevents forging. Defensive gap: no `SELECT 1 FROM widgets WHERE id=:widget_id AND org_id=:org_id` to re-validate the binding. Any future admin-impersonation token or JWT-bypass would enable silent cross-tenant audit-write.

**EARS:**

WHEN `record_widget_turn` is called, THE SYSTEM SHALL NOT accept `org_id` as a parameter; instead it SHALL derive `org_id` server-side via `SELECT org_id FROM widgets WHERE id = :widget_id` issued through `cross_org_session()` for the lookup, then open `tenant_scoped_session(derived_org_id)` for the INSERT.

WHEN any caller in `klai-portal/backend/app/api/partner.py` calls `record_widget_turn`, THE SYSTEM SHALL stop passing the explicit `org_id` parameter.

**Files:** `klai-portal/backend/app/services/widget_audit.py:63-160`; callers in `klai-portal/backend/app/api/partner.py:417-430`.

**Dependency:** REQ-15 depends on this (defensive widget→org binding must land first so preview-detection cannot be misled by a forged org_id).

### REQ-15 — Preview-session conversations SHALL be flagged and excluded from stats

**Finding:** B-11 (MED). Admin preview-test writes conversation/messages rows without `is_preview=true` flag → stats polluted by admin's own probing.

**EARS:**

WHEN `widget_preview_session` mints a preview JWT, THE SYSTEM SHALL add an `is_preview: true` claim to the JWT payload.

WHEN `record_widget_turn` is called and the chat-path detects `is_preview=true` in the JWT, THE SYSTEM SHALL set `widget_conversations.is_preview = true` on the row.

WHEN the alembic migration runs, THE SYSTEM SHALL add `widget_conversations.is_preview BOOLEAN NOT NULL DEFAULT false` column (DDL in `upgrade()`, no row-write).

WHEN `widget_activity_stats` computes any aggregate, THE SYSTEM SHALL add `WHERE is_preview = false` to all queries so admin preview tests do not count toward visitor totals.

**Files:**
- new alembic migration `<rev>_widget_conversations_is_preview.py`
- `klai-portal/backend/app/api/admin_widgets.py:324-356` (mint JWT with claim)
- `klai-portal/backend/app/services/widget_audit.py` (write column)
- `klai-portal/backend/app/api/admin_widgets.py:614-668` (stats filter)

**Dependency:** depends on REQ-14 (org_id binding integrity).

### REQ-16 — Widget DELETE SHALL be soft-delete; CASCADE on audit tables SHALL be removed

**Finding:** B-14 (MED). `widget_id ... ON DELETE CASCADE` wipes all conversations + messages on widget DELETE. Admin can "wipe traces" mid-investigation. Exploit chain CC-2 final step.

**EARS:**

WHEN an admin calls `DELETE /api/widgets/{widget_id}`, THE SYSTEM SHALL set `widgets.deleted_at = NOW()` instead of physical DROP.

WHEN queries fetch widgets for admin reads or partner endpoints, THE SYSTEM SHALL add `WHERE deleted_at IS NULL`.

WHEN `widget_conversations` / `widget_messages` are queried for the admin Activity tab, THE SYSTEM SHALL keep showing them even if the widget is soft-deleted (audit-trail preservation).

WHEN the alembic migration runs, THE SYSTEM SHALL add `widgets.deleted_at TIMESTAMP NULL` column AND drop the `ON DELETE CASCADE` constraint on `widget_conversations.widget_id` AND `widget_messages.widget_id` (replace with plain FK so widget-soft-delete preserves the audit rows).

**Files:**
- new alembic migration `<rev>_widget_soft_delete_and_audit_preserve.py`
- `klai-portal/backend/app/api/admin_widgets.py:409-434` (DELETE handler)
- all widget-fetching queries in `partner.py` and `admin_widgets.py` (add `deleted_at IS NULL` filter)

---

## 9. Module 5 — Infrastructure & Cross-Cutting

Four requirements (P0 + P2). REQ-3 is the P0 RLS-hardening; REQ-17 and REQ-19 are cross-repo (klai-infra) infrastructure work; REQ-18 closes the latent slug-injection class.

### REQ-3 — `portal_templates` RLS policy SHALL have explicit WITH CHECK clause

**Finding:** C-1 (HIGH). Migration `34d8f876ffbf` writes only `USING (_rls_current_org_id() IS NULL OR org_id = _rls_current_org_id())`. PostgreSQL reuses USING as implicit WITH CHECK for `FOR ALL` policies → WITH CHECK passes ANY `org_id` when `app.cross_org_admin=true`. Bug-class A-1 from the 2026-05-05 audit re-introduced two weeks after standardisation. Latent (no current cross-org writer), but the next SPEC that adds cross-tenant template management hits it immediately (chain CC-4).

**EARS:**

WHEN a new alembic migration is created after `5b7c9d1e2f3a` (current head), THE SYSTEM SHALL redefine the `tenant_isolation` policy on `portal_templates` with explicit USING + WITH CHECK clauses per `reports/audit-tenant-isolation-2026-05-05/standards.md` § 1 Cat-D pattern:

```sql
DROP POLICY tenant_isolation ON portal_templates;
CREATE POLICY tenant_isolation ON portal_templates
    FOR ALL
    USING      (_rls_current_org_id() IS NULL OR org_id = _rls_current_org_id())
    WITH CHECK (org_id = _rls_current_org_id());
```

WHEN the migration runs, THE SYSTEM SHALL place all RLS DDL in `post_deploy_<rev>.sql` (NOT in `upgrade()`), per the `alembic-cannot-drop-non-portal_api-tables` pitfall — `portal_templates` is owned by `klai` superuser.

WHEN the regression test `tests/test_portal_templates_rls_with_check.py` runs, THE SYSTEM SHALL open a `cross_org_session()` and attempt `INSERT INTO portal_templates (org_id, ...) VALUES (1, ...)` from a non-admin session; the INSERT SHALL fail with PostgreSQL ERRCODE matching `42501` (insufficient_privilege) or `23514` (check_violation) when the WITH CHECK clause rejects.

WHEN the migration is reviewed, THE SYSTEM SHALL NOT modify existing migration `34d8f876ffbf` (already deployed) — the new migration is an additive delta.

**Files:**
- new alembic migration `klai-portal/backend/alembic/versions/<new_revid>_portal_templates_rls_with_check.py` (empty `upgrade()` body, just records the post-deploy SQL link)
- new `klai-portal/backend/alembic/versions/post_deploy_<new_revid>_portal_templates_rls_with_check.sql`
- new test `klai-portal/backend/tests/test_portal_templates_rls_with_check.py`

### REQ-17 — `/bot/*` paths SHALL deny iframe embedding

**Finding:** B-19 (MED). `/bot/<widgetId>` is iframable from any origin → clickjacking + cross-bot impersonation. LibreChat block in Caddy sets `frame-ancestors`, but the portal-SPA equivalent is not visible.

**EARS:**

WHEN a response is served on a `/bot/<widget_id>` route by the portal-SPA Caddy block, THE SYSTEM SHALL set response header `Content-Security-Policy: frame-ancestors 'none'`.

**Files:** Caddyfile in klai-infra (cross-repo). Implementation requires a klai-infra PR with a cross-reference back to this SPEC. Reference: discover the file via `grep -rn 'my.getklai.com' klai-infra/`.

### REQ-18 — Provisioning slug SHALL be validated at every boundary

**Finding:** C-3 (MED). `_start_librechat_container`, `_write_tenant_caddyfile`, `_flush_redis_and_restart_librechat`, `_sync_drop_mongodb_tenant_user`, `_create_mongodb_tenant_user` all consume `slug` directly in container names, volume-mount paths, and Caddyfile content. Validation lives only in `_to_slug` (signup). Any future caller bypassing `_to_slug` (admin endpoint, retry handler, migration) opens path-traversal + Caddyfile-injection.

**EARS:**

WHEN any of the provisioning functions listed in the finding is called, THE SYSTEM SHALL invoke `_assert_safe_slug(slug)` as the first statement of the function body. The regex SHALL be `^[a-z0-9]([a-z0-9-]{0,62}[a-z0-9])?$`.

IF the regex match fails, THEN THE SYSTEM SHALL raise `ValueError("slug failed safe-slug validation: <slug>")`.

WHEN the alembic migration runs, THE SYSTEM SHALL add `CHECK CONSTRAINT chk_portal_orgs_slug_safe CHECK (slug ~ '^[a-z0-9]([a-z0-9-]{0,62}[a-z0-9])?$')` on `portal_orgs.slug` so DB-level invariant enforcement layers on top of code-level validation.

**Files:**
- `klai-portal/backend/app/services/provisioning/infrastructure.py:269-401`
- new `klai-portal/backend/app/services/provisioning/_slug_guard.py`
- new alembic migration `<rev>_portal_orgs_slug_check_constraint.py` (DDL in `upgrade()`; `portal_orgs` is owned by `portal_api` so `op.execute` runs)

### REQ-19 — crawl4ai DNS SHALL refuse RFC1918 + link-local resolves

**Finding:** C-4 (MED). `validate_url` SSRF guard is DNS-rebinding vulnerable (TOCTOU). Portal-api does `getaddrinfo` once; crawl4ai re-resolves. Attacker rotates DNS with 1s TTL: primary = public IP (passes validator), second resolve = `172.18.0.5` (klai-net). docker-socket-proxy is per network policy unreachable for crawl4ai (mitigation), but Redis/Qdrant on klai-net are reachable.

**EARS:**

WHEN the Docker compose config for `crawl4ai` is updated, THE SYSTEM SHALL add either a `dns:` config pointing to an internal DNS server that refuses RFC1918 (`10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`) and `169.254.0.0/16` (link-local, AWS-metadata), OR a sidecar HTTPS-only egress proxy that crawl4ai must traverse.

**Files:** `deploy/docker-compose.yml` crawl4ai service block, or equivalent infra config. Implementation via klai-infra PR with cross-reference back to this SPEC.

---

## 10. Acceptance Criteria Summary

Detailed Given/When/Then scenarios for all 19 REQs live in `acceptance.md`. Each REQ has at minimum 2 scenarios (happy-path plus edge-case / negative-case). Quality-gate criteria: 85% coverage on new code; `ruff check` + `ruff format --check` + `pyright` clean; security-review skill PASS for REQ-1, REQ-2, REQ-3.

## 11. Implementation Strategy

### Deploy windows

- **Window 1 (this week, P0):** REQ-1, REQ-2, REQ-3, REQ-9. Surgical code fixes plus REQ-2 default-deny + data-migration. No customer-communication needed — REQ-2 cohort is 2 widgets in the Klai-own `getklai` tenant (verified 2026-05-24); replaced by the automated CI cohort gate in `portal-api.yml` (deploy aborts if `impacted_widgets > 5` OR any external tenant appears). The migration's automated branches pick the safe default per row.
- **Window 2 (week 2-3, P1):** REQ-4, REQ-5, REQ-6, REQ-7, REQ-8. Backend-heavy; parallel-implementable where files do not overlap.
- **Window 3 (week 3-4, P2):** REQ-10..REQ-19. Defense-in-depth and audit-trail integrity; lower risk.

### Methodology

TDD (RED-GREEN-REFACTOR) per `quality.development_mode: tdd` default in `.moai/config/sections/quality.yaml`. Every REQ gets at least one new test file with the cited acceptance criterion as the RED test.

Brownfield enhancement: all REQs touch existing code; first read existing code for context, then write the RED test informed by current behavior.

### Dependencies between REQs

- REQ-11 depends on REQ-4 (audit-event semantics derive from the state-machine `attempted_step`).
- REQ-15 depends on REQ-14 (defensive widget→org binding first; otherwise preview-detection can be misled by a forged `org_id`).
- REQ-13 depends conceptually on REQ-1 (same `require_platform_unlocked` pattern; both implementations should land together for consistency).

### Risk mitigation

- **REQ-2 breaking change:** automated CI cohort gate (GitHub Actions step) replaces the 7-day customer-communication window. Verified 2026-05-24: 2 widgets impacted, both in `getklai` tenant. The deploy pipeline runs `scripts/verify_widget_cohort.sh` via SSH and aborts non-zero if `impacted_widgets > 5` OR any external tenant slug appears. The data-migration picks the safe default per row automatically (allow_any_origin=true for widgets with `public_share_enabled=true`; allowed_origins=[tenant_subdomain] otherwise). If a widget IS legitimately embedded on a non-Klai-subdomain and the migration picks the wrong default, the admin can flip the toggle post-deploy via the UI; the chat will deny until they do, which is the safe-by-default fail mode.
- **REQ-4 state-machine refactor:** hot-cut (no feature flag). Mirror `deprovisioning_orchestrator` pattern exactly; do not expand scope. Reuse `tenant_lifecycle_events` audit table where applicable. Rollback plan: git revert + container restart. Post-deploy: 48h monitoring of `platform_admin.user_delete_partial_failure` audit-event count via Grafana product_events.
- **REQ-12 status enforcement:** inline `user.status` check in `_resolve_caller_with_options` (no Redis cache); the row is already SELECTed per request, so this is 0 extra DB-roundtrips per request.
- **REQ-17 + REQ-19:** infra PRs in the klai-infra repo, not in this SPEC's code scope. Tracked as `cross_repo_dependency: klai-infra` in this SPEC's PR description; SPEC deliverable status does NOT block on klai-infra merge.

### Trust 5 quality gates

- **Tested:** 85%+ coverage on new code; all 19 ACs have at least one passing test.
- **Readable:** docstring on every new public function with `@MX:NOTE` where non-obvious.
- **Unified:** `ruff check` + `ruff format` + `pyright` without errors.
- **Secured:** REQ-1, REQ-2, REQ-3 require security-review skill PASS before merge.
- **Trackable:** conventional commits cross-linked to Finding-IDs (B-1, A-2, etc.) in the commit body — e.g. `feat(widgets): platform-unlock on public endpoints (Finding B-1)`.

## 12. mx_plan — @MX tag targets

Potential @MX annotations across the changed surface area:

**@MX:ANCHOR candidates (fan_in >= 3 expected):**
- `assert_platform_unlocked(org, feature)` in `permissions.py` — used by REQ-1 (3 partner endpoints) + REQ-13 (3 admin endpoints) = fan_in 6 minimum.
- `record_widget_turn` in `widget_audit.py` after REQ-14 signature change — used by every chat-completion turn.
- `_assert_safe_slug(slug)` in `_slug_guard.py` (REQ-18) — used by 5 provisioning functions on every call.

**@MX:WARN candidates (danger zone, requires @MX:REASON):**
- `user_deletion_orchestrator.run()` (REQ-4) — multi-step external mutation with partial-failure state; @MX:REASON should cite irreversibility + partial-recovery flow.
- The data-migration in REQ-2 — touches every existing widget row; @MX:REASON should cite the automated CI cohort gate (`scripts/verify_widget_cohort.sh` invoked from `.github/workflows/portal-api.yml`, verified 2026-05-24 at 2 widgets in `getklai` tenant) and link to the cohort-query SQL in the REQ-2 section.

**@MX:NOTE candidates (context delivery):**
- `widget_messages_retention.py` worker (REQ-8) — 90-day retention with chunked DELETE; @MX:NOTE explaining the chunking strategy.
- `assert_platform_unlocked` call sites in `partner.py` (REQ-1) — @MX:NOTE explaining why 404 (not 403) on `/widget-config` and `/public-bot-config` (existence-non-disclosure per audit standards).
- The WITH CHECK clause in the REQ-3 migration — @MX:NOTE explaining the standards.md § 1 Cat-D template and the C-1 finding history.

**@MX:TODO candidates (resolved in GREEN phase):**
- Each new public function in `user_deletion_orchestrator.py` (REQ-4) starts as `@MX:TODO` until the failing test passes.

Final tag-set will be confirmed during the Run phase per the SPEC-MX-001 protocol.

## 13. Definition of Done

- All 19 REQs have a passing test (`acceptance.md` AC1..AC19).
- `klai-portal/backend` `uv run pytest -q --tb=short` exits 0.
- `klai-portal/backend` `uv run ruff check .` exits 0.
- `klai-portal/backend` `uv run ruff format --check .` exits 0.
- `klai-portal/backend` `uv run --with pyright pyright` exits 0.
- `klai-portal/frontend` `npm run lint` + `npm run test` exit 0 (covers REQ-9, REQ-2 UI bits).
- Coverage on new code >= 85% (per `.moai/config/sections/quality.yaml` threshold).
- security-review skill PASS for REQ-1, REQ-2, REQ-3.
- Alembic head still single (verify with `alembic heads | wc -l == 1`) — see `alembic-multi-pr-head-split` pitfall.
- Production smoke-test: after Window 1 deploy, a curl against `/partner/v1/widget-config?id=<widget-of-tenant-with-widgets-disabled>` returns 404; a curl against `/partner/v1/widget-config?id=<widget-of-tenant-with-widgets-enabled>` returns 200.
- Automated CI cohort gate (GitHub Actions step in `portal-api.yml`) passes on the deploy run for Window 1; the step's output (cohort SQL result) is captured in the workflow log for audit.
- Cross-repo: klai-infra PRs for REQ-17 + REQ-19 tracked as `cross_repo_dependency` in this SPEC's PR description; deliverable status of this SPEC does NOT block on klai-infra merge — REQ-17 + REQ-19 are marked "delegated to klai-infra" once the cross-repo PR is opened with this SPEC ID in the description.

## 14. Exclusions (What NOT to Build)

This section is the canonical authority for scope-limits. See § 3 Non-goals above for the full list. Notable exclusions:

- No new agent or service: all changes are in-repo to `klai-portal`, `klai-portal/frontend`, and `klai-infra`.
- No frontend redesign: REQ-2 EmbedTab toggle and REQ-9 ActivityTab scheme filter are surgical edits — no new design tokens, no new components.
- No new pricing/billing change: REQ-1 + REQ-13 enforce `enabled_addons` membership but do not change the addon-purchase flow.
- No master-key rotation: B-13 is explicitly deferred to a separate infra SPEC.
- No prompt-injection mitigation: B-10 is explicitly deferred to a separate security/UX SPEC.

---

End of SPEC-SEC-CROSS-TENANT-FOLLOWUP-001.
