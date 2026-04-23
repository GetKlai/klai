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

# Plan — SPEC-CHAT-TEMPLATES-001

## Approach

### Rationale: bottom-up

De implementatie loopt **bottom-up**: eerst de gedeelde foundation (slug-util, cache-helper, alembic-merge), dan de data-laag (model + migraties), dan de API-laag (CRUD + internal endpoint + provisioning-haak), en pas als laatste de consumer (LiteLLM-hook).

Waarom bottom-up:

1. **Testbaarheid per laag.** Foundation en model zijn zelfstandig unit-testbaar zonder de CRUD- of hook-laag. Elke fase levert tests die groen moeten zijn vóór de volgende fase start.
2. **Risicoreductie op migraties.** De 6 open heads in `main` moeten als eerste gemerged — anders blokkeert elke volgende migratie de CI. Door dit in Fase A te doen is dit risico weggenomen voordat er één regel app-code is geschreven.
3. **Independent deployability.** De portal-API kan live zonder de LiteLLM-hook-wijziging: tenants krijgen dan al defaults geseed en org-admins kunnen templates beheren — de hook komt er later bij. Dit geeft een natuurlijke staging van de rollout.
4. **Hook als laatste = minste risico op chat-breakage.** De LiteLLM-hook raakt het kritieke chat-pad. Door hem als laatste te wijzigen en fail-open te implementeren, is het risico op een chat-outage minimaal.

### Geen parallel gather in de hook

De bestaande `klai_knowledge.py` doet al een sequentiële KB-fetch; templates-fetch komt daar voor. Er is geen `asyncio.gather` nodig — de templates-fetch is non-blocking (cached 30s, fail-open op timeout 2s) en het totale latency-budget blijft binnen de bestaande chat-SLA. Parallelisatie kan later in een apart refactor-SPEC als metriek-data aantoont dat het nodig is.

### Niets uit Jantine wegdelen

Deze SPEC voegt **alleen toe**. Alle andere wijzigingen uit `feat/chat-first-redesign` (SPEC-PROV-001 deletions, klai-libs wijzigingen, RLS-test deletions) blijven ongemoeid — dit is een kleinste-mogelijke-PR aanpak.

---

## Dependency Graph

```
Fase A: Foundation
  A1 slug util ─────────────┐
  A2 litellm_cache helper ──┤
  A3 alembic merge-head ────┤
                            │
Fase B: DB & Model ─────────┤
  B1 PortalTemplate model ──┤
  B2 add_portal_templates ──┤──→ Fase C: Portal-API
  B3 add_active_template_ids┤       C1 default_templates service
  B4 PortalUser.active_… ───┘       C2 app_templates CRUD router
                                    C3 app_account extension
                                    C4 internal /templates/effective
                                    C5 main.py router registration
                                    C6 orchestrator step 6b
                                    C7 tests voor C1-C6
                                          │
                                          ▼
                                    Fase D: LiteLLM Hook
                                      D1 env vars
                                      D2 _get_templates helper
                                      D3 system-message injection
                                          │
                                          ▼
                                    Fase E: Docs
                                      E1 platform.md update
                                      E2 knowledge-retrieval-flow.md update
```

Harde volgordelijkheid:

- A3 (merge-head) MUST complete before B2/B3 (alembic blocks otherwise).
- B1 MUST complete before C1 (`default_templates` imports `PortalTemplate`).
- B4 MUST complete before C3 (account endpoint references `active_template_ids`).
- C4 MUST complete before D2 (hook needs the internal endpoint live).
- C7 MUST pass before D-phase starts (no hook wiring on untested CRUD).

Parallelle ruimte binnen fase:

- Binnen Fase A zijn A1/A2/A3 onafhankelijk — kunnen in één commit samen.
- Binnen Fase C zijn C1 en C2 sequentieel (C2 gebruikt `ensure_default_templates` uit C1), maar C3/C4/C6 zijn parallel uitvoerbaar zodra C1+C2 staan.

---

## Fases

### Fase A — Foundation

Doel: de fundering leggen zodat alles wat erop gaat staan, staat.

#### A1 — `app/utils/slug.py`

Create `slugify(name: str) -> str` extracted from Jantine's inline `_slugify`:

- Lowercase, strip, remove non-word/space/hyphen via regex, collapse whitespace and hyphens to single hyphen, trim leading/trailing hyphens, truncate to 64 chars.
- Return `""` if input collapses to empty — callers decide hoe om te gaan met lege slug (CRUD-router geeft 400).

