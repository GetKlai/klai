---
id: SPEC-PLATFORM-STATS-001
version: "0.1"
status: draft
created: 2026-07-07
updated: 2026-07-07
author: Mark Vletter
priority: medium
related: SPEC-PLATFORM-ADMIN-001 (platform console), SPEC-GRAFANA-METRICS (product_events)
---

## HISTORY

| Version | Date | Author | Change |
|---------|------|--------|--------|
| 0.1 | 2026-07-07 | Mark Vletter | Initial draft. Not yet implemented. |

---

# SPEC-PLATFORM-STATS-001: Platform Admin "Stats" tab — cross-tenant usage analytics

## 1. Context

Platform admins (Klai staff, `getklai` org) currently answer "how much is Klai
being used, and by whom?" via ad-hoc SSH + psql sessions against production
Postgres, or via the Grafana `klai-product` dashboard
(`deploy/grafana/provisioning/dashboards/klai-product.json`). Neither is
tenant-drillable from the product itself, and neither combines product usage
(portal `product_events`) with token/spend usage (LiteLLM DB) in one view.

The existing Platform admin console (`/admin/platform`,
SPEC-PLATFORM-ADMIN-001) already has the right chassis:

- **Frontend**: `klai-portal/frontend/src/routes/admin/platform/index.tsx`
  renders summary `StatCard`s and an underline tab row (`users`,
  `organizations`, `messages`, `knowledge-bases`, `templates`,
  `subscriptions`, `bots`, `feedback`, `chat-errors`, `status`, `subdomains`)
  with tab state in the URL search param. Tab bodies live in
  `-components/PlatformDashboardTabs.tsx`; data hooks in `-hooks.ts`; types in
  `-types.ts`. Tenant detail is a separate route
  (`orgs.$orgId.tsx`) with its own tab row — per portal UI standards
  (no drawers/sheets).
- **Backend**: `app/api/admin/platform.py` — every endpoint gated on
  `require_platform_admin()`, reads via `cross_org_session()` (RLS bypass),
  and writes a `platform_admin.viewed` audit event per read. Sub-routers are
  aggregated in `app/api/admin/__init__.py` under prefix `/api/admin`.

This SPEC adds a **"Stats" tab** to that console: platform-wide usage for a
selected period, a per-tenant usage table, and a click-through tenant usage
detail — combining two data sources that live in the same Postgres cluster
but in different databases.

### Data sources (verified 2026-07-07 against production)

**1. Portal DB `klai`, table `product_events`** (SPEC-GRAFANA-METRICS):

- Model: `app/models/events.py::ProductEvent` — columns `id`, `event_type`
  (String 64, indexed), `org_id` (nullable FK → `portal_orgs.id`, SET NULL),
  `user_id` (nullable String 64), `properties` (JSONB), `created_at`
  (timestamptz, server default now()).
- Indexes (migration `p6q7r8s9t0u1_add_product_events_table.py`):
  `idx_product_events_org_created (org_id, created_at)` and
  `idx_product_events_type_created (event_type, created_at)`.
- RLS: Category C (INSERT permissive / SELECT scoped) with a cross-org read
  policy already in place
  (`fb1c2d3e4a5b_product_events_cross_org_read_policy` + post-deploy SQL), so
  `cross_org_session()` reads work today.
- Key event types: `knowledge.queried` (primary Knowledge usage signal),
  `knowledge.uploaded`, `meeting.started`, `widget.created/updated/deleted`,
  `api_key.created/deleted/rotated`, `klai_assistant.problem_report`,
  `login`, `signup`, `billing.*`, `connector.*`, `notebook.*`,
  `source.added`.
- Caveat: pre-auth events (`login`, `signup`) have `org_id = NULL` — they
  count in platform totals but never in per-tenant rows.

**2. LiteLLM DB `litellm`** (same Postgres container `klai-core-postgres-1`,
different database):

- `"LiteLLM_TeamTable"`: `team_id`, `team_alias` (observed to equal tenant
  slug, e.g. `voys`), `spend` (lifetime cumulative).
- `"LiteLLM_DailyTeamSpend"`: `team_id`, `date`, `model`, `api_requests`,
  `successful_requests`, `failed_requests`, `prompt_tokens`,
  `completion_tokens`, `spend`. This daily rollup is the query target — never
  the raw request log.
