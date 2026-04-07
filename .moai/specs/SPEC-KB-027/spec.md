---
id: SPEC-KB-027
version: "1.0.0"
status: completed
created: "2026-04-07"
updated: "2026-04-07"
author: Mark Vletter
priority: high
tags: [taxonomy, retrieval, query-classification, maybe_generate_proposal, doc_count]
related: [SPEC-KB-021, SPEC-KB-022, SPEC-KB-024, SPEC-KB-026]
---

# SPEC-KB-027: Taxonomy-Aware Retrieval + Taxonomy Completeness Fixes

## HISTORY

| Versie | Datum | Auteur | Wijziging |
|---|---|---|---|
| 1.0.0 | 2026-04-07 | Mark Vletter | Initiële versie na analyse van taxonomy pipeline completeness |

---

## Context

Na KB-021 t/m KB-026 staat de volledige taxonomy pipeline overeind: ingest classificeert documenten, Qdrant slaat `taxonomy_node_ids` op, de retrieval-api heeft een `taxonomy_node_ids` filter in `search.py`. Maar **de filter wordt nooit gebruikt**. De research-api (chat) stuurt nooit `taxonomy_node_ids` mee naar de retrieval-api — dus werkt het als identieke fallback op het pad zónder filter.

Dit SPEC lost drie resterende gaten op, in volgorde van impact:

**R1 — Query classificatie in research-api (biggest gap):**
De chat-flow moet de query classificeren naar taxonomy nodes vóór retrieval, zodat de bestaande filter in `klai-retrieval-api/retrieval_api/services/search.py` eindelijk actief wordt. Dit is de overgang van metadata-opslag naar daadwerkelijk taxonomy-guided retrieval.

**R2 — Fix dead code in `maybe_generate_proposal`:**
De aanroep van `maybe_generate_proposal()` in `routes/ingest.py` stuurt altijd precies 1 document mee. De functie heeft een `_MIN_UNMATCHED_FOR_PROPOSAL = 3` drempel. Resultaat: de functie returnt altijd direct zonder actie — het is dead code. De clustering job genereert proposals correct; de per-document aanroep is dus niet alleen nutteloos maar misleidend.

**R3 — Verwijder `doc_count` denormalisatie:**
`PortalTaxonomyNode.doc_count` wordt handmatig bijgehouden bij node-delete. Bij elke andere mutatie (re-ingest, connector cleanup, backfill) loopt het uit de pas. De coverage dashboard haalt de echte count al live uit Qdrant. De PostgreSQL kolom is misleidende denormalisatie zonder waarde.

---

## Scope

**In scope:**
- `klai-focus/research-api`: query classificatie vóór retrieval (R1)
- `klai-knowledge-ingest/routes/ingest.py`: verwijder dode `maybe_generate_proposal` aanroep (R2)
- `klai-knowledge-ingest/taxonomy_tasks.py`: voeg proposal generatie toe aan einde van backfill Phase 2 (R2)
- `klai-portal/backend/app/models/taxonomy.py`: verwijder `doc_count` kolom (R3)
- `klai-portal/backend/app/api/taxonomy.py`: verwijder alle `doc_count` mutaties (R3)
- Alembic migratie voor `doc_count` verwijdering (R3)

**Buiten scope:**
- Browse interface (navigate-by-topic)
- Cross-KB taxonomy coherentie
- Tag-gebaseerde retrieval filters
- Taxonomy governance UI verbeteringen
- Centroid store naar database verplaatsen

---

## Requirements

### R1 — Query classificatie in research-api voor taxonomy-aware retrieval

**Context:**
`klai-retrieval-api/retrieval_api/services/search.py` bevat al een werkende `MatchAny` filter op `taxonomy_node_ids`. `klai-retrieval-api/retrieval_api/models.py` heeft al `taxonomy_node_ids: list[int] | None = None` in `RetrieveRequest`. De filter ligt klaar maar wordt nooit gevuld.

**EARS:**

WHEN de research-api een retrieval request uitvoert voor een KB
AND die KB heeft taxonomy nodes geconfigureerd in de portal,
THEN SHALL de research-api de query tekst eerst classificeren naar taxonomy node IDs via het knowledge-ingest classify endpoint,
AND de geretourneerde node IDs meesturen als `taxonomy_node_ids` in de retrieval request naar de retrieval-api.

WHEN de taxonomie-classificatie faalt (timeout, service unavailable, lege response),
THEN SHALL de retrieval request worden uitgevoerd zonder `taxonomy_node_ids` (bestaand gedrag behouden).

