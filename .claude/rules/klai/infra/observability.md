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
Requires SSH tunnel to the production server (VictoriaLogs is only on Docker's internal `monitoring` network).
Tunnel setup and auth credentials are documented in `klai-infra/docs/rules/observability.md` (private).

Uses LogsQL — NOT LogQL (Loki). Key tools: `query`, `hits`, `field_names`, `facets`, `streams`.

### Authentication
VictoriaLogs requires basic auth. Credentials are stored in SOPS (klai-infra).

| Consumer | Auth method |
|---|---|
| core-01 Alloy (internal) | `basic_auth` in `loki.write` endpoint — `deploy/alloy/config.alloy` |
| public-01 Alloy (external) | Bearer token via Caddy, Caddy passes basic auth upstream |
| MCP (local Mac) | `VL_INSTANCE_HEADERS` env var with Basic auth in `.mcp.json` |

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
