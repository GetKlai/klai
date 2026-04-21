---
id: SPEC-SEC-024
version: 0.2.0
status: draft
created: 2026-04-21
updated: 2026-04-21
author: Mark Vletter
priority: high
parent: SPEC-SEC-021
issue_number: null
---

# SPEC-SEC-024: docker-socket-proxy compliance-audit (portal-api + runtime-api)

## HISTORY

### v0.2.0 (2026-04-21)
- Sparring-sessie met product owner: project is pre-launch, geen betalende
  klanten — "stukmaken" is een acceptabel leersignaal, geen incident.
- Reductie-strategie aangepast: van "conservatief droppen na gesprek" naar
  **agressief droppen direct na M1-meting** (R5). Als runtime-api stilletjes
  iets nodig had, zien we dat via de alert en zetten we die verb terug.
- 24u-log-window (oude R12) vervangen door **permanente alert-rule + dashboard
  met deploy-annotaties** (nieuwe R12). Eenmalige windows passen niet bij
  meerdere deploys per dag. Industry standard voor dit event-type
  (proxy-denial op een endpoint dat nooit geraakt zou mogen worden) is
  zero-tolerance alerting — bron: Google SRE, Grafana docs, NIST SP 800-92.
- Rollback-ceremonie geschrapt: in pre-launch is fix-forward sneller en
  simpeler dan eerst reverten en dan alsnog een fix maken. Oude AC-10
  vervalt.
- Alert-kanaal: e-mail (geen Slack/PagerDuty).

### v0.1.0 (2026-04-21)
- Opgesteld als directe vervolg op SPEC-SEC-021.
- Aanleiding: incident 2026-04-21. SEC-021 zette portal-api en runtime-api achter
  `tecnativa/docker-socket-proxy:v0.4.2` (whitelist `CONTAINERS=1 NETWORKS=1 POST=1 DELETE=1`).
  Er lag echter `exec_run([...])` productie-code op vier plekken die pas faalde toen ze
  na SEC-021 voor het eerst werd aangeroepen — elke call loopt vast op `POST /exec/*/start`
  met HTTP 403 van de proxy.
- Fixes van vandaag (zie `git log` commits `c5653159`, `a3920a75`, `9dc03c67`):
  - `klai-portal/backend/app/api/internal.py` — `regenerate_librechat_configs` Redis FLUSHALL
  - `klai-portal/backend/app/services/provisioning/infrastructure.py` —
    `_flush_redis_and_restart_librechat`, `_create_mongodb_tenant_user`,
    `_sync_drop_mongodb_tenant_user`
  - `klai-portal/backend/app/services/provisioning/...` chat-health probe verschoof
    van `/api/endpoints` (vereist exec) naar `/api/config` (HTTP)
- Pitfall-regel vastgelegd in `.claude/rules/klai/platform/docker-socket-proxy.md`.
- Deze SPEC formaliseert de *audit + hardening* stap: bewijs leveren dat er geen
  verborgen `exec_run` meer overblijft, de proxy-whitelist terugbrengen naar het
  minimum, en een CI-guard + deploy-smoke-test toevoegen zodat een regressie
  mechanisch gevangen wordt.

---

## Achtergrond

SEC-021 heeft de directe `/var/run/docker.sock` bind-mount bij portal-api en
runtime-api vervangen door een proxy die alleen een handvol Docker API verbs
toelaat. Het expliciete doel was om de blast radius van een gecompromitteerde
container terug te brengen van "root-equivalent op core-01" naar "alleen de
endpoints die de applicatie daadwerkelijk nodig heeft".

De implementatie was correct, maar de **compliance verificatie ontbrak**:
niemand had systematisch gescand of de productie-code (a) nog andere verboden
endpoints raakte en (b) of de ingestelde whitelist daadwerkelijk minimaal was.
Het incident van 2026-04-21 — Redis FLUSHALL via `redis_ctr.exec_run(...)`
tijdens een routine tenant-offboarding — maakte dit gat zichtbaar.

