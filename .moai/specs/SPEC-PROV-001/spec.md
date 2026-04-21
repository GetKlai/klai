---
id: SPEC-PROV-001
version: 0.2.0
status: draft
created: 2026-04-19
updated: 2026-04-21
author: mark.vletter@voys.nl
priority: HIGH
issue_number: null
---

# SPEC-PROV-001: Transactionele tenant provisioning met rollback en idempotent retry

## HISTORY

| Datum       | Versie | Wijziging                                                       | Auteur               |
|-------------|--------|-----------------------------------------------------------------|----------------------|
| 2026-04-19  | 0.1.0  | Initiële draft, opgesteld na SEC-021 refactor (commits `c5653159`, `a3920a75`) | mark.vletter@voys.nl |
| 2026-04-21  | 0.2.0  | Sparring-review: `AsyncExitStack`-patroon voor rollback, soft-delete + partial unique index voor slug-release, inline Alembic UPDATE i.p.v. los migratiescript, startup stuck-detector toegevoegd. | mark.vletter@voys.nl |

## Context en trigger

Tenant provisioning in `klai-portal/backend/app/services/provisioning/orchestrator.py`
voert een reeks side-effects uit (Zitadel OIDC app, LiteLLM team, MongoDB user, .env,
Docker container, Caddyfile, system groups, DB commit) zonder expliciete transactie-
semantiek. State wordt bijgehouden in een in-memory `_ProvisionState` dataclass. De
huidige failure handler (zie regels 293–304) roept `_rollback(state)` aan binnen een
brede `except Exception`, maar:

1. Als het proces hard faalt (SIGKILL, OOM, crash) tussen twee stappen, wordt
   `_rollback` nooit uitgevoerd en blijft de rommel staan.
2. Bij retry van signup hit `_create_mongodb_tenant_user` sinds de SEC-021 herschrijving
   (commit `c5653159`) een `OperationFailure(code=51003, UserExists)`, die vervolgens
   wordt omgezet in `RuntimeError` — handmatige DB cleanup is de enige weg terug.
3. Er is geen state machine: `provisioning_status` heeft drie waarden (`pending`,
   `ready`, `failed`) en biedt geen zicht op welke stap de rolmop heeft opgezet.
4. Concurrent signups voor dezelfde org (dubbele submit, polling + BFF retry) kunnen
   dezelfde steps parallel uitvoeren. De orchestrator serialiseert niet op DB-niveau.

Deze SPEC introduceert een expliciete state machine op `provisioning_status`, per-step
checkpointing, gepaarde compensators uitgevoerd via `contextlib.AsyncExitStack`
(industry-standard Python-patroon, al in gebruik in `main.py` en `database.py`),
slug-vrijgave via soft-delete + partial unique index (industry-standard SaaS-patroon,
o.a. Linear/Notion/GitLab), een startup stuck-detector en een admin-only retry endpoint.

Referenties:
- SEC-021 post-incident: `.claude/rules/klai/platform/docker-socket-proxy.md`
- Vandaag herschreven leaf functions: `klai-portal/backend/app/services/provisioning/infrastructure.py` (commits `c5653159`, `a3920a75`)
- Health probe consumer: `klai-portal/backend/app/api/app_chat.py` (regel 65)
- Me endpoint consumer: `klai-portal/backend/app/api/me.py` (regels 50, 89, 104, 135)

## Doel

Tenant provisioning is een **one-level compenserende transactie**: elke forward step
heeft een gepaarde compensator geregistreerd op een `AsyncExitStack`, elke transitie is
een DB checkpoint, en elke recoverable fout resulteert ofwel in een `ready` tenant
ofwel in een `failed_rollback_complete` tenant die soft-deleted is (slug wordt vrijgegeven
via de partial unique index). Operators hoeven alleen nog in te grijpen bij
`failed_rollback_pending` (rollback zelf gefaald).

## EARS Requirements

### Ubiquitous — altijd geldig

