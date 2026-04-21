---
spec: SPEC-DOCS-001
version: 1.0.0
status: draft
created: 2026-04-16
updated: 2026-04-16
author: Mark Vletter
priority: high
---

# SPEC-DOCS-001 — Implementation Plan

Dit plan beschrijft de structurele refactor van de KB/Docs editor in prioriteitsgestuurde milestones. Tijdsschattingen zijn bewust weggelaten — afhankelijkheden en prioriteit bepalen de volgorde, niet klokduur.

---

## Milestones (op prioriteit)

### Primary Goal — Backend idempotency + gecombineerde response

Doel: elimineert duplicate page creation aan de bron en reduceert het aantal roundtrips dat de frontend nodig heeft voor state-refresh.

Ownership: backend

Taken:

1. **P1.1** — Voeg `Idempotency-Key` header ondersteuning toe aan het page creation endpoint in `klai-portal/backend/app/api/docs.py`.
   - Accepteer header via FastAPI `Header(...)` dependency
   - Sla sleutel + response op in korte TTL cache (bv. Redis, TTL 24h) of in de `page_index` tabel met unieke index
   - Bij recurring key binnen TTL → retourneer oorspronkelijke response (HTTP 200)
   - Bij ontbrekende header → HTTP 400
2. **P1.2** — Wijzig de page creation response naar `{ page, pageIndex }`.
   - `page` bevat de nieuwe/bestaande page record (id, slug, title, icon, ...)
   - `pageIndex` bevat de volledige slug ↔ UUID mapping ná de mutatie
   - Documenteer in OpenAPI schema
3. **P1.3** — Voeg slug-naar-UUID redirect toe aan `GET /pages/{slug}` endpoint.
   - Wanneer path param een slug is die in `page_index` bestaat → 301 redirect naar `/pages/{uuid}`
   - Wanneer path param een UUID is → huidige gedrag
4. **P1.4** — Backend tests voor alle bovenstaande: pytest + coverage ≥ 85% op gewijzigde modules.

### Secondary Goal — Frontend creation-lock + synchrone pageIndex update

Doel: elimineert dubbel-POST bij snelle clicks en zorgt dat `pageIndex` altijd verse data bevat bij navigatie.

Ownership: frontend

Afhankelijkheden: Primary Goal afgerond (backend retourneert `{ page, pageIndex }`)

Taken:

1. **P2.1** — Introduceer `creationLock` state (useRef of useState) in `route.tsx` `handleNewPage`.
   - Flag wordt true gezet bij start request
   - Flag wordt gereset in `finally` block (success + error)
   - Tweede invocation tijdens lock → early return (no-op)
2. **P2.2** — Genereer `Idempotency-Key` (crypto.randomUUID()) per logische user-action en stuur mee als header.
3. **P2.3** — Vervang het na-save-refetch pattern door `setPageIndex(response.pageIndex)` — synchroon met server-truth.
4. **P2.4** — Zorg dat `await setPageIndex(...)` logisch klaar is vóórdat `navigateToPage(uuid)` wordt aangeroepen.
5. **P2.5** — Vitest unit tests voor `handleNewPage` dubbele invocation en pageIndex synchronisatie.

### Secondary Goal — Pre-navigation save guarantee

Doel: elimineert silent failures van `doSaveRef` en zorgt voor duidelijke feedback bij save-problemen.

Ownership: frontend

Afhankelijkheden: geen harde — parallel met creation-lock mogelijk

Taken:

1. **P3.1** — Evalueer TanStack Router `beforeLoad` als vervanging voor `doSaveRef` pattern.
   - Prototype in een losse branch; meten: werkt `beforeLoad` voor sidebar clicks én voor programmatic navigate?
   - Als ja → migreer sidebar navigatie naar `beforeLoad` guard
   - Als nee → wrap `doSaveRef.current?.()` in expliciete error handling + user feedback
2. **P3.2** — Implementeer save-status state in `KBEditorContext`: `idle | saving | error`
   - Bij error-status: toon toast + blokkeer navigate totdat retry succesvol is of user dismisses
