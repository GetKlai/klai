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

# SPEC-CHAT-TEMPLATES-001 — Compact

Auto-generated from spec.md + acceptance.md. Used by `/moai run` to save ~30% tokens vs full spec.md.
For full context (Overview, Approach, Alternatives, References, History) see `spec.md`.

---

## Requirements (EARS)

### REQ-TEMPLATES-MODEL — Data Model & Persistence

**Ubiquitous**
- U1: The portal-api SHALL persist prompt templates in `portal_templates` with `(org_id, slug)` UNIQUE.
- U2: SHALL enforce `char_length(prompt_text) <= 8000` via DB CHECK constraint.
- U3: SHALL enforce strict RLS on `portal_templates` — query zonder `app.current_org_id` GUC → 0 rows.
- U4: `scope` CHECK-restricted tot `{"org","personal"}`.
- U5: `PortalUser.active_template_ids` SHALL be `INTEGER[] NULL` (NULL = geen actieve templates).

**Event-Driven**
- E1: WHEN POST template met `prompt_text > 8000` chars → HTTP 400.
- E2: WHEN POST template met `scope` outside `{"org","personal"}` → HTTP 400.
- E3: WHEN POST template met `name > 128` chars → HTTP 422 (Pydantic).

### REQ-TEMPLATES-CRUD — CRUD & Authorization

**Ubiquitous**
- U1: CRUD aan `/api/app/templates` + `/api/app/templates/{slug}` — Zitadel bearer auth verplicht.
- U2: `slug` server-side derived via `app.utils.slug.slugify()`; lege slug → HTTP 400.
- U3: Rate-limit 10 req/s per org via Redis sliding-window, key `templates_rl:{org_id}`, fail-open.

**Event-Driven**
- E1: WHEN non-admin POSTs `scope="org"` → HTTP 403 met NL-message.
- E2: WHEN non-owner non-admin PATCH → HTTP 403.
- E3: WHEN non-owner non-admin DELETE → HTTP 403.
- E4: WHEN write-rate > 10/s → HTTP 429 + `Retry-After`.
- E5: WHEN PATCH `active_template_ids` met cross-org ID → HTTP 400.
- E6: WHEN tenant wordt geprovisioneerd → `portal_templates` bevat exact 4 rows met slugs `{klantenservice, formeel, creatief, samenvatter}`.
- E7: WHEN `GET /api/app/templates` op bestaande org met 0 templates → lazy-seed binnen tenant-context vóór return.

**State-Driven**
- S1: WHILE een personal-scope template van een andere user bestaat → `GET /api/app/templates` SHALL NOT include, EXCEPT voor admin-role (admins zien alle personal templates in hun org).
- S2: WHILE `is_active=false` → template blijft zichtbaar in CRUD, wordt uitgesloten uit `/internal/templates/effective`.

**Unwanted Behavior**
- N1: IF POST `(org_id, slug)` UNIQUE violation → HTTP 409 (niet 500) met conflicting slug in body.

### REQ-TEMPLATES-CACHE — Cache Invalidation

**Ubiquitous**
- U1: Helper `app.services.litellm_cache.invalidate_templates(org_id, librechat_user_id=None)` SHALL fire-and-forget.

**Event-Driven**
- E1: WHEN POST/PATCH/DELETE template met `scope="org"` → Redis keys matching `templates:{org_id}:*` verwijderd via SCAN+DEL binnen 100ms.
- E2: WHEN `scope="personal"` write → alleen single key `templates:{org_id}:{creator_librechat_user_id}`.
- E3: WHEN PATCH eigen `active_template_ids` → alleen `templates:{org_id}:{self_librechat_user_id}`.

**Unwanted Behavior**
- N1: IF Redis DEL/SCAN raises → structured warning `templates_cache_invalidation_failed`, endpoint retourneert HTTP 2xx (30s TTL-fallback absorbeert).

### REQ-TEMPLATES-INTERNAL — Internal Endpoint for LiteLLM Hook

