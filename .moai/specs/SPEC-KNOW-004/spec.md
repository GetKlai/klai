---
id: SPEC-KNOW-004
title: "Knowledge Management - Save Confirmation & Personal Items Browser"
version: 1.0.0
status: completed
created: 2026-03-26
updated: 2026-03-26
author: MoAI
priority: medium
issue_number: 0
tags: [knowledge, mcp, portal, personal-knowledge]
modules: [M1-save-confirmation, M2-list-endpoint, M3-delete-endpoint, M4-portal-backend, M5-portal-frontend]
---

# SPEC-KNOW-004: Knowledge Management - Save Confirmation & Personal Items Browser

## Environment

- **Platform**: Klai AI-platform (self-hosted, EU-only)
- **Backend**: FastAPI (portal-backend), Knowledge-ingest service (standalone)
- **Frontend**: React 19 + TanStack Router + TanStack Table + Radix UI + Paraglide i18n
- **Database**: PostgreSQL (`knowledge.artifacts` tabel), Qdrant (vectorstore)
- **Auth**: Zitadel OIDC via portal-backend, `X-Internal-Secret` voor service-to-service
- **MCP**: `klai-knowledge-mcp` tool + `agent-system-prompt.md`
- **Bestaande componenten**:
  - `DeleteConfirmButton` (`klai-portal/frontend/src/components/ui/delete-confirm-button.tsx`)
  - AlertDialog-patroon (`klai-portal/frontend/src/routes/admin/users/index.tsx`)
  - Knowledge stats endpoint (`GET /api/knowledge/stats`)
  - Bestaande knowledge pagina (`klai-portal/frontend/src/routes/app/knowledge/index.tsx`)

## Assumptions

- A1: De `knowledge.artifacts` tabel bevat een `belief_time_end` sentinel waarde van `253402300800` voor actieve items.
- A2: `pg_store.soft_delete_artifact(org_id, kb_slug, path)` en `qdrant_store.delete_document(org_id, kb_slug, path)` bestaan en werken correct (momenteel alleen getriggerd via Gitea webhooks).
- A3: Persoonlijke kennisitems worden opgeslagen met `kb_slug='personal'`.
- A4: Tags worden opgeslagen in de Qdrant payload, niet in PostgreSQL. De list endpoint kan `tags=[]` retourneren in v1.
- A5: De assertion_mode waarden die de MCP tool accepteert zijn `fact`, `claim`, `note` - niet de waarden in de database schema (`factual`, `procedural`, `quoted`, `belief`, `hypothesis`).
- A6: Het portal-backend gebruikt `httpx.AsyncClient` voor service-to-service calls naar knowledge-ingest.
- A7: Gebruikers mogen alleen hun eigen persoonlijke kennisitems zien en verwijderen (user_id scoping).

## Requirements

### Module M1: Save Confirmation in Chat

**WHEN** de AI bepaalt dat een bericht kenniswaardige informatie bevat (een beslissing, inzicht, procedure of feit dat de gebruiker later terugwil) **THEN** vraagt de AI eenmalig: "Wil je dat ik dit opsla?" voordat de tool wordt aangeroepen. [REQ-M1-001]

Het systeem **shall niet** bij elke informatieve vraag een savevoorstel doen. Alleen bij informatie met duidelijke persoonlijke of organisatorische waarde. [REQ-M1-001b]

Het systeem **shall** de assertion_mode waarden in `agent-system-prompt.md` uitlijnen met wat de tool accepteert: `fact`, `claim`, `note`. [REQ-M1-002]

Het systeem **shall niet** kennisitems opslaan zonder voorafgaande expliciete bevestiging van de gebruiker. [REQ-M1-003]

**Scope**: Alleen een wijziging in `klai-mono/deploy/klai-knowledge-mcp/agent-system-prompt.md`. Geen API-werk.

**Opmerking over assertion_mode inconsistentie**: De database schema gebruikt `factual/procedural/quoted/belief/hypothesis`, maar de tool accepteert alleen `fact/claim/note`. Deze SPEC fixt alleen de system prompt om overeen te komen met de tool. Het schema wordt NIET aangepast.

### Module M2: Personal Knowledge List Endpoint

