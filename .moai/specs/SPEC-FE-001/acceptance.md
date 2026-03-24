---
id: SPEC-FE-001
document: acceptance
version: "1.0.0"
status: draft
created: "2026-03-24"
updated: "2026-03-24"
---

# SPEC-FE-001: Acceptatiecriteria

## Overzicht

Dit document bevat de acceptatiecriteria voor het admin panel uitbreiding in Given/When/Then formaat. Elk scenario is gekoppeld aan een requirement uit `spec.md`.

---

## Scenario 1: Admin ziet gebruikersstatus badge (R1)

**Given** een admin is ingelogd en navigeert naar `/admin/users`
**And** de users API retourneert gebruikers met verschillende statussen (active, suspended, offboarded)
**When** de users tabel wordt geladen
**Then** elke rij toont een status badge:
  - Active gebruikers: geen badge of `default` badge
  - Suspended gebruikers: `warning` badge met tekst "Geschorst"
  - Offboarded gebruikers: `destructive` badge met tekst "Offboarded"

### Randgeval: status veld ontbreekt

**Given** de backend retourneert een gebruiker zonder `status` veld
**When** de tabel wordt gerenderd
**Then** wordt er geen badge getoond voor die gebruiker (graceful degradation)

---

## Scenario 2: Admin schorst een actieve gebruiker (R2)

**Given** een admin is ingelogd en bekijkt de users tabel
**And** er is een gebruiker met status "active"
**When** de admin klikt op het actiemenu (MoreVertical) van de gebruiker
**And** klikt op "Schorsen"
**And** bevestigt de actie
**Then** wordt `POST /api/admin/users/{id}/suspend` aangeroepen
**And** verschijnt er een success toast met "Gebruiker is geschorst"
**And** wordt de users tabel automatisch ververst
**And** toont de gebruiker nu een `warning` badge met "Geschorst"
**And** verandert het actiemenu: "Schorsen" is vervangen door "Heractiveren"

---

## Scenario 3: Admin probeert een al geschorste gebruiker te schorsen (R2)

**Given** een admin is ingelogd en bekijkt de users tabel
**And** er is een gebruiker met status "suspended"
**When** de backend een 409 status retourneert op de suspend actie
**Then** wordt er een error toast getoond met het foutbericht van de backend
**And** verandert de status van de gebruiker niet in de UI

### Aanvullend: actiemenu toont correcte opties

**Given** een gebruiker heeft status "suspended"
**When** de admin het actiemenu opent
**Then** is "Schorsen" niet beschikbaar
**And** is "Heractiveren" wel beschikbaar
**And** is "Offboarden" wel beschikbaar

---

## Scenario 4: Admin offboardt een gebruiker (R2)

**Given** een admin is ingelogd en bekijkt de users tabel of user edit pagina
**And** er is een gebruiker met status "active" of "suspended"
**When** de admin klikt op "Offboarden"
**Then** verschijnt er een bevestigingsdialoog met:
  - Titel: "Gebruiker offboarden"
  - Beschrijving: waarschuwingstekst dat dit onomkeerbaar is, inclusief vermelding van verwijdering van lidmaatschappen en producten
  - Een `destructive` bevestigingsknop
  - Een annuleerknop

**When** de admin bevestigt de offboard actie
**Then** wordt `POST /api/admin/users/{id}/offboard` aangeroepen
**And** verschijnt er een success toast met "Gebruiker is geoffboard"
**And** wordt de users tabel automatisch ververst
**And** toont de gebruiker nu een `destructive` badge met "Offboarded"
**And** zijn alle lifecycle acties uitgeschakeld voor deze gebruiker (status is definitief)

---

## Scenario 5: Admin wijst een product toe aan een gebruiker (R4)

**Given** een admin is ingelogd en navigeert naar de edit pagina van een gebruiker (`/admin/users/$userId/edit`)
**And** de producten sectie toont beschikbare producten uit `GET /api/admin/products`
**And** de huidige producten van de gebruiker zijn opgehaald via `GET /api/admin/users/{id}/products`
**When** de admin zet een product toggle aan (van uit naar aan)
**Then** wordt `POST /api/admin/users/{id}/products` aangeroepen met `{product: "product_name"}`
**And** verschijnt er een success toast met "Product toegewezen"
**And** toont de toggle nu de `enabled_at` datum en `enabled_by` informatie

**When** de admin zet een product toggle uit (van aan naar uit)
**Then** wordt `DELETE /api/admin/users/{id}/products/{product}` aangeroepen
**And** verschijnt er een success toast met "Product ingetrokken"
**And** verdwijnt de `enabled_at` en `enabled_by` informatie

---

## Scenario 6: Admin maakt een groep aan, voegt een lid toe, en toggled group admin (R5)

### 6a: Groep aanmaken

**Given** een admin is ingelogd en navigeert naar `/admin/groups`
**When** de admin klikt op "Groep aanmaken"
**Then** verschijnt er een formulier (Dialog of Sheet) met:
  - Naam invoerveld (verplicht)
  - Beschrijving invoerveld (optioneel)
  - Opslaan knop
  - Annuleren knop

**When** de admin vult "Engineering" in als naam en klikt op opslaan
**Then** wordt `POST /api/admin/groups` aangeroepen met `{name: "Engineering"}`
**And** verschijnt er een success toast met "Groep aangemaakt"
**And** verschijnt de nieuwe groep in de tabel

### 6b: Lid toevoegen

**Given** een admin bekijkt de detail pagina van groep "Engineering" (`/admin/groups/$groupId`)
**When** de admin klikt op "Lid toevoegen"
**Then** verschijnt er een zoek/selectie component (combobox) met bestaande org-gebruikers
**And** de admin zoekt en selecteert een gebruiker

