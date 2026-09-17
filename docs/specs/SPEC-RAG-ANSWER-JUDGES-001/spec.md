---
id: SPEC-RAG-ANSWER-JUDGES-001
version: "0.2.0"
status: REQ-1 t/m REQ-4 (pad B, helpdeskwidget) in uitvoering; REQ-5 (pad A) na meting
created: 2026-09-17
author: Claude (Opus 5), in opdracht van Mark Vletter
priority: high
tenant_scope: platform-breed; eerst pad B (widget in support mode via portal-api), daarna pad A (LibreChat via de LiteLLM-hook)
related:
  - SPEC-RAG-CLARIFY-FLOW-001 (vervangt op pad B de trigger van beslissing 1 en de claimscheck van beslissing 2)
  - SPEC-RAG-ANSWER-TIERS-001 (turn_scope gaat op in de vraag-judge)
  - SPEC-CHAT-QUALITY-LOOP-001 (de judge achteraf blijft de onafhankelijke meetlat)
  - SPEC-KNOWLEDGE-ACTIVITY-001 (answer_signals krijgt de oordelen van beide judges)
---

# HISTORY

| Versie | Datum | Wijziging |
|---|---|---|
| 0.2.0 | 2026-09-17 | Gemeten tegen productie-`klai-fast` vóór livegang: de booleans uit v0.1.0 vielen om, `grounding` met drie opties hield stand, een wedervraag wordt herkend aan het vraagteken. REQ-1 t/m REQ-4 gebouwd. |
| 0.1.0 | 2026-09-17 | Eerste versie na onderzoek van gesprekken #900 en #904 (Voys Help NL) en de hele widgetketen. Richting van Mark: "de vraag moet zijn: is dit antwoord goed genoeg, plus vooraf een judge die bepaalt of ik moet doorvragen, en de combinatie bepaalt wat je daarna doet." Goedgekeurd met "Wil je hier een plan voor maken en daarna de implementatie gaan doen?". |

---

# SPEC-RAG-ANSWER-JUDGES-001: een vraag-judge en een antwoord-judge beslissen samen wat de bezoeker krijgt

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
2. Escalatie (regex of `wants_human`, of `sentiment == negative`): het antwoord met knop; tekst zonder bron alleen als `grounding` niet `some_not_in_articles` is.
3. Broad-mode-antwoord: ongewijzigd, niet gejudged.
4. Gespreksbeurt (`scope == conversation`): tonen zonder bronnen als `grounding` niet `some_not_in_articles` is, anders de vaste weigering.
5. Daarna de tabel:

| clarity | verdict | Uitkomst |
|---|---|---|
| clear | answered | het gecomponeerde antwoord met bronnen; zonder citeerbare bron alleen als `grounding` niet `some_not_in_articles` is, anders vaste weigering |
| clear | partial | als "answered", plus de afspraakknop onder het antwoord |
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
- **REQ-5 Pad A.** Dezelfde judges voor de interne chat, eerst alleen in Strict (waar het antwoord al wordt vastgehouden). Pas na meting van REQ-4 op widgetverkeer.

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
