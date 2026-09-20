# Intakekwaliteit — meetlog

## 1. Meetgrens (19 september 2026)

Onderzocht: echte interne chat- en widgetvragen, opgeslagen bronartikelen en
zoekfragmenten, supportanalyses, actuele code en een herhaalde crawl. Ruwe gegevens
staan uitsluitend in privébestanden. Onderstaande aantallen betreffen afgebakende
onderzoeksets, geen klantgebruikscijfers of algemene productpercentages.

Bestaand werk hergebruikt: de LibreChat-lezer en berichtnormalisatie, RAGAS-suite
plus artikelcontrole, support-gap-evaluator en de in-process widgetherspeling uit
het antwoordonderzoek. Nieuw zijn alleen een leesexport en een offline intake-eval;
beide staan in het bestaande meetpuntenregister. Geen nieuwe provider of dependency.

## 2. Wat de intake daadwerkelijk gebruikt (19 september)

| Onderdeel | Vastgesteld in code en opslag | Betekenis |
|---|---|---|
| HyPE-vragen | Vragen worden ook gegenereerd voor typen zonder vraagvector. `content_profiles.py` bepaalt het embedden per type en diepte. | Vragen in de payload bewijzen niet dat de zoekroute ze gebruikt. |
| Vraagvector | Eén vector voor de samengevoegde vragen; `search.py` zoekt met `vector_questions`. De vraagtekst gaat niet naar het antwoordmodel. | Een promptwijziging verandert kandidaten, niet rechtstreeks het antwoord. |
| Context | `context_prefix` wordt wel aan de zoektekst gekoppeld. | Het onderwerp kan een los fragment situeren. |
| Samenvatting | `enrichment_tasks.py` genereert/gebruikt een samenvatting in het lokale `extra_payload`, maar slaat haar daar niet terug in PostgreSQL op. `qdrant_store.py` sluit haar uit de payload uit. | In de onderzochte actieve artifacts was geen historische samenvatting beschikbaar. Niet achteraf te beoordelen. |
| Chunking | Fase 1 gebruikt profielgrenzen; fase 2 herverdeelt standaard op 1200 tekens, overlap 200, parent 6000. Kopteksten komen erbovenop. | 1200 is geen harde limiet op de uiteindelijke payloadlengte en geen universele fase-1-instelling. |
| Uploads | De Docling-route geeft bestaande chunks door met `skip_chunking`; het uploadtype is `document`. | Een profiel voor `pdf_document` dekt niet automatisch iedere PDF-upload. |
| Navigatie | `graph_episode_skip_reason` herkent navigatiepagina's al, maar sluit alleen graafextractie uit. | Hergebruik die herkenning als een toekomstige HyPE-proef nodig is; bouw geen tweede detector. |

In het onderzochte supportcorpus had 98,8% van de chunks vragen en een
contextprefix; 98,4% had een vraagvector. Andere aangetroffen typen met vragen
zonder vraagvector waren `document`, `notion_page`, `plain_text` en `web_page`.
Dat is bestaande profielwerking, geen bewezen verlies aan antwoordkwaliteit.

## 3. Bronverlies vóór het chunken (19 september)

Een scan naar lege navigatiestappen signaleerde dit patroon in ongeveer 10% van
de onderzochte supportartikelen. Het patroon is een aanleiding tot inspectie,
geen bewezen defect voor ieder gevonden artikel.

Bij drie opnieuw opgehaalde publieke pagina's is de oorzaak direct gereproduceerd:
inline linktekst staat in `raw_markdown`, ontbreekt in `fit_markdown`, en
`adapters/crawler.py` kiest `fit_markdown or raw_markdown`. De ingest slaat die
gekozen tekst ook onder de naam `raw_markdown` op. Het verlies is dus al aanwezig
vóór chunking, contextbeschrijving of vraaggeneratie.

Besluit: bronbehoud is de eerste kandidaat voor een antwoordmeting. Een globale
wisseling naar ruwe tekst is nog niet bewezen: die kan ook navigatieruis toevoegen.
Alleen opnieuw verrijken herstelt verdwenen tekst niet. De crawler slaat bovendien
ongewijzigde HTML vroeg over; een reparatie vereist een gecontroleerde hercrawl.
Geen productie-instellingen gewijzigd en geen herindexering gestart.

## 4. Context en gegenereerde vragen: steekproef van 30 (19 september)

Selectie vooraf: deterministische hashvolgorde van verrijkte supportchunks, één
chunk per artikel, eerste dertig verschillende artikelen. Dit is geen uniforme
artikelsteekproef: lange artikelen hebben meer kansen. `klai-large` kreeg bron,
chunk, contextprefix en vragen zonder generatiemodel of versie. Broninspectie door
de onderzoeksagent controleerde alle dertig; er zijn geen menselijke labels.

| Controle | Uitkomst | Grens |
|---|---|---|
| Contextprefix | Judge accepteert 30/30; broninspectie vond geen duidelijke tegenspraak. | Selectie bevat alleen reeds verrijkte chunks. |
| Vragen antwoordbaar uit chunk | Judge accepteert 131/150. | Geen betrouwbare precisiescore: twee linklijsten kregen samen 9/10 goedkeuringen voor procedurevragen, terwijl de procedures ontbreken. |
| Historische samenvatting | Niet meetbaar. | Opnieuw genereren zou een nieuwe samenvatting beoordelen. |

De 150 gegenereerde vragen beginnen in 36% van de gevallen met Hoe/How. In de
onderzochte echte gebruikersberichten is dat ongeveer 4%. Medianen zijn 12 en 11
woorden. Dit meet formulering, geen semantische dekking of oorzaak van mislukking.
De aanname dat bezoekersvragen alleen langer moeten worden gemaakt volgt hier niet uit.

## 5. Gap detection: scores en tekstoordeel (19 september)

De bestaande `evaluate_support_gaps.py` draaide op een privé-export. Van 37
onderzochte cases waren 36 geanalyseerd en één mislukt. Er zijn 92 bevindingen,
waarvan 22 onzeker. Geen case bevatte een bruikbare `_reference`; precision en
recall zijn daarom `null`, niet nul en niet een gemeten nauwkeurigheid.

