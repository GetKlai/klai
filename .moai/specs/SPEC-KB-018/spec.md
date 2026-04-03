---
id: SPEC-KB-018
version: "2.0.0"
status: completed
created: 2026-04-03
updated: 2026-04-03
author: MoAI
priority: high
---

## HISTORY

| Date | Version | Change |
|------|---------|--------|
| 2026-04-03 | 1.0.0 | Initial SPEC creation |
| 2026-04-03 | 2.0.0 | Rewrite: multi-step wizard met 4 stappen, scope/visibility split, rechten-stap altijd aanwezig |

---

# SPEC-KB-018: Knowledge Base Creation Wizard — Multi-Step

## Summary

Vervang het huidige single-page creation formulier door een 4-stap wizard met interne step-state. Elke stap focust op één vraag. De wizard splitst scope/visibility/rechten in aparte stappen zodat de gebruiker niet overweldigd raakt. Persoonlijke KBs slaan stap 2 en 3 over.

## Context

De backend heeft al volledige access control (user- en groep-level rollen, highest-wins resolutie, `default_org_role`). De v1 frontend creation flow was een single-page formulier met conditionele secties — functioneel correct maar UX-matig te druk. Een multi-step wizard geeft focus per stap en maakt de flow begrijpelijker.

## Scope

### In scope
- Multi-step wizard (4 stappen) op `/app/knowledge/new` met interne state
- Stappenbalk navigatie (terug/volgende, indicator klikbaar naar eerdere stappen)
- Stap 1: scope (org/persoonlijk) + naam + slug + beschrijving
- Stap 2: visibility keuze (publiek/organisatie/beperkt)
- Stap 3: rechten verfijnen (default rol, extra groepen/personen)
- Stap 4: samenvatting + bevestiging
- Skip-logica: persoonlijk → stap 1 direct naar stap 4
- i18n (EN + NL) voor alle wizard-tekst
- Validatie per stap met disabled "Volgende" knop

### Out of scope (ongewijzigd t.o.v. v1)
- Backend API wijzigingen (v1 endpoints zijn al correct)
- Alembic migratie (v1 migratie is al toegepast)
- Access service wijzigingen
- Members tab wijzigingen (REQ-7 uit v1 blijft toekomstig werk)
- Pagina-level permissies

## Constraints

- [HARD] Permissies blijven portal-side (PostgreSQL), niet Zitadel
- [HARD] Single route `/app/knowledge/new` — geen URL-changes per stap
- [HARD] Alle UI componenten uit `@/components/ui/` — geen raw HTML `<select>`, `<label>`, `<input>`
- [HARD] i18n via Paraglide `* as m` — geen hardcoded strings
- [HARD] `apiFetch` voor alle API calls
- Wizard moet werken op desktop en tablet viewport
- Browser back = zelfde als ← Terug (geen URL changes, geen state verlies)

---

## Requirements

### REQ-1: Stappenbalk Navigatie (EARS: Ubiquitous)

Het systeem SHALL een horizontale stappenbalk tonen bovenaan de wizard:

```
① Naam  ——  ② Toegang  ——  ③ Rechten  ——  ④ Bevestigen
```

**Detail:**
- Actieve stap: geaccentueerd (accent kleur)
- Voltooide stappen: klikbaar, stijl wijst op "afgerond"
- Toekomstige stappen: gedempt, niet klikbaar
- Bij persoonlijke scope: stappen 2 en 3 worden visueel overgeslagen (①→④)

### REQ-2: Stap 1 — Naam & Scope (EARS: Ubiquitous)

Stap 1 SHALL tonen:

| Element | Type | Detail |
|---------|------|--------|
| Annuleren link | Link linksboven | Navigeert naar `/app/knowledge` |
| Scope picker | 2 card buttons | "Organisatie" (👥) en "Persoonlijk" (👤) |
| Naam veld | Input | Verplicht, auto-genereert slug |
| Slug veld | Input | Verplicht, editable, preview URL eronder |
| Beschrijving | Textarea | Optioneel |
| Volgende knop | Button rechtsonder | Disabled als naam of slug leeg |

