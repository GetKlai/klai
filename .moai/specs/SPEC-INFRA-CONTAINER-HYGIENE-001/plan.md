# SPEC-INFRA-CONTAINER-HYGIENE-001 — Implementation Plan (v0.2.0)

## Approach

Drie lagen, zes requirements (REQ-4 vervallen), strikt mechanisch.
Het verschil met v0.1.0 is dat élke laag een mechaniek heeft die de
fout-klasse onmogelijk maakt — geen "discipline", geen "rule",
ofwel:

- **Pre-tool guard** (REQ-1): hook in `.claude/settings.json` blokkeert
  destructieve docker-acties tot een script de checks heeft gedraaid.
  Een AI kan het niet vergeten of overslaan.
- **CI-guard** (REQ-2a): PR die orphans introduceert faalt.
- **Deploy-wrapper** (REQ-3): één script, alle workflows roepen het
  aan, `--remove-orphans` zit ingebouwd.
- **Detectie-stream** (REQ-5): VictoriaLogs events, AI queryt zelf
  vóór beslissingen.
- **Timer** (REQ-6): cron-equivalent, mechanisch.

Volgorde-discipline blijft cruciaal: REQ-2c (librechat-voys in
compose) **moet** vóór REQ-3 deploy-wrapper, anders wist
`--remove-orphans` de container weer.

## Task Decomposition

| # | Task | Files | Repo | Risk | Volgorde |
|---|---|---|---|---|---|
| 1 | Schrijf preflight-hook script | `.claude/hooks/klai/container-hygiene-preflight.sh` | klai | Laag | 1 |
| 2 | Registreer hook in settings.json | `.claude/settings.json` | klai | **Medium** (kan andere Bash-calls breken bij regex-fout) | 2 |
| 3 | Schrijf narrative rule + pitfall | `.claude/rules/klai/infra/container-hygiene.md`, `.claude/rules/klai/pitfalls/process-rules.md` | klai | Laag | 3 |
| 4 | Voeg `librechat-voys` compose-block toe (REQ-2c) | `deploy/docker-compose.yml` | klai-infra | **HIGH** | 4 |
| 5 | Vervang draaiende `librechat-voys` op core-01 door compose-managed | core-01 ssh handmatig | runtime | **HIGH** (downtime ~30s) | 5 |
| 6 | Schrijf `compose-up.sh` deploy-wrapper (REQ-3) | `deploy/scripts/compose-up.sh` | klai-infra | Medium | 6 |
| 7 | Update `docs.yml` workflow als REQ-3 pilot | `.github/workflows/docs.yml` | klai-infra | Laag | 7 |
| 8 | Update overige 9 service-workflows | `.github/workflows/{caddy,klai-connector,klai-knowledge-mcp,klai-mailer,knowledge-ingest,portal-api,retrieval-api,scribe-api,whisper-server}.yml` | klai-infra | **HIGH** (alle deploy-paden) | 8 |
| 9 | Schrijf `audit-compose-orphans.sh` + regression test (REQ-2a) | `scripts/audit-compose-orphans.sh`, `scripts/test-audit-compose-orphans.sh` | klai-infra | Laag | 9 |
| 10 | Breid `audit-compose.yml` workflow uit met orphan-step | `.github/workflows/audit-compose.yml` | klai-infra | Laag | 10 |
| 11 | Schrijf `audit-orphan-snapshot.sh` post-deploy verifier (REQ-2b) | `scripts/audit-orphan-snapshot.sh` | klai-infra | Laag | 11 |
| 12 | Schrijf systemd `docker-cleanup.{service,timer}` units (REQ-6) | `core-01/systemd/docker-cleanup.{service,timer}` | klai-infra | Laag | 12 |
| 13 | Installeer + activeer cleanup-timer op core-01 | `systemctl link`, `enable --now` | runtime | Laag | 13 |
| 14 | Schrijf `docker-orphan-audit.sh` event-emitter (REQ-5) | `scripts/docker-orphan-audit.sh` | klai-infra | Laag | 14 |
| 15 | Schrijf systemd `orphan-audit.{service,timer}` units | `core-01/systemd/orphan-audit.{service,timer}` | klai-infra | Laag | 15 |
| 16 | Installeer + activeer audit-timer op core-01 | `systemctl link`, `enable --now` | runtime | Laag | 16 |
| 17 | Configureer Grafana panel + alert op audit-stream | Grafana UI / dashboard JSON | klai-infra | Laag | 17 |

Tasks 1–3 vallen in één klai-PR (alle zelfstandig in deze repo, geen
externe afhankelijkheden). Tasks 4–17 vallen in 4–6 kleinere klai-infra
PRs voor regressie-isolatie.

## Files Affected

### klai (deze repo)

- **Nieuw:** `.claude/hooks/klai/container-hygiene-preflight.sh`
  (~80 regels bash + jq parsing).
- **Uitgebreid:** `.claude/settings.json` — PreToolUse hook entry
  (~5 regels JSON).
- **Nieuw:** `.claude/rules/klai/infra/container-hygiene.md`
  (~80 regels narrative).
