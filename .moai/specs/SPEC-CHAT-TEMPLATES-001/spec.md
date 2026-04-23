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

# SPEC-CHAT-TEMPLATES-001 — Prompt Templates

> Per-tenant response-scaffolds geïnjecteerd via de LiteLLM pre-call hook.

## HISTORY

| Version | Date       | Author       | Change                                                                                           |
|---------|------------|--------------|--------------------------------------------------------------------------------------------------|
| 0.1.0   | 2026-04-23 | Mark Vletter | Eerste draft. Geëxtraheerd uit `feat/chat-first-redesign`. Rules/PII apart in SPEC-CHAT-GUARDRAILS-001. |

---

## Overview

### Waarom

Organisaties willen de toon en stijl van AI-antwoorden centraal kunnen sturen — bijvoorbeeld een vaste "Klantenservice"-toon voor een support-team, of een "Formeel"-stijl voor juridische communicatie. Vandaag is daar geen mechanisme voor: elke user schrijft hun eigen system-prompt of herhaalt instructies per sessie. Het resultaat is inconsistente output en herhaald werk.

Prompt Templates bieden een **productfeature voor response-styling**: org-admins beheren herbruikbare prompt-scaffolds, gewone users activeren er 0+ per chat, en de LiteLLM-hook prepent de actieve template-teksten aan het system message van elke call.

### Wat

1. `portal_templates` tabel per tenant (RLS strict), met 4 NL default-templates bij tenant-provisioning: Klantenservice / Formeel / Creatief / Samenvatter.
2. CRUD endpoints `/api/app/templates` met scope-onderscheid (`org` = door admin beheerd, `personal` = door user zelf).
3. User-voorkeur: `PortalUser.active_template_ids` (NULL of lijst van template-IDs die momenteel aan staan).
4. Internal endpoint `GET /internal/templates/effective?zitadel_org_id=...&librechat_user_id=...` voor de LiteLLM-hook.
5. LiteLLM pre-call hook-uitbreiding: fetch actieve templates (30s cache) en prepend hun `prompt_text` aan het system message vóór de bestaande KB-context.

### Hoe (summier, implementatiedetails in plan.md)

- Foundation laag: slug-utility, cache-invalidatie helper, alembic merge-migration voor de 6 open heads in `main`.
- Data laag: `portal_templates` met RLS strict, CHECK constraint op `prompt_text` lengte, (org_id, slug) UNIQUE.
- API laag: FastAPI router met rate-limit, admin-gate op `scope="org"`, cache-invalidatie bij elke write.
- Internal laag: fail-safe `/internal/templates/effective` endpoint dat nooit 404 geeft op ontbrekende user-mapping (chat mag niet breken op config-issues).
- Hook laag: minimale `_get_templates` helper in `deploy/litellm/klai_knowledge.py` die fail-open degradeert bij timeout of 5xx.

### Afbakening t.o.v. SPEC-CHAT-GUARDRAILS-001

Deze SPEC levert **uitsluitend** de Templates-laag (productfeature voor styling). Alles wat met rules, guardrails of PII te maken heeft — `klai-pii` microservice, detectie-regex, block/redact-paden, `portal_rules` tabel, rules-cache — hoort in **SPEC-CHAT-GUARDRAILS-001** en is hier volledig buiten scope. De LiteLLM-hook krijgt in deze SPEC alleen een templates-injection-pad; de rules-injection wordt in de vervolg-SPEC toegevoegd.

Beide SPECs delen het patroon (`/internal/*/effective`, 30s Redis-cache, fail-open hook, SCAN+DEL invalidatie) maar raken geen gedeelde bestanden: templates gebruikt `templates:{org}:{user}` cache-keys en rules gebruikt `guardrails:{org}:{user}`. Er is geen dependency — templates kan eerst live zonder rules.

---

## Requirements

### REQ-TEMPLATES-MODEL — Data Model & Persistence

#### Ubiquitous