**Bij "Persoonlijk":** Volgende knop gaat direct naar stap 4 (skip 2 en 3).

### REQ-3: Stap 2 — Wie mag erbij? (EARS: Event-Response)

WANNEER scope "Organisatie" is, SHALL stap 2 drie visibility cards tonen:

| Card | Label | Icoon | Beschrijving |
|------|-------|-------|-------------|
| Publiek | "Publiek" | 🌐 | Iedereen kan lezen, ook buiten je organisatie. Je docs site wordt openbaar toegankelijk. |
| Organisatie | "Organisatie" | 👥 | Alle teamleden kunnen lezen. Docs zijn alleen zichtbaar voor ingelogde leden. |
| Beperkt | "Beperkt" | 🔒 | Alleen de groepen en personen die jij kiest krijgen toegang. |

**Detail:**
- Cards zijn verticaal gestapeld (niet grid) — beschrijvende tekst vereist breedte
- Default selectie: "Organisatie"
- Styling: selected card heeft accent border + ring, unselected heeft hover effect

**Mapping naar backend:**

| Card | `visibility` | `default_org_role` |
|------|-------------|-------------------|
| Publiek | `"public"` | `"viewer"` (default), `"contributor"` (met checkbox in stap 3) |
| Organisatie | `"internal"` | `"viewer"` (default), `"contributor"` (met checkbox in stap 3) |
| Beperkt | `"private"` | `NULL` |

### REQ-4: Stap 3 — Rechten verfijnen (EARS: Event-Response)

Stap 3 EXISTS ALTIJD bij org-scope (niet alleen bij "Beperkt"). De inhoud verschilt per visibility:

#### Variant A: Bij "Organisatie" of "Publiek"

SHALL tonen:
- **Standaard sectie:** "Alle org-leden: Viewer" met checkbox "Alle org-leden mogen ook content toevoegen (contributor)"
- **Extra rechten sectie (optioneel):** groep-zoeker + persoon-zoeker met rol-selectie per toevoeging
- Info-tekst: "Jij bent automatisch eigenaar (owner)."

**Rol-opties in picker:**
- Bij standaard = viewer: picker toont Contributor en Owner
- Bij standaard = contributor (checkbox aan): picker toont alleen Owner
- Nooit een lagere rol dan de standaard toekenbaar

**Validatie:** Niets verplicht — alles is optioneel. Volgende knop altijd enabled.

#### Variant B: Bij "Beperkt"

SHALL tonen:
- Uitleg: "Alleen de groepen en personen die je hier toevoegt krijgen toegang."
- Groep-zoeker (autocomplete) met rol per toevoeging (Viewer/Contributor/Owner)
- Persoon-zoeker (autocomplete) met rol per toevoeging (Viewer/Contributor/Owner)
- Waarschuwing: "Voeg minimaal 1 groep of persoon toe."
- Info-tekst: "Jij bent automatisch eigenaar (owner)."

**Validatie:** Minimaal 1 groep of persoon vereist. Volgende knop disabled tot ≥1 member.

### REQ-5: Stap 4 — Bevestigen (EARS: Ubiquitous)

SHALL een compacte samenvatting tonen met:

| Element | Wanneer zichtbaar |
|---------|-------------------|
| KB naam + slug | Altijd |
| Beschrijving | Als ingevuld |
| Visibility met icoon | Bij org-scope |
| Default org-rol | Bij org/publiek |
| Extra rechten (groepen + personen) | Als toegevoegd |
| "Alleen jij hebt toegang" | Bij persoonlijk |
| "Docs site wordt automatisch aangemaakt" | Altijd |
| "Knowledge base aanmaken" knop | Altijd (primary, rechtsonder) |

**Bij fouten:** toon error bericht, knop wordt weer enabled.

### REQ-6: Navigatie Gedrag (EARS: Ubiquitous)

