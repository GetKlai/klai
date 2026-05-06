# Retrieval coupling audit — 2026-05-06

## Context

Audit-vraag (Mark, 2026-05-06): is de `/retrieve` pipeline van klai-retrieval-api correct gekoppeld? Werken alle stappen samen zoals bedoeld, geven callers het juiste mee, en gaat er niets stilletjes verloren?

Methodologie: pure code-following. Pipeline gevolgd vanaf [`POST /retrieve`](../../../klai-retrieval-api/retrieval_api/api/retrieve.py) entry point, doorheen alle services (search, reranker, gate, router, evidence_tier, parent_lookup, diversity, graph_search), tot en met de 4 callers.

## Wijzigingen op eerdere versie

- **Schrapping van findings #1 + #3 (focus narrow + broad):** klai-focus is via `SPEC-DECOMM-FOCUS-001` (PR #368, commit `e6fabc73`) van main verwijderd. De code-paden in `klai-focus/research-api/app/services/retrieval_client.py` (`retrieve_narrow` / `retrieve_broad`) bestaan dus niet meer als runtime-callers — eerste bevindingen waren gebaseerd op een working tree die nog op een pre-decommissie branch zat.
- **Nieuwe finding F5:** notebook-scope code-pad in retrieval-api zelf (`_search_notebook`, `_notebook_filter`, `qdrant_focus_collection` setting, `scope="notebook"`/`"broad"` branches) is door bovenstaande decommissie volledig onbereikbaar geworden — code blijft staan, maar geen enkele live caller raakt het meer.

## Pipeline-overzicht (verifiëring)

`POST /retrieve` flow zoals werkelijk gewired in `retrieve.py`:

1. Auth (scope `klai:internal:retrieval:query` of internal-secret bypass) → `verify_body_identity` pinst `request.state.verified_caller`.
2. Coreference rewrite (LiteLLM, klai-fast model, 3-turn historie).
3. Embed dense + sparse parallel via TEI + BGE-M3-sparse sidecar.
4. Gate-check tegen reference-vectors in `data/gate_reference.jsonl` — bypass bij top1-top2 margin > 0.1.
5. Query-router (3 lagen: keyword → semantic centroids → optional LLM) bepaalt source-selectie als kb_slugs niet expliciet meegegeven.
6. Hybrid search Qdrant (3-leg RRF: vector_chunk + vector_questions + vector_sparse) + parallel Graphiti graph search.
7. RRF-merge over beide via `1/(k+rank+1)`, `k=60`.
8. Link expansion (1-hop op `links_to` payload).
9. Authority boost: `score += 0.05 * log(1 + incoming_link_count)`.
10. Cross-encoder rerank via Infinity (`bge-reranker-v2-m3`).
11. Source-aware select (mention-detect + per-source quota).
12. Quality boost (feedback-based, ≥3 votes).
13. Evidence-tier scoring + U-shape ordering — **shadow mode by default** (geserveerde volgorde = flat reranker).
14. Parent-text swap (child → parent chunk via `knowledge.parent_chunks`).
15. Telemetry: `step_latency_seconds`, `retrieval_decision_record` log, `knowledge.queried` product event (uit `verified_caller`, niet uit body).

## Findings

