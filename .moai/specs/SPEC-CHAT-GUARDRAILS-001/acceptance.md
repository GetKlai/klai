---
id: SPEC-CHAT-GUARDRAILS-001
version: 0.1.0
status: draft
created: 2026-04-22
updated: 2026-04-22
author: Mark Vletter
priority: high
issue_number: 0
---

# Acceptance — SPEC-CHAT-GUARDRAILS-001

Alle scenario's zijn geformuleerd als Given-When-Then. Rode draad: observable evidence (HTTP response, DB-row, log-line, Redis-key state, user-message content) — niet "code looks correct".

## Layer 1 — klai-pii service

### Scenario PII-01: NL_BSN detection met valide 11-proof

**Given** klai-pii is running met `models_loaded=True`
**And** de request heeft een valide `Authorization: Bearer $PII_INTERNAL_SECRET` header
**When** we `POST /v1/analyze` sturen met body `{"text": "Mijn BSN is 123456782", "language": "nl", "entities": ["NL_BSN"]}`
**Then** de response is HTTP 200
**And** `hits` bevat minimaal één entry met `entity_type == "NL_BSN"`
**And** `hits[0].start` wijst naar positie 11 en `hits[0].end` naar positie 20

### Scenario PII-02: NL_BSN rejection bij invalide 11-proof

**Given** klai-pii is running
**When** we `POST /v1/analyze` sturen met `{"text": "Mijn BSN is 123456789", "language": "nl", "entities": ["NL_BSN"]}` (11-proof faalt)
**Then** de response is HTTP 200
**And** `hits` bevat GEEN entry met `entity_type == "NL_BSN"`

### Scenario PII-03: GLiNER PERSON detection op NL tekst

**Given** klai-pii is running met GLiNER geladen
**When** we `POST /v1/analyze` sturen met `{"text": "Ik heb gisteren Mark Vletter ontmoet in Amsterdam.", "language": "nl", "entities": ["PERSON", "LOCATION"]}`
**Then** de response is HTTP 200
**And** `hits` bevat minstens één entry met `entity_type == "PERSON"` en `score >= 0.5`
**And** `hits` bevat minstens één entry met `entity_type == "LOCATION"`

### Scenario PII-04: Redact met placeholder mode

**Given** klai-pii is running
**When** we `POST /v1/redact` sturen met `{"text": "Mail me op mark@klai.nl", "language": "nl", "entities": ["EMAIL_ADDRESS"], "keywords": [], "mode": "placeholder"}`
**Then** de response is HTTP 200
**And** `redacted_text == "Mail me op [EMAIL_ADDRESS]"`
**And** `applied` bevat `{"entity_type": "EMAIL_ADDRESS", "action": "redact"}`

### Scenario PII-05: Keyword redaction (case-insensitive)

**Given** klai-pii is running
**When** we `POST /v1/redact` sturen met `{"text": "The SECRET password is foo", "language": "en", "entities": [], "keywords": ["secret"], "mode": "placeholder"}`
**Then** de response is HTTP 200
**And** `redacted_text` bevat NIET het woord "SECRET" (case-insensitive match op `"secret"` is gematcht en vervangen)
**And** `applied` bevat `{"keyword": "secret", "action": "redact"}`

### Scenario PII-06: Auth failure

**Given** klai-pii is running
**When** we `POST /v1/analyze` sturen ZONDER `Authorization` header OF met een verkeerde secret
**Then** de response is HTTP 401
**And** response body is niet de analyze-output (service heeft de body niet verwerkt)

### Scenario PII-07: Unsupported entity 400

**Given** klai-pii is running
**When** we `POST /v1/analyze` sturen met `{"text": "foo", "language": "nl", "entities": ["US_SSN", "MEDICAL_LICENSE"]}`
**Then** de response is HTTP 400
**And** response body bevat `"unsupported_entities": ["US_SSN", "MEDICAL_LICENSE"]`

