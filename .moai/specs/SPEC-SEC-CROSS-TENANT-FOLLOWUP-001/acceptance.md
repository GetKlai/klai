# SPEC-SEC-CROSS-TENANT-FOLLOWUP-001 — Acceptance Criteria

Status: draft
Created: 2026-05-24
Format: Given / When / Then per scenario.
Coverage: at least 2 scenarios per REQ (happy-path + edge-case / negative-case).

## Module 1 — Public Widget Hardening

### AC1 — REQ-1 (Finding B-1): assert_platform_unlocked on partner endpoints

**Scenario AC1.1 (happy path):**
- GIVEN a tenant T1 with `widgets` in `enabled_addons` AND a widget `wgt_X` belonging to T1
- WHEN a caller hits `GET /partner/v1/widget-config?id=wgt_X` from any origin in T1's `allowed_origins`
- THEN the response status is 200 AND the response body contains a valid `session_token`

**Scenario AC1.2 (platform-locked denial):**
- GIVEN a tenant T2 WITHOUT `widgets` in `enabled_addons` AND a widget `wgt_Y` belonging to T2
- WHEN a caller hits `GET /partner/v1/widget-config?id=wgt_Y`
- THEN the response status is 404 (existence-non-disclosure) AND no `session_token` is generated

**Scenario AC1.3 (public-bot-config locked denial):**
- GIVEN a tenant T3 with `public_share_enabled=true` on widget `wgt_Z` BUT WITHOUT `widgets` in `enabled_addons`
- WHEN a caller hits `GET /partner/v1/public-bot-config?id=wgt_Z`
- THEN the response status is 404

**Scenario AC1.4 (chat-path post-JWT denial):**
- GIVEN a tenant T4 with `widgets` initially in `enabled_addons`, a widget `wgt_W`, and a previously-minted valid JWT
- WHEN platform-admin removes `widgets` from T4's `enabled_addons`
- AND a caller POSTs to `/partner/v1/chat/completions` with the previously-valid JWT
- THEN the response status is 403 `{"error_code": "feature_not_unlocked", "feature": "widgets"}`

Test file: `klai-portal/backend/tests/test_widget_platform_unlock.py`

### AC2 — REQ-2 (Finding B-2): default-deny origins (BREAKING)

**Scenario AC2.1 (legacy migration — public_share enabled):**
- GIVEN a pre-migration widget with `widget_config->>'allowed_origins'` empty AND `public_share_enabled=true`
- WHEN the data-migration runs
- THEN `allow_any_origin` is set to `true` AND an audit event `widget.allow_any_origin_migrated` with `reason=public_share_enabled` is emitted

**Scenario AC2.2 (legacy migration — non-public widget):**
- GIVEN a pre-migration widget with empty `allowed_origins` AND `public_share_enabled=false` belonging to tenant slug `acme`
- WHEN the data-migration runs
- THEN `allowed_origins` is set to `["https://acme.getklai.com"]` AND an audit event `widget.allow_any_origin_migrated` with `reason=tenant_subdomain_default` is emitted

**Scenario AC2.3 (post-migration origin enforcement):**
- GIVEN a widget with `allow_any_origin=false` AND `allowed_origins=["https://tenant.getklai.com"]`
- WHEN a caller hits `/partner/v1/chat/completions` from origin `https://random-site.com`
- THEN the response status is 403

**Scenario AC2.4 (post-migration explicit open mode):**
- GIVEN a widget with `allow_any_origin=true`
- WHEN a caller hits `/partner/v1/chat/completions` from origin `https://anywhere.example`
- THEN the response status is 200

**Scenario AC2.5 (admin UI new-widget default):**
- GIVEN an admin in tenant `acme` POSTing `POST /api/widgets` without specifying `allowed_origins`
- WHEN the widget is created
- THEN the persisted `allowed_origins` is `["https://acme.getklai.com"]` AND `allow_any_origin` is `false`

**Scenario AC2.6 (admin UI toggle "allow any origin"):**
- GIVEN an admin editing widget `wgt_X` who clicks the "Allow all origins" toggle
- WHEN the PATCH request is sent
- THEN the persisted `allow_any_origin` is `true` AND the UI displays the warning copy "Conversations from any website will be attributed to this widget — only enable for public chatbots."