WHEN de taxonomy coverage voor een KB lager is dan `TAXONOMY_RETRIEVAL_MIN_COVERAGE` (default: 0.3 — 30% van chunks gecategoriseerd),
THEN SHALL de taxonomy filter NIET worden toegepast, ook niet als classificatie slaagt.
RATIONALE: bij lage coverage sluit een harde filter te veel relevante maar ongeclassificeerde chunks uit.

WHEN de query classificeert naar node IDs die niet overeenkomen met de beschikbare taxonomy nodes van de KB,
THEN SHALL de filter worden overgeslagen (stale classificatie resultaat).

**Implementatiedetails:**

De research-api roept het bestaande endpoint aan:
```
POST /ingest/v1/taxonomy/classify
{"org_id": "<str>", "kb_slug": "<str>", "text": "<query>"}
→ {"taxonomy_node_ids": [5, 7]}
```

Coverage check: de research-api vraagt de coverage stats op via:
```
GET /ingest/v1/taxonomy/coverage-stats?kb_slug=<str>&org_id=<str>
→ {"total_chunks": N, "untagged_count": M, ...}
```
Coverage = `(total_chunks - untagged_count) / total_chunks`. Beide endpoints bestaan al.

De coverage check + classify aanroep lopen parallel met `asyncio.gather()`. Totale overhead: max 3 seconden (timeout). Retrieval wordt niet geblokkeerd bij failure.

Voeg `TAXONOMY_RETRIEVAL_MIN_COVERAGE: float = 0.3` toe aan research-api config.
Voeg `KNOWLEDGE_INGEST_URL` toe aan research-api config (als die er nog niet is).

**Testbare grens:**
Unit test: `_get_taxonomy_filter(query, kb_slug, org_id)` → returnt `list[int] | None`.

---

### R2 — Fix dead code: `maybe_generate_proposal` per document

**Context:**
In `klai-knowledge-ingest/knowledge_ingest/routes/ingest.py`:
```python
if has_taxonomy and not taxonomy_node_ids:
    _t = _asyncio.create_task(
        maybe_generate_proposal(
            unmatched_documents=[DocumentSummary(title=title, content_preview=...)],
            existing_nodes=taxonomy_nodes,
        )
    )
```
`maybe_generate_proposal` heeft `_MIN_UNMATCHED_FOR_PROPOSAL = 3`. Er wordt altijd 1 document meegegeven. De check `len(unmatched_documents) < 3` is altijd True → de functie returnt altijd direct. De aanroep is dead code en genereert nutteloze asyncio tasks.

**EARS:**

WHEN een ingest batch eindigt (backfill Phase 2 `_run_backfill`),
AND er zijn documents die na classificatie `taxonomy_node_ids = []` hebben (unmatched),
THEN SHALL `maybe_generate_proposal()` worden aangeroepen met alle unmatched documents uit die batch.

WHEN `len(unmatched_documents) >= 3`,
THEN SHALL `maybe_generate_proposal()` een taxonomy voorstel genereren en indienen.

WHEN `len(unmatched_documents) < 3`,
THEN SHALL `maybe_generate_proposal()` direct returnen zonder LLM aanroep.

De aanroep van `maybe_generate_proposal()` in `routes/ingest.py` (per-document, per-ingest) SHALL worden verwijderd.

**Implementatie:**

In `taxonomy_tasks.py` `_run_backfill()`, aan het einde van Phase 2:
```python
# Collect unmatched docs from Phase 2 for batch proposal
if unmatched_summaries:  # DocumentSummary list, populated during Phase 2
    await maybe_generate_proposal(
        org_id=org_id,
        kb_slug=kb_slug,
        unmatched_documents=unmatched_summaries,
        existing_nodes=taxonomy_nodes,
    )
```

De backfill slaat `DocumentSummary(title=..., content_preview=...)` op voor elk document dat na classificatie `node_ids = []` heeft.

---

### R3 — Verwijder `doc_count` denormalisatie

**Context:**
`portal_taxonomy_nodes.doc_count` wordt bijgehouden in:
- `delete_taxonomy_node()`: handmatig optellen bij parent
- Nergens anders (re-ingest, backfill, connector cleanup updaten het NIET)

De coverage dashboard (`GET /taxonomy/{kb_slug}/coverage`) haalt chunk counts al live uit Qdrant. `doc_count` in PostgreSQL is structureel onjuist en wordt nooit correct gehouden.

**EARS:**

WHEN de coverage dashboard wordt geladen,
THEN SHALL chunk counts exclusief worden opgehaald uit Qdrant via het bestaande coverage-stats endpoint.

WHEN een taxonomy node wordt verwijderd,
THEN SHALL er geen `doc_count` mutatie plaatsvinden op de parent node.

