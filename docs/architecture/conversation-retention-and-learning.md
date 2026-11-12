# Gesprekken bewaren en ervan leren

Stand: 12 november 2026, gecontroleerd tegen de code na #2366. Wie wil weten hoe lang iets uit een widgetgesprek blijft staan, leest hoofdstuk 1 en hoeft nergens anders te kijken. Dit document beschrijft wat draait en zegt niets over hoe het zo gekomen is: dat staat in [evolution.md](evolution.md) en [chat-quality-history-and-plan.md](chat-quality-history-and-plan.md) (§8 en §8a, "Gesprekken bewaren: één termijn"), en wat andere systemen doen staat in [conversation-retention-other-systems.md](../research/conversation-retention-other-systems.md). Het gaat over de gesprekken van de chatwidget en wat daarvan is afgeleid. De telemetrie van zoekvragen heeft haar eigen regels in [docs/privacy/telemetry-modes.md](../privacy/telemetry-modes.md) en staat hier buiten. Paden zonder voorvoegsel liggen onder `klai-portal/backend/app/`.

## 1. De regel

Alles waar tekst van een bezoeker of een contactgegeven in staat, blijft hooguit de bewaartermijn van de organisatie staan. Die termijn is negentig dagen, en een organisatie kan een eigen termijn tussen 1 en 365 dagen krijgen. Daarna blijven alleen cijfers en labels over. Er zijn geen uitzonderingen per soort gesprek.

| Wat | Hoe lang | Waar |
|---|---|---|
| Berichten van het gesprek, met het spoor van elk antwoord | de termijn, gerekend vanaf het bericht | `widget_messages` |
| Naam en e-mailadres van de bezoeker, de eerste vraag, de hash van IP-adres en browser | de termijn, gerekend vanaf het laatste bericht | `widget_conversations` |
| Toelichting bij het automatische oordeel | tot de berichten van het gesprek weg zijn, en nooit langer dan de termijn vanaf het oordeel | `conversation_quality_judgments.reasoning` |
| Het gespreksrecord zonder die velden: tijdstip, aantal berichten, taal, afloop | onbeperkt | `widget_conversations` |
| Het automatische oordeel zelf: uitkomst, soort fout, zekerheid en het korte advies voor de redacteur van de kennisbank (`suggested_action`) | onbeperkt | `conversation_quality_judgments` |
| Het oordeel van een medewerker: oordeel, oorzaak en de eigen notitie | onbeperkt | `answer_reviews` |
| Testgevallen van de meetlat | onbeperkt, omdat ze nieuw geschreven zijn en naar geen gesprek terugwijzen (hoofdstuk 5) | buiten deze repository |

Het advies bij het automatische oordeel is een aanwijzing van de beoordelaar aan de redacteur (bijvoorbeeld dat een artikel ontbreekt) en geen citaat; alleen de toelichting moet het gesprek citeren (`services/conversation_judge.py:174-180`), en die wordt gewist. De notitie van een medewerker is tekst van de medewerker en geen tekst van de bezoeker. Wie een notitie schrijft, citeert de bezoeker niet en noemt geen naam.

## 2. De termijn en de opruimronde

### 2.1 Eén termijn

`settings.widget_messages_retention_days` is 90 (`core/config.py:419-426`, omgevingsvariabele `PORTAL_API_WIDGET_MESSAGES_RETENTION_DAYS`). De instelling per organisatie (`portal_orgs.widget_messages_retention_days`, 1 tot 365, door Klai-medewerkers in te stellen via `PATCH /api/admin/orgs/{slug}/widget-retention`, `api/admin/widget_retention.py:79`) overschrijft dat. De effectieve termijn van een organisatie is overal `COALESCE(portal_orgs.widget_messages_retention_days, settings.widget_messages_retention_days)`: in de opruimronde (`services/widget_messages_retention.py:53` en `:227`), in de selectie van het nachtelijke oordeel (`services/conversation_judge.py:540`) en in het getal dat de widget te zien krijgt en de termijn in de tickettekst. Die twee laatste gebruiken `effective_retention_days` (`services/widget_messages_retention.py:74-80`), de ene Python-plek voor "eigen termijn, anders de standaard": beide configuratie-antwoorden (`api/partner_widget_config.py:415` en `:563`) en `_ticket_text` (`api/app_activity.py:1479`) roepen hem aan.

