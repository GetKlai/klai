# Plan: SPEC-RAG-EVIDENCE-INTEGRITY-001

Status: draft — wacht op review
Volgorde is bindend: elke fase maakt de volgende meetbaar of veiliger.
Backfill is expliciet het sluitstuk.

## Fase 0 — Observability eerst (REQ-OBS) — Priority High

Kleinste diff, maakt alle vervolgstappen meetbaar.

1. `klai_citations/__init__.py`
   - `_split_selected_by_quality` retourneert per overflow-item de echte
     reden: `below_keep_ratio` | `answer_length_clamp` | `max_reached`.
     `_select_supported_sources_with_decision` geeft die door in de
     decision-entries (veld `reason`).
   - Let op: downstream consumers die op de string `max_sources_exceeded`
     matchen — grep over alle drie de paden + Grafana-queries; gevonden
     matches in dezelfde PR aanpassen.
2. `deploy/litellm/klai_kb_citation_render.py` — `request_id` opnemen in
   `kb_citations_rendered_structured`. Bron van de waarde: retrieval-api
   echo't `X-Request-ID` in de response-header
   (`retrieval_api/logging_setup.py:98`); de hook leest die header uit de
   `/retrieve`-respons (nu wordt alleen `resp.json()` gebruikt) en geeft
   hem via `_klai_kb_meta` door aan de render-callsites.
3. `deploy/litellm/klai_kb_query_rewrite.py` — metadata-log
   (prompt-variant + skip-reden) altijd emitten; querytekst blijft gated op
   telemetry_level (SPEC-PRIVACY-QUERY-SHADOW-001 respecteren).
4. Grafana: paneel op reden-verdeling per week (VictoriaLogs-query).

Deliverable-check: één `request_id`-query in VictoriaLogs toont
retrieval_decision_record + citation render van dezelfde request.

## Fase 1 — Bron-identiteit (REQ-SRC-01/02) — Priority High

1. Kandidaat-diff overnemen (bestaat in honolulu-worktree):
   `crawler.py::_ingest_crawl_result` → `source_domain=urlparse(url).netloc`.
2. Regressietests:
   - bulk-crawl ingest zet `source_domain` (unit op `_ingest_crawl_result`).
   - `compute_source_label` geeft domein terug voor crawl+domain.
   - `extra_payload` bevat `source_domain` vóór `defer_async`
     (pitfall: Procrastinate enrichment passthrough — anders wist de
     enrichment-job het veld weer).
3. Verifieer na deploy op één nieuwe crawl dat Qdrant-payload
   `source_label=<domein>` bevat.

Geen backfill in deze fase.

## Fase 2 — Citatie-rescue in shadow (REQ-CIT) — Priority High

1. `klai_citations`: rescue-evaluatie in `_split_selected_by_quality`-flow,
   flag `citation_rescue_mode` (env/config, default `shadow`).
   Shadow logt `would_rescue=true` per bron in de decision-entries.
2. Drempels als module-constanten met env-override:
   `rescue_answer_score_threshold=19`, `rescue_retrieval_ratio=0.4`.
3. Kalibratie-notitie (deze sectie) = bron van de defaults:
   30d-data, p75 answer_score van geselecteerde bronnen = 19; simulatie
   q>0 ∧ ≥19 ∧ ratio≥0.4 → +28 bronnen op 311 selected (+9%).
   Waarom 0.4 en niet 0.5: de motiverende Zendesk-casus (Redcactus
   zendesk-talk 09:28:44, answer 19, query 1) heeft ratio
   0.4369/0.9836 = 0.4441 en valt bij 0.5 buiten de rescue; de curve is
   vlak (0.5 → +22, 0.4 → +28, 0.3 → +30), dus 0.4 kost weinig extra en
   redt de kerncasus.
4. Na ≥7 dagen shadow: gelogde rescues handmatig beoordelen (steekproef ≥20),
   daarna flip naar `active` per aparte config-PR.
5. Alle drie de chatpaden testen: litellm render (pad A), portal
   `citations.py` (pad B), synthesis (pad C, unit-only).

## Fase 3 — Ranking-contract in shadow (REQ-RANK) — Priority High

1. `retrieve.py`: na rerank `final_rank_score` zetten
   (= `reranker_score` if not None else `score`); page-context/link-expand
   boosts muteren `final_rank_score`.
2. `diversity.py` + `quality_boost.py`: sort op `final_rank_score`;
   quality-boost multiplicatief op `final_rank_score`; geen sort wanneer
   niets geboost is.
3. Authority-boost: wordt uitgefaseerd (REQ-RANK-02). De boost had ook
   vóór deze SPEC geen recall-effect — hij muteert `score` ná de RRF-merge
   zonder hersortering, terwijl de rerank-input `raw_results[:20]` in
   RRF-volgorde blijft; "op `score` laten staan" zou hem dus een stille
   no-op maken. Tijdens shadow blijft de oude ordening (mét boost)
   berekend voor de vergelijking; bij de flip naar `active` wordt de
   boost-code verwijderd (geen oud+nieuw naast elkaar). Her-introductie
   als echte pre-rerank recall-boost (hersorteren vóór de slice,
   genormaliseerd/gecapt t.o.v. de RRF-schaal ~0.016–0.08 per leg) is
   vervolgwerk.
