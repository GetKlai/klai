---
id: SPEC-WIDGET-002
version: 0.3.0
status: draft
created: 2026-04-16
updated: 2026-04-16
author: Mark Vletter
priority: high
issue_number: 0
---

# SPEC-WIDGET-002 — Split Partner API and Chat Widget into independent first-class domains

## HISTORY

- **v0.3.0 (2026-04-16)**: Soft-delete semantics removed entirely. Both domains (API keys and widgets) have only hard delete; there is no `revoke` action, no `active` boolean, and no soft-deleted rows. The migration that creates `widgets` also drops the `active` column on `partner_api_keys`. The revoke endpoint (`POST /api/integrations/{id}/revoke`) is not migrated to the new endpoint sets — it disappears. The `active.is_(True)` filter in `partner_dependencies.get_partner_key` is removed. The "future per-widget JWT revocation" item is removed from Out of Scope (not needed; DELETE handles compromise). Rationale: audit data lives in `product_events` and `audit_log` independently of integration FKs, so DELETE breaks nothing; secret-leak recovery works identically whether the row is deleted or marked inactive (SHA-256 lookup fails either way); the admin UI offers no undo anyway.
- **v0.2.0 (2026-04-16)**: Three open decisions resolved by Mark after verifying current widget auth code. (1) REQ-3 fundamentally revised: the `widgets` table carries no `key_prefix`, `key_hash`, or `permissions` column — widget authentication is 100% JWT-based via the existing `WIDGET_JWT_SECRET` env var, as implemented in `klai-portal/backend/app/services/widget_auth.py` and `klai-portal/backend/app/api/partner_dependencies.py`. The previous assumption that widgets need a `pk_live_...` internal key was wrong. (2) REQ-8 sidebar labels locked to "API keys" and "Chat widgets". (3) REQ-6 confirmed as hard-remove of `/api/integrations` with no redirect, deprecation window, or migration path — admin frontend and backend deploy together and there are no external consumers. Also: REQ-4 data migration simplified (no key_prefix/key_hash/permissions/active columns to copy); research.md extended with a "Widget authentication architecture" section.
- **v0.1.0 (2026-04-16)**: Initial draft. Splits the combined `partner_api_keys` table and unified `/admin/integrations` surface into two independent domains: Partner API keys and Chat widgets. Motivated by recurring defects caused by the discriminator-based design (silent column leakage, revoke-vs-delete ambiguity, pervasive `if (isWidget)` branches in frontend/backend, Pydantic silently dropping `integration_type`, missing RLS DELETE policy). Incorporates today's UI improvements (dedicated Type column, wizard-for-create / tabs-for-edit, Appearance + Embed steps, "Allowed websites" copy, Styling step + embed preview). Consolidates prior SPEC-API-001 (Partner API) and SPEC-WIDGET-001 (Chat Widget) into two separate domain entities at table, endpoint and route level.

---

## Goal

Eliminate the accumulated defect surface caused by multiplexing two unrelated integration types ("API key" and "Widget") on a single table, a single endpoint prefix and a single admin route. After this SPEC lands, the Klai portal has two independent first-class domains:

1. **Partner API keys**: developer-facing `pk_live_...` credentials for server-to-server integration (chat completions, feedback, knowledge append). No widget concepts.
2. **Chat widgets**: embeddable `wgt_...` widget instances served from `cdn.getklai.com`, with appearance configuration, allowed websites, and embed snippet. No API-key concepts and — crucially — **no per-widget secret**. Widget authentication is handled entirely by JWT session tokens signed with the deployment-level `WIDGET_JWT_SECRET`, following the industry pattern used by Intercom, Drift, Crisp and Zendesk.

Each domain has its own database table with its own RLS policies, its own admin endpoint prefix, its own admin route, its own wizard and its own tabbed detail view. The two share nothing except the underlying knowledge-base selector component and the customer organisation context.

Architectural clarification (v0.2.0): the v0.1.0 draft assumed widgets need a `pk_live_...` internal key (either a column on the widgets row or an FK to a hidden `partner_api_keys` row). Both options were wrong. Inspection of `klai-portal/backend/app/services/widget_auth.py` and `klai-portal/backend/app/api/partner_dependencies.py` confirms that widget authentication is already 100% JWT-based: `widget_auth.generate_session_token` signs a 1-hour HS256 JWT with `WIDGET_JWT_SECRET`, and `partner_dependencies.get_partner_key` already branches on the token shape (`pk_live_*` → SHA-256 lookup in `partner_api_keys`; otherwise decode JWT with `WIDGET_JWT_SECRET`). The `key_hash` column on widget rows in `partner_api_keys` is never read on a widget flow. The new `widgets` table therefore contains **no authentication-secret columns at all**.

