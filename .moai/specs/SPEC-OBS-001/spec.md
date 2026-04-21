---
id: SPEC-OBS-001
version: 0.1.0
status: draft
created: 2026-04-19
updated: 2026-04-19
author: Mark Vletter
priority: high
issue_number: null
---

# SPEC-OBS-001: Alerting-infrastructuur voor Klai (vmalert + Grafana provisioning)

## HISTORY

### v0.1.0 (2026-04-19)
- Initiële SPEC, getriggerd door de FLUSHALL-observabilitykloof die aan het licht kwam tijdens SPEC-SEC-021 (de herschrijving van drie portal-api provisioning-paden van `docker exec` naar native protocollen in commits `a3920a75` en `c5653159`).
- Directe aanleiding: een tenant rapporteerde dat een recente config-wijziging niet actief werd. Root cause was `redis_flushall_failed` tijdens de LibreChat config-regenerate: het event stond netjes in VictoriaLogs, maar niemand bevroeg die logs totdat de gebruiker klaagde.
- Commit `aab3848c` voegde een handmatige runbook toe (`docs/runbooks/platform-recovery.md#librechat-stale-config-recovery`) als tussentijdse oplossing. Deze SPEC institutionaliseert dat patroon: elke alert heeft voortaan een runbook, en elk runbook hoort bij een alert — niet andersom.
- Scope bewust beperkt tot MVP-alerting: geen SLO/SLI-framework, geen APM-tracing, geen PagerDuty, geen public status page. Die zijn adjacent en verdienen eigen SPECs zodra deze basis staat en drie maanden stabiel draait.

---

## Goal

Maak operationele infra-fouten zichtbaar binnen minuten, niet "wanneer een gebruiker klaagt". Concreet:

1. **Een alert-engine die LogsQL én PromQL kan evalueren**, geprovisioneerd via git (geen klik-config in Grafana UI).
2. **Een notificatiekanaal voor CRIT-severity** (Slack primair), plus een escalatiekanaal voor repeated CRIT.
3. **Een eerste catalogus van regels** die de concrete gaten uit SEC-021 dicht (FLUSHALL failures, chat health failures, portal-api 5xx, container down), uitbreidbaar per PR zonder re-architectuur.
4. **Een shadow-rollout** waarin alerts een week in een `#alerts-dev` kanaal landen voordat ze de echte on-call route raken — een expliciete fase, niet impliciet weg te poetsen.
5. **Alert-on-alert heartbeat** zodat een stilvallende alerter zelf ook detecteerbaar is.

Succes = de volgende keer dat `redis_flushall_failed` verschijnt, zit het binnen 60 seconden in Slack met een runbook-link. Niet via een ticket van een gebruiker.

---

## Why now

- Directe observabilitykloof: `redis_flushall_failed` (portal-api, SEC-021) en `chat.health_failed` (LibreChat health probes) verschijnen nu alleen in VictoriaLogs. Niemand bevraagt VictoriaLogs proactief.
- Bestaande logpijplijn is compleet: alle services → stdout (JSON via structlog) → Alloy → VictoriaLogs, 30 dagen retentie. `request_id` propagatie werkt end-to-end (Caddy → portal-api → downstream). Zie `.claude/rules/klai/infra/observability.md`.
- Bestaande metrics-pijplijn is compleet: cAdvisor + node-exporter + portal-api `/metrics` + retrieval-api `/metrics` → VictoriaMetrics, 30d retentie. Web-vitals histograms (`webvitals_lcp_seconds` et al.) worden al gescraped.
- Grafana draait, is OIDC-secured (`grafana_admin` Zitadel rol), en heeft datasources geprovisioneerd (`deploy/grafana/provisioning/datasources/datasources.yaml`). Er is géén `alerting/` subdirectory — dat is precies de gap die deze SPEC dicht.
- Het bouwmateriaal ligt al op de plank. Wat ontbreekt is de instrumentatielaag erbovenop.

---

## Scope

### In scope