### Scenario PII-08: Health endpoint gedurende startup

**Given** klai-pii container is net gestart (models nog aan het laden)
**When** we `GET /health` doen (zonder auth)
**Then** de response is HTTP 200
**And** body bevat `{"status": "starting", "models_loaded": false}`
**When** we 90 seconden wachten en opnieuw `GET /health` doen
**Then** body bevat `{"status": "ok", "models_loaded": true, "language_support": ["nl", "en"]}`

## Layer 2 — Portal-api CRUD

### Scenario CRUD-01: Happy-path rule aanmaken

**Given** een ingelogde portal-user met een bestaande org
**And** alembic migraties zijn uitgevoerd
**When** de user `POST /api/app/rules` stuurt met body `{"name": "Blokkeer IBAN", "rule_type": "pii_block", "detector_entities": ["IBAN_CODE"], "keywords": [], "scope": "org"}`
**Then** de response is HTTP 201
**And** de response bevat een `slug` == `"blokkeer-iban"`
**And** een row bestaat in `portal_rules` met `org_id == caller.org.id` en `is_active == true`

### Scenario CRUD-02: Whitelist-afwijzing

**Given** een ingelogde portal-user
**When** de user `POST /api/app/rules` stuurt met `detector_entities: ["US_SSN"]`
**Then** de response is HTTP 400
**And** response body verwijst naar de niet-ondersteunde entity

### Scenario CRUD-03: Template prompt_text lengte-check

**Given** een ingelogde portal-user
**When** de user `POST /api/app/templates` stuurt met `prompt_text` van 8001 chars
**Then** de response is HTTP 400
**And** response body verwijst naar de CHECK-constraint op `prompt_text <= 8000`

### Scenario CRUD-04: Authorization op PATCH

**Given** gebruiker A heeft een `scope="personal"` rule aangemaakt
**And** gebruiker B is geen admin en zit in dezelfde org
**When** gebruiker B `PATCH /api/app/rules/{slug}` probeert op A's rule
**Then** de response is HTTP 403

### Scenario CRUD-05: Rate-limit

**Given** een ingelogde portal-user
**When** de user 11 `POST /api/app/rules` requests in dezelfde seconde stuurt
**Then** de eerste 10 responses zijn HTTP 201
**And** de 11e response is HTTP 429
**And** de 11e response bevat een `Retry-After` header

### Scenario CRUD-06: Cross-org active_template_ids afgewezen

**Given** gebruiker in org A
**And** een template bestaat in org B met id=42
**When** de user `PATCH /api/app/account` stuurt met `active_template_ids: [42]`
**Then** de response is HTTP 400
**And** `portal_users.active_template_ids` is NIET geüpdatet

### Scenario CRUD-07: Personal-scope rule-isolatie

**Given** gebruiker A heeft een `scope="personal"` rule in org X aangemaakt
**And** gebruiker B zit in dezelfde org X (niet-admin, niet-creator)
**When** gebruiker B `GET /api/app/rules` doet
**Then** A's personal rule is NIET in de response lijst
**And** alleen `scope="org"` rules en B's eigen `scope="personal"` rules zijn zichtbaar

### Scenario CRUD-08: RLS strict (geen context = geen rows)

**Given** een direct DB-session zonder `SET app.current_org_id`
**When** we `SELECT * FROM portal_rules` draaien
**Then** de query returnt 0 rows
**And** RLS policy is actief (bewijsbaar via `SELECT relforcerowsecurity FROM pg_class WHERE relname = 'portal_rules'`)

### Scenario CRUD-09: Defaults bij nieuwe org

**Given** een nieuwe org wordt geprovisiond via de bestaande provisioning-flow
**When** provisioning step 6b succesvol draait
**Then** `portal_rules WHERE org_id = new_org` bevat exact 4 rows (E-mailadressen, BSN, IBAN, Creditcard)
**And** `portal_templates WHERE org_id = new_org` bevat exact 4 rows (Klantenservice, Formeel, Creatief, Samenvatter)
**And** `portal_orgs.defaults_seeded_at` is gezet

