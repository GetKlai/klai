---
id: SPEC-OBS-001
version: 0.2.1
status: draft
created: 2026-04-19
updated: 2026-04-22
author: Mark Vletter
priority: high
---

# SPEC-OBS-001 — Implementation Plan

## Approach in één paragraaf

Grafana Unified Alerting is al geactiveerd door SEC-024 (inclusief GF_SMTP via Cloud86, contact point `klai-dev-alerts-email`, eerste rule + dashboard). Deze SPEC **breidt** die infrastructuur uit: nieuw contact point `klai-ops-alerts-email` (type: email, hergebruikt GF_SMTP), een initiële regelcatalogus die de golden signals (latency, traffic, errors, saturation) dekt plus de concrete FLUSHALL/container-health gaten, en een alert-on-alert hartslag via Uptime Kuma op public-01 met een apart SMTP-account (niet Cloud86). Alle credentials via env-var substitution uit `klai-infra/core-01/.env.sops` (reeds aanwezig patroon). Alerts gaan direct live (geen shadow-fase) en worden reactief getuned bij false-positives. LogsQL-regels gebruiken expliciete `field:value` syntax (SEC-024 pitfall). Provisioning-reload via `docker compose up -d grafana` (recreate), niet `restart` (SEC-024 operational fix).

---

## Milestones (prioriteitsvolgorde — geen tijdsschattingen)

### Milestone 1 — Foundations (Priority: High)

**Doel:** nieuw `klai-ops-alerts-email` contact point draait op de bestaande GF_SMTP-infrastructuur; test-mail werkt end-to-end.

Deliverables:
- `deploy/grafana/provisioning/alerting/README.md` — uitleg directory-structuur, secret-conventies, review-checklist, SEC-024 cross-ref.
- `deploy/grafana/provisioning/alerting/contact-points/ops-email.yaml` — `klai-ops-alerts-email` (type: email) met `addresses: ${ALERTS_EMAIL_RECIPIENTS}` en subject-template `[KLAI-ALERT-{severity}] {alertname}`. Naast de reeds aanwezige `klai-dev-alerts-email` (SEC-024), niet als vervanging.
- `deploy/grafana/provisioning/alerting/notification-policies/default.yaml` — bovenop SEC-024's bestaande policy: OBS-001 rules → `klai-ops-alerts-email`; SEC-024 rules blijven routeren naar `klai-dev-alerts-email` (behoud bestaand gedrag).
- `deploy/grafana/provisioning/alerting/mute-timings/deploys.yaml` — voorgefabriceerde mute-timings voor gangbare onderhoudsvensters (empty file met comments als startpunt, indien nog niet aanwezig).
- SOPS-update (`klai-infra/core-01/.env.sops`): `ALERTS_EMAIL_RECIPIENTS` toegevoegd. `GRAFANA_SMTP_PASSWORD` en `GF_SMTP_*` bestaan al (SEC-024 commit `994b504`) — ongemoeid laten.
- `deploy/docker-compose.yml` update: Grafana environment-block uitgebreid met `ALERTS_EMAIL_RECIPIENTS` (Grafana heeft expliciete `environment:` block dat niet auto-forwardt — zie `.claude/rules/klai/lang/docker.md`).

Exit-criteria:
- Grafana herkent het nieuwe contact point na `docker compose up -d grafana` (NIET `restart` — SEC-024 defect 2: bind-mount provisioning vereist recreate); check via Grafana UI → Alerting → Contact points.
- Handmatig test-bericht via contact point `klai-ops-alerts-email` arriveert binnen 60 seconden in de `ALERTS_EMAIL_RECIPIENTS` mailbox, met nette subject en body.
- Bestaande `klai-dev-alerts-email` contact point van SEC-024 blijft onveranderd werken (regressie-check: trigger een `spec-sec-024-proxy-denials` fire en verifieer dat de mail nog aankomt).
- `scripts/audit-alert-secrets.sh` draait lokaal en op CI, passes.

Dependencies:
- SEC-024 M4.5 is afgerond en de Grafana SMTP-infrastructuur draait stabiel.

