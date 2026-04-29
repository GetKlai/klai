---
id: SPEC-SEC-TENANT-001
version: 0.5.1
status: draft
created: 2026-04-24
updated: 2026-04-29
author: Mark Vletter
priority: high
tracker: SPEC-SEC-AUDIT-2026-04
---

# SPEC-SEC-TENANT-001: Tenant Scoping + Zitadel Role Mapping

## HISTORY

### v0.5.1 (2026-04-29)
- **No backfill on migration 006.** v0.5.0 declared an intra-DB
  ``UPDATE … FROM connector.connectors`` backfill plus a runbook
  pre-step for orphan cleanup. v0.5.1 drops both. Migration 006 is
  reduced to ``ADD COLUMN org_id VARCHAR(255) NULL`` plus the
  ``ix_sync_runs_org_id`` index — pure DDL, no data migration, no
  pre-flight runbook step. Historical ``sync_runs`` rows keep
  ``org_id IS NULL`` and fall outside per-org filters; pre-deploy
  sync history becomes invisible to every tenant after deploy.
  Acceptable because (a) sync_runs is operational/audit data — no
  business state lost, (b) ``trigger_sync`` always populates org_id
  on new rows (handler requires X-Org-ID — see REQ-7.4 and
  v0.5.1 trigger_sync note below), (c) the cross-DB / orphan-cleanup
  complexity disappears with no functional cost.
- **REQ-7.1 / REQ-7.2 nullable:** ``sync_runs.org_id`` is nullable
  post-migration. ``SyncRun.org_id`` is ``Mapped[str | None]``
  accordingly. A future SPEC may flip the column to NOT NULL once
  historical rows age out of retention; until then nullable is the
  right contract.
- **REQ-7.6 WRITE-handler refinement:** ``trigger_sync`` returns HTTP
  400 on missing X-Org-ID **regardless of the transition flag**. The
  literal v0.5.0 "proceed without filtering" branch would persist a
  row with org_id=NULL — succeeds at the schema layer (column is
  nullable per above) but produces an orphaned row invisible to every
  per-org filter. Fail-fast at the handler keeps the new-row contract
  clean. WARN ``event="sync_missing_org_id"`` still fires for
  VictoriaLogs visibility. READ handlers (list_sync_runs,
  get_sync_run) keep the v0.5.0 graceful degradation.
- Acceptance A-7 (backfill rowcount verification) is REMOVED. With no
  backfill there is nothing to verify; migration is pure DDL. A-5,
  A-6, A-8 unchanged in shape; A-5 now asserts ``column.nullable is
  True``; A-8 gains a ``test_trigger_sync_missing_org_id_returns_400_regardless_of_flag``
  case for the WRITE-side refinement.
- Phase 3 implementation lands on PR #206; this SPEC bump lands on
  PR #200 to keep the SPEC and shipped behaviour paired.

### v0.5.0 (2026-04-28)
- **Industry-aligned authority model: portal-as-authorization, IDP-as-identity.**
  v0.4.0 mapped each portal role (admin/group-admin/member) to a unique
  Zitadel project-role string and presumed those role-keys were configured
  on the Klai Platform Zitadel project. Audit of the monorepo (signup.py
  2x, users.py, migrate-user-to-portal-org.sh, dev fixture in
  zitadel.py) shows that EVERY production grant_user_role call uses
  `"org:owner"` only — no script, runbook, or bootstrap registers
  `org:group-admin` or `org:member`. Implementing v0.4.0 literally
  would 502-fail every non-admin invite at deploy time.
- More fundamentally, Zitadel's own guidance is that ZITADEL provides
  RBAC but no permission handling; applications map Zitadel roles to
  permissions in their own authority layer. Industry consensus for
  multi-tenant B2B SaaS aligns: IDP for identity, application (or a
  centralized authorization service) for authorization. JWT-claim
  text-matching for cross-tenant decisions is the anti-pattern that
  finding #10 already exemplifies — adding more role-strings does not
  remove that fragility, it merely shifts which string is matched.
- Decision: portal_users.role is the canonical authorization source for
  all portal-side checks (already true today via `_require_admin`).
  Zitadel project roles are reserved for the one downstream signal that
  retrieval-api currently honours: `org:owner` ⇔ portal admin. Non-admin
  invites receive NO Zitadel project-role grant. The JWT roles claim is
  empty for them; `_extract_role` returns None; cross-org checks fire
  as designed (no admin bypass). The longer-term migration path for
  retrieval-api's admin-bypass — replacing JWT-claim matching with a
  portal-signed assertion — lives under SPEC-SEC-IDENTITY-ASSERT-001
  (γ direction).
