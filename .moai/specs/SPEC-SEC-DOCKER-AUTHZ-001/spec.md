---
id: SPEC-SEC-DOCKER-AUTHZ-001
version: "0.1.0"
status: draft
created: "2026-08-14"
updated: "2026-08-14"
author: MoAI
priority: high
issue_number: 0
---

## HISTORY

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 0.1.0 | 2026-08-14 | MoAI | Initial draft. Raised by the SPEC-VEXA-004 adversarial review (finding 3), which observed that `docker-socket-proxy`'s endpoint whitelist does not constrain `HostConfig`, so `POST /containers/create` remains a host-root primitive for any principal allowed to call it. Pre-existing since SPEC-SEC-021; not introduced by the Vexa migration. |

# SPEC-SEC-DOCKER-AUTHZ-001: Constrain container-create, not just the endpoint

## Overview

Two Klai services can create containers on core-01's Docker daemon:

- **portal-api** — provisions a per-tenant LibreChat container on signup
  (`app/services/provisioning/infrastructure.py`).
- **vexa12-runtime** — spawns an ephemeral meeting-bot container per meeting
  (Vexa 0.12 `runtime_kernel/docker_backend.py`).

Neither touches the host socket directly. Both go through
`tecnativa/docker-socket-proxy:v0.5.0`, configured `CONTAINERS=1 NETWORKS=1
POST=1 DELETE=1` — with `EXEC`, `IMAGES`, `VOLUMES`, `BUILD` and `SYSTEM`
deliberately unset. That was SPEC-SEC-021's deliberate reduction and it works as
designed: `POST /exec/{id}/start` and `GET /images/json` return 403, verified
live.

The gap is what the whitelist cannot see. `docker-socket-proxy` authorises on
**method + path**. `POST /containers/create` is on the allow-list, and its
request **body** is never parsed. That body carries `HostConfig`, which is where
container isolation is decided:

```json
{ "HostConfig": { "Binds": ["/:/host"], "Privileged": true,
                  "PidMode": "host", "CapAdd": ["SYS_ADMIN"] } }
```

A principal that may call `POST /containers/create` can therefore mount the host
root filesystem read-write into a container it controls. That is equivalent to
root on core-01, and it is Docker's documented behaviour rather than a bug —
Docker's own daemon attack-surface guidance states that API-driven container
provisioning must validate parameters for exactly this reason.

**This is not new and it is not Vexa-specific.** It has been true since
SPEC-SEC-021 introduced the proxy, and portal-api has had the same capability
the whole time. The SPEC-VEXA-004 review surfaced it while auditing the new
stack; it applies equally to the older consumer.

### Severity, stated honestly

This is a **second-stage escalation, not an entry point**. To use it an attacker
must already have code execution inside `portal-api` or `vexa12-runtime`, or on
a network that can reach `docker-socket-proxy:2375`. It grants no new way in.

What it does is remove the ceiling: a contained compromise of one application
service becomes host root, and from there every tenant's data on the box. It
converts "one service is broken" into "the machine is gone". That is why it is
`high` and not `critical`, and also why it should not sit open indefinitely.

The review that raised it did **not** demonstrate the exploit against
production. The mechanism is inferred from Docker's documented semantics and
from reading the proxy's own authorisation model; that inference is strong, but
REQ-1 below exists to convert it into evidence before anything is designed
around it.

## Why this is tractable

The reason to write a SPEC rather than accept the risk: **both consumers'
legitimate needs are small, static, and enumerable.** This is not a case where
policy would have to permit arbitrary shapes.

### portal-api — per-tenant LibreChat container

From `infrastructure.py::_start_librechat_container`:

| Field | Value |
|---|---|
| `image` | pinned LibreChat image from settings |
| `name` | `librechat-<slug>` |
| `labels` | the three klasse-B hygiene labels (SPEC-INFRA-CONTAINER-HYGIENE-001) |
| `network` | `klai-net`, then `net-mongodb`, `net-meilisearch`, `net-redis` via `networks.connect` |
| `entrypoint` / `command` | fixed wrapper + `npm run backend` |
| `volumes` | **every bind is under `/opt/klai/librechat/`**: the tenant `.env` (ro), `librechat.yaml` (ro), `images/` (rw), the shared patch mounts (ro), `klai-entrypoint.sh` (ro) |
| `restart_policy` | `unless-stopped` |