- Table names are CamelCase → SQL must use quoted identifiers.
- Tenant mapping: `portal_orgs.litellm_team_id` (String 128, nullable) is the
  canonical join key to `LiteLLM_TeamTable.team_id`. The earlier manual
  research joined via `team_alias = slug`; the SPEC treats `litellm_team_id`
  as primary (see Open Questions for the fallback decision).

**3. Sanity baseline (Voys, 2026-06-08 → 2026-07-07 UTC)** — used in the
verification plan, gathered via SSH/psql (background evidence only; the app
must go through backend endpoints, never SSH):

- 260 `knowledge.queried`, 11 distinct users, 262 product events total.
- LiteLLM: 324 API requests (225 successful / 99 failed), 3.27M tokens,
  ≈ $0.92 spend. Weekday-heavy; failed requests clustered 2026-06-15 →
  2026-06-17.

## 2. Goals

- G1: Platform admins see platform-wide Klai usage for a selected period
  (7d / 30d / 90d) in a new "Stats" tab on `/admin/platform`.
- G2: Platform admins see a per-tenant usage table (product + LiteLLM
  metrics side-by-side) with sortable columns and tenant search.
- G3: Platform admins click through to a per-tenant usage detail with a
  daily trend, event-type breakdown, and model breakdown.
- G4: All data is aggregate-only. No raw prompts, messages, document
  content, or PII leaves the database.
- G5: The LiteLLM data path is optional and fail-loud: when unconfigured
  the tab still works with product metrics only; when configured-but-broken
  the UI says so explicitly instead of showing zeros.

## 3. Non-goals

- Not a replacement for Grafana (`klai-product` dashboard stays; ops/alerting
  stays in Grafana).
- No per-user drilldown or user-level usage lists (privacy; aggregate only —
  `user_id` is only ever used inside `COUNT(DISTINCT ...)`).
- No billing/invoicing logic — spend is an *estimate* sourced from LiteLLM,
  not a billing source of truth.
- No custom date-range picker in v1 (see §13).
- No write operations of any kind; the entire feature is read-only.
- No changes to event emission — this SPEC only reads what
  SPEC-GRAFANA-METRICS already emits.

## 4. User stories & acceptance criteria

US-1 — As a platform admin, when I open `/admin/platform?tab=stats`, I see
platform-wide usage cards for the selected period.

- AC-1.1: WHEN the Stats tab loads with default range, THEN the system SHALL
  show aggregates for the last 30 days (UTC): total product events, knowledge
  queries, distinct active users, active tenants, LiteLLM API requests,
  successful/failed requests, total tokens, estimated spend.
- AC-1.2: WHEN I switch the range to 7d or 90d, THEN all cards, the tenant
  table, and any open detail SHALL re-query for that range, and the range
  SHALL be reflected in the URL search params (deep-linkable).
- AC-1.3: IF `LITELLM_ANALYTICS_DATABASE_URL` is not configured, THEN the
  LiteLLM cards SHALL render an explicit "not configured" state (not zeros)
  and the product-event cards SHALL work normally.
- AC-1.4: IF the LiteLLM DB query fails at runtime, THEN the response SHALL
  carry `litellm_available: false` + the UI SHALL show an explicit error
  state, and the failure SHALL be logged at ERROR level (fail loudly — no
  silent zeros).

US-2 — As a platform admin, I see per-tenant usage in a sortable table.

- AC-2.1: WHEN the Stats tab renders, THEN the tenant table SHALL list every
  non-deleted org with: name, slug, plan, billing status, knowledge queries,
  distinct active users, total product events, API requests,
  successful/failed counts, tokens, estimated spend, last activity timestamp.
- AC-2.2: Columns SHALL be client-side sortable; default sort is knowledge
  queries descending.
- AC-2.3: The existing search input SHALL filter the table on tenant
  name/slug (client-side).
- AC-2.4: Tenants without a `litellm_team_id` mapping SHALL show "—" in
  LiteLLM columns (never 0, which would be a false claim).
- AC-2.5: Tenants with zero activity in the range SHALL still be listed
  (with zeros for event counts), so the table doubles as an inactivity view.

US-3 — As a platform admin, I click a tenant row and land on a usage detail.

