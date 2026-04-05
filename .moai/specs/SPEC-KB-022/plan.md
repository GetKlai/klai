---
id: SPEC-KB-022
phase: plan
---

# Implementatieplan -- SPEC-KB-022: Taxonomy V2

## Overzicht

De implementatie is opgedeeld in 7 fasen met duidelijke afhankelijkheden. Fasen 1-3 vormen de kern (multi-label migratie), fasen 4-7 bouwen daarop voort (editorial intelligence).

---

## Fase 1: Database migraties (Primair Doel)

**Prioriteit: Hoog -- geen andere fase kan starten zonder dit**

### 1a. Alembic migratie: `PortalTaxonomyNode.description`

**Bestand:** nieuwe Alembic migratie

```sql
ALTER TABLE portal_taxonomy_nodes ADD COLUMN description TEXT;
```

Nullable, geen default. Bestaande nodes krijgen `NULL` tot de bootstrap of reviewer ze invult.

### 1b. Alembic migratie: `PortalRetrievalGap.taxonomy_node_ids`

**Bestand:** nieuwe Alembic migratie

```sql
ALTER TABLE portal_retrieval_gaps ADD COLUMN taxonomy_node_ids INTEGER[];
CREATE INDEX ix_retrieval_gaps_taxonomy ON portal_retrieval_gaps USING GIN (taxonomy_node_ids);
```

GIN index voor efficient filteren op array-elementen.

### 1c. Portal model updates

**Bestand:** `klai-portal/backend/app/models/taxonomy.py`
- Voeg `description: Mapped[str | None] = mapped_column(Text, nullable=True)` toe aan `PortalTaxonomyNode`

**Bestand:** `klai-portal/backend/app/models/retrieval_gaps.py`
- Voeg `taxonomy_node_ids: Mapped[list[int] | None]` toe met `ARRAY(Integer)` type

---

## Fase 2: Multi-label classifier (Primair Doel)

**Prioriteit: Hoog -- afhankelijk van Fase 1a**

### 2a. TaxonomyClassifier refactor

**Bestand:** `klai-knowledge-ingest/knowledge_ingest/taxonomy_classifier.py`

Wijzigingen:
1. `TaxonomyNode` DTO uitbreiden met `description: str | None`
2. Systeem-prompt aanpassen: node descriptions meesturen naast namen
3. Output format wijzigen: `{ "nodes": [{"node_id": int, "confidence": float}], "tags": ["str"], "reasoning": str }`
4. Return type wijzigen: `tuple[list[tuple[int, float]], list[str]]` -- (nodes met confidence, gesuggereerde tags)
5. Threshold filter: alleen nodes met confidence >= 0.5, maximum 5 nodes
6. Tags: maximum 5, lowercase, getrimd

Prompt template:
```
- id=5: Billing > Subscriptions -- Vragen over facturatie, abonnementen, betalingsmethoden, annuleringen
- id=7: Setup > SSO -- Configuratie van single sign-on, SAML, OIDC, login-problemen
```

### 2b. PortalClient update

**Bestand:** `klai-knowledge-ingest/knowledge_ingest/portal_client.py`

- `TaxonomyNode` DTO: `description` veld toevoegen
- `_fetch_from_portal()`: `description` meenemen uit API response
- Cache invalidatie: ongewijzigd (5 min TTL volstaat)

### 2c. Portal internal endpoint update

**Bestand:** `klai-portal/backend/app/api/taxonomy.py`

- Internal nodes endpoint: `description` veld meesturen in response

---

## Fase 3: Qdrant migratie & ingest pipeline (Primair Doel)

**Prioriteit: Hoog -- afhankelijk van Fase 2**

### 3a. Qdrant payload index

**Bestand:** `klai-knowledge-ingest/knowledge_ingest/qdrant_store.py`

1. `_ensure_payload_indexes()`: voeg `taxonomy_node_ids` en `tags` toe als keyword indexes
2. Behoud `taxonomy_node_id` index voorlopig (backward compat)

### 3b. Upsert functies

