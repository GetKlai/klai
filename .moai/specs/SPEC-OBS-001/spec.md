---
id: SPEC-OBS-001
version: 0.2.0
status: draft
created: 2026-04-19
updated: 2026-04-21
author: Mark Vletter
priority: high
issue_number: null
---

# SPEC-OBS-001: Alerting-infrastructuur voor Klai (Grafana Unified Alerting + email)

## HISTORY

### v0.2.0 (2026-04-21)

Product-sparring met Mark heeft de volgende beslissingen opgeleverd, die in deze versie zijn verwerkt:

- **Notificatie via e-mail, niet Slack.** Klai gebruikt geen Slack. E-mail via de bestaande `klai-mailer` is het enige notificatiepad voor reguliere alerts.
- **Geen shadow-rollout.** De 7-daagse `#alerts-dev` fase is geschrapt. Alerts gaan direct live. Als het volume te hoog wordt, tunen we per regel.
- **Alert-catalogus bijgewerkt op basis van industrie-standaard (SRE golden signals + RED method):**
  - Toegevoegd: latency-alert op portal-api (p95 response-tijd).
  - Toegevoegd: traffic-drop alert op portal-api (plotselinge val in request-rate).
  - Verwijderd: `container_unexpected_restart` (was oorzaak-gericht, niet symptoom-gericht; `container_restart_loop` dekt de echte impact al).
- **Hartslag-pad via Uptime Kuma's eigen SMTP.** Uptime Kuma draait op public-01 en gebruikt een eigen SMTP-configuratie voor de dead-man's-switch mail. Dit blijft werken als core-01 (en dus klai-mailer) uitvalt.
- **SOPS-gebruik volgt standaardpatroon** (`klai-infra/config.sops.env`), geen nieuw mechanisme nodig.

### v0.1.0 (2026-04-19)
- Initiële SPEC, getriggerd door de FLUSHALL-observabilitykloof die aan het licht kwam tijdens SPEC-SEC-021 (de herschrijving van drie portal-api provisioning-paden van `docker exec` naar native protocollen in commits `a3920a75` en `c5653159`).
- Directe aanleiding: een tenant rapporteerde dat een recente config-wijziging niet actief werd. Root cause was `redis_flushall_failed` tijdens de LibreChat config-regenerate: het event stond netjes in VictoriaLogs, maar niemand bevroeg die logs totdat de gebruiker klaagde.
- Commit `aab3848c` voegde een handmatige runbook toe (`docs/runbooks/platform-recovery.md#librechat-stale-config-recovery`) als tussentijdse oplossing. Deze SPEC institutionaliseert dat patroon: elke alert heeft voortaan een runbook, en elk runbook hoort bij een alert — niet andersom.
- Scope bewust beperkt tot MVP-alerting: geen SLO/SLI-framework, geen APM-tracing, geen PagerDuty, geen public status page. Die zijn adjacent en verdienen eigen SPECs zodra deze basis staat en drie maanden stabiel draait.

---

## Goal

Maak operationele infra-fouten zichtbaar binnen minuten, niet "wanneer een gebruiker klaagt". Concreet:

1. **Een alert-engine die LogsQL én PromQL kan evalueren**, geprovisioneerd via git (geen klik-config in Grafana UI).
2. **Een notificatiekanaal via e-mail** (klai-mailer) dat alerts aflevert bij een centrale ontvangerslijst.
3. **Een eerste catalogus van regels** die de concrete gaten uit SEC-021 dicht (FLUSHALL failures, chat health failures, portal-api 5xx, container down) én de industrie-standaard golden signals (latency, traffic, errors, saturation) dekt, uitbreidbaar per PR zonder re-architectuur.
4. **Alert-on-alert hartslag** zodat een stilvallende alerter zelf ook detecteerbaar is — via een volledig onafhankelijk pad (Uptime Kuma op public-01 + eigen SMTP).

Succes = de volgende keer dat `redis_flushall_failed` verschijnt, zit het binnen 60 seconden in de alerts-mailbox met een runbook-link. Niet via een ticket van een gebruiker.

---

## Why now

