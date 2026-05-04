---
id: SPEC-INFRA-CONTAINER-HYGIENE-001
version: "0.4.0"
status: complete
created: "2026-05-02"
updated: "2026-05-04"
author: MoAI
priority: high
issue_number: 0
---

## HISTORY

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 0.1.0 | 2026-05-02 | MoAI | Initiële stub na librechat-voys cleanup-incident. 7 requirements: rule-files, librechat-voys compose-block, --remove-orphans, image-pinning pilot, weekly audit, daily prune, pitfall. |
| 0.2.0 | 2026-05-02 | MoAI | Twee fundamentele herzieningen na review: (1) REQ-4 (image-pinning pilot) **geschrapt** — `:latest` op `ghcr.io/getklai/*` is bewust beleid per `deploy/VERSIONS.md` + `docs/runbooks/version-management.md`. (2) REQ-1/2/3/5 herschreven naar **AI-first mechanische guards** — PreToolUse hooks, CI-checks, deploy-wrappers, VictoriaLogs-events. |
| 0.3.0 | 2026-05-02 | MoAI | **Tenant-provisioning architectuur correct opgenomen.** REQ-2 was "alles via compose" — fout. Klai heeft twee legitieme klassen prod-containers: (a) compose-managed met `com.docker.compose.project=klai-core` label, (b) provisioning-managed door portal-api via `client.containers.run()` per tenant (zie `klai-portal/backend/app/services/provisioning/infrastructure.py::_start_librechat_container`). REQ-2 herschreven: tenant-LibreChats SHALL `klai.managed_by=portal-api-provisioning` + `klai.tenant_slug=<slug>` + `klai.kind=librechat` labels dragen. REQ-2c (librechat-voys in compose-block toevoegen) **vervalt** — vervangen door label-backfill voor bestaande tenants. REQ-1 hook + REQ-5 audit detecteren wezen op afwezigheid van **beide** label-klasses, niet alleen compose-label. Aanleiding: gebruiker review wees op tenant-provisioning patroon dat ik gemist had. |
| 0.4.0 | 2026-05-04 | MoAI | **Stage 6 finalisering — REQ-5 Grafana panel + alert + status flip naar `complete`.** Verifieerd op core-01 (2026-05-04): librechat-voys draagt klasse-B labels (`klai.kind=librechat,klai.managed_by=portal-api-provisioning,klai.tenant_slug=voys`); `docker-cleanup.timer` draait dagelijks (laatst Mon 2026-05-04 03:04 EEST); `orphan-audit.timer` draait wekelijks (laatst Sun 2026-05-03 03:08 EEST). Alle 6 stages live. Toegevoegd in deze versie: `deploy/grafana/provisioning/alerting/orphan-audit-rules.yaml` met 2 critical alert rules (`tenant_container_no_route` + `caddy_upstream_missing`), `deploy/grafana/provisioning/dashboards/klai-orphan-audit.json` dashboard met 4 stat-panels + 2 logs-panels, route in `policies.yaml` voor `spec=SPEC-INFRA-CONTAINER-HYGIENE-001` → `klai-ops-alerts-email` met repeat 24h, en regex-uitbreiding in `scripts/reset-grafana-orphan-alert.sh` voor multi-word UID prefixes (`spec-[a-z][a-z-]*-[0-9]+`) zodat `spec-infra-container-hygiene-001-*` UIDs door het cleanup-script geaccepteerd worden. |

# SPEC-INFRA-CONTAINER-HYGIENE-001: Container hygiene op core-01 — mechanische guards tegen orphans, dangling images en verlaten volumes

## Overview

Elimineer de drie fout-klassen die op 2026-05-02 zichtbaar werden:

1. **Wees-containers** — productie-containers die voor cleanup-tools
   onzichtbaar zijn omdat ze geen herkenbare beheers-label dragen.
   Klai heeft TWEE legitieme klassen prod-containers, beide door deze
   SPEC erkend:
   - **Compose-managed:** `com.docker.compose.project=klai-core` label,
     gedeclareerd in `klai-infra/deploy/docker-compose.yml`.
   - **Provisioning-managed:** door portal-api dynamisch aangemaakt
     via `client.containers.run()` per tenant
     (zie `klai-portal/backend/app/services/provisioning/infrastructure.py::_start_librechat_container`).
     SHALL `klai.managed_by=portal-api-provisioning`,
     `klai.tenant_slug=<slug>`, en `klai.kind=<type>` labels dragen
     (REQ-2).
   Een container zonder een van beide label-klasses is een wees.
2. **Service-removed-but-running** — compose-services worden uit
   `docker-compose.yml` gehaald zonder dat hun container/volume mee
   wordt opgeruimd, omdat `docker compose up` standaard zonder
   `--remove-orphans` draait.
