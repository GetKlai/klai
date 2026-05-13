# SPEC-INFRA-CADDY-CONFIG-DEPLOY-001 — Implementation Plan

## Strategie

Eén PR. Drie file-changes (één new, twee edited). Geen runtime code
geraakt; alleen CI workflow YAML.

## Files

### NEW: `.github/workflows/caddy-validate.yml`

PR-trigger workflow die `caddy validate` runt tegen de Caddyfile in
een Docker container. Implementeert R3.

Volledige inhoud:

```yaml
name: Validate Caddyfile

on:
  pull_request:
    paths:
      - 'deploy/caddy/Caddyfile'
      - 'deploy/caddy/Dockerfile'
      - 'deploy/caddy/build.sh'
      - '.github/workflows/caddy-validate.yml'

permissions:
  contents: read
  packages: read

env:
  FORCE_JAVASCRIPT_ACTIONS_TO_NODE24: "true"

jobs:
  validate:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v6

      - name: Log in to GHCR
        uses: docker/login-action@v4
        with:
          registry: ghcr.io
          username: ${{ github.actor }}
          password: ${{ secrets.GITHUB_TOKEN }}

      - name: Validate Caddyfile syntax
        run: |
          # SPEC-INFRA-CADDY-CONFIG-DEPLOY-001 R3
          # Run `caddy validate` in the same image that serves prod, so
          # the validate sees the same xcaddy plugins (Hetzner DNS,
          # ratelimit). Dummy env vars match the {$ADMIN_EMAIL} +
          # {$DOMAIN} placeholders. Empty tenants dir is mounted to
          # resolve the `import /etc/caddy/tenants/*.caddyfile` glob to
          # zero matches (Caddy 2 silently OK on no-match).
          mkdir -p /tmp/empty-tenants
          docker run --rm \
            -v "$PWD/deploy/caddy/Caddyfile:/etc/caddy/Caddyfile:ro" \
            -v "/tmp/empty-tenants:/etc/caddy/tenants:ro" \
            -e ADMIN_EMAIL=ci@example.com \
            -e DOMAIN=example.com \
            ghcr.io/getklai/caddy-hetzner:latest \
            caddy validate --adapter caddyfile --config /etc/caddy/Caddyfile
```

### EDIT: `.github/workflows/caddy.yml`

Paths-set reduceren — `deploy/caddy/**` wordt te breed; we willen
alleen image-relevante triggers. Implementeert R2.

Diff (paths-blok):

```diff
 on:
   push:
     branches: [main]
     paths:
-      - 'deploy/caddy/**'
+      - 'deploy/caddy/Dockerfile'
+      - 'deploy/caddy/build.sh'
+      - 'deploy/caddy/.trivyignore.yaml'
       - '.github/workflows/caddy.yml'
   workflow_dispatch:
```

Geen andere wijzigingen aan dit bestand. De `Deploy to core-01` step
blijft `compose-up.sh caddy` aanroepen; dat is correct voor het
service-definition / image-pull pad.

### EDIT: `.github/workflows/deploy-compose.yml`

Paths uitbreiden, sparse-checkout uitbreiden, en de rsync + recreate +
health check toevoegen voor Caddyfile. Implementeert R1 + R4.

Diff 1 — paths-trigger:

```diff
 on:
   push:
     branches: [main]
     paths:
       - 'deploy/docker-compose.yml'
       - 'deploy/docker-compose.override.yml'
+      - 'deploy/caddy/Caddyfile'                  # SPEC-INFRA-CADDY-CONFIG-DEPLOY-001 R1
       - 'deploy/grafana/provisioning/**'
       - 'deploy/scripts/**'
       - 'scripts/smoke-docker-socket-proxy.sh'
       - '.github/workflows/deploy-compose.yml'
```

Diff 2 — sparse-checkout (binnen het ssh-action `script:` blok):

```diff
             git sparse-checkout set --skip-checks \
               deploy/docker-compose.yml \
               deploy/docker-compose.override.yml \
+              deploy/caddy/Caddyfile \
               deploy/grafana/provisioning \
               deploy/scripts \
               deploy/systemd \
               scripts
```

Diff 3 — nieuwe rsync + recreate + health check blok, geplaatst NA de
grafana provisioning sync (regel ~114) en VOOR de smoke-test sync
(regel ~119):

```bash
# SPEC-INFRA-CADDY-CONFIG-DEPLOY-001 R1+R4 — sync Caddyfile to bind-mount source.
#
# The compose file mounts ./caddy/Caddyfile (resolved as
# /opt/klai/caddy/Caddyfile) into the caddy container at
# /etc/caddy/Caddyfile. Without this sync, an image rebuild + container
# recreate via caddy.yml leaves the container reading the OLD Caddyfile
# from the bind-mount.
#
# Content-aware: only force-recreate when content changed (mirrors
# grafana provisioning sync above). A bind-mount-only change does not
# trigger compose recreate via plain `up -d`. Caddy has `admin off`,
# so `caddy reload --config` is not available — container restart
# (~1s TLS interruption) is the canonical reload, identical to
# `_reload_caddy()` in portal-api provisioning.
mkdir -p /opt/klai/caddy
RSYNC_CADDY_CHANGES=$(rsync -ac --itemize-changes deploy/caddy/Caddyfile /opt/klai/caddy/Caddyfile | grep -E '^[<>*]' || true)
if [ -n "$RSYNC_CADDY_CHANGES" ]; then
  echo "Caddyfile content changed:"
  echo "$RSYNC_CADDY_CHANGES"
  docker compose --project-directory /opt/klai up -d --force-recreate caddy

  # SPEC-INFRA-CADDY-CONFIG-DEPLOY-001 R4 — post-recreate health check.
  # Fail the workflow if caddy doesn't return to running within 10s.
  # The operator's recovery is `git revert + push`, which triggers
  # another sync run that rsyncs the previous Caddyfile back.
  echo "::group::Caddy post-recreate health check"
  HEALTHY=0
  for i in 1 2 3 4 5; do
    sleep 2
    STATUS=$(docker compose --project-directory /opt/klai ps caddy --format json 2>/dev/null | jq -r '.[0].State // "missing"')
    if [ "$STATUS" = "running" ]; then
      echo "Caddy is running (attempt ${i})"
      HEALTHY=1
      break
    fi
    echo "Caddy state: $STATUS (attempt ${i}/5)"
  done
  if [ "$HEALTHY" -ne 1 ]; then
    echo "::error::Caddy did not reach running state within 10s after recreate"
    docker compose --project-directory /opt/klai logs --tail 50 caddy || true
    exit 1
  fi
  echo "::endgroup::"
else
  echo "Caddyfile unchanged; skipping caddy recreate."
fi
```

## Test plan

### Pre-merge (in PR CI)

- T1: `caddy-validate.yml` SHALL run en groen worden op deze PR.
  De Caddyfile is hier ongewijzigd, dus syntax-validate moet passeren.
- T2: `caddy.yml` SHALL NIET runnen op deze PR. Bevestigen via
  `gh pr checks` — het mag niet listed zijn (paths matched niet).
- T3: `deploy-compose.yml` SHALL NIET runnen op deze PR (push-only,
  not pull_request).

### Post-merge (verificatie procedure)

- V1: Direct na merge: `gh run list --workflow deploy-compose.yml -L 1`
  toont de run als `success`.
- V2: `ssh core-01 "diff -q /opt/klai/caddy/Caddyfile <(curl -fsSL
  https://raw.githubusercontent.com/GetKlai/klai/main/deploy/caddy/Caddyfile)"`
  returns 0.
- V3: Open een opvolg-PR die een onschuldige comment toevoegt aan
  `deploy/caddy/Caddyfile`, merge naar main, en herhaal V1+V2. De
  zojuist gewijzigde regel SHALL nu in `/opt/klai/caddy/Caddyfile`
  EN in `docker exec klai-core-caddy-1 cat /etc/caddy/Caddyfile` staan.

### Rollback plan

`git revert <merge-sha> + git push` — dit hertriggert
`deploy-compose.yml`, dat de oude Caddyfile rsynct + force-recreates.
Geen handmatige interventie nodig op core-01.

## Quality gates

- YAML lint: `actionlint .github/workflows/*.yml`
- Plan compleet: SPEC + plan + acceptance + research aanwezig in
  `.moai/specs/SPEC-INFRA-CADDY-CONFIG-DEPLOY-001/`.
- Geen runtime/code regressies: deze SPEC raakt geen Python / TS / Go
  code, geen migrations, geen tests-die-bestaande-symbols-aanraken.

## Out-of-scope follow-ups

Deze SPEC houdt het strikt bij de drie file-changes. Volgende
mogelijke verbeteringen die expliciet NIET in deze PR landen:

1. `compose-up.sh` uitbreiden met een `--force-recreate` flag zodat
   ook deze pad door de wrapper gaat (nu duplicaat `docker compose
   ... up -d --force-recreate` direct, identiek aan grafana). Kleine
   refactor; aparte SPEC.
2. Image-baked Caddyfile als secondary safety net (multiple-source-of-
   truth schade groter dan baten — niet doen).
3. Caddy admin-API enable + `caddy reload` ipv container restart —
   security-trade-off die buiten dit SPEC valt.
4. `actionlint` als verplichte CI-check op alle workflow-changes —
   waardevol maar bredere scope.
