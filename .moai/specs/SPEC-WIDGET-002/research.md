# Research — SPEC-WIDGET-002

Codebase analysis supporting the split of `partner_api_keys` into separate Partner-API-key and Widget domains.

Scope of verification: all file paths below were verified via Glob/ls in the current tree on `main` at commit `87533cf9`. Contents of `partner_api_keys.py`, `d4e5f6g7h8i9_add_integration_type_and_widget_id.py`, `SPEC-WIDGET-001/spec.md`, and `SPEC-API-001/spec.md` were read. Other files were verified to exist but not fully read (Read-only on demand during implementation).

---

## 1. Current data model

### Table `partner_api_keys`

Defined in `klai-portal/backend/app/models/partner_api_keys.py`:

| Column              | Type           | Notes                                                          |
|---------------------|----------------|----------------------------------------------------------------|
| `id`                | UUID PK        | `gen_random_uuid()`                                            |
| `org_id`            | int FK         | → `portal_orgs.id`, ON DELETE CASCADE                          |
| `name`              | varchar(128)   |                                                                |
| `description`       | varchar(512)?  |                                                                |
| `key_prefix`        | varchar(12)    | e.g. `pk_live_abc...`                                          |
| `key_hash`          | varchar(64)    | SHA-256, unique                                                |
| `permissions`       | JSONB          | `{chat, feedback, knowledge_append}`                           |
| `rate_limit_rpm`    | int            | default 60                                                     |
| `active`            | bool           | default true                                                   |
| `last_used_at`      | tstz?          |                                                                |
| `created_at`        | tstz           |                                                                |
| `created_by`        | varchar(64)    |                                                                |
| `integration_type`  | varchar(10)    | CHECK IN ('api','widget'); SPEC-WIDGET-001 addition            |
| `widget_id`         | varchar(64)?   | UNIQUE; format `wgt_` + 40 hex                                 |
| `widget_config`     | JSONB?         | `{allowed_origins, title, welcome_message, css_variables}`     |

Helper `generate_widget_id()` uses `secrets.token_hex(20)`.

### Table `partner_api_key_kb_access`

Junction with composite PK (`partner_api_key_id`, `kb_id`), plus `access_level` ∈ {'read','read_write'}.

### RLS policies (verified via migration history)

- `partner_api_keys`: `partner_select` (USING true), `partner_insert` (WITH CHECK), `partner_update` (tenant-scoped on `app.current_org_id`), `partner_delete` (tenant-scoped, added today via `e5f6g7h8i9j0_add_partner_api_keys_delete_policy.py`)
- `partner_api_key_kb_access`: permissive (USING true) — depends on the parent table for enforcement.

### Alembic revisions relevant to this SPEC

`klai-portal/backend/alembic/versions/` contains:
- `b1f2a3c4d5e6_add_partner_api_keys.py` — initial table
- `d4e5f6g7h8i9_add_integration_type_and_widget_id.py` — SPEC-WIDGET-001 column additions
- `e5f6g7h8i9j0_add_partner_api_keys_delete_policy.py` — DELETE policy added today, uses DO-block pattern

The DO-block pattern from the DELETE-policy migration (`EXCEPTION WHEN insufficient_privilege THEN RAISE NOTICE`) is the established pattern for RLS DDL and must be reused for the new `widgets` policies.

---

## 2. Current backend API surface

Admin module files (verified present):

- `klai-portal/backend/app/api/admin_integrations.py` — all CRUD for API keys + widgets at `/api/integrations`
- `klai-portal/backend/app/api/admin/__init__.py` — admin router, `_get_caller_org` helper. Today's fix `e341f748` adds `set_tenant` call inside this helper; downstream endpoints rely on it.
- `klai-portal/backend/app/api/admin/` subfolder contains unrelated modules: `audit.py`, `domains.py`, `join_requests.py`, `products.py`, `settings.py`, `users.py`. The split SPEC does **not** touch these.
- `klai-portal/backend/app/api/partner.py` — consumer endpoints (`/partner/v1/*`)
- `klai-portal/backend/app/api/partner_dependencies.py` — auth/scoping for partner endpoints

Services:
- `klai-portal/backend/app/services/partner_keys.py` — `generate_partner_key`
- `klai-portal/backend/app/services/widget_auth.py` — widget short-lived session-token logic

Tests:
- `klai-portal/backend/tests/test_admin_integrations.py` — combined test file
- `klai-portal/backend/tests/test_widget_config.py` — widget-config endpoint test

The auth lookup in `partner_dependencies.py` currently selects on `partner_api_keys` irrespective of `integration_type`. After the split, chat/feedback/knowledge_append must lookup `partner_api_keys` and widget-config must lookup `widgets` (REQ-7).

---

## 3. Current frontend surface