| Actie | Gedrag |
|-------|--------|
| Volgende → | Valideer huidige stap, ga naar volgende |
| ← Terug | Ga terug, behoud alle ingevulde data |
| ← Annuleren (stap 1) | Terug naar `/app/knowledge`, geen data bewaard |
| Stap indicator klikken | Alleen terug naar eerdere stappen, niet vooruit springen |
| Persoonlijk scope | Stap 1 → Stap 4 (skip 2 en 3) |
| Browser back | Zelfde als ← Terug (geen URL changes) |

### REQ-7: Validatie per Stap (EARS: Ubiquitous)

| Stap | Verplicht | "Volgende" disabled wanneer |
|------|-----------|---------------------------|
| 1 | Naam, slug, scope | Naam of slug leeg |
| 2 | Visibility keuze | Niets geselecteerd (onmogelijk door default) |
| 3 (org/publiek) | Niets — alles optioneel | Nooit disabled |
| 3 (beperkt) | ≥1 groep of persoon | Geen members toegevoegd |
| 4 | — | Nooit disabled (alles al gevalideerd) |

---

## UX Flow: Wireframes

### Stap 1: Wat bouw je?

```
┌─────────────────────────────────────────────────┐
│  ← Annuleren                                    │
│                                                 │
│  ① Naam  ——  ② Toegang  ——  ③ Rechten  ——  ④ ✓  │
│  ●                                              │
│                                                 │
│  Wat voor knowledge base wil je maken?          │
│                                                 │
│  ┌────────────────────┐ ┌────────────────────┐  │
│  │ 👥 Organisatie      │ │ 👤 Persoonlijk     │  │
│  │ Gedeeld met je team │ │ Alleen voor jou    │  │
│  └────────────────────┘ └────────────────────┘  │
│                                                 │
│  Naam                                           │
│  [Product Documentatie_______________]           │
│                                                 │
│  Slug                                           │
│  [product-documentatie_______________]           │
│  docs.getklai.com/acme/product-documentatie     │
│                                                 │
│  Beschrijving (optioneel)                       │
│  [Alle productdocs voor het team_____]           │
│                                                 │
│                                     [Volgende →] │
└─────────────────────────────────────────────────┘
```

### Stap 2: Wie mag erbij?

```
┌─────────────────────────────────────────────────┐
│  ← Terug                                        │
│                                                 │
│  ① Naam  ——  ② Toegang  ——  ③ Rechten  ——  ④ ✓  │
│              ●                                  │
│                                                 │
│  Wie mag bij "Product Documentatie"?            │
│                                                 │
│  ┌─────────────────────────────────────────┐    │
│  │ 🌐 Publiek                              │    │
│  │ Iedereen kan lezen, ook buiten je       │    │
│  │ organisatie. Je docs site wordt         │    │
│  │ openbaar toegankelijk.                  │    │
│  └─────────────────────────────────────────┘    │
│                                                 │
│  ┌═════════════════════════════════════════┐    │
│  ║ 👥 Organisatie                          ║    │  ← geselecteerd
│  ║ Alle teamleden kunnen lezen. Docs zijn  ║    │
│  ║ alleen zichtbaar voor ingelogde leden.  ║    │
│  └═════════════════════════════════════════┘    │
│                                                 │
│  ┌─────────────────────────────────────────┐    │
│  │ 🔒 Beperkt                              │    │
│  │ Alleen de groepen en personen die jij   │    │
│  │ kiest krijgen toegang.                  │    │
│  └─────────────────────────────────────────┘    │
│                                                 │
│                                  [Volgende →]   │
└─────────────────────────────────────────────────┘
```

### Stap 3a: Rechten bij Organisatie/Publiek