**Ubiquitous**
- U1: `GET /internal/templates/effective?zitadel_org_id=...&librechat_user_id=...` SHALL be protected by internal bearer secret via `hmac.compare_digest` (reuse `_require_internal_token`).
- U2: SHALL audit via existing `_audit_internal_call`.
- U3: Response body: `{"instructions": [{"source": "template", "name": string, "text": string}]}`.
- U4: SHALL call `set_tenant(org_id)` na zitadel-org resolutie, vóór query op `portal_templates`.

**Event-Driven**
- E1: WHEN user `active_template_ids` is NULL of empty → `{"instructions": []}` HTTP 200.
- E2: WHEN onbekende `librechat_user_id` → `{"instructions": []}` HTTP 200 (fail-safe, NIET 404).
- E3: WHEN onbekende `zitadel_org_id` → HTTP 404 (config-fout, distinct van ontbrekende user-mapping).
- E4: WHEN request zonder valid internal bearer → HTTP 401 ZONDER DB-access.
- E5: WHEN `active_template_ids` referencet `is_active=false` of verwijderde templates → silently skipped in response.

**Unwanted Behavior**
- N1: IF query in `/internal/templates/effective` raises → `logger.exception` + HTTP 500 (niet silently swallowed — hook fail-opent).

### REQ-TEMPLATES-HOOK — LiteLLM Pre-call Hook Integration

**Ubiquitous**
- U1: Hook SHALL per non-trivial chat call `_get_templates(org_id, user_id)` aanroepen — 30s in-proc cache per `(org_id, user_id)`.
- U2: Hook SHALL env `PORTAL_TEMPLATES_URL` (default `${PORTAL_API_URL}/internal/templates/effective`) + `TEMPLATES_TIMEOUT` (default `2.0`) lezen.

**Event-Driven**
- E1: WHEN user heeft ≥1 actieve template → hook SHALL elke `text` prependen (in endpoint-volgorde) vóór bestaande KB-context block in system message.
- E2: WHEN user heeft 0 actieve templates → hook SHALL system message NIET wijzigen buiten bestaande KB-injection path.
- E3: WHEN user activeert nieuwe template + volgende chat-request → (na Redis-invalidation) eerstvolgende request buiten 30s stale window SHALL die template's `text` bevatten.

**Unwanted Behavior**
- N1: IF `/internal/templates/effective` timeout (> `TEMPLATES_TIMEOUT`) of 5xx → warning `templates_degraded` met `(org_id, user_id, reason)`, chat gaat door zonder templates.
- N2: IF raw `prompt_text` in log-line verschijnt → bug (logs MUST alleen `name` + `id` referencen).

### REQ-TEMPLATES-SEED — Idempotent Default Seeder

**Ubiquitous**
- U1: `app.services.default_templates.ensure_default_templates(org_id, created_by, db)` SHALL idempotent zijn via row-count check.
- U2: 4 defaults: `scope="org"`, `created_by="system"`, NL `prompt_text` verbatim uit `DEFAULT_TEMPLATES` constant.

**Event-Driven**
- E1: WHEN `ensure_default_templates` aangeroepen + row-count > 0 → no-op (immediate return).
- E2: WHEN provisioning step `defaults_templates` raises → orchestrator SHALL log warning en doorgaan (non-fatal).

---

## Acceptance Scenarios (Given-When-Then)

All scenarios require observable evidence (HTTP response, DB-row, log-line, Redis-key state, system-message content).

### Area 1 — Data Model & Persistence
- **AC-MODEL-01** — `(org_id, slug)` UNIQUE — tweede POST met zelfde name in zelfde org → 409 conflict-message noemt slug.
- **AC-MODEL-02** — CHECK `char_length(prompt_text) <= 8000` — 8001 chars → 400 met CHECK constraint detail.
- **AC-MODEL-03** — RLS strict — `SELECT` zonder `app.current_org_id` → 0 rows (psql test).
- **AC-MODEL-04** — `scope` CHECK weigert onbekende waarden — `scope="global"` → 400.