**Bestand:** `klai-knowledge-ingest/knowledge_ingest/qdrant_store.py`

1. `upsert_chunks()`: parameter `taxonomy_node_id: int | None` -> `taxonomy_node_ids: list[int] | None`
2. `upsert_full_document()`: idem
3. `base_payload`: `taxonomy_node_ids` (lijst) in plaats van `taxonomy_node_id` (int)
4. Voeg `tags: list[str]` toe aan base_payload wanneer beschikbaar
5. `has_taxonomy` logica: `taxonomy_node_ids` weggelaten wanneer KB geen nodes heeft (zelfde als nu)

### 3c. Ingest route update

**Bestand:** `klai-knowledge-ingest/knowledge_ingest/routes/ingest.py`

1. Classificatie aanroep: `classify_document()` retourneert nu `(nodes, tags)` tuple
2. `taxonomy_node_ids = [node_id for node_id, conf in nodes]`
3. Tags samenvoegen: frontmatter tags + LLM tags (dedup, frontmatter voorrang)
4. Doorgeven aan `upsert_chunks(taxonomy_node_ids=..., tags=...)`
5. Proposal trigger: wanneer `taxonomy_node_ids` leeg is

### 3d. Retrieval filter update

**Bestand:** `klai-retrieval-api/retrieval_api/services/search.py`

1. Filter key wijzigen: `taxonomy_node_id` -> `taxonomy_node_ids`
2. Fallback logica: als chunk `taxonomy_node_ids` niet heeft, check `taxonomy_node_id` (OR-conditie)
3. Nieuw: `tags` filter toevoegen (MatchAny op `tags` veld)

**Bestand:** `klai-retrieval-api/retrieval_api/models.py`

1. Voeg `tags: list[str] | None = None` toe aan `RetrieveRequest`

---

## Fase 4: Backfill migratie (Secundair Doel)

**Prioriteit: Hoog -- kan parallel met Fase 5**

**Bestand:** `klai-knowledge-ingest/knowledge_ingest/routes/taxonomy.py`

1. Scroll eerst chunks met oud `taxonomy_node_id` veld (niet-null): migreer naar `taxonomy_node_ids: [old_value]`
2. Scroll vervolgens chunks zonder enige classificatie: classificeer met multi-label classifier
3. Voor alle verwerkte chunks: genereer tags via classifier en sla op
4. Batch processing: `batch_size` parameter, sleep tussen batches
5. Idempotent: chunks met bestaand `taxonomy_node_ids` veld overslaan

Response uitbreiden:
```json
{ "migrated": int, "classified": int, "tagged": int, "skipped": int }
```

---

## Fase 5: Node description generatie (Secundair Doel)

**Prioriteit: Medium -- kan parallel met Fase 4**

### 5a. Description generator

**Nieuw bestand:** `klai-knowledge-ingest/knowledge_ingest/description_generator.py`

```python
async def generate_node_description(
    node_name: str,
    parent_name: str | None,
    sample_titles: list[str],
) -> str:
    """Generate a 1-2 sentence description for a taxonomy node using klai-fast."""
```

- Input: node naam, parent naam, max 10 sample document titels
- Output: max 200 tekens, taal van de KB content
- Timeout: 5 seconden

### 5b. Bootstrap endpoint uitbreiding

Bestaand bootstrap endpoint uitbreiden:
- Bij het genereren van proposals ook descriptions meesturen in de proposal payload
- Bij goedkeuring: description direct opslaan op de node

### 5c. Portal API: description editing

**Bestand:** `klai-portal/backend/app/api/taxonomy.py`

- PATCH endpoint voor nodes: `description` veld bewerkbaar
- Validatie: max 500 tekens

---

## Fase 6: Gap taxonomy-classificatie (Secundair Doel)

**Prioriteit: Hoog -- afhankelijk van Fase 1b en 2**

### 6a. Gap event opslag uitbreiden

**Bestand:** `klai-portal/backend/app/api/internal.py` (of waar gap events worden opgeslagen)