### Milestone 2 — Seed alert catalogue (Priority: High)

**Doel:** de regels uit SPEC-OBS-001-R9..R17 geprovisioneerd en evaluerend, allemaal gerouteerd naar `email-primary`.

Deliverables:
- `deploy/grafana/provisioning/alerting/rules/portal-api.yaml` — `portal_api_5xx_rate_high` (R9), `portal_api_latency_high` (R10), `portal_api_traffic_drop` (R11).
- `deploy/grafana/provisioning/alerting/rules/infra-containers.yaml` — `container_down` (R12), `container_restart_loop` (R13).
- `deploy/grafana/provisioning/alerting/rules/portal-events.yaml` — `portal_redis_flushall_failed` (R14, LogsQL via VictoriaLogs-plugin datasource).
- `deploy/grafana/provisioning/alerting/rules/librechat-health.yaml` — `librechat_health_failed_elevated` (R15).
- `deploy/grafana/provisioning/alerting/rules/ingest.yaml` — `ingest_error_rate_elevated` (R16).
- `deploy/grafana/provisioning/alerting/rules/node.yaml` — `core01_disk_usage_high` (R17).
- `scripts/verify-alert-runbooks.sh` — parseert alle `runbook_url` annotations, resolved tegen repo, faalt op 404.
- `scripts/audit-alert-secrets.sh` — grept op bekende credential-patterns.
- CI-integratie: `.github/workflows/alerting-check.yml` draait beide scripts voor PRs die `deploy/grafana/provisioning/alerting/**` touchen.

Exit-criteria:
- Alle 9 regels zichtbaar in Grafana → Alerting → Rules, status "OK" of "Firing" (geen "Error").
- LogsQL-query latency p95 < 5s (instrumentatie via `grafana_alerting_rule_evaluation_duration_seconds`).
- Alle regels hebben `runbook_url`, alle `runbook_url`s resolven tegen repo-bestanden.
- FLUSHALL-drill: handmatig injected event produceert binnen 60s een mail in de alerts-mailbox.

Dependencies:
- Milestone 1 moet live staan (nieuw contact point werkend).
- VictoriaLogs-plugin LogsQL-features werken al (SEC-024 verifieerde dat `error:Forbidden AND error:docker-socket-proxy` correct matcht). Onze regels volgen hetzelfde `field:value` patroon.
- Beschikbaarheidscheck van metrics (`caddy_http_requests_total`, `caddy_http_request_duration_seconds_bucket`, `container_last_seen`, `container_restarts_total`, `node_filesystem_avail_bytes`). Fallback-paden per regel gedocumenteerd in `.claude/rules/klai/infra/observability.md`.

### Milestone 3 — Runbooks uitbreiden (Priority: High)

**Doel:** elke alert uit Milestone 2 heeft een passende recovery-sectie in `platform-recovery.md`.

Deliverables:
- `docs/runbooks/platform-recovery.md` uitbreiding met secties:
  - `portal-api-5xx-surge` (nieuw)
  - `portal-api-latency-surge` (nieuw)
  - `portal-api-traffic-drop` (nieuw)
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

### Milestone 4 — Alert-on-alert hartslag (Priority: High)

**Doel:** een stille alerter detecteerbaar via een pad dat volledig losstaat van core-01 en klai-mailer.

Deliverables:
- `deploy/grafana/provisioning/alerting/contact-points/heartbeat.yaml` — contact point `heartbeat-kuma` dat elke 5 min een `GET` naar Uptime Kuma doet via `${KUMA_HEARTBEAT_URL}`.
- `deploy/grafana/provisioning/alerting/rules/heartbeat.yaml` — synthetische rule die altijd fires (condition: `1 > 0`) met 5min interval; eigen notification-policy die uitsluitend deze rule naar `heartbeat-kuma` routeert.
- Uptime Kuma (public-01): nieuwe push-monitor `alerter-heartbeat` met token `${KUMA_HEARTBEAT_TOKEN}`, verwacht elke 5 min heartbeat, raises alert after 15 min stilte.
- Uptime Kuma notification-channel: SMTP-configuratie via public-01's eigen SOPS (apart van core-01), ontvanger-lijst `ALERTS_EMERGENCY_EMAIL_RECIPIENTS`.
- `docs/runbooks/platform-recovery.md` nieuwe sectie `alerter-down-recovery`.

