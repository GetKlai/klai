---
id: SPEC-PORTAL-UNIFY-KB-001
version: "0.2.0"
status: implemented
created: 2026-04-23
updated: 2026-04-23
author: Mark Vletter
priority: high
source_inspiration: feat/chat-first-redesign (Jantine Doornbos) — commits 6ab4f2cd, 044cf810, 7bcdcf42, d312d93c
---

# SPEC-PORTAL-UNIFY-KB-001: Drop Focus, gate Knowledge per plan

## HISTORY

| Date | Version | Change |
|------|---------|--------|
| 2026-04-23 | 0.1.0 | Initiele draft na sparring-sessie. Focus wordt gedeprecateerd; Knowledge wordt het enige KB-oppervlak in het portal met harde limieten voor het `core` plan (5 KBs × 20 documenten per gebruiker, geen connectors / taxonomy / members / gaps). `professional` krijgt dezelfde limieten als `core` (blijft in catalog voor consistentie, niet actief in GTM). `complete` behoudt alles. Focus-data wordt niet gemigreerd — research-api volledig decommission (hard). Website is expliciet buiten scope. |
| 2026-04-23 | 0.2.0 | Post-merge polish. K2 race condition opgelost via `pg_advisory_xact_lock`. D4 grayed-out implementatie gedocumenteerd. Bekende beperkingen (K5 loading flash) opgenomen. Billing plan labels bijgewerkt na collision met andere plan-wijzigingen. Post-merge verificatie appendix toegevoegd. |

---

## Summary

Focus en Knowledge zijn in het product altijd als twee aparte features gepresenteerd, maar technisch doen ze hetzelfde: uploaden en vragen stellen met citations. We collapsen ze in één UI-oppervlak:

- Het hele `/app/focus/*` routeblok en de bijbehorende `research-api` service verdwijnen.
- `/app/knowledge/*` wordt het enige KB-surface voor iedere gebruiker.
- Elk plan bevat `knowledge`. Het `core` (en `professional`) plan is beperkt tot **5 KBs per gebruiker** met **20 documenten per KB**, geen connectors, taxonomy, members, of gaps.
- Het `complete` plan behoudt onbeperkt + connectors + shared write + advanced views.
- Advanced features die een gebruiker niet mag gebruiken worden **grijs** getoond (niet verborgen, niet clickable, tooltip on hover). Upgrade-flow zelf is een latere SPEC.

## Motivation

1. **Cognitieve overhead.** Twee surface-areas die 80% hetzelfde doen leveren support-vragen op ("waar moet ik dit uploaden?").
2. **Dubbel onderhoud.** Twee ingest-stacks, twee data-modellen, twee frontend-treks, twee test-suites.
3. **Product matcht pricing niet.** De pricing belooft `core` een "knowledge base met 5 KBs × 20 docs per user", maar vandaag heeft `core` alleen Focus, geen Knowledge. Deze SPEC trekt dat recht.

Jantine's werk op `feat/chat-first-redesign` is de visuele en interactie-inspiratie (divider-row lists, container-standaard, display-bold 26px h1, `KBScopeBar` met scope-filtering). We bouwen erop voort zonder `SPEC-PORTAL-REDESIGN-002` te dupliceren.

---

## Scope

### In scope

**Backend — plan + quota**

- `klai-portal/backend/app/core/plans.py`: elk plan bevat `knowledge` in de `PLAN_PRODUCTS` dict.
- Nieuwe module `klai-portal/backend/app/core/plan_limits.py` met per-plan capability + quota-tabel.
- Nieuwe `KBQuotaService` (of module-level helpers) die op create-time valideren:
  - `count personal KBs` < plan-limiet
  - `count items per KB` < plan-limiet
  - `owner_type="user"` verplicht voor core/professional
- Quota-enforcement in `app_knowledge_bases.py` bij `create_knowledge_base` én in de knowledge-ingest-flow bij document-add.
- Uitbreiding `app.api.dependencies.get_effective_products` met een capability-dimensie (nieuwe helper `get_effective_capabilities`) zodat routes zoals connectors/members/taxonomy/gaps capability-gebaseerd weigeren.
- Nieuw endpoint (of uitbreiding van het bestaande `internal.py` me-endpoint) dat de effective capabilities teruggeeft naar de frontend.

