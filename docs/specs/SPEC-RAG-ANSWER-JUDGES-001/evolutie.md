# Evolutielogboek — antwoordketen van de helpwidget

Doel van dit bestand: wat er gemeten is, wat daaruit volgde, en wat er live staat. Zo hoeft niemand een meting of een onderzoek over te doen. De spec ernaast (`spec.md`) beschrijft het ontwerp; dit bestand beschrijft de weg ernaartoe.

Bijgewerkt: 2026-09-18, na 2.24.

---

## 1. De keten, schakel voor schakel

Een beurt van een bezoeker loopt door deze schakels. Per schakel: wat het doet, wat gemeten is, en wat de stand is.

| # | Schakel | Waar | Stand |
|---|---|---|---|
| 1 | Vraag binnen, eerste of vervolg | `partner.py::chat_completions` | ongewijzigd |
| 2 | Vervolgvraag omzetten naar zoekvraag | retrieval-api `services/coreference.py` | verbeterd, live 2026-09-18 |
| 3 | Zoeken (vector, graaf, herrangschikken) | retrieval-api `api/retrieve.py` | letterlijke zoekregel toegevoegd, live 2026-09-18 |
| 4 | Controle vooraf op de vraag | `services/turn_judge.py` | beslist niets meer over doorvragen (v0.5.0), praatje-uitzondering weg (v0.6.0), beoordeelt sinds v0.9.0 ook of de vraag binnen de niet-behandelde onderwerpen valt |
| 5 | Antwoord schrijven | `partner_chat.py` + profiel in `klai-libs/chat-prompts` | ongewijzigd; promptvarianten gemeten en afgevallen. Valt de vraag binnen de onderwerpen die de widget niet behandelt, dan wordt deze schakel overgeslagen (v0.9.0) |
| 6 | Koppelen aan bronnen | `klai-libs/citations` | ongewijzigd, dit is de ondergrens |
| 7 | Controle achteraf op het antwoord | `services/answer_judge.py` + `services/answer_grounding.py`; intern `deploy/litellm/klai_answer_grounding.py` | licht oordeel plus controle per zin met reparatie, live 18 sep; dezelfde reparatie op het interne pad sinds #1526 en #1530, platform-breed (2.22, 2.23) |
| 8 | Kennisbank | Voys-artikelen | het echte plafond, niet aangepakt. De widget zoekt in één van de negen kennisbanken van Voys (`support`, 8947 chunks); prijzen, Ascend en de nerds-wiki staan buiten bereik |
| — | Meten van de keten | `scripts/simulate_conversations.py` | hele gesprekken sinds 18 sep; ijking eerlijk gerekend 75% tegen 81% van de herspeling (2.19, gecorrigeerd in 2.24); bezoeker en scoorder sinds 2.24 op een ander model dan de controle die ze meten |

---

## 2. Metingen, op volgorde van uitvoering

Alle metingen op echte Voys-gesprekken, als proefgesprek gedraaid, zonder iets op te slaan bij de klant.

### 2.1 Nulmeting oude systeem (14 dagen vóór de controles)
98 antwoorden: 85 met bron, 4 zonder bron, 9 weigeringen. 38 keer een zwak zoekresultaat, waarvan er 32 tóch een antwoord met bron opleverden. Doorlooptijd over 275 antwoorden: mediaan 1,75 s, 90% binnen 3,76 s.

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
Uit de LibreChat-tenant van Voys: 1097 vragen van 20 gebruikers over vijf maanden, waarvan
751 antwoorden een bronverwijzing dragen. De 50 meest recente daarvan (9 tot en met 18 sep)
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

---

## 4. Onderzoek buiten de eigen code

Herschrijven bij meerdere beurten, uit gepubliceerde praktijk:
- Herschrijf alleen de laatste beurt tot één zelfstandige zoekvraag; gebruik eerdere beurten alleen om verwijzingen op te lossen, en voeg geen feiten toe.
- Herken een onderwerpwissel en gooi de meegesleepte context weg; oude context die het zoeken vervuilt is een van de bekendste faalvormen.
- Gebruik hooguit drie varianten van dezelfde vraag en voeg de resultaten samen; dat is veiliger dan een verzonnen voorbeelddocument.
- Val bij een fout of time-out terug op de letterlijke vraag.

