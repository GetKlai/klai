---
id: SPEC-CHAT-TEMPLATES-001
version: 0.1.0
status: draft
created: 2026-04-23
updated: 2026-04-23
author: Mark Vletter
priority: high
issue_number: 0
---

# Acceptance — SPEC-CHAT-TEMPLATES-001

## Test Area 1 — Data Model & Persistence

### AC-MODEL-01 — `(org_id, slug)` is UNIQUE

**Given** een nieuwe tenant met `org_id=42` en een bestaande template met slug `klantenservice`
**When** een tweede `INSERT INTO portal_templates (org_id=42, slug='klantenservice', ...)` wordt uitgevoerd
**Then** de database raises een UNIQUE constraint violation op `uq_portal_template_org_slug`

### AC-MODEL-02 — CHECK constraint `char_length(prompt_text) <= 8000`

**Given** de tabel `portal_templates` is gemigreerd
**When** een directe SQL-insert wordt uitgevoerd met `prompt_text` van 8001 karakters
**Then** de database raises een CHECK constraint violation op `ck_portal_template_prompt_len`

### AC-MODEL-03 — RLS strict returnt 0 rows zonder tenant-context

**Given** minstens één rij bestaat in `portal_templates` voor `org_id=42`
**When** een nieuwe database-sessie wordt geopend zonder `SET app.current_org_id = '42'` uit te voeren
**And** de sessie als niet-superuser rol verbinding maakt
**When** `SELECT * FROM portal_templates` wordt uitgevoerd
**Then** het resultaat is 0 rows (niet de 1+ rijen die superuser zou zien)

### AC-MODEL-04 — `scope` CHECK weigert onbekende waarden

**Given** de tabel is gemigreerd
**When** een directe SQL-insert wordt uitgevoerd met `scope='experimental'`
**Then** de database raises een CHECK constraint violation op `ck_portal_template_scope`

## Test Area 2 — CRUD & Authorization

### AC-CRUD-01 — Admin mag scope="org" aanmaken (happy path)

**Given** een authenticated user met rol `"admin"` in org `42`
**When** de user `POST /api/app/templates` uitvoert met body `{"name": "Zakelijk", "prompt_text": "Schrijf zakelijk.", "scope": "org"}`
**Then** de response is HTTP 201 met body bevattend `"slug": "zakelijk"` en `"scope": "org"`
**And** een nieuwe row bestaat in `portal_templates` met `org_id=42, created_by=<zitadel_user_id_of_admin>`
**And** Redis keys matching `templates:42:*` zijn verwijderd binnen 100 ms

### AC-CRUD-02 — Non-admin krijgt 403 op scope="org" create

**Given** een authenticated user met rol `"member"` in org `42`
**When** de user `POST /api/app/templates` uitvoert met `scope="org"`
**Then** de response is HTTP 403 met message `"Alleen beheerders mogen organisatie-templates aanmaken"`
**And** er wordt geen row in `portal_templates` aangemaakt

### AC-CRUD-03 — Elke user mag scope="personal" aanmaken

**Given** een authenticated user met rol `"member"` in org `42`
**When** de user `POST /api/app/templates` uitvoert met `scope="personal"`
**Then** de response is HTTP 201
**And** `created_by` van de nieuwe row is de zitadel_user_id van de caller

### AC-CRUD-04 — Non-owner PATCH → 403

**Given** template met slug `formeel` in org `42` is aangemaakt door user A (rol `"member"`)
**And** user B (rol `"member"`) is ook lid van org `42`
**When** user B `PATCH /api/app/templates/formeel` uitvoert met body `{"name": "Nieuw"}`
**Then** de response is HTTP 403

### AC-CRUD-05 — Admin kan elke template PATCHen

**Given** template met slug `formeel` in org `42` is aangemaakt door user A (rol `"member"`)
**And** user C (rol `"admin"`) is lid van org `42`
**When** user C `PATCH /api/app/templates/formeel` uitvoert met body `{"prompt_text": "Herzien."}`
**Then** de response is HTTP 200 met de bijgewerkte prompt_text

