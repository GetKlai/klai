# Research toegepast: ThetaOS en Klai

> Aangemaakt: 2026-03-29
> Gebaseerd op: [Evidence-Weighted Knowledge Research](evidence-weighted-knowledge.md)
> Doel: wat zegt de literatuur over wat ThetaOS en Klai doen?
> Onderdeel van: [Research Synthesis](../README.md)

---

## Deel I — Research toegepast op ThetaOS

### Wat de research valideert

**De 10-laags hiërarchie — het principe klopt**

Knowledge Vault (Google, KDD 2014) doet precies hetzelfde: meerdere onafhankelijke extractors wijzen confidence toe per triple, posterior stijgt bij corroboratie. NELL doet het iteratief over 15 jaar. Het principe van gedifferentieerde bewijskracht per brontype is niet alleen theoretisch — het is 11 jaar geleden al op 1,6 miljard triples bewezen in productie.

**Laag 7 (datum-coincidentie) — het sterkste onderdeel**

FEVER toont aan dat 12–17% van feitenclaims structureel meerdere onafhankelijke bronnen nodig heeft. BayesRAG formaliseert precies wat ThetaOS intuïtief bouwt: corroboratie als Bayesiaanse prior — meer onafhankelijke bronnen = hogere posterior confidence (+20% Recall@20 in productie). Dit is het meest direct gevalideerde onderdeel van het model.

**Laag 10 (menselijke bevestiging) — het meest waardevolle signaal**

RA-RAG meet de correlatie tussen geschatte en menselijke betrouwbaarheidsoordelen: 0,991. Bijna perfecte overeenkomst. SELF-RAG (ICLR 2024, top 1%) bouwt reflection tokens als geautomatiseerde benadering van precies dit. De research bevestigt: menselijke validatie is het krachtigste kwaliteitssignaal. Laag 10 is niet soft — het is empirisch de meest waardevolle laag.

---

### Wat de research nuanceert of uitdaagt

**De absolute percentages zijn niet gekalibreerd**

ThetaOS schrijft: bankafschriften = 100% zekerheid, foto = 95%, tekst = 90–99%. ICLR 2020 toont aan dat confidence scores zonder kalibratiestap niet betrouwbaar zijn als absolute waarden — ook niet bij Knowledge Vault of NELL. TransE onderschat probabiliteiten structureel; ComplEx overschat ze.

De percentages zijn intuïtieve schattingen, geen empirisch gekalibreerde waarden. Het bankafschrift is 100% zeker als *record* — maar de interpretatie ("ik was in Deventer met Jan omdat ik bij Jackie's pintte") bevat inferentie die de zekerheid verlaagt. ThetaOS maakt dit onderscheid zelf al bij laag 3 (kruisvalidator, leugendetector), maar verwerkt het niet terug in de laag-confidence.

**Bronindependentie is de kritische variabele — niet het laagaantal**

De "Lost in the Middle" bevinding en near-duplicate studies zeggen hetzelfde: het gaat niet om het aantal bronnen maar om hun onafhankelijkheid.

Het weekbericht en de blog beschrijven soms hetzelfde evenement. Beide zijn laag 6. Beide verhogen de myeline. Maar ze zijn niet volledig onafhankelijk — zelfde auteur, zelfde herinnering, zelfde framing. De effectieve corroboratiewaarde is lager dan twee onafhankelijke bronnen. ThetaOS telt ze als gelijkwaardig.

**Valentie zit op de verkeerde plek in het model**

TARSA (ACL 2021) toont aan dat stance-aware aggregatie zwaarder weegt dan laagaantal. Bevestigende en weerleggende bronnen zijn niet hetzelfde — ze moeten anders gewogen worden.

ThetaOS heeft valentie (positief/negatief) in de diamantlaag. Maar de myeline-score (gelaagdheid × frequentie × completeness) is valentie-blind. Een relatie die op vijf lagen negatief bevestigd wordt, heeft dezelfde myeline als een relatie die op vijf lagen positief bevestigd wordt. De research zegt: dat is een fout. Contra-evidentie zou de bewijskracht moeten verlagen, niet verhogen.

**Foutpropagatie via laag 9**

NELL's bekendste probleem: fouten propageren door het geloofsnetwerk ("internet cookies = baked goods"). ThetaOS's laag 9 (patroonherkenning) is expliciet de laagste confidence. Maar als laag 9 patronen input geven aan laag 7 (coincidentiedetectie), kunnen fouten opwaarts propageren. Een verkeerd afgeleid patroon dat coincidenties "bevestigt" versterkt zichzelf. ThetaOS adresseert dit risico niet.

