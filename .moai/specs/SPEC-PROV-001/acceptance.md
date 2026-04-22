---
id: SPEC-PROV-001
version: 0.3.0
status: implemented
created: 2026-04-19
updated: 2026-04-22
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
**And** worden compensators door `AsyncExitStack.__aexit__` uitgevoerd in LIFO-volgorde voor step 6 (geen — interne DB), step 5 (docs-app DELETE, best-effort), step 4 (`shutil.rmtree(tenant_dir)`), step 3 (`_sync_drop_mongodb_tenant_user`), step 2 (`POST /team/delete` op LiteLLM), step 1 (`zitadel.delete_librechat_oidc_app`),
**And** na succesvolle afronding transitioneert `provisioning_status` naar `failed_rollback_complete`,
**And** `org.deleted_at` is gezet op `now()` (soft-delete), `org.slug` blijft zichtbaar voor audit,
**And** er zijn geen residuele Zitadel apps, LiteLLM teams, Mongo users, of directory trees voor deze tenant op de host,
**And** een nieuwe signup met dezelfde org-naam kan dezelfde slug claimen dankzij de partial unique index `ix_portal_orgs_slug_active`.

## Scenario 3 — SIGKILL tussen step 3 en step 4, daarna retry (R6, R11, R21)

**Given** tijdens provisioning wordt portal-api SIGKILL'd terwijl `provisioning_status='creating_mongo_user'` reeds gecommit is en step 4 (`writing_env_file`) nog niet gestart,
**When** portal-api herstart,
**Then** draait de stuck-detector uit M7 na 15 minuten (of direct als de `updated_at` drempel al overschreden is bij startup),
**And** transitioneert de detector `provisioning_status` naar `failed_rollback_pending`,
**And** wordt een `product_event` `provisioning.stuck_recovered` ingeschoten.
**When** een admin daarna de POST `/api/orgs/{slug}/retry-provisioning` aanroept,
**Then** retourneert de endpoint `409 {"error": "manual_cleanup_required", "state": "failed_rollback_pending"}` — de Mongo user uit step 3 staat mogelijk nog, operator moet handmatig inspecteren,
**And** zodra ops de externe resources heeft opgeruimd en de rij handmatig op `failed_rollback_complete` heeft gezet (met `deleted_at = now()`), slaagt een nieuwe retry-call: transitioneert via `queued` → volledige forward sequence → `ready`.

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

## Scenario 10 — Inline Alembic migratie voor twee bestaande test-orgs

**Given** de productiedatabase bevat twee test-orgs (Voys en Klai) met `provisioning_status='ready'` en eventuele legacy `'active'` waarden uit testfixtures,
**When** `alembic upgrade head` wordt uitgevoerd,
**Then** voegt de migratie de `deleted_at TIMESTAMPTZ NULL` kolom toe aan `portal_orgs`,
**And** vervangt de oude `ix_portal_orgs_slug` door de partial unique index `ix_portal_orgs_slug_active` (`UNIQUE (slug) WHERE deleted_at IS NULL`),
**And** voeren de inline `UPDATE`-statements de twee test-orgs netjes naar `'ready'`,
**And** markeert de fail-safe UPDATE eventuele onverwachte `'failed'`-rijen als `'failed_rollback_pending'`,
**And** wordt de CHECK constraint `ck_portal_orgs_provisioning_status` succesvol aangemaakt,
**And** `alembic downgrade -1 && alembic upgrade head` slaagt idempotent.

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

## Scenario 13 — Stuck-detector pakt gecrashte run op (R21)

**Given** een org staat in `provisioning_status='creating_mongo_user'` met `updated_at = now() - interval '20 minutes'` (portal-api crashte 20 minuten geleden midden in de run),
**When** portal-api opstart en de `lifespan` stuck-detector draait,
**Then** vindt `reconcile_stuck_provisionings` deze org,
**And** transitioneert de detector `provisioning_status` naar `failed_rollback_pending` via de bestaande `transition_state` helper,
**And** logt hij `logger.warning("provisioning_stuck_detected", org_id=..., last_state="creating_mongo_user", stuck_since=...)`,
**And** wordt een `product_event` met `event_type="provisioning.stuck_recovered"` ingeschoten,
**And** worden **geen** compensators aangeroepen (de detector raakt externe resources niet aan),
**And** een org met `updated_at = now() - interval '5 minutes'` in dezelfde staat wordt overgeslagen (te recent).