- AC-3.1: WHEN I click a row (or its detail action), THEN the app SHALL
  navigate to the existing tenant detail route
  `/admin/platform/orgs/$orgId?tab=usage` — a new "Usage" tab on the existing
  detail page (separate route pattern per portal UI standards; no drawer).
- AC-3.2: The Usage tab SHALL show, for the selected range: a daily bar
  chart of product events + knowledge queries, a daily failed-requests trend,
  a breakdown table by `event_type`, a breakdown table by LiteLLM `model`
  (requests, tokens, spend), distinct active users, and last activity
  timestamp.
- AC-3.3: The detail SHALL be aggregate-only: no event `properties` payloads,
  no message content, no user identifiers are rendered.

US-4 — Security invariants.

- AC-4.1: Every new endpoint SHALL be gated on `require_platform_admin()` and
  read via `cross_org_session()`, mirroring
  `app/api/admin/platform.py` (the `@MX:ANCHOR` security boundary).
- AC-4.2: Every read SHALL write a `platform_admin.viewed` audit event with
  `tab: "usage"` (+ `org_id` for detail reads), so cross-tenant access is
  never silent.
- AC-4.3: A non-platform-admin caller SHALL receive 403 on all new endpoints
  (test-proven).
- AC-4.4: The LiteLLM DB credential SHALL be a read-only role with SELECT on
  exactly the two tables needed — portal-api can never write to the LiteLLM
  DB even if compromised.

## 5. Backend design

### 5.1 New module

`klai-portal/backend/app/api/admin/platform_stats.py` — new file (keep the
1850-line `platform.py` from growing). Router:

```python
router = APIRouter(prefix="/platform/usage", tags=["platform-admin"])
```

Registered in `app/api/admin/__init__.py` next to the other platform
sub-routers (`router.include_router(platform_stats_router)`), so paths land
under `/api/admin/platform/usage/...`.

Auditing: reuse the same event shape as `platform.py::_audit` (module-private
there) — emit via `app.services.audit.log_event` with
`action="platform_admin.viewed"`, `tab="usage"`, and for detail reads the
target `org_id`. Do not refactor `platform.py` for this (minimal changes);
a small local `_audit_usage()` helper is fine.

### 5.2 Endpoints

All three: `Depends(require_platform_admin)`, `cross_org_session()`, shared
`range` query param `Literal["7d", "30d", "90d"]` (default `30d`), computed
as `[now_utc - N days, now_utc)`. Response models are Pydantic and include
the resolved `start` / `end` timestamps so the UI can render the exact
window.

**`GET /api/admin/platform/usage/overview?range=30d`**

```python
class PlatformUsageOverview(BaseModel):
    range: str
    start: datetime
    end: datetime
    litellm_available: bool          # False when unconfigured OR query failed
    litellm_configured: bool         # distinguishes "not set up" from "broken"
    # product_events (portal DB)
    total_events: int
    knowledge_queries: int
    knowledge_uploads: int
    meetings_started: int
    problem_reports: int
    active_users: int                # COUNT(DISTINCT user_id), user_id NOT NULL
    active_tenants: int              # COUNT(DISTINCT org_id), org_id NOT NULL
    # LiteLLM (litellm DB) — None when litellm_available is False
    api_requests: int | None
    successful_requests: int | None
    failed_requests: int | None
    total_tokens: int | None         # prompt + completion
    spend_usd: float | None
```

**`GET /api/admin/platform/usage/tenants?range=30d`**

Returns `list[PlatformUsageTenantRow]` — one row per non-deleted org
(`portal_orgs.deleted_at IS NULL`), zero-activity orgs included:

```python
class PlatformUsageTenantRow(BaseModel):
    org_id: int
    name: str
    slug: str
    plan: str
    billing_status: str
    litellm_team_id: str | None
    knowledge_queries: int
    active_users: int
    total_events: int
    last_activity_at: datetime | None   # MAX(product_events.created_at) in range
    api_requests: int | None            # None when no mapping or litellm down
    successful_requests: int | None
    failed_requests: int | None
    total_tokens: int | None
    spend_usd: float | None
```

No pagination in v1 (tenant count is low double digits; revisit at 200+
orgs). Sorting and search are client-side.

**`GET /api/admin/platform/usage/tenants/{org_id}?range=30d`**

404 when the org does not exist or is soft-deleted. Response:

```python
class PlatformUsageTenantDetail(BaseModel):
    org_id: int
    name: str
    slug: str
    range: str
    start: datetime
    end: datetime
    litellm_available: bool
    active_users: int
    last_activity_at: datetime | None
    daily: list[DailyUsagePoint]         # one entry per day in range (gaps zero-filled)
    event_type_breakdown: list[EventTypeCount]   # {event_type, count} desc
    model_breakdown: list[ModelUsageRow] | None  # {model, api_requests, tokens, spend_usd}

class DailyUsagePoint(BaseModel):
    date: date
    events: int
    knowledge_queries: int
    api_requests: int | None
    failed_requests: int | None
    tokens: int | None
    spend_usd: float | None
```

### 5.3 Queries — portal DB (`product_events`)

Plain SQLAlchemy Core / `text()` aggregates inside `cross_org_session()`.
All bucketing in UTC via `date_trunc('day', created_at AT TIME ZONE 'UTC')`
(document this in the response so the UI labels match). Representative
shapes (all hit the existing indexes):

```sql
-- overview
SELECT COUNT(*)                                        AS total_events,
       COUNT(*) FILTER (WHERE event_type = 'knowledge.queried')  AS knowledge_queries,
       COUNT(*) FILTER (WHERE event_type = 'knowledge.uploaded') AS knowledge_uploads,
       COUNT(*) FILTER (WHERE event_type = 'meeting.started')    AS meetings_started,
       COUNT(*) FILTER (WHERE event_type = 'klai_assistant.problem_report') AS problem_reports,
       COUNT(DISTINCT user_id) FILTER (WHERE user_id IS NOT NULL) AS active_users,
       COUNT(DISTINCT org_id)  FILTER (WHERE org_id IS NOT NULL)  AS active_tenants
FROM product_events
WHERE created_at >= :start AND created_at < :end;

-- per-tenant (LEFT JOIN keeps zero-activity orgs)
SELECT o.id, o.name, o.slug, o.plan, o.billing_status, o.litellm_team_id,
       COUNT(e.id)                                            AS total_events,
       COUNT(e.id) FILTER (WHERE e.event_type = 'knowledge.queried') AS knowledge_queries,
       COUNT(DISTINCT e.user_id)                              AS active_users,
       MAX(e.created_at)                                      AS last_activity_at
FROM portal_orgs o
LEFT JOIN product_events e
       ON e.org_id = o.id AND e.created_at >= :start AND e.created_at < :end
WHERE o.deleted_at IS NULL
GROUP BY o.id;
```

Detail adds a per-day grouped variant scoped to one `org_id` (zero-fill the
day gaps in Python, not SQL) and a `GROUP BY event_type` breakdown.

Never select `properties` content, `user_id` values, or any event payload —
only counts and timestamps (G4 / AC-3.3).

### 5.4 Queries — LiteLLM DB

Separate read-only engine (see §8). Two queries, merged with the portal
results **in Python keyed on `team_id`** — there is no cross-database join
in Postgres and we must not create one:

```sql
-- per-team aggregate for the range
SELECT team_id,
       SUM(api_requests)        AS api_requests,
       SUM(successful_requests) AS successful_requests,
       SUM(failed_requests)     AS failed_requests,
       SUM(prompt_tokens + completion_tokens) AS total_tokens,
       SUM(spend)               AS spend_usd
FROM "LiteLLM_DailyTeamSpend"
WHERE date >= :start_date AND date < :end_date
GROUP BY team_id;

-- tenant detail: add , date and/or , model to SELECT + GROUP BY,
-- filtered on team_id = :team_id
```

Notes:

- Quoted CamelCase identifiers are mandatory.
- `date` is a day-granular column (LiteLLM writes UTC days) — pass
  `:start_date` / `:end_date` as dates derived from the same UTC window as
  the portal query so both sources describe the same period.
- Run the portal query and the LiteLLM query concurrently with
  `asyncio.gather` + `asyncio.wait_for(..., timeout=5)` per klai async rules.
  A LiteLLM timeout/failure degrades to `litellm_available: false` + ERROR
  log with traceback (`exc_info=True`) — never a 500 for the whole endpoint,
  never silent zeros (fail-loud rule, AC-1.4).
