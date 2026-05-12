---
id: SPEC-PORTAL-EXTENSIONS-UNIFY-001
version: "0.1.0"
status: ready-for-review
created: 2026-05-12
updated: 2026-05-12
author: Mark Vletter
priority: high
related:
  - SPEC-PORTAL-RBAC-001 (single-source derivation — wordt aangepast)
  - SPEC-PORTAL-RBAC-REFACTOR-001 (platform_unlocked_features — wordt single gating-laag)
  - SPEC-PORTAL-PROFILES-001 (5-rung profiel-ladder — basis blijft)
  - SPEC-WIDGET-002 (widgets + partner-API — gating consolidatie)
  - SPEC-SEC-IDENTITY-ASSERT-001 (klai_identity_assert response-schema — `enabled_addons` veld vervalt)
---

# SPEC-PORTAL-EXTENSIONS-UNIFY-001: Eén gating-laag voor uitbreidingen, sluit `partner_api` gap

## HISTORY

| Date | Version | Change |
|------|---------|--------|
| 2026-05-12 | 0.1.0 | Initial draft. Scope = unify `enabled_addons` + `platform_unlocked_features` in één laag (`platform_unlocked_features`), fix security gap op `/api/admin/api-keys`, tenant-visible status + superadmin tenant-picker op `/admin/settings`, DROP `enabled_addons` kolom. Prod-data uitgelezen 2026-05-12. |

---

## Summary

`portal_orgs` heeft vandaag **twee parallelle gating-kolommen** voor uitbreidingen:

- `enabled_addons` — bedoeld als tenant-admin self-service (vandaag: `scribe`, `docs`)
- `platform_unlocked_features` — Klai-staff gated (vandaag: `partner_api`, `widgets`, `custom_mcps`)

Per product-besluit (2026-05-12): **alle uitbreidingen zijn superadmin-only. Geen self-service.** Het `enabled_addons` concept was testing-scaffolding, niet productie-design. Daarmee zijn de twee lagen dubbelop en moet alles op `platform_unlocked_features` consolideren.

Daarbovenop één concrete security-gap: `/api/admin/api-keys` heeft géén `platform_unlocked_features`-check, ook al staat `partner_api` in `_KNOWN_FEATURES`. Voys (id=8) heeft daardoor een actieve `pk_live_*`-key zonder dat `partner_api` ooit is ontgrendeld. Bevestigd via prod-query 2026-05-12.

Deze SPEC consolideert beide concepten in `platform_unlocked_features`, dropt de `enabled_addons` kolom, sluit de api-keys gap, en geeft tenant-admins een read-only status van wat aanstaat — plus een tenant-picker voor superadmins op dezelfde pagina.

---

## Conceptueel kader (na deze SPEC)

| Laag | Wie zet aan/uit | Waar | Voorbeelden |
|---|---|---|---|
| **Plan** (`portal_orgs.plan`) | Billing/Stripe | `PLAN_FEATURES` in `core/features.py` | `chat`, `knowledge` |
| **Platform-unlocks** (`portal_orgs.platform_unlocked_features`) | Klai-superadmin (org-slug = `platform_org_slug`) | `_KNOWN_FEATURES` in `admin/platform_unlocks.py` | `partner_api`, `widgets`, `custom_mcps`, `scribe`, `docs` |
| **Profile-filter per feature** (`portal_users.role`) | Tenant-admin (binnen plan-ceiling) | `FEATURE_MIN_PROFILE` in `core/features.py` | `scribe`/`docs` → `company+`, `partner_api`/`widgets`/`custom_mcps` → `admin` |

Drie lagen, duidelijke verantwoordelijkheden. Niets self-service voor uitbreidingen.

---

## Prod-data (verified 2026-05-12)

```
 id |  slug   | enabled_addons | platform_unlocked_features
----+---------+----------------+-----------------------------
  1 | getklai | {docs, scribe} | {widgets, custom_mcps}
  8 | voys    | {}             | {}
```

Side-tabellen:
- `partner_api_keys`: voys=1 key (orphan — `partner_api` niet unlocked).
- `widgets`: getklai=2 widgets (consistent met `widgets` unlock).