- Directe observabilitykloof: `redis_flushall_failed` (portal-api, SEC-021) en `chat.health_failed` (LibreChat health probes) verschijnen nu alleen in VictoriaLogs. Niemand bevraagt VictoriaLogs proactief.
- Bestaande logpijplijn is compleet: alle services → stdout (JSON via structlog) → Alloy → VictoriaLogs, 30 dagen retentie. `request_id` propagatie werkt end-to-end (Caddy → portal-api → downstream). Zie `.claude/rules/klai/infra/observability.md`.
- Bestaande metrics-pijplijn is compleet: cAdvisor + node-exporter + portal-api `/metrics` + retrieval-api `/metrics` → VictoriaMetrics, 30d retentie. Web-vitals histograms (`webvitals_lcp_seconds` et al.) worden al gescraped.
- Grafana draait, is OIDC-secured (`grafana_admin` Zitadel rol), en heeft datasources geprovisioneerd (`deploy/grafana/provisioning/datasources/datasources.yaml`). Er is géén `alerting/` subdirectory — dat is precies de gap die deze SPEC dicht.
- `klai-mailer` draait al op core-01 als Zitadel HTTP notification provider. Een extra endpoint voor alert-mails is een kleine uitbreiding, geen nieuwe service.
- Het bouwmateriaal ligt al op de plank. Wat ontbreekt is de instrumentatielaag erbovenop.

---

## Scope

### In scope

- Keuze van alert-engine (vmalert vs. Grafana managed alerts), inclusief trade-off rationale en default-aanbeveling.
- Notificatiepad via `klai-mailer`: nieuwe endpoint `POST /api/alerts/email` die de Grafana webhook payload accepteert en SMTP-mail verstuurt.
- Initiële regelcatalogus (CRIT / HIGH / MED), elke regel met runbook-URL, severity-label, en optionele `for:` (debounce).
- Silencing + acknowledgement workflow via Grafana's eigen UI.
- Alert-on-alert (hartslag) via Uptime Kuma met **eigen SMTP-configuratie** (niet via klai-mailer) — echt onafhankelijk pad.
- Update van `docs/runbooks/platform-recovery.md`: elke nieuwe alert krijgt een bijbehorende runbook-sectie conform het `librechat-stale-config-recovery` prototype.

### Out of scope — expliciet uitgesloten

- **Slack / Teams / ander chat-platform.** Klai gebruikt geen chat-platform voor ops. Als dat in de toekomst verandert, komt er een separate SPEC.
- **Shadow-rollout / gefaseerde productie-promotie.** Alerts gaan direct live. Tuning gebeurt reactief per regel (threshold, `for:`, filters) wanneer een regel te vaak of te weinig firet.
- **SLO/SLI tracking.** Adjacent onderwerp; verdient een eigen SPEC met error-budget policies, burn-rate alerts, dashboard-design. Deze SPEC gaat over "fire when broken", niet over "measure service health".
- **APM / distributed tracing.** We hebben `request_id`-correlatie via Caddy → middleware → VictoriaLogs. Dat is voor nu genoeg om een request end-to-end te tracen. OpenTelemetry-integratie is later-werk.
- **PagerDuty / Opsgenie / SMS.** Pas als mail onvoldoende blijkt voor urgente zaken buiten kantooruren, heroverwegen we escalatie naar een pager-platform.
- **Public status page** (`status.getklai.com`). Uptime Kuma op public-01 dekt de externe status-page behoefte al. Intern-facing alerting en extern-facing status zijn verschillende producten; mengen = ruis.
- **Performance alerting op LLM-latencies** (LiteLLM p99 response time, Ollama queue depth). Metrics zijn beschikbaar maar tuning van thresholds vereist een baseline-periode. Volgende iteratie.
- **Alert-routing per tenant / org.** Alle alerts in MVP zijn infra-breed; tenant-scoped alerting (bijv. "org X's LibreChat container restart loopt") is mogelijk maar nog niet nodig.
- **Volledige migratie van Uptime Kuma alerting.** Uptime Kuma blijft bestaan voor externe black-box probes (public-01, DNS, TLS). Deze SPEC voegt grey-box alerting toe, niet instead-of.

---

## Success Criteria