- Mapping: build `{litellm_team_id → org}` from the portal rows; LiteLLM rows
  whose `team_id` maps to no org are summed into the platform overview but
  dropped from the tenant table (they're platform-level usage, e.g. shared
  keys). Log a `debug` with the unmapped team count.

## 6. Frontend UX plan

All work under `klai-portal/frontend/src/routes/admin/platform/`, following
existing patterns (portal ui-standards; Paraglide for every string; no new
chart library — mirror the hand-rolled SVG/CSS sparkline pattern from
`admin/widgets/_components/tabs/ActivityTab.tsx::HourlySparkline`).

### 6.1 Stats tab on the console

- `-types.ts`: add `'stats'` to `PlatformTab`.
- `index.tsx`: add `'stats'` to `VALID_TABS` and to `TABS` (label
  `m.platform_tab_stats`). Add a `range` search param
  (`'7d' | '30d' | '90d'`, absent = 30d) to `PlatformSearch` +
  `validateSearch` so range is deep-linkable (AC-1.2). Render
  `{tab === 'stats' && <StatsTab search={search} range={range} ... />}`.
  Add the new query keys to `refresh()` invalidation
  (`platform-usage-overview`, `platform-usage-tenants`).
- New component file `-components/stats/StatsTab.tsx` (own directory — this
  tab is bigger than the single-table tabs in `PlatformDashboardTabs.tsx`):
  - Range selector: three-segment control (7d / 30d / 90d) writing to the URL
    search param.
  - Overview: `StatCard` grid (reuse `@/components/ui/stat-card`) — events,
    knowledge queries, active users, active tenants, API requests,
    success/fail, tokens, spend. LiteLLM cards render a muted "LiteLLM niet
    geconfigureerd" / error state per AC-1.3/1.4.
  - Tenant table: same table conventions as `OrgsTab` (klai-hover rows,
    sortable headers via local `useState` sort key/direction, client-side
    search filter on the shared search input). Row click navigates to
    `/admin/platform/orgs/$orgId?tab=usage&range=...`.
- Data hooks in `-hooks.ts`: `usePlatformUsageOverview(range)`,
  `usePlatformUsageTenants(range)`, `usePlatformUsageTenantDetail(orgId,
  range)` — `apiFetch` + TanStack Query, keys include the range, gated on
  `auth.isAuthenticated` like the existing hooks. Types in `-types.ts`
  mirroring the Pydantic models.

### 6.2 Usage tab on tenant detail

- `orgs.$orgId.tsx`: add `'usage'` to `TabId` + `VALID_TABS`; accept the
  optional `range` search param.
- New section component in `-components/OrgDetailSections.tsx` (or a sibling
  `UsageSection.tsx`): daily bars (events + knowledge queries), a
  failed-requests trend row, event-type breakdown table, model breakdown
  table, active-users + last-activity stat cards. Simple SVG bars, brand
  tokens only (`var(--color-rl-accent)` for bars,
  `var(--color-destructive)` for the failed-trend).

### 6.3 i18n

New Paraglide keys (nl + en), at minimum: `platform_tab_stats` ("Stats"),
`platform_usage_range_7d/30d/90d`, card labels
(`platform_usage_events`, `platform_usage_knowledge_queries`,
`platform_usage_active_users`, `platform_usage_active_tenants`,
`platform_usage_api_requests`, `platform_usage_failed_requests`,
`platform_usage_tokens`, `platform_usage_spend`), table headers,
`platform_usage_litellm_unconfigured`, `platform_usage_litellm_error`,
`platform_usage_last_activity`, `platform_usage_no_mapping` ("—" tooltip),
detail-tab label `platform_org_tab_usage`.

Number formatting: tokens compact (`3.3M`), spend as `$0.92` (USD — LiteLLM
reports USD; do not convert to EUR in v1, label the unit).

## 7. Security & privacy

- Platform-admin only: `require_platform_admin()` on every endpoint
  (AC-4.1); frontend tab is inside the already-gated platform console.
- Audit every read (`platform_admin.viewed`, `tab="usage"`) — AC-4.2.
- Aggregate-only by construction: SQL never selects `properties`, message
  bodies, or user identifiers; responses contain counts, sums, and
  timestamps only. `user_id` appears exclusively inside `COUNT(DISTINCT)`.
