# Hoe de chat van Klai werkt

**Stand: 24 september 2026, commit `67e387304` op main.** Alleen code en configuratie zijn als bron gebruikt. Specs en oudere architectuurdocumenten zijn behandeld als claims en waar nodig gecorrigeerd. Elke bewering heeft een anker `bestand:regel` (paden relatief aan de map die erbij staat).

Dit is de kaart van het chatpad. Lees hem vóór je een voorstel doet of code verandert in `deploy/litellm/`, `klai-portal/backend/app/services/partner_chat.py`, `answer_*.py`, `turn_judge.py`, `query_paraphrase.py`, `klai-libs/chat-prompts/` of `klai-retrieval-api/retrieval_api/api/retrieve.py`, en werk hem bij in dezelfde PR als je een stap, een LLM-aanroep of een beslisregel verandert. Wat er onderzocht is en waarom het zo gebouwd is, staat in [chat-quality-history-and-plan.md](chat-quality-history-and-plan.md).

---

## 0. In één scherm

Er zijn **twee chatpaden** met elk hun eigen beslislogica. Ze delen alleen de zoekdienst (`/retrieve`), de prompttekst uit `klai-libs/chat-prompts` en de bronselectie uit `klai-libs/citations`.

| | Pad A: interne chat | Pad B: website-widget en partner-API |
|---|---|---|
| Ingang | LibreChat → LiteLLM-proxy, hook `klai_knowledge.py` | `POST /partner/v1/chat/completions` in portal-api |
| Waar de beslissingen vallen | LiteLLM-callbacks vóór en na het model | portal-api, rond één modelaanroep |
| Vraag verduidelijken | instructie aan het antwoordmodel, bij band `low` zonder direct bewijs | vaste regel over de gevonden artikelen (`clarify_gate`) beslist of er één vraag komt; een klein model schrijft alleen die vraag |
| Zwakke zoekresultaten | band stuurt verduidelijken, Strict-weigering en modelkeuze | band wordt alleen opgeslagen; de regel "alle bronnen < 0,4" stuurt |
| Onbewezen uitspraken | alleen in Strict gecontroleerd en gerepareerd; Open niet | gecontroleerd per uitspraak, gerepareerd of geweigerd |
| Mens aanbieden | bestaat niet | afspraakknop bij weigering, deelantwoord, escalatie |
| Wat er per beurt bewaard wordt | alleen logregels (30 dagen) | `widget_messages.answer_signals` in de database |

