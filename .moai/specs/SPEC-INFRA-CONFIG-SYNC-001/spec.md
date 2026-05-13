---
id: SPEC-INFRA-CONFIG-SYNC-001
version: "0.1.0"
status: ready-for-implementation
created: "2026-05-07"
updated: "2026-05-07"
author: MoAI
priority: high
issue_number: 0
---

## HISTORY

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 0.1.0 | 2026-05-07 | MoAI | Initial. Generalises the SPEC-INFRA-CADDY-CONFIG-DEPLOY-001 pattern to alloy / searxng / vexa, refactors the rsync block into a reusable bash helper, and codifies the rule for future bind-mounts. |

# SPEC-INFRA-CONFIG-SYNC-001 — Bind-mount config sync expansion

## Overview

SPEC-INFRA-CADDY-CONFIG-DEPLOY-001 (merged 2026-05-07, commit
`a2a090a5`) sloot het Caddyfile-sync gat. Tijdens de post-merge
review werd duidelijk dat het gat een **klasse** is, niet een
incidenteel probleem voor één service. `deploy/docker-compose.yml`
bevat zeven relative bind-mounts naar config-files; zonder
sync-workflow blijft de host-versie steken bij wat ooit handmatig
gescpt werd. Vier daarvan vallen ONDER deze SPEC; drie hebben hun
eigen aparte mechanismes en blijven buiten scope.

| Bind-mount | Compose service | Status vóór deze SPEC | Status na |
|---|---|---|---|
| `./caddy/Caddyfile` | `caddy` | gesynct (SPEC-INFRA-CADDY-CONFIG-DEPLOY-001) | idem (gerefactored naar helper) |
| `./alloy/config.alloy` | `alloy` | NIET gesynct — gat | gesynct |
| `./searxng/settings.yml` | `searxng` | NIET gesynct — gat | gesynct |
| `./vexa/profiles.yaml` | `runtime-api` | NIET gesynct — gat | gesynct |
| `./grafana/provisioning/` | `grafana` | gesynct (SPEC-OBS-001 Phase C) | ongewijzigd (dir-rsync, andere shape) |
| `./litellm/*.{py,yaml}` | `litellm` | eigen workflow (`litellm-hook-deploy.yml`) | buiten scope |
| `./librechat/...` | meerdere | eigen workflow (`deploy-librechat-config.yml`) | buiten scope |

## Doel

1. **Refactor** de Caddyfile-sync inline-block uit
   `.github/workflows/deploy-compose.yml` naar een reusable bash-helper
   `sync_and_recreate()`. Eén service was nog te tolereren als inline
   block; vier identieke kopieën zou een code-smell zijn.
2. **Apply** de helper op alloy, searxng, en vexa — same shape, same
   rsync semantics, same content-aware force-recreate, same health
   check.
3. **Codify** de regel in `.claude/rules/klai/infra/deploy.md` zodat de
   volgende ontwikkelaar (mens of AI) die een nieuwe relative
   bind-mount aan compose toevoegt, mechanisch herinnerd wordt aan de
   sync-verplichting.

## Environment

- **Affected workflow files:**
  - `.github/workflows/deploy-compose.yml` — refactor naar helper
    function; paths-trigger uitbreiden met de drie nieuwe paths;
    helper aanroepen voor caddy + alloy + searxng + runtime-api.

- **Affected rule files:**
  - `.claude/rules/klai/infra/deploy.md` — nieuwe sectie "Bind-mount
    config sync — required pattern" met checklist voor nieuwe
    bind-mounts.

- **Out-of-scope (geen wijziging):**
  - Caddy validate workflow (`.github/workflows/caddy-validate.yml`)
    — alloy/searxng/vexa hebben geen pre-merge validate-step in deze
    SPEC. Reden: alloy heeft een complexe HCL-achtige config die
    `alloy fmt` zou willen, searxng's settings.yml is YAML zonder
    custom validate, vexa's profiles.yaml is YAML. Een generieke
    YAML-lint zou trivieel zijn maar voegt weinig toe omdat de
    post-recreate health check de werkelijke runtime-fouten al vangt.
    Validate-PR-stappen kunnen alsnog komen als follow-up SPECs per
    service indien dat ROI biedt; out-of-scope voor deze sync-fix.
  - Grafana provisioning sync — werkt prima, dir-rsync ipv file-rsync,
    niet de moeite om in dezelfde helper te wringen.
  - LiteLLM en LibreChat — eigen deploy-workflows; aparte audits.

