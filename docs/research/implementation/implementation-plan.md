# Evidence-Weighted Knowledge: Implementatieplan voor Klai

> Aangemaakt: 2026-03-29
> Gebaseerd op: [Evidence-Weighted Knowledge Research](../foundations/evidence-weighted-knowledge.md) + [ThetaOS & Klai Comparison](../foundations/thetaos-klai-comparison.md)
> Status: voorstel — nog niet geïmplementeerd
> Scope: `retrieval-api/` en `klai-knowledge-ingest/`
> Onderdeel van: [Research Synthesis](../README.md)
> Gerelateerd: [Assertion Mode Weights](../assertion-modes/assertion-mode-weights.md) (nuanceert de gewichten in dit document), [Corroboration Scoring](../corroboration/corroboration-scoring.md) (deferred corroboratie), [RAG Evaluation Framework](../evaluation/rag-evaluation-framework.md) (meetmethode)

---

## Aanleiding

Het onderzoek in de [ThetaOS & Klai Comparison](../foundations/thetaos-klai-comparison.md) identificeert zes concrete gaps in Klai's retrieval-pipeline
die wetenschappelijk bewezen oplossingen hebben. Dit document vertaalt die gaps naar
exacte codewijzigingen — welk bestand, welke regel, met welke wetenschappelijke
onderbouwing.

De kern: **alle chunks wegen nu gelijk**. Een handmatig geschreven KB-artikel en een
automatisch gecrawlde webpagina hebben identieke retrieval-scores. RA-RAG toont +51%
nauwkeurigheid bij bronweging in adversariale settings (Hwang et al., 2024). TREC Health
Misinformation toont +60% MAP bij credibility-gewogen fusie (Huang et al., 2025).

---

## De zes gaps en hun fixes

### Gap 1 — `content_type` stored, nooit gebruikt voor scoring

**Huidige code:**
- `qdrant_store.py`: `content_type` wordt opgeslagen als Qdrant payload ✓
- `retrieve.py`: `content_type` zit in `ChunkResult` maar beïnvloedt de score niet ✗

**Wetenschappelijke basis:**
- RA-RAG (Hwang et al., 2024): brongewogen retrieval +51% vs. Majority Voting in adversariale setting
- TREC Health Misinformation (Huang et al., 2025): +60% MAP via credibility-gewogen fusie
- Domeinspecifieke reranker (2024): +35% NDCG@10 door onderscheid peer-reviewed vs. niet

**Proposed evidence tier mapping:**

| content_type | evidence_tier | Rationale |
|---|---|---|
| `kb_article` | 1.00 | Handmatig geschreven — menselijke validatie (analogie: ThetaOS laag 10) |
| `pdf_document` | 0.90 | Officieel document, doorgaans gecureerd |
| `meeting_transcript` | 0.80 | Primaire bron — gesproken, maar onbewerkt |
| `1on1_transcript` | 0.80 | Idem |
| `email_thread` | 0.75 | Context-afhankelijk, hogere ruis |
| `graph_edge` | 0.70 | Graph-resultaten: sterk in relaties, zwak als absolute feiten |
| `web_crawl` | 0.60 | Laagste epistemische afstand tot bron, hoogste ruis |
| `unknown` | 0.55 | Onbekend type → defensief gewicht |

**Implementatie:** nieuw bestand `retrieval_api/services/evidence_tier.py` (zie sectie 2).

---

### Gap 2 — `assertion_mode` valt weg bij Qdrant upsert

**Huidige code:**
- `pg_store.create_artifact()`: `assertion_mode` opgeslagen in PostgreSQL ✓
- `ingest.py` regel 209: `extra_payload` bevat `assertion_mode` NIET ✗
- `qdrant_store._ALLOWED_METADATA_FIELDS`: `assertion_mode` staat er niet in ✗

**Wetenschappelijke basis:**
- SELF-RAG (Asai et al., ICLR 2024): reflection token `IsSup` (Is Supported) — het systeem
  beoordeelt of een antwoord ondersteund wordt door bewijs. `assertion_mode` is de
  menselijke variant van dit signaal: de auteur classificeert zelf of iets een feit,
  claim of hypothese is.
- TARSA (ACL 2021): stance-aware aggregatie — bevestigende en weerleggende bronnen moeten
  anders gewogen worden. `assertion_mode` is de pre-cursor voor dit onderscheid.

