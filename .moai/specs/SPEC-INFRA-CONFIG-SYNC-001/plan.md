# SPEC-INFRA-CONFIG-SYNC-001 — Implementation Plan

## Strategie

Eén PR. Twee file-changes (één refactor, één nieuwe doc-sectie).

## Files

### EDIT: `.github/workflows/deploy-compose.yml`

Drie wijzigingen in dit bestand:

#### Wijziging 1 — paths-trigger

```diff
     paths:
       - 'deploy/docker-compose.yml'
       - 'deploy/docker-compose.override.yml'
       - 'deploy/caddy/Caddyfile'                  # SPEC-INFRA-CADDY-CONFIG-DEPLOY-001 R1
+      - 'deploy/alloy/config.alloy'               # SPEC-INFRA-CONFIG-SYNC-001 R3
+      - 'deploy/searxng/settings.yml'             # SPEC-INFRA-CONFIG-SYNC-001 R3
+      - 'deploy/vexa/profiles.yaml'               # SPEC-INFRA-CONFIG-SYNC-001 R3
       - 'deploy/grafana/provisioning/**'
       - 'deploy/scripts/**'
       - 'scripts/smoke-docker-socket-proxy.sh'
       - '.github/workflows/deploy-compose.yml'
```

#### Wijziging 2 — sparse-checkout

```diff
             git sparse-checkout set --skip-checks \
               deploy/docker-compose.yml \
               deploy/docker-compose.override.yml \
               deploy/caddy/Caddyfile \
+              deploy/alloy/config.alloy \
+              deploy/searxng/settings.yml \
+              deploy/vexa/profiles.yaml \
               deploy/grafana/provisioning \
               deploy/scripts \
               deploy/systemd \
               scripts
```

#### Wijziging 3 — helper-extract + 4 calls

Het bestaande inline Caddyfile-block (regel ~115-166 sinds de caddy
SPEC merge) wordt vervangen door:

```bash
            # SPEC-INFRA-CONFIG-SYNC-001 R1 — bash helper for bind-mount
            # config sync. Replaces the inline Caddyfile block from
            # SPEC-INFRA-CADDY-CONFIG-DEPLOY-001 (which is now one of
            # four call-sites). See infra/deploy.md "Bind-mount config
            # sync — required pattern" for the canonical reference.
            #
            # Args:
            #   $1 = compose service name (for force-recreate + ps -q)
            #   $2 = source path in this sparse-checkout
            #   $3 = destination path on host (will mkdir -p its parent)
            #
            # Behaviour:
            #   - If rsync content diff is non-empty → force-recreate
            #     the service + run a 5×2s health check loop. Fail the
            #     workflow if the container does not reach `running`
            #     within 10s.
            #   - If rsync content diff is empty → skip (idempotent).
            #
            # Caddy has `admin off`, so `caddy reload --config` is not
            # available — container restart is the canonical reload.
            # Same applies to alloy/searxng/runtime-api: all four read
            # their config at startup and need a recreate to pick up
            # changes.
            sync_and_recreate() {
              local svc="$1"
              local src="$2"
              local dst="$3"
              mkdir -p "$(dirname "$dst")"
              local changes
              changes=$(rsync -ac --itemize-changes "$src" "$dst" | grep -E '^[<>*]' || true)
              if [ -z "$changes" ]; then
                echo "$svc config unchanged; skipping recreate."
                return 0
              fi
              echo "$svc config content changed:"
              echo "$changes"
              docker compose --project-directory /opt/klai up -d --force-recreate "$svc"

              echo "::group::$svc post-recreate health check"
              local healthy=0
              local i cid status
              for i in 1 2 3 4 5; do
                sleep 2
                cid=$(docker compose --project-directory /opt/klai ps -q "$svc" 2>/dev/null || true)
                if [ -z "$cid" ]; then
                  echo "$svc container not found (attempt ${i}/5)"
                  continue
                fi
                status=$(docker inspect --format '{{.State.Status}}' "$cid" 2>/dev/null || echo "missing")
                if [ "$status" = "running" ]; then
                  echo "$svc is running (attempt ${i})"
                  healthy=1
                  break
                fi
                echo "$svc state: $status (attempt ${i}/5)"
              done
              if [ "$healthy" -ne 1 ]; then
                echo "::error::$svc did not reach running state within 10s after recreate"
                docker compose --project-directory /opt/klai logs --tail 50 "$svc" || true
                exit 1
              fi
              echo "::endgroup::"
            }

            # SPEC-INFRA-CONFIG-SYNC-001 R2 — apply helper to all four
            # single-file relative bind-mounts that lack a dedicated
            # service-deploy workflow. Note vexa's profiles.yaml is
            # mounted into the runtime-api compose service, NOT a
            # service named "vexa" — the asymmetry matters for the
            # force-recreate target.
            sync_and_recreate caddy       deploy/caddy/Caddyfile        /opt/klai/caddy/Caddyfile
            sync_and_recreate alloy       deploy/alloy/config.alloy     /opt/klai/alloy/config.alloy
            sync_and_recreate searxng     deploy/searxng/settings.yml   /opt/klai/searxng/settings.yml
            sync_and_recreate runtime-api deploy/vexa/profiles.yaml     /opt/klai/vexa/profiles.yaml
```

