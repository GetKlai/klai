---
id: SPEC-RAG-ANSWER-JUDGES-001
version: "0.11.0"
status: REQ-1 t/m REQ-4 (pad B, helpdeskwidget) live; REQ-5 (pad A) live sinds 18 sep, reparatie aan voor elke tenant
created: 2026-09-17
author: Claude (Opus 5), in opdracht van Mark Vletter
priority: high
tenant_scope: platform-breed, geen poort per tenant; pad B (widget in support mode via portal-api) en pad A (LibreChat via de LiteLLM-hook, Strict)
related:
  - SPEC-RAG-CLARIFY-FLOW-001 (vervangt op pad B de trigger van beslissing 1 en de claimscheck van beslissing 2)
  - SPEC-RAG-ANSWER-TIERS-001 (turn_scope gaat op in de vraag-judge)
  - SPEC-CHAT-QUALITY-LOOP-001 (de judge achteraf blijft de onafhankelijke meetlat)
  - SPEC-KNOWLEDGE-ACTIVITY-001 (answer_signals krijgt de oordelen van beide judges)
---

# HISTORY

| Versie | Datum | Wijziging |
|---|---|---|
| 0.11.0 | 2026-09-18 | Regel 9: de eerste vraag gaat met twee herformuleringen naar het zoeken, elk als eigen pass, na herrangschikken samengevoegd (retrieval-api `query_variants`, widget `query_paraphrase.py`). Gemeten op 54 echte eerste vragen, twee rondes, blind mét de artikelen: 63 om 43, "lost op" 15 naar 27, verzonnen 30 naar 23, kosten ongeveer 2,3 s per eerste beurt (logboek 2.27, 2.33). Het vorige antwoord als zoekleg bij vervolgbeurten won op zoekniveau en bleef eind-tot-eind gelijk (65 om 61) en is niet live (2.32). |
| 0.10.0 | 2026-09-18 | Regel 8 en REQ-5 gelijkgetrokken met wat draait: de reparatie staat sinds #1526 (niet-streamend) en #1530 (de vastgehouden Strict-stroom) ook op het interne pad aan, platform-breed en zonder poort per omgeving. De voorwaarden zijn alleen technisch (`_repair_would_be_wrong`: Strict, citeerbare bronnen, hele antwoord nog in handen, niets dat op geplakte tekst rust). Meting: 80 antwoorden per pad, intern 85% / 72% boven de drempel tegen 75% / 65% op de widget (logboek 2.22); kosten intern mediaan 4,0 s, 8,3 s in de traagste tien procent (2.23). |
| 0.9.0 | 2026-09-18 | Onderwerpen die de widget niet behandelt, per widget instelbaar (#1509, #1510): de vraag-judge beoordeelt of de vraag erin valt en de bezoeker krijgt dan de vaste tekst met de afspraakknop, zonder generatie. Gemeten 14 van 14 afgevangen, 12 van 12 hulpvragen ongemoeid (logboek 2.10f, 2.13). |
| 0.8.0 | 2026-09-18 | Tijdsbudget per stap vastgelegd (controle 4 s, reparatie 3 s) na twee live metingen, en de controle per zin draait meekijkend op de interne chat met dezelfde gedeelde tekst. Inkorten van de artikelen voor de controle is gemeten en afgevallen. |
| 0.7.0 | 2026-09-18 | Controle per zin op het zwaardere model, met reparatie in plaats van weigeren. Gemeten op 25 echte antwoorden met de volledige artikelteksten, na de reviewfixes: 64% bevat minstens één uitspraak die niet in de artikelen staat, 10 haalden de reparatiedrempel (2 of meer, of tegenspraak), 8 werden gerepareerd (2 daarvan hielden nog een melding over) en 2 bleken volledig verzonnen (prijzen van € 25,- en € 5,- voor een 0800-nummer, en een terugboekprocedure) en werden de eerlijke weigering. Een blinde vergelijking voor en na reparatie: 5 keer het gerepareerde antwoord, 3 keer het origineel. Controle 1,9 s mediaan, 3,4 s in de traagste tien procent. |
| 0.6.0 | 2026-09-18 | Drie onderzoekslijnen op echte gesprekken afgerond (eerste vraag, vervolgbeurten, verzonnen details). Gespreksbeurten verliezen hun uitzondering in de citatiemotor: die kostte een verkeerd bestempelde vraag haar bronnen (11 keer afgegaan op 90 vervolgbeurten, minstens 3 fout, één echte vraag geweigerd). Tekst zonder bron loopt nu overal via dezelfde controle achteraf. |
| 0.5.0 | 2026-09-17 | Blinde vergelijking oud tegen nieuw op 50 echte Voys-eerste vragen × 3 rondes (klai-medium als beoordelaar, willekeurige volgorde): oud 69, nieuw 65, gelijk 16. Bij een identieke keten 24 tegen 24. De doorvraag-opdracht maakte antwoorden slechter (oud beter in 19 van 23) en gaf maar 2 echte vervolgvragen op 150 antwoorden: verwijderd. De uitzondering "negatief sentiment escaleert niet bij een onduidelijke vraag" verviel mee. In beide versies bevatte ongeveer een derde van de antwoorden verzonnen details (47 van 150). |
| 0.4.0 | 2026-09-17 | Achteruitgang hersteld en ontwerp vastgezet op aanwijzing van Mark. Replay van de eerste vraag uit de laatste 9 echte Voys-gesprekken (2×): 7 van de 18 antwoorden werden "niet gevonden", 7 van 7 door het veto van de antwoord-judge op antwoorden mét bron, en 5 van de 9 vragen wisselden van uitkomst. Nieuw uitgangspunt: het oorspronkelijke systeem is de ondergrens; de judges voegen alleen toe. Zie "Definitief ontwerp". |
| 0.3.0 | 2026-09-17 | Live gemeten na deploy (preview-sessies op de Voys-widget). Twee regels bijgesteld: negatief sentiment escaleert niet meer bij een onduidelijke vraag ("mijn telefoon werkt niet" kreeg anders altijd het medewerkeraanbod in plaats van een wedervraag), en een niet volledig beantwoord concept met een uitspraak die niet in de artikelen staat krijgt de weigering, ook met bronnen. |
| 0.2.0 | 2026-09-17 | Gemeten tegen productie-`klai-fast` vóór livegang: de booleans uit v0.1.0 vielen om, `grounding` met drie opties hield stand, een wedervraag wordt herkend aan het vraagteken. REQ-1 t/m REQ-4 gebouwd. |
| 0.1.0 | 2026-09-17 | Eerste versie na onderzoek van gesprekken #900 en #904 (Voys Help NL) en de hele widgetketen. Richting van Mark: "de vraag moet zijn: is dit antwoord goed genoeg, plus vooraf een judge die bepaalt of ik moet doorvragen, en de combinatie bepaalt wat je daarna doet." Goedgekeurd met "Wil je hier een plan voor maken en daarna de implementatie gaan doen?". |

---

# SPEC-RAG-ANSWER-JUDGES-001: een vraag-judge en een antwoord-judge beslissen samen wat de bezoeker krijgt

## Definitief ontwerp (bijgewerkt t/m v0.11.0, leidend boven alles hieronder)

Vastgesteld met Mark op 2026-09-17, na de achteruitgang van v0.2.0 en v0.3.0. Wijk hier niet van af zonder zijn akkoord; de secties daaronder zijn de geschiedenis die tot dit ontwerp leidde.

**Uitgangspunt.** Het oorspronkelijke systeem is de ondergrens. Het toonde elk antwoord dat de citatiemotor aan een artikel kon koppelen, en controleerde alleen tekst zónder bron op beweringen. Dat werkte omdat bewijs besliste en geen mening: dezelfde vraag gaf dezelfde uitkomst. In de 14 dagen vóór de judges: 87% van de antwoorden met bron, 4% zonder bron, 9% weigeringen; bij 39% een zwak zoekresultaat, en daarvan 84% toch een getoond antwoord met bron.

**Regels.**
1. Een antwoord met bron wordt altijd getoond. Vindt de antwoord-judge dat het de vraag niet (volledig) beantwoordt, dan komt de afspraakknop eronder; zijn oordeel haalt nooit iets weg. Alleen de controle per zin mag tekst aanpassen: uitspraken die niet in de artikelen staan worden eruit gehaald (v0.7.0), en alleen als er niets bruikbaars overblijft wordt het de eerlijke weigering.
2. Tekst zonder bron volgt de oorspronkelijke regel: uitspraken die niet in de artikelen staan geven de vaste weigering, anders wordt de tekst getoond. Bij een onduidelijke vraag en een concept dat op een vraagteken eindigt is het een vervolgvraag, zonder knoppen.
3. De vraag-judge stuurt de generatie niet: er gaat geen opdracht mee om door te vragen (v0.5.0, gemeten schadelijk). Onduidelijkheid wordt gemeten en labelt alleen een concept dat op een vraagteken eindigt. Hoe de eerste vraag rijker wordt, is in onderzoek.
4. Alle controles draaien met temperatuur 0. De controle per zin en de reparatie draaien op het zwaardere model (`answer_grounding_model`, 900 aanroepen per minuut), zodat ze het quotum van het antwoordmodel niet opeten.
5. Repareren gebeurt pas bij twee of meer afgekeurde uitspraken, of bij één die het artikel tegenspreekt: één afgekeurde uitspraak klopt in 77% van de gevallen, deze drempel in 92%.
6. Een gefaalde judge: tonen met bron, weigeren zonder bron. Een gefaalde controle per zin of reparatie laat het antwoord staan zoals het was.
7. Tijdsbudget: de controle per zin hooguit 4 s, de reparatie hooguit 3 s. De controle loopt naast de lichte judge, de reparatie erna en alleen bij de drempel uit regel 5. Elke stap logt zijn eigen tijd (`checks_ms`, `repair_ms`). De artikelen gaan volledig naar de controle: inkorten scheelde 0,2 s en veranderde 2 van de 25 oordelen.
8. De interne chat draait dezelfde controle én dezelfde reparatie, met dezelfde tekst, hetzelfde schema en dezelfde drempel uit `klai-libs/chat-prompts`, voor elke tenant en zonder poort per organisatie (#1526, #1530). De reparatie draait overal waar het hele antwoord nog in handen is: het niet-streamende pad en de Strict-stroom, die de renderer volledig vasthoudt tot het slotframe. Een Open-stroom stuurt de woorden zoals ze komen en wordt daar alleen gemeten. Budget intern 12 s controle en 8 s reparatie (tegen 4 en 3 op de widget), omdat de antwoorden drie keer zo lang zijn; loopt een limiet af, dan blijft het antwoord zoals het was. Anders dan op de widget wordt intern nooit geweigerd op dit signaal: blijft er niets over, dan blijft het antwoord staan en logt de stap dat.
9. De eerste vraag van een support-beurt gaat met twee herformuleringen naar het zoeken (`klai-medium`, 2,5 s budget, geen details toevoegen). De zoekdienst draait elke herformulering als eigen pass, herrangschikt tegen de eigen tekst, en voegt de top-8-lijsten met RRF samen ná de bronselectie; vóór het herrangschikken samenvoegen doet niets (2.32). De letterlijke vraag blijft de primaire zoekvraag en een mislukte herformulering laat de beurt zoals hij was. Vervolgbeurten krijgen dit niet: daar won het vorige antwoord als extra pass op zoekniveau en bleef het eind-tot-eind gelijk (2.32).

**Poort vóór livegang van elke wijziging aan deze regels of prompts.** Replay van de eerste vraag uit echte gesprekken (nu 9, doel 50), drie keer per vraag. Elk antwoord dat het oorspronkelijke systeem met bron toonde moet nog steeds getoond worden; uitzonderingen worden gelezen en aan Mark voorgelegd. Gemeten: weigeringen die een vervolgvraag of antwoord worden, wisselingen per vraag, doorlooptijd.

## Probleem

De widget geeft altijd een antwoord. Of er doorgevraagd wordt, hangt af van de zoekscore en een woordentelling: twee woorden uit de vraag die ook in een artikel staan tellen als "direct bewijs". De lijst met woorden die niet mee mogen tellen is maar deels Nederlands, dus "die", "heb", "kan", "geen" en "steeds" tellen mee. Gevolg, gemeten op 2026-09-17: sinds de livegang van SPEC-RAG-CLARIFY-FLOW-001 kregen alle 13 beoordeelde widgetbeurten "antwoorden", ook de 4 met band `low`. Nagespeeld op de echte helppagina's:

| Gesprek | Zoekscore | Gedeelde woorden | Oordeel woordentelling |
|---|---|---|---|
| #900 "factuur betaald én geïncasseerd, hoe storneren?" | 0,08 | factuur, betaald, betaling, die, heb, kan, maar, ook… | direct bewijs |
| #904 "krijg steeds geen permissie om het gekozen nummer te bellen" | 0,13 | bellen, geen, gekozen, nummer, steeds | direct bewijs |

Het diepere probleem is niet de woordenlijst. Niemand vraagt of het antwoord de vraag beantwoordt, en niemand vraagt of de vraag duidelijk genoeg was. Er zijn nu zes losse beslissers (band, woordentelling, gap-classificatie, turn_scope, escalatieclassificatie, claimscheck), elk met een eigen regel op een eigen plek, en de voorrang tussen ze staat verspreid door `partner.py` en `partner_chat.py`.

## Hoe het nu werkt (vastgesteld in de bron en in productie, 2026-09-17)

- **Het antwoord streamt niet.** `_citation_runtime_options` zet elke widget- en partnerbeurt op `markers`, en `_chat_completion_streaming_with_composed_citations` buffert de hele modeltekst voordat er één contentframe uitgaat. Een controle achteraf kost dus geen streaming, alleen de tijd van de aanroep.
- **Taalmodelaanroepen per widgetbeurt:** coreferentie-herschrijving in retrieval-api (#900 beurt 2: 671 ms), `classify_escalation` (elke beurt, parallel met retrieval, time-out 2 s), `classify_turn_scope` (alleen bij een gap of bij band low zonder bewijs, ná retrieval, time-out 2 s), het antwoord, en `classify_answer_claims` (alleen bij tekst zonder bron, time-out 4 s). Alle kleine checks op `klai-fast`.
- **Doorlooptijd** (vraag-rij tot antwoord-rij in `widget_messages`, 14 dagen, 275 antwoorden, geen preview/test): p50 1,75 s, p90 3,76 s, p99 5,80 s. Retrieval zelf 1,0 tot 1,4 s (#900: coref 671, qdrant 401, graph 413, rerank 166 ms).
- **Quotum:** `klai-primary` en `klai-fast` zijn allebei `mistral-small-2603`, samen 100 RPM / 100k TPM in de abonnementswerkruimte (PAYG als vangnet). `klai-medium` heeft 900 RPM.
- **Kwaliteit** volgens de judge achteraf (`conversation_judge`, `klai-medium`, 172 webchatgesprekken in 14 dagen): 35 resolved, 66 abandoned_early; failure_category retrieval_miss 25, generation_error 10, scope_mismatch 7, policy_refusal 6, user_confusion 5, retrieval_wrong 3.

## Ontwerp

Twee judges en één beslisfunctie. Ze vervangen beslissers; ze komen er niet naast.

### Vraag-judge (vóór het genereren)

Eén aanroep vervangt `classify_escalation` en `classify_turn_scope`, en beoordeelt daarnaast de duidelijkheid. Leest de laatste beurten van het gesprek, niet alleen de laatste zin. Draait op elke support-mode-beurt parallel met retrieval.

Uitvoer (strict JSON-schema):

```
scope:       "conversation" | "organisation" | "world"
wants_human: bool
sentiment:   "negative" | "neutral" | "positive"
clarity:     "clear" | "ambiguous"
missing:     string   (kort, wat ontbreekt; leeg bij "clear"; door geen code gelezen)
```

### Antwoord-judge (ná het genereren, vóór tonen)

Leest de vraag met korte geschiedenis, het concept-antwoord (na het strippen van markers) en de artikelen die het model kreeg (titel plus ingekorte tekst). Draait op elke support-mode-beurt waar het model tekst schreef, behalve een safety-blokkade en een broad-mode-antwoord.

```
grounding:  "no_company_statements" | "all_in_articles" | "some_not_in_articles"
verdict:    "answered" | "partial" | "not_answered"
```

Of het concept een verduidelijkingsvraag is, beslist de code: de tekst eindigt op een vraagteken. Zie "Gemeten vóór livegang" voor waarom geen van beide booleans uit v0.1.0 bleef.

### Beslisfunctie

Eén pure functie, volgorde van voorrang:

1. Safety-blokkade: ongewijzigd.
2. Escalatie (regex, `wants_human`, of `sentiment == negative` bij een duidelijke vraag): het antwoord met knop; tekst zonder bron alleen als `grounding` niet `some_not_in_articles` is.
3. Broad-mode-antwoord: ongewijzigd, niet gejudged.
4. Gespreksbeurt (`scope == conversation`): alleen nog een instructie aan het model, geen uitzondering in de citatiemotor (v0.6.0). Levert de beurt geen bron op, dan geldt regel 2.
5. Daarna de tabel:

| clarity | verdict | Uitkomst |
|---|---|---|
| clear | answered | het gecomponeerde antwoord met bronnen; zonder citeerbare bron alleen als `grounding` niet `some_not_in_articles` is, anders vaste weigering |
| clear | partial | als "answered", plus de afspraakknop onder het antwoord; met `some_not_in_articles` de vaste weigering, ook met bronnen |
| clear | not_answered | vaste "niet gevonden" plus doorverwijzing |
| ambiguous | answered | het antwoord; het model eindigt met één korte controlevraag (zie addendum) |
| ambiguous | partial of not_answered | toont de verduidelijkingsvraag van het model als die op een vraagteken eindigt en `grounding` niet `some_not_in_articles` is, zonder knoppen; anders vaste weigering |

Het addendum voor een onduidelijke beurt vraagt het model in één generatie: beantwoorden de artikelen de vraag duidelijk, antwoord dan en eindig met één korte controlevraag over wat ontbreekt; anders stel precies één verduidelijkingsvraag over wat ontbreekt. Er is dus geen extra aanroep om een wedervraag te schrijven. Het addendum is vaste tekst: v0.2.0 zette de omschrijving van de vraag-judge van wat ontbreekt tussen aanhalingstekens in de systeeminstructie, maar die tekst is door de bezoeker te sturen modeluitvoer (review 2026-09-17). Het antwoordmodel leest hetzelfde gesprek en benoemt zelf wat ontbreekt. Het veld `missing` blijft wel in het schema: zonder dat veld zag de vraag-judge "mijn telefoon werkt niet" nog maar 1 van de 3 keer als onduidelijk (was 3 van 3) en "dankjewel" nog maar 1 van de 3 keer als gespreksbeurt (was 3 van 3).

### Faalrichting

- Vraag-judge faalt: `scope` onbekend (behandeld als kennisvraag), `clarity` clear, geen escalatie door het model (de regex blijft werken). Precies het gedrag van vandaag bij een time-out.
- Antwoord-judge faalt, beurt mét citeerbare bronnen: het gecomponeerde antwoord wordt getoond.
- Antwoord-judge faalt, beurt zónder citeerbare bron: vaste weigering, gelijk aan de claimscheck van vandaag.
- Elke mislukking staat als woord in de logs en in `answer_signals` (`judge_failed`), zodat een storing zichtbaar is.

## Requirements

- **REQ-1 Vraag-judge.** `app/services/turn_judge.py` met het schema hierboven, gevoed met de laatste beurten (geknipt), time-out 2 s. Vervangt `classify_escalation` en `classify_turn_scope` op het widgetpad; de functies en hun modules verdwijnen als er geen andere aanroeper is. `escalation_intent()` (regex) en de addenda blijven. Log `partner_chat_turn_judge` met alle velden.
- **REQ-2 Antwoord-judge.** `app/services/answer_judge.py` met het schema hierboven, time-out 2,5 s. Vervangt `app/services/answer_claims.py` en `_show_uncited_reply_without_claims`.
- **REQ-3 Beslisfunctie en addendum.** Eén pure functie met de tabel hierboven, gebruikt door zowel het streaming- als het niet-streamingpad. Op het widgetpad verdwijnen `should_clarify`, `has_direct_evidence_for_query` en `CLARIFY_TURN_ADDENDUM` als beslissers; de band blijft als meetgegeven. De gedeelde bibliotheek blijft ongewijzigd zolang pad A haar gebruikt.
- **REQ-4 Meting per beurt.** `answer_signals` krijgt `clarity`, `verdict`, `grounding`, `decision` (de uitkomst uit de tabel) en `judge_failed` waar van toepassing. Eén logregel `partner_chat_turn_timing` met `retrieval_ms`, `turn_judge_ms`, `generation_ms`, `answer_judge_ms`, `total_ms`.
- **REQ-5 Pad A.** De controle per zin draait sinds 18 sep op de interne chat in Strict met dezelfde gedeelde tekst en hetzelfde schema (drift-test bewaakt de kopie), en sinds #1526 en #1530 repareert hij daar ook, voor elke tenant: niet-streamend en op de vastgehouden Strict-stroom (`_repair_or_measure` in `deploy/litellm/klai_kb_citation_render.py`), alleen meten op een Open-stroom. Er is geen instelling per organisatie; de enige voorwaarden staan in `_repair_would_be_wrong`.

## Gemeten vóór livegang (2026-09-17, productie-`klai-fast`, drie rondes)

**Antwoord-judge, v0.1.0-schema (booleans):** `unsupported_claims` 18 van de 18 keer false, ook bij een verzonnen telefoonnummer ("bel 020-7001234, binnen 3 werkdagen teruggestort") en een verzonnen prijs. Dat is dezelfde ineenstorting die turn_scope in 2026-09-15 had. `asks_clarification` was false bij een pure wedervraag in 2 van 3 rondes.

**Alternatieven op zes concepten × drie rondes:**

| Vorm | Resultaat |
|---|---|
| lijst met niet-gedekte uitspraken | markeert "maandelijks" en "achteraf" (staan in het artikel) en de weigering zelf: onbruikbaar |
| `grounding` met drie opties | 18 van 18 goed; met de uiteindelijke prompt 17 van 18 (één verzonnen prijs gemist in één ronde) |
| `draft_kind` met drie opties | pure wedervraag 0 van 3 keer herkend: vervangen door de vraagtekenregel |

**Vraag-judge:** "dankjewel" na een antwoord 3/3 `conversation`; "ik wil een medewerker spreken" 3/3 `wants_human`; "mijn telefoon werkt niet" 3/3 `ambiguous` met wat ontbreekt; "wat kost een extra gebruiker" 3/3 `clear`; #904 3/3 `clear` met sentiment negatief; #900 2/3 `clear`.

**Latentie per aanroep:** vraag-judge 424 tot 1398 ms (mediaan ~530), antwoord-judge 352 tot 833 ms (mediaan 495).

**Bekend plafond:** een uitspraak die niet in de artikelen staat maar wel met citeerbare bronnen samenkomt, wordt getoond en alleen gelogd (`grounding` in `answer_signals`). Of dat een weigering moet worden, volgt uit de verdeling op echt verkeer: de valse-positievenkans op lange, parafraserende antwoorden is nog niet gemeten.

## Live gemeten na deploy (2026-09-17, preview-sessies, 8 beurten)

| Stap | Bereik |
|---|---|
| retrieval | 638 tot 1169 ms |
| vraag-judge (parallel) | 468 tot 927 ms |
| generatie | 317 tot 1758 ms |
| antwoord-judge | 404 tot 566 ms |
| totaal | 1695 tot 3382 ms |

Twee bevindingen die tot v0.3.0 leidden: "mijn telefoon werkt niet" kwam 2 van 2 keer terug als `ambiguous` én `negative`, waardoor het frustratieaanbod de wedervraag verdrong; en een deels beantwoorde incassovraag met een verzonnen verwerkingstijd naast een echt artikel werd getoond (`partial`, `some_not_in_articles`, met bron).

## Performancebudget

- Doorlooptijd widget: p50 hooguit 2,3 s (nu 1,75), p90 hooguit 4,3 s (nu 3,76), gemeten over dezelfde rij-afstand als de baseline.
- De vraag-judge loopt parallel met retrieval en kost geen wandkloktijd. De sequentiële turn_scope-aanroep ná retrieval verdwijnt.
- De antwoord-judge is de enige nieuwe sequentiële stap. De claimscheck (tot 4 s) verdwijnt ervoor.
- Kleine uitvoer: strict schema met korte velden. De antwoord-judge krijgt hooguit de artikelen die het model kreeg, ingekort per artikel.
- Model: `settings.extraction_model` (`klai-fast`). Wordt het gedeelde Small-quotum een knelpunt (429's of fallbacks in de LiteLLM-logs) of blijkt `klai-medium` even snel, dan verhuizen beide judges naar `klai-medium`.

## Acceptatie

- #904 als vraag met de Klik-en-bel-artikelen: de beslisfunctie volgt de escalatieregel (negatief sentiment), niet de tabel. Met neutraal sentiment en `clarity: ambiguous`: een verduidelijkingsvraag zonder knoppen.
- #900 beurt 1 (duidelijk, niet beantwoord): vaste "niet gevonden" met doorverwijzing, ongeacht de woordoverlap.
- Een beurt met citeerbare bronnen en `verdict: not_answered`: de vaste weigering, geen bronnen.
- Een falende antwoord-judge met bronnen: het gecomponeerde antwoord. Zonder bronnen: de vaste weigering.
- Gates: `uv run pytest`, ruff check en format, pyright op de gewijzigde modules.
- Na deploy: de nieuwe logwoorden verschijnen op echt widgetverkeer, en de doorlooptijd valt binnen het budget.