Na de hot-fix blijft er werk liggen:
- Het is niet bewezen dat *alle* `exec_run` callsites weg zijn.
- De proxy grant `CONTAINERS=1 NETWORKS=1 POST=1 DELETE=1` is toegekend zonder
  per-verb motivatie. Mogelijk zit daar nog overschot in (bijv. `DELETE=1` is
  vandaag alleen nodig voor container-cleanup; `NETWORKS=1` raakt de lopende
  `net.connect(container_name)` in `_start_librechat_container`).
- Er is geen mechanische guard: morgen kan iemand ongemerkt weer een
  `exec_run([...])` toevoegen.

---

## Scope

**In scope:**
- Exhaustieve scan van `klai-portal/backend/` op elke Docker API aanroep die
  achter de proxy loopt (zowel `exec_run` als gerelateerde endpoints:
  `/exec/*/start`, `/containers/{id}/top`, `/containers/{id}/attach`,
  `/containers/{id}/commit`).
- Per-verb motivatie van de huidige `docker-socket-proxy` whitelist en reductie
  tot het minimum.
- CI-guard die een PR blokkeert zodra `exec_run(` opnieuw opduikt in
  productie-code.
- Documentatie-sync: concrete "allowed verbs" subsectie in
  `.claude/rules/klai/platform/docker-socket-proxy.md` afgeleid uit de audit.
- Post-deploy smoke-test (1 script, max 1 pagina) die per toegelaten verb één
  call doet en verifieert dat de proxy de call accepteert.

**Zie ook "Buiten scope" hieronder.**

---

## Buiten scope (What NOT to Build)

- **Rewriten van `runtime-api`'s Docker SDK-gebruik.** Runtime-api is een
  vendored Vexa image (`vexaai/runtime-api:0.10.0-260419-1129`). We hebben
  geen controle over de source. De audit behandelt runtime-api uitsluitend als
  *black-box consumer* van de proxy-whitelist. Eventuele code-refactor van
  runtime-api wordt in een losse SPEC opgenomen of niet-gedaan.
- **Kubernetes-migratie.** De socket-proxy pattern is Docker-Compose specifiek.
  Een move naar Kubernetes verandert het gehele dreigingsmodel en valt buiten
  deze audit.
- **Arbitrary-container exec als feature.** We willen `EXEC=1` op de proxy
  niet alleen voor productiecode niet, maar ook niet voor operationele tooling,
  debugging of migrations. Elke toekomstige feature die neigt naar "voer iets
  uit in een sidecar" moet via native protocol of een HTTP endpoint op de
  sidecar zelf — nooit via `exec_run`.
- **Het verwijderen van de `exec_run` referenties in tests.** De bestaande
  regressie-guards in
  `klai-portal/backend/tests/services/provisioning/test_infrastructure.py` en
  `klai-portal/backend/tests/test_librechat_regenerate.py` verifiëren juist
  dat productiecode géén `exec_run` aanroept. Die blijven staan.
- **runtime-api image upgrade.** Blijft op `0.10.0-260419-1129`. Een upgrade
  is trackable onder een los VEXA SPEC.

---

## EARS Requirements

Alle requirements zijn traceerbaar via de `SPEC-SEC-024-Rn` tag.

### Exhaustieve scan

**SPEC-SEC-024-R1** — WHEN de audit uitgevoerd wordt, the system SHALL een
exhaustieve scan produceren van elke `exec_run(` aanroep in
`klai-portal/backend/` (inclusief `scripts/`, `tests/`, `migrations/` en
`docs/`), waarbij per hit het verdict `fixed` / `never-ran` / `acceptable`
met motivatie gedocumenteerd is.

