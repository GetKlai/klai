# SPEC-SEC-TENANT-001 — Acceptance

Testable scenarios. Each scenario maps to one or more REQs in `spec.md`
and states the exact setup, action, and assertion. These form the
regression suite that would have caught findings #5 and #10 before
merge.

---

## A-1: Cross-tenant offboarding isolation

**REQs covered:** REQ-1.1, REQ-1.2, REQ-5.1

**Purpose:** Offboarding a user from org A MUST NOT remove their
memberships in org B.

**Given:**
- Two organisations seeded: `org_a` (id=101) and `org_b` (id=102).
- User `U` (`zitadel_user_id="user-U"`) exists in both orgs as an
  active `portal_users` row (one row per org).
- `org_a` has a `PortalGroup` `Engineering-A` (id=201, org_id=101).
- `org_b` has a `PortalGroup` `Engineering-B` (id=301, org_id=102).
- `PortalGroupMembership` rows exist for `U`:
  - membership `M_A` (group_id=201, zitadel_user_id="user-U")
  - membership `M_B` (group_id=301, zitadel_user_id="user-U")
- Admin `caller` is authenticated against `org_a` and passes the
  `_require_admin` check.

**When:**
- `POST /api/admin/users/user-U/offboard` is called with `caller`'s
  bearer token.

**Then:**
- Response is `200 OK` with body `{"message": "User user-U offboarded."}`.
- In DB, `M_A` is absent (rowcount in `portal_group_memberships`
  WHERE id=M_A.id is 0).
- In DB, `M_B` is still present (rowcount in
  `portal_group_memberships` WHERE id=M_B.id is 1).
- `portal_users` row for (`user-U`, `org_a`) has `status="offboarded"`.
- `portal_users` row for (`user-U`, `org_b`) has `status="active"`
  (unchanged).
- Structured log line `event="user_offboarded"` contains
  `org_id=101`, `zitadel_user_id="user-U"`,
  `memberships_removed_count=1`.

**Failure mode under current code (regression signal):**
- `M_B` is deleted (rowcount 0) → the test fails red, exposing the
  IDOR. This is the regression guard REQ-5.1 requires.

**Test location:**
`klai-portal/backend/tests/test_admin_users.py::test_offboard_user_does_not_wipe_other_org_memberships`

---

## A-2: Invite role -> Zitadel grant mapping

**REQs covered:** REQ-2.1, REQ-2.2, REQ-5.2

**Purpose:** `invite_user` MUST pass the Zitadel role matching the
admin's chosen portal role, not the hardcoded `org:owner`.

**Given:**
- Admin `caller` is authenticated against `org_a` and passes
  `_require_admin`.
- `zitadel.invite_user` is patched to return
  `{"userId": "new-user-<role>"}` without hitting the network.
- `zitadel.grant_user_role` is patched to capture the `role` argument.
- Seat limit is not reached.

**When (parametrised — one test, three cases):**
For each `portal_role` in `["admin", "group-admin", "member"]`:
- `POST /api/admin/users/invite` with body
  `{"email": "<role>@example.com", "first_name": "A", "last_name": "B",
    "role": <portal_role>, "preferred_language": "nl"}`.

**Then:**
- Response is `200 OK`.
- `zitadel.grant_user_role` is called exactly once per request.
- The `role` argument passed to `grant_user_role` matches the REQ-2.2
  mapping. Expected values (subject to REQ-3 Zitadel-console
  confirmation during implementation):
  - `portal_role="admin"`     -> grant `"org:owner"`
  - `portal_role="group-admin"` -> grant `"org:group-admin"`
  - `portal_role="member"`    -> grant `"org:member"`
- In all cases the portal row `portal_users.role` matches `portal_role`.

**Failure mode under current code:**
- All three cases pass `role="org:owner"` to `grant_user_role`
  regardless of input → assertions on member / group-admin cases fail
  red. This is the regression guard REQ-5.2 requires.

**Test location:**
`klai-portal/backend/tests/test_admin_users.py::test_invite_user_grants_portal_role_to_zitadel`