### AC-CRUD-06 — Rate-limit geeft 429 met Retry-After

**Given** de sliding-window teller voor `templates_rl:42` staat op 10 writes binnen het laatste 60s-venster
**When** een 11e write (POST/PATCH/DELETE) komt binnen hetzelfde venster
**Then** de response is HTTP 429
**And** de response header bevat `Retry-After: <positive int>` in seconden

### AC-CRUD-07 — 4 defaults na tenant provisioning

**Given** tenant-provisioning wordt gedraaid voor een nieuwe `org_id=99`
**When** de orchestrator step `defaults_templates` voltooit
**Then** `SELECT COUNT(*) FROM portal_templates WHERE org_id=99` returnt exact `4`
**And** de slugs zijn (in elke volgorde): `klantenservice, formeel, creatief, samenvatter`
**And** alle 4 hebben `scope='org'` en `created_by='system'`

### AC-CRUD-08 — Lazy-seed in list-endpoint voor bestaande orgs zonder templates

**Given** een bestaande org `88` heeft 0 rijen in `portal_templates` (pre-dating SPEC-CHAT-TEMPLATES-001)
**When** de eerste user in die org `GET /api/app/templates` aanroept
**Then** de response bevat 4 templates (`klantenservice, formeel, creatief, samenvatter`)
**And** `SELECT COUNT(*) FROM portal_templates WHERE org_id=88` returnt `4`

### AC-CRUD-09 — Personal template alleen voor creator zichtbaar

**Given** user A (rol `"member"`, org `42`) heeft template `mijn-stijl` aangemaakt met `scope="personal"`
**And** user B (rol `"member"`, org `42`) is lid van dezelfde org
**When** user B `GET /api/app/templates` aanroept
**Then** de response bevat `mijn-stijl` NIET
**When** user A (creator) `GET /api/app/templates` aanroept
**Then** de response bevat `mijn-stijl` WEL

### AC-CRUD-10 — Admin ziet alle personal templates in zijn org

**Given** user A (member, org 42) heeft `mijn-stijl` als `scope="personal"`
**And** user C (admin, org 42) is lid van dezelfde org
**When** user C `GET /api/app/templates` aanroept
**Then** de response bevat `mijn-stijl` WEL

### AC-CRUD-11 — active_template_ids cross-tenant ID → 400

**Given** user in org `42` en een template `id=500` bestaat in org `77`
**When** de user `PATCH /api/app/account/kb-preference` aanroept met body `{"active_template_ids": [500]}`
**Then** de response is HTTP 400 (de ID bestaat niet in de tenant-context van de caller)

### AC-CRUD-12 — PATCH active_template_ids invalideert user-scoped cache

**Given** user met `librechat_user_id="lru-abc"` in org `42`
**And** Redis key `templates:42:lru-abc` bestaat met TTL 30s
**When** de user `PATCH /api/app/account/kb-preference` aanroept met body `{"active_template_ids": [1]}`
**Then** de response is HTTP 200
**And** Redis key `templates:42:lru-abc` is verwijderd
**And** andere Redis keys matching `templates:42:*` zijn NIET verwijderd

## Test Area 3 — Cache Invalidation

### AC-CACHE-01 — org-scope write → SCAN+DEL

**Given** 5 Redis keys bestaan matching pattern `templates:42:*` met diverse librechat_user_ids
**When** een admin `POST /api/app/templates` aanroept met `scope="org"` en de commit succeed
**Then** alle 5 Redis keys onder `templates:42:*` zijn verwijderd
**And** een structured log `templates_cache_invalidated` bevat `org_id=42` en `user_id="org-wide"`

### AC-CACHE-02 — personal-scope write → single DEL

**Given** Redis keys `templates:42:lru-a`, `templates:42:lru-b`, `templates:42:lru-c` bestaan
**When** user met `librechat_user_id="lru-a"` `POST /api/app/templates` aanroept met `scope="personal"`
**Then** alleen `templates:42:lru-a` is verwijderd
**And** `templates:42:lru-b` en `templates:42:lru-c` bestaan nog steeds

