# SPEC-PERF-001: Acceptance Criteria

```yaml
spec: SPEC-PERF-001
title: Frontend Web Vitals Monitoring
format: Given-When-Then (Gherkin)
```

## AC-1: Frontend Web Vitals Collection (R1)

### AC-1.1: All five Core Web Vitals are collected

```gherkin
Given the portal frontend is loaded in a browser
When the page finishes loading and all metrics fire their callbacks
Then the vitals buffer contains entries for LCP, FCP, INP, CLS, and TTFB
  And each entry has a "name", "value", "rating", and "page" field
  And "rating" is one of "good", "needs-improvement", or "poor"
  And "page" matches the current route path (e.g., "/app/docs")
```

### AC-1.2: Metrics are sent via sendBeacon on visibilitychange

```gherkin
Given the frontend has collected at least one metric in the buffer
When the user navigates away from the tab (visibilitychange to "hidden")
Then navigator.sendBeacon is called with URL "/api/vitals"
  And the payload is a JSON array of metric objects
  And the buffer is cleared after sending
```

### AC-1.3: No beacon is sent when buffer is empty

```gherkin
Given the frontend has collected zero metrics (e.g., immediate tab close before any callback fires)
When the visibilitychange event fires with state "hidden"
Then navigator.sendBeacon is NOT called
  And no network request is made to /api/vitals
```

### AC-1.4: perfLogger is used for debug output

```gherkin
Given the portal frontend is running in development mode (import.meta.env.DEV === true)
When a Web Vital metric callback fires
Then perfLogger.debug() is called with the metric name and value
  And the log output is tagged with "perf"
```

## AC-2: POST /api/vitals Backend Endpoint (R2)

### AC-2.1: Valid metrics are accepted and recorded

```gherkin
Given the portal-api is running
When a POST request is sent to /api/vitals with body:
  [{"name": "LCP", "value": 2450.5, "rating": "good", "page": "/app/docs"}]
Then the response status is 204 No Content
  And the webvitals_lcp_seconds histogram has recorded one observation
  And the observed value is 2.4505 (milliseconds converted to seconds)
  And the observation has labels page="/app/docs" and rating="good"
  And the webvitals_reports_total counter has incremented by 1
```

### AC-2.2: CLS values are stored without conversion

```gherkin
Given the portal-api is running
When a POST request is sent to /api/vitals with body:
  [{"name": "CLS", "value": 0.15, "rating": "needs-improvement", "page": "/app/meetings"}]
Then the response status is 204 No Content
  And the webvitals_cls_score histogram has recorded value 0.15 (no ms-to-s conversion)
```

### AC-2.3: Invalid metric names are rejected

```gherkin
Given the portal-api is running
When a POST request is sent to /api/vitals with body:
  [{"name": "INVALID_METRIC", "value": 100, "rating": "good", "page": "/"}]
Then the response status is 422 Unprocessable Entity
  And no histogram is updated
  And the webvitals_reports_total counter is NOT incremented
```

### AC-2.4: Oversized batches are rejected

```gherkin
Given the portal-api is running
When a POST request is sent to /api/vitals with 11 metric objects (exceeds max 10)
Then the response status is 422 Unprocessable Entity
  And no histograms are updated
```

### AC-2.5: Out-of-range values are rejected

```gherkin
Given the portal-api is running
When a POST request is sent to /api/vitals with body:
  [{"name": "LCP", "value": -5.0, "rating": "good", "page": "/"}]
Then the response status is 422 Unprocessable Entity

When a POST request is sent to /api/vitals with body:
  [{"name": "LCP", "value": 99999, "rating": "good", "page": "/"}]
Then the response status is 422 Unprocessable Entity
```

### AC-2.6: Oversized page path is rejected

```gherkin
Given the portal-api is running
When a POST request is sent to /api/vitals with a "page" field longer than 256 characters
Then the response status is 422 Unprocessable Entity
```

### AC-2.7: Batch with mixed valid and invalid entries

```gherkin
Given the portal-api is running
When a POST request is sent to /api/vitals with body:
  [
    {"name": "LCP", "value": 2000, "rating": "good", "page": "/app/docs"},
    {"name": "FAKE", "value": 100, "rating": "good", "page": "/"}
  ]
Then the response status is 422 Unprocessable Entity
  And the entire batch is rejected (no partial processing)
```

### AC-2.8: Empty batch is accepted

```gherkin
Given the portal-api is running
When a POST request is sent to /api/vitals with an empty array []
Then the response status is 204 No Content
  And no histograms are updated
  And webvitals_reports_total is incremented by 1
```

## AC-3: GET /metrics Prometheus Endpoint (R3)

### AC-3.1: Metrics endpoint returns Prometheus exposition format

```gherkin
Given the portal-api is running
  And at least one metric has been recorded via POST /api/vitals
When a GET request is sent to /metrics
Then the response status is 200
  And the Content-Type header is "text/plain; version=0.0.4; charset=utf-8"
  And the response body contains lines matching Prometheus exposition format
  And the body includes "webvitals_lcp_seconds_bucket" histogram lines
  And the body includes "webvitals_reports_total" counter line
```

