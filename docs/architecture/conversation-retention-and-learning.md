# Gesprekken bewaren en ervan leren

Stand: 12 november 2026. Dit document is het ontwerp en daarna het overzicht: wie wil weten hoe lang iets uit een widgetgesprek blijft staan, leest hoofdstuk 1 en hoeft nergens anders te kijken. Het gaat over de gesprekken van de chatwidget en wat daarvan is afgeleid. De telemetrie van zoekvragen heeft haar eigen regels in [docs/privacy/telemetry-modes.md](../privacy/telemetry-modes.md) en verandert hier niet.

## 1. De regel

Alles waar tekst van een bezoeker of een contactgegeven in staat, blijft hooguit de bewaartermijn van de organisatie staan. Die termijn is negentig dagen, en een organisatie kan een eigen termijn tussen 1 en 365 dagen krijgen. Daarna blijven alleen cijfers en labels over. Er zijn geen uitzonderingen per soort gesprek.

| Wat | Hoe lang | Waar |
|---|---|---|
| Berichten van het gesprek, met het spoor van elk antwoord | de termijn, gerekend vanaf het bericht | `widget_messages` |
| Naam en e-mailadres van de bezoeker, de eerste vraag, de hash van IP-adres en browser | de termijn, gerekend vanaf het laatste bericht | `widget_conversations` |
| Toelichting bij het automatische oordeel | tot de berichten van het gesprek weg zijn, en nooit langer dan de termijn vanaf het oordeel | `conversation_quality_judgments.reasoning` |
| Het gespreksrecord zonder die velden: tijdstip, aantal berichten, taal, afloop | onbeperkt | `widget_conversations` |
| Het automatische oordeel zelf: uitkomst, soort fout, zekerheid | onbeperkt | `conversation_quality_judgments` |
| Het oordeel van een medewerker: oordeel, oorzaak en de eigen notitie | onbeperkt | `answer_reviews` |
| Testgevallen van de meetlat | onbeperkt, omdat ze nieuw geschreven zijn en naar geen gesprek terugwijzen (hoofdstuk 5) | buiten deze repository |

De notitie van een medewerker is tekst van de medewerker en geen tekst van de bezoeker. Wie een notitie schrijft, citeert de bezoeker niet en noemt geen naam.

## 2. Wat er was

Tot deze wijziging gold zeven dagen, met een uitzondering van vier weken voor de vraag bij een open kennisgat. De database liet op 12 november 2026 zien dat de praktijk rommeliger was dan de regel:

| Wat | Regel | In de database |
|---|---|---|
| Berichten | 7 dagen | klopte, het oudste bericht was 7,0 dagen oud |
| Eerste vraag en hash van IP-adres en browser | geen regel, nooit gewist | 3631 gespreksrecords ouder dan de termijn droegen nog zo'n veld |
| Toelichting bij het oordeel over een widgetgesprek | weg samen met de berichten | 105 oudere stonden er nog, omdat hun berichten gewist waren voordat het wissen van de toelichting bestond, of omdat het gesprek zelf verwijderd was |
| Toelichting bij het oordeel over een intern gesprek | geen regel | 554 ouder dan de termijn |
| Vraag bij een open kennisgat | 28 dagen, afgeschermd | 10 |
| Echte gesprekken van de meetlat | buiten de repository, zonder termijn | losse bestanden op de server, het oudste van 29 september |

Twee documenten spraken de praktijk tegen. De notitie bij de instelling per organisatie noemde negentig dagen voor de eerste organisatie met een widget, terwijl die waarde nooit is ingevoerd. En de verwerkersbijlage beloofde dat niets uit de letterlijke vraag langer dan zeven dagen blijft, terwijl de eerste vraag van elk gesprek bleef staan.

Voor het leren was zeven dagen te kort. Het oordeel over een antwoord bleef, maar het geval zelf was na een week weg, zodat een fout nooit meer tegen een nieuwe versie af te spelen was en het automatische oordeel achteraf niet te controleren. Een overgeslagen weeklezing was verloren.

## 3. Wat andere systemen doen

Onderzocht op 12 november 2026 in de documentatie van de leveranciers zelf.

| Wie | Gesprek | Wat daarna blijft |
|---|---|---|
| Intercom Fin | onbeperkt, tot de klant zelf verwijdert | de cijfers blijven standaard ook na verwijderen |
| Ada | twee jaar | langer als de persoonsgegevens eruit zijn |
| Microsoft Copilot Studio | dertig dagen, door de klant te verlengen | afloop en tevredenheid per gesprek |
| Zendesk | de klant stelt een verwijderschema in, dertig dagen als voorbeeld | niet gevonden |
| Langfuse en LangSmith (meetgereedschap) | kort, bij LangSmith veertien dagen in de basis | wat in een testset is gezet, blijft onbeperkt |

