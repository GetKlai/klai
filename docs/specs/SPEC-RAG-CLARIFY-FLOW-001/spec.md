---
id: SPEC-RAG-CLARIFY-FLOW-001
version: "0.2.0"
status: REQ-1 gebouwd (PR #1471); REQ-0 pad B gebouwd en baseline gemeten; REQ-0 pad A volgt vóór REQ-4; REQ-2 volgende
created: 2026-09-17
author: Claude (Opus 5), in opdracht van Mark Vletter
priority: high
tenant_scope: platform-breed; beide chatketens (pad A LibreChat via de LiteLLM-hook, pad B widget en partner-API via portal-api)
related:
  - SPEC-RAG-ANSWER-TIERS-001 (de beurtclassificatie vóór het genereren; §"Known ceiling" noemt de controle achteraf die REQ-1b hier bouwt)
  - SPEC-RAG-LOW-CONFIDENCE-ABSTAIN-001 (de weigering vóór het model in Strict die REQ-5 vervangt)
  - SPEC-KNOWLEDGE-ACTIVITY-001 (de zekerheidsband; sinds 0.3.1 op beide paden dezelfde waarde)
  - SPEC-VOYS-HELPBOT-001 REQ-7 (brede modus; ongewijzigd)
---

# HISTORY

| Versie | Datum | Wijziging |
|---|---|---|
| 0.2.0 | 2026-09-17 | Baseline gemeten (REQ-0, pad B) en REQ-1 gebouwd. Pad B stelde in 0 van de 80 beurten een wedervraag en liet geen enkele tekst zonder bron door: het pad is vandaag strikt weigeren of citeren. Vage vragen: 43% vaste weigering. Ook 17% van de beantwoordbare vragen eindigde op de vaste weigering, een retrievalbevinding buiten deze spec. De runner voor pad A is naar vóór REQ-4 verschoven, omdat hij alleen op de server kan draaien. |
| 0.1.0 | 2026-09-17 | Eerste versie na onderzoek in beide ketens, OpenClaw en recent onderzoek. Goedgekeurd door Mark ("Yes go!"), met de opdracht de doorvraagflow generiek te maken voor interne én externe chat. |

---

# SPEC-RAG-CLARIFY-FLOW-001: doorvragen en een natuurlijke "niet gevonden" op beide chatketens

## Probleem

Twee klachten van de productowner, die in de code dezelfde oorzaak hebben:

1. Een te korte of vage vraag ("prijzen?", "werkt niet") krijgt geen wedervraag, terwijl extra context de retrieval juist beter maakt.
2. "Dat kan ik niet vinden" is een vaste zin in plaats van een zin in het gesprek. Intern is dat te verdedigen: medewerkers weten dat ze tegen een kennisbank praten. Een externe bezoeker weet dat niet, en voor hem is een vaste zin een muur.

Gemeten vóór deze spec (SPEC-RAG-ANSWER-TIERS-001 v0.3.0): van 73 weigerende widgetbeurten in zeven dagen hadden er 47 geen enkel inhoudelijk zoekwoord. Dat zijn precies de vragen waar doorvragen helpt.

## Hoe het nu werkt (vastgesteld in de bron, 2026-09-17)

- **De prompt vraagt het al.** Het helpdeskprofiel (`klai-libs/chat-prompts`, "When the question is unclear") zegt: stel hooguit één verduidelijkingsvraag. En onder "When the answer isn't there": zeg eerlijk wat er niet is.
- **Pad B gooit die tekst weg.** `_compose_backend_managed_answer` in `klai-portal/backend/app/services/partner_chat.py`: de citatiemotor laat een wedervraag staan (nagemeten), maar `if not sources:` vervangt daarna elke tekst zonder geciteerde bron door `no_citable_sources_message`. Een wedervraag citeert nooit iets. Alleen beurten die `turn_scope` vooraf "conversation" noemt ontsnappen.
- **Pad A, Strict, laat het model niet eens aan het woord.** `deploy/litellm/klai_knowledge.py` (blok `strict_low_confidence_deterministic_refusal`): bij band `low`/`unknown` zonder direct bewijs zet de hook `mock_response` en wordt het model niet aangeroepen. Komt het model wel aan de beurt, dan vervangt `klai_kb_citation_render.py` zijn tekst bij `no_trusted_sources` en `strict_no_sentence_level_support`.
- **Pad A, Open**, laat de modeltekst al door (`no_trusted_sources_broad_passthrough`).
- **Pad A slaat de kennisbank over voor elke tekst korter dan 8 tekens** (`is_trivial` in `klai_kb_request_context.py`), dus "VPN?" krijgt geen enkele kennis. Pad B heeft die regel niet.
- **Onderzoek**: modellen herkennen onduidelijkheid, maar stellen uit zichzelf in minder dan 1–5% van de gevallen een vraag, en opgehaalde context onderdrukt dat verder (arXiv 2605.25284). Gericht doorvragen verbetert antwoorden met minder vragen (arXiv 2511.08798; ASK, ACL 2025 Industry). OpenClaw doet het omgekeerde ("act now", bij leeg resultaat "vary query, then conclude") en is niet overdraagbaar naar een helpdeskbot met aansprakelijkheid (Moffatt v. Air Canada, 2024).

## Ontwerp: twee beslissingen, één thuis

**Beslissing 1, vóór het genereren: antwoorden of doorvragen.** Een instructie in de prompt alleen is aantoonbaar niet genoeg; de sterke vorm die deze codebase al kent is een instructie per beurt (`CONVERSATIONAL_TURN_ADDENDUM`, `ESCALATION_TURN_ADDENDUM`). Deze spec voegt `CLARIFY_TURN_ADDENDUM` toe. De voorwaarde is dezelfde die vandaag op pad A tot weigeren leidt: band `low` of `unknown` en geen direct bewijs voor de vraag. Wat vandaag een weigering oplevert, levert straks een wedervraag op.

**Beslissing 2, ná het genereren: mag de eigen tekst van het model zonder bron getoond worden?** Ja, als die tekst niets beweert over de organisatie. Een wedervraag en een natuurlijke "dat vind ik niet, gaat het om je factuur of je abonnement?" komen daarmee door. Een prijs, procedure, instelling, beschikbaarheid of storing niet: dan blijft de vaste weigering, precies als nu. Dit is de controle achteraf die `turn_scope.py` zelf als volgende stap noemt. Eén regel lost beide klachten op.

Mechanisme van beslissing 2: dezelfde vorm als `turn_scope`, die op dit soort probleem 11/11 haalde waar een boolean 0 scoorde. Een modelaanroep met een vaste keuzelijst (`no_claims` / `claims`) via `response_format` json_schema, een korte timeout, en bij elke fout of twijfel `claims`. De aanroep kost alleen iets op beurten die nu al geweigerd worden. De bestaande stripper `_answer_without_retrieved_sources` (links, nepcitaten, bewijslabels) draait altijd over doorgelaten tekst.

Bewust niet: de token-meting `inspect_answer_epistemics` (pad A). Die ziet het verschil tussen een vraag en een bewering niet, en is bedoeld om te meten, niet om in te grijpen.

**Per omgeving verschilt toon, niet code.** Extern: klantwoorden, nooit "kennisbank", een mens aanbieden. Intern Strict: mag "staat niet in de kennisbank" zeggen en Open voorstellen. Intern Open: beslissing 2 is overbodig, want de tekst gaat al door.

## Requirements

**REQ-0 — Meten vóór gedrag verandert.** Een evaluatieset en een runner die door de échte endpoints gaan, niet langs `/retrieve`.
- Set: ongeveer 30 vage of korte vragen, 30 duidelijke vragen die de kennisbank beantwoordt, 20 vragen die niet in de kennisbank staan. Op de kennisbank van de Klai-tenant, zodat pad A en pad B vergelijkbaar zijn. Nederlands en Engels.
- Runner pad B: via `/partner/v1/widget-config` en `/partner/v1/chat/completions` met een widgetsessie, draaibaar vanaf een laptop.
- Runner pad A: via de LiteLLM-proxy met een org-gescoopte sleutel, zodat de hook end-to-end draait; draait op de server, zoals `deploy/litellm/scripts/eval_pasted_correspondence_live.py`.
- Metingen per beurt: weigering (vaste tekst), wedervraag, antwoord met bronnen, antwoord zonder bronnen. Voor elke doorgelaten tekst zonder bron: bevat hij een bewering over de organisatie (handmatig of met een apart jurymodel beoordeeld, niet met de classificatie die REQ-1b zelf levert).
- De baseline wordt gemeten en in deze spec vastgelegd vóór REQ-2.

**Baseline pad B, gemeten 2026-09-17** (`klai-portal/backend/evaluation/`, 80 vragen, 1 sample, widget "Klai Website NL"):

| categorie | n | vaste weigering | wedervraag | antwoord met bron | antwoord zonder bron |
|---|---|---|---|---|---|
| vaag | 30 | 13 (43%) | 0 | 17 (57%) | 0 |
| beantwoordbaar | 30 | 5 (17%) | 0 | 25 (83%) | 0 |
| niet in kennisbank | 20 | 11 (55%) | 0 | 9 (45%) | 0 |

Kanttekeningen bij deze meting:
- Doorgelaten tekst zonder bron met een bewering over de organisatie: 0, omdat er geen doorgelaten tekst zonder bron was.
- De kennisbank van deze widget blijkt breder dan de publieke websitepagina's: 9 van de 20 "niet in kennisbank"-vragen kregen een antwoord met bron. Die categorie is dus ruwer dan bedoeld.
- Het testverkeer is niet als test te markeren vanuit de publieke widgetflow. `is_test` vereist een ingelogde reviewer (`PUT /conversations/{id}/test`), dus deze beurten staan in de activiteit van de Klai-tenant.

**REQ-1 — Gedeelde bouwstenen in `klai-libs/chat-prompts`**, zonder I/O:
- (a) `CLARIFY_TURN_ADDENDUM` in een externe en een interne variant: één vraag, hooguit drie keuzes en die uitsluitend uit de titels van de meegegeven artikelen, geen enkele uitspraak over de organisatie.
- (b) De prompt en het JSON-schema voor de antwoordclaim-classificatie, plus een pure functie die het resultaat leest en bij alles behalve een geldig `no_claims` `claims` teruggeeft.
- (c) Een pure beslisregel `should_clarify(confidence_band, has_direct_evidence)`.
- De vendored kopie `deploy/litellm/klai_chat_prompts.py` wordt bijgewerkt; de drifttest bewaakt de nieuwe namen.

**REQ-2 — Beslissing 2 op pad B**, op beide plekken in `_compose_backend_managed_answer` die nu `no_citable_sources_message` teruggeven na compositie. Achter een widgetinstelling, standaard uit, eerst aan voor Voys en Klai. De beslissingsvlaggen (brede-modusaanbod, escalatie) blijven meekomen met doorgelaten tekst. Gebouwd door een Opus-uitvoerder: dit is de groundingsgrens.

**REQ-3 — Beslissing 1 op pad B**, naast de bestaande aanvullingen in `app/api/partner.py`, achter dezelfde instelling. Mag pas aan waar REQ-2 aan staat.

**REQ-4 — Beslissing 2 op pad A**, in `klai_kb_citation_render.py` bij `no_trusted_sources` en `strict_no_sentence_level_support`. De uitvoerder stelt eerst vast of de Strict-render de volledige tekst buffert, en zegt het als dat niet zo is.

**REQ-5 — Beslissing 1 op pad A.** Vervangt de weigering vóór het model in Strict door een modelaanroep met `CLARIFY_TURN_ADDENDUM`. **Mag alleen live waar REQ-4 live is**: die weigering vooraf bestaat om te voorkomen dat het model bij zwak bewijs uit algemene kennis antwoordt, en beslissing 2 is wat dat dan tegenhoudt. Neemt ook een besluit over `is_trivial` onder 8 tekens, met de meting uit REQ-0.

**REQ-6 — Telemetrie.** Elke beurt waarop een beslissing viel logt de uitkomst als één doorzoekbaar woord (`clarify` / `answer`, `no_claims` / `claims` / `classifier_failed`), zoals `turn_scope` dat doet. Anders drift een grens ongemerkt.

## Acceptatie

Gemeten met REQ-0 op beide paden, per pad, vóór en na:
- **Harde grens:** doorgelaten teksten zonder bron met een bewering over de organisatie: 0.
- Wedervraag op de vage set: stijgt ten opzichte van de baseline. Doel voorlopig ≥ 60%, vastgezet na de baseline.
- Onnodige wedervraag op de duidelijke set: ≤ 10%. Te vaak doorvragen irriteert.
- Vaste weigering op de niet-in-kennisbank-set (extern): daalt ten opzichte van de baseline, zonder dat de harde grens wordt geraakt.
- Faalrichting bewezen met een test: een fout of timeout in de classificatie geeft de vaste weigering.

## Risico's

- **Beslissing 2 kan een bewering aanzien voor geen bewering.** Dan bereikt een onderbouwde uitspraak over de organisatie de gebruiker. Dat is het restrisico. Beperkt door de faalrichting, de stripper en de harde nul in de evaluatie; gemeten door REQ-6.
- **REQ-5 opent op pad A opnieuw een modelaanroep bij zwak bewijs.** Daarom mag REQ-5 alleen na REQ-4.
- **Latentie:** één extra modelaanroep, alleen op beurten die nu geweigerd worden.

## Bouwvolgorde (afdwingbaar)

REQ-0 en REQ-1 parallel → baseline vastleggen → REQ-2 → REQ-3 → meten → REQ-4 → REQ-5 → meten → breder aanzetten.