- REQ-TEMPLATES-MODEL-U1: The portal-api SHALL persist prompt templates in table `portal_templates` with `(org_id, slug)` UNIQUE.
- REQ-TEMPLATES-MODEL-U2: The portal-api SHALL enforce `char_length(prompt_text) <= 8000` via a database CHECK constraint.
- REQ-TEMPLATES-MODEL-U3: The portal-api SHALL enforce strict Row-Level Security on `portal_templates`: any query executed without `app.current_org_id` GUC set SHALL return zero rows.
- REQ-TEMPLATES-MODEL-U4: The portal-api SHALL store `scope` as a string restricted by CHECK constraint to `{"org","personal"}`.
- REQ-TEMPLATES-MODEL-U5: The portal-api SHALL persist `PortalUser.active_template_ids` as `INTEGER[] NULL` (NULL = geen actieve templates).

#### Event-Driven

- REQ-TEMPLATES-MODEL-E1: WHEN a user POSTs `/api/app/templates` with `prompt_text` length exceeding 8000 characters, THEN the endpoint SHALL respond HTTP 400.
- REQ-TEMPLATES-MODEL-E2: WHEN a user POSTs `/api/app/templates` with `scope` outside `{"org","personal"}`, THEN the endpoint SHALL respond HTTP 400.
- REQ-TEMPLATES-MODEL-E3: WHEN a user POSTs `/api/app/templates` with `name` length exceeding 128 characters, THEN the endpoint SHALL respond HTTP 422 via Pydantic.

### REQ-TEMPLATES-CRUD — CRUD Endpoints & Authorization

#### Ubiquitous

- REQ-TEMPLATES-CRUD-U1: The portal-api SHALL expose CRUD endpoints at `/api/app/templates` and `/api/app/templates/{slug}` requiring valid Zitadel bearer authentication.
- REQ-TEMPLATES-CRUD-U2: The portal-api SHALL derive `slug` server-side from `name` using the shared `app.utils.slug.slugify()` helper and reject slugs that would collapse to an empty string (HTTP 400).
- REQ-TEMPLATES-CRUD-U3: The portal-api SHALL rate-limit CRUD writes per org at 10 requests per second using a Redis sliding-window with key `templates_rl:{org_id}`, fail-open on Redis errors.

#### Event-Driven

- REQ-TEMPLATES-CRUD-E1: WHEN a non-admin user POSTs a template with `scope="org"`, THEN the endpoint SHALL respond HTTP 403 with NL message `"Alleen beheerders mogen organisatie-templates aanmaken"`.
- REQ-TEMPLATES-CRUD-E2: WHEN a user PATCHes a template they did not create and their role is not `"admin"`, THEN the endpoint SHALL respond HTTP 403.
- REQ-TEMPLATES-CRUD-E3: WHEN a user DELETEs a template they did not create and their role is not `"admin"`, THEN the endpoint SHALL respond HTTP 403.
- REQ-TEMPLATES-CRUD-E4: WHEN the write-rate for an org exceeds 10 req/s, THEN subsequent writes within the window SHALL respond HTTP 429 with header `Retry-After: <seconds>`.
- REQ-TEMPLATES-CRUD-E5: WHEN a user PATCHes `active_template_ids` via `/api/app/account/kb-preference` containing an ID that does not exist or belongs to a different org, THEN the endpoint SHALL respond HTTP 400.
- REQ-TEMPLATES-CRUD-E6: WHEN a new tenant is provisioned (orchestrator step `defaults_templates`), THEN `portal_templates` for that tenant SHALL contain exactly 4 rows with slugs `{klantenservice, formeel, creatief, samenvatter}`.
- REQ-TEMPLATES-CRUD-E7: WHEN `GET /api/app/templates` is called for an existing org that has zero templates, THEN the endpoint SHALL lazy-seed the 4 defaults within the current tenant-context before returning the list.

#### State-Driven

