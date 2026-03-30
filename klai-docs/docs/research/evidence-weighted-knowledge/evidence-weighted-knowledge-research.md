# Evidence-Weighted Knowledge: Onderzoek

> Aangemaakt: 2026-03-29
> Status: Compleet
> Aanleiding: Vergelijking ThetaOS (synaptische gelaagdheid) met Klai kennissysteem

---

## Onderzoeksvraag

Werkt kennisretrieval beter als elk stukje kennis een gewicht krijgt op basis van:
1. De betrouwbaarheid van de bron (kort vs. lang causale keten naar werkelijkheid)
2. Hoeveel onafhankelijke bronnen hetzelfde bevestigen (cross-source validatie)
3. Het type bewijs (feit, hypothese, afleiding)

Onderzoek eerst onafhankelijk van AI/RAG. Dan gekoppeld aan kennissystemen en RAG.

---

## Synthese

### Het korte antwoord

**Ja, het werkt. Maar de manier van implementatie bepaalt alles.**

Alle vier domeinen geven hetzelfde signaal: brongewogen retrieval verbetert kwaliteit meetbaar. Tegelijk zijn er in alle vier domeinen bekende failure modes die de claims relativeren. Het principe is bewezen; de kalibratie is het openstaande probleem.

### Wat de literatuur als geheel zegt

**Drie principes zijn solide:**

1. **Meer onafhankelijke bronnen die hetzelfde bevestigen = sterker bewijs** — FEVER toont aan dat 12–17% van feitenclaims structureel meerdere bronnen nodig heeft. BayesRAG formaliseert dit als Bayesiaanse prior en haalt +20% Recall@20. Het werkt, maar alleen als bronnen echt onafhankelijk zijn. Drie near-duplicate chunks tellen als één bron.

2. **Bronautoriteit verbetert retrieval** — TREC Health Misinformation: +60% MAP, +30% NDCG door credibility-gewogen fusie. PageRank is 25 jaar commercieel bewezen. De methode werkt; de kwetsbaarheid is manipulatie (linkspam, populairiteitsvertekening).

3. **Confidence scores in knowledge graphs correleren met nauwkeurigheid** — Knowledge Vault (Google, 2014): 271 miljoen triples met >90% confidence. NELL: 91,3% KB-nauwkeurigheid. De ordening klopt; de absolute waarden zijn zonder kalibratiestap niet betrouwbaar (ICLR 2020: populaire KGE-modellen zijn systematisch miscalibrated).

**Één kritisch openstaand probleem:**

ACL 2025 toont aan dat geen enkele bestaande uncertainty estimation methode correct werkt in RAG-context. Vijf axioma's geformuleerd — geen systeem voldoet aan alle vijf. Dit ondermijnt niet het principe, maar wel de aanname dat bestaande confidence scores direct bruikbaar zijn.

### Wat bewezen werkt vs. wat nog open is

| Claim | Status | Sterkste bewijs |
|---|---|---|
| Cross-source corroboratie verbetert feitverificatie | **Bewezen** | FEVER, BayesRAG (+20% Recall@20) |
| Bronautoriteit verbetert retrieval kwaliteit | **Bewezen** | TREC Health (+60% MAP), PageRank (commercieel) |
| Confidence scores in KGs correleren met juistheid | **Bewezen** | Knowledge Vault, NELL (91,3% KB-accuracy) |
| Meer bronnen = altijd beter | **Weerlegd** | Lost in the Middle: >30% degradatie bij meer docs |
| Confidence scores zijn direct bruikbaar in RAG | **Twijfelachtig** | ACL 2025: geen methode voldoet aan alle axioma's |
| Evidence type als retrievalsignaal verbetert RAG | **Veelbelovend maar onbewezen** | PragAURA (2025), geen brede validatie |
| Brongewicht tonen aan gebruiker verbetert beslissingen | **Weerlegd** | CHI 2024, MDPI 2024: geen significant effect |

---