**WHEN** het portal-backend een `GET /knowledge/v1/personal/items` request stuurt met geldige `X-Internal-Secret`, `org_id` en `user_id` **THEN** retourneert de knowledge-ingest service een gepagineerde lijst van actieve persoonlijke kennisitems. [REQ-M2-001]

**IF** de query parameters `org_id` of `user_id` ontbreken **THEN** retourneert het systeem een `400 Bad Request`. [REQ-M2-002]

Het systeem **shall** alleen items retourneren waar `kb_slug='personal'` AND `belief_time_end=253402300800` (actief). [REQ-M2-003]

Het systeem **shall** paginering ondersteunen via `limit` (standaard 50, max 200) en `offset` (standaard 0) query parameters. [REQ-M2-004]

Het systeem **shall** per item de volgende velden retourneren: `id` (UUID), `path` (= titel), `assertion_mode`, `tags` (lijst, mag leeg zijn in v1), `created_at`. [REQ-M2-005]

**Implementatie**:
- Nieuw endpoint: `GET /knowledge/v1/personal/items`
- Auth: `X-Internal-Secret` header (service-to-service)
- Nieuwe `pg_store` functie: `list_personal_artifacts(org_id, user_id, limit, offset) -> list[ArtifactSummary]`
- Tags komen uit Qdrant payload; in v1 mag `tags=[]` geretourneerd worden

### Module M3: Personal Knowledge Delete Endpoint

**WHEN** het portal-backend een `DELETE /knowledge/v1/personal/items/{artifact_id}` request stuurt met geldige `X-Internal-Secret`, `org_id` en `user_id` **THEN** soft-deletet het systeem het artifact en verwijdert de bijbehorende vector. [REQ-M3-001]

**IF** het artifact niet bestaat, niet van de opgegeven gebruiker is, of al verwijderd is **THEN** retourneert het systeem een `404 Not Found`. [REQ-M3-002]

Het systeem **shall** de volgende stappen uitvoeren bij verwijdering:
1. Lookup artifact op `id` AND `org_id` AND `user_id` AND `kb_slug='personal'` AND `belief_time_end=253402300800`
2. `pg_store.soft_delete_artifact(org_id, 'personal', path)` aanroepen — zet `belief_time_end` op nu (**soft-delete**: item blijft bewaard in PostgreSQL voor auditdoeleinden, maar is functioneel inactief)
3. `qdrant_store.delete_document(org_id, 'personal', path)` aanroepen — verwijdert vectorchunks permanent (**hard-delete**: zodat de AI het item niet meer kan ophalen bij toekomstige zoekopdrachten)
4. `{"status": "ok"}` retourneren
[REQ-M3-003]

**Ontwerpkeuze**: De combinatie soft-delete (PostgreSQL) + hard-delete (Qdrant) is bewust. PostgreSQL bewaart een audittrail van wat ooit opgeslagen was en wanneer het verwijderd werd. Qdrant verwijdert direct zodat het item niet meer zichtbaar is voor de AI.

Het systeem **shall niet** items van andere gebruikers verwijderen, zelfs niet met een geldig `artifact_id`. [REQ-M3-004]

### Module M4: Portal Backend Proxy

**WHEN** een geauthenticeerde gebruiker een `GET /api/knowledge/personal/items` request stuurt **THEN** valideert het portal-backend de OIDC token, extraheert `zitadel_user_id` en `org.klai_org_id`, en proxiet naar knowledge-ingest. [REQ-M4-001]

**WHEN** een geauthenticeerde gebruiker een `DELETE /api/knowledge/personal/items/{artifact_id}` request stuurt **THEN** valideert het portal-backend de OIDC token en proxiet naar knowledge-ingest delete endpoint. [REQ-M4-002]

Het systeem **shall** het bestaande `_get_caller_org()` patroon en `bearer` dependency gebruiken voor authenticatie. [REQ-M4-003]

Het systeem **shall niet** een admin-rol vereisen - gebruikers kunnen alleen hun eigen items verwijderen (user_id scoping dwingt dit af). [REQ-M4-004]

Het systeem **shall** `httpx.AsyncClient` gebruiken voor service-to-service calls naar knowledge-ingest. [REQ-M4-005]