**SPEC-PROV-001-R1 (Ubiquitous).**
Het provisioning-subsysteem **shall** voor elke forward step die persistente side-effects
oplevert, één gepaarde idempotente compensator definiëren in `infrastructure.py` of in de
orchestrator zelf, en deze compensator **shall** op een `contextlib.AsyncExitStack`
worden geregistreerd via `stack.push_async_callback(compensator, *args)` direct na een
succesvolle forward step. Bij happy-path afsluiting wordt `stack.pop_all()` aangeroepen
om de stack te consumeren zonder compensators te draaien.

**SPEC-PROV-001-R2 (Ubiquitous).**
Elke wijziging van `portal_orgs.provisioning_status` **shall** plaatsvinden binnen een
`SELECT ... FOR UPDATE` transactie op de PortalOrg-rij, gevolgd door `UPDATE` en `COMMIT`,
zodat concurrente provision/retry requests niet racen.

**SPEC-PROV-001-R3 (Ubiquitous).**
Elke state transitie **shall** één structured log entry emitten (`logger.info(
"provisioning_state_transition", org_id=..., slug=..., from_state=..., to_state=...,
step=...)`) én één `product_event` (`event_type="provisioning.state_transition"`).

**SPEC-PROV-001-R4 (Ubiquitous).**
De `provisioning_status` kolom **shall** een DB-niveau `CHECK` constraint krijgen die
alleen de in R8 gedefinieerde waarden toestaat, plus de legacy waarden `pending` en
`failed` gedurende de migratieperiode.

### State-driven — afhankelijk van huidige status

**SPEC-PROV-001-R5 (State-driven).**
**While** `provisioning_status` gelijk is aan `queued`, the orchestrator **shall**
iedere forward step exact één keer uitvoeren, elke successvolle step afsluiten met een
state transitie naar de corresponderende step-naam (zie R8), en na de laatste step naar
`ready` transitioneren.

**SPEC-PROV-001-R6 (State-driven).**
**While** `provisioning_status` gelijk is aan `failed_rollback_complete`, the retry
endpoint **shall** de org rij terugzetten naar `queued` en de forward sequence opnieuw
starten zonder dat de aanroeper handmatige DB cleanup hoeft uit te voeren.

**SPEC-PROV-001-R7 (State-driven).**
**While** `provisioning_status` gelijk is aan `failed_rollback_pending`, the retry
endpoint **shall** HTTP 409 teruggeven met body `{"error": "manual_cleanup_required",
"state": "failed_rollback_pending"}` en **shall niet** opnieuw forward of rollback starten.

### Event-driven — triggered by events

**SPEC-PROV-001-R8 (Event-driven).**
**When** `provision_tenant(org_id)` wordt aangeroepen voor een org in status
`pending` of `queued`, the orchestrator **shall** per forward step eerst een checkpoint
write doen naar één van de volgende statussen, in deze volgorde:

| Volgorde | State                         | Forward step                                                                 | Compensator                              |
|----------|-------------------------------|------------------------------------------------------------------------------|------------------------------------------|
| 0        | `queued`                      | (begin)                                                                      | n.v.t.                                   |
| 1        | `creating_zitadel_app`        | `zitadel.create_librechat_oidc_app`                                          | `zitadel.delete_librechat_oidc_app`      |
| 2        | `creating_litellm_team`       | `POST /team/new` + `POST /key/generate` op LiteLLM                           | `POST /team/delete` op LiteLLM           |
| 3        | `creating_mongo_user`         | `_create_mongodb_tenant_user`                                                | `_sync_drop_mongodb_tenant_user`         |
| 4        | `writing_env_file`            | `tenant_dir.mkdir` + `.env` write                                            | `shutil.rmtree(tenant_dir)`              |
| 5        | `creating_personal_kb`        | `docs-app POST /api/orgs/{slug}/kbs` (soft dependency, zie R13)              | `docs-app DELETE /api/orgs/{slug}/kbs/personal` (best-effort) |
| 6        | `creating_portal_kbs`         | `ensure_default_knowledge_bases`                                             | n.v.t. (interne DB, rollt mee met DB transactie) |
| 7        | `starting_container`          | `_start_librechat_container`                                                 | `_sync_remove_container`                 |
| 8        | `writing_caddyfile`           | `_write_tenant_caddyfile`                                                    | `unlink(tenant_file)`                    |
| 9        | `reloading_caddy`             | `_reload_caddy`                                                              | `_reload_caddy` (na unlink)              |
| 10       | `creating_system_groups`      | `create_system_groups`                                                       | n.v.t. (rollt mee met DB transactie)     |
| 11       | `ready`                       | Eindtoestand (org velden + status commit)                                    | —                                        |