**Scenario AC2.7 (loaded_origin audit-trail):**
- GIVEN a widget that receives a chat turn from origin `https://embed.customer.com`
- WHEN `record_widget_turn` writes the conversation row
- THEN `widget_conversations.loaded_origin = 'https://embed.customer.com'`

Test file: `klai-portal/backend/tests/test_widget_origin_default_deny.py` + frontend `EmbedTab.test.tsx`

### AC7 — REQ-7 (Finding B-4): per-widget mint rate-limit

**Scenario AC7.1 (within limit):**
- GIVEN no prior requests to mint a token for widget `wgt_X` in the last 60 seconds
- WHEN a caller hits `GET /partner/v1/widget-config?id=wgt_X` 10 times in 1 minute
- THEN all 10 responses are 200

**Scenario AC7.2 (over limit):**
- GIVEN 10 prior successful mints for widget `wgt_X` in the last 60 seconds
- WHEN the 11th caller hits `GET /partner/v1/widget-config?id=wgt_X`
- THEN the response status is 429 AND the response includes header `Retry-After: <seconds>`

**Scenario AC7.3 (separate widgets isolated):**
- GIVEN 10 prior mints for widget `wgt_X` (at limit) AND 0 prior mints for widget `wgt_Y`
- WHEN a caller hits `GET /partner/v1/widget-config?id=wgt_Y`
- THEN the response status is 200

Test file: `klai-portal/backend/tests/test_widget_mint_rate_limit.py`

### AC8 — REQ-8 (Finding B-5): length-cap + retention

**Scenario AC8.1 (content clamping):**
- GIVEN a `record_widget_turn` call with `content` of length 15000
- WHEN the row is inserted
- THEN `widget_messages.content` is stored with length exactly 10000

**Scenario AC8.2 (DB-level constraint):**
- GIVEN a direct SQL INSERT bypassing application clamping
- WHEN the SQL inserts a row with `LENGTH(content) = 10001`
- THEN PostgreSQL raises ERRCODE 23514 (check_violation)

**Scenario AC8.3 (retention worker):**
- GIVEN `widget_messages` rows with `created_at` ages 30d, 60d, 100d, 200d
- WHEN the retention worker runs with `WIDGET_MESSAGES_RETENTION_DAYS=90`
- THEN the 100d-old and 200d-old rows are deleted AND the 30d-old and 60d-old rows remain AND an audit event `widget_messages.retention_deleted` with `deleted_count=2` is emitted

Test file: `klai-portal/backend/tests/test_widget_messages_length_cap.py` + `test_widget_messages_retention.py`

## Module 2 — Platform-Admin Destructive Operations

### AC4 — REQ-4 (Finding A-2): user-deletion state machine

**Scenario AC4.1 (happy path — order):**
- GIVEN a target user U in tenant T with `deletion_status=NULL`
- WHEN platform-admin DELETEs the user via the orchestrator
- THEN the steps execute in order: Zitadel-remove → external-KB-delete → portal-DB-delete
- AND `deletion_status` ends as NULL (row gone) AND audit event `platform_admin.user_deleted` is emitted

**Scenario AC4.2 (partial failure — Zitadel 5xx):**
- GIVEN Zitadel returns 502 on `remove_user`
- WHEN the orchestrator runs step 1 (zitadel-remove)
- THEN `portal_users.deletion_status = 'failed_partial'` AND `failure_reason` JSONB contains the Zitadel error AND `last_attempted_step = 'zitadel_remove'`
- AND audit event `platform_admin.user_delete_partial_failure` with `step='zitadel_remove'` and `zitadel_identity_deleted=false` is emitted

**Scenario AC4.3 (partial failure — external KB delete):**
- GIVEN Zitadel succeeds BUT knowledge-ingest returns 503 on `delete_kb`
- WHEN the orchestrator runs
- THEN `last_attempted_step = 'external_kb_delete'` AND `zitadel_identity_deleted=true` in the audit details