- Keuze van alert-engine (vmalert vs. Grafana managed alerts), inclusief trade-off rationale en default-aanbeveling.
- Notificatiekanaal-provisioning: Slack primair, secundair kanaal (klai-mailer) voor escalatie. Alle webhook-secrets via SOPS → env vars → provisioning — nooit inline.
- Initiële regelcatalogus (CRIT / HIGH / MED), elke regel met runbook-URL, severity-label, en optionele `for:` (debounce).
- Silencing + acknowledgement workflow.
- Alert-on-alert (heartbeat) mechanisme.
- Shadow-rollout protocol (`#alerts-dev` → `#alerts`) met expliciete exit-criteria voordat naar productie geschakeld wordt.
- Update van `docs/runbooks/platform-recovery.md`: elke nieuwe alert krijgt een bijbehorende runbook-sectie conform het `librechat-stale-config-recovery` prototype.

### Out of scope — expliciet uitgesloten

- **SLO/SLI tracking.** Adjacent onderwerp; verdient een eigen SPEC met error-budget policies, burn-rate alerts, dashboard-design. Deze SPEC gaat over "fire when broken", niet over "measure service health".
- **APM / distributed tracing.** We hebben `request_id`-correlatie via Caddy → middleware → VictoriaLogs. Dat is voor nu genoeg om een request end-to-end te tracen. OpenTelemetry-integratie is later-werk.
- **PagerDuty / Opsgenie.** We beginnen met Slack + email. Pas als de frequentie van CRIT-alerts een echte pager-workflow rechtvaardigt (meer dan 1× per week buiten kantooruren), heroverwegen we escalatie naar een pager-platform.
- **Public status page** (`status.getklai.com`). Uptime Kuma op public-01 dekt de externe status-page behoefte al. Intern-facing alerting en extern-facing status zijn verschillende producten; mengen = ruis.
- **Performance alerting op LLM-latencies** (LiteLLM p99 response time, Ollama queue depth). Metrics zijn beschikbaar maar tuning van thresholds vereist een baseline-periode. Volgende iteratie.
- **Alert-routing per tenant / org.** Alle alerts in MVP zijn infra-breed; tenant-scoped alerting (bijv. "org X's LibreChat container restart loopt") is mogelijk maar nog niet nodig.
- **Volledige migratie van Uptime Kuma alerting.** Uptime Kuma blijft bestaan voor externe black-box probes (public-01, DNS, TLS). Deze SPEC voegt grey-box alerting toe, niet instead-of.

---

## Success Criteria

1. **Alert-engine keuze vastgelegd en gedeployed** — compose-change landt op core-01, container runt gezond, regels worden herkend en geëvalueerd. Documented beslissing inclusief rationale in deze SPEC.
2. **Notificatie werkt end-to-end** — een handmatig getriggerde test-alert arriveert in het geconfigureerde Slack-kanaal met payload die severity, service, summary en runbook-URL bevat.
3. **Geen secrets in git** — audit van het provisioning-pad toont dat alle webhook-URLs en tokens via env vars worden geïnjecteerd uit SOPS. Grep over `deploy/` levert geen `hooks.slack.com/...` op.
4. **Initiële regelcatalogus actief** — minimaal 7 regels van §EARS Requirements zijn geprovisioneerd, evaluaten correct tegen echte productie-data, geen vals-positieven in een 7-daagse shadow-window.
5. **Alert-on-alert fires when alerter stops** — simulatie: stop de alert-engine container, verifieer dat binnen 15 minuten een "alerter-down" signaal verschijnt (via secundair pad, niet via dezelfde alerter).
6. **Runbook-koppeling verplicht** — elke alert-regel heeft een `runbook_url` annotation; CI faalt als een regel die mist.
7. **Shadow → productie gate** — schakeling van `#alerts-dev` naar `#alerts` vereist expliciete bevestiging (PR-approval met checklist) op basis van een 7-daagse zero-false-positive window.
8. **FLUSHALL-gat gedicht** — bij een gesimuleerde `redis_flushall_failed` event in VictoriaLogs arriveert binnen 60 seconden een Slack-bericht met runbook-URL naar `librechat-stale-config-recovery` en de betrokken `slug`.

