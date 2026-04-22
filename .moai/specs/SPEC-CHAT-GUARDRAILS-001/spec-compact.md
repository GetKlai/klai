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

# SPEC-CHAT-GUARDRAILS-001 — Compact

Auto-generated from spec.md + acceptance.md. Used by `/moai run` to save ~30% tokens vs full spec.md.
For full context (Overview, Approach, Alternatives, References, History) see `spec.md`.

---

## Requirements (EARS)

### REQ-PII-SERVICE — klai-pii microservice

**Ubiquitous**
- The klai-pii service **shall** expose `POST /v1/analyze`, `POST /v1/redact`, `GET /health`, and `GET /metrics` endpoints.
- The klai-pii service **shall** preload the spaCy `nl_core_news_sm` model and the GLiNER `urchade/gliner_multi-v2.1` model during FastAPI lifespan startup.
- The klai-pii service **shall** register a custom `NL_BSN` PatternRecognizer with 11-proof validation and an NL phone-number recognizer override.

**State-Driven**
- **While** the service is starting up and `models_loaded=False`, the `GET /health` endpoint **shall** return HTTP 200 with `{"status": "starting", "models_loaded": false}`.
- **While** the service has completed model loading, the `GET /health` endpoint **shall** return HTTP 200 with `{"status": "ok", "models_loaded": true, "language_support": ["nl", "en"]}`.

**Event-Driven**
- **When** `POST /v1/analyze` receives text containing a Dutch person name with `language="nl"` and `entities` including `"PERSON"`, the service **shall** return at least one hit with `entity_type="PERSON"` and `score >= 0.5`.
- **When** `POST /v1/analyze` receives `"Mijn BSN is 123456782"` with `entities=["NL_BSN"]`, the service **shall** return a hit with `entity_type="NL_BSN"`.
- **When** text contains an invalid BSN (failing 11-proof) and `entities=["NL_BSN"]`, the service **shall NOT** return any `NL_BSN` hit.
- **When** `POST /v1/redact` with `mode="placeholder"` detects entities, `redacted_text` **shall** contain `[ENTITY_TYPE]` substitutions at detected offsets.
- **When** `POST /v1/redact` receives `keywords=["secret"]` and text contains `"secret"` (case-insensitive), `applied` **shall** contain `{"keyword": "secret", "action": "redact"}`.

**Unwanted Behavior**
- **If** a request lacks a valid `Authorization: Bearer $PII_INTERNAL_SECRET` header, the service **shall** respond HTTP 401 without processing the body.
- **If** `entities` contains a value outside the WHITELIST (`EMAIL_ADDRESS`, `PHONE_NUMBER`, `IBAN_CODE`, `CREDIT_CARD`, `NL_BSN`, `PERSON`, `LOCATION`, `ORGANIZATION`), the service **shall** respond HTTP 400 with the unsupported list.
- **If** GLiNER inference raises, the service **shall** return HTTP 500 with a generic error body and log with `request_id`.

**Optional**
- **Where** `GET /metrics` is available, the service **shall** expose Prometheus counters/histograms including `analyze_duration_seconds`, `redact_duration_seconds`, and `entity_hits_total{entity_type}`.

### REQ-PORTAL-CRUD — Rules + Templates API

**Ubiquitous**
- The portal-api **shall** expose CRUD endpoints for rules at `/api/app/rules` and `/api/app/rules/{slug}`.
- The portal-api **shall** expose CRUD endpoints for templates at `/api/app/templates` and `/api/app/templates/{slug}`.
- The portal-api **shall** expose `GET /internal/guardrails/effective?zitadel_org_id=...&librechat_user_id=...`.
- The portal-api **shall** validate `detector_entities` against a hardcoded WHITELIST on rule-creation and rule-update.
- The portal-api **shall** validate `rule_type` against `{pii_block, pii_redact, keyword_block, keyword_redact}`.

**Event-Driven**
- **When** a user POSTs a rule with `detector_entities` outside the WHITELIST, respond HTTP 400.
- **When** a user POSTs a template with `prompt_text > 8000` chars, respond HTTP 400 referencing the CHECK constraint.
- **When** a user PATCHes a rule they did not create and is not admin, respond HTTP 403.
- **When** CRUD request count exceeds 10 req/sec per org, respond HTTP 429 with `Retry-After`.
- **When** a new tenant is provisioned, `portal_rules` **shall** contain 4 defaults AND `portal_templates` **shall** contain 4 defaults AND `portal_orgs.defaults_seeded_at` **shall** be set.
- **When** a user PATCHes `active_template_ids` including an ID from another org, respond HTTP 400.
- **When** `/internal/guardrails/effective` is called for a user with no active rule/template, return `{"detectors": [], "instructions": []}` HTTP 200.

