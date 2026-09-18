# Evolutielogboek — antwoordketen van de helpwidget

Doel van dit bestand: wat er gemeten is, wat daaruit volgde, en wat er live staat. Zo hoeft niemand een meting of een onderzoek over te doen. De spec ernaast (`spec.md`) beschrijft het ontwerp; dit bestand beschrijft de weg ernaartoe.

Bijgewerkt: 2026-09-18 (na de livegang bij Voys).

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
| 7 | Controle achteraf op het antwoord | `services/answer_judge.py` + `services/answer_grounding.py` | licht oordeel plus controle per zin met reparatie, live 18 sep |
| 8 | Kennisbank | Voys-artikelen | het echte plafond, niet aangepakt |

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
Engels mochten spreken, één wilde een medewerker. Zie 2.17.

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
vijf werkdagen" levert precies één markering op en hoort geweigerd te worden). In plaats daarvan
de controle geleerd wat níét over de organisatie gaat: welke talen hij spreekt, dat er een
medewerker of afspraak via deze chat bereikbaar is, en wat een knop in dit venster doet.

| | Oude prompt | Nieuwe prompt |
|---|---|---|
| Gespreksbeurten zonder markering (6 echte) | 2 van 6 | **6 van 6** |
| Verzonnen prijs/stap nog gemarkeerd (4 opzettelijke) | 3 van 4 | 3 van 4 |
| 30 echte antwoorden: minstens 1 onbewezen | 73% | 73% |
| 30 echte antwoorden: boven de reparatiedrempel | 60% | 60% |
| 30 echte antwoorden: andere reparatiebeslissing | – | **0 van 30** |

Nul verschil op echte antwoorden, dus dit kost niets aan detectie.

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

1. **Varianten voor het herschrijven meten** (onderwerpwissel, trefwoord-stijl, geschiedenis zonder de antwoorden van de assistent) en de beste live zetten.
2. ~~Zware controle op verzonnen details bouwen~~ — gebouwd, gemeten en live op 18 sep, zie 2.8 en 2.9.
3. **Reparatie op het interne pad aanzetten.** De controle draait daar sinds 18 sep meekijkend (2.11). De cijfers van beide paden liggen nu naast elkaar (2.14): intern 86% tegen extern 64%, en 70% tegen 40% boven de reparatiedrempel. Openstaand is niet meer óf het moet, maar met welk tijdsbudget: de mediane controle duurt daar 3,6 s.
4. **Geen nieuwe gok zonder nieuw artikel:** bij een vervolgbeurt zonder nieuw gevonden artikel een medewerker aanbieden (23 van de 38 correctiebeurten).
5. ~~Korte vraag: antwoorden plus één vervolgvraag~~ — gemeten op 18 sep en afgevallen, zie 2.10.
6. ~~Stijlregels uit de Voys-basisprompt halen~~ — **teruggenomen als advies.** Meting 2.5 liet zien dat álle stijlregels weghalen het juist slechter maakte (62% tegen 49%). Wat wél gebeurd is op 18 sep: de herhaalde weiger-alinea en de vijf letterlijke voorbeeldzinnen eruit, zie 2.13. Niet apart gemeten.
7. ~~Instelling per widget voor onderwerpen die de assistent niet behandelt~~ — live op 18 sep (#1509, #1510), ingevuld en live geverifieerd bij Voys, zie 2.10f en 2.13.
8. **Kennisbank aanvullen**; dat is het plafond dat geen enkele schakel wegneemt.

---

## 6. Meetafspraken

- Elke wijziging aan deze keten wordt vóór livegang gemeten op dezelfde echte gesprekken, met het oude gedrag ernaast.
- Een antwoord dat het oorspronkelijke systeem met bron toonde, mag niet verdwijnen.
- Dezelfde vraag drie keer stellen, want het antwoordmodel varieert; een verschil onder ongeveer tien beurten is ruis.
- De beoordelaar krijgt beide antwoorden in willekeurige volgorde en weet niet welke versie wat schreef. Hij kiest iets vaker het eerst getoonde antwoord (76 tegen 58), dus bij twijfelgevallen wordt in beide volgordes beoordeeld.
- Meetopstellingen staan buiten de repo (`/tmp/probe` lokaal, `/tmp/exp` per onderzoekslijn); de uitkomsten die ertoe doen staan in dit bestand.
