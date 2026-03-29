# SPEC-PERF-001: Frontend Web Vitals Monitoring

```yaml
id: SPEC-PERF-001
version: 1.0.0
status: completed
created: 2026-03-28
updated: 2026-03-29
author: manager-spec
priority: high
```

## Environment

- **Portal frontend:** React 19, Vite 7, TypeScript 5.9, TanStack Router — served at `my.getklai.com`
- **Portal backend:** Python 3.12, FastAPI, running as `portal-api:8010` on core-01
- **Monitoring stack:** Alloy (scraper) on `klai-net` + `monitoring` networks, VictoriaMetrics on `monitoring` network, Grafana for dashboards
- **Existing scrape pattern:** Alloy scrapes `retrieval-api:8040/metrics` every 30s and forwards to VictoriaMetrics via `prometheus.remote_write`
- **Error tracking:** GlitchTip (Sentry-compatible) with `@sentry/react`, `tracesSampleRate: 0.05`
- **Logging:** consola tagged loggers in `portal/frontend/src/lib/logger.ts` with Sentry reporter
- **Rate limiting:** Caddy applies 60 req/min/IP on `/api/*` routes
- **Existing dashboards:** `klai-health.json`, `klai-product.json`, `node-metrics.json`, `container-metrics.json`, `logs.json` — all provisioned via JSON at `deploy/grafana/provisioning/dashboards/`

## Assumptions

- A1: The `web-vitals` library (Google, ~1.5 kB gzipped) is the industry standard for collecting Core Web Vitals in the browser. It reports each metric once per page load via a callback.
- A2: `prometheus_client` (Python) is the standard library for exposing Prometheus-format metrics from Python applications. It provides in-memory Histogram storage with no external dependencies.
- A3: Alloy can reach `portal-api:8010` on `klai-net` — identical network topology to the existing `retrieval-api:8040` scrape.
- A4: The current user count is low enough that `tracesSampleRate: 0.3` (30%) will not cause GlitchTip quota issues.
- A5: `navigator.sendBeacon` is available in all target browsers (Chrome, Firefox, Safari, Edge — baseline 2017).
- A6: Caddy's existing rate limit of 60 req/min/IP is sufficient to prevent vitals endpoint abuse without a dedicated rate limit.
- A7: Histogram cardinality is bounded: 5 metrics x ~10 pages x 3 ratings = ~150 time series. This is negligible for VictoriaMetrics.

## Requirements

### R1: Frontend Web Vitals Collection

**WHEN** the portal frontend loads in a user's browser, **THEN** the system shall collect all five Core Web Vitals (LCP, FCP, INP, CLS, TTFB) using the `web-vitals` library.

**WHEN** the `visibilitychange` event fires (user navigates away or closes tab), **THEN** the frontend shall send the collected metrics to `POST /api/vitals` using `navigator.sendBeacon`.

**Details:**

- New file: `portal/frontend/src/lib/vitals.ts`
- New tagged logger: `perfLogger` in `portal/frontend/src/lib/logger.ts`
- The beacon payload is a JSON array of metric objects:
  ```json
  [
    {
      "name": "LCP",
      "value": 2450.5,
      "rating": "needs-improvement",
      "page": "/app/docs"
    }
  ]
  ```
- `name`: one of `LCP`, `FCP`, `INP`, `CLS`, `TTFB`
- `value`: numeric metric value (milliseconds for LCP/FCP/INP/TTFB, unitless score for CLS)
- `rating`: one of `good`, `needs-improvement`, `poor` (provided by `web-vitals` library)
- `page`: the current route path at time of metric collection (from TanStack Router)
- Metrics are buffered in a module-level array; the beacon sends whatever has been collected when visibility changes
- The `vitals.ts` module must be initialized from `main.tsx` after app setup

### R2: POST /api/vitals Backend Endpoint

**WHEN** the portal-api receives a `POST /api/vitals` request, **THEN** it shall validate the payload and record each metric into in-memory `prometheus_client` Histogram objects.

**Details:**

- New file: `portal/backend/app/api/vitals.py`
- New router registered in `portal/backend/app/main.py` via `app.include_router(vitals_router)`
- **Unauthenticated** — no Bearer token required (browser sends via sendBeacon which cannot set headers)
- Request body schema (Pydantic):
  ```python
  class VitalMetric(BaseModel):
      name: Literal["LCP", "FCP", "INP", "CLS", "TTFB"]
      value: float = Field(ge=0, le=60000)  # max 60s; CLS max is ~100 but 60000 covers all
      rating: Literal["good", "needs-improvement", "poor"]
      page: str = Field(max_length=256)
  ```
- Request body is `list[VitalMetric]` with max length 10 entries per request
- Each valid metric is `.observe()`-d on the corresponding Histogram
- Returns `204 No Content` on success (sendBeacon ignores responses)
- Returns `422 Unprocessable Entity` on validation failure

**Histogram definitions** (one per metric, with metric-appropriate buckets):

