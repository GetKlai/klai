# SPEC-EVIDENCE-001: Evidence Tier Scoring + Evaluatieframework

> Status: Draft
> Priority: HIGH
> Created: 2026-03-30
> Research: `docs/research/evidence-weighted-knowledge/`, `docs/research/rag-evaluation-framework-research.md`, `docs/research/assertion-mode-weights-research.md`
> Architecture: `docs/architecture/klai-knowledge-architecture.md`
> Scope: `klai-retrieval-api/`, `deploy/knowledge-ingest/`, `klai-portal/backend/`

---

## Context

Klai's retrieval pipeline treats all chunks equally. A handmatig geschreven KB-artikel en een automatisch gecrawlde webpagina hebben identieke retrieval-scores. Onderzoeek toont aan dat metadata-gewogen retrieval meetbaar betere resultaten oplevert (RA-RAG: +51% in adversariale settings, TREC Health: +60% MAP bij credibility-fusie).

Tegelijkertijd ontbreekt een evaluatieframework om te meten of wijzigingen daadwerkelijk verbetering opleveren. Zonder meting is elke scoringwijziging gokwerk. Het evaluatieframework is daarom een eerste-klas deliverable, niet een bijzaak.

---

## Goal

Chunks krijgen een evidence-gewogen score op basis van `content_type` en `ingested_at`. Het evaluatieframework meet of deze scoring retrieval daadwerkelijk verbetert. Assertion mode plumbing wordt aangelegd maar staat op flat weights (1.00) tot empirische validatie het tegendeel bewijst.

---

## Requirements (EARS)

### R1 — Content type evidence tier

**When** a chunk is retrieved from Qdrant, **the system shall** multiply its reranker score by a `content_type_weight` derived from a configurable evidence profile.

Default evidence profile:

| content_type | weight | Rationale |
|---|---|---|
| `kb_article` | 1.00 | Handmatig geschreven, menselijk gevalideerd |
| `pdf_document` | 0.90 | Officieel document, doorgaans gecureerd |
| `meeting_transcript` | 0.80 | Primaire bron, onbewerkt |
| `1on1_transcript` | 0.80 | Idem |
| `web_crawl` | 0.65 | Externe bron, hoogste ruis |
| `graph_edge` | 0.70 | Graph-resultaten: sterk in relaties, zwak als absolute feiten |
| `unknown` | 0.55 | Onbekend type, defensief gewicht |

### R2 — U-shape chunk ordering

**When** the top-k chunks are selected for LLM injection, **the system shall** order them in U-shape: sterkste chunk op positie 0, op-een-na-sterkste op de laatste positie, zwakste in het midden.

Rationale: Lost in the Middle (Liu et al., Stanford 2023) toont >30% performance degradatie wanneer het meest relevante document midden in de context staat.

### R3 — Temporal decay

**When** a chunk's `ingested_at` is older than 30 dagen, **the system shall** apply a decay factor to de score.

| Leeftijd | decay factor |
|---|---|
| < 30 dagen | 1.00 |
| 30-180 dagen | 0.95 |
| 180-365 dagen | 0.90 |
| > 365 dagen | 0.85 |

Feature-flagged: `EVIDENCE_TEMPORAL_DECAY_ENABLED=true/false` (default: true).

### R4 — Metadata passthrough

**When** a Qdrant search returns chunks, **the system shall** include `ingested_at`, `content_type`, en `assertion_mode` in het result dict, zodat downstream scoring ze kan gebruiken.

### R5 — Assertion mode plumbing (flat weights)

**When** a chunk has an `assertion_mode` value, **the system shall** include it in the evidence tier calculation with weight 1.00 voor alle modes.

De plumbing bestaat — het effect is nul. Dit wordt pas geactiveerd in SPEC-EVIDENCE-002 na empirische validatie.

### R6 — Evidence profile als configuratie-object

**The system shall** laden evidence weights uit een `EvidenceProfile` dict, niet hardcoded. V1 gebruikt `DEFAULT_EVIDENCE_PROFILE`. De architectuur moet toelaten dat een org-specifiek profiel geladen wordt in een toekomstige versie.

### R7 — Score formule