- **Affected production paths op core-01 (na deze SPEC):**
  - `/opt/klai/alloy/config.alloy`
  - `/opt/klai/searxng/settings.yml`
  - `/opt/klai/vexa/profiles.yaml`

  De directories `/opt/klai/{alloy,searxng,vexa}/` worden door de
  helper aangemaakt indien afwezig (`mkdir -p`).

## Assumptions

- A1: Alloy ondersteunt clean container restart (~2-3s observability-
  blackout tijdens recreate). Geen log-data verloren omdat Alloy de
  Docker socket positie persistert in zijn data volume.
- A2: Searxng leest `settings.yml` bij startup; recreate is de
  canonieke reload-route. Geen hot-reload nodig.
- A3: Vexa runtime-api leest `profiles.yaml` bij startup en bij
  expliciete profile-load API-calls. Recreate vangt beide gevallen.
- A4: De caddy SPEC's force-recreate pattern (`docker compose
  --project-directory /opt/klai up -d --force-recreate <svc>`) werkt
  voor alle vier services identiek. Bevestigd door grafana-precedent
  (zelfde flag, zelfde shape).
- A5: Vexa profiles.yaml-mount zit op compose-service `runtime-api`
  (NIET `vexa`). De helper accepteert de compose-service-naam als
  parameter, niet de file-path-derived naam.
- A6: De drie nieuwe bind-mounts hebben eerder via handmatige scp /
  initial-bootstrap een file op core-01 staan. Eerste sync na merge
  rsynct met content-checksum vergelijking; identieke files = geen
  recreate (idempotent).

## Requirements

### R1 — Ubiquitous: bash-helper voor sync-and-recreate

`.github/workflows/deploy-compose.yml` SHALL een bash function
`sync_and_recreate()` definiëren die de volgende parameters accepteert:

- `$1` = compose service name (e.g., `caddy`, `alloy`, `searxng`,
  `runtime-api`)
- `$2` = source path in checkout (e.g., `deploy/caddy/Caddyfile`)
- `$3` = destination path op host (e.g., `/opt/klai/caddy/Caddyfile`)

De helper SHALL:

1. `mkdir -p "$(dirname "$3")"` zodat de dest-directory bestaat.
2. `rsync -ac --itemize-changes "$2" "$3"` runnen en de content-
   change-output capturen.
3. Indien content-change non-empty:
   a. Echo `<svc> config content changed:` + de rsync-output.
   b. `docker compose --project-directory /opt/klai up -d
      --force-recreate "$1"` runnen.
   c. Health check loop (5×2s) via `docker compose ps -q "$1"` +
      `docker inspect --format '{{.State.Status}}'`. Op `running`
      breken + success. Anders na 10s `::error::`, log-dump van laatste
      50 regels, en `exit 1`.
4. Indien content-change empty:
   a. Echo `<svc> config unchanged; skipping recreate.`
   b. Geen verdere actie.

De helper SHALL geen externe afhankelijkheden hebben anders dan
`rsync`, `docker`, en de docker compose CLI.

### R2 — Ubiquitous: pas helper toe op alle vier de single-file bind-mounts

Het workflow-script SHALL de helper aanroepen voor:

```bash
sync_and_recreate caddy       deploy/caddy/Caddyfile        /opt/klai/caddy/Caddyfile
sync_and_recreate alloy       deploy/alloy/config.alloy     /opt/klai/alloy/config.alloy
sync_and_recreate searxng     deploy/searxng/settings.yml   /opt/klai/searxng/settings.yml
sync_and_recreate runtime-api deploy/vexa/profiles.yaml     /opt/klai/vexa/profiles.yaml
```

Deze vier calls vervangen het bestaande inline caddy-block in
`deploy-compose.yml` (geïntroduceerd door SPEC-INFRA-CADDY-CONFIG-
DEPLOY-001). Functioneel resultaat voor caddy is identiek; verschil is
alleen de structurele DRY.

### R3 — Ubiquitous: paths-trigger uitbreiden

`.github/workflows/deploy-compose.yml` SHALL triggeren op pushes naar
main die enige van deze files raken:

```yaml
paths:
  - 'deploy/docker-compose.yml'
  - 'deploy/docker-compose.override.yml'
  - 'deploy/caddy/Caddyfile'                  # bestaand
  - 'deploy/alloy/config.alloy'               # nieuw
  - 'deploy/searxng/settings.yml'             # nieuw
  - 'deploy/vexa/profiles.yaml'               # nieuw
  - 'deploy/grafana/provisioning/**'
  - 'deploy/scripts/**'
  - 'scripts/smoke-docker-socket-proxy.sh'
  - '.github/workflows/deploy-compose.yml'
