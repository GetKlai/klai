---
id: SPEC-PROV-001
version: 0.1.0
status: draft
created: 2026-04-19
updated: 2026-04-19
---

# Acceptance Criteria SPEC-PROV-001

## Scenario 1 — Happy path signup (R5, R8, R12)

**Given** een nieuwe org is aangemaakt met `provisioning_status='queued'`,
**When** `provision_tenant(org_id)` wordt aangeroepen en alle externe afhankelijkheden (Zitadel, LiteLLM, MongoDB, Docker, docs-app, Caddy) beschikbaar zijn,
**Then** transitioneert `provisioning_status` in volgorde door `creating_zitadel_app → creating_litellm_team → creating_mongo_user → writing_env_file → creating_personal_kb → creating_portal_kbs → starting_container → writing_caddyfile → reloading_caddy → creating_system_groups → ready`,
**And** elke transitie produceert één `product_event` met `event_type='provisioning.state_transition'`,
**And** een SELECT op `product_events WHERE event_type='provisioning.state_transition' AND org_id=<id> ORDER BY created_at` retourneert 11 rijen in de juiste volgorde.

## Scenario 2 — Failure op stap 7 met succesvolle rollback (R9, R15)

**Given** provisioning is gestart voor een org en `_start_librechat_container` raiset `docker.errors.APIError` (bijvoorbeeld image-pull failure),
**When** de orchestrator de exception catcht,
**Then** transitioneert `provisioning_status` eerst naar `failed_rollback_pending`,
**And** worden compensators uitgevoerd voor step 6 (geen — interne DB), step 5 (docs-app DELETE, best-effort), step 4 (`shutil.rmtree(tenant_dir)`), step 3 (`_sync_drop_mongodb_tenant_user`), step 2 (`POST /team/delete` op LiteLLM), step 1 (`zitadel.delete_librechat_oidc_app`),
**And** na succesvolle afronding transitioneert `provisioning_status` naar `failed_rollback_complete`,
**And** `org.slug` is leeg (`""`),
**And** er zijn geen residuele Zitadel apps, LiteLLM teams, Mongo users, of directory trees voor deze tenant op de host.

## Scenario 3 — SIGKILL tussen step 3 en step 4, daarna retry (R6, R11)

**Given** tijdens provisioning wordt portal-api SIGKILL'd terwijl `provisioning_status='creating_mongo_user'` reeds gecommit is en step 4 (`writing_env_file`) nog niet gestart,
**When** portal-api herstart en een admin de POST `/api/orgs/{slug}/retry-provisioning` aanroept,
**Then** retourneert de endpoint `409 {"error": "not_in_retryable_state", "state": "creating_mongo_user"}` omdat de state geen `failed_rollback_complete` is,
**And** de operator voert `scripts/migrate_failed_provisioning_status.py --single-org <id> --to failed_rollback_pending` (of via cleanup-pipeline naar `failed_rollback_complete`),
**And** een tweede retry-provisioning call vanaf `failed_rollback_complete` transitioneert via `queued` → volledige forward sequence → `ready`,
**And** dit gebeurt zonder dat de operator handmatige Mongo-user cleanup heeft uitgevoerd (de compensator step die in `failed_rollback_complete` mapping al gedraaid heeft, heeft de user gedropped).

## Scenario 4 — Compensator faalt mid-rollback (R10)

**Given** provisioning faalt op step 7 (`starting_container`) en tijdens rollback raiset `_sync_drop_mongodb_tenant_user` een onverwachte `OperationFailure` (niet `UserNotFound`),
**When** de rollback-lus verder loopt,
**Then** blijven overige compensators alsnog uitgevoerd (step 2: LiteLLM delete, step 1: Zitadel delete),
**And** blijft `provisioning_status` op `failed_rollback_pending` staan,
**And** een subsequente POST `/api/orgs/{slug}/retry-provisioning` retourneert `409 {"error": "manual_cleanup_required", "state": "failed_rollback_pending"}`,
**And** de VictoriaLogs query `request_id:<uuid> AND level:error` toont één `provisioning_rollback_failed` entry met `step="creating_mongo_user"` én een `provisioning_failed` entry met de oorspronkelijke container-start exception.

