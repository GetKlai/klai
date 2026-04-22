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

# SPEC-CHAT-GUARDRAILS-001 — Rules + Templates backend met Presidio/GLiNER PII-service

## HISTORY

### v0.1.0 (2026-04-22) — Mark Vletter
- Eerste draft. Drie-laags architectuur: nieuwe `klai-pii` microservice met Presidio + GLiNER, uitbreiding `klai-portal/backend` met Rules + Templates CRUD + `/internal/guardrails/effective`, en refactor van `deploy/litellm/klai_knowledge.py` naar de PII-service (geen inline regex meer).
- Bewust geschrapt t.o.v. `origin/feat/chat-first-redesign`: `rule_text` kolom, `"instruction"` rule_type, `"global"` scope-naam. Rules zijn strict guardrails; instructies komen alleen uit Templates.
- `detector_entities` + `keywords` zijn expliciete TEXT[] kolommen met WHITELIST-validatie; geen slug-based detector-afleiding.

## Overview

### Waarom

De `origin/feat/chat-first-redesign` branch (Jantine) bouwt een Rules + Templates laag voor chat-guardrails, maar heeft twee problemen:

1. **Stille faalmodus in PII-detectie** — detectors worden afgeleid uit een slug-match op de rule-naam. Een custom rule met een willekeurige naam (bijv. "E-mails filteren") activeert niks. Gebruikers krijgen een "actieve" rule die niets doet.
2. **Zelfgemaakte regex voor NL PII** — BSN, IBAN, telefoon en e-mail via inline regex in de LiteLLM hook, terwijl onze eigen architectuur (`docs/architecture/klai-knowledge-architecture.md:1554`, `docs/research/knowledge-pipeline-architecture.md:333-410`) Presidio + GLiNER (`urchade/gliner_multi-v2.1`) al voorschrijft voor NL PII-detectie.

Tegelijkertijd ligt het product-ontwerp voor Rules + Templates al vast in `docs/architecture/platform.md:30-31, 59-65` en `docs/architecture/knowledge-retrieval-flow.md:486-493`, maar de code leeft alleen in die branch. Deze SPEC bouwt wat al ontworpen is — schoon.

### Wat

Drie lagen:

1. **Laag 1 — `klai-pii` microservice (NIEUW).** Presidio Analyzer + GLiNER (NL+EN) achter een bearer-authenticated HTTP API. Entry points: `POST /v1/analyze`, `POST /v1/redact`, `GET /health`, `GET /metrics`. Intern (`klai-net` only), geen Caddy exposure.
2. **Laag 2 — `klai-portal/backend` uitbreiding.** `PortalRule` + `PortalTemplate` modellen met RLS (strict), CRUD endpoints (`/api/app/rules`, `/api/app/templates`) met rate-limiting, `/internal/guardrails/effective` endpoint voor de LiteLLM hook, default-seeders in het provisioning-orchestrator, en cache-invalidation helper.
3. **Laag 3 — LiteLLM hook refactor.** Alle inline regex eruit. Hook roept `klai-pii` parallel naast retrieval-api aan, past block/redact rules toe op user-message, en injecteert template-instructies in de system prompt.

### Hoe (hoofdlijn)

- **Eén bron van waarheid voor detectie** — klai-pii handelt zowel Presidio-entities (`EMAIL_ADDRESS`, `PHONE_NUMBER`, `IBAN_CODE`, `CREDIT_CARD`, `NL_BSN` custom) als GLiNER zero-shot labels (`PERSON`, `LOCATION`, `ORGANIZATION`) én keyword-matching af. Portal-api stuurt `entities` + `keywords` mee in één request.
- **Expliciete detector-configuratie** — `portal_rules.detector_entities TEXT[]` en `portal_rules.keywords TEXT[]` als NOT NULL kolommen. Geen slug-magic. Portal-api valideert tegen een gehardcodeerde WHITELIST voor rules worden opgeslagen.
- **Fail-open op detectie-errors** — klai-pii timeout of 5xx → chat gaat door zonder guardrails, `guardrails_degraded` log entry. Conform het bestaande fail-open beleid bij retrieval-api-errors.
- **Parallel met retrieval** — hook doet `asyncio.gather(analyze, retrieve)` zodat de analyze-roundtrip niet serieel op de retrieve-roundtrip zit.