1. **Alert-engine keuze vastgelegd en gedeployed** — compose-change landt op core-01, container runt gezond, regels worden herkend en geëvalueerd. Documented beslissing inclusief rationale in deze SPEC.
2. **Notificatie werkt end-to-end** — een handmatig getriggerde test-alert arriveert binnen 60 seconden in de `ALERTS_EMAIL_RECIPIENTS` mailbox met payload die severity, service, summary en runbook-URL bevat.
3. **Geen secrets in git** — audit van het provisioning-pad toont dat alle credentials via env vars worden geïnjecteerd uit SOPS (`klai-infra/config.sops.env`). Grep over `deploy/grafana/provisioning/alerting/` levert geen SMTP-credentials of tokens op.
4. **Initiële regelcatalogus actief** — alle regels uit §EARS Requirements zijn geprovisioneerd, evaluaten correct tegen echte productie-data.
5. **Alert-on-alert fires when alerter stops** — simulatie: stop de Grafana container, verifieer dat binnen 15 minuten een "alerter-down" mail arriveert via het Uptime Kuma pad (niet via klai-mailer).
6. **Runbook-koppeling verplicht** — elke alert-regel heeft een `runbook_url` annotation; CI faalt als een regel die mist.
7. **FLUSHALL-gat gedicht** — bij een gesimuleerde `redis_flushall_failed` event in VictoriaLogs arriveert binnen 60 seconden een mail met runbook-URL naar `librechat-stale-config-recovery` en de betrokken `slug`.

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

### Primair pad: e-mail via klai-mailer

- `klai-mailer` is de bestaande Zitadel HTTP notification provider (`ghcr.io/getklai/klai-mailer:latest`) op core-01.
- Nieuw endpoint: `POST /api/alerts/email` accepteert de Grafana webhook payload en stuurt een mail via de bestaande SMTP-config.
- Ontvanger-lijst in env var `ALERTS_EMAIL_RECIPIENTS` (SOPS) — start met één adres, uitbreidbaar zonder provisioning-change.
- Subject-format: `[KLAI-ALERT-{severity}] {alertname}`. Body bevat severity, service, summary, runbook-URL, en indien aanwezig `request_id` / `slug` / `org_id`.

### Alert-on-alert pad: Uptime Kuma met eigen SMTP

- Uptime Kuma draait op public-01 — een andere host dan core-01.
- De heartbeat-monitor in Uptime Kuma verwacht elke 5 min een push vanuit Grafana (`status=up`). Stopt die push gedurende 15 min → Uptime Kuma stuurt een mail via zijn **eigen** SMTP-configuratie naar een aparte ontvangerslijst.
- **Belangrijk:** dit pad gebruikt **niet** klai-mailer. Als core-01 volledig uitvalt (OS-crash, netwerkprobleem), kan klai-mailer ook niet mailen — maar Uptime Kuma op public-01 wel. Die onafhankelijkheid is de hele reden van de hartslag.
- SMTP-credentials voor Uptime Kuma in public-01 SOPS-env (`klai-infra/public-01/.env.sops` of equivalent), volledig los van core-01 SOPS.

### [HARD] Geen inline secrets in alert-config

- **Regel:** SMTP-credentials, tokens en andere geheimen verschijnen **nooit** in `deploy/grafana/provisioning/alerting/*.yaml` als literal. Altijd `${VAR_NAME}` met env-substitution in de provisioning-loader.
- **Verificatie:** `scripts/audit-alert-secrets.sh` grept `deploy/grafana/provisioning/alerting/` voor bekende secret-patronen (`xoxb-`, `smtp://[^$]`, `-----BEGIN`, literal e-mailadres+wachtwoord-paren); CI faalt op hit.
- **Rationale:** git staat open in CI-logs, alert-configs worden breed gereviewed, en het kost nul moeite om het goed te doen vanaf dag één.

---

## EARS Requirements

### Alert-engine deployment

**SPEC-OBS-001-R1** — WHEN een PR `deploy/grafana/provisioning/alerting/*.yaml` toevoegt of wijzigt en naar `main` merged, SHALL de deploy-compose workflow de nieuwe provisioning-bestanden op core-01 synchroniseren en Grafana herstarten zodat de rules actief worden.

