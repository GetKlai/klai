---
id: SPEC-DOCS-001
version: 1.0.0
status: draft
created: 2026-04-16
updated: 2026-04-16
author: Mark Vletter
priority: high
lifecycle: spec-anchored
title: KB Editor — Reliable page creation and navigation
---

# SPEC-DOCS-001 — KB Editor: Reliable page creation and navigation

## HISTORY

| Versie | Datum      | Auteur        | Wijziging                                             |
|--------|------------|---------------|-------------------------------------------------------|
| 1.0.0  | 2026-04-16 | Mark Vletter  | Initiële draft — structurele refactor KB/Docs editor  |

---

## Overview

De KB/Docs editor in Klai is een TanStack Router + BlockNote + FastAPI systeem waarmee gebruikers pagina's in een knowledge base kunnen aanmaken, bewerken en navigeren. Pagina's worden als files op disk opgeslagen (served door de Next.js `klai-docs` app) en geïndexeerd in de PostgreSQL `page_index` tabel met `slug`, `id` (UUID), `title` en `icon`.

Deze SPEC definieert een structurele refactor die vier terugkerende user-facing bugs elimineert: duplicate pages, lege pagina's na navigatie, silent save failures en inconsistente URL routing. De refactor verandert GEEN UI, i18n of editor-functionaliteit — alleen het control-flow gedrag rond page creation, pageIndex management en navigatie.

Scope: frontend control flow (TanStack Router routes, KBEditorContext, tree-utils) en backend page creation endpoint (idempotency + gecombineerde response).

---

## Problem Statement

De huidige KB editor heeft vijf structurele problemen die tot concrete, reproduceerbare user-facing bugs leiden.

### Probleem 1 — Non-atomic page creation veroorzaakt duplicate pages

`handleNewPage` in `route.tsx` voert drie separate async stappen uit:

1. `await doSaveRef.current?.()` — huidige pagina opslaan
2. `await apiFetch(PUT /pages/slug)` — nieuwe pagina aanmaken
3. `refetchTree()` + `navigateToPage(slug)` — sidebar refreshen, navigeren

Er is geen idempotency key. Racecondition: bij dubbelklik, of wanneer stap 2 slaagt maar stap 3 faalt (waarna de gebruiker retryt), wordt een tweede PUT uitgevoerd → tweede pagina aangemaakt. De backend heeft geen duplicate prevention.

### Probleem 2 — Stale `pageIndex` veroorzaakt lege pagina's bij navigatie

`pageIndex` (slug ↔ UUID mapping) wordt alleen ververst via `void refetchPageIndex()` binnen `doSave()`. Als de gebruiker navigeert zonder te hebben opgeslagen, of direct na het aanmaken van een pagina, bevat `pageIndex` de nieuwe pagina mogelijk nog niet. `resolveSlug()` valt dan terug op het behandelen van de pageId als slug — wat een UUID kan zijn — en de backend ontvangt een UUID in plaats van een slug en retourneert verkeerde of lege data.

### Probleem 3 — `doSaveRef` pattern heeft silent failure mode

`doSaveRef` is een mutable `useRef` die de `saveNow` functie van de huidige pagina vasthoudt. Als de ref `null` is op het moment van aanroepen (component niet gemount, of ref nog niet gezet), wordt de pre-navigation save stilzwijgend overgeslagen. Geen error handling, geen retry, geen user feedback.

### Probleem 4 — Twee coëxisterende URL-schema's met incomplete resolution

URL's zijn ofwel `/docs/$kbSlug/my-page-slug` (oud) of `/docs/$kbSlug/a1b2c3d4-uuid` (nieuw). De `resolveSlug()` functie heeft een fallback-chain die foute resultaten kan produceren wanneer `pageIndex` stale is. Dit is een secundaire oorzaak van lege pagina's.

### Probleem 5 — Tree-operaties zijn niet atomair

Sidebar drag-and-drop doet een optimistic update plus async API-call. Nieuwe page creation leest uit de optimistic tree state. Als de API-call faalt en de tree revert, zijn pagina's die daarbovenop gemaakt zijn in een inconsistente toestand.

### Impact op gebruikers

- Duplicate pages verschijnen in de sidebar en vereisen handmatige opschoning
- Lege pagina's vlak na navigatie leiden tot vermeend dataverlies
- Silent save failures betekenen dat typewerk verloren gaat zonder waarschuwing
- Bookmarks of gedeelde links met oude slug-URL's kunnen onverwacht gedrag vertonen

---

## Goals

1. **Idempotente page creation** — dubbelklikken of retryen leidt nooit tot twee pagina's
2. **Altijd verse pageIndex vóór navigatie** — lege pagina's door stale index zijn geëlimineerd
3. **Pre-navigation save faalt nooit stilzwijgend** — gebruiker krijgt altijd feedback wanneer save niet is gebeurd
4. **Stabiele URL routing** — één URL-schema, geen ambigue fallback-logica