### AC-3.2: Metrics endpoint works with no data recorded

```gherkin
Given the portal-api has just started (no POST /api/vitals received yet)
When a GET request is sent to /metrics
Then the response status is 200
  And the response body contains histogram definitions with zero counts
  And the body includes "webvitals_reports_total 0.0"
```

### AC-3.3: Metrics endpoint is unauthenticated

```gherkin
Given the portal-api is running
When a GET request is sent to /metrics without an Authorization header
Then the response status is 200 (not 401 or 403)
```

## AC-4: Alloy Scrape Configuration (R4)

### AC-4.1: Alloy scrapes portal-api every 30 seconds

```gherkin
Given the Alloy config at deploy/alloy/config.alloy contains a "portal_api" scrape block
  And the scrape target is "portal-api:8010"
  And the scrape interval is "30s"
When Alloy runs its scrape cycle
Then it sends a GET request to http://portal-api:8010/metrics
  And the scraped metrics are forwarded to prometheus.remote_write.victoriametrics.receiver
```

### AC-4.2: VictoriaMetrics stores the scraped metrics

```gherkin
Given Alloy has scraped portal-api at least once
When querying VictoriaMetrics with:
  webvitals_lcp_seconds_bucket
Then results are returned with job="portal-api" label
  And data points are present with timestamps within the last 60 seconds
```

## AC-5: Grafana Dashboard (R5)

### AC-5.1: Dashboard is auto-provisioned

```gherkin
Given the file deploy/grafana/provisioning/dashboards/web-performance.json exists
When Grafana starts or reloads its provisioning
Then a dashboard named "Web Performance" appears in the Grafana dashboard list
  And it uses the "victoriametrics" datasource
```

### AC-5.2: All required panels are present

```gherkin
Given the "Web Performance" dashboard is loaded in Grafana
Then the dashboard contains at least 8 panels:
  | Panel | Type | Metric |
  | LCP p50/p95 | time series | webvitals_lcp_seconds |
  | FCP p50/p95 | time series | webvitals_fcp_seconds |
  | INP p50/p95 | time series | webvitals_inp_seconds |
  | CLS p50/p95 | time series | webvitals_cls_score |
  | TTFB p50/p95 | time series | webvitals_ttfb_seconds |
  | Rating Distribution | pie chart | rating label counts |
  | Page Breakdown | table | p95 by page |
  | Report Volume | stat | webvitals_reports_total |
```

### AC-5.3: Threshold coloring matches Web Vitals standards

```gherkin
Given the LCP panel is displayed
Then the threshold zones are:
  | Color | Range |
  | green | < 2.5s |
  | yellow | 2.5s - 4s |
  | red | >= 4s |
And similar thresholds for each metric per the Web Vitals standard
```

## AC-6: Sentry tracesSampleRate Increase (R6)

### AC-6.1: tracesSampleRate is 0.3

```gherkin
Given the file portal/frontend/src/main.tsx
When the Sentry.init() call is inspected
Then tracesSampleRate is set to 0.3
  And no other Sentry configuration has changed
```

## Quality Gates

### Definition of Done

- [ ] All 5 Core Web Vitals are collected in the browser and sent via sendBeacon
- [ ] POST /api/vitals validates and records metrics in prometheus_client Histograms
- [ ] GET /metrics returns valid Prometheus exposition format
- [ ] Alloy config scrapes portal-api:8010/metrics
- [ ] Grafana "Web Performance" dashboard is provisioned with all 8 panels
- [ ] tracesSampleRate is 0.3 in main.tsx
- [ ] Backend endpoint has unit tests for validation (valid input, invalid names, out-of-range values, oversized batch)
- [ ] Frontend vitals module has unit tests for buffer management and sendBeacon triggering
- [ ] No existing tests broken
- [ ] `ruff check` passes on new Python files
- [ ] TypeScript strict mode passes on new frontend files
- [ ] No raw `console.log` in new code (use perfLogger)

### Verification Methods

| Verification | Method |
|---|---|
| Metrics collection works end-to-end | Open portal in browser, navigate pages, close tab, check `curl portal-api:8010/metrics` for recorded data |
| Alloy scrapes successfully | Check Alloy logs for successful scrape of `portal-api` job |
| VictoriaMetrics receives data | Query `webvitals_lcp_seconds_count` in Grafana Explore |
| Dashboard renders correctly | Open "Web Performance" dashboard in Grafana, verify all panels load |
| Sentry sample rate updated | Check GlitchTip transaction count after deploy (should increase ~6x) |
| Endpoint rejects invalid data | Run `curl -X POST -d '[{"name":"FAKE","value":1,"rating":"good","page":"/"}]' -H 'Content-Type: application/json' http://localhost:8010/api/vitals` and verify 422 response |