---

## Alert-engine keuze (decision)

### Opties

**Optie A — vmalert** (`victoriametrics/vmalert`): native alert-engine van de VictoriaMetrics stack. Leest LogsQL rules tegen VictoriaLogs én PromQL rules tegen VictoriaMetrics. Stateless; evaluation-loop + Alertmanager-compatibele output. Geen eigen UI voor silencing — dat gaat via Alertmanager of direct.

**Optie B — Grafana Managed Alerts** (provisioned via `deploy/grafana/provisioning/alerting/`): Grafana's ingebouwde alerting. UI-first (die we via YAML-provisioning dwingen), één plek voor dashboards + alerts + silences. Kan queryen tegen alle Grafana datasources — dus óók VictoriaLogs (via de plugin datasource) én VictoriaMetrics.

### Trade-offs

| Aspect | vmalert | Grafana Managed |
|---|---|---|
| LogsQL over VictoriaLogs | Native, direct | Via plugin (werkt, iets meer latency) |
| PromQL over VictoriaMetrics | Native | Native |
| Silencing UI | Geen eigen (Alertmanager of custom) | Ingebouwd |
| Runbook-URL annotation | Standaard Alertmanager schema | Standaard Grafana schema |
| Provisioning | YAML rule files (simpel) | YAML provisioning (iets meer boilerplate) |
| Secret injection | Env vars in notifier config | Env vars in contact-point provisioning |
| Afhankelijkheden | Nieuwe container + Alertmanager | Geen nieuwe container |
| Complete observability UX | Split tussen Alertmanager + Grafana | Eén UI |

### Default aanbeveling: **Grafana Managed Alerts**

Rationale:
- **Minder nieuwe services.** We hebben Grafana al. vmalert voegt 1-2 containers toe (vmalert + optioneel Alertmanager) voor functionaliteit die Grafana al aanbiedt. Op een single-host setup (core-01) weegt operationele eenvoud zwaarder dan theoretische performance.
- **Unified UX.** Silencing, muting, dashboard-panel-naar-alert koppeling zitten allemaal in dezelfde UI waar dagelijks naar gekeken wordt. Minder context-switching, betere kans dat alerts ook écht opgevolgd worden.
- **Datasource-flexibiliteit.** Via de VictoriaLogs Grafana-plugin (al geïnstalleerd: `GF_INSTALL_PLUGINS: victoriametrics-logs-datasource`) en de Prometheus-datasource naar VictoriaMetrics kunnen we beide signaalbronnen bevragen.
- **Provisioning als code.** `deploy/grafana/provisioning/alerting/` wordt per PR getoucht, reviewable, revertable. Zelfde pad als bestaande datasources + dashboards.
- **OIDC-geauthentiseerd.** Alerting-UI erft Zitadel `grafana_admin` role. Geen tweede auth-oppervlak om te beheren.

vmalert zou voorkeur krijgen als: we meerdere Grafana-tenants zouden draaien (tenant-isolatie in alerts), óf als we al Alertmanager gebruikten voor andere alert-streams. Geen van beide geldt.

### Beslissing: Grafana Managed Alerts

- Alle alert-rules, contact points, notification policies en mute-timings via `deploy/grafana/provisioning/alerting/`.
- Evaluation engine: Grafana's built-in Unified Alerting.
- Als blijkt dat een specifieke LogsQL-regel te traag evalueert via de plugin, kunnen we die specifieke regel later naar vmalert verplaatsen — beide systemen kunnen naast elkaar draaien. Deze flexibiliteit bevestigt de MVP-keuze.

---

## Notificatiekanalen

### Primair: Slack

- Een nieuw Slack-kanaal `#alerts` (productie) + `#alerts-dev` (shadow).
- Inkomende webhook gegenereerd in de Slack-workspace; URL opgeslagen in SOPS als `SLACK_WEBHOOK_ALERTS` en `SLACK_WEBHOOK_ALERTS_DEV`.
- Payload-template: severity-emoji (kleur via Slack attachments), alert-name, korte samenvatting, runbook-URL als button-link, `request_id` / `slug` / `org_id` indien aanwezig.

