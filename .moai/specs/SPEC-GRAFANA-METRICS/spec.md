# SPEC-GRAFANA-METRICS: Grafana Product & Business Metrics Dashboards

## Metadata

| Field       | Value                                          |
|-------------|------------------------------------------------|
| SPEC ID     | SPEC-GRAFANA-METRICS                           |
| Title       | Grafana Product & Business Metrics Dashboards  |
| Created     | 2026-03-24                                     |
| Status      | In Progress                                    |
| Priority    | High                                           |
| Lifecycle   | spec-anchored                                  |

## Problem Statement

Klai has no visibility into business health or product usage. Customer counts, feature adoption, churn, and system health are only discoverable through manual database queries or anecdotal observation. This makes it impossible to make data-driven decisions about pricing, feature investment, or customer success.

## Constraints

- Open-source only, EU data residency (no Amplitude, Mixpanel, Segment, etc.)
- No new infrastructure: reuse existing `klai-core-grafana-1` and `klai-core-postgres-1` on core-01
- Privacy-first: no PII (names, emails) in analytics events -- use `org_id` and `user_id` only
- Dutch B2B context: metrics should reflect business accounts, not individual consumers
- Portal backend is Python/FastAPI with SQLAlchemy async + asyncpg

---

## Environment

- Grafana is running as `klai-core-grafana-1` on core-01
- Portal database is `klai-core-postgres-1` on core-01 (PostgreSQL, asyncpg)
- VictoriaMetrics is running as `klai-core-victoriametrics-1` on core-01
- Portal backend uses SQLAlchemy 2.0 async with Alembic migrations
- Existing tables: `portal_orgs`, `portal_users`, `vexa_meetings`

## Assumptions

- Grafana can connect to the portal PostgreSQL database as a read-only data source
- The portal Postgres instance can handle the additional query load from Grafana (polling dashboards)
- LiteLLM has a PostgreSQL database on the same host that Grafana can also connect to for chat/token metrics
- The portal backend can emit lightweight events without significant performance impact
- Grafana provisioning (dashboards-as-code via JSON) is the preferred approach for reproducibility

---

## Requirements

### R1: Events Table Schema

**WHEN** a user performs a trackable action in the portal backend, **THEN** the system shall insert a row into the `product_events` table with the event metadata.

The `product_events` table schema:

```sql
CREATE TABLE product_events (
    id          BIGSERIAL PRIMARY KEY,
    event_type  VARCHAR(64)  NOT NULL,  -- e.g. 'signup', 'meeting.started', 'notebook.created'
    org_id      INTEGER      REFERENCES portal_orgs(id),
    user_id     VARCHAR(64),            -- zitadel_user_id (NOT email/name)
    properties  JSONB        DEFAULT '{}',
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE INDEX idx_product_events_type_created ON product_events (event_type, created_at);
CREATE INDEX idx_product_events_org_created ON product_events (org_id, created_at);
```

The system shall NOT store any PII (names, emails, IP addresses) in the `properties` column or any other column of this table.

**Privacy principle for event properties:** Events shall capture *behavioural patterns*, never *content*. Permitted: `{scope, had_results, result_count, platform, duration_seconds}`. Not permitted: document names, query text, notebook titles, meeting titles, file names, or any customer data.

### R2: Event Emission from Portal Backend

**WHEN** any of the following actions occur, **THEN** the portal backend shall emit a product event:

| Event Type              | Trigger                                    | Properties                                          |
|-------------------------|--------------------------------------------|-----------------------------------------------------|
| `signup`                | Successful signup (POST /api/signup 201)   | `{plan: str}`                                       |
| `login`                 | Successful login (POST /api/auth/login)    | `{method: "password"\|"sso"}`                       |
| `billing.plan_changed`  | Plan change via mandate endpoint           | `{from_plan: str, to_plan: str, billing_cycle: str}`|
| `billing.cancelled`     | Subscription cancellation                  | `{plan: str}`                                       |
| `meeting.started`       | Bot dispatched (POST /api/bots/meetings)   | `{platform: str}`                                   |
| `meeting.completed`     | Webhook: meeting status -> done            | `{platform: str, duration_seconds: int}`            |
| `meeting.summarized`    | Summary generated                          | `{language: str}`                                   |
| `notebook.created`      | Focus notebook created (if portal proxies) | `{scope: str}`                                      |
| `knowledge.uploaded`    | Knowledge base document uploaded           | `{scope: str, file_type: str}`                      |
| `knowledge.queried`     | RAG lookup triggered in chat               | `{scope: str, had_results: bool, result_count: int}`|
| `notebook.opened`       | Focus notebook opened/accessed             | `{scope: str}`                                      |
| `source.added`          | Source added to Focus notebook             | `{scope: str}`                                      |

