---
id: SPEC-OBS-001
version: 0.2.0
status: draft
created: 2026-04-19
updated: 2026-04-21
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

### AC-4: Test-alert komt als mail aan
**Relates to:** SPEC-OBS-001-R4, R5

- **Given** contact point `email-primary` is geconfigureerd met `url: ${KLAI_MAILER_ALERT_WEBHOOK_URL}`, klai-mailer's `POST /api/alerts/email` endpoint is live, `ALERTS_EMAIL_RECIPIENTS` bevat minimaal één geldig adres.
- **When** een operator via Grafana UI → Contact points → email-primary → "Test" een test-bericht triggert.
- **Then** binnen 60 seconden arriveert een mail in `ALERTS_EMAIL_RECIPIENTS` met subject `[KLAI-ALERT-test] TestAlert` (of Grafana's equivalent), body bevat severity, alert-name, en een testbericht.

### AC-5: Alle severity-levels routeren naar email-primary
**Relates to:** SPEC-OBS-001-R5

- **Given** meerdere rules actief met verschillende severities (CRIT, HIGH, MED).
- **When** een CRIT-rule fires (bijv. handmatig een 5xx-spike veroorzaakt) en separately een MED-rule fires (bijv. disk vol-simulatie).
- **Then** beide berichten arriveren bij `ALERTS_EMAIL_RECIPIENTS`; beide subjecten bevatten hun juiste severity-waarde (`[KLAI-ALERT-critical]` vs `[KLAI-ALERT-medium]`); geen bericht gaat naar een ander kanaal.

### AC-6: Alert-payload met secret wordt geredacteerd
**Relates to:** SPEC-OBS-001-R6

- **Given** een alert-rule met template die een label-waarde `{{ $labels.auth_header }}` gebruikt.
- **When** een rule evalueert waarbij het label de literal waarde `Bearer xoxb-1234567890-abcdef` bevat (gesimuleerd via test-labels).
- **Then** de verstuurde mail bevat `[REDACTED]` in plaats van de bearer-token; de originele label-waarde verschijnt niet in het bericht.

---

## Scenario's — Secret hygiene

### AC-7: Literal credential in config blokkeert PR
**Relates to:** SPEC-OBS-001-R7, R8

- **Given** een PR wijzigt `deploy/grafana/provisioning/alerting/contact-points/email.yaml` en bevat bijvoorbeeld een literal `password: s3cr3t!` of een SMTP-URL met wachtwoord.
- **When** de `alerting-check` GitHub Action draait.
- **Then** het `audit-alert-secrets.sh` script exiteert met non-zero, het CI-check-block rapporteert de vindplaats, en de PR kan niet merged worden totdat de waarde vervangen is door `${VAR_NAME}`.

### AC-8: Env-var substitution werkt in runtime
**Relates to:** SPEC-OBS-001-R7

- **Given** `email.yaml` bevat `url: ${KLAI_MAILER_ALERT_WEBHOOK_URL}` en de env-var is correct gezet via SOPS.
- **When** Grafana leest de provisioning-file.
- **Then** Grafana verstuurt bij een test-alert naar de daadwerkelijke mailer-URL; geen logging van die URL zelf in Grafana-logs; Grafana's `/api/v1/provisioning/contact-points` retourneert de URL-veld gemaskeerd of als lege string (standaard Grafana API-gedrag voor secure fields).

---

## Scenario's — Initiële regelcatalogus (seed rules)

### AC-9: portal-api 5xx surge fires
**Relates to:** SPEC-OBS-001-R9 (CRIT)

- **Given** `portal_api_5xx_rate_high` regel actief; baseline 5xx-rate is 0%.
- **When** een kunstmatige load van 20 requests/s naar `/api/health-simulate-500` gedurende 6 minuten een 5xx-rate van ~15% produceert.
- **Then** binnen 5 minuten en 30 seconden fires de alert; de mail bevat severity `critical`, summary met actual percentage, en runbook_url naar `#portal-api-5xx-surge`.

### AC-10: portal-api latency high fires
**Relates to:** SPEC-OBS-001-R10 (CRIT)

- **Given** `portal_api_latency_high` regel actief; baseline p95 onder 500ms.
- **When** een kunstmatige load met artificial delay (bijv. via een test-endpoint dat `time.sleep(3)` doet) p95 naar 3.0s duwt gedurende 6 minuten.
- **Then** binnen 5 minuten en 30 seconden fires de alert; de mail bevat summary met actual p95-duration, runbook_url naar `#portal-api-latency-surge`.

### AC-11: portal-api traffic drop fires tijdens kantooruren
**Relates to:** SPEC-OBS-001-R11 (CRIT)

- **Given** `portal_api_traffic_drop` regel actief, moment van test is werkdag tussen 08:00 en 20:00 Europe/Amsterdam, normale traffic-rate is >10 req/s.
- **When** Caddy wordt gestopt (of alle traffic geblokkeerd) gedurende 11 minuten.
- **Then** binnen 10 minuten en 30 seconden fires de alert; de mail bevat summary "traffic dropped to Nx of hourly baseline"; runbook_url naar `#portal-api-traffic-drop`.

### AC-12: portal-api traffic drop fires NIET buiten kantooruren
**Relates to:** SPEC-OBS-001-R11 (CRIT) — false-positive preventie

- **Given** dezelfde regel actief, moment van test is weekend of 22:00 's avonds.
- **When** de traffic-rate natuurlijk laag is (< 1 req/s).
- **Then** de alert fires **niet**; Grafana UI toont rule-state "Inactive" door time-window predicate.

### AC-13: Container down fires binnen 2 minuten
**Relates to:** SPEC-OBS-001-R12 (CRIT)

- **Given** `container_down` regel actief; alle klai-core containers draaien.
- **When** een operator `docker stop klai-core-litellm-1` uitvoert.
- **Then** binnen 2 minuten fires de alert `container_down{container="klai-core-litellm-1"}`; na `docker start` lost de alert binnen 1 evaluation-cycle weer op.

### AC-14: FLUSHALL failure fires binnen 60 seconden
**Relates to:** SPEC-OBS-001-R14 (HIGH) — **primaire gap-closing scenario**

- **Given** `portal_redis_flushall_failed` regel actief met 30s evaluation-interval; baseline toont geen matches in de laatste 24 uur.
- **When** een operator via `docker exec klai-core-portal-api-1 python -c "import structlog; log=structlog.get_logger().bind(event='redis_flushall_failed', slug='test-tenant', error='simulated', request_id='test-req-abc'); log.warning('redis flushall failed')"` een test-event injecteert in VictoriaLogs.
- **Then** binnen 60 seconden arriveert een mail in `ALERTS_EMAIL_RECIPIENTS` met:
  - severity: `high`
  - summary bevat `slug=test-tenant` en `request_id=test-req-abc`
  - `runbook_url` die linkt naar `docs/runbooks/platform-recovery.md#librechat-stale-config-recovery`

### AC-15: LibreChat health-failed spike fires
**Relates to:** SPEC-OBS-001-R15 (HIGH)

- **Given** `librechat_health_failed_elevated` regel actief.
- **When** een baseline van minder dan 1 `chat.health_failed` event per uur; gedurende 10 minuten 7 events verschijnen in VictoriaLogs.
- **Then** de alert fires op HIGH-severity binnen 10 minuten van de zevende event.

### AC-16: Knowledge-ingest error surge fires
**Relates to:** SPEC-OBS-001-R16 (HIGH)

- **Given** `ingest_error_rate_elevated` regel actief; baseline < 1 error/uur.
- **When** `knowledge-ingest` logt 12 errors in 10 minuten.
- **Then** de alert fires op HIGH-severity; annotations bevatten een LogsQL-link om de fouten zelf op te zoeken.

### AC-17: Container restart loop fires bij sustained restarts
**Relates to:** SPEC-OBS-001-R13 (CRIT)

- **Given** `container_restart_loop` regel actief.
- **When** een container gedurende 15 minuten herhaaldelijk crasht en restart (simuleerbaar met een misconfig die een crash-loop triggert).
- **Then** de alert fires op CRIT-severity met label `container` in annotations; runbook_url naar `#container-restart-loop`.

### AC-18: Enkele geplande restart fires GEEN restart-loop
**Relates to:** SPEC-OBS-001-R13 (CRIT) — false-positive preventie

- **Given** dezelfde regel actief, CI heeft zojuist een deploy van LiteLLM gedaan (1 legitieme restart).
- **When** die ene restart plaatsvindt.
- **Then** `container_restart_loop` fires **niet** — `for: 15m` + `rate([15m]) > 0` eist sustained restarts, niet een single event.

### AC-19: Disk-usage high fires bij sustained low disk
**Relates to:** SPEC-OBS-001-R17 (MED)

- **Given** `core01_disk_usage_high` regel actief, drempel 0.15 beschikbaar (= 85% vol).
- **When** de `/`-filesystem op core-01 zakt tot 12% beschikbaar (gesimuleerd via temporary large file).
- **Then** na 30 minuten aanhoudende conditie fires de alert op MED-severity.

---

## Scenario's — Runbook-koppeling

### AC-20: Ontbrekende runbook_url blokkeert PR
**Relates to:** SPEC-OBS-001-R18

- **Given** een PR voegt een nieuwe alert-rule toe zonder `runbook_url` annotation.
- **When** `scripts/verify-alert-runbooks.sh` draait.
- **Then** het script exiteert non-zero met bericht "Rule <alertname> has no runbook_url annotation (file: rules/xxx.yaml)"; PR-check faalt.

### AC-21: runbook_url met dode anchor blokkeert PR
**Relates to:** SPEC-OBS-001-R18

- **Given** een alert-rule met `runbook_url: docs/runbooks/platform-recovery.md#nonexistent-section`.
- **When** `scripts/verify-alert-runbooks.sh` draait.
- **Then** het script exiteert non-zero met "Anchor '#nonexistent-section' not found in docs/runbooks/platform-recovery.md"; PR-check faalt.

### AC-22: Nieuwe runbook-sectie volgt prototype-structuur
**Relates to:** SPEC-OBS-001-R19

- **Given** een nieuwe alert-rule landt in een PR.
- **When** de PR-reviewer leest `docs/runbooks/platform-recovery.md`.
- **Then** de nieuwe sectie heeft alle kopjes: `**Situation:**`, `**Signal to look for:**`, ≥ 1 `### Step N` header, `### Verify`, `### Follow-up` of `### Related alert:`.

---

## Scenario's — Silencing en mute-timings

### AC-23: Silence maskt alert tijdelijk
**Relates to:** SPEC-OBS-001-R20

- **Given** een firing alert, operator heeft `grafana_admin` Zitadel-rol.
- **When** operator via Grafana UI → Alerting → Silences een silence toevoegt met matcher `alertname=container_down`, comment "Known issue, deploy in progress", expiry 1 uur.
- **Then** de alert verdwijnt uit de notification-routing zolang silence actief is; bij expiry (na 1 uur) hervat notification als alert nog fires.

### AC-24: Silence zonder comment wordt geblokkeerd
**Relates to:** SPEC-OBS-001-R20

- **Given** een operator probeert een silence aan te maken via de API zonder comment.
- **When** de POST naar `/api/alertmanager/grafana/api/v2/silences` plaatsvindt met een lege `comment`.
- **Then** de API retourneert 400 met bericht dat comment vereist is; silence wordt niet aangemaakt.

### AC-25: Mute-timing onderdrukt tijdens onderhoud
**Relates to:** SPEC-OBS-001-R21

- **Given** een mute-timing `deploy-window` geprovisioneerd met tijdsvenster elke vrijdag 14:00–15:00 Europe/Amsterdam, geassocieerd met een notification-policy child voor `alertname=~"container_.*"`.
- **When** op vrijdag 14:30 een `container_restart_loop` fires.
- **Then** geen mail-notificatie; de alert is wel zichtbaar als "firing" in Grafana UI met indicator "muted".

---

## Scenario's — Alert-on-alert hartslag

### AC-26: Hartslag push elke 5 minuten
**Relates to:** SPEC-OBS-001-R22

- **Given** alle alerting-infrastructuur draait, hartslag-rule actief.
- **When** operator monitort Uptime Kuma push-monitor `alerter-heartbeat` gedurende 1 uur.
- **Then** exact 12 pushes arriveerden (± 1 voor timing-drift); status is "UP".

### AC-27: Stille alerter detecteert zichzelf via onafhankelijk SMTP-pad
**Relates to:** SPEC-OBS-001-R23, R24

- **Given** alerting draait, hartslag werkt.
- **When** operator voert `docker stop klai-core-grafana-1` uit.
- **Then** binnen 15 minuten na de stop arriveert een mail in `ALERTS_EMERGENCY_EMAIL_RECIPIENTS`; mail-headers tonen dat het via Uptime Kuma's eigen SMTP is verstuurd (niet via klai-mailer); de mail-body bevat dat heartbeat `alerter-heartbeat` down is sinds <timestamp>.

### AC-28: Hartslag-pad werkt ook bij klai-mailer uitval
**Relates to:** SPEC-OBS-001-R24

- **Given** alerting draait, hartslag werkt.
- **When** operator voert `docker stop klai-core-klai-mailer-1` uit, wacht tot de `container_down` rule voor klai-mailer firet (maar kan niet gemaild worden).
- **Then** Grafana blijft zelf werken; hartslag-push naar Uptime Kuma blijft doorgaan; geen valse alerter-down mail (want hartslag werkt immers gewoon). De klai-mailer `container_down` alert is onderweg niet detecteerbaar via mail, maar wel zichtbaar in Grafana UI en VictoriaLogs.

### AC-29: Hartslag-pad is onafhankelijk van core-01
**Relates to:** SPEC-OBS-001-R24

- **Given** een verifiable separation: operator inspecteert `deploy/grafana/provisioning/alerting/contact-points/heartbeat.yaml`, Uptime Kuma config op public-01, en beide SOPS-scopes.
- **When** operator controleert:
  1. `heartbeat-kuma` contact point gebruikt een ander secret (`KUMA_HEARTBEAT_URL`, bevat token) dan reguliere alerts.
  2. Uptime Kuma's notification-channel gebruikt zijn **eigen SMTP-configuratie** (ingesteld in Uptime Kuma UI of public-01 SOPS), **niet** via klai-mailer.
  3. `ALERTS_EMAIL_RECIPIENTS` (core-01 SOPS) en `ALERTS_EMERGENCY_EMAIL_RECIPIENTS` (public-01 SOPS) zijn verschillende env-vars in verschillende scopes.
- **Then** alle waarden zijn distinct; er is geen gedeelde component (geen klai-mailer, geen gedeelde SMTP-credential) tussen regulier en hartslag pad.

---

## Edge cases

### EC-1: Grafana kan VictoriaLogs niet bereiken
- **Given** VictoriaLogs container crashed of network-misconfig.
- **When** Grafana probeert een LogsQL-alert te evalueren.
- **Then** de specifieke rule gaat naar state "Error" (niet "Firing"); `execErrState: Alerting` triggert een alert met name `rule_evaluation_failed` → zichtbaar als een distinct signaal.

### EC-2: klai-mailer is down tijdens alert fire
- **Given** klai-mailer container is gestopt.
- **When** Grafana probeert een alert-webhook te POST-en.
- **Then** Grafana logt `level=error msg="notification failed"`, retries een paar keer (Grafana-default), dan stopt; de alert blijft in state "Firing" in Grafana UI maar er komt geen mail aan. Detectie: de `container_down` rule voor klai-mailer zelf (maar die kan ook niet gemaild worden). Hartslag via Uptime Kuma detecteert dit niet (die blijft werken). **Mitigatie:** klai-mailer is zelf een deployment-kritische service; bestaande container health-checks + restart-policy houden hem up. Quarterly review checks of klai-mailer uptime acceptabel is.

### EC-3: Operator wijzigt rule tijdens actieve fire
- **Given** een alert fires.
- **When** een PR wijzigt de threshold en wordt gemerged terwijl alert actief is.
- **Then** Grafana herlaadt de rule; als de nieuwe threshold de conditie nog steeds satisfies, blijft alert in "Firing" zonder nieuwe notificatie (continuation); als nieuwe threshold conditie niet meer satisfies, lost alert op.

### EC-4: SOPS-decryption faalt voor `KLAI_MAILER_ALERT_WEBHOOK_URL`
- **Given** `.env` mist de webhook-var na een corrupte SOPS-sync.
- **When** Grafana opstart.
- **Then** de email-primary contact-point krijgt een lege URL; Grafana's interne test-send toont een error in de UI; operator moet handmatig detecteren. **Mitigatie:** een extra pre-flight check in de deploy-workflow die `printenv KLAI_MAILER_ALERT_WEBHOOK_URL | grep -q '^http'` vóór Grafana-recreate; faalt de deploy als env leeg is.

### EC-5: Rule met labels die niet matchen bij evaluatie
- **Given** een rule refereert `{{ $labels.slug }}` in de summary, maar een concrete match heeft geen `slug` label.
- **When** de rule fires.
- **Then** de summary toont `(no value)` of `<no value>` voor het label; alert fires nog steeds; de runbook kan alsnog uitgevoerd worden, alleen zonder tenant-specifieke info. Niet blocking, wel een tune-target.

### EC-6: Grafana upgrade breekt provisioning-schema
- **Given** een major Grafana-versie verandert het provisioning-schema.
- **When** `docker compose pull grafana` trekt een nieuwe major versie.
- **Then** opstart-logs tonen "deprecated schema" warnings of errors; testprotocol: nieuwe Grafana-major eerst in een staging-context (lokale docker-compose) testen voordat productie-pin geüpdatet wordt. Dit is bestaande `klai-infra` upgrade-policy.

### EC-7: Test-event voor FLUSHALL lekt naar productie VictoriaLogs
- **Given** operator voert AC-14 drill uit in productie (niet in staging).
- **When** het test-event met `slug=test-tenant` gepushed wordt.
- **Then** VictoriaLogs retentie houdt 30 dagen, event blijft doorzoekbaar; dit is acceptabel, maar operator moet het event taggen met `test: true` label om latere log-analyses niet te vervuilen. Runbook documenteert dit.

### EC-8: Grafana-restart tijdens actieve firing alert
- **Given** een alert fires, Grafana wordt gerestart (bijv. provisioning-update).
- **When** Grafana is down gedurende 30 seconden.
- **Then** de fires-state wordt uit Grafana's interne storage herladen bij opstart; het is mogelijk dat één evaluatie-cycle overgeslagen wordt, waardoor een kortstondig gecorrigeerd event gemist wordt. Aanvaardbare trade-off; bij aanhoudende conditie fires de rule opnieuw bij de volgende evaluatie.

### EC-9: Mail-spam-filter vangt alert-mails af
- **Given** operator's inbox filtert mails van `klai-mailer` naar spam.
- **When** een echte alert firet.
- **Then** mail arriveert in spam-map; operator ziet hem niet. **Mitigatie:** bij initiële rollout: verifieer dat minstens één test-alert in hoofdinbox aankomt. SPF/DKIM goed configureren. Quarterly review: check of recente alerts daadwerkelijk zijn gelezen.

### EC-10: PromQL `hour()`-functie gebruikt UTC, traffic-drop alert fires in Nederlands nacht
- **Given** R11 gebruikt `hour() >= 8 and hour() < 20` zonder timezone-offset.
- **When** Nederlandse tijd is 10:00 (UTC 08:00 in winter, 09:00 in zomer).
- **Then** in winter: PromQL `hour()` = 8, predicate true → regel kan fires; in zomer: PromQL `hour()` = 9, predicate true → ook fires. Maar rond middernacht: Nederlandse tijd 00:00 (UTC 23:00 in winter) → predicate false → regel kan niet fires. **Mitigatie:** M2 valideert empirisch het exacte gedrag; indien nodig offset toevoegen (`(hour() + 1) % 24` voor winter / `+2` voor zomer), of een recording rule met timezone-aware `time()`.

---

## Quality gate criteria

Alle punten hieronder moeten groen zijn voor merge-to-main van elke milestone-PR:

### Lint/config

- [ ] `scripts/audit-alert-secrets.sh` passes.
- [ ] `scripts/verify-alert-runbooks.sh` passes.
- [ ] YAML-lint passes voor alle bestanden in `deploy/grafana/provisioning/alerting/`.
- [ ] `docker compose config grafana | grep -A 20 environment` toont alle verwachte nieuwe env-vars correct geïnterpoleerd (geen onopgeloste `${…}`).

### Functional

- [ ] Handmatige test-alert naar `email-primary` komt aan binnen 60 seconden.
- [ ] Elke nieuwe rule heeft een `uid`, `title`, `runbook_url` annotation en een `severity` label.
- [ ] Elke rule's `datasourceUid` matcht een bestaande datasource (`victoriametrics` of `victorialogs`).
- [ ] Nieuwe rules evalueren zonder "Error"-status in Grafana UI na 2 evaluation-cycles.

### Security / secret hygiene

- [ ] Geen literal webhook-URLs, API-tokens, of SMTP-credentials in git.
- [ ] Nieuwe env-vars zijn gedocumenteerd in de SPEC en toegevoegd aan SOPS.
- [ ] Hartslag-pad gebruikt onafhankelijke SMTP (Uptime Kuma eigen config), niet klai-mailer.

### Documentation

- [ ] Elke alert heeft een matching runbook-sectie in `docs/runbooks/platform-recovery.md`.
- [ ] De runbook-sectie volgt het prototype-format (Situation / Signal / Step N / Verify / Follow-up).
- [ ] `docs/runbooks/alerting-rollout.md` bevat initial-rollout-entry + log-template voor tuning.

### Drills

- [ ] Drill AC-14 (FLUSHALL) is uitgevoerd en geverifieerd.
- [ ] Drill AC-27 (alerter-down via Uptime Kuma SMTP) is uitgevoerd en geverifieerd.
- [ ] Drill AC-11 + AC-12 (traffic-drop fires tijdens kantooruren, niet 's nachts) is empirisch gevalideerd.

---

## Definition of Done

De SPEC is DONE wanneer:

1. Alle milestones uit `plan.md` zijn gemerged op `main`.
2. Alle acceptance-scenario's AC-1 t/m AC-29 zijn geverifieerd, bewijs bijgevoegd in de afsluit-PR (screenshots, LogsQL-queries, mail-screenshots waar toepasbaar).
3. Minstens één echt productie-event heeft een bruikbaar alert-mail opgeleverd dat een operator heeft helpen diagnosen (of geverifieerd dat dit zou werken via een drill).
4. Geen enkele van de buiten-scope items is "stiekem" toegevoegd: geen SLO-dashboard, geen PagerDuty-integratie, geen chat-integratie, geen tenant-scoped routing. Als dat nodig blijkt, volgt een aparte SPEC.
5. `docs/runbooks/platform-recovery.md#librechat-stale-config-recovery` blijft bestaan (niet verwijderd) en de "Follow-up" sectie verwijst expliciet naar deze SPEC als "done, alerting now automated".
6. `docs/runbooks/alerting-rollout.md` bevat een finale entry "Initial rollout complete on YYYY-MM-DD, reviewers: X, Y" én een quarterly review-template.
7. Quarterly review geagendeerd (placeholder issue of calendar-invite).
8. Lessons learned uit de eerste weken (threshold-tunings, regel-iteraties) zijn gedocumenteerd in `docs/runbooks/alerting-rollout.md` — zodat de volgende persoon die alerts toevoegt weet wat werkte en wat niet.

---

## References

- `spec.md` — requirements en scope.
- `plan.md` — milestones en technische aanpak.
- `docs/runbooks/platform-recovery.md#librechat-stale-config-recovery` — prototype-runbook (commit `aab3848c`).
- Commits `a3920a75`, `c5653159` — SEC-021 provisioning rewrite (de bron van de FLUSHALL-observabilitykloof).
- `.claude/rules/klai/infra/observability.md` — bestaande logpijplijn en MCP-tooling.
- Google SRE — Monitoring Distributed Systems — golden signals (latency, traffic, errors, saturation) als structuur voor de catalogus.
