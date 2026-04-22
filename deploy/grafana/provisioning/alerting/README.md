# Grafana Alerting Provisioning

File-based provisioning for Grafana-managed alerting. Each file below lands
into Grafana at startup or after `docker compose up -d grafana` (recreate).

> `docker compose restart grafana` does NOT re-read bind-mount provisioning
> files. Always use `up -d` for config changes. (SEC-024 operational fix
> commit `2b0f697f`.)

## Files

| File | Purpose | SPEC |
|---|---|---|
| `contact-points.yaml` | Email receivers (`klai-dev-alerts-email`, `klai-ops-alerts-email`) + heartbeat webhook (`heartbeat-kuma`) | SEC-024, OBS-001 |
| `policies.yaml` | Notification routing: matchers on `spec=...` labels route to the right receiver | SEC-024, INFRA-005, OBS-001 |
| `rules.yaml` | Permanent zero-tolerance alert on docker-socket-proxy 403's | SEC-024-R12 |
| `persistence-rules.yaml` | Persistence file staleness + missing alerts | SPEC-INFRA-005 Phase 6 |
| `portal-api-rules.yaml` | 5xx rate, p95 latency, traffic drop (golden signals) | OBS-001-R9/R10/R11 |
| `infra-rules.yaml` | Container down, restart loop, core-01 disk saturation | OBS-001-R12/R13/R17 |
| `portal-events-rules.yaml` | FLUSHALL failure (LogsQL via VictoriaLogs) | OBS-001-R14 |
| `librechat-rules.yaml` | LibreChat chat.health_failed spike | OBS-001-R15 |
| `ingest-rules.yaml` | Knowledge-ingest error rate spike | OBS-001-R16 |
| `heartbeat-rules.yaml` | Synthetic always-fires rule → heartbeat-kuma webhook every 5 min | OBS-001-R22 |

> Rule-file names above marked "OBS-001" are added by SPEC-OBS-001 and land in
> subsequent commits. SEC-024 and INFRA-005 files are already present.

## Routing (see `policies.yaml`)

All rules set a `spec` label so routing is deterministic:

- `spec=SPEC-SEC-024` → `klai-dev-alerts-email` (security incidents)
- `spec=SPEC-INFRA-005` → `klai-dev-alerts-email` (persistence concerns)
- `spec=SPEC-OBS-001` + `alert_type=heartbeat` → `heartbeat-kuma` (5-min push to Uptime Kuma)
- `spec=SPEC-OBS-001` → `klai-ops-alerts-email` (operational alerts)
- Default fall-through → `grafana-default-email` (Grafana auto-installed)

## Environment variables

All SMTP/URL/recipient secrets are injected via `${VAR}` substitution. Grafana
reads these from its container environment. Declared in
`deploy/docker-compose.yml` grafana service; values from SOPS
(`klai-infra/core-01/.env.sops` → `/opt/klai/.env`):

| Var | Used by | Source |
|---|---|---|
| `GRAFANA_SMTP_PASSWORD` | GF_SMTP (Cloud86 outbound mail) | SEC-024 commit `994b504` |
| `ALERTS_EMAIL_RECIPIENTS` | `klai-ops-alerts-email` contact point | OBS-001 |
| `KUMA_HEARTBEAT_URL` | `heartbeat-kuma` contact point (push URL incl. token) | OBS-001 |
| `VICTORIALOGS_AUTH_USER` / `_PASSWORD` | VictoriaLogs datasource basic-auth | SEC-024 |

## Guardrails

Both gates run in CI on every PR touching this directory via
`.github/workflows/alerting-check.yml`:

1. `scripts/audit-alert-secrets.sh` — fails on literal SMTP-credential, token,
   or PEM-key patterns. Env-var substitution is the only legitimate route.
2. `scripts/verify-alert-runbooks.sh` — parses every rule's `runbook_url`
   annotation, verifies the referenced file + anchor exist in the repo.
   Missing annotation OR dangling anchor blocks the PR.

## LogsQL pitfall (SEC-024 M4.5 defect 1)

VictoriaLogs unqualified text-search only scans `_msg`. Our structlog output
puts fields in structured keys (`event:`, `error:`, `service:`). Always use
explicit `field:value` syntax in alert rules:

- ✅ `service:portal-api AND event:redis_flushall_failed`
- ❌ `portal-api redis_flushall_failed` (matches nothing)

Validate every new LogsQL rule via the VictoriaLogs MCP (`query`) before
landing it in a provisioning file.

## Verifying an alert end-to-end

1. Provision the rule on a branch.
2. After deploy-compose lands on main: `ssh core-01 "docker compose up -d grafana"`.
3. Grafana UI → Alerting → Rules: rule shows status "Normal" or "Firing" (not "Error").
4. Trigger the condition (real event, drill, or force via log injection — see
   the relevant runbook section in `docs/runbooks/platform-recovery.md`).
5. Within the rule's evaluation interval + policy's `group_wait`: email arrives
   at the recipient list for the matching `spec` label.

## Companion dashboards

- `Security — Proxy Denials` (`deploy/grafana/provisioning/dashboards/security-proxy-denials.json`) — SEC-024.
- Future: `Alerting Health` — OBS-001 Milestone 5 (fire rate per rule, eval latency, silence coverage).

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| Alert never fires despite matching condition | LogsQL query shape mismatch — see pitfall above. Inspect rule in UI → Test. |
| Alert fires but no email | SMTP misconfig. Check Grafana logs for `smtp` errors. Verify `GRAFANA_SMTP_PASSWORD` is set on the running container (`docker exec klai-core-grafana-1 printenv GF_SMTP_PASSWORD`). |
| Email goes to wrong inbox | `spec=` label missing or wrong on the rule — route matcher mismatches and falls through to the default receiver. |
| Alert fires every minute without stopping | Log line keeps repeating; `keepFiringFor` delays resolve but rule stays firing. Fix the underlying call. |
| Duplicate emails | `repeat_interval` too low, or multiple overlapping routes without `continue: false`. Check `policies.yaml`. |
| Provisioning change doesn't take effect | Used `restart` instead of `up -d` — bind-mount is not re-read. Recreate the container. |
