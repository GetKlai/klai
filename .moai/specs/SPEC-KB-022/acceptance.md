---
id: SPEC-KB-022
phase: acceptance
---

# Acceptatiecriteria -- SPEC-KB-022: Taxonomy V2

---

## AC-1: Multi-label chunk tagging

**Given** een knowledge base met taxonomy nodes "Billing" (id=5), "Setup" (id=7), en "Security" (id=9)
**And** een document met titel "SSO configuratie voor enterprise billing"
**When** het document wordt geingested
**Then** de Qdrant chunks voor dat document bevatten `taxonomy_node_ids: [7, 5]` (of subset, afh. van confidence)
**And** het veld `taxonomy_node_ids` is een array van integers
**And** elk id in de array komt voor in de taxonomy nodes van de KB

---

## AC-2: Geen match bij lage confidence

**Given** een knowledge base met taxonomy nodes
**And** een document dat niet past bij enige node (bijv. een intern memo)
**When** het document wordt geingested en alle node matches scoren < 0.5
**Then** de chunks bevatten `taxonomy_node_ids: []` (lege lijst)
**And** het document wordt toegevoegd aan de unmatched batch

---

## AC-3: KB zonder taxonomy nodes

**Given** een knowledge base zonder taxonomy nodes
**When** een document wordt geingested
**Then** het `taxonomy_node_ids` veld is NIET aanwezig in de Qdrant chunk payload
**And** er vindt geen LLM classificatie-call plaats

---

## AC-4: Backward-compatible retrieval

**Given** een KB met gemixte chunks:
  - Oude chunks met `taxonomy_node_id: 5` (int, pre-migratie)
  - Nieuwe chunks met `taxonomy_node_ids: [5, 7]` (array, post-migratie)
**When** een retrieve request `taxonomy_node_ids: [5]` bevat
**Then** BEIDE typen chunks worden geretourneerd (fallback logica werkt)
**And** de retrieval latency stijgt niet meer dan 20ms (P95)

---

## AC-5: Tags bij ingest

**Given** een document met YAML frontmatter `tags: ["sso", "enterprise"]`
**And** de LLM classifier suggereert tags `["sso", "okta", "authentication"]`
**When** het document wordt geingested
**Then** de Qdrant chunks bevatten `tags: ["sso", "enterprise", "okta", "authentication"]`
**And** frontmatter tags worden behouden (geen overschrijving)
**And** duplicaten zijn verwijderd

---

## AC-6: Tag retrieval filter

**Given** chunks met `tags: ["sso", "okta"]` en chunks met `tags: ["billing", "invoice"]`
**When** een retrieve request `tags: ["sso"]` bevat
**Then** alleen chunks met "sso" in hun tags worden geretourneerd
**And** chunks zonder "sso" tag worden niet geretourneerd

---

## AC-7: Node description opslag en gebruik

**Given** een taxonomy node "Billing > Subscriptions" met description "Vragen over abonnementen, prijswijzigingen, annuleringen en verlengingen"
**When** de classifier een document classificeert
**Then** de classifier prompt bevat de node description naast de node naam
**And** de description is maximaal 200 tekens

---

## AC-8: Gap taxonomy-classificatie -- met taxonomy_node_ids in event

**Given** een retrieval gap event wordt ontvangen met `taxonomy_node_ids: [5, 7]` vanuit het LiteLLM hook
**When** het portal de gap opslaat
**Then** het `PortalRetrievalGap` record bevat `taxonomy_node_ids: [5, 7]`

---

## AC-9: Gap taxonomy-classificatie -- zonder taxonomy_node_ids in event

**Given** een retrieval gap event wordt ontvangen ZONDER `taxonomy_node_ids`
**And** de gap heeft `nearest_kb_slug: "my-kb"` en die KB heeft taxonomy nodes
**When** het portal de gap opslaat
**Then** het portal classificeert de `query_text` tegen de taxonomy nodes van "my-kb"
**And** het resultaat wordt opgeslagen in `taxonomy_node_ids` op de gap record
**And** de gap opslag wordt NIET geblokkeerd door een classificatie-timeout

