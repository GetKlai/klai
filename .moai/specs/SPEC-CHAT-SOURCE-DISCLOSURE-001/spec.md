---
id: SPEC-CHAT-SOURCE-DISCLOSURE-001
version: 0.2.0
status: draft
created: 2026-07-08
updated: 2026-07-08
author: Mark Vletter
priority: medium
issue_number: 0
---

# SPEC-CHAT-SOURCE-DISCLOSURE-001: Structurele bron/agent-activity-weergave voor pad A (mini-SPEC)

## HISTORY

| Versie | Datum | Wijziging |
|---|---|---|
| 0.1.0 | 2026-07-08 | Eerste draft n.a.v. de Engelse-footer-bug (share aZIY-N3YZSqGHh3qsrcs1) |
| 0.2.0 | 2026-07-08 | REQ-DISC-06 reproductie-notitie: flush-pad vereist géén extra fix (zie onder). |
| 0.3.0 | 2026-07-08 | Fase 3 herzien na architectuur-vondst (`stream.cjs` + LibreChat-rendering). Marker-rename/footer-reductie descoped; taal-neutraal client-script i.p.v. marker-driven. Zie "Fase 3 architectuurbesluit". |

## Reproductie-notitie (REQ-DISC-06, 2026-07-08)

Bron: request `a8e26845-f23a-4670-acd0-5f5bac858890` (org `368884765035593759`).
Getraceerd via VictoriaLogs (retrieval-api) + directe source-analyse van
`deploy/litellm/klai_kb_citation_render.py`.

**Bewezen (source):**
1. De zichtbare footer wordt geëmit door `_append_visible_sources_section`
   (`:453`). De koppen zijn taal-afhankelijk: `_visible_trace_language`
   (`:379`) is een **query-token-heuristiek** — een vraag zónder Nederlandse
   markers levert `"en"` → backend schrijft `**Sources**` / `**Agent activity**`.
   De aanleiding-tekst ("hardcoded Nederlands") is dus achterhaald: de footer
   is al server-side tweetalig; dát is precies de bron van de mismatch met het
   NL-only script (`H=new Set(["Bronnen","Agent activiteit"])`, v8).
