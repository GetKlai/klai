# De chat van Klai: evolutie, onderzoek, ontwerp en plan

**Stand: 29 september 2026.** Dit is het ene document over de kwaliteit van de chat. Het vervangt de versie van 24 september en vat de logboeken samen die eronder lagen. Hoe de code vandaag loopt staat in [chat-system.md](chat-system.md); dit document zegt waarom, wat we willen, en in welke volgorde.

Elke bewering draagt een label:

- **[gemeten]** door ons, op onze eigen keten, met de sectie van het logboek erbij.
- **[onderzoek]** extern, bron geopend op 29 september 2026; "(samenvatting)" waar alleen de samenvatting is gelezen.
- **[praktijk]** wat leveranciers of praktijkmensen doen, zonder meting.
- **[aanname]** nog niet gemeten, door ons te toetsen.

Afkortingen: AJ = `docs/specs/SPEC-RAG-ANSWER-JUDGES-001/evolutie.md`, IQ = `docs/specs/SPEC-RAG-INTAKE-QUALITY-001/evolutie.md`. Beide zijn archief: ze worden niet meer aangevuld, nieuwe uitkomsten komen in §8 van dit document.

---

## 1. Waar het om gaat

Er zijn twee chats op dezelfde kennis. De interne chat laat medewerkers de kennisbank bevragen. De publieke chat is een supportchat op de helppagina van een klant. Het antwoordmodel is klein (`mistral-small-2603`) en gaat vaker de fout in dan een groot model. De opgave is een betrouwbaar systeem om dat model heen: code doet wat code betrouwbaar kan, het model doet waar het goed in is, namelijk begrijpen wat iemand bedoelt en een antwoord schrijven dat een mens kan lezen.

## 2. De evolutie

| Periode | Wat er veranderde | Hoe het gemeten was |
|---|---|---|
| Maart tot juli | Zoekkwaliteit, en een zekerheidsband die op de interne chat weigeren en doorvragen stuurt | De drempels 0,60 en 0,30 komen uit twee scores van één incident; de geplande nametingen zijn niet gedraaid |
| Augustus | Geplakte klantmails distilleren, bronselectie, zwaarder model bij risico | Telkens één incident; het effect van het zwaardere model is nooit gemeten |
| 15 tot 17 sep | De widget weigerde 13,5% van de beurten; een vraagbeoordelaar en een antwoordbeoordelaar kwamen erbij | De eerste versie maakte van 7 op 18 goede antwoorden een weigering en is dezelfde dag bijgesteld (AJ 2.4) |
| 18 sep | Controle per zin met reparatie; twee herformuleringen van de eerste vraag | Herformuleringen: 63 om 43 over twee rondes, de sterkste meting die we hebben (AJ 2.33). Reparatie: 25 antwoorden (AJ 2.8) |
| 19 tot 22 sep | Zes proeven aan de intake-kant | Alle zes afgevallen: meer gevonden, geen beter antwoord (IQ) |
| 22 tot 24 sep | Een vraagstap in drie versies; de regel voor zwakke bronnen | 17 gesprekken met een gesimuleerde bezoeker. Later bleek dat de vraagstap in 0 van 488 herspeelde vragen iets deed (AJ 2.53, 2.54) |
| 24 tot 25 sep | Eén pijplijn voor widget, partner en interne chat; maskering van persoonsgegevens op widgetaanroepen; een vaste vraagstap | De widget is na het samenvoegen niet opnieuw gemeten; de maskering is niet gemeten |
| 28 tot 29 sep | Review door de eigenaar van 109 echte antwoorden uit 58 gesprekken | Aandeel goede antwoorden 54% naar 37% |
| 30 sep | De sporen van alle beoordeelde gesprekken gelezen; de reparatie op de widget eruit; een keuzestap vóór het schrijven die bepaalt welke passages de vraag beantwoorden, en een vaste "niet gevonden" in code als geen passage dat doet | Eerst per onderdeel op gelezen gevallen met herhalingen, dan gekoppeld op kleine sets, dan twee rondes naast twee rondes van het oude pad op de hele meetlat (§8) |
| 5 tot 12 okt | Eerste week echt verkeer met de keuzestap gelezen: 58 gesprekken, 118 beurten | Ongeveer twee derde goed of eerlijk, tegenover ruim een derde in september (één lezer, niet de eigenaar). Wat overblijft wijst naar het zoeken op vervolgbeurten, het linkfilter en de herkomst van harde feiten (§8) |
| 12 okt | Het ontwerp herzien vanuit de architectuur: één expliciete gespreksstaat als ruggengraat, drie modelaanroepen op het pad, herkomstcontrole in code, meten erachter (§5) | Nog te bouwen en te bewijzen, in de volgorde van §7 |
| 13 okt | De stroom werd de drager: `widget_turn()` en `internal_turn()` op dezelfde stapfuncties, één schrijfstap en één uitzender; de vraagbeoordelaar, de herschrijving, de vraagstap op titels, de zwakke-bronnenregel, de reparatie en de oude citatiemodus eruit; elke LibreChat-omgeving op het portalpad; zoeken levert één passage per sectie en acht bronnen voor de keuzestap | Per snede een eigen uitvoerder, de volledige suite, één tot twee meetrondes op de hele meetlat en de interne meetlat, een verificatie-subagent en Sol (§8, O1 tot O6 en J). Widget: kwaliteit gelijk binnen de ruis, een geschreven beurt ruim een seconde sneller; intern Open: onbewezen zinnen per antwoord 3,8 naar 2,9; na J: verwachte antwoorden 61 van 81 tegen 53 |

**Wat de review liet zien** [gemeten, 109 antwoorden, door één mens beoordeeld]. De grootste foutsoorten over de hele periode: antwoord beschadigd door de reparatie (18), een duidelijke vraag verkeerd gelezen (12), een kale of nutteloze reactie (14), geweigerd terwijl het antwoord er was (6). Bij een sterke beste bron (score 0,4 of hoger) zakte het aandeel goede antwoorden van 18 op 33 naar 8 op 26; bij een zwakke beste bron van 14 op 26 naar 8 op 17. Het verlies zit dus vooral waar de zoekscore hoog is. De beschadiging door reparatie is niet nieuw: ze kwam vóór 24 september even vaak voor. Bij deze aantallen is 54% naar 37% een richting (p ongeveer 0,09); de foutsoorten zelf zijn per antwoord gelezen.

**Het patroon.**

1. Er kwam steeds een stap bij en er ging bijna niets weg. Een supportbeurt ging van ongeveer drie aanroepen van een taalmodel naar maximaal acht.
2. Elke stap was een reactie op het laatst gevonden probleem, niet op een ontwerp vooraf.
3. De metingen waren klein, vaak op nagespeelde beurten, en met een taalmodel als rechter.
4. De poort uit de spec (eerste vragen herspelen vóór livegang) is bij de laatste wijzigingen niet gedraaid.
5. De documenten liepen achter op de code.

