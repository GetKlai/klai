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

## 5. Het ontwerp

### Het leidende inzicht

Een klein model dat passages krijgt over het onderwerp van de vraag, schrijft een antwoord, ook als het antwoord er niet in staat. Het leent de vorm uit wat het leest (een menupad, een stappenlijst) en vult de inhoud zelf in. Een instructie "zeg dat het er niet staat" houdt dat niet tegen: bij zwakke bronnen werd ze in vier op de tien gevallen genegeerd.

De zoekscore kan dit niet voorkomen, want ze meet of een passage op de zoekvraag lijkt, niet of ze de vraag beantwoordt. In bijna de helft van de nagespeelde beurten waarin het model schreef, bevatte geen enkele passage het antwoord; in de meerderheid daarvan was de score hoog [gemeten, §8].

Daaruit volgt de regel waar het ontwerp op rust:

> **Beslis vóór het schrijven wat de schrijver mag lezen, en of er geschreven wordt. Na het schrijven wordt alleen nog gemeten, niet meer gerepareerd.**

Dit is ook wat het onderzoek zegt (§3.1: een model verzint vooral bij onvoldoende context; §3.2: eerst kiezen, dan schrijven) en wat de open-source systemen in hun code doen (§3.6). Wat wij daaraan toevoegen is dat het oordeel niet op onderwerp toetst maar op "beantwoordt dit deze vraag, voor hetzelfde product, apparaat en dezelfde richting", en dat "staat er niet in" in code wordt afgehandeld in plaats van aan de schrijver te worden gevraagd.

### De stroom

Eén stroom voor beide chats; alleen de uiteinden verschillen.

| # | Stap | Wie | Wat het oplevert |
|---|---|---|---|
| 1 | **Begrijpen.** Soort beurt (kennisvraag, verzoek om een mens, praatje, onderwerp dat niet behandeld wordt) en toon | klein model, vaste velden; code kiest de route | route |
| 2 | **Zoeken.** Breed: de woorden van de bezoeker plus extra zoekvragen. Liever te veel vinden dan te weinig, want stap 3 kiest | zoekmachine | passages |
| 3 | **Kiezen.** Eén oordeel uit drie over de gevonden passages: deze beantwoorden de vraag, het hangt af van één feit, of geen enkele doet het | middelgroot model, vaste velden; code kiest de route | de gekozen passages, of een route zonder schrijver |
| 4a | **Schrijven**, uit alleen de gekozen passages, kort, met alleen de stappen die het artikel geeft | antwoordmodel | het antwoord |
| 4b | **Eén vraag**, als het antwoord per variant verschilt; hooguit één keer per gesprek | antwoordmodel, met het ontbrekende feit uit stap 3 | de vraag |
| 4c | **Eerlijk "niet gevonden"** met de weg naar een mens | code, vaste tekst | de reactie |
| 5 | **Meten.** De controle per zin kijkt mee en legt vast; ze verandert het antwoord niet | middelgroot model | meetgegevens |

Valt stap 3 uit, dan loopt de beurt zonder keuze door, zoals elk open-source systeem dat ook doet: een storing mag geen weigering worden.

| Punt in de stroom | Publieke chat | Interne chat |
|---|---|---|
| "Staat er niet" | Afspraakknop | Zeggen dat het niet in de kennisbank staat; in Open mag algemene kennis, duidelijk gelabeld |
| Weg naar een mens | Afspraakknop | Bestaat niet |
| Bronnen | Bronkaart | Volledige bronnenlijst |
| Toegang | Publieke kennis | De kennisbanken van de medewerker |
| Invoer | Korte vragen | Lange vragen en geplakte klantmails, eerst gedistilleerd |

### Wat dit overbodig maakt

Deze onderdelen bestaan omdat de schrijver alles te lezen kreeg. Ze gaan eruit zodra de meting van begin tot eind het beeld van de proef bevestigt, elk met een eigen regel in §8:

- de regel voor zwakke bronnen op basis van de score (stap 3 leest de passages zelf);
- de vraagstap die artikeltitels vergelijkt (stap 3 ziet of het antwoord per variant verschilt);
- het herschrijven na de controle (op de widget al weg; de regel die ervoor in de plaats kwam gaat ook weg);
- de lichte antwoordbeoordelaar, als stap 3 en de controle samen hetzelfde zeggen.

### Wat het ontwerp niet oplost

