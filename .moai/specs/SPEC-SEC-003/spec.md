# SPEC-SEC-003: Volledige RLS Coverage voor Multi-Tenant Isolatie

## Metadata

| Field    | Value                          |
|----------|--------------------------------|
| SPEC-ID  | SPEC-SEC-003                   |
| Created  | 2026-04-03                     |
| Status   | Draft                          |
| Priority | High                           |
| Domain   | Security / Database            |
| Author   | Claude + Mark                  |
| Depends  | SPEC-SEC-001 (eerste RLS-ronde)|

## Summary

Voeg PostgreSQL Row-Level Security (RLS) toe aan alle resterende tabellen met `org_id` die momenteel onbeschermd zijn. SPEC-SEC-001 dekte 6 tabellen; er zijn nog 10 tabellen zonder RLS. Dit is defense-in-depth: de applicatielaag heeft `_get_*_or_404` helpers, maar een gemiste org_id-filter betekent cross-tenant datalek zonder RLS.

## Background / Motivation

Na SPEC-SEC-001 hebben de volgende tabellen RLS:
- `portal_groups` (org_id direct)
- `portal_knowledge_bases` (org_id direct)
- `portal_group_products` (org_id direct)
- `portal_group_memberships` (via parent portal_groups)
- `portal_group_kb_access` (via parent portal_knowledge_bases)
- `portal_audit_log` (split SELECT/INSERT policies)

De volgende 10 tabellen met `org_id` hebben GEEN RLS:

| Tabel | org_id type | Risico zonder RLS |
|-------|-------------|-------------------|
| `portal_users` | direct FK | Userlijst cross-tenant lekbaar |
| `portal_user_kb_access` | direct FK | KB-toegangsregels cross-tenant lekbaar |
| `portal_user_products` | direct FK | Productentitlements cross-tenant lekbaar |
| `portal_connectors` | direct FK | Connector-configuratie cross-tenant lekbaar |
| `portal_retrieval_gaps` | direct FK | Zoek-analytics cross-tenant lekbaar |
| `portal_kb_tombstones` | direct FK | Verwijderde KB's cross-tenant lekbaar |
| `product_events` | direct FK (nullable) | Gebruiksevents cross-tenant lekbaar |
| `vexa_meetings` | direct FK (nullable) | Meeting-transcripties cross-tenant lekbaar |
| `portal_taxonomy_nodes` | indirect (via kb_id) | Taxonomie cross-tenant lekbaar |
| `portal_taxonomy_proposals` | indirect (via kb_id) | Taxonomie-proposals cross-tenant lekbaar |

`portal_orgs` is bewust uitgesloten — het is de root-tabel en heeft geen org_id op zichzelf.

## Requirements (EARS Format)

### REQ-1: Veilige tabellen — directe RLS (Fase 1)

**When** een authenticated request een query uitvoert op een van de volgende tabellen, **the system shall** alleen rijen retourneren waar `org_id` (of de parent's `org_id`) overeenkomt met `app.current_org_id`:

- `portal_kb_tombstones` (org_id direct)
- `portal_user_kb_access` (org_id direct)
- `portal_retrieval_gaps` (org_id direct)
- `portal_taxonomy_nodes` (via kb_id → portal_knowledge_bases.org_id)
- `portal_taxonomy_proposals` (via kb_id → portal_knowledge_bases.org_id)

### REQ-2: Tabellen met background tasks — split policies (Fase 2a)

**When** een background task of fire-and-forget schrijver een INSERT uitvoert op een van de volgende tabellen zonder tenant context, **the system shall** de INSERT toestaan, maar SELECT beperken tot de tenant's eigen rijen:

- `product_events` — `emit_event()` gebruikt independent session zonder `set_tenant()`
- `vexa_meetings` — background poller/cleanup gebruikt `AsyncSessionLocal()` zonder `set_tenant()`

Policy-patroon (zoals audit_log):
- `FOR SELECT USING (org_id = app.current_org_id)`
- `FOR INSERT WITH CHECK (true)`
- `FOR UPDATE USING (org_id = app.current_org_id OR NULLIF(current_setting('app.current_org_id', true), '') IS NULL)`

vexa_meetings heeft ook UPDATE nodig vanuit background tasks (status-transities).

### REQ-3: Tabellen met interne endpoints — set_tenant per endpoint (Fase 2b)

**When** een intern endpoint (`X-Internal-Secret` auth) een query uitvoert op `portal_users` of `portal_user_products`, **the system shall** `set_tenant()` aanroepen met de org_id van de opgehaalde resource voordat verdere queries worden uitgevoerd.

Betreffende endpoints in `app/api/internal.py`:
- `/api/internal/user-language` — lookup user, dan `set_tenant(db, user.org_id)`
- `/api/internal/users/{zitadel_user_id}/products` — lookup user, dan `set_tenant(db, user.org_id)`
- `/api/internal/v1/users/{librechat_user_id}/feature/knowledge` — lookup user, dan `set_tenant(db, user.org_id)`

Na deze code-fix kunnen standaard RLS-policies worden toegepast:
- `portal_users` — `USING (org_id = app.current_org_id)`
- `portal_user_products` — `USING (org_id = app.current_org_id)`

### REQ-4: Connectors — fix set_tenant + RLS (Fase 2b)

**When** het interne endpoint `/api/internal/connectors/{connector_id}/sync-status` een connector ophaalt, **the system shall** `set_tenant(db, connector.org_id)` aanroepen voordat verdere queries worden uitgevoerd.

Na deze code-fix kan standaard RLS worden toegepast:
- `portal_connectors` — `USING (org_id = app.current_org_id)`

Het endpoint `/api/internal/connectors/{connector_id}` roept al `set_tenant()` aan (regel 121) en is veilig.

### REQ-5: Documentatie-update

**When** de RLS-migraties zijn toegepast, **the system shall** de volgende documentatie bijwerken:
- Serena `domain-model` memory — RLS-sectie toevoegen
- `.claude/rules/klai/pitfalls/security.md` — RLS coverage-tabel toevoegen

## Constraints

- C-1: Geen downtime — migraties moeten online toepasbaar zijn (`ALTER TABLE ... ENABLE ROW LEVEL SECURITY` is non-blocking)
- C-2: Bestaande `set_tenant()` middleware mag niet gewijzigd worden — alleen interne endpoints worden aangepast
- C-3: De `portal_audit_log` split-policy pattern (SPEC-SEC-001) is het bewezen patroon voor INSERT-permissive policies
- C-4: Alle RLS-policies gebruiken `FORCE ROW LEVEL SECURITY` zodat ook de table owner gebonden is
- C-5: Background tasks die UPDATE nodig hebben (vexa_meetings) moeten een UPDATE-policy krijgen die werkt zonder tenant context

## Risks

| Risk | Impact | Mitigatie |
|------|--------|-----------|
| Internal endpoint mist `set_tenant()` → query retourneert 0 rijen | Auth/feature breuk voor LibreChat users | REQ-3 fixt alle bekende interne endpoints; integration test per endpoint |
| Background task INSERT faalt door RLS | Events/meetings niet opgeslagen | Split INSERT/SELECT policy (bewezen patroon audit_log) |
| Subquery in RLS-policy op taxonomy-tabellen is traag | Latency op taxonomy-endpoints | Subquery gaat via indexed FK; monitor na deploy |

## Out of Scope

- RLS op `portal_orgs` (root-tabel, geen org_id)
- Wijzigingen aan de `set_tenant()` middleware
- RLS op klai-docs tabellen (apart schema, eigen SPEC)
- Performance-optimalisatie van bestaande RLS-policies