**Hoge confidence kan systematisch fout zijn in specifieke domeinen**

CRAG toont: evaluator-confidence 98% "Correct" bij religion-queries → 5% uiteindelijke accuracy. Hoge zekerheid in één domein garandeert niets in een ander. Bankdata is 100% zeker als financieel record — maar als proxy voor sociale nabijheid (laag 3 als relatiekaart) zit er een inferentiesprong in die de feitelijke zekerheid verlaagt.

---

### Wat de research toevoegt dat ThetaOS niet adresseert

**Een kalibratiestap is vereist**

De 10 percentages moeten empirisch gevalideerd worden. Methode: neem een steekproef van bekende feiten, meet hoe vaak elke laag het correct heeft. Dat geeft gekalibreerde waarden in plaats van intuïtieve schattingen.

**Conflictdetectie als eerste-klas signaal**

MADAM-RAG (2025) en TARSA (2021) zeggen: conflicten zijn *waardevoller* dan bevestigingen voor kwaliteitscontrole. ThetaOS heeft anti-coincidentie (laag 7) en de bank als leugendetector (laag 3) — maar een weerlegging uit één betrouwbare bron zou expliciet zwaarder moeten wegen dan drie bevestigingen uit lagere lagen. Dit is geen rekenregel in het huidige model.

**Retrieval-volgorde van het dossier**

Als ThetaOS een dossier genereert voor Peter Ros — honderden datapunten — in welke volgorde staan die? Lost in the Middle (Stanford 2023): als de meest relevante informatie midden in een lange lijst staat, daalt de bruikbaarheid drastisch (>30% degradatie). Dit is de directe toepassing op hoe ThetaOS dossiers serveert aan een LLM of aan een mens.

---

### Scorekaart ThetaOS

| Onderdeel | Research-oordeel |
|---|---|
| 10-laags hiërarchie als principe | Gevalideerd — Knowledge Vault, NELL doen hetzelfde |
| Laag 7 (cross-source corroboratie) | Sterk gevalideerd — BayesRAG, FEVER bevestigen het |
| Laag 10 (menselijke validatie) | Sterk gevalideerd — RA-RAG 0,991 correlatie |
| Absolute percentages per laag | Niet gekalibreerd — ordinale volgorde klopt, absolute waarden niet |
| Myeline valentie-blind | Uitgedaagd — contra-evidentie moet anders wegen (TARSA) |
| Bronindependentie niet gemeten | Gap — near-duplicate bronnen inflateren myeline onterecht |
| Foutpropagatie laag 9 → laag 7 | Niet geadresseerd — NELL toont dit risico |
| Kalibratiestap | Ontbreekt — noodzakelijk voor betrouwbare absolute scores |
| Conflictdetectie als rekenregel | Ontbreekt — research zegt dit is waardevoller dan bevestiging |
| Dossier-volgorde bij retrieval | Niet geadresseerd — Lost in the Middle risico |

---

---

## Deel II — Research toegepast op Klai

### Wat de research valideert in Klai

**Drie-vector retrieval (dense + sparse + HyPE)**

Hybride retrieval via RRF-fusie van drie signalen wordt direct ondersteund door de TREC Health literatuur: fusie van meerdere retrieval-systemen gaf +60% MAP. Dense-only retrieval is zwakker dan fusion. Klai's drie-vector aanpak (semantisch, keyword, question-alignment) is de juiste architectuurkeuze.

HyPE specifiek: de research op vocabulary gap (SELF-RAG, BayesRAG) bevestigt dat question-answer alignment een sterker retrievalsignaal is dan content-to-content similarity. Dezelfde vraag matcht beter op een "wat beantwoordt dit stuk?" vector dan op de tekst zelf.

**Contextual Retrieval prefix**

Anthropic's eigen studie: -67% top-20 retrieval failure rate door contextuele prefixes. Dit is het meest impactvolle enkelvoudige verrijkingsstap in de literatuur. Klai implementeert dit correct via `text_enriched = context_prefix + chunk`.

**Reranker**

Cross-attention reranking (bge-reranker-v2-m3) is gevalideerd als een precisieslag bovenop vector similarity. Meerdere benchmarks tonen consistent verbetering. De keuze om reranking toe te voegen na vector search is empirisch correct.

