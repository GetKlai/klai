---
paths:
  - "klai-portal/backend/app/api/**/*.py"
---

# Multi-Tenant Security Pattern

## SENSITIVE_FIELDS key mismatch (CRIT)

When adding a new connector, `SENSITIVE_FIELDS` in `app/services/connector_credentials.py`
maps connector type → list of config dict keys to encrypt. If a key name in this mapping
doesn't exactly match what the adapter writes to `connector.config`, encryption is silently
skipped — the token stays in plaintext JSONB with no error.

**Why this happened (SPEC-KB-019/020):** KB-020 had `"notion": ["api_token"]` but the
Notion adapter used `"access_token"` as the config key. Fixed in commit `ed642f9`.

**Prevention:** When adding a connector to `SENSITIVE_FIELDS`, grep the adapter source for
every key it writes to the config dict and verify exact name parity before merging.

**After any SENSITIVE_FIELDS key rename:** grep test files for the old key before committing —
tests that use the old key name will silently set up the wrong fixture and fail on assertion.
```bash
grep -r "old_key_name" klai-portal/backend/tests/
```

**[HARD] Every database query on a tenant-scoped model MUST be scoped to org_id.**

## The pattern

All tenant-scoped models (groups, knowledge_bases, connectors, etc.) must be
accessed via a `_get_{model}_or_404(id, org_id, db)` helper — never via a raw
`select(...).where(Model.id == id)` without org_id.

The canonical examples:

```python
# groups.py — reference implementation
async def _get_group_or_404(group_id: int, org_id: int, db: AsyncSession) -> PortalGroup:
    result = await db.execute(select(PortalGroup).where(
        PortalGroup.id == group_id, PortalGroup.org_id == org_id
    ))
    ...

# knowledge_bases.py
async def _get_kb_or_404(kb_slug: str, org_id: int, db: AsyncSession) -> KnowledgeBase:
    result = await db.execute(select(KnowledgeBase).where(
        KnowledgeBase.slug == kb_slug, KnowledgeBase.org_id == org_id
    ))
    ...
```

## Why this matters

Without org_id in the query, an attacker can supply any `id` and access or
delete resources belonging to another tenant (IDOR). This happened in
`revoke_kb_group_access` where the org was discarded before the delete.

## Personal resource ownership

- Org-scoping alone is insufficient for personal resources. Add `created_by` check.
- Listings: return org-wide resources + only the caller's personal resources.
- Public endpoints: return 404 (not 403) for private resources — never leak existence.
- Authenticated endpoints: return 403 for resources owned by someone else.

## Defense in depth: PostgreSQL RLS

As a second layer, RLS policies on all tenant-scoped tables enforce org isolation
at the database level via `app.current_org_id` (set by `set_tenant()` in every
authenticated request). RLS catches what the helper misses; the helper remains
explicit and auditable.

## RLS + Alembic

- `portal_api` user is NOT table owner — cannot run `ALTER TABLE ENABLE ROW LEVEL SECURITY`.
- Run RLS DDL directly as `klai` superuser via psql. Keep Alembic migration for code history.
- Use `IF NOT EXISTS` in all policy/index creation to make migrations idempotent.

## RLS + SQLAlchemy

- ORM adds implicit `RETURNING` — triggers SELECT policy on inserts.
- Split `ALL` policies into separate `SELECT` and `INSERT` when inserting role differs from reading role.
- Use `text()` raw SQL for audit/analytics inserts on split-policy tables.

## RLS policy categories (4-category framework, live since 2026-04-21)

Every RLS-enabled table falls in one of four categories. Policy shape
derives from the category. **Do not move a table between categories
without the pre-flight audit described in `docs/runbooks/rls-upgrade.md`.**

| Cat | Policy pattern | When to use | Tables |
|---|---|---|---|
| A | `USING (org_id = … OR current_setting IS NULL)` — permissive on missing | Query runs BEFORE set_tenant can fire (auth lookup, pre-auth webhook) | `portal_users`, `portal_connectors` |
| B | SELECT `USING (true)`, other cmds strict | Public/pre-auth endpoint must SELECT without tenant context | `widgets`, `widget_kb_access`, `partner_api_keys`, `partner_api_key_kb_access` |
| C | INSERT permissive, SELECT scoped | Fire-and-forget audit/event writes survive caller rollback | `portal_audit_log`, `product_events`, `portal_feedback_events` |
| D | `USING (_rls_current_org_id() IS NULL OR org_id = _rls_current_org_id())` — strict, raises on missing, supports explicit cross-org bypass | Every access path sets tenant context | `portal_knowledge_bases`, `portal_groups`, `portal_group_products`, `portal_group_kb_access`, `portal_kb_tombstones`, `portal_user_kb_access`, `portal_user_products`, `portal_retrieval_gaps`, `portal_taxonomy_nodes`, `portal_taxonomy_proposals`, `vexa_meetings` |