**SPEC-OBS-001-R2** — WHILE Grafana draait, SHALL het Unified Alerting-mechanisme enabled zijn en SHALL de provisioning directory structuur minimaal bevatten: `alerting/rules/`, `alerting/contact-points/`, `alerting/notification-policies/`, `alerting/mute-timings/`.

**SPEC-OBS-001-R3** — IF Grafana opstart met een syntactisch ongeldig alert-rule bestand, THEN SHALL Grafana de specifieke rule negeren, een structured log op `level:error` emitteren naar VictoriaLogs, en wél opstarten (geen crash-loop op één slechte rule).

### Notification routing

**SPEC-OBS-001-R4** — The alerting-systeem SHALL één contact point `email-primary` definiëren dat via een webhook naar `klai-mailer`'s `POST /api/alerts/email` routeert; het mailer-endpoint gebruikt `${ALERTS_EMAIL_RECIPIENTS}` als ontvanger-lijst en `[KLAI-ALERT-{severity}] {alertname}` als subject-format.

**SPEC-OBS-001-R5** — The default notification policy SHALL alle alerts routeren naar `email-primary`, ongeacht severity (severity wordt wel zichtbaar in subject en body, maar bepaalt niet het kanaal — er is er maar één).

**SPEC-OBS-001-R6** — IF een alert-payload inline een bekend secret-patroon bevat (e.g. `xoxb-`, `-----BEGIN`, of een `Authorization:`-header-waarde), THEN SHALL de template-rendering deze vervangen door `[REDACTED]` voordat verzending plaatsvindt.

### Secret hygiene

**SPEC-OBS-001-R7** — The alerting-configuratie SHALL geen literal secrets bevatten; alle webhook-URLs, tokens en credentials SHALL via env-var substitutie geïnjecteerd worden uit `klai-infra/config.sops.env`.

**SPEC-OBS-001-R8** — WHEN een PR `deploy/grafana/provisioning/alerting/` wijzigt, SHALL `scripts/audit-alert-secrets.sh` in CI draaien en de PR blokkeren als een literal secret-patroon gedetecteerd wordt.

### Initiële alert-regels (seed catalogue)

De catalogus volgt de SRE golden signals (latency, traffic, errors, saturation) en RED-method (rate, errors, duration), aangevuld met enkele Klai-specifieke log-gebaseerde regels voor concrete gaten uit SEC-021.

**SPEC-OBS-001-R9** (CRIT, errors) — WHILE `sum(rate(caddy_http_requests_total{service="portal-api",status=~"5.."}[5m])) / sum(rate(caddy_http_requests_total{service="portal-api"}[5m]))` boven 0.01 blijft gedurende 5 minuten, SHALL een alert `portal_api_5xx_rate_high` firen op CRIT-severity met annotation `runbook_url: docs/runbooks/platform-recovery.md#portal-api-5xx-surge`.

**SPEC-OBS-001-R10** (CRIT, latency) — WHILE de portal-api p95 request-latency (`histogram_quantile(0.95, sum(rate(caddy_http_request_duration_seconds_bucket{service="portal-api"}[5m])) by (le))`) boven 2.0 seconden blijft gedurende 5 minuten, SHALL een alert `portal_api_latency_high` firen op CRIT-severity met annotation `runbook_url: docs/runbooks/platform-recovery.md#portal-api-latency-surge`.

**SPEC-OBS-001-R11** (CRIT, traffic) — WHILE de portal-api request-rate (`sum(rate(caddy_http_requests_total{service="portal-api"}[5m]))`) meer dan 80% onder zijn eigen uursgemiddelde (`sum(avg_over_time(rate(caddy_http_requests_total{service="portal-api"}[5m])[1h:1m]))`) ligt gedurende 10 minuten tijdens kantooruren (Europe/Amsterdam 08:00–20:00), SHALL een alert `portal_api_traffic_drop` firen op CRIT-severity met annotation `runbook_url: docs/runbooks/platform-recovery.md#portal-api-traffic-drop`.

**SPEC-OBS-001-R12** (CRIT) — WHEN een container met labels `container=~"klai-core-.*"` gedurende 2 minuten geen cAdvisor `container_last_seen` metric update heeft, SHALL een alert `container_down` firen op CRIT-severity met annotation `runbook_url: docs/runbooks/platform-recovery.md#container-down`.