**Proposed assertion weight mapping (bijgewerkt o.b.v. [Assertion Mode Weights](../assertion-modes/assertion-mode-weights.md)):**

De oorspronkelijke spread (1.00–0.70 = 0.30) is te agressief voor de huidige classifiernauwkeurigheid (~85%). Maximum veilige spread: **0.10** als startpunt. Zie [Section 4.2](../assertion-modes/assertion-mode-weights.md#42-deriving-maximum-safe-spread-from-error-rate) voor de formele afleiding.

**v1 — Conservatief (aanbevolen startpunt):**

| assertion_mode | weight | Rationale |
|---|---|---|
| `factual` | 1.00 | Referentiegewicht |
| `procedural` | 1.00 | Instructies zijn niet minder betrouwbaar dan feiten — ander type, niet mindere kwaliteit |
| `quoted` | 0.98 | Minimale reductie: geattribueerde content is betrouwbaar maar indirect |
| `belief` / `claim` | 0.95 | Lichte reductie voor subjectieve content |
| `hypothesis` | 0.90 | Grootste reductie, nog steeds conservatief |
| `None` / onbekend | 0.97 | Ongelabeld krijgt voordeel van de twijfel — nooit penaliseren voor ontbrekende metadata |

**Totale spread: 0.10 (1.00 tot 0.90)**

**Alternatief v0 — Vlak (veiligst):** Alle modes op 1.00. Dit is de meest verdedigbare keuze tot empirische evaluatie aantoont dat differentiatie helpt. Zie [Einhorn & Hogarth argument](../assertion-modes/assertion-mode-weights.md#52-when-flat-weighting-outperforms-differentiated-weighting).

**Verbreed pas** naar 0.20 spread na: 200+ chunk classificatie-evaluatie, A/B retrieval test met >3% verbetering, gemeten classifiernauwkeurigheid >85% op Klai-content.

**Implementatie:** 2 regels in `ingest.py` + 1 regel in `qdrant_store.py` + opnemen in
`evidence_tier.py`.

---

### Gap 3 — Corroboratie: hoeveel bronnen noemen dezelfde entiteit?

**Huidige code:**
- `graph.py`: Graphiti slaat episodes op in FalkorDB, entiteiten worden geresolved ✓
- `graph_search.py`: search-resultaten bevatten geen corroboration count ✗
- Geen mechanisme om "hoeveel onafhankelijke bronnen bevestigen dit?" te beantwoorden ✗

**Wetenschappelijke basis:**
- Knowledge Vault (Google, KDD 2014): kern van het systeem is posterior confidence die
  stijgt als meerdere onafhankelijke extractors hetzelfde feit claimen. 271M triples met
  confidence ≥ 0.9 op productie-schaal.
- NELL (CMU, CACM 2018): iteratieve confidence propagatie, 91.3% KB-nauwkeurigheid.
- BayesRAG (Li et al., 2026): corroboratie formeel gemodelleerd als Bayesiaanse prior —
  +20% Recall@20 vs. vector retrieval baseline.
- **Kritische nuance (FEVER):** bronindependentie is de sleutelvariabele, niet het aantal.
  Drie near-duplicate chunks tellen epistemisch als één bron. Graphiti's entity resolution
  (deduplicatie van varianten) is hierbij onze bescherming.

**Proposed corroboration boost:**

| Onafhankelijke bronnen | boost factor | Rationale |
|---|---|---|
| 1 | 1.00 | Baseline — geen corroboratie |
| 2 | 1.10 | BayesRAG: eerste corroboratie heeft grootste effect |
| 3 | 1.18 | Diminishing returns |
| 4+ | 1.25 | Plafond — FEVER: >3 bronnen marginale winst |

**Implementatie:** Graphiti's `EdgeResult` bevat een `episodes` attribute (lijst van
brondocument-IDs). In `_convert_results` in `graph_search.py`: `len(set(episodes))` geeft
de corroboratiegraad. Voor Qdrant-resultaten: corroboratietelling via FalkorDB Cypher-query
als second-pass (optioneel, gated achter feature flag).

---

### Gap 4 — Chunk-volgorde naar LLM niet geoptimaliseerd

**Huidige code:**
- `retrieve.py` regel 122–137: chunks worden in reranker-volgorde doorgegeven ✗
- Meest relevante chunk kan op een middenpositie belanden

**Wetenschappelijke basis:**
- "Lost in the Middle" (Liu et al., Stanford 2023): LLM-context heeft U-vormige
  aandachtsverdeling. >30% performance degradatie wanneer relevant document midden in de
  context staat. Begin en einde worden beter verwerkt.

**Proposed fix:**

```
Voor LLM:  [sterkste, zwakke, zwakke, ..., op-één-na-sterkste]
           positie 0 = maximale LLM-aandacht
           positie -1 = tweede maximale LLM-aandacht
           midden = laagste LLM-aandacht → daarheen de minst kritische chunks
```

**Implementatie:** 6 regels in `retrieve.py` na evidence tier sortering.

---

### Gap 5 — Temporele ouderdom speelt geen rol in score

**Huidige code:**
- `ingested_at` staat in Qdrant payload (zie `qdrant_store.py` regel 177) ✓
- `valid_until` als hard cutoff ✓
- Geen graduele decay tussen "recent" en "verlopen" ✗

**Wetenschappelijke basis:**
- ThetaOS temporeel model: heet/warm/lauw/koud vervalsfunctie — expliciete toestandsovergang
  op basis van leeftijd, niet alleen hard cutoff.
- TrustGraph (productiesysteem): gecorrigeerde triples krijgen lagere confidence;
  frequenter bevestigde triples hogere.
- Implicatie: een KB-artikel van 2 jaar geleden kan nog steeds relevant zijn, maar een
  web-crawl van 2 jaar geleden is waarschijnlijk verouderd. Temporele decay verschilt
  per content_type.

**Proposed decay functie (bijgewerkt — conservatievere spread):**

De oorspronkelijke spread (1.00–0.70) overschrijdt dezelfde veilige grens als bij assertion mode. Met 4 multiplicatieve dimensies is de kans op minstens één misclassificatie ~48% (zie [compounding-analyse](../assertion-modes/assertion-mode-weights.md#52-the-multiplicative-compounding-problem)). Per-dimensie spread moet daarom **0.10–0.15** zijn, niet 0.30.

| Leeftijd | decay factor | ThetaOS-analogie |
|---|---|---|
| < 30 dagen | 1.00 | heet |
| 30–180 dagen | 0.95 | warm |
| 180–365 dagen | 0.90 | lauw |
| > 365 dagen | 0.85 | koud (minimum — nooit lager) |

**Totale spread: 0.15 (1.00 tot 0.85)**

**Nuance:** KB-artikelen krijgen een lagere decaysnelheid (gecureerd blijft relevant).
Web-crawls vervallen sneller. Dit is een tweede iteratie — v1 gebruikt één universele curve.

**Implementatie:** functie in `evidence_tier.py`, `ingested_at` doorgegeven via
`search.py`.

---

### Gap 6 — `ingested_at` en `assertion_mode` niet doorgegeven bij retrieval

**Huidige code:**
- `_search_knowledge` in `search.py` geeft `ingested_at` en `assertion_mode` niet terug
  in de result dict, ook al staan ze in de Qdrant payload ✗

**Fix:** 2 extra velden in de return dict van `_search_knowledge`.

---

## Implementatieplan: exacte bestandswijzigingen

### Nieuw bestand: `retrieval_api/retrieval_api/services/evidence_tier.py`

Centraliseert alle wetenschappelijke scorecorrecties. Elke dimensie is onafhankelijk
schakelbaar. Eén functie `apply()` als entry point.

```
Dimensies:
  1. content_type weight  (RA-RAG, TREC Health)
  2. assertion_mode weight (SELF-RAG, TARSA)
  3. temporal decay        (ThetaOS, TrustGraph)
  4. corroboration boost   (Knowledge Vault, NELL, BayesRAG)

Final score = base_score × content_weight × assertion_weight × decay × corr_boost
```

### Gewijzigd: `retrieval_api/retrieval_api/api/retrieve.py`

Stap 5.5 (na reranking, vóór ChunkResult): `evidence_tier.apply(reranked)`
Stap 5.6 (na evidence tier): `_order_for_llm(reranked)` — U-shape ordering

### Gewijzigd: `retrieval_api/retrieval_api/services/search.py`

`_search_knowledge` return dict: voeg `ingested_at` en `assertion_mode` toe uit payload.

### Gewijzigd: `retrieval_api/retrieval_api/services/graph_search.py`

`_convert_results`: extract `corroboration_count` uit Graphiti `EdgeResult.episodes`.

### Gewijzigd: `knowledge_ingest/knowledge_ingest/routes/ingest.py`

Regel ~212: voeg `assertion_mode` toe aan `extra_payload`.

### Gewijzigd: `knowledge_ingest/knowledge_ingest/qdrant_store.py`

`_ALLOWED_METADATA_FIELDS`: voeg `assertion_mode` en `corroboration_count` toe.

### Gewijzigd: `retrieval_api/retrieval_api/models.py`

`ChunkResult`: voeg `final_score: float | None`, `evidence_tier: float | None`,
`corroboration_count: int | None` toe — voor transparantie in logs en dashboard.

---

## Wat bewust NIET geïmplementeerd wordt (nu)

### Conflictdetectie
**Reden:** ACL 2025 (Soudani et al.) toont dat geen enkele bestaande uncertainty
estimation methode correct werkt in RAG-context. Vijf axioma's geformuleerd — geen
systeem voldoet aan alle vijf. Implementeren van conflictdetectie op basis van bestaande
methoden zou false positives genereren die het retrieval zouden degraderen.
**Beslissing:** defer tot er een betrouwbare methode bestaat.

### Confidence kalibratie (absolute waarden)
**Reden:** ICLR 2020 (Safavi & Koutra): populaire KGE-modellen zijn systematisch
miscalibrated zonder kalibratiestap. ACL 2025: het kalibratieprobleem is onopgelost voor
RAG. De gewichten in `evidence_tier.py` zijn **ordinale rangschikking** (relatief), geen
gekalibreerde absolute waarden. Ze bepalen de sorteervolgorde — niet een absolute
betrouwbaarheidsscore die aan gebruikers getoond wordt.
**Beslissing:** gebruik als relatief gewicht, nooit als absolute confidence score.

### Bronindependentie-meting voor Qdrant-chunks
**Reden:** Near-duplicate detec tie vereist extra vergelijkingslogica bij ingest. De
huidige content-hash dedup detecteert exacte duplicaten, maar niet semantische
near-duplicates (zelfde meeting, twee verslagen). Dit is een apart project.
**Beslissing:** defer. Graphiti's entity resolution is de proxie voor nu.

---

## Verwachte impact (op basis van onderzoek)

| Wijziging | Verwachte verbetering | Bron |
|---|---|---|
| Evidence tier op content_type | +51% relevantie in adversariale settings | RA-RAG 2024 |
| Evidence tier op content_type | +60% MAP | TREC Health 2025 |
| U-shape ordering | >30% minder degradatie voor middenpositie | Lost in the Middle 2023 |
| Corroboratie boost (graph) | +20% Recall@20 | BayesRAG 2026 |
| Assertion mode als signaal | Kwalitatief — geen directe meting beschikbaar | SELF-RAG 2024 |
| Temporele decay | Kwalitatief — minder verouderde content in top-k | ThetaOS model |

**Kanttekening:** deze cijfers zijn uit gecontroleerde experimenten op benchmark-datasets en vertegenwoordigen het maximale effect bij optimale gewichten.
De werkelijke verbetering in Klai hangt af van de distributie van content_types en
assertion_modes in de productie-kennisbank. Meting via de gaps-dashboard na uitrol.

---

## Assertion mode: eerlijke herziening van de "3 van 5" claim

### De discrepantie in de huidige code

De MCP-implementatie (`klai-knowledge-mcp/main.py`) gebruikt:
```python
VALID_ASSERTION_MODES = frozenset({"fact", "claim", "note"})  # 3 modes
```

De ingest-route (`knowledge_ingest/routes/ingest.py`) accepteert:
```python
("factual", "procedural", "quoted", "belief", "hypothesis")  # 5 modes
```

Dit zijn ook nog eens **verschillende namen** voor vergelijkbare concepten. Dit is een technische inconsistentie die opgelost moet worden ongeacht het epistemische debat.

### Sta ik nog achter de "3 van 5" claim?

**Deels — maar de oorspronkelijke formulering was te stellig.**

De claim was: "Een LLM kan betrouwbaar 3 van de 5 assertion modes onderscheiden." Er bestaat geen paper die dit specifiek zo bewijst. Het is een redenering vanuit aangrenzend onderzoek:

**Wel solide:**
- `procedural` is structureel herkenbaar (opsommingen, stappen, imperatieven). Geen epistemisch oordeel nodig.
- `quoted` is syntactisch herkenbaar (aanhalingstekens, "according to", bronvermelding).
- `hypothesis` heeft sterke hedging-markers ("might", "could suggest", "preliminary"). Hedging-detectie is redelijk betrouwbaar in NLP-literatuur.

**Moeilijk te onderscheiden:**
- `factual` vs. `belief`: dit is het kernprobleem van epistemische modaliteit in taalkunde. "The vaccine is effective" (factual) vs. "I think the vaccine is effective" (belief) — maar wat is "The evidence suggests the vaccine is effective"? Zelfs mensen zijn het hier niet over eens. Dit is geen LLM-beperking, dit is intrinsiek moeilijk.

**Conclusie:** De drie goed-onderscheidbare modes zijn `procedural`, `quoted`, `hypothesis`. De twee moeilijke zijn `factual` vs. `belief`. De MCP's keuze voor `fact/claim/note` is waarschijnlijk de **pragmatisch correcte** keuze, niet toevallig.

### Wat doen we als de LLM het niet weet?

Drie gevallen:

| Situatie | Aanbevolen gedrag | Gewicht |
|---|---|---|
| LLM classificeert `factual` of `belief` maar is onzeker | Accepteer classificatie, gebruik het conservatieve tabelgewicht | factual → 1.00, belief → 0.95 |
| LLM kan niet kiezen — retourneert `None` of lege string | Default naar `None` | 0.97 (benefit of the doubt) |
| LLM hallucineert een ongeldige mode | Validatie vangt dit op → val terug op `None` | 0.97 |

**Kernregel:** `None` krijgt 0.97 — nooit 0.00. Een niet-geclassificeerde chunk is niet per definitie slecht; het ontbreekt alleen aan extra metadata. Hard penaliseren zou nieuwe content (nog niet geclassificeerd) systematisch bevoordelen of benadelen op een manier die niet representatief is.

### Aanbeveling voor de implementatie

Gebruik de MCP-naamgeving (`fact`, `claim`, `note`) als de werkende standaard en voeg `procedural` en `quoted` toe als structureel herkenbare uitzonderingen:

| assertion_mode | Detecteerbaarheid | weight (v1 conservatief) |
|---|---|---|
| `fact` | Middel — vereist epistemisch oordeel | 1.00 |
| `procedural` | Hoog — structurele markers | 1.00 |
| `quoted` | Hoog — syntactische markers | 0.98 |
| `note` | Laag — vrije vorm | 0.97 |
| `claim` | Middel — hedging + subjectiviteit | 0.95 |
| `None` / onbekend | n.v.t. | 0.97 |

**Technische actie:** synchroniseer de naamgeving tussen MCP en ingest-route. Kies één vocabulaire. De MCP-naamgeving (`fact/claim/note`) is beknopter; uitbreiden met `procedural` en `quoted` als optionele categorieën is de schoonste weg.

---

## Evidence-stratificatie in een generiek kennissysteem

### Het spanningsveld

Klai begint als een domeinspecifiek kennissysteem (kennisbeheer voor professionals) maar wordt generiek: elke organisatie met andere content-types, andere epistemische normen, andere risicobereidheid.

Het risico: harde gewichten (`kb_article = 1.00`) worden zinloos of zelfs schadelijk in domeinen waar `kb_article` niet bestaat of waar iets anders de gouden standaard is.

### De oplossing: drie lagen

**Laag 1 — Universeel schema (niet aanpasbaar)**

De *dimensies* zijn voor alle organisaties identiek. Elke chunk heeft:
- `content_type` (wat is het)
- `assertion_mode` (hoe sterk wordt het beweerd)
- `ingested_at` (wanneer)
- `corroboration_count` (hoeveel onafhankelijke bronnen)

Dit schema is universeel omdat deze dimensies *altijd* epistemisch relevant zijn, ongeacht domein. Een ziekenhuis-KB en een juridisch archief hebben allebei baat bij het onderscheid tussen een protocol (procedural) en een aantekening (note).

**Laag 2 — Evidence-profiel (org-niveau configureerbaar)**

De *gewichten* zijn per organisatie instelbaar:

```python
# Standaard profiel (generiek)
DEFAULT_EVIDENCE_PROFILE = {
    "content_weights": {
        "kb_article": 1.00,
        "pdf_document": 0.90,
        ...
    },
    "assertion_weights": {...},
    "temporal_decay": "standard",  # of "fast", "slow", "none"
    "corroboration_boost": "standard",
}

# Medisch profiel — NB: spreads blijven binnen veilige grenzen (max 0.20)
MEDICAL_EVIDENCE_PROFILE = {
    "content_weights": {
        "clinical_guideline": 1.00,   # eigen type
        "case_report": 0.90,
        "patient_note": 0.85,
    },
    "assertion_weights": {
        "fact": 1.00,
        "hypothesis": 0.85,           # breder dan standaard, maar binnen max safe spread
    },
    "temporal_decay": "fast",         # medische richtlijnen verouderen snel
}
```

Klai levert een bibliotheek van profielen (`default`, `medical`, `legal`, `engineering`). Organisaties kiezen een profiel als startpunt en passen aan.

**Laag 3 — Adaptief leren (toekomst)**

Per organisatie kan het systeem gewichten bijstellen op basis van feedback: welke chunks leidden tot goede antwoorden, welke niet? Dit is het BayesRAG-pad — posterior updates op basis van observatie.

**Bewust niet in v1:** vereist gelabelde feedback-data die Klai nu nog niet heeft.

### Hoe dit de gelaagdheid bewaart in een generiek systeem

| Generiek risico | Oplossing |
|---|---|
| "Mijn org heeft geen `kb_article`" | Profiel definieert eigen content-types; fallback naar `unknown` (0.55) bij onbekend type |
| "In mijn domein zijn hypotheses juist waardevol" | Assertion-gewichten aanpasbaar per profiel |
| "Temporele decay werkt niet in juridische archivering" | `temporal_decay: "none"` uitschakelbaar per profiel |
| "De standaard gewichten kloppen niet voor ons" | A/B-testbaar: profiel A vs. profiel B, meetbaar via retrieval metrics |

### Wat dit betekent voor de huidige implementatie

De eerste implementatie (v1) kan harde gewichten gebruiken — dat is correct als startpunt. De architectuur moet echter zo zijn dat de gewichten **uit een configuratie-object komen**, niet hardcoded zijn. Dan is het upgrade-pad naar profielen een non-breaking refactor.

Concreet: `evidence_tier.py` krijgt een `profile: dict = DEFAULT_EVIDENCE_PROFILE` parameter. V1 gebruikt altijd de default. V2 slaat het org-profiel op in PostgreSQL en laadt het bij retrieval.

---

## Relatie tot ThetaOS

ThetaOS bewijst dat evidence-stratificatie werkt op persoonlijk niveau:
84% van de gemeten dagen heeft 2+ bevestigende bronnen, 170.000 gemeten synapsen.

Klai's implementatie is de organisatorische pendant:
- ThetaOS laag 10 (menselijke bevestiging) → `kb_article` evidence tier 1.00
- ThetaOS laag 7 (cross-source corroboratie) → corroboration boost via Graphiti episodes
- ThetaOS temporeel model (heet/warm/lauw/koud) → temporal decay op `ingested_at`
- ThetaOS valentie (positief/negatief) → `assertion_mode` (factual vs. hypothesis)

Wat ThetaOS niet heeft en Klai wél: HyPE question-alignment vectoren, cross-attention
reranking, en multi-tenant isolatie. Wat ThetaOS heeft en Klai nog niet: een
kalibratiestap voor de absolute gewichten, bronindependentie-meting.

---

## Belangrijke nuancering: assertion mode gewichten

Het [Assertion Mode Weights](../assertion-modes/assertion-mode-weights.md) onderzoek (2026-03-30) nuanceert de gewichten in Gap 2 van dit document:

- De hier voorgestelde spread van 0.30 (1.00 tot 0.70) is **te agressief** voor de huidige classifiernauwkeurigheid (~85%)
- Maximum veilige spread: 0.20 (minimum gewicht 0.80)
- Aanbeveling: start met **vlakke gewichten** (allemaal 1.00) of conservatief (spread max 0.10)
- Verbreed pas na een 200+ chunk classificatie-evaluatie + A/B retrieval test

Zie ook: [Corroboration Scoring](../corroboration/corroboration-scoring.md) — nuanceert Gap 3: implementatie uitgesteld tot prerequisites zijn gebouwd.