**State-Driven**
- **While** a personal-scope rule exists that the requesting user did not create, `GET /api/app/rules` **shall NOT** include it.
- **While** RLS is active on `portal_rules`/`portal_templates`, a SELECT without `app.current_org_id` **shall** return zero rows.

**Unwanted Behavior**
- **If** `detector_entities` or `keywords` is NULL in a DB row, the migration **shall** have failed. Default is `'{}'::text[]`.
- **If** provisioning step 6b (default seeding) raises, the orchestrator **shall** log a warning and continue (non-fatal).

### REQ-CACHE — Cache invalidation

**Event-Driven**
- **When** a user POSTs/PATCHes/DELETEs a rule or template, the Redis key pattern `guardrails:{org_id}:{librechat_user_id}` (SCAN+DEL org-wide, single DEL user-specific) **shall** be deleted within 100ms.
- **When** a user PATCHes `active_template_ids`, the key `guardrails:{org_id}:{librechat_user_id}` for that user **shall** be deleted within 100ms.
- **When** cache DEL fails, the next chat-call within 30s **shall** pick up fresh state via TTL fallback.

**Unwanted Behavior**
- **If** `invalidate_guardrails` raises, the write endpoint **shall** log a warning and still return HTTP 200 (fire-and-forget).

### REQ-HOOK — LiteLLM guardrail flow

**Ubiquitous**
- The LiteLLM pre-call hook **shall NOT** contain any inline PII regex patterns.
- The LiteLLM pre-call hook **shall** call `klai-pii /v1/analyze` in parallel with `retrieval-api /retrieve` when any active detector exists for the calling org/user.

**Event-Driven**
- **When** the user-message matches a `pii_redact`/`keyword_redact` rule, the message **shall** be replaced BEFORE the retrieval-api result is merged.
- **When** the user-message matches a `pii_block`/`keyword_block` rule, the chat-call **shall** be aborted with user-facing NL error `"Bericht geblokkeerd door guardrail: {rule_name}"`.
- **When** the user has `active_template_ids`, each active template's `prompt_text` **shall** appear in the system message before the KB-context block.
- **When** a complete flow (rule + template + KB) executes, the system message **shall** contain in order: template-instructions → KB-context → pre-existing system content.

**State-Driven**
- **While** no active rules AND no active templates exist for an org/user pair, the hook **shall** make zero extra round-trips to `klai-pii`.

**Unwanted Behavior**
- **If** `klai-pii` times out or returns 5xx, the hook **shall** log `guardrails_degraded` (structlog warning with `org_id`, `user_id`) and continue without guardrails.
- **If** `klai-pii` returns HTTP 401, the hook **shall** log `guardrails_config_error` (error level) and fail-open.
- **If** the hook logs a guardrail hit, the log entry **shall NOT** contain the raw matched PII text — only `rule_name`, `action`, `entity_type_or_keyword_name`, `org_id`, `user_id`.

---

## Acceptance Scenarios (Given-When-Then)

All scenarios must be demonstrated with observable evidence (HTTP response, DB row, log line, Redis state, user-message content).

### Layer 1 — klai-pii service
- **PII-01** NL_BSN detection met valide 11-proof — POST /v1/analyze met `"Mijn BSN is 123456782"` + `entities=["NL_BSN"]` → 200 met minimaal 1 hit entity_type=NL_BSN, start=11 end=20.
- **PII-02** NL_BSN rejection bij invalide 11-proof — `"Mijn BSN is 123456789"` → 200 met `hits == []`.
- **PII-03** GLiNER PERSON detection op NL tekst — `"Jan de Vries belde over zijn abonnement"` + entities=["PERSON"] → ≥1 hit entity_type=PERSON score≥0.5.
- **PII-04** Redact met placeholder mode — POST /v1/redact met EMAIL_ADDRESS hit → `redacted_text` bevat `[EMAIL_ADDRESS]` op offset.
- **PII-05** Keyword redaction (case-insensitive) — keywords=["secret"] + "This SECRET must not leak" → `applied` bevat `{keyword:"secret", action:"redact"}`.
- **PII-06** Auth failure — request zonder bearer → 401 zonder body-processing.
- **PII-07** Unsupported entity 400 — entities=["US_SSN"] → 400 met `unsupported: ["US_SSN"]`.
- **PII-08** Health endpoint gedurende startup — GET /health binnen 10s van start → 200 `{models_loaded: false}`. Na ≤90s → `models_loaded: true`.

