---
id: SPEC-GDPR-001
version: 1.0.0
status: draft
created: 2026-03-27
updated: 2026-03-27
author: klai-team
priority: medium
---

## HISTORY

| Datum      | Versie | Wijziging                                      |
|------------|--------|-------------------------------------------------|
| 2026-03-27 | 1.0.0  | Initieel SPEC-document na brownfield-implementatie |

---

# SPEC-GDPR-001: Subject Access Request (AVG Art. 15)

## Overzicht

Dit SPEC-document beschrijft de self-service Subject Access Request (SAR) functionaliteit voor het Klai portal. De functie stelt een geauthenticeerde gebruiker in staat om al hun persoonlijke gegevens die Klai verwerkt als JSON-bestand te downloaden, in overeenstemming met de Algemene Verordening Gegevensbescherming (AVG) Artikel 15.

De implementatie is uitgevoerd als brownfield enhancement in het bestaande `me.py` API-bestand en de `account.tsx` frontend-pagina.

## AVG-context

**Wettelijke grondslag:** AVG Artikel 15 (Recht van inzage door de betrokkene)

De betrokkene heeft het recht om van de verwerkingsverantwoordelijke uitsluitsel te verkrijgen over het al dan niet verwerken van hem betreffende persoonsgegevens en, wanneer dat het geval is, om inzage te verkrijgen van die persoonsgegevens.

**Klai's aanpak:**
- Volledig self-service: de gebruiker kan zonder tussenkomst van een beheerder hun eigen gegevens exporteren
- Altijd beperkt tot de eigen gegevens van de ingelogde gebruiker (geen cross-user data)
- JSON-formaat voor machine-leesbaarheid en draagbaarheid
- Externe systemen worden benoemd met instructies voor aanvullende verzoeken

---

## Requirements

### R-GDPR-001: Self-service data-export endpoint

**WHEN** een geauthenticeerde gebruiker een POST-verzoek stuurt naar `/api/me/sar-export` **THEN** retourneert het systeem een JSON-response met alle persoonlijke gegevens die Klai over deze gebruiker verwerkt, gestructureerd in de secties `klai_portal` en `external_systems`.

### R-GDPR-002: Databronnen in klai_portal

**WHERE** persoonlijke gegevens beschikbaar zijn in de portal-database, **THEN** bevat de `klai_portal`-sectie de volgende subsecties:

| Subsectie                | Bron                                            | Inhoud                                                    |
|--------------------------|-------------------------------------------------|-----------------------------------------------------------|
| `identity`               | Zitadel `get_user_by_id` + `has_any_mfa`       | Voornaam, achternaam, display name, e-mail, aanmaakdatum, MFA-status |
| `account`                | `PortalUser` record                             | Rol, status, taalvoorkeur, GitHub-username, KB-instellingen, aanmaakdatum |
| `group_memberships`      | `PortalGroupMembership` + `PortalGroup`         | Groepsnaam, toetredingsdatum, is_group_admin              |
| `knowledge_base_access`  | `PortalUserKBAccess` + `PortalKnowledgeBase`    | KB-naam, slug, rol, toekenningsdatum                      |
| `audit_events`           | `PortalAuditLog` (alleen actor)                 | Actie, resource_type, resource_id, tijdstempel            |
| `usage_events`           | `ProductEvent`                                  | Event-type, tijdstempel                                   |
| `meetings`               | `VexaMeeting`                                   | Titel, platform, URL, status, taal, duur, tijdstempels, transcript, samenvatting |

### R-GDPR-003: Graceful degradation bij Zitadel-fouten

**IF** de Zitadel identity-fetch (`get_user_by_id`) faalt, **THEN** gaat de export door met lege `identity`-velden (alle velden `null`) en wordt de fout gelogd als warning. De export mag niet falen door een Zitadel-storing.

**IF** de Zitadel MFA-check (`has_any_mfa`) faalt, **THEN** wordt `mfa_enrolled` op `false` gezet en wordt de fout gelogd als warning. De export mag niet falen door een MFA-check fout.

