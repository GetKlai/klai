# SPEC-SEC-TENANT-001 — Research

Codebase analysis supporting SPEC-SEC-TENANT-001. Captures the current
state of tenant scoping, role mapping, and RLS coverage relevant to the
two findings in scope (audit finding #5 and #10).

Working directory referenced: `c:\Users\markv\stack\02 - Voys\Code\klai`.

---

## 1. Offboarding delete — actual vs intended scope

### Current code (`klai-portal/backend/app/api/admin/users.py:413-458`)

```python
@router.post("/users/{zitadel_user_id}/offboard", ...)
async def offboard_user(zitadel_user_id, credentials, db):
    caller_id, org, caller_user = await _get_caller_org(credentials, db)
    _require_admin(caller_user)

    # Fetches the user, org-scoped — this part is correct.
    result = await db.execute(
        select(PortalUser).where(
            PortalUser.zitadel_user_id == zitadel_user_id,
            PortalUser.org_id == org.id,
        )
    )
    user = result.scalar_one_or_none()
    if not user: raise HTTPException(404)
    if user.status == "offboarded": raise HTTPException(409)

    # FINDING #5: no org_id filter. PortalGroupMembership has no org_id
    # column, so this wipes every membership U has, across EVERY tenant.
    await db.execute(
        delete(PortalGroupMembership).where(
            PortalGroupMembership.zitadel_user_id == zitadel_user_id
        )
    )

    # This one is correct — PortalUserProduct has an org_id column and
    # it is filtered.
    await db.execute(
        delete(PortalUserProduct).where(
            PortalUserProduct.zitadel_user_id == zitadel_user_id,
            PortalUserProduct.org_id == org.id,
        )
    )
    ...
```

### Why this is broken

`PortalGroupMembership` (`app/models/groups.py:39-47`):

```python
class PortalGroupMembership(Base):
    __tablename__ = "portal_group_memberships"
    id: Mapped[int] = mapped_column(primary_key=True)
    group_id: Mapped[int] = mapped_column(ForeignKey("portal_groups.id", ondelete="CASCADE"), nullable=False)
    zitadel_user_id: Mapped[str] = mapped_column(String(64), nullable=False)
    is_group_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    joined_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
```

No `org_id` column. The tenant is reachable only via the parent
`PortalGroup.org_id` through the `group_id` FK. A direct `delete` keyed on
`zitadel_user_id` therefore crosses every tenant the user has ever been
added to. Per `portal-security.md`:

> Outside the classification: `portal_group_memberships` has no RLS
> policy — membership rows inherit their tenant via the parent group's FK.

RLS cannot save us on this table. The code-layer join is the only guard.

### Canonical fix shape

Two equivalent patterns match the existing codebase style:

```python
# Pattern A — subselect join (single round-trip)
await db.execute(
    delete(PortalGroupMembership).where(
        PortalGroupMembership.group_id.in_(
            select(PortalGroup.id).where(PortalGroup.org_id == org.id)
        )
    )
)

# Pattern B — select ids, then delete (two round-trips, explicit count)
ids = (await db.execute(
    select(PortalGroupMembership.id)
    .join(PortalGroup, PortalGroup.id == PortalGroupMembership.group_id)
    .where(
        PortalGroupMembership.zitadel_user_id == zitadel_user_id,
        PortalGroup.org_id == org.id,
    )
)).scalars().all()
if ids:
    await db.execute(delete(PortalGroupMembership).where(PortalGroupMembership.id.in_(ids)))
```

Pattern A is preferred for parity with the positive reference
(`PortalUserProduct` delete already in the handler). Pattern B is a
fallback if the subselect runs into RLS interactions that the smoke test
surfaces during implementation.

---

## 2. Inventory of delete/update in `app/api/admin/`

Source: `Grep` over `klai-portal/backend/app/api/admin/` for
`\bdelete\(|\bupdate\(`. Classified by whether the statement is
explicitly org-scoped, relies on a prior ORM fetch that is org-scoped,
or is a cross-tenant surface.

| File:line | Statement | Scope verdict |
|---|---|---|
| `users.py:311-343` (`remove_user`) | `await db.delete(user)` | SAFE — `user` was fetched with `PortalUser.org_id == org.id` |
| `users.py:436` (`offboard_user`) | `delete(PortalGroupMembership).where(zid == …)` | **BROKEN — finding #5, fixed by REQ-1** |
| `users.py:437-442` (`offboard_user`) | `delete(PortalUserProduct).where(zid == …, org_id == org.id)` | SAFE — org-scoped |
| `users.py:272` (`update_user_role`) | `user.role = body.role; await db.commit()` | SAFE — `user` was fetched with `org_id == org.id` |
| `users.py:246` (`update_user`) | `user.preferred_language = body.preferred_language; commit` | SAFE — same |
| `users.py:408` (`reactivate_user`) | `user.status = "active"; commit` | SAFE — same |
| `users.py:371` (`suspend_user`) | `user.status = "suspended"; commit` | SAFE — same (fetched org-scoped, not shown above) |
| `products.py:148-170` (`delete_user_product`) | `await db.delete(row)` | Depends on `row` fetch. Grep shows the row is fetched with `zitadel_user_id ==` filter; must confirm org scoping during REQ-1 implementation (not in scope unless finding surfaces) |
| `settings.py:113` (`update_settings`) | `await db.delete(row)` | Admin-settings rows are org-scoped by fetch; unchanged scope |
| `settings.py:128` (`update_settings`) | `await db.delete(row)` | Same |
| `domains.py:133-156` (`delete_domain`) | `await db.delete(domain)` | Safe when `domain` is org-scoped at fetch; unchanged scope |

No other `delete()` or `update()` statement in `admin/` exhibits the
finding-#5 shape (direct bulk delete keyed only on a user-supplied
identifier without tenant join). The offboarding delete is the singleton
defect. REQ-1 fixes it; REQ-5.1 adds the regression guard.

