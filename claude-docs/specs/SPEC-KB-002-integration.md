# SPEC-KB-002: Knowledge Base Integration

> Status: DRAFT — 2026-03-25
> Author: Mark Vletter (design) + Claude (SPEC)
> Builds on: `claude-docs/specs/SPEC-KB-001-unification.md`

---

## Context en probleem

SPEC-KB-001 heeft de datastructuur unified (één `portal_knowledge_bases` tabel, visibility, role). Maar de verbindingen tussen de systemen zijn nog niet gelegd:

1. **KB aanmaken provisioneert Gitea niet** — `gitea_repo_slug` blijft null na aanmaken
2. **`/app/docs` en `/app/knowledge` zijn ontkoppeld** — Docs maakt KBs direct aan in Gitea zonder de portal te betrekken
3. **Connector heeft eigen database** — niet multi-tenant-veilig, geen echte FK naar een KB, geen toegangscontrole
4. **Qdrant visibility wordt niet gehandhaafd** — `public`/`internal` op de KB heeft geen effect bij retrieval
5. **MCP schrijft naar hardcoded slugs** — `"personal"` en `"org"`, niet naar named KBs; gebruiker kan per ongeluk in de verkeerde KB schrijven

---

## Design Decisions

### D1: Aanmaken loopt altijd via de portal

De portal is het enige aanmaakpunt voor een KB. Bij aanmaken:
1. Portal slaat de KB op in `portal_knowledge_bases`
2. Portal roept klai-docs API aan om de Gitea-repo te provisioneren
3. Portal slaat de teruggekregen `gitea_repo_slug` op

`/app/docs/new` en de bijbehorende "nieuwe KB" knop in `/app/docs` verdwijnen. Aanmaken gaat altijd via `/app/knowledge/new`.

### D2: Connector verhuist naar de portal

De `klai-connector` service behoudt de sync-logica (GitHub adapter, scheduler, sync engine) maar heeft geen eigen database meer. De connector-configuratie verhuist naar de portal database als nieuwe tabel `portal_connectors` met een echte FK naar `portal_knowledge_bases`.

De klai-connector service leest connector-configuratie via de portal API in plaats van zijn eigen DB. Zo loopt toegangscontrole automatisch via de bestaande portal auth en multi-tenant structuur.

**Waarom geen volledige merge in de portal backend:** de sync-engine heeft zware afhankelijkheden (GitHub SDK, scheduler, long-running jobs) die niet thuishoren in de portal API. De scheiding blijft: portal = config en auth, klai-connector = sync worker.

### D3: Qdrant visibility filtering

Bij het indexeren wordt elk Qdrant-punt getagd met `visibility: public` of `visibility: internal`, overeenkomend met de KB-instelling.

Bij retrieval filtert de knowledge-ingest/retrieval service op basis van de auth-context van de aanvrager:
- Niet-geauthenticeerd: alleen `public` punten
- Geauthenticeerd org-lid: `public` + `internal` punten van KBs waartoe de gebruiker toegang heeft

### D4: MCP write_to_kb vraagt expliciet om KB

De bestaande `save_org_knowledge` tool wordt vervangen door `write_to_kb`. Deze tool:
1. Haalt de KBs op waar de gebruiker Contributor-toegang toe heeft (via portal API)
2. Vraagt de gebruiker expliciet te kiezen
3. Schrijft naar de gekozen KB met de juiste visibility

Dit voorkomt dat een gebruiker per ongeluk inhoud in een publieke KB schrijft terwijl ze iets privé bedoelden.

---

## Acceptance Criteria

### AC-1: KB aanmaken provisioneert Gitea

**WHEN** een gebruiker een nieuwe KB aanmaakt via `/app/knowledge/new`,
**THEN** roept de portal backend de klai-docs API aan om een Gitea-repo te provisioneren,
**AND** wordt `gitea_repo_slug` gevuld op de `portal_knowledge_bases` rij,
**AND** is de KB daarna zichtbaar in `/app/docs` (als tab/entry met de Gitea-editor).

**WHEN** de Gitea-provisioning mislukt,
**THEN** wordt de portal KB-rij teruggedraaid (rollback),
**AND** krijgt de gebruiker een foutmelding.

### AC-2: /app/docs heeft geen eigen aanmaakformulier meer

**WHEN** een gebruiker naar `/app/docs` navigeert,
**THEN** ziet hij geen "Nieuwe KB" knop meer,
**AND** bestaat de route `/app/docs/new` niet meer.

**WHEN** een gebruiker een nieuwe docs-KB wil aanmaken,
**THEN** doet hij dat via `/app/knowledge/new`.

### AC-3: /app/docs toont alleen KBs die via de portal bestaan

**WHEN** een gebruiker naar `/app/docs` navigeert,
**THEN** worden alleen KBs getoond die een corresponderende `portal_knowledge_bases` rij hebben,
**AND** heeft elke rij een gevulde `gitea_repo_slug`.

### AC-4: Connector is child van een KB

