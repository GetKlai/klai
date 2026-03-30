# SPEC-SEC-001: NEN 7510 Security Hardening (R2 + R3)

| Field      | Value                                    |
|------------|------------------------------------------|
| SPEC-ID    | SPEC-SEC-001                             |
| Created    | 2026-03-27                               |
| Completed  | 2026-03-27                               |
| Status     | Done                                     |
| Priority   | High                                     |
| Domain     | Security / Compliance                    |
| Releases   | R2 (backend code), R3 (DB + infra)       |

## Summary

This SPEC covers the remaining NEN 7510 security fixes for the Klai portal, split across two releases. R2 addresses backend code changes in `klai-portal/backend/`: audit logging for authentication events, MFA policy enforcement at login, and middleware ordering. R3 addresses database migrations (append-only audit log, RLS on audit table) and infrastructure (automated PostgreSQL backups with offsite storage). The P0 fix (FalkorDB port binding) is already completed and out of scope.

## Background / Motivation

Klai processes sensitive data for healthcare-adjacent organizations in the Netherlands. NEN 7510 (the Dutch information security standard for healthcare) requires:

- **Auditability**: All authentication events (login, logout, failed attempts) must be logged in an immutable audit trail.
- **Access control enforcement**: Multi-factor authentication policies set by the organization must be enforced at the application layer, not merely suggested.
- **Data isolation**: Audit logs must be tenant-isolated and append-only to prevent tampering.
- **Backup and recovery**: Automated backups with offsite storage and defined retention.
- **Observability**: Structured logging must include tenant and user context for incident investigation.

A security review identified six fixes. The P0 item (FalkorDB port exposure) is already resolved. The remaining six are split into R2 (pure backend code, no schema changes, deployable immediately) and R3 (requires database migrations and infrastructure provisioning, coordinated deployment).

---

## R2 -- Backend Code Changes (Higher Priority)

### Fix 1: Login/Logout Audit Logging

**Problem Statement**

Authentication events are currently sent to analytics only via `emit_event("login", ...)` in `klai-portal/backend/app/api/auth.py`. The `audit.log_event()` function exists in `klai-portal/backend/app/services/audit.py` but is not called from any authentication endpoint. NEN 7510 requires an immutable audit trail of all authentication events.

**Current State**

- `auth.py` imports `emit_event` from `app.services.events` but does not import `audit.py`.
- `log_event()` signature: `async def log_event(db, org_id, actor, action, resource_type, resource_id, details)`.
- The `login` endpoint already has `db: AsyncSession = Depends(get_db)`.
- The `logout` endpoint has no `db` dependency.
- `log_event()` uses `flush()` and swallows exceptions (non-fatal), so it will not break business operations.

**Requirements**

- REQ-SEC-001-01: WHEN a user successfully authenticates via password, THE SYSTEM SHALL write an audit log entry with action `auth.login`, resource_type `session`, and details including `{"method": "password"}`.

- REQ-SEC-001-02: WHEN a user successfully authenticates via TOTP (second factor), THE SYSTEM SHALL write an audit log entry with action `auth.login.totp`, resource_type `session`, and details including `{"method": "totp"}`.

- REQ-SEC-001-03: WHEN a user logs out, THE SYSTEM SHALL write an audit log entry with action `auth.logout`, resource_type `session`.

- REQ-SEC-001-04: WHEN a password authentication attempt fails (HTTP 401 raised), THE SYSTEM SHALL write an audit log entry with action `auth.login.failed`, resource_type `session`, and details including `{"reason": "invalid_credentials"}`.

- REQ-SEC-001-05: WHEN a TOTP verification attempt fails (HTTP 400 raised), THE SYSTEM SHALL write an audit log entry with action `auth.totp.failed`, resource_type `session`, and details including `{"reason": "invalid_code"}`.

- REQ-SEC-001-06: WHERE the logout endpoint requires a database session for audit logging, THE SYSTEM SHALL add `db: AsyncSession = Depends(get_db)` as a dependency to the logout endpoint.

- REQ-SEC-001-07: WHERE the user's org_id is not resolvable (e.g., failed login for unknown email), THE SYSTEM SHALL use `org_id=0` as a sentinel value in the audit log entry.