**Twee paden is de uitzondering, niet de regel.** Het doel is één pijplijn ([plan §7](chat-quality-history-and-plan.md#7-plan-goedgekeurd-24-september-2026)). Een wijziging aan het chatpad zegt in de PR per beslissing die ze raakt: *gedeeld*, of *bewust apart, omdat …*. Geldige redenen om apart te zijn: de ingang en identiteit (teamkey tegenover anonieme bezoeker), de modi (Strict/Open tegenover support/breed), de afspraakknop tegenover de bronnenvoettekst, en het distilleren van geplakte correspondentie. De rest van de verschillen in de tabel hierboven is toevallig ontstaan en wordt samengevoegd; "het was al zo" is geen reden.

Modelaliassen (`deploy/litellm/config.yaml`): `klai-primary` en `klai-fast` zijn allebei `mistral-small-2603` (`:7,29`), `klai-medium` is `mistral-medium-3.5` (`:70`), `klai-large` is `mistral-large-2512` (`:47`). Bij een quotafout vallen primary en fast terug op medium (`:114`). LiteLLM-timeout 120 s met 1 retry (`:122,125`).

---

## 1. Pad A: interne chat (LibreChat → LiteLLM)

LibreChat vraagt altijd `klai-primary` aan, ook voor titels (`deploy/librechat/librechat.yaml:176-200`). LiteLLM draait de callbacks in deze volgorde (`deploy/litellm/config.yaml:126-158`): `klai_knowledge.klai_knowledge_hook`, `custom_router.token_router`, `klai_pii_observe`, `klai_pii_enforce`. "Mock" hieronder betekent: het model wordt overgeslagen en de gebruiker krijgt een vaste tekst (`data["mock_response"]`). Alle ankers in deze sectie zijn in `deploy/litellm/` tenzij anders vermeld.

### 1.1 Vóór het model (`klai_knowledge.py`)

| # | Stap | Beslist op basis van | LLM | Bij falen | Vastgelegd als |
|---|---|---|---|---|---|
| A1 | Taal van het antwoord, per beurt afgeleid uit alle user-beurten, met een drempel tegen wisselen (`:443-444`) | alle user-beurten, deterministisch (`klai_conversation_language.py:1058-1073`) | nee | model kiest zelf | in `_klai_kb_meta` |
| A2 | PDF-bijlagen via docling (`:446-478`) | bijlagen | nee | mock-fouttekst (`:458-460`) | `chat_pdf_attachment_processed` |
| A3 | Geplakte correspondentie herkennen (`:486-498`) | tekstpatronen (`klai_pasted_correspondence.py`) | nee | – | `pasted_correspondence_detected` |
| A4 | Triviale berichten slaan de kennisbank over: korter dan 8 tekens zonder `?` en geen meta-vraag (`:499-500`, `klai_kb_request_context.py:87-98`) | tekst | nee | – | – |
| A5 | Geen `org_id` of geen LibreChat-gebruiker: hook doet niets (`:503-517`) | key-metadata | nee | – | – |
| A6 | Invoerveiligheid op de laatste user-beurt en op tool-resultaten (`:559-594`); deterministische regels, geen externe provider (`klai_llm_safety/policy.py:185-191`) | tekst | nee | mock-weigering | `llm_safety_litellm_decision` |
| A7 | Titelverzoek of meta-vraag ("wat kan ik hier?"): geen retrieval (`:596-639`) | promptvorm, regex | nee | – | info, niet zichtbaar (§6) |
| A8 | KB-instellingen ophalen (`:661`); portal onbereikbaar én geen cache geeft in beide modi een mock-weigering (`:663-674`) | portal + Redis | nee | **fail-closed** | `kb_settings_unavailable_refusal` |
| A9 | Modus: `general`, `open_kb`, `strict_kb`, `strict_no_kb` of `*_unavailable` (identiteit ontbreekt) (`klai_kb_scope_policy.py:80-122`). Strict haalt web search weg (`:685-693`), `strict_no_kb` en `strict_unavailable` geven een mock-weigering (`:695-712,740-795`) | KB-voorkeuren | nee | – | deels info |
| A10 | Taxonomiebomen en dekking ophalen, parallel, 0,8 s (`:843-847`, `klai_kb_query_rewrite.py:66`) | – | nee | fail-open | `taxonomy_trees_fetch_failed` |
| A11 | **Herschrijven van de zoekvraag**, gecombineerd met taxonomieclassificatie en distillatie van geplakte correspondentie in één aanroep, op elke niet-triviale beurt, ook de eerste (`:875-889`, `klai_kb_query_rewrite.py:710-719`). Een herschrijving die het onderwerp laat vallen wordt teruggedraaid (`klai_kb_query_rewrite.py:235-241`) | vraag + laatste 6 berichten (`klai_kb_request_context.py:373-390`) | `klai-fast`, 1,5 s | fail-open: ruwe vraag | `query_rewrite`, `query_rewrite_destructive_blocked` |
| A12 | Meerdere vragen splitsen in maximaal 6 deelvragen, deterministisch (`klai_kb_confidence_policy.py:187-244`); niet bij geplakte correspondentie (`:992-1012`) | `?`-regels, lijstjes | nee | – | `sub_questions_truncated` |
| A13 | **`/retrieve`** met herschreven vraag, `raw_query`, `coreference_resolved`, `top_k=20`, `sub_queries` (`:1013-1026`, `:292`). Timeout in code 3 s (`:286`), in productie 60 s (`deploy/docker-compose.yml:486`) | – | zie §3 | Strict: mock-weigering; Open: waarschuwing in de prompt (`:1063-1122`) | error-regel |
| A14 | Geen evidence pack in het antwoord: Strict mock-weigering (`:1227-1276`) | – | nee | fail-closed | `retrieval_response_missing_evidence_pack` |
| A15 | Alleen Strict: chunks met score < 0,15 vallen weg (`:297,1283-1320`) | score | nee | – | `kb_evidence_below_score_floor_dropped` |
| A16 | Veiligheidscheck per chunk (`:1323-1373`) | chunktekst | nee | alles geblokkeerd: mock-weigering | `llm_safety_litellm_context_chunks_dropped` |
| A17 | **Lage-zekerheidsvlag** = band `low`/`unknown` **én** geen direct bewijs, dat wil zeggen minder dan 2 salient woorden van de vraag in de chunks (`:1376-1381`, `klai_chat_prompts.py:1097-1136`) | band + woordoverlap | nee | geen band: geen vlag | – |
| A18 | Meervoudige-vraagvlag = deelvragen gevonden of de regel `is_multi_question_query` (vraagtekens plus met en/and/or verbonden vragen) (`:1394-1396`, `klai_kb_confidence_policy.py:164-184`) | tekst | nee | – | in kb_meta |
| A19 | Gat-event en retrieval-log, fire-and-forget (`:1434-1450`) | reranker < 0,4, dense < 0,35 | nee | – | `portal_retrieval_gaps`, Redis |
| A20 | **Verduidelijken**: in aanmerking bij chunks + band + geen meervoudige vraag + geen eigen content + geen geplakte correspondentie; verduidelijkt als ook A17 geldt (`:1451-1482`) | A17, A18 | nee | – | `kb_clarify_decision clarify_decision=clarify\|answer` |
| A21 | Strict + lage zekerheid + geplakte correspondentie: mock-weigering (`:1484-1544`) | – | nee | – | `strict_low_confidence_deterministic_refusal` |
| A22 | Nul chunks: Strict mock-weigering; Open een prompt "niet in je kennisbank, dit is een algemeen antwoord" (`:1546-1647`) | – | nee | – | – |
| A23 | Contextprompt bouwen (`:1652-1684`). Bij verduidelijken komt `CLARIFY_TURN_ADDENDUM["internal"]` erin (in Strict én Open, `:1665-1674`): "stel precies één korte vraag, gok geen antwoord" (`klai_chat_prompts.py:1000-1008`). Anders bij lage zekerheid de low-relevance-tekst, bij een meervoudige vraag de per-vraag-instructie (`:1675-1681`) | A17-A20 | nee | – | `low_confidence_injection_applied` |

### 1.2 Modelkeuze en generatie

| # | Stap | Beslist op basis van | LLM | Bij falen | Vastgelegd als |
|---|---|---|---|---|---|
| A24 | **Router**, alleen als `klai-primary` is aangevraagd (`custom_router.py:199-241`): tool-historie → `klai-large`; laatste user-bericht > 300 tokens → `klai-large`; ≥ 3 URL's in één bericht → `klai-fast`; Strict-KB met chunks en (meervoudige vraag of lage zekerheid) → `klai-medium`; andere KB-beurt → `klai-primary`; zonder KB en > 3000 tokens → `klai-fast` | berichten + kb_meta | nee | aangevraagde model | `klai_router_final_model` op info: **niet zichtbaar** (§6) |
| A25 | Generatie op het gekozen model; LibreChat streamt | – | ja | LiteLLM-fallback naar medium | – |

### 1.3 Na het model (`klai_kb_citation_render.py`)

| # | Stap | Beslist op basis van | LLM | Bij falen | Vastgelegd als |
|---|---|---|---|---|---|
| A26 | Een **Strict-stream wordt helemaal vastgehouden** tot het eindoordeel; de gebruiker ziet tot dan niets (`:1540-1547,1559-1584`). Een Open-stream loopt direct door (`:1669-1716`) | modus | nee | – | – |
| A27 | Bronselectie per zin (`:156-330`). Zonder ondersteunde bron: Strict vervangt de tekst door een vaste weigering, Open laat hem staan (`:199-254`). Bij een lage band is de lat per bron lager (`klai-libs/citations/klai_citations/__init__.py:919-932`) | antwoord vs bronnen, band | nee | – | `kb_citations_rendered_structured` |
| A28 | Claims-check, alleen Strict en alleen als de weigering zou volgen: een concept dat geen bedrijfsfeiten beweert (bijvoorbeeld een verduidelijkingsvraag) mag toch door (`:1286-1366`) | concept + artikeltitels | `klai-fast`, 4 s | weigering blijft | `kb_answer_claims` |
| A29 | **Grounding-check en reparatie**, alleen Strict met citeerbare bronnen en niet op geplakte of bijgevoegde content (`:1164-1259`). Reparatie bij ≥ 2 niet-onderbouwde uitspraken of 1 tegenspraak (`klai_chat_prompts.py:1237-1244`). Weigert nooit: blijft er niets over, dan blijft het antwoord staan (`klai_answer_grounding.py:230-241`) | antwoord + chunks | `klai-medium`, check 12 s, reparatie 8 s | antwoord ongewijzigd | `kb_answer_grounding`, `kb_answer_repaired` |
| A30 | **Open wordt niet gecontroleerd en ook niet gemeten**: `_measure_answer_grounding` stopt bij niet-Strict (`:1266-1267`) | – | – | – | – |
| A31 | Voettekst met bronnen en "Agent activiteit", inclusief de band als "Retrieval score: low" zichtbaar voor de gebruiker (`:776-785`), deelvragen en niet-doorzochte vragen (`:663-703`) | kb_meta | nee | – | – |

LibreChat geeft het model ook de MCP-tool `klai-knowledge` (`deploy/librechat/librechat.yaml:194-195`), waarmee het zelf kan zoeken. Dat pad is hier niet beschreven.

---

## 2. Pad B: widget en partner-API (portal-api)

Ingang: `POST /partner/v1/chat/completions` (`klai-portal/backend/app/api/partner.py:96,1646-1689`). **Support-modus** is een vlag per widget (`widget_config.support_mode`, alleen `wgt_`-keys, `partner.py:427-445`); stappen met "(support)" draaien alleen daar. Een partner-API-key zonder support krijgt alleen retrieval, generatie en de bronnencomposer. Ankers in `klai-portal/backend/app/` tenzij anders vermeld.

### 2.1 Vóór het model

| # | Stap | Beslist op basis van | LLM | Bij falen | Vastgelegd als |
|---|---|---|---|---|---|
| B1 | Permissie en toegestaan model (`klai-primary`/`klai-fast`), invoerveiligheid (`api/partner.py:98,666-681,1706`) | request | nee | 400/403 of weigering | – |
| B2 | KB-scope fail-closed (`api/partner.py:1724-1754`) | key/widget | nee | fail-closed | `partner_kb_ids_unresolved` |
| B3 | User-beurt naar `widget_messages` (`api/partner.py:1832-1858`) | – | nee | – | `widget_messages` |
| B4 | **Parafrasen**: alleen de eerste support-vraag krijgt er 2, als extra zoekpasses (`services/query_paraphrase.py:54-65`, `services/partner_chat.py:2912,2928`) | vraag | `klai-medium`, 2,5 s | geen varianten | `partner_chat_query_paraphrase` |
| B5 | **`/retrieve`** met het laatste user-bericht, `top_k=8`, zonder `raw_query` en `coreference_resolved` (`services/partner_chat.py:2883,2914-2933`, `api/partner.py:1902`), timeout 10 s | – | zie §3 | **HTTP 502 aan de bezoeker** (`api/partner.py:1920-1944`) | – |
| B6 | **Vraagbeoordelaar** (support), parallel met B4/B5: scope, wil een mens, sentiment, duidelijkheid, onderwerp behandeld (`services/turn_judge.py:59-110,179-191`) | laatste 6 beurten | `klai-fast`, 2 s | behandeld als duidelijke kennisvraag | `partner_chat_turn_judge` |
| B7 | Chunk-veiligheid (`services/partner_chat.py:3002-3019`). **De band gaat alleen naar de opslag** (`:2792-2812,3020`) | chunks | nee | – | `answer_signals.band` |
| B8 | Gat-klasse (support): *soft* = alle reranker-scores < 0,4, *hard* = nul chunks (`services/gap_classification.py:13-37`) | scores | nee | – | `portal_retrieval_gaps` |
| B9 | Brede modus (algemene kennis) alleen met toestemming van de bezoeker en bij nul chunks (`services/partner_chat.py:2501-2524`) | B8 | nee | – | `answer_signals.broad_mode` |
| B10 | Escalatie (support): regex op "mens gevraagd" of frustratie, of vraagbeoordelaar (`services/escalation_intent.py:81-92`, `api/partner.py:1955-1965`) | tekst + B6 | nee | – | – |
| B11 | **Buiten het onderwerp** (support, als ingesteld): een doorverwijzing met afspraakknop, zonder antwoordmodel (`api/partner.py:2014-2063`) | B6 | `klai-fast`, 2,5 s | vaste tekst | `partner_chat_off_topic` |
| B12 | **Eén vraag vooraf** (support en intern, niet bij brede modus, escalatie, gespreksbeurt of geplakte correspondentie): vraagt alleen als sterke artikelen (≥ drempel) uit twee of meer documenten hetzelfde onderwerp in verschillende varianten behandelen (gedeelde kop of grotendeels gedeelde titel die raakt aan wat de bezoeker zei) en het gesprek geen variant noemt; opties zijn de varianten uit de titels, hooguit vier. Nooit bij een soft gap en niet direct na een eigen vraag. Het schrijfmodel krijgt gesprek, as en opties; de vraag moet één regel met vraagteken zijn (`services/clarify_gate.py`, `services/clarify_decision.py`, `api/partner.py`) | gesprek + sterke chunks | alleen de schrijfstap: `klai-fast`, 2 s | direct antwoorden | `clarify_decision` (fired, reason, axis, documents, options), `answer_signals.planned_question`, `answer_signals.asked_about` |
| B13 | **Zwakke bronnen** (support en intern, soft gap): "gebruik de artikelen alleen als er één de vraag letterlijk beantwoordt, zeg anders dat het er niet staat", met afspraakknop; bronkaarten vervallen als het antwoord niet `answered` heet (`api/partner.py:2087-2099`, `services/clarify_decision.py`) | B8 | nee | – | `answer_signals.weak_sources` |

### 2.2 Generatie

**B14.** Het model wordt aangeroepen via LiteLLM met de master key; het hele antwoord wordt gebufferd (`services/partner_chat.py:2317-2367`). De kennishook van pad A doet hier niets, want er is geen `org_id` (`deploy/litellm/klai_knowledge.py:503-507`). **De router van pad A doet wel mee**: een prompt boven 3000 tokens wordt `klai-fast`, een laatste user-bericht boven 300 tokens `klai-large` (`deploy/litellm/custom_router.py:199-241`).

### 2.3 Na het model

| # | Stap | Beslist op basis van | LLM | Bij falen | Vastgelegd als |
|---|---|---|---|---|---|
| B15 | Bronnencomposer; zonder ondersteunde bron de vaste weigering, in support met afspraakknop (`services/partner_chat.py:1916-1961`). Krijgt de band niet mee | antwoord vs bronnen | nee | – | `partner_chat_citation_selection_decision` |
| B16 | Uitvoerveiligheid (`services/partner_chat.py:2383-2394`) | tekst | nee | weigering | `partner_chat_output_blocked` |
| B17 | **Antwoordbeoordelaar en grounding-check parallel** (support): verdict (beantwoord, deels, niet) en per uitspraak of een artikel hem draagt (`services/partner_chat.py:1975-2045`) | concept + artikelen + gesprek | beoordelaar `klai-fast` 2,5 s; check `klai-medium` 4 s | met bron tonen, zonder bron weigeren | `partner_chat_answer_judge`, `answer_grounding_late` |
| B18 | **`decide_answer`** (`services/answer_judge.py:103-141`): met bron altijd tonen, niet volledig beantwoord → `partial_answer` met afspraakknop; zonder bron en niet-onderbouwd → weigering; zonder bron, vraag onduidelijk en concept eindigt op `?` → verduidelijkingsvraag zonder knoppen; anders tonen | B6, B12, B17 | nee | – | `answer_signals.decision` |
| B19 | **Reparatie** bij ≥ 2 niet-onderbouwde uitspraken of 1 tegenspraak; blijft er niets over, dan een weigering met knop (`services/partner_chat.py:2113-2192`) | check | `klai-medium`, 3 s | antwoord ongewijzigd | `partner_chat_answer_repair`, `answer_signals.repaired` |
| B20 | Opslag van de assistent-beurt met `answer_signals` (`api/partner.py:2168-2234`, `services/widget_audit.py:261-279`) | – | nee | – | `widget_messages.answer_signals` |

`CLARIFY_TURN_ADDENDUM["external"]` in `klai-libs/chat-prompts` wordt nergens in klai-portal gebruikt.

---

## 3. De zoekdienst `/retrieve` (klai-retrieval-api)

- **Coreferentie** draait alleen als de aanroeper het niet zelf deed (`klai-retrieval-api/retrieval_api/api/retrieve.py:138-154`), met `klai-fast` en 3 s (`retrieval_api/config.py:38-39`). Pad A doet het zelf in A11; pad B niet, dus elke widget-vervolgvraag met geschiedenis krijgt deze aanroep.
- **Zoeken**: dense + sparse embeddings, hybride RRF in Qdrant met een letterlijke leg als de vraag herschreven is (`retrieve.py:700-712`), graph-search (Graphiti, fail-open) en link-expansie; dan herrangschikken (Infinity, fail-open naar Qdrant-volgorde), kwaliteitsvloer, maximaal 2 chunks per bron. Varianten (pad B) krijgen elk een eigen pass en worden na het herrangschikken samengevoegd; deelvragen (pad A) krijgen elk een eigen retrieve.
- **Zekerheidsband** (`retrieval_api/api/ranking.py:20-64`): de hoogste `final_rank_score` over de geserveerde chunks, alleen als er een rerankerscore is. ≥ 0,60 `high`, < 0,30 `low`, daartussen `medium`, anders `unknown` (`retrieval_api/config.py:84-91`). Eén berekening voor beide paden (`retrieve.py:1250`). `answer_signals.top_score` op pad B is de score vóór de boosts en dus níet de score waarop de band beslist.
- **Evidence pack**: maximaal 3 bronnen, of 5 bij meerdere kennisbanken (`retrieve.py:1264-1268`); beide paden bouwen hun context daaruit.
- **Telemetrie**: bij niveau `shadow`/`full` een rij in `telemetry.query_shadow` met band, chunk-ids en rerankerscore, zonder vraagtekst (`retrieve.py:1338-1354`).

---

## 4. Alle LLM-aanroepen per beurt

**Pad A** (in volgorde): herschrijven (`klai-fast`, 1,5 s, A11) → coreferentie in `/retrieve` alleen als A11 om infrastructuurredenen oversloeg (`klai-fast`, 3 s) → generatie (primary, via de router naar large, medium of fast) → claims-check (`klai-fast`, 4 s, alleen Strict bij dreigende weigering) → grounding-check (`klai-medium`, 12 s, alleen Strict) → reparatie (`klai-medium`, 8 s, alleen boven de drempel). **Open-modus: alleen herschrijven en generatie.**

**Pad B, support** (in volgorde): parafrasen (`klai-medium`, 2,5 s, alleen de eerste vraag) ∥ vraagbeoordelaar (`klai-fast`, 2 s) ∥ retrieval met coreferentie op vervolgvragen (`klai-fast`, 3 s) → doorverwijzing buiten onderwerp (`klai-fast`, 2,5 s, dan stopt de beurt) → antwoordplan (`klai-fast`, 2 s) → generatie (primary, router kan fast of large maken) → antwoordbeoordelaar (`klai-fast`, 2,5 s) ∥ grounding-check (`klai-medium`, 4 s) → reparatie (`klai-medium`, 3 s, boven de drempel). **Zonder support: alleen coreferentie en generatie.**

---

## 5. De beslissingen waar het om draait

**Antwoorden, weigeren, verduidelijken of een mens aanbieden**
- *Pad A.* Weigeren gebeurt vóór het model (instellingen onbereikbaar, veiligheid, en in Strict: geen kennisbank, retrieval faalt, nul chunks, lage zekerheid bij geplakte mail) of erna (Strict zonder ondersteunde bron, tenzij de claims-check niets bedenkelijks ziet). Verduidelijken is een instructie aan het antwoordmodel bij A20; of het model daarna echt een vraag stelt, wordt niet vastgelegd. Een mens aanbieden bestaat niet.
- *Pad B.* Verduidelijken via het antwoordplan (B12, vóór het schrijven) of als een bronloos concept toevallig op een vraagteken eindigt terwijl de vraag onduidelijk heette (B18). Weigeren bij geen bron plus onbewezen uitspraken, of als de reparatie niets overlaat. Bij zwakke bronnen eerlijk "staat er niet" (B13). Afspraakknop bij weigering, deelantwoord, escalatie en buiten het onderwerp.

**Wordt de zekerheidsband gebruikt?**
- *Pad A: ja*, voor verduidelijken (A20), de Strict-weigering bij geplakte mail (A21), de upgrade naar `klai-medium` (A24) en een lagere bronlat (A27). De gebruiker ziet de band in de voettekst (A31).
- *Pad B: nee.* Opgeslagen, niet gebruikt. Zwakke resultaten worden herkend met "alle rerankerscores < 0,4" (B8, B13).

**Meerdere vragen in één bericht**
- *Pad A*: deelvragen krijgen een eigen zoekpass, de rest verschijnt als "niet doorzocht"; zet de per-vraag-instructie in de prompt, schakelt verduidelijken uit en geeft in Strict een upgrade naar `klai-medium`.
- *Pad B*: niet gebruikt.

**Modelkeuze**: de router van A24, ook op pad B. Geen van de regels is tegen antwoordkwaliteit gemeten.

**Onbewezen uitspraken**: pad A Strict controleert en repareert en weigert nooit op dit signaal; pad A Open doet niets; pad B support controleert, repareert, en weigert als er niets overblijft.

**Wat de eerste vraag extra krijgt**: pad A niets (herschrijven draait altijd); pad B twee parafrasen, vervolgvragen krijgen coreferentie.

---

## 6. Wat er vandaag gemeten wordt

**Logregels van pad A** (`service:litellm` in VictoriaLogs). Alleen **warning en hoger** komt aan (`klai_kb_citation_render.py:1016-1022`). Zichtbaar zijn onder meer `query_rewrite*`, `kb_clarify_decision`, `low_confidence_injection_applied`, `kb_answer_claims`, `kb_answer_grounding`, `kb_answer_repaired`, `kb_citations_*`, `chat_synthesis_complete`, `llm_safety_litellm_*`. **Niet zichtbaar** (info): de routerbeslissing (`custom_router.py:272`), taxonomie, meta-vragen, titelverzoeken en de Strict-weigeringen zonder kennisbank. Welk model een interne beurt kreeg, is daardoor niet terug te vinden.

**Logregels van pad B** (`service:portal-api`, structlog, info komt wel aan): `partner_chat_turn_judge`, `partner_chat_answer_judge`, `partner_chat_query_paraphrase`, `partner_chat_answer_repair`, `clarify_decision`, `partner_chat_turn_timing`, `answer_grounding_late`, `partner_chat_off_topic`.

**Database**
- `widget_messages.answer_signals` (per assistent-beurt van support-widgets): band, top_score (vóór boosts), gat-type, bronnen, broad_mode, taal, model, duidelijkheid, verdict, grounding, beslissing, aantal onbewezen uitspraken, gerepareerd, geplande vraag, zwakke bronnen. Pad A heeft hier geen tegenhanger.
- `portal_retrieval_gaps` voor beide paden; `conversation_quality_judgments` (nachtelijke LLM-beoordeling per widgetgesprek, `klai-medium`); `telemetry.query_shadow` (retrieval-api).
- `knowledge.rag_eval_results`: de nachtelijke RAGAS-run in knowledge-ingest. Die **genereert zijn eigen antwoord met `klai-fast`** en meet dus geen van beide chatpaden (`klai-knowledge-ingest/knowledge_ingest/eval/judge_client.py:4-20`).

**Operatorscripts** in `klai-portal/backend/scripts/`: `grounding_report.py` (widgetgrounding per dag), `calibrate_confidence_bands.py` (band tegen uitkomst), `simulate_conversations.py` (gesimuleerde bezoeker), `export_librechat_messages.py`. De metingen van het logboek (herspelen met een gesimuleerde bezoeker, blind beoordeeld op de volgende beurt) draaien met gereedschap buiten de repo.

---

## 7. Geprobeerd en afgewezen

Voorstellen die al gemeten zijn. Stel ze niet opnieuw voor zonder te zeggen wat er nu anders is. Details en meetmethode: [chat-quality-history-and-plan.md](chat-quality-history-and-plan.md).

| Idee | Wanneer | Waarom afgewezen |
|---|---|---|
| Antwoordmodel in dezelfde generatie laten doorvragen | 17 sep, 18 sep, 22 sep | Het model vraagt dan bijna nooit (2 op 150; 7 op 32; 4 op 12); een aparte stap (B12) werkt wel |
| Vorige antwoord als extra zoekleg bij vervolgvragen | 18 sep | Zoekwinst 39% → 64%, eind-tot-eind gelijk met meer verzinsels |
| Doorvragen op een taxonomie-aspect | 18 sep | Vindt even vaak het goede artikel (7 van 12) |
| Promptvarianten tegen verzonnen details | 17-18 sep | Gelijk of slechter; controle per zin met reparatie werkt wel |
| LiteLLM's ingebouwde complexity router | 24 sep | Noemt alle Nederlandse supportvragen en hun Engelse vertaling `SIMPLE` |
| Encodermodel Laya (zero-shot) voor routering en meerdere vragen | 24 sep | Onder de meerderheidsbasis voor routering; gelijk aan de oude vraagtekenregel |
| Meerdere vragen en moeilijkheid laten bepalen door de herschrijfaanroep | 24 sep | Meerdere vragen slechter dan de regel (36 tegen 40 van 42), +250 ms |
| Banddrempels verschuiven | 24 sep | Band voorspelt niet of een beantwoord concept klopt (72/67/67%); geen beter snijpunt |
| Vierde bron, linktekst herstellen, prijsregels groeperen | 19-22 sep | Meer gevonden, geen beter antwoord |

---

## 8. Niet uitgezocht

- De echte waarden in de server-`.env` (hierboven staan code- en compose-standaarden).
- Of Graphiti-search per zoekopdracht een LLM aanroept.
- Het MCP-toolpad van LibreChat.
- De binnenkant van retrieval-api voorbij de aanroepplekken (router, diversiteit, quality boost).