- LiteLLM DB access is a dedicated read-only role (§8) — SELECT on exactly
  `"LiteLLM_TeamTable"` and `"LiteLLM_DailyTeamSpend"`, nothing else. This
  keeps virtual keys, budgets, and user tables in the `litellm` DB
  unreadable by portal-api.
- No RLS interaction on the LiteLLM side (LiteLLM manages its own DB); on
  the portal side the existing product_events cross-org read policy +
  `cross_org_session()` is the sanctioned path.
- Secrets: the LiteLLM RO credential lives in SOPS (`klai-infra`), never in
  the repo. Empty value = feature off (see §8), so no fail-open risk: this
  is a read-only analytics path, not an auth path.

## 8. Migration & config impact (how the backend reaches the LiteLLM DB)

**Chosen approach: direct read-only SQL to the `litellm` database via a
second, small async engine.** Considered alternative — the LiteLLM proxy
admin HTTP API (`/global/spend/...` with the master key) — rejected for v1:
those endpoints vary by LiteLLM version, return shapes we'd have to
re-aggregate anyway, and would put the master key (a write-capable
credential) on a read path. A scoped RO database role is strictly less
privilege. The DB schema is vendor-internal, so we pin to the two observed
tables and fail loudly on drift (see Risks).

Steps, in deploy order (validator-env-parity pitfall — env var first, code
second):

1. **Create the RO role** (operator, as `klai` superuser on the production
   Postgres — post-deploy-SQL pattern; portal-api's alembic cannot and must
   not touch the `litellm` DB):

   ```sql
   CREATE ROLE litellm_ro LOGIN PASSWORD '<generated>';
   GRANT CONNECT ON DATABASE litellm TO litellm_ro;
   -- in the litellm database:
   GRANT USAGE ON SCHEMA public TO litellm_ro;
   GRANT SELECT ON "LiteLLM_TeamTable", "LiteLLM_DailyTeamSpend" TO litellm_ro;
   ```

   Password MUST be alphanumeric-only (URL-encoded-password pitfall — `/ + :`
   in DSN passwords break URL parsing).

2. **SOPS**: add `LITELLM_ANALYTICS_DATABASE_URL`
   (`postgresql+asyncpg://litellm_ro:<pw>@postgres:5432/litellm`) to
   `klai-infra/core-01/.env.sops` via the documented roundtrip workflow
   (line-count check).

3. **Compose**: portal-api uses an explicit `environment:` block — add
   `LITELLM_ANALYTICS_DATABASE_URL: ${LITELLM_ANALYTICS_DATABASE_URL:-}` to
   `deploy/docker-compose.yml`. Without this the var never reaches the
   container.

4. **Settings** (`app/core/config.py`):
   `litellm_analytics_database_url: str = ""` — empty string = feature
   disabled (klai feature-flag-via-empty-env-var pattern). Deliberately NO
   fail-closed validator: this is optional analytics, not auth, and a
   validator would 502 the whole portal on a missing var (validator-env-parity
   incident class).

5. **Engine** (`app/core/litellm_analytics.py`, new): lazily-created
   `create_async_engine(url, pool_size=2, max_overflow=2,
   pool_pre_ping=True)` + a `connect_args` statement timeout (5s). No
   sessionmaker/ORM needed — raw `text()` queries. Never imported by the
   main DB module (no coupling with the RLS session machinery).

- **No alembic migration** on the `klai` DB: `product_events`,
  `portal_orgs.litellm_team_id`, and both indexes already exist.
- **No new service, no new container, no Caddy change.**
- Local dev: var stays empty → tab works with product metrics only
  (AC-1.3) — no LiteLLM DB needed for development.

## 9. Performance & indexing

- Current volume is small (Voys, the most active tenant: 262 events / 30d;
  platform-wide comfortably in the low thousands per month).
  `idx_product_events_org_created` and `idx_product_events_type_created`
  cover every WHERE/GROUP BY in §5.3. No new indexes for v1.
- `LiteLLM_DailyTeamSpend` is already a daily rollup — 90d × teams × models
  is at most a few thousand rows; a seq scan is fine. Do NOT query LiteLLM's
  raw per-request tables.
- Concurrency: portal + LiteLLM queries run under `asyncio.gather` with
  per-call `wait_for` deadlines (5s); endpoint p95 target < 1s at current
  volume.