A follow-up SPEC (out of scope here) could migrate the remaining
`delete(row)` sites to go through `_get_{model}_or_404` helpers for
consistency. The present inventory shows they are already functionally
safe.

---

## 3. Role mapping chain — body.role -> Zitadel grant -> JWT claim -> _extract_role

### Step 1: `InviteRequest.role` (Pydantic Literal)

`klai-portal/backend/app/api/admin/users.py:51-56`:

```python
class InviteRequest(BaseModel):
    email: EmailStr
    first_name: str
    last_name: str
    role: Literal["admin", "group-admin", "member"] = "member"
    preferred_language: Literal["nl", "en"] = "nl"
```

Frontend exposes three radio options. The Literal is the contract.

### Step 2: Zitadel grant call (current — broken)

`users.py:161-166`:

```python
await zitadel.grant_user_role(
    org_id=settings.zitadel_portal_org_id,
    user_id=zitadel_user_id,
    role="org:owner",   # <-- hardcoded, regardless of body.role
)
```

`app/services/zitadel.py:99`:

```python
async def grant_user_role(self, org_id: str, user_id: str, role: str) -> None:
    """Assign a project role to a specific user (user grant)."""
    resp = await self._http.post(...)
```

Pure pass-through. The argument value becomes the Zitadel role key.

### Step 3: JWT claim shape

`klai-retrieval-api/retrieval_api/middleware/auth.py:60-61` documents the
claim name:

```python
_ZITADEL_ROLES_CLAIM = "urn:zitadel:iam:org:project:roles"
```

Dev fixture (`klai-portal/backend/app/services/zitadel.py:193-196`)
shows the shape:

```python
return {
    "sub": settings.auth_dev_user_id,
    "urn:zitadel:iam:org:project:roles": {"org:owner": {}},
}
```

So Zitadel produces a dict keyed on the granted role string. Today every
invited user gets `{"org:owner": {}}` — regardless of the admin choice.