Exit-criteria:
- Stop Grafana container → binnen 15 min arriveert mail in `ALERTS_EMERGENCY_EMAIL_RECIPIENTS` via Uptime Kuma SMTP.
- Verificatie: mail-headers tonen Uptime Kuma's SMTP-relay, niet klai-mailer.
- Stop klai-mailer container → hartslag blijft werken (alleen reguliere alerts kunnen niet gemaild worden; dat wordt gedetecteerd via de bestaande `container_down` rule).
- Stop core-01 helemaal → binnen 15 min arriveert alerter-down mail (Uptime Kuma op public-01 gebruikt zijn eigen SMTP).

Dependencies:
- Uptime Kuma heeft SMTP-configuratie beschikbaar (existing of nieuw toe te voegen in public-01 SOPS).
- Nieuwe env-vars toegevoegd aan relevante SOPS-scopes.

### Milestone 5 — Post-rollout observability (Priority: Medium)

**Doel:** weten of de alerting zelf werkt zoals bedoeld; voorbereiden op reactieve tuning.

Deliverables:
- Nieuw Grafana dashboard "Alerting Health": fire-rate per alert, evaluation latency per rule, silence-coverage, mute-timing hit-rate.
- `docs/runbooks/alerting-rollout.md` — bevat:
  - Initial-rollout-log (datum, wie, welke regels eerst live).
  - Per-alert tuning-log: iedere threshold-wijziging krijgt een entry (datum, regel, voor → na, rationale).
  - Quarterly review-template: per regel vraag "zit deze in de top-5 noise-makers? moeten we tighter of juist looser?".

Exit-criteria:
- Dashboard gedeployed, toegankelijk voor `grafana_admin`.
- Review-template in runbook-directory.
- Eerste kwartaalreview ingepland (placeholder calendar-invite / issue).

Dependencies:
- Milestone 4 afgerond (alles draait).

---

## Technical approach

### Provisioning directory layout

De directory bestaat al (SEC-024). Deze SPEC voegt bestanden toe, raakt SEC-024's bestanden niet aan.

```
deploy/grafana/provisioning/alerting/
├── README.md
├── contact-points/
│   ├── dev-alerts.yaml       # [SEC-024] klai-dev-alerts-email (ongemoeid)
│   ├── ops-email.yaml        # [NIEUW] klai-ops-alerts-email (native email, GF_SMTP)
│   └── heartbeat.yaml        # [NIEUW] heartbeat-kuma (push to Uptime Kuma)
├── notification-policies/
│   └── default.yaml          # [UITGEBREID] SEC-024 routes + OBS-001 routes
├── mute-timings/
│   └── deploys.yaml          # (empty initially; operator-populated as needed)
└── rules/
    ├── security.yaml         # [SEC-024] spec-sec-024-proxy-denials (ongemoeid)
    ├── portal-api.yaml       # [NIEUW] portal_api_5xx_rate_high, portal_api_latency_high, portal_api_traffic_drop
    ├── infra-containers.yaml # [NIEUW] container_down, container_restart_loop
    ├── portal-events.yaml    # [NIEUW] portal_redis_flushall_failed
    ├── librechat-health.yaml # [NIEUW] librechat_health_failed_elevated
    ├── ingest.yaml           # [NIEUW] ingest_error_rate_elevated
    ├── node.yaml             # [NIEUW] core01_disk_usage_high
    └── heartbeat.yaml        # [NIEUW] synthetic always-fires rule (goes to heartbeat-kuma)
```

### Contact point (native email, hergebruikt GF_SMTP)

Grafana's native `email` contact-point-type gebruikt de `GF_SMTP_*` configuratie die SEC-024 al heeft gezet. Geen tussenlaag, geen webhook.