**Backend — system groups**

- `klai-portal/backend/app/core/system_groups.py`: labels herijken. "Chat + Focus" → "Chat". De groep met `["chat","knowledge"]` als default voor core.

**Backend — decommission research-api (hard)**

- `deploy/docker-compose.yml` — `research-api` service block weg.
- `deploy/volume-mounts.yaml` — research-api entries weg.
- `deploy/scripts/push-health.sh` + `klai-infra/core-01/scripts/push-health.sh` — research-api checks weg.
- SOPS — `RESEARCH_API_ZITADEL_AUDIENCE` + `KUMA_TOKEN_RESEARCH_API` weg (via de standaard SOPS decrypt-edit-encrypt workflow).
- `klai-portal/backend/app/api/proxy.py` — `proxy_research` handler weg.
- `deploy/README.md` + `deploy/VERSIONS.md` — refs weg.
- Docling note bijwerken (alleen nog consumer: knowledge-ingest).
- `klai-focus/` submodule blijft in git-tree voor historie, krijgt README-header "FROZEN — replaced by Knowledge per SPEC-PORTAL-UNIFY-KB-001". Niet meer gebuild, niet meer gedeployd.

**Frontend — route collapse**

- `klai-portal/frontend/src/routes/app/focus/` directory delete.
- Nieuwe `routes/app/focus.tsx` als redirect-only component → `/app/knowledge`.
- Sidebar (`routes/app/route.tsx`): "Focus"-item weg. "Kennis"/"Knowledge" blijft.

**Frontend — capability-guards (grayed, niet hidden)**

- `components/layout/ProductGuard.tsx` blijft product-level gate (hide-on-missing, bestaand gedrag).
- Nieuwe `ProductCapabilityGuard` component die binnen een Product scope op capability-niveau guard-t (bv. `capability="kb.connectors"`).
- Tabs in `routes/app/knowledge/$kbSlug/`:
  - `connectors.tsx` → capability `kb.connectors`
  - `members.tsx` → capability `kb.members`
  - `taxonomy.tsx` → capability `kb.taxonomy`
  - `advanced.tsx` → capability `kb.advanced`
- `routes/app/gaps/index.tsx` → capability `kb.gaps`.
- **Presentatie bij ontbrekende capability**: tab blijft zichtbaar in de tab-nav maar wordt grijs gerenderd (lagere opacity, `cursor-default`), niet clickable, en toont een tooltip on hover met korte message (bv. *"Beschikbaar op Klai Knowledge."*). Geen slotje-icoon, geen CTA-click, geen redirect — upgrade-flow is een separate SPEC (`SPEC-BILLING-UPGRADE-001`).
- Routes blijven server-side gaat de API-call 403 als capability ontbreekt. De grijze tab is alleen cosmetisch; de backend is de waarheid.

**Frontend — create-limits (grijs + tooltip, zelfde patroon)**

- KB-overzichtspagina (`routes/app/knowledge/index.tsx` of wherever de "+ Nieuwe kennisbank" CTA staat): knop wordt **grijs** als `user.plan === "core"` en `personal_kb_count >= 5`, met tooltip on hover *"Je hebt het maximum van 5 kennisbanken bereikt. Upgrade naar Klai Knowledge voor onbeperkt."*. Geen click-gedrag.
- `routes/app/knowledge/new.tsx`: blijft bereikbaar via directe URL, maar bij volle quota toont hetzelfde bericht als inline notice en is submit disabled. Org-KB radio/toggle is uberhaupt niet zichtbaar voor core.
- `routes/app/knowledge/$kbSlug/items.tsx`: upload-knop wordt **grijs** bij `item_count >= 20`, tooltip *"Deze kennisbank bevat al 20 documenten (maximum). Upgrade naar Klai Knowledge voor onbeperkt."*.
- Voor `complete` gebruikers: geen limiet-UI wijzigingen; alles blijft onbeperkt zoals vandaag.

**Frontend — Knowledge-as-landing**

- `routes/app/index.tsx` (app landing): de tegel die nu naar Focus linkt wordt Knowledge. Core-users zien hun personal KB(s); complete-users zien ook shared KBs.
- `KBScopeBar` (bestaand, van Jantine): scopes die de user niet mag zien worden uitgefilterd, niet disabled getoond.

