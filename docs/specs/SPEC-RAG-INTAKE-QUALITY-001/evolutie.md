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