**Note on mapping values:** The exact strings are the REQ-3 /
`research.md` §7 open question. If the Zitadel project does not
configure a matching key (e.g. `org:group-admin` is not present),
REQ-2 mapping uses the configured value; the test assertion is
updated to match. Either way the invariant holds: NO CASE maps to
`org:owner` except `portal_role="admin"`.

---

## A-3: Cross-org request from a member JWT returns 403

**REQs covered:** REQ-4.1, REQ-4.3, REQ-4.4, REQ-5.3

**Purpose:** A user invited with `role="member"` MUST NOT trigger the
admin bypass in retrieval-api's `verify_body_identity`. A body
carrying a different `org_id` than the JWT's `resourceowner` MUST
return HTTP 403.

**Given:**
- retrieval-api is running against a mocked JWKS with a test
  `RS256` key.
- A JWT is issued with:
  - `sub="user-member-1"`
  - `resourceowner="101"` (org A)
  - `"urn:zitadel:iam:org:project:roles": {"org:member": {}}`
    (the REQ-2 mapping for portal role `member`).
  - `aud=<settings.zitadel_api_audience>`, `iss=<settings.zitadel_issuer>`.
- Internal-secret auth is NOT used for this request.

**When:**
- `POST /retrieve` (or any endpoint calling `verify_body_identity`)
  with `Authorization: Bearer <jwt>` and body
  `{"org_id": "102", "user_id": "user-member-1", ...}`.

**Then:**
- Response is `403 Forbidden`.
- Response body contains `{"error": "org_mismatch"}` (per
  `verify_body_identity` line 352).
- Metric `cross_org_rejected_total` incremented by 1.
- Structured log `event="cross_org_rejected"` emitted with
  `reason="org_mismatch"`, `auth_method="jwt"`, truncated `jwt_sub_hash`.

**Negative case (guard):**
- Same request but with JWT carrying
  `"urn:zitadel:iam:org:project:roles": {"admin": {}}` → response is
  `200 OK` (admin bypass is the SPEC-SEC-010 REQ-3.1 behaviour and
  is intentional for genuine admins). This is the control case
  confirming the bypass still works as specified for actual admin
  roles.

**Failure mode under current code (when combined with A-2 defect):**
- Because current code grants `org:owner` for every invite, the JWT
  for a nominal "member" would carry `{"org:owner": {}}`.
  `_extract_role` returns `"org:owner"` (not `"admin"` today), so
  today the 403 still fires — but only by chance. If an operator
  ever adds `"org:owner"` to the bypass set, the cross-org check is
  silently removed. REQ-4.1 + REQ-4.4 lock the bypass set to values
  the mapping can reach AND test that `member` never hits the bypass.

**Test location:**
`klai-retrieval-api/tests/test_auth_middleware.py::test_verify_body_identity_rejects_cross_org_for_member_role`

---

## A-4: RLS second-layer catches a missing-org-id query

**REQs covered:** REQ-6.1, REQ-6.2

**Purpose:** Confirm that PostgreSQL RLS is the working second layer
on category-D tables. A direct SELECT without setting the tenant GUC
MUST be constrained (zero rows under the permissive branch or raise
under the strict branch), proving defence-in-depth is live.

**Given:**
- Test DB seeded with two orgs (`org_a` id=101, `org_b` id=102) and
  at least one `portal_group_kb_access` row in each.
- A fresh session is opened against the `portal_api` role (NOT the
  `klai` superuser role).
- `set_tenant` has NOT been called. `app.current_org_id` GUC is NULL.

**When:**
- `SELECT count(*) FROM portal_group_kb_access` is executed via the
  raw SQLAlchemy session (or `asyncpg.connect` using the `portal_api`
  DSN).

**Then (accept either outcome — the policy governs):**
- **Outcome A (permissive-on-missing branch):** query returns 0 rows
  even though two exist. RLS is filtering via
  `USING (_rls_current_org_id() IS NULL OR org_id = _rls_current_org_id())`
  with `IS NULL` taking the permissive path — but the strict variant
  on category D currently returns zero rows for NULL GUC when the
  policy uses `_rls_current_org_id() IS NULL OR …` in a filter
  context. Assertion: returned count < actual row count.
