---
id: SPEC-OBS-001
version: 0.1.0
status: draft
created: 2026-04-19
updated: 2026-04-19
author: Mark Vletter
priority: high
---

# SPEC-OBS-001 — Acceptance Criteria

Alle scenarios in Given-When-Then-formaat. Elke scenario is gekoppeld aan één of meer EARS-requirements uit `spec.md`. Edge cases staan onderaan, evenals de quality-gate criteria en Definition of Done.

---

## Scenario's — Alert-engine deployment

### AC-1: Provisioning directory wordt herkend
**Relates to:** SPEC-OBS-001-R2

- **Given** `deploy/grafana/provisioning/alerting/` bestaat met minimaal `contact-points/`, `notification-policies/`, `rules/`, `mute-timings/` subdirectories en tenminste één valide YAML in elk.
- **When** Grafana opstart met `docker compose up -d grafana` op core-01.
- **Then** Grafana logt binnen 30 seconden `level=info msg="alerting provisioning complete"` of equivalent; `curl -sf http://localhost:3000/api/v1/provisioning/contact-points` (met Grafana admin-auth) retourneert de geprovisioneerde contact points.

### AC-2: Ongeldige rule crasht Grafana niet
**Relates to:** SPEC-OBS-001-R3

- **Given** één van de rule-YAML bestanden bevat een syntactisch fout (bijv. ontbrekende `datasourceUid`).
- **When** Grafana opstart.
- **Then** Grafana logt `level=error msg="failed to provision alert rule"` voor de slechte rule, maar blijft draaien; de overige valide rules zijn wel zichtbaar in `/api/v1/provisioning/alert-rules`; exit-code van de container is niet-crashend.

### AC-3: Runtime rule-update door PR-merge
**Relates to:** SPEC-OBS-001-R1

- **Given** een nieuwe rule-YAML komt via PR op `main`.
- **When** de `deploy-compose.yml` workflow synct het bestand naar core-01 en recreëert Grafana.
- **Then** binnen 2 minuten na workflow-completion is de nieuwe rule zichtbaar in Grafana's Alerting UI; evaluatie is actief (status "Normal" of "Pending", geen "Error").

---

## Scenario's — Notification routing

### AC-4: Test-alert naar shadow kanaal
**Relates to:** SPEC-OBS-001-R4, R6

- **Given** contact point `slack-shadow` is geconfigureerd met `url: ${SLACK_WEBHOOK_ALERTS_DEV}` en `ALERTS_ROLLOUT_MODE=shadow`.
- **When** een operator via Grafana UI → Contact points → slack-shadow → "Test" een test-bericht triggert.
- **Then** binnen 15 seconden verschijnt een bericht in `#alerts-dev` met de standaard test-payload; de Slack-attachment bevat severity-color en alert-name.

### AC-5: Shadow-mode routeert alle severity-levels naar shadow
**Relates to:** SPEC-OBS-001-R6

- **Given** `ALERTS_ROLLOUT_MODE=shadow`, alle rules actief.
- **When** een rule op CRIT-severity fires (bijv. handmatig een 5xx-spike veroorzaakt).
- **Then** het bericht arriveert in `#alerts-dev`, **niet** in `#alerts`; notification policy UI toont "routing to slack-shadow" voor alle children.

### AC-6: Productie-mode routeert CRIT naar primary
**Relates to:** SPEC-OBS-001-R4 (productie-variant)

- **Given** `ALERTS_ROLLOUT_MODE=production`, alle rules actief, notification policy in productie-tree.
- **When** een CRIT-rule fires.
- **Then** het bericht arriveert in `#alerts` (slack-primary); een parallel-bericht arriveert **niet** in `#alerts-dev`.

### AC-7: Escalatie naar email bij herhaalde CRIT
**Relates to:** SPEC-OBS-001-R7

- **Given** productie-mode, notification policy met `email-escalation` child voor 3× binnen 30 min.
- **When** dezelfde CRIT-alert fires 3 keer binnen 30 minuten (via gesimuleerde condities).
- **Then** de derde fire resulteert in zowel een Slack-bericht naar `#alerts` als een email via klai-mailer naar `ALERTS_EMAIL_RECIPIENTS` met subject `[KLAI-ALERT-critical] <alertname>`.