- REQ-TEMPLATES-CRUD-S1: WHILE a personal-scope template exists whose `created_by` differs from the requesting user, `GET /api/app/templates` SHALL NOT include that template in the response, EXCEPT when the requester's role is `"admin"` (admins see all personal templates in their org).
- REQ-TEMPLATES-CRUD-S2: WHILE a template's `is_active=false`, the template SHALL still appear in CRUD responses but SHALL be excluded from the `/internal/templates/effective` instruction list.

#### Unwanted Behavior

- REQ-TEMPLATES-CRUD-N1: IF a POST produces a `(org_id, slug)` UNIQUE violation, THEN the endpoint SHALL respond HTTP 409 (not 500) with a message naming the conflicting slug.

### REQ-TEMPLATES-CACHE — Cache Invalidation

#### Ubiquitous

- REQ-TEMPLATES-CACHE-U1: The portal-api SHALL expose a helper `app.services.litellm_cache.invalidate_templates(org_id, librechat_user_id=None)` that fires-and-forgets against Redis.

#### Event-Driven

- REQ-TEMPLATES-CACHE-E1: WHEN a user POSTs, PATCHes or DELETEs a template with `scope="org"`, THEN Redis keys matching `templates:{org_id}:*` SHALL be removed via SCAN+DEL within 100 ms of commit.
- REQ-TEMPLATES-CACHE-E2: WHEN a user POSTs, PATCHes or DELETEs a template with `scope="personal"`, THEN only the single Redis key `templates:{org_id}:{creator_librechat_user_id}` SHALL be removed.
- REQ-TEMPLATES-CACHE-E3: WHEN a user PATCHes their own `active_template_ids`, THEN only the single Redis key `templates:{org_id}:{self_librechat_user_id}` SHALL be removed.

#### Unwanted Behavior

- REQ-TEMPLATES-CACHE-N1: IF Redis DEL or SCAN raises an exception, THEN the write endpoint SHALL log a structured warning `templates_cache_invalidation_failed` and respond HTTP 2xx to the user (the 30-second TTL fallback absorbs the staleness).

### REQ-TEMPLATES-INTERNAL — Internal Endpoint for LiteLLM Hook

#### Ubiquitous

- REQ-TEMPLATES-INTERNAL-U1: The portal-api SHALL expose `GET /internal/templates/effective?zitadel_org_id=...&librechat_user_id=...` protected by the internal bearer secret using `hmac.compare_digest` (reusing `_require_internal_token`).
- REQ-TEMPLATES-INTERNAL-U2: The endpoint SHALL audit every call via the existing `_audit_internal_call` helper.
- REQ-TEMPLATES-INTERNAL-U3: The endpoint response body SHALL conform to `{"instructions": [{"source": "template", "name": string, "text": string}]}` (stable contract consumed by the LiteLLM hook).
- REQ-TEMPLATES-INTERNAL-U4: The endpoint SHALL call `set_tenant(org_id)` after resolving the Zitadel-org and before any query against `portal_templates`.

#### Event-Driven

- REQ-TEMPLATES-INTERNAL-E1: WHEN `/internal/templates/effective` is called for a valid user whose `active_template_ids` is NULL or empty, THEN the response SHALL be `{"instructions": []}` with HTTP 200.
- REQ-TEMPLATES-INTERNAL-E2: WHEN `/internal/templates/effective` is called with a `librechat_user_id` that has no matching `PortalUser` row, THEN the response SHALL be `{"instructions": []}` with HTTP 200 (fail-safe — chat may not break on missing mappings).
- REQ-TEMPLATES-INTERNAL-E3: WHEN `/internal/templates/effective` is called with a `zitadel_org_id` that has no matching `PortalOrg`, THEN the response SHALL be HTTP 404 (config-fout, distinct from missing user-mapping).
- REQ-TEMPLATES-INTERNAL-E4: WHEN the request lacks a valid internal bearer secret, THEN the endpoint SHALL respond HTTP 401 without performing any database access.
- REQ-TEMPLATES-INTERNAL-E5: WHEN `active_template_ids` references templates where `is_active=false` or that have been deleted, THEN those SHALL be silently skipped in the response.

#### Unwanted Behavior

