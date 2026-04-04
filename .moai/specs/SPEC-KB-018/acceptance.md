# Acceptance Criteria: SPEC-KB-018

## Scenario 1: KB aanmaken met Organisatie visibility (default viewer)

**Given** een ingelogde org-admin op `/app/knowledge/new`
**When** de gebruiker "Organisatie" kiest, een naam invult, en contributor toggle UIT laat
**Then** wordt de KB aangemaakt met `visibility = "internal"` en `default_org_role = "viewer"`
**And** alle org-leden kunnen de KB zien en doorzoeken
**And** geen org-lid kan content toevoegen (tenzij later expliciet contributor gemaakt)
**And** docs worden aangemaakt met visibility "private"

## Scenario 2: KB aanmaken met Organisatie visibility (contributor enabled)

**Given** een ingelogde org-admin op `/app/knowledge/new`
**When** de gebruiker "Organisatie" kiest en contributor toggle AAN zet
**Then** wordt de KB aangemaakt met `visibility = "internal"` en `default_org_role = "contributor"`
**And** alle org-leden kunnen content toevoegen aan de KB
**And** de Members tab toont "Standaard: Contributor" bovenaan

## Scenario 3: KB aanmaken met Beperkt visibility

**Given** een ingelogde org-admin op `/app/knowledge/new`
**When** de gebruiker "Beperkt" kiest
**Then** verschijnt een member picker met groep- en persoon-zoeker
**And** de gebruiker kan groepen en personen toevoegen met viewer of contributor rol
**And** minimaal 1 member is vereist naast de creator
**When** de gebruiker bevestigt
**Then** wordt de KB aangemaakt met `visibility = "private"` en `default_org_role = NULL`
**And** alleen de creator (owner) en expliciet toegevoegde members hebben toegang

## Scenario 4: KB aanmaken met Publiek visibility

**Given** een ingelogde org-admin op `/app/knowledge/new`
**When** de gebruiker "Publiek" kiest
**Then** wordt de KB aangemaakt met `visibility = "public"` en `default_org_role = "viewer"`
**And** docs site is openbaar toegankelijk (geen login vereist)
**And** alle org-leden zijn standaard viewer (of contributor als toggle aan)

## Scenario 5: Access resolution met default_org_role fallback

**Given** een KB met `default_org_role = "viewer"` en groep "Redactie" met rol "contributor"
**And** user Jan is lid van groep "Redactie"
**And** user Lisa heeft geen expliciete access en is geen lid van een groep met access
**When** `get_user_role_for_kb()` wordt aangeroepen voor Jan
**Then** is Jan's effectieve rol "contributor" (groep wint boven default)
**When** `get_user_role_for_kb()` wordt aangeroepen voor Lisa
**Then** is Lisa's effectieve rol "viewer" (fallback naar default_org_role)

## Scenario 6: Access resolution bij Beperkt KB

**Given** een KB met `default_org_role = NULL` (beperkt)
**And** user Jan is niet expliciet toegevoegd
**When** `get_user_role_for_kb()` wordt aangeroepen voor Jan
**Then** heeft Jan geen toegang (NULL default = geen fallback)

## Scenario 7: Members tab toont default rol en uitzonderingen

**Given** een KB met `default_org_role = "viewer"` en groep "Redactie" = contributor
**When** de owner de Members tab opent
**Then** toont de tab bovenaan "Standaard voor de organisatie: Viewer"
**And** toont alleen groep "Redactie" als uitzondering met rol "Contributor"
**And** org-leden zonder extra rechten worden NIET individueel getoond

## Scenario 8: Owner wijzigt default_org_role via Members tab

**Given** een bestaande KB met `default_org_role = "viewer"`
**When** de owner de default rol wijzigt naar "contributor"
**Then** wordt `PATCH /api/app/knowledge-bases/{slug}` aangeroepen met `default_org_role = "contributor"`
**And** alle org-leden krijgen effectief contributor-toegang
**And** de Members tab reflecteert de wijziging direct

## Scenario 9: Backwards compatibility bestaande KBs

**Given** bestaande KBs aangemaakt voor SPEC-KB-018
**When** de Alembic migratie wordt uitgevoerd
**Then** krijgen alle bestaande KBs `default_org_role = "viewer"`
**And** het gedrag van bestaande KBs is ongewijzigd

## Scenario 10: Samenvatting card voor aanmaak

**Given** een gebruiker heeft alle wizard stappen doorlopen
**When** de samenvatting wordt getoond
**Then** bevat deze: KB naam, slug, visibility met icoon, default rol, eventuele extra members, docs vermelding
**And** de "Aanmaken" knop is actief

---

## Edge Cases

- Personal KB (`owner_type = "user"`): geen visibility cards, geen default_org_role
- User in meerdere groepen met verschillende rollen: highest wins
- Groep zonder leden: kan wel toegevoegd worden, geen effect tot leden worden toegevoegd
- Beperkt KB met alleen groepen (geen individuele users): geldig
- Creator verwijdert zichzelf als owner: niet toegestaan (bestaande validatie)

---

## Quality Gates

- [ ] Backend unit tests voor `get_user_role_for_kb()` fallback logica
- [ ] Backend unit tests voor `initial_members` in create endpoint
- [ ] Frontend component tests voor visibility cards
- [ ] i18n completeness: alle nieuwe keys in EN + NL
- [ ] Alembic migration up + down werkt correct
- [ ] Bestaande members tab tests blijven groen
- [ ] RLS policies werken correct met nieuw veld
