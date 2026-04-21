---
id: SPEC-OBS-001
version: 0.1.0
status: draft
created: 2026-04-19
updated: 2026-04-19
author: Mark Vletter
priority: high
---

# SPEC-OBS-001 — Implementation Plan

## Approach in één paragraaf

We activeren Grafana's Unified Alerting via file-based provisioning. We voegen `deploy/grafana/provisioning/alerting/` toe met vier subdirectories (rules, contact-points, notification-policies, mute-timings), injecteren alle webhook-secrets via env-var substitution uit SOPS, bouwen een minimale initiële regelcatalogus die concreet de FLUSHALL- en container-health-gaten dicht, en zetten een shadow-rollout op met een expliciete zero-false-positive gate van 7 dagen voordat alerts daadwerkelijk on-call mensen bereiken. Alert-on-alert draait via een onafhankelijk Uptime Kuma-pad zodat een stilvallende alerter zichzelf niet maskeert.

---

## Milestones (prioriteitsvolgorde — geen tijdsschattingen)

### Milestone 1 — Foundations (Priority: High)

**Doel:** alerting-backbone staat, geen alerts live; alles draait in shadow-modus.

Deliverables:
- `deploy/grafana/provisioning/alerting/README.md` — uitleg directory-structuur, secret-conventies, review-checklist.
- `deploy/grafana/provisioning/alerting/contact-points/slack.yaml` — `slack-primary` + `slack-shadow`, env-var secrets.
- `deploy/grafana/provisioning/alerting/contact-points/email.yaml` — `email-escalation` via klai-mailer (nieuwe endpoint `POST /api/alerts/email`).
- `deploy/grafana/provisioning/alerting/notification-policies/default.yaml` — alle routes naar `slack-shadow` initieel; severity-gebaseerde children.
- `deploy/grafana/provisioning/alerting/mute-timings/deploys.yaml` — voorgefabriceerde mute-timings voor gangbare onderhoudsvensters.
- SOPS-updates: `SLACK_WEBHOOK_ALERTS`, `SLACK_WEBHOOK_ALERTS_DEV`, `ALERTS_EMAIL_RECIPIENTS`, `ALERTS_ROLLOUT_MODE=shadow`.
- `klai-mailer` uitgebreid met `/api/alerts/email` endpoint (accepteert Grafana webhook payload → SMTP naar `ALERTS_EMAIL_RECIPIENTS`).
- `docker-compose.yml` update: Grafana environment-block uitgebreid met nieuwe env-vars (let op: portal-api en mailer hebben expliciete `environment:` blocks die niet auto-forwarden — zie `.claude/rules/klai/lang/docker.md`).

Exit-criteria:
- Grafana herkent de alerting-directory na `docker compose up -d grafana`; check via Grafana UI → Alerting → Contact points.
- Handmatig test-bericht via contact point `slack-shadow` arriveert in `#alerts-dev`.
- `scripts/audit-alert-secrets.sh` draait lokaal en op CI, passes.

Dependencies:
- Nieuwe Slack-webhook URLs (handmatig in Slack-workspace aangemaakt door operator; waarden via SOPS ingevoerd).

### Milestone 2 — Seed alert catalogue (Priority: High)

**Doel:** de zeven initiële regels uit SPEC-OBS-001-R11..R18 geprovisioneerd en evaluerend, alle targeting shadow-kanaal.

Deliverables:
- `deploy/grafana/provisioning/alerting/rules/portal-api.yaml` — `portal_api_5xx_rate_high`.
- `deploy/grafana/provisioning/alerting/rules/infra-containers.yaml` — `container_down`, `container_restart_loop`, `container_unexpected_restart`.
- `deploy/grafana/provisioning/alerting/rules/portal-events.yaml` — `portal_redis_flushall_failed` (LogsQL-gebaseerd via VictoriaLogs-plugin datasource).
- `deploy/grafana/provisioning/alerting/rules/librechat-health.yaml` — `librechat_health_failed_elevated`.
- `deploy/grafana/provisioning/alerting/rules/ingest.yaml` — `ingest_error_rate_elevated`.
- `deploy/grafana/provisioning/alerting/rules/node.yaml` — `core01_disk_usage_high`.
- `scripts/verify-alert-runbooks.sh` — parseert alle `runbook_url` annotations, resolved tegen repo, faalt op 404.
- CI-integratie: `audit-alert-secrets.sh` en `verify-alert-runbooks.sh` draaien op `.github/workflows/alerting-check.yml` voor PRs die `deploy/grafana/provisioning/alerting/**` touchen.

