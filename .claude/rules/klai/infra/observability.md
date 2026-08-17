# Observability & Debugging

## Log pipeline
All services → stdout (JSON via structlog) → Alloy (Docker socket) → VictoriaLogs (30d).
Caddy also outputs JSON to stdout since SPEC-INFRA-004.

## Cross-service trace correlation
Caddy generates `X-Request-ID` per request via `request_header`. Portal-api reads it
(or generates UUID fallback) and propagates to downstream services via `get_trace_headers()`
from `app.trace`. Downstream services bind it via `RequestContextMiddleware`.

Chain: Caddy → portal-api → knowledge-ingest / retrieval-api / connector / scribe / mailer / research-api.

One `request_id:<uuid>` query in VictoriaLogs shows the full chain.

## VictoriaLogs MCP (preferred for production debugging)
Configured in `.mcp.json` as `victorialogs` server (read-only, v1.8.0).
The launcher opens a per-MCP-process SSH tunnel to the production server
(VictoriaLogs is only on Docker's internal `monitoring` network), so parallel
Conductor/Claude sessions do not share a single `localhost:9428` tunnel.
Auth credentials are documented in `klai-infra/docs/rules/observability.md` (private).

Uses LogsQL — NOT LogQL (Loki). Key tools: `query`, `hits`, `field_names`, `facets`, `streams`.

### Authentication
VictoriaLogs requires basic auth. Credentials are stored in SOPS (klai-infra).

| Consumer | Auth method |
|---|---|
| core-01 Alloy (internal) | `basic_auth` in `loki.write` endpoint — `deploy/alloy/config.alloy` |
| public-01 Alloy (external) | Bearer token via Caddy, Caddy passes basic auth upstream |
| MCP (local Mac) | `.claude/scripts/victorialogs-launcher.mjs` reads `VICTORIALOGS_BASIC_AUTH_B64` and sets `VL_INSTANCE_HEADERS` |

Common LogsQL queries:
- Trace a request: `request_id:<uuid>`
- Service errors: `service:portal-api AND level:error`
- Tenant logs: `org_id:<org_id> AND level:error`
- Caddy 5xx: `service:caddy AND status:5*`
- Time-scoped: add `_time:[2026-04-08T10:00, 2026-04-08T11:00)`

## Grafana MCP (dashboards, metrics, alerts)
Configured in `.mcp.json` as `grafana` server (read-only).
**Cannot query VictoriaLogs** — the `query_loki_logs` tool speaks Loki protocol,
not the VictoriaLogs API. Use the `victorialogs` MCP for log queries instead.

Use Grafana MCP for: dashboard search, Prometheus/VictoriaMetrics queries,
PostgreSQL queries (product_events), and alert inspection.

The launcher `.claude/scripts/grafana-launcher.mjs` maps the shell's
`GRAFANA_SERVICE_ACCOUNT_TOKEN` to the `GRAFANA_API_KEY` variable expected by
`mcp-grafana`. Do not put tokens directly in `.mcp.json`.

## MCP smoke test
Before production debugging, validate the actual MCP stdio launchers:

```bash
node .claude/scripts/observability-mcp-smoke.mjs
```

If you already have a local VictoriaLogs tunnel on `localhost:9428`, avoid
opening a managed SSH tunnel during the smoke test:

```bash
OBS_MCP_SMOKE_LOCAL_VICTORIALOGS=1 node .claude/scripts/observability-mcp-smoke.mjs
```

If this fails with 401, fix the launcher/env first. Do not fall back to
Grafana Loki tools for VictoriaLogs, and do not debug production behavior from
code alone when the relevant runtime data should be available.

## Key log fields
| Field | Set by | Available in |
|---|---|---|
| `request_id` | Caddy / middleware | All services |
| `org_id` | Auth middleware / X-Org-ID header | All services |
| `user_id` | Auth middleware | portal-api only |
| `service` | `setup_logging()` | All services |
| `level` | structlog | All services |

## Docker log rotation
`/etc/docker/daemon.json`: `max-size: 50m`, `max-file: 3`.
Alloy captures real-time — rotation only affects local Docker cache.

## Product events (SPEC-GRAFANA-METRICS)
All user-facing actions emit to the `product_events` table in the `klai` database.
Query via Grafana PostgreSQL datasource or direct SQL on the production server.

| Event | Service | Emitted from |
|---|---|---|
| `signup`, `login` | portal-api | auth/signup endpoints |
| `billing.*` | portal-api | billing endpoints |
| `meeting.*` | portal-api | meetings endpoints |
| `knowledge.uploaded` | portal-api | connectors endpoint |
| `connector.connected` | portal-api | OAuth callback — first-time provider connection |
| `connector.reconnected` | portal-api | OAuth callback — recovery from `auth_error` |
| `connector.reconnect_failed` | portal-api | OAuth callback — reconnect attempt failed (`reason=consent_denied` or `reason=token_exchange_failed`; only emitted when the connector was already in `auth_error`) |
| `notebook.created`, `notebook.opened` | research-api | notebooks endpoint (SQLAlchemy) |
| `source.added` | research-api | sources endpoint (SQLAlchemy) |
| `knowledge.queried` | retrieval-api | retrieve endpoint (asyncpg pool) |

Useful queries:
- Feature adoption: `SELECT event_type, COUNT(*) FROM product_events GROUP BY 1`
- Tenant activity: `SELECT * FROM product_events WHERE org_id = <id> ORDER BY created_at DESC`
- Reconnect-funnel health: `SELECT properties->>'reason' AS reason, COUNT(*) FROM product_events WHERE event_type = 'connector.reconnect_failed' GROUP BY 1`

## A provisioned alert must be proven able to read its data (HIGH)

An alert that cannot read its datasource is worse than no alert: it reports
healthy forever. On 2026-08-14, four of six Postgres-backed rules were blind.
Nothing in Grafana showed it, because a query error under `execErrState: OK`
looks exactly like "nothing wrong".

Two distinct failure modes, and checking only the first is how the second hid
for months:

| Mode | Cause | Looks like |
|---|---|---|
| Permission | no `SELECT` grant, or `USAGE` on the schema missing | query error, swallowed by `execErrState: OK` |
| RLS blackhole | grant exists, but the table's SELECT policy is scoped to another role or to a tenant GUC Grafana never sets | success, zero rows, forever |

`deploy/scripts/verify-alert-datasource-access.py` checks both — `EXPLAIN` as
`grafana_reader` for the first, and a superuser-vs-reader row-count comparison
per relation for the second. It runs on core-01 from `deploy-compose.yml` after
the provisioning sync, and fails the deploy on any blind rule that is not
listed in the script's `KNOWN_BLIND` allowlist with a reason. The parser has its
own self-test in CI, and a stale allowlist entry fails it.

When wiring a new Postgres-backed alert:

- `grafana_reader` needs `GRANT USAGE ON SCHEMA <s>` **and** `GRANT SELECT ON
  <s>.<table>` — one without the other still denies.
- `ALTER DEFAULT PRIVILEGES` in `deploy/grafana/sql/grafana-reader-setup.sql`
  only covers tables created by the role that ran it. Alembic creates tables as
  `portal_api`, so anything newer is invisible until granted explicitly.
- For an RLS table, prefer a superuser-owned view exposing only the columns the
  rule needs (see `portal_feedback_correlation_stats`). A view that is not
  `security_invoker` evaluates the base table's RLS as its owner, so the rule
  can read the aggregate without widening the base-table grant — which matters
  when the table also holds user-written text.
- Set `execErrState: Error`, not `OK`, on any rule whose purpose is catching
  silence. Otherwise the rule can fail silently itself.

## When to use what
| Scenario | Tool |
|---|---|
| Production error investigation | `victorialogs` MCP → LogsQL query |
| Cross-service request trace | `victorialogs` MCP with `request_id:<uuid>` |
| Feature usage / business metrics | `grafana` MCP → PostgreSQL (product_events) |
| Dashboards / metrics | `grafana` MCP → Prometheus queries |
| Container startup issues | `docker logs --tail 30 <container>` |
| Real-time log tailing (dev) | `docker logs -f <container>` |
| HTTP-level debugging | `victorialogs` MCP with `service:caddy` |