- **Uitgebreid:** `.claude/rules/klai/pitfalls/process-rules.md` —
  pitfall toevoeging (~40 regels).
- **Bestaand:** `.moai/specs/SPEC-INFRA-CONTAINER-HYGIENE-001/*.md`.

### klai-infra (cross-repo)

- **Nieuw:** `deploy/scripts/compose-up.sh` (~30 regels bash).
- **Nieuw:** `scripts/audit-compose-orphans.sh` (~80 regels bash + yq).
- **Nieuw:** `scripts/test-audit-compose-orphans.sh` (~50 regels
  fixture-based regression test, volgt
  `scripts/test-audit-compose.sh` patroon).
- **Nieuw:** `scripts/audit-orphan-snapshot.sh` (~50 regels bash).
- **Nieuw:** `scripts/docker-orphan-audit.sh` (~150 regels bash + jq +
  curl naar VictoriaLogs).
- **Uitgebreid:** `deploy/docker-compose.yml` — `librechat-voys`
  service-block (~22 regels).
- **Uitgebreid:** `.github/workflows/audit-compose.yml` — extra step
  voor orphan-check (~6 regels).
- **Uitgebreid:** 10 service-deploy-workflows — `docker compose up`
  vervangen door `ssh core-01 "/opt/klai/deploy/scripts/compose-up.sh
  <service>"` (~2 regels per workflow).
- **Nieuw:** `core-01/systemd/docker-cleanup.{service,timer}`.
- **Nieuw:** `core-01/systemd/orphan-audit.{service,timer}`.

### Operator-acties op core-01 (geen Git, runtime)

- Eenmalig: vervang draaiende `librechat-voys` door compose-managed
  variant (`docker stop && docker rm && cd /opt/klai && docker compose
  up -d librechat-voys`) — uit te voeren na merge van REQ-2c PR.
- `systemctl link` + `enable --now` voor `docker-cleanup.timer`
  (na merge van REQ-6 PR).
- `systemctl link` + `enable --now` voor `orphan-audit.timer`
  (na merge van REQ-5 PR).
- Grafana-panel JSON importeren in dashboard.

## Technology Choices

- **Bash over Python** voor alle scripts — geen runtime-deps op core-01,
  past in bestaande `scripts/` patroon (zie `audit-compose-volumes.sh`,
  `victorialogs-tunnel.sh`).
- **PreToolUse hook over Skill of MCP-tool** — hook draait
  mechanisch vóór elke matching tool-call zonder dat AI er expliciet om
  vraagt. Skill of MCP-tool zou AI moeten aanroepen, dus terug bij
  "vertrouw op discipline".
- **Eén deploy-wrapper script over per-workflow flag** — DRY, single
  source, AI die nieuwe workflow schrijft kopieert SSH-regel zonder
  flag-discipline.
- **VictoriaLogs over `product_events`** voor REQ-5 — ops-events horen
  in observability stack, niet in business-event tabel. Past bij
  bestaande Alloy → VictoriaLogs flow.
- **Stdout-emit + Alloy-pickup over directe HTTP push** — script
  hoeft geen VictoriaLogs auth te kennen, Alloy doet de heavy
  lifting. Minder fragiele integratie.
- **Systemd timer over cron** — `journalctl -u` logging, makkelijker
  inspecteerbaar, modernere syntax.

## Risks & Mitigations

| Risk | Mitigation |
|---|---|
| REQ-1 hook regex matcht te breed en blokkeert legitieme `docker logs`/`ps` calls | Hook test-fixture in PR: drie blokkade-cases + drie doorlaat-cases. Pre-merge handmatige test in een dummy sessie. |
| REQ-1 hook is te traag (5+s per Bash-call) en frustreert workflow | Hook MOET <5s zijn per spec; check 5 (VictoriaLogs traffic) is duurste — backoff naar 1s timeout. Idempotent caching van compose-history check. |
| REQ-3 wist `librechat-voys` voor REQ-2c gemerged is | Volgorde-discipline: REQ-2c PR moet gemerged + gedeployed zijn vóór REQ-3 PR open gaat. AC-3 verifieert dit. |
| REQ-3 deploy-wrapper failt en breekt alle 10 deploys tegelijk | Test REQ-3 op `docs.yml` als pilot vóór de andere 9. Pilot-deploy moet 24u live draaien zonder issues vóór bredere rollout. |
| `--remove-orphans` blijkt nog ANDERE bestaande wezen te wissen | Vóór REQ-3 deploy-wrapper-pilot: draai REQ-5 audit-script eenmalig handmatig op core-01, review output, los onbekende wezen op vóór de wrapper live gaat. |
| systemd timer faalt silent op core-01 | REQ-5 audit-script bevat een check op `journalctl -u docker-cleanup --since '24h ago'` — als geen successful run, audit-event `event:cleanup_timer_unhealthy` met `severity:critical`. Grafana alert. |
| Audit-script vals-positief op legitiem ad-hoc debug-container | Audit-script respecteert `klai.adhoc=*` label — labeled containers vallen in eigen sectie ipv "wees" sectie. |
| `.claude/settings.json` JSON-fout breekt alle hooks | Pre-merge: `jq < .claude/settings.json` moet exit-0. Pre-commit-hook in deze repo zou dit kunnen mechanisch maken (out of scope hier). |
| Hook werkt niet voor `ssh core-01 "docker rm"` (commando is een string-argument van ssh) | Erkende beperking. Hook checkt het top-level Bash-commando; geneste SSH commands zijn niet afgevangen. REQ-5 audit is detectie-vangnet achteraf. |
| Hook script vereist `jq`, `curl`, `git` lokaal — kan ontbreken op nieuwe dev-machines | Hook script begint met dependency-check; bij ontbreken exit-0 met waarschuwing (fail-open ipv fail-closed voor dev-vriendelijkheid; productie-bescherming via REQ-2/REQ-5). Discutabel — zie open vraag. |

