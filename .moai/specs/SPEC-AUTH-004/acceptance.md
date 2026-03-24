# SPEC-AUTH-004: Group-Based Product Entitlements -- Acceptance Criteria

**SPEC ID:** SPEC-AUTH-004
**Status:** Draft

---

## Test Scenarios

### TS-001: Groepsproduct Toewijzen (R2)

**Given** org "Acme" heeft plan "professional" (producten: chat, scribe)
**And** groep "Developers" bestaat in org "Acme" zonder producttoewijzingen
**When** een org-admin product "chat" toewijst aan groep "Developers"
**Then** wordt een `portal_group_products` record aangemaakt met group_id, org_id, product="chat", enabled_at en enabled_by
**And** de response status is `201 Created`

**Given** org "Acme" heeft plan "professional" (producten: chat, scribe)
**And** groep "Developers" heeft al product "chat" toegewezen
**When** een org-admin product "chat" nogmaals toewijst aan groep "Developers"
**Then** retourneert het systeem `409 Conflict` met detail "Product already assigned to group"
**And** er wordt geen duplicate record aangemaakt

### TS-002: Plan-plafond Handhaving voor Groepen (R4)

**Given** org "Acme" heeft plan "professional" (producten: chat, scribe)
**And** groep "Researchers" bestaat in org "Acme"
**When** een org-admin product "knowledge" toewijst aan groep "Researchers"
**Then** retourneert het systeem `403 Forbidden` met detail "Product not included in org plan"
**And** er wordt geen `portal_group_products` record aangemaakt

**Given** org "Acme" heeft plan "complete" (producten: chat, scribe, knowledge)
**And** groep "Researchers" bestaat in org "Acme"
**When** een org-admin product "knowledge" toewijst aan groep "Researchers"
**Then** wordt het record succesvol aangemaakt met status `201 Created`

### TS-003: Groepsproduct Intrekken (R3)

**Given** groep "Developers" heeft product "chat" toegewezen
**When** een org-admin product "chat" intrekt van groep "Developers"
**Then** wordt het `portal_group_products` record verwijderd
**And** de response status is `204 No Content`

**Given** groep "Developers" heeft geen product "scribe" toegewezen
**When** een org-admin product "scribe" intrekt van groep "Developers"
**Then** retourneert het systeem `404 Not Found`

### TS-004: Gebruiker Erft Toegang via Groepslidmaatschap (R1)

**Given** gebruiker "alice" is lid van groep "Developers"
**And** groep "Developers" heeft product "chat" toegewezen
**And** gebruiker "alice" heeft geen directe `portal_user_products` toewijzing voor "chat"
**When** de effectieve producten van "alice" worden berekend
**Then** bevat het resultaat "chat"

**Given** gebruiker "bob" is lid van groep "Developers" (product: chat) en groep "Writers" (product: scribe)
**And** gebruiker "bob" heeft een directe toewijzing voor "knowledge"
**When** de effectieve producten van "bob" worden berekend
**Then** bevat het resultaat {"chat", "scribe", "knowledge"} (unie van alle bronnen)

### TS-005: Gebruiker Verliest Toegang na Groepslidmaatschap-verwijdering (R1, R3)

**Given** gebruiker "alice" is lid van groep "Developers" met product "chat"
**And** "alice" heeft geen directe toewijzing voor "chat"
**When** "alice" wordt verwijderd uit groep "Developers"
**Then** bevat de effectieve productenlijst van "alice" niet langer "chat"
**And** bij de volgende JWT refresh ontbreekt "chat" uit de `klai:products` claim

**Given** gebruiker "alice" is lid van groep "Developers" met product "chat"
**And** "alice" heeft ook een directe `portal_user_products` toewijzing voor "chat"
**When** "alice" wordt verwijderd uit groep "Developers"
**Then** bevat de effectieve productenlijst van "alice" nog steeds "chat" (via directe toewijzing)

### TS-006: JWT Enrichment met Groepsproducten (R7)

**Given** gebruiker "alice" heeft directe toewijzing voor "chat"
**And** "alice" is lid van groep "Writers" met product "scribe"
**When** Zitadel de interne endpoint `/internal/users/{alice_id}/products` aanroept
**Then** retourneert het endpoint `["chat", "scribe"]`
**And** de `klai:products` JWT claim bevat beide producten

**Given** gebruiker "bob" heeft geen directe toewijzingen
**And** "bob" is lid van geen enkele groep met producttoewijzingen
**When** Zitadel de interne endpoint `/internal/users/{bob_id}/products` aanroept
**Then** retourneert het endpoint `[]`
**And** de `klai:products` JWT claim is leeg

### TS-007: Plan Downgrade Trekt Groepsproducten In (R5)