- REQ-2.1 / REQ-2.2 / REQ-2.3 rewritten: mapping is
  `Mapping[str, str | None]`, exhaustive over the InviteRequest Literal,
  with `None` denoting "no Zitadel grant — portal authority only". A
  None value short-circuits the grant_user_role call.
- REQ-2.4 deleted (no Zitadel project-role configuration is required;
  the only used role-key, `org:owner`, has shipped since SPEC-AUTH-001).
- REQ-3 doc updated: the table now reflects that group-admin and member
  produce an empty roles claim. The "admin equivalence" subsection
  flags retrieval-api's claim-string match as a tech-debt item to be
  closed by SPEC-SEC-IDENTITY-ASSERT-001 rather than a contract that
  TENANT-001 hardens.
- REQ-4 unchanged in shape but tightened: `_extract_role`'s
  admin-equivalent set MUST drop `org_admin` (unreachable under any
  invite path, present or post-fix) and explicitly NOT include
  `org:owner`. The `admin` literal stays only as the dev-fixture
  contract until γ.
- Acceptance A-2 rewritten: admin → grant called once with `org:owner`;
  group-admin and member → grant NOT called.
- No change to REQ-1, REQ-5.1, REQ-6, REQ-7, REQ-8.

### v0.4.0 (2026-04-28)
- **Reconciliation with production schema.** v0.3.0 declared `org_id` on
  `connector.sync_runs` as `Integer` sourced from `portal_connectors.org_id`
  (portal int). Production code has since 2026-03-23 (migration
  `003_org_id_string`, SPEC-SEC-IDENTITY-ASSERT-001 prep) standardised on
  the Zitadel resourceowner string for all connector-DB tenancy:
  `connector.connectors.org_id` is `VARCHAR(255)`, the connector auth
  middleware writes `request.state.org_id = str(zitadel_org_id)` from
  `urn:zitadel:iam:user:resourceowner:id`, and `PortalOrg` carries the
  same value as `zitadel_org_id: Mapped[str]`. Implementing v0.3.0
  literally would create a type mismatch within the connector DB
  (sync_runs.int vs connectors.varchar) and a wire mismatch with the
  portal session source.
- Updated REQ-7.1, REQ-7.2, REQ-7.3 to declare `org_id` as
  `String(255) NOT NULL, index=True` consistent with `Connector.org_id`.
- Updated REQ-8.1, REQ-8.2, REQ-8.3 to source `X-Org-ID` from
  `PortalOrg.zitadel_org_id` (the Zitadel resourceowner string), not
  `PortalOrg.id` (the portal int).
- Backfill (REQ-7.1) is now intra-DB: `UPDATE connector.sync_runs r
  SET org_id = c.org_id FROM connector.connectors c WHERE
  r.connector_id = c.id`. No cross-DB script required. Risk of orphan
  rows (sync_runs whose parent connector was deleted) remains and is
  handled by a runbook pre-step.
- Acceptance literals A-5..A-8 updated from numeric ids (`101`, `102`)
  to opaque Zitadel resourceowner strings (`org-a-resourceowner`,
  `org-b-resourceowner`).
- No change to REQ-1..REQ-6 or to deploy ordering (REQ-8.5).

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

The system SHALL ensure that `invite_user` does not grant the same
Zitadel project role to every invited user regardless of `body.role`.
Per v0.5.0 architecture (portal-as-authorization, IDP-as-identity),
only the `admin` portal role results in a Zitadel grant; group-admin
and member receive none. Their JWT roles claim is empty;
`_extract_role` returns None; cross-org checks fire normally.

- **REQ-2.1:** WHEN `invite_user` resolves `body.role`, THE service SHALL
  consult a module-level mapping keyed on the portal role to obtain the
  optional Zitadel role string. IF the mapping value is a non-empty
  string, THE service SHALL invoke `zitadel.grant_user_role` with that
  role exactly once. IF the mapping value is `None`, THE service SHALL
  NOT invoke `zitadel.grant_user_role` at all and SHALL log a structured
  event `event="invite_no_zitadel_grant"` with `org_id`, `portal_role`,
  and `zitadel_user_id` so that the absence-of-grant is observable in
  VictoriaLogs.