Acceptance hook: `tests/test_slug.py` — 6 cases (happy, empty-string, unicode-stripped, length-cap, hyphen-collapse, leading/trailing-trim).

#### A2 — `app/services/litellm_cache.py`

Create `invalidate_templates(org_id: int, librechat_user_id: str | None = None) -> None`:

- Grab existing Redis pool via `get_redis_pool()` (or equivalent helper).
- If `librechat_user_id is None`: SCAN with `MATCH templates:{org_id}:*` and pipeline DEL per batch. Use cursor-based SCAN (not KEYS — blocks Redis).
- If `librechat_user_id` is a string: single `DEL templates:{org_id}:{librechat_user_id}`.
- Fire-and-forget: wrap in try/except, log warning `templates_cache_invalidation_failed` with `exc_info=True` on Exception, never re-raise.
- Structured log on success: `templates_cache_invalidated` with `(org_id, user_id or "org-wide")`.

Acceptance hook: `tests/test_litellm_cache_templates.py` — SCAN+DEL for None, single DEL for string, swallowed exception on Redis down.

#### A3 — Alembic merge-migration

Copy shape from `aa7531c292e4_merge_dev_heads.py`:

- `down_revision` is a tuple of the 6 current heads: `("c160d2b9d885", "a2b3c4d5e6f7", "b4c5d6e7f8g9", "b5c6d7e8f9a0", "c4d5e6f7a8b9", "32fc0ed3581b")`.
- Empty `upgrade()` / `downgrade()` bodies — pure merge, no schema change.
- File name: `<ts>_merge_main_heads_before_templates.py`.

Acceptance hook: `alembic heads` returns exactly 1 head after this migration.

---

### Fase B — DB & Model

Doel: persistent opslag en schema-garanties staan, inclusief RLS strict.

#### B1 — `app/models/templates.py`

`PortalTemplate` model met:

- Columns: `id`, `org_id` (FK `portal_orgs.id`), `name` (String 128), `slug` (String 64), `description` (Text nullable), `prompt_text` (Text NOT NULL), `scope` (String 16, default `"org"`), `created_by` (String 64), `is_active` (Boolean default True), `created_at`, `updated_at`.
- Table args:
  - `UniqueConstraint("org_id", "slug", name="uq_portal_template_org_slug")`
  - `Index("ix_portal_template_org_active_scope", "org_id", "is_active", "scope")`
  - `CheckConstraint("char_length(prompt_text) <= 8000", name="ck_portal_template_prompt_len")`
  - `CheckConstraint("scope IN ('org','personal')", name="ck_portal_template_scope")`

Acceptance hook: import smoke test in `tests/test_models_templates.py`.

#### B2 — Migration `add_portal_templates.py`

`down_revision` = merge-migration from A3.

```
op.create_table("portal_templates", ...columns...)
op.create_unique_constraint("uq_portal_template_org_slug", ...)
op.create_index("ix_portal_template_org_active_scope", ...)
op.create_check_constraint("ck_portal_template_prompt_len", "portal_templates", "char_length(prompt_text) <= 8000")
op.create_check_constraint("ck_portal_template_scope", "portal_templates", "scope IN ('org','personal')")

# RLS strict — following 1b8736eb6455 pattern but WITHOUT "OR IS NULL" fallback
op.execute("ALTER TABLE portal_templates ENABLE ROW LEVEL SECURITY")
op.execute("ALTER TABLE portal_templates FORCE ROW LEVEL SECURITY")
op.execute(
    "CREATE POLICY tenant_isolation ON portal_templates "
    "USING (org_id = NULLIF(current_setting('app.current_org_id', true), '')::int)"
)
```

Acceptance hook: `tests/test_rls_templates.py` — query without `set_tenant()` returns 0 rows.

#### B3 — Migration `add_active_template_ids_to_portal_users.py`

```
op.add_column("portal_users", sa.Column("active_template_ids", ARRAY(sa.Integer), nullable=True))
```

Downgrade drops the column.

Acceptance hook: smoke test — `PortalUser.active_template_ids` round-trips NULL, `[]`, `[1,2,3]`.

#### B4 — `PortalUser.active_template_ids`

Add the SQLAlchemy mapping:

```
active_template_ids: Mapped[list[int] | None] = mapped_column(ARRAY(Integer), nullable=True)
```