- REQ-TEMPLATES-INTERNAL-N1: IF any query in `/internal/templates/effective` raises, THEN the error SHALL be logged via `logger.exception` and propagated as HTTP 500 (not silently swallowed — the hook handles 5xx via fail-open).

### REQ-TEMPLATES-HOOK — LiteLLM Pre-call Hook Integration

#### Ubiquitous

- REQ-TEMPLATES-HOOK-U1: The LiteLLM pre-call hook SHALL, for every non-trivial chat call, invoke `_get_templates(org_id, user_id)` which fetches `/internal/templates/effective` with a per-`(org_id, user_id)` in-process cache of TTL 30 seconds.
- REQ-TEMPLATES-HOOK-U2: The hook SHALL read `PORTAL_TEMPLATES_URL` (default `${PORTAL_API_URL}/internal/templates/effective`) and `TEMPLATES_TIMEOUT` (default `2.0` seconds) from environment variables.

#### Event-Driven

- REQ-TEMPLATES-HOOK-E1: WHEN the user has one or more active templates returned by the internal endpoint, THEN the hook SHALL prepend each template's `text` (in order returned by the endpoint) before the existing KB-context block inside the system message.
- REQ-TEMPLATES-HOOK-E2: WHEN the user has no active templates, THEN the hook SHALL NOT alter the system message beyond the existing KB-injection path.
- REQ-TEMPLATES-HOOK-E3: WHEN a user activates a new template and a subsequent chat request occurs, THEN (after Redis invalidation has run) the NEXT request outside the 30-second stale window SHALL include that template's `text`.

#### Unwanted Behavior

- REQ-TEMPLATES-HOOK-N1: IF `/internal/templates/effective` times out (> `TEMPLATES_TIMEOUT`) or returns HTTP 5xx, THEN the hook SHALL log a warning `templates_degraded` with context `(org_id, user_id, reason)` and continue building the system message without template instructions.
- REQ-TEMPLATES-HOOK-N2: IF the raw `prompt_text` of a template is included in any log line, THEN this SHALL be treated as a bug (logs MUST reference templates by `name` and `id` only).

### REQ-TEMPLATES-SEED — Idempotent Default Seeder

#### Ubiquitous

- REQ-TEMPLATES-SEED-U1: The portal-api SHALL provide `app.services.default_templates.ensure_default_templates(org_id, created_by, db)` which is idempotent via a row-count check (`SELECT COUNT(*) FROM portal_templates WHERE org_id = :org_id`).
- REQ-TEMPLATES-SEED-U2: The four default templates SHALL use `scope="org"`, `created_by="system"`, and the NL `prompt_text` values defined verbatim in `app.services.default_templates.DEFAULT_TEMPLATES`.

#### Event-Driven

- REQ-TEMPLATES-SEED-E1: WHEN `ensure_default_templates` is called and row-count > 0, THEN the function SHALL return immediately (no-op).
- REQ-TEMPLATES-SEED-E2: WHEN the provisioning orchestrator step `defaults_templates` raises an exception, THEN the orchestrator SHALL log a warning and continue provisioning (non-fatal).

---

## Files to Create / Modify

### NEW

