---
spec_id: SPEC-KNOW-004
type: acceptance-criteria
version: 1.0.0
---

# Acceptatiecriteria SPEC-KNOW-004

## M1: Save Confirmation in Chat

### AC-M1-01: AI vraagt bevestiging voor opslaan

```gherkin
Given de gebruiker een gesprek voert met de AI
When de AI bepaalt dat informatie opgeslagen moet worden als persoonlijke kennis
Then vraagt de AI expliciet: "Wil je dat ik dit opsla als persoonlijke kennis?"
And de AI roept NIET `save_personal_knowledge` aan voordat de gebruiker bevestigt
```

### AC-M1-02: Gebruiker bevestigt opslaan

```gherkin
Given de AI heeft gevraagd om bevestiging voor opslaan
When de gebruiker bevestigend antwoordt (ja, yes, opsalaan, etc.)
Then roept de AI `save_personal_knowledge` aan met de juiste parameters
And gebruikt de AI een van de geldige assertion_mode waarden: fact, claim, note
```

### AC-M1-03: Gebruiker weigert opslaan

```gherkin
Given de AI heeft gevraagd om bevestiging voor opslaan
When de gebruiker afwijzend antwoordt (nee, no, niet opslaan, etc.)
Then slaat de AI het item NIET op
And gaat de AI door met het gesprek zonder verdere opslagpogingen voor dit item
```

### AC-M1-04: assertion_mode waarden kloppen

```gherkin
Given de agent-system-prompt.md is bijgewerkt
When de AI een assertion_mode selecteert voor een kennisitem
Then is de waarde een van: fact, claim, note
And komen deze waarden overeen met wat de save_personal_knowledge tool accepteert
```

## M2: Personal Knowledge List Endpoint

### AC-M2-01: Succesvolle lijst ophalen

```gherkin
Given er zijn 5 actieve persoonlijke kennisitems voor gebruiker "user-123" in org "org-456"
When een GET request wordt gestuurd naar /knowledge/v1/personal/items?org_id=org-456&user_id=user-123
  met een geldig X-Internal-Secret header
Then retourneert het endpoint een 200 response
And bevat de response een "items" array met 5 objecten
And heeft elk item de velden: id, path, assertion_mode, tags, created_at
And bevat de response total=5, limit=50, offset=0
```

### AC-M2-02: Paginering werkt correct

```gherkin
Given er zijn 75 actieve persoonlijke kennisitems
When een GET request wordt gestuurd met limit=20&offset=40
Then retourneert het endpoint 20 items
And is total=75
And is offset=40
```

### AC-M2-03: Verwijderde items worden niet getoond

```gherkin
Given er is een kennisitem met belief_time_end != 253402300800 (soft-deleted)
When de lijst wordt opgehaald
Then bevat de response dit item NIET
```

### AC-M2-04: Limit wordt begrensd op 200

```gherkin
Given een request met limit=500
When de lijst wordt opgehaald
Then wordt de effectieve limit begrensd tot 200
```

### AC-M2-05: Ontbrekende parameters

```gherkin
Given een request zonder org_id parameter
When de lijst wordt opgehaald
Then retourneert het endpoint een 400 Bad Request
```

## M3: Personal Knowledge Delete Endpoint

### AC-M3-01: Succesvol verwijderen

```gherkin
Given er is een actief kennisitem met id "item-abc" voor user "user-123" in org "org-456"
When een DELETE request wordt gestuurd naar /knowledge/v1/personal/items/item-abc?org_id=org-456&user_id=user-123
  met een geldig X-Internal-Secret header
Then retourneert het endpoint {"status": "ok"}
And is belief_time_end in PostgreSQL gezet op een timestamp (niet meer 253402300800)
And is het document verwijderd uit Qdrant
```

### AC-M3-02: Item niet gevonden

```gherkin
Given er is GEEN kennisitem met id "nonexistent-id" voor de opgegeven gebruiker
When een DELETE request wordt gestuurd voor dit id
Then retourneert het endpoint een 404 Not Found
```

### AC-M3-03: Ownership verificatie

```gherkin
Given er is een kennisitem met id "item-abc" voor user "user-123"
When een DELETE request wordt gestuurd met user_id="other-user-456"
Then retourneert het endpoint een 404 Not Found
And is het item NIET verwijderd
```

### AC-M3-04: Reeds verwijderd item

```gherkin
Given er is een kennisitem dat al soft-deleted is (belief_time_end != sentinel)
When een DELETE request wordt gestuurd voor dit item
Then retourneert het endpoint een 404 Not Found
```

### AC-M3-05: Verwijderd item niet vindbaar in AI zoekresultaten

