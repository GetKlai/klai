---
id: SPEC-INFRA-CADDY-CONFIG-DEPLOY-001
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
| 0.1.0 | 2026-05-07 | MoAI | Initial. Closes the Caddyfile sync gap discovered during SPEC-MCP-AUTH-001. |

# SPEC-INFRA-CADDY-CONFIG-DEPLOY-001 — Sluit de Caddyfile sync gap

## Overview

Vandaag tijdens SPEC-MCP-AUTH-001 ontdekt: `.github/workflows/caddy.yml`
rebuildt de `caddy-hetzner` Docker image bij wijzigingen onder
`deploy/caddy/**` en recreate de container, maar geen workflow synct
`deploy/caddy/Caddyfile` naar de bind-mount source op
`/opt/klai/caddy/Caddyfile`. De Dockerfile heeft geen `COPY Caddyfile`
— de file zit puur als bind-mount in de runtime. Resultaat: een
Caddyfile-wijziging die naar `main` merget bereikt nooit de draaiende
Caddy-container, totdat iemand het bestand handmatig scp't. Dat is
gebeurd vanmiddag en het schendt de just-gecodificeerde
`no-docker-cp-for-permanent-fixes` regel.

Deze SPEC sluit het gat met **één PR**, geen multi-phase rollout.
Aanpak: external config + sync, identiek aan de bestaande Grafana
provisioning sync in `deploy-compose.yml` (SPEC-OBS-001 Phase C). Image
rebuild blijft bij `caddy.yml` voor binary-changes; config-sync gaat
naar `deploy-compose.yml` voor Caddyfile-only changes.

Tenant Caddyfiles (`caddy/tenants/*.caddyfile`, runtime geschreven door
portal-api `_write_tenant_caddyfile()` naar de `caddy-tenants` Docker
named volume) blijven **out-of-scope**. Hun volume + restart-on-write
mechanisme is autonoom en werkt — mengen vergroot blast radius zonder
ROI.

## Environment

- **Affected workflow files:**
  - `.github/workflows/caddy.yml` — paths-trigger reduce (Caddyfile uit
    de set; alleen Dockerfile/build.sh/.trivyignore.yaml + workflow zelf
    triggeren image rebuild).
  - `.github/workflows/deploy-compose.yml` — paths-trigger uitbreiden
    met `deploy/caddy/Caddyfile`; sparse-checkout uitbreiden; rsync +
    content-aware recreate-block toevoegen, identiek patroon aan de
    bestaande grafana provisioning sync.
  - `.github/workflows/caddy-validate.yml` — NIEUW. PR-trigger op
    `deploy/caddy/**`; runt `caddy validate` in een
    `ghcr.io/getklai/caddy-hetzner:latest` container met de Caddyfile
    bind-gemount. Faalt op syntax errors vóór merge.

- **Affected files (none modified, only referenced):**
  - `deploy/caddy/Caddyfile` — wordt voortaan via `deploy-compose.yml`
    gesynct; geen wijziging aan inhoud zelf in deze SPEC.
  - `deploy/caddy/Dockerfile` — ongewijzigd; image rebuild keten blijft
    in `caddy.yml`.
  - `klai-portal/backend/app/services/provisioning/infrastructure.py`
    `_write_tenant_caddyfile` + `_reload_caddy` — ongewijzigd. Tenant
    Caddyfile mechanisme valt buiten scope.
  - `deploy/scripts/compose-up.sh` (klai-infra) — ongewijzigd. SPEC
    gebruikt direct `docker compose up -d --force-recreate caddy` voor
    de bind-mount-content-only recreate path, identiek aan
    grafana-precedent. `compose-up.sh` blijft de standaard voor de
    service-definition path elders in `caddy.yml`.

- **Affected production paths on core-01:**
  - `/opt/klai/caddy/Caddyfile` — bind-mount source. Wordt voortaan
    automatisch door CI bijgewerkt.

## Assumptions

- A1: De Grafana provisioning sync in `deploy-compose.yml` (SPEC-OBS-001
  Phase C) werkt productie-stabiel sinds april 2026 en is daarmee een
  bewezen patroon om te kopiëren.
- A2: Caddy's `admin off` in de Caddyfile blijft beleid. Daardoor is
  `caddy reload --config` via Admin API geen optie; container-recreate
  (~1s TLS-onderbreking) is de enige reload-route. Die is acceptabel
  bij Klai's huidige schaal — bevestigd in de comment van `_reload_caddy`.