**Scenario AC4.4 (idempotent retry):**
- GIVEN a user with `deletion_status='failed_partial'` AND `last_attempted_step='external_kb_delete'`
- WHEN an operator hits `POST /api/admin/platform/users/{zitadel_user_id}/retry-delete`
- THEN the orchestrator restarts from step 1; step 1 detects "Zitadel already removed" and skips; step 2 retries the external KB deletes
- AND on success, the row is deleted

Test file: `klai-portal/backend/tests/test_user_deletion_orchestrator.py`

### AC5 — REQ-5 (Finding A-4): Zitadel role-grant sync

**Scenario AC5.1 (promotion):**
- GIVEN a user with `portal_users.role='company'` AND no `org:owner` Zitadel grant
- WHEN platform-admin PATCHes the role to `admin`
- THEN the DB commit succeeds AND `zitadel.grant_user_role(role_key='org:owner')` is called once

**Scenario AC5.2 (demotion):**
- GIVEN a user with `portal_users.role='admin'` AND an `org:owner` Zitadel grant
- WHEN platform-admin PATCHes the role to `company`
- THEN the DB commit succeeds AND the Zitadel "remove grant" API is called once

**Scenario AC5.3 (Zitadel failure — no DB rollback):**
- GIVEN a role change from `company` to `admin` AND Zitadel returns 502 on the grant call
- WHEN the handler runs
- THEN `portal_users.role='admin'` is committed (NOT rolled back) AND audit event `platform_admin.role_change_zitadel_desync` is emitted AND the response surfaces `zitadel_sync_failed=true`

Test file: `klai-portal/backend/tests/test_platform_role_change_zitadel_sync.py`

### AC6 — REQ-6 (Finding A-7): audit failed Zitadel paths

**Scenario AC6.1 (invite Zitadel-failure audit):**
- GIVEN Zitadel returns 502 on `invite_user` during `platform_invite`
- WHEN the handler runs
- THEN an audit event `platform_admin.invite_zitadel_invite_failed` with `target_email`, `target_org_id`, `error` (200-char truncated) is emitted before the HTTPException is raised

**Scenario AC6.2 (create-tenant role-grant failure audit):**
- GIVEN `platform_create_tenant` succeeds at user creation but Zitadel returns 502 on `grant_user_role`
- WHEN the handler runs
- THEN audit event `platform_admin.create_tenant_grant_role_failed` is emitted

**Scenario AC6.3 (audit-emit failure fallback):**
- GIVEN the audit-emit call itself raises (DB session aborted)
- WHEN the handler runs
- THEN a structlog `platform_admin_audit_emit_failed` exception-event is logged with the original audit-event details

Test file: `klai-portal/backend/tests/test_platform_admin_manage.py` (extended)

### AC10 — REQ-10 (Finding A-3): tenant_scoped_session for create-tenant

**Scenario AC10.1 (refactor verification):**
- GIVEN `platform_create_tenant` is called
- WHEN the new owner-user INSERT runs
- THEN the INSERT is issued under a `tenant_scoped_session(org_row.id)` context (verifiable via SQLAlchemy event listener or session-tracker)
- AND the request-scoped session (from `Depends(get_db)`) is NOT mutated with `set_tenant`

**Scenario AC10.2 (request-scoped session left at NULL GUC):**
- GIVEN `platform_create_tenant` completes successfully
- WHEN the request handler returns
- THEN `current_setting('app.current_org_id', true)` on the request-scoped session is empty/NULL

Test file: `klai-portal/backend/tests/test_platform_admin_manage.py` (extended)

### AC11 — REQ-11 (Finding A-5): partial-failure audit semantics

**Scenario AC11.1 (audit details completeness):**
- GIVEN the orchestrator from REQ-4 hits a partial failure
- WHEN audit event `platform_admin.user_delete_partial_failure` is emitted
- THEN the `details` payload contains all of: `attempted_step`, `kbs_deleted_externally`, `api_keys_revoked`, `mcp_tokens_revoked`, `zitadel_identity_deleted`, `db_user_deleted`