### Scenario CRUD-10: Defaults idempotent

**Given** een org waar step 6b al één keer succesvol heeft gedraaid
**When** step 6b opnieuw draait
**Then** er worden GEEN duplicaten aangemaakt (`portal_rules` blijft op 4 defaults; `portal_templates` blijft op 4)

### Scenario CRUD-11: /internal/guardrails/effective — happy path

**Given** org X heeft 1 actieve rule (`pii_redact` op `EMAIL_ADDRESS`)
**And** user Y in org X heeft `active_template_ids = [template_id_1]`
**When** interne service `GET /internal/guardrails/effective?zitadel_org_id={X.zitadel_id}&librechat_user_id={Y.librechat_id}` doet
**Then** de response is HTTP 200
**And** `detectors` bevat één entry met `action="redact"`, `entities=["EMAIL_ADDRESS"]`, `keywords=[]`, `rule_name="E-mailadressen redacten"`
**And** `instructions` bevat één entry met `source="template"` en de `prompt_text` van template 1

### Scenario CRUD-12: /internal/guardrails/effective — lege state

**Given** org Z heeft geen actieve rules en user in org Z heeft geen active_template_ids
**When** interne service het effective-endpoint aanroept
**Then** de response is HTTP 200
**And** `detectors == []` en `instructions == []`

### Scenario CRUD-13: /internal/guardrails/effective — missing librechat mapping

**Given** de `librechat_user_id` in de request heeft geen koppeling naar een `zitadel_user_id`
**When** het effective-endpoint wordt aangeroepen
**Then** de response is HTTP 200 (fail-safe)
**And** `detectors == []` en `instructions == []`
**And** er is een warning-log `guardrails_unknown_user` met de `librechat_user_id`

## Layer 3 — Cache invalidation

### Scenario CACHE-01: POST rule invalideert user-cache

**Given** Redis bevat `guardrails:{org_id}:{librechat_user_id}` met TTL 30s
**When** een user `POST /api/app/rules` succesvol doet
**Then** binnen 100 ms is de key gepatternde `guardrails:{org_id}:*` weg uit Redis (SCAN+DEL)
**And** de volgende chat-call bouwt de cache opnieuw op

### Scenario CACHE-02: active_template_ids invalideert alleen eigen user

**Given** Redis bevat zowel `guardrails:{org}:{user_A}` als `guardrails:{org}:{user_B}`
**When** user A `PATCH /api/app/account` doet met nieuwe `active_template_ids`
**Then** `guardrails:{org}:{user_A}` is weg
**And** `guardrails:{org}:{user_B}` bestaat nog (single-key DEL, niet pattern)

### Scenario CACHE-03: Redis down — fail-open invalidate

**Given** Redis is unreachable
**When** een user `POST /api/app/rules` doet
**Then** de response is HTTP 201 (de schrijfactie slaagt)
**And** een structlog warning `cache_invalidate_failed` is geschreven
**And** de volgende chat-call na TTL-expiry (30s) pakt de nieuwe state op

## Layer 4 — LiteLLM hook flow

### Scenario HOOK-01: Fully-wired redact flow

**Given** org X heeft active rule `pii_redact` op `EMAIL_ADDRESS`
**And** user Y in org X heeft active template 1 (prompt_text = "Je bent een klantenservice-medewerker.")
**And** klai-pii is healthy, retrieval-api is healthy
**When** user Y een chat-message stuurt `"Mijn mail is mark@klai.nl wat weten jullie van mij?"`
**Then** de LiteLLM hook doet parallel een `/v1/analyze` call en een `/retrieve` call
**And** de user-message die naar het model gaat bevat NIET `"mark@klai.nl"` maar `"[EMAIL_ADDRESS]"`
**And** de system-prompt bevat (in volgorde): template-instructies → KB-context → pre-existing system
**And** een structlog entry `guardrail_applied` is geschreven met `action="redact"`, `entity_type="EMAIL_ADDRESS"`, `org_id=X`, `user_id=Y`
**And** de log entry bevat NIET `"mark@klai.nl"` als raw text