Het veld `portal_taxonomy_nodes.doc_count` SHALL worden verwijderd uit het datamodel en de API responses.

**Implementatie:**

1. Verwijder `doc_count: Mapped[int]` uit `PortalTaxonomyNode` model
2. Verwijder `doc_count` uit `TaxonomyNodeOut` Pydantic schema
3. Verwijder alle `doc_count` mutaties in `taxonomy.py` API
4. Alembic migratie: `ALTER TABLE portal_taxonomy_nodes DROP COLUMN doc_count`
5. Controleer frontend: als `doc_count` wordt getoond in de UI → vervangen door live Qdrant count via coverage-stats (of weglaten)

**Backward compatibility:**
De `TaxonomyNodeOut` response verliest `doc_count`. Check frontend-componenten die dit veld uitlezen.

---

## Volgorde van implementatie

| Stap | Requirement | Reden |
|---|---|---|
| 1 | R2 | Kleinste wijziging, geen datamodel impact, verwijdert dead code |
| 2 | R3 | Alembic migratie + API cleanup, onafhankelijk van R1 |
| 3 | R1 | Research-api wijziging, bouwt op R2+R3 maar is onafhankelijk |

---

## Data model wijzigingen

### `PortalTaxonomyNode` (verwijdering)

```python
# VERWIJDERD:
doc_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
```

### `TaxonomyNodeOut` (verwijdering)

```python
# VERWIJDERD:
doc_count: int
```

### Research-api config (toevoeging)

```python
taxonomy_retrieval_min_coverage: float = 0.3
knowledge_ingest_url: str = "http://knowledge-ingest:8000"
```

### `_run_backfill` return dict (toevoeging)

```python
{
    "labelled": int,
    "migrated": int,
    "classified": int,
    "tagged": int,
    "skipped": int,
    "proposals_submitted": int  # NIEUW
}
```

---

## Acceptatiecriteria

| # | Criterium | Verificatie |
|---|-----------|-------------|
| AC1 | Chat retrieval gebruikt taxonomy filter als coverage >= 30% | Stel KB in met 5 gecategoriseerde chunks, stuur query, check retrieval-api logs: `taxonomy_node_ids=[N]` aanwezig in request |
| AC2 | Taxonomy filter wordt overgeslagen bij coverage < 30% | Stel KB in met 1 gecategoriseerde van 10 chunks, check dat `taxonomy_node_ids=None` in retrieval request |
| AC3 | Taxonomy filter wordt overgeslagen bij classify timeout | Mock classify endpoint om te time-outen, check dat retrieval toch slaagt |
| AC4 | Backfill genereert een proposal als >= 3 docs ongematchd zijn | Start backfill op KB met 5 ongeclassificeerde docs, geen taxonomy nodes beschikbaar → 1 proposal in portal review queue |
| AC5 | Geen `asyncio.create_task(maybe_generate_proposal(...))` meer in ingest route | `grep -r "maybe_generate_proposal" klai-knowledge-ingest/knowledge_ingest/routes/` → geen resultaat |
| AC6 | `doc_count` kolom bestaat niet meer in database | `\d portal_taxonomy_nodes` → geen `doc_count` kolom |
| AC7 | Taxonomy API responses bevatten geen `doc_count` veld meer | `GET /api/app/knowledge-bases/{slug}/taxonomy/nodes` → geen `doc_count` in items |
| AC8 | Coverage dashboard toont nog steeds correcte chunk counts | Coverage dashboard openen → per-node chunk counts gelijk aan Qdrant live count |

---

## Aannames

| Aanname | Confidence | Risico als fout |
|---|---|---|
| Research-api heeft al een httpx client voor knowledge-ingest calls | Gemiddeld | Kleine extra setup, geen blocker |
| Frontend gebruikt `doc_count` niet voor kritische functionaliteit | Gemiddeld | Visuele regressie in taxonomy node list; oplossing: toon coverage count live |
| 30% coverage als minimum is conservatief genoeg voor productie KBs | Gemiddeld | Te restrictief → coverage setting aanpassen per KB; te soepel → retrieval precision daalt |
| `classify_document()` latency < 2s voor korte queries (< 50 woorden) | Hoog | Retrieval latency stijgt; oplossing: striktere timeout + parallel met coverage check |
| Backfill batch grootte van 100 is voldoende om >= 3 unmatched docs per batch te hebben | Gemiddeld | Bij KBs met hoge classificatiegraad kan een batch allemaal matched zijn → `maybe_generate_proposal` vuurt dan niet vanuit backfill, maar dat is correct gedrag |