**Locatie**: `klai-portal/backend/app/api/knowledge.py` (bestaand bestand, nieuwe routes toevoegen)

### Module M5: Portal Frontend

**WHEN** een gebruiker de personal knowledge base pagina bezoekt (`/app/knowledge/personal`) **THEN** toont het systeem een extra tab "Items" naast de bestaande tabs Overview, Connectors en Members. [REQ-M5-001]

Het systeem **shall** de "Items" tab alleen tonen als `isPersonal === true` (reeds beschikbaar als `kb?.owner_type === 'user'`). [REQ-M5-002]

De "Items" tab **shall** de volgende kolommen tonen: Titel (`path`), Type (`assertion_mode` badge), Opgeslagen op (`created_at` geformatteerd), Acties (verwijderknop). [REQ-M5-003]

**WHEN** een gebruiker op de verwijderknop klikt **THEN** toont het systeem een bevestigingsdialoog (AlertDialog, conform bestaand patroon in dit bestand) voordat het item wordt verwijderd. [REQ-M5-004]

**WHEN** een item succesvol is verwijderd **THEN** ververst het systeem de lijst automatisch via `queryClient.invalidateQueries`. [REQ-M5-005]

**IF** er geen persoonlijke kennisitems zijn **THEN** toont het systeem een vriendelijke lege-staat melding. [REQ-M5-006]

Het systeem **shall** React Query gebruiken voor ophalen en `useMutation` voor verwijderen, conform het patroon in ConnectorsSection. [REQ-M5-007]

Het systeem **shall** i18n ondersteunen via Paraglide met nieuwe message keys (Nederlands eerst, Engels tweede). [REQ-M5-008]

**Locatie**: Nieuw `ItemsSection` component in `klai-portal/frontend/src/routes/app/knowledge/$kbSlug.tsx`. Geen nieuwe route nodig — de personal KB is al bereikbaar op `/app/knowledge/personal` via de bestaande `$kbSlug` route. De tab wordt toegevoegd aan het bestaande tab-systeem op regels 940-961.

## Specifications

### Data Model

```
knowledge.artifacts (bestaand):
  id          UUID PRIMARY KEY
  org_id      TEXT NOT NULL
  user_id     TEXT NOT NULL
  kb_slug     TEXT NOT NULL
  path        TEXT NOT NULL
  assertion_mode TEXT
  belief_time_start BIGINT
  belief_time_end   BIGINT  -- 253402300800 = actief
  created_at  TIMESTAMPTZ
```

### API Contracts

**Knowledge-Ingest (nieuw)**:

```
GET /knowledge/v1/personal/items
  Headers: X-Internal-Secret
  Query: org_id, user_id, limit (default 50, max 200), offset (default 0)
  Response 200:
    {
      "items": [
        {
          "id": "uuid",
          "path": "string",
          "assertion_mode": "fact|claim|note",
          "tags": [],
          "created_at": "iso8601"
        }
      ],
      "total": 42,
      "limit": 50,
      "offset": 0
    }

DELETE /knowledge/v1/personal/items/{artifact_id}
  Headers: X-Internal-Secret
  Query: org_id, user_id
  Response 200: {"status": "ok"}
  Response 404: {"detail": "Artifact not found"}
```

**Portal Backend (nieuw)**:

```
GET /api/knowledge/personal/items
  Headers: Authorization: Bearer <token>
  Response: proxy van knowledge-ingest response

DELETE /api/knowledge/personal/items/{artifact_id}
  Headers: Authorization: Bearer <token>
  Response: proxy van knowledge-ingest response
```

## Traceability

| Requirement | Module | Bestanden |
|---|---|---|
| REQ-M1-001..003 | M1 | `deploy/klai-knowledge-mcp/agent-system-prompt.md` |
| REQ-M2-001..005 | M2 | `knowledge-ingest: routes, pg_store` |
| REQ-M3-001..004 | M3 | `knowledge-ingest: routes, pg_store, qdrant_store` |
| REQ-M4-001..005 | M4 | `klai-portal/backend/app/api/knowledge.py` |
| REQ-M5-001..007 | M5 | `klai-portal/frontend/src/routes/app/knowledge/` |