```
final_score = reranker_score * content_type_weight * assertion_weight * temporal_decay
```

Alle dimensies zijn onafhankelijk schakelbaar via feature flags.

### R8 — RAGAS evaluatieframework

**The system shall** een evaluatiescript bevatten dat:
1. 150 queries (50 curated + 100 synthetisch via RAGAS `TestsetGenerator`) als testset gebruikt
2. Retrieval draait met flat scoring (baseline) en evidence-tier scoring (treatment)
3. RAGAS Context Precision, Faithfulness, en Answer Relevancy meet voor beide
4. NDCG@10 en Recall@10 berekent op de 50 gecureerde queries
5. Wilcoxon signed-rank test uitvoert op de paired resultaten
6. Per-dimensie isolatie ondersteunt (elke dimensie aan/uit)

### R10 — Connector-level content_type configuratie

**When** een admin een connector aanmaakt of bijwerkt, **the system shall** een `content_type` veld aanbieden dat bepaalt welk label chunks van die connector krijgen bij ingest.

Default waarden per `connector_type`:

| connector_type | default content_type |
|---|---|
| `web_crawler` | `web_crawl` |
| `github` | `kb_article` |
| `notion` | `kb_article` |
| `google_drive` | `pdf_document` |
| `ms_docs` | `kb_article` |

De admin kan de default overschrijven. Een web crawl van een interne kennisbank krijgt zo `content_type=kb_article` in plaats van `web_crawl`.

**Implementation:**
- `PortalConnector` model krijgt kolom `content_type: str` (nullable, default per `connector_type`)
- `ConnectorCreateRequest` en `ConnectorUpdateRequest` krijgen optioneel `content_type: ContentType | None = None`
- `ConnectorOut` stuurt `content_type` mee
- Knowledge ingest leest `content_type` uit connector-configuratie en zet het op elk chunk

---

### R9 — Shadow scoring in productie

**When** evidence-tier scoring wordt gedeployed, **the system shall** beide scoring-methoden draaien en alleen flat scoring serveren. Evidence-tier resultaten worden gelogd voor offline vergelijking.

---

## Acceptance criteria

- [ ] `evidence_tier.py` bestaat met `apply(chunks, profile)` functie
- [ ] Content type weging actief, assertion mode op flat (1.00)
- [ ] Temporal decay actief (feature-flaggable)
- [ ] U-shape ordering actief na evidence tier scoring
- [ ] `ingested_at`, `content_type`, `assertion_mode` doorgestuurd in retrieval resultaten
- [ ] Evidence profile is een configuratie-object, niet hardcoded
- [ ] RAGAS evaluatiescript draait op 150 queries
- [ ] Baseline metrics opgeslagen
- [ ] Evidence-tier metrics gemeten en statistisch vergeleken (Wilcoxon)
- [ ] Per-dimensie isolatie getest (content_type only, temporal only, combined)
- [ ] Shadow scoring geimplementeerd voor productie-rollout
- [ ] `PortalConnector.content_type` kolom aanwezig (met Alembic migratie)
- [ ] `ConnectorCreateRequest` accepteert optioneel `content_type` met zinvolle default per `connector_type`
- [ ] Knowledge ingest gebruikt connector `content_type` bij elk chunk
- [ ] Default content_type per connector_type gedocumenteerd en getest

---

## Architecture fit

```
retrieve.py (bestaand)
  step 1: vector search (Qdrant)
  step 2: graph search (FalkorDB/Graphiti)
  step 3: merge + deduplicate
  step 4: reranker (BGE-reranker-v2-m3)
  step 5: ── NIEUW ── evidence_tier.apply(reranked, profile)
  step 6: ── NIEUW ── _order_for_llm(scored)  # U-shape
  step 7: return ChunkResult[]
```

### Nieuw bestand

`retrieval_api/retrieval_api/services/evidence_tier.py`
- `apply(chunks, profile=DEFAULT_EVIDENCE_PROFILE) -> list[ScoredChunk]`
- `_content_type_weight(content_type, profile) -> float`
- `_assertion_weight(assertion_mode, profile) -> float` (v1: altijd 1.00)
- `_temporal_decay(ingested_at, profile) -> float`
- `_order_for_llm(chunks) -> list[ScoredChunk]` (U-shape)

