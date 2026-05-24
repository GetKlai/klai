# SPEC-SEC-CROSS-TENANT-FOLLOWUP-001 — Implementation Plan

Status: draft
Created: 2026-05-24
Owner: platform/backend
Priority: P0 (security)
Methodology: TDD per `.moai/config/sections/quality.yaml`

## 1. Goal

Convert the 19 EARS requirements in `spec.md` into a phased, parallel-implementable plan with concrete task decomposition, technology choices, risk-mitigation steps, and references to in-codebase patterns. This plan covers only the WHAT-and-WHEN of implementation; the WHAT-TO-BUILD lives in `spec.md`.

## 2. Technology stack (existing klai stack — no new external libraries)

| Layer | Technology | Version pin |
|---|---|---|
| Backend runtime | Python 3.13 + FastAPI | per `klai-portal/backend/pyproject.toml` |
| ORM | SQLAlchemy 2.0 async | per pyproject |
| Migrations | Alembic | per pyproject |
| Validation | Pydantic v2 | per pyproject |
| DB | PostgreSQL with RLS Cat-A + Cat-D patterns | server policy |
| Rate-limit | Redis ZSET sliding window via `klai-portal/backend/app/services/partner_rate_limit.py::check_rate_limit` | existing |
| Auth | OIDC via Zitadel; HKDF-per-tenant widget JWT | existing |
| Logging | structlog with `ProcessorFormatter` per `portal-logging-py.md` rule | existing |
| Frontend | React 18 + TanStack Router | per `klai-portal/frontend/package.json` |
| Frontend tests | vitest + Playwright | per package.json |

No new external libraries are introduced. Every new helper (e.g. `user_deletion_orchestrator`, `widget_messages_retention`, `_slug_guard`) is a copy-pattern from an existing klai module.

## 3. Phased deploy windows

| Window | Scope | REQs | Risk | Pre-deploy check |
|---|---|---|---|---|
| 1 | P0 surgical + REQ-2 default-deny | REQ-1, REQ-2, REQ-3, REQ-9 | Medium (REQ-2 data-migration on 2 widgets) | Operator re-runs REQ-2 cohort query immediately before merge; aborts to follow-up SPEC if external customer widgets appear |
| 2 | P1 hardening | REQ-4, REQ-5, REQ-6, REQ-7, REQ-8 | Medium | Standard CI quality gates |
| 3 | P2 defense-in-depth | REQ-10, REQ-11, REQ-12, REQ-13, REQ-14, REQ-15, REQ-16, REQ-17, REQ-18, REQ-19 | Low | Standard CI quality gates; REQ-17 + REQ-19 tracked as cross-repo dependency |

Each window is independently deployable; later windows do NOT depend on earlier ones for compilation (with the dependency exceptions listed in spec.md § 11).

## 4. Module-by-module task decomposition

### Module 1 — Public Widget Hardening (4 REQs)

#### REQ-1 (Finding B-1) — assert_platform_unlocked on partner endpoints

Tasks:
- **REQ-1.T1 (RED):** write `tests/test_widget_platform_unlock.py` with three test functions:
  - `test_widget_config_returns_404_when_widgets_not_unlocked`
  - `test_public_bot_config_returns_404_when_widgets_not_unlocked`
  - `test_chat_completion_returns_403_when_widgets_not_unlocked_post_jwt`