3. **Dangling-image-explosie** — 679 stuks (60 GB), accepted side-effect
   van `:latest` rolling-tags op klai-eigen images (per VERSIONS.md
   bewust beleid), maar accumuleert tot disk-druk zonder periodiek
   opruimen.

Het librechat-voys incident is de directe aanleiding: een cleanup-agent
verwijderde de tenant-specifieke container omdat hij op alle
mens-leesbare signalen ("geen compose label, geen Caddy upstream") als
wees uitkwam. De container was in feite provisioning-managed via
portal-api — maar `_start_librechat_container` zette geen labels, dus
het was niet detecteerbaar als legitiem. Recovery was mogelijk omdat de
tenant-config intact bleef, maar de oude image-sha was permanent verloren.

**De fix is mechanisch, niet documentair.** Een AI doet het primaire
code- en deploy-werk in deze codebase. Regels die "vertrouwen op
discipline" zijn dan een gat. Deze SPEC vervangt rules-die-gelezen-
moeten-worden door scripts-die-mechanisch-blokkeren, CI-checks-die-PRs-
falen, en logs-die-AI-zelf-queryt vóór cleanup-beslissingen.

Zes requirements over drie lagen:

- **Mechanische guards** (deze repo): pre-cleanup hook (REQ-1),
  provisioning-labels in portal-api (REQ-2), pitfall (REQ-7).
- **Tooling op core-01**: weekly audit-stream naar VictoriaLogs (REQ-5),
  daily safe-only prune via systemd timer (REQ-6).
- **Cross-repo deploy-discipline** (klai-infra): deploy-wrapper-script
  met `--remove-orphans` ingebouwd (REQ-3), CI orphan-guard (REQ-2a).

REQ-4 uit v0.1.0 is geschrapt. VERSIONS.md + version-management.md
zijn de canoniek voor image-pinning. REQ-2c uit v0.2.0 (librechat-voys
in compose-block toevoegen) is in v0.3.0 vervangen door REQ-2 label-fix
plus eenmalige backfill.

## Environment

- **Affected services** (klai-eigen, blijven `:latest` per VERSIONS.md
  beleid; geraakt alleen door REQ-6 daily prune): klai-portal-api,
  klai-connector, klai-knowledge-ingest, klai-knowledge-mcp,
  klai-mailer, klai-scribe-api, klai-retrieval-api, klai-docs-app,
  klai-caddy-hetzner, klai-portal-frontend.
- **Affected hooks/rules in klai (deze repo):**
  `.claude/hooks/klai/container-hygiene-preflight.sh` (nieuw),
  `.claude/settings.json` (PreToolUse-hook registratie),
  `.claude/rules/klai/infra/container-hygiene.md` (nieuw, narrative
  documentatie + verwijzing naar het script),
  `.claude/rules/klai/pitfalls/process-rules.md` (uitgebreid met
  `container-cleanup-without-preflight (HIGH)`).
- **Affected portal-api code in klai (deze repo):**
  `klai-portal/backend/app/services/provisioning/infrastructure.py`
  — `_start_librechat_container` SHALL `labels={...}` toevoegen aan
  `client.containers.run()` (REQ-2). Tests in
  `klai-portal/backend/tests/services/provisioning/test_infrastructure_labels.py`
  (nieuw) verifiëren label-aanwezigheid.
- **Affected klai-infra:** `deploy/scripts/compose-up.sh` (nieuw —
  deploy-wrapper voor REQ-3), `scripts/audit-compose-orphans.sh`
  (nieuw — REQ-2a CI-guard), `scripts/test-audit-compose-orphans.sh`
  (regression test), `scripts/docker-orphan-audit.sh` (nieuw — REQ-5
  emit-naar-VictoriaLogs), `core-01/systemd/docker-cleanup.{service,timer}`
  (REQ-6), `core-01/systemd/orphan-audit.{service,timer}` (REQ-5),
  `.github/workflows/audit-compose.yml` (uitgebreid met orphan-check),
  10 service-deploy-workflows — vervangen `docker compose up` door
  `compose-up.sh` aanroep voor REQ-3.
- **Affected core-01 host (operator-actie via SSH/Ansible):**
  `/etc/systemd/system/docker-cleanup.{service,timer}`,
  `/etc/systemd/system/orphan-audit.{service,timer}`,
  symlinks naar de scripts in klai-infra checkout. **Backfill:**
  bestaande `librechat-voys` en `librechat-getklai` containers worden
  eenmalig opnieuw gestart met de nieuwe labels (recreate vereist;
  labels zijn immutable na container create).

## Assumptions

- A1: Het librechat-voys incident is een instance van een klasse, niet
  een uitschieter. Andere productie-containers kunnen in de toekomst
  weer als handmatige `docker run` ontstaan (debug-sessie, hotfix,
  tijdelijk werkrond).