**Fail-open ontwerp**

Als retrieval mislukt, gaat de chat door ongewijzigd. Dit is de juiste keuze: reliability boven precision. De research toont dat incomplete retrieval beter is dan een gebroken interface.

**Gap detection**

Hard en soft gaps worden gelogd en weergegeven in het dashboard. Dit is een feedbackloop die ontbreekt in de meeste RAG-systemen. De research (SELF-RAG's `IsUse` token) valideert het principe: meet wanneer retrieval tekortschiet.

---

### Wat de research uitdaagt in Klai

**Alle chunks wegen gelijk — de grootste gap**

Klai heeft `assertion_mode` (factual, belief, hypothesis) en `content_type` (kb_article, meeting_transcript, web_crawl). Beide staan opgeslagen. Geen van beide beïnvloedt de retrievalscore.

RA-RAG toont: brongewogen retrieval geeft +51% nauwkeurigheid in adversariale settings. TREC Health: +60% MAP via credibility-gewogen fusie. Een handmatig geschreven KB-artikel en een automatisch gecrawlde webpagina wegen nu identiek in Klai's reranker. Dat is aantoonbaar suboptimaal.

**Cross-source corroboratie wordt niet gemeten**

Als twee documenten onafhankelijk hetzelfde besluit vermelden, weet Klai's retrieval dat niet. FalkorDB/Graphiti extraheren entiteiten maar berekenen geen corroboratiegraad per entiteit.

Knowledge Vault's kern is precies dit: posterior confidence stijgt als meer extractors hetzelfde feit claimen. NELL doet het iteratief. Klai heeft de infrastructuur (FalkorDB) maar gebruikt het mechanisme niet.

**Conflicterende chunks worden niet gedetecteerd**

Als twee opgehaalde chunks elkaar tegenspreken, gaat dat ongemarkeerd naar het model. MADAM-RAG (2025) adresseert dit als open probleem. Klai heeft geen conflict detection. Dit vergroot het hallucinatierisico: het model moet zelf conflicten oplossen zonder te weten dat ze er zijn.

**Het Lost in the Middle risico**

Klai injecteert top 5–10 chunks als systeembericht prefix. In welke volgorde? Als de meest relevante chunk op positie 4 staat van de 8, verliest het model een groot deel van de waarde (Stanford 2023: >30% degradatie voor middenpositie). Dit is een concrete implementatievraag die niet geadresseerd is.

**Het calibratieprobleem is reëel maar onopgelost**

ACL 2025: geen enkele bestaande uncertainty estimation methode werkt correct in RAG-context. Klai is hier niet uniek achter — dit is een open probleem in het hele veld. Maar het ondermijnt de aanname dat confidence scores direct bruikbaar zijn als je ze wél gaat implementeren.

---

### Wat de research toevoegt dat Klai nog niet heeft

**Evidence type als retrievalgewicht — directe toepassing**

Het meest actionable inzicht. Map `content_type` naar een confidence tier:

| content_type | evidence_tier |
|---|---|
| kb_article (handmatig) | 1.0 |
| meeting_transcript | 0.75 |
| pdf_document | 0.85 |
| web_crawl | 0.60 |
| unknown | 0.50 |

Sla dit op als Qdrant payload veld. Gebruik het als reranker weight. Lage implementatie-inspanning, directe analogie met RA-RAG en TREC Health resultaten.

**Corroboratiegraad per entiteit in FalkorDB**

FalkorDB bevat al entiteiten. Voeg per entiteit toe: hoeveel onafhankelijke brondocumenten vermelden dit? Gebruik dit als graph-level confidence score bij retrieval. Dit is de Knowledge Vault/NELL aanpak voor Klai's knowledge graph.

**Chunk-volgorde op basis van evidence strength**

De top-k chunks die naar het model gaan: sorteer niet alleen op relevantiescore maar ook op evidence strength. Zet de hoogste bewijskracht bovenaan. Dit adresseert direct het Lost in the Middle risico.

---

### Scorekaart Klai

| Onderdeel | Research-oordeel |
|---|---|
| Drie-vector fusion (dense + sparse + HyPE) | Gevalideerd — TREC Health, SELF-RAG ondersteunen fusion |
| Contextual Retrieval prefix | Sterk gevalideerd — Anthropic: -67% retrieval failure |
| Reranker (cross-attention) | Gevalideerd — precision improvement bewezen |
| Fail-open ontwerp | Correct — reliability boven precision |
| Gap detection | Gevalideerd in principe — SELF-RAG `IsUse` equivalent |
| Alle chunks gelijke weging | Uitgedaagd — RA-RAG +51%, TREC +60% bij bronweging |
| Cross-source corroboratie | Ontbreekt — FalkorDB aanwezig, mechanisme niet gebruikt |
| Conflictdetectie | Ontbreekt — verhoogt hallucinatierisico ongemerkt |
| Chunk-volgorde bij injectie | Niet geoptimaliseerd — Lost in the Middle risico |
| Assertion mode actief in retrieval | Ontbreekt — veld bestaat, niet aangesloten |
| Confidence kalibratie | Ontbreekt — maar onopgelost probleem in heel het veld |

---

## Vergelijking: ThetaOS vs. Klai per research-dimensie

| Dimensie | ThetaOS | Klai |
|---|---|---|
| Evidence type hiërarchie | Volledig gebouwd (10 lagen) | Veld bestaat, niet gebruikt |
| Cross-source corroboratie | Kern van het systeem (laag 7) | Infrastructuur aanwezig, niet geïmplementeerd |
| Bronindependentie meting | Niet gemeten | Niet gemeten |
| Menselijke validatie | Laag 10 — expliciet | Impliciet (KB-artikel schrijven = menselijke bevestiging) |
| Confidence kalibratie | Niet gekalibreerd | Niet gekalibreerd |
| Valentie/contra-evidentie | In diamantlaag, niet in myeline | Niet aanwezig |
| Conflict detection | Anti-coincidentie, bank als leugendetector | Niet aanwezig |
| Temporele decay | Expliciet (heet/warm/lauw/koud) | Hard cutoff (`valid_until`), geen decay |
| Retrieval-volgorde | Niet geadresseerd | Niet geoptimaliseerd |
| Ongestructureerde tekst | Zwak (laag 6, tekst-extractie) | Sterk (HyPE, contextual retrieval, reranker) |
| Multi-tenant | Niet van toepassing | Volledig geïmplementeerd |

ThetaOS is sterker in bewijsmodellering.
Klai is sterker in semantisch retrieval van ongestructureerde tekst.
Beide missen bronindependentiemeting en confidence kalibratie.

---

## Wat Martijn kan leren van Klai

> Geschreven zonder jargon — voor de architect van ThetaOS, niet de programmeur.

ThetaOS heeft de betere *bewijslogica*: hoe je bepaalt of iets waar is, hoe sterk bewijs weegt, hoe je bronnen vergelijkt. Maar Klai heeft de betere *zoeklogica*: hoe je iets terugvindt in een grote hoeveelheid ongestructureerde tekst. Hieronder drie concrete dingen die Klai anders doet en die ThetaOS sterker zouden maken.

---

### 1. Sla niet alleen op wát een herinnering zegt — sla ook op welke vraag ze beantwoordt

**Hoe het nu werkt in ThetaOS:**
Een synaps wordt opgeslagen met de inhoud: "Op 12 maart 2024 besprak ik met Jan de overname van bedrijf X."

Als je later vraagt "wanneer sprak ik Jan over een overname?", zoekt het systeem tekst die lijkt op die vraag. Dat werkt — maar niet altijd. De manier waarop je iets onthoudt is niet altijd de manier waarop je ernaar zoekt.

**Wat Klai anders doet:**
Klai genereert bij elke opgeslagen chunk ook hypothetische vragen die de chunk beantwoordt:
- *"Wanneer sprak Mark Jan voor het eerst over de overname?"*
- *"Wat was de aanleiding voor het gesprek over bedrijf X?"*

Die vragen worden als aparte index opgeslagen. Bij retrieval vergelijk je je zoekvraag ook met die vraag-index — niet alleen met de tekst zelf.

**Waarom dit voor ThetaOS relevant is:**
Mensen halen herinneringen op via de vraag die ze stellen, niet via de letterlijke inhoud. "Wanneer was ik in Deventer?" is een andere zoekopdracht dan de manier waarop je die dag noteerde: "Trein naar Deventer, vergadering met Remco, daarna lunch." Door bij synaps-aanmaak ook te noteren welke vragen deze herinnering beantwoordt, verbetert de terugvind-snelheid — met name voor oude of vergeten synapsen.

---

### 2. Zoek ook op exacte woorden, niet alleen op betekenis

**Het verschil tussen betekenis-zoeken en woord-zoeken:**

*Betekenis-zoeken* (Klai en ThetaOS gebruiken dit allebei) werkt als een bibliotheekassistent die snapt wat je bedoelt. Als je vraagt naar "gesprekken over teamdynamiek", vindt hij ook notities over "groepscultuur" en "samenwerking" — ook al staan die woorden er niet letterlijk in. Slim, maar hij kan ook de verkeerde kant op.

*Woord-zoeken* (Klai heeft dit, ThetaOS waarschijnlijk niet) werkt als Ctrl+F: het vindt precies de letter-combinatie die je intypt. "Remco" vindt Remco. "12 maart 2024" vindt die datum. Geen interpretatie, geen afronding.

**Waarom je allebei nodig hebt:**
- Betekenis-zoeken mist soms specifieke namen, datums en technische termen omdat ze "dicht bij" andere namen liggen.
- Woord-zoeken mist context en synoniemen.

Klai gebruikt ze samen: eerst twee aparte zoeklijsten, dan combineren. Zo krijg je de nauwkeurigheid van Ctrl+F én de intelligentie van betekenis-zoeken.

**Wat dit voor ThetaOS betekent:**
Met 170.000 synapsen is woordzoeken niet meer optioneel bij vragen als "wanneer sprak ik Remco?" of "vind alles over bedrijf X uit 2023." Betekenis-zoeken alleen is daarvoor niet precies genoeg. Een simpele tekstindex naast de semantische index lost dit op.

---

### 3. Zorg dat "Jan" en "Jan van der Berg" als dezelfde persoon tellen bij corroboratie

**Het probleem:**
ThetaOS telt corroboratie: hoeveel bronnen bevestigen hetzelfde? Dat is een sterk principe. Maar als in de ene bron staat "Jan" en in de andere "Jan van der Berg" — telt het systeem dat als twee verschillende entiteiten, en dus als sterkere corroboratie dan het eigenlijk is.

**Wat Klai doet:**
Klai gebruikt een kennisgraaf (FalkorDB/Graphiti) die bij elke opname variantherkenning toepast. "Jan", "Jan vdB", "Jan van der Berg" worden samengevoegd tot één entiteit. Pas daarna telt het systeem corroboratie.

**Wat dit voor ThetaOS betekent:**
Als de corroboratietelling de kern van het bewijsmodel is, dan is de kwaliteit van die telling afhankelijk van hoe goed namen en entiteiten worden samengevoegd. Dezelfde meting van "Jan bevestigde dit" telt nu misschien als twee bewijzen als Jan op twee manieren wordt geschreven. Dat infleert de zekerheid kunstmatig.

De oplossing is entity resolution bij synaps-aanmaak: check of de persoon of het object al bekend is onder een andere naam, en koppel dan aan de bestaande entiteit in plaats van een nieuwe aan te maken.

---

### Samenvatting

| Klai-principe | Wat het is (simpel) | Relevantie voor ThetaOS |
|---|---|---|
| Vraag-alignment (HyPE) | Sla ook op welke vragen een herinnering beantwoordt | Betere terugvindbaarheid van oude synapsen |
| Woord-zoeken (sparse) | Ctrl+F naast de intelligente betekeniszoeker | Nauwkeurig zoeken op namen, datums, termen |
| Entiteit-resolutie | "Jan" en "Jan van der Berg" = dezelfde persoon | Eerlijkere corroboratietelling |

---

## Gerelateerde documenten

- [Evidence-Weighted Knowledge Research](evidence-weighted-knowledge.md) — het brede onderzoek waarop dit document is gebaseerd
- [Assertion Modes Research](../assertion-modes/assertion-modes-research.md) — diep onderzoek naar assertion modes (Klai scorekaart: "assertion mode actief in retrieval ontbreekt")
- [Assertion Mode Weights](../assertion-modes/assertion-mode-weights.md) — nuanceert de gewichten uit het implementatieplan: spread van 0.30 is te agressief
- [Corroboration Scoring](../corroboration/corroboration-scoring.md) — diep onderzoek naar corroboratie (Klai scorekaart: "cross-source corroboratie ontbreekt")
- [Implementation Plan](../implementation/implementation-plan.md) — vertaling van de zes gaps naar concrete codewijzigingen
- [RAG Evaluation Framework](../evaluation/rag-evaluation-framework.md) — hoe we de verbeteringen gaan meten
