# SPEC-GRAFANA-METRICS: Implementation Plan

## Milestones

### Primary Goal: Business Metrics Foundation

**Scope:** Events table, emit utility, Grafana data source, Business dashboard

1. **Create `product_events` table**
   - Write Alembic migration with table schema and indexes
   - Create `ProductEvent` SQLAlchemy model in `app/models/events.py`
   - Run migration on dev, verify table structure

2. **Implement `emit_event()` utility**
   - Create `app/services/events.py` with async fire-and-forget function
   - Use `try/except` with WARNING-level logging on failure
   - Write unit test to verify non-blocking behavior

3. **Add event emissions to existing endpoints**
   - `app/api/signup.py`: emit `signup` after successful signup
   - `app/api/auth.py`: emit `login` after successful login
   - `app/api/billing.py`: emit `billing.plan_changed` and `billing.cancelled`
   - `app/api/meetings.py`: emit `meeting.started`, `meeting.completed`, `meeting.summarized`

4. **Create `grafana_reader` database user**
   - SQL script to create read-only user
   - Grant SELECT on all tables + default privileges
   - Document credentials storage (SOPS or .env)

5. **Configure Grafana PostgreSQL data source**
   - Add data source via Grafana provisioning YAML
   - Store in `deploy/grafana/provisioning/datasources/`
   - Test connectivity from Grafana container

6. **Build "Klai Business" dashboard**
   - Create dashboard JSON with all P0 panels
   - MRR calculation query with plan pricing logic
   - Customers by Plan pie chart
   - Signups and Cancellations time series
   - Store in `deploy/grafana/dashboards/klai-business.json`

### Secondary Goal: Product & Health Dashboards

**Scope:** Product usage panels, system health monitoring

7. **Build "Klai Product" dashboard**
   - DAU/WAU time series from product_events
   - Feature adoption bar chart (meeting.started, notebook.created, login by org)
   - Scribe metrics from vexa_meetings table
   - Store in `deploy/grafana/dashboards/klai-product.json`

8. **Build "Klai Health" dashboard**
   - Provisioning status stats from portal_orgs
   - Active meeting bots stat from vexa_meetings
   - Store in `deploy/grafana/dashboards/klai-health.json`

9. **Prometheus metrics endpoint (if needed)**
   - Add `prometheus-fastapi-instrumentator` to portal backend
   - Expose `/metrics` endpoint
   - Configure VictoriaMetrics to scrape it
   - Add API error rate and latency panels to Health dashboard

### Optional Goal: Advanced Analytics

10. **Activation funnel panel**
    - Query signup -> first login -> first feature use from product_events
    - Add to Product dashboard

11. **Retention cohorts heatmap**
    - Cohort analysis query grouping by signup month
    - Add to Product dashboard

12. **LiteLLM data source**
    - If LiteLLM Postgres is accessible, add as second Grafana data source
    - Add chat message count and token usage panels

---

## Technical Approach

### Event Emission Pattern

```
API endpoint
  |-> business logic (existing)
  |-> emit_event(db, event_type, org_id, user_id, properties)
       |-> INSERT INTO product_events (fire-and-forget)
       |-> on error: log WARNING, continue
```

The event emission uses the same database session where possible (same transaction for consistency) but can fall back to a separate lightweight connection if needed.

### Grafana Dashboard-as-Code

```
deploy/grafana/
  provisioning/
    datasources/
      portal-postgres.yaml     # data source config
    dashboards/
      default.yaml             # dashboard provider config
  dashboards/
    klai-business.json         # Business metrics dashboard
    klai-product.json          # Product metrics dashboard
    klai-health.json           # System health dashboard
```

Dashboards are mounted into the Grafana container via Docker volume and automatically loaded on startup.

### Database Impact Assessment

- `product_events` is append-only with indexed columns -- minimal write impact
- Grafana polling interval: 30s-60s for dashboards, using time-bounded queries
- Read-only user prevents accidental data modification
- At current scale (early-stage), no partitioning needed

---

## Risks and Mitigation

| Risk                                    | Mitigation                                      |
|-----------------------------------------|-------------------------------------------------|
| Grafana cannot reach Postgres container | Verify Docker network connectivity; both on same Docker network on core-01 |
| Event emission slows down API endpoints | Fire-and-forget pattern; monitor latency after deployment |
| product_events table grows too large    | Add time-based partitioning if exceeding 10M rows |
| MRR calculation becomes stale with pricing changes | Extract pricing to a config table or constant module |
| Focus/Chat metrics unavailable from portal DB | Document limitations; add LiteLLM data source in Optional Goal |

---

## Architecture Notes

### What Data Lives Where

| Data                   | Source              | Accessible from Portal Postgres? |
|------------------------|---------------------|----------------------------------|
| Orgs, users, plans     | `portal_orgs/users` | Yes (direct query)               |
| Meeting recordings     | `vexa_meetings`     | Yes (direct query)               |
| Product events         | `product_events`    | Yes (new table)                  |
| Chat messages/tokens   | LiteLLM database    | No (separate DB, Optional Goal)  |
| Focus notebooks        | Research service     | No (separate service)            |
| Knowledge base chunks  | Qdrant              | No (vector DB)                   |

For Focus and Chat usage, the event emission approach bridges the gap: the portal backend emits events when it proxies requests to these services, capturing usage data in the portal Postgres without requiring direct access to external databases.

---

## Traceability

| Milestone       | Requirements |
|-----------------|-------------|
| Primary Goal    | R1, R2, R3, R4, R7, R8 |
| Secondary Goal  | R5, R6      |
| Optional Goal   | R5 (P2 panels) |
