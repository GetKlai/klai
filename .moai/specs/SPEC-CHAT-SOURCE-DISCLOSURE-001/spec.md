---
id: SPEC-CHAT-SOURCE-DISCLOSURE-001
version: 0.1.0
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