## Topic 1: Cross-source evidence corroboration

### Kernvraag
Produceert meerdere onafhankelijke bronnen die hetzelfde bevestigen meetbaar betere verificatieresultaten dan één bron?

### Antwoord
**Ja, met belangrijke randvoorwaarden.** Multi-source corroboratie verbetert feitverificatie consistent, maar de manier van samenvoegen telt zwaarder dan het aantal bronnen.

### Sleutelbevindingen

**FEVER dataset (Thorne et al., 2018)**
- 185.445 claims op basis van Wikipedia
- 12–15% van claims vereist cross-pagina evidence (meerdere onafhankelijke documenten) — structureel, niet optioneel
- 16,82% vereist multi-zin bewijs
- Zonder externe evidence: ChatGPT haalt slechts 45,78% op multi-hop claims

**BayesRAG (2026)**
- Dempster-Shafer evidence theory toegepast op multimodaal RAG
- Corroboratie formeel als Bayesiaanse prior gemodelleerd
- +20% Recall@20 vs. vector retrieval baseline
- +11,4% overall LLM score

**Multi-hop iteratief retrieval (Papelo, FEVER 2024)**
- +4,5 procentpunt label accuracy
- +15,5 punten AVeriTeC score vs. single-pass retrieval

**Ensemble verificatie (CliVER, 2024)**
- +3–11% F1 over individuele modellen bij klinische claim verificatie

### Failure modes
**"Lost in the Middle" (Liu et al., Stanford 2023)**
Meer documenten toevoegen aan RAG kan accuracy *verlagen*. >30% performance degradatie wanneer relevant document midden in context staat. U-vormige curve: begin en einde van context worden beter verwerkt.

**Ruis bij ongestructureerde retrieval**
Top-4 context vs. top-1: minimale verbetering (0.292 vs. 0.287) bij ongefiltered retrieval. Meer bronnen zonder kwaliteitsfilter introduceert ruis.

### Kritische nuance
**Bronindependentie is de sleutelvariabele, niet het aantal bronnen.** Drie near-duplicate chunks tellen epistemisch als één bron. Near-duplicaatdetectie is vereiste voorwaarde voor effectieve corroboratie.

