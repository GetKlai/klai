# Acceptance: SPEC-RAG-EVIDENCE-INTEGRITY-001

## Scenario 1 — Eerlijke reden-labels (REQ-OBS-01)

**Given** een citatie-selectie met 3 kandidaat-bronnen waarvan de beste
retrieval_score 0.98 heeft en een tweede bron 0.44 (ratio 0.45 < 0.85) met
answer_score 19 en query_score 1
**When** `_select_supported_sources_with_decision` draait met max_sources=4
**Then** wordt de tweede bron afgewezen met reason `below_keep_ratio`
(niet `max_sources_exceeded`) en bevat de decision-entry retrieval_score,
answer_score, query_score en required_query_score zoals nu.

## Scenario 2 — Antwoordlengte-clamp krijgt eigen label (REQ-OBS-01)

**Given** een antwoord van ≤20 tokens en 2 kandidaat-bronnen die beide de
keep-ratio en support-checks halen
**When** de selector draait
**Then** wordt bron #2 afgewezen met reason `answer_length_clamp`.

## Scenario 3 — Request-correlatie (REQ-OBS-02)

**Given** een pad-A chatvraag die retrieval triggert
**When** de citatie-render logt
**Then** bevat `kb_citations_rendered_structured` hetzelfde `request_id` als
het `retrieval_decision_record` van die call, en levert één
VictoriaLogs-query `request_id:<uuid>` beide records op.

**Edge (REQ-OBS-02):** retrieval faalt vóór er een respons is → de render-log
bevat het veld `request_id` met waarde `null` (nooit weggelaten).

## Scenario 3b — Rewrite-metadata altijd gelogd (REQ-OBS-03)

**Given** een pad-A chatvraag waarbij telemetry_level `shadow` is
**When** de query-rewrite draait (of wordt geskipt)
**Then** bevat de metadata-log de prompt-variant (`plain` | `classify`) en de
skip-reden (of `skipped=false`), en bevat de log GEEN letterlijke querytekst.

**Edge:** telemetry_level `full` → zelfde metadata-velden, querytekst wél
aanwezig (bestaand gedrag ongewijzigd).

## Scenario 3c — Reden-verdeling queryable (REQ-OBS-04)

**Given** ten minste één citatiebeslissing met een afgewezen bron na deploy
**When** in VictoriaLogs gefilterd wordt op citatie-render events over 24h
**Then** is per afwijsreden (`below_keep_ratio`, `answer_length_clamp`,
`max_reached`, `query_not_supported`, `answer_not_supported`, `rescued`) een
count te aggregeren zonder vrije-tekst-parsing van geneste structuren
(reden beschikbaar als plat, filterbaar veld), en toont het Grafana-paneel
deze verdeling per week.

## Scenario 4 — Bulk-crawl bron-identiteit (REQ-SRC-01/02)

**Given** een bulk-crawl van `https://wiki.example.cloud/nl/pagina`
**When** `_ingest_crawl_result` de pagina ingest en de enrichment-job daarna
de chunks herschrijft
**Then** heeft elke chunk in Qdrant `source_domain="wiki.example.cloud"` en
`source_label="wiki.example.cloud"` — ook ná enrichment (extra_payload
passthrough).

## Scenario 5 — Rescue in shadow verandert niets zichtbaar (REQ-CIT-03)

**Given** `citation_rescue_mode=shadow` en de productiecasus van
2026-07-07 09:28:44 als fixture: beste bron retrieval_score 0.9836,
overflow-bron 0.4369 (ratio 0.4441 ≥ drempel 0.4) met query_score=1 en
answer_score=19
**When** de selector draait
**Then** blijft de zichtbare selectie identiek aan vandaag en bevat de
decision-entry `would_rescue: true`.

## Scenario 6 — Rescue actief (REQ-CIT-01/04)

**Given** `citation_rescue_mode=active` en dezelfde bron als scenario 5
**When** de selector draait
**Then** wordt de bron geselecteerd met reason `rescued`, blijft het totaal
≤ effectieve max (nooit > 4), en blijft de volgorde van eerder geselecteerde
bronnen ongewijzigd.

**Edge:** zelfde input maar query_score=0 → geen rescue.
**Edge:** answer_score=18 (net onder drempel) → geen rescue.
**Edge:** retrieval_ratio=0.39 (net onder drempel 0.4) → geen rescue.
**Edge (REQ-CIT-02):** env-overrides `RESCUE_ANSWER_SCORE_THRESHOLD=25` en
`RESCUE_RETRIEVAL_RATIO=0.6` gezet → de productiecasus (answer_score 19,
ratio 0.4441) wordt niet gerescued; een bron met answer_score 25 én
ratio 0.6 wél.

