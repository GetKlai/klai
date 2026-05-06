# F3 — Link-expansion is in praktijk dead weight

**Severity:** INFO — performance / config
**Status:** OPEN — needs verification

## Initial finding

Link-expansion in [`retrieve.py:205-236`](../../../klai-retrieval-api/retrieval_api/api/retrieve.py#L205-L236) (SPEC-CRAWLER-003 R14-R16):

1. Pak `links_to` van top-`link_expand_seed_k=10` chunks
2. `fetch_chunks_by_urls` haalt extra chunks op met `score=0.0` ([`search.py:383`](../../../klai-retrieval-api/retrieval_api/services/search.py#L383))
3. Authority boost: `score += 0.05 * log(1 + incoming_link_count)` ([`retrieve.py:243`](../../../klai-retrieval-api/retrieval_api/api/retrieve.py#L243))

**Probleem:** stap 5 rerank pakt alleen de top-`reranker_candidates=20` van `raw_results`. Dense Qdrant chunks scoren typisch 0.5-0.95 op cosine similarity. Een link-expanded chunk start op 0.0 + maximaal `0.05 * log(101) ≈ 0.23` als hij 100 inlinks heeft. Dat haalt de top-20 niet.

## Implicatie

- Latency-cost: extra Qdrant scroll-call (3s timeout, lijn 370) per request
- Output-impact: vrijwel nul — de uitgebreide chunks bereiken de reranker niet

## Open vragen voor verificatie

1. Empirisch: query VictoriaLogs op `link_expand_count` veld in `retrieve` log-events. Hoeveel chunks worden er gemiddeld toegevoegd?
2. Komt een link-expanded chunk ooit in `reranker_scores_top5`? Cross-correleer chunk_ids van link_expand met de uiteindelijke top-5.
3. Best practice in moderne RAG (research): is link-expansion zoals hier geïmplementeerd nog state-of-the-art, of zijn er betere benaderingen (bijv. graph-augmented rerank, knowledge-graph traversal als rerank-feature)?
4. Wat zou een betere ontwerp zijn:
   - **Optie A:** link-expanded chunks meteen door de reranker laten gaan (uitsluiten van de top-20 cut-off). Latency-impact?
   - **Optie B:** authority-boost veel agressiever maken (hogere coefficient, of multiplicatief i.p.v. additief).
   - **Optie C:** link-expansion uitschakelen tot een SPEC z'n waarde aantoont.

## Voorgestelde aanpak (voor agent te valideren)

Online valideren: zoek naar literatuur / blog-posts over "link expansion in hybrid retrieval RAG", "1-hop document expansion reranker", "incoming-link authority boost RAG". Zijn er gepubliceerde benchmarks die deze pattern als nuttig classificeren?

Als het pattern zeldzaam is of verlaten: optie C (uitschakelen) overwegen.

## Verification

### Score-arithmetic correction (the original finding had the score scale wrong)

De initiele finding nam aan dat dense Qdrant chunks scoren op `0.5-0.95 cosine similarity`. Dat klopt niet voor deze pipeline. `_search_knowledge` (`search.py:223-300`) gebruikt **Qdrant native `Fusion.RRF`** (`search.py:295`) met de Qdrant default `k=2` (geverifieerd via [Qdrant docs](https://qdrant.tech/documentation/concepts/hybrid-queries/) — "k is a constant (set to 2 by default)"). De `score` op `raw_results` is dus geen cosine, maar een RRF-fused score. Voor 3 prefetch-legs (vector_chunk + vector_questions + vector_sparse, allemaal rank 1):

| Rank | 3-leg RRF (k=2) | 2-leg RRF (geen sparse) |
|---|---|---|
| 1 | 1.0000 | 0.6667 |
| 5 | 0.4286 | 0.2857 |
| 10 | 0.2500 | 0.1667 |
| 19 | 0.1429 | 0.0952 |
| 59 (top-cap) | ~0.05 | ~0.03 |

De authority boost `0.05 * log(1+N)`:

| N (incoming) | boost |
|---|---|
| 1 | 0.0347 |
| 10 | 0.1199 |
| 100 | 0.2308 |
| 1000 | 0.3454 |

**Conclusie van de wiskunde:** de initiele claim (boost ≈ 0.23 max → kan top-20 niet halen) overschat de Qdrant scores en onderschat dus de impact van expansion. Een link-expanded chunk start op `0.0 + boost`. Een dense chunk op rank 19 zit op ~0.143. Met **N ≥ 17 inkomende links beat een expanded chunk een dense rank-19** (`0.05 * log(18) ≈ 0.145 > 0.143`). Met N ≥ 100 (boost 0.23) verslaat hij ranks 14-19. **De expanded chunks zijn dus geen dead weight per se**; in productie kunnen ze wel degelijk de top-20 binnenkomen wanneer de inlinks-distributie scheef genoeg is.

Aan de andere kant: de boost wordt op **alle** 60+expanded chunks toegepast (`retrieve.py:240` itereert over `raw_results`, niet alleen de expanded subset). Dense chunks krijgen ook een boost wanneer hun bron veel inlinks heeft. Het netto-effect is dus dat expanded chunks de top-20 binnenkomen alleen als hun N significant hoger is dan dat van de dense chunk op de uitsmijter-positie.

### Tweede RRF-pad (`_rrf_merge` met k=60) is in productie dood

`retrieve.py:45-64` definieert een aparte `_rrf_merge(k=60)` die alleen wordt aangeroepen wanneer `graph_results` niet leeg is (lijn 195). Empirisch — `service:retrieval-api AND graph_results_count:>0` over 48h: **0 hits**. Graphiti-pad fired niet in productie deze week, dus `_rrf_merge` (k=60) speelt geen rol. De analyse "scores 0.012-0.016" was hypothetisch en valt af.

### Empirische evidence (VictoriaLogs, 2026-05-05 → 2026-05-06)

`service:retrieval-api AND link_expand_count:>0`:
- **214 retrieve-requests** met expansion fired
- **3685 expanded chunks toegevoegd** in totaal
- **Gemiddelde 17.2 chunks per request**, mediaan/dominant op `link_expand_count=20` (107 hits — d.w.z. de cap wordt vaak gehaald)
- Verdeling: `link_expand_count=20` (107x), `=19` (48x), `=18` (24x), rest <10x. Erg sterk geconcentreerd op de cap.

Latency-cost (`link_expand_ms`):
- avg = **8.9ms**, p50 = 7.7ms, p95 = 13.2ms, max = 53.3ms (over dezelfde 214 requests)
- Total request `total_ms` is meestal 400-500ms, dus de scroll-call kost ~2% van de end-to-end latency. Niet pijnlijk.

`reranker_scores_top5` zoals geobserveerd in 2026-05-06 03:00 UTC voorbeelden:
- Voorbeeld query "How do I handle een klant…": `[0.84, 0.66, 0.49, 0.35, 0.16]`
- Voorbeeld query "?": `[0.96, 0.87, 0.79, 0.75, 0.57]`
- Voorbeeld query "hoi" (degenerate): `[0.035, 0.028, 0.008, 0.006, 0.006]`

Dit zijn cross-encoder scores, geen vector scores — niet direct vergelijkbaar met de raw_results-score. Wat ze WEL bevestigen: de reranker krijgt 20 candidates en scoort ze van scratch; als een expanded chunk de top-20 haalt vanwege de boost, krijgt hij een eerlijke beoordeling op semantische relevantie ten opzichte van de query.

### Wat ik NIET kon valideren

- **Of expanded chunks daadwerkelijk in de finale top-5 verschijnen.** Het log-event `retrieval_decision_record` bevat `reranker_scores_top5` maar niet de chunk_ids of een flag "is_link_expanded". Dedicated logging is nodig om de overlap (seed_ids ∩ top5, expanded_ids ∩ top5) te meten. Zonder die telemetry kan ik niet onderscheiden tussen "expansion brengt nuttige chunks" en "expansion brengt chunks die alsnog door de reranker worden weggegooid".
- **Lokale reproductie** van een query met `link_expand_enabled=true` vs `false` op staging — geen toegang tot een staging retrieval-api binnen scope van deze audit.

### Literatuur — bevindingen samengevat

1. **Score-additie tussen verschillende schalen is een bekend anti-pattern.** Gerard Laforge ([blog post](https://glaforge.dev/posts/2026/02/10/advanced-rag-understanding-reciprocal-rank-fusion-in-hybrid-search/), 2026): "how do you meaningfully combine a cosine similarity score of 0.85 with a BM25 score of 12.4? Those values are on two distinct unrelated scales!" De aanbevolen oplossing: **rank-based fusion (RRF) met k=60**, niet additieve normalisatie. De Klai-implementatie violeert dit: hij telt een `0.05 * log(1+N)` term op bij een Qdrant-RRF score (k=2). De schalen zijn niet alignend.

2. **HippoRAG (NeurIPS 2024, [arxiv:2405.14831](https://arxiv.org/abs/2405.14831), [github](https://github.com/OSU-NLP-Group/HippoRAG))** combineert PageRank niet additief met embedding similarity. Het encoder-pad identificeert alleen seed nodes; de finale ranking is pure-PageRank: `passage_score = sum of (PPR_node_probability × node_specificity)`. Dat is een conceptueel andere manier van graph-augmented retrieval — graph-signaal als primary ranking, niet als kleine boost.

3. **Microsoft GraphRAG ([github](https://github.com/microsoft/graphrag))** doet entity-level expansion (subgraph rond query-entities), niet 1-hop link expansion. De retrieved subgraph wordt textually gelineariseerd tot pseudo-documents en die gaan parallel naast de gewone embedding-retrieval naar de LLM — geen additieve score-merge.

4. **Elastic graph RAG ([blog](https://www.elastic.co/search-labs/blog/rag-graph-traversal))** voert iteratieve graph expansion uit en pruned door shortest-paths. Expanded triplets gaan **niet** direct in de reranker; ze worden eerst gelineariseerd tot pseudo-documents. Reranking gebeurt op het einde, op een homogene candidate set.

5. **Graph-Based Re-ranking survey, [arxiv:2503.14802](https://arxiv.org/html/2503.14802v1)** documenteert GAR/SlideGAR (Adaptive Re-ranking) als de canonieke pattern: een neighbor-expansion candidate pool wordt iteratief uitgebreid via reranker-feedback — niet via een vooraf-gefixeerde additieve boost. De survey merkt expliciet op: "a standard benchmark to measure performance has not yet been developed to evaluate graph-based passage and document ranking tasks." Geen BEIR/STaRK/MTEB benchmark dwingt deze additieve-boost pattern af.

6. **HopRAG ([arxiv:2502.12442](https://arxiv.org/abs/2502.12442))** doet retrieve-reason-prune: lexically/semantically similar passages → multi-hop neighbor exploration → reasoning-guided pruning. Geen additieve score-mix.

**Samenvatting van literatuur:** geen van de gevestigde graph-augmented RAG patterns gebruikt "1-hop link expansion + score=0 + additieve `c * log(1+inlinks)` boost" als integratiepatroon. Het Klai-design is custom en niet aantoonbaar gevalideerd op een publieke benchmark. Wat in de literatuur dominant is: (a) RRF-merge tussen graph-leg en dense-leg, of (b) graph-as-primary-ranking (HippoRAG), of (c) graph-naar-pseudo-document linearization (GraphRAG, Elastic). Geen daarvan komt overeen met deze pipeline.

### Wat dit netto betekent

- De pipeline is **niet zo dood als de initiele finding suggereerde** — de RRF-score schaal alignet net wel ongeveer met de boost-schaal voor chunks met N ≥ 17 inlinks.
- De pipeline is **wel structureel mis-gedesignd**: de boost-coefficient (0.05) is gekalibreerd onder de aanname van cosine similarity (0.5-0.95), terwijl de echte score Qdrant-RRF is (0.05-1.5). De default coefficient is op de verkeerde aannames gefit.
- **Latency-cost is laag** (~9ms p50, ~13ms p95).
- **Output-bijdrage is onzichtbaar** door het ontbreken van `is_link_expanded` flag in `reranker_scores_top5` logging.

## Recommended fix

**Optie D (uitschakelen tot validatie) is niet de beste keuze** — er is een plausibele kans dat de pipeline wel waarde toevoegt, en de kosten zijn laag. **Maar de huidige kalibratie is bewijsbaar verkeerd ten opzichte van de score-schaal.**

**Aanbevolen pad — gefaseerd:**

### Fase 1: instrument om empirisch waarde aan te tonen (1 PR, low risk)

Voeg in `retrieve.py` na de rerank-stap (lijn 249) een log-veld toe:

```python
expanded_chunk_ids = {c["chunk_id"] for c in raw_results if c not in seed_chunks_pre_expand}
top_k_chunk_ids = [r["chunk_id"] for r in reranked[:req.top_k]]
decision_record["link_expand_top_k_overlap"] = len(set(top_k_chunk_ids) & expanded_chunk_ids)
decision_record["link_expand_seed_top_k_overlap"] = len(set(top_k_chunk_ids) & {c["chunk_id"] for c in seed_chunks})
```

(Vereist een kleine refactor — `seed_chunks_pre_expand` moet bewaard worden vanaf lijn 207 vóór de boost-stap.)

Met deze velden in de logs kun je over een week meten:
- `avg(link_expand_top_k_overlap)` — hoe vaak komt een expanded chunk daadwerkelijk in de finale top-k?
- Distributie van `incoming_link_count` op de chunks die wel in top-k komen.

Als avg < 0.1 (i.e. expansion zelden bijdraagt), dan is Optie D (uitschakelen) gerechtvaardigd. Als avg > 0.5, dan is de pipeline aantoonbaar nuttig.

### Fase 2: structureel fix de score-schaal (na 1 week meten)

**Optie A (canoniek aanbevolen door de literatuur):** vervang de additieve boost met een RRF-merge tussen drie ranklists:

```python
# pseudocode
ranking_dense = sorted(qdrant_results, key=lambda r: r["score"], reverse=True)
ranking_authority = sorted(all_chunks, key=lambda r: r.get("incoming_link_count", 0), reverse=True)
ranking_expansion = expansion_chunks  # rank-ordered van fetch_chunks_by_urls
fused = rrf_merge_n([ranking_dense, ranking_authority, ranking_expansion], k=60)
```

Dit aligneert met Qdrant's eigen praktijk (Fusion.RRF in `_search_knowledge`) en met de RRF-pattern die literatuur expliciet aanbeveelt voor heterogene score-bronnen ([Laforge 2026](https://glaforge.dev/posts/2026/02/10/advanced-rag-understanding-reciprocal-rank-fusion-in-hybrid-search/), [Cormack 2009 origineel](https://opensearch.org/blog/introducing-reciprocal-rank-fusion-hybrid-search/)). De boost-coefficient verdwijnt — geen kalibratie meer nodig.

**Optie A.alt (minimal change):** als de RRF-refactor te groot is, hercalibreer op z'n minst de boost-coefficient. De huidige Qdrant top-rank score is ~1.0 (3-leg RRF). Een boost die "modest signaal" is moet ~5-10% van de top-score zijn, dus ~0.05-0.1 in absolute waarde. Bij N=10 inlinks geeft `0.05 * log(11) = 0.12` — binnen die range. Bij N=1000 geeft `0.05 * log(1001) = 0.35` — boven 30% van de top-score, te hoog. Aanbevolen: **boost-coefficient verlagen naar 0.03 EN cappen op 0.15** om het buitenste deel van de log-curve af te knippen:

```python
boost = min(settings.link_authority_boost * math.log(1 + incoming), 0.15)
```

**Optie B (boost veel hoger maken) wordt afgeraden** — dat verergert het scale-mismatch probleem en kan irrelevante expanded chunks de top-20 doen domineren.

**Optie C (entity-PageRank vervanging):** out-of-scope voor deze finding; vereist aparte SPEC en kennisgraaf-integratie. De codebase heeft al `entity_pagerank_max` als payload-veld (`search.py:321, 393`); een toekomstige SPEC kan onderzoeken of dat signaal directer bruikbaar is dan het Notion-style 1-hop-links pad.

### Voorgestelde acties

1. **Niet uitschakelen** — kosten zijn laag, mogelijke waarde is plausibel, maar onbewezen.
2. **Wel instrumenteren** (Fase 1) — maakt de empirische vraag definitief beantwoordbaar.
3. **Op basis van metingen na 1 week:** ofwel Optie A (RRF-refactor — canonieke fix), ofwel Optie A.alt (kalibratie-tweak — minimaal-invasief), ofwel Optie D (uitschakelen — als de telemetry zegt dat het pad nul oplevert).

## Risk if not fixed

**Severity blijft INFO**, met de volgende nuances ten opzichte van het oorspronkelijke severity-oordeel:

- **Latency-risico: minimaal.** ~9ms p50 op een 400-500ms request, ~2%. Geen p99-tail risico binnen de geobserveerde 48h. Niet pijnlijk genoeg om alleen op latency-basis fix-prioriteit te krijgen.
- **Output-kwaliteitsrisico: onbekend, mogelijk relevant.** Door de gebrekkige boost-kalibratie kunnen er twee tegenovergestelde regressies optreden:
  - Type 1: nooit een expanded chunk in top-k ondanks dat hij relevant is (oorspronkelijke claim — gedeeltelijk waar voor lage-N pages).
  - Type 2: een expanded chunk met hoge N (bijv. een homepage met 1000 inlinks, boost 0.35) verdringt een relevante dense chunk uit de top-20, en alleen de reranker-cross-encoder kan dat nog goedmaken — als dat überhaupt lukt (homepage-content is vaak generiek). Dit is het inverse risico dat in de SPEC-rationale ("logaritmische demping voorkomt dat homepages disproportioneel scoren") als gemitigeerd werd verklaard, maar de schaalmis-match herintroduceert het risico stilletjes.
- **Onderhoudsrisico: middelhoog.** De huidige code lijkt de RRF-conventie (Qdrant native, k=60 in-code merge) consistent toe te passen, maar de additieve authority-boost breekt die conventie zonder commentaar. Een toekomstige refactor (bijv. om de drie ranks te uniformiseren) zal moeten reverse-engineeren of de boost een design-keuze of een schaal-fout was. De motivatie "modest signaal ten opzichte van semantische relevantie" in de SPEC is op de verkeerde score-scale gebaseerd; dat verdient een correctie ongeacht of de feature live blijft.
- **SPEC-correctness risico: laag-middelhoog.** SPEC-CRAWLER-003 R17 specifieert wel de formule maar geeft geen empirische validatie van de coefficient op de echte score-scale. Een formele post-hoc evaluatie zoals voorgesteld in Fase 1 sluit een open vraag van de oorspronkelijke SPEC af.

**Conclusie van de assessor:** de finding is reëel maar niet zo extreem als de eerste analyse suggereerde. De pipeline doet iets wel-gedefinieerd, niet niets — maar wat het doet is gekalibreerd op een aanname die niet klopt met de actuele score-distributie. Aanbevolen pad: instrument eerst, fix de schaal daarna. Niet domweg uitschakelen, niet domweg laten staan.

## Risk if not fixed

_Agent vult dit in._
