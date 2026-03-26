---
spec_id: SPEC-KNOW-004
type: implementation-plan
version: 1.0.0
---

# Implementatieplan SPEC-KNOW-004

## Deployment Volgorde

De modules moeten in strikt sequentiele volgorde worden uitgerold. Elke module moet werkend zijn voordat de volgende wordt gestart.

### Primair doel: M1 - Save Confirmation (System Prompt)

**Scope**: Config-only wijziging, geen code.

**Taken**:
1. `klai-mono/deploy/klai-knowledge-mcp/agent-system-prompt.md` aanpassen:
   - Instructie toevoegen dat de AI altijd expliciet om bevestiging moet vragen voordat `save_personal_knowledge` wordt aangeroepen
   - assertion_mode waarden corrigeren van `procedural/factual/belief/hypothesis/quoted` naar `fact/claim/note` (overeenkomend met wat de tool accepteert)
2. Testen door een gesprek te voeren en te verifieren dat de AI bevestiging vraagt

**Afhankelijkheden**: Geen

**Referentie-implementatie**: Huidige `agent-system-prompt.md` in `klai-mono/deploy/klai-knowledge-mcp/`

### Primair doel: M2 - List Endpoint (Knowledge-Ingest)

**Scope**: Nieuw endpoint + pg_store functie in knowledge-ingest service.

**Taken**:
1. Nieuwe `pg_store` functie: `list_personal_artifacts(org_id, user_id, limit, offset)` implementeren
   - Query: `SELECT id, path, assertion_mode, created_at FROM knowledge.artifacts WHERE org_id=? AND user_id=? AND kb_slug='personal' AND belief_time_end=253402300800 ORDER BY created_at DESC LIMIT ? OFFSET ?`
   - Count query voor `total` veld
   - Return type: `list[ArtifactSummary]` dataclass/model
2. Nieuw route endpoint `GET /knowledge/v1/personal/items` toevoegen
   - `X-Internal-Secret` header validatie
   - Query parameter parsing met defaults en limits
   - Response model met `items`, `total`, `limit`, `offset`
3. Unit tests schrijven voor pg_store functie en endpoint

**Afhankelijkheden**: Geen (onafhankelijk van M1)

**Referentie-implementatie**: Bestaande `POST /ingest/v1/document` endpoint structuur in knowledge-ingest

### Primair doel: M3 - Delete Endpoint (Knowledge-Ingest)

**Scope**: Nieuw endpoint in knowledge-ingest service, hergebruikt bestaande soft-delete functies.

**Taken**:
1. Artifact lookup functie: `get_artifact_by_id(artifact_id, org_id, user_id)` toevoegen aan pg_store
   - Scoping op `kb_slug='personal'` en `belief_time_end=253402300800`
2. Nieuw route endpoint `DELETE /knowledge/v1/personal/items/{artifact_id}` toevoegen
   - `X-Internal-Secret` header validatie
   - Artifact lookup met ownership verificatie
   - `pg_store.soft_delete_artifact(org_id, 'personal', path)` aanroepen
   - `qdrant_store.delete_document(org_id, 'personal', path)` aanroepen
   - 404 als artifact niet gevonden
3. Unit tests schrijven

**Afhankelijkheden**: M2 moet werkend zijn (deelt route structuur en pg_store patronen)

**Referentie-implementatie**:
- Delete patroon: `portal/backend/app/api/admin.py` regels 420-451
- Bestaande soft-delete: `pg_store.soft_delete_artifact` en `qdrant_store.delete_document`

### Secundair doel: M4 - Portal Backend Proxy

**Scope**: Twee nieuwe routes in bestaand knowledge.py bestand.

**Taken**:
1. `GET /api/knowledge/personal/items` route toevoegen
   - OIDC token validatie via `bearer` dependency
   - `_get_caller_org()` voor org_id extractie
   - `zitadel_user_id` extractie uit token
   - `httpx.AsyncClient` call naar knowledge-ingest `GET /knowledge/v1/personal/items`
   - Response doorsturen