```yaml
# deploy/grafana/provisioning/alerting/contact-points/ops-email.yaml
apiVersion: 1
contactPoints:
  - orgId: 1
    name: klai-ops-alerts-email
    receivers:
      - uid: klai-ops-alerts-email-1
        type: email
        settings:
          addresses: ${ALERTS_EMAIL_RECIPIENTS}
          singleEmail: false  # one email per alert, easier to triage
          subject: '[KLAI-ALERT-{{ .CommonLabels.severity | default "info" }}] {{ .CommonLabels.alertname }}'
          message: |
            {{ range .Alerts }}
            Severity: {{ .Labels.severity }}
            Service: {{ .Labels.service }}
            Summary: {{ .Annotations.summary }}
            Runbook: https://github.com/getklai/klai/blob/main/{{ .Annotations.runbook_url }}
            Started: {{ .StartsAt }}
            {{ if .Labels.request_id }}request_id: {{ .Labels.request_id }}{{ end }}
            {{ if .Labels.slug }}slug: {{ .Labels.slug }}{{ end }}
            {{ end }}
```

Secret injection via env var (`${ALERTS_EMAIL_RECIPIENTS}`). SMTP-credentials (`GRAFANA_SMTP_PASSWORD`) zitten in `GF_SMTP_PASSWORD` env var (al geconfigureerd door SEC-024), niet in deze YAML.

Docker-compose zorgt dat de nieuwe env-var beschikbaar is:

```yaml
# deploy/docker-compose.yml (grafana service)
environment:
  # Reeds aanwezig (SEC-024):
  GF_SMTP_ENABLED: "true"
  GF_SMTP_HOST: ${GRAFANA_SMTP_HOST}
  GF_SMTP_USER: ${GRAFANA_SMTP_USER}
  GF_SMTP_PASSWORD: ${GRAFANA_SMTP_PASSWORD}
  GF_SMTP_FROM_ADDRESS: ${GRAFANA_SMTP_FROM_ADDRESS}
  # Nieuw (OBS-001):
  ALERTS_EMAIL_RECIPIENTS: ${ALERTS_EMAIL_RECIPIENTS}
  KUMA_HEARTBEAT_URL: ${KUMA_HEARTBEAT_URL}
```

De env-vars komen uit `/opt/klai/.env`, beheerd via SOPS in `klai-infra/core-01/.env.sops` (standaard klai-infra patroon).

### Provisioning reload (SEC-024 operational rule)

Bind-mount provisioning-files worden pas bij container-recreate opnieuw ingelezen. `docker compose restart grafana` is **niet** voldoende — Grafana leest de mount niet opnieuw.

- **Fout**: `docker compose restart grafana` → oude config blijft actief.
- **Goed**: `docker compose up -d grafana` → container recreate, mount herlezen, nieuwe config actief.

Deploy-compose workflow volgt reeds dit patroon (SEC-024 commit `2b0f697f`).

### LogsQL field-scoping (SEC-024 pitfall)

VictoriaLogs `_msg` is de default search-scope voor unqualified queries. structlog output zet events in **structured fields**, niet in `_msg`. Gevolg: `event:redis_flushall_failed` matcht; een free-text `redis_flushall_failed` matcht niet.

- **Fout**: `expr: 'redis_flushall_failed'` → 0 matches, rule fires nooit.
- **Goed**: `expr: 'service:portal-api AND event:redis_flushall_failed'` → matcht correct.

Elke LogsQL-regel in de catalogus wordt handmatig gevalideerd via de VictoriaLogs MCP tool vóór hij in een provisioning-file landt.

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

Standaard patroon. Voorbeeld `portal_api_5xx_rate_high`:

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

### Latency-alert (R10) — golden signal: latency

```yaml
- uid: portal-api-latency-high
  title: portal_api_latency_high
  data:
    - refId: A
      datasourceUid: victoriametrics
      model:
        expr: |
          histogram_quantile(0.95,
            sum(rate(caddy_http_request_duration_seconds_bucket{service="portal-api"}[5m])) by (le))
        instant: true
    - refId: C
      datasourceUid: __expr__
      model:
        type: threshold
        expression: A
        conditions:
          - evaluator: { params: [2.0], type: gt }
  for: 5m
  annotations:
    summary: 'portal-api p95 latency > 2.0s over 5m ({{ $value | humanizeDuration }})'
    runbook_url: 'docs/runbooks/platform-recovery.md#portal-api-latency-surge'
  labels:
    severity: critical
    service: portal-api
```