**When** de admin bevestigt het toevoegen
**Then** wordt `POST /api/admin/groups/{id}/members` aangeroepen met `{zitadel_user_id: "..."}`
**And** verschijnt er een success toast met "Lid toegevoegd"
**And** verschijnt de gebruiker in de ledentabel

### 6c: Group admin toggle

**Given** een admin bekijkt de ledentabel van een groep
**And** een lid heeft `is_group_admin = false`
**When** de admin zet de "Groepsbeheerder" toggle aan voor dat lid
**Then** wordt `PATCH /api/admin/groups/{id}/members/{user_id}` aangeroepen met `{is_group_admin: true}`
**And** verschijnt er een success toast met "Groepsbeheerder status bijgewerkt"
**And** toont het lid nu een "Groepsbeheerder" badge

---

## Scenario 7: Admin probeert een niet-pending gebruiker te verwijderen (R3)

**Given** een admin is ingelogd en bekijkt de users tabel
**And** er is een gebruiker met `invite_pending = false` en status "active"
**When** de admin het actiemenu opent voor die gebruiker
**Then** is de optie "Verwijderen" NIET beschikbaar
**And** is de optie "Offboarden" WEL beschikbaar

### Tegenvoorbeeld: pending gebruiker

**Given** er is een gebruiker met `invite_pending = true`
**When** de admin het actiemenu opent
**Then** is de optie "Verwijderen" WEL beschikbaar
**And** is de optie "Offboarden" NIET beschikbaar (of onzichtbaar)

**When** de admin klikt op "Verwijderen" en bevestigt
**Then** wordt `DELETE /api/admin/users/{id}` aangeroepen
**And** verdwijnt de gebruiker uit de tabel

---

## Scenario 8: Groups pagina toont lege staat (R5)

**Given** een admin is ingelogd en navigeert naar `/admin/groups`
**And** de organisatie heeft geen groepen (`GET /api/admin/groups` retourneert `{groups: []}`)
**When** de pagina is geladen
**Then** toont de pagina een lege staat met:
  - Informatief bericht: "Nog geen groepen aangemaakt"
  - Beschrijving: "Maak een groep aan om gebruikers te organiseren."
  - Een "Groep aanmaken" knop die het create formulier opent

---

## Scenario 9: Admin verwijdert een groep met leden (R5)

**Given** een admin bekijkt de detail pagina van een groep met 3 leden
**When** de admin klikt op "Groep verwijderen"
**Then** verschijnt er een bevestigingsdialoog met:
  - Titel: "Groep verwijderen"
  - Waarschuwing: "Alle lidmaatschappen worden verwijderd. Dit kan niet ongedaan worden gemaakt."
  - `destructive` bevestigingsknop

**When** de admin bevestigt
**Then** wordt `DELETE /api/admin/groups/{id}` aangeroepen
**And** verschijnt er een success toast met "Groep verwijderd"
**And** wordt de admin teruggestuurd naar `/admin/groups`

---

## Scenario 10: Lid toevoegen -- gebruiker is al lid (R5)

**Given** een admin is op de group detail pagina en probeert een gebruiker toe te voegen die al lid is
**When** de backend een 409 status retourneert
**Then** wordt er een error toast getoond met "Gebruiker is al lid van deze groep"
**And** verandert de ledentabel niet

---

## Scenario 11: Groep aanmaken -- duplicate naam (R5)

**Given** een admin vult een groepsnaam in die al bestaat
**When** de backend een 409 status retourneert bij `POST /api/admin/groups`
**Then** wordt er een error toast of inline foutmelding getoond met "Een groep met deze naam bestaat al"
**And** blijft het formulier open zodat de admin de naam kan aanpassen

---

## Scenario 12: Admin sidebar en home (R5)

**Given** een admin is ingelogd
**When** de admin sidebar wordt gerenderd
**Then** bevat deze een "Groepen" navigatie-item met een passend icoon (`Users` of `FolderKanban`)
**And** navigeert dit item naar `/admin/groups`

**When** de admin het admin home/dashboard bekijkt
**Then** toont een overzichtskaart het totaal aantal groepen
**And** is de kaart klikbaar en navigeert naar `/admin/groups`

---

## Quality Gates

| Criterium                    | Vereiste                                                     |
|------------------------------|--------------------------------------------------------------|
| Functionaliteit              | Alle 12 scenario's slagen                                    |
| i18n                         | Alle keys aanwezig in zowel `nl.json` als `en.json`          |
| TypeScript                   | Geen `any` types; strikte typing voor API responses          |
| Error handling               | Alle 409 en foutstatussen worden afgehandeld met toast       |
| Responsiveness               | Tabel en formulieren bruikbaar op tablet formaat (>768px)    |
| Bestaande functionaliteit    | Geen regressies in bestaande admin pagina's                  |
| Badge varianten              | Correcte shadcn/ui badge varianten gebruikt                  |
| Query invalidation           | Alle mutations invalideren de juiste query keys              |

## Definition of Done

- [ ] Alle 12 acceptatiescenario's zijn gevalideerd
- [ ] `status` veld is toegevoegd aan `UserOut` in de backend
- [ ] Alle i18n keys zijn aanwezig in `nl.json` en `en.json`
- [ ] Geen TypeScript fouten (`tsc --noEmit` slaagt)
- [ ] Bestaande admin pagina's werken ongewijzigd (geen regressies)
- [ ] Lifecycle acties respecteren de juiste status/invite_pending condities
- [ ] Destructieve acties hebben bevestigingsdialogen
- [ ] Groups CRUD werkt end-to-end met correcte error handling
- [ ] Group membership management werkt inclusief admin toggle