### Secundair (escalatie-pad): email via klai-mailer

- Voor CRIT-severity die 3× binnen een rolling window (30 min) afgaat, wordt óók een email verstuurd.
- `klai-mailer` service is al gedeployed (Zitadel HTTP notification provider, `ghcr.io/getklai/klai-mailer:latest`). Een nieuw endpoint `POST /api/alerts/email` accepteert de Grafana webhook payload en stuurt email.
- Ontvanger-lijst (`ALERTS_EMAIL_RECIPIENTS`, SOPS) begint bij één adres: `alerts@getklai.com`. Uitbreidbaar zonder provisioning-change.

### [HARD] Geen inline secrets in alert-config

- **Regel:** Webhook-URLs, API-tokens, SMTP-credentials verschijnen **nooit** in `deploy/grafana/provisioning/alerting/*.yaml` als literal. Altijd `${VAR_NAME}` met Docker-compose env-substitution in de provisioning-loader.
- **Verificatie:** `scripts/audit-alert-secrets.sh` grept `deploy/grafana/provisioning/alerting/` voor bekende secret-patronen (`https://hooks.slack.com/`, `xoxb-`, `smtp://[^$]`); CI faalt op hit.
- **Rationale:** git is public in CI-logs, alert-configs worden breed gereviewed, en het kost nul moeite om het goed te doen vanaf dag één.

---

## EARS Requirements

### Alert-engine deployment

**SPEC-OBS-001-R1** — WHEN een PR `deploy/grafana/provisioning/alerting/*.yaml` toevoegt of wijzigt en naar `main` merged, SHALL de deploy-compose workflow de nieuwe provisioning-bestanden op core-01 synchroniseren en Grafana herstarten zodat de rules actief worden.

**SPEC-OBS-001-R2** — WHILE Grafana draait, SHALL het Unified Alerting-mechanisme enabled zijn en SHALL de provisioning directory structuur minimaal bevatten: `alerting/rules/`, `alerting/contact-points/`, `alerting/notification-policies/`, `alerting/mute-timings/`.

**SPEC-OBS-001-R3** — IF Grafana opstart met een syntactisch ongeldig alert-rule bestand, THEN SHALL Grafana de specifieke rule negeren, een structured log op `level:error` emitteren naar VictoriaLogs, en wél opstarten (geen crash-loop op één slechte rule).

### Notification routing

**SPEC-OBS-001-R4** — The alerting-systeem SHALL een contact point `slack-primary` definiëren dat de env var `${SLACK_WEBHOOK_ALERTS}` gebruikt en SHALL een contact point `slack-shadow` definiëren dat `${SLACK_WEBHOOK_ALERTS_DEV}` gebruikt.

**SPEC-OBS-001-R5** — The alerting-systeem SHALL een contact point `email-escalation` definiëren dat via `klai-mailer` emails verstuurt naar `${ALERTS_EMAIL_RECIPIENTS}` met als subject `[KLAI-ALERT-{severity}] {alertname}`.

**SPEC-OBS-001-R6** — WHILE de shadow-rollout fase actief is (flag `ALERTS_ROLLOUT_MODE=shadow`), SHALL alle notification policies naar `slack-shadow` routeren, ongeacht severity.

**SPEC-OBS-001-R7** — WHEN een CRIT-severity alert 3× binnen een rolling 30-minuten venster triggert voor dezelfde `alertname` + labels-hash, THEN SHALL de notification policy óók routeren naar `email-escalation`.

**SPEC-OBS-001-R8** — IF een alert-payload inline een bekend secret-patroon bevat (e.g. `hooks.slack.com/services/`, `xoxb-`, of `-----BEGIN`), THEN SHALL de template-rendering deze vervangen door `[REDACTED]` voordat verzending plaatsvindt.