**SPEC-OBS-001-R13** (CRIT) — WHILE een container restart-rate `rate(container_restarts_total[15m]) > 0` gedurende 15 minuten aanhoudt (sustained restart loop), SHALL een alert `container_restart_loop` firen op CRIT-severity met label `container` gekopieerd naar annotations en `runbook_url: docs/runbooks/platform-recovery.md#container-restart-loop`.

**SPEC-OBS-001-R14** (HIGH) — WHEN een logline matchend `service:portal-api AND event:redis_flushall_failed` in VictoriaLogs verschijnt binnen het laatste evaluatie-interval (30 sec), SHALL een alert `portal_redis_flushall_failed` firen op HIGH-severity met annotation `runbook_url: docs/runbooks/platform-recovery.md#librechat-stale-config-recovery` en `slug` + `request_id` geëxtraheerd uit de log-payload naar annotations.

**SPEC-OBS-001-R15** (HIGH) — WHILE het aantal loglines matchend `service:librechat-* AND event:chat.health_failed` in de laatste 10 minuten boven 5 blijft, SHALL een alert `librechat_health_failed_elevated` firen op HIGH-severity met annotation `runbook_url: docs/runbooks/platform-recovery.md#librechat-chat-health-failed`.

**SPEC-OBS-001-R16** (HIGH) — WHILE het aantal loglines matchend `service:knowledge-ingest AND level:error` in de laatste 10 minuten boven 10 blijft, SHALL een alert `ingest_error_rate_elevated` firen op HIGH-severity met annotation `runbook_url: docs/runbooks/platform-recovery.md#knowledge-ingest-error-surge`.

**SPEC-OBS-001-R17** (MED, saturation) — WHILE de disk-usage metric `node_filesystem_avail_bytes / node_filesystem_size_bytes` op core-01 voor `mountpoint="/"` onder 0.15 blijft gedurende 30 minuten, SHALL een alert `core01_disk_usage_high` firen op MED-severity met annotation `runbook_url: docs/runbooks/platform-recovery.md#core01-disk-usage-high`.

### Runbook-koppeling

**SPEC-OBS-001-R18** — WHILE een alert-rule geen `runbook_url` annotation heeft of WHERE de annotation verwijst naar een pad dat niet bestaat in de repo, SHALL de CI-check `scripts/verify-alert-runbooks.sh` de PR blokkeren.

**SPEC-OBS-001-R19** — WHEN een nieuwe alert-rule toegevoegd wordt, SHALL `docs/runbooks/platform-recovery.md` een bijbehorende sectie bevatten met dezelfde structuur als `librechat-stale-config-recovery` (Situation → Signal → Step 1..N → Verify → Follow-up), en de `runbook_url` annotation in de rule SHALL daarheen linken.

### Silencing en acknowledgement

**SPEC-OBS-001-R20** — The alerting-systeem SHALL silencing ondersteunen via Grafana's ingebouwde UI (toegankelijk voor `grafana_admin` Zitadel-rol via OIDC), waarbij elke silence verplicht een `comment` veld heeft en een automatische expiry van maximaal 7 dagen.

**SPEC-OBS-001-R21** — The alerting-systeem SHALL mute-timings ondersteunen via provisioned `alerting/mute-timings/*.yaml` bestanden voor voorspelbare onderhoudsvensters (e.g. geplande deploys die korte restart-loops produceren).

### Alert-on-alert (hartslag)

**SPEC-OBS-001-R22** — The alerting-systeem SHALL elke 5 minuten een hartslag-push sturen naar Uptime Kuma op public-01 via een `webhook` contact point met `${KUMA_HEARTBEAT_URL}` (bevat push-token).

**SPEC-OBS-001-R23** — IF Uptime Kuma gedurende 15 minuten geen hartslag ontvangt, THEN SHALL Uptime Kuma een mail sturen via zijn **eigen SMTP-configuratie** (niet via klai-mailer) naar `${ALERTS_EMERGENCY_EMAIL_RECIPIENTS}` met subject `[KLAI-ALERTER-DOWN] Grafana alerter heartbeat missing`.