Drie dingen keren terug. De kortste standaard die gevonden is, is dertig dagen. Wie van gesprekken leert, scheidt de ruwe gesprekken (kort) van een bewust gekozen testset (lang). En toezichthouders noemen geen termijn maar eisen doel en noodzaak, waarbij afgeschermde vrije tekst een persoonsgegeven blijft; van de Nederlandse toezichthouder is hierover niets gevonden. Dat laatste is de reden dat hoofdstuk 5 geen afgeschermde kopieën bewaart.

## 4. Ontwerp

### 4.1 Eén termijn

`settings.widget_messages_retention_days` gaat van 7 naar 90. De instelling per organisatie (`portal_orgs.widget_messages_retention_days`, 1 tot 365, in te stellen op het platformbeheerscherm) blijft zoals ze is. De effectieve termijn van een organisatie is overal `COALESCE(portal_orgs.widget_messages_retention_days, settings.widget_messages_retention_days)`.

Een organisatie met een termijn korter dan het venster van de lijst Kennisgaten (dertig dagen) ziet in die lijst hooguit wat ze bewaart. Dat is de bedoeling: de termijn gaat voor.

### 4.2 De opruimronde

De dagelijkse ronde in `app/services/widget_messages_retention.py` wist per organisatie, met de effectieve termijn van die organisatie:

1. De berichten ouder dan de termijn (bestaand), en in dezelfde stap de toelichting bij het oordeel over hun gesprek (bestaand).
2. Van elk gespreksrecord waarvan het laatste bericht ouder is dan de termijn: `visitor_name`, `visitor_email`, `first_user_query`, `ip_hash` en `user_agent_hash`. Dit gaat op de leeftijd van het record zelf en niet via de berichten die in deze ronde verdwijnen, omdat een record waarvan de berichten al weg zijn anders nooit meer wordt aangeraakt.
3. Van elk oordeel, op elk kanaal, waarvan `judged_at` ouder is dan de termijn: `reasoning`, met `anonymized_at` op het moment van wissen. Dit dekt de interne gesprekken, die geen bericht in deze database hebben, en de oordelen waarvan het gesprek verwijderd is.

Het record en het oordeel zelf blijven staan. `session_key` blijft ook: het is een willekeurige sleutel waarop het record uniek is, en zonder de hashes zegt hij niets over de bezoeker.

Elke stap schrijft in een sessie die aan één organisatie gebonden is, zoals de ronde nu al doet, en een organisatie waar het misgaat houdt de andere niet tegen. De logregel `widget_messages.retention_deleted` krijgt de aantallen van stap 2 en 3 erbij.

Gevolg voor wat het portaal toont: de tien meest gestelde vragen over "alles" en de lijst met gesprekken van een organisatie tonen van oudere gesprekken geen eerste vraag meer.

### 4.3 Wat verdwijnt

De bewaarde vraag bij een open kennisgat (`portal_gap_kept_questions`, ingevoerd op 11 november 2026) bestond omdat de berichten na zeven dagen weg waren terwijl een kennisgat vier weken open kan staan. Met negentig dagen leest de lijst Kennisgaten elke beurt van haar venster uit de berichten zelf, en houdt de tabel niets meer vast. Ze gaat er daarom helemaal uit: de tabel, het bewaren in de opruimronde, het lezen in de lijst, het weggooien bij een besluit over een groep, bij het markeren van een testgesprek en bij het verlaten van telemetrieniveau `full`, de dagelijkse opschoning ervan, de tests en de regels in de CI die de tabel nabootsen. De tien rijen die er op 12 november in stonden, horen bij gesprekken waarvan de berichten al gewist zijn; die vragen verdwijnen uit de lijst.

Het verwijderen gaat in twee wijzigingen. De eerste haalt alle code weg die de tabel leest of schrijft; de tabel zelf blijft dan nog staan. De tweede verwijdert de tabel. In één wijziging kan het niet, omdat het uitrolscript elk `post_deploy`-script draait voordat de nieuwe code start: de oude code zou dan een tabel lezen die er niet meer is, en een automatische terugrol zou die oude code terugzetten op een database zonder tabel. De tweede wijziging bestaat uit een markering in de migratieketen, een `post_deploy`-script dat de tabel verwijdert, en een aanpassing van het eerdere script dat de tabel zijn eigenaar en rechten geeft, omdat dat bij elke uitrol opnieuw draait en zonder tabel zou falen.