### Secret hygiene

**SPEC-OBS-001-R9** — The alerting-configuratie SHALL geen literal secrets bevatten; alle webhook-URLs, tokens en credentials SHALL via env-var substitutie geïnjecteerd worden.

**SPEC-OBS-001-R10** — WHEN een PR `deploy/grafana/provisioning/alerting/` wijzigt, SHALL `scripts/audit-alert-secrets.sh` in CI draaien en de PR blokkeren als een literal secret-patroon gedetecteerd wordt.

### Initiële alert-regels (seed catalogue)

**SPEC-OBS-001-R11** (CRIT) — WHILE `sum(rate(caddy_http_requests_total{service="portal-api",status=~"5.."}[5m])) / sum(rate(caddy_http_requests_total{service="portal-api"}[5m]))` boven 0.01 blijft gedurende 5 minuten, SHALL een alert `portal_api_5xx_rate_high` firen op CRIT-severity met annotation `runbook_url: docs/runbooks/platform-recovery.md#portal-api-5xx-surge`.

**SPEC-OBS-001-R12** (CRIT) — WHEN een container met labels `container=~"klai-core-.*"` gedurende 2 minuten geen cAdvisor `container_last_seen` metric update heeft, SHALL een alert `container_down` firen op CRIT-severity.

**SPEC-OBS-001-R13** (CRIT) — WHILE een container restart-rate `rate(container_restarts_total[15m]) > 0` gedurende 15 minuten aanhoudt (sustained restart loop), SHALL een alert `container_restart_loop` firen op CRIT-severity met label `container` gekopieerd naar annotations.

**SPEC-OBS-001-R14** (HIGH) — WHEN een logline matchend `service:portal-api AND event:redis_flushall_failed` in VictoriaLogs verschijnt binnen het laatste evaluatie-interval (30 sec), SHALL een alert `portal_redis_flushall_failed` firen op HIGH-severity met annotation `runbook_url: docs/runbooks/platform-recovery.md#librechat-stale-config-recovery` en `slug` + `request_id` geëxtraheerd uit de log-payload naar annotations.

**SPEC-OBS-001-R15** (HIGH) — WHILE het aantal loglines matchend `service:librechat-* AND event:chat.health_failed` in de laatste 10 minuten boven 5 blijft, SHALL een alert `librechat_health_failed_elevated` firen op HIGH-severity.

**SPEC-OBS-001-R16** (HIGH) — WHILE het aantal loglines matchend `service:knowledge-ingest AND level:error` in de laatste 10 minuten boven 10 blijft, SHALL een alert `ingest_error_rate_elevated` firen op HIGH-severity.

**SPEC-OBS-001-R17** (MED) — WHEN een container met label `service=~"litellm|librechat-.*"` een `container_start_time_seconds` delta vertoont die niet overeenkomt met een geplande deploy (geen GitHub Actions `workflow_dispatch` event in de laatste 30 min voor deze service), SHALL een alert `container_unexpected_restart` firen op MED-severity.

**SPEC-OBS-001-R18** (MED) — WHILE de disk-usage metric `node_filesystem_avail_bytes / node_filesystem_size_bytes` op core-01 voor `mountpoint="/"` onder 0.15 blijft gedurende 30 minuten, SHALL een alert `core01_disk_usage_high` firen op MED-severity.

### Runbook-koppeling

**SPEC-OBS-001-R19** — WHILE een alert-rule geen `runbook_url` annotation heeft of WHERE de annotation verwijst naar een pad dat niet bestaat in de repo, SHALL de CI-check `scripts/verify-alert-runbooks.sh` de PR blokkeren.

**SPEC-OBS-001-R20** — WHEN een nieuwe alert-rule toegevoegd wordt, SHALL `docs/runbooks/platform-recovery.md` een bijbehorende sectie bevatten met dezelfde structuur als `librechat-stale-config-recovery` (Situation → Signal → Step 1..N → Verify → Follow-up), en de `runbook_url` annotation in de rule SHALL daarheen linken.

