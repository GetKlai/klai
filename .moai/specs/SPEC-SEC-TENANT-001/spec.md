---
id: SPEC-SEC-TENANT-001
version: 0.3.0
status: draft
created: 2026-04-24
updated: 2026-04-24
author: Mark Vletter
priority: high
tracker: SPEC-SEC-AUDIT-2026-04
---

# SPEC-SEC-TENANT-001: Tenant Scoping + Zitadel Role Mapping

## HISTORY

### v0.3.0 (2026-04-24)
- Added Finding V (internal-wave): klai-connector sync-routes perform
  NO org scoping — every sync endpoint filters only on `connector_id`,
  and the `SyncRun` model has no `org_id` column. Portal service secret
  leak = cross-tenant sync read/trigger for every org.
- Added REQ-7 (connector-side): add `org_id` to `SyncRun`, backfill from
  `connector.org_id`, filter all sync-route handlers on `org_id` sourced
  from a portal-supplied `X-Org-ID` header.
- Added REQ-8 (portal-side): portal SHALL inject `X-Org-ID` on every
  proxied sync call derived from the authenticated session; connector
  SHALL reject requests lacking the header after the transition period.
- Added Acceptance A-5 (org_id column), A-6 (cross-tenant sync-run 404),
  A-7 (backfill produces zero NULLs on multi-tenant fixture), A-8
  (missing X-Org-ID returns 400 post-transition).
- Cross-reference added to SPEC-SEC-IDENTITY-ASSERT-001 (portal->service
  identity-assertion contract is a prerequisite for REQ-8 trust).

### v0.2.0 (2026-04-24)
- Expanded from stub to full EARS SPEC
- Added `research.md` with offboarding delete audit, role-mapping chain, and RLS coverage matrix
- Added `acceptance.md` with four testable cross-tenant / role-claim scenarios
- Scope confirmed: code-layer only (no RLS migration on `portal_group_memberships`)

### v0.1.0 (2026-04-24)
- Stub created from SPEC-SEC-AUDIT-2026-04 (Cornelis audit 2026-04-22)
- Priority P1

---

## Goal

Two HIGH-severity defects let a single admin action cross tenant boundaries:

1. **Offboarding IDOR (finding #5).** `offboard_user` issues
   `delete(PortalGroupMembership).where(PortalGroupMembership.zitadel_user_id == zid)`
   with no `org_id` join. The model has no `org_id` column and RLS is not enabled
   on `portal_group_memberships` (`portal-security.md`: "membership rows inherit
   their tenant via the parent group's FK"). The delete therefore crosses every
   tenant the target user belongs to. An admin in org A can wipe the user's
   memberships in org B.
2. **Zitadel role-grant hardcode (finding #10).** `invite_user` stores
   `body.role` on the portal row but grants `role="org:owner"` in Zitadel for
   every invite, regardless of what the admin selected. Downstream services
   that trust the JWT role claim (retrieval-api's `_extract_role`) see every
   invited user as `admin`, bypassing cross-org / cross-user checks.

This SPEC restores tenant scoping for the offboarding delete, aligns the
Zitadel grant with the chosen portal role, documents the Zitadel role ->
JWT claim mapping, and adds regression tests that would have caught both
defects before merge.

---

## Findings addressed

| # | Finding | Severity | Source |
|---|---|---|---|
| 5 | Offboarding wipes PortalGroupMembership across all tenants (missing org_id filter) | HIGH | audit 2026-04-22 |
| 10 | invite_user hardcodes Zitadel `role="org:owner"` regardless of admin's choice | HIGH (config-dep CRITICAL) | audit 2026-04-22 |
| V | klai-connector sync-routes (`/connectors/{id}/sync`, `/syncs`, `/syncs/{run_id}`) perform NO org scoping — `SyncRun` has no `org_id` column, handlers filter only on `connector_id`, `request.state.org_id` is `None` on portal calls | MEDIUM (on `PORTAL_CALLER_SECRET` leak: CRITICAL) | internal-wave audit 2026-04-24 |

---

## Requirements

### REQ-1: Org-Scoped Offboarding Delete

The system SHALL scope every membership delete in the offboarding flow to the
caller's `org_id` so that a user's memberships in other tenants are untouched.

- **REQ-1.1:** WHEN `offboard_user` executes for a user `U` in org `A`,
  THE service SHALL delete only the `PortalGroupMembership` rows whose
  `group_id` resolves to a `PortalGroup` with `org_id = A`. Memberships where
  the parent group belongs to any other org SHALL remain intact.
- **REQ-1.2:** THE delete SHALL be expressed as either an explicit join
  (`delete(PortalGroupMembership).where(group_id.in_(select(PortalGroup.id).where(PortalGroup.org_id == A)))`)
  or an equivalent two-step pattern (select memberships, then delete by id
  set). Both forms MUST produce zero cross-tenant rowcount on a mixed-org
  fixture (see `acceptance.md` A-1).
- **REQ-1.3:** THE existing `PortalUserProduct` delete in the same handler
  (already org-scoped at `users.py:437-442`) SHALL remain unchanged — it is
  the positive reference for this pattern.
- **REQ-1.4:** WHEN the delete completes, THE service SHALL log
  `event="user_offboarded"` with `org_id`, `zitadel_user_id`, and
  `memberships_removed_count` so operations can audit any future regression
  via VictoriaLogs.
- **REQ-1.5:** WHERE `portal_group_memberships` gains an `org_id` column in a
  future migration (tracked as follow-up, not in this SPEC), THE delete MUST
  add `PortalGroupMembership.org_id == A` as a direct filter. Until then,
  the parent-group join is the authoritative guard.

### REQ-2: Role Mapping in invite_user

The system SHALL pass the Zitadel grant role that corresponds to `body.role`
selected by the inviting admin, rather than the hardcoded `org:owner`.

- **REQ-2.1:** WHEN `invite_user` calls `zitadel.grant_user_role`, THE
  service SHALL select the Zitadel role string from a module-level mapping
  keyed on `body.role`. The mapping SHALL be exhaustive for the three
  accepted values of the `InviteRequest.role` Literal (`admin`,
  `group-admin`, `member`).
- **REQ-2.2:** THE mapping SHALL live as a frozen module-level constant
  (e.g. `_ZITADEL_ROLE_BY_PORTAL_ROLE: Final[Mapping[str, str]]`) so that
  changes are reviewable in diff and traceable in code search.
- **REQ-2.3:** IF `body.role` is not present in the mapping at runtime,
  THEN the service SHALL raise `HTTPException(500, "Unsupported role")`
  rather than falling back to a permissive default. The pydantic Literal
  already guards this at parse time; the runtime check exists to keep
  the mapping and schema in lock-step.
- **REQ-2.4:** THE mapping SHALL match the role values that Zitadel is
  configured to accept on the klai project. The canonical list (with
  rationale for each entry) SHALL be documented in the Zitadel rule file
  (see REQ-3).

### REQ-3: Zitadel Role -> JWT Claim Mapping Documented

The system SHALL have a canonical document describing which Zitadel project
role produces which JWT `urn:zitadel:iam:org:project:roles` claim value, so
that any service consuming that claim can reason about the role surface
without reading Zitadel configuration.

- **REQ-3.1:** A new section SHALL be added to
  `.claude/rules/klai/platform/zitadel.md` titled "Project roles and JWT
  claims". It SHALL list every role key used in the klai Zitadel project,
  the corresponding `grant_user_role` string, and the JWT claim shape the
  caller can expect.
- **REQ-3.2:** THE document SHALL explicitly state which role values
  `_extract_role` in `retrieval-api` currently treats as admin-equivalent
  (today: `admin` and `org_admin`) AND whether those values are actually
  reachable under the REQ-2 mapping. Unreachable values SHALL be flagged
  for removal in REQ-4.
- **REQ-3.3:** THE document SHALL include a "how to verify" one-liner: how
  to decode a JWT for a known test user and read the claim shape, so that
  a future role-mapping change can be verified end-to-end.

### REQ-4: retrieval-api Role-Bypass Audit

The system SHALL audit the admin-bypass in
`retrieval_api.middleware.auth._extract_role` against the documented REQ-3
mapping and remove unreachable claim values.

- **REQ-4.1:** THE `_extract_role` function SHALL treat as admin-equivalent
  only claim values that the REQ-2 mapping can produce. Values that cannot
  be produced by the portal invite flow SHALL be removed.
- **REQ-4.2:** IF the set of admin-equivalent claim values changes, THEN the
  change SHALL be reflected in both the code (with an inline comment
  pointing to REQ-3) and the Zitadel rule document, to avoid silent drift.
- **REQ-4.3:** `verify_body_identity` SHALL continue to skip cross-org /
  cross-user checks only when `auth.role == "admin"` (REQ-3.1/3.2 of
  SPEC-SEC-010). No additional bypass paths SHALL be introduced as part of
  this SPEC.
- **REQ-4.4:** THE admin-bypass behaviour SHALL be covered by the existing
  SPEC-SEC-010 test suite; this SPEC adds one additional case (see
  `acceptance.md` A-3) verifying that a JWT produced by an invite with
  `role="member"` does NOT trigger the bypass.

### REQ-5: Regression Tests for Cross-Tenant Scenarios

The system SHALL ship pytest coverage that would have caught both findings
before merge.

- **REQ-5.1:** A test `test_offboard_user_does_not_wipe_other_org_memberships`
  SHALL seed user `U` as a member of groups in org `A` and org `B`, call
  the offboard endpoint authenticated as an admin of org `A`, and assert
  that `U`'s memberships in org `B` are untouched (rowcount unchanged).
- **REQ-5.2:** A test `test_invite_user_grants_portal_role_to_zitadel` SHALL
  patch `zitadel.grant_user_role` and assert, for each of `admin`,
  `group-admin`, and `member`, that the call receives the mapped Zitadel
  role string from REQ-2 — not `org:owner` for every case.
- **REQ-5.3:** A test
  `test_verify_body_identity_rejects_cross_org_for_member_role` SHALL run
  against retrieval-api with a mocked JWT decoded to
  `role=<member-equivalent claim>` and assert that a body carrying a
  different `org_id` produces HTTP 403 (not 200 via admin bypass).
- **REQ-5.4:** THE regression tests SHALL live in
  `klai-portal/backend/tests/test_admin_users.py` (REQ-5.1, REQ-5.2) and
  `klai-retrieval-api/tests/test_auth_middleware.py` (REQ-5.3), next to
  the existing coverage for each handler.
- **REQ-5.5:** The test seed SHALL create at least two `PortalOrg` rows so
  that the cross-tenant scenario is measurable — a single-org fixture
  cannot catch finding #5.

### REQ-6: RLS Second-Layer Verification

The system SHALL verify, not assume, that the defence-in-depth RLS layer
catches a direct database query that lacks an `org_id` filter on a
tenant-scoped model.

- **REQ-6.1:** A test `test_rls_blocks_cross_org_query_without_tenant_context`
  SHALL open a session against the `portal_api` role WITHOUT calling
  `set_tenant`, issue a `SELECT * FROM portal_group_kb_access` (category-D
  table), and assert that either zero rows are returned (when the policy
  allows NULL-GUC reads) or that the query raises
  `InsufficientPrivilegeError` (when the strict branch applies). The test
  serves as a live verification that RLS is in effect and is the correct
  second layer.
- **REQ-6.2:** THE test SHALL explicitly document that
  `portal_group_memberships` is NOT covered by RLS (per
  `portal-security.md` "Outside the classification" note) and that the
  REQ-1 join is therefore the ONLY code-layer guard against cross-tenant
  membership deletes. No RLS policy SHALL be added on that table in this
  SPEC — see Out of Scope.
- **REQ-6.3:** IF a future SPEC adds an `org_id` column to
  `portal_group_memberships` and enrolls it in category-D RLS, THEN this
  test suite SHALL be extended to cover it; until then REQ-1 is the sole
  guard and REQ-5.1 is the regression test that proves it.

### REQ-7: klai-connector SyncRun org_id column + org-scoped handlers

The klai-connector service SHALL add an `org_id: int NOT NULL` column to
the `SyncRun` model and SHALL filter every sync-route handler query by
`org_id` sourced from a trusted channel, so that portal-secret possession
alone cannot read or trigger sync runs across tenants.

- **REQ-7.1:** THE `connector.sync_runs` table SHALL gain an `org_id int
  NOT NULL` column via a new Alembic migration. A backfill step within
  the same migration SHALL populate existing rows by joining against the
  portal-side `portal_connectors` table through `connector_id`, using
  `portal_connectors.org_id` as the source of truth. The column SHALL be
  created nullable, backfilled, then altered to `NOT NULL` in a single
  migration transaction.
- **REQ-7.2:** THE `SyncRun` SQLAlchemy model
  (`klai-connector/app/models/sync_run.py`) SHALL declare `org_id:
  Mapped[int] = mapped_column(Integer, nullable=False, index=True)`
  without a `ForeignKey` — consistent with the existing
  `connector_id`-no-FK convention documented in the model docstring
  ("portal is source of truth").
- **REQ-7.3:** Every handler in `klai-connector/app/routes/sync.py`
  (`trigger_sync`, `list_sync_runs`, `get_sync_run`) SHALL add
  `SyncRun.org_id == org_id` to its query filter. The `org_id` SHALL be
  read from a portal-supplied `X-Org-ID` request header by a new
  helper (`_require_portal_org_id(request) -> int`) that complements
  the existing `_require_portal_call(request)`.
- **REQ-7.4:** THE `trigger_sync` handler SHALL persist `org_id` on the
  new `SyncRun` row. THE active-sync guard (`SyncRun.status ==
  SyncStatus.RUNNING` check at `sync.py:47-54`) SHALL also be scoped by
  `org_id` so that one tenant's running sync cannot block another
  tenant's trigger attempt.
- **REQ-7.5:** THE `get_sync_run` handler SHALL return HTTP 404 (not
  403) when the requested `run_id` exists but belongs to a different
  `org_id` — consistent with the "never leak existence" rule in
  `portal-security.md`.
- **REQ-7.6:** DURING a transition period (one release), IF the
  `X-Org-ID` header is absent, THEN the connector SHALL log a WARN
  structured event (`event="sync_missing_org_id"`,
  `connector_id=<id>`) and SHALL proceed without org filtering, so
  that a staggered portal-first / connector-second deploy does not
  break in-flight syncs. AFTER the transition period ends (tracked by
  a config flag `sync_require_org_id: bool = False`, flipped to
  `True` in the release following portal deployment of REQ-8), THE
  connector SHALL return HTTP 400 (`detail="X-Org-ID header required"`)
  on any portal call missing the header.
- **REQ-7.7:** THE connector SHALL NOT derive `org_id` from the
  `connector_id` itself (e.g. by calling back into the portal to look
  it up). The trust contract is explicit: portal asserts the org by
  header, connector trusts the header only because
  `_require_portal_call` proved the caller holds `PORTAL_CALLER_SECRET`.
  This aligns with the portal->service identity-assertion contract
  drafted in SPEC-SEC-IDENTITY-ASSERT-001.

### REQ-8: Portal-side X-Org-ID injection on sync proxies

The portal backend SHALL inject the authenticated session's `org_id` as
the `X-Org-ID` header on every sync-related call it makes to
klai-connector, so that the connector can enforce tenant scoping under
REQ-7.

- **REQ-8.1:** `klai_connector_client.trigger_sync`,
  `klai_connector_client.get_sync_runs`, and any other method that hits
  `/connectors/{id}/sync`, `/syncs`, or `/syncs/{id}` SHALL accept an
  `org_id: int` parameter and SHALL include
  `"X-Org-ID": str(org_id)` in the outbound request headers alongside
  the existing portal-caller bearer token.
- **REQ-8.2:** Every portal handler that calls these client methods
  (`trigger_sync` + `list_sync_runs` in
  `klai-portal/backend/app/api/connectors.py`, plus any app-facing
  equivalents under `app/api/app/`) SHALL pass `org.id` from the
  `_get_caller_org(credentials, db)` tuple. THE org_id source SHALL be
  the authenticated session's PortalOrg — never a body field, never a
  query-string parameter.
- **REQ-8.3:** THE `org_id` value on the wire SHALL be the portal
  internal integer (`PortalOrg.id`), matching the `connector.org_id`
  backfill source from REQ-7.1. IF a future refactor changes the
  connector's org identifier to the Zitadel resourceowner id, BOTH
  sides SHALL be updated in the same release — no mixed-identifier
  state is permitted on the wire.
- **REQ-8.4:** A test `test_portal_sync_client_forwards_org_id` SHALL
  patch the httpx transport, call each sync client method with a known
  `org_id`, and assert the `X-Org-ID` header is present and matches.
- **REQ-8.5:** Portal deployment of REQ-8 SHALL precede the connector
  flip of `sync_require_org_id=True` (REQ-7.6). THE SPEC deployment
  plan SHALL document this ordering explicitly in the run-phase
  runbook so the transition period is non-breaking.

---

## Success Criteria

- `offboard_user` deletes only memberships whose parent group belongs to the
  caller's org. REQ-5.1 regression test passes on main.
- `invite_user` issues the Zitadel grant matching `body.role` for all three
  values of the Literal. REQ-5.2 passes.
- `.claude/rules/klai/platform/zitadel.md` contains the REQ-3 section with
  role -> claim mapping and verification procedure.
- retrieval-api's `_extract_role` admin-equivalent set matches the REQ-3
  document. REQ-5.3 passes.
- REQ-6.1 RLS verification test passes on main; RLS remains the
  defence-in-depth second layer across category-D tables.
- `SyncRun` has an `org_id NOT NULL` column; backfill migration produces
  zero NULL rows on multi-tenant fixtures (REQ-7.1, A-7).
- All klai-connector sync-route handlers filter on `org_id`, and a
  cross-tenant fetch attempt returns HTTP 404 (REQ-7.3, REQ-7.5, A-6).
- Portal sync client injects `X-Org-ID` on every proxied call, derived
  from the authenticated session's `org.id` (REQ-8.1, REQ-8.2, A-8).
- After the transition period flag flips, connector rejects portal
  calls lacking `X-Org-ID` with HTTP 400 (REQ-7.6, A-8).
- No existing test suite regresses.

---

## Environment

- **Portal backend:** Python 3.13, FastAPI, SQLAlchemy 2 async, PostgreSQL.
  Module under change: `klai-portal/backend/app/api/admin/users.py`.
- **Group model:** `klai-portal/backend/app/models/groups.py`
  (`PortalGroupMembership` has no `org_id`; inherits tenancy via
  `PortalGroup.org_id` through `group_id` FK).
- **Zitadel client:** `klai-portal/backend/app/services/zitadel.py` —
  `grant_user_role(org_id, user_id, role)` is a thin wrapper over the
  Zitadel Management API.
- **Retrieval-api:** `klai-retrieval-api/retrieval_api/middleware/auth.py`
  (`_extract_role`, `verify_body_identity`, `AuthMiddleware`).
- **Rule coupling:**
  - `.claude/rules/klai/projects/portal-security.md` (canonical
    `_get_{model}_or_404` pattern; 4-category RLS framework; the note
    that `portal_group_memberships` is outside the classification).
  - `.claude/rules/klai/platform/zitadel.md` (gets the REQ-3 section).
- **klai-connector (REQ-7):**
  - `klai-connector/app/routes/sync.py` — three sync-route handlers
    currently filtering only on `connector_id`; gains `X-Org-ID`
    helper and `org_id` filter on every query.
  - `klai-connector/app/models/sync_run.py` — `SyncRun` model; gains
    `org_id` mapped column.
  - `klai-connector/app/middleware/auth.py:118-121` — portal-call
    branch currently sets `request.state.org_id = None`; documented
    unchanged (auth middleware is NOT the trust source for sync
    `org_id`; REQ-7.3 helper reads the header directly).
  - New Alembic migration under `klai-connector/alembic/versions/`
    implementing REQ-7.1 backfill.
- **klai-portal (REQ-8):**
  - `klai-portal/backend/app/services/klai_connector_client.py` —
    `SyncRunData` client methods gain `org_id` parameter and
    `X-Org-ID` header injection.
  - `klai-portal/backend/app/api/connectors.py` — `trigger_sync` and
    `list_sync_runs` forward `org.id` to the client (callsites at
    `connectors.py:483, 529` under the current file).

---

## Assumptions

- The `_get_{model}_or_404(id, org_id, db)` pattern documented in
  `portal-security.md` is the intended canonical form for tenant-scoped
  lookups. Verified: 50+ callsites in `klai-portal/backend/app/api/` (see
  `research.md` inventory).
- Zitadel accepts the role strings `org:owner`, `org:group-admin`,
  `org:member` on the klai project. REQ-3 will document the exact Zitadel
  configuration; if a value is missing, REQ-2 mapping is adjusted to
  match the configured Zitadel state rather than adding new Zitadel roles
  in this SPEC.
- `PortalGroupMembership` is the only membership-style table without an
  `org_id` column. Verified by grep against `app/models/`.
- RLS is already enabled and enforced on all category-D tables named in
  `portal-security.md`. REQ-6.1 verifies this rather than adding new
  policies.
- `retrieval-api._extract_role`'s admin-equivalent list (`admin`,
  `org_admin`) is the only downstream consumer whose bypass behaviour
  depends on invite-time role selection. Other services that read the
  roles claim (research-api, scribe, mailer) do not branch on the value
  today — verified by grep for `_ZITADEL_ROLES_CLAIM` / `"admin"` in
  `klai-*`.

---

## Out of Scope

- Adding an `org_id` column and RLS policy to `portal_group_memberships`.
  This is a schema change with migration cost and callsite fan-out
  beyond this SPEC's scope. Tracked as follow-up if REQ-5.1 proves
  insufficient or if a second IDOR surfaces against this table.
- Refactoring all admin endpoints to use the canonical
  `_get_{model}_or_404` helper. Only the offboarding delete is in scope
  here; a broader refactor SPEC can chase the rest.
- Replacing the Zitadel Management API with a first-class IdP abstraction.
  REQ-2/REQ-3 remain Zitadel-specific.
- Changing `verify_body_identity`'s existing skip semantics for the
  internal-secret path (REQ-3.3 of SPEC-SEC-010). This SPEC only touches
  the JWT role path.

---

## Risks

| Risk | Mitigation |
|---|---|
| Unknown Zitadel role strings for group-admin / member | REQ-3 research step confirms the configured role set before REQ-2 mapping lands. If a needed role is not configured, SPEC pauses and a dependency on a Zitadel config change is declared in `research.md` step 3. |
| Changing the grant role breaks users who were already invited as `org:owner` | REQ-2 only affects NEW invites. Existing portal_users rows retain their portal `role` column; existing Zitadel grants are untouched. A follow-up cleanup SPEC can re-grant mis-roled historical users if needed. |
| retrieval-api admin bypass removal narrows attack surface but may affect callers that relied on the bypass accidentally | REQ-4.1 audits the admin-equivalent set; REQ-4.4 adds regression coverage. The SPEC-SEC-010 cross-org/cross-user tests remain the backstop. |
| REQ-6.1 relies on RLS being correctly deployed on the target table | The test itself is the verification. A failure surfaces a real defence-in-depth gap, which is a valuable signal even if it blocks this SPEC. |
| REQ-7.1 backfill requires cross-database join (connector DB -> portal DB) | The migration runs against the connector DB; the backfill SHALL read `portal_connectors.org_id` via the operator-run psql path documented for cross-schema changes in `portal-security.md` "RLS + Alembic". An out-of-band CSV export/import or a one-shot Python script using both DSNs is acceptable. The migration is idempotent (NOT NULL alter is the commit point). |
| REQ-7.6 transition period leaves a window where connector accepts missing X-Org-ID | The WARN log event `sync_missing_org_id` is queryable in VictoriaLogs; the deployment runbook (REQ-8.5) SHALL require a zero-event dwell time before flipping `sync_require_org_id=True`. |
| REQ-8.3 org_id value mismatch (portal int vs Zitadel resourceowner) | The backfill in REQ-7.1 anchors the contract to `portal_connectors.org_id` (portal integer). Any future migration to a different identifier requires a paired portal+connector release; SPEC-SEC-IDENTITY-ASSERT-001 is the canonical place to negotiate that change. |

---

## Cross-references

- Tracker: [SPEC-SEC-AUDIT-2026-04](../SPEC-SEC-AUDIT-2026-04/spec.md)
- Related SPEC: [SPEC-SEC-010](../SPEC-SEC-010/spec.md)
  (retrieval-api cross-org/cross-user guards; REQ-4 builds on its
  admin-bypass contract)
- Related SPEC: [SPEC-SEC-IDENTITY-ASSERT-001](../SPEC-SEC-IDENTITY-ASSERT-001/spec.md)
  (portal->service identity-assertion contract; prerequisite for REQ-8
  trust — the connector trusts `X-Org-ID` only because the portal-caller
  secret authenticates the channel. If a stronger assertion form is
  adopted there, REQ-8 adopts it in the same release.)
- Rule: [portal-security.md](../../../.claude/rules/klai/projects/portal-security.md)
- Rule: [.claude/rules/klai/platform/zitadel.md](../../../.claude/rules/klai/platform/zitadel.md)
  (receives the REQ-3 section)
- Research: [research.md](./research.md)
- Acceptance: [acceptance.md](./acceptance.md)