### AC-8: Alert-payload met secret wordt geredacteerd
**Relates to:** SPEC-OBS-001-R8

- **Given** een alert-rule met template die een label-waarde `{{ $labels.auth_header }}` gebruikt.
- **When** een rule evalueert waarbij het label de literal waarde `Bearer xoxb-1234567890-abcdef` bevat (gesimuleerd via test-labels).
- **Then** het Slack-bericht bevat `[REDACTED]` in plaats van de bearer-token; de originele label-waarde verschijnt niet in het bericht.

---

## Scenario's — Secret hygiene

### AC-9: Literal Slack-webhook blokkeert PR
**Relates to:** SPEC-OBS-001-R9, R10

- **Given** een PR wijzigt `deploy/grafana/provisioning/alerting/contact-points/slack.yaml` en bevat literal `url: https://hooks.slack.com/services/T01234/B5678/abc123`.
- **When** de `alerting-check` GitHub Action draait.
- **Then** het `audit-alert-secrets.sh` script exiteert met non-zero, het CI-check-block rapporteert "Literal Slack webhook URL found at contact-points/slack.yaml:12", en de PR kan niet merged worden totdat de waarde vervangen is door `${SLACK_WEBHOOK_ALERTS}`.

### AC-10: Env-var substitution werkt in runtime
**Relates to:** SPEC-OBS-001-R9

- **Given** `slack.yaml` bevat `url: ${SLACK_WEBHOOK_ALERTS}` en de env-var is correct gezet in `/opt/klai/.env`.
- **When** Grafana leest de provisioning-file.
- **Then** Grafana verstuurt bij een test-alert naar de daadwerkelijke Slack-URL; geen logging van de URL zelf in Grafana-logs; Grafana's `/api/v1/provisioning/contact-points` retourneert de URL gemaskeerd als `[REDACTED]` of als lege string (standaard Grafana API-gedrag voor secure fields).

---

## Scenario's — Initiële regelcatalogus (seed rules)

### AC-11: portal-api 5xx surge fires
**Relates to:** SPEC-OBS-001-R11 (CRIT)

- **Given** `portal_api_5xx_rate_high` regel actief; baseline 5xx-rate is 0%.
- **When** een kunstmatige load van 20 requests/s naar `/api/health-simulate-500` gedurende 6 minuten een 5xx-rate van ~15% produceert.
- **Then** binnen 5 minuten en 30 seconden fires de alert; het Slack-bericht in `#alerts-dev` (shadow) bevat severity `critical`, summary met actual percentage, en runbook_url naar `#portal-api-5xx-surge`.

### AC-12: Container down fires binnen 2 minuten
**Relates to:** SPEC-OBS-001-R12 (CRIT)

- **Given** `container_down` regel actief; alle klai-core containers draaien.
- **When** een operator `docker stop klai-core-litellm-1` uitvoert.
- **Then** binnen 2 minuten fires de alert `container_down{container="klai-core-litellm-1"}`; na `docker start` lost de alert binnen 1 evaluation-cycle weer op.

### AC-13: FLUSHALL failure fires binnen 60 seconden
**Relates to:** SPEC-OBS-001-R14 (HIGH) — **primaire gap-closing scenario**

- **Given** `portal_redis_flushall_failed` regel actief met 30s evaluation-interval; baseline toont geen matches in de laatste 24 uur.
- **When** een operator via `docker exec klai-core-portal-api-1 python -c "import structlog; log=structlog.get_logger().bind(event='redis_flushall_failed', slug='test-tenant', error='simulated', request_id='test-req-abc'); log.warning('redis flushall failed')"` een test-event injecteert in VictoriaLogs.
- **Then** binnen 60 seconden arriveert een Slack-bericht in het geconfigureerde kanaal (shadow of productie, afhankelijk van `ALERTS_ROLLOUT_MODE`) met:
  - severity: `high`
  - summary bevat `slug=test-tenant` en `request_id=test-req-abc`
  - `runbook_url` die linkt naar `docs/runbooks/platform-recovery.md#librechat-stale-config-recovery`