### Alternatieven overwogen (en verworpen)

- **Branch mergen met patch** — laat de stille faalmodus (slug-based detector afleiding) in productie zitten. Afgewezen: architectureel vastgelegd pad (Presidio+GLiNER) is er om precies dit soort fragility te voorkomen.
- **Presidio in-proc in portal-api** — spaart een container, maar spaCy + GLiNER kosten ~900MB RSS en koppelen model-lifecycle aan portal-api deploys. Afgewezen: klai-pii als aparte service is schaalbaar en deploy-onafhankelijk.
- **Alleen regex** — geen GLiNER, alleen Presidio built-ins + custom NL patterns. Afgewezen: `PERSON` en `LOCATION` detectie op NL tekst is praktisch niet haalbaar zonder transformer-model; architectuurdocs schrijven GLiNER voor.

## Requirements

### REQ-PII-SERVICE — klai-pii microservice

**Ubiquitous**
- The klai-pii service **shall** expose `POST /v1/analyze`, `POST /v1/redact`, `GET /health`, and `GET /metrics` endpoints.
- The klai-pii service **shall** preload the spaCy `nl_core_news_sm` model and the GLiNER `urchade/gliner_multi-v2.1` model during FastAPI lifespan startup.
- The klai-pii service **shall** register a custom `NL_BSN` PatternRecognizer with 11-proof validation and an NL phone-number recognizer override.

**State-Driven**
- **While** the service is starting up and `models_loaded=False`, the `GET /health` endpoint **shall** return HTTP 200 with `{"status": "starting", "models_loaded": false}`.
- **While** the service has completed model loading, the `GET /health` endpoint **shall** return HTTP 200 with `{"status": "ok", "models_loaded": true, "language_support": ["nl", "en"]}`.

**Event-Driven**
- **When** the service receives `POST /v1/analyze` with text containing a Dutch person name and `language="nl"` and `entities` including `"PERSON"`, the service **shall** return at least one hit with `entity_type="PERSON"` and `score >= 0.5`.
- **When** the service receives `POST /v1/analyze` with text `"Mijn BSN is 123456782"` and `entities=["NL_BSN"]`, the service **shall** return a hit with `entity_type="NL_BSN"`.
- **When** the service receives `POST /v1/analyze` with text containing an invalid Dutch BSN (failing the 11-proof check) and `entities=["NL_BSN"]`, the service **shall NOT** return any `NL_BSN` hit.
- **When** the service receives `POST /v1/redact` with `mode="placeholder"` and detected entities, the service **shall** return `redacted_text` containing `[ENTITY_TYPE]` substitutions at the detected offsets.
- **When** the service receives `POST /v1/redact` with `keywords=["secret"]` and text containing `"secret"` (case-insensitive substring match), the service **shall** return `applied` containing `{"keyword": "secret", "action": "redact"}`.

**Unwanted Behavior**
- **If** a request to `/v1/analyze` or `/v1/redact` lacks a valid `Authorization: Bearer $PII_INTERNAL_SECRET` header, **then** the service **shall** respond HTTP 401 without processing the request body.
- **If** a request body contains `entities` values outside the supported whitelist (`EMAIL_ADDRESS`, `PHONE_NUMBER`, `IBAN_CODE`, `CREDIT_CARD`, `NL_BSN`, `PERSON`, `LOCATION`, `ORGANIZATION`), **then** the service **shall** respond HTTP 400 with the list of unsupported entities.
- **If** GLiNER inference raises an exception, **then** the service **shall** return HTTP 500 with a generic error body and log the exception with `request_id`.

**Optional**
- **Where** `GET /metrics` is available, the service **shall** expose Prometheus counters/histograms including `analyze_duration_seconds`, `redact_duration_seconds`, and `entity_hits_total{entity_type}`.

### REQ-PORTAL-CRUD — Rules + Templates API