## Scenario 5 — Concurrent retry race (R14)

**Given** een org in status `failed_rollback_complete`,
**When** twee admin clients binnen 100ms elk een POST `/api/orgs/{slug}/retry-provisioning` uitvoeren,
**Then** retourneert exact één van de twee requests `202 {"status": "queued"}`,
**And** retourneert de andere `409 {"error": "not_in_retryable_state", "state": "queued"}` (of een latere state),
**And** er wordt exact één `BackgroundTask` gescheduled voor `provision_tenant(org.id)`,
**And** de resulterende tenant heeft exact één set externe resources (geen dubbele Zitadel apps, geen dubbele containers).

## Scenario 6 — Docs-app onbereikbaar tijdens creating_personal_kb (R13)

**Given** provisioning is bij step 5 (`creating_personal_kb`) en docs-app is unreachable (`httpx.ConnectError`),
**When** de orchestrator de error catcht,
**Then** logt de orchestrator `logger.error("docs_kb_creation_degraded_docs_app_unreachable", slug=..., org_id=..., error=...)`,
**And** blijft `provisioning_status` doorlopen naar step 6 (`creating_portal_kbs`),
**And** eindigt de sequence normaal op `ready`,
**And** wordt **geen** rollback gestart.

## Scenario 7 — Non-2xx van bereikbare docs-app tijdens creating_personal_kb (R13)

**Given** provisioning is bij step 5 (`creating_personal_kb`) en docs-app retourneert `500 Internal Server Error`,
**When** de orchestrator de `httpx.HTTPStatusError` van `raise_for_status()` catcht,
**Then** wordt dit als harde failure behandeld,
**And** transitioneert `provisioning_status` naar `failed_rollback_pending`,
**And** worden compensators voor step 4, step 3, step 2, step 1 uitgevoerd,
**And** eindigt de status op `failed_rollback_complete`.

## Scenario 8 — Non-admin caller probeert retry (R17)

**Given** een caller met `portal_role='member'` en een org in `failed_rollback_complete`,
**When** de caller POST `/api/orgs/{slug}/retry-provisioning` aanroept,
**Then** retourneert de endpoint `403 Forbidden`,
**And** blijft `provisioning_status='failed_rollback_complete'` onveranderd,
**And** wordt er geen BackgroundTask gescheduled.

## Scenario 9 — CHECK constraint blokkeert invalid state (R4, R16)

**Given** de migratie uit M1 is gedeployed,
**When** een regressie probeert `UPDATE portal_orgs SET provisioning_status='bogus' WHERE id=1` uit te voeren,
**Then** raiset PostgreSQL een `IntegrityError` met constraint-naam `ck_portal_orgs_provisioning_status`,
**And** blijft de rij onveranderd,
**And** `alembic upgrade head` op een schone DB creëert de constraint succesvol.

## Scenario 10 — Legacy `'failed'` data migreert correct

**Given** de productiedatabase bevat orgs met `provisioning_status='failed'` en residuele Docker containers,
**When** het ops-script `migrate_failed_provisioning_status.py` dry-run wordt uitgevoerd,
**Then** bevat de CSV output per org een `recommended_new_state` kolom met:
- `failed_rollback_pending` voor orgs met nog levende containers, Zitadel apps, LiteLLM teams, of Mongo users,
- `failed_rollback_complete` voor orgs zonder residuele resources,
**And** de operator kan de CSV reviewen en corrigeren voordat `--apply` wordt uitgevoerd,
**And** na `--apply` matchen alle gemigreerde rijen aan de CHECK constraint.

## Scenario 11 — get_chat_health compatibel met tussenstaten (R18)

**Given** een org in `provisioning_status='starting_container'`,
**When** het frontend `GET /api/app/chat-health` aanroept,
**Then** retourneert het endpoint `{"healthy": false, "reason": "provisioning_in_progress"}` (de ongewijzigde bestaande waarde voor niet-`ready` states),
**And** de frontend toont de generieke "provisioning in progress" copy.