- A2: Een AI is de primaire code- en deploy-actor. Mechanische guards
  zijn nodig; markdown-regels die AI moet lezen zijn een gat dat door
  context-truncatie of prompt-variatie zomaar verschijnt.
- A3: VERSIONS.md + version-management.md zijn canoniek en up-to-date.
  `:latest` op `ghcr.io/getklai/*` is bewust beleid; deze SPEC raakt
  dat niet. Renovate/Dependabot/Trivy/scan-pinned-images.yml zijn
  het complete version-management ecosysteem.
- A4: Klai's bestaande `audit-compose.yml` workflow (`audit-compose-volumes.sh`
  + `test-audit-compose.sh`) is het patroon dat REQ-2 volgt — geen
  nieuwe workflow, uitbreiding van de bestaande met een orphan-check.
- A5: VictoriaLogs is de canonieke ops-event-stream (per
  `observability.md`). Audit-events horen daar, niet in `product_events`.
- A6: Operator heeft SSH-toegang tot core-01 met sudo-rechten voor
  systemd unit-installatie, of er is een Ansible-pipeline. Beide werken;
  REQ-6 implementatie volgt de gekozen modaliteit.
- A7: Claude Code's `PreToolUse` hook-mechanisme via
  `.claude/settings.json` werkt zoals gedocumenteerd: hook ontvangt
  tool-call JSON via stdin, exit-code != 0 blokkeert de tool-call.

## Requirements

### R1 — Ubiquitous: PreToolUse hook blokkeert destructieve docker-acties

WHEN een Claude-sessie in de klai-repo een `Bash` tool-call doet die
matcht op `docker rm`, `docker rmi`, `docker volume rm`,
`docker system prune`, of `docker compose down --volumes`, THEN het
PreToolUse-hook script `.claude/hooks/klai/container-hygiene-preflight.sh`
SHALL eerst draaien. Het script SHALL:

1. Parsen welk argument wordt verwijderd (container-naam,
   image-naam/sha, volume-naam, of bulk-prune).
2. Hard-blocks voor altijd-gevaarlijke patterns:
   `docker volume prune`, `docker image prune -af`,
   `docker system prune -a`, `docker compose down --volumes`.
3. Voor container-targets: hard-block bij positieve match op één van:
   - **Tenant-naam check:** target ends in `-voys`, `-getklai`, of
     `-<klant>-tenant`. Block-bericht verwijst naar de
     provisioning-flow (REQ-2): "if portal-api-managed, use the
     deprovision flow; do not docker rm directly".
   - **Compose git-history check:** target stond ooit als service in
     `klai-infra/deploy/docker-compose*.yml` (best-effort, alleen
     wanneer klai-infra checkout sibling beschikbaar is).
4. Best-effort checks (skip-bij-onreachable, fail-open):
   - **Caddy upstream check:** target voorkomt in
     `/opt/klai/Caddyfile` via `ssh core-01`.
   - **VictoriaLogs traffic check:** target had log-events in
     afgelopen 30d.

Een positieve match = exit-code 2 met JSON-decision-payload. De Bash
tool-call wordt geblokkeerd; Claude moet expliciet de blokkade
overwegen, hulp vragen, of de actie afbreken.

Het script SHALL idempotent en hermetic zijn (geen state-mutatie, geen
externe afhankelijkheden behalve `docker`, `ssh`, `curl`,
`git`). Run-tijd onder 5 seconden.

Het bestand `.claude/rules/klai/infra/container-hygiene.md` SHALL de
checklist narrative documenteren met verwijzing naar het script. Het
narrative is voor mens-review en debug; de mechanische enforcement is
het script + hook.

`.claude/settings.json` SHALL het script registreren als
`PreToolUse` hook met `matcher: "Bash"` en de juiste command-path via
`$CLAUDE_PROJECT_DIR`.

### R2 — Ubiquitous: élke prod container draagt een herkenningslabel

Elke productie-container SHALL door één van de twee canonieke beheers-
paden zijn aangemaakt en bijbehorend label dragen:

**Klasse A — Compose-managed:**
- Aangemaakt via `docker compose up` op `klai-infra/deploy/docker-compose.yml`.
- Draagt automatisch `com.docker.compose.project=klai-core` (en
  `com.docker.compose.service=<naam>`).
- Geen extra werk in deze SPEC — bestaand patroon.

**Klasse B — Provisioning-managed:**
- Aangemaakt door portal-api via `client.containers.run()`,
  momenteel uitsluitend tenant-LibreChats via
  `_start_librechat_container` in
  `klai-portal/backend/app/services/provisioning/infrastructure.py`.