**SPEC-PROV-001-R9 (Event-driven).**
**When** een forward step een exception raiset, the orchestrator **shall**:

1. `provisioning_status` atomair transitioneren naar `failed_rollback_pending` (checkpoint bevat `last_forward_state`);
2. Compensators uitvoeren in **omgekeerde volgorde** via `AsyncExitStack.__aexit__` (LIFO automatisch door de stack);
3. Na succesvolle afronding van alle compensators `provisioning_status` transitioneren naar `failed_rollback_complete` en `org.deleted_at = func.now()` zetten zodat de slug via de partial unique index vrijkomt voor retry (zie R15);
4. De oorspronkelijke exception loggen via `logger.exception("provisioning_failed", org_id=..., last_forward_state=...)`.

**SPEC-PROV-001-R10 (Event-driven).**
**When** een compensator tijdens rollback een exception raiset, the orchestrator **shall**:

1. De exception loggen via `logger.exception("provisioning_rollback_failed", step=..., error=...)` met volledige context (org_id, slug, step-naam, oorspronkelijke forward exception);
2. De volgende compensator nog steeds proberen uit te voeren (best-effort rollback zoals de huidige `_rollback`);
3. `provisioning_status` op `failed_rollback_pending` laten staan — **shall niet** naar `failed_rollback_complete` transitioneren;
4. De oorspronkelijke forward exception bubble laten (`raise`).

**SPEC-PROV-001-R11 (Event-driven).**
**When** `POST /api/orgs/{slug}/retry-provisioning` wordt aangeroepen door een caller met
admin rol voor een org in `failed_rollback_complete`, the endpoint **shall**:

1. De org rij met `SELECT ... FOR UPDATE` locken (lookup via `id`, niet via `slug` —
   de failed rij is soft-deleted en de partial unique index maakt slug-lookup ambigu
   zodra een retry een nieuwe actieve rij met dezelfde slug heeft aangemaakt);
2. Verifiëren dat `provisioning_status == "failed_rollback_complete"` (herlezen binnen de lock);
3. `provisioning_status` transitioneren naar `queued` en `deleted_at` terugzetten naar `NULL` — dit maakt de rij weer actief; de partial unique index garandeert dat er op dat moment geen andere actieve rij met dezelfde slug bestaat;
4. `provision_tenant(org_id)` schedulen als `BackgroundTask`;
5. HTTP 202 Accepted retourneren met body `{"status": "queued"}`.

**SPEC-PROV-001-R12 (Event-driven).**
**When** een state transitie plaatsvindt, the orchestrator **shall** een
`product_event` record inserten met `event_type="provisioning.state_transition"`,
`org_id=<id>`, en `properties={"from_state": ..., "to_state": ..., "step": ...,
"duration_ms": ...}` — bruikbaar als Grafana timeline per tenant signup.

### Optional — feature-afhankelijk

**SPEC-PROV-001-R13 (Optional).**
**Where** de docs-app (persoonlijke KB creatie) niet bereikbaar is, the orchestrator
**shall** de step als **soft success** loggen (`logger.error(
"docs_kb_creation_degraded_docs_app_unreachable", ...)`) en doorgaan naar de volgende
step — conform het bestaande degradatie-gedrag in `orchestrator.py` regels 241–249.
Een non-2xx response van een bereikbare docs-app **shall** echter als harde failure
behandeld worden en de forward sequence aborten (trigger van R9).

### Unwanted behavior — verboden