- Frontend: TanStack Query caches per `(endpoint, range)` key;
  `staleTime: 30_000` on the tenants query (mirrors the subdomains-hook
  precedent) so tab-flipping doesn't refire aggregates.
- Escape hatch documented, not built: if `product_events` grows past ~10M
  rows, introduce a nightly rollup table (`product_events_daily`) — out of
  scope now, noted in §13 so nobody bolts caching on ad hoc later.

## 10. Test strategy

Backend (`klai-portal/backend/tests/test_platform_usage_stats.py`, pytest +
existing test-DB fixtures):

1. **Gate**: non-platform-admin → 403 on all three endpoints; platform admin
   → 200 (mirrors existing platform.py gate tests) — AC-4.3.
2. **Audit**: each endpoint writes `platform_admin.viewed` with
   `tab="usage"` — AC-4.2.
3. **Aggregation correctness**: seed `product_events` fixtures (two orgs,
   mixed event types, one pre-auth NULL-org event, events straddling the
   range boundary) and assert overview counts, per-tenant rows
   (zero-activity org included, AC-2.5), boundary exclusion (`>= start`,
   `< end`), and NULL-org handling (in totals, not in tenant rows).
4. **LiteLLM merge logic**: pure-function tests — stub LiteLLM rows merged
   onto portal rows by `team_id`; unmapped team → overview only; org without
   `litellm_team_id` → `None` fields (AC-2.4).
5. **Degradation**: unconfigured URL → `litellm_configured=False`,
   `litellm_available=False`, product fields populated (AC-1.3);
   configured-but-raising engine (monkeypatched) → `litellm_available=False`
   + ERROR log captured, endpoint still 200 (AC-1.4).
6. **Detail**: unknown/soft-deleted org → 404; daily zero-fill covers every
   day in range; no `properties`/`user_id` values anywhere in the response
   schema (assert on the serialized payload) — AC-3.3.

Frontend:

7. Route/type changes compile (`tsc --noEmit`); `routeTree.gen.ts`
   regenerated and committed (known pitfall).
8. Vitest on sort/filter helpers if extracted; otherwise covered by e2e.
9. Playwright click-through (verify-changes-landed rule): open
   `/admin/platform?tab=stats`, switch range, sort a column, click a tenant
   row, assert the usage detail renders. Runs against local dev
   (`VITE_AUTH_DEV_MODE`) with seeded events.

## 11. Rollout & verification plan

1. Land infra prereqs first: RO role (§8.1), SOPS var (§8.2), compose var
   (§8.3). Verify:
   `docker exec klai-core-portal-api-1 printenv LITELLM_ANALYTICS_DATABASE_URL`.
2. Merge backend + frontend (single PR is fine — feature is dark until the
   tab is clicked; no flag needed beyond the env var).
3. Standard deploy DoD: `git push` → `gh run watch --exit-status` → confirm
   rollout on core-01.
4. **Sanity check against the Voys baseline** (§1.3): open Stats, range 30d
   — if the deploy window is close to 2026-07-07, Voys should show in the
   order of ~260 knowledge queries, ~11 active users, ~324 API requests,
   ~3.27M tokens, ~$0.92 spend. Cross-check any surprising delta with the
   same psql aggregates before trusting either side. Numbers drift with the
   moving window — same-day comparison only.
5. Verify the audit trail: `platform_admin.viewed` events with `tab=usage`
   appear after browsing the tab.
6. Verify AC-1.4 once deliberately: temporarily revoke... **no** — do not
   break prod to test. The degradation path is proven by test #5; in prod,
   verify only the happy path plus the "not configured" path on a local run.

## 12. Risks & open questions

Risks:

- **R1 — LiteLLM schema drift**: `LiteLLM_*` tables are vendor-internal, not
  a contract we own. An upstream LiteLLM upgrade may rename/alter columns.
  Mitigation: queries touch two tables/eight columns; failures surface as
  `litellm_available: false` + ERROR logs (never silent), and a pinned test
  documents the expected shape. Accept as residual risk.
- **R2 — mapping completeness**: `portal_orgs.litellm_team_id` is nullable.
  Tenants provisioned before team-id persistence may have NULL → "—" in
  LiteLLM columns despite real usage. See Q1.
