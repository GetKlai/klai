---
spec: SPEC-DOCS-001
version: 1.0.0
status: draft
created: 2026-04-16
updated: 2026-04-16
author: Mark Vletter
priority: high
---

# SPEC-DOCS-001 — Acceptance Criteria

Gherkin Given/When/Then scenario's voor manuele en geautomatiseerde acceptatie. Deze scenario's vormen samen met het Test Plan in `spec.md` de definitie van "klaar".

---

## Scenario 1 — Dubbelklik op "New Page" creëert exact één pagina

**Given** een gebruiker op `/docs/{kbSlug}` met een bestaande pagina geopend,
**And** de sidebar "New Page" knop is zichtbaar,
**And** er zijn N pagina's in de sidebar,
**When** de gebruiker binnen 500 ms tweemaal op "New Page" klikt,
**Then** de frontend stuurt exact één `POST /pages` request met een unieke `Idempotency-Key` header,
**And** de backend creëert exact één nieuwe pagina record,
**And** de sidebar toont N+1 pagina's (geen duplicaat),
**And** de browser navigeert naar de UUID-URL van de nieuwe pagina,
**And** de nieuwe pagina is leeg en klaar voor input.

### Verificatie

- E2E (Playwright): click-event dubbel triggeren, netwerktab controleren op aantal requests
- Backend unit test: zelfde `Idempotency-Key` twee keer → één record in DB
- Manuele test: sidebar count vóór en ná dubbelklik

---

## Scenario 2 — Navigeren direct na aanmaken toont de nieuwe pagina correct

**Given** een gebruiker op `/docs/{kbSlug}/{existingUuid}` (bestaande pagina),
**And** de huidige pagina is in save-status `idle` (niets onopgeslagen),
**When** de gebruiker op "New Page" klikt,
**And** de backend een succesvolle response stuurt met `{ page, pageIndex }`,
**Then** de `pageIndex` in de KBEditorContext bevat de nieuwe pagina's UUID,
**And** pas daarna gebeurt `navigateToPage(newUuid)`,
**And** de `$pageId.lazy.tsx` route laadt met de juiste UUID,
**And** `resolveSlug` vindt de UUID in `pageIndex`,
**And** de backend ontvangt een GET-request op `/pages/{newUuid}` (geen slug),
**And** de nieuwe (lege) pagina rendert zonder "empty page" fallback.

### Verificatie

- Integration test: mock backend met delay op pageIndex refresh → assert dat navigate wacht
- E2E: netwerk-tab toont volgorde: POST /pages → GET /pages/{uuid} → 200 met content
- Manuele test: creëer pagina, observeer geen flash van lege state / 404

---

## Scenario 3 — Gefaalde save tijdens navigatie blokkeert navigatie en toont error

**Given** een gebruiker bewerkt een pagina op `/docs/{kbSlug}/{uuidA}`,
**And** er zijn onopgeslagen wijzigingen (save-status `idle` → `saving` bij trigger),
**And** de backend save-endpoint zal een 500-response retourneren,
**When** de gebruiker op een andere pagina in de sidebar klikt,
**Then** de frontend roept `doSave` aan en wacht op voltooiing,
**And** zodra de save faalt, zet de KBEditorContext save-status naar `error`,
**And** er verschijnt een error-toast/notification met duidelijke tekst (bv. "Save failed — changes not navigated away"),
**And** de URL verandert NIET — de gebruiker blijft op `/docs/{kbSlug}/{uuidA}`,
**And** de originele pagina-content blijft zichtbaar in de editor,
**And** er is een retry-optie (button in toast of sidebar).

### Verificatie

- Unit test: mock `doSave` naar reject → assert `navigate` wordt niet aangeroepen
- Integration test: 500-response → assert toast is rendered + URL unchanged
- Manuele test: network throttle + injected 500 op save endpoint

---

## Additional Scenarios (edge cases)

### Scenario 4 — Legacy slug-URL uit bookmark redirectt naar UUID-URL

**Given** een gebruiker opent een gebookmarkte URL `/docs/{kbSlug}/my-old-page`,
**And** de slug `my-old-page` bestaat nog in `page_index` met UUID `abc-123`,
**When** de browser de URL oplost,
**Then** de backend retourneert een 301 redirect naar `/docs/{kbSlug}/abc-123`,
**And** de browser volgt de redirect,
**And** de bookmark wordt door de browser automatisch bijgewerkt (browser-gedrag),
**And** de pagina rendert correct via strict `resolveSlug`.

### Scenario 5 — UUID niet in pageIndex toont "page not found"