**SPEC-PROV-001-R14 (Unwanted).**
**If** meerdere concurrent `provision_tenant(org_id)` of `retry-provisioning` aanroepen
voor dezelfde org binnenkomen, **then** the orchestrator **shall** maximaal één forward
sequence tegelijk uitvoeren, geforceerd door `SELECT ... FOR UPDATE` op de
`portal_orgs` rij aan het begin van iedere state transitie.

**SPEC-PROV-001-R15 (Unwanted).**
**If** de orchestrator een org op slug `X` rollback't naar `failed_rollback_complete`,
**then** the orchestrator **shall** `org.deleted_at = func.now()` zetten (soft-delete).
De partial unique index `ix_portal_orgs_slug_active` (`UNIQUE (slug) WHERE deleted_at IS NULL`)
geeft de slug daarmee automatisch vrij voor een nieuwe signup of retry. De slug blijft op
de gefaalde rij zichtbaar voor audit. Dit vervangt het originele "slug leegmaken"-patroon
en volgt de industry standard zoals toegepast door Linear, Notion en GitLab.

**SPEC-PROV-001-R16 (Unwanted).**
**If** `provisioning_status` niet in de toegestane verzameling valt, **then** the DB
**shall** de UPDATE afwijzen via de CHECK constraint uit R4.

**SPEC-PROV-001-R17 (Unwanted).**
**If** de caller van `retry-provisioning` geen admin rol heeft, **then** the endpoint
**shall** HTTP 403 teruggeven en **shall niet** de provisioning state wijzigen.

### Event-driven — startup reconciliation

**SPEC-PROV-001-R21 (Event-driven).**
**When** portal-api opstart (in de FastAPI `lifespan` startup fase), the service **shall**
een stuck-detector draaien die alle orgs vindt waarvoor geldt:
`provisioning_status NOT IN ('ready', 'failed_rollback_pending', 'failed_rollback_complete', 'pending', 'queued')`
**AND** `updated_at < now() - interval '15 minutes'`. Voor elke gevonden org **shall** de
detector:

1. Één `logger.warning("provisioning_stuck_detected", org_id=..., last_state=..., stuck_since=...)` entry emitten;
2. `provisioning_status` transitioneren naar `failed_rollback_pending` (zonder compensators te draaien — de in-memory `AsyncExitStack` is verloren bij restart, dus compensatie is niet veilig);
3. Één `product_event` met `event_type="provisioning.stuck_recovered"` inserten.

De stuck-detector **shall niet** retry starten of compensators aanroepen. Operators zien
de org terug in Grafana als `failed_rollback_pending` en moeten de externe resources
handmatig inspecteren vóór ze de state naar `failed_rollback_complete` mappen.

### Ubiquitous — compatibiliteit met bestaande consumers

**SPEC-PROV-001-R18 (Ubiquitous).**
Het `get_chat_health` endpoint (`app/api/app_chat.py`) **shall** blijven reageren met
`healthy=False, reason="provisioning_in_progress"` voor elke waarde van
`provisioning_status` ongelijk aan `"ready"` — geen wijziging in de probe-contract. Dit
betekent dat alle nieuwe tussenstaten (`queued`, `creating_*`, `starting_container`,
`writing_caddyfile`, `reloading_caddy`, `failed_rollback_*`) impliciet gemapt worden op
`provisioning_in_progress` voor de iframe pre-flight check.

**SPEC-PROV-001-R19 (Ubiquitous).**
Het `/api/me` endpoint (`app/api/me.py`) **shall** de nieuwe `provisioning_status` waarden
onveranderd doorgeven als string. De frontend **shall** voor onbekende waarden terugvallen
op het generieke "provisioning in progress" copy — aparte UI strings per tussenstaat zijn
out-of-scope voor deze SPEC.

**SPEC-PROV-001-R20 (Ubiquitous).**
Het interne endpoint in `app/api/internal.py` regel 959 dat filtert op
`provisioning_status == "ready"` **shall** onveranderd blijven werken — alleen
volledig geprovisionede orgs worden door deze endpoint opgepakt.

## Exclusions (What NOT to Build)

Expliciet buiten scope voor SPEC-PROV-001:

0. **User-facing retry knop.** Provisioning loopt volledig autonoom op signup. Als het
   faalt retried de gebruiker door een nieuwe signup te doen (nieuwe org-rij).
   Het retry-endpoint is admin-only voor ops en voor e2e-tests die eindgebruiker-
   gedrag simuleren.
1. **Multi-region failover.** De orchestrator draait op één core-01 node. Failover
   tussen regions vereist een heel ander consistency-model (distributed locks, quorum)
   en is buiten scope.
2. **Concurrent signups door dezelfde org.** De signup-flow op de API layer (niet deze
   SPEC) serialiseert dubbele submits. Deze SPEC dekt alleen concurrent provision/retry
   aanroepen voor een reeds aangemaakte `portal_orgs` rij (via `SELECT FOR UPDATE`).
3. **Full sagas pattern.** We implementeren one-level compensation, geen gedistribueerde
   sagas met event sourcing, geen compensation log met replay. Alle state leeft in één
   tabel (`portal_orgs.provisioning_status`). Een faliërende rollback blijft in
   `failed_rollback_pending` staan — geen geautomatiseerde rollback-retry.
4. **Nieuwe UI strings per provisioning state.** De frontend blijft één "provisioning
   in progress" copy tonen voor alle tussenstaten. Meer granulaire UI feedback is een
   aparte feature.
5. **Observability beyond structured logs + product_events.** Geen nieuwe Prometheus
   metrics, geen OpenTelemetry spans, geen dedicated Grafana dashboard. Bestaande
   VictoriaLogs queries en het `product_events` tabel voldoen.
6. **Aanpassing aan deprovisioning.** Tenant offboarding is een aparte flow en wordt
   in deze SPEC niet aangeraakt. De compensators worden gedeeld met offboarding waar
   ze al idempotent zijn (zie `_sync_drop_mongodb_tenant_user`).
7. **Docker-socket-proxy aanpassingen.** Alle provisioning steps respecteren het
   bestaande protocol-first contract uit SEC-021 (geen `container.exec_run`).

## Risico's

### R1. Database zelf is down midden in provisioning

**Impact:** Forward sequence faalt op een willekeurige DB write. State transitie kan
niet gepersisteerd worden → `provisioning_status` blijft hangen op de laatst-succesvolle
checkpoint state.

**Mitigatie:** De outer `except Exception` van `_provision` catcht de DB error. Rollback
wordt geprobeerd met de in-memory `_ProvisionState` als leidraad (fallback voor als de
DB herstelt tijdens rollback). Als de DB down blijft, bubbled de exception up naar de
BackgroundTask runner en wordt de stuck state later hersteld door de retry endpoint
(R6/R11).

**Residual risk:** Als de DB down is en blijft tijdens rollback, stranden externe
resources (Zitadel app, LiteLLM team, Mongo user, Docker container, Caddyfile). Na
DB-herstel moet een operator de org op `failed_rollback_pending` zetten en handmatig
opruimen. Hetzelfde risico bestaat vandaag al.

### R2. Docker socket-proxy faalt midden in provisioning

**Impact:** `_start_librechat_container` of `_sync_remove_container` raiset
`docker.errors.APIError`. De container-step faalt, maar alle eerder aangemaakte
resources zijn wel gecommit.

**Mitigatie:** R9 triggert rollback. De container-compensator (`_sync_remove_container`)
is idempotent — `docker.errors.NotFound` wordt al gewallowed. Als socket-proxy blijft
falen tijdens rollback, blijft status op `failed_rollback_pending` en handelt een
operator het af.

**Residual risk:** Een kapotte socket-proxy maakt alle compensators die Docker aanroepen
onbruikbaar. Operators kunnen na socket-proxy herstel de retry endpoint gebruiken om
opnieuw te proberen (status `failed_rollback_pending` blokkeert retry bewust — operator
moet eerst manual cleanup doen en de state handmatig op `failed_rollback_complete` zetten).

### R3. Compensator faalt mid-way in rollback

**Impact:** Bijvoorbeeld Mongo drop faalt na succesvolle container remove. Systeem
zit in half-geopruimde staat.

