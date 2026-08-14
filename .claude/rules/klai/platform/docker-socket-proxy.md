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

## Two layers, and what each one can see (CRIT)

There are now TWO controls in front of the daemon, and confusing them is how the
gap below stayed open for months:

```
portal-api ──────────────► klai-docker-authz :2375 ┐
                                                    ├─► docker-socket-proxy ─► /var/run/docker.sock
runtime-api-socket-proxy ► klai-docker-authz :2376 ┘        (method + path)
                                    (request BODY)
```

| Layer | Authorises on | Cannot see |
|---|---|---|
| `docker-socket-proxy` | method + path (`CONTAINERS`, `NETWORKS`, `POST`, `DELETE`) | anything inside the request body |
| `klai-docker-authz` | the `HostConfig` inside `POST /containers/create` | nothing else — every other endpoint passes straight through |

**Why the second layer exists.** `POST /containers/create` is on the path
whitelist and its body was never parsed. `HostConfig` lives in that body, and
`HostConfig` is where container isolation is decided. Proven on 2026-08-14
against `tecnativa/docker-socket-proxy:v0.5.0` with production's exact env: a
create carrying `Binds: ["/:/host"]`, `Privileged: true`, `PidMode: "host"`
returned **201**, started **204**, and the container read the host's
`/etc/hostname`. Endpoint whitelisting does not constrain `HostConfig` — that is
documented Docker behaviour, not a proxy bug. SPEC-SEC-DOCKER-AUTHZ-001.

### Per-principal allowlist

Identity comes from the listener port, not from a peer address — the Vexa runtime
arrives through a socat bridge and would otherwise present the sidecar's address.

| Port | Principal | Binds | NetworkMode |
|---|---|---|---|
| 2375 | `portal-api` | only under `/opt/klai/librechat` | any (provisioning attaches networks separately) |
| 2376 | `vexa12-runtime` | **none at all** | `vexa12-bots` only |

Forbidden for BOTH, regardless of port: `Privileged`, `CapAdd`, `Devices`,
`DeviceCgroupRules`, `CgroupParent`, `PidMode`, `IpcMode`, `UsernsMode`,
`SecurityOpt`, `Sysctls`, `Runtime`, and `NetworkMode: host|none`.

The two allowlists are not guesses. Every bind in
`_start_librechat_container` sits under that one prefix, and the Vexa runtime's
three bind sources all belong to Vexa's agent feature, which Klai does not deploy
(`AGENT_IMAGE` empty, `HOST_CLAUDE_CREDENTIALS` / `VEXA_AGENT_SRC_MOUNT` unset).

### Extending it

Widening a policy is a security-review event, not a routing tweak.

1. Change `klai-docker-authz/app/policy.py` — the `Policy` for that principal.
2. Add a test in `klai-docker-authz/tests/test_policy.py` proving the NEW shape
   passes AND the escalation shape still fails. Both directions, or it is not a
   test.
3. Say in the diff WHY the caller's shape changed. "To unblock the deploy" is not
   a reason; it is the symptom that something upstream changed unnoticed.

Note on falsy values: `Privileged: false` and `CapAdd: null` are what docker-py
sends on every create and are explicitly ALLOWED. A policy keyed on key-presence
rather than on a requested value passes every hostile-input test and then refuses
all tenant provisioning.

### Who may reach the daemon at all

`scripts/audit-docker-access.sh` pins two sets in CI (via `audit-compose.yml`):
the `socket-proxy` network members, and the containers mounting the raw
`/var/run/docker.sock`. The MUST-NOT list further down this file enumerates the
*forbidden*, so a new service is permitted by default; the audit inverts that.

`alloy` mounts the raw socket and therefore bypasses `klai-docker-authz`
entirely. Its mount is `RW=false`, which protects the socket FILE but does not
make the Docker API read-only — a process that can write the byte stream can
still `POST /containers/create`. It needs `GET` only. Recorded as an open item in
SPEC-SEC-DOCKER-AUTHZ-001; do not treat the read-only flag as containment.

## Containers that MUST NOT join the socket-proxy network (SPEC-SEC-SSRF-001 REQ-5)

