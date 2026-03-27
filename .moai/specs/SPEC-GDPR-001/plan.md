---
id: SPEC-GDPR-001
document: plan
---

# SPEC-GDPR-001: Implementatieplan

## Technische aanpak

De SAR-functionaliteit is geimplementeerd als **brownfield enhancement**: toegevoegd aan bestaande bestanden in plaats van een nieuw API-module. Dit is bewust gekozen omdat:

1. De endpoint hoort semantisch bij `/api/me` (gebruiker vraagt eigen data op)
2. Hergebruik van bestaande auth-patronen (`HTTPBearer` + `zitadel.get_userinfo`)
3. Geen nieuwe dependencies of infra-wijzigingen nodig
4. De Pydantic response-modellen leven in hetzelfde bestand als de endpoint

**Architectuurpatroon:** Directe DB-queries in de endpoint-functie (geen service-laag), consistent met het bestaande `me.py` patroon en het `separation-of-concerns` frontend-patroon (inline `queryFn`).

---

## Bestandswijzigingen

| Bestand                                          | Wijziging                                                         |
|--------------------------------------------------|-------------------------------------------------------------------|
| `portal/backend/app/api/me.py`                   | SAR response-modellen + `POST /api/me/sar-export` endpoint       |
| `portal/backend/tests/test_sar_export.py`        | Unit tests: happy path, MFA-status, Moneybird contact_id         |
| `portal/frontend/src/routes/app/account.tsx`     | SAR download Card-sectie met `useMutation` + Blob-download       |
| `portal/frontend/messages/nl.json`               | i18n-keys: `account_sar_title`, `_description`, `_button`, `_downloading`, `_error` |
| `portal/frontend/messages/en.json`               | Engelse vertalingen van dezelfde i18n-keys                        |

---

## Dataprivacy-beslissingen

### Bewust opgenomen velden

| Data                         | Reden                                                                 |
|------------------------------|-----------------------------------------------------------------------|
| Zitadel identity (live)      | Source of truth voor naam/e-mail; geen stale data uit portal DB       |
| `transcript_text`            | Persoonlijke gegevens: opname van vergaderingen van de gebruiker      |
| `summary_json`               | Afgeleide persoonlijke gegevens: samenvatting van eigen vergaderingen |
| `moneybird_contact_id`       | Enige beschikbare identifier voor betalingsgegevens                   |
| `librechat_user_id`          | Enige beschikbare identifier voor chatgeschiedenis                    |

### Bewust uitgesloten velden

| Veld                        | Reden                                                                  |
|-----------------------------|------------------------------------------------------------------------|
| `audit_log.details`         | JSON-blob die organisatiebrede data kan bevatten (bijv. namen van andere gebruikers, systeemconfiguratie). Alleen `action`, `resource_type`, `resource_id` en `created_at` worden geexporteerd. |
| `product_events.properties` | JSON-blob die organisatiebrede sessie-data kan bevatten. Alleen `event_type` en `created_at` worden geexporteerd. |

### Externe systemen zonder directe export

| Systeem     | Status                                                                   |
|-------------|--------------------------------------------------------------------------|
| Moneybird   | Alleen org-level `contact_id` in portal DB; volledige export via privacy@getklai.com |
| LibreChat   | Chatgeschiedenis zit in MongoDB per-tenant; niet toegankelijk via portal DB; verwijzing naar beheerder of privacy@getklai.com |
| Twenty CRM  | Geen portal-Twenty user mapping bestaat; verwijzing naar privacy@getklai.com |

---

## Risico's en mitigaties

| Risico                                        | Impact  | Mitigatie                                                    |
|-----------------------------------------------|---------|--------------------------------------------------------------|
| Zitadel downtime tijdens SAR-request          | Laag    | Graceful degradation: identity-velden worden `null`, export gaat door |
| Grote datasets (veel meetings met transcripten)| Medium  | Geen paginering geimplementeerd; monitor response-grootte in productie |
| Ongeautoriseerde toegang tot SAR-endpoint     | Hoog    | Zelfde bearer-auth als `/api/me`; geen admin-override mogelijk |
| Stale portal DB-data vs. Zitadel              | Laag    | Identity wordt live uit Zitadel opgehaald; account-data uit portal DB is authoritative voor portal-specifieke velden |

---

## Openstaande kwaliteitsgates

### Testdekking

De huidige tests dekken:
- [x] Happy path: alle top-level keys aanwezig
- [x] MFA-status in identity-sectie
- [x] Moneybird contact_id in external_systems

Nog te verbeteren:
- [ ] **Error path: gebruiker niet gevonden** -- test dat een gebruiker zonder `portal_users` record HTTP 404 krijgt
- [ ] **Zitadel graceful degradation** -- test dat de export slaagt wanneer `get_user_by_id` en/of `has_any_mfa` een exception raisen
- [ ] **Lege collecties** -- test dat lege arrays (`[]`) worden geretourneerd (niet `null`) voor gebruikers zonder meetings, groepen of KB-toegang
- [ ] **Coverage-meting** -- verifieer dat `test_sar_export.py` minimaal de error-paden en degradation-paden dekt

### Code-kwaliteit

- [ ] **Ruff compliance** -- `uv run ruff check app/api/me.py`
- [ ] **Pyright compliance** -- `uv run pyright app/api/me.py`
- [ ] **MX tags** -- `sar_export` is een nieuw publiek endpoint; overweeg `@MX:ANCHOR` of `@MX:NOTE` tag

### Frontend

- [x] TypeScript compilatie -- `npm run i18n:compile && tsc --noEmit` (bevestigd passing)
- [x] i18n-keys aanwezig in zowel `nl.json` als `en.json`
- [x] Gebruik van `useMutation` (TanStack Query) voor download-flow
- [x] Foutmelding via semantic color token (`--color-destructive`)

---

## Toekomstige uitbreidingen

| Uitbreiding                          | Prioriteit | Omschrijving                                                   |
|--------------------------------------|------------|----------------------------------------------------------------|
| Twenty CRM user mapping              | Laag       | Zodra portal-Twenty koppeling bestaat, user-ID opnemen in SAR  |
| LibreChat MongoDB export             | Medium     | Chatgeschiedenis uit MongoDB per-tenant exporteren als onderdeel van SAR |
| PDF/CSV exportformaten               | Laag       | Naast JSON ook PDF of CSV aanbieden voor minder technische gebruikers |
| Rate limiting                        | Laag       | Voorkomen van misbruik van het SAR-endpoint                     |
| Admin-geinitieerde SAR               | Laag       | Beheerder kan SAR uitvoeren namens een gebruiker (met audit trail) |
| Async export met notificatie         | Medium     | Voor grote datasets: export op achtergrond + e-mailnotificatie  |

---

## Traceability

| TAG            | Referentie                             |
|----------------|----------------------------------------|
| SPEC-GDPR-001 | `spec.md` -- requirements              |
| SPEC-GDPR-001 | `acceptance.md` -- acceptatiecriteria  |