De teksten in het portaal bij de telemetrie-instelling noemen de vier weken niet meer.

### 4.4 Wat de bezoeker leest

De stap "Voordat we beginnen" zegt wat er gebeurt, met de echte termijn van de organisatie:

> We bewaren dit gesprek 90 dagen om ons systeem beter te maken. Je e-mailadres gebruiken we om te zien bij welke klant het gesprek hoort, zodat we contact met je kunnen opnemen als dat nodig is.

> We keep this conversation for 90 days to improve our system. We use your e-mail address to see which customer the conversation belongs to, so we can contact you if needed.

De belofte dat elk gesprek wordt nagelezen en dat een correctie per e-mail volgt, verdwijnt, omdat dat niet gebeurt.

Contract: de twee configuratie-antwoorden die de widget en de deelpagina ophalen (`GET /partner/v1/widget-config` en `GET /partner/v1/public-bot-config`) krijgen het veld `retention_days` (geheel getal, de effectieve termijn van de organisatie). De widget en de kopie in het portaal vullen het getal in de tekst in, met het juiste enkelvoud bij één dag. Ontbreekt het veld, wat tijdens een uitrol kan omdat de widget het antwoord een kwartier bewaart, dan staat er dezelfde zin zonder termijn ("We bewaren dit gesprek om ons systeem beter te maken.") en nooit een leeg sjabloon.

## 5. Leren

Drie dingen dragen het leren, en alle drie bestonden al.

**De gesprekken zelf, negentig dagen.** De weeklezing, het automatische oordeel van elke nacht en het oordeel van een medewerker lezen echte gesprekken. Negentig dagen is genoeg om een fout terug te vinden, een maand met de vorige te vergelijken en een oordeel na te lopen.

**Het evolutiedocument.** [chat-quality-history-and-plan.md](chat-quality-history-and-plan.md) legt per wijziging de verwachting, de uitkomst en het besluit vast. Dat is het geheugen dat langer meegaat dan negentig dagen: het bewaart wat er geleerd is en niet het gesprek waaraan het geleerd is.

**De meetlat, met een testset van nieuw geschreven gevallen.** De meetlat speelt gevallen af tegen een nieuwe versie. Een geval is een vraag met de verwachte reactie en het artikel dat het antwoord draagt. Tot nu toe waren dat kopieën van echte gesprekken in losse bestanden op de server. Dat houdt op: een geval in de testset is voortaan nieuw geschreven en wijst naar geen gesprek terug.

Er komt bewust geen opslag van afgeschermde gesprekken, geen tabel met gekozen gevallen en geen automatische doorzet van een gesprek naar de testset. Afgeschermde vrije tekst blijft herleidbaar, en een regel per soort geval is precies de rommel van hoofdstuk 2. Wat blijft is de les uit het gesprek, opgeschreven als een nieuw geval.

### 5.1 Wat een geval in de testset mag bevatten

- De vraag is opnieuw geformuleerd in algemene woorden over het product. Geen zin of zinsdeel van de bezoeker wordt overgenomen.
- Geen naam, e-mailadres, telefoonnummer, klantnummer, adres, bedrijfsnaam van de bezoeker, datum of bedrag uit het gesprek. Is een voorbeeldwaarde nodig, dan is ze verzonnen.
- Geen gespreksnummer, geen tijdstip en geen ander veld dat terugwijst. Het evolutiedocument noemt hooguit hoeveel gevallen een week opleverde en voor welk patroon.
- Eén geval per patroon van fouten en niet één per gesprek. Kan de les alleen verteld worden met de bijzondere omstandigheden van één bezoeker, dan komt er geen geval.

`meetlat.py check-cases` bewijst de eerste twee punten voor elk geval dat erbij komt: geen bericht van een geval bevat een e-mailadres of een lange cijferreeks, en geen bericht deelt een reeks van zes of meer opeenvolgende woorden met een bericht van een bezoeker dat nog in de database staat. Na de termijn is het gesprek weg en is er niets meer om mee te vergelijken.

De testset staat buiten deze repository, omdat de vragen over het product van een klant gaan en de verwachte artikelen op het helpcentrum van die klant staan. Ze hoort in de besloten infrastructuurrepository, per organisatie één bestand in het formaat dat de meetlat al leest, zodat elke toevoeging een regel in de geschiedenis is.