Een organisatie met een termijn korter dan het venster van de lijst Kennisgaten (dertig dagen) ziet in die lijst hooguit wat ze bewaart: de termijn gaat voor.

### 2.2 De opruimronde

De dagelijkse ronde (`widget_messages_retention_loop`, `services/widget_messages_retention.py:293`, interval `:46`, de eerste keer na 60 seconden) wist per organisatie, met de effectieve termijn van die organisatie:

1. De berichten ouder dan de termijn, in stukken van 10.000 (`:48`, `:179`), en in dezelfde transactie de toelichting bij het oordeel over hun gesprek (`_purge_org_chunk`, `:89`, de update op `:109-119`, het verwijderen op `:121-129`). Mislukt het anonimiseren, dan blijven de berichten staan tot een volgende ronde.
2. Van elk gespreksrecord waarvan het laatste bericht ouder is dan de termijn: `visitor_name`, `visitor_email`, `first_user_query`, `ip_hash` en `user_agent_hash` (`_expire_org`, `:134`, de update op `:149-162`). Dit gaat op de leeftijd van het record zelf en niet via de berichten die in deze ronde verdwijnen, omdat een record waarvan de berichten al weg zijn anders nooit meer wordt aangeraakt.
3. Van elk oordeel, op elk kanaal, waarvan `judged_at` ouder is dan de termijn: `reasoning`, met `anonymized_at` op het moment van wissen (`:163-174`). Dit dekt de interne gesprekken, die geen bericht in deze database hebben, en de oordelen waarvan het gesprek verwijderd is.

Stap 2 en 3 zoeken eerst de organisaties die nog iets te wissen hebben (`_ORGS_TO_EXPIRE_SQL`, `:52-71`, aangeroepen op `:255-268`) en draaien ook voor een organisatie waarvan stap 1 mislukte, omdat ze alleen wissen.

Het gespreksrecord en het oordeel zelf blijven staan. `session_key` blijft ook: het is een willekeurige sleutel waarop het record uniek is, en zonder de hashes zegt hij niets over de bezoeker.

Elke stap schrijft in een sessie die aan één organisatie gebonden is (`tenant_scoped_session`, `:116` en `:156`), omdat het beleid op `widget_conversations` en `conversation_quality_judgments` een schrijfactie vanuit een sessie zonder organisatie weigert. Elke stap heeft een wachttijd van 5 seconden op een slot en 60 seconden per opdracht (`_bound_waits`, `:83-95`). Een organisatie waar het misgaat wordt gelogd (`widget_messages_retention_org_failed`) en houdt de andere niet tegen. De logregel `widget_messages.retention_deleted` (`:279-289`) draagt de aantallen: `deleted_count`, `anonymized_judgments` (stap 1), `cleared_conversations` (stap 2), `anonymized_judgments_by_age` (stap 3) en `failed_org_ids`.

Gevolg voor wat het portaal toont: de tien meest gestelde vragen over "alles" en de lijst met gesprekken van een organisatie tonen van oudere gesprekken geen eerste vraag.

## 3. Wat de bezoeker leest

De stap "Voordat we beginnen" van de widget en van de deelpagina zegt wat er gebeurt, met de echte termijn van de organisatie:

> We bewaren dit gesprek 90 dagen om ons systeem beter te maken. Je e-mailadres gebruiken we om te zien bij welke klant het gesprek hoort, zodat we contact met je kunnen opnemen als dat nodig is.

> We keep this conversation for 90 days to improve our system. We use your e-mail address to see which customer the conversation belongs to, so we can contact you if needed.

De tekst belooft niet dat elk gesprek wordt nagelezen of dat een correctie per e-mail volgt, want dat gebeurt niet.

