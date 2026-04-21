---
paths:
  - "klai-portal/backend/**"
  - "deploy/docker-compose.yml"
  - ".moai/specs/SPEC-*/plan.md"
---
# docker-socket-proxy

SEC-021 routes all portal-api and runtime-api Docker API traffic through
`tecnativa/docker-socket-proxy` instead of binding `/var/run/docker.sock`
directly. The proxy restricts which Docker API endpoints are reachable.

## Allowed verbs (current prod config)

`deploy/docker-compose.yml` sets:

```yaml
docker-socket-proxy:
  environment:
    CONTAINERS: 1   # GET/POST/DELETE /containers/*
    NETWORKS: 1     # GET/POST/DELETE /networks/*
    POST: 1         # enable POST/DELETE verbs on the above
    DELETE: 1
```

## Allowed verbs (per-verb rationale, SPEC-SEC-024)

Each `=1` below has at least one concrete production code path in
`klai-portal/backend/app/`. Any verb **not** listed here is either
"keep not-set" (explicitly never enabled) or outright forbidden.
Source of truth: SPEC-SEC-024 plan.md Tabel C (exhaustive audit).

| Verb | Status | Production callsite | Docker endpoint |
|---|---|---|---|
| `CONTAINERS` | **keep** | `infrastructure.py:68,135,207,222` `client.containers.get(name)` | `GET /containers/{id}/json` |
| `CONTAINERS + DELETE` | **keep** | `infrastructure.py:69,223` `c.remove(force=True)` | `DELETE /containers/{id}?force=true` |
| `CONTAINERS + POST` | **keep** | `infrastructure.py:136,208` `container.restart(timeout=10)` | `POST /containers/{id}/restart` |
| `CONTAINERS + POST` | **keep** | `infrastructure.py:236` `client.containers.run(image=..., ...)` | `POST /containers/create` + `POST /containers/{id}/start` |
| `NETWORKS` | **keep** | `infrastructure.py:254` `client.networks.get(net_name)` | `GET /networks/{id}` |
| `NETWORKS + POST` | **keep** | `infrastructure.py:255` `net.connect(container_name)` | `POST /networks/{id}/connect` |
| `EXEC` | **keep not-set** | — (audit: 0 calls in `app/`) | `POST /exec/*/start`, `POST /containers/{id}/exec` — both 403 |
| `IMAGES` | **keep not-set** | — (audit: 0 calls in `app/`; images pinned at deploy time) | `GET/POST /images/*` |
| `VOLUMES` | **keep not-set** | — | `GET/POST/DELETE /volumes/*` |
| `BUILD` | **keep not-set** | — | `POST /build` |
| `SYSTEM` | **keep not-set** | — | `GET /info`, `/version` |
| `PLUGINS` | **keep not-set** | — | `GET/POST /plugins/*` |

**Runtime-api** is a black-box vendored image (`vexaai/runtime-api:0.10.0-...`).
Its Docker-API usage is not audited. If a runtime-api call-path needs a verb
we dropped, the **permanent Grafana alert "Security — Proxy Denials"**
(SPEC-SEC-024-R12) fires on the first 403. Fix-forward: add the specific verb
with a single compose-commit — never preventively.

**Mechanical guard against reintroduction**: `rules/no-exec-run.yml` runs on
every portal-api PR via `ast-grep/action` in `.github/workflows/portal-api.yml`
(SPEC-SEC-024-R7). A new `$OBJ.exec_run($$$)` in `klai-portal/backend/app/`
fails CI with exit-code ≠ 0. Regression-guard tests under
`klai-portal/backend/tests/` are allow-listed.

## /exec/*/start is blocked by design (CRIT)

The proxy does NOT set `EXEC=1`. Any call to `container.exec_run([...])`
from portal-api or runtime-api goes through
`POST /containers/{id}/exec` → `POST /exec/{exec_id}/start`. The second
call returns **403 Forbidden** ("Request forbidden by administrative rules").

**Symptom:**
```
docker.errors.APIError: 403 Client Error for
http://docker-socket-proxy:2375/v1.53/exec/.../start: Forbidden
```

**Why we do not just flip EXEC=1:** that would grant the ability to run
arbitrary shell inside any container on the host — effectively full Docker
access. SEC-021 explicitly reduced that blast radius.

## Rule: talk the service's native protocol, never docker exec

When portal-api needs to do something inside a sidecar container, DO NOT
reach for `container.exec_run([...])`. Portal-api lives on `klai-net`
together with every service it needs to talk to. Pick the protocol client:

| Target | Don't | Do |
|---|---|---|
| Redis cache clear | `redis_ctr.exec_run(["redis-cli", "FLUSHALL"])` | `redis.asyncio.Redis(host=settings.redis_host).flushall()` |
| MongoDB user mgmt | `mongo_ctr.exec_run(["mongosh", "--eval", ...])` | `motor.AsyncIOMotorClient(settings.mongo_root_uri).admin.command(...)` |
| Postgres DDL | `pg_ctr.exec_run(["psql", "-c", ...])` | `asyncpg.connect(...)` / SQLAlchemy engine |
| File ops inside a container | `container.exec_run(["rm", ...])` | Mount the volume into portal-api, or expose a small HTTP endpoint on the target service |

## Still allowed via the proxy

The following remain available under `CONTAINERS=1 + POST=1` and are fine:

- `docker.from_env().containers.get(name).restart(timeout=10)` — `/containers/{id}/restart`
- `docker.from_env().containers.get(name).remove(force=True)` — `/containers/{id}` DELETE
- `client.containers.list()` / `.get()` — `/containers/json`, `/containers/{id}/json`
- `client.networks.*` — network lifecycle

## When writing a new provisioning step

Before adding `exec_run([...])` to portal-api code, check: is the target
container reachable on `klai-net`? If yes, use the protocol client. If
no (private sidecar, file-only access), add the target to `klai-net`
and expose a minimal protocol — never route the operation through
docker exec.

**Ref:** SEC-021 post-incident review — `internal.py` (regenerate endpoint)
and `infrastructure.py` (`_flush_redis_and_restart_librechat`,
`_create_mongodb_tenant_user`, `_sync_drop_mongodb_tenant_user`) all hit
this once. Keep the pattern out of new code.