Exit-criteria:
- Alle 7 regels zichtbaar in Grafana → Alerting → Rules, status "OK" of "Firing" (geen "Error").
- LogsQL-query latency p95 < 5s (instrumentatie via `grafana_alerting_rule_evaluation_duration_seconds`).
- Alle regels hebben `runbook_url`, alle `runbook_url`s resolven.

Dependencies:
- Milestone 1 moet live staan in shadow-modus.
- VictoriaLogs-plugin versie check: plugin ondersteunt nodige LogsQL-features (basic query via `event:` filter werkt; getest in POC).

### Milestone 3 — Runbooks uitbreiden (Priority: High)

**Doel:** elke alert uit Milestone 2 heeft een passende recovery-sectie in `platform-recovery.md`.

Deliverables:
- `docs/runbooks/platform-recovery.md` uitbreiding met secties:
  - `portal-api-5xx-surge` (nieuw)
  - `container-down` (nieuw, generieke stap-voor-stap)
  - `container-restart-loop` (nieuw)
  - `librechat-chat-health-failed` (nieuw; complementair aan bestaande `librechat-stale-config-recovery`)
  - `knowledge-ingest-error-surge` (nieuw)
  - `core01-disk-usage-high` (nieuw)
- Elke sectie volgt het prototype: Situation → Signal → Step 1..N → Verify → Follow-up.
- Cross-references toegevoegd: in elke sectie een "Related alert: SPEC-OBS-001-Rx" regel.

Exit-criteria:
- `scripts/verify-alert-runbooks.sh` passes voor alle nieuwe alerts.
- Peer review door iemand die de runbooks ooit daadwerkelijk zou moeten uitvoeren (niet de auteur).

Dependencies:
- Milestone 2 moet landen voordat deze reviewbaar is.

### Milestone 4 — Alert-on-alert heartbeat (Priority: High)

**Doel:** een stille alerter detecteerbaar via onafhankelijk pad.

Deliverables:
- Grafana contact point `heartbeat-kuma` (nieuw) dat elke 5 min een `push` naar Uptime Kuma doet via HTTPS webhook.
- Uptime Kuma op public-01: nieuwe monitor type "push" met `KUMA_TOKEN_ALERTER_HEARTBEAT`, verwacht elke 5 min heartbeat, raises alert after 15 min stilte.
- Uptime Kuma secundair notification pad: apart webhook naar `#alerts-emergency` (apart Slack-kanaal, apart webhook-secret, apart SOPS-entry).
- `docs/runbooks/platform-recovery.md` nieuwe sectie `alerter-down-recovery`.

Exit-criteria:
- Stop Grafana container → binnen 15 min verschijnt notificatie in `#alerts-emergency`.
- Stop Grafana + Uptime Kuma → geen valse notificatie (geen cascading false-positive; de secundair pad is op public-01, onafhankelijk van core-01).

Dependencies:
- Nieuw Slack-webhook voor `#alerts-emergency`, apart van reguliere alerts-webhook, in SOPS.

### Milestone 5 — Shadow rollout window (Priority: High)

**Doel:** 7 dagen draaien in shadow-modus, observeren, tunen, proberen te breken.

Activiteiten:
- `#alerts-dev` actief bewaken (minimaal dagelijks scannen).
- Voor elke false-positive: tune de regel (threshold, `for:` duration, filter-labels) in een kleine PR; documenteer in `docs/runbooks/alerting-rollout.md`.
- Voor elk missed event (signaal in logs dat geen alert gaf): diagnose root cause, extra regel of regel-tuning.
- Gesimuleerde events: handmatig een `event:redis_flushall_failed` logline injecteren (via `docker exec portal-api python -c "import structlog; log=structlog.get_logger(); log.warning('test', event='redis_flushall_failed', slug='test-slug', error='simulated')"`); verifieer dat alert fires binnen 60s.
- Drill: stop container → `container_down` fires.
- Drill: artificial 5xx spike via `curl` op een test-endpoint dat een 500 returnt → `portal_api_5xx_rate_high` fires.