3. **P3.3** — Verwijder alle silent `void`-prefixed save-calls uit navigatiepaden.
4. **P3.4** — Unit tests voor save-error propagation in navigatie flow.

### Tertiary Goal — Strict resolveSlug + URL canonicalisation

Doel: één URL schema, geen ambigue fallbacks.

Ownership: frontend

Afhankelijkheden: Primary Goal (backend redirect) moet live zijn vóór strict mode actief wordt

Taken:

1. **P4.1** — Wijzig `resolveSlug` in `lib/kb-editor/tree-utils.ts`:
   - UUID in → zoek in `pageIndex` → return UUID of throw "not-in-index" error
   - Slug in → zoek in `pageIndex` → return UUID of throw "not-in-index" error
   - Geen fallback naar "behandel input als slug"
2. **P4.2** — Behandel de error in `$pageId.lazy.tsx`: toon "page not found" UI met link terug naar KB root.
3. **P4.3** — Update alle call sites van `navigateToPage` om UUID's te verwachten (geen slugs).
4. **P4.4** — Integration test: bookmark met oude slug-URL → browser volgt 301 → UUID URL → pagina rendert.

### Optional Goal — Atomic tree-operations

Doel: voorkom inconsistente tree-state bij gelijktijdige reorder + create.

Ownership: frontend

Afhankelijkheden: Primary + Secondary goals

Taken:

1. **P5.1** — Serialiseer tree-mutations: wacht op afgeronde drag-and-drop API call voordat nieuwe create wordt toegestaan.
2. **P5.2** — Als tree-mutation faalt en revert plaatsvindt, verwerp pending creates die op optimistic state leunden; toon retry-knop.
3. **P5.3** — Regressie-tests voor race scenarios.

---

## Dependencies tussen taken

```
P1.1 ─┐
P1.2 ─┼──► P2.1 ─► P2.2 ─► P2.3 ─► P2.4 ─► P2.5
P1.3 ─┘         │
                └──► P4.1 ─► P4.2 ─► P4.3 ─► P4.4
P1.4 (backend tests) loopt parallel aan P1.1/P1.2/P1.3

P3.1 ─► P3.2 ─► P3.3 ─► P3.4  (parallel aan P2 track)

P5.* afhankelijk van P2 + P3 volledig
```

Kritiek pad: P1.1 → P1.2 → P2.3 → P2.4 (levert kern-stabiliteit).

---

## Technische aanpak

### Backend

- FastAPI `Header(..., alias="Idempotency-Key")` voor header capture
- Idempotency storage: **Redis** met `SET NX EX 86400` (24h TTL) — past bij bestaande Klai Redis infra; fallback tabel `idempotency_keys` met unique index als Redis niet gewenst is
- Gecombineerde response via bestaande Pydantic model composition: `CreatePageResponse(page: Page, pageIndex: PageIndex)`
- Slug-redirect via FastAPI `RedirectResponse(status_code=301)` bij `GET /pages/{key}`

### Frontend

- `crypto.randomUUID()` voor Idempotency-Key — web-standaard, geen dependency
- `creationLock` via `useRef<boolean>(false)` — renderloos, geen re-render nodig
- `setPageIndex` als synchrone state-setter; backend-truth prevaleert boven optimistic
- `beforeLoad` patroon: TanStack Router router.ts voor pre-nav hooks (zie context7 docs)

### Migratie

- Bestaande slug-gebaseerde bookmarks blijven werken via 301 redirect — geen data migratie
- Storage-format van pagina's blijft ongewijzigd
- Feature flag overwogen voor strict `resolveSlug` mode — off → on als alle endpoints de redirect ondersteunen

---

## Referentie-implementaties in de codebase

Gebruik bestaande patronen als vertrekpunt — dit is géén greenfield werk.