---

## Non-Goals

- UI design changes (BlockNote editor, sidebar layout, styling blijven ongewijzigd)
- i18n / translation changes
- Performance-optimalisatie buiten wat nodig is voor reliability
- Upgraden van de BlockNote editor versie of feature set
- Wijzigen van het backend storage format (pagina's blijven als files opgeslagen)
- Introductie van realtime collaboration / CRDT
- Wijzigingen aan de `klai-docs` Next.js docs-app serving layer

---

## Architecture

### Huidige architectuur (simplified)

```
┌────────────────────────────────────────────┐
│ TanStack Router route: /docs/$kbSlug/*     │
│                                            │
│  route.tsx                                 │
│  ├── doSaveRef (useRef) ◄── silent null    │
│  ├── handleNewPage ◄── non-atomic, 3 steps │
│  └── navigateToPage                        │
│                                            │
│  $pageId.lazy.tsx                          │
│  ├── resolveSlug (fallback chain) ◄── ambiguous │
│  └── doSave (refetchPageIndex after save)  │
│                                            │
│  KBEditorContext                           │
│  ├── pageIndex (stale after create)        │
│  └── refetchPageIndex                      │
└────────────────────────────────────────────┘
                     │
                     ▼
┌────────────────────────────────────────────┐
│ FastAPI: /api/docs/*                       │
│  PUT /pages/{slug}  ◄── no idempotency key │
│  GET /page-index                           │
└────────────────────────────────────────────┘
```

### Doelarchitectuur

```
┌────────────────────────────────────────────────────┐
│ TanStack Router route: /docs/$kbSlug/$pageId       │
│                                                    │
│  route.tsx                                         │
│  ├── creationLock (prevents double submit)         │
│  ├── handleNewPage                                 │
│  │    1. await doSave (strict, throws on failure)  │
│  │    2. POST /pages (idempotency-key, returns     │
│  │         { page, pageIndex } in one response)    │
│  │    3. setPageIndex(response.pageIndex)          │
│  │    4. navigateToPage(uuid)                      │
│  └── beforeLoad guard for pre-navigation save      │
│                                                    │
│  $pageId.lazy.tsx                                  │
│  └── resolveSlug (strict, errors if not in index)  │
│                                                    │
│  KBEditorContext                                   │
│  ├── pageIndex (always fresh after mutations)      │
│  └── setPageIndex (synchronous, server-truth)      │
└────────────────────────────────────────────────────┘
                       │
                       ▼
┌────────────────────────────────────────────────────┐
│ FastAPI: /api/docs/*                               │
│  POST /pages (Idempotency-Key header)              │
│    returns { page, pageIndex }                     │
│  GET /pages/{slug}  ◄── legacy slug → 301 redirect │
│    to /pages/{uuid}                                │
└────────────────────────────────────────────────────┘
```

### Belangrijkste structurele wijzigingen

| Laag         | Verandering                                                                                                      |
|--------------|------------------------------------------------------------------------------------------------------------------|
| Backend      | `Idempotency-Key` header ondersteuning op page creation endpoint                                                 |
| Backend      | Page creation retourneert `{ page, pageIndex }` in één response (geen separate fetch nodig)                       |
| Backend      | Slug-based URL's worden met 301 redirect naar UUID-based URL doorverwezen                                        |
| Frontend     | `handleNewPage` krijgt een creation-lock flag en awaited `setPageIndex` vóór `navigateToPage`                    |
| Frontend     | `resolveSlug` wordt strict — bij UUID mismatch error in plaats van fallback                                      |
| Frontend     | `doSaveRef` wordt vervangen door een TanStack Router `beforeLoad` guard of expliciete save-status check          |
| URL schema   | UUID-URLs zijn canoniek; slug-URLs redirecten naar UUID                                                          |

---

## Requirements (EARS)

### Ubiquitous Requirements (altijd actief)

- **REQ-UBI-01**: Het systeem MOET alle page creation requests voorzien van een unieke `Idempotency-Key` header.
- **REQ-UBI-02**: Het systeem MOET UUID-based URLs gebruiken voor navigatie binnen de KB/Docs editor.
- **REQ-UBI-03**: Het systeem MOET bij elke navigatie een verse `pageIndex` garanderen voordat de nieuwe pagina wordt gerenderd.

### Event-Driven Requirements (WHEN … THEN …)

- **REQ-EVT-01** (Idempotente creation): WHEN een gebruiker binnen 500 ms tweemaal op "New Page" klikt, THEN creëert het systeem exact één pagina.
- **REQ-EVT-02** (Eager refresh na create): WHEN een nieuwe pagina wordt aangemaakt, THEN ververst het systeem `pageIndex` vóórdat naar de nieuwe pagina genavigeerd wordt.
- **REQ-EVT-03** (Eager refresh na rename): WHEN een pagina wordt hernoemd, THEN ververst het systeem `pageIndex` vóórdat de navigatie wordt afgerond.
- **REQ-EVT-04** (Sidebar save-await): WHEN de gebruiker via de sidebar navigeert, THEN wacht het systeem de pending save af voordat de nieuwe pagina wordt gerenderd.
- **REQ-EVT-05** (Legacy slug redirect): WHEN een slug-based URL wordt opgevraagd (bookmark, gedeelde link), THEN redirect het systeem naar de equivalente UUID-based URL.

### State-Driven Requirements (IF … THEN …)

- **REQ-STA-01** (Duplicate detection): IF er al een pagina bestaat met dezelfde `Idempotency-Key` of dezelfde slug, THEN retourneert het systeem de bestaande pagina zonder een duplicaat te creëren.
- **REQ-STA-02** (PageIndex refresh faalt): IF `pageIndex` refresh faalt, THEN toont het systeem een foutmelding aan de gebruiker en navigeert niet met stale data.
- **REQ-STA-03** (Pre-nav save faalt): IF de pre-navigation save faalt, THEN toont het systeem een error-indicator en blijft op de huidige pagina staan.
- **REQ-STA-04** (UUID niet gevonden): IF een opgevraagde UUID niet in `pageIndex` bestaat, THEN toont het systeem een "page not found" foutstatus in plaats van terug te vallen op slug-resolutie.

### Unwanted Behavior Requirements (SHALL NOT)

- **REQ-UNW-01**: Het systeem MAG NIET silent de pre-navigation save overslaan wanneer `doSaveRef` null is.
- **REQ-UNW-02**: Het systeem MAG NIET een UUID behandelen als een slug in API-calls.
- **REQ-UNW-03**: Het systeem MAG NIET een nieuwe pagina creëren wanneer een eerdere aanvraag met dezelfde Idempotency-Key nog in-flight is of al geslaagd is.
- **REQ-UNW-04**: Het systeem MAG NIET met stale `pageIndex` navigeren zonder de gebruiker te waarschuwen.

### Optional Requirements (WHERE possible)

- **REQ-OPT-01**: WHERE mogelijk kan het systeem TanStack Router `beforeLoad` gebruiken als pre-navigation save guarantee in plaats van het huidige `doSaveRef` pattern.
- **REQ-OPT-02**: WHERE mogelijk kan het systeem een toast/notification tonen bij succesvolle auto-save tijdens navigatie.

---

## Test Plan

### Unit tests (frontend)

- `handleNewPage` met dubbele invocation → exact één POST-request wordt uitgevoerd
- `handleNewPage` met server-response met nieuwe pageIndex → context wordt synchroon geüpdatet vóór navigate
- `resolveSlug` met UUID die niet in pageIndex bestaat → returnt error state (geen slug fallback)
- `doSave` wrapper bij gefaalde save → propagates error, geen stille swallow

### Unit tests (backend)

- `POST /pages` met dezelfde Idempotency-Key twee keer → exact één record aangemaakt, tweede call retourneert eerste result
- `POST /pages` zonder Idempotency-Key → 400 Bad Request
- `POST /pages` respons bevat `page` en `pageIndex` in één payload
- `GET /pages/{slug}` met legacy slug → 301 redirect naar `/pages/{uuid}`

### Integration tests

- Simuleer dubbelklik op "New Page" binnen 500ms → één pagina in sidebar
- Creëer pagina, navigeer direct naar die pagina zonder tussenkomst → pagina rendert correct (niet leeg)
- Forceer save-failure (mock 500 response) → error indicator zichtbaar, navigatie geblokkeerd
- Open bestaande slug-based URL in browser → redirect naar UUID-URL, pagina rendert

### End-to-end tests (Playwright)

- User creëert pagina via toolbar → pagina verschijnt exact één keer in sidebar
- User typt in huidige pagina en klikt op andere pagina in sidebar → save wacht af, inhoud is bewaard
- Network-throttle op `PUT /pages` → geen tweede request bij snelle dubbelklik

### Quality gates

- Alle unit/integration/E2E tests groen
- TypeScript `tsc --noEmit` zonder errors
- Backend `ruff` + `pytest` groen met coverage op nieuwe code ≥ 85%
- Geen silent catch-blocks in `handleNewPage`, `doSave` of `resolveSlug`
- Manuele regressietest uitgevoerd conform `acceptance.md` scenarios

---

## Traceability

- Product context: `.moai/project/product.md` (Klai knowledge base & docs editor)
- Technical context: `.moai/project/tech.md` (React 19, TanStack Router, FastAPI)
- Gerelateerde backend: `klai-portal/backend/app/api/docs.py`
- Gerelateerde frontend routes: `klai-portal/frontend/src/routes/app/docs/$kbSlug/`
- Gerelateerde libs: `klai-portal/frontend/src/lib/kb-editor/`
- Editor component: `klai-portal/frontend/src/components/kb-editor/BlockPageEditor.tsx`
