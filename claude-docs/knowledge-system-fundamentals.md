# Het beste kennissysteem: wat het onderzoek zegt

> Geschreven op basis van een research-sessie op 2026-03-25.
> Doel: de fundamentals van een goed kennissysteem begrijpen, onderbouwd door bestaand onderzoek.
> Toon: toegankelijk — geschikt om aan anderen uit te leggen.

---

## Inhoudsopgave

| § | Sectie | Wat je hier vindt |
|---|--------|-------------------|
| 1 | [Vijf universele entiteitstypen](#bevinding-1-in-elke-organisatie-bestaan-precies-vijf-soorten-dingen) | De categorieën die elk kennissysteem nodig heeft; empirische convergentie over 8 systemen |
| 2 | [Zes universele relaties](#bevinding-2-er-zijn-zes-relaties-die-je-altijd-nodig-hebt) | De verbindingen die je altijd nodig hebt om organisatiekennis te beschrijven |
| 3 | [Kwaliteit bij opslag](#bevinding-3-de-kwaliteit-wordt-bepaald-bij-opslag-niet-bij-zoeken) | Waarom contextrijke ingest 49% minder fouten geeft; het belangrijkste moment in het systeem |
| 4 | [Hybride verwerking](#bevinding-4-volledig-automatisch-werkt--maar-net-niet-goed-genoeg) | 20-45% foutmarge bij volledig automatisch; de hybride aanpak die dat oplost |
| 5 | [Drie zoekmethoden](#bevinding-5-drie-zoekmethoden-elk-goed-in-iets-anders) | PostgreSQL vs. vector search vs. knowledge graph; wanneer welke methode wint |
| 6 | [Knowledge graph laag](#bevinding-6-de-knowledge-graph-laag--hoe-met-wat-en-waarom) | FalkorDB, Graphiti, temporaliteit, density-probleem, volledige architectuur |
| 7 | [Taxonomie bootstrappen](#bevinding-7-hoe-je-de-taxonomie-bouwt-voor-een-nieuwe-organisatie) | Gelaagde taxonomie, vier bootstrap-fasen, onderhoudsstrategie |
| 7b | [Retroactieve herclassificatie](#bevinding-7b-retroactieve-herclassificatie-bij-taxonomie-uitbreiding) | Nieuwe categorie toevoegen zonder re-embedding; similarity screening + Qdrant payload update |
| 8 | [Document-updates](#bevinding-8-hoe-je-document-updates-verwerkt-zonder-chaos) | Delta sync, deduplicatie, graph-updates, Resource Registry, connector-valkuilen |
| 9 | [Monitoring](#bevinding-9-hoe-je-weet-of-het-systeem-goed-werkt) | Vier monitoringlagen, staleness-detectie, RAGAS metrics, prioriteitsvolgorde |
| 10 | [Intentionele laag](#bevinding-10-de-intentionele-laag--het-waarom-vastleggen) | Decision als zesde entiteitstype; waarom automatische extractie niet werkt |
| 11 | [Omgaan met onzekerheid](#bevinding-11-hoe-het-systeem-omgaat-met-onzekerheid) | OWA vs. CWA spanning; confidence-scored provenance; rol van assertion_mode en confidence |
| 12 | [Communicatie als kennisbron](#bevinding-12-communicatie-als-kennisbron) | E-mail, chat, calls, vergaderingen, 1-op-1; per type de extractie-aanpak |
| — | [Volledige architectuur in één overzicht](#de-volledige-architectuur-in-één-overzicht) | Alle zes entiteitstypen, technische lagen en de ingest-pipeline op één plek |

---

## Het probleem

Elk bedrijf heeft mensen die enorm veel weten. Over klanten, processen, beslissingen, waarom dingen zijn zoals ze zijn. Maar die kennis zit in hoofden, e-mails, documenten en gesprekken — verspreid en ongrijpbaar.

Als iemand vertrekt, verdwijnt die kennis. Als je iets wil weten, moet je de juiste persoon kennen om het te vragen.

Het doel van een kennissysteem: die kennis automatisch opvangen — uit gesprekken, documenten, e-mails — en zo opslaan dat je er later slimme vragen over kunt stellen.

De centrale vraag die dit onderzoek beantwoordt: **wat is de beste manier om die kennis op te slaan en terug te vinden?**

---

## Bevinding 1: In elke organisatie bestaan precies vijf soorten dingen

Onderzoekers hebben de grootste kennissystemen ter wereld geanalyseerd — van Wikipedia tot grote bedrijfssystemen. Acht onafhankelijke systemen, gebouwd tussen 2001 en 2024, kwamen allemaal op dezelfde vijf categorieën uit:

| # | Categorie | Voorbeelden |
|---|---|---|
| 1 | **Mensen en organisaties** | Mark, Voys, een klant, een leverancier |
| 2 | **Documenten en berichten** | een e-mail, een handleiding, een beslissing, een Slack-bericht |
| 3 | **Containers** | een project, een team, een map, een afdeling |
| 4 | **Gebeurtenissen** | een vergadering, een gesprek, een deployment |
| 5 | **Categorieën** | "dit is een klacht", "dit hoort bij Billing", "dit is een beslissing" |

Alles wat er in een bedrijf bestaat valt in één van deze vijf bakjes.

**Voorbeeld:** Een e-mail van Mark aan een klant over een factuurprobleem is een *bericht* (2), gemaakt door een *persoon* (1), over een onderwerp gelabeld als *Billing* (5), verstuurd tijdens een *klantgesprek* (4), dat valt onder het *supportteam* (3).

Dit is empirische convergentie: acht onafhankelijke systemen, van enterprise-datamodellering tot W3C-ontologieën en recente AI-memory-systemen, kwamen zonder coördinatie op dezelfde vijf types uit. Silverston's werk uit 2001 is één van de vroege bronnen — zijn bijdrage is enterprise-datapatronen (partijen, producten, orders). W3C formaliseerde vergelijkbare structuren in 2013 via PROV-O en de ORG Ontology. Een AI-memory systeem uit 2024 herontdekte exact dezelfde vijf categorieën. De convergentie is breder dan één bron en sterker dan een theoretisch argument.

---

## Bevinding 2: Er zijn zes relaties die je altijd nodig hebt

Die vijf soorten dingen hebben verbindingen met elkaar. Ook hier geldt: zes relatie-typen zijn voldoende om alle organisatiekennis te beschrijven:

- Mark **heeft gemaakt**: die e-mail
- Voys **bezit**: dat document
- Dit document **zit in**: dit project
- Deze beslissing **is gelinkt aan**: dat gesprek
- Dit bericht **is geclassificeerd als**: Billing
- Mark **is lid van**: het supportteam

Met deze vijf bakjes en zes relaties kun je vragen beantwoorden als: "Welke documenten over Billing heeft het supportteam het afgelopen jaar gemaakt?" — maar alleen als je de relaties ook daadwerkelijk hebt opgeslagen.

---

## Bevinding 3: De kwaliteit wordt bepaald bij opslag, niet bij zoeken

Dit was de meest verrassende bevinding.

Anthropic onderzocht wat er gebeurt als je bij elk stuk tekst dat je opslaat ook context meegeeft. Niet alleen de tekst zelf, maar ook: *dit is een e-mail, geschreven door iemand van support, over een factuurprobleem bij klant X, uit een gesprek in maart.*

**Resultaat: 49% minder fouten bij het terugvinden.**

De implicatie is groot: geen enkele zoektechniek compenseert een slechte ingest. Als je een Google Drive inleest met 5.000 documenten en je slaat ze op als losse tekstblokken zonder context, haal je er later nooit meer uit dan wat je erin hebt gestopt.

**Het moment dat je iets inleest is het belangrijkste moment in het hele systeem.**

---

## Bevinding 4: Volledig automatisch werkt — maar net niet goed genoeg

De vraag was: kan een systeem zelf begrijpen wat iets is, of moet een mens dat doen?

Systemen die volledig automatisch context extraheren maken 20-45% fouten op gewone bedrijfsdocumenten. Dat is te veel. Het meest confronterende voorbeeld uit het onderzoek: voeg een apostrof toe aan één woord, en het systeem extraheert compleet andere informatie uit dezelfde zin.

**Maar de hybride aanpak werkt uitstekend:**

90% van de documenten kan het systeem volledig automatisch verwerken. Alleen de 10-15% waar het systeem zelf twijfelt, krijgt een mens te zien. Resultaat: bijna dezelfde kwaliteit als volledig menselijk gecureerd — voor 500 keer minder werk.

Het systeem hoeft mensen niet te vragen *wat* iets is, alleen *of het zelf goed heeft begrepen*. Dat is een fundamenteel ander — en veel lichter — soort menselijke input.

---

## Bevinding 5: Drie zoekmethoden, elk goed in iets anders

Er zijn drie manieren om iets terug te vinden in een kennissysteem:

**Relationele database (PostgreSQL)** — precies en snel. "Geef me alle documenten van het supportteam tussen 1 januari en 1 maart." Werkt feilloos op structuur, maar weet niets van betekenis.

**Vector search** — begrijpt betekenis. Vindt relevante documenten ook als ze andere woorden gebruiken. Maar minder goed in precieze filtering.

**Knowledge graph** — volgt verbindingen. Sterk bij vragen die meerdere stappen vereisen: "wat zijn terugkerende problemen in onze supportgesprekken over Billing?"

### De gemeten verbeteringen

| Methode | Toepassing | Gemeten verbetering |
|---|---|---|
| Taxonomie/labels | Filtering op categorie | +9 tot +40% precisie |
| Knowledge graph | Multi-stap vragen | +43 tot +75% beter dan gewone zoekopdrachten |
| Vector | Directe feitenvragen | Beste bij isolated facts |
| Alle drie gecombineerd | Elk type vraag | Consistent beter dan elk apart |

### Waar elke methode wint

- **"Wat is het telefoonnummer van klant X?"** → vector of relationeel
- **"Alle contracten van dit kwartaal"** → relationeel
- **"Wat zijn terugkerende klachten in onze supportgesprekken?"** → knowledge graph
- **"Vind alles over onboarding"** (ook als dat woord er niet in staat) → vector

---

## Conclusie: de beste architectuur

De hypothese waarmee deze sessie begon — een hybride van relationele database + ontologie + vector/RAG geeft de beste retrieval — is empirisch bevestigd.

De architectuur heeft een **aanbevolen build-volgorde**, gebaseerd op wat in de praktijk de meeste waarde per investering oplevert. Dit is geen logische dwang: je kunt elk van de drie lagen (taxonomie, ontologie, knowledge graph) onafhankelijk bouwen. Grote productiesystemen zoals Wikidata en Google Knowledge Graph hebben bewezen dat een knowledge graph zonder formele ontologie kan werken. De aanbeveling staat echter: begin met de universele kern, want dat geeft de meeste waarde per investering en maakt de latere lagen eenvoudiger te bouwen.

**Aanbevolen volgorde:**

1. **Definieer de vijf bakjes en zes relaties** — dit is de universele kern die voor elke organisatie werkt
2. **Bouw een taxonomie** — de categorieën (Billing, Support, HR) als hiërarchie bovenop die kern
3. **Ingest met context** — laat het systeem bij opslag begrijpen wat iets is; laat mensen alleen de twijfelgevallen beoordelen (10-15%)
4. **Indexeer slim** — taxonomy-labels als integraal onderdeel van de zoekindex, niet als nabewerking
5. **Combineer bij zoeken** — relationeel voor filters, vector voor betekenis, graph voor verbindingen

De rode draad: **de indeling bij opslag is primair. De zoektechniek is secundair.**

---

## Bevinding 6: De knowledge graph laag — hoe, met wat, en waarom

Deze sectie behandelt de technische keuzes voor de graph-laag: welke database, welk extractieframework, hoe temporaliteit werkt, en hoe je het density-probleem oplost.

### Waarom je een knowledge graph nodig hebt

PostgreSQL + Qdrant zijn niet voldoende voor een organisatiebrein. Ze missen de verbindingslaag die vragen mogelijk maakt als:

- "Wat zijn terugkerende problemen in onze supportgesprekken over Billing?"
- "Welke beslissingen hangen samen met dit document?"
- "Wat wist het team over dit onderwerp vóór die vergadering?"

Voor dit soort vragen moet het systeem meerdere stappen kunnen volgen: van document naar entiteit naar gerelateerde entiteiten naar patroon. Dat is wat een knowledge graph doet.

### Welke graph-database

**Aanbeveling: FalkorDB**

- Open-source, geen licentiekosten
- Sub-10ms queries voor multi-hop traversals in productie
- Werkt natively samen met Graphiti (het extractie-framework, zie hieronder)
- Als je later wil switchen naar Neo4j: Graphiti ondersteunt beide, de overstap is minimaal

Apache AGE (PostgreSQL-extensie) klinkt aantrekkelijk omdat het geen extra service toevoegt, maar heeft een serieuze beperking: PostgreSQL major version upgrades werken niet. Voor een systeem dat jaren mee moet, is dat onaanvaardbaar.

### Hoe je automatisch een goede graph bouwt

Elk binnenkomend document doorloopt een pipeline:

1. Opdelen in tekstsegmenten
2. LLM extraheert entiteiten en relaties ("Mark besloot X", "project Y hangt samen met Z")
3. Varianten van dezelfde entiteit worden samengevoegd ("factuurprobleem Q2" + "bug factuurmodule mei" → één node)
4. Nieuwe entiteiten worden vergeleken met bestaande — zijn het dezelfde of verschillende dingen?
5. Alles wordt opgeslagen in graph + vector store + metadata

**Het framework dat dit kant-en-klaar afhandelt: Graphiti**

Graphiti doet entity resolution, deduplicatie en temporaliteit automatisch. Cruciale eigenschap: elk feit krijgt een tijdslabel. Als een besluit in maart wordt herzien, blijft het oude feit bewaard maar wordt het gemarkeerd als vervallen. Zo kun je vragen: "wat wist het systeem op 1 februari?" — essentieel voor een organisatiebrein.

### Hoe Graphiti tijd bijhoudt

Graphiti werkt op het niveau van individuele relaties. Elke relatie ("Mark IS lid van Engineering") heeft vijf tijdsvelden:

| Veld | Betekenis |
|---|---|
| `created_at` | Wanneer de relatie in de database is aangemaakt |
| `valid_at` | Wanneer de relatie in de werkelijkheid begon |
| `invalid_at` | Wanneer de relatie in de werkelijkheid eindigde |
| `expired_at` | Wanneer een latere bron hem heeft tegengesproken |
| `episodes[]` | Welk document was de bron? |

Dit is fijner dan het bewaren van een tijdsstempel per document. Een document uit 2024 kan tien relaties bevatten die elk op een andere datum zijn begonnen.

**Het kritieke moment:** bij het inlezen van een document moet je Graphiti vertellen *wanneer het document geldt*, niet wanneer je het inlaadt. Als je op 25 maart een vergadering van 15 december inleest, moet Graphiti denken: "dit feit begon op 15 december" — niet vandaag. Dat stel je in via één parameter: `reference_time = datum_van_het_document`.

### Twee tijdsmodellen naast elkaar

Klai bewaart al tijdsinformatie op documentniveau (`belief_time_start`/`end`). Graphiti doet dat op relatieniveau. Ze zijn **complementair** — Graphiti weet precies wanneer een specifieke relatie geldig was, Klai weet wanneer het hele document geldig was en hoe betrouwbaar het is.

De brug tussen de twee: bij ingestie gebruik je `belief_time_start` van het document als `reference_time` voor Graphiti. En je slaat de `episode_id` die Graphiti teruggeeft op in het Klai-document. Zo kun je altijd van document naar graph-edges en terug.

**Het synchronisatieprobleem:** als je in Klai een document markeert als `superseded_by` (vervangen door een nieuwer document), weet Graphiti dat niet automatisch. Je moet dan actief een nieuw episode inladen met de vervangende informatie — pas dan zet Graphiti `expired_at` op de verouderde graph-edges. Dit is een expliciete stap in de pipeline, geen automatisme.

### Het density-probleem oplossen

Een graph werkt alleen als er genoeg verbindingen zijn. De minimumdrempel: gemiddeld meer dan 2 verbindingen per node. Twee technieken die dit oplossen:

**Techniek 1 — Elke entiteit terugverbinden met het brondocument**
Simpel maar zeer effectief. In productie resulteert dit in gemiddeld ~6 verbindingen per node — ruim boven de drempelwaarde. (HippoRAG2, onafhankelijk geverifieerd)

**Techniek 2 — Varianten samenvoegen**
"Vulnerabilities", "vulnerable" en "vulnerability" zijn dezelfde entiteit. Door deze samen te voegen na extractie, worden geïsoleerde nodes verbonden. (KGGen, NeurIPS 2025 — peer-reviewed)

### De volledige architectuur

```
CONNECTORS (Drive, GitHub, webcrawler, email, Slack)
        |
        v
INGESTIE PIPELINE
  → Chunker
  → LLM: entiteiten + relaties extraheren
  → KGGen-clustering: varianten samenvoegen
  → Entity resolution: vergelijk met bestaande graph
  → Opslaan in alle drie lagen
        |
        +---> FalkorDB       (verbindingen en traversal)
        +---> Qdrant         (semantisch zoeken)
        +---> PostgreSQL     (metadata, connectorstatus, gebruikersdata)

RETRIEVAL
  1. Qdrant vindt de relevante entiteiten (semantisch)
  2. FalkorDB volgt de verbindingen (multi-hop)
  3. PostgreSQL levert de structuur en filters
  4. LLM combineert alles tot een antwoord
```

**De taakverdeling:**
- **PostgreSQL** — wie heeft wat geüpload, wanneer, connector-logs, gebruikersdata
- **Qdrant** — "vind alles wat lijkt op Billing + support" (ook als die woorden er niet in staan)
- **FalkorDB** — "welke beslissingen hangen hier mee samen, via hoeveel stappen ook"

### Kanttekening bij de getallen

De meeste prestatiecijfers van FalkorDB en Graphiti zijn vendor-gerapporteerd. De enige onafhankelijk geverifieerde meting in dit onderzoek is KGGen's extractiekwaliteit: 66% op de MINE benchmark vs. 47.8% voor Microsoft GraphRAG (NeurIPS 2025, peer-reviewed).

---

## Bevinding 7: Hoe je de taxonomie bouwt voor een nieuwe organisatie

Deze sectie behandelt het bootstrap-probleem: hoe bouw je een taxonomie op voor een organisatie die net begint, en hoe houd je die up-to-date?

### Het probleem met een lege start

Een nieuwe organisatie meldt zich aan. Er is nog geen taxonomie. Maar de taxonomie is precies wat de retrieval 9-75% beter maakt. Hoe los je dat op?

### De aanpak: gelaagde taxonomie

Niet één taxonomie per organisatie, maar twee lagen:

**Laag 1 — Organisatiebreed (gedeeld):**
De Big 5 die voor elke organisatie gelden, ongeacht sector:
- Person (medewerker, klant, partner)
- Organization (team, afdeling, leverancier)
- Topic (domeinkennis, technologie, methode)
- Document (rapport, handleiding, beslissing)
- Project (product, campagne, implementatie)

"Mark Vos" als persoon bestaat één keer — ook als hij in meerdere kennisbanken voorkomt. Entiteiten op deze laag zijn zichtbaar over kennisbanken heen, voor zover je toegang hebt.

**Laag 2 — Kennisbankspecifiek:**
Elke kennisbank bouwt zijn eigen uitbreiding. Engineering heeft Kubernetes en Pull requests. Sales heeft ICP en Offertes. Deze entiteiten zijn altijd privé tot de kennisbank.

Dit lost het access-control constraint direct op: de gedeelde laag is toegankelijk op basis van bestaande rechten, de kennisbanklaag nooit buiten de bank.

### Hoe je bootstrapt

**Fase 0 (0-20 documenten):** gebruik alleen de universele startertaxonomie — de Big 5. Geen automatische aanvulling, want er is te weinig data voor betrouwbare extractie.

**Fase 1 (20-200 documenten):** een LLM genereert automatisch een startertaxonomie uit de eerste batch. Vereiste: minimaal 20 documenten van minimaal 5 verschillende bronnen (zodat je niet bootstrapt op basis van één connector). Kwaliteit: 70-85% precisie. Onzekere categorieën worden apart gemarkeerd voor optionele menselijke review.

**Fase 2 (200+ documenten):** taxonomie stabiliseert. 80-85% van de uiteindelijke categorieën zijn aanwezig. Automatisch bijwerken bij elke batch van 50 nieuwe documenten.

**Fase 3 (500+ documenten):** incrementele updates in plaats van volledige herberekening. Nieuwe concepten worden toegevoegd; hoofdcategorieën liggen vast.

Diversiteit telt meer dan volume: 100 documenten uit vier verschillende bronnen geven een rijkere taxonomie dan 500 documenten van één connector.

### Onderhoud

Hoofdcategorieën veranderen zelden (1-2 keer per jaar). Subcategorieën hebben meer turnover (10-20% per kwartaal bij actieve organisaties).

Het systeem detecteert automatisch nieuwe categorieën en legt ze voor aan de beheerder. De beheerder handelt alleen bij notificaties — geen actief onderhoud nodig. Categorieën verwijderen doe je nooit automatisch: dat riskeert dataverlies.

---

## Bevinding 7b: Retroactieve herclassificatie bij taxonomie-uitbreiding

Als er een nieuwe categorie aan de taxonomie wordt toegevoegd (bv. "Security"), moeten bestaande documenten worden herclassificeerd — zonder alles opnieuw in te lezen.

### De aanpak: drie stappen zonder re-embedding

**Stap 1 — Similarity screening:** embed de definitie van de nieuwe categorie en zoek in Qdrant welke bestaande chunks er semantisch op lijken (drempelwaarde: cosine similarity >0.68). Dit is razendsnel en filtert 95% van de documenten eruit als niet-kandidaat.

**Stap 2 — LLM-classificatie op kandidaten:** stuur alleen de gevonden kandidaten (typisch 5% van het totaal) naar een LLM voor definitieve beoordeling. Bij 10.000 documenten zijn dat ~500 LLM-aanroepen — verwaarloosbare kosten, ~2-5 minuten.

**Stap 3 — Payload update in Qdrant:** Qdrant heeft een `set_payload` API die alleen de labels bijwerkt zonder de vector aan te raken. Geen re-indexering nodig.

### Prioritering

Verwerk in volgorde: meest bevraagde documenten eerst, dan meest recente, dan de rest als nachtelijke achtergrondjob.

### Risico: gedeeltelijke herclassificatie

Tijdens de update heeft 30% al het nieuwe label, 70% nog niet. Gebruikers missen relevante content zonder foutmelding.

**Mitigatie:** voeg embedding-based fallback toe — als een query weinig resultaten geeft via het label-filter, val terug op pure vector similarity. Sla ook een `reclassification_status` op zodat rapportage betrouwbaar blijft tijdens de migratie.

### Wanneer wél volledig heringesteren

Alleen als de taxonomie fundamenteel wordt herstructureerd (niet alleen uitgebreid), of als je ook betere chunk-grenzen wilt. In alle andere gevallen is de gelaagde aanpak hierboven voldoende.

---

## Bevinding 8: Hoe je document-updates verwerkt zonder chaos

Deze sectie behandelt het update-probleem: wat doe je als een document in Google Drive wordt bijgewerkt? Detectie, deduplicatie, graph-updates en cascading deletes.

### Het probleem

Een document in Google Drive wordt bijgewerkt. Het systeem moet dat detecteren, de oude vectoren vervangen, de knowledge graph bijwerken, en geen dubbele data achterlaten. Klinkt eenvoudig — maar hier gaan de meeste systemen mis.

### Wijzigingen detecteren: delta sync

De aanbevolen aanpak combineert twee methoden:

**Webhooks** voor snelheid: Google Drive of GitHub stuurt een notificatie zodra iets verandert. Je verwerkt het direct. Nadeel: als je systeem even offline is, mis je events.

**Periodieke reconciliatie** als vangnet: eens per uur of nacht check je alle connectors opnieuw op basis van opgeslagen versie-tokens. Vangt gemiste webhook-events op.

**Kritieke valkuil voor Google Drive:** push notification channels verlopen na maximaal 7 dagen. Als je ze niet proactief vernieuwt, stopt je systeem stilletjes met luisteren — zonder foutmelding.

### Deduplicatie in Qdrant

Elke vector krijgt een stabiele ID op basis van document + chunk:

```
point_id = sha256(document_id + chunk_index)
```

Bij heringestie gebruik je `upsert` — bestaande vectors worden overschreven, niet gedupliceerd. Sla altijd `document_version` (de native versieid van Google Drive of GitHub) én een `content_hash` op als payload. Zo kun je detecteren of een document echt is veranderd vóórdat je de hele ingestie-pipeline opstart.

Als een document korter wordt (minder chunks), verwijder dan de overtollige chunks expliciet — upsert alleen overschrijft, verwijdert niet.

### Knowledge graph updates: geen delete+recreate

Het lijkt verleidelijk: verwijder alle nodes van document X, maak ze opnieuw. Maar dat werkt niet zodra entiteiten in meerdere documenten voorkomen. "Mark Vos" staat in tien documenten — zijn node kun je niet verwijderen als één document wordt bijgewerkt.

De juiste aanpak: **entiteiten zijn persistent, edges krijgen een brondocument-label**.

Bij update van document X:
1. Extraheer nieuwe entiteiten en relaties
2. Verwijder alleen de edges gelabeld `source_document = X`
3. Voeg nieuwe edges toe
4. Nodes zonder enige resterende edge worden opgeruimd

### De Resource Registry — het meest vergeten onderdeel

Zonder dit component kun je niet betrouwbaar verwijderen. Als een connector wordt losgekoppeld of een map wordt verwijderd, moet je alle bijbehorende vectors en graph-nodes kunnen vinden en verwijderen.

De oplossing: een registry die bijhoudt welke vectoren en graph-nodes bij welk document horen:

```
(connector_id, document_id) → [vector_ids, kg_node_ids]
```

Dit is één tabel in PostgreSQL. Zonder deze tabel weet je bij een delete nooit wat je moet opruimen.

### Bekende valkuilen per connector

**Google Drive:**
- Gedeelde drives missen: voeg `includeItemsFromAllDrives=True` toe aan elke API-call
- Prullenbak-events: controleer `trashed: true` en verwijder corresponderende data
- Google Docs exporteren: native Google Docs hebben geen directe download, moeten als plain text worden geëxporteerd

**GitHub:**
- Hernoemen van bestanden: een rename heeft een `previous_filename` veld — negeer je dat, dan houd je de oude versie staan naast de nieuwe
- Grote bestanden (>100MB): niet beschikbaar via de API, zonder duidelijke foutmelding

**Algemeen:**
- Maak de pipeline idempotent: als hetzelfde event twee keer binnenkomt, mag het geen dubbele data opleveren
- Sla altijd de pageToken/sha op na elke sync, anders begin je elke keer opnieuw

---

## Bevinding 9: Hoe je weet of het systeem goed werkt

Deze sectie behandelt monitoring: hoe detecteer je stille fouten, verouderde informatie en kwaliteitsdegradatie voordat gebruikers er last van hebben?

### Het probleem

Een knowledge graph ziet er "gezond" uit — nodes en edges groeien — terwijl de informatie stilletjes verouderd of onjuist wordt. Zonder monitoring merk je dit pas als een gebruiker je erop wijst.

### De vier lagen die je nodig hebt

**Laag 1 — Pijplijn-gezondheid (elke ingest-run)**
- Hoeveel documenten zijn volledig verwerkt? Streefwaarde: >95%
- Hoeveel nodes en edges worden gemiddeld per document geëxtraheerd? Een plotselinge daling betekent dat de extractie stuk is.
- Alarm bij: success rate <95% of extractie-output >30% lager dan de vorige run

**Laag 2 — Graph-structuur (dagelijks)**
- Hoeveel nodes zijn geïsoleerd (geen verbindingen)? Streefwaarde: <10%
- Duplicaten-ratio: hoeveel nodes verwijzen naar dezelfde echte entiteit? Streefwaarde: <2%
- Watch out voor "black holes": als alles verbonden is via één centrale node, is de extractie oppervlakkig

**Laag 3 — Retrieval-kwaliteit (wekelijks)**
Drie metrics die je automatisch kunt meten zonder handmatige labels, via een LLM-judge:
- **Faithfulness**: bevat het antwoord alleen claims die aantoonbaar uit de context komen? Streefwaarde: ≥4.0/5
- **Answer Relevance**: beantwoordt het antwoord de vraag? Streefwaarde: ≥4.0/5
- **Context Precision**: welk percentage van de teruggegeven chunks was echt nodig? Streefwaarde: >70%

Implementatie: RAGAS framework, draait op je eigen LLM-backend — geen data naar externe providers.

**Laag 4 — Gebruikerssignalen (continu)**
- Thumbs up/down op antwoorden
- "Geen antwoord gevonden" rate — streefwaarde: <20%
- Reactietijd P95 — boven 5 seconden haken gebruikers af
- Alarm bij: bounce rate >20%, negatieve feedback >15%

### Staleness: het sluipende probleem

Verouderde informatie is de gevaarlijkste kwaliteitsdegradatie — het systeem ziet er gezond uit, maar de feiten kloppen niet meer.

**Bi-temporele modellering** is de structurele oplossing: elk feit krijgt twee tijdstempels — wanneer werd het geldig in de echte wereld, en wanneer is het opgeslagen? Zo kun je altijd vragen: "wat wist het systeem op tijdstip T?"

**Als alternatief: confidence decay**
Ken elk feit een score toe die daalt als het niet herbevestigd wordt door nieuwe documenten:
- Na 30 dagen zonder herbevestiging: -20%
- Na 90 dagen: -50%
- Na 180 dagen: markeer als "mogelijk verouderd"

**Freshness ratio**: welk percentage van nodes in actieve kennisgebieden is de laatste 30 dagen herbevestigd? Streefwaarde: >60%.

### Prioriteitsvolgorde voor implementatie

Start met deze drie in productie:
1. **Faithfulness** — één fout antwoord kost meer vertrouwen dan tien goede antwoorden opleveren
2. **Ingestion success rate** — stille fouten worden anders nooit gezien
3. **Entity coverage** — ontbrekende entiteiten zijn onmiddellijk zichtbaar voor gebruikers

Voeg daarna toe: staleness monitoring, context precision, graph structure metrics.

---

## Bevinding 10: De intentionele laag — het "waarom" vastleggen

Deze sectie behandelt een lacune in de vijf universele types: het vastleggen van beslissingen en de motivatie erachter.

### Het gat in de vijf bakjes

De vijf universele entiteitstypen dekken *wat* er bestaat, *wie* het heeft gemaakt, en *wanneer* het is gebeurd. Maar ze missen één fundamentele vraag: **waarom** is iets besloten?

Dit is het verschil tussen een kennissysteem dat archieven doorzoekbaar maakt, en een kennissysteem dat organisatieintelligentie bewaart.

### Beslissing als zesde entiteitstype

Een `Decision` is geen Artifact en geen Activity — het is een eigen entiteitstype. Een beslissing heeft vijf velden:

| Veld | Inhoud |
|---|---|
| **Vraag** | Wat was het probleem of de vraag? |
| **Beslissing** | Wat is er besloten? |
| **Rationale** | Waarom deze keuze? (1-3 zinnen) |
| **Alternatieven** | Wat is overwogen maar afgewezen? |
| **Gevolgen** | Wat verandert hierdoor? |

En hij verbindt met de andere vijf entiteitstypen:
- → Party (wie besliste)
- → Artifact (welk document is beïnvloed)
- → Container (in welk project/afdeling)
- → Activity (welke vergadering/sprint)
- → Classification (type: architectuur, beleid, operationeel)
- → andere Decision (vervangt, vereist)

### Automatische extractie werkt niet

Dit was de meest teleurstellende bevinding. LLMs kunnen kandidaat-beslissingen detecteren, maar de *rationale* — het waarom — staat zelden expliciet in tekst. LLMs vullen dit dan in met plausibele maar onjuiste motivaties.

Verwachte kwaliteit bij automatische extractie: 60-75% recall, met significant hallucinatierisico op de rationale zelf. Niet betrouwbaar genoeg voor een systeem waarop mensen moeten kunnen vertrouwen.

### Wat wel werkt: invullen op het moment van beslissen

De enige aanpak die in productie werkt — bewezen door Atlassian, ADR-tooling bij tientallen techbedrijven — is een lage-drempel template die wordt ingevuld *op het moment van beslissen*, niet achteraf.

Drie minuten invultijd. Vijf velden. Gekoppeld aan de context waar de beslissing valt — een vergadering, een PR, een projectdocument.

LLM-ondersteuning heeft hier wél waarde: als *suggestietool* die een eerste concept genereert op basis van de omliggende context, dat een mens dan bevestigt of aanpast. Niet als vervanger, maar als versneller.

### Reikwijdte: pragmatische kern, niet de volledige intentionele laag

Het `Decision`-entiteitstype dekt de **pragmatische kern** van de intentionele laag — maar niet de volledige intentionele laag zoals die in de Enterprise Ontology-literatuur gedefinieerd is.

Enterprise Ontology (Dietz, 2006) en aanverwante raamwerken onderscheiden een complete vocabulaire voor voorwaartse intentionaliteit:

| Concept | Inhoud |
|---|---|
| **Purpose** | Het fundamentele bestaansrecht van de organisatie |
| **Strategy** | De gekozen aanpak om Purpose te realiseren |
| **Goal** | Een concreet, meetbaar gewenst resultaat |
| **Objective** | Een deelinvulling van een Goal, met tijdshorizon |
| **Plan** | De geordende reeks acties die een Objective realiseert |
| **Decision** | Een kristallisatiemoment: waarom is voor deze aanpak gekozen? |

`Decision` is de **achterwaartse** vorm van intentionaliteit: het legt vast *waarom* iets al besloten is. De overige vijf concepten zijn **voorwaartse** intentionaliteit: ze beschrijven wat de organisatie wil bereiken.

**Voor V1 is de volledige intentionele laag prematuur.** Geen enkel productie-B2B-kennissysteem implementeert goals, strategies en objectives als eersteklas entiteiten — dit vereist governance-processen en datamodellen die ver buiten de scope liggen van een knowledge base voor de eerste gebruikers. De aansluiting op de bedrijfsprocessen en de subjectiviteit van "wat is een goal?" maken automatische extractie onbetrouwbaar en handmatige curation duur.

**Wat `Decision` wél oplevert:** De empirische waarde van ADRs is bewezen — ze reduceren herhaalde discussies meetbaar en verbeteren onboarding. Dit is de juiste plek om te beginnen. De `Decision` verbindt bovendien al met de andere vijf universele entiteitstypen (wie besliste, in welk project, gekoppeld aan welk document), waardoor de intentionele context zichtbaar wordt zonder een volledig strategisch raamwerk te vereisen.

**Wanneer reviseren:** Als het platform volwassen is en organisaties structureel goals en OKRs in Klai willen bijhouden, is de intentionele laag een logische uitbreiding. Dit is een bewuste V2+-beslissing, niet een architectuurmis.

---

## Bevinding 11: Hoe het systeem omgaat met onzekerheid

Deze sectie behandelt een fundamentele spanning in kennissystemen: wat doet het systeem als bronnen tegenstrijdige informatie bevatten, en hoe overbruggen `confidence`, `assertion_mode` en `belief_time_*` de kloof tussen PostgreSQL en Qdrant?

### De spanning tussen twee wereldbeelden

Er is een fundamenteel verschil in hoe de twee hoofdlagen van het systeem met onzekerheid omgaan:

**PostgreSQL** werkt vanuit een Closed World Assumption (CWA): wat niet in de tabel staat, bestaat niet. Als een feit niet opgeslagen is, is het onbekend — niet onzeker.

**Qdrant (vector search)** werkt vanuit een Open World Assumption (OWA): wat niet gevonden wordt, is onbekend — maar kan wel bestaan. Het systeem doet geen uitspraken over wat het niet weet.

Dit klinkt theoretisch, maar heeft praktische consequenties: als twee documenten tegenstrijdige claims bevatten, wat "gelooft" het systeem dan? En hoe rapporteert het die onzekerheid aan de gebruiker?

### Hoe productiesystemen dit oplossen

De grootste productieschaal-systemen (Facebook Knowledge Graph, Google Knowledge Vault) lossen dit op via **confidence-scored provenance**: elk feit krijgt een bronverwijzing en een confidence-score. Bij tegenstrijdige claims wint de meest betrouwbare bron — maar alle claims worden bewaard. Dit is geen formele OWA-implementatie (geen OWL/SPARQL), maar een pragmatisch patroon dat in productie werkt.

**Zep/Graphiti** gebruikt een andere benadering: temporele invalidatie. Nieuwere informatie wint van oudere. Oude feiten blijven bewaard maar worden gemarkeerd als vervallen. Dit beantwoordt "wanneer was dit waar?" maar niet "hoe zeker zijn we?".

### Klai's aanpak

Klai heeft drie velden die samen de brug vormen tussen het CWA-systeem (PostgreSQL) en het OWA-systeem (Qdrant):

- **`confidence`** — hoe zeker is het systeem van dit feit? Laat het retrieval-systeem conflicterende bronnen wegen.
- **`assertion_mode`** (`factual` / `belief` / `hypothesis`) — is dit een vastgesteld feit, een mening, of een hypothese?
- **`belief_time_start` / `belief_time_end`** — voor welke periode gold dit als waar?

Het expliciete taggen van `assertion_mode` op documentniveau gaat verder dan wat de meeste productieschaal-systemen doen. Facebook doet het impliciet via source credibility scores; Klai maakt het een eersteklas gegeven in het datamodel. Dit is de juiste keuze: het geeft het retrieval-systeem de informatie die het nodig heeft om conflicterende bronnen te wegen en de gebruiker te informeren over de zekerheidsgraad van een antwoord.

### Praktische regel

Vertrouw op `assertion_mode` + `confidence` + `belief_time_*` als de brug tussen het CWA-systeem (PostgreSQL) en het OWA-systeem (Qdrant vector search). Zorg dat deze velden altijd ingevuld zijn bij ingest — ook als de defaultwaarden worden gebruikt. Een ontbrekend veld is niet hetzelfde als "zeker" of "altijd geldig".

---

## De volledige architectuur in één overzicht

### De zes entiteitstypen

| Type | Voorbeelden | Gevoed door |
|---|---|---|
| **Party** | persoon, team, organisatie, klant | Automatisch (connectors) |
| **Artifact** | document, bericht, code | Automatisch (connectors) |
| **Container** | project, map, afdeling, kennisbank | Automatisch + handmatig |
| **Activity** | vergadering, gesprek, deployment | Automatisch (connectors) |
| **Classification** | Billing, Support, HR | Automatisch (LLM-bootstrap) |
| **Decision** | architectuurkeuze, beleidsbesluit | Handmatig + LLM-suggestie |

### De technische lagen

| Laag | Instrument | Functie |
|---|---|---|
| Structuur + access control | PostgreSQL | Entiteiten, relaties, metadata, KB-isolatie per org |
| Taxonomie | PostgreSQL + SKOS | Gelaagde categorieën (org + KB-specifiek) |
| Semantisch zoeken | Qdrant (filterable HNSW) | Betekenis begrijpen, taxonomy als ingebouwde filter |
| Verbindingen | FalkorDB via Graphiti | Multi-hop vragen, patronen, temporele feiten |
| Ingest-pipeline | LLM + 90/10 menselijke review | Context bij opslag bepaalt retrievalkwaliteit |
| Document-sync | Delta sync + Resource Registry | Updates, deduplicatie, cascading deletes |
| Monitoring | RAGAS + structuurmetrics | Faithfulness, coverage, staleness detectie |
| Synthese | RRF fusion | Resultaten van alle lagen combineren |

### De ingest-pipeline

```
Connector (Drive, GitHub, Slack, webcrawler)
    │
    ▼
Change Detector (content_hash vergelijking)
    │  → geen wijziging: skip
    │  → wijziging: door naar pipeline
    ▼
Ingestie Pipeline
  → Contextual chunking (-49% retrieval failures)
  → LLM extractie: entiteiten + relaties
  → KGGen-clustering: varianten samenvoegen
  → Entity resolution: vergelijk met bestaande graph
  → 10% onzeker → menselijke review queue
    │
    +→ Qdrant (embedding + taxonomy-filter)
    +→ FalkorDB (nodes, edges, temporele labels)
    +→ PostgreSQL (metadata, Resource Registry)
```

**De rode draad:** de kwaliteit van het systeem wordt bepaald bij opslag, niet bij zoeken. De ingest-pipeline is de belangrijkste investering.

---

*Bronnen: peer-reviewed literatuur 2024-2025, W3C-specs (PROV-O, ORG Ontology, SKOS), Silverston Universal Data Models, Microsoft GraphRAG, Anthropic Contextual Retrieval, Zep/Graphiti, KGGen (NeurIPS 2025), HippoRAG2, FalkorDB, Wikidata, ADR/MADR, RAGAS. Volledige technische onderbouwing in ontology-taxonomy-sparring.md.*

---

## Bevinding 12: Communicatie als kennisbron

Naast documenten en websites is communicatie de tweede grote ingest-bron. Empirisch zijn er vijf communicatietypen die voor een organisatiebrein relevant zijn — elk met eigen kennisprofiel, extractiebenadering en privacycontext.

**Universele regel:** elk communicatietype heeft een eigen signaal/ruis-verhouding en vraagt om een type-specifieke extractiepipeline. Dezelfde aanpak voor alles werkt niet.

---

### E-mail

**Uniek aan e-mail:** geschreven commitments met tijdstempel en naam. Goedkeuringsketens zijn volledig traceerbaar (wie CC'd, in welke volgorde, wie goedkeurde). Cross-organisatie afspraken met klanten en leveranciers staan hier en nergens anders.

**Het ruisprobleem:** 98.4% van alle e-mails bevat ruis voor kennisextractie (Microsoft Research, Enron-corpus). Negen zones per e-mail: geciteerde replies, handtekeningen, juridische disclaimers, doorgestuurde inhoud, serverblokken. Zonder zone-filtering extraheer je dezelfde entiteit meerdere keren — één keer per geciteerde kopie in de thread.

**Vereiste pipeline:**
1. Thread-reconstructie via JWZ-algoritme (op basis van `Message-ID`, `In-Reply-To`, `References` headers)
2. Zone-classificatie (87-91% nauwkeurigheid, Zebra/ACL 2009)
3. Extractie alleen op `author_content` zones — niet op geciteerde tekst
4. Per-bericht extractie + thread-niveau aggregatie (beslissing, commitment, goedkeuringsketen)

**Entiteiten die natuurlijk ontstaan:** Party (afzender, ontvanger, CC), Thread (container), Commitment (actie-item met eigenaar en deadline), Decision (wat werd goedgekeurd), Artifact (bijlage).

**Wat je eruit haalt:** commitments met eigenaar en tijdstempel, goedkeuringsketens, beslissingen, entiteiten (personen, bedrijven, producten, deadlines).

**Bijlagen:** PDF/DOCX-bijlagen gaan door de document-pipeline (Docling), niet de e-mailpipeline.

**Scope:** gedeelde inboxen (support@, info@, sales@) als organisatie-connector. Individuele inboxen zijn persoonlijke keuze — die gaan naar de persoonlijke kennisbank van het individu.

**Privacy:** gedeelde inboxen hebben lage privacyverwachting en duidelijke bedrijfsdoelstelling — geen DPIA-complexiteit. Individuele inboxen zijn buiten scope voor de organisatieknowledgebase.

**Productie-benchmark:** LLM-extractie op e-mail + agenda + chat + documenten gecombineerd: 92% entity accuracy, 89% relationship accuracy (arXiv:2503.07993, 6-maands pilot, 78% adoptie).

---

### Chat (Slack / Microsoft Teams)

**Uniek aan chat:** micro-beslissingen die nooit worden opgeschreven ("we gebruiken Postgres", "skip die migratie voor nu"). Emoji-reacties als lichtgewicht goedkeuringssignaal. Expliciete deliberatietrails: vraag + discussie + reacties + beslissing zijn allemaal zichtbaar en tijdgestempeld in één thread.

**De extractie-eenheid is de thread, niet het bericht.** Individuele berichten zijn gemiddeld 3-8 woorden — te kort voor betrouwbare extractie. Pas na thread-aggregatie ontstaat voldoende context. Dit geldt voor zowel Slack als Teams.

**Signaal/ruis per kanaaltype:**

| Kanaaltype | Schatting signaal |
|---|---|
| `#decisions`, `#architecture` | 40-60% |
| `#dev-team`, `#product` | 15-25% |
| `#general`, `#announcements` | 5-15% |
| `#random`, `#fun` | 1-3% |

Kanaalnaam is een eerste-klas feature in de extractiepipeline: hetzelfde bericht in `#architecture` verdient agressievere extractie dan in `#random`.

**Vereiste pipeline:**
1. Thread-assemblage op `thread_ts` (Slack) of `conversationId` (Teams)
2. Pre-filter: berichten korter dan 10 tekens, bot-notificaties, emoji-only replies weggooien
3. Thread-classificatie: Q&A / beslissing / aankondiging / social chatter
4. Extractie op thread-niveau (niet bericht-niveau)
5. Trigger: thread-close-detectie (geen activiteit voor 2 uur) als primaire batch-trigger

**Kostenwaarschuwing:** aggregatieve queries over grote chat-corpora kosten 17-362 miljoen tokens per query zonder gespecialiseerde retrieval (arXiv:2505.23765). Goede indexering bij opslag is verplicht — niet optioneel.

**Extractie-pipeline:**
1. Thread-assemblage op thread-ID (Slack: `thread_ts`, Teams: `conversationId`)
2. Pre-filter — berichten onder 10 tekens, bot-notificaties, emoji-only weggooien
3. Kanaal-prior — verwachte signaaldichtheid op basis van kanaalnaam (`#decisions` = hoog, `#random` = laag)
4. Thread-classificatie — beslissing / Q&A / aankondiging / sociaal
5. **Pad A: beslissingen** — zelfde aanpak als vergadering-pipeline: claims per spreker → filtering → synthese
6. **Pad B: expertise-mapping** — wie beantwoordt wie, over welk onderwerp → `Person -[KNOWS_ABOUT]-> Topic` in de graaf
7. Trigger — 2 uur geen activiteit = thread gesloten, extractie start

**Wat je eruit haalt:** micro-beslissingen met eigenaar en tijdstempel, expertise-signalen (wie de go-to persoon is per onderwerp), issue-oplossing paren uit Q&A-threads, gelinkte resources met context.

**Indexeringsvereiste:** embed op thread-niveau, niet bericht-niveau. Sla kanaalnaam op als Qdrant-payload-filter. Zonder dit: 17-362 miljoen tokens per aggregatieve query (arXiv:2505.23765).

**Privacy:** Legitimate Interest-grondslag. DM's standaard uitsluiten — hogere privacyverwachting dan kanaalberichten.

---

### Support- en salescalls

**Uniek aan customer calls:** de klant is de primaire informatiebron. Pijnpunten in eigen woorden, concurrentiegenoemingen, bezwaren, beslissingshiërarchieën, churnsignalen — dit staat nergens anders in de organisatie.

**Support vs sales zijn fundamenteel verschillende kennisprofielen:**

| Dimensie | Salescall | Supportcall |
|---|---|---|
| Richting | Outbound, proactief | Inbound, reactief |
| Primaire kennistype | Kwalificatie, bezwaren, concurrentiesignalen | Issuepatronen, oplospaden, productfrictie |
| Kennisverval | Hoog (dealcontext verandert snel) | Laag (issuepatronen zijn stabiel over maanden) |
| GDPR-grondslag | Moeilijker voor prospects | Verdedigbaar voor bestaande klanten |

**De kernbevinding: de waarde zit in het patroon, niet het individuele gesprek.** Eén klant die zegt "de onboarding is verwarrend" is een notitie. 340 klanten die dat in zes maanden zeggen is een productroadmap-input. Gong bouwt een "revenue graph" waarbij waarde pas zichtbaar wordt bij aggregatie over honderden gesprekken.

**Nieuw entiteitstype vereist: externe Party.** Voor het eerst komen er entiteiten voor die buiten de organisatie staan. Twee subtypes:
- `external_party` (klant, prospect) — neemt deel aan het gesprek
- `mentioned_entity` (concurrent, product van concurrent) — wordt *genoemd*, neemt nooit deel

Die twee moeten expliciet gescheiden zijn in het datamodel. Een concurrent is geen deelnemer.

**Real-time vs post-call:** alle productiesystemen (Gong, Chorus, CallMiner) doen post-call analyse als standaard. Real-time is zinvol voor agent-assist en compliance-monitoring, maar niet voor kennisextractie. Start altijd met post-call.

**Extractie-taxonomy (cross-platform consensus: Gong, Chorus, CallMiner):**
1. Transcriptie + speaker-diarisatie
2. Sentiment-arc per spreker per segment
3. Named entities: personen, bedrijven, producten, datums, bedragen
4. Topics en thema's
5. Objections en buying signals (intent-classificatie)
6. Actiepunten en commitments
7. Competitor mentions
8. Compliance flags
9. LLM-samenvatting
10. Gestructureerde velden via LLM-extractie naar schema

**Call-type:** geen input maar output. Het LLM bepaalt het type (`support`, `sales`, `onboarding`, etc.) op basis van inhoud — als veld in de extractie-output, niet als aparte classificatiestap. Geen vooraf gedefinieerde taxonomie nodig.

**Patroonwaarde:** de werkelijke strategische waarde zit in aggregatie over alle calls. Eén klacht is een notitie; 340 klachten over hetzelfde in zes maanden is een productroadmap-input. Afgeleide patroonknopen worden periodiek berekend en zijn zelf kennisknopen in de graaf.

**Voys-voordeel:** Voys is een telecomplatform met bestaande call recording-infrastructuur en de juridische randvoorwaarden (GDPR-grondslag, consent-flows) zijn al geregeld op platformniveau. De connector is een integratie op bestaande opnames — geen nieuw opnamesysteem nodig.

---

### Vergadering (groep)

**Uniek aan vergaderingen:** beslissingen met sociale bewijskracht — verbaal genomen in aanwezigheid van de groep, met een ander gewicht dan asynchrone goedkeuringen. Consensus-type is meetbaar: opgelegd, toegejuicht, basis, of deliberatief (Cambridge Handbook of Meeting Science). Dat onderscheid vertelt hoe duurzaam een beslissing is. Dissent en minderheidsstandpunten bestaan alleen in het transcript — ze verdwijnen uit samenvattingen.

**De optimale extractie-aanpak: vier stappen, niet twee.**

Het onderzoek (FRAME, 2025) toont dat modulaire extractie hallucinaties met 3 punten op een 5-puntsschaal reduceert ten opzichte van directe samenvatting. De pipeline:

**Stap 1 — Claim-extractie per spreker** *(nieuw, vóór alles)*
Elke uitspraak wordt een gestructureerd tuple:
`{ speaker, claim, type: [voorstel | vraag | beslissing | commitment | bezwaar], confidence }`
Sprekerattributie wordt hier vastgelegd en nooit meer losgelaten.

**Stap 2 — Relevantiefiltering**
~40% van claims valt af: filler, herhaling, sociaal commentaar. Alleen inhoudelijke claims gaan door.

**Stap 3 — Verificatie**
Contradicteer claims elkaar? Spreekt dezelfde spreker zichzelf tegen? Is een commitment bevestigd of twijfelachtig uitgesproken ("misschien", "ik denk")?

**Stap 4 — Synthese**
Pas nu: decisions, action_items (met owner), open_questions, next_steps, summary markdown.

Dat zijn drie LLM-calls in plaats van twee — maar valse attributie (de meest schadelijke fout) wordt structureel voorkomen.

**Drie lagen bewaren:**
- Ruwe transcript — audittrail, geschillenresolutie
- Samenvatting (markdown) — menselijke consumptie
- Gestructureerde extractie (decisions + action_items + claims) — kennisbank input

**Sprekerattributie is kritiek.** De meest schadelijke fout in meeting-extractie is valse attributie: "Alex stelde X voor" terwijl Alex vroeg "wat als we X zouden doen?" — vraag vs. voorstel. Systemen die sprekers samenvoegen of tijdstempels weggooien forceren het LLM te raden wie wat zei. Dat maakt valse organisatierecords.

**Drie lagen bewaren, niet één kiezen:**

| Laag | Wat het bewaart | Verliest | Gebruik |
|---|---|---|---|
| Ruwe transcript | Alles: twijfeltaal, dissent, sociale dynamiek | Moeilijk doorzoekbaar | Audittrail, geschillen |
| Samenvatting | Hoofdonderwerpen, kernbeslissingen | Rationale, minderheidsstandpunten | Menselijke consumptie |
| Gestructureerde extractie | Beslissingen + eigenaren + deadlines | Context, redenering | Taakopvolging |
| Kennisgraaf | Relaties over vergaderingen heen | In-meeting dynamiek | "Wie heeft X besloten?" |

**Het correctievenster:** een Slack-bot implementatie toonde dat een 15-minuten correctievenster na de vergadering de actiepunt-capture rate van 22% naar 100% bracht en false positives van 14% naar 3% reduceerde. Automatisch extraheren haalt 60-80%; menselijke mini-validatie direct na afloop maakt het compleet.

**Productie-benchmark:** Amazon Nova meeting pipeline: gemiddeld onder 6 minuten voor een 1-uur vergadering, 15% verbetering in actiepunt-identificatie ten opzichte van baseline.

**Aandachtspunt:** actiepunt-annotatie heeft een inter-rater agreement van kappa=0.36 — zelfs mensen zijn het maar voor 36% eens over wat een actiepunt is. Elk geautomatiseerd systeem erft die ambiguïteit. Menselijke bevestiging blijft nodig voor hoge betrouwbaarheid.

---

### 1-op-1 gesprekken

**Geen apart infrastructuurtype.** Een 1-op-1 gesprek gaat via een van de bestaande opname-pipelines: Google Meet (Vexa), telefoon (Voys), of losse opname (Scribe). De beslissing om op te nemen ligt bij de gebruiker — Klai voegt daar geen extra poort aan toe.

**Wat structureel anders is:** twee deelnemers in plaats van een groep. De extractie-pipeline is identiek aan een vergadering, maar de output verschilt:
- Geen groepsdynamica of meerderheidsbeslissingen
- Sterkere commitment-attributie: met twee sprekers is "ik regel het" altijd eenduidig van wie
- Hogere kennisdichtheid per minuut: geen coördinatie-overhead, 100% van het gesprek is inhoud

**Wat uniek is ten opzichte van vergaderingen:** persoonlijke commitments, coaching-inhoud, carrièresignalen, eerlijke feedback — kennis die in groepsverband zelden uitgesproken wordt. Dit is ook de reden dat de privacy-verwachting hoger is dan bij een vergadering, maar dat is aan de gebruiker om te wegen bij de keuze om op te nemen.

**Praktisch gevolg:** geen apart connector-type nodig. Het opname-platform bepaalt de pipeline. De kennisbank krijgt twee-spreker-transcripten als input — hetzelfde formaat als een groepsvergadering.

---

### Hoe elk communicatietype de pipeline inkomt

| Type | Extractie-eenheid | Verplichte voorbewerking | Pipeline | Primaire output |
|---|---|---|---|---|
| Vergadering | Transcript + sprekerlabels | Sprekerattributie bewaren | 4-staps FRAME: claims → filtering → verificatie → synthese | Decisions, Action items, Topics |
| 1-op-1 | Transcript (2 sprekers) | Zelfde als vergadering | Zelfde als vergadering | Commitments, Decisions |
| Call | Post-call transcript | Diarisatie, noise-filtering | Type-classificatie als output, 10-veld extractie | Issues, Commitments, Competitors, Call-type |
| E-mail | Thread (na JWZ-reconstructie) | Zone-filtering (87-91% nauwkeurigheid) | Per-bericht → thread-aggregatie | Commitments, Decisions, Approval chains |
| Chat | Thread (na 2u inactiviteit) | Pre-filter <10 tekens, kanaal-prior | Pad A: beslissingen / Pad B: expertise-mapping | Micro-decisions, Expertise-signalen, Issue-solution pairs |

**De universele stappen** voor elk type:
1. Ruwe audio of tekst → tekst (ASR of native)
2. Extractie-eenheid bepalen (thread, call, transcript)
3. Type-specifieke ruisverwijdering
4. Gelaagde extractie: claims per spreker → filtering → relaties → beslissingen → commitments
5. Lichtgewicht menselijke validatie (correctievenster: 22% → 100% capture rate, false positives 14% → 3%)
6. Opslaan in alle drie lagen: PostgreSQL + Qdrant + FalkorDB

**De rode draad:** automatische extractie haalt 60-80%. Menselijke mini-validatie direct na afloop maakt het compleet. De kwaliteit wordt bepaald bij opslag — niet bij zoeken.
