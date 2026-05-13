---
paths:
  - "klai-portal/backend/**/*.py"
---
# Portal Backend Patterns

## FRONTEND_URL controls OAuth redirect URIs (CRIT)

`FRONTEND_URL` in portal-api env is NOT cosmetic — it is used to construct OAuth callback URLs
(Google Drive, Microsoft, etc.). A wrong value causes `redirect_uri_mismatch` for every user.

- Must match the actual login domain: `https://my.getklai.com`
- Registered Google/Microsoft OAuth redirect URIs must match exactly: `https://my.getklai.com/api/oauth/.../callback`
- `config.py` falls back to `https://portal.{domain}` if FRONTEND_URL is empty — this fallback is wrong for production
- `{tenant}.getklai.com` is NOT the portal URL — that is the per-tenant view; `my.getklai.com` is the login URL

**Why:** Root cause of April 2026 incident: `FRONTEND_URL=https://getklai.com` → callback pointed to `getklai.com` which is unrouted → 50x OAuth errors per affected user.

**Prevention:** After any domain change, verify: `docker exec portal-api printenv FRONTEND_URL` matches `https://my.getklai.com`. Never derive the portal URL from Caddy wildcard config or Zitadel redirect URIs — check servers.md.

## SQLAlchemy + RLS (CRIT)
- SQLAlchemy ORM adds implicit `RETURNING` to all inserts — breaks RLS tables with separate SELECT/INSERT policies.
- Use `text()` raw SQL for inserts on RLS-protected tables where the inserting role differs from the reading role.
- `::jsonb` casts conflict with SQLAlchemy `:param` — use `CAST(:param AS jsonb)` instead.

## Pool-GUC pollution — reset at checkout AND cleanup (CRIT)

PostgreSQL `set_config('app.current_org_id', ...)` persists for the lifetime
of the pooled connection. `app/core/database.py` resets both RLS GUCs on
cleanup, but the reset is wrapped in `suppress(Exception)` — if the session
is in aborted-transaction state (42501 RLS fail-loud, closed connection)
the reset silently fails and the connection returns to the pool with a
stale tenant GUC.

**Symptom:** Intermittent 404 "Organisation not found" on `/api/app/*`
endpoints with a valid session. Same cookie alternately succeeds and fails
within seconds. Adjacent endpoints (`/api/app/knowledge-bases` vs
`/api/app/templates`) return different statuses in the same millisecond
because each call checks out a different pooled connection.

**Root cause (2026-04-24 getklai incident):** `_get_caller_org` queries
`portal_users` (RLS: `org_id = GUC OR GUC IS NULL`) BEFORE calling
`set_tenant`. A pooled connection with `app.current_org_id=8` (Voys, leaked
from a prior request) serving a request for org_id=1 (getklai) filters the
getklai user row out via RLS, and the handler raises 404.

**Fix (three independent layers):**

1. `_pin_and_reset_connection` (renamed from `_pin_and_reset_on_exit`) runs
   `_reset_tenant_context` at checkout, before yielding the session.
   Cleanup-time reset still runs; checkout-time reset catches any leak from
   a prior cleanup that the suppress-blocks ate.
2. `PooledTenantSession` — an `AsyncSession` subclass wired into
   `async_sessionmaker(class_=PooledTenantSession)`. Every
   `async with AsyncSessionLocal() as s:` block auto-pins + resets on
   `__aenter__`, so a future helper that forgets the explicit
   `_pin_and_reset_connection` call cannot re-introduce the bug.