Exit-criteria:
- 7 opeenvolgende dagen zonder false-positive in `#alerts-dev`.
- Alle drills succesvol (drill-log in rollout document).
- Peer-approval van deze fase door minimaal één reviewer die niet de implementor is.

Dependencies:
- Milestones 1–4 landed.

### Milestone 6 — Production promotion (Priority: High)

**Doel:** schakel van shadow naar productie.

Deliverables:
- PR "SPEC-OBS-001: promote to production" bevattend:
  - `ALERTS_ROLLOUT_MODE=production` via SOPS.
  - Notification policy updates: CRIT en HIGH → `slack-primary`; CRIT 3× → `email-escalation`.
  - `docs/runbooks/alerting-rollout.md` aangevuld met promotie-log (datum, reviewers, shadow-stats samenvatting).
  - Checklist in PR-body: shadow-criteria voldaan, drills succesvol, runbooks reviewed, alerter-heartbeat werkt.
- Grafana banner "Shadow mode active" verdwijnt bij env-change.

Exit-criteria:
- PR merged met approval van minimaal één reviewer.
- Eerstvolgende echte alert (kan even duren, is juist gewenst) arriveert in `#alerts` met correcte routing.

Dependencies:
- Milestone 5 succesvol afgerond.

### Milestone 7 — Post-rollout observability (Priority: Medium)

**Doel:** weten of de alerting zelf werkt zoals bedoeld; voorbereiden op future uitbreiding.

Deliverables:
- Nieuw Grafana dashboard "Alerting Health": fire-rate per alert, evaluation latency per rule, silence-coverage, mute-timing hit-rate.
- `docs/runbooks/alerting-rollout.md` voegt quarterly review-template toe: per regel vraag "zit deze in de top-5 noise-makers? moeten we hem tighter of juist looser maken?".
- Quarterly review scheduled (placeholder calendar-invite / issue-template).

Exit-criteria:
- Dashboard gedeployed, toegankelijk voor `grafana_admin`.
- Review-template in runbook-directory.

Dependencies:
- Milestone 6.

---

## Technical approach

### Provisioning directory layout

```
deploy/grafana/provisioning/alerting/
├── README.md
├── contact-points/
│   ├── slack.yaml            # slack-primary + slack-shadow (env-var secrets)
│   ├── email.yaml            # email-escalation via klai-mailer
│   └── heartbeat.yaml        # heartbeat-kuma (push to Uptime Kuma)
├── notification-policies/
│   └── default.yaml          # routing tree; root → shadow; severity children
├── mute-timings/
│   └── deploys.yaml          # (empty initially; operator-populated as needed)
└── rules/
    ├── portal-api.yaml       # portal_api_5xx_rate_high
    ├── infra-containers.yaml # container_down, container_restart_loop, container_unexpected_restart
    ├── portal-events.yaml    # portal_redis_flushall_failed
    ├── librechat-health.yaml # librechat_health_failed_elevated
    ├── ingest.yaml           # ingest_error_rate_elevated
    └── node.yaml             # core01_disk_usage_high
```

### Secret injection

Grafana ondersteunt `$__env{VAR_NAME}` binnen provisioning-files (Grafana ≥ 10). Patroon:

```yaml
# deploy/grafana/provisioning/alerting/contact-points/slack.yaml
apiVersion: 1
contactPoints:
  - orgId: 1
    name: slack-primary
    receivers:
      - uid: slack-primary-1
        type: slack
        settings:
          url: ${SLACK_WEBHOOK_ALERTS}
          title: '{{ template "slack.klai.title" . }}'
          text: '{{ template "slack.klai.body" . }}'
```

Docker-compose zorgt dat de env-var in de Grafana-container beschikbaar is:

```yaml
# deploy/docker-compose.yml (grafana service)
environment:
  SLACK_WEBHOOK_ALERTS: ${SLACK_WEBHOOK_ALERTS}
  SLACK_WEBHOOK_ALERTS_DEV: ${SLACK_WEBHOOK_ALERTS_DEV}
  ALERTS_EMAIL_RECIPIENTS: ${ALERTS_EMAIL_RECIPIENTS}
  ALERTS_ROLLOUT_MODE: ${ALERTS_ROLLOUT_MODE:-shadow}
```

De env-vars komen uit `/opt/klai/.env`, beheerd via SOPS in `klai-infra/core-01/.env.sops`.

### LogsQL rules via VictoriaLogs datasource plugin

Grafana's Unified Alerting laat toe queries te draaien tegen elke geconfigureerde datasource, inclusief de VictoriaLogs plugin. Voorbeeld-regel voor `portal_redis_flushall_failed`:

```yaml
apiVersion: 1
groups:
  - orgId: 1
    name: portal-events
    folder: Klai
    interval: 30s
    rules:
      - uid: portal-redis-flushall-failed
        title: portal_redis_flushall_failed
        condition: C
        data:
          - refId: A
            datasourceUid: victorialogs
            model:
              expr: 'service:portal-api AND event:redis_flushall_failed'
              queryType: logsql
          - refId: C
            datasourceUid: __expr__
            model:
              type: threshold
              expression: A
              conditions:
                - evaluator: { params: [0], type: gt }
        noDataState: OK
        execErrState: Alerting
        for: 0s
        annotations:
          summary: 'Redis FLUSHALL failed in portal-api (slug={{ $labels.slug }})'
          runbook_url: 'docs/runbooks/platform-recovery.md#librechat-stale-config-recovery'
        labels:
          severity: high
          service: portal-api
```

Let op: LogsQL via de plugin is een relatief nieuwe combinatie — validatie tijdens Milestone 2 includeert een smoke-test op query-latency en correcte label-extraction.

### PromQL rules via VictoriaMetrics datasource

Standaard patroon, geen bijzonderheden. Voorbeeld `portal_api_5xx_rate_high`:

```yaml
- uid: portal-api-5xx-rate-high
  title: portal_api_5xx_rate_high
  condition: C
  data:
    - refId: A
      datasourceUid: victoriametrics
      model:
        expr: |
          sum(rate(caddy_http_requests_total{service="portal-api",status=~"5.."}[5m]))
          / sum(rate(caddy_http_requests_total{service="portal-api"}[5m]))
        instant: true
    - refId: C
      datasourceUid: __expr__
      model:
        type: threshold
        expression: A
        conditions:
          - evaluator: { params: [0.01], type: gt }
  for: 5m
  annotations:
    summary: 'portal-api 5xx rate > 1% over 5m ({{ $value | humanizePercentage }})'
    runbook_url: 'docs/runbooks/platform-recovery.md#portal-api-5xx-surge'
  labels:
    severity: critical
    service: portal-api
```

### Alert-on-alert heartbeat

Grafana contact point type `webhook` met push naar Uptime Kuma's push-monitor:

```yaml
- uid: heartbeat-kuma-1
  type: webhook
  settings:
    url: https://status.getklai.com/api/push/${KUMA_TOKEN_ALERTER_HEARTBEAT}?status=up&msg=ok
    httpMethod: GET
```

In Grafana zetten we een synthetische alert-rule die **altijd fires** (condition: `1 > 0`) met een evaluation-interval van 5 minuten en een notification policy die uitsluitend deze rule naar `heartbeat-kuma` stuurt. Elke fire = een heartbeat-push. Uptime Kuma verwacht elke 5 min een push; 15 min stilte → independent notification naar `#alerts-emergency` via Kuma's eigen notification channel.

**Belangrijk:** het Uptime Kuma notification-pad gaat **niet via Grafana of de core-01 Slack-webhook** — het gebruikt een apart webhook-secret naar een apart kanaal. Scheiding voorkomt dat een Grafana-config-fout beide paden tegelijk breekt.

### CI checks

`.github/workflows/alerting-check.yml`:

```yaml
name: alerting-check
on:
  pull_request:
    paths:
      - 'deploy/grafana/provisioning/alerting/**'
      - 'docs/runbooks/platform-recovery.md'
jobs:
  audit:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Audit secrets
        run: scripts/audit-alert-secrets.sh
      - name: Verify runbook URLs
        run: scripts/verify-alert-runbooks.sh
```