- **Het zoeken.** Stap 3 maakt zichtbaar hoe vaak het juiste artikel niet bij de passages zit terwijl de kennisbank het heeft. Dat wordt het volgende knelpunt, en stap 3 maakt breder zoeken veilig: een extra zoekgang haalt lijkende artikelen binnen, en die worden nu weggekozen in plaats van uitgeschreven.
- **De brontekst.** Een gat waar een linktekst hoort blijft een gat.
- **Wat niet in de kennisbank staat.** Dat blijft "niet gevonden", en wordt een lijst voor de eigenaar.
- **Een keuzestap die ernaast zit.** Het oordelende model keurde in een eerdere proef een kwart van de goede antwoorden af op alleen het oordeel "staat er niet in" [gemeten, §8 stap 10]. In de proef met kiezen waren de gelezen afwijzingen terecht, maar dit is het risico dat de meting van begin tot eind moet wegen.

### Hoe het pad vandaag live werkt op de helpwidget (stand 30 september, vóór de keuzestap)

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

Van de foutsoorten uit de review is de grootste (een antwoord dat door de reparatie verminkt werd) bij de bron weggenomen, en zijn vier kleinere opgelost: de afspraak die niet genoemd werd, het aanbod "breder zoeken" waar het niets kon doen, het excuus waar niets misging, en de tweede vraag in hetzelfde gesprek. Niet opgelost: het lijkende artikel dat wint, de vraag met meerdere delen, de vervolgbeurt die het onderwerp echt mist, de gemengde vraag die in zijn geheel wordt doorverwezen, de gaten in de brontekst, en wat niet in de kennisbank staat.

Een nieuw cijfer voor "goed" is er niet [aanname zolang de eigenaar niet opnieuw beoordeelt]: de soort reactie klopte al in de meeste gevallen, en of de inhoud goed is kan alleen de eigenaar zeggen. De nagespeelde antwoorden met en zonder reparatie liggen klaar om te beoordelen.

### Volgorde vanaf nu

1. De keuzestap van begin tot eind meten op de hele meetlat, twee rondes, en de antwoorden lezen. Klopt het beeld van de proef, dan uitrollen.
2. Daarna het zoeken, want dat is dan het knelpunt: elke beurt waar de keuzestap "staat er niet in" zegt terwijl de kennisbank het antwoord heeft. Met de keuzestap erachter is breder zoeken veilig; de extra zoekgang voor vervolgbeurten (stap A) en het verbreden naar de hele sectie komen dan terug op tafel.
3. Wat er na het schrijven nog nodig is opnieuw bepalen: de lichte antwoordbeoordelaar en de regel na de controle per zin zijn gebouwd voor een schrijver die alles te lezen kreeg.
4. De regels in de eigen instructies van de widget die om vaste stappen vragen: besluit van de eigenaar.
5. Vragen met meerdere delen (stap B; de keuzestap slaat ze nu over), de brontekst (stap C) en de interne chat (stap G).

## 6. Werkafspraken