### Gewijzigde bestanden

| Bestand | Wijziging |
|---|---|
| `retrieval_api/services/search.py` | `ingested_at`, `content_type`, `assertion_mode` toevoegen aan return dict |
| `retrieval_api/api/retrieve.py` | `evidence_tier.apply()` aanroepen na reranking |
| `retrieval_api/models.py` | `final_score`, `evidence_tier` velden op ChunkResult |
| `knowledge_ingest/routes/ingest.py` | `assertion_mode` en `content_type` toevoegen aan `extra_payload` |
| `knowledge_ingest/qdrant_store.py` | `assertion_mode` toevoegen aan `_ALLOWED_METADATA_FIELDS` |
| `klai-portal/backend/app/models/connectors.py` | `content_type` kolom toevoegen aan `PortalConnector` |
| `klai-portal/backend/app/api/connectors.py` | `content_type` in `ConnectorCreateRequest`, `ConnectorUpdateRequest`, `ConnectorOut` |
| `klai-portal/backend/alembic/` | Migratie: `content_type` kolom op `portal_connectors` |

### Evaluatie-infrastructuur (nieuw)

`klai-retrieval-api/evaluation/` directory:
- `eval_runner.py` — RAGAS evaluatiescript
- `test_queries_curated.json` — 50 handmatige queries met ground truth
- `eval_config.yaml` — configuratie (model, metrics, thresholds)

---

## Implementatievolgorde

| # | Taak | Geschatte impact |
|---|---|---|
| 1 | R10: `content_type` op connector model + migratie | Prerequisite voor correcte chunk labels |
| 2 | R4: Metadata passthrough in search.py (incl. connector `content_type`) | Prerequisite, nul risico |
| 3 | R6: EvidenceProfile configuratie-object | Architectuurfundament |
| 4 | R1 + R5 + R3: evidence_tier.py met alle dimensies | Kernfunctionaliteit |
| 5 | R2: U-shape ordering in retrieve.py | 6 regels, bewezen effect |
| 6 | R7: Integratie in retrieve.py pipeline | Alles aan elkaar knopen |
| 7 | R8: RAGAS evaluatieframework opzetten | Meetbaarheid |
| 8 | Baseline meting (flat scoring) | Referentiepunt |
| 9 | Evidence-tier meting + vergelijking | Validatie |
| 10 | R9: Shadow scoring voor productie | Veilige rollout |

---

## Wat bewust NIET in scope is

- Assertion mode scoring activeren (SPEC-EVIDENCE-002)
- Corroboration boost (SPEC-EVIDENCE-003)
- Taxonomy alignment MCP/DB (SPEC-TAXONOMY-001)
- Org-specifieke evidence profiles (V2)
- User-facing confidence labels (research zegt: niet doen, CHI 2024)

---

## Risico's

| Risico | Mitigatie |
|---|---|
| Content type weging degradeert retrieval voor bepaalde query-types | Per-dimensie feature flags + shadow scoring + evaluatie voor activatie |
| Temporal decay benadeelt waardevolle oude content | Conservatieve decay (min 0.85), feature-flaggable |
| RAGAS synthetische queries zijn niet representatief | 50 handmatige queries als ground truth anchor |
| LLM-as-judge bias | Klai-large (Mistral) als judge, andere familie dan generatiemodel |

---

## Bronnen

- RA-RAG (Hwang et al., 2024): +51% vs. Majority Voting — [arXiv:2410.22954](https://arxiv.org/abs/2410.22954)
- TREC Health Misinformation (Huang et al., 2025): +60% MAP — [SAGE](https://journals.sagepub.com/doi/10.1177/14604582251388860)
- Lost in the Middle (Liu et al., 2023): >30% degradatie — [arXiv:2307.03172](https://arxiv.org/abs/2307.03172)
- RAGAS (Shahul Es et al., NeurIPS 2023): [arXiv:2309.15217](https://arxiv.org/abs/2309.15217)
- Einhorn & Hogarth (1975): equal weights under uncertainty
- Volledige research: `docs/research/evidence-weighted-knowledge/`, `docs/research/rag-evaluation-framework-research.md`
