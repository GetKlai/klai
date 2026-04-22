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

# Plan — SPEC-CHAT-GUARDRAILS-001

## Approach

Brownfield delta over drie codebases: `klai-pii` (greenfield), `klai-portal/backend` (migraties + nieuwe API), `deploy/litellm/klai_knowledge.py` (refactor). Lagen worden bottom-up opgebouwd en pas aan het eind bedraad:

1. **klai-pii eerst, standalone** — service is testbaar zonder portal-api; kunnen lokaal `curl /v1/analyze` draaien om de model-pipeline te valideren voordat iets anders afhankelijk is.
2. **Portal-api migraties + modellen + CRUD** — naast klai-pii; portal-api heeft geen runtime-dependency op klai-pii (alleen de hook heeft dat).
3. **LiteLLM hook refactor als laatste** — hook consumeert zowel `/internal/guardrails/effective` (portal-api) als `/v1/analyze` (klai-pii). Pas refactoren als beide werken.

De grootste risico's (GLiNER RAM, cold-start, alembic merge-heads) zitten in klai-pii en in migratie-volgorde. Die pakken we in de eerste taken aan.

## Milestones

### Priority High (blokkerend voor end-to-end flow)

- **M1 — klai-pii scaffold werkt** — service start, `/health` returnt `models_loaded=True`, bearer-auth werkt. Validatie: `docker compose up klai-pii` + `curl -H "Authorization: Bearer $SEC" /health`.
- **M2 — klai-pii analyze + redact correct** — Presidio + GLiNER + NL_BSN + keywords alle werken. Validatie: `pytest klai-pii/tests/` groen.
- **M3 — Alembic heads gemerged** — 6 open heads van main naar 1 head. Validatie: `alembic heads` toont 1 head; `alembic upgrade head` op lege DB slaagt.
- **M4 — Portal-api modellen + migraties live** — `portal_rules`, `portal_templates`, `active_template_ids`, `defaults_seeded_at` bestaan met RLS. Validatie: `alembic upgrade head` in dev + `\d portal_rules` toont alle kolommen + policy.
- **M5 — Portal-api CRUD endpoints + internal endpoint werken** — Rules + Templates CRUD, `/internal/guardrails/effective`. Validatie: `pytest klai-portal/backend/tests/test_app_rules.py test_app_templates.py test_internal_guardrails.py` groen.
- **M6 — Cache-invalidate werkt** — `invalidate_guardrails` aangeroepen vanuit 4 write-paden. Validatie: `test_litellm_cache.py` + integration-test die POST-rule doet en Redis-key check.
- **M7 — Provisioning step 6b zet defaults** — nieuwe org krijgt 4 rules + 4 templates + `defaults_seeded_at`. Validatie: `test_default_rules_templates.py` + handmatige provisioning van test-org.
- **M8 — LiteLLM hook consumeert klai-pii** — geen inline regex meer, parallel gather met retrieve, block/redact applied, instructions geïnjecteerd. Validatie: hook pytest suite + end-to-end smoke in docker-compose (chat via LibreChat → triggert rule → message wordt geredacteerd / geblokkeerd).

### Priority Medium (quality + robuustheid)

- **M9 — Rate-limit + 429 werkt** — 11 requests/sec → 1e 10 door, 11e 429. Validatie: CRUD test.
- **M10 — Fail-open getest** — klai-pii omlaag → chat gaat door, `guardrails_degraded` in logs. Validatie: integration test met klai-pii stopped.
- **M11 — Documentatie up-to-date** — `docs/architecture/platform.md` + `knowledge-retrieval-flow.md` matchen de code.

### Priority Low (polish)

- **M12 — Prometheus metrics klai-pii** — `analyze_duration_seconds`, `entity_hits_total{entity_type}` zichtbaar in Grafana.
- **M13 — MX tags geplaatst** — zie MX Tag Plan sectie.

## Task Decomposition

Elke taak heeft een acceptance hook: welke test of observable bewijst dat de taak klaar is.

