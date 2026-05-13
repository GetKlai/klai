---
id: SPEC-GDPR-003
version: 0.1.0
status: done
completed: 2026-05-13
created: 2026-05-13
updated: 2026-05-13
author: klai-team
priority: high
supersedes_scope_of: SPEC-GDPR-001 (LibreChat + Twenty + rate-limit out-of-scope items)
---

## HISTORY

| Datum      | Versie | Wijziging                                                          |
|------------|--------|--------------------------------------------------------------------|
| 2026-05-13 | 0.1.0  | Initieel SPEC — AVG art. 15 volledigheid + abuse-controls          |
| 2026-05-13 | 0.1.0  | Geimplementeerd via Moai:run in Richmond workspace                 |

---

# SPEC-GDPR-003: SAR-export volledigheid en abuse-controls

## Overzicht

SPEC-GDPR-001 leverde een werkende self-service SAR-export, maar drie onderdelen werden bewust **buiten scope** gezet:

- LibreChat chatgeschiedenis (alleen verwezen naar `privacy@getklai.com`)
- Twenty CRM persoonsgegevens (alleen verwezen naar `privacy@getklai.com`)
- Rate-limiting op het endpoint

Bij juridische review (2026-05-13) is vastgesteld dat **LibreChat-chats** en **Twenty CRM-persoonsgegevens** persoonsgegevens zijn in de zin van AVG Art. 4(1) en daarmee onder het inzagerecht van Art. 15 vallen. De huidige "stuur een e-mail"-route is juridisch geldig (Art. 12(3) — antwoord binnen 1 maand), maar de marketing-belofte van een self-service-knop dekt niet de werkelijkheid. Deze SPEC sluit dat gat én pakt twee bekende security-gaten mee (rate-limit + audit-log van de download zelf).

**Moneybird blijft buiten scope:** betaalgegevens zijn organisatie-data van de tenant, geen persoonsgegevens van de individuele eindgebruiker. De org-contactpersoon kan een aparte uitvraag doen via `privacy@getklai.com`.

## Outcome

Geimplementeerd op 2026-05-13:

- SAR-export bevat nu `klai_portal.librechat_conversations` met strikt op `portal_users.librechat_user_id` gescopete LibreChat-conversaties en berichten.
- Twenty CRM wordt op `portal_users.email` bevraagd en vult `external_systems.twenty_crm.records` met de SAR-veilige velden.
- LibreChat en Twenty falen graceful met `null` in de betreffende sectie en warning logs.
- `/api/me/sar-export` heeft een user-keyed Redis sliding-window rate limit van 5 exports per uur met HTTP 429 + `Retry-After`, en fail-closed HTTP 503 bij Redis-onbeschikbaarheid.
- Succesvolle exports en rate-limit rejects schrijven onafhankelijke `PortalAuditLog` entries met `sar.exported` en `sar.rate_limited`.
- De accountpagina toont een specifieke 429-melding.

## AVG-context

**Wettelijke grondslag:** AVG Artikel 15 (Recht van inzage door de betrokkene)

Klai verwerkt namens een betrokkene persoonsgegevens in drie systemen die buiten de portal-database staan:

| Systeem      | Persoonsgegevens                                              | Verplicht in export? |
|--------------|---------------------------------------------------------------|----------------------|
| LibreChat    | Volledige AI-gesprekken (prompts + antwoorden)                | **Ja**               |
| Twenty CRM   | Voornaam, achternaam, e-mailadres, bedrijfsnaam               | **Ja**               |
| Moneybird    | Betaalgegevens van de organisatie                             | Nee (org-data)       |

## Aannames

- LibreChat MongoDB is bereikbaar via `settings.librechat_mongo_root_uri` — patroon bewezen in `app/api/internal.py:741`
- LibreChat-gebruikers zijn gekoppeld aan `portal_users.librechat_user_id` (lazy mapping bestaat al)
- Twenty REST API is bereikbaar via `app/services/twenty.py` met de bestaande `_client()` httpx-wrapper
- Twenty's persons-collectie is bevraagbaar op `email` (REST filter)
- `partner_rate_limit.check_rate_limit` primitive kan ook user-keyed werken (huidige callsites in `internal.py` gebruiken caller_ip; user_id is een andere maar geldige sleutel)
- `PortalAuditLog` schrijfpad bestaat (de SAR-export leest er al uit voor de `audit_events` sectie)

## Randvoorwaarden

