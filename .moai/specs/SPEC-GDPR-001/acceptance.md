---
id: SPEC-GDPR-001
document: acceptance
---

# SPEC-GDPR-001: Acceptatiecriteria

## Testscenario's (Given/When/Then)

### Scenario 1: Happy path -- geauthenticeerde gebruiker met alle datasecties

```gherkin
Given een geauthenticeerde gebruiker met een geldig bearer token
  And de gebruiker heeft een portal_users record gekoppeld aan een portal_orgs record
  And de gebruiker heeft groepslidmaatschappen, KB-toegang, audit-events, usage-events en meetings
  And Zitadel identity-fetch en MFA-check slagen

When de gebruiker POST /api/me/sar-export aanroept

Then retourneert het systeem HTTP 200
  And de response bevat top-level keys: generated_at, request_user_id, klai_portal, external_systems
  And klai_portal.identity bevat first_name, last_name, display_name, email, created_at, mfa_enrolled
  And klai_portal.account bevat role, status, preferred_language, github_username, display_name, email, kb_retrieval_enabled, kb_personal_enabled, kb_slugs_filter, created_at
  And klai_portal.group_memberships is een niet-lege array met group_name, joined_at, is_group_admin
  And klai_portal.knowledge_base_access is een niet-lege array met kb_name, kb_slug, role, granted_at
  And klai_portal.audit_events is een niet-lege array met action, resource_type, resource_id, created_at
  And klai_portal.audit_events bevat GEEN details veld
  And klai_portal.usage_events is een niet-lege array met event_type, created_at
  And klai_portal.usage_events bevat GEEN properties veld
  And klai_portal.meetings is een niet-lege array met transcript_text en summary_json
  And external_systems.moneybird bevat contact_id en note
  And external_systems.librechat bevat librechat_user_id en note
  And external_systems.twenty_crm bevat note
```

### Scenario 2: Gebruiker niet gevonden -- geen portal_users record

```gherkin
Given een geauthenticeerde gebruiker met een geldig bearer token
  And het token bevat een geldig Zitadel sub claim
  But de gebruiker heeft geen portal_users record in de database

When de gebruiker POST /api/me/sar-export aanroept

Then retourneert het systeem HTTP 404
  And de response body bevat detail: "User not found"
```

### Scenario 3: Zitadel identity-fetch faalt -- graceful degradation

```gherkin
Given een geauthenticeerde gebruiker met een geldig bearer token
  And de gebruiker heeft een portal_users record
  And Zitadel get_user_by_id gooit een exception (bijv. timeout, 500)

When de gebruiker POST /api/me/sar-export aanroept

Then retourneert het systeem HTTP 200 (NIET een error)
  And klai_portal.identity.first_name is null
  And klai_portal.identity.last_name is null
  And klai_portal.identity.display_name is null
  And klai_portal.identity.email is null
  And klai_portal.identity.created_at is null
  And de overige secties (account, group_memberships, etc.) zijn normaal gevuld
  And er is een warning-level log geschreven met de Zitadel-foutmelding
```

### Scenario 4: Gebruiker zonder meetings, groepen of KB-toegang -- lege arrays

```gherkin
Given een geauthenticeerde gebruiker met een geldig bearer token
  And de gebruiker heeft een portal_users record
  And de gebruiker heeft geen groepslidmaatschappen
  And de gebruiker heeft geen KB-toegang
  And de gebruiker heeft geen audit-events
  And de gebruiker heeft geen usage-events
  And de gebruiker heeft geen meetings

When de gebruiker POST /api/me/sar-export aanroept

Then retourneert het systeem HTTP 200
  And klai_portal.group_memberships is een lege array []
  And klai_portal.knowledge_base_access is een lege array []
  And klai_portal.audit_events is een lege array []
  And klai_portal.usage_events is een lege array []
  And klai_portal.meetings is een lege array []
  And geen van deze velden is null
```

### Scenario 5: Frontend download -- blob is valide JSON met correct bestandsformaat