### 5.2 De wekelijkse lezing als vaste werkwijze

De werkafspraak "elke week tien tot twintig echte gesprekken lezen; elke nieuwe fout wordt een vast geval in de meetlat" had geen beschreven werkwijze. Die komt er als de skill `chat-evolution`, die een agent elke week draait:

1. Lees de gesprekken van de afgelopen week: alle beurten met een oordeel "fout" van een medewerker, alle beurten die het automatische oordeel als mislukt aanmerkt, de beurten uit de lijst Kennisgaten, en een willekeurige greep uit de rest zodat ook gelezen wordt wat goed lijkt te gaan. De E2E-organisatie telt niet mee.
2. Volg elk gelezen geval van begin tot eind en leg de eerste plek op het pad vast waar het misgaat, zoals de werkafspraken voorschrijven.
3. Groepeer tot patronen en vergelijk met de testset: een patroon dat er al een geval voor heeft, krijgt er geen bij.
4. Schrijf voor elk nieuw patroon één geval volgens 5.1 en draai `check-cases`.
5. Schrijf de lezing bij in het evolutiedocument: wat gelezen is, welke patronen, wat erbij kwam.
6. Werkbestanden met echte gesprekken staan buiten de repository en gaan weg zodra de lezing klaar is.

De keuze welke gesprekken gelezen worden is daarmee automatisch en volgt uit wat het systeem al vastlegt. Het oordeel of iets een nieuw patroon is en hoe het geval luidt, is leeswerk van de agent, met de controle van stap 4 als bewijs dat er niets herleidbaars in staat.

## 6. Wat bewust niet gebouwd is

| Wat | Waarom niet | Wanneer wel |
|---|---|---|
| Afgeschermde gesprekken langer bewaren dan de termijn | Afgeschermde vrije tekst blijft herleidbaar, en het zou weer een uitzondering zijn | Niet, zolang de testset het leren draagt |
| Een tabel met gekozen gevallen in het product | Eén bestand per organisatie met geschiedenis doet hetzelfde | Als klanten hun eigen testset moeten kunnen beheren |
| De wekelijkse lezing op een rooster zonder mens | De lezing heeft toegang tot productie nodig vanaf een ontwikkelmachine | Als de lezing als taak op de server kan draaien |
| De bestaande kopieën van echte gesprekken van de meetlat omzetten | Dat is de eerste run van de skill en vraagt meetrondes om de nieuwe basis vast te leggen | Direct na deze wijziging, zie hoofdstuk 8 |

## 7. Meting

De metriek is het aantal records met tekst of een contactgegeven van een bezoeker dat ouder is dan de termijn van zijn organisatie, geteld in de productiedatabase.

| | Gespreksrecords met eerste vraag, contactgegeven of hash | Toelichtingen | Berichten |
|---|---|---|---|
| Nu, ouder dan de oude termijn van 7 dagen (12 november 2026) | 3631 | 659 | 0 |
| Nu, ouder dan de nieuwe termijn van 90 dagen (12 november 2026) | 65 | 0 | 0 |
| Verwacht na de eerste ronde met de nieuwe code | 0 | 0 | 0 |

Daarnaast groeit de leeftijd van het oudste bericht van 7,0 dagen naar 90 dagen op 3 februari 2027. Dat is alleen op de kalender te meten; verdwijnt er voor die datum een bericht dat jonger is dan de termijn, dan is de wijziging fout.

## 8. Uitrol en wat daarna nog moet

De volgorde is: code live, de telling van hoofdstuk 7 na de eerste opruimronde, en daarna de tweede wijziging die de tabel met bewaarde vragen verwijdert.

Na deze wijziging:

0. De tweede wijziging uit 4.3. Tot die live is, staan de tien bewaarde vragen nog in een tabel die niets meer leest; ze gaat uiterlijk mee voordat de oudste negentig dagen oud is (3 februari 2027).
1. De eerste run van `chat-evolution` zet de bestaande gevallen van de meetlat om naar nieuw geschreven gevallen en legt met twee rondes op de hoofdversie de nieuwe basis vast.
2. Daarna gaan de losse bestanden met echte gesprekken op de server weg, en krijgt die map dezelfde termijn als de gesprekken. Het verwijderen gebeurt pas na een akkoord op de lijst.
3. De verwerkersbijlage (`docs/privacy/dpa-telemetry-addendum.md`) is aangepast aan deze regel; een klant die de oude tekst getekend heeft, krijgt de nieuwe.
