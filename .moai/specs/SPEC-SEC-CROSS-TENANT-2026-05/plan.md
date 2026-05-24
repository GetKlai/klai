# SPEC-SEC-CROSS-TENANT-2026-05 - Remediation Plan

Status: draft
Created: 2026-05-24
Input: cross-tenant security scan of `00308cff..9b652472` (code added since 2026-05-17)
Owner: platform/backend
Priority: P1 security hardening

## Goal

Close the security issues found in the May 2026 cross-tenant scan, with the
highest priority on preventing tenant-scoped actions from causing global
identity side effects, stale privileges, or unintended public exposure of
tenant knowledge bases.

## Non-goals

- Redesign the full platform-admin console.
- Replace the widget JWT scheme with asymmetric signing.
- Rework all multi-org account selection. This plan only fixes the vulnerable
  or ambiguous call sites discovered in the scan.

## Findings Covered

| ID | Severity | Finding | Primary files |
|---|---:|---|---|
| CT-01 | High | Platform hard-delete removes global Zitadel identity from a tenant-scoped action | `platform_manage.py`, `users.py`, `models/portal.py` |
| CT-02 | High | Platform role changes do not notify active MCP/tool sessions | `platform_manage.py`, `mcp_role_notifier.py` |
| CT-03 | Medium/High | Platform invite/create-tenant leaves orphan Zitadel users on partial failure | `platform_manage.py`, `zitadel.py` |
| CT-04 | Medium | Wildcard origin check accepts lookalike domains | `widget_auth.py` |
| CT-05 | Medium | Public bot link makes `widget_id` a public bearer-style handle | `partner.py`, `admin_widgets.py`, widget models/frontend |
| CT-06 | Medium | OAuth callback picks an arbitrary tenant row for multi-org users | `oauth.py` |
| CT-07 | Medium | Platform-admin endpoints lack direct backend regression tests | backend tests |
| CT-08 | Low/Medium | `pip-audit` still fails on `idna` CVE ignore | `uv.lock`, `pyproject.toml`, CI workflow |

## Execution Order

1. Fix destructive account semantics first: CT-01.
2. Close stale privilege windows: CT-02.
3. Add compensation for partial identity creation failures: CT-03.
4. Fix widget origin matching: CT-04.
5. Make public widget sharing explicit and testable: CT-05.
6. Fix OAuth multi-org scoping: CT-06.
7. Add platform-admin regression tests across all new surfaces: CT-07.
8. Remove dependency-audit ignores after bumping vulnerable packages: CT-08.

This order reduces blast radius before changing broader UX/product semantics.

## CT-01 - Tenant-scoped User Removal Must Not Delete Global Identity

### Problem

`portal_users` allows multiple memberships per `zitadel_user_id` via the
unique constraint on `(zitadel_user_id, org_id)`. The new platform delete flow
checks the target org membership but then calls `zitadel.remove_user()` in the
central portal org. That turns a tenant-scoped removal into a global account
delete.

### Implementation

**Target:** `klai-portal/backend/app/api/admin/platform_manage.py`

Change `platform_delete_user()` to:

- Load the target `PortalUser` row under `tenant_scoped_session(org_id)` as it
  does today.
- Before calling Zitadel, check for other memberships for the same
  `zitadel_user_id` using a cross-org-safe helper.
- If other memberships exist:
  - Do not call `zitadel.remove_user()`.
  - Revoke only credentials and KB ownership scoped to the target `org_id`.
  - Delete only the target `PortalUser` row.
  - Audit with `global_identity_deleted=false` and
    `remaining_membership_count`.
- If no other memberships exist:
  - Existing full Zitadel removal remains allowed.
  - Audit with `global_identity_deleted=true`.
- Block deletion of the acting platform-admin identity.
- Block deletion of any identity that is an admin in the platform org unless a
  future explicit break-glass flow is added.

**Target:** `klai-portal/backend/app/api/admin/users.py`

Apply the same membership-aware semantics to the existing tenant admin
hard-delete path, or explicitly split it into:

- `remove_user_from_org()` for tenant-scoped offboarding.
- `delete_global_user_if_last_membership()` for the final-account case.

Do not leave platform-admin and tenant-admin delete semantics divergent.

### Tests