| Tekstoordeel | Geen score-signaal | Soft-signaal |
|---|---:|---:|
| Covered | 29 | 5 |
| Findability | 5 | 1 |
| Incomplete | 7 | 4 |
| Missing | 8 | 10 |
| Uncertain | 11 | 11 |
| Non-knowledge | 0 | 1 |

Geen hard-signalen in deze selectie. De tabel vergelijkt opgeslagen `gap_type`
met `diagnosis`; het tekstoordeel kan meerdere zoekopdrachten meenemen. Het is geen
vergelijking met menselijke waarheid. Een score-signaal ontbreekt bij 15/29
missing/incomplete-bevindingen en 5/6 findability-bevindingen; 5/34 covered krijgt er wel een.

Bij 5/6 findability- en 7/11 incomplete-bevindingen verwijst minstens één
opgeslagen passage naar een artikel met een lege navigatiestap. Dat bewijst
samenloop, geen oorzaak: de ontbrekende stap hoeft niet de gestelde vraag te raken.
Er is nog geen verdedigbaar percentage 'gaten veroorzaakt door intake'.

Minimale inhoudslus, niet gebouwd: gebruik de bestaande clustering en reviewvelden;
een inhoudseigenaar bevestigt het gat en het antwoordartikel, maakt een voorstel,
publiceert via het bestaande KB-pad en herhaalt dezelfde vraag tegen de nieuwe
bronversie. Leg eerst menselijke referentielabels vast met het bestaande reviewcontract.

## 6. Bevraging: nameting op werkelijk verkeer (19 september)

In het vastgelegde logvenster zijn 154 zoekbesluiten met twee uitgevoerde
queryvarianten en nul mislukte varianten gevonden. Ze voegden 2–6 kandidaten toe
(mediaan 4). De corresponderende activiteit valt samen met herspelingen.

Een aanvullende databasecontrole na de laatste wijziging vond nul echte
widgetantwoorden en wel preview/testantwoorden. Er waren geen
`answer_grounding_late`-events in het onderzochte oorspronkelijke logvenster.
Daarom is nog geen uitspraak mogelijk over antwoordwinst of late controles op
werkelijk bezoekerverkeer. De geïsoleerde proeven hieronder zijn geen live nameting.
De bestaande zoekaanpak is niet opnieuw gebouwd of aangepast.

## 7. Antwoordpassage en zoekfragment apart meten (19 september)

Acht vooraf gekozen bronpassages bij echte vragen (zes widgetvragen, twee interne
vragen), met bestaande artikelidentiteit en letterlijke citaten uit de opgeslagen
brontekst. Dit is een doelgerichte diagnose, geen representatieve succeskans.
De geïsoleerde actuele retrieval-route kreeg de echte voorgeschiedenis en de
betreffende KB. Interne vragen zijn met service-identiteit gezocht, niet met de
historische gebruikerssessie. Geen reconstructie van de oorspronkelijke zeven
gevallen uit antwoordlog §2.31: de oude volledige zoekcontext is niet beschikbaar.

| Meting | Uitkomst |
|---|---:|
| Antwoordspan in één geïndexeerde child | 8/8 |
| Die antwoordende child teruggevonden | 3/8 |
| Antwoordspan in teruggegeven context | 5/8 |
| Antwoordartikel geheel afwezig | 2/8 |
| Artikel aanwezig, gekozen antwoordpassage afwezig | 1/8 |

De spans zijn 164–534 tekens. Bij twee van de vijf gedekte gevallen levert een
parent de passage terwijl de antwoordende child niet geselecteerd is. Nul van deze
acht spans is onvindbaar door splitsing, maar dat zegt niets over langere
meerledige antwoorden. De drie gemiste passages bewijzen zoekverlies; ze bewijzen
niet dat ontbrekende HyPE, context of taxonomie de oorzaak is. In één gemist
artikel ontbreken contextprefix en vraagvector, wat een vervolgproef rechtvaardigt.

Vier aanvullende navigatiestappen, uit verse broncrawls bij vier van dezelfde
widgetvragen, zijn apart vergeleken na dezelfde bestaande Markdown-normalisatie
op bron en chunks. Eén stap staat in de index én de teruggegeven tekst; drie
ontbreken in beide. De verse gefilterde crawl laat de linkbestemmingen opnieuw weg.
De eerste acht spans maten dus behoud van reeds opgenomen feiten, deze vier
meten verlies ten opzichte van de externe bron. De tellingen mogen niet worden
opgeteld tot één algemeen foutpercentage. Met de vijf extra crawls is linkverlies
nu op acht pagina's gereproduceerd, waaronder artikelen uit de echte vragen.

## 8. Keuze van vervolgwerk (19 september)

| Prioriteit | Kandidaat | Verwacht effect en benodigd werk |
|---|---|---|
| 1 | Linktekst bij het crawlen behouden | Direct bronverlies opheffen. Bestaande raw/fit-uitvoer en selectie hergebruiken; eerst volledige antwoordvergelijking en vervuiling door navigatie meten, daarna hercrawl inclusief HTML-cache-invalidering begroten. |
| 2 | Vraaggeneratie op navigatiechunks begrenzen | Mogelijk minder kandidaten die een procedure beloven zonder haar te bevatten. Bestaande navigatiedetector hergebruiken; aanpassing pas na blinde antwoordproef. |
| 3 | Gebruikte documentsamenvatting bewaren | Maakt audits herhaalbaar; bestaande `update_artifact_extra` hergebruiken. Geen bewezen antwoordwinst. |
| 4 | Profielen afstemmen op werkelijke uploadtypen | Mogelijk bredere HyPE-dekking. Eerst per type een eigen vraagset; geen algemene inschakeling op basis van deze supportproef. |

Geen nieuwe chunker, crawler, judgeframework of publicatieworkflow gebouwd.
De productie-intake is ongewijzigd. Verwachte effecten hierboven zijn hypotheses;
bronbehoud is gereproduceerd, de winst in uiteindelijke antwoorden is nog niet gemeten.

## 9. HyPE aan/uit door de volledige widgetroute (19 september)

