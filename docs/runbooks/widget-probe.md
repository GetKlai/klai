# Widget Probe

Alerts: `widget_probe_failing`, `widget_probe_missing` (both `severity=critical`,
`page="true"`)

## Meaning

A systemd timer on core-01 asks the customer widget path one fixed question
every 5 minutes and logs a verdict. It walks the calls a customer's browser
makes (`klai-widget/src/api/widget-config.ts`, `chat-stream.ts`):

1. `GET https://api.getklai.com/partner/v1/widget-config?id=<widget id>` with an
   `Origin` from the widget's `allowed_origins` and an `X-Klai-Widget-Session-Id`,
   which returns a one-hour `session_token`.
2. `POST /partner/v1/chat/completions` with `Authorization: Bearer <token>` and
   `{"messages": [...], "stream": true}`, read as server-sent events.

The verdict is one JSON line in `/opt/klai/logs/widget-probe.log` (Alloy ships it
as `service:widget-probe event:widget_probe`):

```text
{"ts":..., "status":"ok|fail", "reason":..., "http_status":..., "latency_ms":..., "has_sources":..., "answer_chars":...}
```

`ok` needs HTTP 2xx, no error frame, a non-empty answer, at least one source and
a total time under 30000 ms. A fixed "Er ging iets mis" reply arrives as HTTP 200
without sources, so `no_sources` is the reason you see when the pipeline answers
but retrieval found nothing. `reason` is the first failing check:

| reason | meaning |
|---|---|
| `config` | `WIDGET_PROBE_WIDGET_ID` missing on core-01 |
| `unreachable` | no connection to `api.getklai.com` from core-01 (DNS, Caddy, portal-api down) |
| `timeout` | a read stalled for 45 s |
| `http` | non-2xx on the mint or the chat call (`http_status` says which code; 403 on the mint is the origin or widget, 401 on chat is the token, 5xx is portal-api) |
| `mint_response` | the mint answered 200 without `session_token` / `chat_endpoint` |
| `stream_error` | an SSE `error` frame (the answer model failed) |
| `empty_answer` | stream ended with no text |
| `no_sources` | answer without any citation: retrieval returned nothing or the fixed error reply |
| `latency` | answered, but slower than the threshold |

The 30 s threshold: over the last 7 days (to 2026-10-23) `partner_chat_turn_timing`
`total_ms` had p50 3.7 s and p95 18.7 s (527 turns, including the 03:00-10:40
incident); over the 7 days before the incident p95 was 11.9 s. Re-measure with

```text
_time:7d service:portal-api event:partner_chat_turn_timing
| stats quantile(0.95, total_ms) as p95
```

and change `WIDGET_PROBE_MAX_LATENCY_MS` (in the env file) if normal traffic moves.

## Confirm

```text
_time:30m service:widget-probe event:widget_probe | sort by (_time) desc | limit 10
```

`widget_probe_failing` fires when the last two verdicts are `fail`;
`widget_probe_missing` when no verdict arrived for 15 minutes. Both carry
`page="true"`, which the external pager reads from Grafana's Alertmanager API.

## What to check first

```text
_time:30m service:portal-api event:partner_chat_turn_timing | sort by (_time) desc | limit 20
_time:30m service:retrieval-api "graph_search_timeout" | stats count() as n
_time:30m service:litellm "crossed budget" | stats count() as n
_time:30m service:caddy status:5* | stats by (status) count() as n
```

- `retrieval_ms` high in `partner_chat_turn_timing`, or `graph_search_timeout`
  lines: retrieval or the graph (FalkorDB) is stalled. The 2026-10-23 incident
  looked like this.
- `crossed budget` from LiteLLM: a Mistral workspace is full, see
  [litellm-provider-auth-failed.md](litellm-provider-auth-failed.md).
- Caddy 5xx with no portal-api timing lines: portal-api itself is down.
- Probe reason `http` with 403 and a healthy widget elsewhere: someone changed the
  probe widget's `allowed_origins` or deleted the widget.