No `Privileged`, no `CapAdd`, no `PidMode`, no `Devices`, and every bind source
sits under one host prefix.

### vexa12-runtime — ephemeral meeting bot

From `runtime_kernel/docker_backend.py::start`, the `HostConfig` it builds:

| Field | Set when |
|---|---|
| `NetworkMode` | always — `DOCKER_NETWORK` = `vexa12-bots` |
| `ShmSize` | always — Chromium stability |
| `Mounts` / `Binds` | only from `workspace_binds(env)`, `HOST_CLAUDE_CREDENTIALS`, `VEXA_AGENT_SRC_MOUNT` |

Those three bind sources belong to Vexa's **agent** feature, which Klai does not
deploy: `AGENT_IMAGE` and `AGENT_WORKER_IMAGE` are empty and
`HOST_CLAUDE_CREDENTIALS` / `VEXA_AGENT_SRC_MOUNT` are unset (verified on
core-01). **A Klai bot spawn therefore sends no binds at all today** — the code
path exists but is inert.

That asymmetry is the design opening: the bot runtime can be held to a
near-zero policy (no binds, no privilege, one fixed network) while portal-api
gets a bounded prefix allowlist. Neither needs a general-purpose rule engine.

## Requirements (EARS)

### Ubiquitous

**[REQ-U-001]** The system SHALL NOT permit any principal reaching the Docker
API through `docker-socket-proxy` to create a container whose `HostConfig`
requests host-root-equivalent capability — specifically `Privileged`, `PidMode:
host`, `IpcMode: host`, `UsernsMode: host`, `CapAdd`, `Devices`,
`CgroupParent`, or a bind whose source resolves outside an explicitly allowed
prefix.

**[REQ-U-002]** The policy SHALL be expressed per calling principal, not
globally: portal-api, vexa12-runtime and alloy have different legitimate needs
(create-with-binds, create-without-binds, and read-only respectively) and MUST
NOT inherit each other's allowance.

**[REQ-U-002a]** Every principal with daemon access SHALL reach it through a
policy-bearing path. A container mounting the raw socket bypasses every control
in this SPEC; `alloy` does that today and is in scope.

**[REQ-U-003]** A denied request SHALL fail loudly — a non-2xx to the caller and
a log line naming the principal, the endpoint, and the rejected field — never a
silent strip-and-proceed. A silently mutated create is harder to diagnose than a
refused one and hides a genuine attack.

**[REQ-U-004]** The policy layer SHALL NOT become a new single point of failure
for tenant provisioning without an explicit availability answer. Tenant signup
and meeting start both traverse it.

### Event-driven

**[REQ-E-001]** WHEN `POST /containers/create` arrives with a `HostConfig`
containing a field outside the calling principal's allowlist, THE system SHALL
reject it with 403 and emit a structured denial event.

**[REQ-E-002]** WHEN a bind source is supplied by portal-api, THE system SHALL
resolve it (including symlinks and `..`) and reject it unless the resolved path
is under `/opt/klai/librechat/`.

**[REQ-E-003]** WHEN vexa12-runtime supplies any `Binds` or `Mounts` entry, THE
system SHALL reject the create — Klai does not deploy Vexa's agent feature, so
no legitimate bot spawn carries one.

**[REQ-E-004]** WHEN a denial occurs, THE system SHALL raise a Grafana alert on
first occurrence. Baseline is zero; a denial is either an attack or a
legitimate need nobody wrote down, and both warrant a human.

### State-driven

**[REQ-S-001]** WHILE the policy layer is unavailable, container-create SHALL
fail closed. A meeting that cannot start is recoverable; an unpoliced create is
not.

## Design options

Three directions were considered. None is free; the trade-offs differ in kind.

### Option A — Docker authorization plugin (AuthZ API)