**SPEC-OBS-001-R24** — The hartslag-pad SHALL bewust **geen gebruik maken van klai-mailer, core-01 infrastructuur, of dezelfde SMTP-credentials** als reguliere alerts, zodat een core-01 uitval of een klai-mailer configuratiefout de hartslag-notificatie niet uitschakelt.

---

## Risks and mitigations

| Risico | Mitigatie |
|---|---|
| **Noisy alerts eroderen vertrouwen** — mensen leren alerts negeren. | Bewuste keuze: geen shadow-fase, maar wél reactief tunen. Bij eerste false-positive in productie: kleine PR die threshold, `for:` duration of label-filters aanpast. Thresholds in §EARS zijn expliciet startwaarden. Quarterly review kijkt naar top-5 noise-makers. |
| **Alerts komen niet aan bij juiste persoon** — mail belandt in spam, één persoon mist het. | `ALERTS_EMAIL_RECIPIENTS` is een lijst, niet een individu. Begin met minimaal twee ontvangers. SPF/DKIM via bestaande mailer-config. Quarterly check: worden mails gelezen? Desnoods forward-rules / filters in de inbox. |
| **Secret leak via alert payloads** — een structured log met `Authorization: Bearer …` wordt gemaild. | SPEC-OBS-001-R6 (template-redaction op bekende patterns), plus structlog-side rule: services mogen geen Authorization-headers in logs emitteren. Reuse van bestaande Semgrep-regel `python-logger-credential-disclosure` op de portal-api repo om injection te voorkomen. |
| **Alerter zelf gaat stil** — container crash, netwerkissue, config-fout maakt alle alerts onzichtbaar. | Alert-on-alert hartslag via onafhankelijk pad: Uptime Kuma op public-01 + Uptime Kuma's eigen SMTP. SPEC-OBS-001-R22..R24 beschrijven de scheiding. Dead-man's switch werkt zonder Grafana én zonder klai-mailer. |
| **klai-mailer uitval** — als klai-mailer zelf crasht of SMTP niet bereikbaar is, komen ook alert-mails niet aan. | De hartslag detecteert dit indirect: als Grafana niet kan mailen, blijft de hartslag naar Uptime Kuma wél werken (want dat is een aparte HTTP-push, geen SMTP). Maar als Grafana zelf fires én ze komen niet aan, zal de hartslag dat niet detecteren. Mitigatie: klai-mailer health in de alert-catalogus opnemen zodra we een geschikte metric hebben (nu: container_down dekt het al als klai-mailer crasht). |
| **Grafana-plugin latency voor VictoriaLogs-queries maakt alert-evaluation onbetrouwbaar.** | Start met 30s evaluatie-interval (comfortabel ruim voor de plugin). Monitor query-latency via Grafana's built-in `grafana_alerting_rule_evaluation_duration_seconds`. Als p95 > 10s: specifieke LogsQL-regel verplaatsen naar vmalert (geen re-architectuur nodig — beide engines kunnen coëxisteren). |
| **Alert fatigue door te agressieve thresholds** in initiële catalogus. | Alle thresholds in §EARS zijn startwaarden, expliciet tune-baar. Per false-positive een kleine PR. Quarterly review-template in `docs/runbooks/alerting-rollout.md` captures threshold-tuning per kwartaal. |
| **Grafana config-reload bij provisioning-change veroorzaakt brief downtime.** | Provisioning reload is live-reloadable in recente Grafana-versies; getest voordat `deploy-compose.yml` op `main` landt. Worst case: 30s Grafana downtime acceptabel (geen data-impact, alleen UI). |
| **Runbook verwijst naar niet-bestaande procedure.** | SPEC-OBS-001-R18: CI-check `scripts/verify-alert-runbooks.sh` resolved elke `runbook_url` annotation tegen het repo-bestand + anchor en faalt bij 404. |
| **Traffic-drop alert (R11) firet tijdens rustige perioden (nacht, weekend).** | Regel is begrensd tot kantooruren (Europe/Amsterdam 08:00–20:00) via `for:` conditie + time-window predicaat. Bij false-positive tijdens werkdagen: drempel (nu 80%) omhoog, of rolling-baseline-window vergroten van 1u naar 4u. |
| **Latency-alert (R10) firet bij legitieme spikes (backup, grote import).** | Drempel 2.0s p95 is startpunt. Bij false-positives: verhoog drempel of wijzig naar p99. Als specifieke endpoints (bijv. knowledge-upload) structureel traag zijn: exclude via label-filter. |