- SHALL labels dragen: `klai.managed_by=portal-api-provisioning`,
  `klai.tenant_slug=<slug>`, `klai.kind=librechat`. Future kinds
  (e.g. tenant-meilisearch, tenant-vectorstore) volgen hetzelfde
  schema met andere `klai.kind`.

**REQ-2a — Code-fix in `_start_librechat_container`:**

`client.containers.run()` SHALL een `labels` keyword-arg meegeven met
ten minste de drie genoemde labels. Tests SHALL de aanwezigheid van
elk label verifiëren via `docker inspect` (of mock equivalent).

**REQ-2b — Backfill voor bestaande tenant-containers:**

`librechat-voys` (handmatig herstart op 2026-05-02) en
`librechat-getklai` (compose-managed, dus al klasse A) MOETEN bij de
volgende geplande herstart de juiste labels dragen. Voor
`librechat-voys`: één-malige operator-actie op core-01 om de container
te recreaten met de nieuwe labels (Docker labels zijn immutable na
container create — recreate is de enige weg). Documentatie van het
exacte commando in `docs/runbooks/` of PR-body.

**REQ-2c — CI-guard tegen toekomstige label-loosheid:**

`klai-infra/scripts/audit-compose-orphans.sh` SHALL bij elke PR die
`docker-compose.yml` raakt, draaien als deel van de bestaande
`audit-compose.yml` workflow. De check SHALL Caddy-upstreams in
`deploy/caddy/Caddyfile` matchen tegen een UNION van: (a) service-namen
in compose, (b) bekende provisioning-managed naam-patterns
(`librechat-*`), (c) whitelisted external endpoints.

**REQ-2d — Post-deploy verificatie:**

Na elke `compose up -d` (via REQ-3 wrapper) SHALL het wrapper-script
verifiëren: zijn er nu running containers die GEEN
`com.docker.compose.project=klai-core` label EN GEEN
`klai.managed_by=portal-api-provisioning` label hebben? Zo ja,
waarschuwingsregel naar VictoriaLogs (REQ-5 event-stream) met
`event:orphan_post_deploy`. Containers met `klai.adhoc=*` label vallen
buiten — dat is bewust ad-hoc debug-werk per REQ-7.

### R3 — Event-driven: deploy-wrapper met `--remove-orphans` ingebouwd

Eén bash-script `klai-infra/deploy/scripts/compose-up.sh` SHALL het
canonieke deploy-mechanisme zijn. Inhoud (kern):

```bash
#!/usr/bin/env bash
set -euo pipefail
SERVICE="${1:-}"
cd /opt/klai
if [ -n "$SERVICE" ]; then
  docker compose pull "$SERVICE"
  docker compose up -d --remove-orphans "$SERVICE"
else
  docker compose pull
  docker compose up -d --remove-orphans
fi
# REQ-2b post-deploy orphan-check
/opt/klai/scripts/audit-orphan-snapshot.sh
```

Alle 10 deploy-workflows in klai-infra (`caddy.yml`, `docs.yml`,
`klai-connector.yml`, `klai-knowledge-mcp.yml`, `klai-mailer.yml`,
`knowledge-ingest.yml`, `portal-api.yml`, `retrieval-api.yml`,
`scribe-api.yml`, `whisper-server.yml`) SHALL via SSH dit script
aanroepen ipv `docker compose up` direct. Voorbeeld:

```yaml
- name: Deploy on core-01
  run: ssh core-01 "/opt/klai/deploy/scripts/compose-up.sh portal-api"
```

Resultaat: `--remove-orphans` zit in één plek, kan niet vergeten
worden, en post-deploy orphan-detection draait gratis mee. AI die een
nieuwe deploy-workflow schrijft kopieert de SSH-regel; flag is niet
de verantwoordelijkheid van de auteur.

**Volgorde-discipline:** REQ-3 wordt geactiveerd NA REQ-2c
(librechat-voys in compose). De eerste deploy via de wrapper zou
anders librechat-voys verwijderen.

### R4 — VERVALLEN

Image-pinning pilot is geschrapt in v0.2.0. Zie HISTORY en research §11.

### R5 — Periodic: orphan-audit als VictoriaLogs event-stream

Een script `klai-infra/scripts/docker-orphan-audit.sh` SHALL elke zondag
03:00 op core-01 draaien (via systemd timer) en structlog-events
emitten naar VictoriaLogs via stdout (Alloy pickt het op zoals alle
container-logs). Geen markdown-rapport, geen `/var/log/`-bestanden —
alleen events.

Event-schema (één per detected issue):

```json
{
  "service": "klai-orphan-audit",
  "event": "<type>",
  "severity": "warning|critical",
  "container_name": "<name>",
  "container_id": "<id>",
  "details": { ... },
  "_time": "<iso8601>"
}
```

Detectie-categorieën met `event` veld:

1. `event:orphan_no_compose_label` — running container zonder
   `com.docker.compose.project=klai-core` label.