Docker's own extension point. The daemon calls a plugin with the full request
(method, path, body) before executing, and honours an allow/deny verdict.

- **For:** the designed mechanism; sees the body; no change to how callers speak
  to Docker; applies to every client of the daemon including any future one.
- **Against:** the plugin sits in the daemon's request path — a crashed or slow
  plugin degrades every Docker operation on core-01, not just ours. Requires a
  daemon restart to install (whole-host blip). Klai would own the plugin.

### Option B — policy-aware reverse proxy in front of the socket proxy

Replace or front `docker-socket-proxy` with something that parses the body.
Structurally identical to today's topology, one hop deeper.

- **For:** blast radius is confined to the two callers; no daemon restart, no
  daemon-wide dependency; deployable and revertable as a normal compose service;
  the allowlist above is small enough to express declaratively.
- **Against:** a new Klai-owned service on the tenant-provisioning path
  (REQ-U-004). Only constrains callers routed through it — a future service
  given the socket directly bypasses it silently, so it needs a companion guard
  asserting no container mounts `/var/run/docker.sock` (portal-api and
  vexa12-runtime already do not; `runtime-api-socket-proxy` bridges the proxy,
  not the host socket).

### Option C — separate, less-privileged daemon for spawned workloads

Run bot containers (and possibly tenant containers) on a rootless or otherwise
confined daemon, so compromise does not yield host root.

- **For:** removes the capability rather than filtering it — strictly the
  strongest.
- **Against:** by far the largest change. Tenant LibreChat containers need
  `klai-net`, `net-mongodb`, `net-meilisearch` and `net-redis`, all on the main
  daemon; cross-daemon networking is not a config tweak. Rootless Docker has its
  own constraints around bind mounts and networking that would need proving
  against LibreChat's actual requirements first.

### Recommendation

**Option B, with Option C kept as the direction for bot workloads specifically.**

Option B matches the shape of the problem: a small static allowlist, two known
principals, and a failure mode confined to the two callers rather than the whole
daemon. Option A's daemon-wide blast radius buys generality Klai does not need
with two consumers. Option C is right in principle but is a platform project;
the bot runtime — inert bind path, one network, ephemeral containers — is the
natural first candidate if it is ever taken on, and Option B does not block it.

This recommendation is **not** final: REQ-1's spike exists to test it against
reality before implementation, and a spike result that contradicts it should
change it.

## Interim mitigation (available before any of the above)

The exposure that makes this urgent is *reachability*, and that is already
narrower than it was:

- `docker-socket-proxy` sits on the `socket-proxy` network (`internal: true`).
- `.claude/rules/klai/platform/docker-socket-proxy.md` lists seven containers
  that MUST NOT join it — every service that fetches a user-supplied URL.
  `scripts/smoke-ssrf-isolation.sh` asserts this post-deploy.

Two cheap steps tighten it further without waiting for the design:

1. **A CI guard asserting the socket-proxy network membership set is exactly
   the four expected members** (portal-api, docker-socket-proxy,
   runtime-api-socket-proxy, and nothing else). Today the rule is a documented
   MUST-NOT list, which enumerates the forbidden rather than the permitted — a
   new service is permitted by default. Same class as the vexa12 network tests
   from SPEC-VEXA-004.
2. **Assert the raw-socket mount set is exactly the expected one.** A live
   sweep found two: `docker-socket-proxy` (expected) and **`alloy`** (see open
   question 5 — it bypasses the proxy). A CI guard should pin that set so a
   third appearance fails, and Alloy's entry should carry its justification
   inline rather than being discovered again by the next audit.

Both are hours, not weeks, and neither depends on which option is chosen.

## Success criteria

- [ ] REQ-1 spike: the escalation is demonstrated on a non-production host and
      written down, or shown not to apply — no implementation starts on an
      inferred mechanism.
- [ ] A `HostConfig` carrying `Privileged`, `Binds: ["/:/host"]`, `PidMode:
      host` or `CapAdd` is rejected for both principals, proven by a test that
      fails when the policy is removed.