### AC-CACHE-03 — Redis-error swallowed, schrijf slaagt

**Given** de Redis-connectie is down (simulatie: gooi `ConnectionError` bij SCAN/DEL)
**When** een admin `POST /api/app/templates` aanroept met `scope="org"`
**Then** de response is HTTP 201 (schrijf slaagt)
**And** een structured warning `templates_cache_invalidation_failed` is gelogd met `exc_info=True`

## Test Area 4 — Internal Endpoint

### AC-INT-01 — 200 empty voor user zonder active_template_ids

**Given** een bestaande `PortalOrg` met `zitadel_org_id="zid-42"`
**And** een `PortalUser` met `librechat_user_id="lru-x"` en `active_template_ids=NULL`
**When** de hook `GET /internal/templates/effective?zitadel_org_id=zid-42&librechat_user_id=lru-x` met geldig bearer aanroept
**Then** de response is HTTP 200 met body `{"instructions": []}`

### AC-INT-02 — Fail-safe: onbekende librechat_user_id → 200 empty

**Given** een bestaande `PortalOrg` met `zitadel_org_id="zid-42"`
**And** GEEN `PortalUser` rij met `librechat_user_id="lru-missing"` bestaat
**When** de hook `GET /internal/templates/effective?zitadel_org_id=zid-42&librechat_user_id=lru-missing` aanroept
**Then** de response is HTTP 200 met body `{"instructions": []}` (NIET 404 — chat mag niet breken)

### AC-INT-03 — Config-fout: onbekende zitadel_org_id → 404

**Given** GEEN `PortalOrg` met `zitadel_org_id="zid-missing"` bestaat
**When** de hook `GET /internal/templates/effective?zitadel_org_id=zid-missing&librechat_user_id=lru-x` aanroept
**Then** de response is HTTP 404

### AC-INT-04 — 401 zonder bearer, geen DB-access

**Given** er zijn geen auth-headers op de request
**When** de hook `GET /internal/templates/effective?...` aanroept
**Then** de response is HTTP 401
**And** geen SQL query is uitgevoerd (verifiëren via geïnstrumenteerde test-fixture)

### AC-INT-05 — 200 met instructions in user-opgegeven volgorde

**Given** user met `active_template_ids=[5, 2, 9]`
**And** templates `id=2` (name="Formeel"), `id=5` (name="Klantenservice"), `id=9` (name="Creatief") alle `is_active=true`
**When** de hook het endpoint aanroept
**Then** de response is HTTP 200 met body:
```json
{"instructions": [
  {"source": "template", "name": "Klantenservice", "text": "..."},
  {"source": "template", "name": "Formeel", "text": "..."},
  {"source": "template", "name": "Creatief", "text": "..."}
]}
```
(volgorde = `[5, 2, 9]` zoals in `active_template_ids`)

### AC-INT-06 — Inactive templates worden overgeslagen

**Given** user met `active_template_ids=[5, 2]`
**And** template `id=5` heeft `is_active=false`, template `id=2` heeft `is_active=true`
**When** de hook het endpoint aanroept
**Then** de response bevat alleen de instruction voor template 2

## Test Area 5 — LiteLLM Hook Integration

### AC-HOOK-01 — Template-prefix wordt gepreprend vóór KB-block

**Given** een user in org `42` heeft template "Klantenservice" actief
**And** de hook bouwt een KB-context block met "De klant heeft product X gekocht"
**When** een chat-call binnenkomt met een original system message "Jij bent een assistent"
**Then** het nieuwe system message bevat (in volgorde):
1. De template-tekst van Klantenservice
2. Het KB-context block
3. De originele system message "Jij bent een assistent"

### AC-HOOK-02 — Geen templates → system message ongewijzigd (buiten KB-pad)