2. `DELETE /api/knowledge/personal/items/{artifact_id}` route toevoegen
   - Zelfde auth patroon
   - Proxy naar knowledge-ingest delete endpoint
3. Tests schrijven met gemockte knowledge-ingest responses

**Afhankelijkheden**: M2 en M3 moeten werkend en deployed zijn

**Referentie-implementatie**:
- Auth patroon: `portal/backend/app/api/dependencies.py` regels 41-68
- Bestaande knowledge routes: `portal/backend/app/api/knowledge.py`

### Secundair doel: M5 - Portal Frontend

**Scope**: Uitbreiding bestaande knowledge pagina met persoonlijke items tabel.

**Taken**:
1. API client functies toevoegen voor list en delete endpoints
2. Nieuwe component: `PersonalKnowledgeTable`
   - TanStack Table met kolommen: Titel, Type (badge), Opgeslagen op, Acties
   - `useQuery(['personal-knowledge'])` voor data ophalen
   - `useMutation` voor delete met `invalidateQueries` on success
   - `DeleteConfirmButton` voor verwijder-actie
   - Lege-staat component
3. Integratie in bestaande knowledge pagina (tab of sectie)
4. i18n message keys toevoegen via Paraglide (NL + EN)
5. Visuele test en responsive check

**Afhankelijkheden**: M4 moet werkend zijn

**Referentie-implementatie**:
- Tabel patroon: `portal/frontend/src/routes/admin/users/index.tsx`
- DeleteConfirmButton: `portal/frontend/src/components/ui/delete-confirm-button.tsx`
- i18n: `import * as m from '@/paraglide/messages'`

## Risico's en Mitigatie

### R1: Tags in Qdrant vs PostgreSQL

**Risico**: De list endpoint kan geen tags retourneren zonder extra Qdrant lookup, wat de latency verhoogt.

**Mitigatie**: In v1 retourneren we `tags=[]`. Tags worden in een latere iteratie toegevoegd, ofwel via Qdrant batch lookup, ofwel door tags te dupliceren naar PostgreSQL.

**Impact**: Laag - tags zijn informatief, niet functioneel voor de browse/delete use case.

### R2: LibreChat versie-constraints op system prompt formaat

**Risico**: Toekomstige LibreChat updates kunnen het system prompt formaat wijzigen of beperken.

**Mitigatie**: De system prompt wijziging (M1) is puur tekstueel en gebruikt geen LibreChat-specifieke syntax. Valideer na LibreChat updates.

**Impact**: Laag - system prompt is een eenvoudig markdown bestand.

### R3: assertion_mode inconsistentie

**Risico**: De database bevat waarden (`factual`, `procedural`, etc.) die niet overeenkomen met de tool waarden (`fact`, `claim`, `note`). De list endpoint toont database waarden.

**Mitigatie**: In de frontend een mapping toepassen voor weergave. Schema alignment is buiten scope van deze SPEC.

**Impact**: Medium - visueel verwarrend maar functioneel niet blokkerend.

### R4: Concurrent delete en list

**Risico**: Race condition als een gebruiker een item verwijdert terwijl de lijst wordt opgehaald.

**Mitigatie**: Optimistic UI update in frontend + `invalidateQueries` na delete. PostgreSQL transacties voorkomen data-inconsistentie.

**Impact**: Laag - standaard eventually-consistent patroon.

## Architectuur

```
Gebruiker (chat)
  |
  v
AI Agent (system prompt M1)
  |-- vraagt bevestiging -->  Gebruiker
  |-- save_personal_knowledge --> knowledge-ingest

Gebruiker (portal)
  |
  v
Portal Frontend (M5)
  |
  v
Portal Backend (M4)  -- httpx --> Knowledge-Ingest API (M2, M3)
  |                                    |
  v                                    v
Zitadel OIDC                    PostgreSQL + Qdrant
```