Consumer endpoints (`/partner/v1/chat/completions`, `/partner/v1/widget-config`) continue to function. `/partner/v1/chat/completions` authenticates against `partner_api_keys`; `/partner/v1/widget-config` reads the `widgets` row by public `widget_id` (no secret lookup) and issues a JWT session token for subsequent widget chat calls.

## Success Criteria

- The `partner_api_keys` table contains only API-key rows; no `integration_type`, `widget_id`, or `widget_config` column exists on it.
- A new `widgets` table contains all widget rows, with its own RLS policies tenant-scoped on `app.current_org_id` and its own `widget_kb_access` junction (read access only, no `access_level` column).
- **The `widgets` table contains no authentication-secret columns.** No `key_prefix`, `key_hash`, or `permissions` column exists on it. Widget authentication is handled by the existing JWT mechanism via `WIDGET_JWT_SECRET`.
- **No token starting with `pk_live_` is ever issued, stored, or referenced in any widget-domain code path.** Grep for `pk_live` across `klai-portal/backend/app/api/admin_widgets*`, `klai-portal/backend/app/models/widgets*`, `klai-portal/frontend/src/routes/admin/widgets/**` and `klai-portal/backend/tests/test_admin_widgets.py` yields zero matches.
- Every widget row in the old `partner_api_keys` table has been migrated idempotently to `widgets`, preserving its `id`, `widget_id`, `widget_config`, `org_id`, KB access rows, and `created_at`. The columns `key_prefix`, `key_hash`, `permissions`, and `active` are **not** copied — they have no analogue on the new table.
- The admin portal sidebar shows two separate menu items ("API keys" and "Chat widgets"); the `/admin/integrations` route no longer exists.
- The API-keys wizard has exactly four steps: Details, Permissions, Knowledge bases, Rate limit. The widgets wizard has exactly four steps: Details, Knowledge bases, Appearance, Embed. Neither wizard contains a type-select step.
- The API-keys detail view has five tabs (Details, Permissions, Knowledge bases, Rate limit, Danger zone) and the widgets detail view has five tabs (Details, Knowledge bases, Appearance, Embed, Danger zone). Tab order matches wizard step order.
- The "Revoke" action and the `active` boolean are gone for both domains. Only "Delete" remains in the Danger zone.
- Admin endpoints exist at `/api/api-keys` and `/api/widgets`. The `/api/integrations` prefix is removed (hard removal, no redirect). Both endpoint sets are covered by independent test files.
- Consumer endpoint `/partner/v1/chat/completions` authenticates exclusively against `partner_api_keys`. Consumer endpoint `/partner/v1/widget-config` resolves the widget row by public `widget_id` lookup against `widgets` and issues a JWT session token signed with `WIDGET_JWT_SECRET`.
- TypeScript types are strictly typed: a discriminator field does not exist in either `ApiKey` or `Widget`; no `IntegrationResponse` union type is imported anywhere.
- All tests green (backend pytest, frontend vitest, Playwright smoke for the two admin flows).

## Environment

This SPEC modifies the existing Klai portal environment:

- **Portal backend**: Python 3.13, FastAPI, SQLAlchemy 2.0 async, Alembic, PostgreSQL with RLS enforced via `app.current_org_id`.
- **Portal frontend**: React 19, Vite, TypeScript 5.9, TanStack Router, TanStack Query, Mantine 8, Paraglide i18n, Tailwind 4. UI components come from `components/ui/` exclusively.
- **Admin auth**: Zitadel OIDC session with `admin` or `owner` role.
- **RLS deployment constraint**: DDL for RLS policies (`CREATE POLICY`) must run as the `klai` superuser role. Migration files use a `DO` block with `EXCEPTION WHEN OTHERS` trap, established in today's migrations `e5f6g7h8i9j0_add_partner_api_keys_delete_policy.py` and `2464089c`.
- **Tenant scoping helper**: `app.set_tenant(org_id)` sets the `app.current_org_id` session variable; the admin helper `_get_caller_org` (in `app/api/admin/__init__.py`) must call this before any CRUD on RLS-scoped tables (established today in commit `e341f748`).
- **Widget consumer**: JavaScript bundle at `cdn.getklai.com/widget/klai-chat.js`, loaded with `<script data-widget-id="wgt_...">`.
- **Existing prior SPECs**: SPEC-API-001 (Partner API) and SPEC-WIDGET-001 (Chat Widget) are the sources being split. Both remain in `.moai/specs/` as historical references; their requirements live on in this SPEC.