**Given** een user zonder active_template_ids
**When** een chat-call binnenkomt
**Then** de hook verandert het system message alleen zoals de bestaande KB-injection dat zou doen
**And** geen template-prefix wordt toegevoegd

### AC-HOOK-03 — Fail-open bij portal-api timeout

**Given** portal-api antwoordt niet (timeout > `TEMPLATES_TIMEOUT=2.0s`)
**When** de hook `_get_templates(org_id, user_id, cache)` aanroept
**Then** de return-waarde is `[]`
**And** een warning `templates_degraded` is gelogd met `reason="TimeoutException"`
**And** de chat-request gaat succesvol door zonder template-prefix

### AC-HOOK-04 — 30s cache werkt (geen dubbele fetch binnen TTL)

**Given** de hook heeft zojuist `/internal/templates/effective` aangeroepen voor `(org=42, user=lru-x)`
**When** binnen 30 seconden dezelfde `(org=42, user=lru-x)` opnieuw een chat-call doet
**Then** geen nieuwe HTTP call naar portal-api gebeurt (verifiëren via mock-teller)
**And** dezelfde instructions lijst wordt gebruikt

### AC-HOOK-05 — Nieuwe activatie landt na cache-invalidatie

**Given** user heeft cached templates `[]` in de hook (30s TTL)
**When** de user `PATCH /api/app/account/kb-preference` aanroept met `active_template_ids=[1]`
**And** de Redis invalidatie `templates:42:lru-x` verwijdert (of TTL verloopt)
**When** de volgende chat-call binnenkomt (buiten de 30s-window of na explicit cache-clear)
**Then** de hook haalt opnieuw op van `/internal/templates/effective`
**And** het system message bevat de text van template `id=1`

---

## Edge Cases

### EC-01 — `prompt_text` exact op limiet (8000 chars) → 201

**Given** een admin in org `42`
**When** POST met `prompt_text` van exact 8000 karakters
**Then** HTTP 201 (CHECK toetst `<= 8000`, niet `< 8000`)

### EC-02 — `prompt_text` = 8001 chars → 400 (Pydantic) vóór DB

**Given** Pydantic veld `prompt_text: str = Field(max_length=8000)`
**When** POST met 8001-char `prompt_text`
**Then** HTTP 422 van Pydantic vóór DB-roundtrip
**And** geen row in `portal_templates`

### EC-03 — `name` produceert lege slug na slugify → 400

**Given** `name="!!! ??? ###"` (alle karakters worden gestript)
**When** POST wordt uitgevoerd
**Then** `slugify(name)` returnt `""`
**And** response is HTTP 400 met message `"Name must produce a valid slug"`

### EC-04 — Dup slug binnen zelfde org → 409 (geen 500)

**Given** bestaande template slug `formeel` in org `42`
**When** POST met `name="Formeel"` in zelfde org (slugify → `formeel`)
**Then** response is HTTP 409 met message bevattend `formeel`
**And** de `IntegrityError` is netjes gevangen (geen 500 stacktrace)

### EC-05 — Dezelfde slug in andere org → toegestaan

**Given** org `42` heeft `formeel`, org `77` heeft nog geen `formeel`
**When** admin in org `77` POST met `name="Formeel"`
**Then** HTTP 201 — UNIQUE is op `(org_id, slug)`, niet globaal

### EC-06 — `active_template_ids=[]` (lege lijst, niet NULL) → toegestaan

**Given** user heeft momenteel `active_template_ids=[5]`
**When** PATCH kb-preference met `active_template_ids=[]`
**Then** HTTP 200, user heeft nu `active_template_ids=[]` (niet NULL)
**And** `/internal/templates/effective` returnt `{"instructions": []}`

### EC-07 — `active_template_ids` verwijst naar gedeletede template

**Given** user heeft `active_template_ids=[5]` maar template 5 is gedelete door admin
**When** de hook `/internal/templates/effective` aanroept
**Then** response is `{"instructions": []}` (de query filtert op bestaande + is_active=true)