- A3: De Caddy image bevat `caddy validate` als CLI-subcommand, en
  validatie tegen een Caddyfile met ongeresolvde `{$ADMIN_EMAIL}` /
  `{$DOMAIN}` env-vars werkt (vars worden tot lege strings of
  ge-injecteerde dummy-vars).
- A4: De `import /etc/caddy/tenants/*.caddyfile` directive met een
  glob die niets matcht (lege `tenants/` dir bij validate) is in
  Caddy 2.x silently OK — niet een validate-fout. Komt overeen met
  Caddy 2 docs.
- A5: `compose-up.sh` (REQ-3 in SPEC-INFRA-CONTAINER-HYGIENE-001) is
  geïnstalleerd op core-01 en blijft de standaard voor service-deploys.
  Deze SPEC bypassed het alleen voor de `--force-recreate` pad in
  `deploy-compose.yml`, identiek aan grafana, niet in `caddy.yml`.
- A6: `ghcr.io/getklai/caddy-hetzner:latest` blijft beschikbaar als
  validatie-image. De PR-validate workflow heeft `packages: read` permissie
  om die te pullen.

## Requirements

### R1 — Ubiquitous: `deploy/caddy/Caddyfile` veranderingen syncen automatisch naar `/opt/klai/caddy/Caddyfile`

WHEN een commit naar `main` wijzigingen bevat aan `deploy/caddy/Caddyfile`,
THEN binnen één GitHub Actions run:

1. `.github/workflows/deploy-compose.yml` SHALL triggeren.
2. Het workflow SHALL via `git sparse-checkout` op core-01 het
   bijgewerkte bestand binnenhalen samen met de bestaande compose +
   grafana provisioning paden.
3. `rsync -ac --itemize-changes deploy/caddy/Caddyfile
   /opt/klai/caddy/Caddyfile` SHALL het bestand bijwerken EN de set van
   gewijzigde files rapporteren via stdout.
4. Indien het rsync diff niet-leeg is, SHALL de workflow
   `docker compose --project-directory /opt/klai up -d --force-recreate caddy`
   uitvoeren om de container te recreaten met de nieuwe Caddyfile.
5. Indien het rsync diff leeg is (de gesyncte file matched al; gebeurt
   bij workflow_dispatch zonder content-change), SHALL de workflow
   `up -d` zónder `--force-recreate` runnen (no-op voor
   bind-mount-only path).

### R2 — Ubiquitous: Image-rebuild blijft gescheiden van config-sync

`.github/workflows/caddy.yml` SHALL na deze SPEC alléén triggeren op
binary- of image-relevante wijzigingen:

```yaml
paths:
  - 'deploy/caddy/Dockerfile'
  - 'deploy/caddy/build.sh'
  - 'deploy/caddy/.trivyignore.yaml'
  - '.github/workflows/caddy.yml'
```

`deploy/caddy/Caddyfile` SHALL NIET in deze paths-set staan. Resultaat:
een Caddyfile-only PR triggert geen image rebuild (5-10 min besparing
per config-edit) en de image push frequency daalt naar de werkelijke
binary-upgrade cadans (Renovate driven).

### R3 — Event-driven: PR-trigger pre-merge Caddyfile validatie

WHEN een Pull Request files onder `deploy/caddy/**` raakt, THEN
`.github/workflows/caddy-validate.yml` SHALL automatisch draaien en:

1. De Caddyfile bind-mounten in een
   `ghcr.io/getklai/caddy-hetzner:latest` container.
2. `caddy validate --adapter caddyfile --config /etc/caddy/Caddyfile`
   uitvoeren met dummy `ADMIN_EMAIL=ci@example.com` en
   `DOMAIN=example.com` env-vars.
3. Een lege `/etc/caddy/tenants` directory mounten zodat de
   `import /etc/caddy/tenants/*.caddyfile` glob-resolve geen onverwacht
   bestand inleest.
4. Bij niet-zero exit-code SHALL de PR-status `failed` zijn (vóór
   merge). Bij zero exit-code SHALL de status `success` zijn.

Deze workflow vangt 99% van syntax fouten vóór merge en daarmee voordat
de sync uit R1 ze naar productie kan brengen.

### R4 — Ubiquitous: Post-recreate health check

NA de `--force-recreate caddy` actie uit R1, SHALL de workflow:

1. Tot 5 keer met 2 seconden interval `docker compose ... ps caddy` runnen
   en de `State` veld inspecteren.
2. Op `running` (of, indien een healthcheck gedefinieerd is, `healthy`)
   doorgaan naar de volgende stap.
