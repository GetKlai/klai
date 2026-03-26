# SPEC-KB-009: Docs → Qdrant Sync

> Status: DRAFT (2026-03-26)
> Author: Mark Vletter (design) + Claude (SPEC)
> Builds on: SPEC-KB-006 (content-type adapters), SPEC-KB-007 (hybrid search)
> Architecture reference: `claude-docs/klai-knowledge-architecture.md`
> Created: 2026-03-26

---

## What exists today

### knowledge-ingest: webhook handler aanwezig maar dood

`deploy/knowledge-ingest/knowledge_ingest/routes/ingest.py` bevat een volledig geïmplementeerde Gitea push webhook handler op `POST /ingest/v1/webhook/gitea`. De handler:

- Verifieert de HMAC-SHA256 handtekening via `X-Gitea-Signature`
- Parseert org_slug en kb_slug uit de Gitea repo naam (`org-{slug}/{kb}`)
- Haalt `org_id` (Zitadel org ID) op uit het Gitea org description-veld
- Indexeert toegevoegde en gewijzigde `.md` bestanden via `ingest_document()`
- Verwijdert verwijderde bestanden uit Qdrant en soft-deletes het artifact in PostgreSQL
- Tests staan in `tests/test_webhook_hmac.py`

De handler is functioneel maar nooit bereikbaar: **er wordt nergens een Gitea webhook aangemaakt.** Wanneer een KB aangemaakt wordt in Docs, registreert `docs/lib/knowledge_ingest.ts` alleen KB-level events (delete, visibility). Pagina-level sync ontbreekt volledig.

### Docs: Gitea als waarheidsbron, geen sync-trigger

Pagina's worden opgeslagen in Gitea repos (één repo per org per KB). De Docs API schrijft via de Gitea API:

- `PUT /api/orgs/{org}/kbs/{kb}/pages/{...path}` → `gitea.putFile()`
- `DELETE /api/orgs/{org}/kbs/{kb}/pages/{...path}` → `gitea.deleteFile()`
- `POST /api/orgs/{org}/kbs/{kb}/page-rename/{...path}` → `gitea.putFile()` + `gitea.deleteFile()`

Elke Gitea API-schrijfactie creëert een commit en vuurt een push-event — wat betekent dat de bestaande webhook handler automatisch getriggerd wordt, zodra de webhook geregistreerd is.

### knowledge-ingest: content_type voor KB-artikelen

De webhook handler maakt een `IngestRequest` aan met `source_type="docs"` maar zonder expliciete `content_type`. Hierdoor valt alles terug op `content_type="unknown"` in plaats van `"kb_article"`. Dit leidt tot de verkeerde enrichment-strategie (KB-006: `first_n` context, geen HyPE questions vector).

### Wat ontbreekt