- Export mag NIET falen als LibreChat MongoDB onbereikbaar is — graceful degradation met log-warning, identiek aan de bestaande Zitadel-fallback in SPEC-GDPR-001 R-GDPR-003
- Export mag NIET falen als Twenty API onbereikbaar is (zelfde patroon)
- Rate-limit moet **per user_id** keyen, niet per IP — een gestolen bearer-token achter een carrier-NAT moet niet onbeperkt kunnen downloaden
- Rate-limit-overschrijding levert HTTP 429 met `Retry-After` header
- Audit-log entry voor de download zelf moet de exporterende user als `actor_user_id` hebben, ook bij rate-limit-rejectie (zodat misbruikpogingen zichtbaar zijn)

---

## Requirements

### R-1: LibreChat-conversaties in de export

**WHEN** een geauthenticeerde gebruiker een POST-verzoek stuurt naar `/api/me/sar-export` **AND** de gebruiker een gekoppelde `librechat_user_id` heeft, **THEN** bevat de `klai_portal`-sectie een nieuwe subsectie `librechat_conversations` met per gesprek: titel, aanmaakdatum, laatst-gewijzigd-datum, en alle berichten (rol, tekst, tijdstempel).

**Traceability:** SPEC-GDPR-003-R1

### R-2: Twenty CRM-persoonsgegevens in de export

**WHEN** een geauthenticeerde gebruiker een POST-verzoek stuurt naar `/api/me/sar-export`, **THEN** zoekt het systeem in Twenty CRM op `email` (van het bevestigde `portal_users.email` veld) en neemt elke matchende persoon op in de nieuwe subsectie `external_systems.twenty_crm.records` met: voornaam, achternaam, e-mail, bedrijfsnaam.

**WHERE** geen matchende personen worden gevonden in Twenty, **THEN** is `records` een lege lijst en blijft de `note` aanwezig voor procedurele uitleg.

**Traceability:** SPEC-GDPR-003-R2

### R-3: Graceful degradation voor LibreChat en Twenty

**IF** de LibreChat MongoDB-query faalt (connectie-fout, timeout, ontbrekende configuratie), **THEN** wordt `librechat_conversations` op `null` gezet, een waarschuwing gelogd, en gaat de export door. Het `librechat_user_id`-veld blijft beschikbaar als referentie.

**IF** de Twenty API-call faalt, **THEN** wordt `external_systems.twenty_crm.records` op `null` gezet, een waarschuwing gelogd, en gaat de export door. De bestaande `note` blijft aanwezig.

**Traceability:** SPEC-GDPR-003-R3

### R-4: Rate-limiting op SAR-export endpoint

Het systeem **SHALL** maximaal **5 succesvolle SAR-exports per gebruiker per uur** toestaan.

**WHEN** een gebruiker het zesde verzoek binnen een uur stuurt, **THEN** retourneert het systeem HTTP 429 met een `Retry-After` header die het aantal seconden tot de volgende toegestane poging bevat.

De rate-limiter SHALL keyen op `zitadel_user_id`, niet op IP. De bestaande sliding-window primitive in `app/services/partner_rate_limit.py` SHALL hergebruikt worden.

**WHEN** de rate-limit-backend (Redis) onbereikbaar is, **THEN** SHALL het systeem fail-closed werken (verzoek weigeren met 503), conform de bestaande `internal_rate_limit_fail_mode="closed"` conventie voor security-kritieke endpoints.

**Traceability:** SPEC-GDPR-003-R4

### R-5: Audit-log entry op SAR-download

**WHEN** een SAR-export-verzoek succesvol wordt afgerond (HTTP 200), **THEN** SHALL het systeem een `PortalAuditLog` entry schrijven met:
- `action = "sar.exported"`
- `actor_user_id = <zitadel_user_id van de aanvrager>`
- `resource_type = "self"`
- `resource_id = <zitadel_user_id van de aanvrager>`
- `created_at = <tijdstip van de export>`

**WHEN** een SAR-export-verzoek wordt afgewezen door de rate-limiter (HTTP 429), **THEN** SHALL het systeem een `PortalAuditLog` entry schrijven met `action = "sar.rate_limited"` en dezelfde overige velden, zodat misbruik zichtbaar is.

De audit-entry SHALL geschreven worden via een **onafhankelijke session** (`AsyncSessionLocal()`), conform het bestaande fire-and-forget patroon in `portal-backend.md` — een rollback in de request-scope session mag de audit-entry niet wegvegen.

**Traceability:** SPEC-GDPR-003-R5

### R-6: Externe-systemen notitie bijwerken