---

## AC-10: Gap dashboard per taxonomy node

**Given** 47 open gaps geclassificeerd naar node "Billing > Subscriptions" (id=5)
**And** 31 open gaps geclassificeerd naar node "Setup > SSO" (id=7)
**When** een admin `GET /api/app/gaps/by-taxonomy` aanroept
**Then** de response bevat:
  - `{ "taxonomy_node_id": 5, "taxonomy_node_name": "Billing > Subscriptions", "open_gaps": 47, "priority": "high" }`
  - `{ "taxonomy_node_id": 7, "taxonomy_node_name": "Setup > SSO", "open_gaps": 31, "priority": "high" }`
**And** de resultaten zijn gesorteerd op `open_gaps` aflopend

---

## AC-11: Gap filter op taxonomy node

**Given** de gap dataset uit AC-10
**When** een admin `GET /api/app/gaps?taxonomy_node_id=5` aanroept
**Then** alleen gaps geclassificeerd naar node 5 worden geretourneerd

---

## AC-12: Coverage dashboard

**Given** een KB "my-kb" met:
  - Taxonomy node "Billing" (id=5): 340 chunks, 28 documenten, 47 open gaps
  - Taxonomy node "Setup" (id=7): 120 chunks, 10 documenten, 3 open gaps
  - 200 chunks zonder taxonomy_node_ids
  - Totaal: 660 chunks
**When** een admin `GET /api/app/knowledge-bases/my-kb/taxonomy/coverage` aanroept
**Then** de response bevat:
  - Node 5: `chunk_count: 340, document_count: 28, percentage_of_total: 51.5, gap_count: 47, health: "attention_needed"`
  - Node 7: `chunk_count: 120, document_count: 10, percentage_of_total: 18.2, gap_count: 3, health: "healthy"`
  - `untagged_count: 200, untagged_percentage: 30.3`

---

## AC-13: Backfill migratie

**Given** een KB met 100 bestaande chunks:
  - 60 met `taxonomy_node_id: 5` (oud formaat)
  - 40 zonder enige taxonomy classificatie
**When** het backfill endpoint wordt aangeroepen
**Then** de 60 chunks krijgen `taxonomy_node_ids: [5]` (migratie van oud naar nieuw)
**And** de 40 chunks worden opnieuw geclassificeerd met de multi-label classifier
**And** alle 100 chunks krijgen tags gesuggereerd
**And** de response bevat `{ "migrated": 60, "classified": 40, "tagged": 100, "skipped": 0 }`

---

## AC-14: Backfill idempotentie

**Given** een KB waarop backfill al is uitgevoerd
**When** het backfill endpoint opnieuw wordt aangeroepen
**Then** geen chunks worden opnieuw verwerkt
**And** de response bevat `{ "migrated": 0, "classified": 0, "tagged": 0, "skipped": 100 }`

---

## AC-15: Geen US-cloud modellen

**Given** enige taxonomy classificatie, description generatie, of tag suggestie call
**When** de LLM wordt aangeroepen
**Then** het model is `klai-fast` (of andere klai-* tier alias)
**And** geen `gpt-*`, `claude-*`, of `text-embedding-*` model namen worden gebruikt

---

## Definition of Done

- [ ] Alle bovenstaande scenario's zijn handmatig of automatisch geverifieerd
- [ ] Backward compatibility: bestaande retrieval werkt ongewijzigd met pre-migratie chunks
- [ ] P95 retrieval latency niet meer dan 20ms gestegen
- [ ] Alembic migraties zijn reversible (downgrade pad)
- [ ] Unit tests dekken classifier, upsert, retrieval filter, gap classificatie
- [ ] Geen US-cloud model namen in enige code, config, of prompt