| ID | Severity | Title | Status na agent-verificatie |
|---|---|---|---|
| F1 | HIGH (latent) | gap_rescorer authentiseert met `Authorization: Bearer <internal_secret>` — wordt door retrieval-api als JWT geïnterpreteerd → 401 | **CONFIRMED, currently DORMANT.** `portal_retrieval_gaps` is leeg op prod (LiteLLM gap-emit pipeline brak 7 dagen geleden); bug fired niet in steady state, maar fired op eerste herstel van gap-emit. Test mockt alleen `X-Caller-Service`, niet de auth-header. Fix: 1-line + assert-test. |
| F2 | LOW (latent) | partner_chat dropt `knowledge.queried` events (geen user_id → geen verified_caller) | **NOT-A-BUG-BUT-DESIGN.** SPEC-API-001 zegt expliciet "partners have no end-user concept". 0 actieve partner-keys op prod, dus latente lacune. Recommended: synthetic `user_id=f"partner:{key_id}"` + auth.py `partner:`-prefix bypass. |
| F3 | INFO | Link-expansion: score=0 + authority boost beïnvloeden top-20 reranker niet | **PARTIALLY CONFIRMED — math was wrong.** Qdrant native `Fusion.RRF` gebruikt k=2 (schaal 0.05-1.5), niet cosine 0.5-0.95. Met N≥17 inlinks beat een expanded chunk al rank-19. Niet dead-weight, wel suboptimaal score-merge tussen verschillende schalen. Phase 1: instrumenteer top-k overlap. Phase 2: RRF-merge i.p.v. additieve boost. |
| F4 | INFO — action recommended | Evidence-tier shadow mode by default (`EVIDENCE_SHADOW_MODE=true`); content_type/temporal/pagerank weights beïnvloeden output niet | **CONFIRMED.** Geen `EVIDENCE_*` env vars op prod. RAGAS-cron wired LITERALLY GISTEREN (PR #369). 284 shadow_eval events in 8d, top-1 chunk wisselt in 2/5 sample requests, max delta 0.574 — materieel. Run RAGAS A/B nu via bestaande `variant` kolom; staged rollout of decommissioneren met 30-dagen deadline. |
| F5 | ✅ ALREADY-DONE | Notebook scope (`_search_notebook`, `_notebook_filter`, `req.scope == "notebook"` branches) is onbereikbaar code geworden na klai-focus deprecation | **CLOSED door `SPEC-DECOMM-FOCUS-001` (#368, commit `e6fabc73`, 2026-05-05).** Geverifieerd op origin/main: alle codepaden weg (`_search_notebook`, `_notebook_filter`, `qdrant_focus_collection`, scope literals, test-fixtures, deprovisioning tuple). Audit forkte vóór deze merge. |
| F6 | NIT | `parent_lookup` warning-logs gebruiken format-string i.p.v. structlog kwargs (queryability-verlies in VictoriaLogs) | **CONFIRMED, harmless.** Log fired 0× per 30d. 5 andere retrieval-api files hebben gelijksoortige antipatterns. Bundelen met bredere logging-cleanup. |

Per-finding details + literatuur-citaten + log-evidence staan in `findings/F<N>-*.md`.

## Wat opvalt — niet als finding maar als observatie

- **Klai_focus Qdrant collection bestaat niet meer op prod** — F5-agent heeft dit geverifieerd: `GET /collections/klai_focus → 404`. Cleanup van de `qdrant_focus_collection` setting + `_search_notebook` code valt onder F5.
- **Branches die nog vóór SPEC-DECOMM-FOCUS-001 zijn afgesplitst** dragen `klai-focus/` als getrackte files mee. Dat lost zichzelf op bij merge naar main; geen actie nodig vanuit dit audit-rapport.

## Beslissing per finding

| Finding | Beslissing | Vehikel |
|---|---|---|
| F1 | **Hotfix** — voor gap-emit pipeline herstelt | PR met test (geen SPEC) — **#387** |
| F2 | **Defer** tot eerste partner-integratie nadert | Ticket / referentie naar `F2-...md` |
| F3 phase 1 (instrumentation) | **Quick-win** — voegt vereist signal toe voor phase 2 beslissing | PR (geen SPEC) |
| F3 phase 2 (RRF migration) | **SPEC** — score-fusion herontwerp, A/B nodig | Toekomstige SPEC |
| F4 | **SPEC** — `SPEC-EVIDENCE-002` of R10 op SPEC-EVIDENCE-001 | RAGAS A/B met `variant` kolom + rollout-plan |
| F5 | ✅ Done | `SPEC-DECOMM-FOCUS-001` (#368, gemerged 2026-05-05) |
| F6 | **Bundle** in opportunistic logging-cleanup | Geen aparte vehikel |

## Status

- [x] Audit uitgevoerd, ruwe findings vastgelegd
- [x] Findings ge-cleaned op klai-focus decommissie
- [x] Sub-agent verificatie per finding
- [x] Eindbeslissing per finding
- [ ] Hotfixes + SPECs ingepland