**WHERE** SPEC-GDPR-001 R-GDPR-005 een `external_systems.librechat.note` definieerde die naar `privacy@getklai.com` verwees, **THEN** SHALL die note vervangen worden door een korte verklaring dat AI-gesprekken nu in de `klai_portal.librechat_conversations` sectie staan. Het `librechat_user_id` veld blijft behouden voor traceability.

**WHERE** SPEC-GDPR-001 R-GDPR-005 een `external_systems.twenty_crm.note` definieerde, **THEN** SHALL die note vervangen worden door een verklaring dat matchende CRM-records nu in `records` staan, met fallback-tekst als de Twenty-lookup faalde (zie R-3).

**Traceability:** SPEC-GDPR-003-R6

### R-7: Security en privacy invarianten (behoud)

Het systeem **SHALL** alle bestaande invarianten uit SPEC-GDPR-001 R-GDPR-004 behouden:

1. Self-service only — geen admin-override
2. Authenticatie via bearer-token
3. HTTP 401 bij ongeldig/verlopen token
4. HTTP 404 bij ontbrekend `portal_users` record

Aanvullend:

5. De LibreChat-query SHALL strikt scopen op `librechat_user_id == portal_user.librechat_user_id` — geen tenant-brede dump
6. De Twenty-query SHALL strikt scopen op `email == portal_user.email` — geen company-brede dump
7. Geen velden uit LibreChat of Twenty die niet over de betrokkene zelf gaan worden meegenomen (bijv. messages van andere users in shared conversations — Klai gebruikt LibreChat per-user, dus dit risico is laag, maar de query SHALL het expliciet uitsluiten)

**Traceability:** SPEC-GDPR-003-R7

---

## Buiten scope

| Uitgesloten onderdeel                  | Reden                                                                                  |
|----------------------------------------|----------------------------------------------------------------------------------------|
| Moneybird factuur-export               | Betaalgegevens zijn org-data, niet persoonsgegevens van de individuele user            |
| Cryptografische ondertekening (HMAC/JWS) | Niet vereist onder AVG Art. 15; expliciet besloten door product-owner op 2026-05-13 |
| Zip-formaat                            | JSON blijft de single-file vorm; de marketing-belofte was "een JSON-bestand"           |
| Pagination/chunking voor grote exports | Eerst meten of >50MB exports in de praktijk voorkomen; optimalisatie later             |
| Admin-geinitieerde SAR namens gebruiker | Behoudt self-service-only invariant uit SPEC-GDPR-001                                  |
| Frontend wijzigingen anders dan 429-handling | UI blijft één Download-knop; alleen toast-melding bij rate-limit nodig          |

---

## Afhankelijkheden

| Component                   | Locatie                                                  | Relatie         |
|-----------------------------|----------------------------------------------------------|-----------------|
| sar_export endpoint         | `klai-portal/backend/app/api/me.py:325-534`              | Uitbreiden      |
| SarExportResponse schemas   | `klai-portal/backend/app/api/me.py:189-286`              | Velden toevoegen |
| LibreChat MongoDB pattern   | `klai-portal/backend/app/api/internal.py:741`            | Referentiepatroon |
| Twenty service              | `klai-portal/backend/app/services/twenty.py`             | Methode toevoegen |
| Rate-limit primitive        | `klai-portal/backend/app/services/partner_rate_limit.py` | Hergebruiken    |
| Audit log model             | `klai-portal/backend/app/models/audit.py`                | Schrijfpad      |
| Frontend SAR mutation       | `klai-portal/frontend/src/routes/app/account.tsx:58-74`  | 429-handling toevoegen |
| Tests                       | `klai-portal/backend/tests/test_sar_export.py`           | Uitbreiden      |

---

## Traceability

| TAG                | Bestand                                                          |
|--------------------|------------------------------------------------------------------|
| SPEC-GDPR-003     | `.moai/specs/SPEC-GDPR-003/spec.md`                              |
| R-1               | `klai-portal/backend/app/api/me.py` (LibreChat-query)            |
| R-2               | `klai-portal/backend/app/api/me.py` + `app/services/twenty.py`   |
| R-3               | `klai-portal/backend/app/api/me.py` (try/except blokken)         |
| R-4               | `klai-portal/backend/app/api/me.py` + `app/services/partner_rate_limit.py` |
| R-5               | `klai-portal/backend/app/api/me.py` (audit-write na 200/429)     |
| R-6               | `klai-portal/backend/app/api/me.py:494-519` (notes herschrijven) |
| R-7               | `klai-portal/backend/app/api/me.py` (scoping invarianten)        |