Event emission shall be fire-and-forget (non-blocking). If the insert fails, the failure shall be logged but the user-facing operation shall NOT be affected.

### R3: Grafana Data Source Configuration

The system shall configure two data sources in Grafana:

1. **Portal PostgreSQL** — pointing to `klai-core-postgres-1` with a read-only database user (see below).
2. **LiteLLM PostgreSQL** — pointing to the LiteLLM database on core-01 with a separate read-only user, for chat token and usage metrics.

The system shall configure a PostgreSQL data source in Grafana pointing to `klai-core-postgres-1` with a **read-only** database user.

```sql
CREATE USER grafana_reader WITH PASSWORD '<generated>';
GRANT CONNECT ON DATABASE portal TO grafana_reader;
GRANT USAGE ON SCHEMA public TO grafana_reader;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO grafana_reader;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO grafana_reader;
```

The Grafana data source shall NOT have write access to any table.

### R4: Business Metrics Dashboard

The system shall provide a Grafana dashboard named **"Klai Business"** with the following panels:

**P0 Panels (Primary Goal):**

| Panel                        | Type       | Query Source       | Description                                       |
|------------------------------|------------|--------------------|---------------------------------------------------|
| Customers by Plan            | Pie chart  | `portal_orgs`      | Count of orgs grouped by `plan` (excluding free)  |
| Total Seats Sold             | Stat       | `portal_orgs`      | SUM(seats) WHERE billing_status = 'active'        |
| Billing Status Distribution  | Pie chart  | `portal_orgs`      | Count of orgs grouped by `billing_status`         |
| New Signups Over Time        | Time series| `product_events`   | Count of `signup` events per day/week             |
| Cancellations Over Time      | Time series| `product_events`   | Count of `billing.cancelled` events per day/week  |
| MRR Estimate                 | Stat       | `portal_orgs`      | Calculated from plan prices x seats x billing_cycle|
| ARR (locked in)              | Stat       | `portal_orgs`      | SUM of yearly_early + yearly customers using ARR query — contractually committed revenue |
| Billing Cycle Distribution   | Pie chart  | `portal_orgs`      | Count of active orgs: monthly / yearly_early / yearly — shows early adopter mix and commitment |

**Pricing (as of 2026-03-24):**

| Plan | Monthly | Early adopter yearly | Regular yearly |
|---|---|---|---|
| `core` (Chat + Focus) | €28 | €20 | €22 |
| `professional` (+ Scribe) | €42 | €29 | €34 |
| `knowledge` (+ Knowledge) | €68 | €48 | €54 |

`billing_cycle` values: `monthly` \| `yearly_early` \| `yearly`

**MRR Calculation Logic** (normalised to monthly equivalent):

```sql
SELECT SUM(
    CASE
        WHEN plan = 'core'         AND billing_cycle = 'monthly'      THEN seats * 28
        WHEN plan = 'core'         AND billing_cycle = 'yearly_early'  THEN seats * 20
        WHEN plan = 'core'         AND billing_cycle = 'yearly'        THEN seats * 22
        WHEN plan = 'professional' AND billing_cycle = 'monthly'       THEN seats * 42
        WHEN plan = 'professional' AND billing_cycle = 'yearly_early'  THEN seats * 29
        WHEN plan = 'professional' AND billing_cycle = 'yearly'        THEN seats * 34
        WHEN plan = 'knowledge'    AND billing_cycle = 'monthly'       THEN seats * 68
        WHEN plan = 'knowledge'    AND billing_cycle = 'yearly_early'  THEN seats * 48
        WHEN plan = 'knowledge'    AND billing_cycle = 'yearly'        THEN seats * 54
        ELSE 0
    END
) AS mrr
FROM portal_orgs
WHERE billing_status = 'active';
```

