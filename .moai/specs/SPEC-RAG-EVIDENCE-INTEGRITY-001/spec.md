---
id: SPEC-RAG-EVIDENCE-INTEGRITY-001
version: 0.1.2
status: draft
created: 2026-07-07
updated: 2026-07-07
author: Mark Vletter
priority: high
issue_number: 0
---

# SPEC-RAG-EVIDENCE-INTEGRITY-001: Integriteit van de ranking→evidence→citation-keten

## HISTORY

| Versie | Datum | Wijziging |
|---|---|---|
| 0.1.2 | 2026-07-07 | Review-2 verwerkt: `rescue_retrieval_ratio` 0.5→0.4 (motiverende Zendesk-casus heeft ratio 0.4441 en viel buiten de rescue), REQ-RANK-02 herschreven (authority-boost wordt uitgefaseerd — hij had ook vóór deze SPEC geen recall-effect), 1-bron-cijfer gepreciseerd (63% rendered / 13% nul), request_id-capture via X-Request-ID response-header geconcretiseerd, exclusion 8 toegevoegd (taxonomy-activering alle-KB's-scope) |
| 0.1.1 | 2026-07-07 | Review-1 defecten verwerkt: AC's voor OBS-03/04, SRC-04 naar EARS-Optional, BRIDGE-02 gedefinieerd (skip vervalt, geen merk-heuristiek), symbolen gekwalificeerd, Fase-5-gate gedefinieerd, boosts in REQ-RANK-01 |
| 0.1.0 | 2026-07-07 | Eerste draft op basis van Redcactus/Voys-analyse (zie research.md) |

## Overzicht

Productie-analyse (2026-07-07, zie `research.md`) toont vijf generieke defecten
in de keten retrieval → rerank → serving-order → evidence pack → LLM-context →
citatie-render. Netto-effect: goed geïndexeerde, relevante bronnen bereiken de
LLM-context niet of worden onzichtbaar in het citatiepaneel; 63% van de
requests rendert exact 1 bron en 13% rendert er 0 (samen 76% ≤ 1 bron;
`rendered_sources`-veld, 30d-data); het afwijzingslabel
`max_sources_exceeded` is in 0 van 302 gevallen de echte oorzaak (het
geconfigureerde maximum van 4 zat nooit vol).

Deze SPEC herstelt de keten generiek — zonder bron-specifieke regels — in vijf
requirement-modules: observability, bron-identiteit, citatie-kalibratie,
ranking-contract en brand-bridging. Backfill/datamigratie is het expliciete
sluitstuk. Het aspect-bewuste evidence pack is bewust uitgesloten
(vervolg-SPEC).

## Scope

**Services:** klai-retrieval-api, klai-knowledge-ingest, deploy/litellm (pad A),
klai-libs/citations (gedeeld door pad A/B/C).

**Chatpaden geraakt door REQ-3:** pad A (LibreChat→LiteLLM-hook), pad B
(portal `citations.py`, partner/widget), pad C (retrieval-api `synthesis.py`,
dormant). Alle drie krijgen dezelfde selector-wijziging via `klai_citations`.

---

## Requirements (EARS)

### Module 1 — REQ-OBS: Observability en eerlijke reden-labels

- **REQ-OBS-01 (Ubiquitous):** THE citation-selector (`klai_citations`) SHALL
  per afgewezen bron de werkelijke afwijsreden rapporteren, waarbij het huidige
  verzamel-label `max_sources_exceeded` wordt gesplitst in:
  `below_keep_ratio`, `answer_length_clamp`, `max_reached`.
  Bestaande redenen (`query_not_supported`, `answer_not_supported`,
  `max_sources_zero`) blijven ongewijzigd.
- **REQ-OBS-02 (Event-driven):** WHEN de LiteLLM-hook een citatie-render logt
  (`kb_citations_rendered_structured`), THE system SHALL het `request_id` van
  de bijbehorende retrieval-call meeloggen, zodat render- en
  retrieval-decision-records via één `request_id` correleerbaar zijn. IF het
  `request_id` niet beschikbaar is (bv. retrieval faalde vóór respons), THEN
  het veld SHALL aanwezig zijn met waarde `null` — nooit weggelaten.
- **REQ-OBS-03 (Event-driven):** WHEN de query-rewrite draait of wordt
  geskipt, THE hook SHALL prompt-variant (`plain` | `classify`) en skip-reden
  als gestructureerde metadata loggen, onafhankelijk van telemetry_level
  (inhoud van de query blijft gated per SPEC-PRIVACY-QUERY-SHADOW-001).
- **REQ-OBS-04 (State-driven):** WHILE citatiebeslissingen worden gelogd, THE
  system SHALL de reden-verdeling als queryable metric beschikbaar maken
  (VictoriaLogs-veld voldoet; Grafana-paneel op reden-verdeling per week).

### Module 2 — REQ-SRC: Bron-identiteit voor bulk-crawls (+ backfill als sluitstuk)

- **REQ-SRC-01 (Event-driven):** WHEN de bulk-crawl pipeline een pagina ingest
  (`_ingest_crawl_result`), THE system SHALL `source_domain` zetten op
  `urlparse(url).netloc`, gelijk aan het bestaande single-page pad
  (`routes/crawl.py:853`).
- **REQ-SRC-02 (Ubiquitous):** THE `compute_source_label()` SHALL voor
  crawl-chunks met `source_domain` het domein als `source_label` opleveren
  (bestaand gedrag, geborgd met regressietest die het bulk-pad end-to-end
  dekt, inclusief de Procrastinate `extra_payload` passthrough).
- **REQ-SRC-03 (Unwanted):** IF een bestaande crawl-chunk `source_domain=null`
  heeft maar wél een `source_url`, THEN de backfill-migratie SHALL
  `source_domain` en `source_label` afleiden uit `source_url` — uitgevoerd als
  LAATSTE fase van deze SPEC, pas nadat REQ-SRC-01/02 en REQ-RANK in productie
  geverifieerd zijn.
- **REQ-SRC-04 (Optional):** WHERE de backfill-sweep met de optie
  `--clean-stale-taxonomy` wordt uitgevoerd, THE sweep SHALL uit
  `taxonomy_node_ids`-payloads alle node-IDs verwijderen die niet in
  `portal_taxonomy_nodes` bestaan, en SHALL het aantal opgeschoonde chunks
  per KB rapporteren.

### Module 3 — REQ-CIT: Citatie-overflow empirisch kalibreren

- **REQ-CIT-01 (Event-driven):** WHEN een bron door de keep-ratio of
  antwoordlengte-clamp in overflow belandt, THE selector SHALL een
  rescue-evaluatie uitvoeren: de bron wordt alsnog geselecteerd als
  `query_score > 0` EN `answer_score >= rescue_answer_score_threshold` EN
  `retrieval_score >= rescue_retrieval_ratio × beste_retrieval_score`, binnen
  het effectieve maximum.
- **REQ-CIT-02 (Ubiquitous):** THE rescue-drempels SHALL configureerbaar zijn
  met defaults uit de 30d-kalibratie: `rescue_answer_score_threshold=19`
  (= p75 van geselecteerde bronnen), `rescue_retrieval_ratio=0.4`.
  Waarom 0.4 en niet 0.5: de motiverende Zendesk-casus (Redcactus
  zendesk-talk, answer_score 19, query_score 1) heeft ratio
  0.4369/0.9836 = 0.4441 en valt bij 0.5 buiten de rescue; de curve is vlak
  (0.5 → +22, 0.4 → +28, 0.3 → +30 rescues op 30d). De drempelkeuze is als
  kalibratie-notitie in plan.md gedocumenteerd en wordt periodiek herijkt
  als percentiel, niet als vaste constante.
- **REQ-CIT-03 (State-driven):** WHILE de feature-flag
  `citation_rescue_mode=shadow` (default) actief is, THE selector SHALL de
  rescue-beslissing alleen loggen (`would_rescue=true` per bron) zonder de
  zichtbare selectie te wijzigen; `citation_rescue_mode=active` activeert het
  gedrag. Uitrol: minimaal 7 dagen shadow, dan review van de gelogde rescues.
- **REQ-CIT-04 (Unwanted):** IF de rescue-regel actief is, THEN het aantal
  zichtbare bronnen SHALL nooit `klai_citations._DEFAULT_MAX_SOURCES` (= 4,
  de citatie-laag) overschrijden en de volgorde van reeds geselecteerde
  bronnen SHALL ongewijzigd blijven. NB: dit is een ander symbool dan
  `evidence_pack._DEFAULT_MAX_SOURCES` (= 3, de context-laag); de rescue kan
  dus nooit meer bronnen tonen dan het evidence pack aanlevert.

### Module 4 — REQ-RANK: Ranking-contract herstellen

- **REQ-RANK-01 (Ubiquitous):** THE retrieval-pipeline SHALL na de reranker
  één expliciet rankingveld hanteren (`final_rank_score`, geïnitialiseerd op
  `reranker_score`, fallback `score` wanneer de reranker uitstaat of faalt);
  `source_aware_select` (diversify-sort) en `quality_boost` SHALL hierop
  sorteren in plaats van op het RRF-veld `score`, en de bestaande post-rerank
  boosts (page-context boost, link-expand boost) SHALL `final_rank_score`
  muteren in plaats van `reranker_score`/`score`.
- **REQ-RANK-02 (Ubiquitous):** THE authority-boost
  (`link_authority_boost × log(1+incoming)`) SHALL worden uitgefaseerd als
  ranking-signaal. Feitelijke basis: de boost beïnvloedt vandaag óók de
  kandidaat-recall niet — `retrieve.py` muteert `score` ná de RRF-merge
  zonder hersortering, en de rerank-input blijft `raw_results[:20]` in
  RRF-volgorde; zijn enige effect is het verstoren van de servingvolgorde
  ná het reranken. WHILE `ranking_contract_mode=shadow` actief is, THE
  pipeline SHALL de oude ordening (inclusief boost) blijven berekenen voor
  de shadow-vergelijking; bij de flip naar `active` SHALL de boost-code
  worden verwijderd (geen oud+nieuw naast elkaar). Her-introductie als
  échte pre-rerank recall-boost (hersorteren vóór de rerank-slice,
  genormaliseerd/gecapt t.o.v. de RRF-schaal) is expliciet vervolgwerk,
  niet deze SPEC.
- **REQ-RANK-03 (Ubiquitous):** THE `quality_boost` SHALL zijn boost toepassen
  op `final_rank_score` (begrensd, zoals nu ±10%) en SHALL de lijst niet
  hersorteren wanneer geen enkele chunk de cold-start-drempel haalt.
- **REQ-RANK-04 (State-driven):** WHILE de flag `ranking_contract_mode=shadow`
  (default) actief is, THE pipeline SHALL beide ordeningen berekenen en per
  request loggen: served top-5 chunk-ids en evidence-pack bronnen onder de
  oude én nieuwe ordening (patroon: bestaande `shadow_eval`). Activatie naar
  `active` pas na analyse van minimaal 7 dagen shadow-data.
- **REQ-RANK-05 (Unwanted):** IF de reranker faalt (fallback met
  `reranker_score=None`), THEN de pipeline SHALL terugvallen op de huidige
  score-ordening zonder gedragswijziging.

### Module 5 — REQ-BRIDGE: Brand-bridging ontkoppelen van taxonomie

- **REQ-BRIDGE-01 (Event-driven):** WHEN de query-rewrite draait zonder
  beschikbare taxonomy-tree, THE hook SHALL alsnog de brand-bridging
  instructie toepassen (derde-partij merk → 2–4 categorie/partner-termen in
  de rewrite), als onderdeel van de plain-rewrite prompt.
- **REQ-BRIDGE-02 (Event-driven):** WHEN een eerste vraag zonder
  conversation-history en zonder taxonomy-tree binnenkomt en KB-retrieval van
  toepassing is, THE hook SHALL de rewrite uitvoeren — de huidige
  `no_history_no_tree` skip vervalt volledig. Er wordt bewust GEEN
  deterministische merk-detectie-heuristiek gebouwd (lowercase merknamen als
  "hubspot" zijn niet betrouwbaar herkenbaar zonder lexicon); de LLM-rewrite
  zelf beslist of bridging van toepassing is. Kosten: maximaal één extra
  rewrite-call (bestaand model, bestaande 1.5s timeout, fail-open) per
  first-turn vraag.
- **REQ-BRIDGE-03 (Unwanted):** IF de rewrite de query verandert, THEN de
  bestaande raw-query RRF-leg SHALL de letterlijke gebruikersquery blijven
  meenemen (bestaand gedrag `raw_query_leg_applied` — regressietest borgt dit).

---

## Delta-markers (brownfield)

### [DELTA] klai-retrieval-api
- [MODIFY] `retrieval_api/services/diversity.py` — diversify-sort op `final_rank_score` (REQ-RANK-01)
- [MODIFY] `retrieval_api/quality_boost.py` — boost + sort op `final_rank_score`, geen no-op re-sort (REQ-RANK-03)
- [MODIFY] `retrieval_api/api/retrieve.py` — `final_rank_score` introduceren, authority-boost scheiden, shadow-log (REQ-RANK-01/02/04)
- [EXISTING] `retrieval_api/services/reranker.py` — ongewijzigd; characterization tests voor fallback-pad (REQ-RANK-05)
- [EXISTING] `retrieval_api/services/evidence_pack.py` — ongewijzigd (leest servingvolgorde; gedrag verandert indirect via REQ-RANK)

### [DELTA] klai-libs/citations
- [MODIFY] `klai_citations/__init__.py` — reden-splitsing (REQ-OBS-01), rescue-regel + flag (REQ-CIT); geldt voor pad A, B en C

### [DELTA] deploy/litellm
- [MODIFY] `klai_kb_citation_render.py` — request_id in render-log (REQ-OBS-02)
- [MODIFY] `klai_kb_query_rewrite.py` — bridging in plain-rewrite, skip-logica, metadata-log (REQ-BRIDGE, REQ-OBS-03)

### [DELTA] klai-knowledge-ingest
- [MODIFY] `knowledge_ingest/adapters/crawler.py` — `source_domain` propagatie (REQ-SRC-01; kandidaat-diff bestaat)
- [NEW] backfill-script/migratie voor `source_domain`/`source_label` + stale taxonomy cleanup (REQ-SRC-03/04, sluitstuk)

---

## Exclusions (wat NIET wordt gebouwd)

1. **Aspect-bewust evidence pack** (coverage per query-aspect i.p.v.
   eerste-3-unieke-URLs) — groot ontwerpwerk, eigen vervolg-SPEC nadat het
   ranking-contract (REQ-RANK) live en gevalideerd is.
2. Geen bron-specifieke boosts, geen Redcactus-regels, geen KB-herindeling.
3. Geen harde tag/taxonomy-filters in het chatpad (bridge-bronnen sneuvelen).
4. Geen verhoging van `max_sources`/`_DEFAULT_MAX_SOURCES` als losse knop.
5. Geen query-vertaallaag vóór retrieval; geen tweede reranker.
6. Geen wijziging aan graph-search of link-expansion (dead-weight-vraag is
   apart traject; meetinstrumentatie bestaat al).
7. Geen wijziging aan `reranker_candidates` (rerank-venster 20/60) in deze
   SPEC — kandidaat voor het aspect-pack vervolg; hier alleen gedocumenteerd.
8. Geen taxonomy-activering bij "alle KB's"-scope (`kbs_in_scope=[]` blijft
   de taxonomy-classify skippen) — hoort bij het aspect-pack vervolg-SPEC;
   hier gedocumenteerd zodat het niet zoekraakt.

## mx_plan

- `@MX:ANCHOR` op `source_aware_select` en `quality_boost` (fan-in via
  retrieve.py; invariant: post-rerank volgorde = `final_rank_score` desc).
- `@MX:ANCHOR` op `_split_selected_by_quality` (contract: reden-labels +
  rescue-flag semantiek; gedeeld door 3 chatpaden).
- `@MX:NOTE` op `_ingest_crawl_result` (source_domain verplicht voor
  source_label; verwijzing naar deze SPEC).
- `@MX:WARN` op de backfill-migratie (RLS/batch-gedrag Qdrant; alleen als
  operator-stap draaien).