Frontend root: `klai-portal/frontend/src/routes/admin/integrations/` contains:

```
$id.tsx          — detail route with tabs
-hooks.ts        — TanStack Query hooks
-types.ts        — TS types (currently includes discriminator IntegrationResponse)
_components/
  CreatedKeyModal.tsx
  EmbedSnippet.tsx
  KbAccessEditor.tsx          — shared, accepts hideReadWrite prop
  RevokeConfirmDialog.tsx     — to be deleted
  tabs/
    AccessTab.tsx
    DangerTab.tsx
    GeneralTab.tsx
    SettingsTab.tsx
index.tsx
new.tsx
```

Sidebar: `klai-portal/frontend/src/components/layout/Sidebar.tsx`. Current entry labelled "Integrations". Also present: `AuthPageLayout.tsx`, `ProductGuard.tsx`.

UI primitives rule from `klai-portal/CLAUDE.md`: every UI component comes from `components/ui/`. Error text uses `text-[var(--color-destructive)]`. Form pages use `max-w-lg` and `flex items-center justify-between mb-6` for the header. i18n via Paraglide. Reference implementation is `frontend/src/routes/admin/users/invite.tsx`.

Today's accumulated UI refinements must carry over to the split views:
- Dedicated Type column replaced by domain-specific list layout (REQ-11): no `key_prefix` in list.
- Wizard-for-create, tabs-for-edit (REQ-9/REQ-10).
- Widget wizard step 4 split into Appearance + Embed (commit `08f4ef65`).
- Styling step + embed-code preview (commit `daf215a3`).
- "Allowed websites" copy (commit `774ab881`) — "origin" is gone from user-facing text.
- Widget setup step UX (commit `54eff0ac`).

---

## 4. Current predecessor SPECs

### SPEC-API-001 (Partner API, v0.2.0)

Located at `.moai/specs/SPEC-API-001/{spec.md,plan.md,acceptance.md,research.md}`. Live in production. Defines the `pk_live_...` Bearer authentication, the junction table with `access_level`, the admin `/api/integrations` surface, and the consumer `/partner/v1/*` surface. After SPEC-WIDGET-002, the admin surface moves to `/api/api-keys` and the consumer surface is unchanged.

### SPEC-WIDGET-001 (Klai Chat Widget, v0.2.0)

Located at `.moai/specs/SPEC-WIDGET-001/{spec.md,plan.md,acceptance.md,progress.md,spec-compact.md}`. Live. Adds the `integration_type` discriminator, `widget_id`, `widget_config`, and the widget-config endpoint. This is the SPEC that today's fixes repeatedly patched around; the discriminator model is now being abandoned.

Key invariants from SPEC-WIDGET-001 that remain true after SPEC-WIDGET-002:
- Widgets identified by `wgt_...` id, never `pk_live_...` in browser.
- Short-lived session token flow for widget chat calls via `widget_auth.py`.
- Origin-validation blocks unauthorised domains.
- Embed snippet is a single `<script data-widget-id="wgt_...">`.

Everything about **how** the widget is stored changes; everything about **what** it does to external consumers is unchanged.

---

## 5. Today's commit history relevant to the split

The following commits on `main` since `main` last rebased from upstream are the accumulated patch surface that this SPEC eliminates:

| Commit     | Subject                                                            |
|------------|--------------------------------------------------------------------|
| `656989d2` | fix(integrations): propagate integration_type and widget fields    |
| `e341f748` | fix(admin): call set_tenant in admin `_get_caller_org` for RLS     |
| `219d2a5c` | fix(integrations): add DELETE policy on partner_api_keys           |
| `2464089c` | fix(migrations): wrap DELETE policy DDL in DO block                |
| `dfa59fc0` | refactor(integrations): wizard for create, tabs for edit           |
| `54eff0ac` | refactor(integrations): improve widget setup step UX               |
| `7869458d` | refactor(integrations): dedicated Type column, drop key prefix     |
| `daf215a3` | feat(integrations): add Styling step + embed code preview          |
| `08f4ef65` | refactor(integrations): split widget wizard step 4 into Appearance + Embed |
| `774ab881` | refactor(integrations): rewrite allowed origins copy               |

All of these are preserved in behaviour after the split — they move from a discriminator-based module into two dedicated modules.

---

## 6. Deployment context

From `klai-portal/CLAUDE.md`:
- Deploy flow: `git push` → `gh run watch --exit-status` → verify bundle timestamp / container age on core-01.
- Never claim deployed before CI green + server confirmed.
- `portal-deploy.sh` is not run manually — the GitHub Action handles it.