Twaalf echte eerste widgetvragen, vooraf vastgelegd; drie antwoordpogingen per
variant, 72 antwoorden. Twee afzonderlijke containers gebruiken de draaiende
portal- en retrieval-images. Alleen de `vector_questions`-zoekleg verandert;
queryvarianten, herschrijven, bronselectie, parentcontext, controles en reparatie
blijven actief. Geen gespreksopslag, retrievallog of gapevents; databaseverbindingen
staan op alleen-lezen. Eén beurt per minimaal acht seconden.

De bestaande blinde beoordelingsrubriek is hergebruikt met `klai-large`, volledige
opgehaalde passages en twee rondes met willekeurige antwoordvolgorde.

| Uitkomst | Ronde 1 | Ronde 2 | Samen |
|---|---:|---:|---:|
| HyPE aan beter | 14 | 21 | 35 |
| HyPE uit beter | 16 | 13 | 29 |
| Gelijk | 6 | 2 | 8 |
| Onbewezen detail, aan / uit | 12 / 7 | 10 / 12 | 22 / 19 |

Beide varianten geven 34/36 antwoorden met bron. Mediane doorlooptijd is voor
beide 6,59 seconden. Het verschil van zes beoordelingen ligt onder de bestaande
ruisgrens; de richting draait tussen rondes om. **Besluit: HyPE ongewijzigd laten.**

De teruggegeven passages zijn overwegend `kb_article`, naast graafresultaten en
enkele `plain_text`-passages zonder vraagvector. Dit is geen meting van het
inschakelen van HyPE op andere typen. Een alternatieve vraagprompt is evenmin
beproefd. Daarvoor ontbreken in deze proef vooraf gevalideerde vraagsets per type;
de telling van vragen en vectoren uit §2 vervangt die antwoordproeven niet.

## 10. Nieuwe bestaande gap-controle hergebruikt (19 september)

Tijdens het onderzoek kwam een bron-afgeleide detectorcontrole op main:
`ingest_gap_evaluation.evaluate_ingest_snapshot`, met een begrensde nachtelijke
RAGAS-aanroep. Deze geeft de gap-beoordelaar een bron en houdt die daarna weg.
Dat is een andere vraag dan onze offline meting van bronbehoud en zoekdekking.
Geen tweede automatische detectorcontrole toegevoegd.

De nieuwe controle is in een aparte container uitgevoerd op vijf van de echte
widgetvragen uit §7, met hun volledige opgeslagen antwoordartikel. Uitkomst:
**5 unscorable, 0 scored, 0 failed; inconclusive**. Met bron waren de oordelen
missing, incomplete, uncertain, covered en non_knowledge. Alleen covered ging door
naar de proef zonder bron; daar werd het oordeel non_knowledge. Dit geeft geen
nauwkeurigheidspercentage en is geen bewijs dat de intake goed of slecht scoort.
Een bekend antwoordfragment is nog geen volledig beantwoorde gebruikersvraag.
De vernieuwde offline support-gap-evaluator bevestigt opnieuw nul beoordeelbare
cases wegens ontbrekende menselijke referenties.

## 11. Bronbehoud: extern onderzoek en vooraf vastgelegde proef (19 september)

De [systeembeoordeling en werkwijze](systeemoordeel.md) zijn vastgelegd in PR #1566.
Afzonderlijke Sol-agents onderzoeken de filter, ontwerpen de proef en voeren de
kleine configuratiewijziging uit; de hoofdagent controleert de meetopstelling.