### Silencing en acknowledgement

**SPEC-OBS-001-R21** — The alerting-systeem SHALL silencing ondersteunen via Grafana's ingebouwde UI (toegankelijk voor `grafana_admin` Zitadel-rol via OIDC), waarbij elke silence verplicht een `comment` veld heeft en een automatische expiry van maximaal 7 dagen.

**SPEC-OBS-001-R22** — The alerting-systeem SHALL mute-timings ondersteunen via provisioned `alerting/mute-timings/*.yaml` bestanden voor voorspelbare onderhoudsvensters (e.g. geplande deploys die korte restart-loops produceren).

**SPEC-OBS-001-R23** — WHILE een alert ge-silenced is, SHALL de silencing in Grafana zichtbaar blijven met creator, reason, en expiry; SHALL bij expiry een status-change-event naar `#alerts-dev` gepost worden zolang shadow-rollout actief is.

### Alert-on-alert (heartbeat)

**SPEC-OBS-001-R24** — The alerting-systeem SHALL een heartbeat alert `klai_alerter_heartbeat` uitsturen naar een externe dead-man's-switch service (Uptime Kuma, `KUMA_TOKEN_ALERTER_HEARTBEAT`) elke 5 minuten.

**SPEC-OBS-001-R25** — IF Uptime Kuma gedurende 15 minuten geen heartbeat ontvangt, THEN SHALL Uptime Kuma een independent notification sturen naar een secundair Slack-kanaal `#alerts-emergency` (separate webhook, separate auth-pad) met de boodschap "Klai alerter is down — check Grafana / vmalert container status".

**SPEC-OBS-001-R26** — The heartbeat-pad SHALL bewust géén gebruik maken van dezelfde notification policies, contact points of webhook-secrets als reguliere alerts, zodat een configuratiefout in reguliere alerting de heartbeat niet uitschakelt.

### Rollout

**SPEC-OBS-001-R27** — WHEN de initiële deploy landt, SHALL de environment variable `ALERTS_ROLLOUT_MODE` op `shadow` staan, SHALL alle alerts naar `slack-shadow` routeren, en SHALL een banner in de Grafana UI "Shadow mode active — alerts routing to #alerts-dev" tonen.

**SPEC-OBS-001-R28** — IF gedurende een continue 7-daagse periode géén false-positive alerts in `#alerts-dev` verschijnen (handmatig bepaald door operator-review, vastgelegd in SPEC-OBS-001 §Rollout log), THEN SHALL een promotie-PR geopend worden die `ALERTS_ROLLOUT_MODE` naar `production` schakelt en notification policies naar `slack-primary` routeert.

**SPEC-OBS-001-R29** — WHILE `ALERTS_ROLLOUT_MODE=production`, SHALL de shadow-banner verdwijnen en SHALL de promotie gelogd worden in `docs/runbooks/alerting-rollout.md` met datum, reviewers, en het aantal bij benadering in shadow-window ge-firede (echte + vals-positieve) alerts.

---

## Buiten scope (explicit exclusions)

Deze SPEC bouwt geen:

1. **SLO/SLI framework.** Error budgets, burn-rate alerts, service-level objectives — allemaal buiten scope. Behoort bij een aparte SPEC-OBS-002 zodra deze basis stabiel is.
2. **APM of distributed tracing.** `request_id` correlatie via Caddy + middleware + VictoriaLogs is voor nu toereikend. OpenTelemetry / Tempo / Jaeger integratie kan later.
3. **PagerDuty / Opsgenie integratie.** Slack + email als MVP. Pager-systemen worden pas geëvalueerd als de CRIT-frequentie buiten kantooruren een echt probleem wordt.
4. **Public-facing status page.** Uptime Kuma draait al extern op public-01 voor externe monitoring. `status.getklai.com` is een productbeslissing, geen alerting-beslissing.
5. **Tenant-scoped alert routing.** Alle alerts in MVP zijn infra-breed. Per-org of per-tenant alerting (bijv. "alleen routeren naar de klant wiens org dit raakt") is mogelijk maar vereist additionele routing-logica en tenant-aware channel-mapping.
6. **LLM-kwaliteit metrics.** Latency / token-rate / cost / hallucination-detection — allemaal belangrijk, niet in MVP.
7. **Auto-remediation / self-healing hooks.** Deze SPEC stuurt notificaties, het probeert niet automatisch redis te restarten of containers te recreaten. Handmatig runbook-werk is expliciet het model.
8. **Alert-historie data warehouse.** Grafana houdt alert-state en history standaard 7 dagen. Langere-termijn analyse (bijv. "hoe vaak fired dit per maand over het laatste jaar") is out-of-scope; als dit nodig blijkt, landen we een long-term alert-archive apart.