**Ubiquitous**
- The portal-api **shall** expose CRUD endpoints for rules at `/api/app/rules` and `/api/app/rules/{slug}`.
- The portal-api **shall** expose CRUD endpoints for templates at `/api/app/templates` and `/api/app/templates/{slug}`.
- The portal-api **shall** expose `GET /internal/guardrails/effective?zitadel_org_id=...&librechat_user_id=...` for internal service-to-service use.
- The portal-api **shall** validate `detector_entities` against a hardcoded WHITELIST on rule-creation and rule-update.
- The portal-api **shall** validate `rule_type` against the set `{pii_block, pii_redact, keyword_block, keyword_redact}`.

**Event-Driven**
- **When** a user POSTs a rule with `detector_entities` containing a value outside the WHITELIST, the endpoint **shall** respond HTTP 400.
- **When** a user POSTs a template with `prompt_text` exceeding 8000 characters, the endpoint **shall** respond HTTP 400 referencing the CHECK constraint.
- **When** a user PATCHes a rule they did not create and they are not an org admin, the endpoint **shall** respond HTTP 403.
- **When** a user sends more than 10 CRUD requests per second per org, the endpoint **shall** respond HTTP 429 with a `Retry-After` header.
- **When** a new tenant is provisioned, `portal_rules` **shall** contain 4 default entries AND `portal_templates` **shall** contain 4 default entries AND `portal_orgs.defaults_seeded_at` **shall** be set.
- **When** a user PATCHes `active_template_ids` on their own account including an ID belonging to another org, the endpoint **shall** respond HTTP 400.
- **When** `/internal/guardrails/effective` is called for a user without an active rule or template, the endpoint **shall** return `{"detectors": [], "instructions": []}` with HTTP 200.

**State-Driven**
- **While** a personal-scope rule exists that the requesting user did not create, `GET /api/app/rules` **shall NOT** include that rule.
- **While** RLS is active on `portal_rules` and `portal_templates`, a SELECT without `app.current_org_id` set **shall** return zero rows.

**Unwanted Behavior**
- **If** `detectors_entities` or `keywords` is NULL in a DB row, **then** the migration **shall** have failed. Default is `'{}'::text[]`.
- **If** the provisioning step 6b (default guardrails seeding) raises an exception, **then** provisioning **shall** log a warning and continue (non-fatal).

### REQ-CACHE — Cache invalidation

**Event-Driven**
- **When** a user POSTs, PATCHes, or DELETEs a rule or template, the Redis cache key pattern `guardrails:{org_id}:{librechat_user_id}` **shall** be deleted within 100 ms (SCAN+DEL for org-wide changes, single DEL for user-specific changes).
- **When** a user PATCHes `active_template_ids` on their own account, the Redis key `guardrails:{org_id}:{librechat_user_id}` for that user **shall** be deleted within 100 ms.
- **When** cache DEL fails (Redis unreachable), the next chat-call within 30 s **shall** still pick up the fresh state via the TTL fallback on the cache entry.

**Unwanted Behavior**
- **If** `invalidate_guardrails` raises an exception, **then** the write endpoint **shall** log a warning and still return HTTP 200 to the user (cache invalidation is fire-and-forget).

### REQ-HOOK — LiteLLM guardrail flow

**Ubiquitous**
- The LiteLLM pre-call hook **shall NOT** contain any inline PII regex patterns.
- The LiteLLM pre-call hook **shall** call `klai-pii /v1/analyze` in parallel with `retrieval-api /retrieve` whenever any active detector (entities or keywords) exists for the calling org/user.

**Event-Driven**
- **When** the user-message matches a `pii_redact` or `keyword_redact` rule, the message **shall** be replaced with a redacted version before the retrieval-api result is merged into the system prompt.
- **When** the user-message matches a `pii_block` or `keyword_block` rule, the chat-call **shall** be aborted with a user-facing NL error `"Bericht geblokkeerd door guardrail: {rule_name}"`.
- **When** the user has `active_template_ids` set on their PortalUser, the `prompt_text` of each active template **shall** appear in the system message before the KB-context block.
- **When** a complete flow (rule + template + KB) executes, the system message **shall** contain in order: template-instructions-block → KB-context-block → pre-existing system content.