### AC-14: LibreChat health-failed spike fires
**Relates to:** SPEC-OBS-001-R15 (HIGH)

- **Given** `librechat_health_failed_elevated` regel actief.
- **When** een baseline van minder dan 1 `chat.health_failed` event per uur; gedurende 10 minuten 7 events verschijnen in VictoriaLogs.
- **Then** de alert fires op HIGH-severity binnen 10 minuten van de zevende event.

### AC-15: Knowledge-ingest error surge fires
**Relates to:** SPEC-OBS-001-R16 (HIGH)

- **Given** `ingest_error_rate_elevated` regel actief; baseline < 1 error/uur.
- **When** `knowledge-ingest` logt 12 errors in 10 minuten.
- **Then** de alert fires op HIGH-severity; annotations bevatten een LogsQL-link om de fouten zelf op te zoeken.

### AC-16: Container restart loop fires, niet bij enkele geplande restart
**Relates to:** SPEC-OBS-001-R13 (CRIT), R17 (MED)

- **Given** beide regels actief; CI heeft zojuist een deploy van LiteLLM gedaan (1 legitieme restart).
- **When** die ene restart plaatsvindt.
- **Then** `container_restart_loop` fires **niet** (rate is te laag); `container_unexpected_restart` beoordeelt de timing en fires **niet** omdat een GitHub Actions deploy-event zichtbaar is in de laatste 30 minuten.

### AC-17: Disk-usage high fires bij sustained low disk
**Relates to:** SPEC-OBS-001-R18 (MED)

- **Given** `core01_disk_usage_high` regel actief, drempel 0.15 beschikbaar (= 85% vol).
- **When** de `/`-filesystem op core-01 zakt tot 12% beschikbaar (gesimuleerd via temporary large file).
- **Then** na 30 minuten aanhoudende conditie fires de alert op MED-severity.

---

## Scenario's — Runbook-koppeling

### AC-18: Ontbrekende runbook_url blokkeert PR
**Relates to:** SPEC-OBS-001-R19

- **Given** een PR voegt een nieuwe alert-rule toe zonder `runbook_url` annotation.
- **When** `scripts/verify-alert-runbooks.sh` draait.
- **Then** het script exiteert non-zero met bericht "Rule <alertname> has no runbook_url annotation (file: rules/xxx.yaml)"; PR-check faalt.

### AC-19: runbook_url met dode anchor blokkeert PR
**Relates to:** SPEC-OBS-001-R19

- **Given** een alert-rule met `runbook_url: docs/runbooks/platform-recovery.md#nonexistent-section`.
- **When** `scripts/verify-alert-runbooks.sh` draait.
- **Then** het script exiteert non-zero met "Anchor '#nonexistent-section' not found in docs/runbooks/platform-recovery.md"; PR-check faalt.

### AC-20: Nieuwe runbook-sectie volgt prototype-structuur
**Relates to:** SPEC-OBS-001-R20

- **Given** een nieuwe alert-rule landt in een PR.
- **When** de PR-reviewer leest `docs/runbooks/platform-recovery.md`.
- **Then** de nieuwe sectie heeft alle kopjes: `**Situation:**`, `**Signal to look for:**`, ≥ 1 `### Step N` header, `### Verify`, `### Follow-up` of `### Related alert:`.

---

## Scenario's — Silencing en mute-timings

### AC-21: Silence maskt alert tijdelijk
**Relates to:** SPEC-OBS-001-R21

- **Given** een firing alert, operator heeft `grafana_admin` Zitadel-rol.
- **When** operator via Grafana UI → Alerting → Silences een silence toevoegt met matcher `alertname=container_down`, comment "Known issue, deploy in progress", expiry 1 uur.
- **Then** de alert verdwijnt uit de notification-routing zolang silence actief is; bij expiry (na 1 uur) hervat notification als alert nog fires.

### AC-22: Silence zonder comment wordt geblokkeerd
**Relates to:** SPEC-OBS-001-R21

- **Given** een operator probeert een silence aan te maken via de API zonder comment.
- **When** de POST naar `/api/alertmanager/grafana/api/v2/silences` plaatsvindt met een lege `comment`.
- **Then** de API retourneert 400 met bericht dat comment vereist is; silence wordt niet aangemaakt.