```gherkin
Given de gebruiker is ingelogd op de account-pagina in het portal
  And de SAR Card-sectie is zichtbaar met titel en beschrijving

When de gebruiker op de "Download mijn gegevens" knop klikt

Then toont de knop een laadstatus ("Exporteren..." / "Exporting...")
  And wordt een POST-request gestuurd naar /api/me/sar-export met het bearer token
  And wordt de JSON-response omgezet naar een Blob met type application/json
  And wordt een download getriggerd met bestandsnaam sar-export-{YYYY-MM-DD}.json
  And is het gedownloade bestand valide JSON
  And bevat het JSON-bestand de keys generated_at, request_user_id, klai_portal, external_systems
```

### Scenario 6: Frontend foutafhandeling

```gherkin
Given de gebruiker is ingelogd op de account-pagina
  And de backend retourneert een error (bijv. 500, netwerk-timeout)

When de gebruiker op de "Download mijn gegevens" knop klikt

Then toont het systeem een foutmelding in de destructive color token
  And de foutmelding is vertaald via i18n (account_sar_error)
  And de knop is weer klikbaar (niet disabled)
```

### Scenario 7: Ongeldig of verlopen token

```gherkin
Given een gebruiker met een ongeldig of verlopen bearer token

When de gebruiker POST /api/me/sar-export aanroept

Then retourneert het systeem HTTP 401
  And de response body bevat detail: "Invalid token" of "Invalid or expired token"
```

---

## Kwaliteitsgates

### Backend tests

```bash
# Alle SAR tests moeten slagen
uv run pytest tests/test_sar_export.py -v

# Verwacht resultaat: minimaal 3 tests PASSED (bestaande tests)
# Doelstelling: uitbreiden naar 5+ tests (error path, degradation, lege arrays)
```

### Linting en type-checking

```bash
# Ruff compliance (geen warnings/errors)
uv run ruff check app/api/me.py

# Pyright compliance (geen type errors)
uv run pyright app/api/me.py
```

### Frontend checks

```bash
# TypeScript compilatie (al bevestigd passing)
cd portal/frontend && npm run i18n:compile && npx tsc --noEmit

# Verwacht resultaat: 0 errors
```

### Coverage doelstelling

De tests in `test_sar_export.py` moeten minimaal de volgende paden dekken:

| Pad                                    | Test aanwezig? |
|----------------------------------------|----------------|
| Happy path: alle secties gevuld        | Ja             |
| MFA-status in identity                 | Ja             |
| Moneybird contact_id in external       | Ja             |
| User not found (404)                   | Ja             |
| Zitadel identity-fetch faalt           | Ja             |
| Zitadel MFA-check faalt               | Geïmpleerd via degradation test (mfa_enrolled=False) |
| Lege arrays (geen meetings/groepen)    | Geïmpleerd via degradation test (alle secties leeg) |

### MX tags

- [ ] `sar_export` functie in `me.py`: overweeg `@MX:ANCHOR` -- publiek endpoint met externe afhankelijkheden (Zitadel) en privacy-gevoelige data
- [ ] SAR response-modellen: overweeg `@MX:NOTE` -- GDPR Art. 15 compliance-vereisten

---

## Definition of Done

- [x] `POST /api/me/sar-export` endpoint operationeel met bearer-auth
- [x] Response bevat alle 7 subsecties in `klai_portal`
- [x] Response bevat alle 3 externe systemen met notities
- [x] Frontend download-flow werkt met correcte bestandsnaam
- [x] i18n-keys aanwezig in NL en EN
- [x] Bestaande 3 unit tests slagen
- [x] Error-path tests toegevoegd (404, Zitadel degradation) — 5 tests totaal
- [x] Ruff + Pyright compliance geverifieerd (0 errors, 0 warnings)
- [x] MX tags toegevoegd aan `sar_export` en response-modellen

---

## Traceability

| TAG            | Referentie                          |
|----------------|-------------------------------------|
| SPEC-GDPR-001 | `spec.md` -- requirements           |
| SPEC-GDPR-001 | `plan.md` -- implementatieplan      |