**State-Driven**
- **While** no active rules AND no active templates exist for an org/user pair, the hook **shall** make zero extra round-trips to `klai-pii`.

**Unwanted Behavior**
- **If** `klai-pii` times out or returns 5xx, **then** the hook **shall** log `guardrails_degraded` (structlog, level=warning, including `org_id` and `user_id`) and continue the chat-call without applying any guardrail.
- **If** `klai-pii` returns HTTP 401 (bad internal secret), **then** the hook **shall** log `guardrails_config_error` at level=error and fail-open (chat continues).
- **If** the LiteLLM hook logs a guardrail hit, **then** the log entry **shall NOT** contain the raw matched PII text, only `rule_name`, `action`, `entity_type_or_keyword_name`, `org_id`, `user_id`.

## Files to Modify / Create

### NEW — klai-pii microservice

- `klai-pii/Dockerfile` — multi-stage build, spaCy + GLiNER pre-download in build stage
- `klai-pii/pyproject.toml` — deps: `fastapi`, `pydantic-settings`, `presidio-analyzer==2.2.358`, `presidio-anonymizer==2.2.358`, `gliner>=0.2.0`, `spacy==3.7.*`, `structlog`, `httpx`, `prometheus-client`
- `klai-pii/uv.lock` — gelockte versies
- `klai-pii/scripts/download_models.py` — pre-download spaCy + GLiNER (build-time)
- `klai-pii/klai_pii/__init__.py`
- `klai-pii/klai_pii/main.py` — FastAPI app + `lifespan` met model-preload + RequestContextMiddleware
- `klai-pii/klai_pii/config.py` — `Settings` (PII_INTERNAL_SECRET, SPACY_MODEL, GLINER_MODEL, LOG_LEVEL)
- `klai-pii/klai_pii/logging_setup.py` — structlog config + `setup_logging("klai-pii")`
- `klai-pii/klai_pii/middleware/__init__.py`
- `klai-pii/klai_pii/middleware/auth.py` — bearer-secret middleware met `hmac.compare_digest` (skip `/health`, `/metrics`)
- `klai-pii/klai_pii/api/__init__.py`
- `klai-pii/klai_pii/api/analyze.py` — `POST /v1/analyze` router
- `klai-pii/klai_pii/api/redact.py` — `POST /v1/redact` router
- `klai-pii/klai_pii/api/health.py` — `GET /health` router
- `klai-pii/klai_pii/services/__init__.py`
- `klai-pii/klai_pii/services/analyzer.py` — Presidio AnalyzerEngine wrapper, GLiNER als NlpEngine geregistreerd, analyze()/redact() methods
- `klai-pii/klai_pii/services/nl_recognizers.py` — NL_BSN PatternRecognizer met 11-proof validator, NL_PHONE override
- `klai-pii/klai_pii/services/keyword_matcher.py` — case-insensitive substring matcher, returns offsets
- `klai-pii/tests/__init__.py`
- `klai-pii/tests/conftest.py` — pytest fixtures (FastAPI TestClient, mock auth)
- `klai-pii/tests/test_analyze.py` — covers PERSON/EMAIL/BSN/IBAN/CREDIT_CARD + 401/400
- `klai-pii/tests/test_redact.py` — covers placeholder/hash mode, keyword mode
- `klai-pii/tests/test_nl_recognizers.py` — 11-proof happy/sad path

### MODIFY — deploy/

- `deploy/docker-compose.yml` — ADD service `klai-pii` (build: `../klai-pii`, networks: `klai-net` only, mem_limit: 1500M, healthcheck start_period: 90s)
- `deploy/.env.example` — ADD `PII_INTERNAL_SECRET=...`
- `deploy/litellm/klai_knowledge.py` — REMOVE all inline `_PII_PATTERNS` + regex application code; ADD `_call_pii_service()` helper that calls `/v1/analyze` and `/v1/redact`; REFACTOR pre-call flow to `asyncio.gather(analyze, retrieve)`; REFACTOR `_get_guardrails()` to consume the new `{detectors, instructions}` schema; ADD `PII_SERVICE_URL`, `PII_INTERNAL_SECRET`, `PII_TIMEOUT` env vars