### AC-23: Silence expiry wordt gelogd
**Relates to:** SPEC-OBS-001-R23

- **Given** shadow-rollout actief, een silence expires.
- **When** de expiry-tijd passeert.
- **Then** een notification naar `#alerts-dev` bevestigt "Silence <id> for <alertname> expired".

### AC-24: Mute-timing onderdrukt tijdens onderhoud
**Relates to:** SPEC-OBS-001-R22

- **Given** een mute-timing `deploy-window` geprovisioneerd met tijdsvenster elke vrijdag 14:00–15:00 Europe/Amsterdam, geassocieerd met een notification-policy child voor `alertname=~"container_.*"`.
- **When** op vrijdag 14:30 een `container_restart_loop` fires.
- **Then** geen Slack- of email-notificatie; de alert is wel zichtbaar als "firing" in Grafana UI met indicator "muted".

---

## Scenario's — Alert-on-alert heartbeat

### AC-25: Heartbeat push elke 5 minuten
**Relates to:** SPEC-OBS-001-R24

- **Given** alle alerting-infrastructuur draait, heartbeat-rule actief.
- **When** operator monitort Uptime Kuma push-monitor `alerter-heartbeat` gedurende 1 uur.
- **Then** exact 12 pushes arriveerden (± 1 voor timing-drift); status is "UP".

### AC-26: Stille alerter detecteert zichzelf
**Relates to:** SPEC-OBS-001-R25

- **Given** alerting draait, heartbeat werkt.
- **When** operator voert `docker stop klai-core-grafana-1` uit.
- **Then** binnen 15 minuten na de stop arriveert een bericht in `#alerts-emergency` via Uptime Kuma's **eigen** notification channel (niet via Grafana), met de boodschap dat heartbeat `alerter-heartbeat` down is sinds <timestamp>.

### AC-27: Heartbeat-pad is onafhankelijk
**Relates to:** SPEC-OBS-001-R26

- **Given** een verifiable separation: operator inspecteert `deploy/grafana/provisioning/alerting/contact-points/heartbeat.yaml` en Uptime Kuma config.
- **When** operator controleert dat `heartbeat-kuma` contact point een ander webhook-secret gebruikt (`KUMA_TOKEN_ALERTER_HEARTBEAT`) dan reguliere alerts (`SLACK_WEBHOOK_ALERTS*`), én dat Uptime Kuma's notification-channel naar `#alerts-emergency` een apart Slack-webhook (`SLACK_WEBHOOK_ALERTS_EMERGENCY`) gebruikt.
- **Then** alle drie waarden zijn distinct in `/opt/klai/.env`; geen enkele is gedeeld tussen regulier en heartbeat pad.

---

## Scenario's — Rollout

### AC-28: Shadow-banner in UI
**Relates to:** SPEC-OBS-001-R27

- **Given** `ALERTS_ROLLOUT_MODE=shadow`.
- **When** operator opent Grafana UI, login via Zitadel, navigeert naar Alerting.
- **Then** een banner "Shadow mode active — alerts routing to #alerts-dev" is zichtbaar bovenaan de Alerting-pagina; notification-policy tree toont alle routes eindigend op `slack-shadow`.

### AC-29: Promotie vereist zero-false-positive window
**Relates to:** SPEC-OBS-001-R28

- **Given** 7 opeenvolgende dagen draaide in shadow; operator bekijkt de rollout log.
- **When** operator telt false-positives: 0.
- **Then** operator opent een promotie-PR; de PR-template bevat een checklist met "zero false-positives in 7-daags venster" aangevinkt; een reviewer approve-t de PR met eigen verificatie.

### AC-30: Promotie wijzigt routing + banner verdwijnt
**Relates to:** SPEC-OBS-001-R29

- **Given** promotie-PR gemerged, `ALERTS_ROLLOUT_MODE=production` actief na Grafana restart.
- **When** operator opent Grafana UI en monitoring-kanalen.
- **Then** shadow-banner is weg; notification-policy tree toont CRIT/HIGH routes naar `slack-primary`, CRIT 3×/30min naar óók `email-escalation`; `docs/runbooks/alerting-rollout.md` bevat een nieuwe entry met datum, reviewers, totaal aantal shadow-fires.

