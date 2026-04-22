# Alerting Rollout Log

> Operational log for SPEC-OBS-001 alerting rollout.
> Per-rule tuning history, threshold changes, false-positive analysis.
> Quarterly review template at the bottom.

---

## Initial rollout — 2026-04-22

**Phases delivered**:

- **A**: contact point `klai-ops-alerts-email` (native Grafana email, reuses
  SEC-024's GF_SMTP / Cloud86 backend) + routing policy for `spec=SPEC-OBS-001`.
  Bonus fix: route added for `spec=SPEC-INFRA-005` (was firing into the void
  before — landed mail to `klai-dev-alerts-email` from this point).
- **B**: helper scripts `audit-alert-secrets.sh` + `verify-alert-runbooks.sh`
  + CI workflow `alerting-check.yml` to enforce on every PR.
- **C**: 3 PromQL rules (container_down, container_restart_loop,
  core01_disk_usage_high). Workflow fix added: deploy-compose now
  conditionally `--force-recreate grafana` when provisioning files
  actually change (rsync content checksum), so future rule pushes
  auto-deploy without manual intervention.
- **D**: 3 LogsQL rules (caddy_5xx_count_high, portal_redis_flushall_failed
  — THE primary gap-closer — and ingest_error_rate_elevated).
- **E**: heartbeat infrastructure. Synthetic always-firing rule pushes
  webhook to Uptime Kuma every ~3 min via contact point `heartbeat-kuma`.
  Uptime Kuma push monitor `Klai alerter heartbeat (OBS-001)` (token
  ends `...43252a`) listens; on missed heartbeats (5min interval +
  maxretries=1) it triggers its own SMTP notification to
  `mark.vletter@voys.nl` independent of Grafana.
- **F (deferred-rules cleanup)**: previously deferred R10/R11/R15 landed.
  R10 (caddy_p95_latency_high) via LogsQL `stats quantile(0.95, duration)`
  + reduce: last + threshold > 2.0s. R11 (caddy_traffic_drop) via two-query
  ratio with absolute-baseline guard (>600 req/h, avoids quiet-period
  false-positives without needing tz-aware business-hours filtering).
  R15 (librechat_health_failed_elevated) via case-insensitive substring
  match on `_msg` for "health" + "fail" — text-fragile but honest about
  the unstructured LibreChat log format. DEFERRED.md removed.

**Active alerts at end of rollout**:

| Rule | UID | Severity | Threshold | Routes to |
|---|---|---|---|---|
| caddy_5xx_count_high | `obs-001-caddy-5xx-rate-high` | critical | >10 5xx in 5m | klai-ops-alerts-email |
| caddy_p95_latency_high | `obs-001-caddy-p95-latency-high` | critical | p95 duration >2s for 5m | klai-ops-alerts-email |
| caddy_traffic_drop | `obs-001-caddy-traffic-drop` | critical | <20% of 1h baseline AND baseline>600 req/h, 10m | klai-ops-alerts-email |
| container_down | `obs-001-container-down` | critical | >120s gap, sustained 2m | klai-ops-alerts-email |
| container_restart_loop | `obs-001-container-restart-loop` | critical | >0 restarts in 15m, sustained 15m | klai-ops-alerts-email |
| core01_disk_usage_high | `obs-001-core01-disk-usage-high` | warning | <15% free, sustained 30m | klai-ops-alerts-email |
| ingest_error_rate_elevated | `obs-001-ingest-error-rate-elevated` | warning | >10 errors/10m | klai-ops-alerts-email |
| librechat_health_failed_elevated | `obs-001-librechat-health-failed-elevated` | warning | >5 health-fail mentions in 10m | klai-ops-alerts-email |
| portal_redis_flushall_failed | `obs-001-portal-redis-flushall-failed` | warning | any FLUSHALL failure | klai-ops-alerts-email |
| alerter_heartbeat | `obs-001-alerter-heartbeat` | (none) | always firing | heartbeat-kuma |

**Cosmetic residue**: Grafana DB carries an orphan rule
`obs-001-caddy-5xx-count-high` from a mid-Phase-D rename. Functionally
identical to the live `obs-001-caddy-5xx-rate-high` (same query, same
threshold, same routing → same mail dedup). Cleanup requires direct
Postgres DELETE on `alert_rule` — out of provisioning's reach.

---

## Tuning log

Each false-positive triage or threshold adjustment gets an entry below.
Format: date, rule, change, rationale.

| Date | Rule | Change | Rationale |
|---|---|---|---|
| 2026-04-22 | container_down | `for: 0s` → `for: 2m` | Self-inflicted false-positive on every Grafana redeploy: cAdvisor's old container time-series persists ~5m post-recreate, query matches both old (frozen mtime) and new (live) series by `name`. for: 2m absorbs the transient. |

---

## Quarterly review template

Run quarterly. Look at last 90 days of alert fires per rule. For each:

1. **Did this alert lead to action that wouldn't have happened without it?**
   If no: tighten threshold OR delete the rule.
2. **Were there any incidents this rule should have caught but didn't?**
   If yes: loosen threshold OR add a complementary rule.
3. **What's the false-positive rate?** Target: <1 per quarter per rule.
   If higher: tune (see Tuning log above).
4. **Top-5 noise-makers?** If a rule fires >10 times/quarter without
   genuine impact, it's a candidate for removal or major redesign.

Next review due: 2026-07-22.

---

## References

- SPEC: `.moai/specs/SPEC-OBS-001/spec.md`
- Provisioning README: `deploy/grafana/provisioning/alerting/README.md`
- Deferred rules: `deploy/grafana/provisioning/alerting/DEFERRED.md`
- Runbook (per-alert recovery): `docs/runbooks/platform-recovery.md`
