# Research: SPEC-RAG-EVIDENCE-INTEGRITY-001

Datum: 2026-07-07
Bron: holistisch onderzoek Redcactus/Voys/HubSpot/Zendesk retrieval-casus
(brief: `.context/handoffs/redcactus-retrieval-architecture-brief.md` in de
redcactus-wiki-sources workspace). Alle bevindingen geverifieerd tegen
broncode op `origin/main`, 30 dagen citatiedata (887 beslissingen, 301
requests) en VictoriaLogs-productielogs.

## Samenvatting

Vijf generieke defecten stapelen zich op in de keten
retrieval → rerank → serving-order → evidence pack → LLM-context → citatie-render.
Geen ervan is bron-specifiek; de Redcactus-casus raakt ze toevallig allemaal.

## Bevinding 1 — Bron-identiteit ontbreekt voor bulk-crawls

- Bulk-crawl ingest zet geen `source_domain`:
  `klai-knowledge-ingest/knowledge_ingest/adapters/crawler.py::_ingest_crawl_result`
  (regel ~960) stuurt `source_type="crawl"` zonder `source_domain`.
  Het single-page pad zet het wél: `routes/crawl.py:853`.
- `compute_source_label()` (`knowledge_ingest/source_label.py:22`) valt daardoor
  terug op `kb_slug` → help.voys.nl én wiki.redcactus.cloud heten beide `"support"`.
- Gevolgen:
  1. `source_aware_select` (`retrieval_api/services/diversity.py`) behandelt
     twee domeinen als één bron; `max_per_source=2` quota is betekenisloos.
  2. `_detect_mentioned_sources` (diversity.py:64) kan "redcactus" nooit
     matchen: label is `"support"` en dat woord staat in `STOP_WORDS` (regel 57).
  3. Observability: `top_source_labels=["support","support","notion"]` —
     bronfamilies zijn niet te onderscheiden in dashboards.
- Kandidaat-fix bestaat (worktree honolulu, ongetest, niet gemerged):
  `source_domain=urlparse(url).netloc` in `_ingest_crawl_result`.

## Bevinding 2 — Reranker-volgorde wordt post-rerank weggegooid

- `diversity.py:181` (diversify-modus):
  `selected.sort(key=lambda x: x.get("score", 0.0), reverse=True)` — sorteert
  op de RRF-fusiescore, niet op `reranker_score`.
- `retrieval_api/quality_boost.py:42`: `reranked.sort(key=lambda c: c["score"])`
  — draait ALTIJD, ook zonder actieve feedback-boost (cold-start guard grijpt
  alleen op de vermenigvuldiging, niet op de sort).
- Vóór het reranken is de authority-boost al in `score` gemengd
  (`retrieve.py:385`: `score += 0.05 * log(1+incoming_link_count)`).
  RRF-scores zijn ~0.016–0.03; de authority-term kan 0.1+ zijn → de
  servingvolgorde wordt gedomineerd door interne-linkpopulariteit.
- Downstream-effect: `build_evidence_pack` (`services/evidence_pack.py:194-213`)
  pakt de eerste 3 unieke source-keys in servingvolgorde
  (`_DEFAULT_MAX_SOURCES=3`). Welke bronnen LLM-context én citatiepaneel halen
  wordt dus bepaald door RRF + linkgraaf, niet door de cross-encoder.
- Verwant: rerank-venster is 20 van 60 kandidaten
  (`config.py:29-30`: `retrieval_candidates=60`, `reranker_candidates=20`).
  Kandidaat 21–60 wordt nooit gererankt.

## Bevinding 3 — Evidence pack is de enige LLM-context (harde trechter naar 3 bronnen)

- Hook vraagt `top_k=20` (`deploy/litellm/klai_knowledge.py`, `RETRIEVE_TOP_K`)
  maar injecteert alleen `evidence_pack_items_as_chunks(evidence_pack)`:
  `context_chunks = evidence_chunks`. Chunks buiten de top-3-bronnen bestaan
  niet voor de LLM.
- Relationele vragen ("HubSpot aan Freedom koppelen") vereisen bridge-bron
  (Voys/Notion: Freedom↔Bubble↔CRM) én doel-bron (Redcactus: HubSpot-config)
  tegelijk; de trechter selecteert 3 bronnen op één (vervormde) ranglijst
  zonder aspect-besef.

## Bevinding 4 — Citatie-overflow: `max_sources_exceeded` is een misnomer

Selector: `klai-libs/citations/klai_citations/__init__.py`
- `_split_selected_by_quality` (regel 601): bron #1 altijd; elke volgende bron
  alleen bij `retrieval_score >= 0.85 × beste` (`_EXTRA_SOURCE_KEEP_RATIO`,
  regel 72) én `answer_score>0` én `query_score>0`.
- `_effective_max_sources` (regel 591): antwoord ≤20 tokens → max 1 bron,
  ≤30 → max 2 (`_SIMPLE/_COMPLEX_ANSWER_SOURCE_TOKEN_LIMIT`).
- Alles wat sneuvelt krijgt reason `max_sources_exceeded` (regel 806).

Empirie (30d, 887 beslissingen, 301 requests; CSV in honolulu-workspace
`.context/historical_citation_decisions_30d.csv`):