### Traffic-drop alert (R11) — golden signal: traffic

Detecteert ongebruikelijke stilte. Alleen actief tijdens kantooruren om nachtelijke lage-traffic-perioden niet als false-positive te tellen.

```yaml
- uid: portal-api-traffic-drop
  title: portal_api_traffic_drop
  data:
    - refId: A
      datasourceUid: victoriametrics
      model:
        expr: |
          (
            sum(rate(caddy_http_requests_total{service="portal-api"}[5m]))
            /
            sum(avg_over_time(
              rate(caddy_http_requests_total{service="portal-api"}[5m])[1h:1m]
            ))
          )
          and on() (hour() >= 8 and hour() < 20 and day_of_week() > 0 and day_of_week() < 6)
        instant: true
    - refId: C
      datasourceUid: __expr__
      model:
        type: threshold
        expression: A
        conditions:
          - evaluator: { params: [0.2], type: lt }  # < 20% of hourly baseline
  for: 10m
  annotations:
    summary: 'portal-api traffic dropped to {{ $value | humanizePercentage }} of hourly baseline'
    runbook_url: 'docs/runbooks/platform-recovery.md#portal-api-traffic-drop'
  labels:
    severity: critical
    service: portal-api
```

Noot: `hour()` en `day_of_week()` zijn PromQL-functies die UTC gebruiken. Europe/Amsterdam is UTC+1/+2 — pas offset toe bij implementatie, of gebruik een recording rule met timezone-aware labels. Exacte implementatie valideren tijdens M2.

### Alert-on-alert hartslag

Grafana contact point type `webhook` met GET naar Uptime Kuma's push-monitor:

```yaml
# deploy/grafana/provisioning/alerting/contact-points/heartbeat.yaml
apiVersion: 1
contactPoints:
  - orgId: 1
    name: heartbeat-kuma
    receivers:
      - uid: heartbeat-kuma-1
        type: webhook
        settings:
          url: ${KUMA_HEARTBEAT_URL}
          httpMethod: GET
```

`KUMA_HEARTBEAT_URL` heeft de vorm `https://status.getklai.com/api/push/{token}?status=up&msg=ok` — token zit dus in de URL zelf (standaard Uptime Kuma push-patroon).

Synthetische alert-rule die altijd fires, op 5-min interval, gerouteerd naar `heartbeat-kuma` via een aparte notification policy:

```yaml
# deploy/grafana/provisioning/alerting/rules/heartbeat.yaml
apiVersion: 1
groups:
  - orgId: 1
    name: heartbeat
    folder: Klai
    interval: 5m
    rules:
      - uid: alerter-heartbeat
        title: alerter_heartbeat
        condition: C
        data:
          - refId: A
            datasourceUid: __expr__
            model: { type: math, expression: '1' }
          - refId: C
            datasourceUid: __expr__
            model:
              type: threshold
              expression: A
              conditions:
                - evaluator: { params: [0], type: gt }
        for: 0s
        labels:
          alert_type: heartbeat
        annotations:
          summary: 'Heartbeat tick'
```

Notification policy routing op `alert_type=heartbeat` naar `heartbeat-kuma`; alle andere alerts gaan naar `email-primary`.

**Belangrijk:** de dead-man's-switch notification gaat via Uptime Kuma's **eigen SMTP-configuratie** op public-01 — dat staat los van core-01's klai-mailer. Als core-01 volledig uitvalt blijft Uptime Kuma werken en kan alsnog mailen. Configuratie van die SMTP-instelling gebeurt in Uptime Kuma's admin-UI of zijn eigen config-file op public-01.

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

`scripts/audit-alert-secrets.sh` grept op bekende patterns (`xoxb-`, `-----BEGIN`, `smtp://[^$]`, literal email+password combos) en faalt op hit.