Bronnen: [Alhena over herschrijven bij meerdere beurten](https://alhena.ai/blog/query-rewriting-before-retrieval-multi-turn-rag/), [Leveraging historical information to boost RAG in conversations](https://www.sciencedirect.com/science/article/pii/S0306457325003905), [Learning When to Retrieve, What to Rewrite, and How to Respond](https://arxiv.org/pdf/2409.15515), [NVIDIA over meerdere beurten](https://docs.nvidia.com/rag/2.4.0/multiturn.html).

**Belangrijker dan de literatuur:** het interne chatpad (LibreChat via de LiteLLM-hook, `deploy/litellm/klai_kb_query_rewrite.py`) doet dit grotendeels al. Het geeft de geschiedenis als geciteerde tekst, heeft een uitgewerkt voorbeeld van een onderwerpwissel, schrijft de zoekvraag in trefwoorden, verbreedt merknamen, en stuurt de letterlijke vraag mee naar de zoekdienst. De widget liep daarop achter. Het interne pad heeft wél nog de lichte controle op beweringen, die extern maar 22% ving.

---

## 5. Wat nog open staat

Op volgorde van wat de metingen als grootste rem aanwijzen.

1. **De kennisbank aanvullen.** Van de negen beurten zonder antwoord (2.16) stond het bij zes niet
   in de artikelen, en bij twee liep de artikelketen halverwege dood ("hoe schakel ik het account
   in voor internationale gesprekken"). Geen enkele schakel in deze keten neemt dat weg. Dit is
   inhoudswerk, en het is de grootste overgebleven hefboom.
2. **De reikwijdte van de widget heroverwegen.** Hij mag in één van de negen kennisbanken zoeken.
   `priceright-prijzen-voys` (4527 chunks) en `ascend` (6710) bevatten antwoorden op vragen die
   bezoekers stellen. Voor een publieke helppagina is dat waarschijnlijk bewust, maar het is een
   keuze die sinds de inrichting niet is herzien, en de instelling uit 2.10f vangt nu precies de
   vragen af waarvan het antwoord in de kennisbank ernaast staat.
3. **Meten op echt verkeer.** Alles hierboven is gemeten met herspelingen en simulaties. Het
   dagrapport (`scripts/grounding_report.py`) had op 18 sep twee antwoorden. Wat er vandaag live
   ging is dus nog nergens op echte bezoekers bevestigd.
4. **Het harnas groter draaien.** Twaalf gesprekken kunnen een effect van de grootte van 2.21 niet
   aantonen, en meer gesprekken kosten snelheidslimiet die met bezoekers gedeeld wordt. Meerdere
   rondes buiten kantooruren.
5. **Het valse alarm in de niet-behandelde onderwerpen.** Eén op de vijftig hulpvragen krijgt de
   doorverwijstekst; drie oplossingen gemeten en alle drie duurder dan de kwaal (2.18). Dit is een
   afweging voor de eigenaar van de widget.
6. **Varianten voor het herschrijven meten** (onderwerpwissel, trefwoord-stijl, geschiedenis zonder
   de antwoorden van de assistent).
7. **Geen nieuwe gok zonder nieuw artikel:** bij een vervolgbeurt zonder nieuw gevonden artikel een
   medewerker aanbieden (23 van de 38 correctiebeurten).

Afgehandeld: de zware controle op verzonnen details (2.8, 2.9), dezelfde controle én reparatie op
het interne pad (2.11, 2.14, 2.22, 2.23), de instelling per widget (2.10f, 2.13), de korte vraag met vervolgvraag (2.10),
de stijlregels uit de basisprompt (2.5, 2.13) en aspectgericht doorvragen (2.20).

---

## 6. Meetafspraken

- Elke wijziging aan deze keten wordt vóór livegang gemeten op dezelfde echte gesprekken, met het oude gedrag ernaast.
- Een antwoord dat het oorspronkelijke systeem met bron toonde, mag niet verdwijnen.
- Dezelfde vraag drie keer stellen, want het antwoordmodel varieert; een verschil onder ongeveer tien beurten is ruis.
- De beoordelaar krijgt beide antwoorden in willekeurige volgorde en weet niet welke versie wat schreef. Hij kiest iets vaker het eerst getoonde antwoord (76 tegen 58), dus bij twijfelgevallen wordt in beide volgordes beoordeeld.
- Meetopstellingen staan buiten de repo (`/tmp/probe` lokaal, `/tmp/exp` per onderzoekslijn); de uitkomsten die ertoe doen staan in dit bestand.
