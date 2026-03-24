# SPEC-GRAFANA-METRICS: Acceptance Criteria

## AC-1: Events Table Exists

**Given** the portal backend database
**When** the Alembic migration for `product_events` has been applied
**Then** the table `product_events` exists with columns: `id` (bigserial PK), `event_type` (varchar(64) NOT NULL), `org_id` (integer FK to portal_orgs), `user_id` (varchar(64)), `properties` (jsonb), `created_at` (timestamptz NOT NULL DEFAULT now())
**And** indexes exist on `(event_type, created_at)` and `(org_id, created_at)`

---

## AC-2: Event Emission on Signup

**Given** a new user submits the signup form with valid data
**When** the POST /api/signup endpoint returns 201
**Then** a row is inserted into `product_events` with `event_type = 'signup'`, the correct `org_id`, the `user_id` from Zitadel, and `properties` containing the assigned plan
**And** the signup response is NOT delayed or affected if the event insert fails

---

## AC-3: Event Emission on Login

**Given** a registered user with valid credentials
**When** the POST /api/auth/login endpoint returns a successful response
**Then** a row is inserted into `product_events` with `event_type = 'login'` and `properties` containing the login method ('password' or 'sso')

---

## AC-4: Event Emission on Billing Changes

**Given** an admin user changes their organization's plan
**When** the POST /api/billing/mandate endpoint completes successfully
**Then** a row is inserted into `product_events` with `event_type = 'billing.plan_changed'` and `properties` containing `from_plan`, `to_plan`, and `billing_cycle`

**Given** an admin user cancels their subscription
**When** the POST /api/billing/cancel endpoint returns successfully
**Then** a row is inserted into `product_events` with `event_type = 'billing.cancelled'` and `properties` containing the cancelled plan

---

## AC-5: Event Emission on Meeting Actions

**Given** a user starts a meeting bot
**When** the POST /api/bots/meetings endpoint returns 202
**Then** a row is inserted into `product_events` with `event_type = 'meeting.started'` and `properties` containing the platform

**Given** a meeting webhook reports completion
**When** the meeting status transitions to 'done' after transcription
**Then** a row is inserted into `product_events` with `event_type = 'meeting.completed'` and `properties` containing `platform` and `duration_seconds`

**Given** a meeting summary is generated
**When** the POST /api/bots/meetings/{id}/summarize endpoint returns successfully
**Then** a row is inserted into `product_events` with `event_type = 'meeting.summarized'` and `properties` containing the language

---

## AC-6: Fire-and-Forget Event Emission

**Given** the `product_events` table is unreachable or the insert fails
**When** any event emission is attempted
**Then** the error is logged at WARNING level
**And** the user-facing API response is returned normally without error
**And** the API response latency is NOT increased by more than 5ms due to the failed emission

---

## AC-7: No PII in Events

**Given** any product event is emitted
**When** the event row is inspected
**Then** no column or `properties` JSON key contains email addresses, names, IP addresses, or any other personally identifiable information
**And** users are identified only by `user_id` (Zitadel user ID) and `org_id` (portal_orgs FK)

---

## AC-8: Grafana Data Source

**Given** the Grafana instance at `klai-core-grafana-1`
**When** the PostgreSQL data source is configured
**Then** Grafana can execute SELECT queries against `portal_orgs`, `portal_users`, `vexa_meetings`, and `product_events`
**And** the data source uses a `grafana_reader` user with SELECT-only permissions
**And** the Grafana data source cannot INSERT, UPDATE, or DELETE any rows

---

## AC-9: Business Dashboard Panels

**Given** the "Klai Business" dashboard is loaded in Grafana
**When** a user views the dashboard
**Then** the following panels display correct, current data:
  - Customers by Plan: pie chart showing count of active orgs per plan tier
  - Total Seats Sold: stat showing SUM(seats) for active orgs
  - Billing Status Distribution: pie chart showing org counts per billing_status
  - New Signups Over Time: time series of signup events per day/week
  - Cancellations Over Time: time series of billing.cancelled events per day/week
  - MRR Estimate: stat calculated from plan prices x seats x billing_cycle

---

## AC-10: Product Dashboard Panels

**Given** the "Klai Product" dashboard is loaded in Grafana
**When** a user views the dashboard
**Then** the following panels display correct data:
  - DAU: time series of distinct user_ids with events per day
  - WAU: time series of distinct user_ids per 7-day window
  - Feature Adoption: bar chart showing % of active orgs using each feature
  - Scribe Meetings Per Week: time series from vexa_meetings
  - Scribe Total Minutes: stat from vexa_meetings duration_seconds
  - Scribe Platform Split: pie chart from vexa_meetings platform field

---

## AC-11: Health Dashboard Panels

**Given** the "Klai Health" dashboard is loaded in Grafana
**When** a user views the dashboard
**Then** the following panels display correct data:
  - Provisioning Failures: stat count of orgs with provisioning_status = 'failed'
  - Active Meeting Bots: stat count of meetings in active statuses

**If** VictoriaMetrics is scraping FastAPI metrics
**Then** the dashboard also shows:
  - API Error Rate: time series of HTTP 5xx per minute
  - API Latency P95: time series of 95th percentile response time

---

## AC-12: Dashboard Provisioning

**Where possible**, dashboard JSON files are stored in `deploy/grafana/dashboards/`
**And** Grafana loads them automatically via provisioning configuration on container restart
**And** no manual dashboard creation through the Grafana UI is required for the base dashboards

---

## Definition of Done

- [ ] Alembic migration for `product_events` table created and tested
- [ ] `emit_event()` utility implemented in `app/services/events.py`
- [ ] Event emissions added to signup, login, billing, and meeting endpoints
- [ ] `grafana_reader` database user created with SELECT-only permissions
- [ ] Grafana PostgreSQL data source configured
- [ ] "Klai Business" dashboard created with all P0 panels
- [ ] "Klai Product" dashboard created with all P1 panels
- [ ] "Klai Health" dashboard created with available panels
- [ ] No PII present in any product_events row (verified by manual review)
- [ ] Dashboard JSON files stored in `deploy/grafana/dashboards/`

---

## Traceability

| Acceptance Criteria | Requirement |
|---------------------|-------------|
| AC-1                | R1          |
| AC-2, AC-3, AC-4, AC-5 | R2      |
| AC-6                | R7          |
| AC-7                | R1, R2      |
| AC-8                | R3          |
| AC-9                | R4          |
| AC-10               | R5          |
| AC-11               | R6          |
| AC-12               | R8          |