- **REQ-2.2:** THE mapping SHALL live as a frozen module-level constant
  `_ZITADEL_ROLE_BY_PORTAL_ROLE: Final[Mapping[str, str | None]]`,
  exhaustive over the three values of the `InviteRequest.role` Literal:
  - `"admin"` -> `"org:owner"`
  - `"group-admin"` -> `None`
  - `"member"` -> `None`
- **REQ-2.3:** IF `body.role` is not present in the mapping at runtime,
  THEN the service SHALL raise `HTTPException(500, "Unsupported role")`
  rather than falling back to a permissive default. The pydantic Literal
  already guards this at parse time; the runtime check exists to keep
  the mapping and schema in lock-step.
- **REQ-2.4 (deleted in v0.5.0):** No Zitadel project-role configuration
  is required by this SPEC. The single role-key used (`org:owner`) has
  shipped since SPEC-AUTH-001 and is verified by signup.py and
  migrate-user-to-portal-org.sh continuing to work. Future expansion of
  Zitadel-side roles, if ever needed, falls under
  SPEC-SEC-IDENTITY-ASSERT-001 or a successor SPEC.

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

- **REQ-4.1:** THE `_extract_role` function SHALL remove `org_admin`
  from the admin-equivalent set. It is not produced by any flow in the
  monorepo (signup, invite, migration scripts) and represents pure
  attack surface for any future provisioner that ever emits it.
  THE function SHALL retain `admin` as admin-equivalent: it is not
  produced by any production flow either, but it is the test-fixture
  contract that the SPEC-SEC-010 admin-bypass suite depends on. The
  full retirement of JWT-claim admin-bypass moves to
  SPEC-SEC-IDENTITY-ASSERT-001 (gamma direction). The function SHALL
  NOT add `org:owner` to the set under any circumstance: under the
  v0.5.0 mapping `org:owner` is reachable via every admin invite and
  every signup, so adding it would re-introduce finding #10 in a more
  direct form.
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

The klai-connector service SHALL add an `org_id` column to the
`SyncRun` model and SHALL filter every sync-route handler query by
`org_id` sourced from a trusted channel, so that portal-secret
possession alone cannot read or trigger sync runs across tenants.

- **REQ-7.1 (v0.5.1):** THE `connector.sync_runs` table SHALL gain an
  `org_id VARCHAR(255) NULL` column plus an `ix_sync_runs_org_id`
  index via a new Alembic migration. Type matches the existing
  `connector.connectors.org_id` (set by migration `003_org_id_string`)
  — the Zitadel resourceowner string. **No backfill.** Historical
  rows pre-date the column and keep `org_id IS NULL`; per-org filters
  do not match NULL and those rows are invisible to every tenant
  after deploy. Acceptable because (a) `sync_runs` is operational /
  audit data — no business state lost, (b) `trigger_sync` populates
  `org_id` for every NEW row (handler requires X-Org-ID per
  REQ-7.4 + REQ-2.1 of v0.5.1 trigger_sync rationale below). A
  future SPEC may flip the column to `NOT NULL` once historical rows
  age out of retention.
- **REQ-7.2 (v0.5.1):** THE `SyncRun` SQLAlchemy model
  (`klai-connector/app/models/sync_run.py`) SHALL declare `org_id:
  Mapped[str | None] = mapped_column(String(255), nullable=True,
  index=True)` without a `ForeignKey` — consistent with the existing
  `connector_id`-no-FK convention ("portal is source of truth") and
  with the schema constraint chosen in REQ-7.1.
- **REQ-7.3:** Every handler in `klai-connector/app/routes/sync.py`
  (`trigger_sync`, `list_sync_runs`, `get_sync_run`) SHALL add
  `SyncRun.org_id == org_id` to its query filter when the asserted
  `org_id` is present. The `org_id` SHALL be read from a
  portal-supplied `X-Org-ID` request header by a new helper
  (`_require_portal_org_id(request, settings) -> str | None`) that
  complements the existing `_require_portal_call(request)`. The
  header value is the Zitadel resourceowner string; the helper
  performs no further parsing.
- **REQ-7.4:** THE `trigger_sync` handler SHALL persist `org_id` on
  the new `SyncRun` row. THE active-sync guard (`SyncRun.status ==
  SyncStatus.RUNNING` check) SHALL also be scoped by `org_id` when
  asserted so that one tenant's running sync cannot block another
  tenant's trigger attempt.