---

## Risks and mitigations

| Risico | Mitigatie |
|---|---|
| **Noisy alerts eroderen vertrouwen** — mensen leren alerts negeren. | Shadow-rollout met expliciet 7-daags zero-false-positive gate voordat alerts naar productie routeren. Elke false-positive in shadow triggert een tune-PR; geen promotie zonder gate-pass. |
| **Secret leak via alert payloads** — een structured log met `Authorization: Bearer …` wordt naar Slack gepost. | SPEC-OBS-001-R8 (template-redaction op bekende patterns), plus structlog-side rule: services mogen geen Authorization-headers in logs emitteren. Reuse van bestaande Semgrep-regel `python-logger-credential-disclosure` op de portal-api repo om injection te voorkomen. |
| **Alerter zelf gaat stil** — container crash, networking issue, config-fout maakt alle alerts onzichtbaar. | Alert-on-alert heartbeat via onafhankelijk pad (Uptime Kuma + apart webhook). SPEC-OBS-001-R24..R26 beschrijven de scheiding. Dead-man's switch werkt zonder Grafana. |
| **Slack webhook-secret lekt in git via config-file foutje.** | SPEC-OBS-001-R9/R10: CI-audit `scripts/audit-alert-secrets.sh` blokkeert PR bij literal secret-patroon. Env-var substitution is de enige legitieme route. |
| **Grafana-plugin latency voor VictoriaLogs-queries maakt alert-evaluation onbetrouwbaar.** | Start met 30s evaluatie-interval (comfortabel ruim voor de plugin). Monitor query-latency via Grafana's built-in `grafana_alerting_rule_evaluation_duration_seconds`. Als >p95 > 10s: specifieke LogsQL-regel verplaatsen naar vmalert (geen re-architectuur nodig — beide engines kunnen coëxisteren). |
| **Alert fatigue door te agressieve thresholds** in initiële catalogus. | Alle thresholds in §EARS zijn startwaarden, expliciet tune-baar. Rollout log (`docs/runbooks/alerting-rollout.md`) captures threshold-tuning per week. |
| **Grafana config-reload bij provisioning-change veroorzaakt brief downtime.** | Provisioning reload is live-reloadable in recent Grafana versies; getest voordat `deploy-compose.yml` op `main` landt. Worst case: 30s Grafana downtime acceptabel (geen data-impact, alleen UI). |
| **Runbook verwijst naar niet-bestaande procedure.** | SPEC-OBS-001-R19: CI-check `scripts/verify-alert-runbooks.sh` resolved elke `runbook_url` annotation tegen het repo-bestand + anchor en faalt bij 404. |

---

## Migration plan

Het bestaande runbook `docs/runbooks/platform-recovery.md#librechat-stale-config-recovery` is het **prototype** voor hoe deze SPEC alerts aan runbooks koppelt:

1. **Situation** — wat is er kapot (in plaintext, niet "error code 4271").
2. **Signal to look for** — exacte LogsQL of PromQL query; matches wat de alert ook evalueert.
3. **Step 1..N** — deterministische recovery-stappen, kopieer-plakbaar.
4. **Verify** — hoe bevestig je dat het opgelost is.
5. **Follow-up** — dependency op deze SPEC (expliciet genoemd in de huidige runbook).