### Top papers
1. [FEVER (Thorne et al., 2018)](https://arxiv.org/abs/1803.05355)
2. [Lost in the Middle (Liu et al., 2023)](https://arxiv.org/abs/2307.03172)
3. [BayesRAG (Li et al., 2026)](https://arxiv.org/abs/2601.07329)
4. [TARSA stance-aware aggregation (ACL 2021)](https://aclanthology.org/2021.acl-long.128/)
5. [Team Papelo FEVER 2024](https://arxiv.org/abs/2411.05762)

---

## Topic 2: Source credibility en provenance in IR

### Kernvraag
Verbetert weging op bronbetrouwbaarheid/autoriteit de retrievalkwaliteit meetbaar?

### Antwoord
**Ja, aantoonbaar — maar met bekende exploiteerbare failure modes.**

### Sleutelbevindingen

**PageRank (Brin & Page, 1998)**
Linkautoriteit vs. keyword-only ranking: kwalitatief aangetoond dat PageRank precisie op navigational queries dramatisch verbeterde vs. AltaVista. DOJ-rechtszaak 2024 bevestigde: PageRank is nog steeds kernkwaliteitssignaal bij Google.

**TREC Health Misinformation Track (Huang et al., 2025)**
Beste beschikbare kwantitatieve bewijs voor credibility-gewogen retrieval:
- **+60% MAP** vs. beste single-system baseline
- **+30% NDCG_UCC** (meet gelijktijdig nuttig + correct + geloofwaardig)
- Methode: AH-clustering-gebaseerde fusie van 20 IR systemen

**Domeinspecifieke reranker op peer-reviewed bronnen (2024)**
- **+35% NDCG@10** voor academische zoekqueries vs. general-purpose reranker
- Primair door onderscheid peer-reviewed vs. niet-peer-reviewed

**Credibility reranking blogs (Springer, 2011)**
Post-level indicatoren (spellingkwaliteit, lengte, tijdigheid) als reranking-signalen: significante verbetering in MRR en P@5.

### Betrouwbare autoriteitssignalen
| Signaal | Bewijs |
|---|---|
| Inkomende links + kwaliteit (PageRank) | Hoog — commercieel bewezen |
| Trustafstand van seed-sites (TrustRank) | Hoog — VLDB 2004 |
| Peer-review status | Hoog in academisch IR |
| Schrijfkwaliteit (spelling, structuur, lengte) | Matig — blog retrieval |
| Institutionele affiliatie auteur | Matig |

### Failure modes
- **Linkspam**: PageRank direct aanleiding voor TrustRank (2004) als tegenmaatregel
- **Populairiteitsvertekening**: rijken-worden-rijker dynamiek onderdrukt niche-kwaliteitsbronnen
- **Scope mismatch**: hoge autoriteit in domein A helpt niet voor domein B
- **Credibility labels tonen aan gebruikers werkt niet**: MDPI 2024 — geen significant gedragseffect

### Top papers
1. [PageRank (Brin & Page, 1998)](http://ilpubs.stanford.edu:8090/422/)
2. [HITS (Kleinberg, 1999)](https://www.cs.cornell.edu/home/kleinber/auth.pdf)
3. [TrustRank (Gyöngyi & Garcia-Molina, VLDB 2004)](https://www.vldb.org/conf/2004/RS15P3.PDF)
4. [Evaluation Measures for Relevance + Credibility (Clarke et al., ICTIR 2017)](https://arxiv.org/abs/1708.07157)
5. [Combating Health Misinformation via Credible Retrieval (Huang et al., SAGE 2025)](https://journals.sagepub.com/doi/10.1177/14604582251388860)

---

## Topic 3: Probabilistische knowledge graphs

### Kernvraag
Hoe volwassen is het veld van confidence scores op knowledge graph triples? Werkt het?

### Antwoord
**Volwassen en bewezen op productie-schaal. Kalibratie van absolute scores is het openstaande probleem.**

### Sleutelbevindingen

**Knowledge Vault (Google, KDD 2014)**
- 1,6 miljard triples totaal
- 271 miljoen triples met confidence ≥ 0,9
- Meerdere onafhankelijke extractors: posterior confidence stijgt bij corroboratie
- 38× meer confident facts dan elk vergelijkbaar systeem op dat moment

**NELL (CMU, 2010–heden)**
- Continu draaiend systeem, 15+ jaar
- Confidence propagatie: iteratief EM-algoritme, beliefs versterken elkaar
- Resultaten: 87% precisie na 6 maanden, 91,3% bij recente benchmark
- 2,81 miljoen high-confidence beliefs gepubliceerd (van 120M totaal)
- Bekende failure mode: foutpropagatie ("internet cookies = baked goods")

**Calibratie-probleem (Safavi & Koutra, ICLR 2020)**
- Populaire KGE-modellen (TransE, ComplEx) zijn systematisch miscalibrated
- TransE: onderschat probabiliteiten structureel
- Fix: Platt scaling of isotone regressie → "well-calibrated models"
- **Conclusie: ordinale volgorde klopt; absolute waarden niet zonder kalibratiestap**

**BEUrRE probabilistic box embeddings (NAACL 2021)**
- Geometrische representatie: entiteiten als hyperrechthoeken
- Overlappend volume = probabiliteit
- Beter gekalibreerd dan UKGE

**TrustGraph (productiesysteem)**
- RDF-star reification: per triple bron + tijdstip + confidence score
- Zelf-verbeterend: frequenter bevestigde triples krijgen hogere confidence
- Gecorrigeerde triples krijgen lagere confidence

### Rijpheid van het veld
| Dimensie | Status |
|---|---|
| Theoretische basis | Volwassen |
| Productiesystemen op schaal | Bewezen (Knowledge Vault, NELL, TrustGraph) |
| Kalibratie van scores | Actief onderzoek — fixes beschikbaar maar niet standaard |
| Multi-hop query-answering over onzekere KGs | Opkomend |
| LLM-integratie | Nieuw (2024–2025), LLMs zijn overconfident |

### Top papers
1. [Knowledge Vault (Dong et al., KDD 2014)](https://www.cs.ubc.ca/~murphyk/papers/kv-kdd14.pdf)
2. [Never-Ending Learning (Mitchell et al., CACM 2018)](https://burrsettles.pub/mitchell.cacm18.pdf)
3. [Probability Calibration for KGE Models (Safavi & Koutra, ICLR 2020)](https://openreview.net/forum?id=S1g8K1BFwS)
4. [BEUrRE Probabilistic Box Embeddings (NAACL 2021)](https://aclanthology.org/2021.naacl-main.68.pdf)
5. [Uncertainty Management in KGs Survey (arXiv 2024)](https://arxiv.org/html/2405.16929v1)

---

## Topic 4: Confidence en uncertainty in RAG

### Kernvraag
Verbetert het gebruik van confidence/uncertainty-signalen in RAG de antwoordkwaliteit?

### Antwoord
**Ja — maar de implementatie bepaalt alles, en bestaande methoden hebben een fundamenteel kalibratieprobleem in RAG-context.**

### Sleutelbevindingen

**SELF-RAG (Asai et al., ICLR 2024 Oral — top 1%)**
Vier reflection tokens ingebakken in het model:
- `IsRel`: is het opgehaalde relevant?
- `IsSup`: wordt het antwoord ondersteund door evidence?
- `IsUse`: is het totale antwoord nuttig?
- `Retrieve`: moet ik iets ophalen?

Resultaat: SELF-RAG (7B) overtreft ChatGPT op Open-domain QA, redeneren, feitverificatie. Elk afzonderlijk token draagt aantoonbaar bij (ablation).

**RA-RAG (Hwang et al., 2024) — bronbetrouwbaarheid als retrievalgewicht**
- Label-vrije iteratieve schatting van bronbetrouwbaarheid
- Weighted Majority Voting op basis van geschatte betrouwbaarheid
- Pearson correlatie geschatte vs. oracle betrouwbaarheid: **0,991**
- Adversariale setting (7 vijandige + 2 betrouwbare bronnen):
  - RA-RAG: 0,558 op NQ
  - Vanilla RAG: 0,294
  - Majority Voting: 0,327
- TQA adversarial: **+51% vs. Majority Voting**
- 99,1% minder tokens dan volledige bronscreening

**CRAG — Corrective RAG (Yan et al., 2024)**
T5-large evaluator scoort elk document; drempelwaarde triggert Correct/Incorrect/Ambiguous actie.
- Plug-and-play compatibel met bestaande pipelines
- **Failure mode**: hoge evaluator-confidence ≠ juist antwoord. Religion-queries: 98% "Correct" → 5% uiteindelijke accuracy.

**Bayesian RAG (Frontiers in AI, 2025)**
Monte Carlo Dropout op query- én documentembeddings. Scoringsfunctie `Sᵢ = μᵢ − λ·σᵢ`.
- Precision@3: +20,6% vs. BM25
- NDCG@10: +25,4% vs. BM25
- Hallucinatiereductie: 27,8%
- ECE verbeterd met 26,8%
- Kanttekening: gevalideerd op slechts twee financiële documenten

### Kritisch openstaand probleem
**"Why Uncertainty Estimation Methods Fall Short in RAG" (Soudani et al., ACL 2025)**
- Vijf axioma's geformuleerd voor correcte UE in RAG-context
- **Geen enkele bestaande methode voldoet aan alle vijf**
- White-box (token-entropie) en black-box methoden falen beide op specifieke axioma's
- Confidence scores die RAG-systemen rapporteren zijn niet betrouwbaar als maat voor antwoordcorrectheid

### Failure modes
| Mode | Beschrijving |
|---|---|
| Hoge confidence, fout antwoord | CRAG: 98% confidence → 5% accuracy (religion queries) |
| UE-methoden niet geldig in RAG | ACL 2025: geen methode voldoet aan alle axioma's |
| Accuracy-confidence trade-off | Configuratie die confidence maximaliseert geeft laagste accuracy |
| Positiebias | Lost in the Middle: relevant document midden in context = >30% degradatie |
| Adversarial poisoning | Indirect Prompt Injection via opgehaalde bronnen |

### Top papers
1. [SELF-RAG (Asai et al., ICLR 2024)](https://arxiv.org/abs/2310.11511)
2. [RA-RAG (Hwang et al., 2024)](https://arxiv.org/abs/2410.22954)
3. [Why UE Methods Fall Short in RAG (Soudani et al., ACL 2025)](https://arxiv.org/abs/2505.07459)
4. [CRAG (Yan et al., 2024)](https://arxiv.org/abs/2401.15884)
5. [Bayesian RAG (Frontiers in AI, 2025)](https://www.frontiersin.org/journals/artificial-intelligence/articles/10.3389/frai.2025.1668172/full)

---

## Openstaande vragen

1. **Werkt evidence-type als retrievalsignaal in RAG?** Veelbelovend (PragAURA 2025, SELF-RAG) maar geen brede empirische validatie op organisatorische kennisbases.

2. **Hoe meet je bronindependentie?** Near-duplicate chunks inflateren het corroboratiegetal. Deduplicatie en independentieschatting zijn onderbelicht.

3. **Is het kalibratieprobleem oplosbaar in productie-RAG?** ICLR 2020 geeft fixes voor KGs (Platt scaling). ACL 2025 zegt dat bestaande methoden voor RAG fundamenteel ongeschikt zijn. Gap.

4. **Generaliseert credibility-gewogen retrieval buiten gezondheidsdomein?** TREC Health laat +60% MAP zien. Of dit geldt voor algemene organisatorische kennis is niet onderzocht.

5. **Populairiteitsvertekening vs. kwaliteit**: hoe voorkom je dat autoriteitsgewicht bekende bronnen amplificatie geeft ongeacht relevantie?

---

## Meest relevante papers (totaaloverzicht)

| Paper | Domein | Kernresultaat |
|---|---|---|
| FEVER (Thorne et al., 2018) | Feitverificatie | 12–17% claims vereist structureel meerdere bronnen |
| Lost in the Middle (Liu et al., 2023) | RAG | >30% degradatie bij meer documenten naïef toegevoegd |
| BayesRAG (2026) | RAG | +20% Recall@20 via Bayesiaanse corroboratie |
| Knowledge Vault (Google, KDD 2014) | Knowledge graphs | 271M triples met >90% confidence op productie-schaal |
| NELL (CMU, CACM 2018) | Knowledge graphs | 91,3% KB-nauwkeurigheid via iteratieve confidence propagatie |
| Probability Calibration for KGE (ICLR 2020) | Knowledge graphs | Populaire KGE-modellen systematisch miscalibrated; fixes beschikbaar |
| TREC Health Misinformation (Huang et al., 2025) | IR | +60% MAP, +30% NDCG via credibility-gewogen fusie |
| SELF-RAG (ICLR 2024 Oral) | RAG | Reflection tokens verbeteren factualiteit; versloeg ChatGPT |
| RA-RAG (2024) | RAG | +51% vs. Majority Voting in adversariale setting |
| Why UE Falls Short in RAG (ACL 2025) | RAG | Geen bestaande methode voldoet aan alle 5 axioma's voor UE in RAG |
| TrustRank (VLDB 2004) | IR | Seed-gebaseerde trustpropagatie als tegenmaatregel voor linkspam |
| PageRank (Stanford, 1998) | IR | Linkautoriteit als kwaliteitssignaal — 25 jaar commercieel bewezen |
