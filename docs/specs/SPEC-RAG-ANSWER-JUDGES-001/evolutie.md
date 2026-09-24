# Evolutielogboek — antwoordketen van de helpwidget

Doel van dit bestand: wat er gemeten is, wat daaruit volgde, en wat er live staat. Zo hoeft niemand een meting of een onderzoek over te doen. De spec ernaast (`spec.md`) beschrijft het ontwerp; dit bestand beschrijft de weg ernaartoe.

Bijgewerkt: 2026-09-19, na 2.38.

---

## 1. De keten, schakel voor schakel

Een beurt van een bezoeker loopt door deze schakels. Per schakel: wat het doet, wat gemeten is, en wat de stand is.

| # | Schakel | Waar | Stand |
|---|---|---|---|
| 1 | Vraag binnen, eerste of vervolg | `partner.py::chat_completions` | ongewijzigd |
| 2 | Vervolgvraag omzetten naar zoekvraag | retrieval-api `services/coreference.py` | verbeterd, live 2026-09-18; het vorige antwoord als extra zoekleg won op zoekniveau (39% naar 64%, 2.28) en bleef eind-tot-eind gelijk (65 om 61, 2.32): niet live |
| 3 | Zoeken (vector, graaf, herrangschikken) | retrieval-api `api/retrieve.py` + `app/services/query_paraphrase.py` | letterlijke zoekregel toegevoegd, live 2026-09-18; twee herformuleringen van de eerste vraag als eigen passes, na herrangschikken samengevoegd: 35% naar 59% op zoekniveau (2.27), 63 om 43 eind-tot-eind (2.33), live na merge; paginaboost gemeten als onschadelijk en bijna zonder effect (2.26) |
| 4 | Controle vooraf op de vraag | `services/turn_judge.py` | beslist niets meer over doorvragen (v0.5.0), praatje-uitzondering weg (v0.6.0), beoordeelt sinds v0.9.0 ook of de vraag binnen de niet-behandelde onderwerpen valt |
| 5 | Antwoord schrijven | `partner_chat.py` + profiel in `klai-libs/chat-prompts` | ongewijzigd; promptvarianten gemeten en afgevallen. Valt de vraag binnen de onderwerpen die de widget niet behandelt, dan wordt deze schakel overgeslagen (v0.9.0) |
| 6 | Koppelen aan bronnen | `klai-libs/citations` | ongewijzigd, dit is de ondergrens |
| 7 | Controle achteraf op het antwoord | `services/answer_judge.py` + `services/answer_grounding.py`; intern `deploy/litellm/klai_answer_grounding.py` | licht oordeel plus controle per zin met reparatie, live 18 sep; dezelfde reparatie op het interne pad sinds #1526 en #1530, platform-breed (2.22, 2.23) |
| 8 | Kennisbank | Voys-artikelen | ontbreekt bij één op de negen kennisvragen (6 van 54, met de zes onderwerpen erbij in 2.31); bij nog eens één op de acht staat het er maar is de eerste vraag te mager om het te bereiken. Niet aangepakt. De widget zoekt in één van de negen kennisbanken van Voys (`support`); prijzen, Ascend en de nerds-wiki staan buiten bereik |
| — | Meten van de keten | `scripts/simulate_conversations.py` | hele gesprekken sinds 18 sep; ijking eerlijk gerekend 75% tegen 81% van de herspeling (2.19, gecorrigeerd in 2.24); bezoeker en scoorder sinds 2.24 op een ander model dan de controle die ze meten; nulmeting daarmee 33% doel bereikt, 8 van 12 doorverwezen (2.30); sinds 2.35 geeft de bezoeker na twee doorverwijzingen op en is de nulmeting 2 van 12 bereikt, 11 van 12 doorverwezen. Voor één schakel: de widgetroute in-process herspelen met de echte controles (2.32, 2.33, 2.37) |

---

## 2. Metingen, op volgorde van uitvoering

Alle metingen op echte Voys-gesprekken, als proefgesprek gedraaid, zonder iets op te slaan bij de klant.

### 2.1 Nulmeting oude systeem (14 dagen vóór de controles)
87% van de antwoorden met bron, 4% zonder bron, 9% weigeringen. Bij 39% een zwak zoekresultaat, en daarvan leverde 84% tóch een antwoord met bron op. Doorlooptijd: mediaan 1,75 s, 90% binnen 3,76 s.

### 2.2 Vorm van het antwoordformaat (17 sep, productiemodel)
Een ja/nee-veld "bevat dit beweringen die niet in de artikelen staan" kwam 18 van de 18 keer terug als "nee", ook bij een verzonnen telefoonnummer en een verzonnen prijs. Een keuzelijst met drie opties (`no_company_statements` / `all_in_articles` / `some_not_in_articles`) was 17 van de 18 keer goed. Een ja/nee-veld voor "is dit een wedervraag" was 0 van de 3 keer goed; dat wordt nu bepaald op het vraagteken.

**Les:** vraag dit model geen ja/nee over iets subjectiefs; geef het een korte keuzelijst.

### 2.3 Eerste live meting (17 sep)
Beide controles draaiden, tijd per beurt 1,7 tot 3,4 s. Twee bevindingen: een vage klacht kreeg altijd het medewerkeraanbod in plaats van een wedervraag, en een half antwoord met een verzonnen doorlooptijd werd getoond omdat er een bron bij zat.

### 2.4 Blinde vergelijking oud tegen nieuw, eerste vragen (17 sep)
50 echte eerste vragen, 3 rondes per versie, beoordelaar `klai-medium` met willekeurige volgorde.

| Uitkomst | Aantal |
|---|---|
| Oud beter | 69 |
| Nieuw beter | 65 |
| Gelijk | 16 |

Per schakel:

| Schakel in de keten | Beurten | Oud beter | Nieuw beter |
|---|---|---|---|
| Keten identiek (ruismeting) | 55 | 24 | 24 |
| Doorvraag-opdracht meegegeven | 23 | **19** | 4 |
| Afspraakknop door controle achteraf | 21 | 9 | 11 |
| Oud bood medewerker aan, nieuw niet | 21 | 13 | 7 |

**Les:** waar de keten gelijk is, is de uitkomst gelijk; de beoordelaar trekt geen versie voor. De doorvraag-opdracht schaadde aantoonbaar en is verwijderd. Verzonnen details: 47 van de 150 antwoorden, in beide versies gelijk.

### 2.5 Verzonnen details, diepgaand (17-18 sep)
54 antwoorden zin voor zin nagelezen tegen de tekst die het model kreeg.

| Controle | Vangt | Klopt als hij aanslaat |
|---|---|---|
| Huidige lichte controle (`klai-fast`, keuzelijst) | 22% | 72% |
| Blinde vergelijker (`klai-medium`) | 53% | 65% |
| Zware controle per zin (`klai-medium`, hele artikel) | 96% | 77% |
| Zware controle, pas melden bij 2+ of tegenspraak | 85% | 92% |

Herkomst van de ernstige gevallen (18 stuks): algemene kennis invullen 10, stijlregels uit de basisprompt 8, stappen of menunamen verzinnen 7, verkeerd artikel 5, te dun stukje artikel 4.

Maatregelen, elk op dezelfde vragen gemeten:

| Maatregel | Bevat iets verzonnen | Ernstig | Goede antwoorden kwijt | Extra tijd |
|---|---|---|---|---|
| Nulmeting (huidig) | 49% | 29% | – | – |
| Temperatuur 0 | 54% | 42% | 0 | – |
| Strengere opdracht | 54-64% | 36-46% | 0 | – |
| Stijlregels eruit | 62% | 40% | 0 | – |
| **Controleren en repareren per zin** | **11%** | **1%** | 0 | ~3 s |

**Les:** met promptaanpassingen komen we er niet op dit model; alleen controleren en repareren werkt.

### 2.6 Vervolgbeurten (18 sep)
90 echte vervolgbeurten uit 38 gesprekken.

- Het juiste artikel zat in de aangeleverde acht: **41%** (bij eerste vragen ligt dat hoger; zwak zoekresultaat 44% tegen 30%).
- Het herschrijven zette **18 van de 90 keer** het gesprek voort in plaats van een zoekvraag te maken.
- Oud tegen nieuw: 47 om 54, met een ruis van dezelfde orde: geen verschil.
- De letterlijke vraag als extra zoekregel: 3 keer winst, 0 keer verlies, 0,28 s sneller.
- Het bestempelen als praatje: 11 keer afgegaan, minstens 3 keer fout, één echte vraag geweigerd.

### 2.7 De eerste vraag rijker maken (18 sep)
- Een kwart van de eerste vragen (17 van 68) is te mager.
- Rijker helpt: een verrijkte vraag won met 29 om 7 van de magere.
- Geen enkele opzet kreeg die details boven tafel: eerst doorvragen verloor met 6 om 12, keuzes uit gevonden artikelen werkten niet (het zoeken geeft nooit meer dan drie verschillende artikelen), en automatisch verrijken kan het ontbrekende feit niet verzinnen.
- Herkennen van een magere vraag: de controle vooraf noemt 45% van de specifieke vragen ten onrechte onduidelijk; de regel "zes woorden of minder" is 89% raak.
- Plafond: bij de vijf gevallen waarin de bezoeker wél specifiek was, stond het antwoord in geen enkele variant in de artikelen.

### 2.8 Controle per zin en reparatie (18 sep)
Eerste poging gaf een vertekend beeld: de opgeslagen kopieën hadden artikelteksten die op 700 tekens waren afgekapt, dus de controle zag het bewijs niet en keurde goede antwoorden af. Opnieuw gemeten met de volledige artikelteksten, 25 echte antwoorden, na de reviewfixes:

| Uitkomst | Aantal |
|---|---|
| Minstens één uitspraak niet in de artikelen | 16 van 25 (64%) |
| Haalde de reparatiedrempel (2 of meer, of tegenspraak) | 10 |
| Gerepareerd | 8, waarvan 2 daarna nog een melding hadden |
| Volledig verzonnen, dus eerlijke weigering | 2 |
| Mislukte controles | 0 |
| Blind voor tegen na reparatie | 5 voor het gerepareerde antwoord, 3 voor het origineel |

Tijd: controle 1,9 s mediaan, 3,4 s in de traagste tien procent.

De twee volledig verzonnen antwoorden waren het doelwit: prijzen van € 25,- eenmalig en € 5,- per maand voor een 0800-nummer, en een verzonnen terugboekprocedure.

**Les uit de eerste poging:** repareren bij één melding maakte het slechter (het origineel won toen 8 van de 11 blinde vergelijkingen), omdat de controle ook een zin afkeurt die alleen de situatie van de bezoeker herhaalt. Vandaar de drempel van twee meldingen of één tegenspraak.

### 2.9 Live na livegang van de controle per zin (18 sep)
Zes echte vragen als proefgesprek door de live keten:

| Vraag | Uitkomst |
|---|---|
| Kosten van een nieuw 0800-nummer | eerlijke "niet gevonden" (was: verzonnen prijzen) |
| Factuur dubbel geïncasseerd | eerlijke "niet gevonden" met doorverwijzing |
| Waar vind ik mijn facturen | antwoord met bron |
| Hoe wijzig ik mijn belplan | antwoord met bron |
| Internationaal bellen aanzetten | "niet gevonden": alle vijf uitspraken stonden niet in de gevonden artikelen |
| Hoe stel ik een wachtrij in | antwoord met bron, twee uitspraken weggehaald |

Tijd per beurt 2,4 tot 4,1 s, met één uitschieter van 12,3 s doordat het repareren van een lange stappenlijst 8,4 s kostte. Daarom kregen de controle en de reparatie eerst elk maximaal 5 s. Dezelfde zes vragen opnieuw: 2,9 tot 4,4 s, en dezelfde lange vraag nog 9,0 s (3,2 s schrijven plus 5,0 s controleren en repareren). Daarom staat de controle nu op 4 s en de reparatie op 3 s, en wordt elke stap apart gelogd. Loopt een limiet af, dan blijft het antwoord zoals het was.

Wat dit kost: een antwoord dat het model uit eigen kennis opschrijft verdwijnt nu, ook als het misschien klopt. De oorzaak ligt dan in de zoekstap, niet in de controle.

### 2.10 Korte vraag: antwoord plus één vervolgvraag — afgevallen (18 sep)
Gebouwd en gemeten, niet live gezet. Bij een vraag van zes woorden of minder kreeg het model de opdracht te antwoorden én één korte vervolgvraag te stellen uit de gevonden artikelen. Zestien echte korte vragen, twee rondes per versie, blinde vergelijking:

| Uitkomst | Aantal |
|---|---|
| Huidige versie beter | 15 |
| Met vervolgvraag beter | 12 |
| Gelijk | 5 |

Het aantal antwoorden waarin de beoordelaar iets verzonnen zag steeg van 4 naar 9, en maar 7 van de 32 antwoorden eindigde echt met een vraag. De code staat op de branch `feat/short-question-follow-up` en is niet samengevoegd.

**Les:** de winst van een rijkere eerste vraag (29 om 7 bij een echt verrijkte vraag) komt niet binnen bereik door het model om die rijkdom te laten vragen. Kansrijker is de bezoeker vooraf laten kiezen uit onderwerpen uit de kennisbank, want het zoeken levert nooit meer dan drie verschillende artikelen.

### 2.10b Snelheid van de controle: wat wel en niet helpt (18 sep)
Na de strakkere grenzen (controle 4 s, reparatie 3 s) opnieuw dezelfde zes vragen live: 2,8 tot 5,1 s, en de lange wachtrijvraag 8,3 s. Die bestaat uit ongeveer 3,2 s schrijven plus de controle en de reparatie.

Getest of de controle sneller wordt door de artikelen in te korten, op 25 echte antwoorden:

| Lengte artikelen | Mediaan | Traagste tien procent | Zelfde oordeel als volledig |
|---|---|---|---|
| volledig | 2,3 s | 4,2 s | – |
| 4000 tekens | 2,2 s | 4,1 s | 24 van 25 |
| 2500 tekens | 2,1 s | 3,5 s | 23 van 25 |

Inkorten levert dus nauwelijks tijd op en kost wel oordelen. De artikelen gaan volledig naar de controle. Wie de staart korter wil, moet bij de reparatie zijn of bij de lengte van het antwoord zelf, niet bij de invoer van de controle.

### 2.10c Twee controles achteraf: samenvoegen nog niet doen (18 sep)
Sinds de controle per zin live staat, levert de lichte controle op het snelle model nog maar één ding dat echt gebruikt wordt: het oordeel "beantwoordt dit de vraag", dat de afspraakknop bepaalt. Het oordeel over wat in de artikelen staat komt van de zware controle.

Getest of de zware controle dat oordeel er gratis bij kan geven, op 25 echte antwoorden:

| | Lichte controle | Zware controle met oordeel erbij |
|---|---|---|
| "beantwoordt de vraag" | 19 van 25 | 10 van 25 |
| Zelfde oordeel als de ander | – | 16 van 25 |
| Tijd (mediaan) | 0,4 s | 2,2 s, tegen 1,9 s zonder oordeel |
| Reparatiebeslissingen | – | ongewijzigd (8) |

Het zware model is dus veel strenger over "beantwoordt de vraag". Samenvoegen scheelt een aanroep, maar zou bij bijna de helft van de antwoorden een andere knop opleveren, en welke van de twee gelijk heeft weten we niet. Daarom blijft het voorlopig zoals het is, met deze meting als reden.

### 2.10d De letterlijke zoekregel kost bijna niets (18 sep)
De nameting schreef 0,45 tot 0,8 s extra zoektijd toe aan de letterlijke zoekregel. Apart gemeten op 15 echte vervolgvragen:

| Variant | Mediaan |
|---|---|
| Herschrijven én letterlijke zoekregel | 889 ms |
| Zonder herschrijven, zonder zoekregel | 471 ms |
| Zonder herschrijven, met zoekregel | 491 ms |

De zoekregel kost dus 20 ms; het herschrijven kost ongeveer 400 ms, en dat deed het altijd al. De extra tijd in de nameting kwam van een machine die tegelijk drie proeven draaide. Niets teruggedraaid.

### 2.10e Minder "bruikbare" passages bij vervolgvragen, deels verklaard (18 sep)
Na de nieuwe herschrijving komt het juiste artikel even vaak mee (39% tegen 39-41%), maar het aandeel beurten met minstens één bruikbare passage zakte van 74-76% naar 67%. Nagekeken op de 30 beurten die erop achteruit gingen:

- Bij 10 van de 30 was de oude zoekvraag zelf een antwoord (de herschrijving zette het gesprek voort). Zulke tekst lijkt op een artikel en haalt daarom makkelijk "bruikbare" passages op, terwijl er iets anders gezocht werd dan de bezoeker vroeg.
- Bij de overige 20 is het gewoon een andere, kortere zoekvraag met een andere uitslag; 18 beurten gingen er juist op vooruit.

De maat "minstens één bruikbare passage" beloont dus deels de fout die we net hebben weggehaald. Wat telt is of het antwoordgevende artikel meekomt, en dat bleef gelijk, terwijl de blinde vergelijking op vervolgbeurten in het voordeel van de nieuwe versie uitvalt (23 om 13). Geen actie; wel de reden om die maat niet als doel te nemen.

### 2.10f Onderwerpen die de assistent niet behandelt (18 sep)
De vraag van Mark: financiële en commerciële vragen niet beantwoorden maar meteen doorverwijzen. Via de basisprompt lukte dat 8 van de 15 keer (zie 2.12). Nu als instelling per widget: de onderwerpen die de assistent niet behandelt plus de tekst die de bezoeker krijgt. De controle vooraf, die al naast het zoeken draait, beoordeelt of de vraag daarin valt; zo ja, dan krijgt de bezoeker precies die tekst met de afspraakknop en schrijft het model niets.