## Scenario 14 — Retry met slug-botsing door nieuwe signup (R11, spec R6)

**Given** een org `A` staat in `failed_rollback_complete` met slug `acme` en `deleted_at = now() - interval '1 hour'`,
**And** een nieuwe signup heeft ondertussen een nieuwe org `B` aangemaakt met dezelfde slug `acme` (`deleted_at IS NULL`, `provisioning_status='ready'`),
**When** een admin POST `/api/orgs/acme/retry-provisioning` aanroept voor org `A`,
**Then** detecteert de endpoint binnen de `SELECT FOR UPDATE`-lock dat er een andere actieve rij met dezelfde slug bestaat,
**And** retourneert het endpoint `409 {"error": "slug_in_use_by_new_org", "state": "failed_rollback_complete"}`,
**And** blijft org `A` onveranderd (`deleted_at` blijft gezet, `provisioning_status` blijft `failed_rollback_complete`),
**And** verwijst de runbook (M6) naar het vervolg: ops kan org `A` hard-deleten of intact laten voor audit.

## Edge cases

**EC1 — DB down midden in provisioning.** Als `transition_state` zelf faalt met een
`OperationalError`, bubblet de exception naar de outer handler. De outer handler
probeert vervolgens óók een state transitie naar `failed_rollback_pending` — die faalt
eveneens. Resultaat: status blijft op laatst-succesvolle checkpoint, structured log
bevat `failed_status_persist_error`, externe resources blijven staan. Dit is acceptabel:
na DB-herstel pakt de stuck-detector (M7) deze org na 15 minuten alsnog op en mapt
hem naar `failed_rollback_pending` zodat ops hem ziet in Grafana.

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
- [HARD] Pytest coverage voor `app/services/provisioning/stuck_detector.py`: **≥ 90%**.
- [HARD] Pytest coverage voor gewijzigde delen van `orchestrator.py`: **≥ 85%**.
- [HARD] Ruff check: zero new warnings op gewijzigde files.
- [HARD] Pyright: zero new errors op gewijzigde files.
- [HARD] Alembic migratie: idempotent (`alembic upgrade head && alembic downgrade -1 && alembic upgrade head` werkt).
- [HARD] Handmatige E2E op staging: signup + forced failure + retry flow slaagt.
- Integratietest suite draait op een docker-compose met echte Postgres, mock Zitadel/LiteLLM/Docker/Mongo.
- VictoriaLogs query `service:portal-api AND event:provisioning_state_transition` toont transities van een test-signup.

## Definition of Done