- **REQ-1.T2 (GREEN):** add `assert_platform_unlocked(org, "widgets")` calls at:
  - `partner.py::widget_config` after the org resolution
  - `partner.py::public_bot_config` after the org resolution (now at lines 917-919 post-#672)
  - `partner_dependencies.py::_auth_via_session_token` after the widget row is loaded
- **REQ-1.T3 (REFACTOR):** extract a small `_assert_widget_org_unlocked(widget_row, db)` helper if the three call-sites diverge.

Files written: 1 new test file, 2 modified source files.

Reference implementation: `klai-portal/backend/app/api/admin_widgets.py:196` (`Depends(require_platform_unlocked("widgets"))`) and `klai-portal/backend/app/core/permissions.py:425` (`assert_platform_unlocked` imperative form).

#### REQ-2 (Finding B-2) — default-deny origins (BREAKING)

Tasks:
- **REQ-2.T1 (RED):** write `tests/test_widget_origin_default_deny.py` with the four origin matrix tests:
  - `test_origin_allowed_with_empty_list_and_allow_any_origin_false_returns_false`
  - `test_origin_allowed_with_empty_list_and_allow_any_origin_true_returns_true`
  - `test_origin_allowed_with_tenant_subdomain_in_list_passes`
  - `test_origin_allowed_with_other_origin_in_tenant_subdomain_list_rejected`
- **REQ-2.T2 (GREEN code):** update `widget_auth.py::origin_allowed` to require an `allow_any_origin` boolean (read from the widget row by the caller).
- **REQ-2.T3 (GREEN model):** add `allow_any_origin` column on `widgets`; add `loaded_origin` column on `widget_conversations`; write alembic migration `<rev>_widget_allow_any_origin_and_loaded_origin.py`.
- **REQ-2.T4 (GREEN audit):** thread `loaded_origin` through `record_widget_turn` callers in `partner.py` to write the column.
- **REQ-2.T5 (GREEN UI):** add toggle + warning copy to `EmbedTab.tsx` and default-origin logic to `new.tsx`.
- **REQ-2.T6 (GREEN data-migration):** `post_deploy_<rev>.sql` with the three branches per spec.md REQ-2.
- **REQ-2.T7a (GREEN cohort script):** add `scripts/verify_widget_cohort.sh` — bash script that SSHes to core-01, runs the cohort SQL from spec.md REQ-2, parses output, exits 0 if `impacted_widgets <= 5 AND impacted_tenants ⊆ {platform_org_slug}`, exits non-zero with a clear error otherwise. Idempotent and re-runnable from any CI environment with SSH access to core-01.
- **REQ-2.T7b (GREEN CI gate):** add a step to `.github/workflows/portal-api.yml` after the test job and before the deploy job that runs `scripts/verify_widget_cohort.sh`. On non-zero exit: the deploy is blocked; the action log captures the cohort SQL output. The step is unconditional (no `if:` guard) — every portal-api deploy runs the gate.
- **REQ-2.T8 (REFACTOR):** verify `origin_allowed` signature change does not break any existing caller — grep for all sites.

Files written: 1 new test file, ~6 modified source files, 2 frontend files, 1 alembic migration, 1 post-deploy SQL, 1 verification bash script, 1 modified GitHub Actions workflow. Mailer-template + in-app notification dropped per § 10.1 decision (cohort = 2 widgets internal to `getklai`).

Reference implementation: existing widget_auth.py `_parse_origin` for the URL parsing pattern; existing `EmbedTab.tsx` for the toggle UX pattern. Cohort-verification CI gate pattern mirrors how `klai-infra/sync-env-removal.yml` enforces typed-string `allow_removal=I-CONFIRM-REMOVAL` as a CI input — destructive default-flips are automatically gated by the pipeline, not by manual operator pre-merge inspection.

#### REQ-7 (Finding B-4) — per-widget mint rate-limit

Tasks:
- **REQ-7.T1 (RED):** write `tests/test_widget_mint_rate_limit.py` with:
  - `test_widget_config_429_after_10_calls_per_minute`
  - `test_public_bot_config_429_after_10_calls_per_minute`
  - `test_widget_config_includes_retry_after_header_on_429`
- **REQ-7.T2 (GREEN):** add `check_rate_limit(redis_pool, f"widget_mint:{widget_id}", limit_per_minute=10)` at the entry of both endpoints in `partner.py`.
- **REQ-7.T3 (REFACTOR):** extract into a small `_check_widget_mint_rate_limit(widget_id)` helper if duplicated.

Files written: 1 new test file, 1 modified source file.

Reference implementation: `klai-portal/backend/app/services/partner_rate_limit.py::check_rate_limit` (signature `(redis_pool, key_id, limit_per_minute, window_seconds=60)`).

#### REQ-8 (Finding B-5) — length-cap + retention worker

Tasks:
- **REQ-8.T1 (RED):** write `tests/test_widget_messages_length_cap.py` with `test_content_above_10000_chars_is_clamped`.
- **REQ-8.T2 (RED):** write `tests/test_widget_messages_retention.py` with `test_retention_deletes_messages_older_than_90_days` + `test_retention_audit_event_emitted`.
- **REQ-8.T3 (GREEN code):** clamp content at INSERT in `widget_audit.py::record_widget_turn`.
- **REQ-8.T4 (GREEN migration):** alembic migration adding CHECK constraint on `widget_messages.content` length.
- **REQ-8.T5 (GREEN worker):** new `app/services/widget_messages_retention.py` with chunked DELETE loop.
- **REQ-8.T6 (GREEN lifespan):** register worker in `app/main.py` lifespan as background task.
- **REQ-8.T7 (GREEN config):** add `widget_messages_retention_days: int = 90` to `app/core/config.py` Settings.
- **REQ-8.T8 (REFACTOR):** ensure chunked DELETE does not exceed transaction-time limits; add structured logging per `portal-logging-py.md`.

Files written: 2 new test files, ~3 modified source files, 1 new source file, 1 alembic migration.

Reference implementation: `klai-portal/backend/app/services/bot_poller.py` for the lifespan-registered background worker pattern; `klai-portal/backend/app/services/recording_cleanup.py` for the chunked-DELETE pattern.

### Module 2 — Platform-Admin Destructive Operations (5 REQs)

#### REQ-4 (Finding A-2) — state-machine refactor for hard-delete

Tasks:
- **REQ-4.T1 (RED):** write `tests/test_user_deletion_orchestrator.py` with:
  - `test_orchestrator_zitadel_remove_first_then_external_then_db`
  - `test_orchestrator_marks_failed_partial_on_external_kb_delete_failure`
  - `test_orchestrator_marks_failed_partial_on_zitadel_5xx`
  - `test_orchestrator_is_idempotent_on_retry`
  - `test_retry_endpoint_resumes_from_failed_state`
- **REQ-4.T2 (GREEN scaffolding):** create `app/services/user_deletion_orchestrator.py` mirroring `deprovisioning_orchestrator.py` structure (dataclass state, ordered step list, retry loop with exponential backoff).
- **REQ-4.T3 (GREEN steps):** create `app/services/user_deletion_steps.py` with three idempotent step functions in order: `step_remove_zitadel`, `step_delete_external_kbs`, `step_delete_portal_db`.
- **REQ-4.T4 (GREEN schema):** alembic migration adding `portal_users.deletion_status`, `failure_reason JSONB`, `last_attempted_step TEXT`.
- **REQ-4.T5 (GREEN refactor):** update `platform_delete_user` to call the orchestrator instead of inline logic.
- **REQ-4.T6 (GREEN retry endpoint):** add `POST /api/admin/platform/users/{zitadel_user_id}/retry-delete`.
- **REQ-4.T7 (REFACTOR):** ensure each step has `# @MX:NOTE` describing idempotency reasoning; add `# @MX:ANCHOR` on the orchestrator entry point.

Files written: 1 new test file, 1 new orchestrator file, 1 new steps file, 1 alembic migration, 2 modified handler files.

Reference implementation: `klai-portal/backend/app/services/provisioning/deprovisioning_orchestrator.py` (16-step idempotent orchestrator); `klai-portal/backend/app/services/provisioning/deprovisioning_steps.py` (step module).

#### REQ-5 (Finding A-4) — Zitadel role-grant sync

Tasks:
- **REQ-5.T1 (RED):** write `tests/test_platform_role_change_zitadel_sync.py` with:
  - `test_promote_admin_calls_zitadel_grant_org_owner`
  - `test_demote_admin_calls_zitadel_remove_grant`
  - `test_zitadel_5xx_logs_desync_audit_event_but_does_not_rollback_db`
- **REQ-5.T2 (GREEN helper):** add `_sync_zitadel_role_grant(zitadel_user_id, new_role)` to `app/services/zitadel.py`.
- **REQ-5.T3 (GREEN call-sites):** invoke helper in `platform_manage.py::platform_update_role` and `users.py::update_user_role`.
- **REQ-5.T4 (REFACTOR):** audit-event emission via structured logging per `portal-logging-py.md`.

Files written: 1 new test file, 3 modified source files.

#### REQ-6 (Finding A-7) — audit failed Zitadel paths

Tasks:
- **REQ-6.T1 (RED):** extend `tests/test_platform_admin_manage.py` with `test_invite_audit_emitted_on_zitadel_failure` + `test_create_tenant_audit_emitted_on_zitadel_failure`.
- **REQ-6.T2 (GREEN):** wrap each external Zitadel call in `platform_invite` and `platform_create_tenant` with `try / finally` that emits `log_event(action="platform_admin.<flow>_<step>_failed", details={...})`.
- **REQ-6.T3 (REFACTOR):** small helper `_emit_audit_safely(...)` so the audit emission cannot itself raise into the handler.

Files written: 1 extended test file, 1 modified handler file.

#### REQ-10 (Finding A-3) — `tenant_scoped_session` in create_tenant

Tasks:
- **REQ-10.T1 (RED):** extend `tests/test_platform_admin_manage.py` with `test_create_tenant_uses_tenant_scoped_session_for_user_insert` (assert via mock or via a SQL-tracer that the INSERT happens under a separately-scoped session).
- **REQ-10.T2 (GREEN):** refactor `platform_create_tenant:531-559` to mirror `platform_invite:420`.
- **REQ-10.T3 (REFACTOR):** no further action.

Files written: 1 extended test, 1 modified handler.

#### REQ-11 (Finding A-5) — partial-failure audit-event

Tasks: built into REQ-4.T1-T7 (the state-machine audit-events are the implementation). REQ-11 is documented separately for traceability.

### Module 3 — Admin AuthZ Tightening (2 REQs)

#### REQ-12 (Finding A-6) — suspended user denied + Zitadel lock

Tasks:
- **REQ-12.T1 (RED):** write `tests/test_user_suspension_blocks_auth.py` with:
  - `test_suspended_user_authentication_returns_403_user_suspended`
  - `test_platform_suspend_calls_zitadel_lock_user`
  - `test_platform_reactivate_calls_zitadel_unlock_user`
- **REQ-12.T2 (GREEN resolver):** add status check in `permissions.py::_resolve_caller_with_options`.
- **REQ-12.T3 (GREEN suspend):** add Zitadel lock/unlock in `platform_manage.py::platform_suspend` and `platform_reactivate`.
- **REQ-12.T4 (GREEN zitadel.py):** add `lock_user` / `unlock_user` helpers if missing.
- **REQ-12.T5 (REFACTOR):** structured audit on Zitadel desync.

Files written: 1 new test, 3 modified source files.

#### REQ-13 (Finding B-6) — admin activity endpoints platform-unlocked

Tasks:
- **REQ-13.T1 (RED):** extend `tests/test_admin_widgets.py` with three tests covering 403 on conversations / conversation-detail / stats when `widgets` is locked.
- **REQ-13.T2 (GREEN):** add `_platform: UserPermissions = Depends(require_platform_unlocked("widgets"))` to the three routes at `admin_widgets.py:498`, `:558`, `:618`.
- **REQ-13.T3 (REFACTOR):** none — pattern is identical to existing admin routes.

Files written: 1 extended test, 1 modified handler.

### Module 4 — Audit-Trail Integrity (4 REQs)

#### REQ-9 (Finding B-9) — scheme-allowlist on rendered URLs

Tasks:
- **REQ-9.T1 (RED):** write `frontend/src/routes/admin/widgets/_components/tabs/__tests__/ActivityTab.test.tsx` with vitest:
  - `test_javascript_uri_renders_as_plain_text`
  - `test_https_url_renders_as_anchor_with_target_blank`
  - `test_scheme_less_url_renders_as_plain_text`
- **REQ-9.T2 (GREEN):** add scheme check before `<a href={s.url}>`.
- **REQ-9.T3 (REFACTOR):** extract `_isSafeHttpUrl(url)` helper.

Files written: 1 new test file, 1 modified frontend file.

#### REQ-14 (Finding B-7) — server-side `org_id` derivation in record_widget_turn

Tasks:
- **REQ-14.T1 (RED):** extend `tests/test_widget_audit.py` with `test_record_widget_turn_derives_org_id_from_widget_row` (assert via DB introspection that the audit row's org_id matches `widgets.org_id`, regardless of any caller-supplied value).
- **REQ-14.T2 (GREEN):** change `record_widget_turn` signature: remove `org_id` parameter; lookup via `cross_org_session()`; then `tenant_scoped_session(derived_org_id)` for the INSERT.
- **REQ-14.T3 (GREEN callers):** update `partner.py:417-430` to stop passing `org_id`.
- **REQ-14.T4 (REFACTOR):** add `@MX:ANCHOR` on `record_widget_turn` post-refactor (fan_in increases with REQ-2 + REQ-15).

Files written: 1 extended test, 2 modified source files.

#### REQ-15 (Finding B-11) — preview-session flagging

Tasks:
- **REQ-15.T1 (RED):** write `tests/test_widget_preview_flagging.py` with:
  - `test_preview_jwt_carries_is_preview_claim`
  - `test_record_widget_turn_sets_is_preview_when_jwt_has_claim`
  - `test_widget_activity_stats_excludes_preview_conversations`
- **REQ-15.T2 (GREEN migration):** add `widget_conversations.is_preview BOOLEAN NOT NULL DEFAULT false` (DDL only — RLS-safe per `rls-with-check-blocks-migration-update`).
- **REQ-15.T3 (GREEN mint):** update `widget_preview_session` to set `is_preview=true` in payload.
- **REQ-15.T4 (GREEN write):** update `record_widget_turn` to read claim from caller context and set column.
- **REQ-15.T5 (GREEN stats):** update aggregations in `admin_widgets.py:614-668` to filter `is_preview=false`.

Files written: 1 new test, ~4 modified source files, 1 alembic migration.

Dependency: REQ-14 must land first.

#### REQ-16 (Finding B-14) — soft-delete widgets + audit-preserve

Tasks:
- **REQ-16.T1 (RED):** write `tests/test_widget_soft_delete.py` with:
  - `test_delete_widget_sets_deleted_at`
  - `test_get_widget_or_404_returns_404_for_soft_deleted`
  - `test_widget_conversations_preserved_after_widget_soft_delete`
- **REQ-16.T2 (GREEN migration):** add `widgets.deleted_at TIMESTAMP NULL`; drop `ON DELETE CASCADE` on `widget_conversations.widget_id` and `widget_messages.widget_id`.
- **REQ-16.T3 (GREEN handler):** update `delete_widget` to UPDATE `deleted_at = NOW()` instead of DELETE.
- **REQ-16.T4 (GREEN queries):** add `WHERE deleted_at IS NULL` to all widget-read queries.
- **REQ-16.T5 (REFACTOR):** ensure admin Activity tab keeps showing soft-deleted-widget conversations.

Files written: 1 new test, ~3 modified source files, 1 alembic migration.

### Module 5 — Infrastructure & Cross-Cutting (4 REQs)

#### REQ-3 (Finding C-1) — portal_templates RLS WITH CHECK fix

Tasks:
- **REQ-3.T1 (RED):** write `tests/test_portal_templates_rls_with_check.py`:
  - `test_cross_org_insert_rejected_by_with_check`
  - `test_cross_org_read_still_passes_via_using`
- **REQ-3.T2 (GREEN migration):** new alembic migration with empty `upgrade()` referencing `post_deploy_<rev>.sql` (post-deploy is run as `klai` superuser because `portal_templates` is owned by `klai`, per `alembic-cannot-drop-non-portal_api-tables` pitfall).
- **REQ-3.T3 (GREEN SQL):** post-deploy SQL with `DROP POLICY tenant_isolation` + `CREATE POLICY tenant_isolation ... WITH CHECK (...)`.
- **REQ-3.T4 (REFACTOR):** none.

Files written: 1 new test, 1 alembic migration, 1 post-deploy SQL.

Reference implementation: `klai-portal/backend/alembic/versions/c5d6e7f8a9b0_add_rls_policies.py` (Cat-D pattern); `klai-portal/backend/alembic/versions/post_deploy_a4f72e913c8b_widget_conversations_rls.sql` (post-deploy structure).

#### REQ-17 (Finding B-19) — CSP frame-ancestors on /bot/*

Tasks: cross-repo work in klai-infra. This SPEC documents the requirement; implementation = klai-infra PR.

- **REQ-17.T1 (klai-infra PR):** add `header /bot/* Content-Security-Policy "frame-ancestors 'none'"` to the portal-SPA Caddy block.
- **REQ-17.T2 (verification):** `curl -sI https://my.getklai.com/bot/<test-widget>` should include the CSP header.

Files written: 0 in this repo; 1 in klai-infra (Caddyfile).

#### REQ-18 (Finding C-3) — `_assert_safe_slug` + DB CHECK CONSTRAINT

Tasks:
- **REQ-18.T1 (RED):** write `tests/test_slug_guard.py`:
  - `test_assert_safe_slug_passes_canonical_form`
  - `test_assert_safe_slug_rejects_path_traversal`
  - `test_assert_safe_slug_rejects_special_chars`
  - `test_start_librechat_container_raises_on_unsafe_slug`
- **REQ-18.T2 (GREEN guard):** new `app/services/provisioning/_slug_guard.py` with regex `^[a-z0-9]([a-z0-9-]{0,62}[a-z0-9])?$`.
- **REQ-18.T3 (GREEN call-sites):** call `_assert_safe_slug(slug)` as first statement in all five `infrastructure.py` functions per spec.md REQ-18.
- **REQ-18.T4 (GREEN migration):** alembic migration adding `CHECK CONSTRAINT chk_portal_orgs_slug_safe ...` (DDL only, `portal_orgs` owned by `portal_api` so safe in `upgrade()`).
- **REQ-18.T5 (REFACTOR):** `@MX:ANCHOR` on `_assert_safe_slug` (fan_in = 5).

Files written: 1 new test, 1 new helper file, 1 modified source file, 1 alembic migration.

#### REQ-19 (Finding C-4) — crawl4ai RFC1918 DNS refusal

Tasks: cross-repo work in klai-infra. This SPEC documents the requirement; implementation = klai-infra PR.

- **REQ-19.T1 (klai-infra PR):** add DNS config or sidecar egress proxy to `crawl4ai` service block in `deploy/docker-compose.yml`.
- **REQ-19.T2 (verification):** run the `./scripts/smoke-ssrf-isolation.sh` script per `docker-socket-proxy.md` rule, extended to assert that an RFC1918 hostname resolution from inside crawl4ai fails.

Files written: 0 in this repo; 1-2 in klai-infra (compose file + optional sidecar Dockerfile).

## 5. Risk analysis per REQ

| REQ | Risk | Mitigation |
|---|---|---|
| REQ-1 | Existing widget integrations may rely on the public endpoints; 404 from `assert_platform_unlocked` could surprise tenants with widgets-still-installed-but-feature-removed. | Verify in staging: a tenant with `widgets` not in `enabled_addons` SHOULD already be blocked from admin CRUD; if any tenant has widgets but lacks `enabled_addons`, that is a data-correction first (add widgets to that tenant's addons before deploy). |
| REQ-2 | Breaking change for existing widgets with `allowed_origins=[]` AND `public_share_enabled=false`. | Migration covers them by setting `allowed_origins=[tenant_subdomain]`. Verified 2026-05-24: production cohort is 2 widgets, both inside `getklai` tenant — 0 external customers. Automated CI cohort gate (`scripts/verify_widget_cohort.sh` invoked from `portal-api.yml`) blocks the deploy if `impacted_widgets > 5` OR any external tenant slug appears; the migration's automated branches handle the safe defaults per row. If the gate fires, the 7-day customer-communication protocol is re-introduced via a follow-up SPEC. |
| REQ-3 | RLS policy redefinition during deploy must NOT interrupt running queries. | `DROP POLICY` + `CREATE POLICY` in the same transaction (post-deploy SQL) — single brief LOCK is acceptable. Run during a low-traffic window. |
| REQ-4 | State-machine refactor is a large surface; bugs could prevent recovery from partial-failure state. | Mirror `deprovisioning_orchestrator.py` exactly; reuse its test patterns (3-attempt exponential backoff, idempotency assertions). Hot-cut deployment (no feature flag) — pattern is proven, user-delete frequency at Klai scale is <5/week, git revert + container restart is the rollback plan. Post-deploy: 48h monitoring of `platform_admin.user_delete_partial_failure` audit-event count via Grafana product_events. |
| REQ-5 | Zitadel API instability could cause noisy desync audits. | The audit IS the recovery surface — ops can run a reconcile script against `zitadel.role_change_zitadel_desync` audit events. Acceptable. |
| REQ-6 | Audit-event flood under attack (bulk-invite probing). | Audit table is append-only with retention; emission cost is small; acceptable. |
| REQ-7 | Legitimate widget-config / public-bot-config use can exceed 10 mints/min on a busy customer site. | Per-widget limit is generous (10/min ~ 600/hour) and burst-tolerant via Redis sliding window. If legitimate use exceeds, raise the limit per-tenant via a Settings override. |
| REQ-8 | Chunked DELETE blocking other writes. | Use small chunk (10000 rows) with sleep between chunks; runs in a background task off the request path. Reference `recording_cleanup.py`. |
| REQ-9 | None — pure render-side filter, no backend change. | — |
| REQ-10 | None — strict refactor with no behavior change beyond session scoping. | — |
| REQ-11 | Bundled with REQ-4 risk. | — |
| REQ-12 | Adding status check to `_resolve_caller_with_options` is on the hot path of every authenticated request. | Status check is a single Python comparison against an already-loaded `PortalUser` row — no additional DB query. Negligible. |
| REQ-13 | None — additive dependency on three handlers. | — |
| REQ-14 | Changing `record_widget_turn` signature is a breaking change for callers. | All callers are in `partner.py` — small grep surface. Update in same commit. |
| REQ-15 | Stats queries become slightly more complex. | Index on `widget_conversations(widget_id, is_preview, started_at DESC)` to keep query plans efficient. |
| REQ-16 | Removing CASCADE may leave orphan conversation rows on physical-delete of a widget (if a future operation does that). | Document in handler that physical-delete must NOT be performed; the only delete path is soft-delete. Optional: add CHECK constraint or trigger preventing physical-delete. |
| REQ-17 | Misconfigured CSP could break iframe-based embedding the customer intended. | `frame-ancestors 'none'` applies only to `/bot/*`. The embed path is `/widget-config` and `/chat/completions`; not affected. |
| REQ-18 | Tightening slug validation could reject already-deployed tenants if any historical slug fails the new regex. | Run validation query in staging before deploy: `SELECT slug FROM portal_orgs WHERE slug !~ '^[a-z0-9]([a-z0-9-]{0,62}[a-z0-9])?$'` should return 0 rows. If non-zero, slugs need correction first. |
| REQ-19 | Cross-repo infra change; outside this codebase's CI. | Manual smoke-test per `docker-socket-proxy.md` rule after klai-infra PR merges. |

## 6. Reference implementations to study before coding

| Pattern | File | Use for |
|---|---|---|
| `require_platform_unlocked("widgets")` FastAPI dep | `klai-portal/backend/app/api/admin_widgets.py:196` | REQ-1, REQ-13 |
| `assert_platform_unlocked(org, feature)` imperative | `klai-portal/backend/app/core/permissions.py:425` | REQ-1 |
| `origin_allowed(origin, allowed_origins)` URL parsing | `klai-portal/backend/app/services/widget_auth.py:170` | REQ-2 |
| `check_rate_limit(redis_pool, key, limit)` sliding window | `klai-portal/backend/app/services/partner_rate_limit.py:21` | REQ-7 |
| Chunked DELETE retention worker | `klai-portal/backend/app/services/recording_cleanup.py` | REQ-8 |
| 16-step idempotent orchestrator | `klai-portal/backend/app/services/provisioning/deprovisioning_orchestrator.py` | REQ-4 |
| Cat-D RLS migration template | `klai-portal/backend/alembic/versions/c5d6e7f8a9b0_add_rls_policies.py` | REQ-3 |
| Post-deploy SQL for klai-owned tables | `klai-portal/backend/alembic/versions/post_deploy_a4f72e913c8b_widget_conversations_rls.sql` | REQ-3 |
| `tenant_scoped_session(org_id)` | `klai-portal/backend/app/core/database.py` | REQ-10, REQ-14 |
| `cross_org_session()` for org_id lookup | `klai-portal/backend/app/core/database.py` | REQ-14 |
| Zitadel `grant_user_role` / `lock_user` | `klai-portal/backend/app/services/zitadel.py` | REQ-5, REQ-12 |
| Structlog ProcessorFormatter audit pattern | `klai-portal/backend/app/services/audit/tenant_lifecycle.py` | REQ-4, REQ-6 |
| Lifespan-registered background task | `klai-portal/backend/app/main.py` lifespan + `bot_poller.py` | REQ-8 |

## 7. Cross-repo dependencies

| REQ | Repo | What |
|---|---|---|
| REQ-17 | klai-infra | Caddyfile `header /bot/* Content-Security-Policy ...` |
| REQ-19 | klai-infra | Docker compose `dns:` config or egress-proxy sidecar for `crawl4ai` |
| REQ-2 | klai-mailer template repo | New email template `widget-allow-any-origin-migration.j2` |

Each cross-repo change SHALL include `SPEC-SEC-CROSS-TENANT-FOLLOWUP-001 REQ-X` in the PR title and body for traceability.

## 8. Required pre-merge verification

For each window, run from `klai-portal/backend`:

```bash
uv run pytest -q --tb=short
uv run ruff check .
uv run ruff format --check .
uv run --with pyright pyright
```

Plus the specific REQ-test files:

```bash
# Window 1 (P0 surgical + REQ-2 default-deny)
uv run pytest -q tests/test_widget_platform_unlock.py tests/test_widget_origin_default_deny.py tests/test_portal_templates_rls_with_check.py
cd ../frontend && npm test -- ActivityTab.test.tsx EmbedTab.test.tsx

# Window 2 (P1 hardening)
uv run pytest -q tests/test_user_deletion_orchestrator.py tests/test_platform_role_change_zitadel_sync.py tests/test_widget_mint_rate_limit.py tests/test_widget_messages_length_cap.py tests/test_widget_messages_retention.py

# Window 3 (P2 defense-in-depth)
uv run pytest -q tests/test_user_suspension_blocks_auth.py tests/test_widget_audit.py tests/test_widget_preview_flagging.py tests/test_widget_soft_delete.py tests/test_slug_guard.py
```

Final full suite before each window's deploy:

```bash
uv run pytest -q --tb=short
alembic heads | wc -l  # MUST return 1 (per alembic-multi-pr-head-split pitfall)
```

## 9. Deployment checklist per window

### Window 1 (P0 surgical + REQ-2 default-deny)

- [ ] REQ-1, REQ-2, REQ-3, REQ-9 tests passing
- [ ] Full test suite green
- [ ] Alembic single-head verified
- [ ] PR includes per-REQ Finding-ID cross-reference
- [ ] **REQ-2 automated CI cohort gate** — GitHub Actions step in `portal-api.yml` runs `scripts/verify_widget_cohort.sh` on every deploy; the step output (cohort SQL result) is captured in the workflow log. If the gate exits non-zero (impacted_widgets > 5 OR external tenant appears), the deploy is blocked and REQ-2 is split into a follow-up SPEC with the 7-day comm protocol re-introduced.
- [ ] Staging smoke-test: 404 on widget-config for widgets-locked tenant (REQ-1); origin from non-allowed source rejected (REQ-2)
- [ ] Production deploy + VictoriaLogs check for unexpected 403/404 spike
- [ ] Post-deploy: `psql ... -c "SELECT polqual FROM pg_policy WHERE polname = 'tenant_isolation' AND polrelid = 'portal_templates'::regclass"` shows WITH CHECK clause (REQ-3)
- [ ] Post-deploy: VictoriaLogs query for `widget.allow_any_origin_migrated` count matches expected (REQ-2 data-migration)

### Window 2 (P1 hardening)

- [ ] REQ-4 hot-cut deployment (no feature flag) — `deprovisioning_orchestrator` pattern is proven and user-delete frequency is low (<5/week at Klai scale); revert via git is the rollback plan if the new orchestrator misbehaves
- [ ] All REQs in this window deployed directly
- [ ] Post-deploy: 48h monitoring of `platform_admin.user_delete_partial_failure` audit-event count via Grafana product_events

### Window 3 (P2 defense-in-depth)

- [ ] All tests green
- [ ] klai-infra PRs for REQ-17 + REQ-19 merged and verified
- [ ] Final deploy

## 10. Resolved decisions (replaces open-questions list)

All 5 originally-open questions resolved by orchestrator on 2026-05-24 after production-data check + Klai-pattern review.

**1. REQ-2 communication-window — RESOLVED: dropped (automated CI cohort gate instead).**

Production cohort verified at 2 widgets, both in Klai's own `getklai` tenant (0 external customers impacted). Cohort query:

```sql
SELECT COUNT(*) FILTER (
  WHERE jsonb_array_length(COALESCE(widget_config->'allowed_origins', '[]'::jsonb)) = 0
    AND public_share_enabled = false
) AS impacted_widgets,
COUNT(DISTINCT org_id) FILTER (
  WHERE jsonb_array_length(COALESCE(widget_config->'allowed_origins', '[]'::jsonb)) = 0
    AND public_share_enabled = false
) AS impacted_tenants
FROM widgets;
-- Result on 2026-05-24: impacted_widgets=2, impacted_tenants=1 (getklai)
```

The deploy pipeline runs `scripts/verify_widget_cohort.sh` as a GitHub Actions step in `portal-api.yml` before applying the post-deploy SQL. The script SSHes to core-01, runs the cohort query, and exits non-zero if `impacted_widgets > 5` OR `impacted_tenants` includes any slug outside `{platform_org_slug}`. On non-zero exit the deploy is blocked and REQ-2 is split into a follow-up SPEC with the standard 7-day comm protocol re-introduced. No human pre-merge inspection — the migration's automated branches handle the safe defaults per row. Mailer-template + in-app banner originally scoped under REQ-2.T6 remain dropped.

**2. REQ-4 feature-flag vs hot-cut — RESOLVED: hot-cut.**

`deprovisioning_orchestrator` pattern is proven and in production. User-delete frequency at Klai scale is <5/week. Feature-flag adds parallel-code-path complexity without real rollback safety; revert via git is the rollback plan. Post-deploy: 48h monitoring of `platform_admin.user_delete_partial_failure` audit-event count via Grafana product_events.

**3. REQ-17 / REQ-19 cross-repo coupling — RESOLVED: decoupled.**

SPEC closes when all klai-portal code REQs land. REQ-17 (Caddy CSP) and REQ-19 (crawl4ai DNS) tracked as `cross_repo_dependency: klai-infra` with explicit PR-link cross-reference in this SPEC's PR description. SPEC deliverable status does NOT block on klai-infra merge.

**4. REQ-12 inline vs Redis cache — RESOLVED: inline.**

`_resolve_caller_with_options` already SELECTs the portal_users row per request; adding `status` column check is 0 extra DB-roundtrips. Redis cache introduces invalidation edge-cases for a usecase with at most a handful of suspended users at Klai scale. Per `minimal-changes` pitfall.

**5. REQ-8 retention default — RESOLVED: 90d + env-var.**

No unified Klai retention policy found in code. 90d defensible per GDPR legitimate interest (longer than billing cycle, shorter than enterprise-compliance bar). `WIDGET_MESSAGES_RETENTION_DAYS` env-var allows future tuning per tenant/legal review.

## 11. Monitoring (post-deploy)

After each window's rollout, monitor via VictoriaLogs MCP and Grafana MCP:

| REQ | Metric | Tool |
|---|---|---|
| REQ-1 | `service:portal-api AND status_code:404 AND endpoint:widget-config` | VictoriaLogs MCP |
| REQ-2 | `widget.allow_any_origin_migrated` audit event count | Grafana (product_events) |
| REQ-4 | `platform_admin.user_delete_partial_failure` audit count | Grafana (product_events) |
| REQ-5 | `platform_admin.role_change_zitadel_desync` audit count | Grafana (product_events) |
| REQ-7 | `service:portal-api AND status_code:429 AND endpoint:widget-config` | VictoriaLogs MCP |
| REQ-8 | `widget_messages.retention_deleted` event count + `deleted_count` | Grafana (product_events) |
| REQ-12 | `platform_admin.suspend_zitadel_desync` audit count | Grafana (product_events) |

## 12. Definition of done

Per spec.md § 13. This plan is complete when:

- All 19 REQ tasks have a passing test (RED), passing code (GREEN), and a REFACTOR pass.
- All quality-gate commands exit 0.
- All 4 windows deployed to production without rollback.
- klai-infra PRs for REQ-17, REQ-19 merged.
- Post-deploy monitoring shows no anomalous error spikes.

End of plan.md.