- **R3 — spend semantics**: LiteLLM `spend` is its own cost estimate (USD).
  Label as "estimated"; never feed into billing.
- **R4 — two clocks**: portal buckets by timestamptz, LiteLLM by day-date.
  Both are forced to UTC windows, but a same-day comparison at the window
  edge can differ by partial-day. Acceptable for an analytics tab; the daily
  chart labels are UTC days.

Open questions (answer before or during implementation):

- **Q1**: How many production orgs have `litellm_team_id IS NULL` while a
  `LiteLLM_TeamTable.team_alias = slug` row exists? One psql check. If > 0:
  either backfill `litellm_team_id` (preferred — one-time operator UPDATE)
  or add a `team_alias = slug` fallback join in the merge logic. Preference:
  backfill; keep the code on one canonical key.
- **Q2**: Should the platform overview include LiteLLM usage from teams that
  map to no org (shared/platform keys)? Current answer per §5.4: yes in the
  overview, absent from the tenant table. Confirm with product owner.
- **Q3**: Tab label — "Stats" in both locales, or "Statistieken"/"Gebruik"
  in NL? Spec assumes "Stats" (per request); trivially changeable via
  Paraglide.
- **Q4**: Should `widget.*` / `api_key.*` / `connector.*` counts appear in
  the event-type breakdown only (current plan) or also as overview cards?
  Current plan keeps the overview to the eight core cards.

## 13. V1 scope & explicit out-of-scope

**V1 (this SPEC):**

- Stats tab with 7d/30d/90d ranges, overview cards, sortable per-tenant
  table, and Usage tab on the existing tenant detail route.
- Three read-only backend endpoints, one new RO DB role + env var.
- Aggregate-only, platform-admin-only, audited.

**Explicitly out of scope (deliberately NOT done in v1):**

- Custom date-range picker (add later if 7/30/90 proves insufficient).
- CSV/Excel export.
- Caching layers, materialized views, or rollup tables (volume doesn't
  justify it; escape hatch documented in §9).
- Per-user usage drilldown (privacy line: aggregates only).
- Charts beyond simple SVG bars (no chart library dependency).
- EUR conversion of spend, margin/cost analytics, billing integration.
- Alerting/thresholds (that's Grafana's job).
- Backfilling or changing event emission; any write path whatsoever.
- LiteLLM per-key or per-user spend tables (team level only).

## 14. Implementation pointers (for the executing agent)

| Concern | Where |
|---|---|
| Backend module (new) | `klai-portal/backend/app/api/admin/platform_stats.py` |
| Router registration | `klai-portal/backend/app/api/admin/__init__.py` |
| Settings field | `klai-portal/backend/app/core/config.py` |
| LiteLLM engine (new) | `klai-portal/backend/app/core/litellm_analytics.py` |
| Gate + session + audit reference | `klai-portal/backend/app/api/admin/platform.py` (header comment + `_audit`) |
| product_events model | `klai-portal/backend/app/models/events.py` |
| Org model / `litellm_team_id` | `klai-portal/backend/app/models/portal.py` |
| Tab wiring | `klai-portal/frontend/src/routes/admin/platform/index.tsx`, `-types.ts` |
| Stats tab UI (new) | `klai-portal/frontend/src/routes/admin/platform/-components/stats/StatsTab.tsx` |
| Hooks | `klai-portal/frontend/src/routes/admin/platform/-hooks.ts` |
| Tenant detail tab | `klai-portal/frontend/src/routes/admin/platform/orgs.$orgId.tsx` + `-components/OrgDetailSections.tsx` |
| Sparkline precedent | `klai-portal/frontend/src/routes/admin/widgets/_components/tabs/ActivityTab.tsx` |
| SQL inspiration | `deploy/grafana/provisioning/dashboards/klai-product.json` |
| Compose env block | `deploy/docker-compose.yml` (portal-api `environment:`) |
| SOPS | `klai-infra/core-01/.env.sops` (roundtrip + line-count check) |

Rules that bite here: portal UI standards (tabs/search-param/no-drawers),
Paraglide for all strings, `routeTree.gen.ts` must be committed, explicit
compose env block for portal-api, validator-env-parity (no fail-closed
validator for this optional var), URL-safe DB password, fail-loud on
external-provider drift, `asyncio.gather` + `wait_for` for parallel I/O.