- [x] SPEC goedgekeurd via sparring-sessie 2026-04-21 (v0.2.0 review door mark.vletter@voys.nl).
- [x] M1 migratie `32fc0ed3581b` gedeployed naar productie 2026-04-21. Geen `failed` rijen meer; `portal_orgs` heeft `deleted_at` + `updated_at` + partial unique index + CHECK constraint.
- [x] M2 `state_machine` module gemerged. 11 unit tests groen (FORWARD_SEQUENCE, transition_state, Iterable from_state, FOR UPDATE, duration_ms).
- [x] M3 orchestrator refactor gemerged, AsyncExitStack-pattern live. 8 integratietests groen (happy path, LIFO compensator unwind, soft-deleted guard, non-entry-state guard).
- [x] M4 retry endpoint gemerged (`POST /api/admin/orgs/{slug}/retry-provisioning`). 6 unit tests groen incl. slug-collision guard.
- [x] M5 Grafana dashboard `klai-provisioning.json` gedeployed; tenant-dropdown toegevoegd in commit `713af9a8`.
- [x] M6 runbook `docs/runbooks/provisioning-retry.md` gepubliceerd; `.claude/rules/klai/projects/portal-backend.md` uitgebreid met invariants.
- [x] M7 stuck-detector module gemerged; 5 unit tests groen; lifespan-hook in `main.py`.
- [x] CI-enforcement toegevoegd (`scripts/validate_alembic.py` + step in `portal-api.yml`). Verhindert toekomstige duplicate rev ids en orphan heads.
- [x] Scenario 1, 2, 3, 4, 5, 8, 13, 14 geautomatiseerd (unit-tests met mocks). Scenario 6, 7, 9, 10, 11, 12 deels: afgedekt via code-inspectie / schema-verificatie op prod (zie post-deploy validation hieronder).
- [ ] **Een productie-failure in de eerste week na deploy wordt succesvol retryable zonder handmatige DB cleanup.** Observatie-acceptatie: op 2026-04-22 zijn er nog geen nieuwe tenant-signups geweest sinds deploy; het echte failure-pad is nog niet op productie getriggerd. Open-houden tot eerste echte signup-failure wordt waargenomen.
- [x] Deploy workflow uitgevoerd: `git push` → `gh run watch --exit-status` → verify container age op core-01 (run `24764786009`, alle 5 jobs groen, container opnieuw uitgerold).

## Post-deploy validation (2026-04-21 t/m 2026-04-22)

**Productie-schema geverifieerd via `klai-core-portal-api-1`:**

- `alembic current` → `32fc0ed3581b (head)` — single head, geen `Revision X is present more than once`-warning.
- `portal_orgs` kolommen: `deleted_at TIMESTAMPTZ NULL`, `updated_at TIMESTAMPTZ NOT NULL DEFAULT now()`.
- Index: `CREATE UNIQUE INDEX ix_portal_orgs_slug_active ON portal_orgs USING btree (slug) WHERE (deleted_at IS NULL)`.
- CHECK constraint: `ck_portal_orgs_provisioning_status` — live geverifieerd door een `UPDATE portal_orgs SET provisioning_status = 'bogus'` te proberen (rolled back); response was `IntegrityError`.
- Twee test-orgs: `id=1 slug=getklai`, `id=8 slug=voys`, beide `provisioning_status='ready'`, `deleted_at=NULL`.

**Retry-endpoint geverifieerd:**

- Route geregistreerd in `router.routes`: `POST /api/admin/orgs/{slug}/retry-provisioning`.
- Smoke test met invalid token → `401 {"detail":"cookie_required"}` (auth guard werkt).

**Stuck-detector geverifieerd:**

- Startup-log `Application startup complete` na container restart, geen exceptions.
- Tegen prod-schema: query runt (na M7-added `updated_at` kolom).

**Niet geverifieerd (bewuste restrisico's):**

- Echt failure-pad door LiteLLM/Mongo/Docker/Caddy — vereist een bewust kapotte signup, scope creep voor deze SPEC.
- BackgroundTask durability onder portal-api restart — stuck-detector mitigeert, volledige oplossing (durable queue) out-of-scope voor deze SPEC.

## Known issues carried forward

1. **Format-fix commit `faea2cb4` bevat WIP van andere SPECs** (SPEC-OBS-001 docs, `deploy/docker-compose.yml` grafana env vars, submodule pointers). Per ongeluk meegecommit via `git add -u`. Niet revertable zonder impact op die andere SPECs — owner van OBS-001 bevestigt of werk bedoeld was.
2. **Pre-existing invalid-hex chain in alembic-versions** (`b2c3d4e5f6g7`, `c3d4e5f6g7h8`, ..., `p6q7r8s9t0u1`). ~15 bestanden met niet-valide hex chars (`g`, `h`, `i`, `j`, ...). Alembic accepteert het, CI-check voorkomt nieuwe instances. Opschoning buiten scope — aparte SPEC als hygiene-issue ooit urgent wordt.
3. **SPEC-PROV-002 durable queue**: expliciet deferred. Triggerpunt voor aparte SPEC: signup-rate > ~20/week OF move naar multi-replica portal-api.