Acceptance hook: included in B3 smoke test.

---

### Fase C — Portal-API

Doel: CRUD, user-preference, internal endpoint en provisioning-haak werken end-to-end met tests groen.

#### C1 — `app/services/default_templates.py`

Kopieer **verbatim** de `DEFAULT_TEMPLATES` list uit de Jantine-branch (4 NL-prompts: Klantenservice / Formeel / Creatief / Samenvatter). Afwijkingen:

- `scope="org"` (niet `"global"`).
- `created_by="system"` by default (constant — defaults zijn niet eigendom van een specifieke admin).

`ensure_default_templates(org_id: int, created_by: str = "system", db: AsyncSession) -> None`:

- Row-count check via `SELECT COUNT(*) FROM portal_templates WHERE org_id = :org_id`.
- If > 0: return (no-op).
- Else: `db.add_all(...)` 4 rows, `await db.flush()`, structlog `default_templates_seeded`.
- Wrap in try/except: on exception `logger.warning("default_templates_seeding_failed", org_id=org_id, exc_info=True)` + `await db.rollback()` — non-fatal.

Acceptance hook: `tests/test_default_templates.py` — idempotent (tweede call is no-op), exactly 4 rows na eerste call, `created_by == "system"`.

#### C2 — `app/api/app_templates.py`

Router patroon volgens Jantine maar met de volgende afwijkingen:

1. **Admin-gate op `scope="org"`**: in `create_template` vóór de insert: `if body.scope == "org" and caller.role != "admin": raise HTTPException(403, "Alleen beheerders mogen organisatie-templates aanmaken")`.
2. **Rate-limit**: decorator of helper die de Redis sliding-window check doet (`templates_rl:{org_id}`, 10 req/s). Fail-open op Redis-error. Zet `Retry-After` header op 429.
3. **Cache-invalidatie**: na elke succesvolle POST/PATCH/DELETE call: `invalidate_templates(org_id, librechat_user_id)` waarbij `librechat_user_id=None` voor `scope="org"` en de creator's `librechat_user_id` voor `scope="personal"`.
4. **Pydantic validatie strict**: `name` max 128, `prompt_text` max 8000 (Pydantic `Field(max_length=8000)` — geeft 422 vóór DB; CHECK is defence-in-depth), `description` max 500, `scope` als `Literal["org","personal"]`.
5. **Slug handling**: `slug = slugify(body.name)`; als empty → 400 `"Name must produce a valid slug"`.
6. **Personal scope visibility**: list-endpoint filter — `(scope == "org") OR (created_by == caller_zitadel_id)` behalve voor admins, die alles in hun org zien.
7. **Unieke-violation**: catch `IntegrityError`, response 409 met slug-naam in de message.

Router wordt geregistreerd in `main.py` als laatste stap van Fase C.

Acceptance hook: `tests/test_app_templates.py` — CRUD happy-path, 400 (scope invalid, prompt too long, empty slug), 403 (non-admin POST scope="org", non-owner PATCH), 409 (dup slug), 429 (rate-limit).

#### C3 — `app/api/app_account.py` extension

Het bestaande `PATCH /api/app/account/kb-preference` endpoint krijgt een optioneel veld `active_template_ids: list[int] | None = None`:

- Validatie: als niet-None, doe `SELECT id FROM portal_templates WHERE id = ANY(:ids) AND org_id = :org_id AND is_active = true` — het aantal gevonden rows moet gelijk zijn aan `len(ids)`. Ontbrekend of andere org → 400.
- Persist: `user.active_template_ids = body.active_template_ids` (NULL toestaan om "geen actieve" weer te geven).
- After commit: `invalidate_templates(org_id, user.librechat_user_id)`.

Acceptance hook: test dat cross-tenant ID → 400, niet-bestaand ID → 400, valid list → 200 + Redis key removed.

#### C4 — `app/api/internal.py` extension

Add endpoint `GET /internal/templates/effective`:

```
@router.get("/internal/templates/effective")
async def effective_templates(
    zitadel_org_id: str,
    librechat_user_id: str,
    creds: HTTPAuthorizationCredentials = Depends(bearer),
    db: AsyncSession = Depends(get_db),
) -> dict:
    _require_internal_token(creds)
    _audit_internal_call(...)

    org = await _resolve_org_by_zitadel_id(zitadel_org_id, db)
    if org is None:
        raise HTTPException(404, "org_not_found")  # config-fout, chat mag hier op 5xx fallen

    await set_tenant(db, org.id)  # RLS context vóór user lookup

    user = await _resolve_user_by_librechat_id(librechat_user_id, org.id, db)
    if user is None or not user.active_template_ids:
        return {"instructions": []}  # fail-safe

    result = await db.execute(
        select(PortalTemplate)
        .where(PortalTemplate.id.in_(user.active_template_ids))
        .where(PortalTemplate.is_active.is_(True))
    )
    templates = result.scalars().all()
    # Preserve user-specified order
    by_id = {t.id: t for t in templates}
    ordered = [by_id[i] for i in user.active_template_ids if i in by_id]

    return {"instructions": [
        {"source": "template", "name": t.name, "text": t.prompt_text}
        for t in ordered
    ]}
```

Acceptance hook: `tests/test_internal_templates.py` — 401 zonder bearer (geen DB-access, mockable via middleware), 404 unknown org, 200 empty voor onbekende librechat_user_id, 200 empty voor user zonder active_template_ids, 200 met instructions in user-opgegeven volgorde.

#### C5 — `main.py`

`app.include_router(app_templates.router)` toevoegen in de normale include-block.

Acceptance hook: smoke test dat `/api/app/templates` 401 (niet 404) geeft zonder auth.

#### C6 — Provisioning orchestrator step `defaults_templates`

In `app/services/provisioning/orchestrator.py`, na de KB-provisioning step:

```
try:
    await mark_step_start(db, org.id, step="defaults_templates")
    await transition_state(db, org.id, new_state="seeding_templates")
    await ensure_default_templates(org_id=org.id, created_by="system", db=db)
    await db.commit()
    await mark_step_complete(db, org.id, step="defaults_templates")
except Exception:
    logger.warning("defaults_templates_step_failed", org_id=org.id, exc_info=True)
    # non-fatal: lazy-seed in GET /api/app/templates fangt dit op
```

Belangrijk: géén nieuwe state-machine-state als value in `provisioning_status` die een downstream-enforcer zou kunnen breken — gebruik `seeding_templates` als tussen-state consistent met bestaand patroon, en zorg dat de volgende step eroverheen transitioneert.

Acceptance hook: `tests/test_provisioning_orchestrator_templates.py` — bij succes 4 rows in `portal_templates`; bij geforceerde `ensure_default_templates` exception wordt provisioning voltooid.

#### C7 — Tests

Verzamelt alle `tests/*.py` uit C1-C6. Tests moeten groen in CI voordat Fase D start.

---

### Fase D — LiteLLM Hook

Doel: template-instructies landen daadwerkelijk in het system message van de chat-call.

#### D1 — Environment variables

Toevoegen aan `deploy/litellm/klai_knowledge.py`:

```python
PORTAL_TEMPLATES_URL = os.getenv("PORTAL_TEMPLATES_URL", f"{PORTAL_API_URL}/internal/templates/effective")
TEMPLATES_TIMEOUT = float(os.getenv("TEMPLATES_TIMEOUT", "2.0"))
```

Plus documentatie in de environment-template die ops gebruikt voor de deploy.

#### D2 — `_get_templates` helper

```python
async def _get_templates(
    org_id: str,
    user_id: str,
    cache: dict,
) -> list[dict]:
    cache_key = f"templates:{org_id}:{user_id}"
    cached = cache.get(cache_key)
    if cached is not None and cached["expires_at"] > time.time():
        return cached["value"]

    try:
        async with httpx.AsyncClient(timeout=TEMPLATES_TIMEOUT) as client:
            resp = await client.get(
                PORTAL_TEMPLATES_URL,
                params={"zitadel_org_id": org_id, "librechat_user_id": user_id},
                headers={"Authorization": f"Bearer {PORTAL_INTERNAL_SECRET}"},
            )
        if resp.status_code >= 500:
            logger.warning("templates_degraded", extra={"org_id": org_id, "user_id": user_id, "reason": f"http_{resp.status_code}"})
            return []
        resp.raise_for_status()
        instructions = resp.json().get("instructions", [])
    except (httpx.TimeoutException, httpx.HTTPError) as exc:
        logger.warning("templates_degraded", extra={"org_id": org_id, "user_id": user_id, "reason": type(exc).__name__})
        return []

    cache[cache_key] = {"value": instructions, "expires_at": time.time() + 30.0}
    return instructions
```