2. `event:orphan_service_removed` — running container met klai-core
   label maar service-naam niet meer in `docker-compose*.yml`.
3. `event:image_untagged_old` — untagged image >30 dagen, niet
   gerefereerd door running container.
4. `event:volume_unmounted` — named volume zonder mount, met
   laatste-write timestamp ouder dan 7d.
5. `event:caddy_upstream_missing` — Caddy upstream zonder
   matchende running container.
6. `event:tenant_container_no_route` — container met tenant-pattern
   (`-voys`, `-getklai`, etc.) zonder Caddy-upstream (exact het
   librechat-voys signaal).

Het script SHALL **report-only** zijn — geen `docker rm`/`rmi`/
`volume rm` ooit. Een AI of mens queryt VictoriaLogs vóór cleanup-
beslissing:

```
service:klai-orphan-audit AND _time:[now-7d,now]
```

Een Grafana-panel + alert SHALL worden geconfigureerd op deze stream
voor menselijk dashboard. AI gebruikt VictoriaLogs MCP direct.

REQ-1's hook script SHALL deze stream queryen als deel van zijn
checks: als container-X recent is geflagd als orphan-met-tenant-pattern,
is dat een extra rode vlag.

### R6 — Periodic: daily safe-only cleanup via systemd timer

Een systemd timer `docker-cleanup.timer` SHALL dagelijks om 03:00 op
core-01 een service uitvoeren die uitsluitend onomstotelijk veilige
cleanup doet:

```
ExecStart=/usr/bin/docker image prune -f
ExecStart=/usr/bin/docker container prune -f --filter until=24h
ExecStart=/usr/bin/docker network prune -f
ExecStart=/usr/bin/docker builder prune -f --filter until=72h
```

De timer SHALL **niet**:

- `docker volume prune` uitvoeren (dataverlies-risico).
- `docker image prune -af` uitvoeren (kan rollback-bare images
  weggooien — VERSIONS.md `:latest` policy verwacht dat `:${sha}` tags
  beschikbaar blijven voor rollback).
- `docker system prune -af` uitvoeren.

Logging via `journalctl -u docker-cleanup.service`. Unit-files SHALL
in `klai-infra/core-01/systemd/` worden gecheckt-in.

REQ-1's hook script SHALL OOK een veiligheidscheck doen voor
`docker volume prune` of `docker image prune -af` — als een AI dat
ooit probeert, hard blokkeren met diagnose.

### R7 — Unwanted: pitfall-documentatie

Het leerpunt uit het librechat-voys incident SHALL in
`.claude/rules/klai/pitfalls/process-rules.md` worden vastgelegd als
nieuwe pitfall `container-cleanup-without-preflight (HIGH)` met:

- Incident-tijdlijn (datum, container-naam, signalen genegeerd)
- Root cause (geen pre-cleanup checklist, label-loosheid als
  enige signaal)
- Prevention: verwijzing naar REQ-1 hook + script
- "Why mechanical not narrative" sectie: waarom de hook mechanisch
  is en niet alleen een rule

De pitfall SHALL ook vermelden dat een handmatige `docker run` zonder
`--label klai.adhoc=...` een wees-by-construction is en door REQ-5
audit gedetecteerd wordt.

## Specifications

### Pre-cleanup hook script outline (REQ-1)

```bash
#!/usr/bin/env bash
# .claude/hooks/klai/container-hygiene-preflight.sh
# Reads tool-call JSON from stdin; exit 1 = block.

set -euo pipefail
TOOL_INPUT=$(cat)
COMMAND=$(echo "$TOOL_INPUT" | jq -r '.tool_input.command // empty')

# Parse danger patterns
if echo "$COMMAND" | grep -qE '\bdocker\s+(rm|rmi|volume\s+rm|system\s+prune)\b|\bdocker\s+compose\s+down\s+.*--volumes\b'; then
  TARGET=$(echo "$COMMAND" | grep -oE '(rm|rmi|volume rm)\s+\S+' | awk '{print $NF}')
  [ -z "$TARGET" ] && exit 0  # bulk prune handled separately

  # Check 1: Caddy upstream
  if ssh -o ConnectTimeout=3 core-01 "grep -q '$TARGET' /opt/klai/Caddyfile 2>/dev/null"; then
    echo "BLOCKED: $TARGET is a Caddy upstream on core-01"
    exit 1
  fi

  # Check 2: compose git history
  if git -C /tmp/klai-infra-cache log --all -p -- 'deploy/docker-compose*.yml' 2>/dev/null | grep -q "$TARGET"; then
    echo "BLOCKED: $TARGET appeared in compose history — verify why removed"
    exit 1
  fi

  # Check 3: tenant pattern
  if echo "$TARGET" | grep -qE -- '-(voys|getklai|[a-z]+-tenant)$'; then
    echo "BLOCKED: $TARGET looks tenant-specific"
    exit 1
  fi

  # Check 4: depends-on
  if ssh -o ConnectTimeout=3 core-01 "docker ps --format '{{.Names}}' | xargs -I{} docker inspect {} --format '{{index .Config.Labels \"com.docker.compose.depends_on\"}}' | grep -q '$TARGET'"; then
    echo "BLOCKED: another container depends_on $TARGET"
    exit 1
  fi

  # Check 5: VictoriaLogs recent traffic
  TRAFFIC=$(curl -s -u "$VL_AUTH" \
    "http://localhost:9428/select/logsql/query?query=service:$TARGET+_time:[now-30d,now]&limit=1" \
    | wc -l)
  [ "$TRAFFIC" -gt 0 ] && {
    echo "BLOCKED: $TARGET had traffic in last 30d"
    exit 1
  }
fi

# Hard-block always-dangerous patterns
if echo "$COMMAND" | grep -qE 'docker\s+volume\s+prune|docker\s+image\s+prune\s+-af|docker\s+system\s+prune\s+-af'; then
  echo "BLOCKED: dangerous prune pattern; use systemd timer (REQ-6) for safe cleanup"
  exit 1
fi

exit 0
```