### Fase A — klai-pii service

| # | Task | Acceptance hook |
|---|---|---|
| A1 | `klai-pii/` scaffold: `pyproject.toml`, `Dockerfile` (multi-stage met `download_models.py` in build-stage), `klai_pii/main.py` met lifespan, `config.py`, `logging_setup.py`, `middleware/auth.py` | `docker build klai-pii/` slaagt; lokaal `uvicorn klai_pii.main:app` start zonder errors |
| A2 | `services/nl_recognizers.py` — `NL_BSN` PatternRecognizer + 11-proof validator, NL_PHONE override | `test_nl_recognizers.py` covers `123456782` (valid) + `123456789` (invalid) + edge cases |
| A3 | `services/analyzer.py` — Presidio AnalyzerEngine, GLiNER als NLP engine, registreer NL recognizers, `analyze()` + `redact()` methods | Unit test loadt model, verwerkt 1 NL zin, returnt ≥1 hit |
| A4 | `services/keyword_matcher.py` — case-insensitive substring, returnt `[{keyword, start, end}]` | Unit test met `"The secret is hidden"` + `["secret"]` → 1 hit offset 4-10 |
| A5 | `api/analyze.py` — `POST /v1/analyze` router, pydantic schemas, auth-dep, entity-whitelist-validation | `test_analyze.py` covert happy path + 401 + 400 (unsupported entity) |
| A6 | `api/redact.py` — `POST /v1/redact` router, placeholder + hash mode | `test_redact.py` covert placeholder + keyword redaction + offset correctness |
| A7 | `api/health.py` — `GET /health` met `models_loaded` flag + `/metrics` exposition | `test_health.py` check during/after lifespan |
| A8 | Docker-compose entry + `.env.example` update (`PII_INTERNAL_SECRET`) | `docker compose up klai-pii` → healthy binnen 90s; service in `klai-net` |

Dependencies: A2 → A3 → A5/A6; A1 naast A2-A4; A7 onafhankelijk; A8 na A1-A7.

### Fase B — Portal-api migraties + modellen

| # | Task | Acceptance hook |
|---|---|---|
| B1 | Alembic merge-heads migratie (`m1g2r3h4i5j6_merge_main_heads_before_guardrails.py`) — unifieert 6 heads uit main | `alembic heads` toont 1 head na apply |
| B2 | `add_portal_templates.py` — tabel + CHECK `prompt_text <= 8000` + RLS strict | `\d portal_templates` toont kolommen + policy |
| B3 | `add_portal_rules.py` — tabel + CHECK `description <= 500` + `detector_entities TEXT[] NOT NULL DEFAULT '{}'` + `keywords TEXT[] NOT NULL DEFAULT '{}'` + RLS strict | idem |
| B4 | `add_active_template_ids_to_portal_users.py` — kolom `INTEGER[] NULL` | `\d portal_users` toont kolom |
| B5 | `add_portal_org_defaults_seeded_at.py` — kolom `TIMESTAMPTZ NULL` | `\d portal_orgs` toont kolom |
| B6 | `app/models/rules.py` + `app/models/templates.py` + modify `app/models/portal.py` (PortalUser.active_template_ids, PortalOrg.defaults_seeded_at) | SQLAlchemy imports succeed; `pytest tests/test_models.py` groen |

Dependencies: B1 → B2/B3/B4/B5 (parallel); B6 na B2-B5.

### Fase C — Portal-api services + API