```

De sparse-checkout in het script SHALL deze paths ook bevatten.

### R4 — Ubiquitous: regel-codificatie in `infra/deploy.md`

`.claude/rules/klai/infra/deploy.md` SHALL een nieuwe sectie krijgen
"Bind-mount config sync — required pattern" met:

1. Het probleem (host-bind-mount zonder sync-workflow = config drift)
2. De canonieke fix (helper + paths-trigger + sparse-checkout +
   helper-call)
3. Een checklist voor nieuwe relative bind-mounts in compose
4. Verwijzingen naar de SPECs (CADDY-CONFIG-DEPLOY + deze)
5. De huidige inventaris van klasse-A (gesynct) vs klasse-B (eigen
   workflow) vs klasse-C (one-shot init, geen sync nodig) bind-mounts

Dit is het mechanische voorkomen van het volgende bind-mount-zonder-
sync-incident.

### R5 — Out-of-scope: pre-merge validate per service

Pre-merge syntax-validate voor alloy/searxng/vexa SHALL NIET in deze
SPEC opgenomen worden. Reden: shape per service verschilt (HCL-achtig
vs YAML-met-schema vs YAML-zonder-schema), en de post-recreate health
check (R1.3.c) vangt al runtime-fouten met een acceptabele false-
negative-rate. Toekomstige per-service SPECs kunnen dit alsnog
toevoegen indien ROI dat rechtvaardigt.

## Non-Goals

- Pre-merge validate workflows voor alloy/searxng/vexa (zie R5).
- Refactor van grafana provisioning sync (dir-rsync, andere shape,
  werkt; geen waarde uit forceren in dezelfde helper).
- Audit van LiteLLM / LibreChat deploy workflows (eigen workflows,
  aparte SPECs indien nodig).
- Audit van one-shot init bind-mounts (`./postgres/init.sql`,
  `./firecrawl-nuq-init.sql`) — die worden alleen op DB-volume-create
  gelezen, geen drift-risico in lopende productie.

## Risks

| Risk | Mitigation |
|---|---|
| Eerste post-merge run recreate alle 3 services tegelijk → cumulatieve downtime | Health check na elke recreate; failure stopt de loop. In praktijk: rsync content-vergelijking → idempotent skip als de scp-versies al in sync zijn. Plan om dit te bevestigen via post-merge verificatie. |
| Vexa runtime-api recreate breekt actieve meeting-bots | Acceptabel: actieve meetings hebben hun eigen container per meeting; runtime-api is alleen de orchestrator-API. Zelfde semantiek als vandaag bij elke vexa-deploy. |
| Bash helper-syntax fouten | YAML lint + GitHub Actions parses bash op runtime; first run zou meteen een fout signaleren. Verkleind door geen exotische bash-features te gebruiken. |
| Helper crasht halverwege → workflow hangt | `set -e` bovenaan het ssh-action `script:` is al aanwezig. Helper exits non-zero op health failure → workflow exits → operator gealerteerd. |
| Een service heeft een healthcheck-grace-period van >10s na recreate | Health check loop wacht max 10s. Als een service stiller-start, false negative. Mitigation: configureerbaar maken of per-service uitgebreid (out-of-scope follow-up). Voor caddy/alloy/searxng/vexa is 10s ruim voldoende. |

## Implementation order

1. SPEC artefacten schrijven (deze + plan + acceptance + research).
2. Refactor `deploy-compose.yml`:
   a. Helper function definiëren bovenin het ssh-action script (na
      `set -e`).
   b. Bestaande inline caddy-block vervangen door
      `sync_and_recreate caddy ...` call.
   c. Drie nieuwe calls voor alloy, searxng, runtime-api toevoegen.
   d. Paths-trigger en sparse-checkout uitbreiden met de drie nieuwe
      paths.
3. Update `infra/deploy.md` met de nieuwe sectie + checklist + bind-
   mount inventaris.
4. Lokale YAML lint, Markdown review.
5. Open PR. CI runs:
   a. `caddy / build-push` — niet (geen Dockerfile change)
   b. `Validate Caddyfile / validate` — niet (Caddyfile niet aangeraakt
      in deze PR)
   c. `Sync docker-compose.yml...` — niet pre-merge (push-only)
6. Admin merge na review.
7. Post-merge verificatie:
   a. `gh run list --workflow deploy-compose.yml -L 1` → success.
   b. 3-way sha256 match per service:
      - main HEAD vs `/opt/klai/<svc>/<file>` vs container's view.
   c. Workflow log inspect: viermaal `<svc> config unchanged;
      skipping recreate.` (idempotent eerste run) of `<svc> is running
      (attempt N)` (recreate-and-recover).

Zie `plan.md` voor exacte file-edits en `acceptance.md` voor de
verificatie-procedure per service.