- **Outcome B (strict raise):** query raises
  `asyncpg.exceptions.InsufficientPrivilegeError` with message
  matching `RLS: app.current_org_id is not set`. This is the
  behaviour documented in `portal-backend.md` "Post-commit db.refresh
  on RLS tables" and is governed by the GUC-NOT-NULL branch of the
  category-D policy.

**Then (additional positive control):**
- Set the GUC to `org_a`'s id (`SET LOCAL app.current_org_id = 101`),
  re-run the SELECT. Count MUST equal the seeded count for org A
  (non-zero) and MUST be strictly less than total (ie. `org_b` rows
  filtered).
- Set the GUC to `org_b`'s id, re-run. Count MUST equal the seeded
  count for org B.

**And:**
- The test file contains an explicit comment: "RLS does NOT protect
  `portal_group_memberships` — see `portal-security.md` and
  SPEC-SEC-TENANT-001 REQ-6.2. A-1 covers that table via the
  code-layer join, not RLS."

**Failure mode:**
- If the NULL-GUC query returns the full cross-org rowset without
  raising, RLS is NOT enforcing on category-D tables. This is a
  defence-in-depth regression that blocks the SPEC and escalates
  to `portal-security.md` policy review.

**Test location:**
`klai-portal/backend/tests/test_rls_audit.py::test_rls_blocks_cross_org_query_without_tenant_context`
(extends existing RLS audit tests rather than adding a new file).

---

## Aggregate success criterion

