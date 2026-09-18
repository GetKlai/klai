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
| 7 | Controle achteraf op het antwoord | `services/answer_judge.py` | licht; zware variant gemeten, nog niet gebouwd |
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

### 2.8 Financiële en commerciële vragen via de basisprompt (17 sep)
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
2. **Zware controle op verzonnen details bouwen**, met repareren per zin in plaats van weigeren, plus een eerlijke regel en de afspraakknop. Kost ongeveer 3 s.
3. **Dezelfde controle naar het interne pad**, eerst alleen meekijkend, zodat beide paden vergelijkbaar worden.
4. **Geen nieuwe gok zonder nieuw artikel:** bij een vervolgbeurt zonder nieuw gevonden artikel een medewerker aanbieden (23 van de 38 correctiebeurten).
5. **Korte vraag: antwoorden plus één vervolgvraag**, herkend op zes woorden of minder.
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