## Scenario 7 — Ranking-contract shadow-log (REQ-RANK-04)

**Given** `ranking_contract_mode=shadow` en een query waar de RRF-volgorde en
reranker-volgorde verschillen (fixture: chunk met hoge incoming_link_count en
lage reranker_score)
**When** /retrieve draait
**Then** bevat het decision record old/new top-5 chunk-ids en old/new
evidence-pack bron-URLs, en is de response identiek aan het huidige gedrag.

## Scenario 8 — Ranking-contract actief (REQ-RANK-01/02/03)

**Given** `ranking_contract_mode=active`, reranker aan, en de fixture uit
scenario 7
**When** /retrieve draait
**Then** is de servingvolgorde strikt aflopend op `final_rank_score`
(= reranker-score met begrensde boosts), staat de hoog-gelinkte/laag-gerankte
chunk niet meer bovenaan, en kiest het evidence pack zijn 3 bronnen op basis
van deze volgorde.

**Edge (REQ-RANK-05):** reranker-call faalt → fallback met
`reranker_score=None` levert exact de huidige score-ordening (regressietest).
**Edge (REQ-RANK-03):** geen enkele chunk met feedback_count ≥ 3 →
quality_boost verandert volgorde niet.
**Edge (REQ-RANK-02):** chunk met incoming_link_count=20 en
reranker_score 0.3 → krijgt geen voorrang meer via de authority-boost:
de serving volgt `final_rank_score`, en de rerank-input
(`raw_results[:20]`, RRF-volgorde) is byte-identiek aan vóór de wijziging
(de boost had daar nooit effect op — regressietest legt dit vast).

## Scenario 9 — Brand-bridging zonder taxonomie (REQ-BRIDGE)

**Given** een eerste vraag "hoe koppel ik hubspot aan freedom?" (lowercase,
zoals gebruikers typen) zonder conversation-history en zonder
taxonomy-coverage
**When** de hook de query verwerkt
**Then** draait de rewrite (de `no_history_no_tree` skip bestaat niet meer),
bevat de rewritten query 2–4 categorie/partner-termen naast "hubspot", en
gaat de letterlijke gebruikersquery mee als raw-query RRF-leg.

**Edge:** vraag zonder merknaam en zonder history ("wat is jullie
adres?") → de rewrite draait wél (skip is vervallen), de LLM laat de query
inhoudelijk ongewijzigd, en de metadata-log toont `was_changed=false`.
**Edge:** rewrite-call time-out (1.5s) → fail-open: originele query wordt
gebruikt, retrieval gaat door (bestaand gedrag).

## Scenario 10 — Backfill en stale-taxonomy cleanup (REQ-SRC-03/04)

**Given** een Qdrant-testset met (a) crawl-chunks met `source_url` maar
`source_domain=null`, (b) chunks met een taxonomy-node-ID die niet in
`portal_taxonomy_nodes` bestaat, en (c) chunks die al correct zijn
**When** het backfill-script draait met `--dry-run` en daarna zonder, met
`--clean-stale-taxonomy`
**Then** rapporteert de dry-run de aantallen per domein zonder te schrijven;
na de echte run heeft groep (a) `source_domain` + `source_label` afgeleid
uit `source_url`, is bij groep (b) de stale node-ID verwijderd (met count per
KB gerapporteerd), en is groep (c) byte-identiek ongewijzigd (idempotentie:
een tweede run rapporteert 0 mutaties).

## Kwaliteitscriteria

- Alle nieuwe gedragingen achter flags met default = huidig gedrag
  (shadow); geen flag-flip in dezelfde PR als de code.
- Bestaande testsuites van retrieval-api, litellm-hooks, citations en
  knowledge-ingest blijven groen.
- Reproduction-first: het scramble-gedrag (REQ-RANK) en het
  misnomer-label (REQ-OBS-01) hebben elk eerst een falende test die het
  huidige foute gedrag vastlegt.
- Latency: geen extra Qdrant-calls in het hot path en geen nieuwe
  LLM-callsites; de enige toegestane toename is de bestaande rewrite-call die
  door het vervallen van de `no_history_no_tree` skip nu ook op first-turn
  vragen draait (REQ-BRIDGE-02: bestaand model, 1.5s timeout, fail-open). De
  shadow-logs zijn puur in-process.
- Na Fase 5 (backfill): 0 crawl-chunks met `source_url` gezet maar
  `source_domain=null`; geverifieerde source_counts per domein in het
  decision record van een bekende testquery.