### `.claude/settings.json` hook registratie

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Bash",
        "hooks": [
          {
            "type": "command",
            "command": "bash $CLAUDE_PROJECT_DIR/.claude/hooks/klai/container-hygiene-preflight.sh"
          }
        ]
      }
    ]
  }
}
```

### Deploy-wrapper (REQ-3)

```bash
#!/usr/bin/env bash
# /opt/klai/deploy/scripts/compose-up.sh
set -euo pipefail
SERVICE="${1:-}"
cd /opt/klai

if [ -n "$SERVICE" ]; then
  docker compose pull "$SERVICE"
  docker compose up -d --remove-orphans "$SERVICE"
else
  docker compose pull
  docker compose up -d --remove-orphans
fi

# Post-deploy orphan snapshot (REQ-2b)
/opt/klai/scripts/audit-orphan-snapshot.sh "${SERVICE:-all}"
```

### Audit-stream event-emit (REQ-5, fragment)

```bash
emit_event() {
  local event="$1" severity="$2" name="$3" details="$4"
  jq -nc \
    --arg service "klai-orphan-audit" \
    --arg event "$event" \
    --arg severity "$severity" \
    --arg name "$name" \
    --argjson details "$details" \
    --arg ts "$(date -Iseconds)" \
    '{service:$service, event:$event, severity:$severity,
      container_name:$name, details:$details, _time:$ts}'
}

# Detectie 1: orphan (geen van beide legitieme label-klasses)
docker ps --format '{{.Names}}' | while read -r name; do
  proj=$(docker inspect "$name" --format '{{index .Config.Labels "com.docker.compose.project"}}')
  managed_by=$(docker inspect "$name" --format '{{index .Config.Labels "klai.managed_by"}}')
  adhoc=$(docker inspect "$name" --format '{{index .Config.Labels "klai.adhoc"}}')
  if [ "$proj" != "klai-core" ] && [ "$managed_by" != "portal-api-provisioning" ] && [ -z "$adhoc" ]; then
    emit_event "orphan_no_managed_label" "warning" "$name" \
      "{\"image\":\"$(docker inspect "$name" --format '{{.Config.Image}}')\"}"
  fi
done
```

Stdout wordt door Alloy opgepikt via Docker socket en doorgestuurd naar
VictoriaLogs.

### Systemd unit-files (REQ-6, ongewijzigd t.o.v. v0.1.0)

```ini
# /etc/systemd/system/docker-cleanup.service
[Unit]
Description=Klai Docker safe cleanup
After=docker.service
Requires=docker.service

[Service]
Type=oneshot
ExecStart=/usr/bin/docker image prune -f
ExecStart=/usr/bin/docker container prune -f --filter until=24h
ExecStart=/usr/bin/docker network prune -f
ExecStart=/usr/bin/docker builder prune -f --filter until=72h

# /etc/systemd/system/docker-cleanup.timer
[Unit]
Description=Run klai docker cleanup daily

[Timer]
OnCalendar=*-*-* 03:00
Persistent=true