3. Indien na 10 seconden de container niet `running` is, SHALL de
   workflow `::error::` uitsturen, de laatste 50 caddy log-regels
   dumpen via `docker compose ... logs --tail 50 caddy`, en met
   exit-code 1 falen.

Een gefaalde health check in CI is het signaal voor de operator om een
`git revert` te triggeren — de volgende workflow-run rsynct dan de
oude Caddyfile terug en force-recreates Caddy opnieuw.

### R5 — Out-of-scope: tenant Caddyfile mechanisme

Tenant Caddyfiles (`*.caddyfile` in de `caddy-tenants` Docker named
volume, runtime geschreven door
`klai-portal/backend/app/services/provisioning/infrastructure.py
::_write_tenant_caddyfile`) SHALL NIET door deze SPEC geraakt worden.
Het bestaande `_reload_caddy()` (container restart, geen Admin API)
blijft de canonieke reload na tenant-provisioning. Bestaande tests
(`test_writes_caddyfile_with_correct_content`, `test_caddy_lock_*`,
`test_restarts_caddy_container`) SHALL groen blijven.

Argumentatie: tenant Caddyfiles zijn dynamisch (per tenant, runtime),
volume-gemount en daarmee architectuur-incompatibel met een file-sync
patroon dat naar `main` gepushed wordt. Mengen vergroot blast radius
zonder ROI.

## Non-Goals

- Caddy upgrade naar 2.12+ (separate Renovate-PR).
- Admin-API enable + `caddy reload` (separate veiligheidsanalyse;
  expose van het admin-vlak is een security-vraag, geen deploy-vraag).
- Tenant Caddyfile sync mechanisme (zie R5).
- Generieke vervanging van bind-mounts door image-baked configs voor
  andere services (nvt; dit is een Caddy-specifieke fix omdat Caddy
  de enige service is met deze gap).
- Health check observability beyond CI (geen Grafana-alert voor
  "caddy failed to recreate"; bestaande Caddy uptime alerts in Kuma
  vangen runtime fouten).

## Risks

| Risk | Mitigation |
|---|---|
| `caddy validate` weigert config met ongeresolvde env vars | R3 injecteert dummy env-vars `ADMIN_EMAIL=ci@example.com` + `DOMAIN=example.com`. Test in eerste run; bij regression alternatief `caddy adapt`. |
| `--force-recreate caddy` causes ~1s TLS-onderbreking op productie | Acceptabel per A2 + bestaand precedent in `_reload_caddy()`. Identiek aan tenant-provisioning runtime gedrag. |
| Race tussen `caddy.yml` (image rebuild) en `deploy-compose.yml` (config sync) als beiden tegelijk triggeren | Beide eindigen op `up -d` resp. `up -d --force-recreate caddy`; volgorde maakt niet uit, laatste wint. Maximaal twee opeenvolgende ~1s downtimes — negligible. |
| Een `:latest` tag drift tussen validate-image en deploy-image | `caddy.yml` post-merge bouwt `:latest`; volgende validate-PR gebruikt die. Drift-window is één PR. Klein risico geaccepteerd. |
| Glob `import /etc/caddy/tenants/*.caddyfile` faalt validate met lege dir | R3 mount expliciet een lege tmp-dir om de import te resolven naar 0 matches; Caddy 2 silently OK per A4. |
| Health check timeout te kort (10s) bij trage core-01 | Bij regressie: configureer `for i in 1..10; sleep 2` in R4, totaal 20s. SPEC laat 10s als initial; bij PR review aanpassen indien nodig. |

## Implementation order

1. Schrijf SPEC artefacten (deze + plan + acceptance + research).
2. Implementeer in deze branch:
   a. Nieuwe `.github/workflows/caddy-validate.yml`.
   b. Edit `.github/workflows/caddy.yml` (paths reduce).
   c. Edit `.github/workflows/deploy-compose.yml` (paths add + sparse-checkout uitbreiden + rsync block + health check).
3. Open PR. CI runs:
   a. `caddy-validate.yml` runt op deze PR (zelf-test van R3) en moet groen worden.
   b. `caddy.yml` runt NIET (paths matched niet).
   c. `deploy-compose.yml` runt NIET op PR (alleen op push to main); manueel post-merge te verifiëren.
4. Post-merge verificatie: trigger een no-op Caddyfile change (bijv. een comment), verifieer A1-A7 acceptance criteria.

Zie `plan.md` voor exacte file-edits en `acceptance.md` voor de
verificatieprocedure.