| Histogram name | Labels | Buckets |
|---|---|---|
| `webvitals_lcp_seconds` | `page`, `rating` | 0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 4.0, 5.0, 8.0, 10.0 |
| `webvitals_fcp_seconds` | `page`, `rating` | 0.25, 0.5, 0.75, 1.0, 1.5, 1.8, 2.5, 3.0, 5.0 |
| `webvitals_inp_seconds` | `page`, `rating` | 0.05, 0.1, 0.15, 0.2, 0.3, 0.5, 0.75, 1.0 |
| `webvitals_cls_score` | `page`, `rating` | 0.01, 0.025, 0.05, 0.1, 0.15, 0.2, 0.25, 0.5, 1.0 |
| `webvitals_ttfb_seconds` | `page`, `rating` | 0.1, 0.2, 0.4, 0.6, 0.8, 1.0, 1.5, 1.8, 2.5 |

- LCP, FCP, INP, TTFB values arrive in milliseconds from the browser; the endpoint converts to seconds before `.observe()` (Prometheus convention)
- CLS values are unitless scores and are stored as-is (no conversion)
- Histograms are instantiated at module level in `vitals.py` using `prometheus_client.Histogram`
- A counter `webvitals_reports_total` (no labels) tracks total POST requests received

### R3: GET /metrics Prometheus Endpoint

**WHEN** Alloy (or any Prometheus-compatible scraper) sends a `GET /metrics` request to portal-api, **THEN** the system shall respond with all registered `prometheus_client` metrics in Prometheus exposition format.

**Details:**

- New endpoint added to `portal/backend/app/api/vitals.py` (or a dedicated `metrics.py`)
- Uses `prometheus_client.generate_latest()` to produce the response body
- Content-Type: `text/plain; version=0.0.4; charset=utf-8`
- **Unauthenticated** — Alloy scrapes from within `klai-net`, no external access
- This endpoint fulfills SPEC-GRAFANA-METRICS R6

### R4: Alloy Scrape Configuration

**WHEN** Alloy runs its scrape cycle, **THEN** it shall scrape `portal-api:8010/metrics` every 30 seconds and forward the results to VictoriaMetrics.

**Details:**

- New scrape block in `deploy/alloy/config.alloy`:
  ```
  prometheus.scrape "portal_api" {
    targets = [{
      __address__ = "portal-api:8010",
    }]
    metrics_path    = "/metrics"
    forward_to      = [prometheus.remote_write.victoriametrics.receiver]
    scrape_interval = "30s"
    job_name        = "portal-api"
  }
  ```
- Follows the exact pattern of the existing `retrieval_api` scrape block (lines 92-100 of `config.alloy`)
- No network changes required — Alloy is already on `klai-net`

### R5: Grafana "Web Performance" Dashboard

**WHEN** a Klai operator opens Grafana, **THEN** a "Web Performance" dashboard shall be available with panels for each Core Web Vital showing p50/p95 trends over time, rating distribution, and page-level breakdown.

**Details:**

- New file: `deploy/grafana/provisioning/dashboards/web-performance.json`
- Datasource: `victoriametrics` (uid: `victoriametrics`, type: `prometheus`)
- Dashboard contains the following panels:

**Panel 1 — LCP p50/p95 Over Time (time series)**
```promql
histogram_quantile(0.5, sum(rate(webvitals_lcp_seconds_bucket[5m])) by (le))
histogram_quantile(0.95, sum(rate(webvitals_lcp_seconds_bucket[5m])) by (le))
```
Thresholds: green < 2.5s, yellow < 4s, red >= 4s

**Panel 2 — FCP p50/p95 Over Time (time series)**
```promql
histogram_quantile(0.5, sum(rate(webvitals_fcp_seconds_bucket[5m])) by (le))
histogram_quantile(0.95, sum(rate(webvitals_fcp_seconds_bucket[5m])) by (le))
```
Thresholds: green < 1.8s, yellow < 3s, red >= 3s

**Panel 3 — INP p50/p95 Over Time (time series)**
```promql
histogram_quantile(0.5, sum(rate(webvitals_inp_seconds_bucket[5m])) by (le))
histogram_quantile(0.95, sum(rate(webvitals_inp_seconds_bucket[5m])) by (le))
```
Thresholds: green < 0.2s, yellow < 0.5s, red >= 0.5s

**Panel 4 — CLS p50/p95 Over Time (time series)**
```promql
histogram_quantile(0.5, sum(rate(webvitals_cls_score_bucket[5m])) by (le))
histogram_quantile(0.95, sum(rate(webvitals_cls_score_bucket[5m])) by (le))
```
Thresholds: green < 0.1, yellow < 0.25, red >= 0.25

**Panel 5 — TTFB p50/p95 Over Time (time series)**
```promql
histogram_quantile(0.5, sum(rate(webvitals_ttfb_seconds_bucket[5m])) by (le))
histogram_quantile(0.95, sum(rate(webvitals_ttfb_seconds_bucket[5m])) by (le))
```
Thresholds: green < 0.8s, yellow < 1.8s, red >= 1.8s

**Panel 6 — Rating Distribution (pie chart per metric)**
```promql
sum(rate(webvitals_lcp_seconds_count[1h])) by (rating)
```
(Repeat for each metric)