**Scenario AC11.2 (no orphaned audit on success):**
- GIVEN the orchestrator succeeds
- WHEN the operation completes
- THEN ONLY `platform_admin.user_deleted` is in the audit log (NOT both deleted and partial-failure events)

Test file: bundled with AC4 in `klai-portal/backend/tests/test_user_deletion_orchestrator.py`

## Module 3 — Admin AuthZ Tightening

### AC12 — REQ-12 (Finding A-6): suspended users blocked

**Scenario AC12.1 (suspended-user auth rejected):**
- GIVEN a user U with `portal_users.status='suspended'` AND a valid OIDC session
- WHEN U hits any authenticated endpoint
- THEN the response status is 403 `{"error_code": "user_suspended"}` (NOT 401 — distinguishes from invalid-token)

**Scenario AC12.2 (suspend triggers Zitadel lock):**
- GIVEN a user U with `status='active'`
- WHEN platform-admin POSTs `/api/admin/platform/.../suspend` for U
- THEN `portal_users.status='suspended'` is committed AND `zitadel.lock_user(user_id=U.zitadel_user_id)` is called once

**Scenario AC12.3 (reactivate triggers Zitadel unlock):**
- GIVEN a user U with `status='suspended'`
- WHEN platform-admin POSTs `/api/admin/platform/.../reactivate`
- THEN `portal_users.status='active'` is committed AND `zitadel.unlock_user(...)` is called once

**Scenario AC12.4 (Zitadel lock failure surfaces in audit):**
- GIVEN Zitadel returns 502 on `lock_user`
- WHEN platform_suspend completes its DB commit
- THEN audit event `platform_admin.suspend_zitadel_desync` is emitted AND the response surfaces `zitadel_sync_failed=true`

Test file: `klai-portal/backend/tests/test_user_suspension_blocks_auth.py`

### AC13 — REQ-13 (Finding B-6): admin activity endpoints platform-unlocked

**Scenario AC13.1 (conversations endpoint denied when locked):**
- GIVEN tenant T WITHOUT `widgets` in `enabled_addons` AND a pre-existing widget `wgt_X`
- WHEN an admin of T hits `GET /api/widgets/wgt_X/conversations`
- THEN the response status is 403 `{"error_code": "feature_not_unlocked", "feature": "widgets"}`

**Scenario AC13.2 (conversations endpoint allowed when unlocked):**
- GIVEN tenant T WITH `widgets` in `enabled_addons` AND a widget `wgt_X`
- WHEN an admin of T hits `GET /api/widgets/wgt_X/conversations`
- THEN the response status is 200

**Scenario AC13.3 (stats endpoint denied when locked):**
- GIVEN tenant T WITHOUT `widgets` in `enabled_addons`
- WHEN an admin of T hits `GET /api/widgets/wgt_X/stats`
- THEN the response status is 403

Test file: `klai-portal/backend/tests/test_admin_widgets.py` (extended)

## Module 4 — Audit-Trail Integrity

### AC9 — REQ-9 (Finding B-9): scheme-allowlist on rendered URLs

**Scenario AC9.1 (javascript: rendered as plain text):**
- GIVEN a conversation message with `sources=[{"url": "javascript:alert(document.cookie)"}]`
- WHEN ActivityTab.tsx renders the source
- THEN the rendered DOM contains the URL as plain text AND does NOT contain an `<a href="javascript:...">` element

**Scenario AC9.2 (https: rendered as anchor):**
- GIVEN a conversation message with `sources=[{"url": "https://example.com/doc"}]`
- WHEN ActivityTab.tsx renders the source
- THEN the rendered DOM contains `<a href="https://example.com/doc" target="_blank" rel="noopener noreferrer">`

**Scenario AC9.3 (data:, file:, vbscript: all rejected):**
- GIVEN a conversation with sources `[{"url": "data:text/html,<script>alert(1)</script>"}, {"url": "file:///etc/passwd"}, {"url": "vbscript:alert(1)"}]`
- WHEN ActivityTab.tsx renders
- THEN none of these render as an `<a href>` element

Test file: `klai-portal/frontend/src/routes/admin/widgets/_components/tabs/__tests__/ActivityTab.test.tsx`