**ARR (locked-in) Calculation** (yearly customers only — contractually committed revenue):

```sql
SELECT SUM(
    CASE
        WHEN plan = 'core'         AND billing_cycle = 'yearly_early'  THEN seats * 20 * 12
        WHEN plan = 'core'         AND billing_cycle = 'yearly'        THEN seats * 22 * 12
        WHEN plan = 'professional' AND billing_cycle = 'yearly_early'  THEN seats * 29 * 12
        WHEN plan = 'professional' AND billing_cycle = 'yearly'        THEN seats * 34 * 12
        WHEN plan = 'knowledge'    AND billing_cycle = 'yearly_early'  THEN seats * 48 * 12
        WHEN plan = 'knowledge'    AND billing_cycle = 'yearly'        THEN seats * 54 * 12
        ELSE 0
    END
) AS arr_locked
FROM portal_orgs
WHERE billing_status = 'active';
```

### R5: Product Metrics Dashboard

The system shall provide a Grafana dashboard named **"Klai Product"** with the following panels:

**P1 Panels (Secondary Goal):**

| Panel                        | Type        | Query Source       | Description                                        |
|------------------------------|-------------|--------------------|----------------------------------------------------|
| Daily Active Users (DAU)     | Time series | `product_events`   | Distinct user_ids with any event per day            |
| Weekly Active Users (WAU)    | Time series | `product_events`   | Distinct user_ids with any event per 7-day window   |
| Feature Adoption             | Bar chart   | `product_events`   | % of active orgs using each feature (Chat/Scribe/Focus) |
| Scribe: Meetings Per Week    | Time series | `vexa_meetings`    | Count of meetings with status='done' per week       |
| Scribe: Total Minutes        | Stat        | `vexa_meetings`    | SUM(duration_seconds)/60 for completed meetings     |
| Scribe: Platform Split       | Pie chart   | `vexa_meetings`    | Meetings by platform (Google Meet/Zoom/Teams)       |
| Provisioning Health          | Stat        | `portal_orgs`      | Count by provisioning_status (pending/done/failed)  |
| Chat: Tokens Per Org / Week  | Time series | LiteLLM DB         | Token consumption per org per week — proxy for chat intensity |
| Chat: Active Orgs (chat)     | Stat        | LiteLLM DB         | Distinct orgs with >0 tokens in last 7 days |
| Knowledge: KB Size Per Org   | Bar chart   | Qdrant metadata    | Chunk count per org — shows KB investment per customer |
| Knowledge: Queries Per Week  | Time series | `product_events`   | Count of `knowledge.queried` events per week — is the KB actually used? |
| Knowledge: Query Success Rate | Stat        | `product_events`   | % of `knowledge.queried` where `had_results = true` — retrieval quality indicator |
| Focus: Notebooks Created     | Time series | `product_events`   | Count of `notebook.created` events per week |
| Focus: Active Notebook Users | Stat        | `product_events`   | Distinct user_ids with `notebook.opened` in last 7 days |

**P2 Panels (Optional Goal):**

| Panel                        | Type        | Query Source       | Description                                        |
|------------------------------|-------------|--------------------|----------------------------------------------------|
| Activation Funnel            | Bar chart   | `product_events`   | signup -> first login -> first feature use          |
| Signup Retention Cohorts     | Heatmap     | `product_events`   | % of signup cohort active in week N                 |
| Knowledge Base Growth        | Time series | `product_events`   | Cumulative knowledge.uploaded events over time      |

### R6: System Health Dashboard

The system shall provide a Grafana dashboard named **"Klai Health"** with the following panels:

**P1 Panels:**

| Panel                        | Type        | Query Source         | Description                                      |
|------------------------------|-------------|----------------------|--------------------------------------------------|
| API Error Rate               | Time series | VictoriaMetrics      | HTTP 5xx responses per minute                    |
| API Latency P95              | Time series | VictoriaMetrics      | 95th percentile response time                    |
| Provisioning Failures        | Stat        | `portal_orgs`        | Count where provisioning_status = 'failed'       |
| Active Meeting Bots          | Stat        | `vexa_meetings`      | Count where status IN ('pending','joining','recording') |