Existing tables (relevant):

- `partner_api_keys` (id, org_id, name, description, integration_type, key_prefix, key_hash, permissions JSONB, rate_limit_rpm, widget_id, widget_config JSONB, active, created_by, created_at, last_used_at)
- `partner_api_key_kb_access` (partner_api_key_id, kb_id, access_level: 'read' | 'read_write')
- RLS policies on both tables: `partner_select`, `partner_insert`, `partner_update`, `partner_delete`, permissive on `partner_api_key_kb_access`.

## Assumptions

- Widget rows currently in production are few and well-understood. A one-time data migration is acceptable and will be executed as part of this SPEC's Alembic revision.
- Widgets never need a `read_write` KB permission. Any existing widget row with `access_level='read_write'` is a data-model accident and will be downgraded to `read` during migration with a warning logged to `stdout` (structlog `warning` event).
- Widget authentication relies exclusively on JWT session tokens signed with the deployment-level `WIDGET_JWT_SECRET` environment variable. This secret is a single shared value across all widgets in a deployment; it is rotated by rotating the env var (which invalidates all live session tokens). Per-widget secrets are explicitly not part of this SPEC (see Out of Scope). The JWT flow is already implemented in `klai-portal/backend/app/services/widget_auth.py` (HS256 signing, 1-hour expiry) and `klai-portal/backend/app/api/partner_dependencies.py` (token-shape branching).
- The `/api/integrations` admin endpoint prefix and the `/admin/integrations` frontend route are removed hard — no graceful deprecation, no 301 redirect. The admin frontend is deployed in the same GitHub Actions run as the backend; there are no external consumers of `/api/integrations` (consumer endpoints live at `/partner/v1/*` and are unaffected).
- Rate limiting for widgets stays at the current default (60 rpm). Rate-limit configurability for widgets is deliberately out of scope for this SPEC.
- The shared `KbAccessEditor` component continues to accept a `hideReadWrite` prop. Widgets pass `hideReadWrite: true`; API keys do not pass it.
- All copy today labelled "Allowed origins" continues to use the "Allowed websites" wording that was introduced today in commit `774ab881`.

## Out of Scope

- Any behavioural change to the Partner API consumer endpoints beyond replacing the auth lookup (chat completions looks up `partner_api_keys`; widget-config resolves `widgets` by public `widget_id` and issues a JWT session token).
- New widget features (multi-widget per org is already supported, and no new widget UX is added).
- Restoring or redesigning revoke/active semantics — they are gone for both domains.
- Rate-limit editing for widgets (deferred).
- Any change to `/partner/v1/*` request or response schemas.
- Any change to the widget JS bundle hosted on `cdn.getklai.com`.
- Public/tenant-facing API changes beyond internal table references.
- Frontend design refresh of the KB selector or the wizard chrome — the visual shell stays exactly as it is after today's commits.
- Partner API analytics, product events, or dashboard changes.
- Deletion of the prior SPEC-API-001 and SPEC-WIDGET-001 files — they remain as historical context.
- Any change to the JWT signing algorithm, expiry window, or claim shape in `widget_auth.generate_session_token`.

> Note: "future per-widget JWT revocation" is intentionally **not** listed as out-of-scope because it is not needed. A compromised widget is handled by `DELETE /api/widgets/{id}` — the `widgets` row disappears, `/partner/v1/widget-config` can no longer look it up, and in-flight session tokens expire within one hour. No secondary revocation mechanism is required now or in the foreseeable future.

---

## Requirements

All requirements use EARS notation. Each requirement has a stable ID (`REQ-N`) and a category: **NEW** (introduces behaviour), **MODIFY** (changes existing behaviour), **REMOVE** (deletes existing behaviour).

### REQ-1: Strip widget columns from `partner_api_keys` [REMOVE]

**Ubiquitous**: The `partner_api_keys` table **shall** contain only API-key fields: `id`, `org_id`, `name`, `description`, `key_prefix`, `key_hash`, `permissions`, `rate_limit_rpm`, `created_by`, `created_at`, `last_used_at`.

**Event-Driven**: **When** the Alembic migration for this SPEC runs, the system **shall** drop the columns `integration_type`, `widget_id`, `widget_config` and the `ck_partner_api_keys_integration_type` CHECK constraint and the `uq_partner_api_keys_widget_id` unique constraint.