Add backend tests covering:

- User with one membership: Zitadel `remove_user()` is called.
- User with two memberships: Zitadel `remove_user()` is not called; only target
  membership is deleted.
- Platform-admin identity cannot be deleted from a customer tenant.
- Self-delete by platform admin returns `409`.
- Audit details include `target_org_id`, `global_identity_deleted`, and
  `remaining_membership_count`.

### Acceptance

- A platform action on tenant A cannot break the user's login for tenant B.
- Global identity deletion occurs only when the user has no remaining
  memberships.

### Rollback

Rollback is code-only. Reverting restores previous delete behavior, but that
behavior is unsafe and should be treated as an emergency rollback only.

## CT-02 - Platform Role Changes Must Invalidate Active Permission State

### Problem

The normal tenant-admin role update calls `fire_role_change_notification()`.
The new platform role update commits the role change and audit event but does
not notify active MCP/tool sessions.

### Implementation

**Target:** `klai-portal/backend/app/api/admin/platform_manage.py`

- Import `fire_role_change_notification`.
- After a successful role commit in `platform_update_role()`, call
  `fire_role_change_notification(zitadel_user_id)`.
- Keep the notifier fire-and-forget, matching the normal admin path.

Also review suspend/reactivate/delete:

- `platform_suspend()`: notify or revoke active sessions for the target user.
- `platform_reactivate()`: no forced notification required unless session
  state caches status.
- `platform_delete_user()`: notify/revoke before deleting the local row.

### Tests

- Platform role change calls `fire_role_change_notification()` exactly once.
- Failed role change does not call the notifier.
- Last-admin demotion still returns `409` and does not notify.

### Acceptance

- Platform and tenant-admin role changes have the same active-session
  invalidation semantics.

## CT-03 - Compensate Partial Zitadel User Creation Failures

### Problem

`platform_invite()` and `platform_create_tenant()` create a user in the central
portal Zitadel org before all later steps have completed. Failures while
sending invite mail, granting roles, or writing portal rows can leave orphaned
pre-verified identities.

### Implementation

**Target:** `klai-portal/backend/app/api/admin/platform_manage.py`

Add a local compensation helper:

```python
async def _rollback_zitadel_user(user_id: str) -> None:
    with suppress(Exception):
        await zitadel.remove_user(settings_zitadel_portal_org_id(), user_id)
```

Use it in:

- `platform_invite()` when send-code, role-grant, personal-KB creation, or DB
  commit fails after user creation.
- `platform_create_tenant()` when owner setup or portal DB insert fails after
  owner user creation.

Prefer the safer sequencing where possible:

1. Create Zitadel user.
2. Grant required role.
3. Create portal rows and commit.
4. Send invite mail after DB commit.

If the mail send fails after commit, return a partial-failure response that
includes a retry action, but do not delete a now-valid portal account.

### Tests

- Invite mail failure removes the newly-created Zitadel user and creates no
  `PortalUser`.
- Role grant failure removes the newly-created Zitadel user.
- Portal DB failure removes the newly-created Zitadel user.
- Create-tenant owner setup failure removes both the new Zitadel org and owner
  user.
- Mail failure after DB commit returns a retryable partial failure and keeps the
  portal row.

### Acceptance

- No orphan central-portal identities are left after failed platform invite or
  platform tenant creation flows.

## CT-04 - Correct Wildcard Origin Matching

### Problem

The current wildcard check treats `https://*.example.com` as a suffix match.
That accepts lookalike domains such as `https://evil-example.com`.

### Implementation

**Target:** `klai-portal/backend/app/services/widget_auth.py`

Replace string suffix matching with parsed origin matching:

- Parse both `origin` and `allowed` with `urllib.parse.urlparse`.
- Require exact scheme.
- Require exact port semantics. If either side has an explicit port, compare
  effective `(scheme, hostname, port)`.
- For wildcard origins, require:
  - `allowed.hostname` starts with `"*."`.
  - `origin.hostname.endswith("." + suffix)`.
  - `origin.hostname != suffix`.
- Keep exact-match behavior unchanged.
- Keep empty `allowed_origins` behavior unchanged for now; CT-05 decides the
  product semantics.

### Tests

Add tests to `tests/test_widget_config.py` or a new
`tests/test_widget_origin_allowed.py`:

- `https://app.example.com` allowed by `https://*.example.com`.
- `https://example.com` not allowed by wildcard.
- `https://evil-example.com` not allowed.
- `https://example.com.evil.test` not allowed.
- Scheme mismatch rejected.
- Port mismatch rejected when configured.

### Acceptance

- Wildcard origins match only real subdomains of the configured domain.

## CT-05 - Make Public Bot Sharing Explicit

### Problem

`/partner/v1/public-bot-config` intentionally has no Origin gate. Any holder
of `widget_id` can fetch a session token for the widget's linked KBs. This is
safe only if the admin explicitly understands the widget as publicly shared.

### Implementation

**Target:** widget persistence/model layer

Add an explicit public-share flag. Preferred shape:

- `widgets.public_share_enabled BOOLEAN NOT NULL DEFAULT false`

or, if schema churn must be avoided:

- `widget_config["public_share_enabled"]` with server-side default `false`.

Schema column is preferred because it is queryable, auditable, and easier to
gate consistently.

**Target:** `klai-portal/backend/app/api/partner.py`

- `/public-bot-config` returns `404` or `403` when
  `public_share_enabled=false`.
- Keep `/widget-config` embed behavior separate from public share behavior.
- Audit public-bot config fetches at low cardinality, or at minimum emit a
  structured log with `org_id`, `widget_id`, and source IP.

**Target:** `klai-portal/backend/app/api/admin_widgets.py`

- Expose admin update/read support for the public-share flag.
- Default newly-created widgets to private public-share disabled.
- If current production behavior depends on existing public links, run a data
  migration to set `public_share_enabled=true` for existing widgets that have
  published bot links, or do a staged rollout with compatibility mode.

**Target:** frontend/widget admin UI

- Add a clear toggle in the embed/share tab.
- Copy must distinguish:
  - Embed origin restriction.
  - Public share link availability.

### Tests

- `/public-bot-config` rejects by default.
- Enabling public share returns the existing config payload and session token.
- Disabling public share does not affect normal `/widget-config` when origin is
  allowed.
- Admin API persists and returns the flag.
- Existing widget JWT tenant binding tests remain green.

### Acceptance

- A widget is not publicly accessible via `/bot/{widget_id}` unless an admin
  explicitly enables public sharing.

## CT-06 - Make OAuth Callback Tenant-Explicit

### Problem

The OAuth callback resolves `PortalUser` by `zitadel_user_id` only. For users
with multiple tenant memberships, that can choose an arbitrary row before
checking connector ownership.

### Implementation

**Target:** `klai-portal/backend/app/api/oauth.py`

At authorize time:

- Resolve the target connector and include `connector_id` and `org_id` in the
  signed state payload.
- If the flow starts from a KB slug instead of a connector id, resolve the KB
  tenant explicitly and include `org_id`.

At callback time:

- Verify the signed `org_id`.
- Load the connector by id and require `connector.org_id == payload["org_id"]`.
- Validate that the current `user_id` has a `PortalUser` membership for that
  exact `org_id`.
- Set tenant only after the exact org membership and connector ownership are
  confirmed.

Avoid `db.scalar(select(PortalUser).where(PortalUser.zitadel_user_id == user_id))`
for multi-tenant authorization decisions.

### Tests

- Multi-org user reconnects connector in org A while also member of org B; org A
  connector succeeds.
- Same user cannot complete callback for connector in org C where they have no
  membership.
- Tampered state `org_id` fails.
- Connector-id and org-id mismatch fails with non-enumerating 404.

### Acceptance

- OAuth reconnect and credential writes are scoped to the tenant encoded in the
  signed state and verified against exact user membership.

## CT-07 - Direct Platform-admin Regression Tests

### Problem

The new platform-admin surface bypasses RLS for cross-org reads and performs
tenant writes, but there are no direct backend tests for the new
`platform.py` and `platform_manage.py` endpoint families.

### Implementation

**Target:** `klai-portal/backend/tests/`

Add `tests/test_platform_admin_console.py` and
`tests/test_platform_admin_manage.py`.

Minimum test matrix:

- Unauthenticated caller gets `401`.
- Tenant admin gets `403`.
- Platform org non-admin gets `403`.
- Platform admin can read multiple orgs.
- Each read endpoint writes `platform_admin.viewed`.
- Role update uses `tenant_scoped_session(target_org)` and cannot affect a user
  outside the target org.
- Suspend/reactivate only mutate the target org membership.
- Invite creates user in the target org, not the platform org.
- Delete semantics from CT-01.
- Rollback semantics from CT-03.

Also add code-level guard tests:

- Every route in `app/api/admin/platform.py` has `require_platform_admin()`.
- Every cross-org read endpoint uses `cross_org_session()`.
- `platform_manage.py` does not import or use `cross_org_session()` for writes.

### Acceptance

- The platform-admin console has regression coverage for auth gates, RLS
  strategy, audit logging, and destructive side effects.

## CT-08 - Remove Tactical pip-audit Ignores

### Problem

CI currently ignores `CVE-2026-45409` for `idna`, while local `pip-audit`
still reports `idna 3.13` with fix version `3.15`.

### Implementation

**Target:** `klai-portal/backend/pyproject.toml` and lockfile

- Bump `idna` to `>=3.15` through the normal `uv` dependency workflow.
- Regenerate `uv.lock`.
- Run `uv run --with pip-audit pip-audit` without the `CVE-2026-45409` ignore.

**Target:** `.github/workflows/portal-api.yml`

- Remove `--ignore-vuln CVE-2026-45409`.
- Re-evaluate `--ignore-vuln PYSEC-2025-183`. If local audit no longer reports
  it, remove the ignore and its TODO comment as part of the same change.

### Tests

- `uv run --with pip-audit pip-audit` exits 0.
- Portal backend tests still pass.

### Acceptance

- CI no longer suppresses the idna vulnerability.

## Required Verification Before Merge

Run from `klai-portal/backend`:

```bash
uv run pytest -q tests/test_platform_admin_console.py tests/test_platform_admin_manage.py
uv run pytest -q tests/test_widget_config.py tests/test_widget_jwt_per_tenant.py tests/test_partner_dependencies.py tests/test_partner_chat.py tests/test_admin_widgets.py
uv run pytest -q tests/test_oauth_routes.py
uv run --with pip-audit pip-audit
```

Run full backend suite before release:

```bash
uv run pytest -q --tb=short
```

## Deployment Plan

1. Deploy CT-01 and CT-02 together if possible. They both change account and
   permission semantics and should be smoke-tested with one test tenant.
2. Deploy CT-03 after CT-01/CT-02. Exercise platform invite and create-tenant
   against a non-production Zitadel org first.
3. Deploy CT-04 independently; low rollout risk with strong tests.
4. Deploy CT-05 behind compatibility mode if public bot links are already in
   customer use. Announce the explicit share toggle before flipping defaults.
5. Deploy CT-06 after OAuth reconnect tests pass against Google/Microsoft test
   connectors.
6. CT-07 and CT-08 are merge gates, not runtime features.

## Monitoring

After rollout, monitor:

- `platform_admin.user_deleted` audit rows with
  `global_identity_deleted=true`.
- `platform_admin.user_role_changed` count and MCP role notifier errors.
- `platform_invite_*_failed` and `platform_create_tenant_*_failed` logs.
- `/partner/v1/public-bot-config` 403/404 volume after CT-05.
- OAuth callback 404/400 rate after CT-06.
- pip-audit CI job status.

## Open Questions

1. Should tenant-admin delete keep the term "hard-delete" after CT-01, or
   should the UI rename it to "remove from organization" unless this is the
   final membership?
2. Are existing `/bot/{widget_id}` links already shared with customers? This
   decides whether CT-05 needs a migration/compatibility window.
3. Is platform-admin identity deletion ever a supported support operation? If
   yes, define a separate break-glass path with extra audit requirements.
4. Which session systems must be revoked on suspend/delete beyond MCP tool-list
   notifications?

## Definition of Done

- All CT-01 through CT-08 acceptance criteria are met.
- No direct cross-tenant destructive side effect remains from tenant-scoped
  platform actions.
- Widget public exposure is explicit and tested.
- OAuth callback scoping is tenant-explicit.
- Platform-admin endpoint families have direct regression coverage.
- `pip-audit` passes without the idna ignore.
