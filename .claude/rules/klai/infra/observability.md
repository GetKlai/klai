# Observability & Debugging

## Log pipeline
All services → stdout (JSON via structlog) → Alloy (Docker socket) → VictoriaLogs (30d) → Grafana.
Caddy also outputs JSON to stdout since SPEC-INFRA-004.

## Cross-service trace correlation
Caddy generates `X-Request-ID` per request via `request_header`. Portal-api reads it
(or generates UUID fallback) and propagates to downstream services via `get_trace_headers()`
from `app.trace`. Downstream services bind it via `RequestContextMiddleware`.

Chain: Caddy → portal-api → knowledge-ingest / retrieval-api / connector / scribe / mailer / research-api.

One `request_id:<uuid>` query in VictoriaLogs shows the full chain.

## Grafana MCP (preferred for production debugging)
Configured in `.mcp.json` as `grafana` server (read-only). Use instead of `docker logs`.

Common LogsQL queries:
- Trace a request: `request_id:<uuid>`
- Service errors: `service:portal-api AND level:error`
- Tenant logs: `org_id:<org_id> AND level:error`
- Caddy 5xx: `service:caddy AND status:5*`
- Time-scoped: add `_time:[2026-04-04T10:00, 2026-04-04T11:00)`

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
Query via Grafana PostgreSQL datasource or direct SQL on core-01.

| Event | Service | Emitted from |
|---|---|---|
| `signup`, `login` | portal-api | auth/signup endpoints |
| `billing.*` | portal-api | billing endpoints |
| `meeting.*` | portal-api | meetings endpoints |
| `knowledge.uploaded` | portal-api | connectors endpoint |
| `notebook.created`, `notebook.opened` | research-api | notebooks endpoint (SQLAlchemy) |
| `source.added` | research-api | sources endpoint (SQLAlchemy) |
| `knowledge.queried` | retrieval-api | retrieve endpoint (asyncpg pool) |

Useful queries:
- Feature adoption: `SELECT event_type, COUNT(*) FROM product_events GROUP BY 1`
- Tenant activity: `SELECT * FROM product_events WHERE org_id = <id> ORDER BY created_at DESC`

## When to use what
| Scenario | Tool |
|---|---|
| Production error investigation | Grafana MCP → VictoriaLogs |
| Cross-service request trace | Grafana MCP with `request_id:<uuid>` |
| Feature usage / business metrics | `product_events` table via Grafana PostgreSQL |
| Container startup issues | `docker logs --tail 30 <container>` |
| Real-time log tailing (dev) | `docker logs -f <container>` |
| HTTP-level debugging | Caddy JSON logs via `service:caddy` in VictoriaLogs |