**Ubiquitous**: The `partner_api_keys` table **shall not** contain an `active` column after this SPEC. If `active` is present at migration time, it is dropped.

**Unwanted**: **If** any row in `partner_api_keys` has `integration_type = 'widget'` at migration time, **then** the migration **shall not** drop the widget columns until REQ-2 has copied the row to the new `widgets` table successfully.

### REQ-2: Introduce `widgets` table and `widget_kb_access` junction [NEW]

**Ubiquitous**: The system **shall** define a new `widgets` table with exactly the following columns — no authentication-secret columns (see REQ-3):

- `id` UUID primary key, server-default `gen_random_uuid()`
- `org_id` integer NOT NULL, foreign key to `portal_orgs.id` with `ON DELETE CASCADE`
- `name` varchar(128) NOT NULL
- `description` varchar(512) nullable
- `widget_id` varchar(64) NOT NULL UNIQUE (format `wgt_` + 40 hex chars; helper `generate_widget_id` moves from `app/models/partner_api_keys.py` to `app/models/widgets.py`)
- `widget_config` JSONB NOT NULL (allowed_origins, title, welcome_message, css_variables)
- `rate_limit_rpm` integer NOT NULL default 60
- `created_by` varchar(64) NOT NULL
- `created_at` timestamptz NOT NULL default `now()`
- `last_used_at` timestamptz nullable

**Ubiquitous**: The `widgets` table **shall not** contain `key_prefix`, `key_hash`, `permissions`, `active`, or `integration_type` columns. Widget authentication is handled entirely by JWT session tokens (REQ-3), so no per-widget secret is persisted.

**Ubiquitous**: The system **shall** define a new junction table `widget_kb_access` with columns `widget_id` UUID (FK `widgets.id` ON DELETE CASCADE) and `kb_id` integer (FK `portal_knowledge_bases.id` ON DELETE CASCADE). Both columns form the composite primary key. No `access_level` column exists.

**State-Driven**: **While** an authenticated admin or owner session is scoped to an org, the system **shall** enforce RLS on `widgets` via policies `widgets_select`, `widgets_insert`, `widgets_update`, `widgets_delete`, all of which check `org_id = app.current_org_id()`. The same applies to `widget_kb_access` via `widget_kb_access_select/insert/update/delete`, each joining via `widgets.org_id`.

**Unwanted**: **If** a SQL statement attempts to select, insert, update or delete a widget or widget_kb_access row when `app.current_org_id` is not set, **then** RLS **shall** block the statement.

### REQ-3: Widget authentication model — JWT-only, no per-widget secret [MODIFY]

**Context**: The v0.1.0 draft of this SPEC assumed widgets need a `pk_live_...` internal key (either a column on the widget row or an FK to a hidden `partner_api_keys` row). Inspection of `klai-portal/backend/app/services/widget_auth.py` and `klai-portal/backend/app/api/partner_dependencies.py` refutes that assumption: widget authentication is already 100% JWT-based via the deployment-level `WIDGET_JWT_SECRET` env var. No per-widget secret is issued, stored, or looked up anywhere in the current widget flow. This SPEC preserves that model.

**Ubiquitous**: Widget authentication **shall** rely exclusively on HS256 JWT session tokens signed with the `WIDGET_JWT_SECRET` environment variable. The `widgets` table **shall not** store any authentication secret, nor any FK to a row that does.

**Ubiquitous**: The helper `widget_auth.generate_session_token(wgt_id, org_id, kb_ids, secret)` **shall** remain the single entry point for minting widget session tokens. Its signature, expiry (1 hour), and claim shape are unchanged.

**Ubiquitous**: `partner_dependencies.get_partner_key` **shall** continue to branch on token shape: tokens starting with `pk_live_` resolve via SHA-256 lookup in `partner_api_keys.key_hash`; all other tokens are decoded as HS256 JWTs via `WIDGET_JWT_SECRET`. No widget-domain code path **shall** issue, store, or look up a `pk_live_...` token.

**Event-Driven**: **When** an admin creates a widget via `POST /api/widgets`, the system **shall** insert a row into `widgets` with `widget_id` generated by `generate_widget_id()` and return the widget representation. No secret is generated, persisted, or returned.

**Event-Driven**: **When** a browser calls `GET /partner/v1/widget-config?id=wgt_...`, the system **shall** resolve the widget row by public `widget_id` lookup against `widgets`, validate the request origin against `widget_config.allowed_origins`, and issue a short-lived JWT session token signed with `WIDGET_JWT_SECRET` containing the `widget_id`, `org_id`, and authorised `kb_ids`.