- Probe reason `no_sources` while everything else is healthy: the probe KB lost its
  document or the KB was unpublished from the widget.

`widget_probe_missing`, on core-01:

```bash
systemctl status widget-probe.timer widget-probe.service
journalctl -u widget-probe.service -n 30 --no-pager
tail -n 3 /opt/klai/logs/widget-probe.log
```

If the log file is fresh, Alloy or VictoriaLogs is the break.

## Install

`deploy-compose.yml` syncs the script to `/opt/klai/scripts/widget-probe.py` and
stages the unit and timer in `/opt/klai/systemd/`, but it runs no `systemctl`.
Activating them, or picking up a changed unit, is manual:

```bash
ssh core-01
sudo ln -sf /opt/klai/systemd/widget-probe.service /etc/systemd/system/widget-probe.service
sudo ln -sf /opt/klai/systemd/widget-probe.timer   /etc/systemd/system/widget-probe.timer
sudo systemctl daemon-reload
sudo systemctl enable --now widget-probe.timer
sudo systemctl start widget-probe.service && tail -n 1 /opt/klai/logs/widget-probe.log
```

Do this only after the provisioning below, or the first verdicts are `fail`
(`config` or `http`) and page.

## Provision the probe tenant (once, by hand)

The probe needs a widget whose knowledge base holds a document that answers the
probe question. Nothing here is created by the repo.

1. **Tenant.** Use the existing synthetic E2E tenant (`e2e.getklai.com`, see
   `klai-portal/frontend/e2e/prod-tenant/README.md`); it holds no customer data. It has
   no widget yet. Its cleanup only deletes artifacts named `e2e-...`, so name
   everything below with a `probe-` prefix to keep it out of that cleanup.
2. **Knowledge base** `probe-widget-kb` with one document (synthetic text, no real
   company or person):

   ```text
   Probe Bakkerij B.V. is a fictional company used only to test the Klai widget.
   The support team of Probe Bakkerij is available on working days from 08:15 to 17:45.
   You can reach the support team at support@probe.example.com.
   ```

   Wait until the document is ingested (status ready) and retrievable. The default
   probe question is `Wat zijn de openingstijden van het supportteam van Probe
   Bakkerij?`; if you change the document, set `WIDGET_PROBE_QUESTION` to match.
3. **Widget** `probe-widget` on that tenant, with `probe-widget-kb` as its only
   knowledge base and `https://probe.example.com` in `allowed_origins` (or the
   origin you put in `WIDGET_PROBE_ORIGIN`). Use a strict, default profile, like a
   customer widget. Note the `wgt_...` id.
4. **Secret.** Add to the SOPS-managed env inventory for core-01 (the file that
   becomes `/opt/klai/.env`; see `.claude/rules/klai/infra/secrets.md` and the
   private `klai-infra` runbook `sops-edit-via-core01.md`), then let `sync-env.yml`
   deliver it:

   ```text
   WIDGET_PROBE_WIDGET_ID=wgt_...
   WIDGET_PROBE_ORIGIN=https://probe.example.com
   ```

   Optional: `WIDGET_PROBE_QUESTION`, `WIDGET_PROBE_MAX_LATENCY_MS` (default 30000),
   `WIDGET_PROBE_BASE_URL` (default `https://api.getklai.com`). The widget id is the
   only credential the widget flow has; the short-lived session token is minted on
   every run and never stored or printed. No compose service reads these keys, so
   `deploy/SECRETS_MATRIX.md` has no row for them.
5. **Activate** the timer (Install above) and check the first verdict is `ok`.
   An `ok` also proves the CORS assumption: the probe sends the widget's
   `Origin` and fails with reason `cors` when the response lacks the matching
   `Access-Control-Allow-Origin`.
6. **Unpause the alerts.** Both rules in
   `deploy/grafana/provisioning/alerting/widget-probe-rules.yaml` ship with
   `isPaused: true`. Once the first `ok` verdict is in VictoriaLogs, set both to
   `isPaused: false` in a PR and check after the deploy that Grafana shows them
   as Normal, not Paused. Until then nothing pages.