**Frontend — copy + i18n**

- Paraglide messages (`klai-portal/frontend/messages/nl.json` + `en.json`): nieuwe keys voor quota-tooltips (`kb_limit_tooltip_kb_count`, `kb_limit_tooltip_items`, `capability_tooltip_knowledge_only`). Bestaande `focus.*` keys die weg mogen.
- Sidebar labels: "Focus" weg. "Kennis" blijft.

**Telemetry**

- Nieuw `product_events` event `kb.quota_blocked` bij elke server-side 403 op create (met `reason: "kb_count" | "kb_items" | "org_kb_not_allowed"` en `plan`). Voedt upgrade-funnel dashboard.
- `focus.*` events emitters weg.

**Tests**

- Pytest-coverage ≥ 85% op `plan_limits`, `KBQuotaService`, en de nieuwe guard-paden in `app_knowledge_bases.py`.
- Playwright smoke: core-user (a) maakt 5 KBs, (b) 6e geeft grijze knop + tooltip, (c) ziet grijze connectors/members/taxonomy/gaps tabs met tooltip, (d) upload tot 20 items werkt, (e) 21e geeft grijze knop + tooltip. Complete-user: geen grijs, geen limiet.
- Playwright: oude URL `/app/focus` redirect naar `/app/knowledge`.

### Buiten scope (expliciet)

- **Website (`klai-website/`)** — buiten scope. Geen enkele wijziging.
- **Data-migratie Focus → Knowledge** — geen migratie; research-api-data mag weg.
- **Chat-first portal redesign** — `SPEC-PORTAL-REDESIGN-002`.
- **Rules + templates** — `SPEC-PORTAL-REDESIGN-002`.
- **LiteLLM klai_knowledge hook** — ongewijzigd.
- **Upgrade-flow met checkout / self-serve billing** — `SPEC-BILLING-UPGRADE-001`. Deze SPEC laat grijze elementen alleen een tooltip tonen, geen click-actie.
- **Org-scope quota** — deze SPEC doet alleen per-user quota.
- **Grandfathering van users die nu >5 personal KBs hebben** — bestaande data blijft bereikbaar; alleen nieuwe `create` wordt geweigerd.

---

## Design Decisions

### D1: Focus is dood, Knowledge is de enige surface

Geen "Klai Focus" in het portal. Alleen Knowledge. De split was historisch (research-api vs knowledge-ingest); technisch overbodig. Jantine had `/app/focus` al onder `ProductGuard product="chat"` gehangen — wij maken dat expliciet en weghalen de aparte stack.

### D2: Plan-catalog — iedereen krijgt knowledge, limieten doen het werk

```python
# plans.py
PLAN_PRODUCTS = {
    "core":         ["chat", "knowledge"],
    "professional": ["chat", "scribe", "knowledge"],
    "complete":     ["chat", "scribe", "knowledge"],
}
```

```python
# plan_limits.py — nieuw
from dataclasses import dataclass

@dataclass(frozen=True)
class KBLimits:
    max_personal_kbs_per_user: int | None   # None = onbeperkt
    max_items_per_kb: int | None
    can_create_org_kbs: bool
    capabilities: frozenset[str]  # kb.connectors, kb.members, kb.taxonomy, kb.advanced, kb.gaps

PLAN_LIMITS: dict[str, KBLimits] = {
    "core": KBLimits(
        max_personal_kbs_per_user=5,
        max_items_per_kb=20,
        can_create_org_kbs=False,
        capabilities=frozenset(),
    ),
    "professional": KBLimits(
        max_personal_kbs_per_user=5,
        max_items_per_kb=20,
        can_create_org_kbs=False,
        capabilities=frozenset(),
    ),
    "complete": KBLimits(
        max_personal_kbs_per_user=None,
        max_items_per_kb=None,
        can_create_org_kbs=True,
        capabilities=frozenset({"kb.connectors","kb.members","kb.taxonomy","kb.advanced","kb.gaps"}),
    ),
}
```

`professional` is bewust gelijkgetrokken met `core`. Scribe is buiten GTM-scope; de plan-entry moet consistent zijn voor als het later wordt geactiveerd.