Any container that accepts a user-supplied URL and fetches it is one
compose edit away from an env-dump primitive if it can reach
`docker-socket-proxy:2375`. The Cornelis A1 chain depends entirely on
those containers staying off the `socket-proxy` network.

The following containers MUST NOT be added to the `socket-proxy`
network in `deploy/docker-compose.yml`:

| Container | Why (verified call-site on 2026-04-24) |
|---|---|
| `knowledge-ingest` | `routes/crawl.py::preview_crawl` + `crawl_url` accept a user URL and forward it to crawl4ai |
| `crawl4ai` | Browser context fetches every URL submitted by knowledge-ingest and connector |
| `klai-connector` | `SyncEngine._upload_images` → `klai_image_storage.pipeline._download_validate_upload` fetches adapter-extracted image URLs (Notion / Confluence / GitHub / Airtable) |
| `klai-focus` / `research-api` | `app/services/docling.py::convert_url` forwards a user-supplied URL to docling-serve for content extraction |
| `klai-knowledge-mcp` | Delegates search + ingest queries to retrieval-api + knowledge-ingest — same URL surface by transitive reach |
| `retrieval-api` | Enrichment pipeline fetches external URLs during reranking / summary |
| `scribe` / `scribe-api` | Accepts meeting audio URLs (`/transcribe` endpoint) and calls providers on the user's behalf |

**Not in the list, despite surface-level suspicion:**

- `klai-mailer` — only outbound call is a hardcoded `portal_api_url`
  language lookup; the template renderer (`app/renderer.py`) does not
  fetch remote resources. Safe to revisit if a future feature adds
  user-URL rendering.

Verification: every PR touching `deploy/docker-compose.yml` must pass
`./scripts/smoke-ssrf-isolation.sh` post-deploy — runs the AC-13 /
AC-22 curl check from each container above against
`docker-socket-proxy:2375` and asserts `connect timeout`.

## Vexa runtime-api speaks Unix socket only (HIGH)

The Vexa `runtime-api` image (`vexaai/runtime-api:0.10.0-*`) hardcodes
`requests_unixsocket` in `runtime_api/backends/docker.py`. It ignores
`DOCKER_HOST=tcp://...` and builds `http+unix://<encoded path>` URLs
unconditionally. As of Vexa `main` on 2026-04-22 this has not changed
across v0.10 → v0.10.3. There are no open upstream issues or PRs.

**Why it matters:** The portal-api docker-socket-proxy pattern (pure TCP)
cannot be copied directly for runtime-api. Pointing `DOCKER_HOST` at the
proxy makes startup fail with `FileNotFoundError: [Errno 2] No such file
or directory` the moment `_get_session()` runs.

**Klai solution:** A `alpine/socat` sidecar (`runtime-api-socket-proxy`)
listens on a Unix socket in a named volume (`runtime-api-docker-socket`)
and forwards every byte to `docker-socket-proxy:2375`. Runtime-api mounts
the named volume at `/var/run/` so it sees what it believes is a local
socket. The whitelist enforcement still happens in docker-socket-proxy —
runtime-api gets CONTAINERS/NETWORKS/POST/DELETE only, and forbidden
verbs (EXEC, IMAGES, VOLUMES, SYSTEM) return 403 at the proxy.

**Verification (run after any compose change touching runtime-api):**
```bash
# Must 403:
docker exec klai-core-runtime-api-1 python -c \
  "import requests_unixsocket as r; \
   print(r.Session().post('http+unix://%2Fvar%2Frun%2Fdocker.sock/v1.53/exec/x/start').status_code)"
# Must 200:
docker exec klai-core-runtime-api-1 python -c \
  "import requests_unixsocket as r; \
   print(r.Session().get('http+unix://%2Fvar%2Frun%2Fdocker.sock/v1.53/containers/json').status_code)"
```

**Prevention:** Do not point runtime-api at `tcp://docker-socket-proxy:2375`.
Always route through the socat sidecar. If Vexa upstream adds TCP support
in a future release (watch `services/runtime-api/runtime_api/backends/docker.py`),
the sidecar can be removed and `DOCKER_HOST` env var re-introduced.

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