| # | Task | Acceptance hook |
|---|---|---|
| C1 | `app/utils/slug.py` — `slugify(name: str) -> str` | Unit test: `"Mijn E-mail Rule"` → `"mijn-e-mail-rule"` |
| C2 | `app/services/default_rules.py` — `ensure_default_rules(org_id, created_by, db)` idempotent, 4 defaults | `test_default_rules.py` covert: call 1x creates 4, call 2x creates 0 |
| C3 | `app/services/default_templates.py` — `ensure_default_templates(...)` idempotent, 4 defaults (copy prompt_text uit `feat/chat-first-redesign`) | Idem voor templates |
| C4 | `app/services/litellm_cache.py` — `invalidate_guardrails(org_id, librechat_user_id=None)` (SCAN+DEL for None, DEL otherwise) | `test_litellm_cache.py` met mock Redis |
| C5 | `app/api/app_rules.py` — CRUD + WHITELIST-validatie + rate-limit + invalidate_guardrails hooks | `test_app_rules.py` covert CRUD + 400 (whitelist) + 403 (other user) + 429 (rate) |
| C6 | `app/api/app_templates.py` — CRUD + length-validatie + rate-limit + invalidate_guardrails hooks | `test_app_templates.py` idem |
| C7 | Modify `app/api/app_account.py` — accepteer `active_template_ids`, valideer tegen org, invalidate_guardrails | `test_app_account.py` uitgebreid met active_template_ids cases (valid, cross-org 400) |
| C8 | Modify `app/api/internal.py` — `GET /internal/guardrails/effective` met librechat→zitadel mapping + fail-safe | `test_internal_guardrails.py` covert happy path + empty + missing-mapping |
| C9 | Modify `app/services/provisioning/orchestrator.py` — step 6b (non-fatal) + set `defaults_seeded_at` | `test_default_rules_templates.py` covert provisioning creates 4+4 |
| C10 | Modify `app/main.py` — register 2 nieuwe routers | `curl /api/app/rules` returnt 200/401 i.p.v. 404 |

Dependencies: C1 voor C5/C6; C2+C3 voor C9; C4 voor C5/C6/C7; C6/C5 voor C10.

### Fase D — LiteLLM hook refactor

| # | Task | Acceptance hook |
|---|---|---|
| D1 | REMOVE inline `_PII_PATTERNS` en alle regex-apply code uit `deploy/litellm/klai_knowledge.py` | Grep op `_PII_PATTERNS` in de hook returnt 0 regels |
| D2 | ADD `_call_pii_service(text, entities, keywords)` + timeout + bearer-auth + structured error handling | Unit test met mocked httpx: happy + timeout + 401 + 5xx |
| D3 | REFACTOR `_get_guardrails()` naar nieuwe schema (`{detectors, instructions}`) | Unit test met gemockte portal-api response |
| D4 | REFACTOR pre-call flow naar `asyncio.gather(_call_pii_service, _call_retrieval)` met `return_exceptions=True` | Integration-test: beide calls happen parallel (meet via timing of logging) |
| D5 | Implement block-action (raise herkenbare exception met user-facing NL message) | Unit test: block-rule → exception message `"Bericht geblokkeerd door guardrail: ..."` |
| D6 | Implement redact-action (replace user message via `_replace_last_user_message` met redacted_text van `/v1/redact`) | Unit test: `"Mijn IBAN is NL91 ABNA 0417 1643 00"` + IBAN_CODE redact rule → user-message `"Mijn IBAN is [IBAN_CODE]"` |
| D7 | Implement instructions-injection in system prompt (templates → KB-context → existing system) | Unit test: system message volgorde correct |
| D8 | Implement fail-open (klai-pii timeout/5xx → `guardrails_degraded` log + continue) | Integration test met klai-pii offline: chat werkt, log bevat `guardrails_degraded` |
| D9 | Implement PII-scrubbing in logs (`guardrail_applied` log bevat GEEN raw matched text) | Unit test: log-parsing assertion |

Dependencies: D1 eerst; D2-D3 parallel; D4 na D2+D3; D5-D9 na D4.

### Fase E — Smoke + docs

| # | Task | Acceptance hook |
|---|---|---|
| E1 | End-to-end smoke: `docker compose up` → provision test-org (krijgt defaults) → activate template → chat met PII-trigger → expect redaction + template-instructie in system prompt | Manueel + gescripted via `scripts/smoke-guardrails.sh` |
| E2 | Update `docs/architecture/platform.md` Rules + Templates sectie matcht code | `grep "detector_entities" docs/` findt de juiste uitleg |
| E3 | Update `docs/architecture/knowledge-retrieval-flow.md` diagram bevat parallel-analyze stap | Visuele review |

