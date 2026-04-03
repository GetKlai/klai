# Implementation Plan: SPEC-KB-018

## Task Decomposition

### Task 1: Alembic Migration — `default_org_role` kolom
**Files:** `klai-portal/backend/alembic/versions/` (new migration file)
**Effort:** Small

- Voeg `default_org_role` kolom toe aan `portal_knowledge_bases`
- Type: `Text`, nullable, server_default `"viewer"`
- Bestaande rijen krijgen `"viewer"` als default
- RLS policy hoeft niet aangepast (kolom is op dezelfde tabel)

### Task 2: Model Update — `PortalKnowledgeBase`
**Files:** `klai-portal/backend/app/models/knowledge_bases.py`
**Effort:** Small

- Voeg `default_org_role: Mapped[str | None]` toe
- Server default: `"viewer"`

### Task 3: Access Service — Fallback Logic
**Files:** `klai-portal/backend/app/services/access.py`
**Effort:** Medium

- `get_user_role_for_kb()`: als geen expliciete access rij gevonden EN user.org_id == kb.org_id EN kb.default_org_role IS NOT NULL → return `kb.default_org_role`
- `get_accessible_kb_slugs()`: include KBs waar `default_org_role IS NOT NULL` voor org-leden
- Bewaar highest-wins logica: expliciete rij > default_org_role
- Personal KBs (owner_type="user") skippen default_org_role check

### Task 4: API Extension — Create & Update Endpoints
**Files:** `klai-portal/backend/app/api/app_knowledge_bases.py`
**Effort:** Medium

- Extend `AppKBCreateRequest`:
  - `default_org_role: str | None = "viewer"`
  - `initial_members: list[InitialMember] | None = None` where `InitialMember = { type: "user"|"group", id: str|int, role: str }`
- Create endpoint: verwerk `initial_members` in transactie (bulk-insert `PortalUserKBAccess` / `PortalGroupKBAccess`)
- PATCH endpoint: sta `default_org_role` wijziging toe (owner-only)
- Validatie: `default_org_role` moet `"viewer"`, `"contributor"`, of `None` zijn

### Task 5: Frontend — Visibility Cards
**Files:** `klai-portal/frontend/src/routes/app/knowledge/new.tsx`
**Effort:** Medium

- Vervang huidige visibility dropdown door drie cards:
  - Publiek (Globe icon, beschrijving)
  - Organisatie (Users icon, beschrijving)
  - Beperkt (Lock icon, beschrijving)
- Cards met selected state (border accent)
- Bij selectie "Beperkt": toon member picker (Task 7)
- Bij selectie "Organisatie" of "Publiek": toon contributor toggle (Task 6)

### Task 6: Frontend — Contributor Toggle
**Files:** `klai-portal/frontend/src/routes/app/knowledge/new.tsx`
**Effort:** Small

- Switch component onder Organisatie/Publiek card:
  > "Mogen teamleden ook content toevoegen?"
- Ja → `default_org_role = "contributor"`
- Nee → `default_org_role = "viewer"` (default)
- Subtekst met uitleg per optie

### Task 7: Frontend — Member Picker (Beperkt)
**Files:** `klai-portal/frontend/src/routes/app/knowledge/new.tsx` (or new component)
**Effort:** Large

- Groep-zoeker: autocomplete tegen `GET /api/app/groups` (of bestaand endpoint)
- Persoon-zoeker: autocomplete tegen org users endpoint
- Per toevoeging: rol-selector (viewer/contributor)
- Lijst van toegevoegde members met remove optie
- Minimaal 1 member vereist voor submit
- State management: array van `{ type, id, name, role }` in form state

### Task 8: Frontend — Samenvatting Card
**Files:** `klai-portal/frontend/src/routes/app/knowledge/new.tsx`
**Effort:** Small

- Toon voor submit:
  - KB naam + slug
  - Visibility card (compact) met icoon
  - Default org-rol tekst
  - Lijst van extra members (als beperkt)
  - Docs vermelding
- "Aanmaken" button (primary)

### Task 9: Frontend — Members Tab Upgrade
**Files:** `klai-portal/frontend/src/routes/app/knowledge/$kbSlug/members.tsx`
**Effort:** Medium

- Bovenaan: "Standaard voor de organisatie: [rol]" met edit toggle (owner-only)
- Edit: PATCH `/api/app/knowledge-bases/{kbSlug}` met `default_org_role`
- Filter weergave: toon alleen members met rol > default
- "Via" kolom: "Individueel" of "Groep: [naam]"
- Bij beperkt (default_org_role=NULL): toon alle explicit members zonder filter

### Task 10: i18n — EN + NL keys
**Files:** `klai-portal/frontend/messages/en.json`, `klai-portal/frontend/messages/nl.json`
**Effort:** Small

Nieuwe keys namespace `knowledge_sharing_*`:
- Visibility card labels, descriptions
- Contributor toggle labels
- Member picker labels
- Samenvatting teksten
- Members tab default rol teksten

---

## Implementation Order

```
Task 1 (migration) → Task 2 (model)
    ↓
Task 3 (access service) → Task 4 (API)
    ↓
Task 5 (visibility cards) + Task 10 (i18n) — parallel
    ↓
Task 6 (toggle) + Task 7 (member picker) — parallel
    ↓
Task 8 (samenvatting)
    ↓
Task 9 (members tab upgrade)
```

Backend eerst (Tasks 1-4), dan frontend (Tasks 5-10).

---

## Technology Stack

- **Backend:** Python 3.12, FastAPI, SQLAlchemy async, Alembic
- **Frontend:** React 19, TypeScript, TanStack Router, TanStack Query, Mantine 8 (of bestaande UI components), Tailwind 4
- **i18n:** Paraglide JS
- **Geen nieuwe dependencies nodig**

---

## Risk Mitigation

| Risk | Strategy |
|------|----------|
| Access service regression | Characterization tests voor bestaande `get_user_role_for_kb()` behavior |
| Migration backward compat | `default_org_role = "viewer"` voor bestaande rijen = ongewijzigd gedrag |
| Frontend complexity | Visibility cards zijn een verbetering, niet een complete rewrite van new.tsx |
| Autocomplete performance | Bestaande org-users en groups endpoints, geen nieuwe queries |
