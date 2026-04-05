---
id: SPEC-KB-022
version: "1.0.0"
status: draft
created: "2026-04-05"
updated: "2026-04-05"
author: Mark Vletter
priority: high
tags: [taxonomy, multi-label, tags, gap-classification, coverage, editorial-intelligence]
related: [SPEC-KB-021, SPEC-KB-012]
---

# SPEC-KB-022: Taxonomy V2 -- Multi-label tagging, gap-classificatie & editoriale intelligentie

## HISTORY

| Versie | Datum | Auteur | Wijziging |
|---|---|---|---|
| 1.0.0 | 2026-04-05 | Mark Vletter | Initiele versie |

---

## Context

SPEC-KB-021 leverde de eerste taxonomy-integratie op: single-label classificatie (`taxonomy_node_id: int`) op Qdrant chunks, proposal-generatie, en een backfill endpoint. Productiegebruik heeft vijf fundamentele beperkingen blootgelegd:

1. **Single-label is onvoldoende** -- documenten vallen vaak onder meerdere categorieen (bijv. "SSO-configuratie" hoort bij zowel "Setup" als "Security")
2. **Node descriptions ontbreken** -- de classifier krijgt alleen namen, wat leidt tot slechte classificatie bij ambigue nodes
3. **Free tags zijn geen first-class concept** -- het research document beschrijft tags als snelle feedbacklaag voor de long tail, maar ze zijn niet geindexeerd of filterbaar
4. **Gaps worden niet geclassificeerd** -- het research document (sectie 10.3) identificeert taxonomy-aware gap detection als de hoogste editoriale waarde
5. **Coverage dashboard ontbreekt** -- er is geen zicht op welke taxonomy-nodes goed gedekt zijn en welke dunne of lege gebieden hebben

Dit SPEC bouwt voort op de SPEC-KB-021 infrastructuur en transformeert Klai Knowledge van een zoeksysteem naar een **kennismanagementplatform met editoriale intelligentie**.

---

## Scope

**In scope:**
- `klai-knowledge-ingest`: multi-label classificatie, tag-suggestie, updated backfill
- `klai-portal/backend`: node descriptions, gap taxonomy-classificatie, coverage endpoint, tag governance
- `klai-retrieval-api`: aangepaste filter key (`taxonomy_node_ids` array), tag filter
- `deploy/litellm`: gap events met taxonomy_node_ids (reeds aanwezig, portal moet opslaan)

**Buiten scope:**
- Browse-interface (UI) voor taxonomy-navigatie (apart SPEC)
- KBScopeBar aanpassing voor taxonomy-filtering in de chat-UI (apart SPEC)
- Cross-KB taxonomie-coherentie (V3)
- Faceted taxonomy dimensies (V3)
- Automatische taxonomy-evolutie (splits/merges op basis van gap-data)

---

## Requirements

### R1 -- Multi-label chunk tagging

WHEN een document wordt geingested in een knowledge base met taxonomy nodes,
THEN SHALL de ingest pipeline het document classificeren naar **alle** matchende taxonomy nodes
AND `taxonomy_node_ids: list[int]` opslaan op alle resulterende Qdrant chunks.

WHEN geen taxonomy node matcht met voldoende confidence (< 0.5),
THEN SHALL `taxonomy_node_ids` worden opgeslagen als lege lijst `[]`.

WHEN een knowledge base GEEN taxonomy nodes heeft,
THEN SHALL classificatie worden overgeslagen (geen LLM call), `taxonomy_node_ids` SHALL worden weggelaten uit chunks,
AND het document SHALL worden toegevoegd aan de "unmatched" batch voor proposal-generatie.

De classificatie SHALL `klai-fast` gebruiken met:
- Input: document titel + eerste 500 tekens content + lijst taxonomy nodes (id, name, description)
- Output: `{ "nodes": [{"node_id": int, "confidence": float}], "tags": ["str"], "reasoning": str }`
- Maximum 5 nodes per document (gesorteerd op confidence, threshold >= 0.5)
- Timeout: 5 seconden; bij timeout lege lijst opslaan zonder ingest te blokkeren

### R2 -- Qdrant payload migratie