**Mitigatie:** R10 specifieert dat compensators best-effort doorgaan — volgende
compensators worden alsnog geprobeerd. De laatst mislukte compensator is zichtbaar in
de structured log. Status blijft `failed_rollback_pending` zodat retry (R7) 409
teruggeeft en geen tweede rollback cycle start.

**Residual risk:** Operator moet manueel de resterende resources opruimen.
Deprovisioning scripts (buiten scope) kunnen hergebruikt worden.

### R4. Race tussen twee admin retry calls

**Impact:** Twee admins drukken gelijktijdig op "Retry". Beide lezen
`failed_rollback_complete`, starten beide een forward sequence.

**Mitigatie:** R11 vereist `SELECT ... FOR UPDATE` op de PortalOrg rij binnen de retry
endpoint. De tweede caller leest de status na de lock-release als `queued` of verder en
valt door naar R6/R14: de endpoint retourneert 409 conflict.

**Residual risk:** Geen — FOR UPDATE serialiseert bewezen. Bijkomende voorwaarde: de
signup endpoint serialiseert ook dubbele initiële provisioning-aanroepen op dezelfde
org_id. Dit is al gegarandeerd omdat signup een nieuwe `portal_orgs` rij aanmaakt per
aanroep; het is de verantwoordelijkheid van de signup API layer (buiten deze SPEC) om
dubbele submits te voorkomen — zie exclusion #2.

### R5. Stray legacy state `active` in testfixtures

**Impact:** `tests/test_me_org_found.py` zet `org.provisioning_status = "active"` — dit
is geen productie state maar zal na invoering van de CHECK constraint een
`IntegrityError` veroorzaken in de test suite.

**Mitigatie:** De migratie-tests opschonen als onderdeel van deze SPEC (zie migratie
plan). Testfixture vervangen door `"ready"`.

### R6. Retry op soft-deleted rij vs. nieuwe signup race

**Impact:** Als user-signup een nieuwe org-rij aanmaakt met slug `X` tussen het moment
van `failed_rollback_complete` (soft-delete) en een admin `retry-provisioning` call, zijn
er twee rijen: één soft-deleted met slug `X`, één actief met slug `X`. De partial unique
index staat dit toe. Retry op de soft-deleted rij zou dan `deleted_at = NULL` willen
zetten — dat zou de partial unique index schenden.

**Mitigatie:** Retry endpoint verifieert binnen de `SELECT FOR UPDATE`-lock dat er geen
andere actieve rij is met dezelfde slug. Zo ja → retourneer 409
`{"error": "slug_in_use_by_new_org", "state": "failed_rollback_complete"}`. Admin moet
dan beslissen: de soft-deleted rij definitief hard-deleten, of een andere actie.

**Residual risk:** Ops-scenario, komt zelden voor. Documenteren in runbook (M6).

### R7. Stuck-detector racet met lopende provisioning tijdens startup

**Impact:** Een provisioning-run die ten tijde van portal-api shutdown net een state
transitie committe maar nog niet had afgerond, zou door de stuck-detector op
`failed_rollback_pending` gezet kunnen worden — terwijl er misschien nog een valide
BackgroundTask elders loopt. Op een enkele core-01 node met één portal-api-instance
speelt dit niet; de detector draait alleen bij startup en er is dan per definitie geen
actieve BackgroundTask.

**Mitigatie:** De detector gebruikt `updated_at < now() - interval '15 minutes'` als
gate. Een provisioning-run die de afgelopen 15 minuten een state checkpoint heeft
geschreven wordt overgeslagen. Na portal-api restart zonder legacy proces is de drempel
altijd veilig.

**Residual risk:** Als in de toekomst meerdere portal-api replicas gaan draaien, moet
de stuck-detector met een advisory lock serialiseren. Voor nu (single-node) is
`updated_at` voldoende.

### R8. `provisioning_status` lekt in `/api/me` naar frontend

**Impact:** Nieuwe tussenstaten (`creating_mongo_user` etc.) worden als string aan de
frontend geleverd. Frontend heeft geen i18n voor deze waarden.

