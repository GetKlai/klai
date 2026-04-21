---
id: SPEC-PROV-001
version: 0.2.0
status: draft
created: 2026-04-19
updated: 2026-04-21
---

# Implementatieplan SPEC-PROV-001

## Overzicht

Zeven milestones, priority-based. Elk milestone is onafhankelijk testbaar en kan apart
gemerged worden. M1 en M2 zijn blokkerend voor M3. M3 is blokkerend voor M4, M5 en M7.
M6 staat los en kan eerder.

## Milestones

### M1 — Database schema + soft-delete + CHECK constraint (Priority High)

**Doel:** `portal_orgs` krijgt soft-delete ondersteuning, `provisioning_status` accepteert
de volledige set waarden uit SPEC R8, en de twee bestaande test-orgs (Voys en Klai) zijn
gemigreerd naar de nieuwe state machine.

**Taken:**

1. Nieuwe Alembic revision `add_provisioning_state_machine_constraint`. Eén revision
   die het volgende in deze volgorde doet (zie spec.md migratieplan §1 voor SQL):
   - Kolom `deleted_at TIMESTAMPTZ NULL` toevoegen aan `portal_orgs`.
   - Bestaande unique index `ix_portal_orgs_slug` droppen.
   - Partial unique index `ix_portal_orgs_slug_active` aanmaken (`UNIQUE (slug) WHERE deleted_at IS NULL`).
   - Inline `UPDATE portal_orgs ...` voor de twee test-orgs (Voys + Klai) volgens spec §1b.
   - Fail-safe `UPDATE` voor eventuele onverwachte `'failed'` rijen → `failed_rollback_pending`.
   - CHECK constraint `ck_portal_orgs_provisioning_status`.
2. Model-update in `klai-portal/backend/app/models/portal_org.py`:
   - Voeg `deleted_at: Mapped[datetime | None]` kolom toe.
   - SQLAlchemy event listener of `Query.filter(PortalOrg.deleted_at.is_(None))` in alle
     default org-lookups (via een shared `_active_orgs()` helper om shotgun-wijzigingen te
     voorkomen).
3. Testfixture `tests/test_me_org_found.py:16`: wijzig `"active"` → `"ready"`.
4. Pre-deploy check: operator voert `SELECT id, slug, provisioning_status FROM portal_orgs`
   uit op productie om te verifiëren dat de twee UPDATE-statements de lading dekken (zie
   spec §1b). Als er onverwachte rijen staan, revision aanpassen vóór deploy.

**Verificatie:**
- `alembic upgrade head && alembic downgrade -1 && alembic upgrade head` slaagt idempotent.
- `psql -c "INSERT INTO portal_orgs (...) VALUES (..., provisioning_status='foo')"` wordt afgewezen.
- Twee rijen met slug `voys` (één `deleted_at=NULL`, één `deleted_at=now()`) zijn toegestaan.
- Twee rijen met slug `voys` beide `deleted_at=NULL` worden geweigerd door de partial unique index.

**Afhankelijkheden:** Geen.

### M2 — State machine helper (Priority High)

**Doel:** Eén reusable helper die `SELECT ... FOR UPDATE` + state transitie + structured
log + product_event emit combineert.

**Taken:**

1. Nieuwe module `klai-portal/backend/app/services/provisioning/state_machine.py`:
   - Functie `async def transition_state(db, org_id, from_state, to_state, step) -> None`:
     - `SELECT * FROM portal_orgs WHERE id = :org_id FOR UPDATE`
     - Verifieert `org.provisioning_status == from_state` (of `from_state is None` voor de eerste transitie, die ook `pending` en `queued` accepteert).
     - Meet `duration_ms` sinds de forward-step-start (bijgehouden in `_ProvisionState`).
     - Schrijft nieuwe status + commit.
     - Emit `logger.info("provisioning_state_transition", ...)`.
     - Insert `product_event` met `event_type="provisioning.state_transition"` en `properties={"from_state", "to_state", "step", "duration_ms"}`.
   - Constant `FORWARD_SEQUENCE: list[tuple[str, str]]` met `(step_name, next_state)` tuples in volgorde van R8.
   - Constant `COMPENSATORS: dict[str, Callable]` — mapping van state-naam naar compensator callable.