**Panel 7 — Page Breakdown Table (table)**
```promql
histogram_quantile(0.95, sum(rate(webvitals_lcp_seconds_bucket[1h])) by (le, page))
```
Shows p95 LCP per page path — sortable, for identifying slow pages.

**Panel 8 — Report Volume (stat)**
```promql
sum(rate(webvitals_reports_total[5m])) * 60
```
Shows reports per minute — operational health indicator.

### R6: Sentry tracesSampleRate Increase

**WHEN** the portal frontend initializes Sentry, **THEN** the `tracesSampleRate` shall be set to `0.3` (up from `0.05`).

**Details:**

- File: `portal/frontend/src/main.tsx`, line 70
- Change `tracesSampleRate: 0.05` to `tracesSampleRate: 0.3`
- At current user volume, 30% sampling provides meaningful performance data without exceeding GlitchTip quota

## Specifications

### Dependencies

| Package | Location | Version |
|---|---|---|
| `web-vitals` | `portal/frontend/package.json` | `^4.0.0` (latest stable) |
| `prometheus-client` | `portal/backend/requirements.txt` | `>=0.21,<1.0` |

### Files Created

| File | Purpose |
|---|---|
| `portal/frontend/src/lib/vitals.ts` | Web Vitals collection and beacon sending |
| `portal/backend/app/api/vitals.py` | POST /api/vitals + GET /metrics endpoints |
| `deploy/grafana/provisioning/dashboards/web-performance.json` | Grafana dashboard JSON |

### Files Modified

| File | Change |
|---|---|
| `portal/frontend/src/main.tsx` | Import and initialize vitals module; bump tracesSampleRate to 0.3 |
| `portal/frontend/src/lib/logger.ts` | Add `perfLogger` tagged logger |
| `portal/backend/app/main.py` | Register vitals router |
| `deploy/alloy/config.alloy` | Add portal-api scrape block |
| `portal/frontend/package.json` | Add `web-vitals` dependency |
| `portal/backend/requirements.txt` | Add `prometheus-client` dependency |

### Architecture Decisions

#### AD1: GlitchTip behouden, niet migreren naar Grafana Faro

Onderzocht in maart 2026 (zie `research.md`). Drie factoren maken Faro ongeschikt voor Klai's self-hosted setup:

1. **Self-hosted Faro heeft geen error grouping** — errors worden raw log lines in VictoriaLogs. De "Issues" view met grouping, count, resolve/ignore is een Grafana Cloud-only feature. GlitchTip biedt dit out-of-the-box.
2. **faro.receiver heeft geen metrics output** — alleen `logs` en `traces`. Web Vitals zouden als log lines opgeslagen worden, niet als Prometheus histograms. Geen `histogram_quantile()` voor p50/p95 queries. Het web-vitals + prometheus_client pad in deze SPEC is objectief superieur.
3. **Geen TanStack Router support** — Faro's React integratie ondersteunt alleen React Router v6/v7. Klai verliest automatische route tracking.

Netto: GlitchTip verwijderen bespaart 2-4 containers (~256 MB-1 GB RAM), maar vereist custom error grouping dashboards, custom alerting, custom router instrumentatie, en een inferieure Web Vitals pipeline. De "minder bewegende delen" redenering gaat niet op.

Heroverweging triggers: (a) Grafana Faro voegt self-hosted error grouping toe, (b) GlitchTip community stopt met ontwikkeling, (c) Klai migreert naar Grafana Cloud.

#### AD2: web-vitals lib + Prometheus histograms (niet Faro SDK)

De `web-vitals` Google library (~1.5 kB gzipped) + `prometheus_client` Python histograms volgt het bestaande retrieval-api /metrics patroon exact. Voordelen boven Faro SDK (~18-25 kB):
- Native Prometheus histograms met `histogram_quantile()` voor percentile queries
- 5 regels Alloy scrape config vs. faro.receiver + source map pipeline
- Kleinere bundle impact (1.5 kB vs. 18-25 kB)
- Gescheiden concerns: error tracking (GlitchTip) en performance monitoring (VictoriaMetrics)

### Non-Goals

- Grafana Faro migratie (evaluated and rejected — see AD1 and `research.md`)
- Code splitting / bundle optimization (separate SPEC if needed)
- Synthetic monitoring (Lighthouse CI, WebPageTest)
- Custom performance marks beyond Core Web Vitals
- Alerting rules (will be added after baseline data is collected)
- User-facing performance dashboard (this is operator-only via Grafana)

### Traceability

| Requirement | Acceptance Criteria | Plan Task |
|---|---|---|
| R1 | AC-1.1, AC-1.2, AC-1.3 | T1, T2 |
| R2 | AC-2.1, AC-2.2, AC-2.3, AC-2.4 | T3, T4 |
| R3 | AC-3.1, AC-3.2 | T4 |
| R4 | AC-4.1, AC-4.2 | T5 |
| R5 | AC-5.1, AC-5.2, AC-5.3 | T6 |
| R6 | AC-6.1 | T7 |