## Open vragen voor `/moai run`

1. **Hook fail-mode bij ontbrekende deps:** fail-open (waarschuwen,
   doorlaten) of fail-closed (blokkeren)? Fail-closed is veiliger;
   fail-open is dev-vriendelijker. Voorkeur: fail-closed met `jq`,
   `curl` als hard requirements (beide standaard op dev-machines via
   brew/apt); `ssh core-01` reachability check fail-open omdat dev-
   machines niet altijd VPN aan hebben.
2. **Per-workflow `--remove-orphans` of globale env-var:** voor REQ-3
   heb ik gekozen voor de wrapper. Maar als jullie `COMPOSE_REMOVE_ORPHANS=true`
   in `/opt/klai/.env` zetten werkt het ook zonder wrapper. Voorkeur:
   wrapper, omdat het ook REQ-2b post-deploy snapshot dezelfde
   plek geeft. Maar het is uitwisselbaar.
3. **VictoriaLogs auth in `audit-script`:** script schrijft naar
   stdout, Alloy pickt op. Geen auth nodig in script. Bevestiging dat
   Alloy alle stdout van containers zonder filter forward.

## Success Criteria

- AC-1 t/m AC-7 (uit acceptance.md) holden allemaal in de week na
  oplevering.
- Geen productie-incident veroorzaakt door deze SPEC's deploys.
- Eerste audit-stream (REQ-5, één week na go-live) toont **maximaal 1
  `event:orphan_no_compose_label` event** (`librechat-voys` voor
  REQ-2c deploy — anders 0).
- 30 dagen na REQ-6 go-live: dangling-image count op core-01 blijft
  consistent <50.
- Hook-script blokkeert minstens 1 reële cleanup-poging in de eerste
  maand (gemeten via VictoriaLogs `event:hook_blocked` event dat hook
  emit bij blokkade).

## Out of Scope

- Image-pinning policy of pinning-pilot — VERSIONS.md is canoniek.
- Server-side enforcement van handmatige `docker rm` via SSH (zonder
  Claude). REQ-5 audit-stream is het detectie-vangnet achteraf.
- CI-rule die mechanisch alle nieuwe `docker run` patterns weert. Te
  veel false-positives op debug-tooling.
- Vexa recordings volume.
- GHCR retention policy.
- Kubernetes / multi-server hygiene.

## Ordering & Branch Strategy

- **Eén worktree voor klai-repo werk** (REQ-1 + REQ-7 in 1 PR):
  `git worktree add ../klai-container-hygiene -b feature/SPEC-INFRA-CONTAINER-HYGIENE-001 main`.
- **Eén worktree voor klai-infra werk** (REQ-2 t/m REQ-6, opgesplitst
  in stages):
  `git worktree add ../klai-infra-container-hygiene -b feature/SPEC-INFRA-CONTAINER-HYGIENE-001 main`
  in de klai-infra checkout.

**Stages (klai-infra):**

- **Stage 1:** klai-PR (tasks 1-3) — direct mergeable.
- **Stage 2:** klai-infra-PR REQ-2c (compose-block librechat-voys) —
  mergen, deploy via huidige workflow (zonder wrapper), verifiëren via
  `docker inspect`.
- **Stage 3:** klai-infra-PR REQ-3 (compose-up.sh + docs.yml als
  pilot) — mergen, monitor 24u op core-01, dan rollout naar overige
  9 workflows in vervolg-PR.
- **Stage 4:** klai-infra-PR REQ-2a + REQ-2b (audit-compose-orphans
  + audit-orphan-snapshot + workflow-uitbreiding).
- **Stage 5:** klai-infra-PR REQ-6 (systemd cleanup timer + install
  op core-01).
- **Stage 6:** klai-infra-PR REQ-5 (audit-stream script + systemd
  timer + Grafana panel/alert).

Geen mega-PR. Stages 2 t/m 6 elk in eigen PR voor regressie-isolatie.

## Annotation Hooks

Plekken waar annotation tijdens `/moai run` welkom is:

- `// NOTE:` op de hook regex-pattern — als blijkt dat een legitiem
  pattern wordt gevangen.
- `// NOTE:` op de fail-mode keuze (fail-open vs fail-closed).
- `// NOTE:` op de `compose-up.sh` flag-set — `--remove-orphans` is
  zeker, andere flags (`--pull always`, etc.) zijn discussabel.