`scripts/audit-alert-secrets.sh` grept op bekende patterns (`https://hooks.slack.com/services/`, `xoxb-`, regex voor email+password combos) en faalt op hit.

`scripts/verify-alert-runbooks.sh` parseert YAML in `deploy/grafana/provisioning/alerting/rules/`, extracteert elke `runbook_url`, splitst op `#`, verifieert dat de file bestaat en dat de anchor overeenkomt met een kop in het Markdown-bestand.

---

## Risks en aannames

### Aannames (testbaar)

1. **De VictoriaLogs Grafana-plugin ondersteunt LogsQL queries in alert-rules met voldoende betrouwbaarheid.** Wordt geverifieerd in Milestone 2 via een query-latency smoke-test. Fallback: migreer specifieke rules naar vmalert (kost één extra container, geen architectuur-change).
2. **Grafana reload van provisioning-files leidt niet tot alert-misvuren tijdens reload.** Grafana ≥ 10 heeft live-reload zonder downtime voor alerting; te valideren bij de eerste provisioning-change na initiale deploy.
3. **Uptime Kuma push-webhook werkt betrouwbaar als dead-man's switch.** Uptime Kuma draait al op public-01 voor externe monitoring. Push-monitor-type is standaardfunctionaliteit.
4. **Slack webhook-URLs worden niet silently gerate-limited bij normale alert-volumes.** Slack's rate limit is ~1 msg/sec per webhook; we verwachten veel minder dan dat.
5. **Caddy metriek `caddy_http_requests_total` is beschikbaar in VictoriaMetrics.** Te verifieren — Caddy heeft JSON-logging sinds SPEC-INFRA-004, en we moeten controleren of een Prometheus-exporter reeds de status-codes aggregeert, óf dat we die metric uit VictoriaLogs moeten promoten. Als dat ontbreekt: alternatief is een LogsQL-count over `service:caddy AND status:5*` — eveneens werkbaar.

### Risico-mitigatie per milestone

- **Milestone 1:** env-var substitution kan misgaan bij verkeerd escape-karakter. Mitigatie: lokale smoke-test met een dummy-webhook vóór SOPS-update.
- **Milestone 2:** LogsQL query kan syntax-fouten bevatten. Mitigatie: elke query eerst handmatig via VictoriaLogs MCP tool testen, vervolgens pasten.
- **Milestone 3:** runbooks dupliceren bestaande informatie. Mitigatie: volg het prototype-format strict; geen lange prose.
- **Milestone 4:** heartbeat fires constant in shadow-modus = vuile test-data in Kuma. Mitigatie: acceptabel; shadow + productie heartbeats gebruiken dezelfde push-token omdat het systeem-breed is.
- **Milestone 5:** 7 dagen is "lang" voelt — verleiding om eerder te promoten. Mitigatie: de duur is bewust, gelogd in SPEC, en operator-bevestiging verplicht.
- **Milestone 6:** promotion-PR kan per ongeluk reguliere alert-wijzigingen meetrekken. Mitigatie: PR uitsluitend `ALERTS_ROLLOUT_MODE` en notification-policies aanpassen; geen andere changes toegestaan.
- **Milestone 7:** nice-to-have, niet blocking. Kan desnoods later landen.

---

## Dependencies en integratiepunten

### Nieuwe environment variables (SOPS)

| Var | Gebruik | Toegevoegd in milestone |
|---|---|---|
| `SLACK_WEBHOOK_ALERTS` | Slack webhook naar `#alerts` | M1 |
| `SLACK_WEBHOOK_ALERTS_DEV` | Slack webhook naar `#alerts-dev` | M1 |
| `SLACK_WEBHOOK_ALERTS_EMERGENCY` | Slack webhook naar `#alerts-emergency` (Kuma-pad) | M4 |
| `ALERTS_EMAIL_RECIPIENTS` | Comma-separated email-ontvangers | M1 |
| `ALERTS_ROLLOUT_MODE` | `shadow` of `production` | M1 |
| `KUMA_TOKEN_ALERTER_HEARTBEAT` | Uptime Kuma push-token | M4 |