From `.claude/rules/klai/projects/portal-backend.md`:
- SQLAlchemy ORM adds implicit RETURNING to INSERT, which conflicts with RLS split policies. Use `text()` raw SQL for RLS-protected tables where inserting and reading roles differ. This constraint applies to the new `widgets` table — insert logic must either use `text()` or confirm that the admin role can both insert and read under RLS.
- `::jsonb` casts conflict with SQLAlchemy `:param`: use `CAST(:param AS jsonb)` inside `text()`.
- Status strings are cross-layer contracts: removing `active` requires grepping the entire monorepo for every case variant.

From `.moai/specs/SPEC-WIDGET-001/spec.md`:
- `pk_live_...` never in the browser.
- Widgets use short-lived session tokens issued by `/partner/v1/widget-config`.

---

## 7. Widget authentication architecture (added v0.2.0)

The v0.1.0 draft of SPEC-WIDGET-002 assumed widgets need a `pk_live_...` internal key. That assumption was tested against the live code and rejected. This section documents the actual architecture so that implementation does not reintroduce a phantom secret.

### The JWT-only flow

Widget authentication is handled end-to-end by JWT session tokens signed with the `WIDGET_JWT_SECRET` environment variable. There is no per-widget secret anywhere in the system.

**Reference files:**

- `klai-portal/backend/app/services/widget_auth.py` — defines `generate_session_token(wgt_id, org_id, kb_ids, secret)`. Signs an HS256 JWT with `exp=now+1h`. The `secret` argument is the deployment-level `WIDGET_JWT_SECRET`, not a per-widget value.
- `klai-portal/backend/app/api/partner_dependencies.py` — `get_partner_key` branches on token shape: tokens starting with `pk_live_` hit a SHA-256 lookup against `partner_api_keys.key_hash`; all other tokens are decoded via HS256 with `WIDGET_JWT_SECRET`. There is no third branch that consults widget-specific secret storage.

### What happens on a widget chat call

```
Browser  ──GET /partner/v1/widget-config?id=wgt_abc──▶  Portal backend
                                                         │  (1) lookup widgets by widget_id (public, no secret)
                                                         │  (2) validate Origin against widget_config.allowed_origins
                                                         │  (3) generate_session_token(wgt_id, org_id, kb_ids, WIDGET_JWT_SECRET)
Browser  ◀──{session_token: "<jwt>"}──────────────────  Portal backend

Browser  ──POST /partner/v1/chat/completions + Bearer <jwt>──▶  Portal backend
                                                                  │  (1) partner_dependencies.get_partner_key:
                                                                  │       token does not start with pk_live_ → decode JWT
                                                                  │       via WIDGET_JWT_SECRET
                                                                  │  (2) extract wgt_id + kb_ids from claims
                                                                  │  (3) authorise retrieval + LLM call
Browser  ◀──SSE chat stream───────────────────────────────────  Portal backend
```

At no point is a `pk_live_...` token issued to the browser, stored on the widget row, or resolved from the widget table. The `key_hash` column on today's `partner_api_keys` widget rows is **dead code** — it is never read on any widget flow.

### Why the industry does it this way

Intercom, Drift, Crisp, and Zendesk all follow the same pattern: widget embeds expose only a public widget-id, and the server-issued session token (JWT or equivalent) carries the short-lived authorisation. No shared secret exists between the widget JS bundle and the developer-API surface. This avoids:

- Widget-key leakage via browser DevTools
- Server-side fan-out of per-widget secret rotation
- Confusion between developer-API keys and widget embed credentials

### Rotation model

`WIDGET_JWT_SECRET` is a single deployment-level secret. Rotation is done by rotating the env var, which invalidates all in-flight session tokens (browsers silently re-fetch a new one on the next widget-config call). Per-widget JWT secrets with widget-scoped invalidation are a possible future extension and are explicitly out of scope for SPEC-WIDGET-002.

### Implications for SPEC-WIDGET-002

- The `widgets` table has **no** `key_prefix`, `key_hash`, `permissions`, or `api_key_id` column (REQ-2, REQ-3 revised).
- The data migration (REQ-4) copies `id`, `org_id`, `name`, `description`, `widget_id`, `widget_config`, `rate_limit_rpm`, `created_by`, `created_at`, `last_used_at` only. Values currently in `key_prefix`, `key_hash`, `permissions`, `active` on widget rows in `partner_api_keys` are dropped along with the columns in step 8 of the migration — they were never used for widget auth.
- `POST /api/widgets` returns no secret and shows no "CreatedKeyModal" on the admin UI (REQ-5 revised).
- `/partner/v1/chat/completions` keeps two auth branches exactly as today: `pk_live_*` → `partner_api_keys`; JWT → decode via `WIDGET_JWT_SECRET` (REQ-7 revised).

---

## 8. Unknowns and risks identified during drafting