| Afspraak | Waarom |
|---|---|
| Elke bewering in een voorstel draagt een label uit de kop van dit document | Dan is zichtbaar waar een ontwerp op rust |
| Wie een voorstel doet, zegt wat hem van mening zou doen veranderen; een positie verandert op nieuw bewijs | Taalmodellen schuiven mee met tegenspraak: 58% in één meting [onderzoek: SycEval, https://arxiv.org/abs/2502.08177] |
| Eén meetlat: echte gesprekken met per gesprek de verwachting en het bewijs, vastgesteld door de eigenaar | Zonder vaste meetlat stuurt het laatste incident |
| Geen wijziging aan het chatpad zonder de meetlat; de uitkomst staat in de PR | De poort op papier is vijf keer overgeslagen |
| Eén wijziging, één verwachting, vooraf opgeschreven met het criterium om te stoppen | Anders is een daling niet toe te wijzen |
| Elke stap verdient zijn plek: wat op de meetlat niets bijdraagt, gaat eruit | Het systeem groeide alleen |
| Eerst lezen, dan tellen, dan voorstellen: voor elke conclusie worden minstens tien echte gevallen van begin tot eind gevolgd, en wordt per geval de eerste plek op het pad vastgelegd waar het misgaat | Tellen per onderdeel liet de oorzaak vóór het schrijven een week onzichtbaar |
| Een wijziging wordt beoordeeld op het hele pad: wat ze vooraan verandert en wat ze achteraan overbodig maakt | Anders wordt het een patch aan het laatste station |
| Een spoor legt vast wat elke stap werkelijk doorgaf aan de volgende; waar het spoor dat niet laat zien, is dat een gat om te dichten en geen ruimte voor een aanname | De zoekvraag van vervolgbeurten stond niet in het spoor, en de grootste oorzaak uit de eerste lezing bleek voor twee derde een aanname |
| Winst bij het zoeken telt pas als de antwoorden van begin tot eind gelezen zijn | Een beoordelend model noemt een passage over hetzelfde onderwerp snel een antwoord; twee keer gaf meer vinden geen beter antwoord |
| Een tweede lezer met schone blik beoordeelt alleen juistheid | De maker keurt zijn eigen werk te makkelijk goed |
| Elke week tien tot twintig echte gesprekken lezen; elke nieuwe fout wordt een vast geval in de meetlat | Zo valt een daling binnen een week op |
| Meetruns draaien op het Vibe-tegoed (`klai-judge`, `klai-ingest`), nooit op de sleutels van echte bezoekers | Een eerdere meting putte het gedeelde tegoed uit en gaf een bezoeker een foutmelding |
| Echte gesprekken en de meetlat staan buiten deze repository; tests gebruiken verzonnen gevallen | De repository is publiek |
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


Wat eerder is afgewezen en hier niet terugkomt: doorvragen als opdracht aan het antwoordmodel, een ruimer budget voor de controle, het samenvoegen van de twee controles achteraf, de zekerheidsband als reden om te weigeren, meer passages of een vierde bron. Twee stappen raken aan eerder werk en zeggen daarom wat er nu anders is. Stap A: op 18 september is de herschrijving van vervolgvragen juist beperkt tot verwijswoorden, omdat ze in een vijfde van de gevallen het gesprek voortzette in plaats van een zoekvraag te maken (AJ 2.6); de sporen laten zien dat die beperking het onderwerp kost, en de interne route heeft intussen een herschrijving met een rem op het verliezen van het onderwerp. Stap C: het behouden van linkteksten is op 19 september gemeten en niet uitgerold omdat een beoordelend model geen voorkeur had (IQ §11); nu is er een maat in code die het gevolg van de gaten direct telt.

## 8. Uitkomsten per stap

Hier komt per stap van het plan: datum, verwachting vooraf, uitkomst op de meetlat, besluit, PR.

| Stap | Datum | Verwachting | Uitkomst | Besluit |
|---|---|---|---|---|
| 0 | 29 sep | Documenten gelijk aan de code | Acht afwijkingen gevonden door een tweede lezer, alle gecorrigeerd (#1775) | Vastgelegd |
| 1 | 29 sep | Een meetlat die de review weerspiegelt | Eén geval per beoordeeld antwoord, met de verwachte soort reactie en waar bekend het dragende artikel; naspelen door de echte route, score in code. De soort reactie alleen zegt weinig: die klopte bij de meeste gevallen al, ook waar het antwoord fout heette. De inhoud vraagt het oordeel van de eigenaar per geval | In gebruik; privé |
| 2 | 29 sep | Zien wat elke stap bijdraagt | Nulmeting, twee rondes. De reparatie draait op ruim een derde van de beurten; in meer dan de helft daarvan verliest het antwoord stappen en de mediane lengte halveert ongeveer. Een op de zeven beslissingen verschilt tussen twee rondes op dezelfde vraag | Reparatie is het eerste onderdeel om te vervangen (stap 9) |
| 3 | 29 sep | Minder reparaties als de controle zinnen overslaat die niets beweren | Negen op de tien reparaties halen de drempel ook op echte beweringen | Vervallen |
| 4 | 29 sep | Een gefrustreerde bezoeker krijgt een gewoon antwoord met de knop | Op de beurten met een negatieve toon geen winst in de soort reactie, en de uitkomst verschilt tussen runs meer dan het effect | Niet uitgerold; wacht op het oordeel van de eigenaar over die beurten |
| 5 | 29 sep | Elk "niet gevonden" met knop noemt de afspraak | Van ruim acht op de tien naar ruim negen op de tien; wat overblijft zijn antwoorden die hun bron hielden, en daar verandert een beoordelaar de tekst niet | Uitgerold |
| 6 | 29 sep | Geen aanbod "breder zoeken" waar het niets kan doen | Van een op de tien beurten naar alleen de beurt waar het zoeken niets vond | Uitgerold |
| 7 | 29 sep | Geen excuus waar niets misging | Het voorbeeld uit het profiel en de excuusregel uit de frustratie-instructie zijn weg. Eerste antwoorden die met een excuus openen: van enkele per ronde naar nul op de beurten met een negatieve toon, waar ze allemaal vandaan kwamen | Uitgerold |
| 8 | 29 sep | Hooguit één vraag per gesprek; het record zegt wat de bezoeker zag | Vaste code met tests; het record bewaart de knop, de toon en of een geplande vraag gesteld is | Uitgerold |
| 10 | 29 sep | Ons kleine model kan zien of de gevonden passages de vraag beantwoorden | Eerste proef, één ronde, één gesloten vraag per geval, zonder productcode. Waar de eigenaar "terecht niet gevonden" zei, zeiden het kleine en het middelgrote model dat bijna altijd ook. Van de verkeerd gelezen vragen herkende het kleine model ruim de helft en het middelgrote driekwart als "staat niet in deze passages". Maar van de antwoorden die de eigenaar goed noemde, keurde het kleine model een derde af en het middelgrote een kwart; op eerste beurten een kwart en een achtste. De twee modellen waren het in zeven op de tien gevallen eens | Niet bouwen als poort. Wel bruikbaar als signaal naast de score: het ziet verkeerd gelezen vragen die de score mist. Eerst de gevallen lezen waar proef en oordeel van de eigenaar botsen |
| 11 | 29 sep | Controle in code op harde feiten kan de controle per zin vervangen | Gemeten op de nagespeelde antwoorden: een vijfde van de zinnen die de controle afkeurt bevat een hard feit (getal, link, naam van een menu of knop); de rest is een bewering in gewone woorden die code niet kan toetsen. Van de harde feiten in antwoorden staat een derde niet letterlijk in de gevonden passages, waarbij andere schrijfwijzen meetellen | De controle in code dekt een smal deel en kan de controle per zin niet vervangen. Bruikbaar als extra, goedkope toets op verzonnen getallen en namen |
| – | 30 sep | Een blinde beoordelaar kan zeggen of het concept of de gerepareerde versie beter is | Twee rondes, beide volgordes, met de passages ernaast: de beoordelaar koos vrijwel altijd het concept. Maar in een kwart van de gevallen haalde de reparatie getallen of namen weg die niet in de passages stonden, en koos hij toch het concept | De beoordelaar verkiest het rijkere antwoord; bruikbaar als aanwijzing, niet als oordeel |
| – | 30 sep | De lijsten in antwoorden komen uit de artikelen | Per lijstregel teruggezocht in de passages van dezelfde beurt: vier op de tien staan er vrijwel letterlijk in, een derde deels, een kwart nergens | De oorzaak ligt vóór het schrijven; aanleiding voor het lezen van de sporen |
| – | 30 sep | Waar gaat het op het pad voor het eerst mis | Zie §2, "Wat de sporen lieten zien" | Plan herzien, stappen A tot en met G |
| A | 30 sep | Een vervolgbeurt vindt het juiste artikel als de zoekvraag het onderwerp van het gesprek meeneemt | Eerst alleen het zoeken, op alle vervolgbeurten uit de review. De herschrijving van de interne chat in plaats van de huidige zoekvraag: evenveel gewonnen als verloren. Het onderwerp als extra zoekgang ernaast (de eerdere woorden van de bezoeker voor de laatste zin, plus die herschrijving): volgens een beoordelend model vaker een passage die de vraag beantwoordt, ruim boven de ruis van een herhaalde meting. Daarna gebouwd en van begin tot eind nagespeeld, twee rondes met en twee zonder: de soort reactie verschilt niet meer dan de rondes onderling, het aantal beurten met zwakke bronnen halveert bijna, en een beurt duurt een halve tot een hele seconde langer. Bij het lezen van de antwoorden waar het zoeken won, was het antwoord één keer beter en meerdere keren slechter: de extra zoekgang haalt hoog scorende passages binnen over hetzelfde onderwerp maar een andere vraag (inkomend waar uitgaand gevraagd is, de app waar het om een bureautelefoon gaat), en het model schrijft daar dan een stellig antwoord uit waar eerst eerlijk "niet gevonden" stond | Niet uitgerold. De bottleneck op vervolgbeurten is niet dat er te weinig gevonden wordt, maar dat niets op het pad ziet dat een hoog scorende passage een andere vraag beantwoordt. Dat is stap E, en die schuift naar voren |
| F | 30 sep | Zonder de reparatie houdt een afgekeurd antwoord zijn stappen | De reparatie op de helpwidget is vervangen door een vaste regel in code: het antwoord gaat uit zoals geschreven met de afspraak eronder, en wordt alleen de weigering als geen enkele uitspraak onderbouwd is of één een artikel tegenspreekt. Nagespeeld op alle beurten waar de reparatie eerder draaide: evenveel weigeringen als met reparatie, in ruim twee derde het volledige concept met de zin over de afspraak, de mediane lengte een zesde hoger, en een beurt gemiddeld een seconde sneller omdat een modelaanroep vervalt. Gelezen: waar de reparatie een procedure tot losse koppen had teruggebracht, staat de procedure er weer. Wat blijft: een onbewezen zin wordt niet meer weggehaald, en een gat in de brontekst ("Ga naar.") is nu zichtbaar in het antwoord | Uitgerold op de widget; de interne chat houdt de reparatie tot stap G |
| Kiezen vóór schrijven | 30 sep | Als een stap vóór het schrijven kiest welke passages de vraag beantwoorden, verzint het model niet meer | Eerst gemeten waar het verzinnen ontstaat: in bijna de helft van de beurten waarin het model schreef, bevatte geen enkele passage het antwoord, en in de meerderheid daarvan was de score hoog genoeg om geen waarschuwing te geven. Daarna dezelfde beurten met dezelfde passages nagespeeld in vier varianten. Alleen opschonen in code (dubbel, losse feitzinnen, ander product) hielp niet en kostte antwoorden waarvan zo'n passage de enige bron was. Kiezen plus een opdracht zonder de regel voor de slotzin: antwoorden die grotendeels onbewezen waren gingen van een vijfde naar bijna nul, en het aandeel afgekeurde zinnen halveerde. De prijs: de keuzestap zegt in ruim vier op de tien beurten "staat er niet in"; bij de gelezen gevallen klopte dat, en het legt bloot hoe vaak het zoeken het juiste artikel niet aanlevert | Gebouwd (keuzestap, vaste tekst bij "staat er niet in", één vraag bij "hangt af van", profiel zonder slotzin). Nog niet uitgerold: de meting van begin tot eind wacht op het dagtegoed van het meetmodel |

Wat dit voor het ontwerp van §5 betekent: twee aannames zijn gemeten en houden niet zoals ze er stonden. Het beslismoment (stap 3 van het ontwerp) kan met ons model geen harde poort zijn, en de controle in code (stap 5 van het ontwerp) vangt maar een deel. Het ontwerp blijft de richting, maar de vervanging van de reparatie (stap 9) kan niet op deze twee leunen en hangt af van het oordeel van de eigenaar over concept tegenover gerepareerd antwoord.

Over alle gevallen samen bleef de soort reactie op gevallen die de eigenaar goed noemde gelijk, in beide rondes. De meetruns draaiden op het Vibe-tegoed met hetzelfde model als het antwoordmodel; de controles kregen daar meer tijd dan in productie, omdat die sleutel trager antwoordt.

## 9. Wat in de logboeken staat en hier is samengevat

De volledige metingen tot en met 25 september staan in AJ (2.1 tot en met 2.55) en IQ (§1 tot en met §19). De lessen die daaruit blijven gelden:

1. Een opdracht aan het antwoordmodel verandert zelden zijn gedrag; een aparte stap of een regel in code wel (AJ 2.4, 2.5, 2.12, 2.46).
2. Meer gevonden is niet hetzelfde als beter beantwoord (AJ 2.32; IQ §11, §14, §19).
3. Eén ronde is geen meting; een verschil dat tussen rondes omslaat is ruis (AJ 2.32, §6).
4. De meting moet de route nemen die de gebruiker neemt: de herformuleringen waren drie dagen "live" zonder dat de browser ze kreeg (AJ 2.41).
5. Een beoordelaar krijgt alleen wat de gebruiker leest (replay interne chat, 25 september).
6. De kennisbank is voor ongeveer één op de negen kennisvragen de grens: het antwoord staat er niet (AJ 2.31, 2.36).