2. Unit tests `tests/test_provisioning_state_machine.py`:
   - Happy path: `queued → creating_zitadel_app` slaagt en commit de nieuwe status.
   - Concurrent write: twee coroutines met `FOR UPDATE` serialiseren correct (fake-pg of integratietest).
   - Mismatch: als `org.provisioning_status` niet gelijk is aan `from_state`, raise `StateTransitionConflict`.

**Verificatie:**
- Unit tests slagen.
- `product_event` records worden aangemaakt (integratietest met test-DB).

**Afhankelijkheden:** M1 (CHECK constraint moet live zijn).

### M3 — Orchestrator refactor naar state machine (Priority High)

**Doel:** `_provision` gebruikt `transition_state` voor elke forward step en heeft
failure handler zoals beschreven in R9 en R10.

**Taken:**

1. `orchestrator.py`:
   - Vervang de huidige `_ProvisionState` dataclass door een kleinere variant die alleen `slug`, `start_times_per_step`, en de externe resource IDs bevat (zitadel_app_id, litellm_team_id, etc.). Deze blijft in-memory leven als lookup-context voor compensators.
   - Gebruik `contextlib.AsyncExitStack` als rollback-mechaniek (Python stdlib, al gebruikt in `main.py` en `database.py` → consistent met projectstandaard). Patroon:
     ```python
     async with AsyncExitStack() as stack:
         await transition_state(db, org_id, from_state=None, to_state="queued", step="begin")
         for step_name, next_state in FORWARD_SEQUENCE:
             await transition_state(db, org_id, from_state=<prev>, to_state=next_state, step=step_name)
             result = await <forward_step>(state, ...)
             stack.push_async_callback(<compensator>, state, result)
         await transition_state(db, org_id, from_state=<last>, to_state="ready", step="ready")
         stack.pop_all()  # happy path: consume stack zonder compensators te draaien
     ```
     Bij een exception draait `AsyncExitStack.__aexit__` automatisch alle geregistreerde compensators in LIFO-volgorde. Dit vervangt de handgeschreven `_rollback(state)` loop.
   - Enkele checkpoint per stap ("step X started") — conform sparring-beslissing 6. Onze compensators zijn idempotent; een overbodige compensator-aanroep op een halfgelande stap is goedkoop.
   - Vervang `except Exception` block:
     - `await transition_state(db, org_id, from_state=<last>, to_state="failed_rollback_pending", step="rollback_start")` — vóórdat `AsyncExitStack.__aexit__` de compensators draait.
     - Na succesvolle stack-unwind: `org.deleted_at = func.now()` + `to_state="failed_rollback_complete"` in één `SELECT FOR UPDATE`-transitie.
     - Als één of meer compensators faalden (gevangen door een outer try/except rond de `async with` scope): status blijft op `failed_rollback_pending`, log aggregatie.
2. Verwijder de oude `_rollback` functie en het handmatige inverse-call-patroon — vervangen door `AsyncExitStack`-registraties naast elke forward step.
3. Guard aan het begin van `_provision`: als `provisioning_status == 'ready'`, log `provisioning_skipped_already_ready` en return zonder actie (zie acceptance EC5).
4. Integratietest `tests/test_provisioning_orchestrator.py`:
   - Mock Zitadel + LiteLLM + Docker + Mongo clients.
   - Happy path: org gaat door alle states en eindigt op `ready`.
   - Failure op stap 3 (`creating_mongo_user`): state gaat naar `failed_rollback_pending`, compensators voor step 2 en 1 lopen via de `AsyncExitStack`, daarna `failed_rollback_complete` en `org.deleted_at` is gezet.
   - Failure op stap 3 + compensator failure op stap 1: state blijft `failed_rollback_pending`, log bevat aggregatie, `deleted_at` is niet gezet.
   - Retry na `failed_rollback_complete`: een nieuwe signup met dezelfde slug slaagt (partial unique index laat dit toe).

**Verificatie:**
- Bestaande tests in `tests/test_provisioning*.py` slagen onaangepast of met minimal wijzigingen.
- Nieuwe integratietests slagen.
- Handmatige E2E: signup een nieuwe tenant op staging, force-kill portal-api na step 3, observeer dat status op `creating_mongo_user` blijft (geen rollback, proces dood). Retry endpoint (M4) lost dit op.