2. Het streaming-flush-pad zet `stream_flush_alignment` op één van
   `replacement_appended` (`:1003`), `raw_remainder` (`:1025`) of `prefix_cut`
   (`:1027`). **Alle drie de takken convergeren op `:1033`**, waar de footer
   wordt aangehangen. `raw_remainder` bepaalt alleen hoe de *antwoordtekst*
   wordt uitgelijnd (Voys #21-regressie), niet hoe de footer wordt toegevoegd.
3. De footer landt in één finale delta (`set_message_content`, `:1036`) met een
   dubbel-append-guard (`_citation_stream_sources_appended`, `:1037`). Het
   opgeslagen bericht == het gestreamde bericht, footer incluis. De history-
   stripper `KLAI_BACKEND_FOOTER_HEADING_RE` matcht alleen NL en verwijdert de
   EN-footer dus niet uit history.

**Conclusie:** het streaming-flush-pad heeft **geen** extra fix nodig. De bug is
puur presentatie-taal (EN-kop onherkend door NL-only script) + strip-taal
(NL-only strippers). **REQ-DISC-05** (bold-tolerant + meertalige strippers) en de
structurele marker-omzetting (**REQ-DISC-01/02/03/04**, Fase 3) volstaan.

**Niet geverifieerd:** de exacte bytes van het opgeslagen bericht en een live
EN-Playwright-reproductie zijn niet apart opgehaald; dit wordt gedekt door de
Fase 3-acceptance (Playwright nl+en panelen) en de nieuwe unit-tests. De
litellm `citation_decisions` (waarin `stream_flush_alignment` zit) worden als
printf-`_msg` gelogd (`:691`), niet als los queryable veld — vandaar dat een
field-query op `stream_flush_alignment` leeg blijft.

## Fase 3 architectuurbesluit (2026-07-08)

Tijdens Fase 3-grounding bleek de oorspronkelijke aanpak (REQ-DISC-01/02/03:
gestructureerde marker die het client-script uit de DOM leest) **niet haalbaar
in pad A**, om drie in de code bewezen redenen:

1. **`stream.cjs` is de echte marker-consumer, server-side.** De LibreChat-patch
   `deploy/librechat/patches/stream.cjs` (+ `getklai/patches/stream.cjs`) bevat
   `KLAI_SOURCES_MARKER_RE` en `extractKlaiSourcesFromText` (`:612-633`): hij
   stript de `<!-- klai_sources -->`-comment **vóór** rendering en hangt
   `sources` aan het bericht. De marker bereikt de client-DOM dus nooit.
2. **LibreChat rendert model-markdown zonder `rehype-raw`** (bewuste
   security-keuze: geen rauwe HTML/comments uit model-output). Een
   HTML-comment wordt niet als comment-node in de DOM opgenomen. Een
   client-side script kan `klai_kb_meta_v1` daardoor principieel niet uit een
   assistant-bericht lezen — REQ-DISC-03 zoals geschreven is onuitvoerbaar.
3. **De enige betrouwbare client-carrier is de zichtbare tekstfooter.** Het
   v8-script parseert die juist daarom (en werkt vandaag voor NL).

### Besluit (proportioneel + robuust)

De gerapporteerde bug is: een Engelse chat toont een Engelse footer die het
NL-only v8-script niet herkent → platte markdown i.p.v. panelen. De mooiste
*redelijke* oplossing gegeven de pad-A-constraints:

- **REQ-DISC-03 (herzien):** het disclosure-script wordt **taal-neutraal**
  (v8→v9). Het herkent de footer-koppen in een compacte set
  (`Bronnen|Sources`, `Agent activiteit|Agent activity`) en kiest de
  telwoord-labels ("1 bron"/"1 source", "1 stap"/"1 step") op
  `navigator.language` (fallback `nl`). De NL-only `new Set([...])` en de
  NL-only `label()` vervallen. Tekst-parsing blijft — dat is de enige
  betrouwbare pad-A-carrier — maar is niet langer taal-fragiel.
- **REQ-DISC-01 / REQ-DISC-02 (descoped):** marker-rename naar
  `klai_kb_meta_v1`, footer-reductie tot één bronregel en de
  `stream.cjs`-herschrijving vervallen. Reden: (a) er is geen consumer voor
  gestructureerde activity-data (activity wordt uit de zichtbare tekst
  gerenderd), (b) `stream.cjs` levert `sources` al gestructureerd voor
  persistentie, (c) het raakt een niet-in-scope bestand (`stream.cjs`, 2
  kopieën) met deploy-risico voor een cosmetisch paneel. YAGNI +
  proportionaliteit.
- **REQ-DISC-04 (behouden):** fail-open — geen herkende footer → script doet
  niets; oude NL-berichten blijven renderen (NL blijft herkend).
- **REQ-DISC-05 (geleverd, Fase 2):** meertalige bold-tolerante strippers
  dekken de model-imitatie-verdediging al af, onafhankelijk van dit script.

### Herziene acceptance (Fase 3)

1. NL-chat én EN-chat tonen identiek gestylede inklap-panelen; telwoord-labels
   volgen `navigator.language` (getest via de canary vm-harness met een NL- en
   een EN-footer; live Playwright-verificatie ná deploy).
2. Bericht zonder footer → script doet niets, geen console-fouten (bestaande
   null-safety-assert blijft).
3. Canary `getklai_canary_config.test.cjs` asserteert de v9-marker, herkenning
   van NL+EN koppen, `navigator.language`-labels, en afwezigheid van de
   NL-only `new Set(["Bronnen","Agent activiteit"])`.
4. Beide entrypoint-bestanden (`klai-entrypoint.sh` + `getklai/entrypoint.sh`)
   dragen het identieke v9-blok.

## Aanleiding (bewezen, 2026-07-08)

De inklapbare "Bronnen / Agent activiteit"-weergave in LibreChat (pad A) is
een geïnjecteerd DOM-script (`deploy/librechat/klai-entrypoint.sh`,
`klai-kb-disclosure-v8`) dat de **teksttfooter** parseert op exact de twee
Nederlandse koppen (`H=new Set(["Bronnen","Agent activiteit"])`). De backend
(`deploy/litellm/klai_kb_citation_render.py`) schrijft die footer hardcoded
Nederlands en stuurt daarnaast al een óngelezen gestructureerd kanaal mee:
de `<!-- klai_sources=<base64-json> -->`-marker plus een `sources`-veld op
de stream.

Waargenomen bug: in een Engelstalige chat bevatte het opgeslagen antwoord
een Engelse footer ("**Sources** / **Agent activity**", met reële
telemetriewaarden) die het script niet herkent → platte markdown in plaats
van panelen. Het opschoonfilter mist deze vormen bovendien: de
bronnenkop-regex (`_SOURCE_HEADING_RE` in `klai_citations`) is niet
bold-tolerant en voor "Agent activity"-blokken bestaat geen strip-patroon;
de history-stripper (`KLAI_BACKEND_FOOTER_HEADING_RE`) matcht alleen
Nederlands. Het exacte overdrachtsmechanisme van de Engelse variant is nog
niet vastgepind (REQ-DISC-06).

Kern van de oplossing: **tekst-parsing vervangen door het bestaande
gestructureerde kanaal.** Presentatie (en dus taal) hoort client-side,
waar de UI-locale bekend is; server-side kent litellm de taal niet.

## Scope

Pad A (LibreChat → LiteLLM-hook) only: `deploy/litellm/klai_kb_citation_render.py`,
`deploy/librechat/klai-entrypoint.sh`, `klai-libs/citations` (strippers),
`deploy/litellm/klai_kb_request_context.py` (history-stripper).

## Requirements (EARS)

- **REQ-DISC-01 (Ubiquitous):** THE citatie-renderer SHALL één geversioneerde
  gestructureerde marker emitten,
  `<!-- klai_kb_meta_v1=<base64url-json> -->`, met daarin `sources[]`
  (label, title, url, source_label) én de agent-activity-velden (mode,
  chunks_injected, retrieval_ms, kbs_in_scope, kbs_with_results,
  kbs_used_as_sources, candidate/selected counts, confidence_band). De
  bestaande `klai_sources`-marker vervalt in dezelfde wijziging (geen
  oud+nieuw naast elkaar); de history-stripper SHALL beide markervormen
  blijven verwijderen (oude opgeslagen berichten bevatten de oude vorm).
- **REQ-DISC-02 (Ubiquitous):** THE zichtbare teksttfooter SHALL worden
  gereduceerd tot één compacte bronregel (alleen de bronlinks, geen
  activity-blok); de activity-data reist uitsluitend via de marker.
- **REQ-DISC-03 (Event-driven):** WHEN het disclosure-script (bump naar
  `klai-kb-disclosure-v9`) een `klai_kb_meta_v1`-marker aantreft in een
  assistant-bericht, THE script SHALL de panelen "Bronnen" en
  "Agent activiteit" renderen uit de marker-JSON en de fallback-bronregel
  visueel verbergen. Labels komen uit een client-side stringtabel met
  minimaal `nl` en `en`, gekozen op `navigator.language` (fallback `nl`).
  Tekstkop-parsing ("Bronnen"/"Agent activiteit" heading-matching) vervalt.
- **REQ-DISC-04 (Unwanted):** IF de marker ontbreekt of niet parseert,
  THEN het script SHALL niets doen en blijft de compacte bronregel staan
  (fail-open; oude opgeslagen berichten zonder nieuwe marker degraderen
  naar de bronregel, zonder JS-fouten).
- **REQ-DISC-05 (Ubiquitous):** Verdediging tegen model-imitatie,
  onafhankelijk van de marker: THE strippers SHALL (a) bronnenkoppen
  bold-tolerant en meertalig matchen (min. `Bronnen|Sources`, met
  `**…**`-varianten), (b) een strip-patroon krijgen voor
  "Agent activity|Agent activiteit"-blokken in modeltekst, en (c) de
  history-stripper `KLAI_BACKEND_FOOTER_HEADING_RE` SHALL dezelfde
  meertalige set matchen — zodat een door het model nagebootste footer in
  geen enkele taal het opgeslagen antwoord haalt.
- **REQ-DISC-06 (Event-driven):** WHEN de implementatie start, THE eerste
  taak SHALL een gecontroleerde reproductie zijn van de Engelse-footer-bug
  (Engelstalige vraag in een verse conversatie; litellm-response en
  opgeslagen bericht vergelijken; referentie: request
  `a8e26845-f23a-4670-acd0-5f5bac858890`, `stream_flush_alignment=raw_remainder`).
  De bevinding bepaalt of REQ-DISC-05 volstaat of dat het
  streaming-flush-pad een extra fix nodig heeft, en wordt als notitie in
  deze SPEC bijgeschreven vóór de flip.

## Acceptance (kern)

1. NL-chat én EN-chat tonen identiek gestylede inklap-panelen; labels
   volgen de browser-locale (Playwright: beide locales, panelen aanwezig,
   geen platte "**Sources**"-kop in de body).
2. Bericht zonder marker (legacy) → alleen de compacte bronregel, geen
   script-fouten in de console.
3. Model-geschreven "**Sources**"/"**Agent activity**"-blok in een
   testantwoord wordt door de stripper verwijderd vóór opslag (unit,
   klai-libs/citations) — in NL én EN, met en zonder bold.
4. History met oude `klai_sources`- én nieuwe `klai_kb_meta_v1`-marker
   wordt door de history-stripper geschoond (unit, litellm).
5. Canary-test `deploy/librechat/tests/getklai_canary_config.test.cjs`
   asserteert v9-marker + afwezigheid van heading-parsing.
6. Reproductie-notitie (REQ-DISC-06) staat in HISTORY vóór deploy.

## Fasering

1. **Reproductie** (REQ-DISC-06) — half dagdeel, bepaalt restscope.
2. **Strippers** (REQ-DISC-05) — kleine PR, direct deploybaar (litellm
   hook-deploy met `--force-recreate`), stopt de zichtbare bug-klasse ook
   vóór de structurele omzetting.
3. **Marker v1 + footer-reductie + script v9** (REQ-DISC-01/02/03/04) —
   één PR over litellm + entrypoint; `deploy-librechat-config.yml` voor de
   entrypoint-uitrol, versie-bump v9 vervangt het oude injectieblok bij
   container-herstart.

## Exclusions

1. Geen server-side taaldetectie en geen vertaalde teksttfooter — taal is
   een presentatievraag en hoort client-side.
2. Geen LibreChat-fork of patch van de gehashte bundle — uitsluitend het
   bestaande injectiemechanisme (entrypoint).
3. Widget/partner (pad B, `WidgetChatSurface.tsx` heeft een eigen
   hardcoded "Agent activiteit") — apart traject; dit SPEC raakt alleen
   pad A.
4. Geen wijziging aan welke bronnen geselecteerd worden — uitsluitend
   presentatie en anti-imitatie-hygiëne.

## mx_plan

- `@MX:ANCHOR` op `_append_visible_sources_section` (contract: marker v1
  is de enige drager van activity-data; footer = één bronregel).
- `@MX:NOTE` op het disclosure-script-blok in `klai-entrypoint.sh`
  (marker-gedreven, geen tekst-parsing; bump versie bij elke wijziging).