**Acceptance Criteria**

- After a successful password login, `portal_audit_log` contains a row with `action='auth.login'` and the correct `actor_user_id`.
- After a successful TOTP login, `portal_audit_log` contains a row with `action='auth.login.totp'`.
- After logout, `portal_audit_log` contains a row with `action='auth.logout'`.
- After a failed password attempt, `portal_audit_log` contains a row with `action='auth.login.failed'`.
- After a failed TOTP attempt, `portal_audit_log` contains a row with `action='auth.totp.failed'`.
- Audit log failures do not cause the authentication request to fail (non-fatal, per existing `log_event` behavior).

---

### Fix 2: MFA Policy Backend Enforcement

**Problem Statement**

The `portal_orgs` table has an `mfa_policy` column with values `"optional"`, `"recommended"`, or `"required"` (default: `"optional"`). The login endpoint in `auth.py` checks `has_totp` (whether the user has TOTP set up) but never reads the org's `mfa_policy`. When an org sets `mfa_policy = "required"`, users without any second factor (no TOTP, no passkey) can still log in with only a password.

**Current State**

- `auth.py` line 308: `has_totp = await zitadel.has_totp(zitadel_user_id, org_id)` -- checks user capability, not org policy.
- `portal_orgs.mfa_policy` is already exposed in the admin settings API (`app/api/admin.py` lines 374-391).
- The query path to resolve policy: `zitadel_user_id` -> `portal_users.zitadel_user_id` -> `portal_users.org_id` -> `portal_orgs.mfa_policy`.
- `zitadel.has_totp()` already returns `bool`. A passkey check requires `zitadel.list_passkeys()` or equivalent.

**Requirements**

- REQ-SEC-001-08: WHEN a user successfully authenticates with password AND the user's organization has `mfa_policy = "required"` AND the user has no TOTP registered AND the user has no passkey registered, THE SYSTEM SHALL reject the login with HTTP 403 and error detail `"MFA required by your organization. Please set up two-factor authentication."`.

- REQ-SEC-001-09: WHEN `mfa_policy` is `"optional"` or `"recommended"`, THE SYSTEM SHALL NOT enforce MFA at login (existing behavior preserved).

- REQ-SEC-001-10: WHEN the org lookup fails or the user has no portal membership, THE SYSTEM SHALL default to `mfa_policy = "optional"` (fail-open for authentication, logged as warning).

- REQ-SEC-001-11: WHERE the system checks for passkey registration, THE SYSTEM SHALL call `zitadel.list_passkeys(user_id)` (or equivalent) and treat a non-empty result as "has passkey".

**Acceptance Criteria**

- A user in an org with `mfa_policy = "required"` who has no TOTP and no passkey receives HTTP 403 on password login.
- A user in an org with `mfa_policy = "required"` who has TOTP set up proceeds to the TOTP challenge as before.
- A user in an org with `mfa_policy = "required"` who has a passkey registered proceeds to login (passkey counts as enrolled MFA factor).
- A user in an org with `mfa_policy = "optional"` or `"recommended"` logs in normally with password only.
- If the org/portal_user lookup fails, login proceeds as if `mfa_policy = "optional"`.

---

### Fix 3: LoggingContextMiddleware Ordering

**Problem Statement**

`LoggingContextMiddleware` in `klai-portal/backend/app/middleware/logging_context.py` reads `request.state.org_id` and `request.state.user_id` BEFORE calling `call_next()`. FastAPI route dependencies (which set `request.state` via `_get_caller_org()`) run DURING `call_next()`. This means `org_id` and `user_id` are always `None` when bound to structlog context, making all log entries missing tenant/user context.

**Current State**

- Lines 20-25: `org_id = getattr(request.state, "org_id", None)` and the structlog binding happen before `response = await call_next(request)` on line 27.
- structlog context variables are per-asyncio-task, so binding after `call_next()` still enriches the same request's log records emitted during the response phase.

**Requirements**

- REQ-SEC-001-12: THE SYSTEM SHALL bind `org_id` and `user_id` to the structlog context AFTER `call_next()` has executed, so that route dependencies have had the opportunity to populate `request.state`.