**Given** een gebruiker navigeert (handmatig of via gebroken link) naar `/docs/{kbSlug}/nonexistent-uuid`,
**When** `resolveSlug` in `$pageId.lazy.tsx` draait,
**Then** `resolveSlug` throwt een "not-in-index" error,
**And** de route-fallback toont een "Page not found" UI,
**And** er is een link "Back to knowledge base root",
**And** de backend ontvangt GEEN request met de UUID als slug.

### Scenario 6 — `pageIndex` refresh faalt → geen stille navigatie

**Given** een gebruiker creëert een nieuwe pagina,
**And** het page-creation endpoint slaagt, maar de `pageIndex` in de response is corrupt / parsing faalt,
**When** de frontend `setPageIndex` probeert aan te roepen,
**Then** er wordt een error getoond ("Could not refresh page index"),
**And** er wordt NIET genavigeerd naar de nieuwe UUID,
**And** de gebruiker heeft een retry-optie (bv. expliciete "refresh index" actie of page reload).

### Scenario 7 — `doSaveRef` null tijdens navigatie geeft duidelijke feedback

**Given** een edge-case waar `doSaveRef.current` null is (component unmount race),
**When** de gebruiker op een andere pagina klikt,
**Then** het systeem detecteert de null-state,
**And** toont een warning ("Editor not ready — retry in a moment") in plaats van silent skip,
**And** blokkeert de navigatie totdat de editor klaar is of de gebruiker annuleert.

### Scenario 8 — Idempotency-Key reuse bij network retry

**Given** de frontend stuurt een `POST /pages` met `Idempotency-Key: xyz-123`,
**And** de request faalt op transport-laag (timeout / netwerkfout),
**When** de frontend de request retryt met dezelfde key,
**And** de backend ontvangt de retry,
**Then** als de eerste request succesvol was backend-side → zelfde response wordt geretourneerd (geen tweede pagina),
**And** als de eerste request nooit binnenkwam → nieuwe pagina wordt aangemaakt,
**And** in beide gevallen zijn er géén duplicaten in `page_index`.

---

## Quality Gate Criteria

### Functioneel

- [ ] Alle 8 scenario's groen in handmatige regressietest
- [ ] Alle scenario's 1-3 gedekt door geautomatiseerde E2E Playwright tests
- [ ] Scenario's 4-8 gedekt door integration tests

### Code kwaliteit

- [ ] Geen nieuwe ESLint waarschuwingen op gewijzigde files
- [ ] `tsc --noEmit` in `klai-portal/frontend` zonder errors
- [ ] `ruff check` + `ruff format --check` groen in `klai-portal/backend`
- [ ] pytest coverage ≥ 85% op gewijzigde backend modules
- [ ] Vitest coverage ≥ 85% op gewijzigde frontend modules
- [ ] Geen silent `catch` of `void`-prefix save-calls in het navigatiepad

### Observability

- [ ] VictoriaLogs query `service:portal-api AND path:/pages AND status:5*` geen nieuwe error-spike
- [ ] Nieuwe log-events: `page.created` met Idempotency-Key, `page.create.idempotent_hit` bij key-reuse
- [ ] `request_id` propagatie werkt correct over POST /pages en PUT /sidebar updates

### Documentatie

- [ ] OpenAPI schema bijgewerkt met nieuwe response-shape en Idempotency-Key header
- [ ] `klai-docs` pagina of inline comments uitleggen het nieuwe creation-flow-contract
- [ ] CHANGELOG entry voor klanten (KB/Docs editor reliability improvements)

---

## Definition of Done

Een SPEC-DOCS-001 implementatie is gereed wanneer:

1. **Alle EARS-requirements uit `spec.md`** (REQ-UBI-\*, REQ-EVT-\*, REQ-STA-\*, REQ-UNW-\*) zijn geïmplementeerd en gedemonstreerd in code en tests.
2. **Alle 8 scenario's in dit document** zijn groen — 3 hoofdscenario's geautomatiseerd, 5 edge-cases minimaal via integration tests geverifieerd.
3. **Alle Quality Gate Criteria** hierboven zijn aangevinkt.
4. **Productie-observatie gedurende minimaal 48 uur** toont:
   - Duplicate-page rate ≈ 0 (gemeten via DB-query op page_index)
   - Geen significante stijging in 4xx/5xx errors op `/pages` endpoints
   - Geen nieuwe Sentry / error-monitoring reports gerelateerd aan `resolveSlug`, `doSaveRef` of `handleNewPage`
5. **Feature-flag rollout** succesvol afgerond: intern → beta → algemeen, zonder rollback.
6. **Post-deployment review** met frontend + backend owners bevestigt geen regressies in bestaande KB-functionaliteit (editor open/save, sidebar reorder, rename, delete).