| Functie | Status |
|---|---|
| Gitea webhook handler in knowledge-ingest | Geïmplementeerd |
| Webhook registratie bij KB-aanmaak in Docs | **Ontbreekt** |
| `content_type="kb_article"` in webhook handler | **Ontbreekt** |
| Webhook de-registratie bij KB-verwijdering | **Ontbreekt** |
| Initiële bulk-sync voor bestaande pagina's | **Ontbreekt** |
| Recovery-sync (herindexeer alle pagina's van een KB) | **Ontbreekt** |

---

## What this SPEC builds

1. **Webhook registratie bij KB-aanmaak** -- `docs/app/api/orgs/{org}/kbs/route.ts` roept na aanmaak van de Gitea repo `knowledge_ingest.registerKBWebhook()` aan
2. **Webhook de-registratie bij KB-verwijdering** -- `docs/app/api/orgs/{org}/kbs/{kb}/route.ts` verwijdert de webhook bij DELETE
3. **`content_type="kb_article"` in webhook handler** -- kleine fix in `ingest.py` webhook route
4. **Bulk-sync endpoint** -- `POST /ingest/v1/kb/sync` in knowledge-ingest haalt alle pagina's van een KB op uit Gitea en indexeert ze
5. **Initiële sync bij KB-aanmaak** -- na webhook-registratie wordt bulk-sync getriggerd voor lege-naar-gevulde KB's (optioneel: als de KB al content heeft)

Na deze SPEC is elke pagina die een gebruiker aanmaakt, wijzigt of hernoemt in Docs binnen seconden doorzoekbaar via de retrieval-laag.

---

## Sync-architectuur

### Trigger 1: Pagina aanmaken of wijzigen (incrementeel)

```
Gebruiker slaat op in BlockNote editor
  → Docs API: PUT /api/orgs/{org}/kbs/{kb}/pages/{path}
  → gitea.putFile() → commit in Gitea repo
  → Gitea push event → POST /ingest/v1/webhook/gitea
  → knowledge-ingest: fetch raw content van Gitea
  → ingest_document(content_type="kb_article", source_type="docs")
  → chunk → embed (raw) → upsert Qdrant + soft-delete vorig artifact in PG
  → enqueue enrichment (Procrastinate, enrich-interactive queue)
  → [async] contextual prefix genereren → re-embed → Qdrant update
```

Latency van opslaan tot doorzoekbaar: < 10 seconden (raw embedding). Verrijkt: 30-60 seconden afhankelijk van documentlengte en LLM-doorlooptijd.

### Trigger 2: Pagina verwijderen

```
Gebruiker verwijdert pagina
  → Docs API: DELETE /api/orgs/{org}/kbs/{kb}/pages/{path}
  → gitea.deleteFile() → commit
  → Gitea push event → webhook handler
  → qdrant_store.delete_document(org_id, kb_slug, path)
  → pg_store.soft_delete_artifact(org_id, kb_slug, path)
```

De chunks worden hard-verwijderd uit Qdrant. Het artifact in PostgreSQL krijgt een `deleted_at` timestamp (soft-delete) voor audit trail. Zie D3.

### Trigger 3: Pagina hernoemen

```
Gebruiker hernoemt pagina
  → Docs API: POST /api/orgs/{org}/kbs/{kb}/page-rename/{old-path}
  → gitea.putFile(new-path.md)   → commit A → push event A
  → gitea.deleteFile(old-path.md) → commit B → push event B
  → Webhook event A: ingest_document(new-path) → nieuw artifact + chunks
  → Webhook event B: delete_document(old-path) → chunks verwijderd
```

De twee webhooks kunnen in willekeurige volgorde aankomen. Als event B (delete) vóór event A (create) binnenkomt: `delete_document` voor een path dat nog niet in Qdrant staat -- dit is een no-op, geen fout. Als event A vóór B: het nieuwe pad is al geïndexeerd wanneer het oude verwijderd wordt. Beide volgorden zijn correct.

### Trigger 4: KB aanmaken

```
Admin maakt KB aan in portal
  → Docs API: POST /api/orgs/{org}/kbs
  → Gitea repo aangemaakt (bestaand)
  → knowledge_ingest.registerKBWebhook(orgId, kbSlug, giteaRepo)
    → POST /ingest/v1/kb/webhook  (intern endpoint)
    → knowledge-ingest registreert webhook in Gitea via Gitea API
  → knowledge_ingest.bulkSyncKB(orgId, kbSlug)  [alleen als repo al content heeft]
    → POST /ingest/v1/kb/sync
    → knowledge-ingest haalt alle .md files op via Gitea tree API
    → indexeert alle pagina's sequentieel (enrich-bulk queue)
```

### Trigger 5: KB verwijderen

```
Admin verwijdert KB
  → Docs API: DELETE /api/orgs/{org}/kbs/{kb}
  → knowledge_ingest.deleteKB()  (al geïmplementeerd: verwijdert Qdrant chunks)
  → knowledge_ingest.deregisterKBWebhook(orgId, kbSlug, giteaRepo)  [nieuw]
    → DELETE /ingest/v1/kb/webhook
    → knowledge-ingest verwijdert webhook uit Gitea
```

---

## Design decisions

### D1: Sync-trigger — Gitea webhook (reeds geïmplementeerd)

De webhook handler in `knowledge-ingest` bestaat al en is functioneel. De trigger is Gitea zelf: elke schrijfoperatie via de Gitea API (putFile, deleteFile) creëert een commit en vuurt een push event. Dit is het correcte koppelpunt.

**Alternatieven afgewezen:**

- *Event vanuit Docs API zelf* (fire-and-forget call naar knowledge-ingest na elke pagina-save): dit dupliceert de ingest-logica. De webhook verwerkt ook commits die buiten de Docs UI gemaakt worden (directe Gitea API calls, toekomstige git-push door klanten). De Gitea webhook is de enige bron van waarheid.

- *Polling*: introduceert vertraging, extra DB/API load, en state-management voor "wat is er gewijzigd sinds vorige poll". De webhook geeft precies de gewijzigde bestanden per commit — geen vergelijking nodig.

**Wat ontbreekt:** de webhook is nooit geregistreerd in Gitea. Deze SPEC voegt registratie toe in het KB-aanmaak flow.

### D2: Granulariteit — per pagina ingesteren, chunker bepaalt secties

De eenheid van ingest is één Markdown-bestand (één pagina). De webhook ontvangt bestandspaden, niet secties. De chunker in knowledge-ingest splitst elke pagina in 300-500 token chunks op sectie/alinea-grenzen (KB-006 profiel voor `kb_article`).

**Consequentie voor verwijdering:** bij het verwijderen van een pagina worden alle chunks van die pagina verwijderd via het bestandspad (`org_id + kb_slug + path` als samengestelde sleutel). Dit is mogelijk omdat `path` als filterveld geïndexeerd is in Qdrant.

**Alternatief overwogen:** sectie-niveau webhooks (GitHub-style section links). Niet haalbaar: Gitea push events geven alleen bestandspaden, niet secties. Sectie-granulariteit is een ingest-implementatiedetail, geen trigger-detail.

### D3: Verwijdering — hard delete uit Qdrant, soft-delete in PostgreSQL

KB-artikelen zijn gepubliceerde kennisbank-content. Een verwijderde pagina moet niet meer doorzoekbaar zijn. Temporele bewaring (zoals bij meeting-transcripts: "wat zei het team op 15 december") is niet van toepassing op KB-artikelen.

**Huidige implementatie is correct:** `qdrant_store.delete_document()` verwijdert hard uit Qdrant; `pg_store.soft_delete_artifact()` bewaart het artifact in PostgreSQL met een `deleted_at` timestamp. Dit geeft:
- Correcte retrieval (verwijderde pagina's niet meer in zoekresultaten)
- Audit trail in PostgreSQL (wanneer was een pagina aanwezig)
- Geen storage-overhead in Qdrant voor content die nooit meer opgezocht wordt

**`invalid_at` is voor de graph-laag (Graphiti), niet voor Qdrant.** Qdrant is een zoekindex; PostgreSQL (en later Graphiti) is de temporele waarheidsbron. Dit is in lijn met de architectuur in `knowledge-system-fundamentals.md`.

**Uitzondering:** `superseded_by` in frontmatter (een pagina die vervangen is maar bewust bewaard blijft). Dit is geen verwijdering maar een status-update. De webhook herindexeert de pagina met de nieuwe frontmatter; het `superseded_by` veld zit in de Qdrant payload zodat retrieval het kan meewegen. Dit valt buiten scope van KB-009.

### D4: Webhook-registratie in Gitea via knowledge-ingest, niet direct vanuit Docs

Docs heeft de Gitea token al voor zijn eigen operaties (bestandsbeheer). Maar webhook-registratie koppelen we via een intern endpoint in knowledge-ingest om twee redenen:

1. De webhook-URL en het HMAC-secret zijn knowledge-ingest configuratie, niet Docs configuratie. Docs hoeft niet te weten op welk adres knowledge-ingest luistert.
2. knowledge-ingest beheert de volledige webhook lifecycle (registratie, de-registratie, secret rotatie) op één plek.

**Intern protocol:** Docs roept `POST /ingest/v1/kb/webhook` aan met `X-Internal-Secret`. knowledge-ingest registreert vervolgens de webhook in Gitea via zijn eigen Gitea token. De webhook-URL is `{KNOWLEDGE_INGEST_PUBLIC_URL}/ingest/v1/webhook/gitea`.

### D5: Bulk-sync als recovery-mechanisme, niet als primair pad

De primaire sync-route is de Gitea webhook. Bulk-sync (`POST /ingest/v1/kb/sync`) is een hulpmiddel voor:
- Initiële indexering van een KB die al content heeft bij aanmaak
- Recovery na een storing waarbij webhooks gemist zijn
- Handmatige herindexering na een content-migratie

Bulk-sync gebruikt de `enrich-bulk` Procrastinate queue (lagere prioriteit dan `enrich-interactive`). Webhook-sync gebruikt `enrich-interactive`. Zie KB-005 D4.

### D6: org_id-resolutie blijft via Gitea org description-veld

De bestaande webhook handler haalt `org_id` op via `_get_org_id()`: Gitea org description = Zitadel org ID. Dit werkt al. Het alternatief (een lookup-tabel in PostgreSQL) voegt een extra hop toe zonder voordeel.

**Voorwaarde:** bij aanmaak van een Gitea org (bestaand proces in Docs) moet het Zitadel org ID opgeslagen worden als Gitea org description. Dit is al gedaan in `db.createOrg()` -- de Gitea org aanmaak-code moet dit bevestigd worden.

---

## Wijzigingen per service

### docs/lib/knowledge_ingest.ts

Drie nieuwe functies naast de bestaande `deleteKB` en `updateKBVisibility`:

```typescript
/**
 * Register a Gitea push webhook for a knowledge base.
 * Call when a KB is created in Docs.
 */
export async function registerKBWebhook(
  orgId: string,
  kbSlug: string,
  giteaRepo: string
): Promise<void>

/**
 * De-register the Gitea push webhook for a knowledge base.
 * Call when a KB is deleted from Docs.
 */
export async function deregisterKBWebhook(
  orgId: string,
  kbSlug: string,
  giteaRepo: string
): Promise<void>

/**
 * Trigger a full re-index of all pages in a knowledge base.
 * Call on KB creation (if repo has content) or for recovery.
 */
export async function bulkSyncKB(
  orgId: string,
  kbSlug: string
): Promise<void>
```

Alle drie roepen `kiFetch` aan (bestaand patroon, fire-and-forget met warn on failure).

### docs/app/api/orgs/{org}/kbs/route.ts

In de `POST` handler (KB aanmaken), na succesvolle Gitea repo aanmaak:

```typescript
// Register Gitea webhook so page edits are synced to Qdrant
await registerKBWebhook(org.id, slug, giteaRepo);
// Trigger initial index if repo already has content (e.g. cloned or migrated)
await bulkSyncKB(org.id, slug);
```

Beide calls zijn fire-and-forget (net als `deleteKB`). Een fout bij webhook-registratie logt een waarschuwing maar blokkeert de KB-aanmaak niet.

### docs/app/api/orgs/{org}/kbs/{kb}/route.ts

In de `DELETE` handler, vóór of tegelijk met `deleteKB()`:

```typescript
await deregisterKBWebhook(org.id, kbSlug, kb.gitea_repo);
await deleteKB(org.id, kbSlug);
```

### knowledge-ingest/routes/ingest.py

**Twee nieuwe endpoints:**

```python
@router.post("/ingest/v1/kb/webhook")
async def register_kb_webhook(request: Request, req: KBWebhookRequest) -> dict:
    """Register a Gitea push webhook for a KB. Called by Docs on KB creation."""
    _verify_internal_secret(request)
    webhook_url = f"{settings.public_url}/ingest/v1/webhook/gitea"
    await _register_gitea_webhook(req.gitea_repo, webhook_url)
    return {"status": "ok"}

@router.delete("/ingest/v1/kb/webhook")
async def deregister_kb_webhook(request: Request, req: KBWebhookRequest) -> dict:
    """De-register the Gitea push webhook for a KB. Called by Docs on KB deletion."""
    _verify_internal_secret(request)
    webhook_url = f"{settings.public_url}/ingest/v1/webhook/gitea"
    await _deregister_gitea_webhook(req.gitea_repo, webhook_url)
    return {"status": "ok"}

@router.post("/ingest/v1/kb/sync")
async def bulk_sync_kb_route(request: Request, req: BulkSyncRequest) -> dict:
    """Re-index all pages of a KB from Gitea. Called by Docs on KB creation or recovery."""
    _verify_internal_secret(request)
    pages = await _list_gitea_md_files(req.gitea_repo)
    # Enqueue each page as enrich-bulk task (non-blocking)
    for path in pages:
        content = await _fetch_gitea_file(req.gitea_repo, path)
        if content:
            ingest_req = IngestRequest(
                org_id=req.org_id, kb_slug=req.kb_slug,
                path=path, content=content,
                source_type="docs", content_type="kb_article",
            )
            await ingest_document(ingest_req)
    return {"status": "ok", "pages": len(pages)}
```

**Fix `content_type` in bestaande webhook handler:**

In de webhook handler, bij aanmaken van `IngestRequest`:
```python
# Voeg toe: content_type="kb_article"
req = IngestRequest(
    org_id=org_id, kb_slug=kb_slug, path=path,
    content=content, source_type="docs",
    content_type="kb_article",  # ← nieuw
)
```

**Nieuwe modellen:**

```python
class KBWebhookRequest(BaseModel):
    org_id: str
    kb_slug: str
    gitea_repo: str  # e.g. "org-myslug/personal"

class BulkSyncRequest(BaseModel):
    org_id: str
    kb_slug: str
    gitea_repo: str
```

**Nieuwe configuratie:**

```python
# config.py
public_url: str = "http://knowledge-ingest:8000"  # Public-facing URL for Gitea webhook callbacks
```

### knowledge-ingest/config.py

```python
public_url: str = "http://knowledge-ingest:8000"  # Used as webhook callback URL base
```

Gitea en knowledge-ingest delen `klai-net` in docker-compose. De webhook-URL is dus `http://knowledge-ingest:8000/ingest/v1/webhook/gitea` -- geen extra configuratie nodig. `GITEA_WEBHOOK_SECRET` is al aanwezig in docker-compose.yml.

---

## Geen schema-wijzigingen

KB-009 vereist geen PostgreSQL schema-wijzigingen. De `docs.knowledge_bases` tabel hoeft de webhook-ID niet op te slaan: de webhook wordt opgezocht bij de-registratie via een `GET /api/v1/repos/{repo}/hooks` query op Gitea (filter op onze callback URL).

---

## Acceptance criteria

| # | Criterion | EARS pattern |
|---|---|---|
| AC-1 | **When** een KB aangemaakt wordt via Docs, **then** registreert knowledge-ingest automatisch een Gitea push webhook voor die repo binnen 5 seconden | Event-driven |
| AC-2 | **When** een gebruiker een pagina opslaat in de BlockNote editor, **then** is de pagina doorzoekbaar in Qdrant binnen 15 seconden (raw embedding, voor enrichment) | Event-driven |
| AC-3 | **When** een pagina gewijzigd wordt, **then** zijn de oude Qdrant chunks verwijderd en zijn de nieuwe chunks beschikbaar na de webhook verwerking | Event-driven |
| AC-4 | **When** een pagina verwijderd wordt in Docs, **then** zijn alle Qdrant chunks van die pagina verwijderd en is het artifact soft-deleted in PostgreSQL | Event-driven |
| AC-5 | **When** een pagina hernoemd wordt (page-rename), **then** zijn de chunks van het oude pad verwijderd en zijn chunks voor het nieuwe pad aanwezig in Qdrant, ongeacht de volgorde van de twee webhook events | Event-driven |
| AC-6 | **When** een KB via Docs ingested wordt, **then** heeft elk Qdrant punt `content_type="kb_article"` in de payload | Ubiquitous |
| AC-7 | **When** `content_type="kb_article"` is gezet, **then** gebruikt de enrichment-pipeline de `first_n` context-strategie en wordt géén `vector_questions` aangemaakt (conform KB-006 profiel voor `kb_article`) | Event-driven |
| AC-8 | **When** een KB verwijderd wordt, **then** is de Gitea webhook de-geregistreerd en zijn alle Qdrant chunks verwijderd | Event-driven |
| AC-9 | **When** `POST /ingest/v1/kb/sync` aangeroepen wordt voor een KB, **then** worden alle bestaande `.md` bestanden in de Gitea repo geïndexeerd via de `enrich-bulk` queue | Event-driven |
| AC-10 | **When** de webhook-registratie faalt bij KB-aanmaak, **then** logt Docs een waarschuwing maar retourneert de KB-aanmaak als succesvol; de admin kan handmatig bulk-sync triggeren | Unwanted behavior |
| AC-11 | **When** de Gitea webhook een push event ontvangt voor een branch anders dan `main`, **then** wordt het event genegeerd (geen ingest) | Unwanted behavior |
| AC-12 | **When** de Gitea push event bestanden bevat waarvan het pad begint met `_` (zoals `_sidebar.yaml`), **then** worden deze bestanden niet geïndexeerd | Unwanted behavior |
| AC-13 | **When** de webhook-handler `org_id` niet kan ophalen uit Gitea voor de repo, **then** wordt het event genegeerd met een waarschuwing in de logs | Unwanted behavior |
| AC-14 | De bestaande tests in `test_webhook_hmac.py` blijven groen | Ubiquitous |
| AC-15 | **When** `KNOWLEDGE_INGEST_SECRET` geconfigureerd is, **then** weigeren de nieuwe endpoints (`/kb/webhook`, `/kb/sync`) requests zonder geldige `X-Internal-Secret` header | Ubiquitous |

---

## Beslissingen voor review

Hieronder de punten die besproken moeten worden vóór implementatie.

**B1: Gitea org description = Zitadel org ID**

De webhook handler leest `org_id` uit het Gitea org description-veld. Dit werkt als de beschrijving correct gezet is bij org-aanmaak. De Docs code die de Gitea org aanmaakt gebruikt `gitea_org_name = org-{slug}` maar het is niet gecheckt of de Gitea org description het Zitadel org ID bevat.

*Vraag:* Is de Gitea org description momenteel correct gevuld met het Zitadel org ID? Zo niet, vereist dit een migratiestap voor bestaande orgs.

**B2: Branch-filter — alleen main of configureerbaar?**

De bestaande webhook handler filtert niet op branch. De Docs app schrijft altijd naar de default branch (main). Een webhook voor een push naar een feature branch (als klanten ooit directe git-toegang krijgen) zou ongewenste ingest triggeren.

*Vraag:* Voeg nu een branch-filter toe (`ref == "refs/heads/main"`) of uitstellen tot klanten directe git-toegang krijgen?

**B3: Bulk-sync scope bij KB-aanmaak**

De SPEC triggert bulk-sync bij elke KB-aanmaak. Voor een lege KB (nieuw aangemaakt) haalt dit niets op en is het een no-op Gitea API call. Voor een KB met content (bijv. gemigreerd uit een bestaand systeem) is het nuttig.

*Vraag:* Altijd aanroepen (no-op is goedkoop) of alleen als de Gitea repo al content heeft? Voor nu wordt `bulkSyncKB` altijd aangeroepen -- als er geen pages zijn doet het niets. Kan dit voor verwarring zorgen in de logs?

**Opgelost: geen migratie nodig.** Er is alleen testdata; alle bestaande KB's kunnen worden gewist. KB-009 wordt geïmplementeerd op een schone database.