**SPEC-SEC-024-R2** — WHEN de audit uitgevoerd wordt, the system SHALL
daarnaast scannen op aanroepen van Docker API endpoints die door de huidige
whitelist *niet* zijn toegelaten en die via de docker-py client bereikbaar
zijn: `container.top()`, `container.attach()`, `container.commit()`,
`container.export()`, `container.diff()`, `container.stats(stream=True)`,
`client.images.build(...)`, `client.images.pull(...)`, `client.volumes.*`.

**SPEC-SEC-024-R3** — WHERE een gescand symbol een legitieme use-case heeft
(bijv. tests-only), the audit SHALL het pad expliciet op een allow-list
plaatsen met schriftelijke motivatie; anders SHALL het pad een
implementation-action krijgen (remove / replace / refactor).

### Per-verb motivatie en reductie

**SPEC-SEC-024-R4** — WHEN de proxy-whitelist beoordeeld wordt, the system
SHALL per environment-variabele (`CONTAINERS`, `NETWORKS`, `POST`, `DELETE`,
`IMAGES`, `VOLUMES`, `EXEC`, `BUILD`, `SYSTEM`, `PLUGINS`, `EVENTS`, `INFO`,
`VERSION`, `PING`) een motivatie-tabel opleveren met:
(a) welk concreet code-pad in portal-api of runtime-api de verb gebruikt,
(b) de HTTP route(s) die geraakt worden,
(c) verdict `keep` / `drop` / `not-set`.

**SPEC-SEC-024-R5** — WHEN de audit-tabel (R4) compleet is, the system
SHALL **elke** verb die geen concreet productie-code-pad in portal-api
aantoonbaar raakt in `deploy/docker-compose.yml` droppen — ook als
runtime-api (black-box) de verb mogelijk gebruikt. Pre-launch is "eerst
droppen, dan observeren" goedkoper dan "eerst conservatief laten staan en
later snoeien": een runtime-api-403 verschijnt meteen in de permanente
alert (R12) en kan met één compose-edit teruggedraaid worden. De
smoke-test (R10) valideert de reductie direct post-deploy.

**SPEC-SEC-024-R6** — the system SHALL NEVER `EXEC=1`, `BUILD=1`, `IMAGES=1`
(write), `VOLUMES=1`, `SYSTEM=1` of `PLUGINS=1` op
`docker-socket-proxy` activeren, ook niet tijdens debugging of migrations.

### CI-guard

**SPEC-SEC-024-R7** — WHEN een pull request files muteert onder
`klai-portal/backend/app/`, the CI SHALL een ast-grep (of gelijkwaardige
semgrep) regel draaien die iedere nieuwe `exec_run(` match laat falen; een
allow-list mag hit-paden expliciet overslaan met inline rationale.

**SPEC-SEC-024-R8** — WHILE de CI-guard actief is, the system SHALL de regel
ook toepassen op toekomstige toevoegingen onder `klai-portal/backend/scripts/`
en `klai-portal/backend/tests/` (allow-listed maar met audit-log), zodat een
nieuwe productiepath niet via een omweg binnensluipt.

### Documentatie-sync

**SPEC-SEC-024-R9** — WHEN de audit-conclusies definitief zijn, the system
SHALL `.claude/rules/klai/platform/docker-socket-proxy.md` uitbreiden met een
subsectie "Allowed verbs (per-verb rationale)" waarin de reductie uit R4–R5
één-op-één terug te lezen is, met cross-reference naar SPEC-SEC-024.

### Smoke-test (deploy-time)

**SPEC-SEC-024-R10** — WHEN een nieuwe deploy naar core-01 voltooid is, the
system SHALL een idempotent smoke-test script (maximaal 1 pagina shell) uit
portal-api of van core-01 zelf draaien dat per *toegelaten* verb één
minimaal-invasieve call doet en asserteert dat de proxy de call accepteert
(bijv. `GET /containers/json` voor `CONTAINERS=1`, `GET /networks` voor
`NETWORKS=1`, `POST /containers/{noop}/restart` voor `POST=1` op een
wegwerp-container, gevolgd door `DELETE /containers/{noop}` voor `DELETE=1`).