All four scenarios pass (green) in CI on the target branch before the
PR may merge. A-1 and A-2 must pass red against the pre-fix codebase
and green after the REQ-1/REQ-2 implementation — this is the
reproduction-first bug fix rule (CLAUDE.md safeguard #4).

No existing test in `klai-portal/backend/tests/` or
`klai-retrieval-api/tests/` regresses. The SPEC-SEC-010 JWT cross-org
suite continues to pass unchanged.

---

## A-5: SyncRun model carries an org_id column

**REQs covered:** REQ-7.1, REQ-7.2

**Purpose:** The `SyncRun` SQLAlchemy model and `connector.sync_runs`
table MUST declare `org_id` as a `NOT NULL VARCHAR(255)` column with
an index, as the schema foundation for REQ-7.3 org-scoped filtering.
The type matches the existing `Connector.org_id` shape (Zitadel
resourceowner, set by migration `003_org_id_string`).

**Given:**
- The Alembic migration adding `org_id` to `sync_runs` has been
  applied against a connector DB fixture.
- The `SyncRun` SQLAlchemy model has been updated to match.

**When:**
- A `grep` over `klai-connector/app/models/sync_run.py` for
  `org_id`.
- A DB-level `\d connector.sync_runs` (or equivalent SQLAlchemy
  `inspect`) is issued.

**Then:**
- `grep -n "org_id" klai-connector/app/models/sync_run.py` returns at
  least one line of the form
  `org_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)`.
- DB introspection confirms the column exists, is
  `character varying(255)`, is `NOT NULL`, and is indexed (index name
  `ix_sync_runs_org_id` per the migration).
- A model-load test `SyncRun(connector_id=<uuid>, status="running",
  org_id="org-a-resourceowner")` instantiates successfully; omitting
  `org_id` raises `IntegrityError` on flush.

**Failure mode under current code:**
- The grep returns zero results; model load with `org_id=` kwarg
  raises `TypeError: 'org_id' is an invalid keyword argument for
  SyncRun`. This is the regression guard proving REQ-7.2 landed.

**Test location:**
`klai-connector/tests/test_sync_run_model.py::test_sync_run_requires_org_id`

---

## A-6: Cross-tenant sync-run fetch returns 404

**REQs covered:** REQ-7.3, REQ-7.4, REQ-7.5

**Purpose:** A portal caller that fetches `/syncs/{run_id}` (or the
list/trigger equivalents) for a run belonging to org B while asserting
org A via `X-Org-ID` MUST receive HTTP 404 — never 200, never 403.

**Given:**
- Two connectors seeded in the connector DB (`connector.connectors`):
  - `conn_A` (id=`11111111-...`, `org_id="org-a-resourceowner"`).
  - `conn_B` (id=`22222222-...`, `org_id="org-b-resourceowner"`).
- Two `SyncRun` rows seeded in the connector DB:
  - `run_A` (connector_id=`conn_A.id`,
    `org_id="org-a-resourceowner"`, status="completed").
  - `run_B` (connector_id=`conn_B.id`,
    `org_id="org-b-resourceowner"`, status="completed").
- REQ-7 implementation has landed: handlers filter on
  `SyncRun.org_id`; `_require_portal_org_id(request)` reads
  `X-Org-ID`.
- The test client holds `PORTAL_CALLER_SECRET` and can set arbitrary
  headers.

**When (three sub-cases):**
- **GET detail**: `GET /connectors/{conn_B.id}/syncs/{run_B.id}` with
  `X-Org-ID: org-a-resourceowner` (caller claims org A).
- **GET list**: `GET /connectors/{conn_B.id}/syncs` with
  `X-Org-ID: org-a-resourceowner`.
- **POST trigger**: `POST /connectors/{conn_B.id}/sync` with
  `X-Org-ID: org-a-resourceowner`.

**Then:**
- All three requests return HTTP 404 with body
  `{"detail": "Sync run not found"}` (detail handler) or
  `{"detail": "Connector not found"}` (list/trigger handlers — when
  the org_id filter eliminates every match, the shape is
  "nothing exists" not "forbidden", per REQ-7.5 and
  `portal-security.md` "never leak existence").
- VictoriaLogs for the connector service shows NO
  `event="sync_missing_org_id"` entries (the header was present),
  and no DB row was created or mutated (verified by
  `SELECT COUNT(*) FROM connector.sync_runs WHERE id =
  {run_B.id}` → 1 unchanged; no new row with
  `org_id="org-a-resourceowner"`).

**Positive control:**
- Same three requests with `X-Org-ID: org-b-resourceowner` (the
  correct org for `conn_B`) return 200 / 200 / 202 respectively. This
  proves the filter is tenant-scoped, not blanket-blocking.

**Failure mode under current code (pre-REQ-7):**
- All three requests return 200 / 200 / 202 regardless of
  `X-Org-ID` — the filter is absent. This is the regression guard
  the test exists to trigger red.

**Test location:**
`klai-connector/tests/test_sync_routes_org_scoping.py::test_cross_tenant_sync_fetch_returns_404`

---

## A-7: Backfill migration produces zero NULL org_id rows

**REQs covered:** REQ-7.1

**Purpose:** The Alembic migration adding `org_id` to
`connector.sync_runs` MUST backfill every pre-existing row from the
sibling `connector.connectors.org_id` (intra-DB) without leaving any
NULLs, so that the subsequent `NOT NULL` alter succeeds on real
multi-tenant data.

**Given:**
- Multi-tenant fixture seeded in the connector DB:
  - `connector.connectors` rows: 10 connectors split across
    `org_id="org-a-resourceowner"` (5 connectors) and
    `org_id="org-b-resourceowner"` (5 connectors), plus 2 connectors
    in `org_id="org-c-resourceowner"`.
- Connector DB fixture seeded with `connector.sync_runs` rows
  (before migration): at least 3 runs per connector, ~36 rows total,
  all with the current schema (no `org_id` column).
- Orphan sanity: ONE `sync_runs` row exists for a `connector_id`
  that is NOT in `connector.connectors` (parent connector deleted
  upstream). The runbook pre-step deletes this orphan before the
  migration via
  `DELETE FROM connector.sync_runs WHERE connector_id NOT IN
  (SELECT id FROM connector.connectors)`.

**When:**
- The Alembic migration is applied. The backfill statement is a
  single intra-DB UPDATE:
  `UPDATE connector.sync_runs r SET org_id = c.org_id FROM
  connector.connectors c WHERE r.connector_id = c.id`.

**Then:**
- `SELECT COUNT(*) FROM connector.sync_runs WHERE org_id IS NULL`
  returns `0`.
- `SELECT COUNT(*) FROM connector.sync_runs` returns the same total
  as before the migration MINUS the one orphan deleted by the
  runbook pre-step.
- For every surviving row: `sync_runs.org_id` matches
  `connector.connectors.org_id` when joined on `connector_id`.
  Verified by a post-migration audit query:
  ```sql
  SELECT r.id, r.org_id, c.org_id AS connector_org_id
  FROM connector.sync_runs r
  LEFT JOIN connector.connectors c ON c.id = r.connector_id
  WHERE r.org_id IS DISTINCT FROM c.org_id;
  ```
  returns zero rows.
- The `ALTER COLUMN ... SET NOT NULL` step of the migration
  completes without error.

**Failure mode (guard against incomplete backfill):**
- If any row has `org_id IS NULL`, the NOT NULL alter raises
  `NotNullViolation`; the migration transaction aborts and leaves
  the schema at the pre-NOT-NULL step. This is the migration's own
  safety net — the acceptance test reproduces it deliberately (by
  skipping the backfill step in a spike fixture) and asserts the
  alter fails red.

**Test location:**
`klai-connector/tests/test_migration_add_org_id_to_sync_runs.py::test_backfill_covers_every_multi_tenant_row`

Runs under the migration-test harness (alembic-verify or
equivalent) against a disposable Postgres container. Requires the
portal DB fixture to be seedable in the same container or a
parallel container.

---

## A-8: Missing X-Org-ID returns 400 after transition period

**REQs covered:** REQ-7.6, REQ-8.1, REQ-8.5

**Purpose:** Once `sync_require_org_id=True` is set in the connector
config, a portal call without `X-Org-ID` MUST return HTTP 400 —
proving the transition period has closed and the org assertion is
mandatory. During the transition period, the same call MUST succeed
(or degrade without filtering) and emit a WARN log event.

**Given (case 8.a — transition period, flag OFF):**
- Connector config has `sync_require_org_id=False` (default).
- Portal caller holds `PORTAL_CALLER_SECRET`.
- `conn_A` exists with `org_id="org-a-resourceowner"`; `run_A`
  exists tied to `conn_A`.

**When:**
- `GET /connectors/{conn_A.id}/syncs` is called with the portal
  bearer token but NO `X-Org-ID` header.

**Then (case 8.a):**
- Response is HTTP 200 with the `run_A` row present (backward
  compatibility preserved during transition).
- VictoriaLogs shows exactly one structured event
  `event="sync_missing_org_id"` with
  `connector_id="{conn_A.id}"`, `level="warning"`,
  `service="klai-connector"`.

**Given (case 8.b — transition closed, flag ON):**
- Connector config has `sync_require_org_id=True` (flipped after
  portal has deployed REQ-8 and VictoriaLogs shows zero
  `sync_missing_org_id` events for the past N hours — runbook).
- Same seed as 8.a.

**When:**
- Same request: `GET /connectors/{conn_A.id}/syncs` with portal
  bearer but NO `X-Org-ID`.

**Then (case 8.b):**
- Response is HTTP 400 with body
  `{"detail": "X-Org-ID header required"}`.
- No `sync_runs` row is read or mutated.
- VictoriaLogs shows a structured error event (no longer warning)
  rejecting the request.

**Portal-side control (REQ-8.4, same test file):**
- `klai_connector_client.trigger_sync(connector_id,
  org_id="org-a-resourceowner")` is called with a patched httpx
  transport. The transport-level assertion confirms the outbound
  request headers contain `X-Org-ID: org-a-resourceowner` AND
  `Authorization: Bearer <portal-secret>`.
- Calling the same client method without the `org_id` parameter
  raises `TypeError` (parameter is required, not defaulted) —
  proving portal-side code cannot forget the header.

**Failure mode:**
- Case 8.b returning 200 means the flag wire-up is broken and
  REQ-7.6 did not land correctly.
- The portal-side test returning a request without `X-Org-ID`
  means REQ-8.1 regressed.

**Test location:**
`klai-connector/tests/test_sync_routes_org_scoping.py::test_missing_x_org_id_transition_and_enforcement`
and
`klai-portal/backend/tests/test_klai_connector_client.py::test_portal_sync_client_forwards_org_id`