**Afhankelijkheden:** M1, M2.

### M4 — Retry endpoint (Priority High)

**Doel:** Admin kan via HTTP een org in `failed_rollback_complete` retryen.

**Taken:**

1. Nieuwe route `klai-portal/backend/app/api/orgs_retry_provisioning.py`:
   - `POST /api/orgs/{slug}/retry-provisioning`
   - Dependency: `_get_caller_org` + admin-role check via bestaande `require_admin` helper (zie `portal-security.md`).
   - Lookup op `org.id` i.p.v. `slug` om de soft-deleted rij specifiek te kunnen pakken — path-param `slug` wordt gebruikt om de failed rij te vinden via een lookup die bewust óók soft-deleted rijen toont (`Query(PortalOrg).filter(PortalOrg.slug == slug)` zónder `deleted_at IS NULL` filter; indien meerdere rijen: pak de rij met `provisioning_status = 'failed_rollback_complete'`).
   - `SELECT ... FOR UPDATE` op die rij.
   - Guard (binnen lock):
     - Status `failed_rollback_complete`:
       - Check of er een andere actieve rij met dezelfde slug bestaat (`deleted_at IS NULL` + andere id) — zo ja → 409 `{"error": "slug_in_use_by_new_org"}` (zie spec R6).
       - Anders: transitioneer naar `queued`, zet `deleted_at = NULL`, schedule BackgroundTask `provision_tenant(org.id)`, retourneer `202 {"status": "queued"}`.
     - Status `failed_rollback_pending` → retourneer `409 {"error": "manual_cleanup_required"}`.
     - Elke andere status (`ready`, `queued`, `creating_*`) → retourneer `409 {"error": "not_in_retryable_state", "state": <current>}`.
2. Registreer route in `main.py` (of centrale router).
3. Testfile `tests/test_retry_provisioning.py`:
   - 202 op `failed_rollback_complete` (+ BackgroundTask wordt gescheduled, verifieerbaar via mock).
   - 409 op `failed_rollback_pending`.
   - 409 op `ready`.
   - 403 als caller geen admin is.
   - Concurrent retry: twee requests tegelijk, één krijgt 202, de tweede 409 "not_in_retryable_state" omdat FOR UPDATE de eerste laat committen op `queued` vóór de tweede binnenkomt.

**Verificatie:**
- Tests slagen.
- Handmatige E2E: maak een org `failed_rollback_complete` (script of DB-edit op staging), POST `retry-provisioning`, observeer dat status door state machine naar `ready` gaat.

**Afhankelijkheden:** M1, M2, M3.

### M5 — Observability (Priority Medium)

**Doel:** Operators kunnen per-tenant provisioning timeline in Grafana zien.

**Taken:**

1. `product_event` inserts (reeds in M2): verifieer dat alle transities in de Grafana PostgreSQL datasource zichtbaar zijn.
2. Grafana dashboard: query template voor "provisioning timeline voor org_id = X":
   ```sql
   SELECT created_at, event_type, properties->>'from_state' AS from_state,
          properties->>'to_state' AS to_state, properties->>'step' AS step,
          properties->>'duration_ms' AS duration_ms
   FROM product_events
   WHERE event_type = 'provisioning.state_transition' AND org_id = :org_id
   ORDER BY created_at ASC
   ```
3. VictoriaLogs alert-rule (optioneel): fire als meer dan N orgs in 24h eindigen op `failed_rollback_pending`.

**Verificatie:**
- Dashboard toont timeline voor een test-tenant signup op staging.
- Alert fired correct in een gecontroleerde failure test.

**Afhankelijkheden:** M2 (product_event emit).

### M6 — Documentation + rules update (Priority Medium)

**Doel:** Ops-runbook en klai-rules reflecteren het nieuwe gedrag.

**Taken:**