### MODIFY — klai-portal/backend

- `klai-portal/backend/alembic/versions/m1g2r3h4i5j6_merge_main_heads_before_guardrails.py` — NEW, merges 6 current heads (`c160d2b9d885`, `a2b3c4d5e6f7`, `b4c5d6e7f8g9`, `b5c6d7e8f9a0`, `c4d5e6f7a8b9`, `32fc0ed3581b`)
- `klai-portal/backend/alembic/versions/add_portal_templates.py` — NEW, creates `portal_templates` (id, org_id FK, name, slug, description, prompt_text CHECK<=8000, scope, created_by, is_active, timestamps, RLS strict)
- `klai-portal/backend/alembic/versions/add_portal_rules.py` — NEW, creates `portal_rules` (id, org_id FK, name, slug, description CHECK<=500, rule_type, detector_entities TEXT[], keywords TEXT[], scope, created_by, is_active, timestamps, RLS strict)
- `klai-portal/backend/alembic/versions/add_active_template_ids_to_portal_users.py` — NEW, adds `active_template_ids INTEGER[] NULL` to `portal_users`
- `klai-portal/backend/alembic/versions/add_portal_org_defaults_seeded_at.py` — NEW, adds `defaults_seeded_at TIMESTAMPTZ NULL` to `portal_orgs`
- `klai-portal/backend/app/models/rules.py` — NEW, `PortalRule` SQLAlchemy model
- `klai-portal/backend/app/models/templates.py` — NEW, `PortalTemplate` SQLAlchemy model
- `klai-portal/backend/app/models/portal.py` — MODIFY `PortalUser` to add `active_template_ids: Mapped[list[int] | None]`; MODIFY `PortalOrg` to add `defaults_seeded_at: Mapped[datetime | None]`
- `klai-portal/backend/app/utils/slug.py` — NEW, shared `slugify(name: str) -> str` helper
- `klai-portal/backend/app/services/default_rules.py` — NEW, `ensure_default_rules(org_id, created_by, db)` idempotent seeder
- `klai-portal/backend/app/services/default_templates.py` — NEW, `ensure_default_templates(org_id, created_by, db)` idempotent seeder
- `klai-portal/backend/app/services/litellm_cache.py` — NEW, `invalidate_guardrails(org_id, librechat_user_id=None)` helper (pattern DEL for None, single DEL otherwise)
- `klai-portal/backend/app/services/provisioning/orchestrator.py` — ADD step 6b `defaults_guardrails` (non-fatal) calling `ensure_default_rules` + `ensure_default_templates`, then set `portal_orgs.defaults_seeded_at`
- `klai-portal/backend/app/api/app_rules.py` — NEW, CRUD router with WHITELIST validation + `partner_rate_limit` (10 req/s per org) + `invalidate_guardrails` calls
- `klai-portal/backend/app/api/app_templates.py` — NEW, CRUD router with `prompt_text` length validation + rate-limit + `invalidate_guardrails` calls
- `klai-portal/backend/app/api/app_account.py` — MODIFY KB-preference endpoint to also accept `active_template_ids: list[int] | None` with org-scoped validation; call `invalidate_guardrails(org_id, librechat_user_id)` on change
- `klai-portal/backend/app/api/internal.py` — ADD `GET /internal/guardrails/effective` endpoint returning `{detectors: [...], instructions: [...]}` with inline `librechat_user_id` → `zitadel_user_id` mapping (fail-safe: missing mapping returns empty guardrails)
- `klai-portal/backend/app/main.py` — ADD `include_router` calls for `app_rules_router` and `app_templates_router`
- `klai-portal/backend/tests/test_app_rules.py` — NEW, covers CRUD + 403/400/429 + RLS
- `klai-portal/backend/tests/test_app_templates.py` — NEW, idem
- `klai-portal/backend/tests/test_internal_guardrails.py` — NEW, covers `/internal/guardrails/effective`
- `klai-portal/backend/tests/test_default_rules_templates.py` — NEW, covers idempotency + provisioning step
- `klai-portal/backend/tests/test_litellm_cache.py` — NEW, covers `invalidate_guardrails`