### Bestaande componenten die uitgebreid worden

- `deploy/docker-compose.yml` — Grafana `environment:` block (env-var forwarding).
- `klai-mailer` — nieuw endpoint `POST /api/alerts/email`.
- `docs/runbooks/platform-recovery.md` — zes nieuwe secties plus één voor alerter-down.
- `.github/workflows/` — nieuwe `alerting-check.yml`.
- Uptime Kuma (public-01) — nieuwe push-monitor + nieuwe notification-channel.

### Nieuwe componenten

- `deploy/grafana/provisioning/alerting/` — volledig nieuw.
- `scripts/audit-alert-secrets.sh`.
- `scripts/verify-alert-runbooks.sh`.
- `docs/runbooks/alerting-rollout.md`.

---

## Rollback plan

Per milestone onafhankelijk rollbaarable:

- **M1:** verwijder `deploy/grafana/provisioning/alerting/` directory en herstart Grafana → alerting uit, oude staat hersteld. Geen impact op dashboards of datasources.
- **M2:** `git revert` op de rules-PR. Grafana herleest, alerts verdwijnen.
- **M3:** runbook-secties blijven staan; zelfs als rules rolled back worden zijn ze nuttig voor manual recovery.
- **M4:** verwijder heartbeat contact point + notification policy. Uptime Kuma monitor triggert dan na 15 min een valse alarm — dus schakel óók Kuma-monitor pause. Gedocumenteerd in runbook.
- **M5:** shadow-rollout bevat per definitie geen productie-impact; rollback is een no-op voor eindgebruikers.
- **M6:** `ALERTS_ROLLOUT_MODE=shadow` herstellen + notification-policies terug naar `slack-shadow`. Shadow-modus hervat direct.
- **M7:** dashboard verwijderen; geen functionele impact.

De enige échte risico-milestone is M2 (live alerts die niet bestonden), en M6 (echte alerts naar echte kanalen). Beide zijn expliciet gated door voorafgaande smoke-tests en drill-runs.

---

## Open questions (bij draft SPEC)

1. **`caddy_http_requests_total` availability.** Nog niet 100% zeker of deze metric in VictoriaMetrics staat; Milestone 2 eerste taak is dit verifiëren. Bij afwezigheid: LogsQL-variant gebruiken (count over `service:caddy AND status:5*`).
2. **`chat.health_failed` structured event bestaat.** Te verifieren via VictoriaLogs MCP: `service:librechat-* AND event:chat.health_failed`. Als de event-name anders is (bijv. `chat_health_failed` of `chatHealthFailed`), regel aanpassen.
3. **`container_last_seen` + `container_restarts_total` via cAdvisor.** Standaard cAdvisor-metrics; bijna zeker aanwezig maar exacte labels te verifieren (`name` vs. `container_label_com_docker_compose_service`).
4. **Snelheid van VictoriaLogs-plugin voor alerting-queries.** Onzeker zonder meting. Milestone 2 bevat een expliciete meet-stap.
5. **Moet `#alerts-emergency` een ander Slack-team zijn dan core-workspace?** Voor volledige isolatie zou een externe Slack-workspace beter zijn, maar operationeel moeilijk. Voorlopig: apart kanaal in dezelfde workspace, apart webhook, apart SOPS-entry — dat dekt de meeste failure-modes.

---

## References

- spec.md (same directory) — requirements en buiten-scope-lijst.
- acceptance.md (same directory) — Given-When-Then scenarios.
- `.claude/rules/klai/infra/observability.md` — bestaande logpijplijn.
- `.claude/rules/klai/infra/deploy.md` — CI deploy-verificatie-regels die ook voor alerting-PRs gelden.
- `.claude/rules/klai/lang/docker.md` — expliciete `environment:` block-regel (portal-api én Grafana moeten nieuwe env-vars expliciet toegevoegd krijgen, geen auto-forward uit `.env`).
- `docs/runbooks/platform-recovery.md#librechat-stale-config-recovery` — prototype runbook-sectie.
- Commit `aab3848c` — interim runbook-commit.
- Commits `a3920a75`, `c5653159` — SEC-021 provisioning rewrite, bron van de FLUSHALL-observabilitykloof.
