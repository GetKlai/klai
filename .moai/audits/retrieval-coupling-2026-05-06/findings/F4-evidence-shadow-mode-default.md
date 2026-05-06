# F4 — Evidence-tier shadow mode by default

**Severity:** INFO — quality / decision-state
**Status:** OPEN — needs verification

## Initial finding

[`retrieve.py:293-318`](../../../klai-retrieval-api/retrieval_api/api/retrieve.py#L293-L318):

```python
shadow_mode = os.environ.get("EVIDENCE_SHADOW_MODE", "true").lower() in ("true", "1", "yes")
...
scored = evidence_tier.apply(copy.deepcopy(reranked))

if shadow_mode:
    logger.info("shadow_eval", ...)
    serving = reranked      # flat order served
else:
    serving = scored        # U-shape + weighted score served
```

`evidence_tier.apply` rekent `final_score = reranker * content_type_weight * assertion_weight * temporal_decay * pagerank_weight` en past U-shape ordering toe (Lost-in-the-Middle mitigatie). Maar in shadow mode beïnvloedt het de output niet — pure offline scoring.

Default: ON. Per SPEC-EVIDENCE-001 R9: "disable shadow mode after RAGAS validation confirms improvement".

## Implicatie

- Content-type weights (kb_article=1.00, web_crawl=0.65, pdf_document=0.90, etc.) doen niets vandaag.
- Temporal decay (0-30d=1.00, >365d=0.85) doet niets vandaag.
- PageRank-boost via `entity_pagerank_max` doet niets vandaag.
- U-shape ordering (Liu et al. 2023) wordt uitgerekend, niet toegepast.

## Open vragen voor verificatie

1. Is RAGAS-validatie ooit gerund? Check `klai-knowledge-ingest/knowledge_ingest/eval/` history en of er resultaten in een recent dashboard staan.
2. Wat zeggen de `shadow_eval` logs in VictoriaLogs? Specifiek: hoe vaak verschilt de top-5 tussen `flat_top_chunk_ids` en `evidence_top_chunk_ids`? Is de score-delta materieel?
3. Best practice (research): is U-shape ordering (Liu et al. 2023, "Lost in the Middle") nog state-of-the-art voor moderne LLMs (Claude 4.x, GPT-4.x, Gemini 2.x)? Of hebben modellen die context-position issue al verholpen?
4. Welke andere RAG-systemen gebruiken evidence-tier scoring met content_type weights — wat is hun calibratie methodologie?

## Voorgestelde aanpak (voor agent te valideren)

Twee fronten:

**A. Validatie voortdrijven:** Verzamel `shadow_eval` logs voor 30 dagen, bouw een offline analyse: zou `serving=scored` betere resultaten geven (op een gold-set met thumbs-up/down feedback uit `quality_score`)? Als ja → activeer in flag-controlled rollout.

**B. Beslissing nemen:** Als de shadow-mode al maanden draait zonder beslissing, ofwel
- Activeren in canary (5% traffic) met dashboard, of
- Bewust uitstellen + datum vastpinnen, of
- Decommissioneren (verwijder de hele evidence_tier code) als we besloten hebben dat het niet helpt.

## Verification

### 1. Code-trace bevestigd

`klai-retrieval-api/retrieval_api/api/retrieve.py:293-318` (geverifieerd 2026-05-06):

- `EVIDENCE_SHADOW_MODE` default = `"true"` (regel 293).
- `scored = evidence_tier.apply(copy.deepcopy(reranked))` draait altijd (regel 299) — er is dus volledige CPU-kost van scoring + U-shape ordering op iedere request.
- `serving = reranked` als `shadow_mode=true` (regel 316). De `scored` lijst wordt alleen gelogd, nooit aan de LLM gegeven.

`evidence_tier.py` heeft drie sub-flags (`EVIDENCE_CONTENT_TYPE_ENABLED`, `EVIDENCE_TEMPORAL_DECAY_ENABLED`, `EVIDENCE_PAGERANK_ENABLED`) die individueel uitgezet kunnen worden, plus `EVIDENCE_ASSERTION_MODE_ENABLED` (in v1 altijd 1.00 — pure plumbing).

**Productie env** (geverifieerd via `ssh core-01 'docker exec klai-core-retrieval-api-1 env | grep -i evidence'` op 2026-05-06): **geen** EVIDENCE_* environment variables gezet. Alle defaults staan dus actief: scoring draait, shadow mode aan, geen serving-effect.

### 2. RAGAS-validatie status

SPEC-EVIDENCE-001 R8 ("RAGAS evaluatieframework") is in de SPEC zelf afgevinkt (`progress.md` zegt "DONE" 2026-04-03), maar betreft alleen het `klai-retrieval-api/evaluation/eval_runner.py` script + 5 placeholder queries. De productie-RAGAS-pipeline werd pas later geleverd door **SPEC-RAG-EVAL-001** (`f4c11d78` op 2026-04-?? voor de harness, gevolgd door fixes `01c6294f`, `c909716a`, `6256ae85`, `d46557f6`).

De nightly cron is **literally één dag oud**: commit `a5dfe5be feat(rag-eval): wire nightly cron via @procrastinate.periodic` (#369) is gemerged op 2026-05-05 — daarvóór moest het handmatig per `docker exec` worden getriggerd. PR-beschrijving bevestigt: "The eval harness has been operator-triggered since SPEC-RAG-EVAL-001 shipped. The Procrastinate task was registered but no scheduler fired it — every measurement run required a manual docker-exec."

De harness schrijft naar `knowledge.rag_eval_results` met een `variant` kolom (default `'baseline'`, gestuurd door `RAG_EVAL_VARIANT` env var). Er is dus infrastructuur om `baseline` vs. `evidence_tier` te vergelijken — maar er is geen indicatie dat deze A/B-vergelijking ooit gedraaid is. Geen evidence in de SPEC, geen Grafana-panel beschrijving, geen besluit-document.

**Conclusie RAGAS:** Het framework bestaat, de pipeline draait sinds gisteren autonoom, maar er is geen baseline-vs-evidence-tier vergelijking gedaan. De voorwaarde uit R9 ("disable shadow mode after RAGAS validation confirms improvement") is dus formeel niet vervuld.

### 3. Productie shadow_eval logs (afgelopen 7 dagen, VictoriaLogs)

| Datum | shadow_eval entries |
|---|---:|
| 2026-04-29 | 0 |
| 2026-04-30 | 4 |
| 2026-05-01 | 6 |
| 2026-05-02 | 0 |
| 2026-05-03 | 0 |
| 2026-05-04 | 60 |
| 2026-05-05 | 154 |
| 2026-05-06 (tot nu) | 60 |
| **Totaal** | **284** |

De spike op 5 mei (154) sluit aan bij het wiren van de RAGAS-cron + `RAG_EVAL_VARIANT=baseline` runs die zelf via `/retrieve` lopen. Real-user traffic produceert een handvol calls per dag — dit is een **lage-volume** dienst, primair retrieval-voor-eval en LiteLLM-knowledge-hook.

**Sample van 5 willekeurige `shadow_eval`-events (2 mei 2026):**

| Request | flat top-1 == evidence top-1? | Top-5 set overlap | Max abs score_delta |
|---|---|---|---|
| a030...7e0 | **NEE** (ad7b... vs b05d...) | 3/5 | 0.029 |
| 47500...4ced | JA (zelfde, maar #2-5 herordend) | 4/5 | 0.574 |
| e5f07...1f6f | **NEE** (445c... vs 7962...) | 4/5 | 0.488 |
| 6159...2422 | JA (zelfde, herordend) | 4/5 | 0.431 |
| be89...aa20 | JA (zelfde, herordend) | 4/5 | 0.144 |

**Interpretatie van de score_deltas:** De deltas zijn *niet* marginaal. In 2 van 5 sample-requests verandert het meest-relevante chunk (top-1) volledig. In 4 van 5 verandert ten minste de volgorde van top-5 substantieel (deltas tot 0.57). Dit is materieel — evidence-tier zou de output meetbaar veranderen voor de meerderheid van requests.

Wat we **niet** kunnen verifiëren zonder RAGAS A/B: of die verandering een verbetering of verslechtering is. De grote deltas kunnen evengoed betekenen dat content-type weights het reranker-signaal overrulen op een manier die de gebruiker NIET wil (bv. een verse `web_crawl` chunk met sterke reranker-score wordt 35% gediscount door de 0.65 weight, terwijl een oudere `kb_article` met zwakkere reranker-score voorbij wordt geboost).

### 4. Literatuur-research (mei 2026)

#### "Lost in the Middle" — geldigheid voor moderne LLMs

- **Origineel:** Liu et al. 2023, [arXiv:2307.03172](https://arxiv.org/abs/2307.03172) — getest op Claude-1.3, GPT-3.5-Turbo, MPT-30B-Instruct. >30% degradatie wanneer relevant doc midden in context.
- **Databricks long-context RAG benchmark** ([blog](https://www.databricks.com/blog/long-context-rag-performance-llms)): "GPT-4o and Claude 3.5-Sonnet show little to no performance deterioration at longer lengths." Llama-3.1-405b begint te degraderen bij 32k. Het Lost-in-the-Middle effect is dus **model-afhankelijk** en grotendeels gemitigeerd in de huidige frontier-modellen die Klai gebruikt.
- **GM-Extract studie (november 2025)**, [arXiv:2511.13900](https://arxiv.org/abs/2511.13900) is het meest belastend: "a distinct U-shaped curve was not consistently observed" en mitigaties hebben "surprising cases where they lead to a negative impact." De aanbeveling is application-specific evaluatie, niet one-size-fits-all reordering.
- **Maxim AI overzicht 2025** ([artikel](https://www.getmaxim.ai/articles/solving-the-lost-in-the-middle-problem-advanced-rag-techniques-for-long-context-llms/)): retrieval reordering wordt nog steeds genoemd als techniek, maar als één van meerdere — context engineering en agentic retrieval krijgen meer aandacht.

**Conclusie U-shape:** in 2026 is U-shape ordering geen "no-brainer SOTA" meer. Voor frontier-modellen (Claude 3.5+, GPT-4o, Mistral Large 4) is het effect klein tot afwezig. Voor zwakkere modellen kan het nog meetbare winst geven. Vraagt om **eigen meting** op de Klai stack (klai-fast = Mistral Small 4) — daar is het niet duidelijk uit literatuur of het helpt.

#### Content-type / source-credibility weighting

- **PoniakTimes 2025 overzicht** ([artikel](https://www.poniaktimes.com/reliable-rag-ai-search/)): credibility-weighting wordt actief geadviseerd voor enterprise RAG, vooral bij gemengde bronnen (web + docs + transcripts) — exact Klai's situatie.
- **TrustworthyRAG survey** ([arXiv:2409.10102](https://arxiv.org/html/2409.10102v1)) en de RA-RAG paper die in SPEC-EVIDENCE-001 wordt geciteerd ([arXiv:2410.22954](https://arxiv.org/abs/2410.22954)) bevestigen dat metadata-gewogen retrieval +51% kan toevoegen in adversariële settings.
- Geen 2025/2026-paper die specifiek de **calibratie** van content-type weights (kb_article=1.00 vs web_crawl=0.65) beoordeelt. De Klai-defaults zijn redelijk maar onbevestigd — een tenant met grotendeels web_crawl content krijgt een 35% downweight die mogelijk te streng is.

#### Temporal decay

- **TimeRAG (CIKM 2025)** [PDF](http://playbigdata.ruc.edu.cn/dou/publication/2025_CIKM_TimeRAG.pdf) en **Bloomberg case study** beschreven in de "Refresh Trap" blog ([Medium](https://medium.com/@eyosiasteshale/the-refresh-trap-the-hidden-economics-of-vector-decay-in-rag-systems-f73bc15aa011)): temporal embeddings verbeteren top-1 accuracy 6-9% op tijds-gevoelige benchmarks.
- Voor Klai's gebruikssituatie (interne KB-content, beleidsdocumenten, meeting transcripts) is de temporal-decay-curve mogelijk **anders** dan voor news/finance: een interne policy van 6 maanden oud is meestal nog steeds correct. De huidige decay (`<30d=1.00, >365d=0.85`) is conservatief, maximaal -15%, dus zelfs als hij niet helpt, schaadt hij niet veel.

**Net-net:** De literatuur ondersteunt evidence-tier scoring **als concept**, maar bevestigt niet dat de specifieke gewichten en U-shape stap meetbaar helpen op Klai's stack zonder eigen meting.

### 5. Wat we kunnen en niet kunnen verifiëren

**Wel:**
- Default = shadow on. Productie heeft geen overrides.
- Scoring draait per request, levert materiële deltas op (sample), kost CPU.
- RAGAS-harness bestaat, draait sinds gisteren autonoom op een baseline.
- U-shape voordeel is ambigu in 2026 literatuur.

**Niet:**
- Of `serving=scored` *beter* of *slechter* zou zijn op Klai's queries — dat is exact wat de RAGAS A/B-meting moet uitwijzen, en die is nog niet gedraaid.
- Of de specifieke gewicht-calibratie (kb_article=1.00, web_crawl=0.65, etc.) optimaal is voor Klai's content mix.
- Hoe Mistral Small 4 (klai-fast) reageert op middle-of-context content — geen publieke benchmark gevonden.

## Recommended fix

**Niet activeren zonder data. Niet decommissioneren.** De pragmatische volgorde:

### Stap 1 — Run de RAGAS A/B (priority HIGH, blokkerend voor stap 2)

Trigger de RAGAS-harness twee keer met dezelfde suite, één keer met `EVIDENCE_SHADOW_MODE=true` (huidige productie = `variant=baseline`) en één keer met `EVIDENCE_SHADOW_MODE=false` (`variant=evidence_tier_v1`). De harness schrijft naar `knowledge.rag_eval_results` met de variant kolom, dus de A/B is al architecturaal voorbereid.

Concreet:
```bash
# Bestaande baseline (variant=baseline) draait al nightly via #369
# Trigger eenmalig een evidence-tier run:
ssh core-01 'docker exec -e RAG_EVAL_VARIANT=evidence_tier_v1 -e EVIDENCE_SHADOW_MODE=false \
  klai-core-knowledge-ingest-1 \
  python -m knowledge_ingest.eval --suite chat --suite knowledge_org'
```

Vergelijk daarna `context_precision`, `context_recall`, `faithfulness`, `answer_relevance` per suite tussen beide varianten (Wilcoxon signed-rank op de paired query-resultaten — al beschreven in SPEC-EVIDENCE-001 R8).

**Beslisregel:**
- Evidence-tier wint signifcant op 2+ van de 4 metrics → activeer (stap 2).
- Geen significant verschil → laat shadow staan, ga naar stap 3 (decommissioneer-overweging).
- Evidence-tier verliest → fix de calibratie (per-content-type weights herzien) of decommissioneer.

### Stap 2 — Activeer met staged rollout (alleen als RAGAS positief)

Niet via een env var op een datum. Via een per-org / percentage rollout in de retrieve-handler:

1. Voeg een `RetrievalSettings`-veld `evidence_serving_percentage: int = 0` toe (Pydantic-settings, default 0).
2. In `retrieve.py:301`: vervang de boolean `if shadow_mode` door deterministische sampling op `request_id` of `org_id` hash, threshold = `evidence_serving_percentage`.
3. Rollout-schema: 5% → 25% → 100% met 48u-soak per stap, monitoring van `quality_score` (thumbs-up/down feedback uit het bestaande feedback-systeem) en RAGAS metrics.
4. Per-tenant override mogelijk via een nieuwe `portal_orgs.evidence_tier_enabled` kolom voor klanten die expliciet baseline willen blijven.

### Stap 3 — Als RAGAS niets significant laat zien (post-meting beslissing)

Twee opties:

**3a. Decommissioneer** — verwijder `evidence_tier.py`, de aanroep in `retrieve.py:299-318`, de drie sub-flags uit `Settings`, de `final_score` / `evidence_tier_metadata` velden uit `ChunkResult`. Spaart ~50ms CPU per request en simpelt de code. Voorwaarde: minstens 2 weken RAGAS-data zonder verschil > 1pp op alle 4 metrics.

**3b. Behoud als plumbing voor SPEC-EVIDENCE-002** — als assertion-mode weights er ooit komen (nu staat alles op 1.00), heeft de scoring-pipeline al z'n vorm. Decommissioneren = die SPEC opnieuw schrijven. Acceptabele middenweg: laat de scoring staan maar ZET DE U-SHAPE ORDERING ECHT UIT (`EVIDENCE_TEMPORAL_DECAY_ENABLED=false`, `EVIDENCE_CONTENT_TYPE_ENABLED=false`) zodat de CPU-kost wegvalt. Dat is mechanisch goedkoper dan `_order_for_llm` weghalen.

### Stap 4 — Tijdvenster en eigenaar

De huidige situatie ("shadow al twee maanden, beslissing onduidelijk") is exact het scale-the-answer-to-the-problem-scenario uit `pitfalls/process-rules.md`: een feature die niets doet maar wel kost. Maximaal 30 dagen na merge van #369 (dus deadline ~2026-06-04) moet de beslissing zijn genomen — schrijf het als kalender-reminder of als een `@MX:TODO` op `retrieve.py:293` met expiry-datum.

## Risk if not fixed

| Risico | Impact | Waarschijnlijkheid | Notities |
|---|---|---|---|
| Code-rot van ongebruikte feature | LAAG-MEDIUM | HOOG (code blijft staan, refactor-kosten verdubbelen wanneer iemand `retrieve.py` aanpakt) | Bv. SPEC-RAG-PARENT-CHILD-001 (regel 320+ in dezelfde functie) heeft al moeite gehad met de `serving=reranked` vs `serving=scored` branch. |
| Verspilde inference-CPU | LAAG | ZEKER (elke request loopt door `apply()` + `_order_for_llm`) | ~50ms per request schat ik op basis van 6 numerieke ops × N chunks (typisch N=20). Niet een productie-blocker bij huidige laag volume, wel meetbaar zodra LiteLLM-knowledge-hook traffic schaalt. |
| Onbekende kwaliteit-impact bij activatie | HOOG | MEDIUM | Activeren via env-var-flip zonder RAGAS = blind gokken op een gereedschap waarvan literatuur (GM-Extract Nov-2025) zegt dat het soms slechter wordt. Sample-deltas tonen dat 4/5 requests materieel ander top-5 zouden serveren. |
| Reputatieschade door verkeerde weights | MEDIUM | LAAG-MEDIUM | Als content_type weights niet goed gekalibreerd zijn voor een tenant met overwegend `web_crawl` content (bv. een tenant die hun documentatie-portal heeft gecrawld als "externe bron"), krijgt die tenant systematisch slechtere antwoorden. Dit is exact het scenario dat shadow-mode ZOU moeten voorkomen — maar zonder beslissing eindeloos uitstellen helpt evenmin. |
| Drift tussen SPEC-statement en werkelijkheid | LAAG | HOOG | `progress.md` zegt "DONE" sinds 2026-04-03, R9-criterium "disable shadow mode after RAGAS validation" is niet bewust gevolgd. Voor audit-traceerbaarheid is dit nu zo gerepareerd worden dat de SPEC of het audit-record klopt. |

**Bottom-line risico**: het is geen acuut productie-incident. Het is een feature-rot scenario waarbij dood gewicht zich opstapelt en een toekomstige refactor (parent-child child-text swap, SPEC-EVIDENCE-002 assertion modes, een hypothetische scoring v2) duurder en risicovoller maakt. Zolang RAGAS-baseline draait, is de marginale kost om óók een evidence_tier_v1 variant te draaien laag — dus stap 1 is realistisch binnen één week te doen.

### Bronnen (geverifieerd via WebFetch / WebSearch op 2026-05-06)

- Liu et al. 2023, "Lost in the Middle" — [arXiv:2307.03172](https://arxiv.org/abs/2307.03172)
- GM-Extract studie nov-2025 (mitigaties soms negatief) — [arXiv:2511.13900](https://arxiv.org/abs/2511.13900)
- Databricks long-context RAG benchmark — [databricks.com/blog/long-context-rag-performance-llms](https://www.databricks.com/blog/long-context-rag-performance-llms)
- TrustworthyRAG survey — [arXiv:2409.10102](https://arxiv.org/html/2409.10102v1)
- TimeRAG (CIKM 2025) — [PDF](http://playbigdata.ruc.edu.cn/dou/publication/2025_CIKM_TimeRAG.pdf)
- "The Refresh Trap" — [medium.com/@eyosiasteshale](https://medium.com/@eyosiasteshale/the-refresh-trap-the-hidden-economics-of-vector-decay-in-rag-systems-f73bc15aa011)
- "Reliable RAG: Source Credibility" — [poniaktimes.com](https://www.poniaktimes.com/reliable-rag-ai-search/)
- Maxim AI "Solving Lost in the Middle" 2025 — [getmaxim.ai](https://www.getmaxim.ai/articles/solving-the-lost-in-the-middle-problem-advanced-rag-techniques-for-long-context-llms/)