### Step 4: `_extract_role` bypass

`klai-retrieval-api/retrieval_api/middleware/auth.py:211-230`:

```python
def _extract_role(payload: dict[str, Any]) -> str | None:
    roles_claim = payload.get(_ZITADEL_ROLES_CLAIM)
    if isinstance(roles_claim, dict) and roles_claim:
        if "admin" in roles_claim or "org_admin" in roles_claim:
            return "admin"
        return next(iter(roles_claim))
    if isinstance(roles_claim, list) and roles_claim:
        if "admin" in roles_claim or "org_admin" in roles_claim:
            return "admin"
        return roles_claim[0]
    role = payload.get("role")
    return role if isinstance(role, str) else None
```

And `verify_body_identity` at lines 321-367 skips cross-org checks when
`auth.role == "admin"`:

```python
if auth.role == "admin":
    return
```

### The defect chain

Today:

1. Admin picks `role="member"` in the invite UI.
2. Portal stores `role="member"` on `portal_users`.
3. Portal grants Zitadel `role="org:owner"` — **mismatch**.
4. User's JWT carries `{"org:owner": {}}`.
5. retrieval-api's `_extract_role` sees neither `admin` nor `org_admin`
   literally, so it returns `"org:owner"` as the role label. No bypass
   today. **But** if an operator adds `"org:owner"` to the bypass set
   in a future cleanup (plausible: "owner is admin-ish"), every invited
   user becomes admin in retrieval-api.

The audit's finding #10 severity is "HIGH (config-dep CRITICAL)"
precisely because the mismatch is a time-bomb: the code is one line
away from full bypass.

Under REQ-2/REQ-3/REQ-4:

1. Admin picks `role="member"`.
2. Portal grants Zitadel the mapped member role (likely `org:member`).
3. JWT carries `{"org:member": {}}`.
4. `_extract_role` returns the member claim, NOT "admin".
5. `verify_body_identity` enforces cross-org checks as normal.

`_extract_role`'s admin-equivalent set (`admin`, `org_admin`) gets
audited against the REQ-3 mapping. If neither value is reachable through
the invite flow, REQ-4 removes them from the bypass set.

### Required Zitadel configuration check

Before REQ-2 lands, one open question: are `org:group-admin` and
`org:member` actually configured as project roles in the klai Zitadel
tenant? The codebase does not declare Zitadel configuration (Zitadel
Actions / Management API state lives outside the repo). REQ-3 research
step confirms this via a manual Zitadel-console check before REQ-2
mapping lands. If a needed role is missing, SPEC pauses and a dependency
on a Zitadel config change is declared.

---

## 4. RLS coverage — who protects what

From `portal-security.md` (4-category RLS framework, canonical):

| Category | Tables | Pattern |
|---|---|---|
| A (permissive-on-missing) | `portal_users`, `portal_connectors` | `USING (org_id = GUC OR current_setting IS NULL)` |
| B (SELECT-public) | `widgets`, `widget_kb_access`, `partner_api_keys`, `partner_api_key_kb_access` | SELECT `USING (true)`, other strict |
| C (INSERT-permissive) | `portal_audit_log`, `product_events`, `portal_feedback_events` | INSERT permissive, SELECT org-scoped |
| D (strict + bypass) | `portal_knowledge_bases`, `portal_groups`, `portal_group_products`, `portal_group_kb_access`, `portal_kb_tombstones`, `portal_user_kb_access`, `portal_user_products`, `portal_retrieval_gaps`, `portal_taxonomy_nodes`, `portal_taxonomy_proposals`, `vexa_meetings` | `USING (_rls_current_org_id() IS NULL OR org_id = _rls_current_org_id())` |
| **Outside classification** | **`portal_group_memberships`** | **No RLS. Tenancy inherited via parent group's FK.** |

### Implication for this SPEC