| Feit | Waarde |
|---|---|
| MSE-afwijzingen waar het max (4) daadwerkelijk vol zat | 0 van 302 |
| Requests met exact 1 gerenderde bron (`rendered_sources`) | 189/301 (63%); nog eens 40/301 (13%) renderen 0 |
| Requests met exact 1 selected-decision | 218/301 (72%) |
| Mediane retrieval-ratio afgewezen bron t.o.v. beste | 0.37 |
| Afgewezen met ratio ≥ 0.85 (= lengte-clamp of query_score=0) | 10% |
| answer_score p75 van GETOONDE bronnen | 19 |
| Rescue-simulatie: q>0 ∧ answer≥19 ∧ ratio≥0.4 | +28 bronnen (op 311 selected; incl. de Zendesk-casus, ratio 0.4441) |
| Rescue-simulatie: q>0 ∧ answer≥19 ∧ ratio≥0.5 | +22 bronnen (Zendesk-casus valt hier buiten — daarom default 0.4) |
| Rescue-simulatie: alleen answer_score≥19 | +49 (waarvan 2 q=0) |

Productievoorbeelden (VictoriaLogs, `kb_citations_rendered_structured`):
- 09:28:31 (orig. HubSpot-vraag): getoond Voys Integraties (answer 6);
  verborgen Webhooks (answer 24) en Notion (answer 22) via ratio.
- 09:28:44 (Zendesk): Redcactus zendesk-talk verborgen met answer 19 > beide
  getoonde bronnen (2 en 7); retrieval 0.4369 vs best 0.9836.
- 10:03:40: Redcactus HubSpot-embedded verborgen op ratio 0.71 (answer 20)
  terwijl de vraag over HubSpot ging.
- 10:07:02: Redcactus zendesk-talk-embedded verborgen op ratio 0.96 — hier was
  de antwoordlengte-clamp (kort antwoord → max 1) de oorzaak.

## Bevinding 5 — Brand-bridging rewrite is dood gekoppeld aan taxonomie

- `_QUERY_REWRITE_AND_CLASSIFY_PROMPT` (`deploy/litellm/klai_kb_query_rewrite.py:48`)
  bevat brand-bridging (voorbeeld noemt letterlijk RedCactus) — precies wat
  relationele vragen nodig hebben.
- `rewrite_and_classify` (regel 334): zonder taxonomy-tree valt hij terug op
  de kale `_QUERY_REWRITE_PROMPT` (alleen pronomen-resolutie); zonder history
  én zonder tree wordt de rewrite geskipt (`no_history_no_tree`).
- Voor de Voys-org: nul `taxonomy_classify`-events in VictoriaLogs → bridging
  heeft nooit gedraaid in dit pad.

## Overige vaststellingen

- Tags/`entity_names`/`content_label` zitten in de Qdrant-payload maar spelen
  geen rol in dit chatpad; retrieval-api kán op taxonomie/tags filteren
  (`search.py:246-268`) maar krijgt ze nooit aangeleverd; stale taxonomy-node
  IDs (bv. node 29) aanwezig.
- Dead weight in deze flow: graph search 0 kandidaten; link-expansion voegde
  20 chunks toe, 0 in served top-k (decision records 07-07).
- Citation-render log (`kb_citations_rendered_structured`) bevat geen
  request_id → correlatie met retrieval decision records kan alleen via
  tijd/org. Retrieval-api echo't `X-Request-ID` wél in de response-header
  (`retrieval_api/logging_setup.py:98`), dus de hook kan hem uit de
  `/retrieve`-respons capturen — dat is het mechanisme voor REQ-OBS-02.

## Blast radius (shared helpers)

- `klai_citations` consumers: `deploy/litellm/klai_kb_citation_render.py` +
  `klai_knowledge.py` (pad A), `klai-portal/backend/app/services/citations.py`
  (pad B, partner/widget), `klai-retrieval-api/retrieval_api/services/synthesis.py`
  (pad C, dormant), `evidence_pack.py` (URL-normalisatie).
- `quality_boost` / `source_aware_select`: alleen retrieval-api `retrieve.py`.
- LiteLLM draait bind-mounted Python: deploy vereist
  `compose-up.sh --force-recreate litellm`
  (pitfall `bind-mount-content-vs-python-module-cache`).

## Referentie-implementaties

- Shadow-mode patroon: `EVIDENCE_SHADOW_MODE` in `retrieve.py:478-503`
  (SPEC-EVIDENCE-001 R9) — zelfde patroon hergebruiken voor het
  ranking-contract en de citatie-rescue.
- Decision-record patroon: `retrieval_decision_record` accumulator in
  `retrieve.py` — uitbreiden, niet dupliceren.
- Config-flag patroon: `settings.link_expand_score_boost` default no-op
  (`ranking.py::_apply_link_expand_boost`) — veilig uitrollen.

## Wat expliciet NIET (uit de opdrachtbrief)

- Geen Redcactus-specifieke fixes, geen bron-boosts, geen KB-splitsing.
- Geen harde tag/taxonomy-filters (bridge-bronnen sneuvelen).
- Backfill/datamigratie uitsluitend als sluitstuk.
- `max_sources` niet blind verhogen; 0.85-ratio niet verwijderen zonder
  shadow-data (mediane ratio van afgewezen bronnen is 0.37 — meerderheid
  hoort terecht niet in de lijst).
