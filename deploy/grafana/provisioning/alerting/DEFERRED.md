# Deferred OBS-001 alert rules

Rules from SPEC-OBS-001 EARS that are NOT yet provisioned. Each entry
captures why deferral is the right call and what's needed to land it.

## R10 — `portal_api_latency_high` (CRIT)

**SPEC text**: portal-api p95 request-latency >2.0s for 5m.

**Why deferred**: SPEC assumed `histogram_quantile(0.95, sum(rate(caddy_http_request_duration_seconds_bucket{...}[5m])))`. Caddy doesn't expose Prometheus histograms in this setup; it logs `duration` (string seconds) per request to VictoriaLogs.

LogsQL `stats quantile(0.95, duration)` works at the API level (verified: returned p95=60ms over last 5m). What's NOT verified: that the VictoriaLogs Grafana plugin's alert-rule integration accepts `| stats` pipe expressions and exposes the result as a scalar to Grafana's threshold expression. SEC-024's existing rule uses raw queries + a `reduce: count` — no stats pipe. Need to manually validate the stats-query path in Grafana UI before YAML-provisioning it; otherwise the rule lands on "Error" state.

**To land**: open Grafana UI → New Alert Rule → VictoriaLogs query type "instant" → expression `service:caddy | stats quantile(0.95, duration) as p95` → confirm the rule preview returns a scalar p95 → export the YAML.

## R11 — `portal_api_traffic_drop` (CRIT)

**SPEC text**: portal-api request-rate <20% of hourly baseline for 10m during Europe/Amsterdam business hours.

**Why deferred**: needs ratio between current count and rolling baseline + time-window predicate (kantooruren only, no nights/weekends). Possible in LogsQL but:
- baseline window comparison requires two queries with different time ranges + math expression
- timezone-aware "kantooruren" filter in LogsQL has no direct equivalent of PromQL's `hour()`/`day_of_week()`. Either compute via `_time:offset` math or accept UTC approximation (kantooruren ≈ UTC 7-19, off by 1h for Amsterdam summer/winter).

The kantooruren-window false-positive risk in non-business-hours fires the alert when traffic is naturally low — eroding the trust this SPEC is trying to build. Better to defer than to ship a noisy rule.

**To land**: pick a clear approach (UTC approximation OR external recording job that materializes a `business_hours{...}` boolean metric), iterate threshold based on a week of observed traffic patterns.

## R15 — `librechat_health_failed_elevated` (HIGH)

**SPEC text**: `service:librechat-* AND event:chat.health_failed` count >5 in 10m.

**Why deferred**: spec field shape doesn't match production reality. LibreChat container logs in VictoriaLogs are **unstructured** (only `container`, `host`, `_msg` keys; no `service`, no `level`, no `event`). Format is ANSI-coloured text in `_msg`:
```
2026-04-22 08:48:28 [33m[3mwarn[23m[39m: [33m[3m[AGENTS] Forbidden: ...
```

Two paths forward:
1. **Text-based MVP**: query `container:~"librechat-.*" AND _msg:~"health.*failed"` — works but text-fragile (LibreChat string changes silently break the rule).
2. **Instrument LibreChat**: forward LibreChat health endpoint failures via a sidecar that emits structured logs to VictoriaLogs. Cleaner long-term; out of OBS-001 scope.

**To land**: pick path 1 if "any signal beats no signal", path 2 if you want a durable fix. Either way: needs a real LibreChat health failure to verify the rule fires correctly (no failures observed in current logs).