1. Nieuwe rule `klai-portal/docs/runbooks/provisioning-retry.md`:
   - Wanneer gebruik je retry endpoint.
   - Wat te doen bij `failed_rollback_pending` (handmatige inspectie van Zitadel/LiteLLM/Mongo/Docker/Caddy).
   - Wat te doen bij `slug_in_use_by_new_org` (409 bij retry — soft-deleted rij is overbodig geworden, kan hard-deleted).
   - Wat te doen na portal-api deploy: stuck-detector heeft gedraaid, controleer Grafana op `provisioning.stuck_recovered` events.
   - Verwijzing naar deprovisioning script voor manual cleanup.
2. Update `.claude/rules/klai/projects/portal-backend.md`:
   - Nieuwe sectie "Provisioning state machine" met verwijzing naar SPEC-PROV-001.
3. Update `klai-portal/CLAUDE.md`:
   - Link naar runbook onder "Key rules".

**Verificatie:** Runbook review door on-call ops.

**Afhankelijkheden:** M3, M4, M7.

### M7 — Startup stuck-detector (Priority Medium)

**Doel:** Provisionings die vastzaten door een portal-api crash of deploy worden bij
startup automatisch naar `failed_rollback_pending` gemapt zodat ze zichtbaar worden in
Grafana en ops ze kan oppakken. Zie SPEC R21 en risico R7.

**Taken:**

1. Nieuwe module `klai-portal/backend/app/services/provisioning/stuck_detector.py`:
   - Functie `async def reconcile_stuck_provisionings(db: AsyncSession) -> int` die alle
     orgs vindt met een niet-terminale status en `updated_at < now() - interval '15 minutes'`.
   - Per gevonden org: log `provisioning_stuck_detected`, transitioneer naar
     `failed_rollback_pending` via de bestaande `transition_state` helper (M2), emit een
     `product_event` `provisioning.stuck_recovered`.
   - Géén compensators aanroepen — de in-memory `AsyncExitStack` is weg, compensatie is
     niet veilig zonder garantie dat de resources nog bestaan zoals de `_ProvisionState`
     dacht.
2. Haak in op FastAPI `lifespan` startup in `app/main.py`:
   - Na database connectie ready, vóór request serving begint.
   - Failure in reconcile mag app-startup niet blokkeren — try/except met warning log.
3. Unit tests `tests/test_stuck_detector.py`:
   - Org in `creating_mongo_user` met `updated_at > now() - 15min` → overgeslagen.
   - Org in `creating_mongo_user` met `updated_at < now() - 15min` → gemapt naar `failed_rollback_pending`.
   - Org in `ready` → overgeslagen, ongeacht leeftijd.
   - Org in `failed_rollback_pending` of `failed_rollback_complete` → overgeslagen (al terminaal).

**Verificatie:**
- Unit tests slagen.
- Staging test: stop portal-api midden in een provisioning run, wacht 15 minuten,
  start portal-api, verifieer dat de org in Grafana als `failed_rollback_pending`
  verschijnt met een `provisioning.stuck_recovered` event.

**Afhankelijkheden:** M1, M2, M3.

## Technische aanpak

### DB transactie strategie

- Eén `AsyncSession` per `provision_tenant` aanroep (reeds zo via `AsyncSessionLocal`).
- `pin_session(db)` blijft noodzakelijk voor RLS context (al aanwezig).
- Elke state transitie = aparte BEGIN/COMMIT cyclus. Dit betekent dat een single step
  niet atomair is met zijn state-checkpoint, maar dat is bewust: we willen dat elke
  succesvolle step direct zichtbaar is zodat een crash precies weet waar hij was.
- SELECT FOR UPDATE voorkomt dat een concurrent retry of signup dezelfde rij leest.

### Compensator volgorde

Reverse van forward. Voor step N die faalde: compensators [N-1, N-2, ..., 1] in die volgorde. Compensator voor N zelf wordt niet uitgevoerd (step faalde, side-effects zijn ofwel niet geland ofwel de step is verantwoordelijk voor eigen cleanup — zie `_start_librechat_container` dat een bestaande container met dezelfde naam al removet).

### Idempotentie van compensators

Alle compensators zijn vandaag al idempotent (SEC-021 refactor):
- `_sync_drop_mongodb_tenant_user`: wallow `UserNotFound` (code 11).
- `_sync_remove_container`: wallow `docker.errors.NotFound`.
- `tenant_file.unlink(missing_ok=True)`: idempotent by flag.
- `shutil.rmtree(..., ignore_errors=True)`: idempotent by flag.
- `zitadel.delete_librechat_oidc_app`: wallow 404 (verifieer in Zitadel client).
- `POST /team/delete` op LiteLLM: wallow 404 (verifieer in handmatige test).