Cache is de bestaande in-process dict van de hook — dezelfde structuur als `_get_kb_feature`. Geen nieuwe global state.

#### D3 — System message injection

In de functie die het system message opbouwt (typisch `async_pre_call_hook` of een sub-functie):

```python
# Fetch templates — non-blocking, fail-open
templates = await _get_templates(org_id, user_id, _feature_cache)

# Build template-prefix
template_prefix = "\n\n".join(t["text"] for t in templates) if templates else ""

# Build KB-context block (bestaand pad)
kb_block = ...

# Assemble: [template_prefix] [kb_block] [original_system_content]
new_system_content = "\n\n".join(p for p in [template_prefix, kb_block, original_system_content] if p)
```

Let op: volgorde is **templates → KB → original system**. Templates komen eerst omdat ze de algemene stijl/toon dicteren; de KB-context is specifieker (per-query) en komt daarna.

Acceptance hook: integration test (dev-env docker-compose) met mock portal-api — bevestig dat LiteLLM-request log het template-prefix bevat; bij portal-api down: warning `templates_degraded` en chat-request slaagt alsnog.

---

### Fase E — Docs

Doel: architectuur-docs weerspiegelen de live realiteit.

#### E1 — `docs/architecture/platform.md`

Update de Templates-sectie. Kernpunten:

- Templates zijn een **productfeature voor response-styling** — geen guardrail, geen veiligheidslaag.
- De guardrail-/PII-laag (rules, block/redact) leeft in SPEC-CHAT-GUARDRAILS-001 en is orthogonaal aan deze SPEC.
- Beide systemen delen het patroon (`/internal/*/effective` + 30s cache + SCAN+DEL invalidatie), maar hebben gescheiden cache-key-prefixes (`templates:` vs `guardrails:`) en gescheiden tables.

#### E2 — `docs/architecture/knowledge-retrieval-flow.md`

In de "Rules and Templates" sectie:

- Voor v1: alleen Templates-injection beschrijven — endpoint, cache-sleutel, injection-volgorde (templates → KB → original system), fail-open-gedrag.
- Forward-reference naar SPEC-CHAT-GUARDRAILS-001 voor de Rules-injection flow die er in een volgende release bij komt.

Acceptance hook: docs peer-review; geen geautomatiseerde test nodig.

---

## Risks & Mitigations

| # | Risk | Likelihood | Impact | Mitigation |
|---|------|-----------|--------|------------|
| R1 | Alembic heeft momenteel 6 open heads in `main` — nieuwe migraties blokkeren anders | High | High | Fase A3 start expliciet met merge-migration vóór B2/B3 |
| R2 | Cache-invalidatie met SCAN kan traag zijn bij veel users per org | Low | Medium | Cursor-based SCAN met batch-pipeline; Redis-cluster handelt ~100 keys in < 10 ms. Monitoren via `templates_cache_invalidated` log-event; als > 100 ms: switch naar set-tracked keys |
| R3 | Admin-gate breekt de bestaande Jantine-flow (waar elke user `global` mocht aanmaken) | Low | Medium | Defaults worden geseed met `created_by="system"` — niet afhankelijk van een specifieke admin-user; bestaande personal-templates blijven werken |
| R4 | RLS strict breekt de lazy-seed in de list-endpoint (seed zonder tenant-context → 0 rows insert) | Medium | High | List-endpoint roept `set_tenant()` aan vóór `ensure_default_templates`; seed gebeurt binnen tenant-context. Expliciete test in `tests/test_app_templates.py::test_lazy_seed_respects_rls` |
| R5 | `prompt_text > 8000` via directe DB-write (bypassing API) | Low | Low | DB-level CHECK constraint als defence-in-depth (Pydantic is eerste linie, CHECK is laatste) |
| R6 | Default-seed failure tijdens provisioning | Medium | Low | Non-fatal try/except; lazy-seed in list-endpoint is safety-net |
| R7 | LiteLLM-hook timeout blokkeert chat | Low | High | `TEMPLATES_TIMEOUT=2.0s` + fail-open (leeg lijstje op timeout); expliciet gelogd als `templates_degraded`; existing KB-fetch latency is ongewijzigd |
| R8 | Ontbrekende `PortalUser.librechat_user_id` mapping laat chat falen | Medium | High | `/internal/templates/effective` returnt 200 met lege instructions bij onbekende user — expliciet gelogd maar chat gaat door |
| R9 | `set_tenant()` GUC-leak tussen requests in async pool | Low | High | Volg bestaand portal-api patroon — middleware unbinds GUC na request. Niet nieuw aan deze SPEC, wél gevalideerd in tests |
| R10 | Starlette middleware registration order (HIGH per python.md) | Low | Medium | Deze SPEC registreert geen nieuwe middleware — alleen router. Geen registration-order risico |
| R11 | Raw `prompt_text` in logs (privacy-leak) | Medium | Medium | Code-review checklist + grep-test in CI: `rg "prompt_text" klai-portal/backend/app --type py` must niet matchen in log-statements. Structlog kwargs gebruiken altijd `id` en `slug`, nooit `text` |
| R12 | Docs-drift tussen platform.md en implementatie | Medium | Low | Fase E is onderdeel van DoD; docs-review in dezelfde PR als de code-changes |

