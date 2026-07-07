# SPEC-RAG-EVIDENCE-INTEGRITY-001 (compact)

## Requirements

**REQ-OBS-01** — Citation-selector splitst `max_sources_exceeded` in echte
redenen: `below_keep_ratio` | `answer_length_clamp` | `max_reached`.
**REQ-OBS-02** — `kb_citations_rendered_structured` logt het `request_id` van
de retrieval-call; bij onbeschikbaarheid is het veld aanwezig met `null`.
**REQ-OBS-03** — Query-rewrite logt prompt-variant + skip-reden als metadata
(querytekst blijft gated op telemetry_level).
**REQ-OBS-04** — Reden-verdeling van citatiebeslissingen queryable + Grafana-paneel.

**REQ-SRC-01** — Bulk-crawl ingest zet `source_domain=urlparse(url).netloc`.
**REQ-SRC-02** — `source_label` = domein voor crawl-chunks; regressietest dekt
ook de Procrastinate `extra_payload` passthrough.
**REQ-SRC-03** — Backfill `source_domain`/`source_label` uit `source_url` —
UITSLUITEND als laatste fase, na productie-verificatie van SRC-01/02 + RANK.
**REQ-SRC-04** — WHERE de sweep met `--clean-stale-taxonomy` draait: stale
node-IDs (∉ portal_taxonomy_nodes) verwijderen + count per KB rapporteren.

**REQ-CIT-01** — Rescue-regel voor overflow-bronnen: `query_score>0` ∧
`answer_score>=drempel` ∧ `retrieval_ratio>=0.4` → alsnog selecteren binnen max.
**REQ-CIT-02** — Drempels configureerbaar; defaults uit 30d-kalibratie:
answer_score 19 (p75 selected), ratio 0.4 (0.5 zou de motiverende
Zendesk-casus met ratio 0.4441 uitsluiten); herijking als percentiel.
**REQ-CIT-03** — Flag `citation_rescue_mode=shadow|active`, default shadow
(alleen `would_rescue` loggen); ≥7 dagen shadow vóór activatie.
**REQ-CIT-04** — Rescue overschrijdt nooit max 4 bronnen en wijzigt de
volgorde van geselecteerde bronnen niet.

**REQ-RANK-01** — Post-rerank één rankingveld `final_rank_score`
(= reranker_score, fallback score); diversify-sort en quality_boost sorteren
daarop, niet op RRF-`score`; page-context/link-expand boosts muteren
`final_rank_score`.
**REQ-RANK-02** — Authority-boost uitgefaseerd (had ook vóór deze SPEC geen
recall-effect: mutatie zonder hersortering vóór de rerank-slice); shadow
vergelijkt inclusief boost, bij activatie wordt de boost-code verwijderd;
her-introductie als genormaliseerde pre-rerank recall-boost = vervolgwerk.
**REQ-RANK-03** — quality_boost werkt op `final_rank_score`; geen re-sort als
niets geboost is.
**REQ-RANK-04** — Flag `ranking_contract_mode=shadow|active`, default shadow:
log old/new top-5 + old/new evidence-pack bronnen per request.
**REQ-RANK-05** — Reranker-fallback (`reranker_score=None`) behoudt exact het
huidige gedrag.

**REQ-BRIDGE-01** — Brand-bridging ook in de plain-rewrite prompt (zonder
taxonomie).
**REQ-BRIDGE-02** — `no_history_no_tree` skip vervalt volledig: eerste vraag
draait altijd de rewrite; geen merk-detectie-heuristiek (LLM beslist);
kosten max. één extra rewrite-call (1.5s timeout, fail-open).
**REQ-BRIDGE-03** — Raw-query RRF-leg blijft actief bij gewijzigde query.

## Acceptance (kern)

1. Ratio-afwijzing → reason `below_keep_ratio`; korte-antwoord-afwijzing →
   `answer_length_clamp` (falende test eerst: huidig misnomer-label).
2. Eén VictoriaLogs-query `request_id:<uuid>` levert retrieval decision +
   citation render van dezelfde request.
3. Bulk-crawl chunk heeft `source_domain` + domein-label, ook ná enrichment.
4. Rescue in shadow: zichtbare selectie ongewijzigd, `would_rescue: true`
   gelogd; actief: bron geselecteerd met reason `rescued`, totaal ≤ 4.
   Fixture = productiecasus 09:28:44 (best 0.9836, bron 0.4369 = ratio
   0.4441, answer 19, query 1 → rescued). Edges: query_score=0 → geen
   rescue; answer_score 18 of ratio 0.39 (net onder drempel) → geen rescue.
5. Ranking shadow: response identiek, old/new ordening gelogd; actief:
   serving strikt aflopend op `final_rank_score`, evidence pack volgt.
   Edges: reranker-fallback identiek aan nu; geen feedback ≥3 → geen
   re-sort; hoog-gelinkte/laag-gerankte chunk krijgt geen voorrang meer en
   de rerank-input blijft byte-identiek.
6. "hoe koppel ik hubspot aan freedom?" (lowercase, geen history, geen
   taxonomie) → rewrite mét categorie-termen + raw-query-leg actief.
   Edges: vraag zonder merk → rewrite draait, was_changed=false;
   rewrite-timeout → fail-open.
7. Rewrite-metadata (prompt-variant + skip-reden) altijd gelogd, querytekst
   alleen bij telemetry_level=full; reden-verdeling als plat filterbaar veld
   + Grafana-paneel.
8. Backfill: dry-run schrijft niets; echte run idempotent (2e run = 0
   mutaties); correcte chunks byte-identiek; stale-taxonomy cleanup met
   count per KB; daarna 0 crawl-chunks met source_url zonder source_domain.

## Files to modify

- `klai-retrieval-api/retrieval_api/api/retrieve.py`
- `klai-retrieval-api/retrieval_api/services/diversity.py`
- `klai-retrieval-api/retrieval_api/quality_boost.py`
- `klai-libs/citations/klai_citations/__init__.py` (3 chatpaden!)
- `deploy/litellm/klai_kb_citation_render.py`
- `deploy/litellm/klai_kb_query_rewrite.py`
- `klai-knowledge-ingest/knowledge_ingest/adapters/crawler.py`
- [NEW] backfill-script (operator-runbook, sluitstuk)

## Exclusions

1. Aspect-bewust evidence pack → vervolg-SPEC (na REQ-RANK live).
2. Geen bron-specifieke boosts / Redcactus-regels / KB-herindeling.
3. Geen harde tag/taxonomy-filters in het chatpad.
4. Geen `max_sources`-verhoging als losse knop.
5. Geen query-vertaling of tweede reranker.
6. Geen wijziging aan graph-search / link-expansion / `reranker_candidates`.
7. Geen taxonomy-activering bij "alle KB's"-scope (`kbs_in_scope=[]` blijft
   skippen) → aspect-pack vervolg-SPEC.