Gemeten op 13 echte vraagsoorten, twee rondes, met de Voys-onderwerpen (prijzen, tarieven, kortingen, offertes, uitstel van betaling, terugbetalingen, contract en opzeggen):

| | Uitkomst |
|---|---|
| Financiële en commerciële vragen afgevangen | 14 van 14 |
| Hulpvragen ongemoeid (facturen vinden, incasso, factuuradres, belplan, meldingen) | 12 van 12 |

Het scheelt bovendien tijd: de antwoordronde vervalt.

### 2.11 Dezelfde controle op de interne chat, alleen meekijkend (18 sep)
*Achterhaald door 2.22 en 2.23: sinds #1526 en #1530 repareert het interne pad ook, voor elke tenant. Deze sectie beschrijft de meekijkfase.*

De interne chat (LibreChat via de LiteLLM-hook) doet nu dezelfde controle per zin als de widget, met letterlijk dezelfde tekst uit de gedeelde bibliotheek. Daar verandert hij niets aan het antwoord en wacht de gebruiker nergens op: de controle wordt naast het antwoord gestart en logt alleen wat er niet in de artikelen staat. Zo kunnen beide paden straks naast elkaar gelegd worden.

Wat de review ving, en wat het waard was: de controle keek naar een veld dat alleen in mijn tests bestond (`kb_chat_mode`), terwijl productie `chat_retrieval_prompt_mode` schrijft. Hij zou dus nooit gedraaid hebben, met groene tests. Nu gebruikt hij dezelfde strikt-check als de renderer zelf, en testen de tests op de productiewaarde.

**Correctie, 18 sep:** de eerste live regels die hier stonden kwamen van de `e2e`-testtenant (org 22), niet van een klant. Ik had het tenant-id nooit nagekeken en rapporteerde ze als "interne beurten". Wachten op verkeer was sowieso de verkeerde aanpak: echte interne gesprekken staan in de database en konden meteen gemeten worden. Dat is alsnog gedaan, zie 2.14.

### 2.12 Financiële en commerciële vragen via de basisprompt (17 sep)
Onderaan de basisprompt: 3 van de 10 goed. Bovenaan, strenger geformuleerd: 8 van de 15. Gewone factuurvragen bleven goed (6 van 6). Eén keer noemde het model tóch een prijs.

**Les:** de basisprompt alleen is hiervoor te zwak; een instelling per widget met een vaste tekst is de betrouwbare route.

### 2.13 De instelling live op de Voys-widget, en de basisprompt ingekort (18 sep)
De onderwerpen en de vaste tekst staan ingevuld op `Voys Help NL`. Zes vragen door de
productiewidget gehaald, met de echte sessietoken en `Origin: https://help.voys.nl`:

| Vraag | Uitkomst |
|---|---|
| Kan ik uitstel van betaling krijgen voor mijn factuur? | vaste tekst, afspraakknop, 0 bronnen |
| Wat kost een extra belnummer bij jullie? | vaste tekst, afspraakknop, 0 bronnen |
| Ik wil graag een offerte voor 25 gebruikers | vaste tekst, afspraakknop, 0 bronnen |
| Hoe zeg ik mijn contract op? | vaste tekst, afspraakknop, 0 bronnen |
| Waar vind ik mijn facturen in de webinterface? | beantwoord uit 1 artikel |
| Hoe stel ik automatische incasso in? | beantwoord uit 1 artikel |

Tegelijk is de basisprompt ingekort: de alinea die het weigeren herhaalde is eruit (dat
staat al in de regels erboven en wordt sinds 18 sep ook per zin gecontroleerd), en de vijf
letterlijke voorbeeldzinnen zijn vervangen door een beschrijving van de vorm. Bij de regel
over beperkingen staat nu dat alle drie de delen uit het artikel moeten komen.

**Let op bij het lezen van 2.5:** dáár is gemeten dat álle stijlregels weghalen het
slechter maakte (62% tegen 49% met iets verzonnen). Deze ingreep is gerichter — alleen de
kopieerbare zinnen — en is niet apart gemeten. Het is een beredeneerde aanpassing, geen
bewezen winst. De oude configuratie staat als back-up buiten de repo.

### 2.14 Dezelfde controle op echte interne gesprekken van Voys (18 sep)
Uit de LibreChat-tenant van Voys: ruim twee derde van de antwoorden over vijf maanden draagt een
bronverwijzing. De 50 meest recente daarvan (9 tot en met 18 sep)
door exact dezelfde controle als de widget, met de artikelen die het zoeken nu oplevert.

| | Widget (2.8) | Interne chat |
|---|---|---|
| Minstens één uitspraak die de artikelen niet dragen | 64% | **86%** |
| Haalt de reparatiedrempel (2+ of tegenspraak) | 40% | **70%** |
| Mediaan niet-gedragen uitspraken per antwoord | – | 3 |
| Mediaan antwoordlengte | enkele honderden tekens | 1173 tekens |
| Mediaan controletijd | 1,9 s | 3,6 s |

**Conclusie:** het interne pad heeft dit probleem niet minder maar meer, en de reparatie hoort
daar dus ook te draaien, niet alleen de meting.

**De tijdsafweging ligt er wel anders.** De mediane controletijd is 3,6 s tegen 4 s budget op de
widget, dus ongeveer de helft van de interne antwoorden zou op dat budget afkappen. Bij de
eerste ronde met het widgetbudget vielen 17 van de 50 controles af, en juist de langste. Intern
moet het budget dus omhoog, of de controle moet blijven draaien zonder dat de gebruiker wacht.

**Twee beperkingen van dit cijfer.** De artikelen komen uit het zoeken van vandaag, niet uit wat
het model destijds zag; over negen dagen is die afwijking klein maar niet nul. En dit meet de
antwoorden zoals ze zijn opgeslagen, zonder de reparatie die het cijfer op de widget van 64%
naar 11% bracht.

### 2.15 Totaalmeting widget: oud tegen nu, blind (18 sep)
50 echte eerste vragen van 11 tot en met 16 sep, opnieuw door de huidige keten via een
preview-sessie, blind vergeleken met het antwoord dat de bezoeker destijds werkelijk kreeg.
Elk paar twee keer beoordeeld, in beide volgordes.

| Uitkomst | Aantal | Aandeel |
|---|---|---|
| Oud beter | 41 | 44% |
| Nieuw beter | 47 | 50% |
| Gelijk | 6 | 6% |

Uit de herhaling zelf: 38 van de 50 met bron, 24 met afspraakknop, 3 mislukt.

**Dit is geen verschil** — de meetafspraak hieronder noemt alles onder ongeveer tien beurten
ruis, en het verschil is zes. Dat is consistent met 2.4 (69 om 65) en het heeft een aanwijsbare
oorzaak: de beoordelaar krijgt de vraag en twee antwoorden, niet de artikelen, en kan een
verzonnen detail dus niet herkennen. De winst van deze week zit in een maat die deze opzet niet
kan zien (49% naar 11% met iets verzonnen, zin voor zin nagelezen), en de kosten zitten in
tijd, die hij evenmin ziet.

**Les voor volgende metingen:** een blinde vergelijking van twee antwoorden meet leesbaarheid en
behulpzaamheid, niet gegrondheid. Wie gegrondheid wil meten moet de artikelen meegeven of per
zin nalezen.

### 2.16 Waar de winst nog zat: niet in het zoeken (18 sep)
Voor de bulk-experimenten eerst uitgezocht welke schakel het plafond vormt. Van de negen
herspeelde vragen die geen bron opleverden, vijftig kandidaten opgehaald in plaats van acht en
laten beoordelen of er een antwoord tussen zat:

| Oorzaak | Aantal |
|---|---|
| Stond dieper dan plek 8 (rangschikking) | **0 van 9** |
| Stond niet in de kennisbank | 6 van 9 |
| Stond binnen de acht en werd toch niet gebruikt | 3 van 9 |

Nul rangschikkingsproblemen. Elke klassieke zoekverbetering uit de literatuur
(fusie-varianten, andere chunkgrootte, contextuele prefix) zou hier niets bewegen. De
contextuele prefix is bovendien al overal toegepast: 0 van 21.052 Voys-chunks is zonder
verrijking geïndexeerd.

Vier van die zes "niet in de kennisbank" waren geen kennisvragen: twee bezoekers vroegen of ze
Engels mochten spreken, één wilde een medewerker, en één vroeg of hij met zijn manager een
gesprek kon inplannen. Zie 2.17.

**Les:** meet welke schakel het plafond is voordat je een schakel verbetert. Herrangschikken
verbetert per definitie de recall niet, en hier was er ook geen recall-probleem.

### 2.17 De controle markeerde de assistent zijn eigen woorden (18 sep)
Gevonden in de nameting van 2.15. Een gespreksbeurt kreeg de vaste weigering:

| Vraag | Vóór de controles | Erna |
|---|---|---|
| "Ik wil graag een medewerker spreken." | "Je kunt een afspraak maken met een medewerker via de knop onder deze tekst." | "Dit vind ik niet terug in onze helpartikelen." |
| "Can I speak english?" | "Yes, you can." | "Dit vind ik niet terug in onze helpartikelen." |

De logregel wees de oorzaak aan: `grounding=some_not_in_articles, unsupported=1, verdict=answered`.
Zonder bron is één onbewezen uitspraak genoeg voor een weigering, en de controle markeerde
zinnen als "Je kunt een afspraak maken met een medewerker via de knop onder deze tekst" — een
uitspraak over het chatvenster, niet over Voys. Geen helpartikel zal die zin ooit bevatten.