### D3: Limits per-user, niet per-org

Quota-query: `count(*) from portal_knowledge_bases where owner_type='user' and owner_user_id=:caller_id`. Org-KBs tellen niet mee. Op core/professional mag een user toch geen org-KBs maken.

Consequentie: een bedrijf met 10 core-users heeft potentieel 50 KBs × 20 docs = 1000 docs in totaal. Dit matcht de pricing-belofte.

### D4: Grayed out ipv hidden voor ontbrekende capability / quota

Twee motieven komen samen:

1. **Ontdekbaarheid** — een core-user moet kunnen zien dat er meer is (Connectors, Members, etc.) zonder dat we de ruimte vol plakken met locked-icon CTA's.
2. **Minimalisme** — Jantine's design-filosofie (divider-row lists, geen overbodige chrome) verdraagt geen aggressive upsell-UI.

Compromis: elementen die niet beschikbaar zijn worden **grijs gerenderd** (lagere opacity, `cursor-default`), **niet clickable**, en tonen een **tooltip on hover** met een korte uitleg. Voor deze SPEC is dat het enige upgrade-signaal. De echte upgrade-flow (click → checkout → payment) is een latere SPEC.

Dit geldt voor:
- Premium-capability tabs (connectors, members, taxonomy, advanced, gaps)
- "+ Nieuwe kennisbank" knop bij quota-bereikt
- "Item toevoegen" / upload-knop bij 20-items limiet

Server-side blijft strikt: ook als de frontend een knop per ongeluk clickable maakt, geeft de backend 403.

#### Implementatie-notitie (v0.2.0 — wat daadwerkelijk is uitgeleverd)

- Tabs en knoppen worden grijs via `opacity-50` + `cursor-default` + `pointer-events-none` op de wrapper.
- Tooltip on hover via de bestaande `components/ui/tooltip` component (of het `title`-attribuut als fallback).
- `aria-disabled="true"` + `data-capability-guard=<cap>` attributen gezet voor accessibility en testbaarheid.
- Geen lock-icoon. Geen klikbare upgrade-CTA — dat is `SPEC-BILLING-UPGRADE-001`.

### D5: Routes — oude focus-URLs redirecten

Alle `/app/focus/*` routes → 301 redirect (react-router `redirect()`) naar `/app/knowledge`. Geen externe users op Focus, maar interne testers hebben bookmarks.

### D6: Research-api decommission is hard

Geen graceful shutdown, geen read-only fase, geen data-export. Research-api-data mag weg. Volgorde:

1. Frontend met Knowledge-only naar prod.
2. Monitor `/api/research/*` traffic een paar dagen (VictoriaLogs: `service:portal-api AND path:/api/research/*`).
3. Research-api weg uit deploy-config, proxy-handler weg, SOPS-vars weg.
4. Deploy. Container stopt. Volumes worden niet automatisch verwijderd door Docker; handmatige cleanup is een aparte handeling.

`klai-focus/` submodule blijft in de tree voor geschiedenis.

### D7: UI volgt Jantine's design-spine

Geen nieuwe design-tokens. Nieuwe UI (tooltips, grijze states, inline notices) gebruikt:
- Container: `mx-auto max-w-3xl px-6 py-10` voor lijsten, `mx-auto max-w-lg px-6 py-10` voor forms
- H1: display-bold 26px
- Buttons: `rounded-full` pills, sentence-case
- Disabled/grayed: lagere opacity, `cursor-default`, tooltip via `title` attribute of bestaande tooltip-component (`components/ui/tooltip`)

### D8: Bestaande personal-KB auto-provisioning blijft

`_resolve_personal_kb` maakt nog steeds automatisch een personal-KB als een user er geen heeft. Quota geldt alleen op *expliciete* `create_knowledge_base` call, niet op deze fallback. Elke user heeft dus gegarandeerd minstens één personal-KB.

Edge case: gebruikers die vandaag >5 personal KBs hebben houden die — alleen nieuwe create wordt geweigerd. Geen retroactieve cleanup.

---

## EARS Requirements

### Ubiquitous

- **R-U1.** Het systeem geeft elke user toegang tot ten minste zijn eigen personal KB via `/app/knowledge`.
- **R-U2.** De sidebar toont het "Kennis"-item aan elke ingelogde user.
- **R-U3.** Er komen geen nieuwe refs naar "focus" als product, feature-naam of capability in code of i18n.

