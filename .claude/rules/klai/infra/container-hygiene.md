---
paths:
  - "**/docker-compose*.yml"
  - "**/Dockerfile"
  - ".github/**/*.yml"
  - "**/*.sh"
  - "**/Caddyfile"
---
# Container Hygiene

> Mechanical guards against the librechat-voys class of incidents.
> SPEC-INFRA-CONTAINER-HYGIENE-001 REQ-1.

## The bug we are preventing

On 2026-05-02 the `librechat-voys` production container (Voys-tenant
chat) was removed by a cleanup-agent because it lacked
`com.docker.compose.project=klai-core` labels. The cleanup framing was:
"no compose label + no Caddy upstream = wees, safe to delete." The
container was, in fact, a legitimate **provisioning-managed** prod
container — created dynamically by portal-api per tenant, not by
compose. Recovery was possible because the tenant config
(`/opt/klai/librechat/voys/`) survived, but the original image SHA
(untagged, never in any registry) was lost permanently.

The class: a destructive `docker rm`/`rmi`/`volume rm` on a target
whose "orphan" appearance is fully consistent with being a legitimate
prod container managed by a non-compose pathway.

## Two legitimate classes of prod containers

Klai has two canonical management paths for prod containers. Hygiene
tooling MUST recognise both:

### Klasse A — Compose-managed

- Created by `docker compose up` against
  `klai-infra/deploy/docker-compose.yml`
- Carries `com.docker.compose.project=klai-core` (and
  `com.docker.compose.service=<naam>`) labels automatically
- Examples: `klai-core-portal-api-1`, `klai-core-redis-1`,
  `librechat-getklai`, `librechat-dev`
- Cleanup: through compose (`docker compose down`, `--remove-orphans`)

### Klasse B — Provisioning-managed

- Created by portal-api via `client.containers.run()` per tenant
- Source: `klai-portal/backend/app/services/provisioning/infrastructure.py::_start_librechat_container`
- MUST carry these three labels (SPEC-INFRA-CONTAINER-HYGIENE-001 REQ-2a):
  - `klai.managed_by=portal-api-provisioning`
  - `klai.tenant_slug=<slug>` (e.g. `voys`, `acme-corp`)
  - `klai.kind=librechat` (other kinds may follow if portal-api
    provisions more types in future)
- Example: `librechat-voys`, `librechat-<future-tenant>`
- Cleanup: through portal-api deprovisioning flow
  (`provisioning/orchestrator.py::deprovision_tenant`), NEVER via
  direct `docker rm`

A container without ANY of these labels (and without a
`klai.adhoc=*` opt-in for ad-hoc debug — see below) is a wees and
warrants human review before removal.

## How we block it now

Two layers, both mechanical:

### Layer 1 — PreToolUse hook (this rule's enforcement)

`.claude/hooks/klai/container-hygiene-preflight.sh` runs before every
Bash tool-call. If the command matches a destructive docker pattern, it
runs checks and `exit 2` blocks the tool-call:

1. **Hard-block dangerous global prunes:** `docker volume prune`,
   `docker image prune -af`, `docker system prune -a`,
   `docker compose down --volumes`. These never have a one-shot
   legitimate use — REQ-6's daily `docker-cleanup.timer` is the canonical
   safe-cleanup path.
2. **Tenant-pattern match:** target matches `librechat-*` (klasse-B
   provisioning-managed) OR ends in `-voys`/`-getklai`/`-<word>-tenant`.
   Hard block; the message points at the deprovisioning flow because
   direct `docker rm` on a klasse-B container bypasses Mongo/Caddy/
   Meilisearch cleanup that portal-api orchestrates.
3. **Compose git-history grep:** if `klai-infra` checkout reachable,
   search `deploy/docker-compose*.yml` history for the target. Match
   means it was a declared service at some point — needs review.
4. **Caddy upstream check:** target reachable via `/opt/klai/Caddyfile`
   (best-effort, only when core-01 reachable — fail-open on dev).
5. **VictoriaLogs traffic check:** target had log-events in last 30d
   (best-effort — fail-open on dev).

Checks 1 + 2 are always-on; 3 is best-effort with a klai-infra
sibling checkout; 4 + 5 are deferred to a follow-up that adds a fast
TCP probe before they activate.

The hook is registered in `.claude/settings.json` as a PreToolUse
matcher on `Bash` alongside `portal-api-preflight.sh`.

### Layer 2 — VictoriaLogs orphan-audit (REQ-5, separate stage)

A weekly `docker-orphan-audit.sh` running on core-01 emits structlog
events to VictoriaLogs (`service:klai-orphan-audit`) describing every
container without compose label, every container with a tenant-pattern
naam without Caddy upstream, every untagged image >30d, etc. The hook
queries this stream as Check 5 (after that stage lands); operators
inspect via Grafana panel.

This is the catch-net for everything Layer 1 cannot see — including
manual `ssh core-01 "docker rm"` from outside Claude Code.

## What this rule does NOT do

- Does NOT replace human review. A blocked command can be a
  legitimate cleanup — the user can take ownership and proceed with
  explicit approval. The block forces the conversation, not the
  decision.
- Does NOT cover all destructive docker operations. `docker stop`,
  `docker kill`, `docker container restart` are not blocked because
  they are reversible. `docker exec ... rm -rf /...` inside a
  container is also not blocked — that's a different class.
- Does NOT enforce alles-via-compose at deploy time. That is REQ-2's
  CI guard (`audit-compose-orphans.sh` in the klai-infra
  `audit-compose.yml` workflow) — separate stage.
- Does NOT work for SSH-routed commands. `ssh core-01 "docker rm X"`
  is one tool-call (`Bash` of the SSH command); the hook sees the
  outer shell and the regex catches the docker invocation.
  Multi-line scripts and indirect invocations may not match — REQ-5
  audit is the detection-net for those.

## When the hook blocks something legitimate

A block is not a verdict. Common false-positive causes and the
override path:

| Cause | Override |
|---|---|
| Container name happens to end in `-voys` etc. but is a test fixture | Rename the test fixture, or use a `--label klai.adhoc=YYYY-MM-DD-reason` and `--rm` on creation so it self-cleans |
| Compose history match on a service that was renamed | Confirm the old name truly has no consumers, then run the command via SSH bypassing the hook (`ssh core-01 "docker rm X"`) — but **document the override** in the PR message or commit. |
| Image SHA blocked because it's referenced in compose | Verify by running `docker compose config | grep <sha>` — if true match, do NOT delete. If stale reference, fix the compose file first. |

## Ad-hoc debug containers — the safe path

For one-off testing, never `docker run` without `--rm` and never
without a label:

```bash
docker run --rm \
  --label klai.adhoc=2026-05-02-debug-csv-export \
  --label klai.owner=mark.vletter \
  ghcr.io/getklai/portal-api:latest <cmd>
```

`--rm` makes it self-clean on exit. The labels make it filter-able in
REQ-5 audit (so it appears in an "ad-hoc" section, not a "wees"
section). The `klai.owner` makes responsibility traceable.

A long-running debug session that survives multiple commands? Promote
it to a compose service in `klai-infra/deploy/docker-compose.yml`
under a clear `# debug` comment-block, even temporarily. The hook +
audit are designed around compose-as-source-of-truth.

## Related

- `pitfalls/process-rules.md` — `container-cleanup-without-preflight (HIGH)`
- `infra/deploy.md` — CI deploy verification + atomic env writes
- `infra/observability.md` — VictoriaLogs / Grafana / product_events split
- `platform/docker-socket-proxy.md` — what tool to use for which Docker
  operation (and what NOT to use docker exec for)