Bij deze SPEC wordt dat patroon **verplicht**: elke alert-rule refereert met `runbook_url` naar een runbook-sectie die deze structuur volgt, en CI dwingt beide kanten af (SPEC-OBS-001-R19, R20).

De reeds aanwezige `librechat-stale-config-recovery` sectie blijft onveranderd — de nieuwe alert `portal_redis_flushall_failed` (SPEC-OBS-001-R14) linkt rechtstreeks daarheen. Wat voorheen een handmatig periodiek VictoriaLogs-querijtje was (week-retro stijl) wordt nu een 60-seconden notification.

---

## Definition of Done

- Grafana Unified Alerting enabled, provisioning directory-structuur aanwezig, alle regels uit SPEC-OBS-001-R11..R18 geprovisioneerd en evaluating.
- Contact points voor `slack-primary`, `slack-shadow`, `email-escalation` geprovisioneerd, secrets uitsluitend via env vars uit SOPS.
- Alle nieuwe alert-rules hebben een `runbook_url` annotation; `scripts/verify-alert-runbooks.sh` passes in CI; `scripts/audit-alert-secrets.sh` passes in CI.
- Alert-on-alert heartbeat draait via Uptime Kuma, onafhankelijk pad geverifieerd door gesimuleerde alerter-down drill.
- Shadow-rollout 7-daags venster afgerond zonder false-positives in `#alerts-dev`; promotie-PR met checklist gemerged; `ALERTS_ROLLOUT_MODE=production` actief.
- `docs/runbooks/platform-recovery.md` bijgewerkt met een nieuwe sectie per alert uit §EARS Requirements.
- `docs/runbooks/alerting-rollout.md` aangemaakt met een log van shadow → productie promotie.
- Commit `aab3848c` en SPEC-SEC-021 gecross-referenced in de SPEC (deze sectie).
- FLUSHALL-drill: een handmatig geïnjecteerde `event:redis_flushall_failed` logline produceert binnen 60 seconden een Slack-notificatie in het juiste kanaal met de juiste runbook-link. Screenshot of recording bijgesloten bij de sluit-PR.

---

## References

- `docs/runbooks/platform-recovery.md#librechat-stale-config-recovery` — het prototype-runbook (commit `aab3848c`) dat deze SPEC institutionaliseert.
- `.moai/specs/SPEC-SEC-021/spec.md` — de provisioning-rewrite die de FLUSHALL-observabilitykloof blootlegde (commits `a3920a75`, `c5653159`).
- `.claude/rules/klai/infra/observability.md` — bestaande logpijplijn, trace-correlatie, VictoriaLogs/Grafana MCP-gebruik.
- `.claude/rules/klai/platform/docker-socket-proxy.md` — context voor SEC-021 en waarom native protocollen nu `redis_flushall_failed` events produceren.
- `deploy/docker-compose.yml` — bestaande Grafana / VictoriaMetrics / VictoriaLogs / Alloy setup.
- `deploy/alloy/config.alloy` — logpijplijn naar VictoriaLogs; mogelijk uitbreidbaar met `loki.rules` voor vmalert indien ooit nodig.
- `deploy/grafana/provisioning/datasources/datasources.yaml` — VictoriaMetrics + VictoriaLogs datasources (reeds aanwezig; deze SPEC voegt `alerting/` toe).
- `klai-portal/backend/app/services/events.py` — bestaande `product_events` pipeline (Postgres), referentie voor event-structuur.
- `klai-portal/backend/app/api/vitals.py` — bestaande Prometheus histogram-metrics, referentie voor PromQL-targets.
- `scripts/victorialogs-tunnel.sh` — SSH-tunnel naar VictoriaLogs voor MCP-queries; parallel aan alerting-pad, blijft bestaan voor handmatig debugging.
- `klai-infra/core-01/.env.sops` — SOPS-encrypted env file waarin `SLACK_WEBHOOK_ALERTS*`, `ALERTS_EMAIL_RECIPIENTS`, `KUMA_TOKEN_ALERTER_HEARTBEAT` landen.
