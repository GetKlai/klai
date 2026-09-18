# Evolutielogboek — antwoordketen van de helpwidget

Doel van dit bestand: wat er gemeten is, wat daaruit volgde, en wat er live staat. Zo hoeft niemand een meting of een onderzoek over te doen. De spec ernaast (`spec.md`) beschrijft het ontwerp; dit bestand beschrijft de weg ernaartoe.

Bijgewerkt: 2026-09-18.

---

## 1. De keten, schakel voor schakel

Een beurt van een bezoeker loopt door deze schakels. Per schakel: wat het doet, wat gemeten is, en wat de stand is.

| # | Schakel | Waar | Stand |
|---|---|---|---|
| 1 | Vraag binnen, eerste of vervolg | `partner.py::chat_completions` | ongewijzigd |
| 2 | Vervolgvraag omzetten naar zoekvraag | retrieval-api `services/coreference.py` | verbeterd, live 2026-09-18 |
| 3 | Zoeken (vector, graaf, herrangschikken) | retrieval-api `api/retrieve.py` | letterlijke zoekregel toegevoegd, live 2026-09-18 |
| 4 | Controle vooraf op de vraag | `services/turn_judge.py` | beslist niets meer over doorvragen (v0.5.0), praatje-uitzondering weg (v0.6.0) |
| 5 | Antwoord schrijven | `partner_chat.py` + profiel in `klai-libs/chat-prompts` | ongewijzigd; promptvarianten gemeten en afgevallen |
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

### 2.11 Dezelfde controle op de interne chat, alleen meekijkend (18 sep)
De interne chat (LibreChat via de LiteLLM-hook) doet nu dezelfde controle per zin als de widget, met letterlijk dezelfde tekst uit de gedeelde bibliotheek. Daar verandert hij niets aan het antwoord en wacht de gebruiker nergens op: de controle wordt naast het antwoord gestart en logt alleen wat er niet in de artikelen staat. Zo kunnen beide paden straks naast elkaar gelegd worden.

Wat de review ving, en wat het waard was: de controle keek naar een veld dat alleen in mijn tests bestond (`kb_chat_mode`), terwijl productie `chat_retrieval_prompt_mode` schrijft. Hij zou dus nooit gedraaid hebben, met groene tests. Nu gebruikt hij dezelfde strikt-check als de renderer zelf, en testen de tests op de productiewaarde.

Eerste live regels, 18 sep: twee interne beurten gemeten, beide met nul uitspraken over de organisatie (een weigering of een kort antwoord zonder bron). Te weinig om iets te zeggen; de cijfers komen als er verkeer is.

### 2.12 Financiële en commerciële vragen via de basisprompt (17 sep)
Onderaan de basisprompt: 3 van de 10 goed. Bovenaan, strenger geformuleerd: 8 van de 15. Gewone factuurvragen bleven goed (6 van 6). Eén keer noemde het model tóch een prijs.

**Les:** de basisprompt alleen is hiervoor te zwak; een instelling per widget met een vaste tekst is de betrouwbare route.

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
3. ~~Dezelfde controle naar het interne pad~~ — live op 18 sep, meekijkend, zie 2.11. Openstaand: de cijfers van beide paden naast elkaar leggen zodra er intern verkeer is gemeten.
4. **Geen nieuwe gok zonder nieuw artikel:** bij een vervolgbeurt zonder nieuw gevonden artikel een medewerker aanbieden (23 van de 38 correctiebeurten).
5. ~~Korte vraag: antwoorden plus één vervolgvraag~~ — gemeten op 18 sep en afgevallen, zie 2.10.
6. **Stijlregels uit de Voys-basisprompt halen** ("Je hebt nu…", overgenomen voorbeeldzinnen).
7. **Instelling per widget** voor onderwerpen die de assistent niet behandelt, zoals financiële en commerciële vragen.
8. **Kennisbank aanvullen**; dat is het plafond dat geen enkele schakel wegneemt.

---

## 6. Meetafspraken

- Elke wijziging aan deze keten wordt vóór livegang gemeten op dezelfde echte gesprekken, met het oude gedrag ernaast.
- Een antwoord dat het oorspronkelijke systeem met bron toonde, mag niet verdwijnen.
- Dezelfde vraag drie keer stellen, want het antwoordmodel varieert; een verschil onder ongeveer tien beurten is ruis.
- De beoordelaar krijgt beide antwoorden in willekeurige volgorde en weet niet welke versie wat schreef. Hij kiest iets vaker het eerst getoonde antwoord (76 tegen 58), dus bij twijfelgevallen wordt in beide volgordes beoordeeld.
- Meetopstellingen staan buiten de repo (`/tmp/probe` lokaal, `/tmp/exp` per onderzoekslijn); de uitkomsten die ertoe doen staan in dit bestand.