---

## Edge cases

### EC-1: Grafana kan VictoriaLogs niet bereiken
- **Given** VictoriaLogs container crashed of network-misconfig.
- **When** Grafana probeert een LogsQL-alert te evalueren.
- **Then** de specifieke rule gaat naar state "Error" (niet "Firing"); `execErrState: Alerting` triggert een alert met name `rule_evaluation_failed` → zichtbaar als een distinct signaal.

### EC-2: Slack webhook rate-limit
- **Given** plotse storm van 50 alerts binnen 30 seconden.
- **When** Grafana probeert alle te posten.
- **Then** Grafana's ingebouwde grouping + throttling bundelt ze (default: groups met 5m interval); meer dan 10 concurrent posts triggeren geen Slack 429-fouten. Bij 429 logt Grafana en probeert opnieuw.

### EC-3: Operator wijzigt rule tijdens actieve fire
- **Given** een alert fires.
- **When** een PR wijzigt de threshold en wordt gemerged terwijl alert actief is.
- **Then** Grafana herlaadt de rule; als de nieuwe threshold de conditie nog steeds satisfies, blijft alert in "Firing" zonder nieuwe notificatie (continuation); als nieuwe threshold conditie niet meer satisfies, lost alert op.

### EC-4: SOPS-decryption faalt voor `SLACK_WEBHOOK_ALERTS`
- **Given** `.env` mist de webhook-var na een corrupte SOPS-sync.
- **When** Grafana opstart.
- **Then** de slack-contact-point krijgt een lege URL; de `audit-alert-secrets.sh` check zou dit niet vangen (die kijkt naar literals); Grafana's interne test-send toont een 400 van Slack; operator moet handmatig detecteren. **Mitigatie:** een extra pre-flight check in de deploy-workflow die `printenv SLACK_WEBHOOK_ALERTS | grep -q '^https://hooks.slack.com/'` vóór Grafana-recreate; faalt de deploy als env leeg is.

### EC-5: Rule met labels die niet matchen bij evaluatie
- **Given** een rule refereert `{{ $labels.slug }}` in de summary, maar een concrete match heeft geen `slug` label.
- **When** de rule fires.
- **Then** de summary toont `(no value)` of `<no value>` voor het label; alert fires nog steeds; de runbook kan alsnog uitgevoerd worden, alleen zonder tenant-specifieke info. Niet blocking, wel een tune-target.

### EC-6: Grafana upgrade breekt provisioning-schema
- **Given** een major Grafana-versie verandert het provisioning-schema.
- **When** `docker compose pull grafana` trekt een nieuwe major versie.
- **Then** opstart-logs tonen "deprecated schema" warnings of errors; testprotocol: nieuwe Grafana-major eerst in een staging-context (lokale docker-compose) testen voordat productie-pin geüpdatet wordt. Dit is bestaande `klai-infra` upgrade-policy.

### EC-7: Test-event voor FLUSHALL lekt naar productie VictoriaLogs
- **Given** operator voert AC-13 drill uit in productie (niet in staging).
- **When** het test-event met `slug=test-tenant` gepushed wordt.
- **Then** VictoriaLogs retentie houdt 30 dagen, event blijft doorzoekbaar; dit is acceptabel, maar operator moet het event taggen met `test: true` label om latere log-analyses niet te vervuilen. Runbook documenteert dit.

### EC-8: Grafana-restart tijdens actieve firing alert
- **Given** een alert fires, Grafana wordt gerestart (bijv. provisioning-update).
- **When** Grafana is down gedurende 30 seconden.
- **Then** de fires-state wordt uit Grafana's interne storage herladen bij opstart; het is mogelijk dat één evaluatie-cycle overgeslagen wordt, waardoor een kortstondig gecorrigeerd event gemist wordt. Aanvaardbare trade-off; bij aanhoudende conditie fires de rule opnieuw bij de volgende evaluatie.

---

## Quality gate criteria

Alle punten hieronder moeten groen zijn voor merge-to-main van elke milestone-PR:

### Lint/config