- [ ] portal-api can still provision a tenant end to end; vexa12-runtime can
      still spawn a bot that joins a real meeting and produces a transcript.
- [ ] A denial emits a structured event and raises the Grafana alert.
- [ ] The interim CI guards land regardless of chosen option.
- [ ] `.claude/rules/klai/platform/docker-socket-proxy.md` documents the policy
      layer, its allowlist per principal, and how to extend it — the existing
      per-verb rationale table is the model.

## Open questions

1. **Does the escalation actually work against our proxy version?**
   `tecnativa/docker-socket-proxy:v0.5.0` is haproxy-based and path-scoped; the
   inference is that it forwards the body untouched. REQ-1 settles it.
2. **Which principal identity does the policy key on?** The proxy sees a TCP
   peer, not an authenticated caller. portal-api and vexa12-runtime reach it
   over different networks (`socket-proxy` directly vs the socat bridge), which
   may be enough to distinguish them — or may not.
3. **Does the socat bridge preserve enough peer information** for per-principal
   policy, or does everything from `vexa12-runtime` arrive as the sidecar?
4. **What is the availability answer for REQ-U-004?** Fail-closed (REQ-S-001)
   means a policy-layer outage stops tenant signup. Acceptable, or does it need
   redundancy?
5. ~~**Does anything else on core-01 talk to the daemon** outside these two?~~
   **Answered while writing this SPEC — and it matters.** A live sweep of every
   running container found a third principal:

   | Container | Access | Note |
   |---|---|---|
   | `klai-core-docker-socket-proxy-1` | raw socket, rw | the proxy itself — expected |
   | `klai-core-alloy-1` | **raw socket, read-only** | log collection; bypasses the proxy entirely |
   | `klai-core-portal-api-1` | via proxy (socket-proxy network) | |
   | `klai-core-runtime-api-socket-proxy-1` | via proxy (socat bridge) | |

   The `socket-proxy` network has exactly three members: portal-api,
   docker-socket-proxy, runtime-api-socket-proxy. So the proxy-mediated side is
   already as tight as the interim guard would assert.

   **Alloy is the finding.** It mounts `/var/run/docker.sock` directly, so no
   policy layer placed in front of the proxy constrains it. The mount is
   `RW=false`, which blocks writes to the socket *file* but does NOT make the
   Docker API read-only — a process that can write to the socket's byte stream
   can still issue `POST /containers/create`, and read-only on a unix socket
   does not prevent that. Alloy's legitimate need is log tailing, i.e. `GET`
   only.

   This changes the scope: a policy layer in front of the proxy covers two of
   three principals. Alloy needs either its own read-only proxy instance
   (`CONTAINERS=1` with `POST`/`DELETE` unset) or an explicit, recorded
   acceptance. Deciding that belongs in this SPEC's Phase 1, not after.

## Relationship to other SPECs

- **SPEC-SEC-021** introduced `docker-socket-proxy` and the verb whitelist. This
  SPEC does not undo it; it adds the layer that whitelist cannot express.
- **SPEC-SEC-024** produced the per-verb rationale table in
  `docker-socket-proxy.md`, including the deliberate `CONTAINERS + POST` entry
  for `containers.create`. That entry is correct — the verb IS needed. This SPEC
  constrains its argument.
- **SPEC-VEXA-004** migrated Vexa to 0.12 and raised this as review finding 3.
  It confined `vexa12-meeting-api` to two-member networks; the same reasoning
  applied one layer down.
- **SPEC-INFRA-CONTAINER-HYGIENE-001** owns the klasse-A/klasse-B container
  taxonomy and the labels portal-api sets. A policy layer must not break
  klasse-B provisioning.

## Out of scope

- The `EXEC`/`IMAGES`/`VOLUMES`/`BUILD`/`SYSTEM` verbs. They are already off and
  stay off.
- Kubernetes. Klai runs single-host compose; Vexa's Helm path is explicitly not
  a Klai deployment target.
- Whether portal-api should provision containers at all. It should — that is the
  tenant model. This SPEC constrains how, not whether.