`scripts/verify-alert-runbooks.sh` parseert YAML in `deploy/grafana/provisioning/alerting/rules/`, extracteert elke `runbook_url`, splitst op `#`, verifieert dat de file bestaat en dat de anchor overeenkomt met een kop in het Markdown-bestand.

---

## Risks en aannames

### Aannames (testbaar)

1. **De VictoriaLogs Grafana-plugin ondersteunt LogsQL queries in alert-rules met voldoende betrouwbaarheid.** Wordt geverifieerd in Milestone 2 via een query-latency smoke-test. Fallback: migreer specifieke rules naar vmalert (kost één extra container, geen architectuur-change).
2. **Grafana reload van provisioning-files leidt niet tot alert-misvuren tijdens reload.** Grafana ≥ 10 heeft live-reload zonder downtime voor alerting; te valideren bij de eerste provisioning-change na initiale deploy.
3. **Uptime Kuma push-webhook + eigen SMTP werkt betrouwbaar als dead-man's switch.** Uptime Kuma draait al op public-01 voor externe monitoring. Push-monitor + SMTP-notification-channel zijn standaardfunctionaliteit.
4. **klai-mailer kan een nieuw `/api/alerts/email` endpoint krijgen zonder impact op zijn Zitadel-rol.** Endpoint-scope is additief; geen wijziging aan bestaande Zitadel notification-flows.
5. **Caddy metrieken `caddy_http_requests_total` en `caddy_http_request_duration_seconds_bucket` zijn beschikbaar in VictoriaMetrics.** Te verifiëren als eerste taak in M2 — Caddy heeft JSON-logging sinds SPEC-INFRA-004, en we moeten controleren of een Prometheus-exporter deze metrics reeds scraped. Als dat ontbreekt: alternatief is een LogsQL-count/latency-histogram via VictoriaLogs (eveneens werkbaar).
6. **PromQL time-functies (`hour()`, `day_of_week()`) werken zoals verwacht voor de traffic-drop window in R11.** Offset voor Europe/Amsterdam mogelijk nodig; tijdens M2 empirisch vaststellen.

### Risico-mitigatie per milestone

- **Milestone 1:** env-var substitution kan misgaan bij verkeerd escape-karakter. Mitigatie: lokale smoke-test met een dummy-webhook vóór SOPS-update.
- **Milestone 2:** LogsQL query kan syntax-fouten bevatten. Mitigatie: elke query eerst handmatig via VictoriaLogs MCP tool testen, vervolgens pasten. Traffic-drop query (R11) extra zorgvuldig valideren — time-window-predicaat is gevoelig voor PromQL-versie-verschillen.
- **Milestone 3:** runbooks dupliceren bestaande informatie. Mitigatie: volg het prototype-format strict; geen lange prose.
- **Milestone 4:** Uptime Kuma SMTP-config moet bestaan op public-01 en los staan van core-01. Mitigatie: eerst controleren; indien ontbrekend, toevoegen als onderdeel van deze milestone (apart SOPS-entry in public-01 scope).
- **Milestone 5:** nice-to-have, niet blocking. Kan later landen.

---

## Dependencies en integratiepunten

### Nieuwe environment variables (SOPS)

| Var | Gebruik | SOPS-scope | Toegevoegd in milestone |
|---|---|---|---|
| `ALERTS_EMAIL_RECIPIENTS` | Comma-separated ontvangers voor reguliere ops-alerts | `klai-infra/core-01/.env.sops` | M1 |
| `KUMA_HEARTBEAT_URL` | Uptime Kuma push-URL (bevat token) | `klai-infra/core-01/.env.sops` | M4 |
| `ALERTS_EMERGENCY_EMAIL_RECIPIENTS` | Ontvangers voor alerter-down mails | public-01 SOPS-scope | M4 |
| Uptime Kuma SMTP-account (apart van Cloud86) | Onafhankelijke SMTP voor hartslag-mails | public-01 SOPS-scope of Uptime Kuma UI | M4 |

**Reeds aanwezig (SEC-024)** — niet toevoegen, alleen referen:
- `GRAFANA_SMTP_PASSWORD`, `GRAFANA_SMTP_HOST`, `GRAFANA_SMTP_USER`, `GRAFANA_SMTP_FROM_ADDRESS` — Cloud86 SMTP, in `klai-infra/core-01/.env.sops` commit `994b504`.