Het systeem SHALL:
1. Een `keyword`-type payload index aanmaken op `taxonomy_node_ids` (array) in de `klai_knowledge` collection
2. Een `keyword`-type payload index aanmaken op `tags` (array) in de `klai_knowledge` collection
3. De oude `taxonomy_node_id` (single) index behouden voor backward compatibility gedurende de migratiefase

IF een chunk nog het oude `taxonomy_node_id` veld heeft en het nieuwe `taxonomy_node_ids` veld ontbreekt,
THEN SHALL de retrieval filter het oude veld als fallback gebruiken.

### R3 -- Retrieval filter update

WHEN een retrieve request `taxonomy_node_ids: list[int]` bevat (niet-leeg),
THEN SHALL de Qdrant query een `MatchAny` filter toepassen op het `taxonomy_node_ids` veld
(met fallback naar `taxonomy_node_id` voor niet-gemigreerde chunks).

WHEN een retrieve request `tags: list[str]` bevat (niet-leeg),
THEN SHALL de Qdrant query een `MatchAny` filter toepassen op het `tags` veld.

IF beide filters afwezig of leeg zijn,
THEN SHALL geen extra filtering worden toegepast (bestaand gedrag behouden).

### R4 -- Node descriptions

Het systeem SHALL een `description: str | None` veld toevoegen aan `PortalTaxonomyNode`.

WHEN een taxonomy node wordt aangemaakt via de governance queue (proposal approved),
THEN SHALL het systeem een description genereren met `klai-fast`
op basis van de node naam, parent node naam, en eventuele sample documenten.

WHEN de taxonomy bootstrap draait op een bestaande KB,
THEN SHALL voor elke voorgestelde node ook een description worden gegenereerd.

De description SHALL:
- Maximaal 200 tekens lang zijn
- Beschrijven welke vragen en content bij deze node horen
- In dezelfde taal zijn als de KB content
- Door een reviewer bewerkt kunnen worden via de governance queue

### R5 -- Free tags als first-class concept

WHEN een document wordt geingested,
THEN SHALL de classifier naast taxonomy nodes ook een lijst van maximaal 5 vrije tags voorstellen,
AND deze tags SHALL worden opgeslagen in het `tags: list[str]` veld op Qdrant chunks.

WHEN tags uit YAML frontmatter worden gelezen (bestaand gedrag),
THEN SHALL deze tags worden samengevoegd met LLM-gesuggereerde tags (frontmatter heeft voorrang bij duplicaten).

Het systeem SHALL een tag governance flow ondersteunen:
- Tags die door de LLM worden gesuggereerd krijgen status `suggested`
- Een reviewer kan tags accepteren, verwijderen, of hernoemen
- Geaccepteerde tags worden beschikbaar als retrieval filter
- De governance queue hergebruikt het bestaande `PortalTaxonomyProposal` model met `proposal_type = 'tag'`

### R6 -- Gap taxonomy-classificatie

WHEN een gap event wordt opgeslagen in `portal_retrieval_gaps`,
AND het gap event `taxonomy_node_ids` bevat (doorgestuurd vanuit LiteLLM hook, zie SPEC-KB-021 R6),
THEN SHALL het portal deze `taxonomy_node_ids` opslaan op de gap record.

WHEN een gap event GEEN `taxonomy_node_ids` bevat,
THEN SHALL het portal de gap query classificeren tegen de taxonomy nodes van de `nearest_kb_slug`
met dezelfde classifier logica als bij ingest, en het resultaat opslaan.

Het systeem SHALL een nieuw veld `taxonomy_node_ids: list[int]` toevoegen aan `PortalRetrievalGap` (JSONB of integer array).

### R7 -- Gap dashboard per taxonomy node

WHEN een admin de gaps opvraagt via `GET /api/app/gaps`,
THEN SHALL het endpoint een optionele `taxonomy_node_id: int` filter parameter accepteren
AND gaps filteren op die specifieke taxonomy node.

Het systeem SHALL een nieuw endpoint bieden: `GET /api/app/gaps/by-taxonomy`
dat gaps aggregeert per taxonomy node en retourneert:
```json
[
  {
    "taxonomy_node_id": 5,
    "taxonomy_node_name": "Billing > Subscriptions",
    "open_gaps": 47,
    "frequency_per_day": 3.2,
    "priority": "high"
  }
]
```

Priority SHALL worden berekend op basis van frequentie:
- `>= 2.0/dag` = high
- `>= 0.5/dag` = medium
- `< 0.5/dag` = low