### Event-driven

- **R-E1.** Wanneer een core/professional user een 6e personal KB probeert te maken, wijst het systeem de create-call af met HTTP 403 `kb_quota_personal_kb_exceeded` en toont de frontend-knop grijs met tooltip.
- **R-E2.** Wanneer een core/professional user een 21e item probeert toe te voegen, geeft de ingest 403 `kb_quota_items_exceeded` en wordt de upload-knop grijs met tooltip.
- **R-E3.** Wanneer een core/professional user een org-KB probeert te maken, wijst het systeem af met 403 `kb_quota_org_kb_not_allowed`.
- **R-E4.** Wanneer een user een URL onder `/app/focus/*` opent, redirect het systeem via 301 naar `/app/knowledge`.
- **R-E5.** Wanneer een admin een user's plan van core → complete upgrade't, werken de effective capabilities binnen de volgende request bij (geen logout nodig).

### State-driven

- **R-S1.** Terwijl een user's effective plan `core` of `professional` is, worden Connectors-, Members-, Taxonomy-, Advanced- en Gaps-tabs **grijs** gerenderd, niet clickable, met tooltip.
- **R-S2.** Terwijl een user's effective plan `complete` is, zijn alle capabilities-tabs normaal clickable.
- **R-S3.** Terwijl de quota-teller op het maximum staat, is de "+ Nieuwe kennisbank" knop grijs met tooltip.
- **R-S4.** Terwijl een KB op `max_items_per_kb` zit, is de upload-knop grijs met tooltip.

### Optional

- **R-O1.** Het portal kan (toekomstig) per-org overrides op `PLAN_LIMITS` toepassen. Deze SPEC levert de signature `get_effective_limits(org_id)` maar gebruikt 'm nog niet.

### Unwanted

- **R-X1.** Geen deel van het portal doet HTTP-calls naar `/api/research/*` na deze SPEC. CI-grep-gate faalt als refs erin zitten.
- **R-X2.** Capability-gating lekt niet via de frontend; backend handhaaft onafhankelijk.
- **R-X3.** Quota-check discrimineert niet tussen create-paths — alle paden raken dezelfde quota-service.
- **R-X4.** Een core/professional user ziet nooit een org-KB in de KB-switcher.

---

## Bekende beperkingen

- **K5 — Optimistisch guard-rendering tijdens user-data loading**: `ProductCapabilityGuard` rendert zijn children ongewijzigd terwijl `useCurrentUser()` nog aan het laden is (korte flash voordat de grijze wrapper verschijnt). Geen security-impact — de backend capability-check is de autoriteit. Fix uitgesteld; indien visueel storend, vervang de early-return door een skeleton placeholder.

---

## Acceptance Criteria

1. **Plan-catalog**: `PLAN_PRODUCTS["core"]`, `["professional"]`, `["complete"]` bevatten allemaal `knowledge`.
2. **Quota backend**: `plan_limits.py` + `KBQuotaService` met pytest-coverage ≥ 85%. Weigert 6e personal-KB en 21e item voor core/professional.
3. **Capability backend**: `get_effective_capabilities` endpoint bestaat en retourneert juiste set. Connectors/Members/Taxonomy/Gaps endpoints geven 403 als capability ontbreekt.
4. **Route decommission**: `/app/focus/*` directory weg; stub redirect naar `/app/knowledge`. `research-api` service weg uit docker-compose, volume-mounts, health-checks, proxy.
5. **Frontend create-limits**: Playwright-scenario slaagt — core-user 5 KBs ok, 6e geeft grijze knop + tooltip, 20 items ok, 21e geeft grijze knop + tooltip. Complete-user onbeperkt.
6. **Frontend capability-guards (grayed)**: core/professional-user ziet Connectors/Members/Taxonomy/Gaps/Advanced tabs **grijs** met tooltip; niet verborgen, niet clickable. Complete-user: normaal clickable.
7. **System-groups**: "Chat + Focus" label verdwenen.
8. **i18n**: geen `focus.*` keys meer; tooltip-keys aanwezig (NL + EN compleet).
9. **TypeScript strict**: passeert zonder errors.
10. **Build**: portal-frontend build groen; portal-api + knowledge-ingest tests groen.
11. **Alembic**: `upgrade head` clean; geen research-api migrations nodig.
12. **Grep-gate**: `research-api`, `product="focus"`, `/api/research/` komen niet voor in active code (alleen in `klai-focus/` submodule en SPEC-geschiedenis).
13. **LSP**: nul errors in portal-frontend + portal-backend diff.