Primaire bronnen, geraadpleegd op 19 september:
[Crawl4AI Fit Markdown](https://docs.crawl4ai.com/core/fit-markdown/), de
[filterbron van 0.9.3](https://github.com/unclecode/crawl4ai/blob/v0.9.3/crawl4ai/content_filter_strategy.py)
en [Markdown-generator](https://github.com/unclecode/crawl4ai/blob/v0.9.3/crawl4ai/markdown_generation_strategy.py).
De draaiende versie is daadwerkelijk 0.9.3; de filterklasse is tegen die bron
gecontroleerd. Pruning beoordeelt een ouder vóór zijn kinderen. Een span met
voornamelijk linktekst kan daardoor geheel verdwijnen vóór de link wordt bezocht.

Op acht vastgelegde pagina's is dezelfde `cleaned_html` door de daadwerkelijke
generator verwerkt. De oude configuratie reproduceert de REST-uitvoer 8/8 exact.
`preserve_tags=["a"]` verandert niets. De bestaande optie `preserve_tags=["span"]`
herstelt alle 18 eerder ontbrekende unieke labels; het aantal links gaat van
37 naar 83, gelijk aan de opgeschoonde HTML. Genormaliseerde tekst groeit 1,39%; Markdown
groeit 6,29%, mede door de herstelde URL's. De overige pruning blijft actief.
Een synthetische inline-linkproef faalt met de oude configuratie en slaagt met
de kandidaat op dezelfde geïnstalleerde versie. Dit is nog geen antwoordwinst.

Vooraf vastgelegd: zestien unieke echte vragen met hun oorspronkelijke historie,
drie antwoordpogingen per variant en twee blinde beoordelingsrondes. Overlappende
feit- en navigatievragen zijn samengevoegd, met meerdere referentiespans per vraag.
Dezelfde vraag telt dus niet tweemaal als onafhankelijk geval.

| Variant | Doel |
|---|---|
| P | Exacte kopie van de bestaande tenantindex. |
| R | Vier gekozen artikelen opnieuw verrijkt vanuit dezelfde oude tekst; meet drift door opnieuw verwerken. |
| N | Dezelfde vier artikelen opnieuw verrijkt met spanbehoud; het verschil met R is het broneffect. |

De vier oude artikelteksten zijn exact gelijk aan de verse oude filteruitvoer.
Alle varianten behouden dezelfde niet-behandelde zoekconcurrenten. Eigen Qdrant-
collecties en aparte parentteksten voorkomen productiemutaties. De bestaande
widgetroute blijft inclusief queryvorming, zoeken, herordenen, controles,
reparatie en bronweergave actief; er wordt geen antwoordartikel vooraf ingevoegd.
De drie testindexen zijn privé bewaard met de bestaande
[Qdrant-snapshotfunctie](https://qdrant.tech/documentation/operations/snapshots/);
bestandsgrootte en controlesom zijn na het kopiëren voor alle drie gecontroleerd.

De beslispoort blijft antwoordlog §6: beide beoordelingsrondes dezelfde richting,
voorkeursverschil boven de ruisgrens, geen verdwenen ondersteund antwoord en geen
toename van onbewezen details. Rebuild-drift wordt apart gerapporteerd; fouten of
ontbrekende resultaten mogen niet als lege maar geslaagde zoekactie meetellen.
Latency, modelgebruik en uitkomst per unieke vraag worden eveneens vastgelegd.

### Bredere broncontrole en kleinere kandidaat

Twintig extra pagina's zijn vooraf gekozen: tien willekeurige gewone artikelen
met vaste seed, vijf korte pagina's en vijf reeds als navigatie herkende pagina's.
De oude generator reproduceert hun REST-uitvoer 20/20 exact, inclusief het
gebruik van de uiteindelijke URL na redirects. Globaal spanbehoud wijzigt 18/20
pagina's; op de vijf navigatiepagina's groeit de genormaliseerde tekst 63,28%.
Dat maakt de eerste acht pagina's onvoldoende onderbouwing voor algemene uitrol.

De bestaande optie `preserve_classes=["highlighted-color"]` geeft op de acht
probleempagina's byte voor byte dezelfde Markdown als spanbehoud. Op de twintig
extra pagina's verandert alleen de tekst van twee gewone artikelen: zestien
extra links, 0,18% extra genormaliseerde tekst binnen de gewone artikelen en
0,15% over de gehele steekproef. De korte en navigatiepagina's blijven exact
gelijk. De smallere optie is daarom vóór de blinde antwoordbeoordeling gekozen.

De oorspronkelijke acht pagina's bevatten na herstel 83 links en twee
afbeeldingen; een telling van Markdown-linksyntax alleen zou die ten onrechte
als 85 links rapporteren. Alle oorspronkelijke woordvolgordes blijven behouden.
De steekproef dekt twee bronlayouts, niet alle mogelijke websites. De oplossing
blijft gekoppeld aan een bestaande opmaakklasse en beschermt geen tekst waarvan
een bovenliggend element al door pruning is verwijderd.

### Controle van de meetopstelling

De antwoordgeneratie levert alle 144 verwachte resultaten. De eerste juryproef
is na 80 oordelen gestopt: de gebruikte prompt noemde alle gevonden passages
ten onrechte de eigen antwoordcontext, inclusief passages die de selectie niet
aan het antwoordmodel had doorgegeven. Deze oordelen tellen niet mee. De
herstelde proef gebruikt de bestaande `render_evidence_context` voor precies
de daadwerkelijk gebruikte passages; overige gevonden tekst blijft apart
beschikbaar als referentie. Alle 831 gebruikte passages in de 144 antwoorden
blijven in die weergave behouden. De antwoordparen en beslisgrenzen veranderen
niet; beide beoordelingsrondes worden opnieuw uitgevoerd.

Een verbindingsfout (`httpx.ReadError`) onderbreekt de herstelde juryproef na
24 geldige oordelen. Hervatten behoudt die oordelen en controleert hun exacte
plaats in de vooraf vastgelegde volgorde. Alleen transportfouten krijgen maximaal
drie pogingen; elke poging houdt acht seconden startafstand. Prompt, rubric,
antwoordparen en beslisgrenzen blijven ongewijzigd.

### Van gevonden passage naar gebruikte antwoordcontext

Tien vooraf bekende bronspans, zes feitelijk en vier navigerend, zijn elk in drie
antwoordpogingen gevolgd. Dit is letterlijke dekking binnen een geselecteerde
steekproef, geen semantische score of succespercentage voor alle zestien vragen.

| Variant | Gevonden span, alle typen | Gebruikte span, alle typen | Gebruikte feitelijke span | Gebruikte navigatiespan |
|---|---:|---:|---:|---:|
| P | 14/30 | 11/30 | 8/18 | 3/12 |
| R | 15/30 | 14/30 | 11/18 | 3/12 |
| N | 21/30 | 15/30 | 6/18 | 9/12 |

Bij één feitelijke span onderbreekt de vooraf bekende herstelde navigatietekst
de letterlijke woordreeks. Alleen die invoeging weglaten voor een aanvullende
controle geeft N 9/18 gebruikte feitelijke spans. De hoofdmeting hierboven
blijft letterlijk. Een andere gevonden antwoordbron valt bij N in alle drie
pogingen buiten de bestaande limiet van drie geselecteerde bronnen.

De getoonde herordenscore is geen globale score waarmee dit direct te repareren
is: hoofdvraag en herformulering krijgen afzonderlijke scores, waarna reciprocal
rank fusion hun ranglijsten samenvoegt. De bronselectie gebruikt vervolgens die
volgorde. Opnieuw sorteren op de getoonde deelscores zou het bestaande contract
veranderen. Zie het [oorspronkelijke RRF-onderzoek](https://plg.uwaterloo.ca/~gvcormac/cormacksigir09-rrf.pdf)
en de [Qdrant-uitleg](https://qdrant.tech/documentation/search/hybrid-queries/).
Er is daarom geen zoek- of selectiewijziging aan deze bronproef toegevoegd.

### Antwoorduitslag en besluit

Alle 144 antwoorden en 192 geldige juryoordelen zijn compleet. De onderstaande
voorkeuren tellen oordelen over dezelfde zestien vragen, geen onafhankelijke
bezoekers. R is de opnieuw verwerkte oude tekst; N gebruikt de herstelde tekst.

| Vergelijking | Ronde | Oude variant wint | Nieuwe variant wint | Gelijk | Verschil nieuw minus oud |
|---|---:|---:|---:|---:|---:|
| P → R | 1 | 21 | 26 | 1 | +5 |
| P → R | 2 | 25 | 21 | 2 | −4 |
| R → N | 1 | 26 | 22 | 0 | −4 |
| R → N | 2 | 21 | 27 | 0 | +6 |

**Besluit: niet uitrollen.** N wint samen 49 keer tegen 47 voor R. Beide rondes
moesten positief zijn en het gezamenlijke verschil minstens tien; de uitkomst
voldoet aan geen van beide voorwaarden. Op vraagniveau zijn zes verschillen
positief, vier negatief en zes nul. Bij omgekeerde presentatie krijgen 11/48
R–N-paren een andere winnaar. Dat kan presentatiegevoeligheid en variatie van de
beoordelaar omvatten; deze proef scheidt die oorzaken niet.

Opnieuw verwerken zonder bronwijziging geeft R 47 voorkeuren tegen 46 voor P,
drie gelijke uitkomsten en eveneens tegengestelde rondes. Er is dus geen
systematische voorkeur voor de rebuild. Binnen uitsluitend de R–N-beoordeling
signaleert de jury onbewezen details bij R 23/96 en N 14/96 keer. Die gunstige
nevenuitkomst vervangt de mislukte voorkeurspoort niet en is geen menselijk
gevalideerde nauwkeurigheidsscore.

Afzonderlijke agents hebben alle 39 gemarkeerde antwoordparen tegen vraag,
historie, antwoord en werkelijk gebruikte context gecontroleerd: veertien P–R,
veertien R–N en elf afgeleide P–N-signalen. Dit zijn geen menselijke labels of
39 onafhankelijke vragen; P–N was bovendien geen rechtstreeks voorkeursduel.
Binnen R–N bevestigt die inspectie vijf verliezen van bruikbare bronsteun bij
twee vragen. De relevante bron wordt gevonden, maar de limiet van drie bronnen
houdt haar buiten de gebruikte context. Andere signalen komen uit antwoordvariatie,
onbewezen toevoegingen of inconsistente juryoordelen. Ook de veiligheidscontrole
ondersteunt daarom geen uitrol.

De mediane antwoordtijd is P 6,79 s, R 7,03 s en N 6,43 s; p95 is respectievelijk
9,58 s, 9,64 s en 9,70 s. Vastgelegde tokens van de laatste antwoordaanroep zijn
275.190, 269.999 en 271.371. Dit omvat niet alle zoek-, reparatie- en jurykosten;
een totale kostenvergelijking is hiermee niet mogelijk.

De configuratiekandidaat blijft buiten main en productie. De vastgelegde teksten,
indexen en uitslagen blijven privé beschikbaar. De extractiewinst rechtvaardigt
geen hercrawl zolang de antwoordwinst ontbreekt. De proef bevat één verrijking per
herbouwde variant en drie antwoordpogingen; zij schat geen variatie tussen
meerdere onafhankelijke intakeverrijkingen. De eerstvolgende inhoudelijke
kandidaat blijft de kwaliteit van gegenereerde vragen bij navigatie-inhoud.

## 12. Vraagvectoren: invloed aantonen vóór een promptwijziging (19 september)

Voor deze stap zijn [HyPE v1](https://arxiv.org/html/2607.29402v1),
[AQG v1](https://arxiv.org/html/2508.09755v1) en
[Doc2Query-- v3](https://arxiv.org/abs/2301.03266v3) geraadpleegd.
HyPE laat kwaliteitsselectie open; AQG vraagt om vragen die rechtstreeks uit de
chunk beantwoordbaar zijn. Doc2Query-- toont schadelijke gegenereerde uitbreidingen
in een andere zoekopzet. Deze bronnen motiveren broncontrole, geen verwachte
winst voor onze eigen keten.

De bestaande intake voegt alle gegenereerde vragen samen tot één vraagvector.
Een lege vragenlijst is al toegestaan en gebruikt de oorspronkelijke chunktekst
als vectorinput. Het KB-contentprofiel heeft een bestaand promptveld; een apart
filter, nieuw model of nieuwe module is voor een eerste kandidaat niet nodig.
Navigatieherkenning in de bestaande policy geldt alleen voor graph-verrijking;
die policy uitbreiden zou meer veranderen dan vraaggeneratie.

Alle dertig eerder onderzochte chunks hebben nog dezelfde tekst en vragen, plus
een vraagvector met 1024 dimensies. Een nieuwe broninspectie door een agent
bevestigt twee linklijsten met samen tien onbeantwoordbare procedurevragen.
De oorspronkelijke judge keurde negen daarvan goed. Dit zijn agentlabels,
geen onafhankelijke menselijke referenties.

De diagnose gebruikt de zestien vastgelegde echte vragen uit §11, met hun
opgeslagen resolved query van P, antwoordpoging 1. De productiehelper bouwt de
tenant-, kennisbank- en tijdsfilters en haalt per zoekkanaal 240 kandidaten op.
De drie kanalen leveren samen via RRF zestig kandidaten. Een tweede zoekactie
gebruikt dezelfde vectoren en filters, maar laat uitsluitend het vraagkanaal weg.
De kennisbankkoppeling is vooraf via de bestaande tenantgebonden sessie geverifieerd.

| Controle | Uitkomst |
|---|---:|
| Complete vragen / afzonderlijke zoekkanalen | 16/16 / 48/48 |
| Vragen waarvan de eerste twintig kandidaten veranderen zonder vraagkanaal | 16/16 |
| Beide foutieve navigatiechunks aanwezig in enig kanaal bij deze vragen | 0/16 |

**Besluit:** het vraagkanaal beïnvloedt de zoekselectie, maar deze meetset toont
geen blootstelling aan de twee aangetoonde navigatiefouten. Daarmee is een
gerichte promptwijziging nog niet meetbaar onderbouwd. Eerst bestaande echte
gesprekken op passende onderwerpen controleren, vóór kandidaatgeneratie.
De diagnose bevat geen herformuleringen, nieuwe coreference-stap, reranking,
bronselectie of antwoorden; er volgt dus geen uitspraak over antwoordkwaliteit.
Productiegegevens zijn alleen gelezen; de eigen meetcontainer is na exit 0 verwijderd.

## 13. Vraagvectoren: aanvullende onderwerpcontrole (19 september)

Na §12 zijn bestaande gespreksuitvoer en brononderwerpen gebruikt om vooraf de
eerste twaalf unieke passende vragen in tijdsvolgorde vast te leggen. Vragen zijn
niet herschreven of verzonnen; antwoorden en zoekuitkomsten bepaalden de selectie
niet. Drie widgetvragen hebben expliciet geen test- of previewmarkering. Negen
LibreChat-vragen zijn complete gebruikersberichten, maar die export bevat geen
testmarkering; hun authenticiteit is daarmee niet volledig vast te stellen.
Eén vraagtekst komt ook in de eerdere zestien voor. De groepen optellen als
28 onafhankelijke vragen zou dus onjuist zijn.

Dezelfde zoekhelper en tenant-/kennisbankfilters zijn hergebruikt. Ditmaal is
de ongewijzigde gebruikersvraag de input; beschikbare historie wordt in deze
basiskanaaldiagnose niet verwerkt. Alle twaalf zoekacties zijn compleet, met
36 kanalen van 240 kandidaten en 24 fusies van zestig kandidaten.

Eén van de twee foutieve navigatiechunks verschijnt nergens. De andere verschijnt
bij twee vragen: eenmaal op fusiepositie 56, terwijl dezelfde zoekactie zonder
vraagkanaal positie 51 geeft; eenmaal buiten de zestig gefuseerde kandidaten.
Geen van beide chunks bereikt bij enige vraag de eerste twintig kandidaten
die de reranker aangeboden krijgt. Dit bewijst geen onschadelijkheid bij andere
vragen, herformuleringen of het volledige chatpad.

**Besluit:** geen nieuwe vraagprompt bouwen op deze twee geselecteerde fouten.
Het opslagprobleem is aangetoond, maar deze controles verbinden het niet aan
verlies in de antwoordketen. De cohortuitbreiding stopt hier. Beide eigen
meetcontainers uit §12–13 zijn verwijderd; productie is alleen gelezen.

## 14. Vierde bron: proef door de antwoordketen (19 september)

Extern onderzoek begint bij [RAGAs](https://aclanthology.org/2024.eacl-demo.16/),
[Lost in the Middle](https://aclanthology.org/2024.tacl-1.9/) en
[RECOMP](https://openreview.net/forum?id=mlJLVigNHp). Zij ondersteunen het apart
meten van antwoorddekking, nutteloze context en werkelijk brongebruik. Meer
context is op zichzelf geen bewijs voor betere antwoorden.

De code bevat al `build_evidence_pack(..., max_sources=...)`. Bij één kennisbank
houdt de huidige retrievalroute maximaal drie verschillende bronnen over; bij
meerdere kennisbanken vijf. De helper bewaart de bestaande volgorde. Ruwe scores
van verschillende zoekpasses opnieuw sorteren zou een andere ingreep zijn.

De diagnose gebruikt uitsluitend de oorspronkelijke P-baseline uit §11:
zestien vragen, ieder drie keer. Cap 3 reproduceert de 48 opgeslagen evidence-packs
exact. Cap 4 behoudt alle oorspronkelijke bronnen en items in dezelfde relatieve
volgorde; uitsluitend de vierde bron en haar items komen erbij.

| Controle | Drie bronnen | Vier bronnen |
|---|---:|---:|
| Gedekte verwachte passages over drie pogingen | 11/30 | 14/30 |
| Unieke verwachte passages minstens eenmaal gedekt | 4/10 | 5/10 |
| Totale tekstomvang van evidence-items in tekens | 416.360 | 470.856 |
| Mediane tekstomvang van evidence-items per antwoord | 7.365 | 8.081 |

Alle drie extra dekkingobservaties zijn één passage bij één vraag, herhaald in
drie pogingen. De definitie gebruikt dezelfde markdownstrip, witruimtenormalisatie
en canonieke bronvergelijking als §11. Een voorlopige telling van 8 naar 11 gebruikte
een andere normalisatie en is daarom vervangen. Bij 35/48 records komt een vierde
bron erbij, samen 37 extra items; hun tekstomvang groeit met 13,1%.
Dit telt geen metadata, overige prompttekst of modeltokens.

Een aparte agent inspecteerde alle zestien eerste pogingen zonder de antwoorden
of juryuitkomsten te lezen. Twee vierde bronnen bieden mogelijk nuttige extra
informatie, waarvan één afhangt van een niet vastgesteld toesteltype; vier zijn
redundant, zes niet relevant en vier vragen hebben geen vierde bron. Dit zijn
agentlabels, geen menselijke beoordeling of gemeten antwoordwinst.

**Vooraf vastgelegd:** één vergelijking door de volledige antwoordketen met
alle zestien vragen, zonder selectie op de gevonden winst. Twee identieke kopieën
van de bevroren productie-index houden Qdrant-inhoud en ouderteksten gelijk.
De gedeelde live graph-service is niet bevroren en levert in de eerste controles
ook kandidaten; de proef bevriest daarmee niet alle kennisopslag in de keten.
Drie antwoordpogingen per variant en twee blinde, omgekeerd gepresenteerde
beoordelingsrondes blijven vereist. Beide voorkeurssaldi moeten positief zijn,
het gezamenlijke verschil minstens tien, zonder verlies van eerder ondersteunde
antwoorden of toename van onbewezen details.

De volledige proef bevat 96 antwoorden: zestien vragen, drie pogingen, twee
varianten. Twee kopieën van dezelfde snapshot hebben exact dezelfde punten,
payloads en vectoren; ouderteksten en de elf invoer-/harnasbestanden zijn op hash
gecontroleerd. Een versiecontrole stopte de eerste start vóór enig antwoord.
De inmiddels vernieuwde portal-image is daarna voor beide armen vastgelegd;
alleen de gapgroepering verschilde, niet de antwoordroute. Hypothese, vragen en
beslisgrenzen veranderden niet.

Alle antwoorden bevatten precies één zoekaanvraag met de juiste bronlimiet.
Vraag, historie, kennisbank en vaste zoekparameters zijn gelijk in 48/48 paren.
Gegenereerde zoekvarianten zijn gelijk in 42/48; bij deze identieke aanvragen
is de volgorde van teruggegeven chunk-ID's gelijk in 38/42. De live graaf levert
telkens tien kandidaten. De proef omvat dus ook variatie in zoeken en genereren.

De vierde bron komt in 35/48 antwoorden erbij. In de 38 paren met identieke
aanvraag, opgeloste vraag en chunkvolgorde groeit de evidence-tekst 27 keer en
blijft zij elf keer gelijk. De gevraagde modelalias is steeds dezelfde; 87/96
responses noemen uitsluitend die alias, zodat het achterliggende model daar
niet onafhankelijk is vast te stellen. Negen responses noemen twee concrete
modellen. Model- en zoekvariatie beperken de causale uitleg van een verschil.

Alle 96 antwoorden en 96 blinde beoordelingen zijn compleet. Iedere combinatie
van vraag en antwoordpoging is in twee omgekeerde presentatievolgordes beoordeeld.
De jury zag de werkelijk gebruikte context per antwoord en daarnaast een apart
gelabelde unie van ruwe passages. Extra passages in die unie tellen niet als
eigen-contextsteun. Er waren geen transportherhalingen of uitgesloten resultaten.

| Uitkomst | Ronde 1 | Ronde 2 | Samen |
|---|---:|---:|---:|
| Drie bronnen beter | 24 | 21 | 45 |
| Vier bronnen beter | 23 | 26 | 49 |
| Gelijk | 1 | 1 | 2 |
| Beoordeling met onbewezen detail, drie / vier | 5 / 7 | 5 / 6 | 10 / 13 |

Het voorkeurssaldo voor vier bronnen is −1 en +5, samen +4. Daarmee falen beide
voorkeursgrenzen; ook de grens voor onbewezen details faalt. Die laatste telling
betreft beoordelingen met minstens één claim die niet door de unie wordt gedragen,
geen onafhankelijke menselijke foutlabels. Bij 35/48 antwoordparen is het oordeel
in beide volgordes gelijk; dertien krijgen tegengestelde winnaars. Per vraag zijn
er zes positieve, zeven negatieve en drie gelijke gecombineerde saldi.

De mediane doorlooptijd is 6,97 tegenover 7,18 seconden; p95 is 9,54 tegenover
10,17 seconden. Het vastgelegde tokengebruik van de uiteindelijke antwoordaanroep
is 274.631 tegenover 294.924. Dit sluit zoeken, reparaties en jurycalls uit en is
daarom geen volledige kostenmeting.

Een afzonderlijke agent inspecteerde alle zes verliesflags en negen bredere
steunflags, samen dertien verschillende antwoordparen. Twee verliesflags houden
stand; één antwoord verliest nuttige instructies maar biedt wel de expliciet
gevraagde menselijke hulp. Eén andere escalatie is passend en twee flags zijn
foutpositief of missen een bewezen goede baseline. Zes van de negen bredere flags
betreffen steunverslechtering; drie blijken onjuist of inconsistent. Dit zijn
agentlabels. De eerste telling van drie antwoordverliezen is aangescherpt door
gevraagde menselijke hulp volgens het vooraf vastgelegde jurycontract te tellen.

Een bevestigd verlies treedt op bij exact dezelfde aanvraag, zoekresultaten en
antwoordcontext, zonder vierde bron. Een bredere flag blijkt juist een nuttig
antwoord met de vierde bron te markeren. De proef rechtvaardigt dus geen causale
claim dat de extra bron ieder verschil veroorzaakt, en evenmin een nieuwe
algemene promptwijziging op basis van de flags alleen.

**Besluit: niet uitrollen.** Meer bekende-spandekking levert hier geen herhaalbare
antwoordwinst op. De bestaande limiet van drie bronnen blijft actief. Geen
productcode gewijzigd, geen herindexering en geen nieuwe prompt gebouwd.
Alle vijf eigen proefcontainers zijn na opslag van de resultaten verwijderd.

## 15. Brononderbouwde referenties vóór een volgende reparatie (20 september)

[Ragas](https://docs.ragas.io/en/stable/concepts/metrics/available_metrics/context_recall/)
onderscheidt dekking van referentieclaims van het terugvinden van documenten.
Een [prompt-gecontroleerde studie](https://arxiv.org/abs/2603.28005) laat bovendien
zien dat het opsplitsen van claims niet vanzelf een betere beoordelaar oplevert;
referentiekwaliteit beïnvloedt beide onderzochte beoordelingsvormen. De resultaten
van die benchmarks zijn geen nauwkeurigheidsschatting voor onze keten.

De bestaande intake-eval, suite-loader en markdownnormalisatie worden hergebruikt.
Alle acht eerder gekozen gold-cases blijven in deze diagnose, inclusief historie.
Zes krijgen het volledige, eerder vastgelegde ruwe bronartikel; twee krijgen de
opgeslagen brontekst. Bronidentiteit en inhoud zijn vooraf op hash vastgelegd.
Deze geselecteerde, eerder onderzochte cases zijn geen onafhankelijke testset.

Een nieuwe agent stelt referentieantwoorden op uit alleen vraag, historie en
bron. Hij krijgt geen proefantwoorden, zoekresultaten of juryuitslagen.
Iedere feitelijke claim vereist een letterlijk broncitaat. Onbeantwoorde delen,
ontbrekende gespreksdatum en passende menselijke hulp worden apart vastgelegd.
Een tweede agent controleert de referenties vóór de dekkingsmeting.

Dit levert agentlabels, geen menselijke waarheid. De bestaande supportreview
reserveert `_reference` voor een menselijk oordeel; deze diagnose schrijft daar
niets in. De eerdere afgewezen uitrolbesluiten blijven staan. Een nieuwe reparatie
vereist een concrete oorzaak en vervolgens opnieuw de volledige antwoordproef.

De onafhankelijke controle leidde tot correcties in zes referenties. Daarbij
ontbraken relevante voorwaarden, werd een broninstructie te ruim uitgelegd en
bleek een eerder assistentantwoord in de historie zelf niet brononderbouwd.
Dat antwoord mag de betekenis van een vervolgvraag niet ongemerkt vastzetten.
Ook een ontbrekende eenheid mag niet uit een eerdere assistentuitspraak worden
overgenomen. Na één correctiecontrole zijn alle acht referenties geaccepteerd:
één volledig, vijf gedeeltelijk en twee dubbelzinnig. Dit is geen telling van
goede of slechte productantwoorden, maar van wat de aangeleverde bron onderbouwt.

Vóór de meting zijn de definitieve referenties, controle en meetadapter op hash
vastgezet. De 22 letterlijke citaten zijn na bestaande markdownnormalisatie
43–294 tekens lang, mediaan 141. Sommige bevatten meerdere feiten; de maat is
daarom dekking van tekstpassages, geen semantische beoordeling per los feit.

| Stap in de oorspronkelijke momentopname | Gedekte passages |
|---|---:|
| Gecontroleerde brontekst | 22/22 |
| In minstens één opgeslagen child-chunk | 21/22 |
| Child met die passage teruggevonden | 12/22 |
| Passage aanwezig in teruggegeven tekst, inclusief parents | 14/22 |

Deze momentopname komt uit een directe `/retrieve`-aanroep met `top_k=8` en
gesprekshistorie, zonder door de chatlaag gemaakte queryvarianten. De laatste rij
betreft de zoek-API, niet de uiteindelijk geselecteerde antwoordcontext. Eén
passage ontbreekt al in de opgeslagen brontekst; zeven andere staan in een child
maar niet in de teruggegeven tekst. Die zeven zijn geen zeven bewezen bugs:
dubbelzinnigheid en gevraagde menselijke hulp tellen mee in de referenties.

Voor de zes crawlcases zijn twintig citaten ook tegen dezelfde vastgelegde HTML
vergeleken. De oorspronkelijke filteruitvoer bevat er negentien; de eerder
afgewezen optie die gemarkeerde inline-elementen behoudt bevat alle twintig.
De ene herstelde passage ontbreekt ook in de oude opgeslagen chunks. Dit is
werkelijk tekstverlies, geen verschil dat markdownnormalisatie wegpoetst.

Daarna is een tweede diagnose vooraf vastgelegd op alleen de historische
baseline met drie bronnen uit §14. De zes overlappende widgetcases hebben elk
drie antwoordpogingen: twintig referentiepassages leveren zestig waarnemingen.
Daarvan staan er 39 in de zoekresultaten en 37 in de werkelijk aangeleverde
antwoordcontext. Het verschil betreft één passage in twee pogingen bij dezelfde
vraag. De twee interne chatcases ontbreken in deze proef en worden niet ingevuld.
De herhalingen zijn geen zestig onafhankelijke vragen. Deze terugblik beoordeelt
geen antwoordtekst en vergelijkt geen nieuwe productvariant.

**Besluit:** de meetlat is aangescherpt en de bestaande evaluatie hergebruikt.
De cijfers isoleren één eerder bekende filterfout en bevestigen een eerder
waargenomen verlies bij bronselectie. Ze rechtvaardigen geen nieuwe algemene
instelling of herhaling van dezelfde afgewezen proef. Bronbehoud en vier bronnen
blijven afgewezen voor uitrol; eerst is nieuw bewijs op een concrete antwoordroute
nodig. Geen nieuwe productcode, antwoordgeneraties of productiewijzigingen in deze stap.

## 16. Een interne vervolgvraag per stap onderzocht (20 september)

[QReCC](https://aclanthology.org/2021.naacl-main.44/) scheidt herschrijven,
zoeken en antwoordvorming in zijn evaluatie. Die scheiding is hier toegepast
op één interne vervolgvraag waarvan de bekende bronpassage ontbrak in §15.
De oude directe zoekproef verloor een prijsvoorwaarde uit de gesprekshistorie.
Dat bewijst geen fout in het huidige interne chatpad: dat gebruikt eerst een
eigen herschrijver en vraagt twintig zoekresultaten op, in plaats van acht.

De bestaande functies voor historie, toegangsbeleid, taxonomie, herschrijven en
zoeken zijn daarom aangeroepen in een eigen tijdelijke container. De werkelijke
gebruikersidentiteit en actieve kennisfunctie zijn gecontroleerd. Alleen de
kennisbankkeuze uit de eerdere proef is expliciet toegepast; de huidige opgeslagen
voorkeur wijst elders. Dit is een gecontroleerde proef met huidige functies, geen
letterlijke huidige gebruikersaanvraag. Er is geen antwoord gegenereerd.

Ook deze herschrijver liet de prijsvoorwaarde weg. Een tweede zoekaanroep voegde
alleen die voorwaarde toe aan de zoekvraag; alle andere aanvraagvelden bleven
gelijk. Image en alle veertig gemounte onderdelen waren in beide proeven gelijk.

| Controle | Huidige herschrijving | Met expliciete prijsvoorwaarde |
|---|---:|---:|
| Ruwe zoekresultaten / geselecteerde evidence-items | 20 / 16 | 20 / 16 |
| Eerste positie van een passage uit het verwachte document | 4 | 1 |
| Bekende prijsregel in ruwe resultaten | 0 | 0 |
| Bekende prijsregel in geselecteerde evidence | 0 | 0 |

De evidence is hier gemeten vóór de laatste score- en veiligheidsfilters van de
chatlaag. De live index was niet bevroren. Dit is één gerichte diagnose, geen
herhaalde antwoordproef of bewijs dat de gewijzigde vraag betere antwoorden geeft.
Een promptwijziging alleen is daarmee niet onderbouwd. Alle eigen proefcontainers
en tijdelijke kopieën van de draaiende configuratie zijn verwijderd.

De specifieke child en zijn parent zijn live teruggelezen: beide bevatten de
prijsregel en zijn gelijk aan hun bevroren versie. De traces tonen zeventig
zoekkandidaten; de child ontbreekt in beide lijsten na reranking. Het verlies
ligt dus bij kandidaatselectie of reranking, vóór parentvervanging. De bestaande
logs bevatten niet alle kandidaat-ID's; die twee oorzaken zijn nog niet gescheiden.

[TableRAG](https://proceedings.neurips.cc/paper_files/paper/2024/file/88dd7aa6979e352fda7c4952ca8eac59-Paper-Conference.pdf)
onderzoekt het gericht ontsluiten van schema's en cellen bij tabelvragen; lange,
gemengde rijen kunnen betekenis verliezen in één embedding. Dit motiveert een
intakeproef, geen overdracht van hun winstcijfers. De bestaande JSON-feedadapter
kan al groeperen op categorie, entiteit en merk. Bij deze bron is dat niet
ingesteld: hij gebruikt batches van maximaal tweehonderd records in feedvolgorde.
**Vervolg:** dezelfde records met die bestaande groepering op een eigen kopie
meten. Geen nieuwe groeperingsfunctie of productiewijziging vóór de meetpoort.