4. Flag `ranking_contract_mode=shadow|active` (patroon
   `EVIDENCE_SHADOW_MODE`): shadow logt per request old/new top-5 chunk-ids
   + old/new evidence-pack bron-URLs in het decision record.
5. Na ≥7 dagen shadow: verschil-analyse (hoeveel % requests wijzigt de
   pack-samenstelling; steekproefbeoordeling), dan flip naar `active`.
6. Regressietests: reranker-fallback pad ongewijzigd (REQ-RANK-05);
   characterization test die de oude ordening vastlegt vóór de wijziging
   (reproduction-first: test toont eerst het scramble-gedrag aan).

## Fase 4 — Brand-bridging ontkoppelen (REQ-BRIDGE) — Priority Medium

1. `klai_kb_query_rewrite.py`: bridging-instructie naar de plain-rewrite
   prompt; skip-conditie `no_history_no_tree` volledig laten vervallen.
   Bewuste keuze (review-1 D4): GEEN deterministische merk-detectie vooraf —
   lowercase merknamen ("hubspot") zijn zonder lexicon niet betrouwbaar
   herkenbaar; de LLM-rewrite beslist zelf of bridging van toepassing is.
   Kosten: één extra rewrite-call per first-turn vraag (bestaand model,
   1.5s timeout, fail-open).
2. Tests: brand-vraag (lowercase) zonder history/taxonomie → rewrite bevat
   categorie-termen; vraag zonder merk → rewrite draait, `was_changed=false`;
   rewrite-timeout → fail-open op originele query; raw-query-leg blijft
   actief bij gewijzigde query.

## Fase 5 — Backfill + cleanup (REQ-SRC-03/04) — sluitstuk, Priority Medium

**Startgate (expliciet, review-1 D6):** alle drie de voorwaarden:
1. REQ-SRC-01/02 geverifieerd op ≥1 productie-crawl (Qdrant-payload toont
   `source_domain` + domein-label, ook ná enrichment).
2. `ranking_contract_mode=active` draait ≥7 dagen zonder rollback en zonder
   regressie in het reden-verdeling-paneel.
3. Rescue-shadow-review (Fase 2 stap 4) afgerond.

**Waarom backfill op REQ-RANK wacht:** de backfill verandert `source_label`
van duizenden chunks, wat direct `source_aware_select` (diversify-quota én
mentioned-source routing) beïnvloedt. Zolang de servingvolgorde nog door het
RRF/authority-scramble loopt, is een voor/na-vergelijking van de backfill
niet te interpreteren — twee gedragswijzigingen zouden door elkaar meten.
Eerst het ranking-contract stabiel, dan pas de data massaal aanpassen.

1. Backfill-script (operator-stap, geen auto-migratie): scroll Qdrant op
   `source_type=crawl AND source_domain is null`, leid `source_domain` +
   `source_label` af uit `source_url`, batch-update payloads.
   Idempotent; dry-run modus; rapporteert aantallen per domein.
2. Zelfde sweep optioneel: stale taxonomy-node-IDs verwijderen
   (nodes ∉ portal_taxonomy_nodes).
3. Verificatie: term-tellingen vóór/na (aantal chunks per source_label),
   én een bekende query draait met correcte source_counts in het
   decision record.

## Risico's en mitigaties

| Risico | Mitigatie |
|---|---|
| REQ-RANK verandert elk request | Shadow-mode ≥7 dagen + verschil-analyse vóór activatie; flag-flip is één config-PR, rollback = flip terug |
| `klai_citations` is 3-pads shared helper | CodeIndex `impact` vóór de edit; per-pad tests; reden-string-consumers gegrepd en meegenomen |
| Rescue toont te veel/te zwakke bronnen | Shadow eerst; drempels zijn config; +9% (28 bronnen/30d) is de gemeten verwachting, alerting op afwijking |
| LiteLLM bind-mount module-cache | Deploy met `compose-up.sh --force-recreate litellm` (pitfall bind-mount-content-vs-python-module-cache) |
| Enrichment wist source_domain | `extra_payload`-passthrough test (pitfall Procrastinate enrichment passthrough) |
| Backfill raakt live traffic | Operator-stap, batches, dry-run, buiten piekuren; alléén payload-update (geen re-embed) |
| answer_score is lengte-gevoelig | Drempel gedocumenteerd als percentiel; herijking gepland bij drift in reden-verdeling-paneel |

## Deploy-volgorde per fase

Elke fase is een eigen PR + deploy; retrieval-api via image-build,
litellm via hook-deploy met `--force-recreate`, klai-libs/citations wordt in
beide meegebouwd (portal-api rebuild voor pad B). Fase 5 is een
operator-runbook, geen CI-deploy.

## Referenties

- research.md (deze SPEC) — alle bevindingen met file:line en empirie
- Shadow-patroon: `retrieve.py:478` (`EVIDENCE_SHADOW_MODE`)
- Kandidaat-diff bron-identiteit: honolulu-worktree `crawler.py` (+2 regels)
- Historische data: honolulu `.context/historical_citation_decisions_30d.csv`