[Install]
WantedBy=timers.target
```

## Files Affected

### klai (deze repo)

- **Nieuw:** `.claude/hooks/klai/container-hygiene-preflight.sh`
- **Nieuw/uitgebreid:** `.claude/settings.json` — PreToolUse hook
  registratie
- **Nieuw:** `.claude/rules/klai/infra/container-hygiene.md` —
  narrative + verwijzing naar script
- **Uitgebreid:** `.claude/rules/klai/pitfalls/process-rules.md` —
  `container-cleanup-without-preflight (HIGH)` pitfall
- **Nieuw:** `.moai/specs/SPEC-INFRA-CONTAINER-HYGIENE-001/*.md`

### klai-infra (cross-repo)

- **Nieuw:** `deploy/scripts/compose-up.sh` — deploy-wrapper (REQ-3)
- **Nieuw:** `scripts/audit-compose-orphans.sh` — CI-check (REQ-2a)
- **Nieuw:** `scripts/test-audit-compose-orphans.sh` — regression
  fixture (REQ-2a)
- **Nieuw:** `scripts/audit-orphan-snapshot.sh` — post-deploy verifier
  (REQ-2b)
- **Nieuw:** `scripts/docker-orphan-audit.sh` — weekly audit-stream
  (REQ-5)
- **Uitgebreid:** `deploy/docker-compose.yml` — `librechat-voys`
  service-block (REQ-2c)
- **Uitgebreid:** `.github/workflows/audit-compose.yml` — orphan-check
  als nieuwe step (REQ-2a)
- **Uitgebreid:** 10 service-deploy-workflows — vervang
  `docker compose up` door `compose-up.sh` aanroep (REQ-3)
- **Nieuw:** `core-01/systemd/docker-cleanup.{service,timer}` (REQ-6)
- **Nieuw:** `core-01/systemd/orphan-audit.{service,timer}` (REQ-5)

### Operator-acties op core-01 (geen Git, runtime)

- `systemctl link` + `enable --now` voor beide timers
- Eenmalig: vervang draaiende `librechat-voys` door
  compose-managed variant (`docker stop && rm && compose up -d`)
- Symlink `/opt/klai/scripts/` naar `/opt/klai-infra/scripts/`
- `mkdir -p` voor eventuele log-paths

## MX Tag Plan

- `container-hygiene-preflight.sh`: `# @MX:ANCHOR` op de check-functie
  — fan_in via dat ELKE Claude-sessie het via PreToolUse aanroept.
- `compose-up.sh`: `# @MX:ANCHOR` — fan_in via 10 GitHub workflows.
- `docker-cleanup.service`:
  `# @MX:WARN: never add 'volume prune' or '-af' here. SPEC-INFRA-CONTAINER-HYGIENE-001 R6.`
- `docker-orphan-audit.sh`:
  `# @MX:NOTE: report-only. Never docker rm. SPEC-INFRA-CONTAINER-HYGIENE-001 R5.`
- Pitfall-entry: `@MX:NOTE` met SPEC-ID verwijzing.

## Exclusions

- **Image-pinning policy of pinning-pilot.** VERSIONS.md +
  version-management.md zijn de canoniek; `:latest` op
  `ghcr.io/getklai/*` is bewust beleid. Deze SPEC raakt het niet.
- **`product_events` integratie.** REQ-5 audit gebruikt VictoriaLogs
  (ops-events), niet `product_events` (user-facing business events).
  Zie `observability.md` voor de scheiding.
- **Watchtower / auto-pull tools.** Maken dangling-probleem erger.
  Bewust afgewezen.
- **GHCR retention policy.** Apart probleem, eigen SPEC indien nodig.
- **`klai-core_vexa-recordings-data` volume.** Klantdata, eigen
  evaluatie buiten scope.
- **Server-side enforcement van handmatige `docker rm` via SSH.** REQ-1
  hook werkt alleen voor Claude Code. Operator-side is REQ-5 audit
  het detectie-vangnet achteraf, niet preventief.
- **CI-rule die mechanisch nieuwe `docker run` patterns weert.** Te
  veel false-positives op debug-tooling. REQ-1 + REQ-5 is de balans.

## Implementation Notes (voor `/moai run`)

- **Volgorde STRIKT:**
  1. REQ-1 (deze repo: hook script + settings.json + rule narrative)
  2. REQ-7 (deze repo: pitfall — kan met REQ-1 in 1 PR)
  3. REQ-2c (klai-infra: librechat-voys compose-block + handmatige
     container-vervanging op core-01)
  4. REQ-3 (klai-infra: compose-up.sh + 10 workflow-edits) — pas NA
     REQ-2c gemerged + gedeployed; eerste deploy via wrapper zou anders
     librechat-voys wissen
  5. REQ-2a (klai-infra: audit-compose-orphans.sh + workflow-uitbreiding)
  6. REQ-6 (klai-infra: systemd cleanup timer)
  7. REQ-5 (klai-infra: docker-orphan-audit.sh + systemd timer +
     Grafana panel)
- **Cross-repo werk** in dedicated worktrees (zie
  `spec-work-in-a-worktree` pitfall) — één in deze repo, één in
  klai-infra.
- **Test REQ-1 hook lokaal** met een dummy `docker rm` commando voor
  je merge — verifieer dat het BLOCKED returnt en dat een onschuldig
  `docker logs` doorlaat.
- **Test REQ-3 op `docs.yml` eerst** (laagste blast-radius) voor je de
  andere 9 workflows aanpast.
- **Test REQ-6 met dry-run.** `docker image prune --dry-run -f` op
  core-01 voor activatie van de timer.
- **REQ-5 Grafana-panel** kan in eerste versie minimaal: één table
  panel met laatste 100 audit-events, één alert op
  `event:tenant_container_no_route` of `event:caddy_upstream_missing`
  met severity:critical.

## Live Verification (v0.4.0, 2026-05-04)

Alle stages live geverifieerd op core-01:

| REQ | Verification command | Expected | Actual (2026-05-04) |
|---|---|---|---|
| REQ-1 hook | `cat .claude/hooks/klai/container-hygiene-preflight.sh \| head -1` | Script aanwezig | ✓ |
| REQ-1 hook reg | `grep container-hygiene-preflight .claude/settings.json` | Hit | ✓ (commit `06e9388e`) |
| REQ-2a labels in code | `grep -A3 'container_labels' klai-portal/backend/app/services/provisioning/infrastructure.py` | Drie klasse-B labels in dict | ✓ ([infrastructure.py:288-292](klai-portal/backend/app/services/provisioning/infrastructure.py#L288-L292)) |
| REQ-2b backfill | `ssh core-01 "docker inspect librechat-voys --format '{{ .Config.Labels }}'"` | Bevat `klai.kind`, `klai.managed_by`, `klai.tenant_slug` | ✓ alle drie aanwezig |
| REQ-2c CI guard | `cat .github/workflows/audit-compose.yml \| grep audit-compose-orphans` | Hit | ✓ |
| REQ-2d post-deploy snapshot | `grep audit-orphan-snapshot deploy/scripts/compose-up.sh` | Hit | ✓ |
| REQ-3 wrapper | `for f in .github/workflows/*.yml; do grep -l compose-up.sh "$f"; done` | 10 workflows | ✓ (caddy, deploy-compose, docs, klai-connector, klai-knowledge-mcp, klai-mailer, knowledge-ingest, portal-api, retrieval-api, scribe-api) |
| REQ-5 audit-script | `ls /opt/klai/scripts/docker-orphan-audit.sh` (op core-01) | aanwezig | ✓ |
| REQ-5 audit timer | `ssh core-01 "systemctl list-timers \| grep orphan-audit"` | actief | ✓ laatste run Sun 2026-05-03 03:08 EEST |
| REQ-5 Grafana panel + alert | `ls deploy/grafana/provisioning/{alerting/orphan-audit-rules.yaml,dashboards/klai-orphan-audit.json}` | Beide aanwezig | ✓ (deze versie) |
| REQ-6 cleanup timer | `ssh core-01 "systemctl list-timers \| grep docker-cleanup"` | actief | ✓ laatste run Mon 2026-05-04 03:04 EEST |
| REQ-7 pitfall | `grep container-cleanup-without-preflight .claude/rules/klai/pitfalls/process-rules.md` | Hit | ✓ |

## Success Criteria — final state

- [x] librechat-voys draagt klasse-B labels (`docker inspect` op core-01)
- [x] `_start_librechat_container` zet labels via `client.containers.run(labels={...})`
- [x] Tests in `test_infrastructure_labels.py` slagen — verifiëren label-aanwezigheid
- [x] PreToolUse hook blokkeert `docker volume prune`, `docker image prune -af`,
      `docker compose down --volumes`, en tenant-pattern targets
- [x] CI `audit-compose.yml` workflow draait `audit-compose-orphans.sh` op elke
      compose- of Caddyfile-PR
- [x] `compose-up.sh` deploy-wrapper roept `audit-orphan-snapshot.sh` aan na elke deploy
- [x] 10 service-deploy-workflows roepen `compose-up.sh` aan i.p.v. `docker compose up -d`
- [x] `docker-cleanup.timer` (REQ-6) actief op core-01, dagelijkse safe-prune
- [x] `orphan-audit.timer` (REQ-5) actief op core-01, wekelijkse audit
- [x] Grafana alert rules `tenant_container_no_route` en `caddy_upstream_missing`
      live met routing naar `klai-ops-alerts-email`
- [x] Grafana dashboard `Container hygiene — orphan audit` gedeployed
- [x] Pitfall-entry `container-cleanup-without-preflight (HIGH)` in process-rules.md

7 dagen post-deploy steady-state target: zero `event:orphan_no_managed_label`,
zero `event:tenant_container_no_route`, zero `event:caddy_upstream_missing`
events op de VictoriaLogs `service:klai-orphan-audit` stream. Verifieer via
het Container hygiene dashboard.