**Mitigatie:** R19 specificeert fallback gedrag. Frontend toont één generieke copy voor
alle niet-`ready` en niet-`failed_rollback_*` waarden. De twee failed states krijgen wél
aparte copy (zie acceptance.md scenario 7).

## Migratieplan

### 1. DB migratie (nieuwe Alembic revision `add_provisioning_state_machine_constraint`)

Deze migratie doet drie dingen in één revision:

**1a. Schema-uitbreiding voor soft-delete**

- Nieuwe kolom `portal_orgs.deleted_at TIMESTAMPTZ NULL` (default NULL).
- Bestaande unique index `ix_portal_orgs_slug` droppen.
- Nieuwe partial unique index aanmaken:
  ```sql
  CREATE UNIQUE INDEX ix_portal_orgs_slug_active
      ON portal_orgs (slug)
      WHERE deleted_at IS NULL;
  ```

**1b. Inline UPDATE voor de twee bestaande test-orgs**

Productie heeft momenteel twee test-tenants (Voys en Klai zelf). Geen los migratie­
script — handmatig gecontroleerde UPDATE-statements in de Alembic migratie:

```sql
-- Beide test-orgs zijn momenteel ready; eventuele legacy 'active' of 'failed' waarden
-- worden hier expliciet naar de nieuwe state machine gemapt.
UPDATE portal_orgs
SET provisioning_status = 'ready'
WHERE slug IN ('voys', 'klai') AND provisioning_status IN ('active', 'ready');

-- Fail-safe: als er onverwacht nog een 'failed' rij blijkt te staan, markeer die
-- als failed_rollback_pending zodat ops hem ziet in Grafana.
UPDATE portal_orgs
SET provisioning_status = 'failed_rollback_pending'
WHERE provisioning_status = 'failed';
```

Vóór de deploy voert de operator `SELECT id, slug, provisioning_status FROM portal_orgs`
uit op productie om te verifiëren dat deze twee UPDATE-statements de lading dekken.

**1c. CHECK constraint**

```sql
ALTER TABLE portal_orgs ADD CONSTRAINT ck_portal_orgs_provisioning_status CHECK (
    provisioning_status IN (
        'pending', 'queued',
        'creating_zitadel_app', 'creating_litellm_team', 'creating_mongo_user',
        'writing_env_file', 'creating_personal_kb', 'creating_portal_kbs',
        'starting_container', 'writing_caddyfile', 'reloading_caddy',
        'creating_system_groups', 'ready',
        'failed_rollback_pending', 'failed_rollback_complete'
    )
);
```

Legacy waarde `pending` blijft toegestaan voor orgs tussen signup en BackgroundTask-start.
Legacy waarde `failed` wordt door de UPDATE uit §1b al uitgefaseerd, dus staat niet in de
CHECK. Als er na deploy onverhoopt nog een `failed`-rij ontstaat (zou niet mogen), faalt
de constraint hard — een gewenst fail-loud signaal.

### 2. Signup flow update

- `app/api/auth/signup.py` (de caller van `provision_tenant`) initialiseert nieuwe orgs op `provisioning_status='queued'` in plaats van `'pending'`. De orchestrator leest `queued` en start. `pending` blijft backward-compatible voor orgs die nog tussen signup en BackgroundTask-start zitten (R8 accepteert beide).

### 3. Frontend update (out-of-scope, andere SPEC)

- Frontend copy voor `failed_rollback_pending` (= "support contact required") en
  `failed_rollback_complete` (= "you can retry now") is een aparte FE wijziging. Voor
  deze SPEC is fallback gedrag op generieke copy voldoende (R19).

### 4. Deploy volgorde

1. Alembic migratie met CHECK constraint + mapping script van `failed` → `failed_rollback_complete` / `failed_rollback_pending`.
2. Deploy backend met nieuwe orchestrator + retry endpoint.
3. Frontend update (aparte SPEC) na 1 week observatie in productie.

## Prioriteit

**HIGH** — blokkeert tenant-signup retry zonder handmatige DB cleanup. Elke productie-
failure in signup vandaag betekent ops-tussenkomst.