- REQ-SEC-001-13: THE SYSTEM SHALL always bind `request_id` to the structlog context BEFORE `call_next()` (existing behavior preserved).

**Acceptance Criteria**

- Log entries emitted during request processing include `org_id` and `user_id` when the request has valid authentication.
- `request_id` continues to appear in all log entries.
- The middleware does not raise exceptions if `request.state.org_id` or `request.state.user_id` are not set (unauthenticated routes).

---

## R3 -- Database Migrations + Infrastructure (Lower Priority)

### Fix 4: Audit Log Append-Only Enforcement

**Problem Statement**

The `portal_audit_log` table is designed to be append-only, but this is only enforced by application convention (the `audit.py` module docstring says "No UPDATE or DELETE operations"). There is no database-level protection against accidental or malicious modification of audit records. NEN 7510 requires immutable audit trails.

**Requirements**

- REQ-SEC-001-14: THE SYSTEM SHALL enforce append-only behavior on `portal_audit_log` at the PostgreSQL level by creating RULEs that silently discard UPDATE and DELETE operations.

- REQ-SEC-001-15: WHERE the append-only enforcement is implemented, THE SYSTEM SHALL use a new Alembic migration that depends on `v2w3x4y5z6a7_add_audit_log.py`.

**Implementation Guidance**

```sql
CREATE RULE no_update_audit AS ON UPDATE TO portal_audit_log DO INSTEAD NOTHING;
CREATE RULE no_delete_audit AS ON DELETE TO portal_audit_log DO INSTEAD NOTHING;
```

PostgreSQL RULEs are preferred over triggers here: simpler (no function needed), lower overhead, and the intent is to silently discard (not raise errors).

**Acceptance Criteria**

- `UPDATE portal_audit_log SET action='tampered' WHERE id=1` executes without error but changes zero rows.
- `DELETE FROM portal_audit_log WHERE id=1` executes without error but deletes zero rows.
- `INSERT INTO portal_audit_log (...)` continues to work normally.
- Migration is reversible (downgrade drops the RULEs).

---

### Fix 5: Audit Log Row-Level Security

**Problem Statement**

The `portal_audit_log` table has an `org_id` column but no Row-Level Security policy. Other tenant-scoped tables (`portal_groups`, `portal_knowledge_bases`, etc.) already have RLS via migration `c5d6e7f8a9b0`. Without RLS, a database session with the wrong `app.current_org_id` setting could read another tenant's audit logs.

**Requirements**

- REQ-SEC-001-16: THE SYSTEM SHALL enable Row-Level Security on `portal_audit_log` using the same pattern as existing migration `c5d6e7f8a9b0`.

- REQ-SEC-001-17: THE SYSTEM SHALL create a tenant isolation policy on `portal_audit_log` using `USING (org_id = NULLIF(current_setting('app.current_org_id', true), '')::int)`.

**Implementation Guidance**

Can be in the same Alembic migration as Fix 4 or a separate migration. Follow the established pattern:

```sql
ALTER TABLE portal_audit_log ENABLE ROW LEVEL SECURITY;
ALTER TABLE portal_audit_log FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON portal_audit_log
  USING (org_id = NULLIF(current_setting('app.current_org_id', true), '')::int);
```

**Acceptance Criteria**

- With `app.current_org_id` set to org 1, `SELECT * FROM portal_audit_log` returns only org 1's records.
- With `app.current_org_id` unset or empty, `SELECT * FROM portal_audit_log` returns zero rows (consistent with existing RLS behavior).
- Superuser connections bypass RLS as expected (for administrative access).

---

### Fix 6: Automated PostgreSQL Backups

**Problem Statement**

`deploy/scripts/backup.sh` exists but is not scheduled. There is no automated backup, no offsite storage, and no alerting on backup failure. NEN 7510 requires documented backup procedures with defined retention and recovery testing.

**Requirements**

- REQ-SEC-001-18: THE SYSTEM SHALL run `backup.sh` automatically at 02:00 local time daily via a systemd timer (or cron job) on core-01.

