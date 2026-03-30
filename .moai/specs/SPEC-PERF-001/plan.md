# SPEC-PERF-001: Implementation Plan

```yaml
spec: SPEC-PERF-001
title: Frontend Web Vitals Monitoring
status: planned
priority: high
```

## Technical Approach

The implementation follows the existing metrics pipeline pattern established by `retrieval-api`. The architecture is a straight pipeline:

```
Browser (web-vitals) → POST /api/vitals → portal-api (prometheus_client Histograms)
                                          → GET /metrics → Alloy scrape → VictoriaMetrics → Grafana
```

No new infrastructure components are needed. Alloy already shares `klai-net` with `portal-api` and can scrape it identically to how it scrapes `retrieval-api:8040`.

## Task Breakdown

### Milestone 1: Backend Metrics Pipeline (Primary Goal)

**T1: Add `prometheus-client` dependency**
- File: `klai-portal/backend/requirements.txt`
- Add: `prometheus-client>=0.21,<1.0`
- Dependency: None

**T2: Create vitals router with POST /api/vitals and GET /metrics**
- File: `klai-portal/backend/app/api/vitals.py` (new)
- Create Pydantic model `VitalMetric` with validation (name, value, rating, page)
- Create request model as `list[VitalMetric]` with max 10 items
- Instantiate 5 Histogram objects at module level with metric-specific buckets
- Instantiate 1 Counter (`webvitals_reports_total`)
- POST handler: iterate metrics, convert ms to seconds (except CLS), observe on histograms
- GET /metrics handler: return `prometheus_client.generate_latest()` with correct Content-Type
- Return 204 for POST, text response for GET

**T3: Register vitals router in main.py**
- File: `klai-portal/backend/app/main.py`
- Add import: `from app.api.vitals import router as vitals_router`
- Add: `app.include_router(vitals_router)`
- Place after existing router registrations

### Milestone 2: Frontend Vitals Collection (Primary Goal)

**T4: Add `web-vitals` dependency**
- File: `klai-portal/frontend/package.json`
- Run: `npm install web-vitals`
- Dependency: None

**T5: Add `perfLogger` to logger.ts**
- File: `klai-portal/frontend/src/lib/logger.ts`
- Add: `export const perfLogger = logger.withTag('perf')`

**T6: Create vitals.ts collection module**
- File: `klai-portal/frontend/src/lib/vitals.ts` (new)
- Import `onLCP`, `onFCP`, `onINP`, `onCLS`, `onTTFB` from `web-vitals`
- Import `perfLogger` from `@/lib/logger`
- Maintain a module-level `metrics: VitalPayload[]` buffer
- Register callbacks for each metric that push `{ name, value, rating, page }` to the buffer
- Get `page` from `window.location.pathname` at callback time
- On `visibilitychange` (when `document.visibilityState === 'hidden'`), send buffered metrics via `navigator.sendBeacon('/api/vitals', JSON.stringify(metrics))` and clear the buffer
- Export an `initVitals()` function that registers all callbacks and the visibilitychange listener
- Use `perfLogger.debug()` for each metric collected (dev-only visibility)
- Dependency: T4, T5

**T7: Initialize vitals from main.tsx and bump tracesSampleRate**
- File: `klai-portal/frontend/src/main.tsx`
- Import `initVitals` from `@/lib/vitals`
- Call `initVitals()` after `createRoot(...).render(...)` (outside React tree, module-level side effect)
- Change `tracesSampleRate: 0.05` to `tracesSampleRate: 0.3` (line 70)
- Dependency: T6

### Milestone 3: Alloy Scrape Configuration (Secondary Goal)

**T8: Add portal-api scrape block to Alloy config**
- File: `deploy/alloy/config.alloy`
- Add new `prometheus.scrape "portal_api"` block targeting `portal-api:8010`
- Place between `retrieval_api` and `cadvisor` blocks
- Uses same pattern: `scrape_interval = "30s"`, `job_name = "portal-api"`
- Forward to `prometheus.remote_write.victoriametrics.receiver`
- Dependency: T2 (endpoint must exist before scraping)