**WHEN** een connector wordt aangemaakt,
**THEN** is er een verplichte FK naar `portal_knowledge_bases.id`,
**AND** valideert de portal dat de aanmaker Contributor-toegang heeft tot die KB,
**AND** wordt de connector opgeslagen in `portal_connectors` in de portal database.

**WHEN** een gebruiker connectors beheert via de Sources tab in `/app/knowledge/$kbSlug`,
**THEN** ziet hij alleen connectors die aan die specifieke KB zijn gekoppeld,
**AND** kan hij alleen connectors aanmaken voor KBs waar hij Contributor-toegang toe heeft.

### AC-5: klai-connector leest configuratie via portal

**WHEN** de sync engine een connector job uitvoert,
**THEN** haalt hij de connector-configuratie op via de portal API (niet zijn eigen DB),
**AND** valideert de portal dat de connector bij de juiste org en KB hoort.

### AC-6: Qdrant punten krijgen visibility tag

**WHEN** een document geïndexeerd wordt in Qdrant,
**THEN** wordt het punt getagd met `visibility: public` of `visibility: internal` overeenkomend met de KB,
**AND** wordt bij een visibilitywijziging op de KB alle bijbehorende punten geherindexeerd.

### AC-7: Retrieval filtert op visibility

**WHEN** een niet-geauthenticeerde aanvrager een query doet,
**THEN** worden alleen punten met `visibility: public` teruggegeven.

**WHEN** een geauthenticeerd org-lid een query doet,
**THEN** worden punten met `visibility: public` én `visibility: internal` teruggegeven voor KBs waartoe het lid toegang heeft,
**AND** worden punten van KBs waartoe het lid geen toegang heeft nooit teruggegeven, ongeacht visibility.

### AC-8: MCP write_to_kb vervangt save_org_knowledge

**WHEN** een gebruiker in chat inhoud wil opslaan voor het team,
**THEN** vraagt de MCP welke KB de gebruiker bedoelt (lijst van KBs met Contributor-toegang),
**AND** bevestigt de MCP de visibility van de gekozen KB vóór het opslaan,
**AND** schrijft de MCP naar de gekozen KB.

**WHEN** een gebruiker slechts toegang heeft tot één KB,
**THEN** selecteert de MCP die KB automatisch zonder te vragen.

---

## Data model changes

### portal_connectors (nieuw)

```sql
CREATE TABLE portal_connectors (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  kb_id       INTEGER NOT NULL REFERENCES portal_knowledge_bases(id) ON DELETE CASCADE,
  org_id      INTEGER NOT NULL REFERENCES portal_orgs(id) ON DELETE CASCADE,
  name        TEXT NOT NULL,
  connector_type TEXT NOT NULL,
  config      JSONB NOT NULL DEFAULT '{}',
  schedule    TEXT,
  is_enabled  BOOLEAN NOT NULL DEFAULT true,
  last_sync_at TIMESTAMPTZ,
  last_sync_status TEXT,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  created_by  TEXT NOT NULL
);
```

### klai-connector eigen database (verwijderd)

De `connectors` en `sync_runs` tabellen in de klai-connector database worden verwijderd na migratie.

---

## Wat buiten scope is

- Focus multi-KB read scope
- Toegangsbeheer UI voor KBs (wie mag wat — apart SPEC)
- Billing/limieten per org
- Connector types anders dan GitHub (Google Drive, Notion — staan al als "coming soon")
- Volledige re-index bij visibility-wijziging (kan asynchroon in een apart traject)

---

## Implementatiefasen

### Fase 1 — KB aanmaken provisioneert Gitea
- Portal backend roept klai-docs API aan bij `POST /api/app/knowledge-bases`
- Sla `gitea_repo_slug` op; rollback bij fout
- `/app/docs/new` verwijderen + knop weg uit `/app/docs`
- `/app/docs` lijst haalt KBs op via portal (gefilterd op `docs_enabled = true` en `gitea_repo_slug IS NOT NULL`)

### Fase 2 — Connector migratie naar portal
- Nieuwe tabel `portal_connectors` in portal DB (Alembic migratie)
- CRUD endpoints in portal backend (`/api/app/knowledge-bases/{kb_slug}/connectors`)
- Sources tab in `/app/knowledge/$kbSlug` gebruikt nieuwe portal endpoints
- klai-connector service: verwijder eigen DB, lees connector-config via portal API
- Verwijder oude `klai-connector` database tabellen

### Fase 3 — Qdrant visibility
- knowledge-ingest tagt punten met `visibility` bij indexeren
- Retrieval filtert op visibility + KB-toegang
- MCP en LibreChat-integratie meenemen in filterlogica

### Fase 4 — MCP write_to_kb
- Nieuwe tool `write_to_kb` in klai-knowledge-mcp
- Haalt toegankelijke KBs op via portal API
- Vraagt gebruiker te kiezen (of kiest automatisch bij één KB)
- Bevestigt visibility voor opslaan
- Verwijder `save_org_knowledge` (of laat als fallback naar `"org"` KB)