- **REQ-7.5:** THE `get_sync_run` handler SHALL return HTTP 404 (not
  403) when the requested `run_id` exists but belongs to a different
  `org_id` — consistent with the "never leak existence" rule in
  `portal-security.md`.
- **REQ-7.6 (v0.5.1 — WRITE / READ asymmetry):** the
  transition-period semantics differ between READ and WRITE
  handlers:
  - READ handlers (`list_sync_runs`, `get_sync_run`): IF the
    `X-Org-ID` header is absent, THEN the connector SHALL log a WARN
    structured event (`event="sync_missing_org_id"`,
    `connector_id=<id>`) and SHALL proceed without org filtering
    (legacy connector_id-only filter). This gives a portal-first /
    connector-second deploy a graceful degradation window for
    in-flight reads. AFTER the transition period ends (tracked by
    config flag `sync_require_org_id: bool = False`, flipped to
    `True` post REQ-8 deploy), READ handlers SHALL return HTTP 400
    on any portal call missing the header.
  - WRITE handler (`trigger_sync`): SHALL ALWAYS return HTTP 400 on
    missing `X-Org-ID`, regardless of `sync_require_org_id`. The
    column being nullable (REQ-7.1) means a NULL row would persist
    at the schema layer — but such a row is invisible to every
    per-org filter and effectively orphaned at creation time.
    Fail-fast at the handler keeps the new-row contract clean. The
    WARN event still fires for VictoriaLogs visibility.
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
  `org_id: str` parameter (Zitadel resourceowner) and SHALL include
  `"X-Org-ID": org_id` in the outbound request headers alongside the
  existing portal-caller bearer token.
- **REQ-8.2:** Every portal handler that calls these client methods
  (`trigger_sync` + `list_sync_runs` in
  `klai-portal/backend/app/api/connectors.py`, plus any app-facing
  equivalents under `app/api/app/`) SHALL pass `org.zitadel_org_id`
  from the `_get_caller_org(credentials, db)` tuple. THE org_id source
  SHALL be the authenticated session's PortalOrg — never a body field,
  never a query-string parameter.
- **REQ-8.3:** THE `org_id` value on the wire SHALL be the Zitadel
  resourceowner string (`PortalOrg.zitadel_org_id`), matching the
  `Connector.org_id` shape that the connector DB has carried since
  migration `003_org_id_string` (2026-03-23). This aligns the portal,
  connector auth middleware (`request.state.org_id` from
  `urn:zitadel:iam:user:resourceowner:id`), and the sync-run scoping
  introduced by REQ-7. IF a future refactor changes the canonical
  identifier (e.g. signed-JWT assertion under
  SPEC-SEC-IDENTITY-ASSERT-001 stronger form), BOTH sides SHALL be
  updated in the same release — no mixed-identifier state is permitted
  on the wire.
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
- `SyncRun` has an `org_id VARCHAR(255) NOT NULL` column; backfill
  migration produces zero NULL rows on multi-tenant fixtures (REQ-7.1, A-7).
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
    implementing REQ-7.1 backfill (next slot: `006_add_org_id_to_sync_runs`,
    revises `005`).
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
| REQ-7.1 backfill leaves orphan sync_runs (rows whose parent connector was deleted) with no source for `org_id` | The migration runbook deletes orphan rows BEFORE the backfill step (single SQL: `DELETE FROM connector.sync_runs WHERE connector_id NOT IN (SELECT id FROM connector.connectors)`). Per knowledge.md "Connector-delete cleanup must cover all four layers", `sync_runs` is currently NOT cascaded on connector delete (tracked in SPEC-CONNECTOR-CLEANUP-001 REQ-04); orphans are pre-existing garbage and safe to drop. The NOT NULL alter at the end of the migration is the commit point and fails loud if any survivor lacks `org_id`. |
| REQ-7.6 transition period leaves a window where connector accepts missing X-Org-ID | The WARN log event `sync_missing_org_id` is queryable in VictoriaLogs; the deployment runbook (REQ-8.5) SHALL require a zero-event dwell time before flipping `sync_require_org_id=True`. |
| REQ-8.3 wire-format identifier drift between services | All three sides (portal session, connector auth middleware, sync_runs.org_id) now share the Zitadel resourceowner string. Any future migration to a different identifier (e.g. signed-JWT assertion) requires a paired portal+connector release; SPEC-SEC-IDENTITY-ASSERT-001 is the canonical place to negotiate that change. |

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