- [ ] `scripts/audit-alert-secrets.sh` passes.
- [ ] `scripts/verify-alert-runbooks.sh` passes.
- [ ] YAML-lint passes voor alle bestanden in `deploy/grafana/provisioning/alerting/`.
- [ ] `docker compose config grafana | grep -A 20 environment` toont alle verwachte nieuwe env-vars correct geïnterpoleerd (geen onopgeloste `${…}`).

### Functional

- [ ] Handmatige test-alert naar `slack-shadow` komt aan binnen 15 seconden.
- [ ] Elke nieuwe rule heeft een `uid`, `title`, `runbook_url` annotation en een `severity` label.
- [ ] Elke rule's `datasourceUid` matcht een bestaande datasource (`victoriametrics` of `victorialogs`).
- [ ] Nieuwe rules evalueren zonder "Error"-status in Grafana UI na 2 evaluation-cycles.

### Security / secret hygiene

- [ ] Geen literal webhook-URLs, API-tokens, of SMTP-credentials in git.
- [ ] Nieuwe env-vars zijn gedocumenteerd in de SPEC en toegevoegd aan SOPS.
- [ ] Heartbeat-pad gebruikt apart webhook + apart kanaal van reguliere alerts.

### Documentation

- [ ] Elke alert heeft een matching runbook-sectie in `docs/runbooks/platform-recovery.md`.
- [ ] De runbook-sectie volgt het prototype-format (Situation / Signal / Step N / Verify / Follow-up).
- [ ] `docs/runbooks/alerting-rollout.md` is bijgewerkt bij elke Milestone-transitie.

### Rollout

- [ ] Milestone 5 `#alerts-dev` volledig gereviewd: alle fires zijn beoordeeld, false-positives getuned.
- [ ] Minimaal één niet-auteur reviewer heeft de shadow-resultaten goedgekeurd voor promotie.
- [ ] Drill AC-13 (FLUSHALL) is uitgevoerd en geverifieerd.
- [ ] Drill AC-26 (alerter-down) is uitgevoerd en geverifieerd.

---

## Definition of Done

De SPEC is DONE wanneer:

1. Alle milestones uit `plan.md` zijn gemerged op `main`.
2. `ALERTS_ROLLOUT_MODE=production` staat productie-actief; shadow-banner is verdwenen.
3. Alle acceptance-scenario's AC-1 t/m AC-30 zijn geverifieerd, bewijs bijgevoegd in de afsluit-PR (screenshots, LogsQL-queries, Slack-transcripts, waar toepasbaar).
4. Minstens één echt productie-event heeft een bruikbaar Slack-bericht opgeleverd dat een operator heeft helpen diagnosen (of geverifieerd dat dit zou werken via een drill).
5. Geen enkele van de buiten-scope items is "stiekem" toegevoegd: geen SLO-dashboard, geen PagerDuty-integratie, geen tenant-scoped routing. Als dat nodig blijkt, volgt een aparte SPEC.
6. `docs/runbooks/platform-recovery.md#librechat-stale-config-recovery` blijft bestaan (niet verwijderd) en de "Follow-up" sectie verwijst expliciet naar deze SPEC als "done, alerting now automated".
7. `docs/runbooks/alerting-rollout.md` bevat een finale entry "Production rollout complete on YYYY-MM-DD, reviewers: X, Y".
8. Quarterly review template is geplaatst en de eerste review-ronde is ingepland.
9. Lessons learned uit de shadow-periode (threshold-tunings, regel-iteraties) zijn gedocumenteerd als observations in `.claude/rules/klai/infra/observability.md` of als een nieuwe rule-file — zodat de volgende persoon die alerts toevoegt weet wat werkte en wat niet.

---

## References

- `spec.md` — requirements en scope.
- `plan.md` — milestones en technische aanpak.
- `docs/runbooks/platform-recovery.md#librechat-stale-config-recovery` — prototype-runbook (commit `aab3848c`).
- Commits `a3920a75`, `c5653159` — SEC-021 provisioning rewrite (de bron van de FLUSHALL-observabilitykloof).
- `.claude/rules/klai/infra/observability.md` — bestaande logpijplijn en MCP-tooling.