Extra werk: waar wallow nog niet op plek is, toevoegen in M3.

### Retry na rollback

Na `failed_rollback_complete` is `org.deleted_at` gezet (soft-deleted). De partial unique
index `ix_portal_orgs_slug_active` geeft de slug automatisch vrij voor nieuwe signups of
voor retry op dezelfde rij. Twee paden:

1. **User retry via nieuwe signup:** gebruiker meldt zich opnieuw aan. Signup-flow maakt
   een nieuwe `portal_orgs` rij aan, `_slugify_unique` kan dezelfde slug toewijzen
   (partial index blokkeert niet want de oude rij is soft-deleted). Nieuwe `provision_tenant`
   aanroep, nieuwe run.
2. **Admin retry via endpoint:** endpoint zet `deleted_at = NULL` + `provisioning_status = 'queued'`
   op de bestaande rij en herstart de provisioning op dezelfde rij. Vereist dat er op dat
   moment geen andere actieve rij met dezelfde slug is (guard in M4 endpoint).

In beide gevallen is de slug "vrij" zonder dat we `slug = ""` hoeven te zetten — het
soft-delete + partial unique index patroon (Linear/Notion/GitLab standard) doet het werk.

## Risico's (implementation-level)

- **State transitie gefaald op CHECK constraint tijdens rollback.** Als een
  regressiedefect een verkeerde state-waarde probeert te schrijven tijdens rollback,
  raiset de DB `IntegrityError` en blijft de org op de laatst-succesvolle state. Dit
  is strict genoeg — liever een gefaalde rollback dan een ongeldige state in de DB.
- **BackgroundTask vs explicit worker.** De huidige `provision_tenant` loopt in FastAPI
  BackgroundTasks. Bij portal-api restart tijdens provisioning gaat de BackgroundTask
  verloren. State blijft op de laatst-geschreven tussenstate (bv. `creating_mongo_user`).
  De stuck-detector uit M7 maakt deze orgs automatisch zichtbaar als `failed_rollback_pending`
  na 15 minuten. Ops kan ze vervolgens handmatig mappen naar `failed_rollback_complete`
  (na resource-inspectie) waarna retry via M4 mogelijk is. Volwaardige oplossing
  (durable task queue) blijft out-of-scope.
- **Stuck-detector misidentificeert lopende provisioning.** Mitigatie: `updated_at`
  drempel van 15 minuten is ruim meer dan een gezonde provisioning-run. Op single-node
  single-process voldoende. Bij horizontale scaling later vereist een advisory lock.
- **Soft-delete + referentiële integriteit.** Tabellen die naar `portal_orgs.id` verwijzen
  (bv. `portal_memberships`, `product_events`) moeten blijven werken na soft-delete.
  Verwachte gedrag: de FK's blijven geldig (soft-delete wijzigt geen id), alleen queries
  die "actieve orgs" bedoelen moeten via de `_active_orgs()` helper. Audit tijdens M1/M3
  dat er geen queries zijn die stilletjes soft-deleted rijen mee-filteren.

## Verificatie checklist

Per milestone:

- M1: CHECK constraint werkt, migratie idempotent, partial unique index gedraagt correct, twee test-orgs gemigreerd, tests groen.
- M2: state_machine.py unit tests groen, product_events verschijnen in test-DB.
- M3: orchestrator integratietests groen, `AsyncExitStack` rollback werkt, happy + failure paden bewezen.
- M4: retry endpoint tests groen, admin-only afgedwongen, concurrent race test groen, slug_in_use_by_new_org edge case getest.
- M5: dashboard query werkt, alert rule configured (optional).
- M6: runbook door ops goedgekeurd.
- M7: stuck-detector unit tests groen, staging test (stop portal-api mid-run → start → zie `failed_rollback_pending`) succesvol.

## Co-auteurs en reviewers

- Implementatie: backend team.
- SPEC review: security + ops (CHECK constraint semantiek, migratie plan).
- Acceptance verification: QA + on-call ops (E2E retry flow op staging).