```
┌─────────────────────────────────────────────────┐
│  ← Terug                                        │
│                                                 │
│  ① Naam  ——  ② Toegang  ——  ③ Rechten  ——  ④ ✓  │
│                              ●                  │
│                                                 │
│  Rechten voor "Product Documentatie"            │
│                                                 │
│  Standaard                                      │
│  ┌─────────────────────────────────────────┐    │
│  │ Alle org-leden: Viewer                  │    │
│  │                                         │    │
│  │ ☐ Alle org-leden mogen ook content      │    │
│  │   toevoegen (contributor)               │    │
│  └─────────────────────────────────────────┘    │
│                                                 │
│  Extra rechten (optioneel)                      │
│  Geef specifieke groepen of personen meer       │
│  rechten dan de standaard.                      │
│                                                 │
│  Groepen                                        │
│  [Zoek een groep...                     🔍]     │
│  ┌─────────────────────────────────────────┐    │
│  │ Redactie                [Contributor ▾] ✕  │  │
│  └─────────────────────────────────────────┘    │
│                                                 │
│  Personen                                       │
│  [Zoek op naam of e-mail...             🔍]     │
│  ┌─────────────────────────────────────────┐    │
│  │ Jan de Vries            [Contributor ▾] ✕  │  │
│  └─────────────────────────────────────────┘    │
│                                                 │
│  ⓘ Jij bent automatisch eigenaar (owner).      │
│                                                 │
│                                  [Volgende →]   │
└─────────────────────────────────────────────────┘
```

### Stap 3b: Rechten bij Beperkt

```
┌─────────────────────────────────────────────────┐
│  ← Terug                                        │
│                                                 │
│  ① Naam  ——  ② Toegang  ——  ③ Rechten  ——  ④ ✓  │
│                              ●                  │
│                                                 │
│  Met wie wil je "Product Documentatie" delen?   │
│                                                 │
│  Alleen de groepen en personen die je hier      │
│  toevoegt krijgen toegang.                      │
│                                                 │
│  Groepen                                        │
│  [Zoek een groep...                     🔍]     │
│  ┌─────────────────────────────────────────┐    │
│  │ Redactie                [Contributor ▾] ✕  │  │
│  │ Productteam             [Viewer     ▾] ✕  │  │
│  └─────────────────────────────────────────┘    │
│                                                 │
│  Personen                                       │
│  [Zoek op naam of e-mail...             🔍]     │
│                                                 │
│  (nog niemand toegevoegd)                       │
│                                                 │
│  ⚠ Voeg minimaal 1 groep of persoon toe.       │
│                                                 │
│  ⓘ Jij bent automatisch eigenaar (owner).      │
│                                                 │
│                                  [Volgende →]   │
│                          (disabled tot ≥1 member)│
└─────────────────────────────────────────────────┘
```

### Stap 4: Bevestigen

```
┌─────────────────────────────────────────────────┐
│  ← Terug                                        │
│                                                 │
│  ① Naam  ——  ② Toegang  ——  ③ Rechten  ——  ④ ✓  │
│                                              ●  │
│                                                 │
│  Klopt dit?                                     │
│                                                 │
│  ┌─────────────────────────────────────────┐    │
│  │ 📚 Product Documentatie                  │    │
│  │    product-documentatie                  │    │
│  │    "Alle productdocs voor het team"      │    │
│  │                                         │    │
│  │ 👥 Organisatie                           │    │
│  │    Alle org-leden: Viewer               │    │
│  │                                         │    │
│  │ Groepen met extra rechten:              │    │
│  │    • Redactie → Contributor             │    │
│  │                                         │    │
│  │ Personen met extra rechten:             │    │
│  │    • Jan de Vries → Contributor         │    │
│  │                                         │    │
│  │ 📄 Docs site wordt automatisch          │    │
│  │    aangemaakt (intern)                  │    │
│  └─────────────────────────────────────────┘    │
│                                                 │
│                      [Knowledge base aanmaken]  │
└─────────────────────────────────────────────────┘
```

---

## Technical Approach

### Scope: Frontend-only

De backend API is al volledig geïmplementeerd (v1). Deze v2 is uitsluitend een frontend refactor van `knowledge/new.tsx`.

### Implementatie strategie

1. **Wizard state machine**: `useState<1|2|3|4>(1)` voor huidige stap. Alle form data in één `useState` object dat behouden blijft bij terug/vooruit navigatie.

