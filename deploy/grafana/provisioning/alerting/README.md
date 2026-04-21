# Grafana Alerting Provisioning (SPEC-SEC-024)

Alert rules, notification policies, and contact points for Grafana-managed alerting.

## Files

| File | Purpose | SPEC ref |
|---|---|---|
| `rules.yaml` | Alert rule that fires on docker-socket-proxy 403's | R12 |
| `policies.yaml` | Routes `spec=SPEC-SEC-024` alerts to `klai-dev-alerts-email` | R12 |
| `contact-points.yaml` | Email contact point definition | R13 |

## Deploy flow

**There is currently no CI step that rsyncs `deploy/grafana/` to core-01.**
The compose-sync workflow (`.github/workflows/deploy-compose.yml`) only copies
`docker-compose.yml` + its override file. This matches how `deploy/caddy/`,
`deploy/alloy/`, and `deploy/postgres/` work today — their config lives in
the repo for versioning, but landing on core-01 is a manual rsync or part of
initial setup (`deploy/setup.sh`).

SPEC-SEC-024 M4.3 extends `deploy-compose.yml` to also rsync this directory
and reload Grafana. Once that commit lands, any change here auto-deploys.

## SMTP pre-requisite (⚠️ required before AC-9 works)

`contact-points.yaml` provisions an email receiver, but Grafana needs an
SMTP relay to actually send mail. Add the following env vars to the
`grafana:` service in `deploy/docker-compose.yml` and provide the values
via SOPS:

```yaml
GF_SMTP_ENABLED: "true"
GF_SMTP_HOST: smtp.example.com:587
GF_SMTP_USER: ${GRAFANA_SMTP_USER}
GF_SMTP_PASSWORD: ${GRAFANA_SMTP_PASSWORD}
GF_SMTP_FROM_ADDRESS: alerts@getklai.com
GF_SMTP_FROM_NAME: Klai Grafana
GF_SMTP_STARTTLS_POLICY: MandatoryStartTLS
```

Options for the SMTP backend (pick one):

1. **SendGrid / Resend / Amazon SES** — cheapest, widely used. Create a
   single outbound-only API user, put credentials in SOPS.
2. **Use the existing Zitadel SMTP** — if Zitadel already has a configured
   outbound SMTP (see `.claude/rules/klai/platform/zitadel.md` runbooks),
   share those credentials. Watch out for rate limits and From-address
   policy if the provider is domain-scoped.
3. **Self-hosted relay** — adds ops burden; only worth it if we need many
   more alert destinations later.

**Not recommended:** sending directly from Gmail / Outlook personal accounts.
They rate-limit outbound mail aggressively and flag automated alerts as spam.

## Verifying the alert works end-to-end (SPEC-SEC-024-R14 / AC-9)

1. On a branch, add `container.exec_run(["true"])` to a portal-api code
   path that will execute on next request.
2. Merge, deploy to main, hit the endpoint that runs the new code.
3. Within 2 minutes, an email should arrive at the address in
   `contact-points.yaml`.
4. Revert the branch. After 30m of silence, Grafana auto-resolves the alert.

## Dashboard companion

The `Security — Proxy Denials` dashboard
(`deploy/grafana/provisioning/dashboards/security-proxy-denials.json`) visualises
the same signal with deploy annotations.

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| Alert never fires despite 403's | VictoriaLogs query shape mismatch. Inspect rule in UI → Test. |
| Alert fires but no email | SMTP not configured (see pre-req above). Check Grafana logs for `smtp` errors. |
| Alert fires every minute without stopping | Log line keeps repeating; `keepFiringFor: 30m` delays resolve but rule stays firing. Fix the underlying call. |
| Duplicate emails | `repeat_interval` too low or multiple matching policies. Check `policies.yaml`. |