1. Bij ontvangst van gap event: check of `taxonomy_node_ids` aanwezig is
2. Zo ja: direct opslaan op de gap record
3. Zo nee: classificeer de `query_text` tegen taxonomy nodes van `nearest_kb_slug`
4. Classificatie: hergebruik classifier logica (async, best-effort, niet-blokkerend)

### 6b. Gap dashboard endpoints

**Bestand:** `klai-portal/backend/app/api/app_gaps.py`

1. `GET /api/app/gaps`: voeg optionele `taxonomy_node_id: int` query parameter toe
2. Nieuw: `GET /api/app/gaps/by-taxonomy`: aggregatie per taxonomy node
3. Priority berekening: frequentie / dag over de gevraagde periode

---

## Fase 7: Coverage dashboard (Einddoel)

**Prioriteit: Medium -- afhankelijk van Fase 3**

### 7a. Coverage endpoint

**Bestand:** `klai-portal/backend/app/api/taxonomy.py` (of nieuw bestand)

`GET /api/app/knowledge-bases/{kb_slug}/taxonomy/coverage`

Implementatie:
1. Haal taxonomy nodes op uit PostgreSQL (naam, id, parent)
2. Query Qdrant: scroll alle chunks voor org/kb, tel `taxonomy_node_ids` per node
3. Query PostgreSQL: tel open gaps per taxonomy node
4. Combineer tot coverage response met health status

Alternatief (performanter voor grote KBs):
- Gebruik Qdrant `count` API met filter per taxonomy node
- Cache resultaat 5 minuten

### 7b. Untagged percentage

In hetzelfde endpoint:
- Tel chunks zonder `taxonomy_node_ids` veld of met lege lijst
- Bereken percentage van totaal

---

## Afhankelijkheden

```
Fase 1 (DB migraties)
  |
  +-> Fase 2 (Multi-label classifier) -> Fase 3 (Qdrant + ingest) -> Fase 4 (Backfill)
  |                                                                    |
  +-> Fase 5 (Node descriptions) [parallel met 4]                     +-> Fase 7 (Coverage)
  |
  +-> Fase 6 (Gap classificatie) [na Fase 1b + 2]
```

---

## Risico's

| Risico | Waarschijnlijkheid | Mitigatie |
|---|---|---|
| Multi-label classificatie genereert te veel false positives | Medium | Start met max 3 nodes, verhoog naar 5 na evaluatie |
| Qdrant MatchAny op array is trager dan op integer | Laag | Keyword index op array is standaard Qdrant operatie |
| Backfill duurt te lang op grote KBs (100k+ chunks) | Medium | batch_size + sleep + progress logging; kan in achtergrond draaien |
| Node descriptions zijn te generiek om classificatie te verbeteren | Laag | Descriptions worden door reviewer verfijnd; LLM krijgt ook sample titels |
| Gap classificatie voegt latency toe aan gap event opslag | Laag | Async fire-and-forget; classificatie faalt gracefully |
| Tag suggesties zijn inconsistent (synoniemen, typos) | Medium | Governance queue; geaccepteerde tags worden canonical |
| Coverage query op Qdrant is langzaam bij grote collecties | Medium | Qdrant count API + caching; alternatief: periodieke batch aggregatie |

---

## Test strategie

### Unit tests
- TaxonomyClassifier: mock LiteLLM, test multi-label output parsing
- Qdrant upsert: test `taxonomy_node_ids` als array in payload
- Retrieval filter: test MatchAny op `taxonomy_node_ids` array + fallback naar oud veld
- Gap classificatie: test opslag met en zonder taxonomy_node_ids

### Integratie tests
- End-to-end ingest: document -> classificatie -> Qdrant payload bevat taxonomy_node_ids + tags
- Backfill: migratie van `taxonomy_node_id` naar `taxonomy_node_ids`
- Coverage endpoint: correcte aggregatie van chunks en gaps

### Backward compatibility
- Bestaande chunks met `taxonomy_node_id` (int) werken nog met retrieval filter
- Retrieval zonder taxonomy filters gedraagt zich identiek aan voor de migratie