Product-besluit voor migratie:
- **getklai** → `platform_unlocked_features = {widgets, custom_mcps, docs, scribe}` (union van bestaande state).
- **voys** → `platform_unlocked_features = {partner_api, widgets, custom_mcps, scribe, docs}` (per product owner: Voys mag alle features aan hebben).

---

## Motivation

1. **Twee parallelle gating-kolommen zonder gedrags-verschil.** Beide lijsten kunnen vandaag features bevatten die `derive_user_products` als equivalent zou behandelen — alleen de write-paden verschillen. Drift-risico permanent.
2. **`partner_api` gap op `/api/admin/api-keys`.** Beleid en code lopen 4+ weken uit elkaar. Voys heeft een actieve key zonder unlock. Elke tenant-admin kan vandaag live `pk_live_*`-keys aanmaken zonder review.
3. **`enabled_addons` werd nergens als business-besluit gebruikt.** De toggle bestaat alleen omdat hij ooit als testing-scaffolding was bedoeld. Het feit dat hij zo lang ongebruikt is gebleven zonder dat iemand het zag, is precies waarom dubbelop-kolommen geen blijvers mogen zijn.
4. **Geen tenant-zichtbaar overzicht.** Tenant-admins kunnen niet zien welke uitbreidingen aanstaan voor hun org. Superadmin kan dat ook niet zonder direct in de DB te kijken — er is geen UI voor `platform_unlocked_features` beheer.
5. **Tile-pollutie op `/admin/index.tsx`.** Tegels voor api-keys / widgets / mcps verschijnen altijd voor elke admin, ongeacht of de feature ge-unlocked is. Doorklikken geeft 403 (widgets/mcps) of 200 (api-keys, want bug). Slechte UX, en onbedoeld "preview" van features die de tenant niet heeft.

---

## Scope — vijf phases

### Phase 1: Security gap dichten (REQ-1)

**1A: Backend gate op `/api/admin/api-keys`.**

Alle 5 endpoints in `klai-portal/backend/app/api/admin_api_keys.py` (GET list, POST create, GET detail, PATCH, DELETE) krijgen `Depends(require_platform_unlocked("partner_api"))` toegevoegd náást de bestaande `Depends(get_caller_at_least(ProfileRole.ADMIN))`.

```python
@router.get("")
async def list_api_keys(
    perms: UserPermissions = Depends(get_caller_at_least(ProfileRole.ADMIN)),
    _platform: UserPermissions = Depends(require_platform_unlocked("partner_api")),
    db: AsyncSession = Depends(get_db),
) -> list[ApiKeyResponse]:
    ...
```

**1B: Test coverage.**

Nieuwe testset `test_admin_api_keys_platform_gate.py`:
- Tenant zonder `partner_api` → 403 op elk endpoint.
- Tenant mét `partner_api` → endpoint werkt normaal.
- Voys' bestaande key blijft na migratie zichtbaar (zie Phase 2).