### ADD — documentation

- `docs/architecture/platform.md` — small update bij Rules + Templates sectie naar de feitelijke implementatie + verwijzing naar deze SPEC
- `docs/architecture/knowledge-retrieval-flow.md` — diagram bij guardrail-injection updaten met de parallel-analyze stap

## Exclusions (What NOT to Build)

- **Frontend UI** (`/app/rules`, `/app/templates` routes, chat config bar, template picker) — vervolg-SPEC `SPEC-CHAT-GUARDRAILS-002`.
- **Transcript-pseudonimisatie** (audio/meetings scribe-integratie met dezelfde klai-pii service) — aparte SPEC.
- **Cross-tenant shared rules/templates marketplace** — niet in v1.
- **Audit-log van guardrail-hits naar Grafana/product_events** — niet in v1 (alleen structlog entry).
- **Alle overige wijzigingen uit `feat/chat-first-redesign`** — SPEC-PROV-001 deletions, klai-libs deletions, RLS-test deletions. Wij nemen NIKS weg.
- **`rule_text` kolom** — bewust geschrapt; detectie is expliciet via `detector_entities` + `keywords`.
- **`"instruction"` rule_type** — bewust geschrapt; rules zijn strict guardrails. Instructies komen uit Templates.
- **`"global"` scope naam** — we gebruiken `"org"` en `"personal"` om dubbelzinnigheid met cross-tenant te voorkomen.
- **Per-KB template-scoping** (`template.kb_id`) — v1 is alleen `org` / `personal`; KB-scoping later.
- **Alle niet-whitelisted Presidio entities** (`US_SSN`, `MEDICAL_LICENSE`, `DATE_TIME`, etc.) — klai-pii responded 400. Uitbreiding in latere SPEC.

## References

### Architectuur / ontwerp
- `docs/architecture/klai-knowledge-architecture.md:1554` — Presidio + GLiNER keuze
- `docs/research/knowledge-pipeline-architecture.md:333-410` — technische onderbouwing NL PII
- `docs/architecture/platform.md:30-31, 59-65` — Rules + Templates productontwerp
- `docs/architecture/knowledge-retrieval-flow.md:486-493` — guardrail-injection flow

### Bron-implementatie (API-shape, niet regex-PII)
- `origin/feat/chat-first-redesign` — CRUD shape, default seed copy (NL prompt_text Klantenservice/Formeel/Creatief/Samenvatter)

### Patterns in main
- `klai-retrieval-api/` — reference voor microservice-pattern: `Dockerfile`, `main.py` lifespan, `retrieval_api/middleware/auth.py`
- `klai-portal/backend/alembic/versions/1b8736eb6455_add_rls_phase2_user_tables.py` — RLS strict pattern
- `klai-portal/backend/alembic/versions/aa7531c292e4_merge_dev_heads.py` — alembic merge-heads pattern
- `klai-portal/backend/app/services/partner_rate_limit.py` — Redis sliding-window rate-limit pattern
- `klai-portal/backend/app/services/default_knowledge_bases.py` — default-seeder pattern (idempotent, provisioning integration)
- `klai-portal/backend/app/api/app_account.py` — bestaand Redis cache-invalidate pattern (`_invalidate_litellm_kb_cache`)
- `klai-portal/backend/app/services/provisioning/orchestrator.py` — bestaande step 6 (`ensure_default_knowledge_bases`) als model voor step 6b

### Process rules
- `.claude/rules/klai/pitfalls/process-rules.md` — `adapter-framework-bleed`, `search-broadly-when-changing`, `spec-discipline`, `data-before-code`
- `.claude/rules/klai/infra/observability.md` — structlog patterns, `RequestContextMiddleware`

### Config
- `.moai/config/sections/language.yaml` — NL user-facing, EN code/docs
- `.claude/rules/klai/no-ask-user-question.md` — no AskUserQuestion