---

## Rollout fases

### Fase A — backend plan + capabilities

- `core/plan_limits.py` nieuw
- `core/plans.py` update
- `api/dependencies.py` capability-resolver
- `api/internal.py` (of me-endpoint) uitbreiden met capabilities
- `api/app_knowledge_bases.py` quota-enforcement
- `core/system_groups.py` labels
- Pytest suite

Gate: LSP + tests groen

### Fase B — frontend guards + route collapse

- `components/layout/ProductCapabilityGuard.tsx` nieuw (grayed tooltip-variant)
- `routes/app/focus/` directory delete
- `routes/app/focus.tsx` redirect-stub
- Knowledge-routes: capability-guards op tabs
- `routes/app/knowledge/index.tsx` + `new.tsx` + `$kbSlug/items.tsx` quota-UI (grijze knoppen + tooltips)
- `routes/app/index.tsx` landing-tiles
- `KBScopeBar` scope-filter op capabilities
- i18n keys: focus-refs weg, tooltip-keys erbij

Gate: Playwright smoke groen

### Fase C — research-api decommission

- `deploy/docker-compose.yml` — research-api block weg
- `deploy/volume-mounts.yaml` — entries weg
- `deploy/scripts/push-health.sh` + core-01 copy — checks weg
- SOPS — RESEARCH_API_* weg (standaard decrypt-edit-encrypt flow)
- `klai-portal/backend/app/api/proxy.py` — `proxy_research` weg
- `deploy/README.md` + `deploy/VERSIONS.md` — refs weg
- Deploy via normale CI. Verify op core-01: `docker ps | grep research-api` leeg; `/app/focus` redirect; core-user flow test.

Gate: observability bevestigt geen 404/500's op `/api/research/*` paden; monitoring-dashboard schoon.

### Fase D — cleanup + docs

- Paraglide hergenereren
- `klai-portal/CHANGELOG.md` entry
- `klai-focus/README.md` FROZEN-header
- CodeIndex re-analyze

---

## Testing

### Unit / API (pytest)

- `test_plan_limits.py` — tabel-integrity per plan-key, capability-strings valide.
- `test_kb_quota_service.py` — count-query edge-cases (leeg, exact op limit, boven limit).
- `test_app_knowledge_bases_quota.py` — HTTP 403 paden + response-body schema (`error_code: kb_quota_personal_kb_exceeded` etc.).
- `test_capabilities.py` — `get_effective_capabilities` per plan, admin-override.
- `test_proxy_research_removed.py` — `/api/research/*` geeft 404.

### Playwright (E2E)

Scenario's tegen dev-stack met seed-users per plan:

1. Core-user maakt 5 personal KBs — alle slagen.
2. Core-user probeert 6e — knop grijs met tooltip, geen netwerk-call.
3. Core-user upload 20 items in KB-slot-3 — alle slagen.
4. Core-user 21e item — upload-knop grijs met tooltip.
5. Core-user in KB-detail ziet Connectors/Members/Taxonomy/Gaps/Advanced tabs **grijs** met tooltip, niet clickable.
6. Core-user naar `/app/focus/new` → geredirect naar `/app/knowledge`.
7. Core-user naar `/app/focus/some-old-notebook-id` → geredirect naar `/app/knowledge`.
8. Complete-user maakt 8 KBs, upload 50 items in één — alles slaagt.
9. Complete-user ziet alle tabs normaal clickable.
10. Admin wijzigt plan core → complete. Volgende page-refresh: tabs worden clickable zonder re-login.

### Grep-gates in CI

- `rg "research-api" -g '!klai-focus/**' -g '!**/.moai/**' -g '!CHANGELOG.md'` leeg.
- `rg "product=['\"]focus['\"]" klai-portal/frontend/src/` leeg.
- `rg "/api/research/" klai-portal/` leeg.