Dit blok vervangt het hele bestaande SPEC-INFRA-CADDY-CONFIG-
DEPLOY-001 inline-block. Functioneel identiek voor caddy; nieuw werk
voor de andere drie services.

### EDIT: `.claude/rules/klai/infra/deploy.md`

Nieuwe sectie toevoegen na de bestaande "docker-compose.yml sync"
sectie:

```markdown
## Bind-mount config sync — required pattern

> Closes the bind-mount-without-sync-workflow class of bugs that the
> librechat-voys + Caddyfile incidents both belong to. Codified by
> SPEC-INFRA-CONFIG-SYNC-001 (refactored from SPEC-INFRA-CADDY-
> CONFIG-DEPLOY-001).

### The class

When `deploy/docker-compose.yml` declares a relative bind-mount
(`./<svc>/<file>:/etc/...`), the host source resolves to
`/opt/klai/<svc>/<file>`. Without a workflow that syncs the file from
the repo to that host path, edits to the repo never reach the
running container. The bind-mount silently uses whatever was scp'd
manually long ago — sometimes drifting for months without notice.

### The fix

`.github/workflows/deploy-compose.yml` ships a bash helper
`sync_and_recreate <service> <repo-src> <host-dst>` that:

1. Adds the source path to its `paths:` trigger and sparse-checkout
2. Rsyncs with `-ac --itemize-changes` (content-checksum, ignores
   mtime churn from a fresh git clone)
3. On content change: `docker compose ... up -d --force-recreate
   <service>` + 5×2s health check loop using `docker inspect`
4. On no change: idempotent skip

### Required when adding a new bind-mount

When you add a new line of the form `- ./<svc>/<file>:/...` to
`deploy/docker-compose.yml`, you MUST in the same PR:

1. Add `'deploy/<svc>/<file>'` to `deploy-compose.yml`'s `paths:`
   trigger
2. Add the same path to the `git sparse-checkout set` invocation
3. Add a `sync_and_recreate <compose-service> deploy/<svc>/<file>
   /opt/klai/<svc>/<file>` call alongside the existing four

If the bind-mount is a directory (not a single file), use a
directory-rsync block in the style of the existing grafana
provisioning sync — the helper is single-file only.

### Inventory

#### Class A — synced via deploy-compose.yml `sync_and_recreate`

- `deploy/caddy/Caddyfile` → `caddy` service
- `deploy/alloy/config.alloy` → `alloy` service
- `deploy/searxng/settings.yml` → `searxng` service
- `deploy/vexa/profiles.yaml` → `runtime-api` service (note: NOT a
  service named "vexa" — the file is consumed by runtime-api)

#### Class A-dir — synced via deploy-compose.yml directory rsync

- `deploy/grafana/provisioning/` → `grafana` service (SPEC-OBS-001
  Phase C, predates the helper; kept inline for now)

#### Class B — own dedicated workflow

- `deploy/litellm/*.{py,yaml}` → `litellm-hook-deploy.yml`
- `deploy/librechat/...` → `deploy-librechat-config.yml`
- Tenant Caddyfiles (`caddy/tenants/*.caddyfile`) → portal-api
  `_write_tenant_caddyfile` runtime (NOT CI; per-tenant)

#### Class C — one-shot init, no drift risk

- `deploy/postgres/init.sql` — read once on DB volume init
- `deploy/firecrawl-nuq-init.sql` — read once on DB volume init

### When you change a Class A file

Just `git push`. The workflow rsyncs + force-recreates. ~30s end to
end.

### When you change a Class A-dir file

Same — directory rsync handles it. ~30s end to end.

### When you change a Class B file

Use the dedicated workflow — see the workflow's own paths-trigger
and follow that contract.

### When you change a Class C file

Don't bother editing the existing file directly — it only affects
fresh DB-volume bootstraps. For existing prod, write a migration.

### Adding a new service with a bind-mount config?

Default to Class A. Class B is justified only when the service has a
non-trivial reload mechanism that recreate cannot replace, or when
its deploy cadence differs strongly from the rest of compose.
Class C is rare — only for true one-shot init that cannot be a
migration.
```

## Test plan

### Pre-merge (in PR CI)

- T1: Geen workflow runs verwacht. `caddy / build-push` triggert niet
  (geen Dockerfile change). `caddy-validate` triggert niet (geen
  Caddyfile/Dockerfile/build.sh change). `deploy-compose.yml` is
  push-only. PR-checks lijst zal leeg zijn.

### Post-merge (verificatie procedure)

- V1: `gh run list --workflow deploy-compose.yml -L 1 --json
  conclusion --jq '.[0].conclusion'` returns `"success"` binnen 5
  minuten.
- V2: Workflow-log toont per service ofwel `<svc> config unchanged;
  skipping recreate.` (idempotent — als de file op core-01 al matched
  HEAD) ofwel `<svc> is running (attempt N)` (recreate-and-recover —
  als de file daadwerkelijk wijzigde).
- V3: 3-way sha256 match per service:
  ```bash
  for pair in \
    "alloy:/opt/klai/alloy/config.alloy:deploy/alloy/config.alloy" \
    "searxng:/opt/klai/searxng/settings.yml:deploy/searxng/settings.yml" \
    "runtime-api:/opt/klai/vexa/profiles.yaml:deploy/vexa/profiles.yaml"; do
    svc="${pair%%:*}"
    rest="${pair#*:}"
    host_path="${rest%%:*}"
    repo_path="${rest##*:}"
    HEAD=$(curl -fsSL "https://raw.githubusercontent.com/GetKlai/klai/main/$repo_path" | sha256sum | awk '{print $1}')
    HOST=$(ssh core-01 "sha256sum $host_path" | awk '{print $1}')
    CTR_PATH=$(case "$svc" in
      alloy) echo "/etc/alloy/config.alloy";;
      searxng) echo "/etc/searxng/settings.yml";;
      runtime-api) echo "/app/profiles.yaml";;
    esac)
    CTR=$(ssh core-01 "docker exec klai-core-${svc}-1 sha256sum $CTR_PATH" 2>/dev/null | awk '{print $1}')
    [ "$HEAD" = "$HOST" ] && [ "$HOST" = "$CTR" ] && echo "$svc PASS" || echo "$svc FAIL — HEAD=$HEAD HOST=$HOST CTR=$CTR"
  done
  ```

### Rollback plan

Identiek aan SPEC-INFRA-CADDY-CONFIG-DEPLOY-001: `git revert <merge-
sha> + git push` triggert opnieuw `deploy-compose.yml`. Alle services
syncen terug naar oude versies + recreate. Geen handmatige interventie
op core-01.

## Quality gates

- YAML lint: `python3 -c "import yaml; yaml.safe_load(...)"` op
  deploy-compose.yml.
- Bash syntax: `bash -n` op een geëxtraheerde versie van het script
  (alleen het ssh-action script-blok).
- SPEC compleet: 4 artefacten in `.moai/specs/SPEC-INFRA-CONFIG-
  SYNC-001/`.
- Geen runtime/code regressies: alleen workflow YAML + .md docs
  geraakt; geen Python/TS/Go.

## Out-of-scope follow-ups

1. Pre-merge syntax-validate workflows voor alloy/searxng/vexa. Per
   service een eigen `<svc>-validate.yml` met een service-specifieke
   `<tool> validate` of `<tool> fmt --check`. ROI te bekijken na
   eerste config-change-incident.
2. Refactor van grafana provisioning sync naar de helper (vereist
   directory-mode toevoegen aan helper). Geen ROI tot een andere
   directory-bind-mount opduikt.
3. Audit van LiteLLM en LibreChat deploy-workflows — elk hun eigen
   verhaal, niet hier oplossen.
4. Per-service health check tuning (10s grace period kan te kort
   blijken voor één service onder zware load) — adressering bij
   eerste false-negative incident.