Dit patroon is bekend. Meer aanroepen van een taalmodel helpen bij makkelijke vragen en schaden bij moeilijke, waardoor het totaal eerst stijgt en dan daalt [onderzoek (samenvatting): Chen e.a. 2024, https://arxiv.org/abs/2403.02419]. Een toename van AI-gebruik in ontwikkelteams gaat samen met 7,2% lagere stabiliteit van opleveringen, en de remedie die het rapport noemt is kleine wijzigingen en stevige tests [onderzoek: DORA 2024].

### Hoe de reparatie ontstond, en waarom ze niet kon werken

De reparatie is nooit ontworpen; ze is het eind van een reeks stappen die elk het gevolg van de vorige probeerden op te vangen.

1. Het model kreeg acht passages en schreef een antwoord. Of die passages de vraag beantwoordden, stelde niemand vast.
2. Antwoorden bevatten verzonnen stappen en menunamen. Daarop kwam de controle per zin (18 september), die elke zin naast de artikelen legt.
3. De controle vond de verzonnen zinnen. De vraag was toen wat ermee te doen: weigeren kostte goede antwoorden (7 van 18 in de eerste versie), dus kwam er een derde aanroep die de afgekeurde zinnen weghaalt en de rest laat staan.
4. Die reparatie is gemeten op de vraag of er minder onbewezen zinnen overbleven, op 25 antwoorden. Of het antwoord daarna nog te volgen was, is niet gemeten.
5. In de review bleek ze de grootste oorzaak van een slecht antwoord: stappen weg, koppen zonder inhoud.

Waarom ze niet kon werken: de controle keurt twee soorten zinnen af die niets met elkaar te maken hebben. De ene soort is onschuldig (de slotzin die het profiel zelf voorschreef, "Ga naar je belplan", een herhaling van wat de bezoeker zei). De andere soort is een verzonnen stap in een antwoord dat in zijn geheel verzonnen is, omdat het antwoord niet in de passages stond. Knippen beschadigt in het eerste geval een goed antwoord en laat in het tweede geval een romp over van een antwoord dat er nooit had moeten zijn. Geen regel achteraf kan dat goedmaken, want de fout is gemaakt vóór het schrijven [gemeten, §8].

### Wat de sporen lieten zien (30 september)

Tot 30 september keken we per onderdeel of het deed wat het moest doen, en telden we uitkomsten. Op die dag zijn alle gevallen uit de review opnieuw nagespeeld met het hele pad vastgelegd (vraag, vraagbeoordelaar, herformuleringen, gevonden passages, de letterlijke opdracht aan het model, het concept, beide controles, de reparatie, de eindtekst), en zijn de fout beoordeelde gevallen één voor één gelezen. Per geval is de eerste plek op het pad vastgelegd waar het misging [gemeten, één lezer, één naspeelronde].

| Aandeel van de fout beoordeelde gevallen | Eerste plek waar het misgaat |
|---|---|
| ruim een derde | Vervolgbeurt: de zoekvraag is alleen de laatste zin en mist het onderwerp van het gesprek |
| bijna een vijfde | Eerste beurt: de vraag heeft meerdere delen of varianten en geen stap merkt dat |
| een zesde | Zoeken: een artikel dat op de vraag lijkt maar iets anders behandelt, wint met een hoge score |
| een zevende | Kennisbank: het antwoord staat er niet in |
| enkele gevallen | Onderwerpstap: een technische vraag wordt afgewezen als commercieel |
| enkele gevallen | Brontekst of zoekscore: een gat in de passage, of het juiste artikel scoort te laag |
| één geval | Schrijven: goede passages, het model vult toch zelf aan |

**Correctie op de eerste rij, later op 30 september.** Het spoor legde de laatste zin van de bezoeker vast, niet de zoekvraag die het zoeken er zelf van maakt, en dat gat is ingevuld met een aanname. Nagemeten op alle vervolgbeurten uit de review: in een derde van de gevallen uit die rij miste de zoekvraag het onderwerp echt (een los woord als antwoord op onze eigen vraag, of een verkeerd ingevulde verwijzing); in twee derde stond het onderwerp er wel in en beantwoordden de gevonden passages de vraag toch niet. Die gevallen horen bij de rijen over het lijkende artikel en de kennisbank. De rij over vervolgbeurten is dus een achtste van de fout beoordeelde gevallen, geen derde [gemeten, één lezer].

In bijna alle gevallen ligt de eerste oorzaak vóór het schrijven. Wat er daarna gebeurt, over alle nagespeelde beurten:

- In zes op de tien beurten heeft minstens één passage een gat waar een linktekst hoort ("Ga naar ." zonder menunaam). Het model vult dat zelf in. Dit verlies bij het inlezen is op 19 september gevonden (IQ §3) en toen niet hersteld, omdat een beoordelend model geen voorkeur zag.
- In een derde van de beurten staat een passage uit de documentatie van een ander product in de top; in een achtste is dat de hoogst scorende.
- De opdracht "zeg dat het er niet staat" bij zwakke bronnen wordt in vier op de tien gevallen genegeerd: het model schrijft toch een antwoord.
- De lichte antwoordbeoordelaar zegt in zes op de tien gevallen "alles staat in de artikelen" waar de controle per zin twee of meer beweringen afkeurt.
- De controle per zin vangt verzonnen zinnen, maar ziet een antwoord dat netjes uit het verkeerde artikel komt niet, en keurt ook zinnen af die het profiel zelf voorschrijft (de slotzin "Je hebt nu ...").
- Een kwart van de lijstregels in antwoorden staat in geen enkele gevonden passage. Het profiel vraagt om een nette, complete procedure met een slotzin, terwijl het model losse fragmenten uit meerdere artikelen krijgt.
- Het linkfilter haalt ook het adres weg als dat adres het antwoord is.

De les: het systeem laat het model schrijven zodra er iets gevonden is, ook als wat gevonden is niet bij de vraag past, en alles daarna probeert dat te herstellen. De reparatie is het laatste station van een fout die eerder ontstond. De volgorde van aanpakken is daarom het pad zelf, van voor naar achter (§7).

## 3. Wat het onderzoek zegt

### 3.1 Beslissen: antwoorden, vragen, eerlijk stoppen of een mens

- **De zoekscore is geen bewijs dat het antwoord er staat.** Een passage die relevant lijkt maar het antwoord niet bevat, is erger dan geen passage: een klein model (27B) gaf met voldoende context 25% verzonnen antwoorden en met onvoldoende context 35% [onderzoek: Sufficient Context, ICLR 2025, tabel 4, https://arxiv.org/abs/2411.06037].
- **Een aparte toets "is dit genoeg om de vraag te beantwoorden" kan dat zien.** Een groot model haalde 93% overeenstemming met mensen, een afgesteld model van 24B 88%, op 115 voorbeelden [onderzoek: zelfde bron, tabel 1]. Voor een niet-afgesteld model van onze klasse is dit [aanname].
- **Weigeren op instructie werkt niet betrouwbaar.** Modellen van 7B weigerden in 6 tot 31% van de gevallen waar het antwoord ontbrak [onderzoek: RGB, AAAI 2024, https://arxiv.org/abs/2309.01431]; in een Nederlandstalige proef haalden kleine modellen 2 tot 20% van de terechte weigeringen [onderzoek: bLLeQA, 2026, https://aclanthology.org/2026.knowfm-1.4/]. De beslissing om niet te antwoorden hoort dus buiten het antwoordmodel te vallen.
- **Modellen zien vaagheid slecht aan de vraag zelf**, ook met voorbeelden of stap-voor-stap redeneren [onderzoek: CLAMBER, ACL 2024, https://aclanthology.org/2024.acl-long.578/]. Of doorvragen nodig is, valt beter af te leiden uit de samenhang van de gevonden artikelen [onderzoek: Arabzadeh e.a. 2022, https://arxiv.org/abs/2208.04882].
- **Een vraag is pas iets waard in de beurt erna**; bij twijfel antwoorden [onderzoek: Zhang, Knox en Choi, ICLR 2025, https://arxiv.org/abs/2410.13788].
- **Informatie die over beurten verspreid raakt kost gemiddeld 39% prestatie** [onderzoek: Laban e.a. 2025, https://arxiv.org/abs/2505.06120]. Na het antwoord op een vraag wordt alles samengevoegd tot één zoekvraag en opnieuw gezocht.
- **Frustratie: eerst helpen, de mens erbij aanbieden.** "Escalating too early reduces Fin's effectiveness" [praktijk: Intercom, https://www.intercom.com/help/en/articles/12396892]. Een verzoek om een mens gaat direct door. Een gecontroleerde meting hiervan is niet gevonden.

### 3.2 Schrijven en nakijken

- **Ook met de juiste context verzint een klein model.** Een model van onze klasse: ongeveer 5% bij samenvatten en ongeveer 10% bij vraag en antwoord [onderzoek: Vectara-ranglijst september 2026; FaithJudge, EMNLP Industry 2025, https://arxiv.org/abs/2505.04847]. De nieuwere modellen van dezelfde maker scoren op die ranglijst duidelijk slechter dan de oudere; ons eigen model staat er niet op. Dit moeten we zelf meten.
- **Weinig en juiste passages.** De kwaliteit stijgt en daalt dan weer met meer passages; lijkende maar foute passages schaden het meest [onderzoek: Jin e.a., ICLR 2025, https://arxiv.org/abs/2410.05983].
- **Achteraf herschrijven is de zwakste reparatie.** In een vergelijking op 916 antwoorden hield herschrijven 80% van de tekst maar haalde het de minste fouten weg; schrappen hield 64% over. Van de 176 goede antwoorden in die groep werd 83,5% toch aangepast [onderzoek, nog niet door vakgenoten beoordeeld: https://arxiv.org/abs/2608.29307]. Modellen verbeteren zichzelf niet betrouwbaar zonder feedback van buiten [onderzoek: Huang e.a., ICLR 2024, https://arxiv.org/abs/2310.01798].
- **Een controle hoort alleen controleerbare uitspraken te lezen**, niet een excuus of een "dit vind ik niet terug" [onderzoek: Claimify, ACL 2025, https://arxiv.org/abs/2502.10855].
- **Beoordelende modellen zijn geen waarheid.** Een model als detector haalde op antwoordniveau een precisie van 47% [onderzoek: RAGTruth, ACL 2024, https://arxiv.org/abs/2401.00396]. Bij ons was één afkeuring in ongeveer de helft van de gevallen terecht [gemeten, AJ 2.52].
- **Controle in code** op getallen, namen en letterlijke citaten vangt verzonnen waarden; ze ziet geen geparafraseerde onjuistheid, weggevallen voorwaarde of verkeerde volgorde. Een meting van de dekking is niet gevonden [aanname].
- **Temperatuur nul maakt een antwoord herhaalbaar, niet juister** [onderzoek: Renze 2024, https://arxiv.org/abs/2402.05201].

### 3.3 Begrijpen en zoeken

- **Eén mechanisme voor elke beurt**: de laatste beurt wordt een zelfstandige zoekvraag; bij de eerste beurt verandert er dan niets [onderzoek (samenvatting): MTRAG 2025, https://arxiv.org/abs/2501.03468].
- **Zoekmodellen zien richting en ontkenning slecht.** Eenvoudige zoekmodellen scoren onder toeval op een vraag en haar omgekeerde; het beste herrangschikmodel haalt 51% [onderzoek: NevIR, EACL 2024, https://arxiv.org/abs/2305.07614]. Importeren tegen exporteren en inkomend tegen uitgaand horen daarom als kenmerk bij het artikel te staan.
- **Een gesloten keuze in een afgedwongen formaat** lost vormfouten op bij kleine modellen en past bij classificatie [onderzoek: Geng e.a. 2025, https://arxiv.org/abs/2501.10868; Tam e.a. 2024, https://arxiv.org/abs/2408.02442]. Wij vonden hetzelfde: een keuzelijst was 17 op 18 goed, een ja/nee-veld 0 op 18 [gemeten, AJ 2.2].

### 3.4 Meten

- **Foutenanalyse op echte gesprekken is het belangrijkste werk**: 30 om te beginnen, 100 voor een volledig beeld, daarna 10 tot 20 per week [praktijk: Husain en Shankar, https://hamel.dev/blog/posts/evals-faq/].
- **Eén deskundige die oordeelt** is beter dan een groep [praktijk: zelfde bron].
- **Gesimuleerde bezoekers zijn te meegaand** en maken uitkomsten te gunstig [onderzoek (samenvatting): https://arxiv.org/abs/2609.00608]. Bij ons telde de scoorder eerst een doorverwijzing als succes [gemeten, AJ 2.24].
- **Beoordeel de eerste beurt niet als maat voor een gesprek** [gemeten, AJ 2.44; onderzoek: Zhang, Knox en Choi].

### 3.5 Niet gevonden

Een meting van wat controle in code vangt; een vergelijking tussen opnieuw schrijven en terugvallen op de letterlijke passage; een meting van "antwoord plus aanbod" tegenover "direct doorverwijzen"; een Nederlandse meting op korte supportteksten; praktijk voor Nederlandse en Belgische inhoud in één kennisbank.

### 3.6 Hoe open-source systemen het in code doen

Op 30 september is de broncode gelezen van twaalf systemen met een chat over eigen documenten (Onyx, RAGFlow, Dify, Kotaemon, R2R, Haystack, LlamaIndex, de LangGraph-voorbeelden voor Corrective en Self-RAG, Open WebUI, Chatwoot Captain, Khoj, AnythingLLM) [onderzoek: broncode].

- Geen enkel systeem herschrijft een antwoord zin voor zin na een controle. Wat ze wel doen: opnieuw zoeken, opnieuw schrijven, het antwoord als onvolledig labelen of het niet tonen.
- Het gangbare patroon is een oordeel per passage vóór het schrijven, en alleen wat daar doorheen komt gaat naar de schrijver (Onyx, LangGraph, Kotaemon, Haystack, LlamaIndex). De meeste toetsen daarbij op onderwerp, niet op "beantwoordt dit de vraag". Onyx benoemt als enige "lijkt op het onderwerp maar gaat over iets anders" als aparte klasse; RAGFlow vraagt of de hele set volstaat en welke passages nuttig zijn.
- Weigeren in code gebeurt alleen bij nul passages (RAGFlow, AnythingLLM, LlamaIndex). Met passages in handen laat iedereen "staat er niet" aan de opdracht van de schrijver over.
- Vrijwel elk systeem faalt open: geeft het oordeel niets of een fout, dan gaat de ongefilterde lijst door.
- Wat gevonden is wordt verbreed naar de sectie of het document eromheen (Onyx, Dify, RAGFlow, LlamaIndex), en dubbele tekst wordt samengevoegd.
- Een vervolgvraag wordt herschreven tot een zelfstandige vraag met de eerdere beurten erbij; alleen Onyx en R2R zoeken daarnaast ook met de oorspronkelijke zin.

## 4. Onderzoek naast onze praktijk

| Principe | Wat wij doen | Verschil |
|---|---|---|
| Niet schrijven als het antwoord er niet staat, beslist buiten het antwoordmodel | Beslissen op de zoekscore (alles onder 0,4), en het model daarna vragen "niet gevonden" te zeggen | De score meet gelijkenis; boven 0,4 wordt altijd geschreven |
| Weinig en juiste passages | Acht fragmenten, drie bronnen | Geen selectie op "beantwoordt dit de vraag" |
| Schrijven door het model, dicht op de bron | Vrij schrijven, daarna controle per zin | Geen verschil in wie schrijft; wel in wat er daarna gebeurt |
| Niet achteraf herschrijven | Tot 30 september herschreef een derde aanroep bij twee of meer afkeuringen. Sindsdien blijft het antwoord op de widget heel | Was de grootste foutsoort in de review; opgelost op de widget, intern nog niet |
| Alleen controleerbare uitspraken nakijken | De controle keurt ook eigen "niet gevonden"-zinnen af | Leidt tot weigeringen en rompen |
| Eén vraag, zelden, uit wat de artikelen onderscheidt | Een vraag als artikeltitels op elkaar lijken | Vroeg naar het apparaat bij een vraag over belrechten |
| Bij frustratie eerst helpen | Negatieve toon vervangt het antwoord door een afspraakaanbod en slaat de vraagstap en de regel voor zwakke bronnen over | Een oplosbaar probleem krijgt geen antwoord |
| Richting en apparaat als kenmerk | Geen kenmerken per artikel | Verkeerd gelezen vragen bij hoge score |
| Meten op echte gesprekken met menselijk oordeel | Meestal een model als rechter op nagespeelde beurten | Een daling bleef een week onopgemerkt |
| Interne chat: dezelfde zorgvuldigheid | Open-modus wordt niet gecontroleerd; twee routes naast elkaar | De nieuwe route haalde de maat voor de klanttenant niet |

## 5. Het systeem zoals het nu is

**Stand 22 oktober 2026, nagelezen tegen de code op main.** Deze sectie beschrijft wat een beurt vandaag doet, zoals een lezer het systeem nu aantreft. Het ontwerp waaruit het voortkwam staat ongewijzigd in §5a, hoe het zo gegroeid is staat stap voor stap in §8, en de technische kaart met de stappen W0 tot en met W8, de logregels en de budgetten is [chat-system.md](chat-system.md).

Er is één pad. Elke beurt op de helpwidget, via een partnersleutel of in de interne chat (Strict, Open en general) komt binnen op hetzelfde endpoint en loopt dezelfde stappen in dezelfde volgorde: de zaak wordt bijgewerkt, code kiest de route waar geen artikel voor nodig is, het zoeken haalt passages op, de keuzestap wijst in die passages de letterlijke zinnen aan die de vraag beantwoorden, één schrijver schrijft uit die zinnen, één controle leest het antwoord daarna na, de voettekst zegt wat de lezer nog moet weten en het record bewaart wat elke stap deed. De widget en de partnersleutel lopen door `widget_turn()`. De interne chat loopt door `internal_turn()`, dat dezelfde stapfuncties gebruikt en alleen aan de uiteinden verschilt, omdat een medewerker geen afspraakknop heeft en vaak de vraag van een ander doorgeeft. De schrijver houdt het antwoord vast tot het gecontroleerd is, behalve in de interne modi Open en general bij een streamend verzoek, waar de medewerker de tekst ziet terwijl die geschreven wordt en het antwoord daarna op dezelfde manier wordt afgerond. General zoekt niets, dus daar vallen de zaak, het zoeken en de keuzestap weg. Buiten het pad blijven alleen twee doorgeefroutes zonder kennis: een partnersleutel met `general_chat` die geen kennisvelden meestuurt, en het verzoek waarmee LibreChat een titel voor een gesprek laat maken.

### De zaak

De zaak is de ene lezing van het gesprek, zodat niet elke modelaanroep voor zich hoeft af te leiden wat iemand bedoelt. Eén aanroep op het kleine model (`klai-fast`, 3 seconden) leest de zaak van de vorige beurt en het gesprek tot nu toe, en schrijft haar opnieuw: het probleem, het product, de variant (richting of situatie), wat al geprobeerd is, wat het laatste bericht vraagt (kennis, een mens, contact, een afspraak, een praatje of iets anders), of het onderwerp buiten de widget valt, de toon, de taal van het hele gesprek en twee zoekvragen van hooguit twaalf woorden die product en probleem noemen. Op een vervolgbeurt loopt ze vóór het zoeken, omdat haar zoekvragen meegaan, en op een eerste beurt ernaast.

Wat ze nooit mag, is een route kiezen op haar eigen oordeel alleen. Voor een contactwens en voor een onderwerp buiten de widget citeert ze eerst de woorden van de bezoeker, en code zoekt dat citaat terug in het laatste bericht; staat het er niet, dan wordt de contactwens een gewone kennisvraag en het onderwerp een onderwerp dat beantwoord wordt. Valt de aanroep uit, dan loopt de beurt door als duidelijke kennisvraag, want een storing mag geen weigering worden.

De widget bewaart de zaak bij het antwoord en leest haar de volgende beurt terug, maar alleen als het opgeslagen antwoord ook het laatste antwoord van dit gesprek is, omdat één sessie meer gesprekken kan dragen. De interne chat doet hetzelfde met een eigen tabel, `internal_chat_cases`, omdat haar record geen tekst mag bevatten: één rij per gesprek met de zaak, de bewaarde passages en de medewerker, die de nachtelijke opschoning na 30 dagen verwijdert en die verdwijnt als de medewerker wordt geoffboard of verwijderd. Een bewaarde passage komt alleen terug uit een kennisbank die de medewerker nu nog mag doorzoeken. Het gesprek is het gespreks-id dat LibreChat als header `X-Klai-Conversation-Id` meestuurt, en de rij hoort bij de org, de medewerker en dat id samen; zonder header, op een omgeving waarvan de config nog niet opnieuw is uitgerold, heeft de beurt geen geheugen, met één waarschuwing in het log op een vervolgbeurt. Een opnieuw gegenereerd antwoord leest de zaak die het eerste antwoord van die beurt bewaarde en overschrijft die. Een partnersleutel leest elke beurt zonder vorige zaak, omdat van een partnersleutel geen gesprek bewaard wordt. In het record van de widget staat de zaak als `case`; een uitgevallen zaak staat op elk oppervlak in het record als `judge_failed: ["case"]`, en een citaat dat niet terugkwam als logregel `case_route_unverified`.

### De route in code

Wat geen artikel nodig heeft, beslist code, zonder schrijver. Op de widget zijn dat vier vaste reacties, in deze volgorde. Een onderwerp dat de eigenaar van de widget heeft uitgesloten krijgt een doorverwijzing met de afspraakknop, waarin één korte aanroep (2,5 seconden) het onderwerp van de bezoeker in een zin van ons noemt, met de ingestelde tekst als die aanroep niets oplevert. Wie om een mens, contact of een afspraak vraagt, krijgt de vaste contactreactie met de knop, aangevuld met het e-mailadres en telefoonnummer als de beheerder die bij de widget invulde; was de vorige beurt al die reactie, dan zegt de tweede dat dit venster alleen een afspraak kan inplannen, omdat dezelfde zin vier keer achter elkaar een bezoeker niet verder hielp. Een contactverzoek gaat voor de doorverwijzing en valt op een vervolgbeurt al vóór het zoeken. Vond de keuzestap geen passage die antwoordt, dan krijgt de bezoeker het eerlijke "niet gevonden" met de knop. En hangt het antwoord af van een variant die het gesprek niet noemt, dan is de vraag van de keuzestap de hele reactie: één regel die op een vraagteken eindigt, hooguit één keer per gesprek, niet als de bezoeker om een mens vroeg of gefrustreerd is, en niet op een bericht met meerdere vragen, waar de schrijver de beantwoorde delen geeft en de vraag voor de rest stelt.

De interne chat heeft geen knop, dus geen contactroute en geen doorverwijzing. In Strict wordt "staat er niet in" de vaste Strict-weigering met de suggestie het in Open te proberen, en dat geldt ook voor een keuzestap die twee keer uitviel en voor een profiel zonder kennisbank; een onbereikbare kennisbank heeft in Strict een eigen vaste tekst. Open weigert nooit. De vraag van de keuzestap wordt intern nooit gesteld, omdat een medewerker vaak de vraag van een klant doorgeeft en niet weet welke variant geldt, dus bij "hangt af van" krijgt de schrijver de opdracht de stappen voor elke variant te geven, elk onder een eigen kopje.

De vaste teksten spreken de taal die de zaak voor het hele gesprek vaststelde, omdat een detectie per bericht een kort vervolg in een andere taal als taalwissel las; zonder zaak, of met een taalcode die de detectie niet kent, beslist de detectie op het hele gesprek. De Strict-weigering en de Open-hint bestaan in het Nederlands, Engels, Duits en Frans. De teksten van de widget (niet gevonden, de twee contactreacties, de afspraakzin en het label voor algemene kennis) en de tekst voor een onbereikbare kennisbank bestaan alleen in het Nederlands en Engels, zodat elke andere taal daar Engels krijgt. Het record zegt welke route het werd (`decision`: `off_topic`, `human_request`, `refusal` of `clarifying_question`) en of de knop eronder hing (`appointment`).

### Het zoeken

Het zoeken vraagt de zoekdienst om passages, met de letterlijke zin van de bezoeker als hoofdvraag: op de widget standaard acht, in de interne chat twintig. Het bewijspakket houdt een bron per passage tot hooguit acht, omdat de keuzestap elke passage leest en het bekende artikel bij drie bronnen buiten het pakket viel (stap J in §8). Een eerste vraag op de widget krijgt twee parafrasen als extra zoekgangen (`klai-medium`, 2,5 seconden), omdat die verbreden waar de zaak versmalt en zo de secties van beide varianten binnenhalen; een vervolgbeurt zoekt met de twee zoekvragen van de zaak erbij. De interne chat heeft geen parafrasen, omdat die alleen op de widget gemeten zijn, en zoekt bij een vervolgbeurt of een geplakte mail met de zoekvragen van de zaak. Een bericht met meerdere vragen wordt op elk oppervlak per deelvraag gezocht, tot zes deelvragen; wat daarboven valt wordt niet gezocht maar wel aan de schrijver genoemd, zodat die het niet uit andere regels beantwoordt.

Wat het zoeken niet doet, is beslissen of een passage antwoordt, want de score zegt alleen dat een passage op de zoekvraag lijkt. De zekerheidsband beslist niets over het antwoord en kiest alleen in intern Strict mee welk model schrijft. De gat-klasse (geen passages, of alle scores onder 0,4) bepaalt alleen of er een kennisgat wordt geregistreerd, of een praatje zonder artikelen beantwoord wordt en of de brede modus kan. Die brede modus is een keuze van de bezoeker: alleen met diens toestemming en alleen als het zoeken niets vond, antwoordt de widget uit algemene kennis, met het label "Algemene kennis, niet afkomstig uit onze helpartikelen." erboven.

De widget en de interne chat onthouden daarnaast de passages die de keuzestap koos, hooguit drie en elk tot 2500 tekens, eerst de nieuw gekozen en dan de eerder bewaarde. De volgende beurt zet ze vóór de zoekresultaten als het zoeken ze niet opnieuw vond, ook als het zoeken niets vond, zodat een vervolgvraag het artikel van de vorige beurt weer aangeboden krijgt; dat geldt alleen voor passages uit een kennisbank die nog binnen het bereik van de beurt valt en waarvan de tekst de contextveiligheid voor de nieuwe vraag doorstaat. Een beurt die niets kiest, zoals een contactreactie of "niet gevonden", geeft de bewaarde passages ongewijzigd door, en kiest de keuzestap een bewaarde passage, dan telt de beurt niet als kennisgat. In het record van de widget staan `search` (de zoekvraag, de zoekgangen en de gevonden passages met titel, url en score, de bewaarde gemarkeerd), `band` en `kept_passages`; het interne record houdt alleen `band` en `case_remembered`, de passages staan in `internal_chat_cases`. Het zoeklogboek wordt vóór de routes geschreven, zodat feedback op een vraag of een weigering deze beurt terugvindt.

### De keuzestap

Er wordt gekozen vóór het schrijven, omdat een klein model dat passages over het onderwerp krijgt ook schrijft als het antwoord er niet in staat. Eén aanroep op het middelgrote model (`klai-medium`) leest het gesprek (het laatste bericht tot 4000 tekens) en de genummerde passages (elk tot 2500 tekens) en schrijft in deze volgorde: de behoefte in één zin, de delen die de bezoeker nu vraagt (hooguit zes; een dankwoord of een vraag om een mens heeft er geen), per deel de vraag in de eigen woorden van de bezoeker (`ask`) en de letterlijke zinnen die antwoorden, of de stappen per variant verschillen (`varies`) en alleen dan de ene vraag van hooguit vijftien woorden. Per deel zijn dat hooguit drie zinnen en samen hooguit zes. De interne chat heeft voor dezelfde stap eigen woorden, omdat een medewerker een klantvraag doorgeeft, vraagt wat iets is of of het bestaat, of een tekst uit het gesprek wil laten herschrijven.

Code zoekt elk citaat terug: minstens 80 procent van de woorden moet in de aangewezen passage staan, en anders in de best passende sectie van hetzelfde artikel, nooit in een ander artikel, omdat een gelijkluidende zin daar over een ander product kan gaan. Naar de schrijver gaan alleen de passages met een teruggevonden zin, zonder dubbele tekst, met een opdracht die de behoefte noemt en de regels uit de passage zelf in plaats van de kopie van het model, zodat een woord dat het model bij het overschrijven veranderde de schrijver niet bereikt. Het model schrijft geen oordeelwoord meer, want dat was een tweede eigenaar van "beantwoord": code leidt alles uit de delen af. "Staat er niet in" is er als de bezoeker iets vroeg en geen enkel deel een teruggevonden zin heeft, "hangt af van" als `varies` waar is en er iets gevonden is, en een kennisvraag waarop de keuzestap geen enkele zin aanwijst telt ook als "staat er niet in". Een deel zonder teruggevonden zin is open, maar alleen als zijn `ask` in de eigen berichten van de bezoeker staat (alle beurten, omdat een kort vervolg een eerdere vraag aanscherpt). Een feit over de situatie van de bezoeker wordt zo nooit als gat genoemd en geeft nooit de knop; dat gebeurde eerder in 4 van 18 gevallen op de widgetmeetlat, en één keer zei een antwoord "Ja." en daarna dat hetzelfde deel er niet stond. De open delen worden één keer per beurt bepaald en bepalen de ene zin in de opdracht van de schrijver ("de help-artikelen dekken dit deel niet"), het record en de afspraakknop.

Het budget is 4 seconden plus 0,25 seconde per aangeboden passage, omdat een interne beurt er 13 tot 22 aanbiedt en op een vast budget te vaak uitviel; een uitgevallen aanroep krijgt één nieuwe poging met hetzelfde budget. Wat de keuzestap nooit mag, is bij uitval de schrijver alle passages geven waar het antwoord alleen uit de bron mag komen, dus op de widget en in Strict is een keuzestap zonder oordeel "staat er niet in". Alleen Open houdt dan alle passages, zonder aparte melding aan de schrijver, omdat de passages weglaten op de interne meetlat een deel van de verwachte kennisbankantwoorden kostte en de melding erbij (21 oktober) overbodig werd toen de keuzestap haar eigen interne opdracht kreeg (22 oktober). De stap draait niet op een beurt in de brede modus, niet op een beurt met geplakte correspondentie en niet op een interne beurt die op een eigen bijlage van de medewerker rust. Het record van de widget bewaart als `selection` het afgeleide oordeel, per deel de vraag en de citaten, de open delen en de vraag; het interne record alleen het oordeel en de nummers van de gekozen passages, en een keuzestap die twee keer uitviel staat er als `failed`.

### De schrijver

Er is één schrijver, `write_reply`, voor elk oppervlak en elke modus. Het antwoordmodel krijgt het profiel, de gekozen passages en de opdracht van de keuzestap. Op de widget en in Strict zegt die opdracht dat het antwoord de volgorde en de woorden van de bron houdt, geen stap samenvoegt of aanvult, geen oorzaak of voorbeeld van zichzelf geeft en in één zin zegt wat de bron niet dekt. In Open zegt de opdracht dat het antwoord op dezelfde regels bouwt en mag aanvullen uit algemene kennis, maar een e-mailadres, link, telefoonnummer, prijs of bedrag alleen noemt als het in de aangewezen regels staat; dat is een opdracht en geen garantie, en daarom meldt de voettekst wat er toch doorheen komt. Intern Strict schrijft op een bericht met meerdere vragen of op zwak bewijs met het middelgrote model.

Wat de schrijver nooit doet, is een antwoord herschrijven nadat het geschreven is: de reparatie achteraf is weg, en wat het model mag lezen wordt vóór het schrijven beslist. Een vastgehouden antwoord gaat na het schrijven door dezelfde afronding. De bronnen worden op de uiteindelijke tekst gekozen en de uitvoerveiligheid leest mee, en op de widget mag een link alleen blijven als hij in de passages staat en valt een zin weg met een telefoonnummer of e-mailadres dat niet in de gekozen passages staat (de herkomstpoort). Daarna beslist `decide_answer` over de afspraakknop, zonder tweede modelaanroep: een antwoord met een bron wordt altijd getoond, en een beurt met een open deel van de keuzestap krijgt de knop eronder. Een tweede, lichte beoordelaar die het geschreven antwoord las en "beantwoord", "deels" of "niet beantwoord" zei, is eruit: hij was een tweede eigenaar van dezelfde vraag en week in productie, in veertien dagen, af van het "beantwoordt" van de keuzestap bij 15 van ongeveer 255 geschreven widgetantwoorden. Er is één vangnet: kreeg de schrijver alle passages omdat de keuzestap niet koos, en draagt de controle geen van minstens twee uitspraken, dan wordt het antwoord de weigering. Een antwoord zonder bron op een beurt die niet uit de artikelen antwoordde (geen passage gekozen, of een open deel) is een doodlopend einde en krijgt dezelfde knop; hangt de knop onder zo'n antwoord zonder dat het hem noemt, dan zet code de afspraakzin erachter. Een mislukte controle op een antwoord zonder bron weigert.

Intern Strict wordt op dezelfde manier vastgehouden en gecontroleerd, zonder knop en met de Strict-weigering in plaats van de tekst van de widget. Open en general worden getoond zoals ze geschreven zijn: de tekst gaat naar de medewerker terwijl het model schrijft, een onaffe link wordt achtergehouden tot hij compleet is, en na de afronding volgen alleen het ongeziene staartstuk en de voettekst. Valt de schrijver uit, dan krijgt de lezer een foutmelding en geen vervangend antwoord. Het record bewaart de beslissing (`decision`), de knop, het model en de tijd per stap.

### De controle na het antwoord

Eén controle leest het antwoord nadat het geschreven is, in één vorm (`answer_check.AnswerCheck`) waaruit de voettekst en het record allebei lezen, zodat ze dezelfde uitkomst zien. Ze heeft twee helften. De ene is code: reguliere expressies vinden de e-mailadressen, links, telefoonnummers (vergeleken op de laatste negen cijfers) en bedragen (vergeleken als munt plus waarde) en zoeken elk op in de passages en in wat de gebruiker in het gesprek schreef. De andere is een model (`klai-medium`) dat de uitspraken van het antwoord opsomt en aangeeft welke geen passage draagt. Las de schrijver alleen gekozen passages en citeert het antwoord er een, dan loopt die tweede helft pas nadat het antwoord weg is, omdat er daarna niets meer aan verandert; anders loopt ze op het pad, met 4 seconden op de widget en 12 intern. Een Open-antwoord met passages wordt altijd achteraf gecontroleerd, en general niet, omdat daar geen artikel was.

Wat de controle niet doet, is het antwoord veranderen: ze meet, en alleen het vangnet bij de schrijver leest haar uitkomst op het pad. In het record komen drie velden: `unsupported` (hoeveel uitspraken geen passage draagt), `grounding` (het oordeel over het hele antwoord) en `hard_facts_outside_kb` (per soort hoeveel harde feiten nergens staan). Dat laatste veld vult alleen Open, omdat de tekst daar al op het scherm staat en alleen nog gemeld kan worden, terwijl op de widget de herkomstpoort een onbekend telefoonnummer of e-mailadres er vóór het versturen al uithaalt. De widget bewaart daarnaast de tekst van hooguit vijf afgekeurde uitspraken, omdat haar record de tekst van het antwoord toch al heeft; het interne record bewaart alleen het aantal.

### De voettekst

De voettekst bestaat alleen in de interne chat, omdat LibreChat alleen tekst toont; de widget stuurt de bronnen als eigen veld mee en de afspraak als knop. Onder het antwoord staan de bronnen en een blok "Agent activiteit" met de modus, het aantal opgehaalde fragmenten en het aantal apart gezochte deelvragen. Een Open-antwoord dat geen bron draagt, krijgt de regel "Antwoord: algemene kennis, niet uit je kennisbank.", en een Open-antwoord met een hard feit dat nergens staat de regel "Let op: … in dit antwoord staat niet in de kennisbank.", met de soorten erin. Beide regels schrijft code en niet het model, omdat het oordeel pas valt als de tekst al gestreamd is. Wie om een antwoord zonder bronnen vraagt, krijgt de waarschuwing over harde feiten toch, en zonder bron en zonder iets te melden is er geen voettekst. De voettekst is Nederlands of Engels.

### Het record

Elk oppervlak legt vast wat de beurt deed, zonder dat de lezer het ziet. De widget schrijft het antwoord met tekst, bronnen en signalen naar `widget_messages.answer_signals`, direct uit het antwoord zelf en met een schrijftaak die start voordat het eerste frame verstuurd wordt, zodat het transcript ook een antwoord bevat dat de bezoeker maar half ontving; zodra de controle achteraf klaar is, worden haar velden in dezelfde rij bijgewerkt. Zo kan een weekreview elke beurt lezen zonder naspelen: de zaak, de zoekvragen, de gevonden en de bewaarde passages, het oordeel van de keuzestap met citaten, de beslissing, de knop, de controle en de tijd per stap. De interne chat schrijft naar `internal_chat_turns` alleen signalen, nooit tekst, geen citaten en geen zaak, omdat LibreChat het gesprek al bewaart. Van een partnersleutel wordt alleen het zoeklogboek bewaard, voor feedback op het antwoord.

### Wat het systeem niet doet

De keuzestap is een zachte poort. Een model beslist wat de schrijver leest, en code ziet alleen of het citaat terugkomt, niet of een afgewezen artikel toch had geantwoord of een gekozen artikel alleen op de vraag lijkt. Op de widget kost een onterechte afwijzing een antwoord ("niet gevonden"); in intern Open kost ze niets, want de schrijver houdt alle passages. Een variant vraagt ze te zelden door, en een scherpere opdracht en een groter model losten dat niet op zonder antwoorden te kosten (§8, de rijen van stap J en "Variant per citaat"). Ook uit gekozen passages blijft ongeveer een op de vijf geschreven zinnen onbewezen; dat is het plafond van kiezen alleen.

De controle op harde feiten is een heuristiek. Ze herkent alleen e-mailadressen, links, telefoonnummers van minstens negen cijfers en bedragen met een munt erbij, dus een menunaam, een stap of een getal zonder munt ziet ze niet, en een telefoonnummer of bedrag dat over twee passages verdeeld staat telt als niet gedragen. Ook de herkomstpoort op de widget kijkt alleen naar telefoonnummers, e-mailadressen en links, niet naar menunamen, omdat die niet exact te herkennen zijn.

Het geheugen van de interne chat is op de meetlat niet te zien, omdat die een beurt naspeelt zonder de beurten ervoor, dus zonder bewaarde zaak. Of het werkt, laat het record live zien: `case_remembered` zegt per beurt of er een zaak lag, en de logregel `internal_chat_case_remembered` telt het per org.

De meting is klein. De widgetmeetlat telt 109 gevallen en de interne 36 beurten, en tussen twee rondes van dezelfde code schuiven de uitkomsten al enkele gevallen, zodat een verschil van die orde niets bewijst; of een antwoord inhoudelijk goed is, kan alleen de eigenaar zeggen, en dat oordeel ligt vast in de verwachting per geval, niet in een live cijfer. Live wordt gemeten wat het record vastlegt: per widgetbeurt het hele spoor en de controle achteraf, per interne beurt de signalen met de harde feiten in Open. Niet live gemeten wordt of een antwoord klopt, of een afgewezen artikel wel had geantwoord, en wat een partnersleutel antwoordde, want daarvan blijft alleen het zoeklogboek.

### Hoe het gemeten wordt

De meetlat is één CLI, `klai-portal/backend/scripts/meetlat.py`, met de werkwijze in [docs/runbooks/meetlat.md](../runbooks/meetlat.md). `run` speelt de beurten uit een casusbestand na door het ene chatpad en schrijft per beurt een rij, en `compare` legt de rijen van een of meer rondes naast de verwachting per geval: per ronde de oordelen per klasse, de beslissingen, de onbewezen uitspraken, de harde feiten buiten de kennisbank, de mediane beurttijd en de oordelen van de keuzestap, met een leessectie voor de gevallen die een mens moet lezen. Een kandidaat wordt gemeten als overlay, een volledige kopie van de code van de kandidaat die vooraan op het pad van een wegwerpkopie van portal-api staat. Omdat in oktober drie rondes ongemerkt de code van main draaiden, drukt `run` vóór de eerste beurt een banner af met het bestand waaruit elke module van het pad geladen is, en met `--overlay` weigert het (exit 2) zodra er één niet uit de overlay komt. Het naspelen is alleen-lezen, dus er komt geen zoeklogboek, geen kennisgat en geen intern record bij. Omdat een vervolgbeurt in productie de zaak leest die de beurten ervoor bewaarden, speelt `run` per gesprek elke beurt tot en met de hoogste genoemde na, op volgorde, en schrijft alleen voor de genoemde beurten een rij; dat geheugen staat in het geheugen van het proces, per gesprek (het gespreks-id van LibreChat, de voorbeeldsessie van de widget), en nooit in de database.

Gemeten wordt in twee rondes per variant tegen twee nulmetingsrondes op main van dezelfde dag, omdat één ronde geen meting is en het verschil tussen twee rondes van dezelfde code de ruis laat zien. Het casusbestand, de rijen en de overlay staan buiten de repository, in de uitvoermap van de wegwerpkopie, omdat de rijen echte antwoorden bevatten; de CLI zelf bevat geen gevalsdata. De widgetrondes in §8 van vóór 22 oktober zijn niet met deze CLI gedraaid.

## 5a. Het ontwerp van 30 september, zoals het bedoeld was

Dit is de tekst van het oorspronkelijke §5, ongewijzigd bewaard als geschiedenis; wat het systeem nu is, beschrijft §5.

### De twee inzichten waarop het rust

**Eerste inzicht (30 september).** Een klein model dat passages krijgt over het onderwerp van de vraag, schrijft een antwoord, ook als het antwoord er niet in staat. Het leent de vorm uit wat het leest en vult de inhoud zelf in. De zoekscore voorkomt dat niet, want ze meet of een passage op de zoekvraag lijkt, niet of ze de vraag beantwoordt. Daaruit volgt: beslis vóór het schrijven wat de schrijver mag lezen en of er geschreven wordt; na het schrijven wordt alleen gemeten [gemeten, §8; onderzoek §3.1 en §3.2; broncode §3.6]. Dit is gebouwd (de keuzestap) en live.

**Tweede inzicht (12 oktober, uit de eerste week echt verkeer).** Elke beurt wordt behandeld als een losse vraag met een lap geschiedenis erachter. Vier modelaanroepen leiden daar elk voor zich uit af wat de bezoeker bedoelt (de vraagbeoordelaar, de herschrijving van de zoekvraag, de keuzestap, de schrijver); niemand legt het vast en niemand geeft het door. Daarom verliest "Android" of "als ingaand nummer" het onderwerp in het zoeken, terwijl de keuzestap drie stappen later wél opschrijft wat de bezoeker nodig heeft en dat veld daarna verdampt. De context van een supportgesprek is niet de laatste zin maar de zaak: het probleem, product, apparaat en richting, wat al geprobeerd is, en wat de bezoeker nu wil [gemeten: ruim twintig vervolgbeurten naar "niet gevonden" in één week; onderzoek: CLAMBER en Sufficient Context over verzinnen bij onvoldoende context; broncode: geen van de twaalf gelezen systemen houdt gespreksstaat bij, ze herschrijven alleen de vraag].

Daaruit volgt de regel waar het ontwerp nu op rust:

> **Eén expliciete zaak per gesprek, in code bewaard en per beurt bijgewerkt. Elke modelaanroep is een zuivere functie van die zaak plus de passages. Code kiest de route. Alles wat naar de bezoeker gaat, wordt in code op herkomst gecontroleerd. Na het schrijven wordt alleen gemeten.**

### De stroom

Eén stroom voor de publieke en de interne chat; de code is sinds 24 september al één pad, alleen de uiteinden verschillen (tabel verderop). Gebouwd en bewezen wordt op de widget, omdat daar de meetlat ligt; de interne chat volgt op dezelfde code met een eigen meetlat (stap N in §7).

| # | Stap | Wie | Levert | Ten opzichte van nu |
|---|---|---|---|---|
| 1 | **Begrijpen: de zaak bijwerken.** Eén aanroep leest de zaak van de vorige beurt en het nieuwe bericht en levert vaste velden: probleem; product, apparaat, richting; al geprobeerd; wat de bezoeker nú wil (kennis, mens, contact, afspraak, praatje, buiten onderwerp); toon; twee zoekvragen afgeleid uit de zaak | klein model, vaste velden | de zaak, in het gesprek bewaard | Vervangt vier aanroepen: vraagbeoordelaar, herschrijving, verwijswoorden in het zoeken, herformuleringen. "Dit werkt niet" wordt "geprobeerd: X" en de zoekvraag blijft het probleem |
| 2 | **Route in code.** Mens, contact, afspraak, praatje, buiten onderwerp: vaste reactie, geen schrijver | code | reactie, of door | De regex en de beoordelaar herkennen dit nu half; "ik wil jullie mailen" hoort nooit "niet gevonden" te krijgen |
| 3 | **Zoeken, breed en op secties.** De twee zoekvragen plus de letterlijke zin, gefuseerd, teruggebracht tot hele secties (ouder-uitbreiding zoals Onyx, Dify, RAGFlow doen), zonder dubbelen en zonder losse feitzinnen uit de kennisgraaf | zoekmachine | secties | Breder zoeken is veilig omdat stap 4 kiest; zonder keuzestap haalde het alleen lijkende artikelen binnen [gemeten, stap A] |
| 4 | **Kiezen**, tegen de zaak in plaats van tegen de laatste zin: behoefte, letterlijk citaat, oordeel; citaat in code teruggezocht | middelgroot model | gekozen secties, of een route zonder schrijver | Staat er; krijgt de zaak als invoer |
| 5 | **Schrijven** uit de gekozen secties met de regels die het antwoord dragen; of de ene vraag; of de vaste tekst | model, model, code | de reactie | Staat er |
| 6 | **Herkomstpoort in code.** Elke url, elk telefoonnummer, elk e-mailadres en elke menunaam in het antwoord moet in de gekozen passages staan; wat daar niet staat gaat niet mee. Links uit de passages blijven juist staan | code | de reactie | Vervangt het linkfilter dat nu het antwoord wegknipt en vangt het nummer uit een ander artikel. Exact werk, dus code [gemeten: de controle op harde feiten dekte een vijfde van alle afgekeurde zinnen, maar voor deze klasse volledig] |
| 7 | **Meten.** De controle per zin, en per beurt een volledig spoor (zaak, zoekvragen, passages, keuze, opdracht), kort bewaard naast het gesprek | code en model | meetgegevens | Een vaste reactie bewaart nu niets, en het spoor bestaat alleen in de privé-replay |

Van maximaal acht modelaanroepen per beurt naar drie op het pad en één meting erachter. Valt stap 1 of 4 uit, dan loopt de beurt door zoals elk gelezen open-source systeem dat doet: een storing wordt geen weigering.

| Punt in de stroom | Publieke chat | Interne chat |
|---|---|---|
| "Staat er niet" | Afspraakknop | Zeggen dat het niet in de kennisbank staat; in Open mag algemene kennis, duidelijk gelabeld |
| Weg naar een mens | Afspraakknop | Bestaat niet |
| Bronnen | Bronkaart | Volledige bronnenlijst |
| Toegang | Publieke kennis | De kennisbanken van de medewerker |
| Invoer | Korte vragen | Lange vragen en geplakte klantmails, eerst gedistilleerd; de zaak neemt die distillatie over |

### Wat dit per fout van de eerste week doet

- Vervolgbeurt naar "niet gevonden" (de grootste): stap 1 en 3.
- Eerste vraag die de kennisbank wel beantwoordt en toch "niet gevonden" krijgt: stap 3, en daarna lezen welke artikelen nog steeds niet komen.
- Het linkfilter dat het antwoord wegknipt: stap 6, met stap C (linkteksten behouden bij het inlezen) ervóór.
- Een telefoonnummer uit een artikel over een ander onderwerp: stap 6.
- Contactverzoeken en de losse zin uit het profielvoorbeeld: stap 2, en het voorbeeld uit het profiel.
- Een eenwoords vervolg dat het onderwerp verliest: stap 1.

### Wat eruit gaat

De lichte antwoordbeoordelaar, de regel voor zwakke bronnen op de widget, de vraagstap op artikeltitels, de herformuleringen als losse aanroep, het linkfilter als tekstknipper, en de reparatie achteraf op de interne chat. Elk pas na de meting die laat zien dat de nieuwe stap het dekt, met een eigen regel in §8. **Gemeten op 12 en 13 oktober (stap M in §8):** de vraagbeoordelaar, de zwakke-bronnenregel en de reparatie zijn eruit; de herformuleringen blijven op de eerste beurt (ze verbreden waar de zaak-zoekvragen versmallen, en die breedte draagt de verduidelijkingsvraag); de lichte beoordelaar blijft voor de knop. De brede modus stond hier eerst ook, ten onrechte: die is geen opruimkandidaat maar een keuze van de bezoeker als de kennisbank niets heeft.

### Wat het ontwerp niet oplost

- **Wat niet in de kennisbank staat.** Dat blijft "niet gevonden" en wordt een lijst voor de eigenaar.
- **De brontekst.** Een gat waar een linktekst hoort blijft een gat tot stap C.
- **Een keuzestap die ernaast zit.** Ze viel in de eerste week 6 van de 139 keer uit op tijd en oordeelde in de gelezen gevallen juist; het risico blijft dat ze een goed artikel afwijst, en de meetlat moet dat elke stap opnieuw wegen.

### Hoe het pad werkte op de helpwidget vóór de keuzestap (ochtend van 30 september)

Dit is wat er nu live staat, per stap, met wat de stap doorgeeft aan de volgende. Gewijzigd sinds de review staat erbij.

| Stap | Wat ze doet | Wie beslist | Geeft door |
|---|---|---|---|
| Vraagbeoordelaar | Leest de laatste beurt: duidelijk of niet, toon, wil een mens, onderwerp dat niet behandeld wordt | klein model, gesloten velden | route en toon |
| Zoekvraag | Eerste beurt: de woorden van de bezoeker plus twee herformuleringen als extra zoekgang. Vervolgbeurt: de laatste zin, waarin het zoeken zelf de verwijswoorden invult | middelgroot model (herformuleren), klein model (verwijswoorden) | een of drie zoekvragen |
| Zoeken | Acht passages, met een score per passage | zoekmachine en herrangschikker | passages en hoogste score |
| Gat-bepaling | Niets gevonden, of alles onder de drempel | code | "zwakke bronnen" of niet |
| Doorverwijzen | Onderwerp dat de eigenaar niet laat behandelen: vaste verwijzing met knop, geen antwoordmodel | code, op het oordeel van de vraagbeoordelaar | einde beurt |
| Vraagstap | Eén vraag als de gevonden artikelen per variant verschillen. **Gewijzigd:** hooguit één vraag per gesprek | code; klein model schrijft de zin | einde beurt, of door |
| Schrijven | Het antwoord, uit de passages, in de toon van het profiel. **Gewijzigd:** geen voorbeeldzin met excuus meer | antwoordmodel | concept |
| Bronnen | Kiest welke artikelen als bron onder het antwoord komen | code | concept met bronnen |
| Twee controles | Lichte beoordelaar (beantwoord of niet) en controle per zin tegen de artikelen | klein en middelgroot model | oordeel en afgekeurde zinnen |
| Beslissing | Antwoord, deelantwoord of weigering | code | reactie |
| Na de controle | **Gewijzigd:** het antwoord wordt niet meer herschreven. Twee of meer afgekeurde zinnen: antwoord blijft heel, knop en zin over de afspraak eronder. Niets onderbouwd of een tegenspraak: de vaste weigering | code | reactie |
| Afspraak | **Gewijzigd:** hangt de knop er en noemt de tekst hem niet, dan zet code de zin erbij. "Breder zoeken" alleen nog als er niets gevonden is | code | wat de bezoeker ziet |
| Record | **Gewijzigd:** bewaart ook de knop, de toon en of een geplande vraag gesteld is | code | meetgegevens |

Drie dingen die het lezen van de sporen over dit pad leerde en die nog gelden:

- De score zegt dat een passage op de vraag lijkt, niet dat ze de vraag beantwoordt. Geen stap op het pad ziet het verschil tussen "zelfde onderwerp" en "zelfde vraag" (inkomend tegenover uitgaand, app tegenover bureautelefoon).
- De controle per zin vangt verzonnen zinnen, maar keurt een antwoord dat netjes uit het verkeerde artikel komt goed.
- Meer vinden maakt het antwoord niet beter zolang dat eerste punt niet is opgelost (stap A in §8).

### Waar we staan

**Stand 13 oktober.** De stroom van dit ontwerp staat live op de widget en op de interne chat, en elke LibreChat-omgeving loopt erover (§8, O1 tot O6). Wat de meetlat zegt: antwoorden die grotendeels verzonnen waren van een vijfde naar een twintigste, een geschreven beurt van 6,7 naar 5,3 seconden, in de open modus intern 3,8 naar 2,9 onbewezen zinnen per antwoord, en na stap J 61 van de 81 verwachte antwoorden in beide rondes tegen 53. Wat niet opgelost is: de keuzestap vraagt bij een variant te zelden door (drie opdrachten en drie modellen gemeten, geen wint zonder antwoorden te kosten), een op de vijf geschreven zinnen is nog onbewezen (het plafond van kiezen alleen), en de LiteLLM-hook staat nog in de container als terugweg. De tekst hieronder is de stand van 29 september en blijft als geschiedenis staan.

Van de foutsoorten uit de review is de grootste (een antwoord dat door de reparatie verminkt werd) bij de bron weggenomen, en zijn vier kleinere opgelost: de afspraak die niet genoemd werd, het aanbod "breder zoeken" waar het niets kon doen, het excuus waar niets misging, en de tweede vraag in hetzelfde gesprek. Niet opgelost: het lijkende artikel dat wint, de vraag met meerdere delen, de vervolgbeurt die het onderwerp echt mist, de gemengde vraag die in zijn geheel wordt doorverwezen, de gaten in de brontekst, en wat niet in de kennisbank staat.

Een nieuw cijfer voor "goed" is er niet [aanname zolang de eigenaar niet opnieuw beoordeelt]: de soort reactie klopte al in de meeste gevallen, en of de inhoud goed is kan alleen de eigenaar zeggen. De nagespeelde antwoorden met en zonder reparatie liggen klaar om te beoordelen.

### Volgorde vanaf nu

De stappen H tot en met N en O1 tot en met O6 en J uit §7 zijn gedaan (§8), net als het zaakgeheugen van de interne chat en de opruiming van 22 en 23 oktober. Wat open staat, met het besluit dat erbij hoort. **De controle op harde feiten** (§5) blijft alleen als de inbox laat zien dat ze terecht aanslaat: het record telt per antwoord `hard_facts_outside_kb`; staan daar na een maand echt verkeer vrijwel alleen onterechte treffers in, of geen, dan gaat ze eruit, want een laag die niets vangt is een laag te veel. **Eén E2E-journey met twee beurten in één gesprek**, zodat het zaakgeheugen van de interne chat bij elke deploy automatisch bewezen wordt; nu bewijst alleen een handmatige proef via het endpoint het, omdat J02 per vraag een nieuw gesprek opent. **Per variant één artikel in de kennisbank** benoemen zodat de keuzestap twee artikelen ziet in plaats van secties van hetzelfde (de rest van J), en **een tweede, blinde beoordeling van de inhoud door de eigenaar**, want de meetlat onderscheidt een goed en een slecht antwoord van dezelfde soort niet. **De Windows-app-vraag** uit de interne meetlat is een zoekprobleem: het antwoord staat in het artikel over Webphone-problemen, dat de zoekdienst zonder het woord Webphone niet vindt; één zin in het artikel Softphones & Apps lost het op, in de kennisbank, niet in code.

## 6. Werkafspraken

| Afspraak | Waarom |
|---|---|
| Elke bewering in een voorstel draagt een label uit de kop van dit document | Dan is zichtbaar waar een ontwerp op rust |
| Wie een voorstel doet, zegt wat hem van mening zou doen veranderen; een positie verandert op nieuw bewijs | Taalmodellen schuiven mee met tegenspraak: 58% in één meting [onderzoek: SycEval, https://arxiv.org/abs/2502.08177] |
| Eén meetlat: echte gesprekken met per gesprek de verwachting en het bewijs, vastgesteld door de eigenaar; draaien en vergelijken gaat met `klai-portal/backend/scripts/meetlat.py`, zie [docs/runbooks/meetlat.md](../runbooks/meetlat.md) | Zonder vaste meetlat stuurt het laatste incident |
| Geen wijziging aan het chatpad zonder de meetlat; de uitkomst staat in de PR | De poort op papier is vijf keer overgeslagen |
| Eén wijziging, één verwachting, vooraf opgeschreven met het criterium om te stoppen | Anders is een daling niet toe te wijzen |
| Elke stap verdient zijn plek: wat op de meetlat niets bijdraagt, gaat eruit | Het systeem groeide alleen |
| Eerst lezen, dan tellen, dan voorstellen: voor elke conclusie worden minstens tien echte gevallen van begin tot eind gevolgd, en wordt per geval de eerste plek op het pad vastgelegd waar het misgaat | Tellen per onderdeel liet de oorzaak vóór het schrijven een week onzichtbaar |
| Een wijziging wordt beoordeeld op het hele pad: wat ze vooraan verandert en wat ze achteraan overbodig maakt | Anders wordt het een patch aan het laatste station |
| Een spoor legt vast wat elke stap werkelijk doorgaf aan de volgende; waar het spoor dat niet laat zien, is dat een gat om te dichten en geen ruimte voor een aanname | De zoekvraag van vervolgbeurten stond niet in het spoor, en de grootste oorzaak uit de eerste lezing bleek voor twee derde een aanname |
| Winst bij het zoeken telt pas als de antwoorden van begin tot eind gelezen zijn | Een beoordelend model noemt een passage over hetzelfde onderwerp snel een antwoord; twee keer gaf meer vinden geen beter antwoord |
| Eerst klein bewijzen, dan groot meten: een onderdeel wordt los getest op een handvol gevallen waarvan de juiste uitkomst door lezen is vastgesteld, meerdere keren herhaald; daarna aan elkaar gekoppeld op een kleine set die elke route raakt; pas dan de hele meetlat | De eerste gekoppelde proef op achttien gevallen liet in vier minuten drie botsingen tussen onderdelen zien die een grote run alleen als een getal had getoond |
| Een tweede lezer met schone blik beoordeelt alleen juistheid | De maker keurt zijn eigen werk te makkelijk goed |
| Elke week tien tot twintig echte gesprekken lezen; elke nieuwe fout wordt een vast geval in de meetlat | Zo valt een daling binnen een week op |
| Meetruns draaien op het Vibe-tegoed (`klai-judge`, `klai-ingest`), nooit op de sleutels van echte bezoekers | Een eerdere meting putte het gedeelde tegoed uit en gaf een bezoeker een foutmelding |
| Echte gesprekken en de meetlat staan buiten deze repository; tests gebruiken verzonnen gevallen | De repository is publiek |
| De E2E-organisatie (slug `e2e`, org 22) telt in geen enkele kwaliteitslezing of -telling mee; de alerts sluiten haar al op slug uit | De E2E-run van 21 oktober leverde 227 van de 236 interne beurten van die week, zodat een weeklezing zonder die uitsluiting het gedrag van een testscript meet |
| Dit document en chat-system.md worden in dezelfde PR bijgewerkt als de code | De documenten liepen achter |

## 7. Plan

Volgorde: eerst vastleggen en de meetlat, daarna de kleine wijzigingen met de grootste impact, daarna de kern. De impact is het aantal antwoorden uit de review dat de wijziging raakt (van 109). Elke stap gaat pas live als de meetlat geen achteruitgang laat zien op gevallen die eerst goed gingen.

| # | Stap | Raakt | Omvang | Validatie |
|---|---|---|---|---|
| 0 | Dit document, chat-system.md gelijk aan de code, logboeken als archief | – | documenten | Ankers nagelezen tegen de code |
| 1 | De meetlat: per echt gesprek de verwachte reactie, het dragende artikel en de feiten die erin moeten staan; een script dat de keten herspeelt en de uitkomst in code naast de verwachting legt | – | gereedschap | De huidige keten scoort op de meetlat wat de review vond |
| 2 | Nulmeting en bijdrage per bestaande stap: keten zoals nu, zonder reparatie, zonder antwoordbeoordelaar | – | meting | Twee rondes, zelfde richting |
| 3 | Vervallen na meting: controle en reparatie laten zinnen overslaan die niets over het bedrijf beweren. In de nulmeting haalde het overgrote deel van de reparaties de drempel ook op echte beweringen, dus dit verandert bijna niets | – | – | Zie §8 |
| 4 | Niet uitgerold na meting: negatieve toon zonder verzoek om een mens gewoon laten antwoorden met de knop eronder | 2, plus overgeslagen stappen | S | Zie §8 |
| 5 | De zin over de afspraak komt uit code als de knop er hangt en de tekst hem niet noemt | 5 | S | Test; telling op echt verkeer |
| 6 | "Breder zoeken" alleen aanbieden als het iets kan doen | 1 tot 2 | S | Test |
| 7 | De excuus-voorbeeldzin uit het profiel. De maskeerinstructie (noemt alleen soorten die in die aanroep gemaskeerd zijn) is een wijziging aan de maskering zelf en wacht op een eigen, zwaardere review | 3 en 1 | S | Meetlat; telling op echt verkeer |
| 8 | De vraagstap telt alleen als het antwoord op een vraag eindigt, en vraagt niet twee keer naar hetzelfde | 1 tot 3 | S | Bestaande poortevaluatie met vaste gevallen |
**Herzien op 30 september, na het lezen van de sporen.** De stappen 9 tot en met 13 zoals ze hier stonden (reparatie vervangen, een beslismoment als poort, controle op harde feiten, kenmerken per artikel, de interne chat) zijn vervangen door de stappen hieronder, in de volgorde van het pad. De twee metingen die al gedaan waren voor het beslismoment en de controle in code staan in §8.

| # | Stap op het pad | Wat verandert | Omvang | Validatie |
|---|---|---|---|---|
| A | Begrijpen, vervolgbeurt | Niet uitgerold na meting: een vervolgbeurt ook zoeken met het onderwerp van het gesprek erbij | M | Zie §8 |
| B | Begrijpen, meerdere delen en varianten | Een vraag met meerdere delen wordt per deel gezocht en per deel beantwoord of eerlijk overgeslagen; bepalen varianten de uitkomst, dan één vraag | M | Sporen van die gevallen lezen; aandeel lijstregels dat in geen passage staat |
| C | Brontekst | Linkteksten blijven behouden bij het inlezen, zodat "Ga naar ." weer een menunaam heeft; besluit van de eigenaar over de documentatie van het andere product in dezelfde kennisbank | M, plus opnieuw inlezen | Aandeel passages met een gat; aandeel lijstregels dat in geen passage staat |
| D | Onderwerpstap | Een technische vraag krijgt niet de tekst over prijzen en offertes | S | De afgewezen technische gevallen uit de meetlat |
| E | Zoeken | Kenmerken per artikel (richting, apparaat) zodat importeren niet wint van exporteren | L | Zoekmeting op de verkeerd gelezen gevallen |
| F | Schrijven en controleren | De reparatie op de widget is eruit (zie §8). Nog open: de regels in het profiel die om een complete procedure vragen, en de lichte antwoordbeoordelaar | – | Hele meetlat opnieuw, sporen lezen |
| G | Interne chat | Dezelfde stappen, met een eigen meetlat uit echte interne vragen; daarna de oude route uit | L | Meetlat intern |

**Herzien op 12 oktober, na de eerste week echt verkeer en het ontwerp van §5.** De stappen B tot en met G blijven staan waar ze nog niet gedekt zijn; de stappen hieronder zijn de architectuur van §5, in de volgorde waarin ze gebouwd en bewezen worden. Elke stap gaat eerst los op gelezen gevallen met herhalingen, dan gekoppeld op een kleine set die elke route raakt, dan op de hele meetlat; pas dan live.

| # | Stap op het pad | Wat verandert | Omvang | Validatie |
|---|---|---|---|---|
| H | Begrijpen: de zaak | Gebouwd (zie §8): één aanroep die de zaak bijwerkt en de zoekvragen eruit afleidt; de zaak wordt bij het gesprek bewaard. Nog niet: de vraagbeoordelaar, de herschrijving en de herformuleringen vervangen (stap M) | L | Eerst los: op de ruim zeventig vervolgbeurten uit september en oktober, draagt de zoekvraag het onderwerp en komt het artikel in de passages? Dan gekoppeld aan de keuzestap. De meting van de herformuleringen herhaald |
| I | Herkomstpoort in code | Gebouwd (zie §8): url, telefoonnummer en e-mailadres in het antwoord moeten in de gekozen passages staan. Niet gedaan: menunamen, die zijn niet exact te herkennen | S | De zes beurten met weggeknipte adressen en de vier met een telefoonnummer uit oktober; geen verlies op de goede antwoorden van de meetlat |
| J | Zoeken op secties, gefuseerd | Gebouwd (zie §8): in de zoekdienst één passage per sectie (het best scorende kindstuk per moedertekst overleeft elke top-k-grens) en een instelbaar aantal bronnen in het bewijspakket; in de portal vraagt een beurt met keuzestap om een bron per passage in plaats van de drie waarop het pakket was afgesteld. Niet gedaan: losse feitzinnen eruit, dat kostte eerder antwoorden | M | Aandeel beurten waar het bekende artikel in de passages zit en gekozen wordt; het aandeel "staat er niet in" |
| K | Route in code voor contactverzoeken | Gebouwd (zie §8): mens, contact, afspraak uit de zaak krijgen de vaste reactie met de knop, zonder schrijver. Nog niet: het profielvoorbeeld "Laat het gerust weten als je vastloopt" eruit | S | De contactverzoeken uit oktober en september |
| L | Spoor per beurt | Gebouwd (zie §8): de zaak, de zoekvragen, de gevonden passages en de keuze bewaard in het record van de beurt, ook bij een vaste reactie. Niet bewaard: de letterlijke opdracht aan het model | M | Een weekreview kan zonder naspelen |
| M | Opruimen | Gebouwd (zie §8): de vraagbeoordelaar vervangen door de zaak, de regel voor zwakke bronnen op de widget eruit, de controle per zin na het antwoord als de passages gekozen en geciteerd zijn. De vraagstap op artikeltitels was op de widget al vervangen door de vraag van de keuzestap. Blijft na meting: de herformuleringen op de eerste beurt (ze verbreden waar de zaak versmalt), de lichte beoordelaar voor de knop (0,4 s), en de brede modus als apart pad (de bezoeker kiest die zelf, zie §8) | M | Per onderdeel een meting op de hele meetlat vóór het eruit gaat |
| N | Interne chat | Gebouwd (zie §8): de keuzestap op intern Strict, met de vaste Strict-weigering als geen passage antwoordt, en de reparatie achteraf eruit (13 okt). Niet: de zaak op de interne chat, want de open modus, waar de meeste medewerkers in werken, leest geen passages (was stap G) | L | Meetlat intern |


**Herzien op 13 oktober, na stap M en N.** De stroom van §5 bestaat als losse kleine modules (zaak, keuze, herkomst), maar wordt aangeroepen vanuit de oude, vertakte route: één functie van 762 regels met twintig vlaggen, twee bestanden van samen ruim 8.600 regels, acht complexiteitsuitzonderingen, en drie chatpaden die geen één stroom zijn. De stappen hieronder maken van de stroom de drager zelf, halen de laatste dubbele mechanismen eruit en brengen de interne chat op dezelfde code. De werking gaat voor, de opruiming volgt de werking. Elke snede wordt door een eigen uitvoerder in een eigen worktree gebouwd en gaat pas live na de meetlat.

| # | Snede | Wat verandert | Omvang | Validatie |
|---|---|---|---|---|
| O1 | De drager | Gebouwd (zie §8): `widget_turn()` in `services/widget_turn.py` in de volgorde van §5, geeft een `Reply` terug; `chat_completions` van 762 naar 295 regels. Geen gedragswijziging | L | Suite groen; één meetronde gelijk aan de laatste binnen de ruis van de keuzestap |
| O3 | Eén compositie | Gebouwd (zie §8): `write_reply` in `services/widget_write.py` (één modelaanroep, bronnencomposer, herkomstpoort, lichte beoordelaar, controle erna, vangnet, afspraakzin, tijd) en één uitzender in `services/widget_reply.py` (JSON of frames); de widgettakken van de twee oude generatiepaden en de twee vaste-reactiefuncties zijn weg | L | Suite groen; één meetronde |
| O2 | Knop uit code | Gemeten en niet gedaan (zie §8): een veld `covers` in de keuzestap maakte haar oordeel minder stabiel (33 van 39 tegen 36 van 39 op de gelezen gevallen). De lichte beoordelaar blijft als de ene kleine aanroep na het schrijven, alleen voor de knop | – | – |
| O4 | Interne chat op dezelfde stroom | Gebouwd (zie §8): `internal_turn()` in `services/internal_turn.py` op dezelfde stapfuncties als de widget; de zaak vervangt `query_rewrite` (verwijderd), de keuzestap draait op elke interne beurt (Open: gekozen passages en opdracht, nooit een weigering; Strict: poort), de vraag van de keuzestap is de reactie op Strict en Open, de vraagstap op titels (`clarify_decision`, verwijderd) en de zwakke-bronnennotitie zijn weg; Strict schrijft via `write_reply`, Open en general streamen live met de controle erna | L | Interne meetlat, Open en Strict |
| O5 | Alleen weghalen | Gebouwd (zie §8): dood na O1 tot O4 weg (de vraagpoort op titels met haar evaluatiescript, de duidelijkheidsinvoer van `decide_answer`, dubbele helpers, de oude citatiemodus "links" van ruim tweeduizend regels die sinds de bronnen op documentniveau onbereikbaar was); `answer_judge.py` blijft (de lichte beoordelaar voor de knop, zie O2); `chat-system.md` §2 herschreven naar de nieuwe stroom | L | Suite groen; niets erbij |
| O6 | Pad A | Uitgevoerd op 13 oktober (zie §8): elke omgeving is per `switch_internal_chat.py` op het portalpad gezet, dus elk LibreChat-gesprek loopt nu door `internal_turn()`. De hook met eigen reparatie en citatiecode is op 21 oktober uit de LiteLLM-container gehaald (zie §8) | L | Echt verkeer in de interne records, na een week |

Volgorde: O1, O3, O4, O5.

Wat eerder is afgewezen en hier niet terugkomt: doorvragen als opdracht aan het antwoordmodel, een ruimer budget voor de controle, het samenvoegen van de twee controles achteraf, de zekerheidsband als reden om te weigeren, meer passages of een vierde bron. Twee stappen raken aan eerder werk en zeggen daarom wat er nu anders is. Stap A: op 18 september is de herschrijving van vervolgvragen juist beperkt tot verwijswoorden, omdat ze in een vijfde van de gevallen het gesprek voortzette in plaats van een zoekvraag te maken (AJ 2.6); de sporen laten zien dat die beperking het onderwerp kost, en de interne route heeft intussen een herschrijving met een rem op het verliezen van het onderwerp. Stap C: het behouden van linkteksten is op 19 september gemeten en niet uitgerold omdat een beoordelend model geen voorkeur had (IQ §11); nu is er een maat in code die het gevolg van de gaten direct telt.

## 8. Uitkomsten per stap

Hier komt per stap van het plan: datum, verwachting vooraf, uitkomst op de meetlat, besluit, PR.

| Stap | Datum | Verwachting | Uitkomst | Besluit |
|---|---|---|---|---|
| 0 | 29 sep | Documenten gelijk aan de code | Acht afwijkingen gevonden door een tweede lezer, alle gecorrigeerd (#1775) | Vastgelegd |
| 1 | 29 sep | Een meetlat die de review weerspiegelt | Eén geval per beoordeeld antwoord, naspelen door de echte route met de score in code; de soort reactie alleen zegt weinig, want die klopte al bij de meeste gevallen. | In gebruik; privé |
| 2 | 29 sep | Zien wat elke stap bijdraagt | De reparatie draait op ruim een derde van de beurten en haalt in meer dan de helft daarvan stappen weg; een op de zeven beslissingen verschilt tussen twee rondes. | Reparatie eerst vervangen (stap 9) |
| 3 | 29 sep | Minder reparaties als de controle zinnen overslaat die niets beweren | Negen op de tien reparaties halen de drempel ook op echte beweringen | Vervallen |
| 4 | 29 sep | Een gefrustreerde bezoeker krijgt een gewoon antwoord met de knop | Op de beurten met een negatieve toon geen winst in de soort reactie, en de uitkomst verschilt tussen runs meer dan het effect | Niet uitgerold; wacht op het oordeel van de eigenaar over die beurten |
| 5 | 29 sep | Elk "niet gevonden" met knop noemt de afspraak | Van ruim acht op de tien naar ruim negen op de tien; wat overblijft zijn antwoorden die hun bron hielden, en daar verandert een beoordelaar de tekst niet | Uitgerold |
| 6 | 29 sep | Geen aanbod "breder zoeken" waar het niets kan doen | Van een op de tien beurten naar alleen de beurt waar het zoeken niets vond | Uitgerold |
| 7 | 29 sep | Geen excuus waar niets misging | Het voorbeeld en de excuusregel zijn weg; eerste antwoorden met een excuus gingen van enkele per ronde naar nul op de beurten met een negatieve toon. | Uitgerold |
| 8 | 29 sep | Hooguit één vraag per gesprek; het record zegt wat de bezoeker zag | Vaste code met tests; het record bewaart de knop, de toon en of een geplande vraag gesteld is | Uitgerold |
| 10 | 29 sep | Ons kleine model kan zien of de gevonden passages de vraag beantwoorden | Eerste proef, één ronde: het kleine model keurde een derde van de goede antwoorden af, het middelgrote een kwart, en de twee waren het in zeven op de tien gevallen eens. | Niet bouwen als poort; wel als signaal |
| 11 | 29 sep | Controle in code op harde feiten kan de controle per zin vervangen | Een vijfde van de zinnen die de controle afkeurt bevat een hard feit, en een derde van de harde feiten staat niet letterlijk in de passages. | Dekt een smal deel; wel een extra goedkope toets |
| – | 30 sep | Een blinde beoordelaar kan zeggen of het concept of de gerepareerde versie beter is | Twee rondes, beide volgordes, met de passages ernaast: de beoordelaar koos vrijwel altijd het concept. Maar in een kwart van de gevallen haalde de reparatie getallen of namen weg die niet in de passages stonden, en koos hij toch het concept | De beoordelaar verkiest het rijkere antwoord; bruikbaar als aanwijzing, niet als oordeel |
| – | 30 sep | De lijsten in antwoorden komen uit de artikelen | Per lijstregel teruggezocht in de passages van dezelfde beurt: vier op de tien staan er vrijwel letterlijk in, een derde deels, een kwart nergens | De oorzaak ligt vóór het schrijven; aanleiding voor het lezen van de sporen |
| – | 30 sep | Waar gaat het op het pad voor het eerst mis | Zie §2, "Wat de sporen lieten zien" | Plan herzien, stappen A tot en met G |
| A | 30 sep | Een vervolgbeurt vindt het juiste artikel als de zoekvraag het onderwerp van het gesprek meeneemt | Het onderwerp als extra zoekgang haalde vaker een passage die antwoordt en halveerde bijna de beurten met zwakke bronnen, maar gaf één keer een beter en meerdere keren een slechter antwoord. | Niet uitgerold; stap E schuift naar voren |
| F | 30 sep | Zonder de reparatie houdt een afgekeurd antwoord zijn stappen | Een vaste regel in code gaf evenveel weigeringen als de reparatie, in ruim twee derde het volledige concept, en een beurt was gemiddeld een seconde sneller. | Uitgerold op de widget; intern tot stap G |
| Kiezen vóór schrijven | 30 sep | Als een stap vóór het schrijven kiest welke passages de vraag beantwoorden, verzint het model niet meer | In bijna de helft van de schrijfbeurten bevatte geen enkele passage het antwoord; met kiezen vooraf gingen grotendeels onbewezen antwoorden van een vijfde naar bijna nul. | Gebouwd en klein bewezen |
| Kiezen vóór schrijven, hele meetlat | 30 sep | Met de definitieve code verzint het pad minder, zonder goede antwoorden te verliezen. | Grotendeels onbewezen antwoorden gingen van ruim een vijfde en een achtste naar een twintigste en een tiende, tegen de prijs van vaker "niet gevonden" (ruim een derde van de beurten). | Uitrollen |
| Echt verkeer, eerste week | 12 okt | Op echt verkeer doet de keuzestap wat ze in de meting deed, en wat overblijft wijst naar het zoeken | Van 118 beurten in 58 gesprekken was ongeveer twee derde goed of eerlijk en kreeg een derde "niet gevonden"; de grootste fout is een vervolgbeurt die het eerdere antwoord verfijnt (ruim twintig keer). | Volgorde aangepast: zoeken op vervolgbeurten eerst |
| H (klein) | 12 okt | De zaak draagt het onderwerp van het gesprek de zoekvraag in, zodat een vervolgbeurt het juiste artikel vindt | Met de zaak-zoekvragen als extra gangen vond de keuzestap in 77 beurten een passage die antwoordt tegen 59 met alleen de letterlijke zin (24 gewonnen, 6 verloren). | Gebouwd; meting op de hele meetlat volgt |
| H en K, hele meetlat | 12 okt | Met de zaak en de contactroute krijgen vervolgbeurten vaker een antwoord en contactverzoeken de afspraak, zonder dat goede antwoorden verloren gaan | Vervolgbeurten (51) kregen een antwoord in 28 en 30 beurten tegen 18 en 24, zonder dat een goed antwoord in beide rondes een weigering werd; grotendeels onbewezen 8 en 5 tegen 3 en 5. | Uitrollen; daarna stap J |
| – | 12 okt | Geen gedragswijziging: de exportlijst van de profielbibliotheek is gesorteerd nadat `contact_reply` erbij kwam (lint) | Niet gemeten, want er verandert geen tekst en geen regel; de 269 tests van de profielteksten en de drift-test tussen de twee kopieën zijn de controle | Samengevoegd zonder meting |
| I | 12 okt | Een adres dat in het artikel staat bereikt de bezoeker; een telefoonnummer of e-mailadres dat niet in de gekozen passages staat, bereikt hem niet | Als code gebouwd en nagespeeld: de bezoeker die twee keer om het API-adres vroeg krijgt het nu, en de regel zelf is bewezen met tests. | Uitgerold |
| J (keuzestap scherper) | 12 okt | Een opdracht die "zelfde apparaat, ander symptoom" expliciet uitsluit, wijst lijkende artikelen vaker af zonder goede te verliezen | De huidige opdracht had 36 van 39 goed en de scherpere ook 36 van 39, met dezelfde twee gevallen fout. | Niet gewijzigd |
| L | 12 okt | Een weekreview kan zonder naspelen | Het record van elke widgetbeurt bewaart nu het spoor van zoekvraag, passages, oordeel van de keuzestap en zaak, zonder gedragswijziging. | Uitgerold |
| M | 12 okt | Minder aanroepen en een snellere beurt zonder verlies: de zaak vervangt de vraagbeoordelaar, herformuleringen en zwakke-bronnenregel gaan eruit en de controle per zin komt na het antwoord. | Op de hele meetlat 71 en 64 zoals verwacht tegen 66 en 71 bij stap I, en een geschreven beurt van 5,3 en 5,4 s tegen 6,7 en 6,6 s. | Uitrollen; brede modus blijft |
| M en N, nazorg | 13 okt | Drie dingen die in de meting van M en N bleven liggen, nu gefixt: een citaat op het verkeerde passagenummer, "staat er niet in" mét een teruggevonden citaat, en de reparatie achteraf op de interne chat. | De negen beurten die de fix raakte kregen alle negen het verwachte antwoord, en de interne reparaties gingen van zeven naar nul op dezelfde twintig gesprekken. | Uitgerold; brede modus blijft |
| O2 | 13 okt | De keuzestap kan in dezelfde aanroep zeggen of het bewijs alles of een deel dekt, zodat de afspraakknop uit code komt en de lichte beoordelaar eruit kan | Met een extra veld `covers` waren de harde gevallen 33 van 39 goed tegen 36 van 39 zonder, omdat het veld het oordeel zelf verschuift. | Niet gedaan; lichte beoordelaar blijft |
| O1 en O3 | 13 okt | De stroom van §5 wordt de drager zelf, zonder gedragswijziging | Twee verbouwingen (`widget_turn()` en één schrijfstap) zonder verschil van betekenis: 88 en 90 van 109 gevallen gaven dezelfde soort reactie, binnen de spreiding tussen rondes. | Uitgerold |
| O4 | 13 okt | De interne chat op dezelfde stroom doet wat de widget deed: minder verzonnen zinnen, zonder antwoorden te verliezen, ook in de open modus die nog nooit gemeten was | Op Open daalden de onbewezen zinnen per gecontroleerd antwoord van 3,8 naar 2,9 bij 45 tegen 43 antwoorden, tegen een mediane beurt van 5,2 in plaats van 4,3 s. | Uitrollen |
| O5 | 13 okt | Alles wat na O1 tot O4 geen aanroeper meer heeft gaat weg, zonder dat er iets bijkomt | Er ging +246/−3369 regels in 30 bestanden weg, en één widgetronde gaf in 90 van 109 gevallen dezelfde soort reactie als de ronde ervoor. | Uitgerold |
| O6 | 13 okt | Eén pad voor iedereen: LibreChat-gesprekken lopen door dezelfde straat als de widget en krijgen daarmee de zaak, de keuzestap, de routes en de meting erna | Alle 42 LibreChat-omgevingen staan op het portalpad, zonder fout in portal-api, LibreChat of Caddy in het uur erna; dat uur had nog geen interne beurten. | Uitgerold; hook eruit na een week |
| J (zoekdienst) | 13 okt | Het zoeken vindt het artikel vaker dan de keuzestap het te zien krijgt | Het bekende artikel stond in de gevonden passages in 33 en 34 van de 40 beurten maar werd gekozen in 27 en 28, omdat het bewijspakket drie bronnen hield en dubbele secties de plekken vulden (44 van 456). | Uitgerold in de zoekdienst |
| J (portal) | 13 okt | Met een bron per passage in plaats van drie ziet de keuzestap het artikel dat het zoeken al vond, en worden meer vragen beantwoord zonder meer te verzinnen | Antwoorden 84 en 81 tegen 74 en 74, zoals verwacht 73 en 74 tegen 66 en 71, grotendeels onbewezen 13 en 9 tegen 10 en 8. | Uitgerold |
| J (keuzestap) | 13 okt | Een opdracht die de variantregel vóór het oordeel uitschrijft, laat de keuzestap met acht passages weer doorvragen zonder antwoorden te kosten. | De variantregel gaf 10 van 18 verwachte vragen tegen 4, maar kostte antwoorden (143 van 198 tegen 153); een groter model hielp niet. | Niet gewijzigd |
| Variant per citaat | 14 okt | De keuzestap vraagt vaker door als ze per gecopieerde zin de variant (toestel, app, model, abonnement) benoemt waarvoor het artikel die zin geeft. | In het lab 11 van 18 verwachte vragen tegen 4, maar op de hele meetlat 2 tegen 1 van de zes in beide rondes, met drie onnodige vragen per ronde erbij. | Niet uitgerold |
| Opruimen na één pad | 14 okt | De code wordt leesbaar zonder dat het gedrag verandert. | Geen bestand op het chatpad boven 1.300 regels en de drie lange functies elk onder 120, terwijl de meetlat binnen de spreiding bleef (zoals verwacht 70 tegen 71 en 74). | Uitgerold |
| Snelheid zonder kwaliteitsverlies | 21 okt | Een widgetbeurt (mediaan 7,1 s live) wordt sneller zonder dat een stap minder leest of controleert. | De keuzestap op het snelle model gaf 177 van 198 antwoorden en 9 van 39 weigeringen tegen 152 en 15, en de zoekdienstvlag maakte de zoekstap niet sneller (3,3 tegen 2,9 s). | Niets uitgerold |
| Schrijfopdracht | 13 en 14 okt | De schrijver verzint minder bij als hij de stappen in de volgorde en de woorden van het artikel moet houden, zonder stap, oorzaak of voorbeeld van zichzelf | Op de widget gingen de afgekeurde zinnen van 26% en 25% naar 21% en 23% en de grotendeels onbewezen antwoorden van 13 en 9 naar 6 en 9; op intern Strict 2,6 tegen 2,9 onbewezen zinnen per antwoord. | Uitgerold op widget en intern Strict |
| N | 12 okt | De keuzestap doet op intern Strict wat ze op de widget deed: minder verzonnen antwoorden, zonder goede te verliezen | Op 61 interne beurten gingen de reparaties van 17 naar 7 en de onbewezen uitspraken van 133 in 48 gecontroleerde antwoorden naar 62 in 28, bij een mediaan van 3,6 tegen 5,1 s. | Uitrollen op intern Strict |
| Eén pad voor partners | 14 okt | Elke beurt die niet van de interne chat is, loopt door `widget_turn()`, zodat er geen tweede generatiepad naast blijft bestaan. | De enige actieve widget liep al over dit pad en de zes widgets zonder schakelaar hadden in twee weken geen beurt, dus een meetronde was niet nodig. | Uitgerold; generieke generatie weg |
| Keuzestap zonder uitzonderingen | 21 okt | Als beide wegen langs de keuzestap sluiten, leest de schrijver alleen de regels die de keuzestap aanwees en geeft een beurt zonder keuze op de widget het eerlijke "niet gevonden". | Afgekeurde zinnen 19% en 19% tegen 21% en 23%, grotendeels onbewezen 5 en 8 tegen 6 en 9, tegen drie antwoorden meer waar de eigenaar "niet gevonden" verwachtte (10 tegen 7). | Uitgerold |
| – | 21 okt | Geen te meten gedrag: de schrijver krijgt de datum van vandaag als feit in zijn prompt, en de vaste contactreactie noemt het e-mailadres en telefoonnummer dat de beheerder bij de widget invult | Niet gemeten: de datum is een feit en de kanalen zijn instelling; zonder kanalen is de tekst byte-voor-byte gelijk aan die van daarvoor, wat een test vastlegt | Samengevoegd zonder meting |
| Zaak als geheugen en citaat | 21 okt | De zaak onthoudt het gesprek (citaat voor de contactroute, gekozen passages, één taal), zodat drie soorten fouten van de meetlat verdwijnen zonder dat goede antwoorden sneuvelen. | De verwachte antwoorden in beide rondes stegen naar 60 van 81 (was 56 en 57), de valse doorverwijzingen zijn weg (1 en 1 tegen 4 en 4) en afgekeurde zinnen bleven op 22% en 20%. | Uitgerold |
| LiteLLM-hook weg | 21 okt | Als het portalpad zich een week op echt verkeer bewijst, kan de hook weg zonder dat een gebruiker er iets van merkt. | Sinds 13 oktober 14:01 UTC draaien alle omgevingen op het portalpad, met 102 interne beurten op de eerste werkdag en 236 in de week erna, zonder één fout van de upstream. | Verwijderd; de terugweg is git |
| Intern uit gekozen regels | 21 okt | Minder antwoorden uit het verkeerde artikel, geen harde feiten zonder melding en geen doorvraag die de medewerker niet kan beantwoorden. | De tweede variant (passages blijven met een melding) gaf automatisch goed 26 en 26 van 31 tegen 22 tot 26 in de nulmeting; de eerste, zonder passages, viel 21 en 19 keer uit op het budget en is teruggedraaid. | Uitgerold in de tweede variant |
| Keuzestap in interne woorden | 22 okt | De keuzestap zoekt voor de widget de zin die een bezoeker vertelt hoe iets moet; een medewerker geeft een klantvraag door, vraagt wat iets is of of het bestaat, of vraagt een tekst uit het gesprek te herschrijven, dus krijgt de interne chat een eigen opdracht voor dezelfde stap. Verwacht: minder onterecht "staat er niet in" zonder verlies van goede antwoorden | Interne meetlat, twee rondes tegen twee nulmetingsrondes op main van dezelfde middag: "staat er niet in" 5 en 3 van 37 tegen 8 en 12; automatisch goed 25 en 26 van 31 tegen 25 en 25; kennisbankantwoorden 20 en 21 van 21 tegen 21 en 20; onbewezen uitspraken 98 en 60 tegen 66 en 68; gemarkeerde harde feiten 3 en 3 tegen 4 en 4; een beurt 6,5 en 6,4 s mediaan tegen 6,7 en 6,3. De keuzestap kiest nu passages op de beurten die een mail uit het vorige antwoord vragen en op de privacyvraag, die eerlijk "de kennisbank bevat geen uittreksel van de verwerkersovereenkomst" kreeg; de Windows-app-vraag blijft in alle rondes "staat er niet in" terwijl de schrijver haar toch goed beantwoordt | Uitgerold: het oordeel klopt vaker en de verwachte antwoorden blijven; het aantal onbewezen uitspraken was in één van de twee rondes slechter (98 tegen 66 en 68) en in de andere beter (60), dus die maat is niet bewezen gelijk |
| Eén schrijver | 22 okt | Er is nog één schrijver: `write_reply` schrijft ook de interne Open- en general-beurt, en streamt die zoals de live schrijver deed. | Interne meetlat: automatisch goed 27 en 25 van 31 tegen 24, onbewezen uitspraken 82 en 73 tegen 107; de widgetmeetlat bleef binnen de spreiding (zoals verwacht 71 en 75 tegen 74 en 71). | Uitgerold; tweede schrijver weg |
| Melding aan de schrijver weg | 22 okt | De melding die de schrijver in intern Open kreeg als de keuzestap niets koos, was een tweede laag over de oorzaak die de interne keuzestap-opdracht een dag later wegnam; zonder melding hoort de meting gelijk te blijven. | Interne meetlat, twee rondes tegen de twee rondes van de keuzestap in interne woorden: automatisch goed 25 en 26 van 31 tegen 25 en 26; kennisbankantwoorden 21 en 21 van 21 tegen 20 en 21; "staat er niet in" 5 en 5 tegen 5 en 3; onbewezen uitspraken 79 en 70 tegen 98 en 60. | Melding weg; de poort die per chatpadwijziging een rij hier eiste is ook weg, een rij komt er als er gemeten is |
| Zaak in de interne chat | 22 okt | De interne chat onthoudt per gesprek de zaak en de passages die de keuzestap koos, zoals de widget, zodat een vervolgvraag het artikel van de vorige beurt weer aangeboden krijgt; op de meetlat, die geen eerdere beurten naspeelt, hoort de uitkomst gelijk te blijven. | Interne meetlat, drie rondes tegen een nulmeting op main in hetzelfde uur: automatisch goed 25, 25 en 25 van 31 tegen 23; kennisbankantwoorden 20, 20 en 20 van 21 tegen 19; "staat er niet in" 5, 6 en 5 tegen 6; onbewezen uitspraken 52, 71 en 66 tegen 67. | Gebouwd; of het geheugen gebruikt wordt, meet het record live (`case_remembered`) |
| – | 22 okt | Geen te meten gedrag: de widgetmeetlat loopt door dezelfde CLI als de interne (`meetlat run --surface widget`, door de route zelf met een voorbeeldsessie), en het privéscript dat haar tot nu toe draaide is weg | Niet gemeten: meetgereedschap, door tests vastgelegd | Samengevoegd zonder meting |
| Gespreks-id als sleutel van het geheugen | 23 okt | De zaak van de interne chat hangt aan het gespreks-id dat LibreChat meestuurt in plaats van aan digests van de berichten, en de meetlat speelt de beurten vóór een genoemde beurt na, zodat een vervolgbeurt de zaak leest zoals in productie. Verwacht: gelijk of beter nu de vervolgbeurten op beide oppervlakken hun geheugen hebben | Interne meetlat, twee rondes tegen de twee nulmetingsrondes van 22 oktober: automatisch goed 26 en 26 van 31 tegen 25 en 26, kennisbankantwoorden 21 en 21 van 21 tegen 21 en 21, onbewezen uitspraken 63 en 85 tegen 79 en 70, en alle 14 vervolgbeurten lazen een zaak. Widgetmeetlat, één ronde tegen een ronde van main uit hetzelfde uur: 74 tegen 70 van 109 | Gebouwd; de LibreChat-config gaat vóór portal-api live |
| – | 22 okt | Geen te meten gedrag: het interne record bewaart voortaan het oordeel van de keuzestap en de nummers van de gekozen passages (geen citaten, geen vraag), zodat de interne meetlat de keuzestap per beurt kan afstellen | Niet gemeten: een signaalveld, door een test vastgelegd | Samengevoegd zonder meting |
| Eén controle achteraf | 22 okt | De twee controles na het antwoord geven één vorm terug (`answer_check.AnswerCheck`), zodat de voettekst en het record dezelfde uitkomst lezen. | Niet gemeten: het gedrag verandert alleen in één randgeval, waar passages en gebruikersberichten nu elk apart gelezen worden. | Gebouwd |
| – | 22 okt | Geen te meten gedrag: de vaste teksten van het chatpad staan in één tabel, een bijlage naast een bestand dat Klai niet leest wordt in één keer verwerkt, en het nepobject in de tests is van het datamodel afgeleid | Niet gemeten: de teksten en de bijlagenverwerking zijn vastgelegd door de bestaande tests, die ongewijzigd groen bleven | Samengevoegd zonder meting |
| – | 22 okt | Geen te meten gedrag: de docstrings van `widget_turn.py` en `internal_turn.py` verwijzen naar het ontwerp in §5a, en die van `internal_turn.py` zegt dat Open bij geen gekozen passage alle passages houdt met de melding aan de schrijver | Niet gemeten: alleen docstrings en een commentaarregel, geen code | Samengevoegd zonder meting |
| – | 21 okt | Geen te meten gedrag: de vaste Strict-weigering en de Open-hint bestaan nu ook in het Duits en Frans (een Duits gesprek kreeg de Engelse tekst), een zoekantwoord dat geen JSON is telt als onbereikbare kennisbank, en de alert op die fout ziet ook de afgewezen identiteit en negeert de E2E-organisatie | Niet gemeten: vaste teksten en een foutpad, door tests vastgelegd | Samengevoegd zonder meting |
| – | 21 okt | Geen te meten gedrag: een partnersleutel of widgettoken van een organisatie die zacht verwijderd is of waarvan de verwijdering loopt of mislukte, authenticeert niet meer, en de widget van zo'n organisatie geeft geen token meer uit | Niet gemeten: een toegangsregel, door vier tests vastgelegd die zonder de regel een context teruggeven | Samengevoegd zonder meting |

Wat dit voor het ontwerp van §5a betekent: twee aannames zijn gemeten en houden niet zoals ze er stonden. Het beslismoment (stap 3 van het ontwerp) kan met ons model geen harde poort zijn, en de controle in code (stap 5 van het ontwerp) vangt maar een deel. Het ontwerp blijft de richting, maar de vervanging van de reparatie (stap 9) kan niet op deze twee leunen en hangt af van het oordeel van de eigenaar over concept tegenover gerepareerd antwoord.

Over alle gevallen samen bleef de soort reactie op gevallen die de eigenaar goed noemde gelijk, in beide rondes. De meetruns draaiden op het Vibe-tegoed met hetzelfde model als het antwoordmodel; de controles kregen daar meer tijd dan in productie, omdat die sleutel trager antwoordt.

## 8a. Metingen per stap, in detail

De tabel in §8 geeft per stap de kop; hier staat per stap de volledige tekst van de verwachting, de uitkomst en het besluit.

### Stap 1 (29 sep)

Verwachting: Een meetlat die de review weerspiegelt

Uitkomst: Eén geval per beoordeeld antwoord, met de verwachte soort reactie en waar bekend het dragende artikel; naspelen door de echte route, score in code. De soort reactie alleen zegt weinig: die klopte bij de meeste gevallen al, ook waar het antwoord fout heette. De inhoud vraagt het oordeel van de eigenaar per geval

Besluit: In gebruik; privé

### Stap 2 (29 sep)

Verwachting: Zien wat elke stap bijdraagt

Uitkomst: Nulmeting, twee rondes. De reparatie draait op ruim een derde van de beurten; in meer dan de helft daarvan verliest het antwoord stappen en de mediane lengte halveert ongeveer. Een op de zeven beslissingen verschilt tussen twee rondes op dezelfde vraag

Besluit: Reparatie is het eerste onderdeel om te vervangen (stap 9)

### Stap 7 (29 sep)

Verwachting: Geen excuus waar niets misging

Uitkomst: Het voorbeeld uit het profiel en de excuusregel uit de frustratie-instructie zijn weg. Eerste antwoorden die met een excuus openen: van enkele per ronde naar nul op de beurten met een negatieve toon, waar ze allemaal vandaan kwamen

Besluit: Uitgerold

### Stap 10 (29 sep)

Verwachting: Ons kleine model kan zien of de gevonden passages de vraag beantwoorden

Uitkomst: Eerste proef, één ronde, één gesloten vraag per geval, zonder productcode. Waar de eigenaar "terecht niet gevonden" zei, zeiden het kleine en het middelgrote model dat bijna altijd ook. Van de verkeerd gelezen vragen herkende het kleine model ruim de helft en het middelgrote driekwart als "staat niet in deze passages". Maar van de antwoorden die de eigenaar goed noemde, keurde het kleine model een derde af en het middelgrote een kwart; op eerste beurten een kwart en een achtste. De twee modellen waren het in zeven op de tien gevallen eens

Besluit: Niet bouwen als poort. Wel bruikbaar als signaal naast de score: het ziet verkeerd gelezen vragen die de score mist. Eerst de gevallen lezen waar proef en oordeel van de eigenaar botsen

### Stap 11 (29 sep)

Verwachting: Controle in code op harde feiten kan de controle per zin vervangen

Uitkomst: Gemeten op de nagespeelde antwoorden: een vijfde van de zinnen die de controle afkeurt bevat een hard feit (getal, link, naam van een menu of knop); de rest is een bewering in gewone woorden die code niet kan toetsen. Van de harde feiten in antwoorden staat een derde niet letterlijk in de gevonden passages, waarbij andere schrijfwijzen meetellen

Besluit: De controle in code dekt een smal deel en kan de controle per zin niet vervangen. Bruikbaar als extra, goedkope toets op verzonnen getallen en namen

### Stap A (30 sep)

Verwachting: Een vervolgbeurt vindt het juiste artikel als de zoekvraag het onderwerp van het gesprek meeneemt

Uitkomst: Eerst alleen het zoeken, op alle vervolgbeurten uit de review. De herschrijving van de interne chat in plaats van de huidige zoekvraag: evenveel gewonnen als verloren. Het onderwerp als extra zoekgang ernaast (de eerdere woorden van de bezoeker voor de laatste zin, plus die herschrijving): volgens een beoordelend model vaker een passage die de vraag beantwoordt, ruim boven de ruis van een herhaalde meting. Daarna gebouwd en van begin tot eind nagespeeld, twee rondes met en twee zonder: de soort reactie verschilt niet meer dan de rondes onderling, het aantal beurten met zwakke bronnen halveert bijna, en een beurt duurt een halve tot een hele seconde langer. Bij het lezen van de antwoorden waar het zoeken won, was het antwoord één keer beter en meerdere keren slechter: de extra zoekgang haalt hoog scorende passages binnen over hetzelfde onderwerp maar een andere vraag (inkomend waar uitgaand gevraagd is, de app waar het om een bureautelefoon gaat), en het model schrijft daar dan een stellig antwoord uit waar eerst eerlijk "niet gevonden" stond

Besluit: Niet uitgerold. De bottleneck op vervolgbeurten is niet dat er te weinig gevonden wordt, maar dat niets op het pad ziet dat een hoog scorende passage een andere vraag beantwoordt. Dat is stap E, en die schuift naar voren

### Stap F (30 sep)

Verwachting: Zonder de reparatie houdt een afgekeurd antwoord zijn stappen

Uitkomst: De reparatie op de helpwidget is vervangen door een vaste regel in code: het antwoord gaat uit zoals geschreven met de afspraak eronder, en wordt alleen de weigering als geen enkele uitspraak onderbouwd is of één een artikel tegenspreekt. Nagespeeld op alle beurten waar de reparatie eerder draaide: evenveel weigeringen als met reparatie, in ruim twee derde het volledige concept met de zin over de afspraak, de mediane lengte een zesde hoger, en een beurt gemiddeld een seconde sneller omdat een modelaanroep vervalt. Gelezen: waar de reparatie een procedure tot losse koppen had teruggebracht, staat de procedure er weer. Wat blijft: een onbewezen zin wordt niet meer weggehaald, en een gat in de brontekst ("Ga naar.") is nu zichtbaar in het antwoord

Besluit: Uitgerold op de widget; de interne chat houdt de reparatie tot stap G

### Kiezen vóór schrijven (30 sep)

Verwachting: Als een stap vóór het schrijven kiest welke passages de vraag beantwoorden, verzint het model niet meer

Uitkomst: **Oorzaak gemeten.** In bijna de helft van de beurten waarin het model schreef, bevatte geen enkele passage het antwoord, en in de meerderheid daarvan was de score hoog genoeg om geen waarschuwing te geven.

**Proef op dezelfde passages, vier varianten.** Alleen opschonen in code (dubbel, losse feitzinnen, ander product) hielp niet en kostte antwoorden waarvan zo'n passage de enige bron was. Kiezen plus een opdracht zonder de slotzin: antwoorden die grotendeels onbewezen waren gingen van een vijfde naar bijna nul.

**De keuzestap los bewezen.** Een eerste versie die eerst het oordeel gaf en dan het bewijs, weigerde beantwoordbare vragen in ruim een kwart van de herhalingen. In de volgorde behoefte, letterlijk citaat, oordeel, met het citaat in code teruggezocht in de passage, waren de gelezen gevallen bij elke herhaling goed, op één wisseling na. Op alle opgeslagen beurten waar de stap niet op is afgesteld, maakte ze van geen enkel antwoord dat de eigenaar goed noemde een weigering; de vijf gevallen waar dat zo leek, waren beurten waar het goede antwoord zelf "niet gevonden" was.

**Gekoppeld op een kleine set.** Drie botsingen gevonden en opgelost: de regel na de controle per zin maakte van een goed antwoord alsnog een weigering (na het schrijven verandert op de widget nu niets meer); de schrijver stelde de gevraagde vraag in de helft van de gevallen niet (de vraag van de keuzestap is nu zelf de reactie); een verzoek om een afspraak kreeg "niet gevonden"

Besluit: Gebouwd en klein bewezen. De meting op de hele meetlat, twee rondes naast twee rondes van het huidige pad, volgt in de regel hieronder

### Kiezen vóór schrijven, hele meetlat (30 sep)

Verwachting: Met de definitieve code verzint het pad minder, zonder goede antwoorden te verliezen. Stoppen als een antwoord dat de eigenaar goed noemde in beide rondes een weigering wordt terwijl het huidige pad het wel beantwoordt

Uitkomst: Twee rondes van het huidige pad naast twee rondes van het nieuwe, elk op de hele meetlat. Antwoorden die grotendeels onbewezen zijn: van ruim een vijfde en een achtste naar een twintigste en een tiende van de gecontroleerde antwoorden, in beide rondes lager dan de beste ronde van het huidige pad. Het aandeel afgekeurde zinnen gaat van een derde naar een vijfde; antwoorden met hooguit één afgekeurde zin van de helft naar driekwart. De prijs: het pad schrijft minder antwoorden en zegt vaker "niet gevonden" (van een kwart tot een derde van de beurten naar ruim een derde), en een geschreven beurt duurt ruim een seconde langer. Het stopcriterium is nagelezen: in vijf gevallen leek een goed antwoord een weigering te worden; in alle vijf was het antwoord van het huidige pad zelf "niet gevonden", of verzonnen uit passages over iets anders. De proef zonder de vormregels uit de eigen instructie van de widget gaf op de hele meetlat geen extra winst. De eerste twee grote rondes, vóór het koppelen op kleine sets was afgerond, lieten geen betrouwbaar verschil zien: de winst zit in de vijf botsingen die klein gevonden zijn

Besluit: Uitrollen. Daarna het zoeken: elke beurt waar de keuzestap "staat er niet in" zegt terwijl de kennisbank het antwoord heeft

### Echt verkeer, eerste week (12 okt)

Verwachting: Op echt verkeer doet de keuzestap wat ze in de meting deed, en wat overblijft wijst naar het zoeken

Uitkomst: Alle gesprekken van 5 tot 12 oktober gelezen (de widget bewaart zeven dagen): 58 gesprekken, 118 beurten. De keuzestap draaide 139 keer, viel 6 keer uit op tijd (vier seconden) en oordeelde 85 keer "beantwoordt", 51 keer "staat er niet in", 3 keer "hangt af van". Een derde van de beurten kreeg het vaste "niet gevonden", in 27 van de 58 gesprekken was dat de laatste beurt. Gelezen: ongeveer twee derde van de beurten is goed of eerlijk, tegenover ruim een derde in de review van september (één lezer, niet de eigenaar). Verzonnen procedures komen nog voor (vier beurten) maar zijn niet meer de grootste fout. Wat nu het meest misgaat, in volgorde: (1) een vervolgbeurt die het eerdere antwoord corrigeert of verfijnt ("dit werkt niet", "waar in beheer?", "als ingaand nummer") krijgt "niet gevonden", ruim twintig keer; (2) een eerste vraag die de kennisbank wel beantwoordt krijgt "niet gevonden" omdat het zoeken het artikel niet aanlevert (belrechten, internationaal bellen, gespreksopname verwijderen, een collega toevoegen), zes tot acht keer; (3) het linkfilter haalt het adres weg dat het antwoord is ("Klik op.", een leeg codeblok, een bezoeker die twee keer om de API-url vraagt en niets krijgt), zes beurten; (4) een telefoonnummer uit een artikel over een ander onderwerp wordt als supportnummer gegeven, drie keer, en één keer een verzonnen nummer; de controle keurt die zin af maar niets handelt ernaar; (5) "ik wil jullie mailen" en "medewerker spreken" krijgen "niet gevonden" of de losse zin "Laat het gerust weten als je vastloopt", die uit het voorbeeld in het profiel komt; (6) een eenwoords vervolg ("webphone", "Huis telefoon") verliest het onderwerp. De duur per geschreven beurt: mediaan 6,2 s, negen op de tien onder 9,2 s

Besluit: Volgorde hieronder aangepast: het zoeken op vervolgbeurten eerst, dan het linkfilter en de telefoonnummers

### Stap H (klein) (12 okt)

Verwachting: De zaak draagt het onderwerp van het gesprek de zoekvraag in, zodat een vervolgbeurt het juiste artikel vindt

Uitkomst: Los onderdeel, op alle 111 vervolgbeurten (51 uit de review, 60 uit oktober). De zaak-stap schreef voor elke beurt een zaak; haar inschatting van wat de bezoeker wil kwam op de contactverzoeken uit oktober overeen met het lezen ("ik wil jullie mailen" contact, "ik heb nu hulp nodig" mens, "wanneer had ik mijn afspraak" afspraak). Zoeken met de zaak-zoekvragen als extra gangen naast de letterlijke zin: de keuzestap vond een passage die antwoordt in 77 beurten tegen 59 met alleen de letterlijke zin, 24 gewonnen en 6 verloren; met de zaak-zoekvraag als hoofdvraag 72, 24 gewonnen en 11 verloren, dus de woorden van de bezoeker blijven de hoofdvraag. Gelezen: van acht winsten waren vijf het juiste artikel (belrechten in Beheer, IP-adres verkrijgen, proxy voor de trunk, nummerweergave in de Android-app), twee een lijkend artikel dat de keuzestap toeliet, één een contactverzoek. Van de verliezen waren de meeste wisselingen van de keuzestap op dezelfde passages, en twee een terecht "niet gevonden"

Besluit: Gebouwd: zaak-stap, opslag bij de beurt, zoekvragen als extra gangen op vervolgbeurten, contactverzoeken naar de route voor een mens. Gekoppeld bewezen op veertien gevallen; de meting op de hele meetlat volgt

### Stap H en K, hele meetlat (12 okt)

Verwachting: Met de zaak en de contactroute krijgen vervolgbeurten vaker een antwoord en contactverzoeken de afspraak, zonder dat goede antwoorden verloren gaan

Uitkomst: Twee rondes naast twee rondes van het pad met alleen de keuzestap. Vervolgbeurten (51): een antwoord in 28 en 30 beurten tegen 18 en 24, "niet gevonden" in 16 en 19 tegen 25 en 30. De antwoorden die de eigenaar goed noemde: 24 en 25 beantwoord tegen 21 en 22; geen enkel goed antwoord werd in beide rondes een weigering. De contactroute vuurde 9 en 10 keer, telkens terecht gelezen. Gelezen waar een vervolgbeurt in beide rondes van "niet gevonden" naar een antwoord ging: drie keer was dat de contactroute, één keer een bruikbaar antwoord over versleuteling, één keer netwerkadvies dat deels naast het artikel lag. De winst op het zoeken is dus kleiner dan de losse proef suggereerde, en antwoorden die grotendeels onbewezen zijn liggen iets hoger (8 en 5 tegen 3 en 5): breder vinden brengt ook lijkende passages binnen die de keuzestap toelaat. Een geschreven beurt duurt bijna een seconde langer door de zaak vóór het zoeken

Besluit: Uitrollen. Daarna de keuzestap scherper op "zelfde onderwerp, andere vraag" (stap J), want dat is nu de rem op breder zoeken

### Stap I (12 okt)

Verwachting: Een adres dat in het artikel staat bereikt de bezoeker; een telefoonnummer of e-mailadres dat niet in de gekozen passages staat, bereikt hem niet

Uitkomst: Gebouwd als code na de bronnencomposer. De zes beurten uit oktober nagespeeld: de bezoeker die twee keer om het API-adres vroeg krijgt het nu (het adres stond in het artikel tussen aanhalingstekens, en het patroon dat adressen herkent nam dat aanhalingsteken mee, zodat het nooit op het artikel paste); de beurten met het Belgische nummer liepen in deze ronde via de contactroute of schreven het nummer niet, dus de regel zelf is bewezen met tests: een nummer uit de passage blijft, een nummer van elders neemt zijn zin mee. Geen effect op de bronkaart

Besluit: Uitgerold

### Stap J (keuzestap scherper) (12 okt)

Verwachting: Een opdracht die "zelfde apparaat, ander symptoom" expliciet uitsluit, wijst lijkende artikelen vaker af zonder goede te verliezen

Uitkomst: Los gemeten op de gelezen gevallen plus vier lijkende artikelen uit de sporen (initializing voor niet-uitbellen, klik-en-bel voor buitenlandse nummers, status offline voor een offline toestel, DECT voor inkomend nummer), drie herhalingen: de huidige opdracht 36 van 39 goed, de scherpere ook 36 van 39, en dezelfde twee gevallen fout (de DECT-passage wordt in beide als antwoord gelezen). Geen winst

Besluit: Niet gewijzigd. Het lijkende artikel dat de keuzestap toelaat is een rest van ongeveer een op de twintig gevallen; eerder lonend zijn de artikelen die het zoeken niet aanlevert

### Stap L (12 okt)

Verwachting: Een weekreview kan zonder naspelen

Uitkomst: Het record van elke widgetbeurt bewaart nu het spoor: de zoekvraag en de extra zoekgangen, de gevonden passages (titel, url, score), het oordeel van de keuzestap met de geciteerde zinnen en de vraag, en de zaak; ook bij een vaste reactie, omdat die hetzelfde record deelt. Geen gedragswijziging, met een test op het record

Besluit: Uitgerold. De weekreview leest voortaan het record

### Stap M (12 okt)

Verwachting: Minder aanroepen en een snellere beurt zonder verlies: de zaak vervangt de vraagbeoordelaar, de herformuleringen en de regel voor zwakke bronnen gaan eruit, de controle per zin na het antwoord

Uitkomst: **Los gemeten vooraf.** Vraagbeoordelaar tegen zaak op 215 opgeslagen beurten: gespreksklasse in alle 215 gelijk, toon in 197, onderwerp buiten de widget in 108 van 109. Herformuleringen op 58 eerste beurten: bekend artikel in de passages 19 van 21 met herformuleringen, 18 van 21 met zaak-zoekvragen, 18 van 21 zonder; antwoorden 41, 40 en 43. Echt verkeer eerste week: zwakke-bronnenregel vuurde 3 van 104 beurten, alle drie waar de keuzestap niets koos; de twee controles samen 1,6 s mediaan van een geschreven beurt van 6,2 s, de lichte beoordelaar alleen 0,4 s.

**Hele meetlat, drie keer twee rondes tegen de twee rondes van stap I** (soort reactie zoals verwacht 66 en 71 van 109; grotendeels onbewezen 8 en 5; afgekeurde zinnen 25% en 20%; geschreven beurt mediaan 6,7 en 6,6 s). Zonder herformuleringen en zonder zaak-zoekvragen op de eerste beurt: 60 en 56 zoals verwacht, 6 en 7 grotendeels onbewezen, 22% en 25%, 4,7 en 4,4 s; twee van de zes verwachte verduidelijkingsvragen werden in beide rondes een antwoord. Met de zaak vóór het zoeken op elke beurt: 63 en 59, 6 en 12, 19% en 25%, 5,7 en 5,4 s; dezelfde twee vragen bleven weg, en de zaak gaf drie keer per ronde te veel "buiten het onderwerp". Gelezen: de zaak-zoekvragen versmallen (product, probleem, variant), de herformuleringen verbreden, en die breedte bracht de secties van beide varianten binnen zodat de keuzestap kon vragen welke geldt; de valse doorverwijzingen waren contactverzoeken ("kan ik jullie ook gewoon bellen"). Een strengere formulering van `out_of_scope` was los gemeten slechter (3 van 6 oordelen van de beoordelaar gemist tegen 1).

**Definitief** (herformuleringen op de eerste beurt gehouden, zaak-zoekvragen op vervolgbeurten, contactverzoek wint van de doorverwijzing): 71 en 64 zoals verwacht, 10 en 6 grotendeels onbewezen, 25% en 20% afgekeurde zinnen, de zes verwachte vragen weer 10 en 9 keer gesteld, geschreven beurt 5,3 en 5,4 s (1,3 s sneller, een modelaanroep minder op elke beurt en de controle per zin van het pad). Beide rondes anders dan beide rondes van I: vier slechter (twee contactverzoeken die nu de vaste contacttekst krijgen in plaats van de doorverwijzing, met dezelfde knop; twee beurten waar de keuzestap "beantwoordt" zei maar de geciteerde zin niet in de aangewezen passage stond, bij zeven bijna gelijke passages van hetzelfde artikel), twee beter.

**Reviews:** verificatie-subagent en Sol (1 high, 4 medium, 3 low bevestigd en gefixt: een antwoord zonder bronvermelding houdt de controle op het pad; het record wordt direct geschreven en daarna aangevuld), en Codex op het hoogste redeneerniveau: de keuze van passages garandeert geen enkele geschreven bewering (het bekende plafond, gelijk aan vóór deze stap), en het aanvullen van het record gaat sinds de fix op de eigen rij-id

Besluit: Uitrollen. De brede modus als apart pad blijft, met reden: als de kennisbank niets heeft, kiest de bezoeker zelf voor een antwoord uit algemene kennis, en dat staat er dan bij. Volgende zoekwinst: een geciteerde zin die in een andere dan de aangewezen passage staat (twee gevallen in beide rondes)

### Stap M en N, nazorg (13 okt)

Verwachting: Drie dingen die in de meting van M en N bleven liggen, nu gefixt in plaats van later: een citaat op het verkeerde passagenummer, "staat er niet in" mét een teruggevonden citaat, en de reparatie achteraf op de interne chat

Uitkomst: **Citaat in een andere passage.** Het zoeken levert tot zeven bijna gelijke secties van één artikel; het model kopieert de juiste zin en zet er soms het verkeerde nummer bij. Code zoekt het citaat nu eerst in de aangewezen passage en anders in de best passende sectie van hetzelfde artikel (zelfde bron-url), nooit in een ander artikel (de reviews wezen op een gelijkluidende zin over een ander product en op twee artikelen met dezelfde titel).

**Teruggevonden citaat weegt zwaarder dan het oordeelwoord.** In vijf rondes (I en M) schreef de keuzestap 39 keer "staat er niet in" met een zin die wél in een passage stond; de eigenaar verwachtte daar 31 keer een antwoord, 6 keer "niet gevonden", 2 keer een vraag. Nu is zo'n beurt een antwoord uit die passages, met de opdracht te zeggen wat de artikelen niet dekken; alleen zonder een citaat van minstens vijf woorden dat als één zin in de passage staat telt het oordeelwoord (een paar gewone woorden staan overal, en losse woorden over drie zinnen zijn geen citaat). Eén ronde op de hele meetlat: de negen beurten die het raakte kregen alle negen het verwachte antwoord (0 tot 3 afgekeurde zinnen elk, de headset-beurt uit de meting van M weer goed); over de hele ronde 72 antwoorden en 25 "niet gevonden" tegen 65 tot 68 en 27 tot 31 in de drie rondes ervoor, zoals verwacht 68, grotendeels onbewezen 5, schone antwoorden 39 van 57 (twee rondes ervoor 33 van 52 en 36 van 50), afgekeurde zinnen 22%, geschreven beurt 5,8 s mediaan.

**Reparatie achteraf intern eruit** (met de aanroep, het budget en de tijdslimieten). Interne meetlat opnieuw, dezelfde twintig gesprekken op Strict: nul reparaties tegen zeven, onbewezen zinnen per gecontroleerd antwoord 2,3 tegen 2,2, de zeven eerder herschreven antwoorden gaan uit zoals geschreven (vier daarvan over geplakte mails en API-koppelingen, waar de "onbewezen" zinnen de eigen inhoud van de medewerker zijn)

Besluit: Uitgerold. De brede modus blijft, met reden: de bezoeker kiest zelf voor algemene kennis als de kennisbank niets heeft

### Stap O2 (13 okt)

Verwachting: De keuzestap kan in dezelfde aanroep zeggen of het bewijs alles of een deel dekt, zodat de afspraakknop uit code komt en de lichte beoordelaar eruit kan

Uitkomst: Keuzelab, de twintig gelezen gevallen, drie herhalingen, huidige opdracht naast dezelfde opdracht met een veld `covers: all/part/none` erachter: harde gevallen goed 36 van 39 tegen 33 van 39. Het extra veld verschuift het oordeel zelf: twee gevallen die terecht "staat er niet in" waren worden "beantwoordt, deels", en een derde wisselt. Het veld dat de knop zou dragen kost dus precies wat de keuzestap moet bewaken

Besluit: Niet gedaan. De lichte beoordelaar blijft (0,4 s, alleen de knop); een knop onder elk antwoord zou de aanroep ook schrappen, maar dat is een zichtbare keuze voor de eigenaar

### Stap O1 en O3 (13 okt)

Verwachting: De stroom van §5 wordt de drager zelf, zonder gedragswijziging

Uitkomst: Twee sneden door eigen uitvoerders in een eigen worktree (Opus voor de drager, Sonnet voor de compositie), elk met de volledige suite, ruff en pyright groen. O1: `widget_turn()` van 464 regels met de stappen als functies van hooguit zestig regels; `chat_completions` van 762 naar 295. O3: één schrijfstap (`write_reply`, 491 regels) en één uitzender (128); de widgettakken van de streaming- en niet-streaminggeneratie, `off_topic_response`, `off_topic_stream` en het terugparsen van frames zijn weg; een bytetest vergelijkt de frames en de JSON-body met wat de oude code maakte. Meting: één ronde per snede op de hele meetlat tegen de ronde ervoor: 88 en 90 van 109 dezelfde soort reactie (de wissel tussen twee rondes van dezelfde code lag eerder ook rond 85 tot 90), zoals verwacht 70 en 66 tegen 68, grotendeels onbewezen 8 en 10 tegen 5 (de band over alle rondes is 5 tot 12), geen fouten, alle signalen compleet. Reviews: verificatie-subagent en Sol vonden geen verschil van betekenis; vier kleine wel, drie gefixt (afgebroken antwoord houdt zijn `finish_reason`, een niet-JSON 200 is een upstreamfout in plaats van een 500, de taalmeting leest de woorden van de bezoeker), één aanvaard (één aanroep met 120 s in plaats van twee budgetten). Nog niet weg: het terugparsen van frames voor het record in de streamwrapper (naar O5)

Besluit: Uitgerold. De verbouwing kost geen kwaliteit en geen tijd; wat ze oplevert is dat elke volgende stap in de stroom belandt en niet in een vlag

### Stap O4 (13 okt)

Verwachting: De interne chat op dezelfde stroom doet wat de widget deed: minder verzonnen zinnen, zonder antwoorden te verliezen, ook in de open modus die nog nooit gemeten was

Uitkomst: Eigen uitvoerder (Opus) in een eigen worktree; suite, ruff en pyright groen; +730/−1207 regels, `query_rewrite.py` (403 regels) en `clarify_decision.py` weg. Interne meetlat, dezelfde twintig gesprekken, nulmeting tegen nieuw.

**Open** (de meerderheid van de medewerkers; zes van de twintig gesprekken zijn van Strict-gebruikers): antwoorden 45 tegen 43, weigeringen 10 tegen 13 (alle van Strict-gebruikers), twee verduidelijkingsvragen waar er geen waren, onbewezen zinnen per gecontroleerd antwoord 3,8 tegen 2,9, mediaan 4,3 tegen 5,2 s (de zaak en de keuzestap erbij). Gelezen waar de daling het grootst was: het API-antwoord gebruikt nu het echte eindpunt en de echte velden in plaats van verzonnen namen, "tijdelijk doorschakelen" is nu de inhoud van het artikel in plaats van een verzonnen procedure per platform, de webhook-uitleg volgt het artikel.

**Strict** (profiel op Strict gezet): antwoorden 25 tegen 26, weigeringen 27 tegen 27, twee vragen, onbewezen zinnen per antwoord 2,3 tegen 2,1, mediaan 5,0 tegen 4,4 s.

**Reviews** (verificatie-subagent 3 laag, Sol 1 hoog en 4 middel): gefixt dat een toolaanroep mét tekst op de stream geen afsluitreden kreeg, dat het zoeklogboek pas na de routes werd geschreven (feedback op een vraag of weigering bond aan de vorige beurt), dat een beurt met alleen een toolaanroep geen beslissing en tijd in het record kreeg, dat een afgedwongen webzoektool na het strippen in `tool_choice` bleef staan, en dat de voettekst ook kwam als de client geen bronnen vroeg. Bewust zo gelaten, na het lezen van de antwoorden: Open krijgt bij gekozen passages alleen die passages en de opdracht, want dat is precies wat de verzonnen namen en procedures weghaalde

Besluit: Uitrollen. Geen keepalive-frames meer op een gestreamde Strict-beurt; als LibreChat of de proxy daarop afbreekt, is dat zichtbaar als 502 of afgebroken stream in de logboeken en komt er een timer-keepalive

### Stap O5 (13 okt)

Verwachting: Alles wat na O1 tot O4 geen aanroeper meer heeft gaat weg, zonder dat er iets bijkomt

Uitkomst: Eigen uitvoerder (Sonnet), twee commits, +246/−3369 regels in 30 bestanden: de vraagpoort op artikeltitels met haar evaluatiescript, de duidelijkheidsinvoer en de uitkomst "verduidelijkingsvraag" van `decide_answer`, dubbele helpers voor zoeklogboek en laatste bericht, de oude vastgehouden interne voettekst, de kolom "gerepareerd" in het dagrapport en het reparatiesignaal op de statuspagina, en de oude citatiemodus "links" (de zuiveraars, acht reguliere expressies, de parameters erbij) die sinds de bronnen op documentniveau nooit meer bereikt werd. Blijft, met reden: de lichte beoordelaar (O2), het terugparsen van frames in de auditwrapper (de widget zonder support-modus streamt nog door de generieke generatie), en twee helpers voor het laatste bericht die anders omgaan met een lijst als inhoud. Suite 4920 groen (van 4970: de tests van het verwijderde gingen mee), ruff en pyright schoon. Meting: één widgetronde tegen de ronde ervoor, 90 van 109 dezelfde soort reactie, zoals verwacht 71 tegen 66, grotendeels onbewezen 8 tegen 10, geen fouten; de interne meetlat is niet opnieuw gedraaid, want geen interne stap veranderde. Reviews: verificatie-subagent nul bevindingen na het natrekken van elke productieaanroeper (de oude modus was onbereikbaar, de opdracht aan het model is byte voor byte gelijk gebleven), Sol één lage (een verwijderde logregel stond nog in de systeembeschrijving, gefixt)

Besluit: Uitgerold

### Stap O6 (13 okt)

Verwachting: Eén pad voor iedereen: LibreChat-gesprekken lopen door dezelfde straat als de widget en krijgen daarmee de zaak, de keuzestap, de routes en de meting erna

Uitkomst: Alle 42 LibreChat-omgevingen op het portalpad gezet met `scripts/switch_internal_chat.py <slug> portal` (twee stonden er al: de e2e-omgeving en getklai), eerst één omgeving met daarna vijf minuten kijken, dan de overige 39 één voor één; elke container opnieuw aangemaakt en gezond gemeld, elke `.env` nagelopen op het portaladres, geen fout in portal-api, LibreChat of Caddy in het uur erna. De E2E-run op de productieomgeving van dezelfde middag liep al over dit pad. Het eerste uur na de omzetting had geen interne beurten (geen gesprekken in dat uur), dus de eerste echte meting komt uit de interne records van de komende dagen: beslissing, onbewezen zinnen, vragen en weigeringen per modus, naast de nulmeting van O4 (Open 3,8 onbewezen zinnen per antwoord, Strict 2,3). De LiteLLM-hook blijft staan als terugweg (`switch_internal_chat.py <slug> litellm`) tot die meting er is; daarna gaat hij eruit, met zijn reparatie, citatiecode en kopieën van de prompts

Besluit: Uitgerold. Nog open: de hook eruit na een week echt verkeer, en een timer-keepalive voor Strict als de logboeken afgebroken streams laten zien

### Stap J (zoekdienst) (13 okt)

Verwachting: Het zoeken vindt het artikel vaker dan de keuzestap het te zien krijgt

Uitkomst: **Gemeten vooraf op de laatste twee widgetrondes.** Het bekende artikel stond in de gevonden passages in 33 en 34 van de 40 beurten met een bekend artikel (op eerste beurten 19 van 21), maar werd gekozen in 27 en 28. De keuzestap kreeg mediaan vijf passages uit mediaan drie artikelen, en 44 van 456 passages waren dezelfde sectie onder een ander kindstuk-id (de zoekdienst vervangt de tekst van elk kindstuk door die van de moedertekst). Oorzaak in de code: het bewijspakket houdt maar drie bronnen over (vijf bij meerdere kennisbanken), afgesteld op een schrijver die alle passages las, en de dubbelen vullen de acht plekken.

**Gedaan in de zoekdienst** (#1858): het best scorende kindstuk per moedertekst overleeft elke top-k-grens (hoofdzoekgang, extra zoekgangen en de gefuseerde lijst), en een aanroeper mag tot acht bronnen vragen (`max_sources`, opt-in; wie het niet meestuurt merkt niets aan de grens). Tests: één sectie één keer, de grens verbreedt tot acht en weigert negen; suite 601 groen

Besluit: Uitgerold in de zoekdienst. De portalhelft (acht bronnen vragen waar de keuzestap draait) volgt na twee meetrondes tegen deze zoekdienst

### Stap J (portal) (13 okt)

Verwachting: Met een bron per passage in plaats van drie ziet de keuzestap het artikel dat het zoeken al vond, en worden meer vragen beantwoord zonder meer te verzinnen

Uitkomst: Twee rondes op de hele meetlat tegen de twee rondes ervoor, tegen de uitgerolde zoekdienst. De keuzestap krijgt nu mediaan acht passages uit vijf artikelen (was vijf uit drie), dubbele secties 15 van 626 (was 44 van 456), het bekende artikel aangeboden in 36 van 40 (was 33). Antwoorden 84 en 81 tegen 74 en 74, "niet gevonden" 18 en 19 tegen 23 en 23, zoals verwacht 73 en 74 tegen 66 en 71; de antwoorden die de eigenaar verwachtte en in beide rondes kwamen 61 van 81 tegen 53 van 81; waar de eigenaar "niet gevonden" verwachtte en beide rondes toch antwoordden 8 tegen 7; grotendeels onbewezen 13 en 9 tegen 10 en 8, afgekeurde zinnen 26% en 25% tegen 27% en 24%, schone antwoorden 60% en 71% tegen 62% en 65%; een geschreven beurt 5,6 en 5,7 s tegen 5,9 en 6,4 s. Wat minder werd: de keuzestap vraagt minder vaak door (4 en 7 keer "hangt af van" tegen 8 en 8; van de zes verwachte vragen in minstens één ronde gesteld 2 tegen 3), met acht passages kiest ze vaker één artikel en zegt "beantwoordt"

Besluit: Uitgerold. Volgende: de keuzestap met acht passages opnieuw lezen op "hangt af van", want dat is nu de rest die nog aan de widget hangt

### Stap J (keuzestap) (13 okt)

Verwachting: Met acht passages vraagt de keuzestap minder door; een opdracht die de variantregel als controle vóór het oordeel uitschrijft, herstelt dat zonder antwoorden te kosten

Uitkomst: Keuzelab op de passages die de stap nu echt ziet (de invoer van één meetronde na J, acht per beurt), met de verwachting van de eigenaar per geval als lat (antwoord, niet gevonden, vraag), 93 gevallen, drie herhalingen, drie opdrachten. Huidige opdracht: verwachte antwoorden goed 153 van 198, verwachte vragen 4 van 18, verwacht "niet gevonden" 15 van 39. Met de variantregel uitgeschreven ("kijk eerst of je citaten uit verschillende varianten van hetzelfde product komen en het gesprek niet zegt welke; dan hangt af van, antwoord nooit voor beide tegelijk"): vragen 10 van 18, maar antwoorden 143 van 198 en "niet gevonden" 12 van 39. Dezelfde regel met het product en de variant uit de zaak als eerste regel van de invoer: vragen 9, antwoorden 134. Gelezen: de regel vraagt door waar de eigenaar een antwoord verwachtte omdat de stappen voor elke variant gelijk zijn (een headset, een Yealink-bureautelefoon, de app waarvan het toestel er niet toe doet); twee vragen gewonnen, ruim drie antwoorden verloren. Wat de meting ook laat zien: van de gevallen die de eigenaar in september "niet gevonden" noemde, zegt de keuzestap er nu in 24 van 39 herhalingen "beantwoordt" met een geverifieerd citaat, en bij lezing is de helft daarvan een echt antwoord uit een artikel dat het zoeken toen niet vond, de andere helft een lijkend artikel

**Groter model, zelfde opdracht en invoer** (op verzoek van de eigenaar): het productiemodel voor deze stap (medium) doet precies wat het Vibe-model deed, antwoorden 151 van 198, vragen 6 van 18, "niet gevonden" 16 van 39; het grote model (large) antwoordt vaker (167 van 198), vraagt niet vaker (5 van 18) en zegt in geen van de 39 herhalingen "staat er niet in": het vindt overal een zin. Een groter model brengt de variantafweging dus niet, en kost de weigeringen

Besluit: Niet gewijzigd; de huidige opdracht en het huidige model blijven. De vraag per variant is een afweging die geen van de drie modellen uit de tekst haalt; de volgende winst zit in de artikelen zelf (één artikel per variant benoemen)

### Variant per citaat (14 okt)

Verwachting: De keuzestap vraagt vaker door als ze per gecopieerde zin de variant (toestel, app, model, abonnement) benoemt waarvoor het artikel die zin geeft, met één zin per variant; onderzoek naar hoe anderen dit doen: geen leverancier documenteert het, de dichtstbijzijnde techniek is de "conversational machine reading"-lijn (ShARC, E3, DISCERN) die de voorwaarden uit één regeltekst haalt en in code beslist wat nog onbeantwoord is

Uitkomst: Keuzelab op de passages van J (93 gevallen, drie herhalingen, Vibe-model): verwachte vragen 11 van 18 tegen 4 van 18, verwachte antwoorden 128 van 198 tegen 152, waarvan 7 gevallen die altijd een vraag werden die een collega ook zou stellen (Android of iPhone, welk Yealink-model). De varianten los laten opnoemen en in code beslissen vroeg twee keer zo vaak onnodig (113 van 198). Daarna twee rondes op de hele meetlat tegen de twee rondes van de schrijfopdracht: van de zes verwachte vragen in beide rondes gesteld 2 tegen 1, in minstens één ronde 3 tegen 3; vragen waar de eigenaar een antwoord verwachtte 6 en 7 per ronde tegen 3 en 4; verwachte antwoorden in beide rondes 58 tegen 57 van 81; afgekeurde zinnen 24% en 27% tegen 21% en 23%, grotendeels onbewezen 11 en 10 tegen 6 en 9, schone antwoorden 59% en 67% tegen 75% en 69%; een geschreven beurt 6,2 en 5,7 s tegen 5,5 en 5,2

Besluit: Niet uitgerold: één verwachte vraag erbij kost drie onnodige vragen per ronde en een slechter onderbouwd antwoord. De winst uit het lab (één zin per variant) draagt in de hele stroom niet, omdat de schrijver dan meer regels krijgt. Wat overblijft uit het onderzoek: varianten bij het indexeren uit een gemengde sectie halen (één extractie-aanroep per sectie, dan ziet de keuzestap twee kandidaten, wat al werkt), en het dekkende antwoord ("voor Android: …, voor iPhone: …") bij hooguit twee korte varianten; beide ongemeten

### Opruimen na één pad (14 okt)

Verwachting: De code wordt leesbaar zonder dat het gedrag verandert: het terugparsen van de stream weg, de supportschakelaar en het dode profielveld weg, de twee grote bestanden gesplitst naar onderwerp, en de drie overgebleven lange functies (het zoeken, de live leg van de interne chat, het chat-endpoint) in benoemde stappen

Uitkomst: Geen bestand op het chatpad boven 1.300 regels, de drie functies elk onder 120 (de langste die overblijft is de niet-gestreamde interne leg, 223 regels), geen complexiteitsontheffing meer; OpenAPI-beschrijving, routevolgorde en CORS-tabel byte-gelijk aan ervoor; twee reviews zonder gedragsverschil. Eén ronde op de hele meetlat tegen de twee rondes van de schrijfopdracht: zoals verwacht 70 tegen 71 en 74, antwoorden 75 tegen 76 en 75, "niet gevonden" 24 tegen 24 en 20, grotendeels onbewezen 7 tegen 6 en 9, afgekeurde zinnen 21% tegen 21% en 23%, schone antwoorden 67% tegen 75% en 69%, een geschreven beurt 5,6 tegen 5,5 en 5,2 s: binnen de spreiding tussen rondes

Besluit: Uitgerold (#1866, #1871 en de drie functies). Het gedrag is gelijk gebleven; wat erbij kwam is dat de straat nu ook als code in volgorde te lezen is

### Snelheid zonder kwaliteitsverlies (21 okt)

Verwachting: Een widgetbeurt (mediaan 7,1 s live: zaak 1,2, zoekstap 3,8 met daarin parafrasen 0,75, zoekdienst 1,34 en keuzestap circa 1,5, schrijver 1,4, lichte beoordelaar 0,65) wordt sneller zonder dat een stap minder leest of controleert: de zoekdienst hoeft de verwijzingen niet nog eens op te lossen als de portal al zoekvragen meestuurt (`coreference_resolved`, 0,45 s mediaan in de zoekdienst), de keuzestap op het snelle model, en Qdrant en de graafzoektocht naast elkaar

Uitkomst: Qdrant en de graafzoektocht lopen al naast elkaar (de code start de graafzoektocht als taak vóór het wachten op Qdrant), dus die winst bestond niet. De keuzestap op het snelle model (keuzelab, 93 gevallen, drie herhalingen): antwoorden 177 van 198, vragen 2 van 18, "staat er niet in" 9 van 39, tegen 152, 4 en 15 op het huidige model: het snelle model vindt overal een zin en verliest juist de weigeringen en de vragen. De vlag voor de zoekdienst, twee rondes op de hele meetlat: kwaliteit binnen de spreiding (verwachte antwoorden in beide rondes 58 tegen 57 van 81, "niet gevonden" toch antwoord 8 tegen 7, afgekeurde zinnen 24% en 22%), maar de zoekstap werd niet sneller (3,3 s tegen 2,9 s mediaan, met vijf uitgevallen beurten in de tweede ronde door drukte op de modellen)

Besluit: Niets uitgerold. De keuzestap blijft op medium; de vlag wacht tot de logs van de zoekdienst de overgeslagen stap zelf laten zien, want de replay meet de servertijd niet apart van de belasting

### Schrijfopdracht (13 en 14 okt)

Verwachting: De schrijver verzint minder bij als hij de stappen in de volgorde en de woorden van het artikel moet houden, zonder stap, oorzaak of voorbeeld van zichzelf

Uitkomst: **Widget:** twee rondes op de hele meetlat tegen de twee rondes van J (zelfde zoekdienst, zelfde keuzestap). Afgekeurde zinnen 21% en 23% tegen 26% en 25%; antwoorden die grotendeels onbewezen zijn 6 en 9 tegen 13 en 9; schone antwoorden (hooguit één afgekeurde zin) 75% en 69% tegen 60% en 71%; zoals verwacht 71 en 74 tegen 73 en 74; de verwachte antwoorden die in beide rondes kwamen 57 van 81 tegen 61 (gelezen: twee van de vier werden een eerlijk "dat staat niet in de helpartikelen" waar eerst een aanvraag- of wachtwoordprocedure verzonnen werd); mediane lengte 361 en 409 tegen 383 en 410; een geschreven beurt 5,5 en 5,2 s. De tweede ronde (25% naar 23%) valt binnen de spreiding tussen rondes, de eerste (26% naar 21%) niet.

**Intern** (dezelfde twintig gesprekken, 67 beurten, de opdracht in de woorden van die chat: medewerker en kennisbank): op Strict onbewezen zinnen per gecontroleerd antwoord 2,6 tegen 2,9 (36 gecontroleerd), een beurt mediaan 4,7 tegen 5,9 s, zelfde verdeling van antwoorden en weigeringen. Op Open in twee rondes slechter: 3,3 tegen 2,7 en 4,4 tegen 3,7 per antwoord; de grote verschuivingen zijn antwoorden uit algemene kennis (een overzicht per platform, een offerte-achtig antwoord dat eerst een weigering was), wat Open juist mag

Besluit: Uitgerold op de widget en intern Strict. Open houdt de regels en de kortere opdracht van O4, omdat Open uit algemene kennis mag aanvullen en de strengere zin daar twee rondes slechter gaf. Dit is de eerste meting sinds de keuzestap die het aandeel onbewezen zinnen zelf verlaagt; het plafond ligt nu rond een vijfde

### Stap N (12 okt)

Verwachting: De keuzestap doet op intern Strict wat ze op de widget deed: minder verzonnen antwoorden, zonder goede te verliezen

Uitkomst: Eigen meetlat: de laatste twintig interne gesprekken van kennisgebruikers (61 medewerkersbeurten), nagespeeld met de echte geschiedenis en het profiel op Strict gezet (Strict is 10 van de 103 actieve gebruikers; de open modus leest geen passages en verandert niet). Zonder keuzestap: 35 antwoorden, 12 deelantwoorden, 14 weigeringen, 17 reparaties, 133 onbewezen uitspraken in 48 gecontroleerde antwoorden, mediaan 5,1 s. Met: 22, 7 en 32, 7 reparaties, 62 onbewezen uitspraken in 28, mediaan 3,6 s; de keuzestap draaide op 36 beurten (16 beantwoordt, 18 staat er niet in, 2 hangt af van). Zes omslagen naar de weigering gelezen: vier terecht (een verzonnen "alle Yealink-toestellen werken", generieke stappen bij "toestel offline", een vertaalvraag, een vraag die al "staat niet in de kennisbank" kreeg), één verdedigbaar (auto-answer uit de API-documentatie), één gemengd (de stappen naar een beheerportal waren bruikbaar, de gevraagde link stond er niet)

Besluit: Uitrollen op intern Strict. De reparatie achteraf blijft daar tot een meting zonder; de zaak komt niet op de interne chat zolang de open modus de regel is

### Eén pad voor partners (14 okt)

Verwachting: Elke beurt die niet van de interne chat is, loopt door `widget_turn()`, zodat een partner-key en een widget zonder supportschakelaar dezelfde zaak, keuzestap, routes en controle krijgen als de helpwidget en er geen tweede generatiepad naast blijft bestaan

Uitkomst: Gemeten op productie vóór de wijziging: de enige actieve widget liep al over dit pad, terwijl de zes widgets zonder de schakelaar en de partner-keys in de afgelopen twee weken geen enkele beurt hadden, en de partner-keys in de afgelopen maand drie. Een meetronde op de meetlat was daarom niet nodig, omdat het pad van de enige widget met verkeer niet verandert

Besluit: Uitgerold: de generieke generatie en de supportschakelaar zijn weg; de interne chat houdt haar streaming-leg. Het terugparsen van de stream is inmiddels ook weg: de widget legt haar antwoord vast uit de `Reply` zelf, vóór de eerste byte, in plaats van het uit haar eigen frames terug te lezen

### Keuzestap zonder uitzonderingen (21 okt)

Verwachting: In de week van 14 tot 21 oktober kwamen alle verzonnen antwoorden binnen via twee wegen langs de keuzestap: een bericht met meerdere vragen sloeg haar over, en een keuzestap die uitviel liet de schrijver alle passages lezen. Als we beide wegen sluiten, leest de schrijver op elke beurt alleen de regels die de keuzestap aanwees, en krijgt een beurt zonder keuze op de widget het eerlijke "niet gevonden" in plaats van een antwoord uit alle passages

Uitkomst: Twee rondes op de hele meetlat tegen de twee rondes van de schrijfopdracht en de twee van de zoekdienstvlag. Afgekeurde zinnen 19% en 19% tegen 21% en 23% en 21% en 24%; grotendeels onbewezen 5 en 8 tegen 6 en 9 en 7 en 8; schone antwoorden 73% en 74% tegen 75% en 69% en 67% en 69%; zoals verwacht 73 en 72 tegen 71 en 74 en 70 en 68; doorvragen 9 en 6 tegen 4 en 7; verwachte antwoorden in beide rondes 56 van 81 tegen 57 en 51; waar de eigenaar "niet gevonden" verwachtte en beide rondes toch antwoordden 10 tegen 7; vragen waar een antwoord verwacht was 6 en 4 tegen 3 en 4; een geschreven beurt 5,8 s mediaan tegen 5,4. De twaalf samengestelde vragen van de meetlat gaan nu door de keuzestap: één ervan werd van doorverwijzing een antwoord, de rest bleef wat het was, met minder afgekeurde zinnen (bijvoorbeeld 2 van 3 en 3 van 4 naar 1 van 8 en 1 van 4). De verzonnen antwoorden van de week zelf (trunk-timer, REFER-menu, drie webphones) staan niet op de meetlat; de live cijfers van volgende week zijn daar de maat

Besluit: Uitgerold. De eerste stap die het aandeel onbewezen zinnen onder een vijfde brengt; de prijs is drie antwoorden meer waar de eigenaar "niet gevonden" verwachtte

### Zaak als geheugen en citaat (21 okt)

Verwachting: In dezelfde week gingen drie beurten mis omdat de zaak iets niet vasthield. Een bericht zonder enige contactwens werd als vraag om een mens gelezen en kreeg de contactreactie, dus moet de zaak nu de woorden van de bezoeker citeren waarop die route rust, en vervalt de route als code dat citaat niet in het laatste bericht terugvindt. Een artikel dat in de eerste beurt antwoordde, was in de vierde uit de zoekresultaten verdwenen, waardoor die beurt "niet gevonden" werd terwijl het gesprek het bewijs al had; de passages die de keuzestap koos gaan nu met de beurt mee en komen de volgende beurt opnieuw bij de keuzestap. En de taal van de vaste reacties werd per bericht bepaald, zodat een kort Nederlands vervolg een Engelse weigering kreeg, terwijl dezelfde contactreactie vier keer achter elkaar woordelijk terugkwam bij iemand die al had gezegd niet te willen bellen; de zaak legt nu één taal voor het gesprek vast en de tweede keer volgt een andere tekst. We verwachten dat deze drie soorten fouten van de meetlat verdwijnen zonder dat er goede antwoorden sneuvelen

Uitkomst: Twee rondes op de hele meetlat tegen de twee rondes van de keuzestap zonder uitzonderingen (waar dit op bouwt) en de twee van de schrijfopdracht. Zoals verwacht 75 en 73 tegen 73 en 72 en 71 en 75; de verwachte antwoorden die in beide rondes kwamen 60 van 81 tegen 56 en 57, het hoogste tot nu toe, omdat een vervolgvraag het artikel van de vorige beurt weer aangeboden krijgt; waar de eigenaar "niet gevonden" verwachtte en beide rondes toch antwoordden 10, gelijk aan de stap ervoor; afgekeurde zinnen 22% en 20% tegen 19% en 19%; grotendeels onbewezen 9 en 6 tegen 5 en 8; verwachte vragen in minstens één ronde 2 tegen 3; doorverwijzingen 1 en 1 tegen 4 en 4: de valse doorverwijzingen zijn weg, maar van de vier verwachte ging er één (een vraag over wat een bundel wel en niet dekt) in beide rondes naar een antwoord omdat de zaak haar niet meer als buiten het onderwerp las; een geschreven beurt 5,8 s mediaan, gelijk

Besluit: Uitgerold. Het geheugen van het gesprek levert de meeste verwachte antwoorden tot nu toe; de ene gemiste doorverwijzing is de volgende meting waard, want het citaat voor "buiten het onderwerp" is nieuw voor de zaak

### LiteLLM-hook weg (21 okt)

Verwachting: De hook was alleen nog een terugweg; als het portalpad zich een week op echt verkeer bewijst, kan hij weg zonder dat een gebruiker er iets van merkt

Uitkomst: Elke LibreChat-omgeving draait sinds 13 oktober 14:01 UTC op het portalpad, met 102 interne beurten op de eerste werkdag en 236 in de week erna, zonder één fout van de upstream. De hook, zijn kopieën van de prompts en de citatiecode, de bijbehorende tests, de mounts en variabelen in de LiteLLM-container en de LiteLLM-kant van `switch_internal_chat.py` zijn verwijderd, terwijl de gateway zelf (modellen, sleutels, router, budgetten en PII-beleid) ongewijzigd bleef

Besluit: Verwijderd, de terugweg is git

### Intern uit gekozen regels (21 okt)

Verwachting: In de 23 interne gesprekken van 14 tot 21 oktober gingen vijf dingen mis. Als de keuzestap niets koos, kreeg de schrijver in Open toch alle passages en schreef hij een zeker antwoord uit het artikel van een ander product, met dat artikel als bron, dus zou Open zonder gekozen regels uit algemene kennis schrijven, zonder passages; dat is gemeten en teruggedraaid naar: alle passages blijven, met de melding aan de schrijver dat de keuzestap er geen vond die antwoordt (zie de uitkomst). De keuzestap zelf viel op interne beurten te vaak uit, omdat haar budget van 4 s niet past bij 13 tot 22 aangeboden passages; het budget groeit nu met het aantal passages. In het deel uit algemene kennis stonden verzonnen harde feiten, zoals een e-mailadres en menupad voor een verwerkersovereenkomst, een productprijs en uitspraken over de planning, en omdat de tekst al gestreamd is, zegt de opdracht nu dat zo'n feit alleen uit de regels mag komen en meldt de voettekst het als het toch in geen passage staat. Een medewerker die een vraag van iemand anders doorgaf, kreeg "iPhone of Android?" terug terwijl alleen die ander dat weet, waardoor de interne chat nu de stappen per variant geeft in plaats van door te vragen. Een Duitstalige medewerker kreeg de vaste weigering in Strict in een andere taal, dus volgen de vaste interne reacties nu de taal van het hele gesprek. En een bericht met tekst naast een bijlage die Klai niet kan lezen, kreeg alleen de zin over pdf-bestanden, terwijl de vraag zelf onbeantwoord bleef; die vraag wordt nu beantwoord, met de zin ervoor. Verwacht wordt minder antwoorden uit het verkeerde artikel, geen harde feiten zonder melding, en geen doorvraag die de medewerker niet kan beantwoorden.

Uitkomst: Interne meetlat van 36 beurten uit 22 echte gesprekken van één klantomgeving plus 12 beurten van een onderzoeksomgeving, nagespeeld met de echte geschiedenis en het echte profiel; vier nulmetingsrondes op main tegen twee rondes per variant. Eerste variant (passages weg als de keuzestap niets koos): automatisch goed 19 en 21 van 31 tegen 23, 26, 22 en 26; kennisbankantwoorden 16 en 15 van 21 tegen 18, 21, 18 en 21, waaronder een Windows-app-vraag en de Emergency Gateway die in beide rondes uit algemene kennis kwamen terwijl het artikel er was; de keuzestap viel 21 en 19 keer uit op haar budget van 4 s, op 7 beurten twee keer. Teruggedraaid. Tweede variant (passages blijven met de melding, budget groeit met het aantal passages): automatisch goed 26 en 26 van 31, de bovenkant van de nulmeting; kennisbankantwoorden 21 en 21 van 21; stappen per variant 3 en 3 van 4 tegen 3, 2, 2 en 3, en de doorvraag "Android of iOS?" komt niet meer voor (één keer in de nulmeting); de keuzestap viel 1 en 2 keer uit, nooit twee keer; een beurt 6,8 en 6,9 s mediaan tegen 7,5, 8,2, 11,1 en 6,9. Harde feiten gemarkeerd: de prijzen van een product (3 bedragen in beide rondes) en een bedrag van 29 euro dat in geen passage staat, precies de twee gevallen waar de eigenaar het verwachtte; de gespreksopnameprijzen worden in beide rondes niet meer herrekend ("de kennisbank vermeldt geen prijs"), en het verzonnen e-mailadres voor de verwerkersovereenkomst komt niet terug. De onderzoeksomgeving krijgt de melding op haar essays (17 en 18 bedragen in één beurt): daar is ze ruis, want die bedragen zijn de inhoud van het werk. Niet opgelost: de vraag over doorsturen naar Retell AI kwam in één van de twee rondes nog uit het Bubble-artikel (in de andere eerlijk "niet in de kennisbank" met een algemeen antwoord), en het aantal onbewezen uitspraken van de controle achteraf is hoger (205 en 241 over 41 en 43 antwoorden tegen 163, 119, 115 en 170 over 38 tot 41), omdat de schrijver nu meer uit algemene kennis zegt als de keuzestap niets vond. De Duitse Strict-weigering blijft Engels: de bibliotheek heeft alleen Nederlands en Engels

Besluit: Uitgerold in de tweede variant. De melding aan de schrijver is een zachte maatregel; de harde-feitenmelding en de live cijfers van volgende week zijn de maat, en een Duitse (en Franse) tekst voor de vaste reacties is de volgende kleine stap

### Eén schrijver (22 okt)

Verwachting: Er is nog één schrijver: `write_reply` schrijft ook de interne Open- en general-beurt, en streamt die zoals de live schrijver deed, waarna het antwoord op dezelfde manier wordt afgerond als een vastgehouden antwoord. Wat de widget, een partnersleutel en intern Strict versturen blijft byte voor byte gelijk (een test legt een widgetbeurt en een Strict-beurt vast zoals main ze verstuurde), de medewerker ziet in Open en general dezelfde stroom, en de live schrijver met zijn eigen voettekst, telling van harde feiten en record is weg. Twee bewuste verschillen: een gestreamde beurt die tekst schrijft en ook een tool aanroept, eindigt nu met `finish_reason: tool_calls` zoals de vastgehouden beurt al deed (de live leg liet dat weg, waardoor een client de tool nooit draaide), en een niet-gestreamde Open- of general-beurt laat bij `include_sources=false` ook de bronnen uit de voettekst, zoals de gestreamde al deed

Uitkomst: Interne meetlat (36 beurten, twee rondes tegen de ronde op main van dezelfde ochtend en de twee rondes van de stap ervoor): automatisch goed 27 en 25 van 31 tegen 24 (en 26 en 26); kennisbankantwoorden 21 en 21 van 21 tegen 19 (en 21, 21); onbewezen uitspraken 82 en 73 over 34 antwoorden tegen 107 over 33; harde feiten gemarkeerd in 3 en 2 antwoorden tegen 2; een beurt 6,0 en 5,7 s mediaan tegen 5,7. Widgetmeetlat (109 gevallen, twee rondes tegen twee rondes op main van dezelfde ochtend): zoals verwacht 71 en 75 tegen 74 en 71; afgekeurde zinnen 83 en 96 van 403 en 390 tegen 90 en 87 van 379 en 379; grotendeels onbewezen 8 en 11 tegen 11 en 9; schone antwoorden 43 en 38 tegen 40 en 45; een geschreven beurt 6,9 en 6,5 s mediaan tegen 6,8 en 6,9: binnen de spreiding tussen twee rondes, zoals het hoort bij een vastgehouden pad dat byte voor byte gelijk is. De meting vond één fout: een nepnotatie als `http://[stap 1]` in een antwoord liet de URL-normaliseerder een uitzondering gooien in de harde-feitencontrole en de beurt mislukken; gefixt met test, de beurt slaagt

Besluit: Uitgerold: de tweede schrijver is weg, de meting houdt stand

### Melding aan de schrijver weg (22 okt)

Verwachting: De melding die de schrijver in intern Open kreeg als de keuzestap niets koos (`NO_PASSAGE_ANSWERS_NOTE`, 21 oktober) was een tweede laag over één oorzaak: de keuzestap sprak de taal van de widget en keurde een medewerkersvraag te vaak af. Die oorzaak nam de interne keuzestap-opdracht een dag later weg. Zonder de melding houdt Open alle passages, zoals het al deed, en hoort de meting gelijk te blijven aan de rondes van die opdracht.

Uitkomst: Interne meetlat (36 beurten, twee rondes tegen de twee rondes van "Keuzestap in interne woorden" van dezelfde dag): automatisch goed 25 en 26 van 31 tegen 25 en 26; kennisbankantwoorden 21 en 21 van 21 tegen 20 en 21; "staat er niet in" 5 en 5 tegen 5 en 3; onbewezen uitspraken 79 en 70 over 34 antwoorden tegen 98 en 60; harde feiten gemarkeerd in 1 en 2 antwoorden tegen 1 en 2; een beurt 5,4 en 6,4 s mediaan tegen 6,5 en 6,4.

Besluit: Melding weg; de laag die niets meer deed is niet meer

### Zaak in de interne chat (22 okt)

Verwachting: De interne chat las de zaak elke beurt opnieuw uit het hele gesprek en onthield geen gekozen passages, zodat een artikel dat de eerste beurt beantwoordde in een latere beurt alleen terugkwam als het zoeken het opnieuw vond. Nu leest een vervolgbeurt de zaak en hooguit drie bewaarde passages van de vorige beurt van hetzelfde gesprek uit `internal_chat_cases`, langs dezelfde functies als de widget, en ook intern Strict biedt de bewaarde passages aan als het zoeken niets vond. De meetlat speelt een beurt na zonder de beurten ervoor en slaat het lezen en bewaren over, dus het geheugen is daar afwezig en de uitkomst hoort gelijk te blijven; het effect is alleen live te zien, in `case_remembered` en de logregel `internal_chat_case_remembered`.

Uitkomst: Interne meetlat (36 beurten), drie rondes tegen een nulmeting op main in hetzelfde uur: automatisch goed 25, 25 en 25 van 31 tegen 23; kennisbankantwoorden 20, 20 en 20 van 21 tegen 19; "staat er niet in" 5, 6 en 5 tegen 6; onbewezen uitspraken 52, 71 en 66 tegen 67; harde feiten gemarkeerd in 2, 1 en 1 antwoorden tegen 3; een beurt 5,4, 5,6 en 5,5 s mediaan tegen 5,2. De twee rondes van "Melding aan de schrijver weg" van eerder die dag gaven 25 en 26; de kennisbankvraag die in alle drie rondes en in de nulmeting van hetzelfde uur "staat er niet in" kreeg, werd daar nog beantwoord, dus dat verschil zit in de omgeving en niet in deze code, die bij een nagespeelde beurt hetzelfde doet als main.

Besluit: Gebouwd

### Gespreks-id als sleutel van het geheugen (23 okt)

Verwachting: De interne chat vond de bewaarde zaak van een gesprek terug via digests van de berichten van de medewerker, omdat LibreChat geen gespreks-id meestuurde. LibreChat 0.8.8 vult `{{LIBRECHAT_BODY_CONVERSATIONID}}` in de headers van een custom endpoint bij elk verzoek in, dus het endpoint "Klai AI" stuurt het id nu mee en `internal_chat_cases` heeft één rij per org, medewerker en gespreks-id. Tegelijk speelde de meetlat alleen de genoemde beurten na, met het geheugen uit, zodat een vervolgbeurt gemeten werd zonder de zaak die de beurt ervoor bewaard zou hebben, op beide oppervlakken. Nu speelt `run` per gesprek elke beurt tot en met de hoogste genoemde na, met het geheugen in het proces. Verwacht: gelijk of beter, omdat de vervolgbeurten nu lezen wat ze in productie lezen.

Uitkomst: Interne meetlat (36 beurten, waarvan 14 vervolgbeurten), twee rondes tegen de twee nulmetingsrondes van 22 oktober, die zonder geheugen naspeelden: automatisch goed 26 en 26 van 31 tegen 25 en 26; kennisbankantwoorden 21 en 21 van 21 tegen 21 en 21; dekkende antwoorden 3 en 3 van 4 tegen 2 en 3; onbewezen uitspraken 63 en 85 tegen 79 en 70; harde feiten gemarkeerd in 2 en 3 antwoorden tegen 1 en 2; "staat er niet in" bij de keuzestap 4 en 4 tegen 5 en 5; een beurt 6,1 en 6,0 s mediaan tegen 5,4 en 6,4. Alle 14 vervolgbeurten lazen in beide rondes een zaak (`case_remembered`), en geen beurt meldde een ontbrekend id. Widgetmeetlat (109 gevallen, waarvan 51 vervolgbeurten), één ronde tegen een ronde van main uit hetzelfde uur, de eerste volledige widgetrondes met deze CLI: verwacht 74 tegen 70 van 109 (antwoord 65 tegen 61 van 81, vraag 2 tegen 1 van 6, niet gevonden 7 tegen 8 van 18, doorverwijzing 0 en 0 van 4); onbewezen uitspraken 85 in 46 van 70 antwoorden tegen 85 in 47 van 65; een beurt 4,9 tegen 5,0 s mediaan. De kandidaat had één foutrij (een `ValueError` op een vervolgbeurt) die in vier losse herhalingen niet terugkwam. Eén widgetronde per kant is geen spreiding; de interne onbewezen uitspraken liggen binnen wat twee rondes van dezelfde code eerder van elkaar verschilden.

Besluit: Gebouwd; uitrollen met de LibreChat-config eerst

### Eén controle achteraf (22 okt)

Verwachting: De twee controles na het antwoord, de regex op e-mailadressen, links, telefoonnummers en bedragen en de controle per zin, geven nu één vorm terug (`answer_check.AnswerCheck`: elke uitspraak met haar soort en de passage of het gebruikersbericht dat haar draagt), zodat de voettekst en het record dezelfde uitkomst lezen. De velden `hard_facts_outside_kb`, `unsupported` en `grounding` en de voettekst blijven gelijk, vastgelegd door de bestaande tests en een test die één gecontroleerde uitkomst in beide terugziet, behalve in één randgeval, waar de voettekst verandert: de passages en gebruikersberichten worden nu elk apart gelezen in plaats van aan elkaar geplakt. Een telefoonnummer of bedrag dat vroeger over de grens van twee passages samensmolt (of met een datum in een gebruikersbericht) en daardoor als niet gedragen telde, telt nu als gedragen; een telefoonnummer of bedrag dat over twee passages verdeeld staat, telt nu als niet gedragen

Uitkomst: Niet gemeten: het gedrag verandert in dit randgeval, verder niet

Besluit: Gebouwd

## 9. Wat in de logboeken staat en hier is samengevat

De volledige metingen tot en met 25 september staan in AJ (2.1 tot en met 2.55) en IQ (§1 tot en met §19). De lessen die daaruit blijven gelden:

1. Een opdracht aan het antwoordmodel verandert zelden zijn gedrag; een aparte stap of een regel in code wel (AJ 2.4, 2.5, 2.12, 2.46).
2. Meer gevonden is niet hetzelfde als beter beantwoord (AJ 2.32; IQ §11, §14, §19).
3. Eén ronde is geen meting; een verschil dat tussen rondes omslaat is ruis (AJ 2.32, §6).
4. De meting moet de route nemen die de gebruiker neemt: de herformuleringen waren drie dagen "live" zonder dat de browser ze kreeg (AJ 2.41).
5. Een beoordelaar krijgt alleen wat de gebruiker leest (replay interne chat, 25 september).
6. De kennisbank is voor ongeveer één op de negen kennisvragen de grens: het antwoord staat er niet (AJ 2.31, 2.36).