### EC-08 — Redis SCAN cursor-iteratie over > 100 keys

**Given** 250 keys matching `templates:42:*` bestaan
**When** `invalidate_templates(42, None)` wordt aangeroepen
**Then** alle 250 keys zijn verwijderd na voltooiing (cursor-loop voltooit)
**And** operatie duurt < 100 ms (gemeten in test met lokale Redis)

### EC-09 — Unicode in template `name` → valide slug

**Given** `name="Naïve Stijl"`
**When** POST wordt uitgevoerd
**Then** `slug` is `"nave-stijl"` of `"naïve-stijl"` afhankelijk van slugify-impl — test pint één deterministisch gedrag

### EC-10 — provisioning step raiseert → provisioning voltooit

**Given** `ensure_default_templates` wordt gemocked om een `SQLAlchemyError` te raisen
**When** de provisioning orchestrator de `defaults_templates` step uitvoert
**Then** de orchestrator logt `defaults_templates_step_failed` (warning)
**And** de provisioning voltooit tot de normale eindtoestand (`provisioning_status` ≠ `failed_rollback_*`)

---

## Performance Criteria

### PERF-01 — CRUD endpoints p95 ≤ 100 ms

- `GET /api/app/templates` p95 ≤ 100 ms (gemeten bij org met 4-20 templates)
- `POST /api/app/templates` p95 ≤ 100 ms (inclusief cache-invalidatie fire-and-forget)
- `PATCH /api/app/templates/{slug}` p95 ≤ 100 ms

### PERF-02 — `/internal/templates/effective` p95 ≤ 20 ms

Gemeten bij een user met 3 active templates in een org met 20 totaal. Dit is het kritieke pad van de LiteLLM-hook; elke extra ms telt in de chat-latency.

### PERF-03 — Cache-hit overhead in LiteLLM hook ≤ 5 ms

Met warme in-process cache: `_get_templates` returnt in ≤ 5 ms (geen netwerk-call, alleen dict-lookup + TTL check).

### PERF-04 — Cache-miss overhead ≤ 30 ms (portal-api p95 20 + netwerk + JSON parse)

Met koude cache: totale roundtrip ≤ 30 ms p95 bij healthy portal-api.

### PERF-05 — SCAN+DEL voor `templates:{org}:*` ≤ 100 ms bij ≤ 500 keys

Target: cursor-based SCAN met batch-pipeline verwerkt 500 keys binnen 100 ms in een lokale Redis-instance. Production monitoring via `templates_cache_invalidated` log-event met duur.

---

## Definition of Done

### Functional

- [ ] Alle 15+ acceptance criteria in Test Areas 1-5 zijn gedekt door automatische tests (pytest) en slagen in CI
- [ ] Alle 10 edge cases hebben een bijbehorende automatische test en slagen
- [ ] Alle 5 performance criteria zijn gemeten; p95-waardes gedocumenteerd in de PR-description

### Schema & Migrations

- [ ] `alembic heads` returnt exact **1** head na het landen van de merge-migration (`<ts>_merge_main_heads_before_templates`)
- [ ] `portal_templates` tabel heeft:
  - [ ] `ENABLE ROW LEVEL SECURITY` en `FORCE ROW LEVEL SECURITY` actief (verifiëren via `SELECT rowsecurity FROM pg_tables WHERE tablename='portal_templates'`)
  - [ ] Policy `tenant_isolation` aanwezig (zonder `OR IS NULL` fallback)
  - [ ] CHECK constraint `ck_portal_template_prompt_len` (`char_length(prompt_text) <= 8000`)
  - [ ] CHECK constraint `ck_portal_template_scope` (`scope IN ('org','personal')`)
  - [ ] UNIQUE constraint `uq_portal_template_org_slug` op `(org_id, slug)`
  - [ ] Index `ix_portal_template_org_active_scope` op `(org_id, is_active, scope)`
- [ ] `portal_users.active_template_ids` kolom bestaat met type `INTEGER[] NULL`

### Code Hygiene