2. **Component structuur** (single file, <600 lines):
   - `NewKnowledgeBasePage` — hoofd-component met step state
   - `StepIndicator` — de stappenbalk
   - `StepName` — stap 1 (scope + naam + slug + beschrijving)
   - `StepAccess` — stap 2 (visibility cards)
   - `StepPermissions` — stap 3 (rechten, twee varianten)
   - `StepConfirm` — stap 4 (samenvatting + submit)
   - `MemberPicker` — herbruikbaar voor stap 3a en 3b (groep/user zoeker)

3. **Skip-logica**: Bij scope="personal", `handleNext()` springt van stap 1 naar stap 4. `StepIndicator` toont visueel de skip.

4. **Validatie**: Elke stap-component exposed een `isValid` boolean. De Volgende knop is disabled wanneer `!isValid`.

5. **Data flow**: Alle form state leeft in het parent component. Sub-componenten ontvangen values + onChange handlers (controlled components pattern).

### Hergebruik uit v1

- Scope picker cards (styling)
- Visibility cards (styling + mapping)
- Member picker (groep/user autocomplete + rol selector)
- `apiFetch` calls naar `/api/app/groups` en `/api/app/users`
- Submit logica naar `POST /api/app/knowledge-bases`
- Alle bestaande i18n keys

### Nieuwe i18n keys

Keys in `knowledge_wizard_*` namespace:
- Step labels: `knowledge_wizard_step_name`, `_access`, `_permissions`, `_confirm`
- Step titles: `knowledge_wizard_title_step1` t/m `_step4`
- Navigatie: `knowledge_wizard_next`, `_back`, `_cancel`
- Stap 3: `knowledge_wizard_default_role_label`, `_contributor_checkbox`, `_extra_permissions_title`, `_extra_permissions_desc`, `_restricted_desc`, `_min_one_member`, `_owner_info`
- Stap 4: `knowledge_wizard_confirm_title`, `_confirm_docs_auto`, `_create_button`
- Rol labels (hergebruik bestaand): `knowledge_members_role_viewer`, `_contributor`, `_owner`

---

## Dependencies

| Dependency | Type | Status |
|------------|------|--------|
| `POST /api/app/knowledge-bases` | Existing | Geen wijzigingen nodig (v1 endpoints) |
| `GET /api/app/groups` | Existing | Geen wijzigingen nodig |
| `GET /api/app/users` | Existing | Geen wijzigingen nodig |
| Portal UI components | Existing | Button, Input, Label, Select, Card |
| Paraglide i18n | Existing | Nieuwe keys toevoegen |

---

## Risks

| Risk | Impact | Mitigation |
|------|--------|------------|
| File wordt te groot (>600 lines) | Medium | Sub-componenten in zelfde file; als >600 dan split naar `_wizard-steps.tsx` |
| State verlies bij browser back | Low | Geen URL changes per stap — browser back verlaat de wizard |
| Accessibility | Medium | Stappenbalk met aria-current, disabled buttons met aria-disabled |
| Tablet viewport | Low | Cards en form al responsive door max-w-lg pattern |

---

## Acceptance Criteria

- [ ] Wizard toont 4 stappen met werkende stappenbalk navigatie
- [ ] Stap 1: scope picker + naam/slug/beschrijving werkt
- [ ] Stap 2: visibility cards tonen correct, default = Organisatie
- [ ] Stap 3a: contributor checkbox + optionele member picker bij org/publiek
- [ ] Stap 3b: verplichte member picker (≥1) bij beperkt
- [ ] Stap 4: samenvatting toont alle keuzes correct
- [ ] Persoonlijk scope: stap 1 → stap 4 direct
- [ ] Terug-navigatie behoudt alle data
- [ ] Submit maakt KB aan via bestaande API
- [ ] Alle tekst via i18n (EN + NL)
- [ ] Geen raw HTML elementen (alleen UI components)
- [ ] Rollen in picker passen zich aan op basis van standaard-rol