### AC14 — REQ-14 (Finding B-7): server-side org_id derivation

**Scenario AC14.1 (caller cannot influence org_id):**
- GIVEN a widget `wgt_X` belonging to tenant T1 (id=42)
- WHEN any caller invokes `record_widget_turn(widget_id='wgt_X', ...)` (with any value or no value for org_id)
- THEN the inserted `widget_conversations.org_id = 42` (derived from the widget row) regardless of caller-supplied value

**Scenario AC14.2 (signature change enforced):**
- GIVEN the post-refactor codebase
- WHEN any caller passes `org_id` as a positional or keyword argument to `record_widget_turn`
- THEN Python raises `TypeError` (signature no longer accepts the parameter)

Test file: `klai-portal/backend/tests/test_widget_audit.py` (extended)

### AC15 — REQ-15 (Finding B-11): preview-session flagging

**Scenario AC15.1 (preview JWT carries claim):**
- GIVEN an admin calls `widget_preview_session` for widget `wgt_X`
- WHEN the JWT is minted
- THEN the decoded payload contains `is_preview: true`

**Scenario AC15.2 (preview conversation flagged):**
- GIVEN a chat turn handled with a preview JWT
- WHEN `record_widget_turn` writes the row
- THEN `widget_conversations.is_preview = true`

**Scenario AC15.3 (stats exclude preview):**
- GIVEN 5 preview conversations AND 20 real-visitor conversations on `wgt_X`
- WHEN admin requests `GET /api/widgets/wgt_X/stats?period=30d`
- THEN the `conversation_count` is 20 (preview rows excluded)

Test file: `klai-portal/backend/tests/test_widget_preview_flagging.py`

### AC16 — REQ-16 (Finding B-14): widget soft-delete + audit preserve

**Scenario AC16.1 (soft-delete sets timestamp):**
- GIVEN a widget `wgt_X` not yet deleted
- WHEN an admin DELETEs `wgt_X`
- THEN `widgets.deleted_at IS NOT NULL` AND the row is NOT physically removed

**Scenario AC16.2 (soft-deleted widget invisible to reads):**
- GIVEN a widget `wgt_X` with `deleted_at IS NOT NULL`
- WHEN an admin GETs `/api/widgets/wgt_X`
- THEN the response status is 404

**Scenario AC16.3 (conversations preserved post-soft-delete):**
- GIVEN a widget `wgt_X` with 10 conversation rows AND `wgt_X` is soft-deleted
- WHEN an admin queries the audit-trail
- THEN the 10 conversation rows still exist in `widget_conversations` (audit-preserve)

**Scenario AC16.4 (CASCADE removed at schema level):**
- GIVEN the post-migration schema
- WHEN `information_schema.referential_constraints` is queried for `widget_conversations_widget_id_fkey`
- THEN `delete_rule = 'NO ACTION'` (NOT `CASCADE`)

Test file: `klai-portal/backend/tests/test_widget_soft_delete.py`

## Module 5 — Infrastructure & Cross-Cutting

### AC3 — REQ-3 (Finding C-1): portal_templates WITH CHECK

**Scenario AC3.1 (cross-org INSERT rejected):**
- GIVEN a `cross_org_session()` context with `app.cross_org_admin=true`
- WHEN code executes `INSERT INTO portal_templates (org_id, name, ...) VALUES (1, 'x', ...)`
- THEN PostgreSQL raises an error (ERRCODE `42501` insufficient_privilege or `23514` check_violation depending on the path)

**Scenario AC3.2 (cross-org READ still works):**
- GIVEN a `cross_org_session()` context
- WHEN code executes `SELECT * FROM portal_templates`
- THEN rows from all tenants are returned (USING `_rls_current_org_id() IS NULL` branch passes)

**Scenario AC3.3 (per-tenant write still works):**
- GIVEN a `tenant_scoped_session(org_id=42)` context
- WHEN code executes `INSERT INTO portal_templates (org_id, name, ...) VALUES (42, 'y', ...)`
- THEN the INSERT succeeds (WITH CHECK passes for `org_id = current_org_id`)