- [ ] Geen raw `prompt_text` in log-statements: grep-check `rg "prompt_text" klai-portal/backend/app --type py -l` toont geen matches in logger.* calls
- [ ] Geen `AskUserQuestion` calls in nieuwe code: grep-check `rg "AskUserQuestion" klai-portal/backend/app deploy/litellm --type py` toont 0 matches
- [ ] Alle nieuwe async code gebruikt `asyncio.wait_for` of expliciete timeouts — geen `await` in for-loops zonder deadline
- [ ] Alle except-blokken loggen met `exc_info=True` of via `logger.exception` (TRY401 ruff-rule)
- [ ] `uv run ruff check klai-portal/backend` returnt 0 errors
- [ ] `uv run --with pyright pyright klai-portal/backend/app/api/app_templates.py klai-portal/backend/app/api/internal.py klai-portal/backend/app/services/default_templates.py klai-portal/backend/app/services/litellm_cache.py` returnt 0 errors

### Tests

- [ ] Coverage op nieuwe modules ≥ 85% (`klai-portal/backend/app/api/app_templates.py`, `default_templates.py`, `litellm_cache.py`, `slug.py`, `/internal/templates/effective`)
- [ ] `tests/test_app_templates.py` dekt: list, create (admin/member), read, update (owner/admin), delete, 400/403/409/422/429
- [ ] `tests/test_internal_templates.py` dekt: AC-INT-01 t/m AC-INT-06 (inclusief 401 zonder DB-access)
- [ ] `tests/test_default_templates.py` dekt: eerste call seed 4, tweede call is no-op, `created_by="system"`
- [ ] `tests/test_litellm_cache_templates.py` dekt: SCAN+DEL path, single DEL path, Redis-error swallowed
- [ ] Integration test voor LiteLLM hook (docker-compose dev-env of mock) dekt AC-HOOK-01 t/m AC-HOOK-05

### MX Tags

- [ ] `@MX:ANCHOR` + `@MX:REASON` op `invalidate_templates`
- [ ] `@MX:WARN` + `@MX:REASON` op fail-safe branch in `effective_templates`
- [ ] `@MX:NOTE` op `DEFAULT_TEMPLATES` constant
- [ ] `@MX:NOTE` op `step_6b_defaults_templates`
- [ ] `@MX:WARN` + `@MX:REASON` op fail-open branch in `_get_templates`
- [ ] `@MX:TODO` in spec-linked file voor frontend (SPEC-CHAT-TEMPLATES-002)

### Observability

- [ ] Structured log events aanwezig en queryable in VictoriaLogs: `template_created`, `template_updated`, `template_deleted`, `templates_cache_invalidated`, `templates_cache_invalidation_failed`, `templates_degraded`, `default_templates_seeded`, `defaults_templates_step_failed`
- [ ] Request-ID propagation via `get_trace_headers()` in alle portal-api → portal-api en LiteLLM → portal-api calls (bestaand patroon, niet breken)
- [ ] Geen `prompt_text` raw in log bodies (stil afgedwongen via code-review + grep-check)

### Documentation

- [ ] `docs/architecture/platform.md` Templates-sectie bijgewerkt; onderscheid met guardrails/PII expliciet benoemd
- [ ] `docs/architecture/knowledge-retrieval-flow.md` "Rules and Templates" sectie beschrijft voor v1 alleen Templates-flow; forward-reference naar SPEC-CHAT-GUARDRAILS-001
- [ ] Alembic migrations hebben docstrings die het doel benoemen (merge / tabel met RLS+CHECK / kolom)

### Deployment Readiness

- [ ] Env-documentatie bijgewerkt voor `PORTAL_TEMPLATES_URL` en `TEMPLATES_TIMEOUT`
- [ ] Post-deploy verificatie gedocumenteerd: `SELECT COUNT(*) FROM portal_templates WHERE created_by='system'` voor een nieuwe test-tenant → `4`
- [ ] Rollback-plan beschreven in PR (down-migrations getest in staging)