- `PortalUserProduct` (`portal_user_products`, category D) — direct
  SQL without tenant GUC set would be blocked or return zero rows.
  The REQ-1 handler already has explicit `org_id` filter on this table,
  so RLS is defence-in-depth, not the primary guard.
- `PortalGroupMembership` (`portal_group_memberships`, outside
  classification) — direct SQL is NOT blocked by RLS. The parent-group
  join added by REQ-1 is the primary guard. RLS cannot substitute here.
- `PortalGroup` (`portal_groups`, category D) — the subselect in REQ-1
  Pattern A reads from this table. Under normal request context
  `_get_caller_org` has called `set_tenant`, so the GUC is set and the
  subselect returns only the caller's org's groups. Even if a future
  refactor drops the explicit `PortalGroup.org_id == org.id` filter,
  the category-D policy constrains the subselect to the caller's org.

REQ-6 verifies, not assumes, that RLS is in effect by running a live
cross-org SELECT with the tenant GUC cleared. The test is the
verification; there is no separate code or SQL change in this SPEC.

---

## 5. Canonical `_get_{model}_or_404` pattern — where it is used

From `portal-security.md`:

> All tenant-scoped models (groups, knowledge_bases, connectors, etc.)
> must be accessed via a `_get_{model}_or_404(id, org_id, db)` helper.

Inventory (non-exhaustive, from `Grep` output):

| File | Helper | Callsites |
|---|---|---|
| `admin_widgets.py:107` | `_get_widget_or_404` | 3 |
| `admin_api_keys.py:104` | `_get_key_or_404` | 3 |
| `app_knowledge_bases.py:229` | `_get_kb_or_404` | 13 |
| `app_knowledge_bases.py:35` | `_get_non_system_group_or_404` | 1 |
| `app_templates.py:122` | `_get_template_or_404` | 3 |
| `groups.py:35` | `_get_group_or_404` | 3 |
| `knowledge_bases.py:24` | `_get_kb_or_404` | 5 |
| `taxonomy.py:131` | `_get_kb_or_404` | 11 |

Observed properties:

- All helpers take `(resource_id, org_id, db)` in that order.
- All helpers use `select(Model).where(Model.id == id, Model.org_id == org_id)`
  (or `.slug ==`, `.key ==`, etc.).
- All helpers `raise HTTPException(404)` on miss — never 403, per
  `portal-security.md` "never leak existence".

### Where the pattern is NOT used inside admin

`admin/users.py` does not expose a `_get_user_or_404` helper. Each of
the seven endpoints (update, update_user_role, resend_invite,
remove_user, suspend_user, reactivate_user, offboard_user) repeats the
same org-scoped `select(PortalUser).where(zid, org_id)` block inline.

Extracting a helper is tempting but **out of scope** for this SPEC — it
would touch six handlers unrelated to finding #5 or #10, violating the
`minimal-changes` rule. A dedicated refactor SPEC can follow.

### Relevance to REQ-1

The REQ-1 fix does not introduce a new helper. It changes the
offboarding delete to either embed a subselect against `PortalGroup`
(Pattern A) or fetch the group-scoped ids first (Pattern B). Both
patterns preserve the "join on org-scoped parent" shape that the
canonical helper would enforce, without creating a new helper
ceremony for a single callsite.

---

## 6. Other consumers of the Zitadel roles claim

Verified by `Grep` for `_ZITADEL_ROLES_CLAIM` and `urn:zitadel:iam:org:project:roles`
across the monorepo:

- `klai-retrieval-api/retrieval_api/middleware/auth.py` —
  `_extract_role` + `verify_body_identity` (finding #10 downstream).
- `klai-research-api` — reads the claim but does not branch on
  `admin` / `org_admin`. Unaffected by REQ-2 mapping changes.
- `klai-portal/backend/app/services/zitadel.py` — dev fixture only.
- No other production consumer branches on the admin value.

This confirms REQ-4's scope: retrieval-api is the only downstream with
an admin-bypass that depends on the invite-time role. The REQ-3
document records this so future additions are visible in code review.

---

## 7. Open questions parked for implementation phase

1. Do we prefer Pattern A (subselect) or Pattern B (select-then-delete)
   for REQ-1? Recommend A (matches PortalUserProduct pattern, single
   query). Decide during `/moai run` based on RLS-smoke-test output
   under category-D policy.
2. What is the exact Zitadel role string for the portal `group-admin`
   role? `org:group-admin` is the natural guess; confirm during REQ-3
   Zitadel-console check before REQ-2 lands.
3. Does REQ-4's removal of `org_admin` from the admin-equivalent set
   change behaviour for existing users? Audit existing JWTs in
   VictoriaLogs (`service:retrieval-api AND auth_accepted AND role:*`)
   to confirm no production user currently carries that claim value.

These are implementation-phase decisions, documented here so the Run
phase does not have to re-derive them.

---

## 8. Internal-wave additions (2026-04-24): klai-connector sync-route cross-tenant exposure

Added after the original v0.2.0 research pass, during the internal-wave
audit of service-to-service boundaries. Triggered by the observation
that `request.state.org_id = None` on every portal-authenticated call
(see `klai-connector/app/middleware/auth.py:118-121`) combined with the
fact that none of the sync-route handlers carries an org filter.

### 8.1 SyncRun schema today — no org_id column

`klai-connector/app/models/sync_run.py` declares:

```python
class SyncRun(Base):
    __tablename__ = "sync_runs"
    __table_args__ = {"schema": "connector"}

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    connector_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
        index=True,
        # No ForeignKey — connector_id is a portal UUID, portal is source of truth.
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    started_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    documents_total: Mapped[int] = mapped_column(Integer, default=0)
    documents_ok: Mapped[int] = mapped_column(Integer, default=0)
    documents_failed: Mapped[int] = mapped_column(Integer, default=0)
    bytes_processed: Mapped[int] = mapped_column(BigInteger, default=0)
    error_details: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    cursor_state: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    quality_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
```

Grep for `org_id` against `klai-connector/app/models/` shows exactly one
hit — `Connector.org_id` (in `app/models/connector.py`). `SyncRun`
inherits no tenancy at all. The comment "portal is source of truth"
explains the absence of a `connector_id` FK but has the side effect of
removing any implicit tenant anchor — `SyncRun` is genuinely
tenant-free today.

### 8.2 All three sync-route handlers and their filter clauses

From `klai-connector/app/routes/sync.py` (lines 30-114, production file
at the time of this audit):

```python
# 1. trigger_sync — sync.py:30-75
@router.post("/connectors/{connector_id}/sync", status_code=202, response_model=SyncRunResponse)
async def trigger_sync(connector_id, request, background_tasks, session):
    _require_portal_call(request)
    # Active-sync guard: filter on connector_id ONLY.
    active_run_result = await session.execute(
        select(SyncRun).where(
            SyncRun.connector_id == connector_id,
            SyncRun.status == SyncStatus.RUNNING,
        )
    )
    ...
    sync_run = SyncRun(connector_id=connector_id, status=SyncStatus.RUNNING)
    # No org_id persisted.

# 2. list_sync_runs — sync.py:78-97
@router.get("/connectors/{connector_id}/syncs", response_model=list[SyncRunResponse])
async def list_sync_runs(connector_id, request, limit, session):
    _require_portal_call(request)
    result = await session.execute(
        select(SyncRun)
        .where(SyncRun.connector_id == connector_id)  # only filter
        .order_by(SyncRun.started_at.desc())
        .limit(min(limit, 100))
    )

# 3. get_sync_run — sync.py:100-114
@router.get("/connectors/{connector_id}/syncs/{run_id}", response_model=SyncRunResponse)
async def get_sync_run(connector_id, run_id, request, session):
    _require_portal_call(request)
    sync_run = await session.get(SyncRun, run_id)
    if sync_run is None or sync_run.connector_id != connector_id:
        raise HTTPException(status_code=404, detail="Sync run not found")
```

None of the three handlers reads a `org_id` from request state (it is
`None` on portal calls anyway — see middleware behaviour in 8.3).
Anyone in possession of `PORTAL_CALLER_SECRET` — in portal env, in CI
secrets, in Docker runtime env — can issue:

```
curl -H "Authorization: Bearer $PORTAL_CALLER_SECRET" \
     https://connector.internal/connectors/<any-uuid>/syncs
```

and list, trigger, or read sync runs for any tenant whose `connector_id`
they can guess or enumerate.

### 8.3 `request.state.org_id` is None on portal calls

From `klai-connector/app/middleware/auth.py:112-121`:

```python
# Portal service-to-service calls bypass Zitadel introspection.
if self._portal_secret and hmac.compare_digest(token.encode("utf-8"), self._portal_secret.encode("utf-8")):
    request.state.from_portal = True
    request.state.org_id = None  # no user org in portal calls
    return await call_next(request)
```

This is intentional — the middleware cannot infer which tenant a portal
call is on behalf of, because the portal secret authenticates the
channel, not the user. The consequence is that
`_require_portal_call(request)` downstream has no org context to add.

The fix is NOT to derive `org_id` inside the middleware (the channel
authenticates the portal, not a specific tenant). The fix is for the
portal to assert the tenant via a request header
(`X-Org-ID`) on every proxied sync call — and for the connector to
trust that header, under REQ-7, only because
`_require_portal_call` already proved the caller is the portal. This
is the same model that SPEC-SEC-IDENTITY-ASSERT-001 drafts for the
broader portal->service boundary.

### 8.4 Sibling (non-sync) routes are already org-scoped — positive reference

From `klai-connector/app/routes/connectors.py`, the connector CRUD
routes use a `get_org_id(request)` dependency from `app/routes/deps.py`
which reads the Zitadel-introspected `request.state.org_id`:

```python
@router.get("", response_model=list[ConnectorResponse])
async def list_connectors(request, session):
    org_id = get_org_id(request)
    result = await session.execute(
        select(Connector).where(Connector.org_id == org_id).order_by(Connector.created_at.desc())
    )
```

These routes work under the non-portal (user-authenticated) path. They
are correct as-is. The sync-route defect is specific to the portal-only
call path, where `request.state.org_id` is `None` by design.

### 8.5 Portal-side proxying today — connector_id only, no org_id

From `klai-portal/backend/app/api/connectors.py:451-502` (`trigger_sync`
handler) and `505-532` (`list_sync_runs` handler), the portal fetches
the authenticated session (via `_get_caller_org(credentials, db)`)
and verifies KB ownership, then calls:

```python
sync_run = await klai_connector_client.trigger_sync(connector_id)
```

```python
return await klai_connector_client.get_sync_runs(connector_id, limit=limit)
```

`klai_connector_client` sends the portal-caller bearer token but no
tenant header. So even though the portal KNOWS the tenant (`org.id`
from `_get_caller_org`), that information is not propagated to the
connector. REQ-8 closes this gap.

### 8.6 Migration plan (Alembic)

Single migration, one transaction, three steps:

```python
# klai-connector/alembic/versions/<rev>_add_org_id_to_sync_runs.py
def upgrade():
    # Step 1: add column, nullable (so existing rows don't break constraint).
    op.add_column(
        "sync_runs",
        sa.Column("org_id", sa.Integer(), nullable=True),
        schema="connector",
    )
    op.create_index(
        "ix_sync_runs_org_id",
        "sync_runs",
        ["org_id"],
        schema="connector",
    )

    # Step 2: backfill. See REQ-7.1 note: connector.sync_runs lives in the
    # connector DB; portal_connectors.org_id lives in the portal DB.
    # Preferred path: one-shot Python script executed by the deploy runbook
    # that opens two engines, reads (id, org_id) from portal_connectors, and
    # updates connector.sync_runs.org_id in batches keyed on connector_id.
    # Alembic's op.execute cannot run cross-DB; the script is invoked from
    # the migration via op.get_bind().dialect.server_version_info gate that
    # fails fast if connector_id -> org_id map is incomplete.
    #
    # Acceptable alternative (operator-run, pre-migration):
    #   - Export portal_connectors (id, org_id) as CSV.
    #   - COPY into a temp table in the connector DB.
    #   - UPDATE connector.sync_runs SET org_id = t.org_id FROM temp t WHERE
    #     connector.sync_runs.connector_id = t.id;
    #   - Verify zero NULLs before the NOT NULL alter.

    # Step 3: enforce NOT NULL. Fails loud if backfill missed any rows.
    op.alter_column(
        "sync_runs",
        "org_id",
        nullable=False,
        schema="connector",
    )
```

A `SyncRun` row whose parent connector no longer exists in
`portal_connectors` is orphan data (connector-delete cleanup today
does NOT cascade to `sync_runs` — see `knowledge.md`
"Connector-delete cleanup must cover all four layers" and the
SPEC-CONNECTOR-CLEANUP-001 REQ-04 FK CASCADE follow-up). The
backfill plan treats such rows as pre-existing garbage: the runbook
step BEFORE the migration SHALL delete orphan `sync_runs` rows that
have no matching `portal_connectors.id`. This is independent of this
SPEC but required to reach zero NULLs (A-7 acceptance).

### 8.7 Trust contract: portal <-> connector

Before REQ-7/REQ-8:

- Portal authenticates the channel with `PORTAL_CALLER_SECRET`.
- Portal forwards only `connector_id`.
- Connector filters only on `connector_id`.
- Tenant boundary is not enforced in the connector — it is assumed
  that the portal would never forward a connector_id belonging to a
  different tenant. This is an implicit trust, not a verified
  invariant.

After REQ-7/REQ-8:

- Portal authenticates the channel with `PORTAL_CALLER_SECRET`.
- Portal asserts tenant: `X-Org-ID: <org.id>` on every request.
- Connector filters on `SyncRun.org_id == X-Org-ID` for every query.
- If the portal is compromised and forges `X-Org-ID`, it can still
  cross-tenant — but at that point the portal itself is the problem,
  and SPEC-SEC-IDENTITY-ASSERT-001 scope applies (stronger assertion
  like signed JWT from portal).
- Defense in depth: even WITHOUT a compromise, a portal-side IDOR
  bug that leaks a foreign `connector_id` into a request no longer
  leaks a foreign `SyncRun` — the `X-Org-ID` header and the
  `connector_id` are derived from different portal-side sources
  (session vs URL param), and a mismatch between the two produces a
  404 at the connector. This matches the "personal resource
  ownership" layering guidance in `portal-security.md`.

### 8.8 Risks specific to the internal-wave additions

1. **Deploy ordering.** Portal MUST deploy REQ-8 (X-Org-ID injection)
   before connector flips `sync_require_org_id=True`. REQ-7.6 is the
   transition-period config flag; REQ-8.5 is the deployment-order
   clause. During the window, the connector WARN-logs on missing
   header so VictoriaLogs can confirm zero events before the flip.
2. **Orphaned sync_runs rows.** Pre-existing `sync_runs` rows whose
   parent connector has been deleted from `portal_connectors` will
   block the NOT NULL alter. The runbook step (8.6) deletes them
   first; this is consistent with the out-of-scope SPEC-CONNECTOR-
   CLEANUP-001 REQ-04 FK CASCADE follow-up.
3. **Portal-side org_id identifier drift.** REQ-7.1 pins the contract
   to `portal_connectors.org_id` (portal integer). If
   SPEC-SEC-IDENTITY-ASSERT-001 chooses a different identifier form
   (Zitadel resourceowner id, signed sub claim), the two SPECs MUST
   align in the same release. REQ-8.3 locks the wire format for
   this SPEC's deployment; any later change is a paired portal+
   connector release.