Contract: de twee configuratie-antwoorden die de widget en de deelpagina ophalen, `GET /partner/v1/widget-config` en `GET /partner/v1/public-bot-config`, hebben het veld `retention_days` (geheel getal, de effectieve termijn van de organisatie; `api/partner_widget_config.py:415` en `:563`, berekend door `effective_retention_days`, `services/widget_messages_retention.py:74`). De widget vult het getal in de tekst in met het juiste enkelvoud bij één dag (`userInfoHelpLabel`, `klai-widget/src/i18n/labels.ts:288`, teksten `:130-133` en `:199-202`, aangeroepen in `klai-widget/src/components/ChatWindow.tsx:738`, veld in `klai-widget/src/api/widget-config.ts`). De kopie in het portaal doet hetzelfde (`retentionTerm`, `klai-portal/frontend/src/features/widgets/chat/WidgetChatSurface.tsx:51`, gebruikt op `:565`; de zin in `klai-portal/frontend/messages/nl.json:2413` en `en.json`; de deelpagina geeft het veld door in `klai-portal/frontend/src/routes/bot/$widgetId.tsx`). Ontbreekt het veld, wat kan omdat de widget het antwoord een kwartier bewaart, dan staat er dezelfde zin zonder termijn ("We bewaren dit gesprek om ons systeem beter te maken.") en nooit een leeg sjabloon.

Los daarvan staat het vinkje "Onthoud mijn gegevens" in dezelfde stap. Vinkt de bezoeker het aan, dan bewaart de widget naam en e-mailadres in de `localStorage` van zijn eigen browser, zodat de velden bij een volgend bezoek zijn ingevuld (`klai-widget/src/store/chat.ts:99-102`, lezen in `normalizeIdentity`, `:226`). Dat heeft geen termijn: het staat op het apparaat van de bezoeker en blijft tot hij in die stap het vinkje uitzet of kiest voor "Liever niet, ga direct naar de chat", wat de velden en de opgeslagen gegevens wist (`clearStoredIdentity`, `:536`, aangeroepen in `klai-widget/src/components/ChatWindow.tsx:792`). De stap verschijnt alleen bij een gesprek zonder berichten (`ChatWindow.tsx:595-596`), dus een terugkerende bezoeker met een lopend gesprek komt er via "Nieuw gesprek"; een aparte wisknop is er niet. Zonder vinkje bewaart de browser geen naam of e-mailadres. De gesprekken zelf staan wel in de browser, los van dit vinkje (zie [chat-system.md](chat-system.md), §9).

## 4. Wat van de berichten afhangt

De lijst Kennisgaten leest de widgetbeurten bij elke aanvraag uit `widget_messages` zelf (`load_gap_turns`, `services/knowledge_gap_report.py:414`, venster `:451`) en houdt niets apart vast. Een beurt telt dus zolang haar berichten bestaan, ook als dat korter is dan het venster van dertig dagen.

Het nachtelijke oordeel (`_judge_org`, `services/conversation_judge.py:510`) kiest alleen gesprekken die binnen de termijn van de organisatie vallen, min een marge van zes uur (`:539-541`, `:106`), en die nog minstens één bericht hebben (`EXISTS` op `widget_messages`, `:544-549`). Een gesprek waarvan de berichten al weg zijn kan anders weer binnen het venster vallen zodra een termijn groeit, en dan zou de beoordelaar een model aanroepen op een leeg gesprek.

## 5. Leren

Drie dingen dragen het leren.

**De gesprekken zelf, negentig dagen.** De weeklezing, het automatische oordeel van elke nacht en het oordeel van een medewerker lezen echte gesprekken. Negentig dagen is genoeg om een fout terug te vinden, een maand met de vorige te vergelijken en een oordeel na te lopen.

**Het evolutiedocument.** [chat-quality-history-and-plan.md](chat-quality-history-and-plan.md) legt per wijziging de verwachting, de uitkomst en het besluit vast. Dat is het geheugen dat langer meegaat dan negentig dagen: het bewaart wat er geleerd is en niet het gesprek waaraan het geleerd is.

**De meetlat, met een testset van nieuw geschreven gevallen.** De meetlat speelt gevallen af tegen een nieuwe versie. Een geval is een vraag met de verwachte reactie en het artikel dat het antwoord draagt. Een geval in de testset is nieuw geschreven en wijst naar geen gesprek terug.

Er is geen opslag van afgeschermde gesprekken, geen tabel met gekozen gevallen en geen automatische doorzet van een gesprek naar de testset. Afgeschermde vrije tekst blijft herleidbaar, en een regel per soort geval zou weer een uitzondering op de regel van hoofdstuk 1 zijn. Wat blijft is de les uit het gesprek, opgeschreven als een nieuw geval.

### 5.1 Wat een geval in de testset mag bevatten