### Bestaande componenten die uitgebreid worden

- `deploy/docker-compose.yml` — Grafana `environment:` block krijgt `ALERTS_EMAIL_RECIPIENTS`, `KUMA_HEARTBEAT_URL` erbij.
- `deploy/grafana/provisioning/alerting/` — bestaande directory (SEC-024) krijgt nieuwe bestanden; SEC-024 bestanden blijven ongemoeid.
- `docs/runbooks/platform-recovery.md` — acht nieuwe secties plus één voor alerter-down.
- `.github/workflows/` — nieuwe `alerting-check.yml`.
- Uptime Kuma (public-01) — nieuwe push-monitor + notification-channel met eigen SMTP-account.

### Nieuwe componenten

- `deploy/grafana/provisioning/alerting/contact-points/ops-email.yaml` en rules-bestanden (zie directory-layout).
- `scripts/audit-alert-secrets.sh`.
- `scripts/verify-alert-runbooks.sh`.
- `docs/runbooks/alerting-rollout.md` (bevat initial-rollout-log en quarterly review-entries).

### Niet langer nodig (was in v0.2.0, geschrapt in v0.2.1)

- `klai-mailer` endpoint `POST /api/alerts/email` — vervangen door Grafana native email via bestaande GF_SMTP.
- `KLAI_MAILER_ALERT_WEBHOOK_URL` env var — niet meer van toepassing.

---

## Rollback plan

Per milestone onafhankelijk rollbaar:

- **M1:** verwijder alleen de nieuwe OBS-001 bestanden uit `deploy/grafana/provisioning/alerting/`; SEC-024 bestanden blijven. `docker compose up -d grafana` → alert-catalogus terug naar SEC-024-only staat. Geen impact op dashboards of datasources.
- **M2:** `git revert` op de rules-PR. Grafana herleest, alerts verdwijnen.
- **M3:** runbook-secties blijven staan; zelfs als rules rolled back worden zijn ze nuttig voor manual recovery.
- **M4:** verwijder heartbeat contact point + notification policy + rule. Uptime Kuma monitor zal na 15 min een valse alerter-down mail produceren — dus schakel óók Kuma-monitor pause. Gedocumenteerd in runbook.
- **M5:** dashboard verwijderen; geen functionele impact.

De échte risico-milestone is M2 (live alerts die niet bestonden). Mitigatie: drills uitvoeren (FLUSHALL, container stop) vóór merge, niet erna.

---

## Open questions (bij draft SPEC)

1. **`caddy_http_requests_total` en `caddy_http_request_duration_seconds_bucket` availability.** Nog niet 100% zeker of deze metrics in VictoriaMetrics staan; Milestone 2 eerste taak is dit verifiëren. Bij afwezigheid: LogsQL-variant gebruiken.
2. **`chat.health_failed` structured event bestaat.** Te verifiëren via VictoriaLogs MCP: `service:librechat-* AND event:chat.health_failed`. Als de event-name anders is (bijv. `chat_health_failed` of `chatHealthFailed`), regel aanpassen.
3. **`container_last_seen` + `container_restarts_total` via cAdvisor.** Standaard cAdvisor-metrics; bijna zeker aanwezig maar exacte labels te verifiëren (`name` vs. `container_label_com_docker_compose_service`).
4. **Snelheid van VictoriaLogs-plugin voor alerting-queries.** Onzeker zonder meting. Milestone 2 bevat een expliciete meet-stap.
5. **Uptime Kuma SMTP-config status op public-01.** Moet bestaande config blijken of als onderdeel van M4 toegevoegd worden. Beslissing: checken aan begin van M4.
6. **PromQL time-window-predicaat correctheid voor R11 (traffic-drop).** Experimenteel te valideren in M2.

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
- Google SRE — Monitoring Distributed Systems (https://sre.google/sre-book/monitoring-distributed-systems/) — rationale voor golden signals en symptoom-georiënteerde regels.