---

## Migration plan

Het bestaande runbook `docs/runbooks/platform-recovery.md#librechat-stale-config-recovery` is het **prototype** voor hoe deze SPEC alerts aan runbooks koppelt:

1. **Situation** — wat is er kapot (in plaintext, niet "error code 4271").
2. **Signal to look for** — exacte LogsQL of PromQL query; matches wat de alert ook evalueert.
3. **Step 1..N** — deterministische recovery-stappen, kopieer-plakbaar.
4. **Verify** — hoe bevestig je dat het opgelost is.
5. **Follow-up** — dependency op deze SPEC (expliciet genoemd in de huidige runbook).

Bij deze SPEC wordt dat patroon **verplicht**: elke alert-rule refereert met `runbook_url` naar een runbook-sectie die deze structuur volgt, en CI dwingt beide kanten af (SPEC-OBS-001-R18, R19).

De reeds aanwezige `librechat-stale-config-recovery` sectie blijft onveranderd — de nieuwe alert `portal_redis_flushall_failed` (SPEC-OBS-001-R14) linkt rechtstreeks daarheen. Wat voorheen een handmatig periodiek VictoriaLogs-querijtje was (week-retro stijl) wordt nu een 60-seconden notification.

---

## Definition of Done

- Grafana Unified Alerting enabled, provisioning directory-structuur aanwezig, alle regels uit SPEC-OBS-001-R9..R17 geprovisioneerd en evaluating.
- Contact point `email-primary` geprovisioneerd; klai-mailer endpoint `POST /api/alerts/email` live en verstuurt mails naar `ALERTS_EMAIL_RECIPIENTS`.
- Alle nieuwe alert-rules hebben een `runbook_url` annotation; `scripts/verify-alert-runbooks.sh` passes in CI; `scripts/audit-alert-secrets.sh` passes in CI.
- Alert-on-alert hartslag draait via Uptime Kuma (public-01) + Uptime Kuma's eigen SMTP, onafhankelijk pad geverifieerd door gesimuleerde alerter-down drill.
- `docs/runbooks/platform-recovery.md` bijgewerkt met een nieuwe sectie per alert uit §EARS Requirements (8 nieuwe secties).
- `docs/runbooks/alerting-rollout.md` aangemaakt met een log van initial-rollout-datum, reviewers, en daarna quarterly review-entries.
- Commit `aab3848c` en SPEC-SEC-021 gecross-referenced in de SPEC (§HISTORY).
- FLUSHALL-drill: een handmatig geïnjecteerde `event:redis_flushall_failed` logline produceert binnen 60 seconden een mail in `ALERTS_EMAIL_RECIPIENTS` met de juiste runbook-link. Screenshot of recording bijgesloten bij de sluit-PR.
- Alerter-down drill: `docker stop klai-core-grafana-1` produceert binnen 15 minuten een mail via het Uptime Kuma pad naar `ALERTS_EMERGENCY_EMAIL_RECIPIENTS`.

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
- `klai-infra/config.sops.env` — centrale SOPS-encrypted env file waarin `ALERTS_EMAIL_RECIPIENTS`, `KUMA_HEARTBEAT_URL` landen.
- `klai-infra/public-01/` — losse SOPS-scope voor Uptime Kuma SMTP-credentials (apart van core-01).

### Industrie-referenties (v0.2.0)

- Google SRE book — Monitoring Distributed Systems (https://sre.google/sre-book/monitoring-distributed-systems/): symptoom- vs oorzaak-gebaseerd alerten; de vier golden signals (latency, traffic, errors, saturation).
- Google Cloud Blog — Why Focus on Symptoms, Not Causes: rationale voor het schrappen van oorzaak-gebaseerde alerts als `container_unexpected_restart` in v0.2.0.
- Tom Wilkie — The RED method: rate/errors/duration als kern-trio voor request-gedreven services; aanleiding voor het toevoegen van latency (R10) en traffic-drop (R11) in v0.2.0.