### Scenario HOOK-02: Block rule abort

**Given** org X heeft active rule `pii_block` op `CREDIT_CARD`
**When** user Y een message stuurt `"Mijn creditcard is 4111 1111 1111 1111"`
**Then** de LiteLLM call wordt afgebroken voordat het bij het model komt
**And** de user ontvangt een error-message: `"Bericht geblokkeerd door guardrail: Creditcardnummers blokkeren"`
**And** een structlog entry `guardrail_applied` met `action="block"` is geschreven

### Scenario HOOK-03: Fail-open bij klai-pii down

**Given** klai-pii container is gestopt
**And** org X heeft active rule `pii_redact` op `EMAIL_ADDRESS`
**When** user Y een message stuurt `"Mijn mail is mark@klai.nl"`
**Then** de chat-call slaagt (reaches the model)
**And** de user-message is ONGEWIJZIGD (`"mark@klai.nl"` is niet geredacteerd — fail-open)
**And** een structlog entry `guardrails_degraded` is geschreven met `reason` (timeout|5xx)

### Scenario HOOK-04: Fail-open bij klai-pii 401

**Given** `PII_INTERNAL_SECRET` env var in de hook is verkeerd geconfigureerd
**When** user Y een message stuurt
**Then** de chat-call slaagt (fail-open)
**And** een structlog entry `guardrails_config_error` op level=error is geschreven

### Scenario HOOK-05: Zero-detector skip

**Given** org X heeft GEEN active rules (geen entities, geen keywords)
**And** user Y heeft GEEN active_template_ids
**When** user Y een message stuurt
**Then** de LiteLLM hook doet GEEN round-trip naar klai-pii
**And** de hook doet wel de normale `/retrieve` call
**And** geen `guardrail_applied` of `guardrails_degraded` log entry verschijnt

### Scenario HOOK-06: Template-only (geen rules)

**Given** org X heeft GEEN active rules
**And** user Y heeft `active_template_ids = [template_1]` met prompt_text "Antwoord formeel."
**When** user Y een message stuurt
**Then** de LiteLLM hook doet GEEN `/v1/analyze` call (geen detectors)
**And** de system-prompt bevat de template-instructie `"Antwoord formeel."` voor het KB-context block

### Scenario HOOK-07: Instructions ordering

**Given** de hook triggert met: template A (prompt "Antwoord kort."), template B (prompt "Gebruik NL."), KB-context is non-empty, en een bestaande system-prompt `"Je bent Klai."`
**When** de pre-call hook het system message samenstelt
**Then** de volgorde is:
1. Template A instruction block
2. Template B instruction block
3. KB-context block
4. `"Je bent Klai."` (pre-existing)

### Scenario HOOK-08: Log-scrubbing verplicht

**Given** een `pii_redact` rule triggert op een user-message met een BSN
**When** de `guardrail_applied` log wordt geschreven
**Then** de log entry bevat `rule_name`, `action`, `entity_type`, `org_id`, `user_id`
**And** de log entry bevat NIET de raw BSN string
**And** de log entry bevat NIET de raw user-message text

## Edge cases

### Scenario EDGE-01: Rule met lege entities EN lege keywords

**Given** een user POSTed een rule met `detector_entities: []` en `keywords: []`
**When** de validatie draait
**Then** de response is HTTP 400 (een rule zonder enige detector is zinloos)

### Scenario EDGE-02: Keyword >64 chars

**When** user POSTed een rule met een keyword van 65 chars
**Then** HTTP 400

### Scenario EDGE-03: >100 keywords in een rule

**When** user POSTed een rule met 101 keywords
**Then** HTTP 400

### Scenario EDGE-04: Slug-collision bij tweede insert