- [NEW] `klai-portal/backend/alembic/versions/<ts>_merge_main_heads_before_templates.py` — merge-only migration unifying the 6 open heads (`c160d2b9d885`, `a2b3c4d5e6f7`, `b4c5d6e7f8g9`, `b5c6d7e8f9a0`, `c4d5e6f7a8b9`, `32fc0ed3581b`) into a single head.
- [NEW] `klai-portal/backend/alembic/versions/<ts>_add_portal_templates.py` — `portal_templates` table + RLS strict (ENABLE + FORCE + tenant_isolation policy without `OR IS NULL`) + CHECK `char_length(prompt_text) <= 8000` + CHECK `scope IN ('org','personal')` + indexes `(org_id, slug)` UNIQUE and `(org_id, is_active, scope)`.
- [NEW] `klai-portal/backend/alembic/versions/<ts>_add_active_template_ids_to_portal_users.py` — `active_template_ids INTEGER[] NULL` column.
- [NEW] `klai-portal/backend/app/models/templates.py` — `PortalTemplate` SQLAlchemy model.
- [NEW] `klai-portal/backend/app/api/app_templates.py` — CRUD router.
- [NEW] `klai-portal/backend/app/services/default_templates.py` — idempotent seeder + 4 NL defaults (Klantenservice / Formeel / Creatief / Samenvatter — copied verbatim from Jantine branch).
- [NEW] `klai-portal/backend/app/utils/slug.py` — shared `slugify(name) -> str` helper (extracted from Jantine's inline `_slugify`).
- [NEW] `klai-portal/backend/app/services/litellm_cache.py` — `invalidate_templates(org_id, librechat_user_id=None)` helper using existing Redis pool.
- [NEW] `klai-portal/backend/tests/test_app_templates.py` — CRUD happy-path, 400/403/409/429, RLS strict enforcement, admin-gate on `scope="org"`.
- [NEW] `klai-portal/backend/tests/test_internal_templates.py` — fail-safe missing mapping returns 200 empty, unknown org returns 404, missing/invalid bearer returns 401 without DB access.
- [NEW] `klai-portal/backend/tests/test_default_templates.py` — idempotent seeder (second call is no-op), exactly 4 rows after first call, non-fatal on orchestrator step failure.
- [NEW] `klai-portal/backend/tests/test_litellm_cache_templates.py` — SCAN+DEL for `scope="org"`, single DEL for personal, Redis-error is swallowed with warning.

### MODIFY

- [MODIFY] `klai-portal/backend/app/models/portal.py` — add `PortalUser.active_template_ids: Mapped[list[int] | None] = mapped_column(ARRAY(Integer), nullable=True)`.
- [MODIFY] `klai-portal/backend/app/api/app_account.py` — extend KB-preference PATCH payload with optional `active_template_ids: list[int] | None`; validate IDs exist and belong to caller's org; call `invalidate_templates(org_id, librechat_user_id)` after commit.
- [MODIFY] `klai-portal/backend/app/api/internal.py` — add `GET /internal/templates/effective` endpoint using existing `_require_internal_token` + `_audit_internal_call` helpers and `set_tenant()`.
- [MODIFY] `klai-portal/backend/app/services/provisioning/orchestrator.py` — insert step `defaults_templates` after KB-provisioning, guarded by try/except (non-fatal), using `mark_step_start` + `transition_state` into `seeding_templates`.
- [MODIFY] `klai-portal/backend/app/main.py` — `include_router(app_templates.router)`.
- [MODIFY] `deploy/litellm/klai_knowledge.py` — add env `PORTAL_TEMPLATES_URL`, `TEMPLATES_TIMEOUT`; add helper `_get_templates(org_id, user_id, cache) -> list[dict]` (30s cache, fail-open on timeout/5xx, log `templates_degraded`); prepend template `text` entries to the system message just before the KB-context block.

### ADD (docs)

- [MODIFY] `docs/architecture/platform.md` — update Templates section to clarify Templates are a productfeature (response-styling) and are orthogonal to the guardrail/PII layer in SPEC-CHAT-GUARDRAILS-001.
- [MODIFY] `docs/architecture/knowledge-retrieval-flow.md` — in the "Rules and Templates" section, describe **only** the Templates injection flow for v1; add a forward-reference to SPEC-CHAT-GUARDRAILS-001 for the rules injection.

---

## Exclusions (What NOT to Build)

- [OUT] **Rules / PII detection / `klai-pii` microservice** — alles met regex-gebaseerde PII-detectie, `portal_rules` tabel, block/redact-paden, rules-cache is volledig belegd in SPEC-CHAT-GUARDRAILS-001.
- [OUT] **Frontend UI** — `/app/templates` routes, chat config-bar template picker, template-editor component — in vervolg-SPEC SPEC-CHAT-TEMPLATES-002.
- [OUT] **Per-KB template scoping** — v1 kent alleen `org` en `personal` scope. Geen koppeling tussen templates en specifieke Knowledge Bases.
- [OUT] **Cross-tenant template marketplace / sharing** — geen export/import van templates tussen orgs.
- [OUT] **Audit-log van template-usage in Grafana product_events** — v1 alleen structlog via standaard logging-pipeline.
- [OUT] **Versioning / revision-history van `prompt_text`** — geen diff-tracking of rollback-mechaniek; PATCH overschrijft direct.
- [OUT] **`defaults_seeded_at` kolom op `portal_orgs`** — de row-count check in `ensure_default_templates` is voldoende idempotent voor v1.
- [OUT] **Rename van LiteLLM cache-key prefixes** — `templates:` is de target-naam; geen refactor nodig.
- [OUT] **Alle andere wijzigingen uit `feat/chat-first-redesign`** — SPEC-PROV-001 deletions, klai-libs wijzigingen, RLS-test deletions etc. blijven ongemoeid; deze SPEC voegt alleen toe.
- [OUT] **Parallel `asyncio.gather` voor templates + KB fetch in de hook** — templates-fetch is non-blocking en snel; de bestaande sequentiële KB-fetch structuur blijft intact.

---

## References

### Project documents

- `.moai/project/product.md`
- `.moai/project/structure.md`
- `.moai/project/tech.md`
- `.moai/specs/SPEC-CHAT-GUARDRAILS-001/` — zustersSPEC voor rules/PII (separate delivery).

### Klai architecture & rules

- `docs/architecture/platform.md` — Templates & Rules sectie (wordt in deze SPEC bijgewerkt).
- `docs/architecture/knowledge-retrieval-flow.md` — KB-injection flow in LiteLLM hook.
- `.claude/rules/klai/projects/portal-backend.md` — SQLAlchemy + RLS CRIT, `SELECT ... FOR UPDATE` patroon, status-string contracten.
- `.claude/rules/klai/projects/portal-logging-py.md` — structlog + `exc_info=True` + context binding.
- `.claude/rules/klai/infra/observability.md` — VictoriaLogs + request_id chain.
- `.claude/rules/klai/no-ask-user-question.md` — geen AskUserQuestion in implementatie.

### Reference implementation (indicatief, NIET letterlijk overnemen)

Uit `origin/feat/chat-first-redesign` (Jantine):

- `klai-portal/backend/app/api/app_templates.py` — CRUD shape (afwijkingen: admin-gate, rate-limit, cache-invalidatie, `scope="global"` → `"org"`).
- `klai-portal/backend/app/models/templates.py` — model shape (afwijkingen: CHECK constraint op `prompt_text`, scope enum).
- `klai-portal/backend/app/services/default_templates.py` — 4 NL defaults **letterlijk overnemen** (product-content).
- `klai-portal/backend/alembic/versions/f7a8b9c0d1e2_add_portal_templates.py` — migratie shape (afwijkingen: RLS strict toevoegen, CHECK constraints, extra index).
- `klai-portal/backend/alembic/versions/a4b5c6d7e8f9_add_active_template_ids_to_portal_users.py` — ARRAY(Integer) kolom.
- `klai-portal/backend/app/services/provisioning/orchestrator.py` — step 6b shape (afwijking: alleen templates, non-fatal).
- `deploy/litellm/klai_knowledge.py` — injection-shape (afwijking: **alle PII/rules-logica geschrapt** uit deze SPEC scope).

### Klai patterns

- `klai-portal/backend/alembic/versions/1b8736eb6455_add_rls_phase2_user_tables.py` — RLS strict migratie-patroon.
- `klai-portal/backend/alembic/versions/aa7531c292e4_merge_dev_heads.py` — merge-migration patroon.
- `klai-portal/backend/app/services/partner_rate_limit.py` — Redis sliding-window rate-limit patroon.
- `klai-portal/backend/app/api/internal.py` — `_require_internal_token`, `_audit_internal_call`, `set_tenant()` patroon voor internal endpoints.