| Patroon                         | Referentielocatie                                                                                          |
|---------------------------------|------------------------------------------------------------------------------------------------------------|
| Idempotency-Key header dep      | `klai-portal/backend/app/api/` (zoek bestaande dependencies) en FastAPI docs                               |
| Redis NX-SET patroon            | Bestaande Redis usage in `klai-portal/backend/app/services/` (e.g. session storage)                        |
| Gecombineerde response model    | Bestaande response schemas in `klai-portal/backend/app/models/` of `/app/schemas/`                         |
| 301 redirect op slug            | FastAPI `RedirectResponse` — documenteren in `docs.py`                                                     |
| TanStack Router `beforeLoad`    | Bestaande routes in `klai-portal/frontend/src/routes/` — zoeken met `grep -r "beforeLoad" src/routes/`    |
| `useRef` lock pattern           | `route.tsx` `doSaveRef` is het huidige voorbeeld (wordt vervangen)                                         |
| Error-toast patroon             | Bestaande Mantine `notifications.show` aanroepen in `frontend/src/lib/`                                    |
| Vitest async test patterns      | Bestaande tests in `klai-portal/frontend/src/**/__tests__/` of adjacent `.test.tsx` files                  |

Aanbevolen research vóór implementatie:

- `context7` docs voor TanStack Router `beforeLoad` (laatste stable: v1)
- Bestaande Klai Redis helpers om dubbelwerk te vermijden
- `BlockPageEditor.tsx` `getContent()` interactie met `doSave` — bevestig dat save-errors worden gethrown, niet geswallowed

---

## Risicoanalyse

| Risico                                                                                         | Impact    | Mitigatie                                                                                                      |
|------------------------------------------------------------------------------------------------|-----------|----------------------------------------------------------------------------------------------------------------|
| `beforeLoad` werkt niet voor alle navigatiescenario's (programmatic + sidebar)                 | Medium    | Prototypen in aparte branch, fallback naar expliciete save-status check (P3.2)                                 |
| Idempotency-Key collisions bij klant-zijde bugs                                                | Low       | Server-side collision handling: zelfde slug + dezelfde user → behandelen als same intent, log waarschuwing     |
| Redis-outage breekt page creation                                                              | Medium    | Fallback op in-memory cache binnen request-scope; accepteer tijdelijke reduce van idempotency-garanties        |
| Legacy slug URL's die niet meer in `page_index` staan (verwijderde pagina's)                   | Low       | 301 redirect check: als slug niet bestaat → 404 met "page not found" UI                                        |
| Strict `resolveSlug` breekt bestaande edge-cases die we niet in tests hebben gevangen          | High      | Feature flag met gefaseerde rollout; monitor sentry/logs voor "not-in-index" errors                           |
| Tree-serialisatie (P5) introduceert perceptuele vertraging bij drag-and-drop                    | Low       | Optional goal — alleen implementeren als metriek laat zien dat race conditions voorkomen                       |
| Dubbele Idempotency-Key op retry door frontend bug                                              | Low       | Per-user-action genereren (nieuwe key bij user-initiated retry); geen key re-use over meerdere klikken         |

### Rollback-strategie

Elke milestone levert een isolated-merge branch met Feature flag waar nodig. Bij productie-regressie:

1. Feature flag uitzetten (strict mode off) → fallback naar huidige loose resolveSlug gedrag
2. Bij backend regressie: revert van `POST /pages` nieuwe versie — oude endpoint blijft side-by-side beschikbaar achter versioned API-path
3. Monitoring via VictoriaLogs: `service:portal-api AND path:/pages AND level:error` voor vroege detectie

---

## Definition of Done (plan-niveau)

- Alle requirements uit `spec.md` geïmplementeerd en verifieerbaar via `acceptance.md` scenarios
- Quality gates uit `spec.md#test-plan` groen
- Code review + minimaal één domain-expert review (frontend + backend)
- Productie-rollout met feature flag: eerst intern 48h, dan algemeen
- Geen regressie op bestaande KB-tests die al groen stonden
- VictoriaLogs dashboards tonen: duplicate-page-rate ≈ 0, empty-page-on-navigate rate ≈ 0