### R-GDPR-004: Security en privacy invarianten

Het systeem **moet altijd** de volgende invarianten handhaven:

1. De SAR-export is uitsluitend self-service: een gebruiker kan alleen hun **eigen** gegevens exporteren. Er is geen admin-override of cross-user toegang.
2. Authenticatie verloopt via hetzelfde bearer-token patroon als de bestaande `/api/me` endpoint.
3. Als de geauthenticeerde gebruiker geen `portal_users` record heeft, retourneert de endpoint HTTP 404.
4. Als het token ongeldig of verlopen is, retourneert de endpoint HTTP 401.

### R-GDPR-005: Externe systemen documentatie

**WHERE** persoonlijke gegevens buiten de portal-database worden verwerkt, **THEN** bevat de `external_systems`-sectie per systeem een notitie en beschikbare identifiers:

| Systeem     | Inhoud                                                                                     |
|-------------|--------------------------------------------------------------------------------------------|
| `moneybird` | Org-level `moneybird_contact_id` + instructie om privacy@getklai.com te mailen voor export |
| `librechat` | `librechat_user_id` (indien beschikbaar) + notitie dat chatgeschiedenis in MongoDB zit      |
| `twenty_crm`| Notitie dat er geen portal-Twenty koppeling bestaat + contact privacy@getklai.com           |

### R-GDPR-006: Frontend download-trigger

**WHEN** de gebruiker op de "Download mijn gegevens" knop klikt op de account-pagina, **THEN**:

1. Wordt een POST-request gestuurd naar `/api/me/sar-export`
2. Wordt de JSON-response omgezet naar een Blob
3. Wordt een browser-download getriggerd met bestandsnaam `sar-export-{YYYY-MM-DD}.json`
4. Wordt een laadstatus getoond tijdens het ophalen
5. Wordt een foutmelding getoond bij een mislukte request

---

## Buiten scope

De volgende onderdelen zijn **bewust** uitgesloten uit deze SPEC:

| Uitgesloten onderdeel                  | Reden                                                                                  |
|----------------------------------------|----------------------------------------------------------------------------------------|
| `audit_log.details` veld               | Kan organisatiebrede data bevatten (andere gebruikers, systeem-metadata)               |
| `product_events.properties` veld       | Kan organisatiebrede data bevatten (bijv. gedeelde sessie-informatie)                  |
| LibreChat chatgeschiedenis             | Staat in MongoDB per-tenant, niet in de portal-database; gedocumenteerd in `external_systems` |
| Twenty CRM gebruikersdata              | Geen portal-Twenty mapping beschikbaar; gedocumenteerd in `external_systems`           |
| Moneybird volledige factuurexport      | Moneybird is een extern systeem; alleen `moneybird_contact_id` is beschikbaar in portal DB |
| PDF/CSV exportformaten                 | JSON is voldoende voor AVG Art. 15; andere formaten kunnen later worden toegevoegd      |
| Admin-geinitieerde SAR namens gebruiker | Geen vereiste in huidige fase; alle requests zijn self-service                          |
| Rate limiting op SAR endpoint          | Niet geimplementeerd in huidige fase; kan worden toegevoegd als misbruik optreedt       |

---

## Traceability

| TAG              | Bestand                                          |
|------------------|--------------------------------------------------|
| SPEC-GDPR-001   | `.moai/specs/SPEC-GDPR-001/spec.md`             |
| R-GDPR-001      | `portal/backend/app/api/me.py` (`sar_export`)   |
| R-GDPR-002      | `portal/backend/app/api/me.py` (DB queries 1-8) |
| R-GDPR-003      | `portal/backend/app/api/me.py` (try/except blokken) |
| R-GDPR-004      | `portal/backend/app/api/me.py` (auth + 401/404) |
| R-GDPR-005      | `portal/backend/app/api/me.py` (external_systems) |
| R-GDPR-006      | `portal/frontend/src/routes/app/account.tsx`     |