**Given** een rule met naam "E-mails" en slug "e-mails" bestaat al in org X
**When** dezelfde user `POST /api/app/rules` doet met nieuwe rule maar ook naam "E-mails"
**Then** HTTP 409 (unique(org_id, slug) constraint)
**Or** HTTP 400 met helpful message

### Scenario EDGE-05: Redact offsets non-overlapping

**Given** een text met 2 niet-overlappende PII-hits
**When** redact draait in placeholder mode
**Then** beide hits worden correct vervangen
**And** offsets in `applied` corresponderen met de originele input (niet met post-redact offsets)

### Scenario EDGE-06: klai-pii 500 tijdens redact-roundtrip (maar analyze succeedde)

**Given** analyze returnt hits, maar `/v1/redact` crashed
**When** de hook de redact-stap draait
**Then** fail-open: user-message gaat ongewijzigd door
**And** `guardrails_degraded` log entry met `phase="redact"` is geschreven

## Performance criteria

### Scenario PERF-01: klai-pii warm p95

**Given** klai-pii draait >5 min (warm)
**When** 100 sequentiële `POST /v1/analyze` calls worden gedaan met 200-char NL text en `entities=["PERSON","EMAIL_ADDRESS","NL_BSN"]`
**Then** p95 latency <= 300 ms
**And** p50 latency <= 150 ms

### Scenario PERF-02: klai-pii cold-start p95

**Given** klai-pii container is net gestart (`models_loaded=False`)
**When** het eerste analyze-request binnenkomt NADAT `models_loaded=True`
**Then** de eerste call p95 <= 1000 ms (eerste GLiNER inference is duurder door lazy-load onderdelen)

### Scenario PERF-03: CRUD p95

**Given** `portal_rules` heeft <100 rows per org (typische casus)
**When** een user `GET /api/app/rules` of `POST /api/app/rules` doet
**Then** p95 latency <= 100 ms

### Scenario PERF-04: Parallel gather

**Given** klai-pii round-trip ~200ms en retrieval-api round-trip ~400ms in isolatie
**When** de hook beide in `asyncio.gather` runt
**Then** totale wall-clock tijd <= max(200, 400) + 50ms overhead = ~450 ms
**And** NIET 600 ms (sequential)
**And** meetbaar via structlog timing-logs of een integration-test-timer

### Scenario PERF-05: Cache-hit overhead verwaarloosbaar

**Given** een second chat-call binnen 30s (cache hit voor guardrails)
**When** hook de cache raadpleegt
**Then** cache-read voegt <5ms toe aan de pre-call path
**And** geen `/internal/guardrails/effective` roundtrip plaatsvindt

## Definition of Done

- [ ] Alle 4 laag-secties (PII, CRUD, CACHE, HOOK) hebben hun scenarios observable-verifieerd (test of smoke).
- [ ] Alle 6 edge-cases zijn covered door automated tests.
- [ ] Alle 5 performance-criteria zijn gemeten en gedocumenteerd (minimaal in een smoke-run-verslag).
- [ ] `pytest` suites van klai-pii + klai-portal/backend groen in CI.
- [ ] `docker compose up` in een schone env komt naar healthy state binnen 90s en een e2e-smoke (rule + template + chat) werkt zoals HOOK-01 beschrijft.
- [ ] Grep op `_PII_PATTERNS` in `deploy/litellm/klai_knowledge.py` returnt 0 regels.
- [ ] `alembic heads` toont 1 head na alle migraties.
- [ ] Documentatie (`docs/architecture/platform.md`, `docs/architecture/knowledge-retrieval-flow.md`) beschrijft de feitelijke implementatie.
- [ ] Geen `AskUserQuestion` in de codebase (per `.claude/rules/klai/no-ask-user-question.md`).
- [ ] Geen raw PII in structlog-entries (PII-scrub test passeert).
- [ ] MX-tags geplaatst per plan.md "MX Tag Plan" sectie.
