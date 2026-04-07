# SPEC-KB-027 — Acceptance Criteria

## Scenario 1: Taxonomy-aware retrieval bij voldoende coverage

**Given** een KB met 10 chunks waarvan 8 gecategoriseerd (80% coverage)
**And** de query "Hoe stel ik SSO in?" classificeert naar node_id=5 ("Setup > SSO")
**When** de research-api een retrieval request uitvoert
**Then** de retrieval-api ontvangt `taxonomy_node_ids=[5]` in het request
**And** de retrieval-api logt `taxonomy_filter_applied=true, node_ids=[5]`
**And** alleen chunks met `taxonomy_node_ids` containing 5 worden opgehaald

## Scenario 2: Taxonomy filter overgeslagen bij lage coverage

**Given** een KB met 10 chunks waarvan 2 gecategoriseerd (20% coverage < 30% drempel)
**When** de research-api een retrieval request uitvoert
**Then** de retrieval-api ontvangt `taxonomy_node_ids=None` (geen filter)
**And** de research-api logt `taxonomy_filter_skipped=true, reason=low_coverage, coverage=0.20`
**And** retrieval werkt identiek aan het huidige gedrag

## Scenario 3: Taxonomy filter overgeslagen bij classify timeout

**Given** het knowledge-ingest classify endpoint reageert niet binnen 3 seconden
**When** de research-api een retrieval request uitvoert
**Then** de retrieval-api ontvangt `taxonomy_node_ids=None`
**And** de research-api logt `taxonomy_classify_timeout=true`
**And** de gebruiker merkt geen vertraging >3s extra ten opzichte van normale retrieval

## Scenario 4: Backfill genereert proposal voor cluster van ongematchde documenten

**Given** een KB met 6 ongeclassificeerde documenten
**And** de taxonomy heeft 2 nodes maar geen van de 6 docs match (confidence < 0.5)
**When** een backfill job wordt gestart voor deze KB
**Then** na Phase 2 wordt `maybe_generate_proposal` aangeroepen met 6 `DocumentSummary` objecten
**And** er verschijnt 1 nieuw voorstel in de portal review queue
**And** het voorstel heeft een niet-lege `title` en `description`

## Scenario 5: Dead code verwijderd uit ingest route

**Given** een ingest request voor een document dat niet classificeert
**When** de ingest endpoint het document verwerkt
**Then** er wordt GEEN `asyncio.create_task` aangemaakt voor `maybe_generate_proposal`
**And** `grep -r "maybe_generate_proposal" klai-knowledge-ingest/knowledge_ingest/routes/` geeft geen resultaat

## Scenario 6: doc_count kolom niet meer aanwezig

**Given** de migratie is uitgevoerd
**When** `\d portal_taxonomy_nodes` wordt uitgevoerd
**Then** is `doc_count` niet aanwezig in de kolomlijst
**And** `GET /api/app/knowledge-bases/{slug}/taxonomy/nodes` bevat geen `doc_count` veld in de response items

## Scenario 7: Coverage dashboard nog correct na doc_count verwijdering

**Given** een KB met taxonomy nodes en 50 gecategoriseerde chunks
**When** de coverage dashboard wordt geladen
**Then** toont elke node een chunk count groter dan 0 (opgehaald uit Qdrant)
**And** de totale chunk count klopt met de werkelijke Qdrant count
**And** er is geen NullPointerException of leeg veld waar een count verwacht wordt

## Edge cases

- **Lege taxonomy:** KB heeft 0 taxonomy nodes → classify stap overgeslagen, retrieval normaal
- **Alle chunks ongeclassificeerd:** coverage = 0% → filter overgeslagen, retrieval normaal
- **Classify retourneert ongeldige node IDs:** node IDs die niet in KB's taxonomy zitten → filter overgeslagen (veiligheidscheck)
- **Backfill met alle gematchde docs:** `unmatched_summaries = []` → `maybe_generate_proposal` niet gecalled

## Performance criteria

- Taxonomy classify + coverage parallel call: max 3 seconden extra latency, P95
- Bij timeout: retrieval niet geblokkeerd — gaat door zonder filter
- Geen merkbare latency impact wanneer taxonomy filter overgeslagen wordt (< 5ms overhead)