- REQ-SEC-001-19: WHEN a backup completes successfully, THE SYSTEM SHALL upload the backup archive to Hetzner Object Storage using `rclone` (already available on core-01).

- REQ-SEC-001-20: THE SYSTEM SHALL retain 30 daily backups locally on core-01 and 90 days of backups in Hetzner Object Storage.

- REQ-SEC-001-21: WHEN a backup completes (success or failure), THE SYSTEM SHALL report the result to an Uptime Kuma push monitor, following the existing pattern in `push-health.sh`.

- REQ-SEC-001-22: IF a backup fails, THEN THE SYSTEM SHALL report the failure to Uptime Kuma with a status that triggers an alert.

**Acceptance Criteria**

- ✅ `crontab -l` shows the backup timer scheduled at 02:00 daily.
- ✅ After the timer fires, new backup files exist locally and in Hetzner Storage Box (offsite).
- ✅ Local backups older than 30 days are automatically pruned (`head -n -30`).
- ⚠️ Remote retention: Storage Box uses restricted shell (no `find`/`rm` via SSH). At ~45MB/day and 100GB capacity, pruning is not needed until ~2026 days. TODO: sftp-based pruning when storage exceeds 80GB.
- ✅ Uptime Kuma shows a heartbeat for monitor "Backup core-01" (ID 48, push token KUMA_TOKEN_BACKUP).
- ✅ ERR trap calls `_kuma_push down` on any backup step failure, triggering Uptime Kuma alert.

**Implementation notes (deviations from spec):**
- Used Hetzner Storage Box (rsync over SSH) instead of rclone + Object Storage. Equivalent offsite encryption via `age`.
- Backed up: PostgreSQL (incl. Gitea DB schema), Gitea git repos + config (KB source of truth), MongoDB, Redis. FalkorDB and Qdrant are derived and excluded (rebuild via ingest pipeline).
- Commits: `de42b79`, `e0293a8` (deploy/scripts/backup.sh); `42f829b`, `9a5875a` (R2+R3 backend/migrations).

---

## Out of Scope

- **P0 FalkorDB port binding**: Already resolved, not part of this SPEC.
- **Frontend MFA enrollment UI**: The portal frontend already has TOTP/passkey setup pages. This SPEC only adds backend enforcement at login time.
- **Backup restoration testing**: A separate runbook should cover recovery procedures. This SPEC covers automated backup creation only.
- **Audit log query API**: No new API endpoints for reading audit logs are included. The existing admin audit log view is sufficient.
- **Encryption at rest**: PostgreSQL data-at-rest encryption is handled at the infrastructure layer (full-disk encryption on core-01), not in this SPEC.

## Dependencies

| Dependency | Location | Used By |
|---|---|---|
| `audit.log_event()` | `klai-portal/backend/app/services/audit.py` | Fix 1 (call from auth.py) |
| `PortalAuditLog` model | `klai-portal/backend/app/models/audit.py` | Fix 4, Fix 5 (migration target) |
| `zitadel.has_totp()` | `klai-portal/backend/app/services/zitadel.py` | Fix 2 (existing, reuse) |
| `zitadel.list_passkeys()` | `klai-portal/backend/app/services/zitadel.py` | Fix 2 (may need to add) |
| `portal_users` / `portal_orgs` models | `klai-portal/backend/app/models/portal.py` | Fix 2 (org lookup) |
| RLS migration pattern | `alembic/versions/c5d6e7f8a9b0_add_rls_policies.py` | Fix 5 (follow same pattern) |
| Audit log migration | `alembic/versions/v2w3x4y5z6a7_add_audit_log.py` | Fix 4 (depends_on) |
| `deploy/scripts/backup.sh` | `deploy/scripts/backup.sh` | Fix 6 (schedule + extend) |
| `push-health.sh` pattern | `klai-infra/core-01/scripts/push-health.sh` | Fix 6 (Uptime Kuma reporting) |
| `rclone` | Already installed on core-01 | Fix 6 (offsite upload) |
| Uptime Kuma monitor setup | See `.claude/rules/klai/patterns/devops.md` | Fix 6 (new push monitor) |