---

## MX Tag Plan

| File | Symbol | Tag | Reason |
|------|--------|-----|--------|
| `app/services/litellm_cache.py` | `invalidate_templates` | `@MX:ANCHOR` | fan_in = 4: CRUD create/update/delete (3) + `app_account.py` active_template_ids PATCH (1). Invariant: fail-open op Redis-fouten, nooit re-raise naar caller. |
| `app/services/litellm_cache.py` | `invalidate_templates` | `@MX:REASON` | `fan_in >= 3` en signature wijziging raakt alle callers; stabiel contract vereist |
| `app/api/internal.py` | `effective_templates` (missing-mapping fallback branch) | `@MX:WARN` | Chat-kritieke pad: ontbrekende `PortalUser` → 200 empty (niet 404) mag nooit per ongeluk een hard 404 worden |
| `app/api/internal.py` | `effective_templates` | `@MX:REASON` | Fail-safe contract met LiteLLM-hook: 404 = config-fout (org niet bestaand), 200-empty = runtime-fallback (user-mapping incompleet) |
| `app/services/default_templates.py` | `DEFAULT_TEMPLATES` (module-level list) | `@MX:NOTE` | Product-content: elke wijziging raakt default-seed voor alle NIEUWE tenants. Geen automatische migratie voor bestaande tenants |
| `app/services/provisioning/orchestrator.py` | `step_6b_defaults_templates` body | `@MX:NOTE` | Non-fatal stap: lazy-seed in `GET /api/app/templates` is de fallback. Failure mag provisioning niet blokkeren |
| `deploy/litellm/klai_knowledge.py` | `_get_templates` (fail-open branch) | `@MX:WARN` | Chat-kritieke pad: timeout of 5xx → return `[]`, nooit re-raise |
| `deploy/litellm/klai_knowledge.py` | `_get_templates` | `@MX:REASON` | Chat mag niet breken door portal-api downtime; 30s cache + fail-open is de garantie |
| Frontend wiring (vervolg-SPEC) | — | `@MX:TODO` | SPEC-CHAT-TEMPLATES-002 levert `/app/templates` pages + chat config-bar picker |

---

## Definition of Done (overzicht, details in acceptance.md)

- [ ] Alle 3 alembic-migraties land; `alembic heads` returnt exact 1 head.
- [ ] `portal_templates` tabel heeft RLS strict enabled (query zonder `set_tenant()` geeft 0 rows).
- [ ] CHECK constraint `char_length(prompt_text) <= 8000` aanwezig (geverifieerd via `\d+ portal_templates`).
- [ ] 4 default templates worden geseed bij nieuwe tenant-provisioning.
- [ ] Admin-gate op `scope="org"` geeft 403 voor non-admins (getest).
- [ ] Rate-limit 10 req/s per org geeft 429 met `Retry-After` (getest).
- [ ] Cache SCAN+DEL voltooit in < 100 ms voor org-scope invalidatie (getest, gemonitord).
- [ ] LiteLLM-hook injecteert template-prefix in system message vóór KB-block (integration test).
- [ ] LiteLLM-hook fail-open bij portal-api timeout (integration test met mock).
- [ ] Geen raw `prompt_text` in log-lines (grep-check in CI).
- [ ] Geen AskUserQuestion calls in nieuwe code (grep-check).
- [ ] `docs/architecture/platform.md` en `docs/architecture/knowledge-retrieval-flow.md` bijgewerkt en peer-reviewed.
- [ ] MX tags gezet volgens plan (zie MX Tag Plan tabel).