**IF** VictoriaMetrics is not collecting FastAPI metrics, **THEN** the portal backend shall expose a `/metrics` endpoint using `prometheus-fastapi-instrumentator` (or equivalent) and VictoriaMetrics shall be configured to scrape it.

### R7: Event Emission Implementation

The system shall implement event emission as a lightweight async utility:

```python
# app/services/events.py
async def emit_event(
    db: AsyncSession,
    event_type: str,
    org_id: int | None = None,
    user_id: str | None = None,
    properties: dict | None = None,
) -> None:
    """Fire-and-forget event emission. Failures are logged, never raised."""
```

**WHEN** an event emission fails, **THEN** the system shall log the error at WARNING level and continue normal operation.

The system shall NOT use a message queue, Kafka, or any external event bus. Events are written directly to Postgres in the same transaction or a separate lightweight transaction.

### R8: Dashboard Provisioning

**Where possible**, Grafana dashboards shall be provisioned as JSON files mounted into the Grafana container, rather than created manually through the UI.

The dashboard JSON files shall be stored in the monorepo at `deploy/grafana/dashboards/` and mounted via the Grafana provisioning configuration.

---

## Specifications

### Event Naming Convention

Events shall follow a `{domain}.{action}` naming pattern:
- `signup` (top-level, no domain prefix -- it is the domain)
- `login`
- `billing.plan_changed`, `billing.cancelled`
- `meeting.started`, `meeting.completed`, `meeting.summarized`
- `notebook.created`, `notebook.opened`
- `knowledge.uploaded`, `knowledge.queried`
- `source.added`

### Data Retention

Product events shall be retained indefinitely in the initial implementation. **IF** the table grows beyond 10 million rows, **THEN** a partitioning strategy (by month on `created_at`) should be implemented.

### Grafana Dashboard Organization

Dashboards shall be organized in a Grafana folder named **"Klai"** with three dashboards:
1. `klai-business` — Business metrics (R4)
2. `klai-product` — Product metrics (R5)
3. `klai-health` — System health (R6)

### Read-Only Access Pattern

Grafana queries shall use the `grafana_reader` user which has SELECT-only permissions. No Grafana query shall modify data.

### Performance Considerations

- Event inserts are single-row, indexed operations -- negligible impact on API latency
- Grafana dashboard queries should use appropriate `WHERE created_at > now() - interval '30 days'` filters to avoid full table scans
- The `idx_product_events_type_created` index supports the most common Grafana query pattern (filter by event_type, group by time)

---

## Implementation Priority

| Priority        | Scope                                                    |
|-----------------|----------------------------------------------------------|
| Primary Goal    | Events table + migration, emit_event utility, Grafana Postgres data source, Business dashboard (R4) incl. ARR + billing cycle panels |
| Secondary Goal  | Product dashboard (R5 P1 panels), System Health dashboard (R6), Prometheus metrics endpoint, LiteLLM data source for chat metrics, Qdrant KB size query |
| Optional Goal   | Product dashboard P2 panels (activation funnel, retention cohorts), knowledge query success rate over time |

---

## Expert Consultation Recommendations

- **expert-backend**: Review event emission pattern, async performance impact, database indexing strategy
- **expert-devops**: Grafana data source configuration, dashboard provisioning, VictoriaMetrics scraping setup

---

## Traceability

| Tag                  | Reference                                           |
|----------------------|-----------------------------------------------------|
| SPEC-GRAFANA-METRICS | This specification                                  |
| portal_orgs          | `klai-portal/backend/app/models/portal.py`               |
| portal_users         | `klai-portal/backend/app/models/portal.py`               |
| vexa_meetings        | `klai-portal/backend/app/models/meetings.py`             |
| billing.py           | `klai-portal/backend/app/api/billing.py`                 |
| signup.py            | `klai-portal/backend/app/api/signup.py`                  |
| meetings.py          | `klai-portal/backend/app/api/meetings.py`                |
| auth.py              | `klai-portal/backend/app/api/auth.py`                    |