```gherkin
Given een kennisitem is succesvol verwijderd via het delete endpoint
When de AI een zoekopdracht uitvoert in de persoonlijke kennisbank
Then wordt het verwijderde item NIET geretourneerd in de zoekresultaten
```

## M4: Portal Backend Proxy

### AC-M4-01: Geauthenticeerde list request

```gherkin
Given een gebruiker is ingelogd met een geldig OIDC token
When de gebruiker een GET request stuurt naar /api/knowledge/personal/items
Then extraheert het backend de zitadel_user_id en klai_org_id
And proxiet het backend naar knowledge-ingest GET /knowledge/v1/personal/items
And ontvangt de gebruiker de lijst met persoonlijke kennisitems
```

### AC-M4-02: Geauthenticeerde delete request

```gherkin
Given een gebruiker is ingelogd met een geldig OIDC token
When de gebruiker een DELETE request stuurt naar /api/knowledge/personal/items/{artifact_id}
Then extraheert het backend de zitadel_user_id en klai_org_id
And proxiet het backend naar knowledge-ingest DELETE endpoint
And ontvangt de gebruiker {"status": "ok"} bij succes
```

### AC-M4-03: Ongeauthenticeerde request

```gherkin
Given een request zonder geldig Authorization header
When de request wordt gestuurd naar /api/knowledge/personal/items
Then retourneert het backend een 401 Unauthorized
```

### AC-M4-04: Geen admin-rol vereist

```gherkin
Given een gebruiker met een standaard (niet-admin) rol
When de gebruiker personal knowledge endpoints aanroept
Then worden de requests normaal verwerkt
And is er geen admin-rol check
```

## M5: Portal Frontend

### AC-M5-01: Persoonlijke items tabel wordt getoond

```gherkin
Given de gebruiker heeft 3 opgeslagen persoonlijke kennisitems
When de gebruiker de knowledge pagina bezoekt
Then ziet de gebruiker een tabel met 3 rijen
And heeft elke rij kolommen: Titel, Type, Opgeslagen op, Acties
```

### AC-M5-02: Type badge wordt correct weergegeven

```gherkin
Given een kennisitem met assertion_mode "fact"
When het item in de tabel wordt getoond
Then wordt "fact" als een badge/tag weergegeven
```

### AC-M5-03: Delete met bevestiging

```gherkin
Given de gebruiker ziet een kennisitem in de tabel
When de gebruiker op de verwijderknop klikt
Then toont het systeem een bevestigingsdialoog (DeleteConfirmButton)
And pas na bevestiging wordt het delete request verstuurd
```

### AC-M5-04: Lijst ververst na verwijdering

```gherkin
Given de gebruiker heeft een kennisitem verwijderd
When de verwijdering succesvol is afgerond
Then wordt de tabel automatisch ververst
And is het verwijderde item niet meer zichtbaar
```

### AC-M5-05: Lege staat

```gherkin
Given de gebruiker heeft geen persoonlijke kennisitems opgeslagen
When de gebruiker de knowledge pagina bezoekt
Then toont het systeem een vriendelijke melding dat er nog geen items zijn
And bevat de melding uitleg over hoe kennisitems opgeslagen worden (via de AI chat)
```

### AC-M5-06: i18n ondersteuning

```gherkin
Given de portal is ingesteld op Nederlands
When de knowledge pagina wordt geladen
Then zijn alle labels en meldingen in het Nederlands

Given de portal is ingesteld op Engels
When de knowledge pagina wordt geladen
Then zijn alle labels en meldingen in het Engels
```

## Scope Grenzen (buiten deze SPEC)

- Schema alignment van assertion_mode waarden in de database
- Tags ophalen uit Qdrant voor de list endpoint (v2)
- Bulk delete functionaliteit
- Zoeken/filteren binnen persoonlijke kennisitems
- Bewerken van bestaande kennisitems
- Kennisitems exporteren

## Definition of Done

- [ ] M1: System prompt bevat bevestigingsinstructie en correcte assertion_mode waarden
- [ ] M2: List endpoint retourneert gepagineerde persoonlijke items met correcte filtering
- [ ] M3: Delete endpoint soft-deletet in PostgreSQL en verwijdert uit Qdrant
- [ ] M4: Portal backend proxy routes werken met OIDC authenticatie
- [ ] M5: Frontend tabel toont items met delete functionaliteit en lege staat
- [ ] Alle endpoints zijn beveiligd (X-Internal-Secret / OIDC)
- [ ] Gebruikers kunnen alleen hun eigen items zien en verwijderen
- [ ] i18n message keys zijn toegevoegd voor NL en EN
