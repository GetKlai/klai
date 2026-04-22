---
id: SPEC-SEC-021
version: 0.3.0
status: implemented
created: 2026-04-19
updated: 2026-04-22
author: Mark Vletter
priority: high
---

# SPEC-SEC-021: runtime-api achter docker-socket-proxy

## HISTORY

### v0.3.0 (2026-04-22)
- **Implemented in dev.** Runtime-api no longer mounts `/var/run/docker.sock`
  directly. All Docker API calls flow through a new socat sidecar
  (`runtime-api-socket-proxy`, `alpine/socat:1.7.3.4-r1`) into the existing
  `docker-socket-proxy:2375` whitelist.
- **Discovered constraint (blocker for original v0.2 approach):** Vexa
  `runtime-api` hardcodes `requests_unixsocket` — it cannot speak TCP to
  `docker-socket-proxy:2375` at all. `DOCKER_HOST=tcp://...` causes startup
  crash with `FileNotFoundError`. Verified by reading `main` branch source
  at 2026-04-22 + checking v0.10.1/0.10.2/0.10.3 changelogs + zero open
  upstream issues. No upstream fix on the horizon.
- **Architecture** (see `.claude/rules/klai/platform/docker-socket-proxy.md`
  "Vexa runtime-api speaks Unix socket only" section for details):
  - New named volume `runtime-api-docker-socket` shared between
    `runtime-api-socket-proxy` (socat) and `runtime-api` (Vexa).
  - socat listens on `UNIX-LISTEN:/var/run/docker.sock,mode=0660,user=999,group=999`
    (runtime-api's uid) and forwards to `TCP:docker-socket-proxy:2375`.
  - runtime-api mounts the named volume at `/var/run/` — sees a local
    Unix socket, never the real host daemon socket.
  - runtime-api removed from the `socket-proxy` network (only the socat
    sidecar is there now).
- **Verification (dev, 2026-04-22):**
  - `runtime-api` startup log: `Docker connected (API 1.53)` ✅
  - Health: `healthy` ✅
  - Binds: only `./vexa/profiles.yaml:ro` + named volume — no host socket ✅
  - Negative: `POST /exec/{id}/start` → 403 ✅
  - Negative: `GET /images/json` → 403 ✅
  - Positive: `GET /containers/json` → 200 (46 containers) ✅
- **Remaining:** production rollout (currently dev-only), end-to-end bot
  spawn test against a live meeting (blocked on user scheduling a test
  meeting), `runtime-api` Dockerfile non-root audit (moved out of scope —
  tracked separately under SEC-018 follow-up).

### v0.2.0 (2026-04-19)
- Verified the foundational claim: portal-api already uses `tecnativa/docker-socket-proxy:v0.4.2` at `docker-compose.yml:281-292` with whitelist `CONTAINERS=1 NETWORKS=1 POST=1 DELETE=1` on the `socket-proxy` network (`internal: true`). Portal-api consumes it via `DOCKER_HOST: tcp://docker-socket-proxy:2375` at line 316.
- runtime-api at line 890-895 still uses direct mount: `/var/run/docker.sock:/var/run/docker.sock` + `group_add: ["988"]`. This is the target to migrate.
- Only two other services mount the host socket: `docker-socket-proxy` (:ro, expected), `alloy` (:ro, for container metadata scraping — acceptable as read-only).
- Approach simplifies: no NEW proxy service — attach runtime-api to the existing `socket-proxy` network.

### v0.1.0 (2026-04-19)
- Initial draft. Based on audit finding V-002 / F-031 in `.moai/audit/08-vexa.md`.
- Pattern copied from portal-api which already uses `tcp://docker-socket-proxy:2375` with endpoint whitelist (CONTAINERS, NETWORKS, POST, DELETE).

---

## Goal

Remove the direct `/var/run/docker.sock` bind-mount from the Vexa `runtime-api` container. Route its Docker API calls through an already-running `docker-socket-proxy` instance with a whitelist that only exposes the endpoints runtime-api actually needs (container create/start/stop, optionally network attach). A compromise of runtime-api must not grant full Docker daemon access (= root-equivalent on core-01) anymore.

---

## Why now

- runtime-api is the component that spawns Chromium bot containers on-demand. It is called by `meeting-api` over the internal `vexa-bots` network. Not publicly exposed.
- Current mount: `/var/run/docker.sock:/var/run/docker.sock` with `group_add: [988]` (docker group). Root-equivalent: an RCE inside runtime-api lets the attacker run `docker run --privileged --mount type=bind,source=/,target=/mnt ...` and own the host.
- portal-api already uses the docker-socket-proxy pattern successfully — blueprint is proven in this codebase.

---

## Success Criteria

1. `deploy/docker-compose.yml` `runtime-api` block:
   - NO `/var/run/docker.sock` volume mount
   - NO `group_add: [988]`
   - Adds env `DOCKER_HOST=tcp://docker-socket-proxy:2375`
   - Network membership includes the network the socket-proxy listens on
2. `docker-socket-proxy` config whitelists ONLY the endpoints runtime-api needs to spawn, start, stop, and inspect containers on the `vexa-bots` network. No broader grants (no `VOLUMES=1`, no `IMAGES=1` beyond pull-list if needed, no `EXEC=1`, no `PLUGINS=1`, no `SYSTEM=1`).
3. Bot lifecycle end-to-end still works:
   - Scheduled meeting triggers bot spawn (POST /v1/meetings/run)
   - Bot container appears, joins the `vexa-bots` network, reaches Vexa transcription-api on gpu-01
   - Bot exits cleanly after the call; runtime-api can stop/remove it
4. Smoke-test: removing docker-socket-proxy access temporarily causes runtime-api calls to fail cleanly with "docker daemon unreachable" — proves the new path is the only path.
5. Rollback documented: if bot spawn fails post-deploy, revert to `/var/run/docker.sock` mount in one commit.

---

## EARS Requirements

**REQ-1** — WHILE `runtime-api` is running, the system SHALL NOT grant it access to `/var/run/docker.sock` via bind-mount.

**REQ-2** — WHEN runtime-api needs to create a bot container, the system SHALL route the Docker API call through `docker-socket-proxy:2375`.

**REQ-3** — WHILE `docker-socket-proxy` is configured for runtime-api, it SHALL allow only the minimal set of endpoints: `CONTAINERS=1`, `POST=1`, `DELETE=1`, `NETWORKS=1`. All other flags SHALL default to `0`.

**REQ-4** — WHEN a bot container is requested by meeting-api, runtime-api SHALL complete the spawn within 15 seconds (current baseline) — proxy latency must not regress bot-start time meaningfully.

**REQ-5** — IF `docker-socket-proxy` is unavailable, runtime-api SHALL return HTTP 503 to meeting-api with a log line identifying the failure — no silent retry against a non-existent fallback.

**REQ-6** — WHILE the new configuration is deployed, a disaster-recovery path SHALL exist in `klai-infra/SERVERS.md`: single-commit revert of the compose block restores direct-socket access.

---

## Out of scope

- Running runtime-api as non-root user (separate Dockerfile change, trackable under SEC-018 follow-up).
- Auditing runtime-api's outbound HTTP surface (V-005 `ALLOW_PRIVATE_CALLBACKS=1` → SPEC-SEC-022 territory).
- Upgrading Vexa runtime-api image version (stays at `0.10.0-260419-1129` for this SPEC).

---

## Approach (simplified after claim verification)

The existing `docker-socket-proxy` service in `deploy/docker-compose.yml:281-292` already has the exact whitelist runtime-api needs (`CONTAINERS=1 NETWORKS=1 POST=1 DELETE=1`). No new service required.

1. Attach `runtime-api` to the existing `socket-proxy` network (currently `internal: true` — must stay internal; both portal-api and runtime-api are permitted consumers).
2. In `runtime-api` service block (`deploy/docker-compose.yml:890-895`):
   - Add `DOCKER_HOST: tcp://docker-socket-proxy:2375` to `environment:`
   - Remove `group_add: ["988"]`
   - Remove the `/var/run/docker.sock:/var/run/docker.sock` volume mount
   - Add `- socket-proxy` to `networks:`
3. Deploy via CI compose-sync. Watch runtime-api logs for docker API errors during the first bot spawn.
4. Document revert in `klai-infra/SERVERS.md` under "Vexa bot lifecycle".

No extra proxy instances — portal-api and runtime-api share the same proxy. If per-service whitelisting ever becomes needed (e.g., runtime-api needs IMAGES=1 for container create from custom image), spin up a second proxy instance then.

---

## Risks

- **Bot spawn latency**: every Docker API call now hops through the proxy. Expected <5 ms extra. Measurable via meeting-api → bot-ready time in product_events.
- **Docker API version mismatch**: docker-socket-proxy pins a Docker API version. If runtime-api's internal Docker client expects a newer version, some calls may fail. Mitigation: pin proxy image to match daemon API version, test in staging.
- **Network-attach during spawn**: bots must attach to `vexa-bots` network after creation. If `NETWORKS=1` is not set, this call 403s. Included in whitelist.

---

## Acceptance tests

1. `docker inspect klai-core-runtime-api-1 --format '{{range .HostConfig.Binds}}{{.}}{{"\n"}}{{end}}'` → does NOT contain `/var/run/docker.sock`
2. `docker exec klai-core-runtime-api-1 env | grep DOCKER_HOST` → `DOCKER_HOST=tcp://docker-socket-proxy:2375`
3. Bot-spawn smoke-test: trigger a scheduled meeting, verify bot container appears, transcription works end-to-end, bot exits cleanly
4. Denial test: temporarily stop `docker-socket-proxy`. Trigger a meeting spawn. Verify runtime-api returns 503 within 5s. Restart proxy → recovery.
5. No regression: `product_events` query for `meeting.bot_joined` events in the 24 hours post-deploy shows comparable count/latency vs pre-deploy baseline.