1. ~~**Internal-key ownership for widgets**~~ — **Resolved v0.2.0**: no per-widget secret exists. See section 7 above.
2. **Row count in production**: the research did not query the live database; we assume the count is small. If the production count is >1000, the in-transaction migration in REQ-4 may need batching. Mark should confirm approximate row count before implementation.
3. **Frontend sidebar icon and ordering**: not covered in the SPEC; the sidebar change is a label + route change only. If icons need to change, that is a separate follow-up.
4. **Paraglide message keys**: renaming the admin section will require new Paraglide message keys for "API keys" and "Chat widgets". Old message keys for "Integrations" may still be referenced in other screens; a grep pass is required before removing them.
5. **Reusable shared `KbAccessEditor` behaviour**: the widgets variant uses `hideReadWrite`. If the API-key side ever wants to deprecate `read_write`, the component can be simplified later. Not in scope.
6. **Rollback of already-created widgets after downgrade**: REQ-13 requires the downgrade to refuse destructive DDL if new widgets have been created. The exact detection logic (likely: if `widgets` contains rows whose `created_at` is after the upgrade marker) needs confirmation during implementation.
7. **SPEC-API-001 and SPEC-WIDGET-001 HISTORY**: both live SPECs describe the current combined design. SPEC-WIDGET-002 does not modify them; they remain as historical context. A small README note referencing SPEC-WIDGET-002 as their successor may be useful, but is deliberately out of scope.
8. **Risk introduced by removing the widget internal key (v0.2.0)**: the v0.1.0 draft would have given each widget its own `pk_live_...` secret, providing per-widget revocation (delete the widget → its secret is invalid). The v0.2.0 design inherits the existing JWT-only model, which means widget rotation is all-or-nothing (rotate `WIDGET_JWT_SECRET` → every widget's session token is invalidated at once). Accepted because (a) it matches industry practice, (b) session tokens are 1-hour short, (c) deletion of a widget row still blocks new session-token issuance via the `widget_id` lookup in `/partner/v1/widget-config`. Per-widget JWT secrets are a future SPEC.

---

## 9. Verified file path inventory

Backend:
- `klai-portal/backend/app/api/admin_integrations.py` ✅
- `klai-portal/backend/app/api/admin/__init__.py` ✅
- `klai-portal/backend/app/api/partner.py` ✅
- `klai-portal/backend/app/api/partner_dependencies.py` ✅
- `klai-portal/backend/app/models/partner_api_keys.py` ✅
- `klai-portal/backend/app/services/partner_keys.py` ✅
- `klai-portal/backend/app/services/widget_auth.py` ✅
- `klai-portal/backend/alembic/versions/b1f2a3c4d5e6_add_partner_api_keys.py` ✅
- `klai-portal/backend/alembic/versions/d4e5f6g7h8i9_add_integration_type_and_widget_id.py` ✅
- `klai-portal/backend/alembic/versions/e5f6g7h8i9j0_add_partner_api_keys_delete_policy.py` ✅
- `klai-portal/backend/tests/test_admin_integrations.py` ✅
- `klai-portal/backend/tests/test_widget_config.py` ✅

Frontend:
- `klai-portal/frontend/src/routes/admin/integrations/$id.tsx` ✅
- `klai-portal/frontend/src/routes/admin/integrations/-hooks.ts` ✅
- `klai-portal/frontend/src/routes/admin/integrations/-types.ts` ✅
- `klai-portal/frontend/src/routes/admin/integrations/index.tsx` ✅
- `klai-portal/frontend/src/routes/admin/integrations/new.tsx` ✅
- `klai-portal/frontend/src/routes/admin/integrations/_components/CreatedKeyModal.tsx` ✅
- `klai-portal/frontend/src/routes/admin/integrations/_components/EmbedSnippet.tsx` ✅
- `klai-portal/frontend/src/routes/admin/integrations/_components/KbAccessEditor.tsx` ✅
- `klai-portal/frontend/src/routes/admin/integrations/_components/RevokeConfirmDialog.tsx` ✅
- `klai-portal/frontend/src/routes/admin/integrations/_components/tabs/AccessTab.tsx` ✅
- `klai-portal/frontend/src/routes/admin/integrations/_components/tabs/DangerTab.tsx` ✅
- `klai-portal/frontend/src/routes/admin/integrations/_components/tabs/GeneralTab.tsx` ✅
- `klai-portal/frontend/src/routes/admin/integrations/_components/tabs/SettingsTab.tsx` ✅
- `klai-portal/frontend/src/components/layout/Sidebar.tsx` ✅

Predecessor SPECs:
- `.moai/specs/SPEC-API-001/` (4 files: spec, plan, acceptance, research) ✅
- `.moai/specs/SPEC-WIDGET-001/` (5 files: spec, spec-compact, plan, acceptance, progress) ✅