### Observability

- VictoriaLogs: `service:portal-api AND error_code:kb_quota*` — quota-logic wordt geraakt.
- `service:portal-api AND path:/api/research/*` — naar 0 na Fase C.
- `level:error AND service:portal-api AND message:*focus*` — sanity.
- Grafana product_events: `SELECT event_type, COUNT(*) FROM product_events WHERE event_type LIKE 'kb.quota%' GROUP BY 1`.

---

## Non-goals (elk een eigen SPEC indien nodig)

- **`SPEC-BILLING-UPGRADE-001`** — self-serve upgrade-flow. Deze SPEC levert alleen grijze elementen + tooltip; een klikbare upgrade-CTA komt hier.
- **`SPEC-KB-ORG-QUOTA-001`** — org-wide KB-limits.
- **`SPEC-PORTAL-GRANDFATHER-001`** — per-org overrides op `PLAN_LIMITS`. De stub `get_effective_limits(org_id, db)` in `app/core/plan_limits.py` is al aanwezig.
- **`SPEC-KB-EXPORT-001`** — user-facing KB-export zodat core-users tegen de limiet zelf kunnen schonen.
- **`SPEC-RESEARCH-API-ARCHIVE-001`** — mocht de `klai-focus/` submodule ooit volledig uit de tree worden verwijderd.

*Opgeloste non-goals (verwijderd in v0.2.0):*
- ~~`SPEC-KB-QUOTA-ATOMICITY-001`~~ — de quota race condition (K2) is opgelost in de v0.2.0 polish via `pg_advisory_xact_lock`. Geen aparte SPEC nodig.

---

## References

**Jantine's inspiratie:**
- `6ab4f2cd` — feat(restore): bring back Jantine's chat-first UI + rules/templates/knowledge work
- `044cf810` — style(portal): center + tidy admin + focus pages (container-standaard)
- `7bcdcf42` — style(portal): restyle rules + templates as divider-row lists
- `d312d93c` — feat(chat-config-bar): add template picker + rules status chip
- `628069b1` — fix(chat-config-bar): KBScopeBar pattern

**Relevante SPECs:**
- `SPEC-PORTAL-REDESIGN-002` — chat-first redesign (orthogonaal)
- `SPEC-CHAT-GUARDRAILS-001` — rules-enforcement (orthogonaal)
- `SPEC-CHAT-TEMPLATES-001` — templates-injection (orthogonaal)

**Code-ankers:**
- `klai-portal/backend/app/core/plans.py`
- `klai-portal/backend/app/core/system_groups.py`
- `klai-portal/backend/app/api/app_knowledge_bases.py` — `_resolve_personal_kb`
- `klai-portal/backend/app/api/proxy.py` — `proxy_research`
- `klai-portal/frontend/src/components/layout/ProductGuard.tsx`
- `klai-portal/frontend/src/routes/app/focus/` — te verwijderen
- `klai-focus/research-api/` — te decommissionen
- `deploy/docker-compose.yml` — research-api service block
- `deploy/volume-mounts.yaml` — research-api volumes

---

## Post-merge verificatie (v0.2.0 — appendix)

### Gemerge PRs

- **Portal PR #117** → main: `7f44784d` (SPEC-PORTAL-UNIFY-KB-001 implementatie)
- **klai-infra PR #2** → main: `f7e1fc2` (research-api decommission infrastructure)

### Productie verificatie

- `PLAN_LIMITS` in draaiende portal-api container matcht spec-waarden (`core`: 5 × 20, `complete`: None × None)
- `/api/research/*` endpoints geven 404 terug — research-api container niet meer actief op core-01
- `docker ps | grep research-api` op core-01 leeg (container is gestopt en verwijderd)
- `/app/focus` redirect naar `/app/knowledge` werkzaam in productie

### Bekende openstaande issues na merge

- **K2 (race condition)** — opgelost in polish PR via `pg_advisory_xact_lock` in `assert_can_create_personal_kb`. Zie HISTORY v0.2.0 en `TestAdvisoryLockPersonalKB` in `tests/test_kb_quota_service.py`.
- **K5 (loading flash)** — gedocumenteerd als bekende beperking; uitgesteld naar toekomstige iteratie.