Outside the classification: `portal_group_memberships` has no RLS
policy — membership rows inherit their tenant via the parent group's FK.

## Post-commit db.refresh on RLS tables (HIGH)

`await db.refresh(obj)` after `await db.commit()` opens a fresh implicit transaction
without the tenant GUC. On a category-D table this trips the RLS guard and raises
`asyncpg.exceptions.InsufficientPrivilegeError: RLS: app.current_org_id is not set`,
surfacing as a 500 response while the preceding INSERT/UPDATE+commit already succeeded.

**Why:** `SET LOCAL app.current_org_id` is transaction-scoped. `db.commit()` ends the
transaction and clears the setting. SQLAlchemy's `db.refresh()` then starts a new
implicit transaction that hits the category-D strict policy. Any table in
`RLS_DML_TABLES` (`app/core/rls_guard.py`) is affected.

**Prevention:**

- **UPDATE endpoints** (object already fetched, only attribute assignments mutate it):
  remove the refresh entirely. `AsyncSessionLocal` uses `expire_on_commit=False`,
  so fields set in Python before commit persist in memory.

  ```python
  meeting.status = "joining"
  meeting.started_at = datetime.now(UTC)
  await db.commit()
  # No refresh — all fields already set in memory
  return await _build_meeting_response(meeting, db)
  ```

- **CREATE endpoints** (new object relying on `server_default=func.now()` or other
  DB-side generated columns): move the refresh **before** the commit, so it runs
  inside the tenant-scoped transaction.

  ```python
  db.add(group)
  await db.flush()
  await db.refresh(group)   # tenant context still active
  await db.commit()
  ```

Historical references: AUTH-008-F (b64d70dc — meetings.py), AUTH-008-G (486336a1 +
ddb6cbc5 — 5 files, 24 sites). Discovered during SEC-021 E2E verification.

## How to set tenant context

Three patterns, in order of preference:

1. **`Depends(_get_caller_org)` / `get_partner_key`** in the route
   signature — the dependency calls `set_tenant()` itself. All normal
   request-scoped work should use this.
2. **`await set_tenant(db, org_id)`** explicit call — use only when the
   dependency pattern does not fit (internal endpoints that carry a
   different auth token, callbacks that resolve the org from the
   payload).
3. **`tenant_scoped_session(org_id)` / `cross_org_session()`** context
   managers — for background tasks and fire-and-forget writes that open
   a fresh session (no request context).

Never open `AsyncSessionLocal()` directly and then query a category-D
table. The RLS guard event listener (`app.core.rls_guard`) logs any
rowcount=0 DML on those tables at ERROR level; in tests,
`PORTAL_RLS_GUARD_STRICT=1` upgrades that to a raise.

## Adding a new RLS-enabled table

1. Pick the category first (see table above). If category D, continue;
   otherwise copy an existing policy from a same-category table.
2. Write the policy in a new `alembic/versions/post_deploy_*.sql` file
   using the category's pattern. Wrap in `BEGIN`/`COMMIT`.
3. For category D: audit every callsite that touches the model or
   tablename (grep, CodeIndex `impact()`). Each must go through one of
   the three patterns above.
4. Add the table to `RLS_DML_TABLES` in `app/core/rls_guard.py` so the
   event listener covers it.
5. Add the table to the verify-section of `scripts/rls-smoke-test.sql`.

## Deploy order for RLS changes

**Code first, SQL second.** The application expects the policy to be in
its target state when it starts. Running the SQL before the Python
deploy breaks any request still relying on the old policy behaviour.

See the full runbook: `docs/runbooks/rls-upgrade.md`.

## Rules for agents

1. When creating a new endpoint that mutates a resource by ID:
   - Call `_get_{model}_or_404(id, org.id, db)` before any mutation
   - Never use the resource ID from the URL path without an org-scoped lookup

2. When creating a new helper:
   - Always include `org_id` as a parameter
   - Mark with `# @MX:ANCHOR fan_in=N` so callers are tracked

3. Code review red flag:
   ```python
   # BAD — no org check
   select(KnowledgeBase).where(KnowledgeBase.slug == kb_slug)

   # GOOD — always use the helper
   kb = await _get_kb_or_404(kb_slug, org.id, db)
   ```

4. Junction tables without org_id (e.g. `portal_group_kb_access`) must be
   accessed only AFTER verifying the parent resource via its helper.