**SPEC-SEC-024-R11** — IF de smoke-test faalt op een verb die R4 als "keep"
motiveerde, THEN de deploy SHALL als failed gemarkeerd worden en een
rollback-instructie SHALL in de post-deploy output verschijnen.

### Permanente monitoring (vervangt de 24u-window)

**SPEC-SEC-024-R12** — WHILE portal-api of runtime-api in productie draait
achter `docker-socket-proxy`, the system SHALL een permanente Grafana
alert-rule hebben die zero-tolerance vuurt op elk log-record dat matcht
met `service:portal-api OR service:runtime-api` gecombineerd met
`"Forbidden" AND "docker-socket-proxy"`. De rule SHALL:
- evalueren elke 1 minuut over een `[5m]` venster,
- zonder pending-period vuren (`for: 0`) — één hit is genoeg,
- groeperen per `(alertname, service)` met `repeat_interval: 24h` zodat
  dezelfde bug niet meer dan één e-mail per dag produceert,
- auto-resolven na 30 minuten zonder nieuwe hits,
- een `deploy` annotatie-overlay tonen op het bijbehorende dashboard zodat
  een piek direct aan een commit/deploy gekoppeld kan worden.

**SPEC-SEC-024-R13** — WHEN de alert-rule vuurt, the system SHALL een
e-mail-notificatie sturen naar het bestaande Grafana contact point voor
dev-alerts (géén Slack, géén PagerDuty — severity `warning` in pre-launch).

**SPEC-SEC-024-R14** — WHEN een test-regressie (bewust `exec_run` in een
throwaway branch gemerged naar een staging-deploy) de alert triggert, the
system SHALL binnen 2 minuten na de eerste 403 een alert firen —
bewezen via een end-to-end dry-run vóór merge.

---

## Succescriteria (samenvatting)

1. Audit-rapport in `plan.md` met:
   - Complete `exec_run` scan-tabel (file, regel, verdict, actie)
   - Complete Docker API verb-rationale tabel
2. `deploy/docker-compose.yml` bevat de **agressief gereduceerde** set
   (en expliciet géén `EXEC=1` / `BUILD=1` / `VOLUMES=1` / `SYSTEM=1`).
3. `.ast-grep/no-exec-run.yml` (of `.semgrep/` equivalent) gecommit en
   geactiveerd in CI voor `klai-portal/backend/`.
4. `docker-socket-proxy.md` pitfall-regel bevat de "Allowed verbs" subsectie.
5. `scripts/smoke-docker-socket-proxy.sh` (≤ 1 pagina) aanwezig, draait
   post-deploy succesvol, faalt hard bij een geblokkeerde verb die "keep"
   is. Script ruimt wegwerp-containers op via `trap EXIT` +
   `--rm` + `sleep 60` als drievoudig vangnet.
6. Permanente Grafana alert-rule + dashboard aanwezig, e-mail als kanaal,
   getriggerd door een end-to-end test-regressie vóór merge.
7. Rollback-strategie: **fix-forward**. Geen afzonderlijke rollback-test,
   geen revert-ceremonie. Als iets breekt push je een fix-commit.

---

## Referenties

- **SEC-021** (parent): `.moai/specs/SPEC-SEC-021/spec.md` — routing naar de proxy
- **Pitfall-regel**: `.claude/rules/klai/platform/docker-socket-proxy.md`
- **Fixes van vandaag**:
  - commit `c5653159` — `fix(provisioning): replace docker exec with pymongo/redis protocol`
  - commit `a3920a75` — `fix(internal): redis FLUSHALL via protocol, not docker exec`
  - commit `9dc03c67` — `fix(chat-health): probe /api/config instead of /api/endpoints`
- **Proxy image**: `tecnativa/docker-socket-proxy:v0.4.2`
  (`deploy/docker-compose.yml:281-292`)
- **Consumers**: portal-api (`deploy/docker-compose.yml:316`),
  runtime-api (`deploy/docker-compose.yml:891`)