## Dependency Graph

```
A1 ─┬─ A2 ── A3 ── A5 ──┐
    ├─ A4 ──────────────┤
    └─ A7               ├─ A8 ─────────────────┐
                        │                      │
B1 ── B2 ─┐             │                      │
      ├── B3 ─── B6 ──┐ │                      │
      ├── B4 ─────────┤ │                      │
      └── B5 ─────────┤ │                      │
                      ▼ ▼                      │
         C1 ── C5 ── C10                       │
         C2+C3 ── C9                           │
         C4 ── C5/C6/C7                        │
         C6 ── C10                             │
         C7                                    │
         C8                                    │
                     │                         │
                     ▼                         ▼
                    D1 ─ D2/D3 ─ D4 ─ D5/D6/D7/D8/D9
                                      │
                                      ▼
                                     E1 ─ E2 ─ E3
```

Parallel-windows:
- A-fase taken (A1-A7) kunnen parallel met B-fase taken (B1-B6) lopen — onafhankelijke codebases.
- Binnen A: A2 en A4 parallel na A1.
- Binnen B: B2/B3/B4/B5 parallel na B1.
- Binnen C: C5 en C6 kunnen parallel zodra C1 en C4 klaar zijn.

## Risks + Mitigations

1. **GLiNER model-size in RAM (~900MB-1GB RSS)** — aparte service, `mem_limit: 1500M`, core-01 heeft >64GB. Mitigatie: monitor `klai-pii` container memory in Grafana na deploy.
2. **Cold-start ~90s bij container-restart** — fail-open in hook betekent chats blijven werken tijdens restart. Healthcheck `start_period: 90s`. Mitigatie: rolling-deploy strategie bij prod-deploy; hook fail-open dekt de gap.
3. **Alembic 6 heads in main** — één merge-migratie als eerste. Bestaand patroon in `aa7531c292e4_merge_dev_heads.py`. Mitigatie: B1 is de eerste migratie; lokaal valideren met `alembic upgrade head` op schone DB.
4. **GLiNER `PERSON` false positives op NL** — score-threshold `>=0.5`, gebruikers kiezen zelf welke entities. Mitigatie: default-rules bevatten GEEN `PERSON`-detectie (alleen EMAIL, BSN, IBAN, CREDIT_CARD); gebruiker moet expliciet een rule aanmaken voor PERSON-detectie.
5. **Race bij cache-invalidate** — in-flight chat-call kan stale cache gebruiken. Mitigatie: 30s TTL fallback; gedocumenteerd in acceptance.md dat dit acceptabel is.
6. **Extra latency door parallel analyze** — warme GLiNER ~200-300ms; retrieval is meestal ~300-500ms. Parallel gather betekent netto geen extra latency op de kritische pad. Mitigatie: meten in E1 smoke, fallback naar sequential als parallel te traag (onwaarschijnlijk).
7. **Dependency pinning drift** — Presidio + spaCy + GLiNER hebben elkaar's model-APIs al eens gebroken. Mitigatie: exact-pin in `pyproject.toml`, gelockte `uv.lock`, CI-build test bij elke PR.
8. **Cross-tenant leak via `active_template_ids`** — user kan een template-ID van een andere org proberen op te slaan. Mitigatie: C7 valideert template-IDs tegen `PortalTemplate.org_id == caller.org.id` → HTTP 400.
9. **Log-leak van PII** — `guardrail_applied` log per ongeluk raw text loggen. Mitigatie: D9 expliciete unit test die checkt dat log GEEN raw text bevat; code review focus.
10. **`/internal/guardrails/effective` zonder zitadel-mapping** — als `librechat_user_id` → `zitadel_user_id` mapping ontbreekt, moet fail-safe zijn (empty guardrails) niet 404. Mitigatie: C8 covert expliciet deze branch.