### Area 2 — CRUD & Authorization
- **AC-CRUD-01** — Admin POST scope="org" → 201 + DB-row.
- **AC-CRUD-02** — Non-admin POST scope="org" → 403 NL-message.
- **AC-CRUD-03** — Any user POST scope="personal" → 201.
- **AC-CRUD-04** — Non-owner non-admin PATCH → 403.
- **AC-CRUD-05** — Admin PATCH any template (incl. personal van andere user) → 200.
- **AC-CRUD-06** — 11e write binnen 1s → 429 + `Retry-After: <seconds>`.
- **AC-CRUD-07** — Na provisioning: 4 defaults met slugs `klantenservice,formeel,creatief,samenvatter`.
- **AC-CRUD-08** — Lazy-seed: bestaande org met 0 templates + GET → 4 defaults geseed, response bevat ze.
- **AC-CRUD-09** — Personal template alleen voor creator zichtbaar (andere non-admin user ziet 'm niet).
- **AC-CRUD-10** — Admin ziet alle personal templates in eigen org.
- **AC-CRUD-11** — PATCH `active_template_ids` met cross-tenant ID → 400.
- **AC-CRUD-12** — PATCH `active_template_ids` → Redis `templates:{org}:{self_user}` deleted, andere users' keys intact.

### Area 3 — Cache Invalidation
- **AC-CACHE-01** — Scope="org" write → SCAN+DEL pattern `templates:{org}:*` ≤100ms, alle users geraakt.
- **AC-CACHE-02** — Scope="personal" write → single DEL `templates:{org}:{creator}`, andere keys intact.
- **AC-CACHE-03** — Redis down tijdens write → warning `templates_cache_invalidation_failed` + endpoint retourneert 2xx.

### Area 4 — Internal Endpoint
- **AC-INT-01** — User met `active_template_ids=NULL` → `{"instructions": []}` 200.
- **AC-INT-02** — Onbekende `librechat_user_id` (geen PortalUser match) → `{"instructions": []}` 200 (fail-safe).
- **AC-INT-03** — Onbekende `zitadel_org_id` → 404.
- **AC-INT-04** — Request zonder bearer → 401 zonder DB-access (geen `SELECT` in trace).
- **AC-INT-05** — `active_template_ids=[2,1,3]` → response `instructions` in die volgorde (user-intent preserveren).
- **AC-INT-06** — Template met `is_active=false` in `active_template_ids` → stilzwijgend overgeslagen.

### Area 5 — LiteLLM Hook
- **AC-HOOK-01** — User heeft 2 templates + KB actief → system-message: `template1.text\n\ntemplate2.text\n\n[KB-context...]\n\n{pre-existing system}`.
- **AC-HOOK-02** — User zonder templates → system-message identiek aan huidige KB-injection path (no diff).
- **AC-HOOK-03** — Portal-api timeout > 2s → warning `templates_degraded` gelogd, chat-call gaat door zonder templates.
- **AC-HOOK-04** — Twee opeenvolgende chat-calls binnen 30s + zelfde (org, user) → slechts 1 HTTP call naar `/internal/templates/effective` (cache-hit).
- **AC-HOOK-05** — User PATCH `active_template_ids` → volgende chat-call (buiten 30s stale window, cache invalidated) SHALL nieuwe template in system-message hebben.

### Edge Cases
- **EDGE-01** — prompt_text exact 8000 chars → 201 (boundary inclusive).
- **EDGE-02** — prompt_text 8001 chars → 400.
- **EDGE-03** — name = "   " (only whitespace) → 400 "lege slug".
- **EDGE-04** — Duplicate POST zelfde name in zelfde org → 409.
- **EDGE-05** — PATCH active_template_ids = [] (lege lijst) → 200, cache-deleted.
- **EDGE-06** — Template referred in `active_template_ids` wordt DELETEd → volgende `/internal/templates/effective` call skipt 'm silently (geen crash).
- **EDGE-07** — SCAN+DEL met >100 matching keys → voltooit ≤100ms (performance-criterion).
- **EDGE-08** — Unicode emoji in prompt_text → correct opgeslagen + geretourneerd.
- **EDGE-09** — Provisioning step raise `DatabaseError` → orchestrator logt warning + continue (non-fatal).
- **EDGE-10** — Lazy-seed tijdens GET met RLS actief → seed gebeurt binnen `set_tenant()` context.

### Performance
- **PERF-01** CRUD endpoints p95 ≤ 100ms.
- **PERF-02** `/internal/templates/effective` p95 ≤ 20ms.
- **PERF-03** Cache-hit overhead in hook ≤ 5ms.
- **PERF-04** Cache-miss (portal-api roundtrip) ≤ 30ms p95.
- **PERF-05** SCAN+DEL bij ≤500 keys ≤ 100ms.

---

## Files to Create / Modify

### NEW
- `klai-portal/backend/alembic/versions/<ts>_merge_main_heads_before_templates.py` — merge 6 heads
- `klai-portal/backend/alembic/versions/<ts>_add_portal_templates.py` — tabel + RLS strict + CHECK × 2 + indexes
- `klai-portal/backend/alembic/versions/<ts>_add_active_template_ids_to_portal_users.py` — `INTEGER[] NULL`
- `klai-portal/backend/app/models/templates.py` — `PortalTemplate`
- `klai-portal/backend/app/api/app_templates.py` — CRUD + admin-gate + rate-limit + cache-invalidate
- `klai-portal/backend/app/services/default_templates.py` — seeder + 4 NL defaults (verbatim Jantine)
- `klai-portal/backend/app/utils/slug.py` — shared `slugify`
- `klai-portal/backend/app/services/litellm_cache.py` — `invalidate_templates(org_id, librechat_user_id=None)`
- `klai-portal/backend/tests/test_app_templates.py`
- `klai-portal/backend/tests/test_internal_templates.py`
- `klai-portal/backend/tests/test_default_templates.py`
- `klai-portal/backend/tests/test_litellm_cache_templates.py`

### MODIFY
- `klai-portal/backend/app/models/portal.py` — add `PortalUser.active_template_ids`
- `klai-portal/backend/app/api/app_account.py` — KB-preference + `active_template_ids` + invalidate
- `klai-portal/backend/app/api/internal.py` — ADD `GET /internal/templates/effective`
- `klai-portal/backend/app/services/provisioning/orchestrator.py` — step `defaults_templates` non-fatal
- `klai-portal/backend/app/main.py` — include_router
- `deploy/litellm/klai_knowledge.py` — env + `_get_templates` helper + prepend vóór KB-block

### ADD (docs)
- `docs/architecture/platform.md` — clarify Templates = productfeature, los van guardrails
- `docs/architecture/knowledge-retrieval-flow.md` — v1 beschrijft alleen Templates-injection; forward-ref naar SPEC-CHAT-GUARDRAILS-001

---

## Exclusions (What NOT to Build)

- **Rules / PII detection / klai-pii microservice** — volledig in SPEC-CHAT-GUARDRAILS-001.
- **Frontend UI** (`/app/templates` routes, chat config bar, template editor) — vervolg-SPEC SPEC-CHAT-TEMPLATES-002.
- **Per-KB template-scoping** — v1 alleen `org` en `personal`.
- **Cross-tenant template marketplace** — niet in v1.
- **Audit-log naar Grafana `product_events`** — v1 alleen structlog.
- **Versioning / revision-history van `prompt_text`** — PATCH overschrijft direct.
- **`defaults_seeded_at` kolom op `portal_orgs`** — row-count check volstaat v1.
- **Rename van LiteLLM cache-key prefixes** — `templates:` is target-naam.
- **Alle andere wijzigingen uit `feat/chat-first-redesign`** — PROV-001, klai-libs, RLS-tests blijven ongemoeid.
- **Parallel `asyncio.gather` voor templates + KB** — sequentieel is voldoende; templates-fetch non-blocking en snel.