### Layer 2 — Portal-api CRUD
- **CRUD-01** Happy-path rule aanmaken — POST /api/app/rules met whitelisted entities → 201 + DB-row present.
- **CRUD-02** Whitelist-afwijzing — detector_entities=["US_SSN"] → 400.
- **CRUD-03** Template prompt_text lengte-check — 8001 chars → 400 CHECK constraint detail.
- **CRUD-04** Authorization op PATCH — non-owner non-admin → 403.
- **CRUD-05** Rate-limit — >10 req/s per org → 429 + `Retry-After` header.
- **CRUD-06** Cross-org active_template_ids afgewezen — template.id van org B → 400.
- **CRUD-07** Personal-scope rule-isolatie — user A ziet user B's personal rule niet in GET list.
- **CRUD-08** RLS strict — SELECT zonder `app.current_org_id` → 0 rows.
- **CRUD-09** Defaults bij nieuwe org — na provisioning: 4 rules + 4 templates + `defaults_seeded_at` SET.
- **CRUD-10** Defaults idempotent — tweede call naar `ensure_default_*` voegt geen rows toe.
- **CRUD-11** /internal/guardrails/effective happy path — retourneert `{detectors: [...], instructions: [...]}` met correcte samenstelling.
- **CRUD-12** /internal/guardrails/effective lege state — user zonder rules/templates → `{detectors: [], instructions: []}`.
- **CRUD-13** /internal/guardrails/effective missing librechat mapping — ontbrekende mapping → returnt lege guardrails (fail-safe), NIET 404.

### Layer 3 — Cache invalidation
- **CACHE-01** POST rule invalideert user-cache — Redis key `guardrails:{org_id}:{user_id}` gedelete binnen 100ms na POST.
- **CACHE-02** active_template_ids invalideert alleen eigen user — andere users' keys blijven intact.
- **CACHE-03** Redis down — fail-open invalidate — endpoint returnt 200 ondanks Redis DEL-fail; warning gelogd.

### Layer 4 — LiteLLM hook flow
- **HOOK-01** Fully-wired redact flow — rule+template+KB actief → user-message vervangen, parallel analyze+retrieve, system prompt bevat templates → KB → existing.
- **HOOK-02** Block rule abort — pii_block match → chat afgebroken met NL error, geen retrieve-call doorgezet.
- **HOOK-03** No-detectors skip — 0 rules EN 0 templates → geen klai-pii roundtrip (netwerk-metric bevestigt).
- **HOOK-04** PII timeout fail-open — klai-pii timeout 3s → `guardrails_degraded` warning, chat gaat door zonder guardrails.
- **HOOK-05** PII 401 config-error fail-open — 401 → `guardrails_config_error` error, chat gaat door.
- **HOOK-06** Template injection volgorde — system prompt: templates-block eerst, dan KB-block, dan pre-existing.
- **HOOK-07** No raw PII in logs — `guardrail_applied` log-entry bevat GEEN matched text, alleen entity_type/keyword_name.
- **HOOK-08** Parallel gather geen short-circuit — retrieve-raise cancelt analyze NIET (return_exceptions=True).

### Edge cases
- **EDGE-01** Lege detectors — `{detectors: []}` van portal → hook skipt analyze call.
- **EDGE-02** Keyword > 64 chars — 400 bij CRUD create.
- **EDGE-03** > 100 keywords in één rule — 400 bij CRUD create.
- **EDGE-04** Slug-collision — tweede POST met zelfde naam → 409 (slug auto-suffix of error).
- **EDGE-05** Non-overlapping redact offsets — meerdere entity-hits in zelfde message → allemaal vervangen zonder offset-corruptie.
- **EDGE-06** /v1/redact 500 → hook valt terug op analyze-only (geen redact toegepast), logt warning.

### Performance
- **PERF-01** klai-pii warm `/v1/analyze` p95 ≤ 300ms (100-woord NL tekst).
- **PERF-02** klai-pii cold start tot `models_loaded=True` ≤ 60s p95 (90s ceiling).
- **PERF-03** CRUD endpoints p95 ≤ 100ms.
- **PERF-04** Parallel `gather(analyze, retrieve)` totaal ≤ max(analyze_p95, retrieve_p95) + 50ms (niet serial 600ms+).
- **PERF-05** Cache-hit overhead op /internal/guardrails/effective ≤ 5ms.

---

## Files to Modify / Create