**Given** org "Acme" heeft plan "complete" (producten: chat, scribe, knowledge)
**And** groep "Researchers" heeft product "knowledge" toegewezen
**And** groep "Developers" heeft producten "chat" en "scribe" toegewezen
**When** org "Acme" wordt gedowngraded naar plan "professional" (producten: chat, scribe)
**Then** wordt het `portal_group_products` record voor "knowledge" in groep "Researchers" verwijderd
**And** blijven de records voor "chat" en "scribe" in groep "Developers" behouden
**And** worden ook directe `portal_user_products` records voor "knowledge" verwijderd (bestaande logica)

**Given** org "Acme" heeft plan "professional" (producten: chat, scribe)
**And** groep "All Users" heeft producten "chat" en "scribe" toegewezen
**When** org "Acme" wordt gedowngraded naar plan "core" (producten: chat)
**Then** wordt het `portal_group_products` record voor "scribe" verwijderd
**And** blijft het record voor "chat" behouden

### TS-008: Standaardgroepen bij Org-aanmaak (R6)

**Given** er wordt een nieuwe organisatie aangemaakt met plan "professional"
**When** het org-creatie proces voltooid is
**Then** bestaan er drie groepen: "Chat users", "Scribe users"
**And** groep "Chat users" heeft product "chat" toegewezen
**And** groep "Scribe users" heeft product "scribe" toegewezen
**And** er is geen "Knowledge users" groep (want knowledge zit niet in plan "professional")

**Given** er wordt een nieuwe organisatie aangemaakt met plan "complete"
**When** het org-creatie proces voltooid is
**Then** bestaan er drie groepen: "Chat users", "Scribe users", "Knowledge users"
**And** elk heeft het corresponderende product toegewezen

**Given** er wordt een nieuwe organisatie aangemaakt met plan "free"
**When** het org-creatie proces voltooid is
**Then** worden er geen standaardgroepen aangemaakt (free plan heeft geen producten)

### TS-009: Autorisatie -- Alleen Org Admin (R2, R3)

**Given** gebruiker "charlie" is group admin van groep "Developers" maar geen org admin
**When** "charlie" probeert product "chat" toe te wijzen aan groep "Developers"
**Then** retourneert het systeem `403 Forbidden`
**And** er wordt geen record aangemaakt

**Given** gebruiker "dave" is org admin van org "Acme"
**When** "dave" product "chat" toewijst aan groep "Developers" in org "Acme"
**Then** wordt het record succesvol aangemaakt met status `201 Created`

### TS-010: require_product() Gate met Groepstoewijzingen (R1)

**Given** gebruiker "alice" heeft geen directe toewijzing voor "scribe"
**And** "alice" is lid van groep "Writers" met product "scribe"
**When** "alice" een request doet naar een endpoint dat `require_product("scribe")` vereist
**Then** wordt de request toegestaan (200 OK)

**Given** gebruiker "bob" heeft geen directe toewijzing voor "knowledge"
**And** "bob" is niet lid van een groep met product "knowledge"
**When** "bob" een request doet naar een endpoint dat `require_product("knowledge")` vereist
**Then** retourneert het systeem `403 Forbidden`

### TS-011: Lijst Groepsproducten (R2)

**Given** groep "Developers" heeft producten "chat" en "scribe" toegewezen
**When** een org-admin `GET /api/admin/groups/{developers_id}/products` aanroept
**Then** retourneert het systeem een lijst met twee items: chat en scribe
**And** elk item bevat product, enabled_at en enabled_by velden

**Given** groep "Empty Group" heeft geen producttoewijzingen
**When** een org-admin `GET /api/admin/groups/{empty_group_id}/products` aanroept
**Then** retourneert het systeem een lege lijst `[]`

### TS-012: Effectieve Producten UI (R8)

**Given** gebruiker "alice" heeft product "chat" via groep "Developers" en product "scribe" via directe toewijzing
**When** een org-admin de bewerkingspagina van "alice" opent
**Then** toont de "Effectieve producten" sectie:
- "chat" met bron "Developers" (groepsnaam)
- "scribe" met bron "Direct"
**And** er zijn geen bewerkbare product-toggles zichtbaar

---

## Quality Gate Criteria

- Alle TS-001 t/m TS-012 scenarios slagen als geautomatiseerde tests
- JWT enrichment latentie: < 50ms extra ten opzichte van huidige baseline
- Geen regressie in bestaande product-gating tests (SPEC-AUTH-002)
- Plan-downgrade tests dekken zowel per-user als groepsproducten
- Frontend: groep-productpagina is volledig functioneel zonder pagina-refresh (optimistic updates)

## Definition of Done

- [ ] Alembic migratie is reversible (up + down)
- [ ] Alle backend endpoints hebben unit tests met >= 85% coverage
- [ ] JWT enrichment integratietest met groepsgebaseerde producten
- [ ] Plan-downgrade test met gecombineerde per-user en groepsproducten
- [ ] Frontend groep-detailpagina toont product toggles
- [ ] Frontend gebruiker-edit toont alleen-lezen effectieve producten
- [ ] Standaardgroepen worden aangemaakt bij nieuwe org-creatie
- [ ] Performance verificatie: JWT enrichment query < 50ms
- [ ] Geen linter warnings, geen TypeScript errors