## Scenario 12 — /api/me geeft tussenstaten correct door (R19)

**Given** een user van een org in `provisioning_status='creating_mongo_user'`,
**When** de user `GET /api/me` aanroept,
**Then** retourneert de endpoint `{"provisioning_status": "creating_mongo_user", ...}` zonder error,
**And** valt de frontend terug op de generieke "provisioning in progress" copy.

## Edge cases

**EC1 — DB down midden in provisioning.** Als `transition_state` zelf faalt met een
`OperationalError`, bubblet de exception naar de outer handler. De outer handler
probeert vervolgens óók een state transitie naar `failed_rollback_pending` — die faalt
eveneens. Resultaat: status blijft op laatst-succesvolle checkpoint, structured log
bevat `failed_status_persist_error`, externe resources blijven staan. Dit is acceptabel:
na DB-herstel kan een operator via het migratiescript mappen naar
`failed_rollback_pending` en handmatig opruimen.

**EC2 — Zeer trage Caddy reload.** Als `_reload_caddy` langer dan 30s duurt (de huidige
restart timeout), raiset de Docker client een timeout. R9 triggert rollback. De Caddy-
compensator `unlink(tenant_file) + _reload_caddy` probeert een tweede reload, die kan óók
time-outen. Dan grijpt R10: volgende compensators draaien door, status blijft op
`failed_rollback_pending`.

**EC3 — Retry endpoint aangeroepen op niet-bestaande slug.** 404 Not Found, geen
state-wijziging — volg bestaande `_get_org_or_404` pattern.

**EC4 — Retry endpoint aangeroepen direct na signup (status=`pending`/`queued`).** 409
`not_in_retryable_state` — alleen `failed_rollback_complete` is retryable via deze endpoint.

**EC5 — Orchestrator start op een reeds-`ready` org.** `provision_tenant` mag niet
re-provision op een ready tenant. Guard aan het begin van `_provision`: als
`provisioning_status == 'ready'`, log waarschuwing en return zonder actie. (Deze guard
was er voorheen niet — toevoeging in M3.)

## Quality gate criteria

- [HARD] Pytest coverage voor `app/services/provisioning/state_machine.py`: **≥ 90%**.
- [HARD] Pytest coverage voor gewijzigde delen van `orchestrator.py`: **≥ 85%**.
- [HARD] Ruff check: zero new warnings op gewijzigde files.
- [HARD] Pyright: zero new errors op gewijzigde files.
- [HARD] Alembic migratie: idempotent (`alembic upgrade head && alembic downgrade -1 && alembic upgrade head` werkt).
- [HARD] Handmatige E2E op staging: signup + forced failure + retry flow slaagt.
- Integratietest suite draait op een docker-compose met echte Postgres, mock Zitadel/LiteLLM/Docker/Mongo.
- VictoriaLogs query `service:portal-api AND event:provisioning_state_transition` toont transities van een test-signup.

## Definition of Done

- [ ] SPEC goedgekeurd door backend lead + ops on-call.
- [ ] M1 migratie gedeployed naar staging, geen legacy `failed` rijen meer.
- [ ] M2 state_machine module gemerged met unit tests.
- [ ] M3 orchestrator refactor gemerged, integratietests groen.
- [ ] M4 retry endpoint gemerged, OpenAPI schema bijgewerkt.
- [ ] M5 Grafana dashboard panel voor provisioning timeline live.
- [ ] M6 runbook `provisioning-retry.md` gepubliceerd, on-call team briefed.
- [ ] Scenario 1 t/m 12 geautomatiseerd in CI.
- [ ] Een productie-failure in de eerste week na deploy wordt succesvol retryable zonder handmatige DB cleanup (observatie-acceptatie).
- [ ] Deploy workflow (`git push` → `gh run watch` → verify container age op core-01) uitgevoerd voor elke milestone.
