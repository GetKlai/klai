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

## 4. Onderzoek naast onze praktijk

| Principe | Wat wij doen | Verschil |
|---|---|---|
| Niet schrijven als het antwoord er niet staat, beslist buiten het antwoordmodel | Beslissen op de zoekscore (alles onder 0,4), en het model daarna vragen "niet gevonden" te zeggen | De score meet gelijkenis; boven 0,4 wordt altijd geschreven |
| Weinig en juiste passages | Acht fragmenten, drie bronnen | Geen selectie op "beantwoordt dit de vraag" |
| Schrijven door het model, dicht op de bron | Vrij schrijven, daarna controle per zin | Geen verschil in wie schrijft; wel in wat er daarna gebeurt |
| Niet achteraf herschrijven | Een derde aanroep herschrijft bij twee of meer afkeuringen | Grootste foutsoort in de review |
| Alleen controleerbare uitspraken nakijken | De controle keurt ook eigen "niet gevonden"-zinnen af | Leidt tot weigeringen en rompen |
| Eén vraag, zelden, uit wat de artikelen onderscheidt | Een vraag als artikeltitels op elkaar lijken | Vroeg naar het apparaat bij een vraag over belrechten |
| Bij frustratie eerst helpen | Negatieve toon vervangt het antwoord door een afspraakaanbod en slaat de vraagstap en de regel voor zwakke bronnen over | Een oplosbaar probleem krijgt geen antwoord |
| Richting en apparaat als kenmerk | Geen kenmerken per artikel | Verkeerd gelezen vragen bij hoge score |
| Meten op echte gesprekken met menselijk oordeel | Meestal een model als rechter op nagespeelde beurten | Een daling bleef een week onopgemerkt |
| Interne chat: dezelfde zorgvuldigheid | Open-modus wordt niet gecontroleerd; twee routes naast elkaar | De nieuwe route haalde de maat voor de klanttenant niet |

## 5. Het ontwerp

Eén stroom voor beide chats. Alleen de uiteinden verschillen.

1. **Begrijpen.** Het model vult gesloten velden in: soort beurt (kennisvraag, verzoek om een mens, praatje, onderwerp dat niet behandeld wordt), de zelfstandige zoekvraag, en wat de bezoeker al noemde (apparaat, richting). Code beslist de route.
2. **Zoeken.** Zoals nu, met twee herformuleringen op de eerste beurt. Dit is het enige onderdeel met een sterke eigen meting.
3. **Eén beslismoment.** Een aparte stap kiest uit de gevonden passages: deze beantwoordt de vraag, geen enkele doet dat, of het hangt af van één feit dat de bezoeker kent. Bij "geen enkele" wordt het antwoordmodel niet aangeroepen; code geeft de vaste eerlijke tekst met de vervolgstap.
4. **Schrijven.** Het model schrijft het antwoord, kort en in gewone taal, uit alleen de gekozen passages.
5. **Nakijken in code.** Getallen, bedragen, namen van menu's en knoppen, links en het aantal stappen moeten in de bron voorkomen. Bij een afwijking wordt één keer opnieuw geschreven; lukt dat niet, dan wordt de passage zelf getoond met een korte inleiding.
6. **Toon.** Bij een negatieve toon blijft het antwoord staan en komt de knop eronder. Een verzoek om een mens gaat direct door.

| Punt in de stroom | Publieke chat | Interne chat |
|---|---|---|
| "Staat er niet" | Afspraakknop | Zeggen dat het niet in de kennisbank staat; in Open mag algemene kennis, duidelijk gelabeld |
| Weg naar een mens | Afspraakknop | Bestaat niet |
| Bronnen | Bronkaart | Volledige bronnenlijst |
| Toegang | Publieke kennis | De kennisbanken van de medewerker |
| Invoer | Korte vragen | Lange vragen en geplakte klantmails, eerst gedistilleerd |

**Wat vervalt als de meting het toelaat:** de antwoordbeoordelaar, de controle per zin als redacteur, en het herschrijven. De controle per zin blijft meekijken om te meten.