3. `assert_portal_users_rls_ready()` runs in the `main.py` lifespan after
   `install_rls_guard`. It fails loud at startup if the `portal_users`
   `tenant_isolation` policy drops its `IS NULL` branch — without that
   branch, every authenticated request 404s after deploy (because the
   freshly-reset GUC makes `_get_caller_org`'s user lookup return zero rows).

**Prevention:**
- Every helper that hands out a pooled session MUST call
  `_pin_and_reset_connection(session)` before yielding. `get_db`,
  `tenant_scoped_session`, `pin_session`, `cross_org_session` all do. This
  layers on top of the `PooledTenantSession` auto-reset — belt + braces.
- Category-A tables (`portal_users`, `portal_connectors` — see the 4-category
  framework in `portal-security.md`) MUST keep the `OR current_setting(...)
  IS NULL` branch. The startup assertion guards `portal_users`; extend it
  if a new Category-A table joins the auth path before `set_tenant` fires.
- Never query an RLS-protected table before `_get_caller_org` / `set_tenant`
  in the same request. With the pool reset, empty GUC = Category-A permissive
  only; Category-D raises 42501, Category-C SELECT returns zero rows.

**Verification:**
- `docker logs klai-core-portal-api-1 | grep "RLS policy checked"` — must
  appear once per startup. Absence means the assertion regressed.
- Reproduce the original bug: inject a stale GUC into the pool and fire a
  cross-tenant burst. Before the fix: mixed 200/404. After: 100% 200.
  Repro script lives in PR #133 description.

## Prometheus metrics in tests
- Never use the global `prometheus_client` registry in tests — causes `Duplicated timeseries`.
- Use a `CollectorRegistry` per instance via dataclass + `autouse` fixture that patches module-level singleton.

## sendBeacon endpoints
- `navigator.sendBeacon` cannot set `Authorization` headers.
- Design analytics endpoints as intentionally unauthenticated. Rate-limit at Caddy. Validate/clamp with Pydantic.

## Fire-and-forget writes (audit, analytics)
- Request-scoped session rolls back on any exception — audit entries are lost.
- Use an independent `AsyncSessionLocal()` session for writes that must survive caller exceptions.

## Status string contracts
- Status values (`recording`, `processing`, etc.) are cross-layer contracts: backend, frontend, i18n, polling, badges.
- Before renaming: `grep -r "old_value"` across the entire monorepo + all case variants.

## Event emission
- Event name must match the actual user action, not a configuration step.
- Before `COUNT(DISTINCT field)` in dashboards, verify the field is populated at emit time.
- Pre-auth events (`login`, `signup`) have no `org_id` — don't use org-based aggregation.

## SELECT FOR UPDATE in get-or-create patterns (CRIT)
Any "get or create" on a shared row (per-org keys, per-tenant state) MUST use `SELECT ... FOR UPDATE`.
Two concurrent requests that both see NULL will generate conflicting values — one silently overwrites the other.
SPEC-KB-020: plain `db.get(PortalOrg, org_id)` in `get_or_create_dek` allowed two requests to generate different DEKs, making the first connector's credentials permanently unreadable.
```python
# Correct pattern
result = await db.execute(
    select(PortalOrg).where(PortalOrg.id == org_id).with_for_update()
)
org = result.scalar_one_or_none()
```

## Locale propagation pattern

Propagate locale through OAuth/redirect flows via query parameter, not browser state. Pattern used in IDP intent signup:

1. Frontend sends `locale` in the request body
2. Backend validates with a `@field_validator` against `_SUPPORTED_LOCALES`, defaulting to `"nl"`
3. Locale is embedded in the `success_url` as a query param before redirecting to Zitadel
4. Callback reads it as `locale: str = Query(default="nl")` and validates again
5. All redirects and cookie payloads carry the locale forward

**Rule:** OAuth callback endpoints must not rely on session state or browser cookies for locale — it must travel through the redirect URL as a validated query parameter.

## portal-api scripts/ not in Docker image (MED)
`klai-portal/backend/scripts/` is NOT copied into the container (no `COPY scripts/` in Dockerfile).
Data migration scripts in `scripts/` cannot be run via `docker exec portal-api python scripts/foo.py`.
Workaround: pass inline via `docker exec portal-api python3 -c "$(cat scripts/foo.py)"` or add `COPY scripts/ scripts/` to the Dockerfile.

## Provisioning state machine (SPEC-PROV-001)

Tenant provisioning is a one-level compensating transaction with a DB state
machine on `portal_orgs.provisioning_status`. Each forward step writes a
checkpoint via `transition_state()` and registers its compensator on a
`contextlib.AsyncExitStack`.

- Orchestrator: `app/services/provisioning/orchestrator.py`
- State machine: `app/services/provisioning/state_machine.py`
- Stuck detector: `app/services/provisioning/stuck_detector.py` (runs at startup)
- Retry endpoint: `app/api/admin/retry_provisioning.py` (admin-only)
- Runbook: `docs/runbooks/provisioning-retry.md`

**Invariants:**
- Every state transition uses `SELECT ... FOR UPDATE` (serialises concurrent retries).
- Slug uniqueness uses a partial unique index `ix_portal_orgs_slug_active` so
  that a failed row can be soft-deleted and the slug reclaimed on retry.
- Compensators MUST be idempotent — they are drained via AsyncExitStack on
  abort and must not raise (best-effort rollback, SPEC R10).
- When adding a new `PortalOrg` query that MUST hide soft-deleted rows, add
  `.where(PortalOrg.deleted_at.is_(None))` explicitly. Never rely on implicit
  filtering.
- Never emit `provisioning_status = 'failed'` — that legacy value is out.
  Use `failed_rollback_pending` (rollback failed) or `failed_rollback_complete`
  (rollback succeeded, row soft-deleted).

## Tenant deprovisioning state machine (SPEC-INFRA-TENANT-DELETE-001)

Mirror of provisioning state machine for tenant delete:
- `deprovisioning` — orchestrator running, auth-flow returns 403 with code `tenant_deleting`
- `deprovisioned` — pre-hard-delete checkpoint (rarely observed; same-tx as DELETE)
- `failed_deprovisioning` — terminal failure; `last_failure` jsonb populated; admin retry possible

Files:
- Orchestrator: `app/services/provisioning/deprovisioning_orchestrator.py`
- 16 steps: `app/services/provisioning/deprovisioning_steps.py`
- Audit emit: `app/services/audit/tenant_lifecycle.py::emit_lifecycle_event` (synchronous, NOT fire-and-forget — failure rolls back the deprovision finalize transaction)
- Endpoints: `app/api/admin/deprovision_org.py`
- Runbook: `docs/runbooks/tenant-delete.md`

Invariants:
- Each step is idempotent — al-weg = OK, no exception
- 3 internal retries with exponential backoff (1s, 2s, 4s) on transient errors
- All steps critical: definitive failure → `failed_deprovisioning` (no fail-soft)
- portal_orgs hard-delete is the final step; audit emit happens BEFORE delete in same transaction
- `tenant_lifecycle_events` has NO FK to portal_orgs — survives the hard-delete by design
- Auth-flow check: `_get_caller_org` returns 403 with code `tenant_deleting` when org is in `deprovisioning` state. The owner status-polling endpoint passes `allow_during_deprovisioning=True` to bypass.
- New non-cascading FK to portal_orgs added in the future MUST be added to the explicit DELETE list in `_finalize_postgres_delete` step — otherwise the final hard-delete throws FK violation. Test fixture asserts the full delete-list against a populated test tenant.

## KB resource access — route-level firewall pattern (SPEC-PORTAL-KB-OWNERSHIP-001)

Every route under `/api/app/knowledge-bases/{kb_slug}/...` MUST include
`Depends(get_kb_with_access)` in its `dependencies=[]` list. The dependency
lives in `app/api/dependencies.py` and does three things in order:

1. **Magic-slug shortcut**: `personal` → caller's personal-{user_id} KB;
   `org` → tenant's org KB. Both lazy-create via
   `app.services.default_knowledge_bases.resolve_personal_kb` /
   `resolve_org_kb` if provisioning missed them.
2. **Tenant-scope SELECT** WHERE org_id = caller.org_id AND slug = kb_slug.
   Cross-tenant slugs return 404. Belt+braces with Cat-D RLS on the table.
3. **Personal-firewall**: if `is_personal_kb(kb)` (single-source-of-truth
   helper in `app.services.access`) AND `kb.owner_user_id != caller.user_id`,
   raise 404 (NOT 403). Existence-non-disclosure: leaking that someone else
   has a personal KB by name is itself the violation we want to prevent.
   Admins receive 404 too — no role-bypass.

Authorisation (owner / contributor / viewer) is layered on TOP of this gate
inside the handler body via `_require_owner` etc. The dependency only
handles existence + privacy; it does not gate write actions.

**Invariant test**: `tests/test_kb_personal_firewall.py::TestRouteFirewallInvariant::test_every_kb_slug_route_uses_firewall_dependency`
introspects `app.routes`, finds every path containing `{kb_slug}`, and
asserts `get_kb_with_access` appears in the flat dep tree. Routes without
`get_caller` in their deps (X-Internal-Secret-only endpoints) are skipped
automatically — they cannot use the dep because it requires `perms`.

**Adding a new KB-route**:

```python
@router.get(
    "/knowledge-bases/{kb_slug}/my-new-thing",
    response_model=MyResponse,
    dependencies=[Depends(get_kb_with_access)],   # <-- mandatory
)
async def my_handler(
    kb_slug: str,
    perms: UserPermissions = Depends(get_caller),
    db: AsyncSession = Depends(get_db),
):
    # The dep already raised 404 for personal-KB-of-others before this body runs.
    # Resolve the kb again locally (or refactor to use the dep's return value).
    kb = await _get_kb_or_404(kb_slug, perms.org_id, db)
    ...
```

If the route already has `dependencies=[Depends(require_capability(...))]`,
append the firewall:

```python
dependencies=[
    Depends(require_capability(Capability.KB_FOO)),
    Depends(get_kb_with_access),
]
```

The connector router uses the router-level form because every route under
its prefix is KB-scoped:

```python
router = APIRouter(
    prefix="/api/app/knowledge-bases/{kb_slug}/connectors",
    dependencies=[
        Depends(require_capability(Capability.KB_CONNECTORS)),
        Depends(get_kb_with_access),
    ],
)
```

Adding a NEW route to that router gets the firewall for free.

## Typed-string admin-override header pattern (SPEC-PORTAL-KB-OWNERSHIP-001)

For admin actions that escalate beyond the normal authz pad (e.g.
`delete_app_knowledge_base` letting an admin remove an org-KB they did not
create), require a typed-string confirmation header rather than a boolean
query-param or body field. Mirrors the precedent in
`klai-infra/.github/workflows/sync-env.yml` where `I-CONFIRM-REMOVAL` is
the typed override for SOPS removals.

Why a typed string, not a boolean:
- A boolean / checkbox is one accidental click-through away from triggering
  the destructive path.
- The typed string forces explicit operator intent and is impossible to set
  by accidental click-through.
- The string itself is the documentation: an operator reading the curl
  command sees `X-Admin-Override-Confirm: I-WAS-NOT-CREATOR` and immediately
  understands what they're agreeing to.

Pattern (backend):

```python
ADMIN_OVERRIDE_HEADER = "X-Admin-Override-Confirm"
ADMIN_OVERRIDE_VALUE = "I-WAS-NOT-CREATOR"

async def my_admin_action(
    request: Request,
    perms: UserPermissions = Depends(get_caller),
):
    is_admin = perms.effective_role == ProfileRole.ADMIN
    header_present = request.headers.get(ADMIN_OVERRIDE_HEADER, "") == ADMIN_OVERRIDE_VALUE
    if not (is_admin and header_present):
        raise HTTPException(
            status_code=403,
            detail=(
                f"Owner access required, or set header "
                f"'{ADMIN_OVERRIDE_HEADER}: {ADMIN_OVERRIDE_VALUE}' as an admin"
                " to perform this action"
            ),
        )
    # ... emit audit event with action='X.admin_overridden' BEFORE the destructive write ...
```

The 403 message is intentionally verbose: it tells the admin EXACTLY which
header to set. This doubles as a graceful-fallback for the deploy-during-
active-session case where the frontend may still serve the OLD modal that
doesn't auto-attach the header — the user gets an actionable 403 instead of
a confusing one.

Frontend pattern (the modal): only attach the header when the user has gone
through the typed-confirmation gate. Never attach by default. Test for
header-absence in `data-test-id` assertions on the owner pad.