**Scenario AC3.4 (per-tenant cross-org write rejected):**
- GIVEN a `tenant_scoped_session(org_id=42)` context
- WHEN code executes `INSERT INTO portal_templates (org_id, name, ...) VALUES (99, 'z', ...)`
- THEN the INSERT is rejected by WITH CHECK (ERRCODE 23514)

Test file: `klai-portal/backend/tests/test_portal_templates_rls_with_check.py`

### AC17 — REQ-17 (Finding B-19): CSP frame-ancestors on /bot/*

**Scenario AC17.1 (CSP header present):**
- GIVEN the klai-infra Caddy config is deployed
- WHEN `curl -sI https://my.getklai.com/bot/<any-widget-id>` runs
- THEN the response includes header `Content-Security-Policy: frame-ancestors 'none'`

**Scenario AC17.2 (iframe attempt blocked by browser):**
- GIVEN an attacker page with `<iframe src="https://my.getklai.com/bot/wgt_X">`
- WHEN a browser renders the attacker page
- THEN the iframe is blocked AND the browser logs a CSP violation

Verification: klai-infra PR + manual curl smoke-test (no Python test).

### AC18 — REQ-18 (Finding C-3): slug validation everywhere

**Scenario AC18.1 (safe slug passes):**
- GIVEN the slug `acme-corp`
- WHEN `_assert_safe_slug('acme-corp')` is called
- THEN the function returns without raising

**Scenario AC18.2 (path-traversal rejected):**
- GIVEN the slug `../etc-passwd`
- WHEN `_assert_safe_slug` is called
- THEN `ValueError("slug failed safe-slug validation: ../etc-passwd")` is raised

**Scenario AC18.3 (provisioning function refuses unsafe slug):**
- GIVEN `_start_librechat_container(slug='bad slug with spaces')` is called
- WHEN the function executes its first statement
- THEN `ValueError` is raised AND no Docker container is started

**Scenario AC18.4 (DB CHECK CONSTRAINT enforces invariant):**
- GIVEN a direct SQL `INSERT INTO portal_orgs (slug, ...) VALUES ('bad slug', ...)`
- WHEN the INSERT runs
- THEN PostgreSQL raises ERRCODE 23514 (check_violation) for `chk_portal_orgs_slug_safe`

Test file: `klai-portal/backend/tests/test_slug_guard.py`

### AC19 — REQ-19 (Finding C-4): crawl4ai DNS refusal

**Scenario AC19.1 (RFC1918 resolve refused):**
- GIVEN the klai-infra DNS config or sidecar proxy is in place
- WHEN crawl4ai attempts to resolve a hostname that returns `10.0.0.5`
- THEN the resolution fails OR the connection times out

**Scenario AC19.2 (public IP resolve succeeds):**
- GIVEN the same config
- WHEN crawl4ai resolves `example.com` (public IP)
- THEN the resolution succeeds AND the HTTP request proceeds

Verification: klai-infra PR + manual smoke-test per `docker-socket-proxy.md` SSRF-isolation script.

---

## Quality gate criteria (per Trust 5)

These criteria gate the merge of EACH window's PR, not the entire SPEC:

| Gate | Threshold | Tool |
|---|---|---|
| Test coverage on new code | >= 85% | `pytest --cov` |
| Linting | exit 0 | `ruff check .` |
| Formatting | exit 0 | `ruff format --check .` |
| Type checking | exit 0 | `pyright` |
| Alembic head count | == 1 | `alembic heads \| wc -l` |
| Security review | PASS for REQ-1, REQ-2, REQ-3 | `Skill("security-review")` |
| Frontend lint + tests | exit 0 | `npm run lint && npm test` |
| MX tags | At minimum 1 @MX:ANCHOR + 1 @MX:NOTE per new file with public functions | `moai mx scan` |

---

## Definition of Done (cross-reference to spec.md § 13)

Each REQ is "done" when:
1. Its acceptance scenarios all pass.
2. Its quality gates all pass.
3. Its commit message links to the Finding-ID and SPEC-ID.
4. It is deployed to production AND post-deploy monitoring (per plan.md § 11) shows no anomalous error spike for 24 hours.

End of acceptance.md.