**Deploy-volgorde [HARD]:** Phase 2 migratie MOET eerst draaien (zodat Voys' `partner_api` ge-unlocked is) vóór Phase 1-code deployt. Anders breekt Voys' bestaande key-admin met 403. Verwoord als deploy-check in PR-beschrijving.

### Phase 2: Datamodel unification + migratie (REQ-2, REQ-3)

**2A: `_KNOWN_FEATURES` uitbreiden.**

In `klai-portal/backend/app/api/admin/platform_unlocks.py`:

```python
_KNOWN_FEATURES = frozenset({
    "partner_api", "widgets", "custom_mcps",
    "scribe", "docs",   # nieuw — verhuisd uit ADDON_FEATURES
})
```

**2B: `derive_user_products` refactor.**

Functie-signatuur verandert:

```python
# BEFORE
def derive_user_products(role: str, plan: str, enabled_addons: list[str]) -> set[str]: ...

# AFTER
def derive_user_products(role: str, plan: str, platform_unlocked_features: list[str]) -> set[str]: ...
```

Body-logica identiek, alleen de naam van de set die door `FEATURE_MIN_PROFILE` wordt gefilterd. `ADDON_FEATURES` constant wordt verwijderd uit `core/features.py`.

**2C: `UserPermissions` veld weghalen.**

`UserPermissions.enabled_addons: frozenset[str]` verdwijnt. Alle reads (`permissions.py:151,164`, `internal.py:425,473`, `admin/settings.py:148`, `admin/products.py:93,96,111`) switchen naar `perms.platform_unlocked_features`.

**2D: `GET/PATCH /api/admin/settings/addons` endpoints verwijderen.**

Endpoints in `admin/settings.py:143-200` vervallen. Frontend hook `useEnabledAddons.ts` en de UI-code in `admin/settings.tsx:114-160` (de addons-checkbox-sectie) worden in Phase 4 vervangen.

**2E: `/internal/identity/permissions` response-schema.**

`enabled_addons: list[str]` veld vervalt uit `IdentityPermissionsResponse`. Geen consumer leest dit veld (geverifieerd 2026-05-12 via grep over `klai-libs/`, `klai-connector/`, `klai-retrieval-api/`, `klai-knowledge-ingest/`, `klai-mailer/`, `klai-knowledge-mcp/`, `scribe-api/`). Veilig om te verwijderen in dezelfde release.

**2F: Eenmalige data-migratie (Alembic post-deploy SQL).**

Bestand: `klai-portal/backend/alembic/versions/post_deploy_extensions_unify.sql`

```sql
BEGIN;

-- Statement A: kopieer enabled_addons → platform_unlocked_features (set-union, idempotent).
UPDATE portal_orgs
SET platform_unlocked_features = (
    SELECT array_agg(DISTINCT x ORDER BY x)
    FROM unnest(COALESCE(platform_unlocked_features, '{}') || COALESCE(enabled_addons, '{}')) AS x
)
WHERE enabled_addons IS NOT NULL
  AND cardinality(enabled_addons) > 0;

-- Statement B: per product owner 2026-05-12 — Voys volledig ontgrendeld.
UPDATE portal_orgs
SET platform_unlocked_features = ARRAY['partner_api','widgets','custom_mcps','scribe','docs']
WHERE slug = 'voys';

-- Verify (one row, expected new state):
-- getklai → {custom_mcps,docs,scribe,widgets}
-- voys    → {custom_mcps,docs,partner_api,scribe,widgets}

COMMIT;
```

**2G: DROP COLUMN — Alembic upgrade migration.**

Volgende migration (zelfde release):

```python
def upgrade():
    op.drop_column('portal_orgs', 'enabled_addons')

def downgrade():
    op.add_column(
        'portal_orgs',
        sa.Column('enabled_addons', postgresql.ARRAY(sa.Text()), nullable=False, server_default='{}'),
    )
```

`portal_orgs` is owner = `portal_api` (geen RLS-blocker — verifieer via `\dt+ portal_orgs` voor PR). Geen RLS-policies te updaten.

**2H: Code-residu opruimen.**

- `core/system_groups.py:5` comment update: `portal_orgs.platform_unlocked_features` in plaats van `portal_orgs.enabled_addons`.
- `models/portal.py:81-86` mapping en comments.
- `api/admin/users.py:248`, `api/admin/products.py:2-3,62`, `api/groups.py:5` comments.

### Phase 3: Backend extensions API (REQ-4, REQ-5, REQ-8)

**3A: Nieuw endpoint `GET /api/admin/extensions`.**

In nieuw bestand `klai-portal/backend/app/api/admin/extensions.py`:

```python
@router.get("/extensions", response_model=ExtensionsResponse)
async def list_extensions(
    org_slug: str | None = None,
    perms: UserPermissions = Depends(get_caller_at_least(ProfileRole.ADMIN)),
    db: AsyncSession = Depends(get_db),
) -> ExtensionsResponse:
    """Return all known extensions with enabled-status for the target org.

    - Without org_slug: returns status for caller's own org.
    - With org_slug: requires perms.is_platform_admin, returns status for target tenant.
    """
    if org_slug is not None and not perms.is_platform_admin:
        raise HTTPException(403, "cross-org query requires platform admin")

    target_org = await _resolve_target_org(perms, org_slug, db)
    unlocked = set(target_org.platform_unlocked_features or [])

    items = [
        ExtensionItem(
            key=key,
            label=_EXTENSION_LABELS[key],
            description=_EXTENSION_DESCRIPTIONS[key],
            enabled=key in unlocked,
            requires_profile=FEATURE_MIN_PROFILE[key],
            manageable_by_caller=perms.is_platform_admin,
        )
        for key in sorted(_KNOWN_FEATURES)
    ]
    return ExtensionsResponse(org_slug=target_org.slug, extensions=items)
```

**3B: `PATCH /api/admin/extensions` (superadmin-only).**

Delegeert naar bestaande `/api/admin/orgs/{slug}/platform-unlocks` PATCH-handler:

```python
@router.patch("/extensions", response_model=ExtensionsResponse)
async def update_extensions(
    body: UpdateExtensionsRequest,    # { org_slug: str, enabled_features: list[str] }
    perms: UserPermissions = Depends(require_platform_admin()),
    db: AsyncSession = Depends(get_db),
) -> ExtensionsResponse:
    # Reuse existing implementation from admin/platform_unlocks.py
    ...
```

Audit log via bestaande `tenant_lifecycle_events` (`platform_unlocks_updated` event).

**3C: Labels & descriptions registry.**

In `core/features.py` (of nieuw `core/extensions_meta.py`):

```python
_EXTENSION_LABELS: dict[str, str] = {
    "partner_api": "API-keys",
    "widgets": "Chat-widgets",
    "custom_mcps": "Custom MCP servers",
    "scribe": "Scribe — meeting-transcripties",
    "docs": "Docs — gedeelde KBs",
}

_EXTENSION_DESCRIPTIONS: dict[str, str] = {
    "partner_api": "Programmatische toegang via pk_live_* API-keys.",
    "widgets": "Embed chat-widget op klant-website.",
    "custom_mcps": "Eigen Model Context Protocol servers koppelen.",
    "scribe": "Automatische meeting-transcriptie.",
    "docs": "Documentatie-KBs delen binnen de organisatie.",
}
```

**3D: `/api/me` response uitbreiden (REQ-8).**

Tenant-frontend leest `is_platform_admin` en `platform_unlocked_features` vandaag NIET. Audit welk endpoint de frontend gebruikt (waarschijnlijk `/api/me` — `klai-portal/backend/app/api/me.py`). Voeg beide velden toe aan de `MeResponse`. Frontend `useAuth()` context absorbeert ze.

### Phase 4: Frontend UI (REQ-6, REQ-7)

**4A: `/admin/settings` — vervangende Uitbreidingen sectie.**

`klai-portal/frontend/src/routes/admin/settings.tsx`:
- Verwijder de huidige addons-checkbox-sectie (regels ~114-160).
- Verwijder de `useEnabledAddons.ts` hook.
- Voeg een nieuwe sectie "Uitbreidingen" toe die `GET /api/admin/extensions` aanroept.
- Voor non-platform-admin: render een read-only status-lijst (Card + Switch met `disabled` of een statisch indicator-icoontje). Status-tekst: "Beheerd door Klai".
- Voor `is_platform_admin`: bovenaan de sectie een tenant-picker (autocomplete via `GET /api/admin/orgs?query=...` of bestaande slug-API). Default-selectie = eigen org. Switch-changes triggeren `PATCH /api/admin/extensions` met `org_slug` + nieuwe set.

UI-spec volgt `klai-portal-ui` skill: components uit `components/ui/`, `text-[var(--color-destructive)]` voor error-toasts, i18n via `import * as m from '@/paraglide/messages'`.

**4B: `/admin/index.tsx` tegel-filtering.**

```tsx
const { effective_products, is_platform_admin, platform_unlocked_features } = useAuth()

const adminSections = [
    // ... users, groups always visible
    {
        title: m.admin_section_api_keys_title(),
        href: '/admin/api-keys',
        requiresFeature: 'partner_api',
    },
    {
        title: m.admin_section_widgets_title(),
        href: '/admin/widgets',
        requiresFeature: 'widgets',
    },
    {
        title: m.admin_section_mcps_title(),
        href: '/admin/mcps',
        requiresFeature: 'custom_mcps',
    },
    // ... billing, settings always visible
].filter(section =>
    !section.requiresFeature
    || is_platform_admin
    || platform_unlocked_features.includes(section.requiresFeature)
)
```

`/admin/route.tsx` nav-items krijgen dezelfde filter.

**4C: i18n strings.**

Nieuwe Paraglide messages voor section-labels, status-pills, "Beheerd door Klai", tenant-picker placeholder. NL + EN.

### Phase 5: Consistency audit & tests (REQ-9)

**5A: Gate-consistency audit.**

Verifieer alle drie gating-paden uniform:
- `admin_api_keys.py`: alle 5 endpoints na Phase 1 → `require_platform_unlocked("partner_api")`. ✓
- `admin_widgets.py`: alle 5 endpoints — `require_platform_unlocked("widgets")`. Bestaand. ✓
- `mcp_servers.py`: GET (catalog list) is platform-unlock-agnostisch (laat alle gebruikers ManagedMCPs zien), PATCH (mutate org-MCPs) gebruikt `assert_platform_unlocked(org, "custom_mcps")`. ✓ Geen wijziging.

**5B: Regression-test op `derive_user_products`.**

Snapshot-test die voor elke prod-tenant het `effective_products` resultaat berekent vóór én na refactor en assert dat ze identiek zijn:

```python
@pytest.mark.parametrize("role,plan,unlocked,expected", [
    ("admin", "complete", {"widgets","custom_mcps","docs","scribe"}, {"chat","knowledge","scribe","docs"}),
    ("personal", "complete", {"widgets","custom_mcps","docs","scribe"}, {"chat","knowledge"}),
    ("admin", "professional", {"partner_api","widgets","custom_mcps","scribe","docs"}, {"chat","knowledge","scribe","docs"}),
    # ... edge cases
])
def test_derive_user_products_snapshot(role, plan, unlocked, expected):
    assert derive_user_products(role, plan, sorted(unlocked)) == expected
```

**5C: ast-grep regression-guard.**

Nieuwe rule `rules/no-enabled-addons-read.yml`:

```yaml
id: no-enabled-addons-read
language: python
rule:
  pattern: $OBJ.enabled_addons
message: enabled_addons column is dropped; use platform_unlocked_features.
```

Gewired in `klai-portal/backend/.github/workflows/portal-api.yml` via `ast-grep/action`. Fail-on-match.

**5D: E2E flow.**

Playwright MCP test (binnen REQ-9):
- Login als platform-admin (Klai-account) → `/admin/settings` → toggle een feature voor Voys → reload → status reflecteert. Toggle terug → reload → terug.
- Login als tenant-admin op een test-tenant → `/admin/settings` → "Uitbreidingen" sectie is read-only.

---

## Acceptance Criteria

- [ ] Post-migratie prod-state:
    - `getklai.platform_unlocked_features = {custom_mcps, docs, scribe, widgets}`
    - `voys.platform_unlocked_features = {custom_mcps, docs, partner_api, scribe, widgets}`
- [ ] `portal_orgs.enabled_addons` kolom bestaat niet meer (`\d portal_orgs` → kolom afwezig).
- [ ] `GET /api/admin/api-keys` zonder `partner_api` in unlocks → 403; mét → 200.
- [ ] Voys' bestaande `partner_api_keys`-record blijft toegankelijk via admin-UI na deploy.
- [ ] `/admin/settings` als tenant-admin (test-tenant zonder platform-admin rechten) → "Uitbreidingen"-sectie is read-only, toont status van álle 5 features, met "Beheerd door Klai" indicatie.
- [ ] `/admin/settings` als platform-admin (Klai-account) → tenant-picker zichtbaar; switch-changes roepen `PATCH /api/admin/extensions` aan; reload bevestigt persisted state.
- [ ] `/admin/index.tsx` als tenant-admin → alleen tegels voor unlocked features zichtbaar. Als platform-admin → alle tegels zichtbaar.
- [ ] `derive_user_products` regression-test groen voor beide prod-tenants — pre/post refactor identiek.
- [ ] `ast-grep --rule rules/no-enabled-addons-read.yml klai-portal/backend/app/` → zero matches.
- [ ] Geen consumer van `/internal/identity/permissions` faalt op missend `enabled_addons` veld (grep clean voor klai-libs en alle services).
- [ ] CI groen: `ruff check`, `ruff format --check`, `pyright`, `pytest`.

---

## Out of Scope

- Tenant-impersonation flow. Tenant-picker op `/admin/settings` volstaat voor 2 tenants. Bij groei naar 10+ tenants → herwaarderen.
- Overzichtspagina "alle tenants × alle features" matrix. Bewust uitgesteld per product-besluit (2026-05-12: "3 niet nu").
- Stripe / billing-koppeling voor platform-unlocked features.
- Wijzigingen aan `FEATURE_MIN_PROFILE` policy (scribe/docs blijven `company+`, api-keys/widgets/mcps blijven `admin`).
- Per-user / per-group product-entitlement tables (al opgeheven in SPEC-PORTAL-RBAC-001).
- Grafana dashboard voor platform-unlocks tijdreeks. Audit-events lopen al via `tenant_lifecycle_events`.

---

## Rollout Volgorde

Vijf PRs, in volgorde:

1. **PR 1 — Phase 2 migratie + datamodel cleanup.** Migratie loopt eerst, code-refactor activeert in dezelfde release. Voys krijgt `partner_api` (en alles) ontgrendeld vóór Phase 1 de gate sluit. `enabled_addons` kolom drop'pt in dezelfde Alembic-chain.
2. **PR 2 — Phase 1 security gate op `/api/admin/api-keys`.** Klein, geïsoleerd. Mag pas na PR 1 om regressie te vermijden.
3. **PR 3 — Phase 3 extensions endpoint + `/api/me` velden.** Backend-only.
4. **PR 4 — Phase 4 frontend UI.** Tenant-picker, status-pills, tegel-filter. Vereist PR 3 voor data.
5. **PR 5 — Phase 5 tests + ast-grep guard.** Sluit het loket: regression-snapshot, no-enabled-addons-read rule, E2E flow.

Elke PR vóór merge: `gh run watch --exit-status` op groen, daarna verificatie op core-01 (bundle timestamp + container age) per `klai-portal/CLAUDE.md` deploy-rule. Voor PR 1 specifiek: post-deploy `SELECT slug, platform_unlocked_features FROM portal_orgs;` om te verifiëren dat beide rows matchen acceptance-criteria.

---

## Risico's en mitigaties

| Risico | Mitigatie |
|---|---|
| PR 2 deployt vóór PR 1 migratie → Voys breekt (geen `partner_api` unlocked, key-CRUD geeft 403). | PR-volgorde hard in beschrijving; CI-job die controleert dat de migratie heeft gedraaid voor PR 2-deploy; Voys' migratie-statement in PR 1 unlocked Voys voor álle features, dus PR 2 raakt Voys niet. |
| `/internal/identity/permissions` consumer breekt op missend veld. | Veld-grep over alle klai-services + klai-libs gedaan 2026-05-12, niemand leest het. Bij twijfel: backward-compat-window via lege list in Phase 2, drop in opvolg-PR. (Niet nodig op basis van audit, dus single-release.) |
| DROP COLUMN op een grotere tabel zou lock-contention veroorzaken. | `portal_orgs` heeft 2 rows. Niet relevant. |
| Frontend `useAuth` cache toont stale `platform_unlocked_features` na superadmin-toggle. | `PATCH /api/admin/extensions` response invalidate via TanStack Query — `queryClient.invalidateQueries({ queryKey: ['me'] })` direct ná success. |
| Bestaande SPECs (`SPEC-PORTAL-RBAC-REFACTOR-001`, `SPEC-PORTAL-PROFILES-001`) refereren naar `enabled_addons`. | Documentatie-update buiten code-scope. Comments in code worden in Phase 2-2H bijgewerkt; SPEC-docs blijven historisch correct. |

---

## Verifiability

Elke acceptance-criterium is binnen ~5 minuten verifieerbaar door:
- Prod-query op `portal_orgs.platform_unlocked_features`.
- `curl` op `/api/admin/api-keys` met en zonder unlock.
- Playwright MCP flow voor `/admin/settings` (twee accounts).
- `pytest klai-portal/backend/tests/test_features_derive.py -k snapshot`.
- `ast-grep --rule rules/no-enabled-addons-read.yml klai-portal/backend/app/`.

Geen acceptance-criterium vereist een handmatige UI-walkthrough zonder script-handvat — alles is automatiseerbaar.