**Wat we niet weten** [aanname, te meten in stap 2 van het plan]: hoe goed ons model het beslismoment kan; hoeveel de controle in code vangt; hoeveel ons model nog verzint met alleen de juiste passage.

## 6. Werkafspraken

| Afspraak | Waarom |
|---|---|
| Elke bewering in een voorstel draagt een label uit de kop van dit document | Dan is zichtbaar waar een ontwerp op rust |
| Wie een voorstel doet, zegt wat hem van mening zou doen veranderen; een positie verandert op nieuw bewijs | Taalmodellen schuiven mee met tegenspraak: 58% in één meting [onderzoek: SycEval, https://arxiv.org/abs/2502.08177] |
| Eén meetlat: echte gesprekken met per gesprek de verwachting en het bewijs, vastgesteld door de eigenaar | Zonder vaste meetlat stuurt het laatste incident |
| Geen wijziging aan het chatpad zonder de meetlat; de uitkomst staat in de PR | De poort op papier is vijf keer overgeslagen |
| Eén wijziging, één verwachting, vooraf opgeschreven met het criterium om te stoppen | Anders is een daling niet toe te wijzen |
| Elke stap verdient zijn plek: wat op de meetlat niets bijdraagt, gaat eruit | Het systeem groeide alleen |
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
| 9 | Reparatie vervangen: één keer opnieuw schrijven, anders de passage tonen | 18 | M | Drie armen op de meetlat |
| 10 | Het beslismoment "staat het antwoord erin", eerst meekijkend | 12 en 6 | M | Overeenstemming met de verwachtingen van de meetlat |
| 11 | Schrijven uit alleen de gekozen passages; controle op harde feiten in code | 18 en 12 | M | Meetlat |
| 12 | Kenmerken per artikel (apparaat, richting, land) als filter; lijst voor de redactie | 12 | L | Zoekmeting en meetlat |
| 13 | Dezelfde stroom op de interne chat, met een eigen meetlat uit echte interne vragen; daarna de oude route uit | – | L | Meetlat intern |

Wat eerder is afgewezen en hier niet terugkomt: doorvragen als opdracht aan het antwoordmodel, een ruimer budget voor de controle, het samenvoegen van de twee controles achteraf, de zekerheidsband als reden om te weigeren, meer passages of een vierde bron. Stap 10 is een stap vóór het schrijven en geen samenvoeging van controles; stap 11 is een smallere invoer en een controle in code, geen andere prompt.

## 8. Uitkomsten per stap

Hier komt per stap van het plan: datum, verwachting vooraf, uitkomst op de meetlat, besluit, PR.

| Stap | Datum | Verwachting | Uitkomst | Besluit |
|---|---|---|---|---|
| 0 | 29 sep | Documenten gelijk aan de code | Acht afwijkingen gevonden door een tweede lezer, alle gecorrigeerd (#1775) | Vastgelegd |

## 9. Wat in de logboeken staat en hier is samengevat

De volledige metingen tot en met 25 september staan in AJ (2.1 tot en met 2.55) en IQ (§1 tot en met §19). De lessen die daaruit blijven gelden:

1. Een opdracht aan het antwoordmodel verandert zelden zijn gedrag; een aparte stap of een regel in code wel (AJ 2.4, 2.5, 2.12, 2.46).
2. Meer gevonden is niet hetzelfde als beter beantwoord (AJ 2.32; IQ §11, §14, §19).
3. Eén ronde is geen meting; een verschil dat tussen rondes omslaat is ruis (AJ 2.32, §6).
4. De meting moet de route nemen die de gebruiker neemt: de herformuleringen waren drie dagen "live" zonder dat de browser ze kreeg (AJ 2.41).
5. Een beoordelaar krijgt alleen wat de gebruiker leest (replay interne chat, 25 september).
6. De kennisbank is voor ongeveer één op de negen kennisvragen de grens: het antwoord staat er niet (AJ 2.31, 2.36).