### NEW — klai-pii microservice
- `klai-pii/Dockerfile`, `pyproject.toml`, `uv.lock`, `scripts/download_models.py`
- `klai-pii/klai_pii/`: `__init__.py`, `main.py`, `config.py`, `logging_setup.py`
- `klai-pii/klai_pii/middleware/`: `__init__.py`, `auth.py`
- `klai-pii/klai_pii/api/`: `__init__.py`, `analyze.py`, `redact.py`, `health.py`
- `klai-pii/klai_pii/services/`: `__init__.py`, `analyzer.py`, `nl_recognizers.py`, `keyword_matcher.py`
- `klai-pii/tests/`: `__init__.py`, `conftest.py`, `test_analyze.py`, `test_redact.py`, `test_nl_recognizers.py`

### MODIFY — deploy/
- `deploy/docker-compose.yml` — ADD service `klai-pii` (build, klai-net, mem_limit 1500M, healthcheck start_period 90s)
- `deploy/.env.example` — ADD `PII_INTERNAL_SECRET`
- `deploy/litellm/klai_knowledge.py` — REMOVE inline `_PII_PATTERNS`; ADD `_call_pii_service()`; REFACTOR pre-call to `asyncio.gather(analyze, retrieve, return_exceptions=True)`; REFACTOR `_get_guardrails()` to consume new schema; ADD `PII_SERVICE_URL`, `PII_INTERNAL_SECRET`, `PII_TIMEOUT`

### MODIFY — klai-portal/backend
**NEW migrations (4 + 1 merge):**
- `alembic/versions/m1g2r3h4i5j6_merge_main_heads_before_guardrails.py` — merge 6 heads
- `alembic/versions/add_portal_templates.py` — portal_templates + RLS strict
- `alembic/versions/add_portal_rules.py` — portal_rules + RLS strict
- `alembic/versions/add_active_template_ids_to_portal_users.py` — `active_template_ids INTEGER[]`
- `alembic/versions/add_portal_org_defaults_seeded_at.py` — `defaults_seeded_at TIMESTAMPTZ`

**NEW models:**
- `app/models/rules.py` — `PortalRule`
- `app/models/templates.py` — `PortalTemplate`

**MODIFY models:**
- `app/models/portal.py` — `PortalUser.active_template_ids`, `PortalOrg.defaults_seeded_at`

**NEW services:**
- `app/utils/slug.py` — shared `slugify`
- `app/services/default_rules.py` — `ensure_default_rules`
- `app/services/default_templates.py` — `ensure_default_templates`
- `app/services/litellm_cache.py` — `invalidate_guardrails`

**MODIFY services:**
- `app/services/provisioning/orchestrator.py` — ADD step 6b non-fatal defaults seeding

**NEW API:**
- `app/api/app_rules.py` — CRUD + WHITELIST + rate-limit + cache invalidation
- `app/api/app_templates.py` — CRUD + length validation + rate-limit + cache invalidation

**MODIFY API:**
- `app/api/app_account.py` — add `active_template_ids` + org-scoped validation + cache invalidation
- `app/api/internal.py` — ADD `GET /internal/guardrails/effective` (fail-safe librechat→zitadel mapping)
- `app/main.py` — ADD router registrations

**NEW tests:**
- `tests/test_app_rules.py`, `tests/test_app_templates.py`, `tests/test_internal_guardrails.py`, `tests/test_default_rules_templates.py`, `tests/test_litellm_cache.py`

### ADD — docs
- `docs/architecture/platform.md` — update Rules+Templates section to reflect implementation
- `docs/architecture/knowledge-retrieval-flow.md` — update guardrail-injection diagram with parallel-analyze step

---

## Exclusions (What NOT to Build)

- **Frontend UI** (`/app/rules`, `/app/templates` routes, chat config bar, template picker) — vervolg-SPEC `SPEC-CHAT-GUARDRAILS-002`.
- **Transcript-pseudonimisatie** (audio/meetings scribe-integratie met dezelfde klai-pii service) — aparte SPEC.
- **Cross-tenant shared rules/templates marketplace** — niet in v1.
- **Audit-log van guardrail-hits naar Grafana/product_events** — niet in v1 (alleen structlog entry).
- **Alle overige wijzigingen uit `feat/chat-first-redesign`** — SPEC-PROV-001 deletions, klai-libs deletions, RLS-test deletions. Wij nemen NIKS weg.
- **`rule_text` kolom** — bewust geschrapt; detectie is expliciet via `detector_entities` + `keywords`.
- **`"instruction"` rule_type** — bewust geschrapt; rules zijn strict guardrails. Instructies komen uit Templates.
- **`"global"` scope naam** — we gebruiken `"org"` en `"personal"` om dubbelzinnigheid met cross-tenant te voorkomen.
- **Per-KB template-scoping** — v1 alleen org/personal scope, KB-binding later.
- **GitHub Issue** — `issue_number: 0` in frontmatter; handmatig later te vullen.