## MX Tag Plan

### @MX:ANCHOR (high fan_in, invariant contract)

- `klai-pii/klai_pii/services/analyzer.py:Analyzer.__init__` — model-singleton (geïnitialiseerd in lifespan, gebruikt door analyze() + redact()). Fan_in vanuit alle API-endpoints.
- `klai-pii/klai_pii/services/analyzer.py:Analyzer.analyze` — gecontracteerde entity-output shape, consumers: `/v1/analyze`, intern vanuit `/v1/redact`.
- `klai-portal/backend/app/services/litellm_cache.py:invalidate_guardrails` — aangeroepen vanuit 4 write-paden (rules CRUD, templates CRUD, app_account `active_template_ids`, intern vanuit provisioning-errors). Signature is contract.

### @MX:WARN (danger zone, requires @MX:REASON)

- `deploy/litellm/klai_knowledge.py:_call_pii_service` — fail-open tak. @MX:REASON: "klai-pii is mag niet de chat blokkeren bij infrastructure-issue; degradatie gelogd als `guardrails_degraded` voor observability."
- `klai-portal/backend/app/api/internal.py:/internal/guardrails/effective` librechat→zitadel mapping fail-safe. @MX:REASON: "ontbrekende mapping mag chat niet stoppen; returnt empty guardrails conform fail-open beleid."
- `klai-portal/backend/app/services/provisioning/orchestrator.py:step_6b_defaults_guardrails` — non-fatal try/except. @MX:REASON: "guardrail-defaults zijn nice-to-have; provisioning van de org is primair."

### @MX:NOTE (context / intent)

- `klai-pii/klai_pii/services/nl_recognizers.py:validate_bsn_11_proof` — beschrijving van het 11-proef algoritme (9×c1 + 8×c2 + ... + 2×c8 + -1×c9, mod 11 == 0, length 8 of 9).
- `klai-pii/klai_pii/main.py:lifespan` — noteer: GLiNER+spaCy preload kost ~60-90s, `models_loaded` flag guard.
- `klai-portal/backend/alembic/versions/add_portal_rules.py` — noteer: `detector_entities` is expliciet NOT NULL met default `'{}'`, NIET nullable. Slug-based detection is bewust NIET gebruikt (stille faalmodus).
- `klai-portal/backend/app/services/provisioning/orchestrator.py:step_6b` — noteer: idempotent via `defaults_seeded_at` timestamp + `ensure_default_*` own idempotency.

### @MX:TODO (incomplete work resolved in GREEN)

- Frontend routes (`/app/rules`, `/app/templates`, chat config bar) → @MX:TODO refereert naar SPEC-CHAT-GUARDRAILS-002.
- Transcript-pseudonimisatie via klai-pii → @MX:TODO refereert naar later aparte SPEC.

## Validation Gates (per phase)

- **Fase A klaar**: `pytest klai-pii/` groen + `docker compose up klai-pii` healthy binnen 90s + handmatige `curl` voor analyze+redact.
- **Fase B klaar**: `alembic heads` → 1 head + `alembic upgrade head` in dev zonder errors + `pytest tests/test_models.py` groen.
- **Fase C klaar**: alle CRUD pytest suites groen + Redis-keys worden geïnvalideerd (observeerbaar via Redis MONITOR in test) + provisioning test maakt 4+4 defaults.
- **Fase D klaar**: hook-pytest suite groen + end-to-end smoke in docker-compose succesvol (redact + block + fail-open + instructions-injection alle verifieerbaar).
- **Fase E klaar**: smoke-script slaagt + `grep "detector_entities\|guardrail" docs/architecture/` matcht actuele code.

## Exclusions (reminder)

Zie `spec.md#Exclusions` — deze plan-taken dekken alléén backend (klai-pii + portal-api + hook). Geen frontend, geen transcript-integratie, geen audit-log-pipeline, geen marketplace.