Niet de drempel verlaagd, want dat zou echte verzinsels doorlaten ("Een portering duurt ongeveer
vijf werkdagen" levert precies één markering op en hoort geweigerd te worden).

**Eerste poging, en waarom hij sneuvelde.** De controle in de prompt laten overslaan wat over de
assistent zelf gaat, inclusief "wat hij wel en niet kan" en "wat de afspraak oplevert". Dat gaf
6 van 6 vrije gespreksbeurten, maar de review wees terecht op de prijs: dezelfde uitzondering
laat "Ik verbind je nu door met een collega", "tijdens die afspraak wordt je contract opgezegd"
en "met deze knop reset je je account" óók door. Een uitzondering die breed genoeg is voor de
afspraakzin is te breed voor de rest.

**Wat het wel werd: het contract als bewijs.** De controleur ziet alleen de vraag, de artikelen
en het antwoord, dus hij kan niet weten dát er een afspraakknop hangt. Die garantie gaat nu mee
als één extra artikel met de titel "This chat", alleen wanneer de knop er echt is. Daarmee is de
afspraakzin gedekt door bewijs en blijft alles wat het antwoord bewéért over wat die afspraak
doet gewoon beoordeeld.

| | Oude prompt | Brede uitzondering | **Contract als bewijs** |
|---|---|---|---|
| Gespreksbeurten zonder markering (6 echte) | 2 van 6 | 6 van 6 | **5 van 6** |
| Doorverbinden, afspraakuitkomst, knop-effect gevangen (3) | – | **0 van 3** | **3 van 3** |
| Verzonnen prijs/stap gevangen (4) | 3 van 4 | 3 van 4 | 3 van 4 |
| 30 echte antwoorden: minstens 1 onbewezen | 73% | 73% | 73% |
| 30 echte antwoorden: andere reparatiebeslissing | – | 0 van 30 | **0 van 30** |

De enige beurt die blijft hangen is "Die plant een persoonlijke afspraak voor je in", en dat is
een bewering over wat de afspraak oplevert — precies wat beoordeeld hoort te worden.

**Les:** een uitzondering in een prompt kan niet onderscheiden wat de applicatie garandeert van
wat het model erbij verzint. Geef de garantie als bewijs, dan hoeft de controleur niets te
geloven.

### 2.18 Vals alarm in de niet-behandelde onderwerpen: drie routes, alle drie afgevallen (18 sep)
Gevonden in de herspeling van 2.15. "ik wil graag uitgaand kunnen bellen met mijn mobiele nummer"
krijgt de vaste tekst voor commerciële vragen. Dezelfde vraag als "hoe stel ik mijn mobiele
nummer in als uitgaand nummer" wordt gewoon beantwoord, mét bron. Het is dus de formulering
"ik wil graag", die als aanvraag wordt gelezen. Reproduceerbaar. Eén op de vijftig echte eerste
vragen.

Drie oplossingen gebouwd en gemeten op 8 commerciële en 10 hulpvragen, twee rondes:

| Route | Commercieel afgevangen | Hulpvragen ongemoeid |
|---|---|---|
| Huidig | 12 van 16 | 18 van 20 |
| Beoordelaarsprompt: "iets willen gebruiken is een hulpvraag" | 16 van 16 | **10 van 20** |
| Strakkere onderwerptekst voor Voys | 12 van 16 | 19 van 20, maar "Hoe zeg ik mijn contract op?" ontsnapt |
| Zoekresultaat overruled de beoordelaar | – | onbruikbaar, zie hieronder |

De tweede maakte het duidelijk slechter: door de formulering te bénoemen ging het model erop
letten en ving het élke wens af, ook "Ik wil graag een wachtrij instellen". De derde faalde op de
meting die hem moest onderbouwen: bij 3 van de 6 commerciële vragen beantwoordt een helpartikel
de vraag óók ("Wat kost een extra belnummer", "Kan ik uitstel van betaling krijgen", "Hoe zeg ik
mijn contract op?"), dus die regel zou de helft van de commerciële vragen doorlaten. Het signaal
scheidt niet.

**Stand:** het valse alarm blijft, en elke gemeten oplossing kost meer dan hij oplevert. Dat is
een afweging voor de eigenaar van de widget, geen technische keuze: één op de vijftig hulpvragen
krijgt de doorverwijstekst, tegenover commerciële vragen die anders met een verzonnen prijs
beantwoord zouden worden (zie de oude nameting in 2.15, waar een prijsvraag "€ per maand"
opleverde).

**Les:** een klasse die een antwoord volledig stillegt, verdient een zachtere faalrichting dan
een klasse die alleen een knop toevoegt. Dat is hier niet opgelost.

### 2.19 Gesimuleerde bezoeker: hele gesprekken meetbaar (18 sep)
Het probleem dat Mark benoemde: alleen de eerste vraag is toetsbaar, want de tweede vraag hangt
af van het antwoord op de eerste. Elke eind-tot-eind vergelijking hier (2.4, 2.15) gaat daarom
over beurt één; de vervolgmetingen (2.6, 2.10e) zijn stapmetingen met een vaste voorgeschiedenis,
wat voor het zoeken klopt maar geen gesprek meet.

`scripts/simulate_conversations.py`: een model speelt de bezoeker met een doel uit een echt
gesprek en praat meerdere beurten met de live widget via een preview-sessie. De eerste vraag is
de echte, letterlijk, want dat is het ijkpunt.

Run na de reviewcorrecties, 12 echte gesprekken van de Voys-widget, maximaal 4 beurten:

| | Uitkomst |
|---|---|
| Doel bereikt | 9 van 12 (75%) |
| Eerste antwoord met bron | 9 van 12 (75%) |
| Gemiddeld aantal assistentbeurten | 2,7 |
| Gemiddeld verspilde beurten | 1,0 |

**IJking, gecorrigeerd in 2.24:** hier stond eerst "38 van de 50 (76%) tegen 75%". Die 50 telde
de 3 mislukte herspelingen mee in de noemer, terwijl het harnas mislukte gesprekken juist buiten de
noemer houdt. Gelijk gerekend is de herspeling 38 van 47 (81%) tegen 9 van 12 (75%) in de
simulatie. Bij twaalf gesprekken is dat verschil één gesprek, dus de eerste beurt gedraagt zich
nog steeds als echt verkeer, maar de ijking is zwakker dan de oude zin suggereerde en er zat een
rekenfout in.

**Twee fouten in de eerste versie, allebei door de review gevonden.** Het "doel" van de bezoeker
bestond uit álle opgeslagen bezoekersbeurten, dus juist de vervolgvragen die afhangen van wat het
oude systeem antwoordde — daarmee stuurde het harnas de simulatie terug het oude pad op. Het doel
is nu één afgeleide zin intentie. En een gesprek zonder bruikbaar oordeel telde als mislukking
mee; die vallen nu buiten de noemer. De eerste versie rapporteerde daardoor 90% doelen bereikt,
wat te rooskleurig was.

**Wat het niet is.** Ook die 75% is geen slagingspercentage van de widget. Een gesimuleerde
bezoeker is geduldiger en formuleert beter dan iemand op een helppagina. Het getal vergelijkt
twee versies, nooit als absolute claim.

**Wat het kostte om dit te leren.** De ongeremde eerste versie verbruikte de gedeelde
snelheidslimiet: het model gaf 429, de widget gaf 502, en een echte bezoeker in dat venster kreeg
een foutmelding. Eén gesimuleerd gesprek kost tot vier widgetbeurten en vijf modelaanroepen, en
elke widgetbeurt besteedt er zelf nog drie op hetzelfde budget. Het harnas wacht nu tussen
gesprekken en loopt op bij een 429; dat is geen nette toevoeging maar een voorwaarde.

### 2.20 Aspectgericht doorvragen vanuit de taxonomie: mechanisme werkt niet (18 sep)
De laatste kandidaat uit het literatuuronderzoek. De [ASK-aanpak](https://aclanthology.org/2025.acl-industry.63.pdf)
vraagt bij hoge ambiguïteit door op domeinaspecten in plaats van op de gevonden artikelen, en die
tweede variant was hier al gemeten en gevallen (het zoeken levert nooit meer dan drie
verschillende artikelen). De support-kennisbank van Voys heeft acht taxonomie-onderwerpen, dus de
eerste variant was wél te proberen.

Mechanisme eerst getest, vóór de bouw. Voor de 12 magere eerste vragen uit de simulatie bepaalde
het echte doel van de bezoeker welk aspect hij gekozen zou hebben; daarna dezelfde vraag opnieuw
gezocht met dat aspect ervoor.

| | Artikel dat het doel dekt |
|---|---|
| Kale vraag | 7 van 12 |
| Met gekozen aspect | **7 van 12** |

Twee gewonnen (beide belplan-vragen), twee verloren (factuur, Engels). Netto nul. Het aspect is
te grof: "Telefonie-instellingen en hardware" beslaat een derde van de kennisbank.

**Niet gebouwd.** De winst van een rijkere vraag (29 om 7) is echt, maar geen van de drie
onderzochte routes bereikt hem: doorvragen schaadt, keuzes uit artikelen kan niet, en een
taxonomie-aspect voegt geen zoeksignaal toe.

### 2.21 Eerste vondst van het harnas, en wat de nameting wel en niet laat zien (18 sep)
Het gesprek dat de simulatie liet zien:

> **Bezoeker:** Ik heb een factuur betaald maar die is ook geïncasseerd. Hoe kan ik bedrag laten storneren?
> **Assistent:** Dat staat niet in onze helpartikelen. Laat het gerust weten als je vastloopt.
> **Bezoeker:** *(herhaalt de vraag)*
> **Assistent:** Ik vind dit niet terug in onze helpartikelen. Je kunt een afspraak maken met een medewerker.
> **Bezoeker:** Hoe kan ik een afspraak maken met een medewerker?

Drie beurten om te ontdekken dat er een mens bereikbaar was. De eerste weigering schrijft het
model zelf; die bereikt de bezoeker ongewijzigd omdat hij niets over de organisatie beweert, en
hij kwam zonder knop. De vaste weigering van de achterkant heeft die knop altijd gehad.

Gerepareerd: een antwoord zonder bron waarvan de lichte beoordelaar zegt dat het de vraag niet
beantwoordt, krijgt dezelfde knop. Niet op een gespreksbeurt, want die geldt bewust als antwoord
en een knop onder "graag gedaan" biedt hulp met niets.

**Nameting op dezelfde 12 gesprekken:**

| | Voor | Na |
|---|---|---|
| Doel bereikt | 75% | 75% |
| Eerste antwoord met bron | 75% | 67% |
| Gemiddeld aantal assistentbeurten | 2,7 | 2,4 |
| Gemiddeld verspilde beurten | 1,0 | 1,1 |
| Het gerichte gesprek (dubbele incasso) | 3 beurten, 2 verspild | **1 beurt, 0 verspild** |

**Wat dit wel zegt:** het geval waarvoor de reparatie is gemaakt, is aantoonbaar opgelost.

**Wat dit niet zegt:** dat de widget als geheel beter is geworden. Het totaal beweegt binnen de
ruis, en bij twaalf gesprekken met een model aan beide kanten is dat te verwachten — de
meetafspraak hieronder noemt alles onder ongeveer tien beurten ruis. Eén gesprek sloeg van 1 beurt
naar 4 om, in de andere richting.

**Wat het harnas hiermee zelf laat zien:** twaalf gesprekken is te weinig om een effect van deze
grootte te meten, en meer gesprekken kost gedeelde snelheidslimiet op productie. Een verbetering
die niet één specifiek gesprek repareert, is met dit harnas alleen aan te tonen met meerdere
rondes of een groter aantal, en dat moet dan buiten kantooruren.

### 2.22 Beide paden op schaal, en de reparatie intern aangezet (18 sep)
De meting van 2.14 gebruikte 50 interne antwoorden en vergeleek met 25 widgetantwoorden uit een
andere week. Dat was een keuze van mij, geen beperking: de controle kost alleen een modelaanroep
en de tenant heeft honderden opgeslagen beurten op beide paden. Opnieuw gedaan, identiek
behandeld — opgeslagen vraag, opgeslagen antwoord, en de artikelen die het zoeken vandaag levert.

| | Widget (80 beurten) | Interne chat (80 beurten) |
|---|---|---|
| Minstens 1 onbewezen uitspraak | 75% | 85% |
| Boven de reparatiedrempel | 65% | 72% |
| Minstens 1 tegenspraak | 4% | 6% |
| Mediaan uitspraken per antwoord | 5 | 5 |
| Mediaan antwoordlengte | 349 tekens | 1173 tekens |

**Dit verfijnt 2.14.** Daar stond 64% extern tegen 86% intern, op 25 tegen 50 antwoorden uit
verschillende weken. De richting klopte: intern is slechter, 85% om 75% en 72% om 65% boven de
drempel. De kloof is alleen kleiner dan de eerste meting suggereerde, en het aantal uitspraken per
antwoord is gelijk (5 om 5) terwijl de antwoorden drie keer zo lang zijn.

**Tussenstand niet als eindstand lezen.** Op de eerste 40 interne antwoorden stond 80% / 65% /
10%; op alle 80 werd het 85% / 72% / 6%. Bij deze aantallen schuift een percentage nog makkelijk
tien punten, en de tegenspraakcategorie is klein genoeg (5 antwoorden) om er geen drempel op te
bouwen.

**Wat dit betekent voor de vraag of de paden gelijk moeten zijn:** ja. Beide zitten ruim boven de
drempel waarop de reparatie op de widget bewezen hielp (49% naar 11%), en intern iets hoger.

**Wat er nu gebouwd is.** Het interne pad repareert, met dezelfde woorden, hetzelfde schema en
dezelfde drempel als de widget. Het budget is ruimer (12 s controle, 8 s reparatie tegen 4 en 3),
want de mediane controle duurt daar 3,6 s op antwoorden van 1026 tekens; met het widgetbudget
viel 17 van de 50 af.

**En de grens die blijft.** Elke interne beurt streamt (41 van 41), en gestreamde tekst is al
gelezen. De reparatie draait dus op de niet-streamende renderweg, die met
`KLAI_KB_CHAT_RENDER_MODE=deterministic_non_streaming` aan te zetten is. Mijn eerdere bewering dat
de architectuur dit blokkeerde was onjuist: de modus bestaat, hij staat alleen niet aan. De prijs
is dat de medewerker wacht in plaats van tokens te zien verschijnen.

### 2.23 De interne reparatie is niet bereikbaar zonder de SSE-stroom te herbouwen (18 sep)
De reparatie uit #1526 kan alleen een antwoord bewerken dat nog in één stuk is. Eerst gemeten wat
dat de lezer zou kosten, op 17 echte interne antwoorden met hun zoekresultaat:

| | Mediaan | Traagste tien procent |
|---|---|---|
| Controle | 2,6 s | 6,0 s |
| Reparatie (bij 13 van 17) | 1,2 s | 2,1 s |
| Samen | **4,0 s** | **8,3 s** |

Negen van de 17 antwoorden werden daadwerkelijk aangepast.

**Twee keer fout gezeten over hetzelfde.** Eerst schreef ik dat de streamingarchitectuur de
reparatie blokkeerde. Toen corrigeerde ik dat: de modus `deterministic_non_streaming` bestaat,
hij staat alleen niet aan. Die correctie was óók fout. In `select_kb_render_strategy` staat de
streamingcheck vóór de modus:

```python
if original_stream is True:
    return KbCitationRenderStrategy(mode=KB_RENDER_MODE_STREAMING_GUARD)
```

`force_non_streaming` is dus nooit van toepassing op een client die om een stroom vraagt, en de
interne chat vraagt daar altijd om (41 van de 41 beurten). De modus bestaat voor clients die niet
streamen. Mijn poging om hem per organisatie aan te zetten (#1527) zette die volgorde om en zou
een streamingverzoek met een gewone JSON-response beantwoorden — een gebroken transportcontract,
gevonden door de review vóór de merge. Die PR is gesloten.

**En toen bleek het al gebouwd te zijn.** Boven in `compose_streaming_kb_response` staat:

> *Every Strict stream is held back in full. ... Held deltas still go out as chunks with empty
> content, one per model token, which keeps bytes flowing to LibreChat while the answer is
> written.*

Een strikte beurt streamt de tékst dus niet. De lezer krijgt lege chunks tot het slotframe, waar
de hele tekst in één keer wordt gezet. Op dat punt is het antwoord nog volledig in handen en heeft
niemand iets gelezen — precies de voorwaarde die de reparatie nodig heeft. Er hoefde niets
herbouwd te worden en er gaat geen streamervaring verloren, want die was er voor deze beurten niet.

Wat het wél kost is tijd: mediaan 4,0 s en 8,3 s in de traagste tien procent, bovenop het
schrijven. Een Open-stroom stuurt de woorden wél zoals ze komen, en daar blijft het bij meten.

**Stand:** de reparatie draait nu op het pad waar alle interne beurten langskomen. Drie keer had ik
het mis over dezelfde vraag voordat ik de opmerking las die er al twee maanden stond.

**Les:** lees de code van de schakel die je "onmogelijk" noemt, vóórdat je dat opschrijft. Ik heb
er drie rondes en een gesloten PR aan besteed.

**De reparatie staat aan voor elke tenant.** Er is geen poort per organisatie: `_repair_would_be_wrong`
kent alleen technische voorwaarden (Strict, citeerbare bronnen, het hele antwoord nog in handen,
niets dat op geplakte tekst rust). Dat is ook houdbaar zonder poort, omdat een antwoord zonder
citeerbare bron alleen gemeten wordt: de tenants die geen bronverwijzingen produceren (over dertig
dagen hadden drie tenants er nul, en de testtenant het grootste volume zonder één) worden dus niet
geraakt. De eerdere alinea hier, die tegen globaal aanzetten pleitte, beschreef een zorg die de code
nooit heeft gehad; spec regel 8 en REQ-5 zeiden tot 18 sep nog "alleen meekijkend" en zijn in
v0.10.0 gelijkgetrokken. Aantallen per tenant staan in de private operationele documentatie.

### 2.24 Het harnas mat zichzelf, en de ijking rekende met twee noemers (18 sep)
Drie fouten in `scripts/simulate_conversations.py` en in wat 2.19 erover zei, alle drie nagekeken in
de code en gerepareerd zonder nieuwe meting, want ze gaan over het meetinstrument en niet over de
keten.

1. **Eén model voor alles.** De gesimuleerde bezoeker, de doelschrijver en de scoorder draaiden op
   `settings.answer_grounding_model`, hetzelfde `klai-medium` dat de controle per zin en de reparatie
   doet. Het harnas beoordeelde de gegrondheidscontrole dus met de mening van het model dat die
   controle is. Nu draaien alle drie op `klai-large` (`KLAI_SIMULATION_MODEL`), en het script weigert
   te starten als dat model gelijk is aan het controlemodel. Niet `klai-primary` of `klai-fast`, want
   die delen het quotum met echte bezoekers (het 502-incident van 2.19). `klai-large` heeft 13
   aanroepen per minuut per sleutel; de bestaande rem (zes seconden tussen gesprekken, anderhalve
   seconde tussen beurten, hooguit vijf modelaanroepen per gesprek) blijft daar onder.
2. **De scoorder beloonde de oplossing die getoetst werd.** "Een eerlijke 'niet gevonden' plus een weg
   naar een persoon" telde als doel bereikt, en de bezoeker stopte zodra een persoon was aangeboden.
   Precies dat gedrag is in 2.21 gebouwd, dus het harnas kon die wijziging alleen maar goedkeuren.
   Nu is `reached` alleen waar als de bezoeker iets kreeg waarmee hij zijn doel kan halen; een
   doorverwijzing wordt apart geteld (`handed_off`, in het rapport "eerlijk doorverwezen zonder
   antwoord") en telt nooit als bereikt. De bezoeker probeert het bij een weigering of doorverwijzing
   één keer opnieuw (herformuleren of één detail uit zijn doel toevoegen) voordat hij stopt.
3. **Ongelijke noemers bij de ijking.** 2.19 zette "38 van de 50 (76%)" uit de herspeling naast "75%"
   van het harnas. Die 50 telde 3 mislukte herspelingen mee, terwijl het harnas mislukte gesprekken
   buiten de noemer houdt. Gelijk gerekend: 38 van 47 (81%) tegen 9 van 12 (75%). De regel in het
   rapport zegt nu expliciet dat mislukte gesprekken aan beide kanten buiten de noemer staan en
   hoeveel het er waren. Het verschil van zes punten is bij twaalf gesprekken één gesprek, dus de
   ijking houdt, maar de oude zin was fout en te stellig.

**Gevolg voor 2.19 en 2.21:** de cijfers daar (75% doel bereikt, voor en na) zijn met de oude
scoorder gemeten en tellen doorverwijzingen als succes. Ze zijn onderling vergelijkbaar, niet met
wat het harnas vanaf nu rapporteert. Een nieuwe nulmeting met de gescheiden modellen is niet
gedraaid: elke ronde van twaalf gesprekken kost gedeelde snelheidslimiet (2.19), en er stond geen
wijziging aan de keten klaar om ertegen af te zetten.

### 2.25 Het fundament onder de 29 om 7: wat de "verrijkte vraag" van 2.7 werkelijk was (18 sep)
Voordat er op dat cijfer gebouwd wordt, nagekeken hoe het tot stand kwam (`/tmp/exp/first-question`,
`build_nat.py`, `summary.json`).

- **Geen antwoord-orakel, wel een vraag-orakel.** De verrijkte vraag was de magere eerste vraag plus
  wat de bezoeker zélf later in hetzelfde gesprek typte ("Hoe stel ik mijn belplan in?" + "ik wil
  graag naar het buitenland kunnen bellen"). Het antwoord zat er niet in, maar de informatie is pas
  beschikbaar nadat het oude systeem een half antwoord had gegeven en de bezoeker corrigeerde. Het
  meet dus "wat als de bezoeker het meteen had gezegd", en dat is precies wat doorvragen had moeten
  opleveren (2.4: schadelijk). Het is een bovengrens van vragen, niet van zoeken.
- **Negen gevallen, twee rondes.** De 29 om 7 komt uit 9 magere vragen × 2 rondes, paarsgewijs in
  beide volgordes. Bij `n=9` is dat één afwijkend gesprek verwijderd van ruis volgens de eigen
  meetafspraak (§6).
- **Het verrijkte antwoord verzon vier keer zo vaak.** Uit dezelfde meting (`answers_thin_2rounds`):
  "bevat iets verzonnen" T (mager) 3 van 18, E (verrijkt) 12 van 18, H (hele geschiedenis) 14 van
  18. De paarsgewijze beoordelaar koos tóch 29 om 7 voor E, terwijl zijn opdracht zei dat verzinsels
  zwaar wegen. Dat is dezelfde blinde vlek als in 2.15: een rijker antwoord leest als een beter
  antwoord. "Lost het echte probleem op" was E 6 van 18 tegen T 0 van 18, dus de winst is echt, maar
  kleiner dan 29 om 7 suggereert en gekocht met verzinsels die de controle per zin nu weghaalt.
- **Wat er wél uit te halen is:** het zoeken vond het goede artikel met de magere vraag 1 van 8 keer,
  met de verrijkte 3 van 8, met de hele geschiedenis 6 van 8 (`retrieval_gold_title_hit_thin_n8`).
  Dat de geschiedenis als zoeksignaal het meeste opleverde, stond er dus al en is nooit opgevolgd.
  Zie 2.28.

**Stand:** 29 om 7 is geen haalbaar zoekdoel, maar een maat voor wat de bezoeker weet en niet zegt.
De haalbare winst zit in wat het systeem wél heeft zonder te vragen: de pagina, de geschiedenis en
de vraag zelf anders geformuleerd. Die drie zijn hieronder gemeten.

### 2.26 Paginacontext: draait, is gratis, doet bijna niets (18 sep)
De widget stuurt de URL van de helppagina mee en de zoekdienst geeft chunks van diezelfde pagina een
factor 1,08 na het herrangschikken (`page_context.py::_apply_page_context_boost`). In 23 metingen
stond nergens of dat helpt. De pagina van echte bezoekers wordt niet opgeslagen (niet in
`widget_messages`, niet in `answer_signals`), dus gemeten met drie kunstmatige pagina's per vraag.

Hoe vaak het speelt, uit de zoeklogs (`retrieval_decision_record`, 10 t/m 17 sep): ruwweg een kwart
tot een derde van de beurten had een pagina die met een kandidaat overlapte, en komt dus van een
artikelpagina.

Gemeten op de 22 kennisvragen uit 2.27 waarvoor een antwoordende passage in de top-50 zat:

| Pagina meegegeven | Antwoordende passage in top-8 | Antwoord op plek 1 |
|---|---|---|
| Geen (huidig gedrag zonder pagina) | 19 van 22 | 8 |
| De pagina die het antwoord bevat | 19 van 22 | **11** |
| Een verkeerde pagina uit de top-8 (19 vragen) | 16 van 19 (gelijk aan zonder) | 6 (gelijk) |
| De startpagina van het helpcentrum | 19 van 22, top-8 identiek in 21 van 22 | 8 |

De boost verandert de top-8 in de helft van de gevallen (11 van 22 bij de goede pagina, 7 van 19 bij
een verkeerde), maar brengt geen antwoord binnen dat er niet al stond en gooit er ook geen uit. Wat
hij wél doet: het antwoord drie keer naar plek 1 tillen als de bezoeker op de juiste pagina staat.
Schaden doet hij niet, ook niet als de bezoeker op een pagina staat die zijn vraag niet beantwoordde.

**Stand:** laten staan, niets aan doen. Een boost van 8% na het herrangschikken kan alleen binnen de
top-8 schuiven en dat is niet waar het probleem zit: bij 3 van de 22 stond het antwoord op plek 9
tot 50, en de boost haalt die niet omhoog omdat de pagina van de bezoeker zelden dat artikel is.
Een sterkere boost is niet gemeten en niet aan te raden zolang het volgende punt niet gebouwd is.

### 2.27 Twee herformuleringen van de eerste vraag, samengevoegd: 35% naar 59% (18 sep)
Meervoudige zoekvragen vanuit de vraag zelf, zonder de bezoeker iets te vragen en zonder details toe
te voegen. `klai-medium` schrijft twee alternatieve formuleringen ("zoals een andere bezoeker met
hetzelfde probleem had kunnen typen, hooguit vijftien woorden, met woorden die in helpartikelen
staan, geen apparaten of oorzaken toevoegen"), elk gaat apart door het zoeken, en de drie top-8
lijsten worden met RRF samengevoegd tot één top-8.

Data: alle 75 verschillende eerste vragen van echte Voys-gesprekken (60 dagen, plus de export van
17 sep), waarvan 54 kennisvragen (8 commercieel, 7 medewerker, 6 taal). De behoefte van de bezoeker
is afgeleid uit ál zijn beurten, zodat de beoordelaar weet wat hij uiteindelijk wilde; de zoekvraag
zelf ziet alleen de eerste beurt. Elke passage blind beoordeeld door `klai-medium` (antwoordt /
gedeeltelijk / irrelevant), één beoordelingspool per vraag over alle varianten.

| Variant (54 kennisvragen) | Antwoordende passage in top-8 | Goede artikel in top-8 | Winst / verlies t.o.v. huidig |
|---|---|---|---|
| Huidig: de letterlijke vraag | 19 (35%) | 26 (48%) | – |
| Herformulering 1 alleen | 21 (40%) | 24 (45%) | 10 / 8 |
| Herformulering 2 alleen | 19 (36%) | 23 (43%) | 8 / 8 |
| Vraag + beide herformuleringen als één lange zoekvraag | 24 (44%) | 27 (50%) | 9 / 4 |
| **Drie aparte zoekvragen, RRF-samengevoegd** | **32 (59%)** | **34 (63%)** | **14 / 1** |

Veertien winsten tegen één verlies is ruim boven de ruisgrens van tien. Drie dingen die het cijfer
verklaren:

- **Het is recall, geen rangschikking.** Bij 13 van de 14 winsten stond de antwoordende passage
  nergens in de top-50 van de letterlijke vraag. De formulering van de bezoeker haalt het artikel
  gewoon niet op; een andere formulering wel. Dit corrigeert 2.16, dat op negen bronloze beurten
  "nul rangschikkingsproblemen" vond en daaruit afleidde dat zoekverbeteringen niets zouden bewegen.
  Dat klopte voor rangschikking en niet voor formulering.
- **De winst zit niet bij de magere vragen.** Mager (≤ 6 woorden, n=12): 50% naar 58%. Specifiek
  (n=42): 31% naar 60%. Lange, pratende eerste berichten ("Goedemorgen, mijn vaste lijn en mobiele
  lijn gaan gelijk over tot voicemail. Kan niet zo snel vinden…") zoeken slecht als één vector; een
  herformulering van vijftien woorden in artikeltaal zoekt goed. Het probleem van 2.7 was dus half
  verkeerd gesteld: niet te weinig woorden, maar de verkeerde.
- **De band ziet het niet.** 16 van de 54 kennisvragen kregen band `high` zonder antwoordende
  passage in de top-8, en 5 kregen `low` mét. De zoekscore is geen bruikbare poort voor wanneer dit
  aan zou moeten; het moet dan altijd.

Het ene verlies: "Mijn voys die laad niet…", waar de herformuleringen op "app laadt niet" zoeken en
de letterlijke tekst een ander artikel raakte.

Kosten, tien echte vragen sequentieel: de herformulering op `klai-medium` mediaan 0,54 s (0,45 tot
1,09 s), één zoekopdracht mediaan 0,68 s. De drie zoekopdrachten kunnen parallel; de zoekdienst heeft
daar al een mechanisme voor (`sub_queries` in `RetrieveRequest`, fan-out met samengevoegd
bewijspakket), alleen gebruikt de widget dat niet. Netto ongeveer 0,6 s extra per eerste beurt,
op het model met 900 aanroepen per minuut, niet op het quotum van het antwoordmodel.

**Wat dit niet bewijst:** dat het antwoord beter wordt. Het meet of het antwoordende artikel bij de
acht zit die het model krijgt; wat het model ermee doet en of de controle per zin het daarna laat
staan, moet vooraf per §6 gemeten worden (eind-tot-eind, drie rondes, blind, mét de artikelen voor de
beoordelaar). De literatuur waarschuwt dat de winst van herformuleren na herrangschikken vaak
wegvalt (RAG-Fusion in productie: Hit@10 0,51 naar 0,48) en dat het model soms zijn eigen kennis in
de herformulering stopt (query2doc-lekkage). Het eerste is hier gemeten en niet gebeurd: de fusie
staat ná het herrangschikken per leg. Het tweede is hier klein gehouden door de instructie geen
details toe te voegen, maar niet apart geteld.

**Stand:** niet gebouwd, wel de grootste gemeten zoekwinst tot nu toe. Bouwvoorstel: bij een eerste
beurt (geen geschiedenis) twee herformuleringen via `klai-medium`, mee als `sub_queries`, met de
letterlijke vraag als primaire. Vóór livegang: de eind-tot-eind poort uit §6 op dezelfde 54 vragen.

### 2.28 Het vorige antwoord als extra zoekleg bij vervolgbeurten: 39% naar 64% (18 sep)
Vraag 4 uit de opdracht: de geschiedenis wordt alleen gebruikt om verwijzingen op te lossen
(`coreference.py`); of eerdere beurten als zoeksignaal iets toevoegen was niet gemeten. Gemeten op
dezelfde 90 echte vervolgbeurten als 2.6 (89 rondgekomen, 70 hebben een artikel nodig volgens de
beoordeling van toen), met de bestaande beoordelingen hergebruikt en alleen nieuwe passages
bijbeoordeeld.

| Variant (70 vervolgbeurten die een artikel nodig hebben) | Antwoordende passage in top-8 | Winst / verlies t.o.v. huidig |
|---|---|---|
| Huidig: herschrijving met geschiedenis + letterlijke zoekregel | 27 (39%) | – |
| Vorige bezoekersbeurt + vervolgvraag als één zoekvraag, geen herschrijving | 22 (31%) | 7 / 12 |
| Vorig assistentantwoord (eerste 500 tekens) + vervolgvraag, geen herschrijving | 35 (50%) | 21 / 13 |
| Huidig + leg "vorige bezoekersbeurt", RRF | 34 (49%) | 7 / 0 |
| **Huidig + leg "vorig antwoord", RRF** | **45 (64%)** | **18 / 0** |
| Huidig + beide legs, RRF | 43 (61%) | 16 / 0 |

Achttien winsten, nul verliezen. Het vorige antwoord is het sterkste signaal omdat de meeste
vervolgbeurten correcties of verdiepingen zijn op datzelfde artikel ("die optie zie ik niet", "en
per collega?"): de tekst van het antwoord staat qua woorden dicht bij het artikel waar het uit kwam,
dus die leg haalt het terug ook als de herschrijving van de korte vervolgvraag ernaast zit. Als
vervanging is hij slechter (21 om 13), als extra leg naast de bestaande herschrijving kost hij niets
aan recall. De 21 beurten met band `low` in de huidige keten: 12 daarvan krijgen met deze leg wel een
antwoordende passage.

Dit sluit aan op 2.7, waar de hele geschiedenis als zoekvraag het goede artikel 6 van 8 keer vond
tegen 1 van 8 voor de magere vraag, en op de CAsT-literatuur (Historical Query Expansion was het beste
automatische systeem in 2019). Het is ook de eerste maatregel op het zoeken die geen modelaanroep
kost: de tekst is er al.

**Wat dit niet bewijst:** hetzelfde als bij 2.27. Bovendien kan het vorige antwoord een verzinsel
bevatten; de leg zoekt dan naar het verzinsel. In deze meting was dat geen probleem (de beoordelaar
beoordeelt tegen de vraag, en het aantal antwoordende passages steeg zonder verlies), maar bij
livegang moet het dagrapport van de controle per zin op vervolgbeurten in de gaten gehouden worden.

**Stand:** niet gebouwd. Bouwvoorstel: in de zoekdienst één extra RRF-leg met de vector van "vorig
assistentantwoord + vervolgvraag" naast de bestaande legs (`hybrid_search` heeft er al drie), alleen
als er geschiedenis is. Geen extra modelaanroep, ongeveer 150 ms extra embedding. Vóór livegang de
poort uit §6.

### 2.29 Het plafond opnieuw gemeten: de kennisbank is het bij een derde, het zoeken bij een derde (18 sep)
§5 zet de kennisbank bovenaan als grootste rem, op grond van 2.16 (6 van 9 bronloze beurten niet in
de artikelen). Met de 54 kennisvragen van 2.27 en top-50 van de letterlijke vraag plus de
herformuleringen:

| Waar het antwoord zat | Aantal | Aandeel |
|---|---|---|
| In de top-8 van de letterlijke vraag (nu goed) | 19 | 35% |
| Op plek 9 tot 50 (rangschikking) | 3 | 6% |
| Alleen via een herformulering (formulering) | 16 | 30% |
| Nergens gevonden, ook niet via herformuleringen | 16 | 30% |

Van de 16 nergens gevonden zijn 3 geen kennisvragen ondanks het label ("Hoe snel gaat een trein?",
klant worden, een contactformulier dat als chatbericht binnenkwam). Voor de overige 13 is niet per
vraag nagegaan of het artikel ontbreekt of een derde formulering het wel had gevonden; 2.16 vond op
negen bronloze beurten bij zes het eerste. Neem 30% dus als bovengrens van het kennisbankplafond.

**Stand:** 2.16 had gelijk dat rangschikking geen probleem is (6%), en ongelijk dat het zoeken
daarmee af was. De kennisbank is het plafond voor ongeveer een derde van de kennisvragen; voor een
ander derde is het de formulering, en dat is met 2.27 en 2.28 te verhelpen. §5 is hierop herschreven.

### 2.30 Nulmeting van het harnas met de gescheiden beoordelaar (18 sep)
Dezelfde twaalf gesprekken als 2.19 en 2.21, nu met bezoeker, doelschrijver en scoorder op `klai-large`
en de scoorder uit 2.24 die een doorverwijzing niet als succes telt. Nul 429's, nul 502's.

| | 2.19 (oude scoorder) | Nu |
|---|---|---|
| Doel bereikt | 9 van 12 (75%) | **4 van 12 (33%)** |
| Eerlijk doorverwezen zonder antwoord | niet geteld | 8 van 12 |
| Eerste antwoord met bron | 9 van 12 (75%) | 9 van 12 (75%) |
| Gemiddeld aantal assistentbeurten | 2,7 | 4,0 |
| Gemiddeld verspilde beurten | 1,0 | 1,2 |

**Wat er nu zichtbaar is:** twee derde van de gesimuleerde gesprekken eindigt bij een medewerker
zonder dat de bezoeker zijn antwoord kreeg. Dat was in 2.19 en 2.21 onzichtbaar omdat de scoorder
precies die uitkomst als "bereikt" telde. De eerste beurt gedraagt zich nog steeds als in de
herspeling (75%).

**Bijwerking van de nieuwe bezoeker:** alle twaalf gesprekken liepen de vier beurten vol. De
bezoeker die pas stopt als zijn doel beantwoord is, geeft na een doorverwijzing niet op, ook niet als
het gesprek aantoonbaar vastzit. Beurttellingen en "verspilde beurten" zijn daardoor niet met 2.19 te
vergelijken; het getal dat vergelijkbaar blijft tussen versies is "doel bereikt". Dit is de nulmeting
waartegen de wijzigingen hieronder na livegang gelegd worden.

### 2.31 Het plafond per vraag: de kennisbank ontbreekt bij één op de negen, niet één op de drie (18 sep)
De dertien kennisvragen uit 2.29 waarvoor nergens een antwoordende passage stond, één voor één
nagezocht met wat de bezoeker uiteindelijk wilde (afgeleid uit al zijn beurten) als zoekvraag plus
vier herformuleringen daarvan, elk top-50, alle passages beoordeeld.

| Uitkomst | Aantal | Wat het betekent |
|---|---|---|
| Wél een antwoordend artikel gevonden | 7 | Het staat in de kennisbank, maar is vanuit de eerste vraag alleen niet te vinden: pas met wat de bezoeker later zei (openingstijden geavanceerd, gespreksopname, keuzemenu, tijdelijke omleiding, belplanextensies, belkosten, overige VoIP-telefoon) |
| Geen antwoordend artikel, wel aangrenzende | 6 | Beschikbaarheid in de app op vaste tijden, gemiste-oproep-log als een ander toestel opneemt, het notificatie-adres wijzigen, een belplan tijdelijk uitschakelen, een ontruimingsbel op alle toestellen, "connectivity" in de app |
| Geen kennisvraag | 3 | Trein, klant worden, een contactformulier dat als chat binnenkwam |

**Stand:** van de 54 kennisvragen staat bij 6 (11%) het antwoord niet in de artikelen; bij 7 (13%)
staat het er wel maar is de eerste vraag te mager om het te bereiken, en dat is het gat waar 2.4,
2.7, 2.10 en 2.20 op stukliepen (doorvragen schaadt, en zonder doorvragen weet het systeem het
ontbrekende detail niet). De "30% bovengrens" uit 2.29 is dus 11% echte kennisbankgrens plus 13%
vraaggrens. De zes concrete gaten staan hierboven; dat is de startlijst voor het inhoudswerk.

### 2.32 Het vorige antwoord als zoekleg: gebouwd, eind-tot-eind gemeten, afgevallen (18 sep)
Drie stappen, waarvan de laatste het verschil maakte.

**Eerst als goedkope leg vóór het herrangschikken.** Dezelfde tekst ("vorig antwoord[:500] +
vervolgvraag") als extra prefetch-leg in de RRF van Qdrant, daarna de gewone herrangschikking tegen
de vraag, op dezelfde 90 vervolgbeurten en met dezelfde beoordelingen: 43% tegen 43%, 5 winsten en
5 verliezen. De herrangschikker, die tegen de kale vraag scoort, duwt precies de kandidaten weer weg
die de leg binnenbracht. Dat is wat de literatuur voorspelde (RAG-Fusion in productie, §4).

**Dan gebouwd zoals gemeten.** Een tweede volledige pass (embedding, zoeken, herrangschikken tegen de
eigen tekst) parallel aan de eerste, en de twee top-8-lijsten met RRF samengevoegd na de
bronselectie. Op zoekniveau is dat de 39% naar 64% uit 2.28.

**Toen eind-tot-eind, en daar viel het om.** De echte widgetroute in-process (zelfde code, zelfde
controles en reparatie, previewtoken, niets opgeslagen) op de 70 vervolgbeurten die een artikel
nodig hebben, met hun echte geschiedenis, twee rondes per variant, blind beoordeeld door
`klai-large` mét de passages die beide varianten hadden, volgorde afgewisseld:

| | Ronde 1 | Ronde 2 | Samen |
|---|---|---|---|
| Oud beter | 39 | 26 | **65** |
| Nieuw beter | 25 | 36 | **61** |
| Gelijk | 6 | 8 | 14 |
| Antwoord met bron, oud → nieuw | 49 → 58 | 50 → 58 | |
| Vaste weigering, oud → nieuw | 19 → 11 | 19 → 11 | |
| "Bevat iets dat niet in de passages staat", oud → nieuw | 12 → 23 | 17 → 19 | 29 → 42 |
| Tijd per beurt, mediaan | 5,4 → 6,3 s | 5,4 → 6,4 s | |

Geen verschil in het blinde oordeel, en de twee rondes wijzen tegengesteld: hetzelfde paar kreeg in
23 van de 70 gevallen in beide rondes hetzelfde oordeel. De variatie van het antwoordmodel is groter
dan het effect. Wat wél consistent verschoof: meer antwoorden met bron, minder weigeringen, en meer
beweringen die de artikelen niet dragen. Per soort vervolgbeurt (140 paren): nieuwe vraag 13 om 7
voor nieuw, correctie ("dat werkt niet") 30 om 29, extra detail 25 om 18 voor oud. De tweede pass
bracht in 52 van de 69 beurten het artikel terug dat het vorige antwoord al citeerde; bij een
correctie krijgt het model dus het artikel dat net niet hielp als vers bewijs, en schrijft opnieuw
in plaats van eerlijk "niet gevonden".

**Nog één variant:** het eerder geciteerde artikel uit de tweede pass weglaten (59 beurten zonder
nieuwe-vraag-type, één ronde): 26 om 25, gelijk 8; verzonnen 15 → 13. Het extra verzinnen verdwijnt,
de winst komt niet.

**Besluit:** niet live. De code (een tweede pass in `retrieve.py`, RRF na bronselectie) is
hergebruikt voor 2.33, waar hij wél wint; de vorig-antwoord-invoer is eruit.

**Twee lessen die de meetafspraken raken.** Een zoekmeting ("antwoordende passage in de top-8") is
geen antwoordmeting: 2.27 en 2.28 wonnen beide op zoekniveau, één won eind-tot-eind. En één ronde is
geen meting: ronde 1 gaf hier oud 39 om 25, ronde 2 nieuw 36 om 26, op dezelfde beurten.

### 2.33 Twee herformuleringen van de eerste vraag: eind-tot-eind gewonnen, gebouwd (18 sep)
Dezelfde opstelling als 2.32, op de 54 echte eerste kennisvragen van 2.27: `klai-medium` schrijft
twee herformuleringen (zelfde taal, hooguit vijftien woorden, artikeltaal, geen details toevoegen),
elk gaat als eigen pass door het zoeken, en de drie top-8-lijsten worden na de herrangschikking
samengevoegd.

| | Ronde 1 | Ronde 2 | Samen |
|---|---|---|---|
| Nieuw beter | 32 | 31 | **63** |
| Oud beter | 22 | 21 | **43** |
| Gelijk | 0 | 2 | 2 |
| "Lost de vraag op", oud → nieuw | 15 → 28 | 15 → 26 | |
| "Bevat iets dat niet in de passages staat", oud → nieuw | 16 → 11 | 14 → 12 | 30 → 23 |
| Antwoord met bron, oud → nieuw | 50 → 53 | 51 → 51 | |
| Vaste weigering, oud → nieuw | 4 → 0 | 3 → 3 | |
| Tijd per beurt, mediaan / traagste tien procent | 4,9 / 7,0 → 7,3 / 10,0 s | 5,4 / 7,7 → 7,1 / 9,7 s | |

Twintig paren verschil, in beide rondes dezelfde richting, en minder verzonnen in plaats van meer:
het model krijgt het juiste artikel en hoeft niets in te vullen. Zoals op zoekniveau zit de winst bij
de lange, specifieke eerste vragen (51 om 32) en niet bij de magere (12 om 11).

**Kosten.** De herformulering 0,6 s mediaan op `klai-medium`; de drie passes parallel 1,95 s tegen
0,7 s voor één (ze delen één herrangschikker). Per eerste beurt ongeveer 2,3 s extra, ook in de
traagste tien procent. **Bijwerking:** de controle per zin haalde haar budget van 4 s vaker niet
(`judge_failed` 3 en 7 van 54 → 15 en 15 van 54), vermoedelijk omdat drie passes meer verschillende
artikelen opleveren en de controle meer tekst te lezen krijgt; het antwoord blijft dan
ongecontroleerd staan. Het aantal verzonnen beweringen daalde toch, maar dit hoort in de
nameting op echt verkeer (§5).

**Gebouwd:** `query_variants` op `RetrieveRequest` (hooguit drie, 500 tekens), elke variant een
eigen pass parallel aan coreferentie en hoofdzoekopdracht, RRF na bronselectie, tellingen in het
beslisrecord (`query_variants_run`, `query_variants_failed`, `query_variants_added`). In de widget
(`app/services/query_paraphrase.py`) alleen op de eerste beurt in support-modus, model
`retrieval_paraphrase_model` (`klai-medium`), 2,5 s budget, en bij een mislukte aanroep gaat de vraag
zoals voorheen. Vervolgbeurten blijven ongewijzigd (2.32).

### 2.34 De herformuleringen live, en het harnas ernaast (19 sep, direct na de deploy van #1548)
Dezelfde twaalf gesprekken als de nulmeting van 2.30, door de live widget, met de gescheiden
beoordelaar. Nul 429's, nul 502's.

| | Nulmeting (2.30) | Na de deploy |
|---|---|---|
| Doel bereikt | 4 van 12 | 4 van 12 |
| Eerlijk doorverwezen zonder antwoord | 8 van 12 | 9 van 12 |
| Eerste antwoord met bron | 9 van 12 | 10 van 12 |
| Gemiddeld verspilde beurten | 1,2 | 1,1 |

Per gesprek sloegen er vier om, twee elke kant. Bij twaalf gesprekken is dat ruis, precies zoals
2.21 en §6 zeggen; het harnas kan een effect van deze grootte niet zien, de herspeling van 2.33
(54 vragen, twee rondes) wel.

**Dat de nieuwe code draaide, staat in de containerlogs:** twaalf keer `partner_chat_query_paraphrase`
met twee herformuleringen en nul mislukkingen, en twaalf beslisrecords met `query_variants_run=2`,
`query_variants_failed=0` en 2 tot 6 chunks in de top-8 die zonder de herformuleringen niet meegekomen
waren (mediaan 4 van 8). De beurt waar het om ging in 2.21 (dubbele incasso) bleef een eerlijke
doorverwijzing.

**Wat nu volgt** staat in §5 punt 1: het dagrapport van de controle per zin op eerste beurten, want in
de herspeling liep die controle met de herformuleringen vaker tegen haar 4 s aan.

### 2.35 De bezoeker geeft op na twee doorverwijzingen, en de nulmeting verschuift (19 sep)
De bezoeker uit 2.24 stopt pas als zijn doel beantwoord is, en liep daardoor in 2.30 en 2.34 alle
twaalf gesprekken de vier beurten vol, ook als de assistent al twee keer een medewerker had
aangeboden. Een echte bezoeker klikt dan op de knop of gaat weg. Nu eindigt een gesprek na de tweede
afspraakknop (`_HAND_OFFS_BEFORE_GIVING_UP = 2` in `simulate_conversations.py`), met een test die het
vastlegt. Dezelfde twaalf gesprekken opnieuw, live, nul 429's:

| | 2.30 (oude bezoeker) | 2.34 (na #1548, oude bezoeker) | **2.35 (na #1548, nieuwe bezoeker)** |
|---|---|---|---|
| Doel bereikt | 4 van 12 | 4 van 12 | **2 van 12** |
| Eerlijk doorverwezen zonder antwoord | 8 van 12 | 9 van 12 | **11 van 12** |
| Eerste antwoord met bron | 9 van 12 | 10 van 12 | 9 van 12 |
| Gemiddeld aantal assistentbeurten | 4,0 | 4,0 | **2,5** (7 gesprekken stoppen na 2) |
| Gemiddeld verspilde beurten | 1,2 | 1,1 | 0,5 |

**Leesregel voor dit logboek, vanaf hier:** alles tot en met 2.34 staat tegen de nulmeting van 2.30
(bezoeker die niet opgeeft); alles vanaf 2.35 staat tegen deze nulmeting. De twee zijn niet met elkaar
te vergelijken: "doel bereikt" daalt van 4 naar 2 zonder dat de widget veranderde, omdat de bezoeker
nu ophoudt waar een mens ophoudt en de scoorder een doorverwijzing niet als succes telt. Wat dit
harnas nu laat zien is de widget zoals een bezoeker hem meemaakt: bij elf van de twaalf echte
beginvragen eindigt het gesprek bij een medewerker, meestal binnen twee beurten.

### 2.36 De zes onderwerpen die aantoonbaar ontbreken, voor de redactie (19 sep)
Uit 2.31, per onderwerp: wat de bezoeker vroeg (eigen woorden, ingekort), wat hij er later bij zei, wat
het zoeken wél vond en waarom dat het antwoord niet was. Geen code kan dit oplossen; het is inhoud.

1. **Beschikbaarheid in de app automatisch op vaste tijden.** Vraag: "Is het mogelijk om de
   beschikbaarheid in de app op vaste tijden automatisch in te stellen, bijvoorbeeld 8:30 aan en
   17:00 uit, elke werkdag?" Later: "kan dit ook per collega?" Gevonden: *Openingstijden | Basis*,
   *Verschillende manieren om Openingstijden te gebruiken*, *Zo gebruik je de Voys-app*. Die gaan
   over openingstijden van een nummer in het belplan, niet over de persoonlijke
   beschikbaarheidsstatus van een gebruiker in de app; of dat per gebruiker op een tijdschema kan,
   staat nergens.
2. **Gemiste oproep in het log terwijl een collega opnam.** Vraag: "zodra ik gebeld word en er wordt
   opgenomen door een andere telefoon, blijft de oproep op mijn telefoon als gemist staan." Later:
   "ik wil dat hij alleen als gemist staat als er niet wordt opgenomen." Gevonden: *Yealink
   bureautelefoon functies*, *Ik heb een probleem met mijn Yealink*, *Cisco-functies*. Die beschrijven
   toestelinstellingen in het algemeen; de instelling voor gemiste-oproepmeldingen bij een belgroep
   (of dat het per toestel niet anders kan) staat er niet.
3. **Het e-mailadres van gespreksnotificaties wijzigen.** Vraag: "ik wil dat de mail van een gemiste
   oproep naar een ander e-mailadres wordt verzonden." Later: "de mails komen al binnen op ons
   infomail, ik wil alleen het adres veranderen"; "waar staat het e-mailadres voor
   gespreksnotificaties?"; "na Administratie zie ik geen Stap 4 notificaties." Gevonden:
   *Gespreksnotificaties*, *E-mail voor gemiste gesprekken met Zapier*. Het eerste zegt dat de
   notificaties bestaan, niet waar het adres staat en hoe je het wijzigt; het tweede is een omweg via
   Zapier die de bezoeker niet zocht. De stappen in het artikel kloppen bovendien niet met wat de
   bezoeker in het scherm zag ("geen Stap 4 notificaties").
4. **Een belplan tijdelijk uitschakelen zodat het nummer direct overgaat.** Vraag: "Hoe kan ik
   tijdelijk het hele belplan van een nummer weghalen zodat het nummer rechtstreeks, zonder menu, te
   bereiken is, en daarna weer terugzetten?" Later: "niet naar voicemail omleiden"; "ik zie geen
   bestemming onder tijdelijke omleiding"; "hoe verwijder ik een belplan." Gevonden: *Tijdelijke
   omleiding*, *Je belplan instellen en aanpassen*. Tijdelijke omleiding stuurt naar een voicemail
   of een ander nummer; een belplan pauzeren met behoud van het plan (of de aanbevolen werkwijze:
   een kopie, een extensie) staat nergens beschreven.
5. **Alle toestellen laten bellen bij een ontruiming.** Vraag: "Is het mogelijk dat bij een
   ontruiming alle toestellen in het kantoor gebeld worden met een ontruimingsmelding?" Later: "het
   hoeft niet automatisch; de BHV'er belt een nummer waardoor bij iedereen de telefoon overgaat en
   blijft overgaan, of een speciale ringtone." Gevonden: *Belgroepen*, *Piketdienst*,
   *Gespreksnotificaties*, *Snom-opties*. Belgroepen komt het dichtst bij (alle toestellen tegelijk
   laten rinkelen) maar zegt niets over doorbellen na opnemen, een omroep of een aparte ringtone per
   belgroep; een artikel "ontruiming of alarm via de telefonie" ontbreekt.
6. **"Connectivity" in de app, en een dubbel gesprek zonder opname.** Vraag: "dubbel gesprek in
   gesprekken, geen opname." Later: "gespreksopname staat aan en werkt normaal ook"; "was een
   uitgaand gesprek"; de bezoeker zoekt een optie die hij "connectivity" of "connectify" noemt.
   Gevonden: *De Voys App*, *iPhone Voys App Probleemoplosser*. Geen artikel noemt die optie of
   beschrijft wat een dubbel gesprek in het overzicht betekent en waarom de opname dan ontbreekt.
   Mogelijk is de term van de bezoeker zelf verkeerd; ook dan hoort de app-terminologie ergens
   uitgelegd te staan.

Daarnaast uit 2.16: de artikelketen voor internationale gesprekken loopt halverwege dood ("hoe schakel
ik het account in voor internationale gesprekken" staat er niet, de bezoeker komt vast te zitten op
*Belkosten*).

### 2.37 Het budget van de controle per zin op eerste beurten: gemeten, niet verhoogd (19 sep)
Met de herformuleringen live haalde de controle per zin in de herspeling van 2.33 haar 4 s vaker niet
(13 en 14 van 54 tegen 3 en 5 daarvoor). Dan blijft het antwoord ongecontroleerd staan. Gemeten wat
een ruimer budget doet: dezelfde 54 eerste vragen door de live keten (mét herformuleringen), één ronde
met 4 s en één met 8 s voor de controle, reparatie ongewijzigd 3 s.

| | Controle 4 s (huidig) | Controle 8 s |
|---|---|---|
| Controle niet afgerond binnen budget | 9 van 54 | 1 van 54 |
| Gerepareerd | 6 | 24 |
| Vaste weigering | 3 | 1 |
| Antwoord met bron | 50 | 53 |
| Duur van de controle-aanroep, mediaan / traagste tien procent | 2,3 s / 4,0 s (afgekapt) | 1,9 s / 4,2 s; 3 aanroepen boven 6 s |
| Tijd per beurt, mediaan / traagste tien procent | 6,0 s / 9,5 s | 6,6 s / 9,6 s |
| Beurten boven 12 s | 1 | 3 |
| Blind, `klai-large` mét passages: beter | 27 | 24 (2 gelijk) |
| "Bevat iets dat niet in de passages staat" | 7 | 7 |

Het ruimere budget laat de controle afronden en de reparatie vier keer zo vaak draaien, maar in het
blinde oordeel en in het aantal antwoorden met een niet-gedragen bewering is daar niets van terug te
zien, terwijl de mediaan 0,6 s stijgt en het aantal beurten boven twaalf seconden verdrievoudigt. Eén
ronde en 54 vragen, dus een verschil onder de ruis; maar de kosten zijn wél zichtbaar.

**Besluit:** het budget blijft 4 s. Wie de controle liever wél laat afronden: van de 9 afgekapte
aanroepen zaten er 5 tussen 4 en 6 s, dus 6 s dekt het merendeel tegen één tot twee seconden extra op
alleen die beurten. Dat is een afweging tussen wachttijd en een controle die vaker afrondt zonder
aantoonbaar beter antwoord, en die ligt bij de eigenaar van de widget.

### 2.38 "Geen nieuwe gok zonder nieuw artikel": onder de ruis, niet gebouwd (19 sep)
§5 noemde de regel: bij een vervolgbeurt waarvan het zoeken alleen artikelen oplevert die al geciteerd
waren, een medewerker aanbieden in plaats van opnieuw te schrijven. Nagerekend op de 140 herspeelde
vervolgbeurten van 2.32 (twee rondes, 70 beurten): het geval doet zich 1 en 2 keer voor, en in die
beurten was het antwoord al de eerlijke "niet gevonden" mét bron. De regel zou dus hooguit twee van
zeventig beurten raken en daar niets veranderen. Niet gebouwd.

### 2.39 De afgekapte controle loopt door op de achtergrond en legt vast wat ze gevonden had (19 sep)
Het besluit van 2.37 laat een open vraag: wat mist een bezoeker als de controle na 4 s wordt afgekapt?
De herspeling zegt het voor 54 vragen, echt verkeer zegt het niet. Daarom wacht het antwoord nog steeds
hooguit 4 s, maar de controle zelf wordt niet meer afgebroken. Ze loopt op de achtergrond af en logt
haar oordeel als `answer_grounding_late`: aantal uitspraken, niet gedragen, tegengesproken, en of het
de drempel voor een reparatie gehaald zou hebben. Wordt dat oordeel vaak "had gerepareerd moeten
worden", dan is dat het bewijs om het budget naar 6 s te zetten; blijft het leeg, dan is 4 s
bevestigd op echt verkeer.

Drie grenzen, uit de review van #1554: een controle op de achtergrond krijgt hooguit 20 s in totaal,
er lopen er hooguit 20 tegelijk (de rest wordt afgebroken en gelogd als
`answer_grounding_late_skipped`, zodat een storing bij het model geen stapel taken oplevert), en een
verzoek dat de bezoeker afbreekt, breekt ook zijn controle af.

### 2.40 Acht meetpunten, geen enkele lezer: het overzicht onder Platform (19 sep)
Bij het nalopen van deze keten bleek dat alles wat hierboven gemeten wordt in productie wel wordt
vastgelegd, maar dat geen dashboard of alarm er één van leest. Wie niet weet dat
`partner_chat_answer_judge` bestaat, vindt het niet. Platformbeheerders zien nu onder **Platform →
Status → Meetpunten** alle elf meetpunten van deze keten: wat er wordt vastgelegd en waarom, waar je
het leest, en of iets het automatisch leest. Het toont nooit de inhoud van de logs of records zelf.
Alleen de bewaking op klantgegevens in de repository leest zichzelf; de andere tien zijn zichtbaar
voor wie gaat kijken, en meer niet. De lijst staat in `app/services/observability_signals.py`; een
nieuw meetpunt in deze keten hoort daar een regel bij te krijgen.

### 2.41 De herformuleringen bereikten de browserwidget niet (22 sep)
`first_question_variants` behandelde iedere beurt met een assistantbericht in de historie als
vervolgbeurt. De browserwidget zet sinds mei zijn welkomstregel als assistantbericht vooraan in elk
gesprek en stuurt die bij iedere vraag mee, ook als de regel leeg is. Een echte eerste vraag uit de
browser kreeg daardoor nul herformuleringen. De unittest, de herspeling van 2.33 en het harnas van
2.34 begonnen alle drie met alleen de vraag van de bezoeker. 2.34 noemde dat de live widget, maar
bewees alleen de rechtstreekse API-route; wat 2.34 in de logs zag, klopt voor die route en blijft
staan.

Met de echte functie nagerekend: welkomstregel plus vraag gaf nul herformuleringen en nul
aanroepen, alleen de vraag gaf er twee, een echte vervolgbeurt nul. Daardoor is ook §5 punt 8 tot nu
toe niet gemeten: sinds 19 september draaide echt browserverkeer zonder herformuleringen, dus de
gegevens van `answer_grounding_late` (2.39) over die periode gaan over eerste beurten zonder.

**Gewijzigd.** "Eerste vraag" telt nu de beurten van de bezoeker: precies één gebruikersbericht.
Nagelopen tegen de drie gespreksvormen die de widget verstuurt. Een nieuw gesprek (welkomstregel
plus vraag) krijgt de herformuleringen. Een hervat gesprek stuurt de bewaarde historie mee en heeft
dus eerdere gebruikersberichten, en na de toestemming voor een breder antwoord staat de
oorspronkelijke vraag al in de historie; die twee blijven vervolgbeurten. De unittest stuurt nu de
browservorm, en het harnas zet de welkomstregel van de widget vooraan, zodat het de route meet die
een bezoeker neemt.

**Bewijs voor de winst** blijft 2.33: de herformulering krijgt alleen de vraag zelf, dus de 54
herspeelde vragen meten precies wat deze reparatie inschakelt. De bekende bijwerkingen gelden nu wel
voor het eerst op echt browserverkeer: ongeveer 2,3 s extra per eerste beurt en vaker een afgekapte
controle per zin (2.33, 2.37). Die horen in de nameting van §5 punt 8.

**Live (#1593, 22 sep).** Na de deploy draaide de productiecontainer de nieuwe helper. Eén eerste
vraag in browservorm (welkomstregel van de widget plus vraag), via een previewsessie zodat er niets
in de gesprekken van de klant terechtkwam: `partner_chat_query_paraphrase` met twee
herformuleringen in 0,9 s, en in het beslisrecord van retrieval-api `query_variants_run=2`,
`query_variants_failed=0`, `query_variants_added=2`. Dat bewijst dat de route nu werkt voor de
berichtvorm die de browser verstuurt. Een klik in de echte widget is bewust overgeslagen, omdat
die een gesprek in de tenant van de klant schrijft; de nameting op echt verkeer volgt uit §5 punt 8.

### 2.42 Een technisch gespreksverzoek kreeg de commerciële doorverwijstekst (22 sep)
Een bezoeker vroeg om een gesprek met de technische afdeling over een koppeling met hun CRM. De
onderwerpbeoordelaar koos `not_handled`, waarna de vaste tekst over prijzen, offertes en contracten
de antwoordgeneratie verving, terwijl het zoeken relevante integratieartikelen had gevonden. De
menselijke beoordeling: een technische vraag, dus geen tekst over prijzen en offertes.

In zes herhalingen van de beoordelaar (twee opstellingen van drie) gaf die bij deze beurt steeds
`wants_human=true`; `topic` sloeg om tussen de opstellingen, maar daar verschilde ook het
gerouteerde model, dus dat verschil is niet toe te schrijven aan de welkomstregel. In de dertig dagen
ervoor kwam `not_handled` samen met een verzoek om een mens één keer voor, bij precies deze beurt.

**Niet gebouwd, en waarom.** De voor de hand liggende ingreep is dat een verzoek om een mens voorgaat
op de vaste tekst: dan schrijft het model een antwoord met de afspraakknop eronder. Dat breekt de
garantie waarvoor de instelling bestaat, namelijk dat bij een uitgesloten onderwerp geen model een
woord schrijft, ook niet bij "ik wil iemand spreken over een offerte". De ingreep raakt op dertig
dagen één beurt; de vraag of die garantie moet wijken voor een gespreksverzoek is een keuze van de
eigenaar van de widget, net als de onderwerptekst "aanvragen voor nieuwe diensten" zelf. Wie die
tekst heeft ingevoerd, is niet na te gaan: widgetinstellingen hebben geen versiegeschiedenis. De
drie eerder gemeten routes van 2.18 blijven afgevallen.

**Besluit van de eigenaar (22 sep, later die dag).** Een verzoek om een mens mag niet op de vaste
tekst over prijzen en offertes uitkomen, en de doorverwijzing mag het onderwerp van de bezoeker
noemen in plaats van de hele lijst uitgesloten onderwerpen. Uitgewerkt in 2.43.

### 2.43 De doorverwijzing noemt het onderwerp van de bezoeker (22 sep)
**Eerste opzet, afgevallen in de review.** Een verzoek om een mens liet de vaste tekst over, en ging
naar de gewone beurt voor een menselijk verzoek. Die beurt genereert met de gevonden artikelen in de
prompt, en de woordherkenning voor een menselijk verzoek slaat ook aan op "kan iemand mij vertellen
wat een 0800-nummer kost?". Een prijs uit een artikel kon zo alsnog bij de bezoeker komen, en de
controle per zin zou die als gedragen goedkeuren. Daarnaast schreef een klein model de hele
doorverwijzing, bewaakt door een lijst verboden tekens en woorden. De review vond daar gaten in:
"is gratis", voluit geschreven bedragen, links zonder `http`.

**Wat er gebouwd is.** Elke beurt met een uitgesloten onderwerp, ook een verzoek om een mens, krijgt
een doorverwijzing met de afspraakknop, en geen antwoordmodel ziet de artikelen. Een kleine aanroep
(`off_topic_referral.py`, `klai-fast`, 2,5 s) levert alleen het onderwerp: een korte zinsnede zoals
"je factuur" of "een offerte voor 25 gebruikers". De zin eromheen is van ons: "Over … kijkt een
collega graag persoonlijk met je mee. Plan hieronder een afspraak, dan helpen we je verder." De
zinsnede mag alleen letters, cijfers en spaties bevatten, en elk inhoudswoord en elk getal moet uit
de vraag van de bezoeker komen. Zo kan het model geen bewering, prijs of link toevoegen die de
bezoeker niet zelf typte. Faalt de aanroep, komt de zinsnede er niet door, of is de taal geen
Nederlands of Engels, dan blijft de vaste tekst van de widget staan. Alleen de Voys-widget gebruikt
de instelling.

**Meting van de eerste opzet** (22 echte vragen die de vaste tekst kregen, drie keer, `klai-fast`,
7,5 s tussen aanroepen, eigen proefcontainer, geen schrijfacties): 66 van 66 aanroepen geslaagd, geen
bedrag of link, mediaan 0,9 s en traagste 2,3 s. Wel twee keer een gedachtestreepje, en twee van de
drie Engelse vragen kregen een Nederlandse zin. Beide zijn in de gebouwde opzet uitgesloten: de taal
komt uit de gesprekstaal en de zin eromheen is vast.

**Meting van de gebouwde opzet** (dezelfde 22 vragen, vier keer, verder gelijk): 77 van 88 keer een
doorverwijzing met het onderwerp, 11 keer de vaste tekst. Van die 11 was er één een verlopen aanroep
(2,5 s); de andere tien gebruikten een woord dat de bezoeker niet typte, zoals "opzegging" bij "hoe zeg
ik mijn contract op", en vielen dus terecht terug. Geen enkele zin met een bedrag, een bewering of een
link, omdat het model alleen het onderwerp levert. Mediaan 0,6 s, 90% binnen 1,0 s. De ik-vorm uit de
eigen woorden van de bezoeker ("met mijn mobiele nummer") wordt vast omgezet naar de je-vorm; dat is
nagerekend op dezelfde 88 opgeslagen uitkomsten. Voorbeelden: "Over je factuur kijkt een collega
graag persoonlijk met je mee", "Over verdere integratie van Voys in je CRM platform …", "Over uitstel
van betaling voor je factuur …".

### 2.44 Doorvragen: wat de beoordelingen laten zien, wat het onderzoek zegt, en het ontwerp (22 sep)
**Wat de beoordelingen laten zien.** Zeventien menselijk beoordeelde gesprekken van 22 september,
goede en foute, met het hele gesprek erbij. In bijna elk begint het systeem meteen aan een antwoord,
ook als het nog niet weet waar de bezoeker het over heeft:
- "ik kan niet bellen of gebeld worden met mijn apparaat" kreeg een lange algemene lijst, terwijl de
  vraag welk apparaat (toestel, Webphone, app; Android of iOS) alles bepaalde;
- "inkomende gesprekken komen niet altijd door" kreeg een netwerkoorzaak, zonder te vragen wat er
  niet doorkomt en wanneer;
- een vraag over een AI-assistent koppelen nam één leverancier aan;
- Webphone-meldingen kregen Windows-stappen voordat bleek dat de bezoeker een Mac had;
- "bellen naar België aanzetten" werd twee keer verkeerd gelezen, terwijl het om belrechten voor het
  buitenland ging.
Waar het systeem wél eerst vroeg (welke headset; of de openingstijden-modules gevuld waren), oordeelde
de beoordelaar goed of perfect. Het model kan het dus, maar doet het toevallig: 9 van de 38
antwoorden in deze gesprekken bevatten een vraag. Op twee weken echt verkeer noemt de vraagbeoordelaar
ongeveer één op de zeven beurten onduidelijk, maar dat stuurt sinds 2.4 niets meer.

**Afbakening van de eigenaar.** Een platformstoring is zeldzaam en het systeem moet daar niet omheen
gebouwd worden: geen "dit kan even duren" of storingspagina als standaardopening. Individuele
verstoringen zijn het grootste deel van het werk: niet kunnen uitbellen, niet gebeld worden, slechte
audio, gesprekken die naar voicemail gaan.

**Wat het onderzoek zegt.**
- Of een vraag nodig is, voorspel je beter uit de gevonden artikelen dan uit de vraag zelf: een vage
  vraag levert artikelen op die weinig op elkaar lijken, en die samenhang voorspelt de noodzaak
  zonder training even goed als gesuperviseerde methoden
  ([Arabzadeh, Seifikar en Clarke 2022](https://arxiv.org/abs/2208.04882)). Taalmodellen herkennen
  vaagheid in de vraag zelf slecht ([CLAMBER, ACL 2024](https://aclanthology.org/2024.acl-long.578/)),
  wat past bij de voicemailvraag die zes keer "duidelijk" heette.
- Bij een storing werkt diagnose vóór advies: gevonden gevallen groeperen per mogelijke oorzaak en de
  vraag stellen die tussen die oorzaken kiest. Op 150 IT-supportgevallen 78,7% opgelost tegen 41,3%
  voor gewone RAG, in 3,9 beurten tegen 8,4 ([DQA, 2026](https://arxiv.org/abs/2604.05350)).
- Een vraag moet steunen op wat er echt in de artikelen staat, anders verzint het model opties
  ([Krasakis, Yates en Kanoulas 2024](https://arxiv.org/abs/2409.18575)); een specifieke vraag helpt
  meer dan een algemene ([Rahmani e.a., EACL 2024](https://arxiv.org/abs/2402.01934)).
- Wie een antwoord los beoordeelt, verkiest een volledig antwoord dat iets aanneemt boven een goede
  vraag; beoordeel wat de beurt erna oplevert
  ([Zhang, Knox en Choi, ICLR 2025](https://arxiv.org/abs/2410.13788)).

**Wat dat zegt over 2.4.** De doorvraag-opdracht van toen hing aan een beoordelaar die naar de vraag
keek, en werd gemeten met een beoordelaar die één antwoord tegen een ander legde. Beide zijn precies
de twee zwaktes die het onderzoek noemt. De 19 om 4 van toen zegt dus weinig over wat een goede vraag
in een gesprek oplevert; hij zegt wel dat een algemene opdracht "vraag bij twijfel" niet werkt.

**Ontwerp, in deze volgorde.**
1. *De meting eerst.* De menselijk beoordeelde gesprekken worden de meetset, met de opmerking van de
   beoordelaar als verwachting. Een gesimuleerde bezoeker met het echte probleem beantwoordt de vraag
   van het systeem, en gemeten wordt of het probleem na de volgende beurt is opgelost. De goed
   beoordeelde gesprekken tellen mee als behoud: een heldere instelvraag mag niet trager of vragender
   worden. Zonder deze meting wint gokken weer, zoals in 2.4.
2. *Individuele storingen: eerst diagnose.* Meldt de bezoeker iets dat niet werkt, dan groepeert een
   kleine aanroep de gevonden artikelen tot hooguit vier mogelijke oorzaken en kiest de ene vraag die
   daartussen het meest onderscheidt. Het antwoord noemt kort de waarschijnlijke oorzaken en eindigt
   met die vraag; opties komen alleen uit de gevonden artikelen.
3. *Instelvragen met meerdere wegen: kort onderscheiden.* Vallen de gevonden artikelen uiteen in
   verschillende procedures, dan staan die kort naast elkaar met één keuzevraag; wijzen ze naar één
   procedure, dan komt het directe antwoord zonder vraag.
4. *Uitrol* alleen als de meting in twee rondes dezelfde kant op wijst, met wachttijd erbij.

### 2.45 Vaste zinnen die het model overnam: "Dit kan even duren", een belofte, gedachtestreepjes (22 sep)
De eigenaar zag in beoordeelde gesprekken steeds dezelfde fouten terug. Alle drie kwamen uit de
SUPPORT-prompts zelf:
- "Dit kan even duren" stond als voorbeeld in de lijst "zeg de gewone zinnen op de Voys-manier", en
  het model opende er antwoorden op een storing van één bezoeker mee;
- het voorbeeld bij excuses was "Onze excuses, we gaan dit oplossen", en dat kwam terug als "we gaan
  dit voor je oplossen", een belofte die de chat niet kan waarmaken;
- gedachtestreepjes: de prompttekst staat er vol mee en nergens stond dat het niet mocht; ook de vaste
  tekst bij "niet gevonden" en het label voor een antwoord uit algemene kennis bevatten er een, en
  die vaste tekst is een van de vaakst getoonde antwoorden.

**Gewijzigd.** "Dit kan even duren" is uit de lijst; het excuusvoorbeeld is "Onze excuses, dat had ik
verkeerd begrepen". In alle drie de widgetprofielen staat nu: zeg nooit dat wij iets voor de bezoeker
oplossen, regelen of repareren. Een widgetantwoord gaat na de
laatste controle door een vaste filter die een gedachtestreepje tussen woorden vervangt door een
komma; een bereik als 9–17 blijft staan. De vaste teksten zijn zonder streepje herschreven. Opgeslagen
gesprekken van vóór de wijziging dragen de oude teksten nog; de uitkomstlabels herkennen beide vormen.

**Gemeten** met de nieuwe meting uit 2.44 (zie 2.46): de 17 beoordeelde gesprekken, twee rondes, de
live code tegen deze wijziging, dezelfde gesimuleerde bezoeker en blinde beoordelaar in beide volgordes.
"Dit kan even duren" 2 keer tegen 0, antwoorden met een gedachtestreepje 6 tegen 0, een belofte "we
gaan" 0 tegen 0, antwoorden "niet gevonden" 6 tegen 7, mediane beurt 7,8 tegen 6,2 s. Blinde voorkeur
ronde 1 live 23 tegen 11, ronde 2 deze wijziging 16 tegen 15: de richting slaat om, dus ruis (§6).
Een extra regel "open niet met een storingspagina" is weer geschrapt: in de meting opende het model bij
een storingsmelding toch met de statuspagina, en in de variant met die regel antwoordde het op dezelfde
melding twee keer "niet beschreven". Die opening hoort bij de diagnosestap uit 2.46, niet in een zin.

### 2.46 Doorvragen als instructie in de prompt: gemeten, niet genoeg (22 sep)
**De meting.** `followup_eval` (buiten de repo, zoals §6 voorschrijft) speelt de widgetroute
in-process met een alleen-lezen database en previewsessies. Per beoordeeld gesprek: het antwoord, dan
een gesimuleerde bezoeker (`klai-large`) die alleen weet wat de echte bezoeker later zelf zei, dan het
volgende antwoord. Een blinde beoordelaar (`klai-large`) vergelijkt beide gesprekken in beide
volgordes tegen de verwachting uit de opmerking van de menselijke beoordelaar: begrijpt het systeem
het probleem, kiest het de juiste vervolgstap, is het bruikbaar. De set: 17 gesprekken, 6 waar de
beoordelaar doorvragen verwachtte, 1 met meerdere procedures, 10 controles.

**De kandidaat.** Een instructie achter de prompt: meldt de bezoeker dat iets niet werkt, noem dan de
oorzaken uit de gevonden artikelen en eindig met één vraag die ze onderscheidt; beschrijven de
artikelen meerdere procedures, zet ze kort naast elkaar en vraag welke; anders direct antwoorden.

**Uitkomst.** Blind ronde 1 kandidaat 20 tegen 10 (4 gelijk), ronde 2 16 tegen 16: geen vaste richting,
dus ruis. De kandidaat stelde niet vaker een vraag (4 van 12 op gesprekken waar doorvragen verwacht
werd, tegen 5 van 12 zonder). Bij "iedereen die belt gaat naar voicemail" legde hij nog steeds uit hoe
je alles naar voicemail stuurt; bij "al dagen geen service" opende hij met de statuspagina ondanks het
expliciete verbod. Eén gesprek won hij in alle vier de oordelen: "ik kan niet bellen of gebeld worden
met mijn apparaat" kreeg "gebruik je de app op een iPhone of op Android?".

**Besluit.** Niet live. Het past bij wat het onderzoek in 2.44 zegt: een losse opdracht in de prompt
stuurt het model nauwelijks, en of een vraag nodig is moet uit de gevonden artikelen komen. De
volgende kandidaat is de aparte diagnosestap: vóór het schrijven groepeert een kleine aanroep de
gevonden artikelen tot oorzaken of procedures en kiest de ene vraag, en het antwoord krijgt die
structuur mee in plaats van een algemene opdracht. Dezelfde meting beslist.

### 2.47 De diagnosestap: eerste versie gemeten, de vraag kende het gesprek niet (23 sep)
2.46 liet zien dat een opdracht in de prompt het gedrag niet stuurt. Deze kandidaat beslist daarom
vóór het schrijven, tegen wat het zoeken werkelijk vond: een kleine aanroep (`answer_plan.py`,
`klai-fast`, 2 s) krijgt de vraag en de gevonden artikelen en kiest tussen direct antwoorden, een
storing diagnosticeren en procedures onderscheiden. Bij de laatste twee levert hij één vraag en twee
tot vier opties, en elke optie moet woordelijk in de artikelen voorkomen, dezelfde regel als de
doorverwijzing van 2.43. Het antwoordmodel krijgt die vraag mee in plaats van een regel.

**Eerste meting** (17 beoordeelde gesprekken, twee rondes, live code ernaast, blind in beide
volgordes): ronde 1 de kandidaat 21 om 12, ronde 2 de live code 17 om 16. De richting slaat om, dus
ruis (§6). Wachttijd bleef gelijk: mediaan 6,4 tegen 6,3 s per beurt.

**Wat de gesprekken zelf lieten zien** telt hier zwaarder dan het oordeel. Bij "tijden instellen voor
doorschakeling" zette de kandidaat de drie situaties naast elkaar en vroeg welke de bezoeker bedoelde,
precies wat de menselijke beoordelaar verwachtte. Bij "iedereen die belt gaat naar voicemail" gaf hij
geen instructies meer om voicemail juist aan te zetten. Maar op de controlegevallen stelde hij in 10
van de 16 beurten een vraag terwijl er niets te vragen viel, en de reden bleek een ontwerpfout: de
stap kreeg alleen het laatste bericht te zien. Op de beurt "Heb een macbook" vroeg hij daardoor welk
probleem de bezoeker had, terwijl dat één beurt eerder stond. De opties waren soms geen oorzaak maar
een herhaling van het symptoom.

**Gewijzigd en opnieuw gemeten.** De stap leest nu dezelfde zes laatste beurten als het zoeken, mag
niets vragen wat het gesprek al beantwoordt, en moet een optie naar de oorzaak of de procedure noemen
in de woorden van het artikel. Tweede meting, zelfde opzet: ronde 1 de kandidaat 20 om 12, ronde 2
18 om 13.

**Review.** Drie ernstige bevindingen, alle drie gerepareerd met eerst een falende test. Een bericht
dat als tekstdelen binnenkomt (een vorm die de route overal elders accepteert) liet de stap crashen.
De vraag van de stap kwam zonder toets in de prompt van het antwoordmodel. En de optiecontrole liet een
los getal of bedrag door, telde dubbele opties als twee keuzes en handhaafde de lengte niet. De vraag
moet nu één regel zijn die op een vraagteken eindigt, zonder link of haken, en minstens één van de
getoetste opties noemen; een getal in een optie moet letterlijk in de artikelen staan. Een eerste
reparatie toetste elk woord van de vraag aan de artikelen. Dat schrapte precies de vragen waarvoor de
stap bestaat (op de gesprekken waar doorvragen verwacht werd 1 van 6 in plaats van 4 van 6), omdat een
storingsmelding andere woorden gebruikt dan het artikel dat de oorzaak uitlegt, en is vervangen.

**Derde meting, de versie die live gaat:** ronde 1 17 om 14, ronde 2 24 om 9, dezelfde richting.
"Begrijpt het probleem" 37 tegen 21 van de 68 oordelen, "juiste vervolgstap" 32 tegen 17, "bruikbaar"
56 tegen 43. Mediane beurt 6,8 tegen 6,6 s. Wat het kost: op de controlegevallen stelde de stap in 5
van de 16 beurten een vraag die niet nodig was (live 1 van 16), en twee gesprekken verloor hij in alle
vier de oordelen: een headsetvraag waar het directe antwoord al goed was, en een webphoneprobleem
waar de gestelde vraag minder hielp dan de lijst controles. Die twee blijven de maat voor een volgende
versie.

**Twee grenzen die blijven.** Heeft een beurt een bron, dan kan de afspraakknop nog onder de gestelde
vraag staan: de beoordelaars voegen alleen toe en halen niets weg, een oudere afspraak die hier niet
verandert. En de stap draait ook als het zoeken alleen zwakke bronnen vond; daar hoort de regel voor
zwakke bronnen, die apart gemeten wordt.

**Uitgerold** met `partner_chat_answer_plan` in de logs en `planned_question` in het beslisrecord, zodat
op echt verkeer te zien is hoe vaak de stap een vraag laat stellen.

**Na de uitrol (24 sep).** Live nagelopen op de voicemailvraag: de stap koos terecht diagnose met twee
juiste opties, maar zijn vraag ("Wat gebeurt er precies met de gesprekken?") noemde geen van beide en
viel af op de regel dat de vraag een optie moet noemen. Geprobeerd en gemeten: de stap opdragen de keuze
in de vraag te zetten, en één optiewoord laten volstaan in plaats van een hele optie. Ronde 1 de live
versie 19 om 13, ronde 2 16 om 16, en de beurt werd trager (mediaan 7,8 tegen 7,0 s). Niet uitgerold.
Wel uitgerold: elke beslissing van de stap komt als `answer_plan_decision` in de logs, met de route
en welke controle een plan liet vallen, zonder de tekst van de vraag. Zonder die regel was het
wegvallen van de voicemaildiagnose alleen met een handmatige proef te vinden geweest.

### 2.48 Geen antwoord meer uit een zwakke bron (24 sep)
**Aanleiding.** De menselijke beoordelingen scheiden goed en fout op de sterkte van de beste bron: bij
antwoorden die de eigenaar goed noemde lag die mediaan op 0,80, bij "verkeerde kennis" op 0,37, en daar
zat 5 van de 6 onder 0,5. In de dertig dagen ervoor kwam ruim een kwart van de echte widgetantwoorden
boven een beste bron onder 0,3: het "waarom begint hij over Grandstream"-patroon. (Een eerdere versie
van deze sectie noemde een veel hoger aantal: die telling filterde niet op test- en previewgesprekken,
die in die periode het overgrote deel van de antwoorden vormden.) Het systeem kende die situatie al als
zachte lacune (`classify_gap`: elke bron onder 0,4), maar dat hield het antwoord niet tegen.

**Gewijzigd.** Bij een zachte lacune, als de stap uit 2.47 geen vraag plande, krijgt het antwoordmodel
de opdracht de artikelen alleen te gebruiken als één ervan de vraag letterlijk beantwoordt, en anders
eerlijk te zeggen dat het niet in de helpartikelen staat, zonder omweg uit een naburig artikel, met de
afspraakknop. Niet op een breed antwoord, een verzoek om een mens, of een gespreksbeurt: een bedankje
haalt vaak ook alleen zwakke artikelen op en houdt zijn korte antwoord (gevonden in de review).

**Gemeten** op de 27 meest recente echte beurten waar alle bronnen onder de drempel zaten en er tóch
geantwoord werd, twee rondes, live code ernaast, blind in beide volgordes: ronde 1 34 om 17, ronde 2 40
om 14. "Juiste vervolgstap" 74 tegen 30 van de 108 oordelen, mediane beurt 5,1 tegen 6,0 s. "Niet
gevonden" steeg van 16 naar 29 van de 54 antwoorden. Omdat de beoordelaar hier als verwachting kreeg
"antwoord alleen als een artikel het echt beantwoordt", zijn de 12 vragen die omsloegen ook met de hand
nagelezen. Eén was een echt verlies: een vraag over een eigen geluidsfragment verloor een bruikbare
route. Meerdere omslagen waren fouten die de eigenaar eerder zelf afkeurde: een Bubble-firewallantwoord
op een vraag over buitenlandbellen, "controleer storingen en update de app" bij een permissiemelding,
Belgische informatie bij bedrijfsgegevens en de iPhone-uitleg bij een bureautoestel. De rest was
ongeveer gelijkwaardig.

**Review en bronkaarten.** In de meting droeg 26 van de 29 antwoorden "dit vind ik niet terug" toch het
naburige artikel als bronkaart eronder (live was dat al 12 van de 16), omdat het citaatfilter een
bron houdt zodra de tekst er woorden mee deelt. De bezoeker zag dan "niet gevonden" met een ongerelateerd
artikel eronder. Op een beurt met alleen zwakke bronnen vervallen de bronkaarten nu als de beoordelaar
het antwoord niet "beantwoord" noemt; de afspraakknop blijft. De vlag gaat als parameter mee, niet via
het auditrecord, zodat het gedrag niet afhangt van of er gelogd wordt. De meting beoordeelde alleen de
tekst, dus deze reparatie verandert niets aan wat gemeten is.

### 2.49 Wat de historische gesprekken wel en niet kunnen, en LibreChat (24 sep)
**Hoeveel historie er is.** Echte widgetgesprekken bestaan pas sinds half september en zijn met enkele
tientallen; de interne LibreChat van dezelfde klant heeft ruim vier maanden historie en ongeveer tien
keer zoveel vragen. Tot nu toe mat deze keten alleen op de widget.

**Wat daaruit volgt.** Om een wijziging te beoordelen is geen nieuw verkeer nodig: een historische vraag
gaat opnieuw door de nieuwe code, met het zoeken zoals het nú scoort, en wordt blind vergeleken. Dat
deden 2.46 tot en met 2.48 al, maar op kleine sets (17 en 27 beurten) en met bronscores uit het oude
beslisrecord in plaats van opnieuw berekend. Dat laatste maakte de strook tussen 0,4 en 0,5 schijnbaar
te klein om te meten, terwijl alle widgetvragen en de LibreChat-vragen opnieuw gezocht kunnen worden. Wachten op echt verkeer is alleen nodig voor wat pas live zichtbaar wordt: hoe vaak een
stap vuurt en wat hij aan wachttijd kost.

**LibreChat telt anders mee.** De wijzigingen van 2.41 tot en met 2.48 zitten in de widgetroute; LibreChat
loopt via de LiteLLM-hook met het interne profiel en haalt er dus niets van. Zijn vragen zijn wel een
tien keer grotere bron van echte Voys-vragen over dezelfde kennisbank, gesteld door medewerkers in plaats
van klanten, en die kunnen als tweede meetset door de widgetroute. Of dezelfde verbeteringen ook in de
interne keten horen, is een aparte vraag met een aparte meting.

### 2.50 De drempel voor zwakke bronnen, gemeten op de brede set (24 sep)
**Opzet, met bestaand gereedschap.** `scripts/calibrate_confidence_bands.py` (#1634) stuurt echte vragen
als previewbeurt door de widgetroute en leest daarna het beslisrecord van elke beurt terug. Uitgebreid
met twee dingen: de eerste vragen uit de interne LibreChat van dezelfde tenant als extra bron (de
database volgt uit de organisatie van de widget, dus het script kan geen andere tenant lezen), en een
tabel per strook van de beste bronscore met het oordeel dat de antwoordbeoordelaar per beurt al
vastlegt: beantwoordt dit de vraag. Dat oordeel is hier de relevantiemaat, omdat "gedragen" alleen
zegt dat iets in het artikel staat en niet dat het artikel over de vraag gaat. De verwachte bronnen in
de gecureerde vraagsets bleken ongeschikt: meestal leeg, en waar ingevuld verwijzen ze naar interne
kennis die de widget niet doorzoekt.

**Uitkomst**, 433 beurten (gecureerde vragen, echte widgetvragen, eerste LibreChat-vragen, vaste
negatieve vragen; 6 vielen weg tijdens een herstart van portal-api door een deploy van een andere
sessie). Een eerste run is weggegooid: de review vond dat het script bij een gesprek dat vóór de
periode begon, of met een te lange openingsvraag, een latere vervolgvraag als eerste vraag nam en die
zonder context verstuurde. Nu wordt per gesprek eerst de echte openingsvraag bepaald. "Deels" staat
voortaan als eigen kolom, en beide oordelen lezen het concept, vóór de reparatiestap.

| Strook beste bron | Beurten | Beantwoordt de vraag | Deels | Niet | Zonder ongedragen bewering |
|---|---|---|---|---|---|
| onder 0,3 | 155 | 15 van 150 | 5 | 130 | 117 van 147 |
| 0,3 tot 0,4 | 13 | 0 van 13 | 1 | 12 | 10 van 13 |
| 0,4 tot 0,5 | 13 | 9 van 13 | 1 | 3 | 6 van 13 |
| 0,5 tot 0,6 | 13 | 8 van 13 | 2 | 3 | 5 van 9 |
| 0,6 en hoger | 133 | 89 van 133 | 27 | 17 | 54 van 107 |

**Besluit.** De grens blijft 0,4. Tussen 0,4 en 0,5 beantwoordt het merendeel van de antwoorden de vraag
wel (9 van 13); de grens naar 0,5 verschuiven zou die in "niet gevonden" veranderen. Onder 0,4 werkt de
regel van 2.48 zoals bedoeld: daar beantwoordt nog maar een op de tien antwoorden de vraag, en de rest
is overwegend het eerlijke "niet gevonden" zonder ongedragen bewering.

**Wat de tabel verder laat zien.** Het grootste resterende probleem zit niet bij zwakke bronnen. Boven
0,5 bevat ongeveer de helft van de concepten een bewering die niet in de artikelen staat (5 van 9 en
54 van 107 zonder). Dat meet het concept vóór de reparatiestap van 2.22, dus wat de bezoeker ziet is
beter dan dit getal, maar het wijst aan waar de volgende winst ligt: niet in welke bron er komt, maar in
wat het antwoordmodel er zelf bij verzint.

### 2.51 Punt 9 en 10 van §5: wat de bezoeker na de reparatie ziet (24 sep)
Gemeten op de opgeslagen eindtekst van de kalibratierun van 2.50 (wat de bezoeker kreeg) plus het
beslisrecord. Bij een beste bron van 0,5 of hoger bevatte 53 van de 129 beantwoorde concepten een
bewering die niet in de artikelen staat: 25 met precies één, 28 met twee of meer. Die laatste worden
gerepareerd; drie keer bleef er niets over en werd het "niet gevonden". De 24 antwoorden met één
markering gaan ongerepareerd de deur uit, bewust: in 2.5 was één markering in 77% van de gevallen
terecht en schrapte repareren daarop ook correcte zinnen.

**Punt 10, een vrijwel leeg antwoord na reparatie, is in deze data geen probleem:** van alle
gerepareerde antwoorden bleven er twee korter dan 120 tekens, en beide zijn bruikbare antwoorden.

**Voor punt 9 ontbrak de afgekeurde zin zelf.** Het beslisrecord bewaarde alleen het aantal, en de
oude meetsets hebben ofwel ingekorte artikelen (700 tekens, wat de controle misleidt) ofwel maar 25
antwoorden. De controle slaat herhalingen van de situatie van de bezoeker inmiddels al over, dus de
77% van 2.5 kan achterhaald zijn. Het beslisrecord bewaart nu ook de afgekeurde zinnen
(`unsupported_statements`, hooguit vijf, elk hooguit 300 tekens), zodat een volgende kalibratierun
kan laten zien hoe vaak één markering terecht is, en of repareren vanaf één markering nu wel veilig is.

### 2.52 Eén afkeuring van de controle: nu in ongeveer de helft terecht, drempel blijft (24 sep)
Een nieuwe kalibratierun (400 beurten; 35 vielen weg tijdens vier deploys van andere sessies) met de
afgekeurde zinnen in het beslisrecord (2.51). Achttien antwoorden met een beste bron van 0,4 of hoger
kregen precies één afkeuring. Elke afkeuring is met de hand getoetst tegen de tekst van de publieke
artikelpagina's waarop het antwoord steunde: 8 terecht, 8 onterecht, 2 niet te beoordelen (een pagina
achter een login, een pagina die niet meer bestaat). In 2.5 was dat 77% terecht; de controle is sindsdien
anders gaan lezen, en één afkeuring is nu te onbetrouwbaar om op te repareren.

**Besluit.** De reparatiedrempel blijft twee afkeuringen of één tegenspraak. Repareren vanaf één
afkeuring zou even vaak een juiste zin schrappen als een verzonnen zin.

**Het patroon in de onterechte afkeuringen** is een zin die alleen een lijst aankondigt (vier keer), een
melding dat iets niet gevonden is en een aanbod (twee keer); de controle hoort die volgens haar eigen
opdracht over te slaan. Over de hele run eindigden 8 van de 153 afgekeurde zinnen op een dubbele punt,
en maar twee antwoorden werden daardoor onnodig gerepareerd. Onder de ruis: niet gebouwd.

**Wat overblijft.** Bij 4 van de 18 was de ene afgekeurde zin een verzonnen feit in het kernantwoord,
over beleid of over hoe het product zich gedraagt. Daar laat schrappen een gat, dus de oplossing ligt
niet bij de drempel maar bij het ontbreken van dat feit in de kennisbank of bij een eerlijker "dit weet
ik niet zeker"; dat is een apart punt.

### 2.53 De vraagstap vuurt vrijwel nooit, en twee lossere versies winnen niet (24 sep)
Punt 12 van §5 begon met tellen. Over de `answer_plan_decision`-regels van echt widgetverkeer koos de
stap in ruim een derde van de beurten een vraag, maar kwam die vraag in minder dan één op de honderd
beurten echt bij het antwoordmodel: ruim vier van de vijf plannen vielen op de regel dat de vraag een
optie letterlijk moet noemen, de rest op opties die niet in de artikelen staan. In de meetopzet van §6
hetzelfde beeld: de live code liet in 3 van de 66 beurten een vraag stellen.

**Dat zet 2.47 in een ander licht.** De versie die daar won (17 om 14, dan 24 om 9) stelde in die meting
in 1 van de 68 beurten een geplande vraag, en de lossere poging van 24 september in 1 van de 66. Die
uitslagen gingen dus niet over de vraagstap; wat daar verschilde moet uit de rest van de keten komen.
Alleen de twee versies van vóór de reviewcontroles vuurden echt (43 en 29 van de 68), en de tweede
daarvan won beide rondes.

**Twee versies gemeten**, elk tegen de live code, 17 beoordeelde gesprekken, twee rondes, blind in
beide volgordes:

- *De stap schrijft geen vraag meer, het antwoordmodel vraagt welke optie geldt.* Vuurt in 25 van de 68
  beurten. Verliest beide rondes (15 om 18, 15 om 19), bruikbaar 40 tegen 54 van de 68. De opties zijn
  labels van hooguit vier woorden en het antwoordmodel zet ze als menu neer ("modules aanpassen,
  doorschakelen toevoegen"), eindigt een keer in het Engels ("Welke van deze drie applies voor jou?")
  en biedt opties die niet passen, zoals iOS aan iemand met de Webphone.
- *De stap houdt zijn eigen vraag, alleen de vorm wordt getoetst* (één regel, vraagteken, geen link of
  code). Vuurt in 11 van de 36 beurten van de eerste ronde. 14 om 16, dan 17 om 16: de richting slaat
  om, dus ruis. Bruikbaar 46 tegen 52. Hij wint waar een vraag paste (vier gesprekken in alle
  of bijna alle oordelen), maar stelt ook een vraag bij 9 van de 16 beurten waar een direct antwoord
  hoorde, tegen 5 bij de live code.

**Besluit.** Geen van beide uitgerold; de live stap blijft zoals hij is. Een vraag die de diagnose
bruikbaar maakt, moet de gesprekken winnen waar doorvragen hoort zonder de directe gevallen te
verliezen, en dat doet een lossere controle alleen niet.

**Wat de stap kost.** Hij draait op elke supportbeurt vóór het schrijven van het antwoord: mediaan 0,76 s,
p90 1,2 s extra wachttijd, voor een effect in minder dan één op de honderd beurten. Sinds #1663 (ook
24 september) is dezelfde stap de enige route voor doorvragen op alle drie de oppervlakken, ook in de
interne chat. Beide metingen hier draaiden op de code van vóór die wijziging.

---

## 3. Wat er live ging, en waarom

| Datum | Wijziging | Aanleiding |
|---|---|---|
| 17 sep | Vraag-judge en antwoord-judge, één beslisfunctie (#1480) | spec v0.2.0 |
| 17 sep | Vage klacht escaleert niet meer; half antwoord met verzonnen details geweigerd (#1481) | meting 2.3 |
| 17 sep | Controles mogen een antwoord met bron nooit weghalen (#1482) | meting 2.4: 7 van 18 antwoorden werden onterecht "niet gevonden" |
| 17 sep | Doorvraag-opdracht verwijderd (#1486) | meting 2.4 |
| 18 sep | Herschrijven met geciteerde geschiedenis, letterlijke zoekregel altijd mee (#1487) | meting 2.6 |
| 18 sep | Praatje-uitzondering uit de bronnencontrole (#1488) | meting 2.6 |
| 18 sep | Controle per zin met reparatie in plaats van weigeren (#1495) | meting 2.5: 49% naar 11% met iets verzonnen |
| 18 sep | Tijdsbudget per stap op de controle en de reparatie (#1496, #1503) | meting 2.9 |
| 18 sep | Dagrapport over wat de artikelen niet dragen (#1502, #1506) | om op echt verkeer te kunnen sturen |
| 18 sep | Dezelfde controle meekijkend op de interne chat (#1498) | meting 2.11 |
| 18 sep | Onderwerpen die de widget niet beantwoordt, per widget instelbaar (#1509) | meting 2.12: basisprompt haalde 8 van 15 |
| 18 sep | Die twee velden in het beheerscherm (#1510) | de instelling was anders alleen via de database te zetten |
| 18 sep | Controle van de grondslag op één plek voor beide paden (#1516) | 2.14 |
| 18 sep | Gespreksbeurten weigeren niet meer; het chatcontract gaat als bewijs mee (#1517) | 2.17 |
| 18 sep | Gesimuleerde bezoeker voor hele gesprekken (#1519) | 2.19 |
| 18 sep | Doodlopend antwoord zonder bron krijgt de afspraakknop (#1520) | 2.21 |
| 18 sep | Het interne pad repareert, niet-streamend (#1526) | 2.22 |
| 18 sep | De reparatie ook op de vastgehouden Strict-stroom, waar elke interne beurt langskomt (#1530) | 2.23 |
| 19 sep | Twee herformuleringen van de eerste vraag als eigen zoekpasses, na herrangschikken samengevoegd (retrieval-api `query_variants`, widget `query_paraphrase.py`, #1548) | 2.27 op zoekniveau, 2.33 eind-tot-eind: 63 om 43; 2.34 bevestigde alleen de rechtstreekse API-route, de browserwidget kreeg ze pas na 2.41 |
| 19 sep | Het harnas: de bezoeker geeft op na twee doorverwijzingen; nieuwe nulmeting | 2.35 |
| 19 sep | Een afgekapte controle loopt door en logt wat ze gevonden had; overzicht van de meetpunten onder Platform → Status (#1554) | 2.39, 2.40 |

---

## 4. Onderzoek buiten de eigen code

Herschrijven bij meerdere beurten, uit gepubliceerde praktijk:
- Herschrijf alleen de laatste beurt tot één zelfstandige zoekvraag; gebruik eerdere beurten alleen om verwijzingen op te lossen, en voeg geen feiten toe.
- Herken een onderwerpwissel en gooi de meegesleepte context weg; oude context die het zoeken vervuilt is een van de bekendste faalvormen.
- Gebruik hooguit drie varianten van dezelfde vraag en voeg de resultaten samen; dat is veiliger dan een verzonnen voorbeelddocument.
- Val bij een fout of time-out terug op de letterlijke vraag.

Bronnen: [Alhena over herschrijven bij meerdere beurten](https://alhena.ai/blog/query-rewriting-before-retrieval-multi-turn-rag/), [Leveraging historical information to boost RAG in conversations](https://www.sciencedirect.com/science/article/pii/S0306457325003905), [Learning When to Retrieve, What to Rewrite, and How to Respond](https://arxiv.org/pdf/2409.15515), [NVIDIA over meerdere beurten](https://docs.nvidia.com/rag/2.4.0/multiturn.html).

Context rond de vraag als zoeksignaal (18 sep, voor 2.25 t/m 2.28):
- Herformuleren en samenvoegen helpt vooral bij woordverschil tussen vraag en corpus en kan na herrangschikken wegvallen; in één productiemeting daalde Hit@10 van 0,51 naar 0,48 ([RAG-Fusion in productie](https://arxiv.org/abs/2603.02153)). Hier gemeten mét herrangschikken per leg: 35% naar 59% (2.27).
- Pseudo-documenten (HyDE, query2doc) winnen deels door lekkage van wat het model al weet ([Hypothetical Documents or Knowledge Leakage?](https://arxiv.org/html/2504.14175v1)); daarom hier alleen herformuleringen zonder toegevoegde details.
- Over de huidige pagina als zoeksignaal bestaat geen gemeten literatuur, alleen leveranciersclaims zonder cijfers (Zendesk Contextual Help e.a.). Hier gemeten: onschadelijk, drie keer plek 1, geen recall (2.26).
- Geschiedenis als zoeksignaal: in TREC CAsT 2019 was uitbreiding met termen uit eerdere beurten het beste automatische systeem, en herschrijven tot één zelfstandige vraag won daar nog ~18% NDCG@3 op ([overzicht CAsT](https://trec.nist.gov/pubs/trec30/papers/Overview-CAsT.pdf)); onderwerpwissels maken het corpusafhankelijk ([TopiOCQA](https://aclanthology.org/2022.tacl-1.27/)). Hier is het geen óf-óf: herschrijving plus een leg met het vorige antwoord, 39% naar 64% (2.28).
- Orakel tegen haalbaar: de kloof tussen handmatige en automatische herschrijving loopt van 3% tot 30% NDCG@3 afhankelijk van de dataset ([Vakulenko e.a.](https://ar5iv.labs.arxiv.org/html/2101.07382)), en het samenvoegen van meerdere automatische herschrijvingen dichtte die kloof het meest ([CMU bij CAsT](https://trec.nist.gov/pubs/trec30/papers/CMU-LTI-CAsT.pdf)). Dat is de reden om in 2.27 te fuseren in plaats van één "beste" herformulering te kiezen.

**Belangrijker dan de literatuur:** het interne chatpad (LibreChat via de LiteLLM-hook, `deploy/litellm/klai_kb_query_rewrite.py`) doet dit grotendeels al. Het geeft de geschiedenis als geciteerde tekst, heeft een uitgewerkt voorbeeld van een onderwerpwissel, schrijft de zoekvraag in trefwoorden, verbreedt merknamen, en stuurt de letterlijke vraag mee naar de zoekdienst. De widget liep daarop achter. Het interne pad heeft wél nog de lichte controle op beweringen, die extern maar 22% ving.

---

## 5. Wat nog open staat

Elk punt draagt zijn stand: **gemeten en afgevallen** (eind-tot-eind, met de sectie), **onder de ruis**
(het verschil is kleiner dan §6 toelaat of het geval komt te weinig voor), of **buiten de code** (inhoud
of een keuze van de eigenaar). Aan de zoekkant en in de keten staat niets meer open dat meetbaar
beter kan zonder eerst op echt verkeer te kijken.

**Buiten de code**
1. **De kennisbank aanvullen** met de zes onderwerpen uit 2.36 en de dode artikelketen voor
   internationale gesprekken (2.16). Eén op de negen kennisvragen (6 van 54) kan geen enkele schakel
   beantwoorden omdat het antwoord er niet is; bij nog eens 7 staat het er wel maar bereikt de eerste
   vraag het niet, en daar is het vragen dat het systeem niet mag doen (2.4, 2.25). Dit is de grootste
   hefboom die over is.
2. **De reikwijdte van de widget.** Hij zoekt in één van de negen kennisbanken; `priceright-prijzen-voys`
   (4527 chunks) en `ascend` (6710) bevatten antwoorden op vragen die bezoekers stellen, en de
   instelling uit 2.10f vangt precies die vragen af. Een keuze van de eigenaar.
3. **Het valse alarm in de niet-behandelde onderwerpen** (2.18): één op de vijftig hulpvragen krijgt
   de doorverwijstekst; drie oplossingen gemeten en alle drie duurder dan de kwaal. Een keuze van de
   eigenaar.
   De vaste tekst zelf noemt sinds 2.43 het onderwerp van de bezoeker in plaats van de hele lijst,
   ook bij een verzoek om een mens; dat deel is opgelost.
4. **Het budget van de controle per zin** (2.37): 4 s laat op eerste beurten 9 van 54 controles
   afkappen; 8 s laat ze afronden zonder meetbaar beter antwoord en met een zwaardere staart. 6 s is de
   ongemeten middenweg (5 van de 9 gered, één tot twee seconden op die beurten). Sinds 2.39 legt
   `answer_grounding_late` op echt verkeer vast wat een afgekapte controle gevonden had; dat is het
   gegeven onder deze keuze tussen wachttijd en volledigheid van de controle.

**Onder de ruis**
5. **Het vorige antwoord als zoekleg bij vervolgbeurten** (2.28, 2.32): 65 om 61 over twee rondes met
   tegengestelde rondes, meer verzinsels. Niet live.
6. **"Geen nieuwe gok zonder nieuw artikel"** (2.38): raakt 1 tot 2 van 70 vervolgbeurten, die al goed
   gingen.
7. **Het harnas als effectmeter**: twaalf gesprekken zien een effect van de grootte van 2.21 of 2.34
   niet (§6). Het harnas is de nulmeting en de bevestiging dat nieuwe code draait; het bewijs voor een
   ketenwijziging komt uit de herspeling van 54 vragen in twee rondes (2.33).

**Wacht op echt verkeer, niet op code**
8. **De herformuleringen op echte bezoekers.** Het dagrapport van de controle per zin
   (`scripts/grounding_report.py`) en de beslisrecords (`query_variants_run`,
   `query_variants_added`) laten over een week na de reparatie van 2.41 zien of 2.33
   op echt verkeer hetzelfde doet als in de herspeling: meer antwoorden die de vraag oplossen, niet
   meer niet-gedragen beweringen, en hoe vaak de controle afkapt. `answer_grounding_late` (2.39) zegt
   daarbij wat die afgekapte controles gevonden hadden. Waar elk van deze staat: Platform → Status →
   Meetpunten (2.40).

**Volgende stappen, in deze volgorde (stand 24 sep, na 2.50)**
9. **Wat het antwoordmodel er zelf bij verzint, bij sterke bronnen.** Gemeten (2.51, 2.52): de
   reparatiedrempel blijft, één afkeuring is nu in ongeveer de helft terecht. Open blijft een klein
   restant: een verzonnen feit in het kernantwoord met maar één afkeuring (4 van 18), waar schrappen een
   gat laat.
10. **Bruikbaarheid na reparatie.** Afgerond (2.51): in de data blijft geen leeg antwoord achter.
11. **Nameting op echt verkeer** van de herformuleringen (2.41), de vraagstap (2.47) en de regel voor
    zwakke bronnen (2.48): hoe vaak ze vuren en wat ze aan wachttijd kosten. De logregels
    `answer_plan_decision`, `planned_question` en `weak_sources` staan erin.
12. **De vraagstap vuurt vrijwel nooit** (2.53): minder dan één op de honderd beurten krijgt de geplande
    vraag, tegen 0,76 s wachttijd per beurt. Twee lossere controles gemeten, geen van beide wint. Open
    is de keuze: de stap schrappen en de wachttijd terugwinnen, of een versie zoeken die alleen vraagt
    waar het gesprek er echt om vraagt. Sinds #1663 hangt ook de interne chat aan deze stap.
13. **Het gespreksoverzicht toont nog niet welke zin de controle afkeurde.** Het beslisrecord bewaart
    die zinnen sinds 2.51 (`unsupported_statements`); tonen vraagt een frontendwijziging met een
    browsercontrole.
14. **Kleine resten uit reviews:** de afspraakknop kan onder een geplande vraag staan als er bronnen
    zijn (2.47); de drift-test vergelijkt de widgetprompts niet tussen de twee kopieën (2.45); een
    gedachtestreepje aan het begin van een regel wordt een komma (2.45); de afspraakherkenning kent
    alleen Nederlands en Engels (2.48).

**Bij de kennisbank van de klant (buiten de code)**
15. Ontbrekende artikelen: uitbelpermissies en buitenlandbellen (standaard uit, niet altijd door een
    beheerder aan te zetten), variabele caller-ID, klant worden, en enkele financiële vragen. De
    grootste thema's in de lacunes: belplannen en doorschakelen, nummerregistratie en portering,
    geluidsfragmenten en voicemail.
16. Informatie voor Belgische klanten staat in dezelfde kennisbank en komt in Nederlandse antwoorden.
17. De opening "controleer eerst de storingspagina" komt uit de eigen probleemoplosser-artikelen en is
    alleen daar duurzaam weg te halen (2.45).

**Keuzes van de eigenaar**
18. De publieke-datacontrole (`audit-public-tenant-data.py`) is geen verplichte check, waardoor een
    falende controle een merge niet tegenhoudt (2.49).
19. Of de verbeteringen van 2.41 tot en met 2.48 ook in de interne keten (LibreChat) horen; dat vraagt
    een eigen meting.

**Gemeten en afgevallen, niet meer proberen:** doorvragen als opdracht aan het antwoordmodel (2.4,
2.10, 2.46; een aparte vraagstap wint wél, 2.47), keuzes uit gevonden artikelen zonder vraagstap
(2.7; in de vraagstap van 2.47 komen de opties juist uit de artikelen), een taxonomie-aspect als zoekprefix (2.20), een sterkere paginaboost
(2.26), de zoekvraag verrijken met wat de bezoeker niet gezegd heeft (2.25), een zoekleg vóór het
herrangschikken (2.32), het vorige antwoord als zoekpass (2.32), het eerder geciteerde artikel uit die
pass weglaten (2.32), inkorten van de artikelen voor de controle (2.10b), samenvoegen van de twee
controles achteraf (2.10c) en een ruimer controlebudget (2.37).

**Afgehandeld en live:** de zware controle met reparatie op beide paden (2.8, 2.9, 2.22, 2.23), de
instelling per widget (2.10f, 2.13), het chatcontract als bewijs (2.17), de afspraakknop onder een
doodlopend antwoord (2.21), de herformuleringen op de eerste beurt (2.33, 2.34), en het harnas met
gescheiden beoordelaar en een bezoeker die opgeeft (2.24, 2.35), de herformuleringen ook in de
browserwidget (2.41), de doorverwijzing die het onderwerp noemt (2.43), geen beloftes, vaste zinnen of
gedachtestreepjes (2.45), de vraagstap (2.47), geen antwoord uit alleen zwakke bronnen (2.48) en de
kalibratie per scorestrook met LibreChat-vragen (2.50).

---

## 6. Meetafspraken

- Elke wijziging aan deze keten wordt vóór livegang gemeten op dezelfde echte gesprekken, met het oude gedrag ernaast.
- Een antwoord dat het oorspronkelijke systeem met bron toonde, mag niet verdwijnen.
- Dezelfde vraag drie keer stellen, want het antwoordmodel varieert; een verschil onder ongeveer tien beurten is ruis.
- De beoordelaar krijgt beide antwoorden in willekeurige volgorde en weet niet welke versie wat schreef. Hij kiest iets vaker het eerst getoonde antwoord (76 tegen 58), dus bij twijfelgevallen wordt in beide volgordes beoordeeld.
- Meetopstellingen staan buiten de repo (`/tmp/probe` lokaal, `/tmp/exp` per onderzoekslijn, `/tmp/ctx` voor 2.25 t/m 2.29); de uitkomsten die ertoe doen staan in dit bestand.
- Een zoekmeting (welk artikel komt mee) is geen antwoordmeting: 2.27 en 2.28 wonnen beide op zoekniveau, alleen 2.27 won eind-tot-eind (2.32, 2.33). Wat op zoekniveau wint gaat vóór livegang alsnog door de eind-tot-eind poort hierboven.
- Eén ronde is geen meting. In 2.32 gaf ronde 1 oud 39 om 25 en ronde 2 nieuw 36 om 26 op dezelfde beurten; hetzelfde paar kreeg in 23 van de 70 gevallen twee keer hetzelfde oordeel. Twee rondes zijn het minimum, en een verschil dat in beide rondes dezelfde kant op wijst (2.33: 32 om 22 en 31 om 21) telt; een verschil dat omslaat is ruis, hoe groot het per ronde ook is.
- De widgetroute in-process herspelen (ASGI-transport, previewtoken, `record_widget_turn` en `write_retrieval_log` uitgeschakeld) is de manier om één schakel eind-tot-eind te meten vóór livegang: dezelfde code, dezelfde controles en reparatie, en de zoekstap is per variant te vervangen door twee aanroepen die samengevoegd worden. Eén beurt per 7,5 s houdt het onder het gedeelde antwoordquotum; het draait in een eigen container van het portal-image (`docker run` op hetzelfde netwerk), want een deploy maakt de productiecontainer opnieuw aan en neemt een lopende meting mee.
