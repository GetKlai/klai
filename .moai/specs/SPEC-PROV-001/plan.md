---
id: SPEC-PROV-001
version: 0.1.0
status: draft
created: 2026-04-19
updated: 2026-04-19
---

# Implementatieplan SPEC-PROV-001

## Overzicht

Zes milestones, priority-based. Elk milestone is onafhankelijk testbaar en kan apart
gemerged worden. M1 en M2 zijn blokkerend voor M3. M3 is blokkerend voor M4 en M5. M6
staat los en kan eerder.

## Milestones

### M1 — Database schema + CHECK constraint (Priority High)

**Doel:** `provisioning_status` accepteert de volledige set waarden uit SPEC R8. Legacy
data is gemigreerd.

**Taken:**

1. Nieuwe Alembic revision `add_provisioning_state_machine_constraint`:
   - Voeg CHECK constraint toe (zie spec.md migratieplan §1).
   - Geen default-wijziging — bestaande rijen blijven op hun huidige waarde.
2. Ops-script `klai-portal/backend/scripts/migrate_failed_provisioning_status.py`:
   - Leest alle orgs met `provisioning_status = 'failed'`.
   - Per org: probeer Zitadel app op te halen, LiteLLM team, Mongo user, Docker container, caddyfile op disk.
   - Output CSV met kolommen `org_id, slug, zitadel_present, litellm_present, mongo_present, docker_present, caddy_present, recommended_new_state`.
   - Operator valideert CSV handmatig, dan tweede script-run met `--apply` voert de UPDATE uit.
3. Testfixture `tests/test_me_org_found.py:16`: wijzig `"active"` → `"ready"`.
4. Portal-api Dockerfile: overweeg `COPY scripts/ scripts/` toe te voegen zodat het migratiescript runbaar is in de container (zie `portal-backend.md` notitie over scripts-dir).

**Verificatie:**
- `alembic upgrade head` slaagt op een database met demo data.
- `psql -c "INSERT INTO portal_orgs (...) VALUES (..., provisioning_status='foo')"` wordt afgewezen.
- Bestaande orgs met `provisioning_status = 'ready'` of `'pending'` blijven onveranderd.

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
   - Vervang de huidige `_ProvisionState` dataclass door een kleinere variant die alleen `slug`, `start_times_per_step`, en de externe resource IDs bevat (zitadel_app_id, litellm_team_id, etc.). Deze blijft in-memory leven voor de rollback.
   - Wrap elke forward step in:
     ```
     await transition_state(db, org_id, from_state=<prev>, to_state=<step_state>, step=<step>)
     <execute step>
     ```
   - Tweede status-write bij step-succes naar de volgende state gebeurt aan het begin van de volgende step (dus elk checkpoint markeert "step X started"). Alternatief overwegen: dubbele write per step (started + completed) — keuze in detail-design tijdens implementatie.
   - Vervang `except Exception` block:
     - `await transition_state(db, org_id, from_state=<last>, to_state="failed_rollback_pending", step="rollback_start")`
     - Run compensators in omgekeerde volgorde van de bereikte step, elk in eigen try/except zoals huidige `_rollback`.
     - Als alle compensators slaagden: `org.slug = ""`, `to_state="failed_rollback_complete"`.
     - Als één of meer compensators faalden: laat status op `failed_rollback_pending`, log aggregatie.
2. Verwijder de oude `_rollback` functie — vervangen door nieuwe inline-logica of `_run_compensators(state, up_to_step)` helper.
3. Integratietest `tests/test_provisioning_orchestrator.py`:
   - Mock Zitadel + LiteLLM + Docker + Mongo clients.
   - Happy path: org gaat door alle states en eindigt op `ready`.
   - Failure op stap 3 (`creating_mongo_user`): state gaat naar `failed_rollback_pending`, compensators voor step 2 en 1 lopen, daarna `failed_rollback_complete` en `org.slug = ""`.
   - Failure op stap 3 + compensator failure op stap 1: state blijft `failed_rollback_pending`, log bevat aggregatie.

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
   - `SELECT ... FOR UPDATE` op de org (via `_get_org_or_404_for_update(slug, db)` helper, volg bestaande org-scoping pattern uit `portal-security.md`).
   - Guard:
     - Status `failed_rollback_complete` → transitioneer naar `queued`, schedule BackgroundTask `provision_tenant(org.id)`, retourneer `202 {"status": "queued"}`.
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
   - Wat te doen bij `failed_rollback_pending`.
   - Verwijzing naar deprovisioning script voor manual cleanup.
2. Update `.claude/rules/klai/projects/portal-backend.md`:
   - Nieuwe sectie "Provisioning state machine" met verwijzing naar SPEC-PROV-001.
3. Update `klai-portal/CLAUDE.md`:
   - Link naar runbook onder "Key rules".

**Verificatie:** Runbook review door on-call ops.

**Afhankelijkheden:** M3, M4.

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

Na `failed_rollback_complete` is `org.slug = ""`. De retry endpoint schedulet
`provision_tenant(org_id)` opnieuw. De orchestrator regenereert het slug via
`_slugify_unique(org.name, existing_slugs)` — zelfde pad als eerste provisioning. Het
slug mag hetzelfde zijn als voorheen mits `_sync_drop_mongodb_tenant_user` en
`_sync_remove_container` slaagden tijdens rollback (resources zijn weg → slug is vrij).

## Risico's (implementation-level)

- **Migratiescript veronderstelt dat alle externe resources bereikbaar zijn.** Als
  LiteLLM of Zitadel down is tijdens `migrate_failed_provisioning_status.py --dry-run`,
  staat de CSV output niet vast. Mitigatie: script retried per-org 3 keer, logt
  "inconclusive" in de CSV, operator beslist handmatig.
- **State transitie gefaald op CHECK constraint tijdens rollback.** Als een
  regressiedefect een verkeerde state-waarde probeert te schrijven tijdens rollback,
  raiset de DB `IntegrityError` en blijft de org op de laatst-succesvolle state. Dit
  is strict genoeg — liever een gefaalde rollback dan een ongeldige state in de DB.
- **BackgroundTask vs explicit worker.** De huidige `provision_tenant` loopt in FastAPI
  BackgroundTasks. Bij portal-api restart tijdens provisioning gaat de BackgroundTask
  verloren. State blijft op de laatst-geschreven tussenstate (bv. `creating_mongo_user`).
  Retry endpoint in M4 accepteert alleen `failed_rollback_complete` — een stuck
  tussenstate is dus niet retryable via de HTTP endpoint. Mitigatie: operator kan via
  de migratiescript (M1) een stuck tussenstate mappen naar `failed_rollback_complete`
  na handmatige inspectie. Volwaardige oplossing (durable task queue) is out-of-scope.

## Verificatie checklist

Per milestone:

- M1: CHECK constraint werkt, migratie idempotent, tests groen.
- M2: state_machine.py unit tests groen, product_events verschijnen in test-DB.
- M3: orchestrator integratietests groen, happy + failure paden bewezen.
- M4: retry endpoint tests groen, admin-only afgedwongen, concurrent race test groen.
- M5: dashboard query werkt, alert rule configured (optional).
- M6: runbook door ops goedgekeurd.

## Co-auteurs en reviewers

- Implementatie: backend team.
- SPEC review: security + ops (CHECK constraint semantiek, migratie plan).
- Acceptance verification: QA + on-call ops (E2E retry flow op staging).