- De vraag is opnieuw geformuleerd in algemene woorden over het product. Geen zin of zinsdeel van de bezoeker wordt overgenomen.
- Geen naam, e-mailadres, telefoonnummer, klantnummer, adres, bedrijfsnaam van de bezoeker, datum of bedrag uit het gesprek. Is een voorbeeldwaarde nodig, dan is ze verzonnen.
- Geen gespreksnummer, geen tijdstip en geen ander veld dat terugwijst. Het evolutiedocument noemt hooguit hoeveel gevallen een week opleverde en voor welk patroon.
- Eén geval per patroon van fouten en niet één per gesprek. Kan de les alleen verteld worden met de bijzondere omstandigheden van één bezoeker, dan komt er geen geval.

`meetlat.py check-cases` (`klai-portal/backend/scripts/meetlat.py:1303`, regels in `check_case_messages`, `:1258`) bewijst de eerste twee punten voor elk geval dat erbij komt: geen bericht van een geval bevat een e-mailadres (`:144`) of een reeks van zes of meer cijfers (`:1246`), en geen bericht deelt een reeks van zes of meer opeenvolgende woorden (`:1247`) met een bericht van een bezoeker dat nog in de database staat (`_visitor_texts`, `:1286`). Na de termijn is het gesprek weg en is er niets meer om mee te vergelijken. De werkwijze staat in [de meetlat](../runbooks/meetlat.md), paragraaf "Check that cases are newly written".

De testset staat buiten deze repository, omdat de vragen over het product van een klant gaan en de verwachte artikelen op het helpcentrum van die klant staan. Ze hoort in de besloten infrastructuurrepository, per organisatie één bestand in het formaat dat de meetlat al leest, zodat elke toevoeging een regel in de geschiedenis is.

### 5.2 De wekelijkse lezing als vaste werkwijze

De skill `chat-evolution` (`.agents/skills/chat-evolution/SKILL.md`) beschrijft de lezing die een agent elke week draait:

1. Lees de gesprekken van de afgelopen week: alle beurten met een oordeel "fout" van een medewerker, alle beurten die het automatische oordeel als mislukt aanmerkt, de beurten uit de lijst Kennisgaten, en een willekeurige greep uit de rest zodat ook gelezen wordt wat goed lijkt te gaan. De E2E-organisatie telt niet mee.
2. Volg elk gelezen geval van begin tot eind en leg de eerste plek op het pad vast waar het misgaat, zoals de werkafspraken voorschrijven.
3. Groepeer tot patronen en vergelijk met de testset: een patroon dat er al een geval voor heeft, krijgt er geen bij.
4. Schrijf voor elk nieuw patroon één geval volgens 5.1 en draai `check-cases`.
5. Schrijf de lezing bij in het evolutiedocument: wat gelezen is, welke patronen, wat erbij kwam.
6. Werkbestanden met echte gesprekken staan buiten de repository en gaan weg zodra de lezing klaar is.

De keuze welke gesprekken gelezen worden is automatisch en volgt uit wat het systeem al vastlegt. Het oordeel of iets een nieuw patroon is en hoe het geval luidt, is leeswerk van de agent, met de controle van stap 4 als bewijs dat er niets herleidbaars in staat.

## 6. Wat bewust niet gebouwd is

| Wat | Waarom niet | Wanneer wel |
|---|---|---|
| Afgeschermde gesprekken langer bewaren dan de termijn | Afgeschermde vrije tekst blijft herleidbaar, en het zou weer een uitzondering zijn | Niet, zolang de testset het leren draagt |
| Een tabel met gekozen gevallen in het product | Eén bestand per organisatie met geschiedenis doet hetzelfde | Als klanten hun eigen testset moeten kunnen beheren |
| De wekelijkse lezing op een rooster zonder mens | De lezing heeft toegang tot productie nodig vanaf een ontwikkelmachine | Als de lezing als taak op de server kan draaien |
| De bestaande kopieën van echte gesprekken van de meetlat omzetten | Dat is de eerste run van de skill en vraagt twee meetrondes op de hoofdversie om de nieuwe basis vast te leggen; tot dan staan de kopieën nog als losse bestanden op de server | Bij de eerste run van `chat-evolution`; daarna krijgt die map dezelfde termijn als de gesprekken |