**Unwanted**: **If** any HTTP response from a widget-domain endpoint (`/api/widgets*`, widget detail view, widget creation flow) contains a string matching `pk_live_`, **then** the response **shall** be rejected by a response-schema test.

**Unwanted**: **If** the Alembic migration for this SPEC (REQ-4) creates any column named `key_prefix`, `key_hash`, `permissions`, or `api_key_id` on the `widgets` table, **then** the migration is malformed and **shall not** be applied.

**Ubiquitous**: `WIDGET_JWT_SECRET` **shall** remain the single shared secret across all widgets in a deployment. It is rotated by rotating the env var (which invalidates all in-flight session tokens). Moving to per-widget secrets is explicitly out of scope for this SPEC (see Out of Scope).

### REQ-4: Data migration from `partner_api_keys` to `widgets` [NEW]

**Event-Driven**: **When** the Alembic upgrade runs, the system **shall** execute the following sequence inside a transaction:

1. `CREATE TABLE widgets` with exactly the columns specified in REQ-2 (no `key_prefix`, `key_hash`, `permissions`, `active`, or `api_key_id` column).
2. `CREATE TABLE widget_kb_access`
3. Enable RLS and create policies on both (inside the `DO $$ EXCEPTION WHEN insufficient_privilege THEN RAISE NOTICE ... END $$` block, matching today's pattern)
4. `INSERT INTO widgets (id, org_id, name, description, widget_id, widget_config, rate_limit_rpm, created_by, created_at, last_used_at) SELECT id, org_id, name, description, widget_id, widget_config, rate_limit_rpm, created_by, created_at, last_used_at FROM partner_api_keys WHERE integration_type = 'widget'`. The columns `key_prefix`, `key_hash`, `permissions`, and `active` **shall not** be copied — they have no analogue on the new table.
5. `INSERT INTO widget_kb_access (widget_id, kb_id) SELECT partner_api_key_id, kb_id FROM partner_api_key_kb_access WHERE partner_api_key_id IN (SELECT id FROM partner_api_keys WHERE integration_type = 'widget')`. During this insert, rows with `access_level = 'read_write'` are inserted as read-only and a `RAISE NOTICE` is emitted for each.
6. `DELETE FROM partner_api_key_kb_access WHERE partner_api_key_id IN (SELECT id FROM partner_api_keys WHERE integration_type = 'widget')`
7. `DELETE FROM partner_api_keys WHERE integration_type = 'widget'`
8. Drop columns `integration_type`, `widget_id`, `widget_config`, `active` from `partner_api_keys`
9. Drop CHECK constraint `ck_partner_api_keys_integration_type` and UNIQUE constraint `uq_partner_api_keys_widget_id`

**State-Driven**: **While** migration step 4 is running, the system **shall** preserve the original UUID of each widget row so that external references (none exist today, but future links should remain stable) continue to work.

**Ubiquitous**: The migration **shall** be idempotent: re-running the upgrade after partial failure **shall** detect already-migrated rows (by `widget_id` existing in `widgets`) and skip them.

**Unwanted**: **If** any step in the sequence fails, **then** the whole migration **shall** roll back and leave `partner_api_keys` in its pre-migration state.

**Unwanted**: **If** the migration attempts to copy the `key_prefix`, `key_hash`, `permissions`, or `active` value from a widget row into the new `widgets` table, **then** the migration is malformed and **shall** be rejected in code review. These four columns exist only on `partner_api_keys` and are dropped there in step 8; they have no destination on the widget side.

### REQ-5: Backend module split — new admin endpoints [NEW]

**Ubiquitous**: The system **shall** expose admin endpoints under exactly two prefixes: `/api/api-keys` and `/api/widgets`. Both are Zitadel-authenticated; both call `_get_caller_org()` (which calls `set_tenant`) before any DB access.

**Event-Driven**: **When** an admin calls `POST /api/api-keys`, the system **shall** create a `partner_api_keys` row, generate a fresh `pk_live_...` key, return the raw key exactly once (via `CreatedKeyModal` on the frontend), and hash + persist the key. The response does not include any widget field.

**Event-Driven**: **When** an admin calls `POST /api/widgets`, the system **shall** create a `widgets` row, generate a fresh `wgt_...` id via `generate_widget_id()`, and return the widget with its `widget_id` and `widget_config`. No `pk_live_...` secret is generated, stored, or returned; no "CreatedKeyModal" is shown for widget creation (unlike API-key creation which still shows the raw key once).

**Event-Driven**: **When** an admin calls `GET /api/api-keys` or `GET /api/widgets`, the system **shall** list rows scoped to the caller's org via RLS.

**Event-Driven**: **When** an admin calls `GET /api/api-keys/{id}` or `GET /api/widgets/{id}`, the system **shall** return the single row or 404 if not in scope.

**Event-Driven**: **When** an admin calls `PATCH /api/api-keys/{id}`, the system **shall** accept updates to `name`, `description`, `permissions`, `rate_limit_rpm` and the KB access set. **When** an admin calls `PATCH /api/widgets/{id}`, the system **shall** accept updates to `name`, `description`, `widget_config` and the KB access set.

**Event-Driven**: **When** an admin calls `DELETE /api/api-keys/{id}` or `DELETE /api/widgets/{id}`, the system **shall** delete the row and its KB-access rows via cascade.

**Unwanted**: **If** a request body for `POST /api/api-keys` contains any widget field (`widget_id`, `widget_config`, `integration_type`), **then** the request **shall** be rejected with HTTP 422 by Pydantic schema validation (using `model_config = ConfigDict(extra="forbid")`).

**Unwanted**: **If** a request body for `POST /api/widgets` contains any API-key-only field (`permissions`, `integration_type`), **then** the request **shall** be rejected with HTTP 422 by the same mechanism.

### REQ-6: Hard-remove the `/api/integrations` prefix and the unified admin module [REMOVE]

**Ubiquitous**: The file `klai-portal/backend/app/api/admin_integrations.py` **shall not** exist after this SPEC.

**Ubiquitous**: Any route at the prefix `/api/integrations` **shall** return HTTP 404. No 301/302 redirect, no deprecation header, no compatibility shim is added. The admin frontend and backend deploy together in the same GitHub Actions run (per `klai-portal/CLAUDE.md`); the admin UI is the only consumer of this prefix and is updated in the same commit that removes the backend routes.

**Unwanted**: **If** any import statement in the codebase references `app.api.admin_integrations`, **then** CI **shall** fail at the static-analysis step (ruff + pyright).

**Unwanted**: **If** any frontend route, router config, or navigation link references `/admin/integrations` or `/api/integrations`, **then** CI **shall** fail at the static-analysis step (vitest route smoke test + grep pre-commit hook).

### REQ-7: Consumer endpoint auth adapter [MODIFY]

**Event-Driven**: **When** `POST /partner/v1/chat/completions` receives a Bearer `pk_live_...` token, the system **shall** look up the token hash exclusively against `partner_api_keys.key_hash`. No fallback lookup against any widget-domain table is performed.

**Event-Driven**: **When** `POST /partner/v1/chat/completions` receives a Bearer JWT (non-`pk_live_*` token), the system **shall** decode it via `WIDGET_JWT_SECRET` using the existing `partner_dependencies.get_partner_key` branch and authorise the call against the widget and KB claims carried in the JWT payload. No per-widget secret is resolved.

**Event-Driven**: **When** `GET /partner/v1/widget-config?id=wgt_...` receives a widget id, the system **shall** look up the widget exclusively against `widgets.widget_id` (public lookup, no secret). It then validates the request Origin against `widget_config.allowed_origins` and issues a short-lived JWT session token signed with `WIDGET_JWT_SECRET` via `widget_auth.generate_session_token`.

**Ubiquitous**: The auth flow from widget-config to chat-completions is JWT-only end-to-end. No `pk_live_...` token is ever handed to a browser, and no widget-domain row is ever looked up by a `pk_live_...` hash.

### REQ-8: Frontend module split — dedicated routes [NEW]

**Ubiquitous**: The frontend **shall** host the API-key admin views at `klai-portal/frontend/src/routes/admin/api-keys/` and the widget admin views at `klai-portal/frontend/src/routes/admin/widgets/`. Each subtree contains `index.tsx`, `new.tsx`, `$id.tsx`, `-types.ts`, `-hooks.ts`, and `_components/`.

**Ubiquitous**: The folder `klai-portal/frontend/src/routes/admin/integrations/` **shall not** exist after this SPEC.

**Ubiquitous**: TypeScript types `ApiKey` and `Widget` **shall** be strictly typed with no optional discriminator field. A shared `IntegrationResponse` interface **shall not** exist.

**Ubiquitous**: The sidebar (`klai-portal/frontend/src/components/layout/Sidebar.tsx`) **shall** list two entries under the admin section with exactly these labels: **"API keys"** and **"Chat widgets"**. The menu item "Integrations" **shall not** exist after this SPEC. No alternative labels ("Developer API", "Widgets") are used; the Paraglide message keys **shall** encode these exact strings.

### REQ-9: API-keys wizard and tabs [NEW]

**Event-Driven**: **When** an admin opens `/admin/api-keys/new`, the system **shall** render a four-step wizard in exactly this order: `Details` → `Permissions` → `Knowledge bases` → `Rate limit`.

**Event-Driven**: **When** an admin opens `/admin/api-keys/{id}`, the system **shall** render five tabs in exactly this order: `Details`, `Permissions`, `Knowledge bases`, `Rate limit`, `Danger zone`.

**Ubiquitous**: The wizard **shall not** contain a Type-select step. The detail view **shall not** contain an Appearance tab, an Embed tab, or any widget-only concept.

**State-Driven**: **While** the `Danger zone` tab is active, the only action available **shall** be `Delete this API key`. No Revoke button **shall** exist.

### REQ-10: Widgets wizard and tabs [NEW]

**Event-Driven**: **When** an admin opens `/admin/widgets/new`, the system **shall** render a four-step wizard in exactly this order: `Details` → `Knowledge bases` → `Appearance` → `Embed`.

**Event-Driven**: **When** an admin opens `/admin/widgets/{id}`, the system **shall** render five tabs in exactly this order: `Details`, `Knowledge bases`, `Appearance`, `Embed`, `Danger zone`.

**Ubiquitous**: The wizard **shall not** contain a Type-select step, a Permissions step or a Rate-limit step.

**Ubiquitous**: The `Knowledge bases` step **shall** pass `hideReadWrite` to the shared `KbAccessEditor`, limiting the selector to `none` / `read`.

**State-Driven**: **While** the `Appearance` step is active, the system **shall** expose the four CSS variables from `widget_config.css_variables` as editable fields and **shall** reuse the Styling controls introduced today in commit `daf215a3`.

**State-Driven**: **While** the `Embed` step is active, the system **shall** render the embed-code preview introduced today in commit `daf215a3` and the Allowed-websites list with the copy from commit `774ab881`.

**State-Driven**: **While** the `Danger zone` tab is active, the only action available **shall** be `Delete this widget`.

### REQ-11: Copy, visual and component rules [MODIFY]

**Ubiquitous**: All user-facing copy referring to `widget_config.allowed_origins` **shall** use the phrase "Allowed websites" (singular "Allowed website") as introduced today. The word "origin" **shall not** appear in user-facing copy.

**Ubiquitous**: The list view (`/admin/api-keys` and `/admin/widgets`) **shall not** show a `key_prefix` column. The list view of API keys **shall** show Name, Description, Last used, Created, and Actions. The list view of widgets **shall** show Name, Description, Widget ID, Last used, Created, and Actions.

**Ubiquitous**: No `active` / `revoked` status badge **shall** appear anywhere in either domain.

**Ubiquitous**: All form fields continue to use primitives from `klai-portal/frontend/src/components/ui/` (per `klai-portal/CLAUDE.md`). Raw `<input>`, `<button>`, `<select>` with inline Tailwind **shall not** appear.

### REQ-12: Tests split and migration test [NEW]

**Ubiquitous**: The test file `klai-portal/backend/tests/test_admin_integrations.py` **shall** be split into `test_admin_api_keys.py` (covers REQ-5 API-key endpoints) and `test_admin_widgets.py` (covers REQ-5 widget endpoints).

**Ubiquitous**: The test file `klai-portal/backend/tests/test_widget_config.py` **shall** be updated to look up `widgets` instead of `partner_api_keys`.

**Ubiquitous**: A new test file `klai-portal/backend/tests/test_migration_widget_split.py` **shall** exercise REQ-4 end-to-end against a test Postgres:
  - seed `partner_api_keys` with one API-type row and two widget-type rows (one with `access_level='read_write'` in its KB access)
  - run the Alembic upgrade
  - assert `widgets` contains the two widget rows with preserved ids and `widget_kb_access` rows are all `read` (downgrade happened, warning emitted)
  - assert `partner_api_keys` contains only the API-type row
  - assert the removed columns are gone

**Event-Driven**: **When** CI runs, all three test files **shall** pass on a clean Postgres.

### REQ-13: Deployment order and rollback [NEW]

**Ubiquitous**: Backend and frontend **shall** deploy together in one GitHub Actions run via the existing `klai-portal` deploy workflow (per `klai-portal/CLAUDE.md`).

**Event-Driven**: **When** the Alembic migration is executed on a server that lacks `klai` superuser privileges, the migration **shall** surface a clear `RAISE NOTICE` via the existing `DO $$ EXCEPTION WHEN insufficient_privilege` pattern and **shall** abort with a non-zero exit code, not a silent skip.

**Ubiquitous**: A rollback plan **shall** exist: `alembic downgrade -1` re-creates `integration_type`, `widget_id`, `widget_config`, `active` on `partner_api_keys`, copies widget rows back from `widgets`, then drops `widgets` and `widget_kb_access`. The downgrade is idempotent.

**Unwanted**: **If** downgrade is executed after new widget rows have been created on the split schema and one of those widgets relies on a column that did not exist pre-split, **then** the downgrade **shall** abort with a clear error before any destructive DDL is executed.

### REQ-14: Observability and audit [MODIFY]

**Ubiquitous**: Structlog events for admin CRUD on API keys **shall** carry `domain="api_key"`, and events for admin CRUD on widgets **shall** carry `domain="widget"`. No event emitted after this SPEC **shall** carry `integration_type` as a structured field.

**Ubiquitous**: Existing `audit` entries written by `app.api.admin.audit` **shall** continue to carry the caller, action, and target id. No backfill of old `integration_type='widget'` audit rows is performed.

### REQ-15: Remove revoke concept [REMOVE]

**Ubiquitous**: The string `revoke` and its translations **shall not** appear in any user-facing admin copy, button, tab, or URL after this SPEC.

**Ubiquitous**: The component `klai-portal/frontend/src/routes/admin/integrations/_components/RevokeConfirmDialog.tsx` **shall not** have a successor component in either `api-keys/_components/` or `widgets/_components/`.

**Unwanted**: **If** any backend endpoint route, Pydantic model, or frontend hook contains the identifier `revoke`, `revoked`, or `is_active` after this SPEC, **then** CI **shall** fail at the static-analysis step.

---

## Verification Summary

Each requirement maps to one or more acceptance scenarios in `acceptance.md`. Each requirement has a testable assertion. The `acceptance.md` file expands these into Given-When-Then scenarios plus the full Definition of Done.

## Traceability

- Predecessor: SPEC-API-001 (Partner API, live)
- Predecessor: SPEC-WIDGET-001 (Klai Chat Widget, live)
- Related migrations (existing): `b1f2a3c4d5e6_add_partner_api_keys.py`, `d4e5f6g7h8i9_add_integration_type_and_widget_id.py`, `e5f6g7h8i9j0_add_partner_api_keys_delete_policy.py`
- Fixes that motivated this SPEC:
  - `656989d2` — fix(integrations): propagate integration_type and widget fields
  - `e341f748` — fix(admin): call set_tenant in admin `_get_caller_org` for RLS
  - `219d2a5c` — fix(integrations): add DELETE policy on partner_api_keys
  - `2464089c` — fix(migrations): wrap DELETE policy DDL in DO block
  - `dfa59fc0` — refactor(integrations): wizard for create, tabs for edit
  - `54eff0ac` — refactor(integrations): improve widget setup step UX
  - `7869458d` — refactor(integrations): dedicated Type column, drop key prefix from list
  - `daf215a3` — feat(integrations): add Styling step + embed code preview
  - `08f4ef65` — refactor(integrations): split widget wizard step 4 into Appearance + Embed
  - `774ab881` — refactor(integrations): rewrite allowed origins copy for non-technical admins

## Decisions resolved (v0.2.0)

All three open decisions from v0.1.0 have been resolved by Mark. They are retained here for traceability:

1. **Widget authentication model (was REQ-3 open question)**: Decided — **no per-widget secret**. The v0.1.0 assumption that widgets need a `pk_live_...` internal key (either a widget-owned column or an FK to a hidden `partner_api_keys` row) was wrong. Verified via `klai-portal/backend/app/services/widget_auth.py` and `klai-portal/backend/app/api/partner_dependencies.py`: widget auth is already JWT-only using `WIDGET_JWT_SECRET`. The `widgets` table now has no secret columns.
2. **Sidebar menu labels (REQ-8)**: Decided — **"API keys"** and **"Chat widgets"** (final strings; Paraglide message keys encode these).
3. **Removal strategy for `/api/integrations` (REQ-6)**: Decided — **hard-remove**. No 301 redirect, no deprecation window. Admin frontend and backend deploy together; no external consumers exist.

No open decisions remain. Implementation is unblocked.