### R8 -- Coverage dashboard

Het systeem SHALL een endpoint bieden: `GET /api/app/knowledge-bases/{kb_slug}/taxonomy/coverage`
dat per taxonomy node retourneert:
```json
[
  {
    "taxonomy_node_id": 5,
    "taxonomy_node_name": "Billing",
    "chunk_count": 340,
    "document_count": 28,
    "percentage_of_total": 14.2,
    "gap_count": 47,
    "health": "attention_needed"
  }
]
```

Health status SHALL worden berekend:
- `healthy`: >= 10 chunks AND gap_count < 5
- `attention_needed`: < 10 chunks OR gap_count >= 5
- `empty`: 0 chunks
- `untagged`: percentage chunks zonder `taxonomy_node_ids` in de KB

Het endpoint SHALL ook een `untagged_count` en `untagged_percentage` retourneren voor de gehele KB.

### R9 -- Backfill migratie

Het bestaande backfill endpoint (`POST /ingest/v1/taxonomy/backfill`) SHALL worden uitgebreid:

WHEN aangeroepen met de bestaande parameters,
THEN SHALL het endpoint:
1. Chunks met het oude `taxonomy_node_id` veld migreren naar `taxonomy_node_ids: [<old_value>]`
2. Chunks zonder enige taxonomy classificatie opnieuw classificeren met de multi-label classifier
3. Voor alle verwerkte chunks ook tags genereren via de classifier

Het endpoint SHALL idempotent blijven: chunks die al `taxonomy_node_ids` hebben worden overgeslagen.

### R10 -- Constraints

Het systeem SHALL NIET het bestaande retrieval-gedrag breken wanneer chunks nog het oude `taxonomy_node_id` formaat hebben (backward compatibility).
Het systeem SHALL NIET P95 retrieval latency verhogen met meer dan 20ms.
Het systeem SHALL NIET meer dan 1 LLM call per document uitvoeren (multi-label + tags in dezelfde call).
Het systeem SHALL NIET duplicate tag-proposals indienen (zelfde tag, zelfde KB) binnen een 24-uurs venster.
Het systeem SHALL NIET US-cloud model namen gebruiken; alleen `klai-fast` als classifier model.

---

## Data Model Wijzigingen

### Qdrant chunk payload (wijzigingen)

```
taxonomy_node_ids: list[int]   (nieuw, vervangt taxonomy_node_id)
                                [] = geclassificeerd, geen match
                                [5, 7] = matched naar meerdere nodes
                                afwezig = KB heeft geen taxonomy nodes

tags: list[str]                 (nieuw als geindexeerd veld)
                                ["sso", "okta", "saml"]
                                Combinatie van frontmatter tags + LLM-gesuggereerde tags

taxonomy_node_id: int | null    (legacy, read-only, niet meer geschreven na migratie)
```

### PortalTaxonomyNode (toevoeging)

```python
description: Mapped[str | None] = mapped_column(Text, nullable=True)
```

### PortalRetrievalGap (toevoeging)

```python
taxonomy_node_ids: Mapped[list[int] | None] = mapped_column(ARRAY(Integer), nullable=True)
```

### RetrieveRequest (toevoeging)

```python
tags: list[str] | None = None  # filter op free tags
```

---

## Aannames

| Aanname | Confidence | Risico als fout |
|---|---|---|
| `klai-fast` kan multi-label classificatie + tag-suggestie in 1 call | Hoog | Kwaliteitsverlies; mitigatie: apart call voor tags |
| Node descriptions van max 200 tekens zijn voldoende voor classificatie-context | Hoog | Slechte classificatie; mitigatie: descriptions uitbreiden |
| JSONB array op `portal_retrieval_gaps` is performant genoeg voor aggregatie | Hoog | Langzame queries; mitigatie: GIN index toevoegen |
| Backfill kan `taxonomy_node_id -> taxonomy_node_ids` migreren zonder downtime | Hoog | Qdrant set_payload is safe voor bestaande punten |
| Tag governance via bestaand proposal model volstaat | Medium | Model te generiek; mitigatie: apart tag-model als nodig |
| Coverage queries op Qdrant (GROUP BY taxonomy_node_ids) zijn snel genoeg | Medium | Langzaam bij grote collecties; mitigatie: Qdrant scroll + aggregatie in Python |