### Milestone 4: Grafana Dashboard (Secondary Goal)

**T9: Create web-performance.json dashboard**
- File: `deploy/grafana/provisioning/dashboards/web-performance.json` (new)
- Datasource: `victoriametrics` (uid: `victoriametrics`)
- 8 panels as defined in spec.md R5:
  - 5 time-series panels (one per metric, p50 + p95 lines with threshold coloring)
  - 1 pie chart panel (rating distribution for all metrics)
  - 1 table panel (page breakdown by p95 LCP)
  - 1 stat panel (report volume per minute)
- Follow the JSON structure pattern from existing dashboards (e.g., `node-metrics.json`)
- Dependency: T8 (data must flow before dashboard is useful)

## Architecture Decisions

### AD0: GlitchTip + web-vitals over Grafana Faro

Evaluated in March 2026. Grafana Faro was rejected for Klai's self-hosted stack because:
- Self-hosted Faro has no error grouping (Grafana Cloud-only feature)
- `faro.receiver` has no metrics output — Web Vitals would be log lines, not Prometheus histograms
- No TanStack Router integration (only React Router v6/v7)

This SPEC implements Web Vitals via `web-vitals` lib + `prometheus_client` histograms, which provides native `histogram_quantile()` for percentile queries. GlitchTip remains for error tracking.

Full evaluation: `.moai/specs/SPEC-PERF-001/research.md`

### AD1: Unauthenticated vitals endpoint
`navigator.sendBeacon` cannot set custom headers (no `Authorization: Bearer`). The endpoint must be unauthenticated. Caddy's existing rate limit of 60 req/min/IP and strict Pydantic validation (allowlisted metric names, bounded values, max 10 entries) mitigate abuse.

### AD2: In-memory Histograms, not Postgres
Prometheus Histograms in `prometheus_client` are in-memory and reset on restart. This is fine because:
- VictoriaMetrics stores the scraped data with 30-day retention
- In-memory means zero database writes per vitals report
- Histogram quantile queries are native to PromQL

### AD3: Milliseconds-to-seconds conversion on the backend
The `web-vitals` library reports in milliseconds. Prometheus convention is seconds. The backend converts before `.observe()` to keep the Grafana queries consistent with other metrics (e.g., `klai_retrieval_step_seconds`).

### AD4: Single beacon on visibilitychange
Sending on `visibilitychange` (not on each metric callback) batches all collected metrics into one request. This minimizes network requests and is the recommended pattern from the `web-vitals` library documentation.

## Risks and Mitigations

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Endpoint abuse (fake metrics) | Low | Low | Caddy rate limit + Pydantic validation + bounded metric names |
| prometheus_client memory growth | Very Low | Low | Cardinality bounded at ~150 series (5 metrics x ~10 pages x 3 ratings) |
| sendBeacon dropped by browser | Low | Low | Acceptable data loss; RUM is statistical, not transactional |
| Alloy fails to scrape portal-api | Low | Medium | Same pattern as retrieval-api which works reliably; verify with `curl portal-api:8010/metrics` |
| GlitchTip quota at 30% sample rate | Low | Low | Current user count is low; monitor GlitchTip usage after deploy |
| Histogram reset on portal-api restart | Very Low | Low | VictoriaMetrics retains scraped data; short gaps during restart are acceptable |

## Deployment Sequence

1. Deploy backend changes first (T1-T3): new endpoint available, no data flowing yet
2. Deploy frontend changes (T4-T7): browsers start sending vitals
3. Deploy Alloy config (T8): scraping begins, data flows to VictoriaMetrics
4. Deploy Grafana dashboard (T9): operators can view data

Steps 1-3 can be deployed together in a single release. Step 4 can follow immediately or asynchronously.

## Expert Consultation Recommendations

- **expert-backend**: Review the vitals endpoint for security (unauthenticated endpoint patterns, validation completeness) and the prometheus_client integration pattern.
- **expert-frontend**: Review the `web-vitals` integration pattern, sendBeacon reliability, and the visibilitychange lifecycle.
