# Hoe de chat van Klai werkt

**Stand: 24 september 2026, commit `67e387304` op main; §2 (pad B) opnieuw nagelezen op 29 september 2026, commit `9f670d82f`.** Alleen code en configuratie zijn als bron gebruikt. Specs en oudere architectuurdocumenten zijn behandeld als claims en waar nodig gecorrigeerd. Elke bewering heeft een anker `bestand:regel` (paden relatief aan de map die erbij staat).

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
| Onbewezen uitspraken | alleen in Strict gecontroleerd en gerepareerd; Open niet | gecontroleerd per uitspraak; widget laat het antwoord heel of weigert, intern repareert |
| Mens aanbieden | bestaat niet | afspraakknop bij weigering, deelantwoord, escalatie |
| Wat er per beurt bewaard wordt | alleen logregels (30 dagen) | `widget_messages.answer_signals` in de database |

**Twee paden is de uitzondering, niet de regel.** Het doel is één pijplijn ([plan](chat-quality-history-and-plan.md#7-plan)). Een wijziging aan het chatpad zegt in de PR per beslissing die ze raakt: *gedeeld*, of *bewust apart, omdat …*. Geldige redenen om apart te zijn: de ingang en identiteit (teamkey tegenover anonieme bezoeker), de modi (Strict/Open tegenover support/breed), de afspraakknop tegenover de bronnenvoettekst, en het distilleren van geplakte correspondentie. De rest van de verschillen in de tabel hierboven is toevallig ontstaan en wordt samengevoegd; "het was al zo" is geen reden.

Modelaliassen (`deploy/litellm/config.yaml`): `klai-primary` en `klai-fast` zijn allebei `mistral-small-2603` (`:7,29`), `klai-medium` is `mistral-medium-3.5` (`:70`), `klai-large` is `mistral-large-2512` (`:47`). `klai-judge` is hetzelfde model als `klai-medium`, maar op de Vibe-key van de Klai-organisatie (hetzelfde Vibe-tegoed als `klai-ingest`, zonder fallback), voor al het achtergrondwerk op Medium van portal-api: de nachtelijke judges, support-case-analyse en het groeperen van gaten (`settings.conversation_judge_model`). Medium-calls binnen een chatbeurt blijven op `klai-medium`. Bij een quotafout vallen primary en fast terug op medium (`:114`). LiteLLM-timeout 120 s met 1 retry (`:122,125`).

---

## 1. Pad A: interne chat (LibreChat → LiteLLM)

LibreChat vraagt altijd `klai-primary` aan, ook voor titels (`deploy/librechat/librechat.yaml:176-202`). LiteLLM draait de callbacks in deze volgorde (`deploy/litellm/config.yaml:126-158`): `klai_knowledge.klai_knowledge_hook`, `custom_router.token_router`, `klai_pii_observe`, `klai_pii_enforce`. "Mock" hieronder betekent: het model wordt overgeslagen en de gebruiker krijgt een vaste tekst (`data["mock_response"]`). Alle ankers in deze sectie zijn in `deploy/litellm/` tenzij anders vermeld.

**Per tenant omzetten naar portal-api.** Het endpoint "Klai AI" in `librechat.yaml` leest zijn sleutel en adres uit twee variabelen in de `.env` van de tenant, `KLAI_CHAT_API_KEY` en `KLAI_CHAT_BASE_URL`. Provisioning zet ze voor een nieuwe tenant op LiteLLM (de teamkey en `http://litellm:4000/v1`, `klai-portal/backend/app/services/provisioning/generators.py`), en dezelfde generator vult ze bij bestaande tenants aan via `/internal/librechat/regenerate`. Daarom komt de variabelen-backfill vóór de yaml die ze leest: eerst portal-api uitrollen en één keer regenereren, dan de yaml, daarna pas omzetten per tenant. Omzetten doet `klai-portal/backend/scripts/switch_internal_chat.py <slug> portal|litellm`, dat alleen die twee regels herschrijft en de container opnieuw aanmaakt, omdat Docker de `.env` bij het aanmaken in de omgeving bakt en een herstart dus het oude endpoint houdt. Op `portal` wijst het naar `http://portal-api:8010/partner/v1` met de interne-chatkey van de org (`app/services/internal_chat_keys.py`: één per org, alleen provisioning maakt hem, zonder kennisbankrijen omdat het profiel van de medewerker de scope bepaalt); terug naar `litellm` zet het de teamkey terug en trekt die interne key in. Tot stap 9 van het plan de hook weghaalt, blijft LiteLLM de standaard voor nieuwe tenants.

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

**Nagelezen op 29 september 2026, commit `9f670d82f`.** Ingang: `POST /partner/v1/chat/completions`, functie `chat_completions` (`klai-portal/backend/app/api/partner.py:1882`). Sinds #1663 bedient die ene functie de widget, de partner-API en de interne chat die via portal-api loopt; `profile.surface` kiest per stap. **Support-modus** is een vlag per widget (`_widget_support_mode_enabled`, `api/partner.py:462`); stappen met "(support)" draaien alleen daar. Ankers in `klai-portal/backend/app/` tenzij anders vermeld. De functienaam is het houvast; het regelnummer hoort bij de commit hierboven.

### 2.1 Vóór het model

| # | Stap | Beslist op basis van | LLM | Bij falen | Vastgelegd als |
|---|---|---|---|---|---|
| B1 | Permissie (`api/partner.py:1896`), model en berichtvorm (`_validate_chat_request`, `:750`; toegestaan `klai-primary` en `klai-fast`, `:111`), invoerveiligheid (`_input_safety_block_response`, `:704`). Een widget mag geen tools meesturen (`:1908`) | request | nee | 400/403 of weigering | – |
| B2 | KB-scope fail-closed (`_partner_kb_scope`, `api/partner.py:1827`) | key/widget | nee | fail-closed | `partner_kb_ids_unresolved` |
| B3 | User-beurt naar `widget_messages`, vóór het zoeken (`api/partner.py:2016-2041`) | – | nee | – | `widget_messages` |
| B4 | **Parafrasen**: alleen de eerste support-vraag krijgt er 2, als extra zoekpasses (`first_question_variants`, `services/query_paraphrase.py:54`). Niet als het bericht in deelvragen is gesplitst (`services/partner_chat.py:3722-3728`) | vraag | `klai-medium`, 2,5 s | geen varianten | `partner_chat_query_paraphrase` |
| B5 | **`/retrieve`** met het laatste user-bericht, `top_k=8`, zonder `raw_query` en `coreference_resolved` (`retrieve_context`, `services/partner_chat.py:3510`, body `:3729-3743`), timeout 10 s (`:3458`). De interne chat op dezelfde functie stuurt wel `raw_query` en `coreference_resolved` mee na een eigen herschrijving, met `top_k=20` en 60 s (`:3689-3709`, `:3465`, `:3462`). Een bericht met meerdere vragen wordt op elk oppervlak gesplitst in deelvragen (`:3673-3676`) | – | zie §3 | widget en partner: **HTTP 502 aan de bezoeker** (`api/partner.py:2142-2166`) | – |
| B6 | **Vraagbeoordelaar** (support), parallel met B4/B5 (`api/partner.py:2121-2132`, `judge_turn`, `services/turn_judge.py:201`): scope, wil een mens, sentiment, duidelijkheid, onderwerp behandeld | laatste 6 beurten | `klai-fast`, 2 s | behandeld als duidelijke kennisvraag | `partner_chat_turn_judge` |
| B7 | Chunk-veiligheid (`services/partner_chat.py:3830-3847`). **De band gaat alleen naar de opslag** (`_record_retrieval_band`, `:3487`). Alleen op de interne chat in Strict voedt de band de keuze voor `klai-medium` (`strict_risk_model`, `:3430`) | chunks | nee | – | `answer_signals.band` |
| B8 | Gat-klasse: *soft* = alle reranker-scores < 0,4, *hard* = nul chunks; zonder reranker-scores telt de dense score onder 0,35 (`classify_gap`, `services/gap_classification.py:13-37`; drempel `core/config.py:342`) | scores | nee | – | `portal_retrieval_gaps` |
| B8b | **Kiezen welke passages de vraag beantwoorden** (alleen helpwidget; niet bij brede modus of een vraag met meerdere delen; `select_passages` in `services/passage_selection.py`, aangeroepen aan het eind van `retrieve_context`). Het model schrijft in deze volgorde: wat de bezoeker nodig heeft, per passage de letterlijke zin die dat beantwoordt, en dan het oordeel (beantwoordt, hangt af van een variant, staat er niet in). Code zoekt elke geciteerde zin terug in de passage; alleen passages met een teruggevonden zin gaan naar de schrijver, zonder dubbele tekst, samen met een korte opdracht die de behoefte en de geciteerde zinnen noemt (`writer_brief`). Voor die passages slaat de bronnencomposer zijn woordvergelijking tussen vraag en bron over | gesprek + passages | `klai-medium`, 4 s | beurt loopt zoals zonder deze stap | `passage_selection` |
| B9 | Brede modus (algemene kennis) alleen met toestemming van de bezoeker en bij nul chunks (`_broad_mode_active`, `services/partner_chat.py:3055`). Het aanbod "breder zoeken" hangt alleen aan een weigering als het zoeken niets vond (`_helpdesk_refusal_offers`), zodat het aanbod en de modus dezelfde voorwaarde hebben. In de praktijk vindt het zoeken bijna altijd iets, dus de brede modus komt zelden voor | B8 | nee | – | `answer_signals.broad_mode` |
| B10 | Escalatie (support): regex op "mens gevraagd" of frustratie (`escalation_intent`, `services/escalation_intent.py:81`), anders `wants_human` of **negatief sentiment** van B6, zonder toets op duidelijkheid (`api/partner.py`, blok "Escalation is the backend's call"). Een escalatie slaat B12 en B13 over en stuurt de generatie met `ESCALATION_TURN_ADDENDUM` (bij frustratie zonder excuus). Een variant waarin frustratie alleen de knop toevoegt is op 29 september gemeten en niet uitgerold (zie het plan, §8) | tekst + B6 | nee | – | `answer_signals.sentiment`, `answer_signals.appointment` |
| B11 | **Buiten het onderwerp** (support, als ingesteld): een doorverwijzing met afspraakknop, zonder antwoordmodel (`api/partner.py:2247-2298`) | B6 | `klai-fast`, 2,5 s | vaste tekst | `partner_chat_off_topic` |
| B11b | **Staat niet in de passages** (helpwidget, oordeel van B8b, of geen enkele geciteerde zin teruggevonden): de vaste tekst "niet gevonden" met afspraakknop, zonder antwoordmodel. Ook bij een gefrustreerde bezoeker en ook als de vraagbeoordelaar de beurt een praatje noemt. Niet als de bezoeker om een mens vraagt (`api/partner.py`, blok na de doorverwijzing) | oordeel B8b | nee | – | `partner_chat_not_in_passages` |
| B12 | **Eén vraag vooraf.** Op de helpwidget beslist B8b: bij "hangt af van een variant" is de vraag die de keuzestap schreef zelf de reactie, zonder antwoordmodel en zonder knop, mits het één korte regel is die op een vraagteken eindigt en de uitvoerveiligheid doorstaat, hooguit één keer per gesprek (`already_asked`). Intern blijft de vergelijking van artikeltitels met een vraagschrijver (`clarify_decision`; niet bij brede modus, escalatie, gespreksbeurt of geplakte correspondentie) | oordeel B8b; intern chunks | widget nee; intern `klai-fast`, 2 s | geen vraag | `clarify_decision` |
| B13 | **Zwakke bronnen** (support alleen als B8b geen passages koos of uitviel, en intern; soft gap, niet bij escalatie): "gebruik de artikelen alleen als er één de vraag letterlijk beantwoordt, zeg anders dat het er niet staat en noem de afspraak" (`api/partner.py`, `WEAK_SOURCES_ADDENDUM`, `services/clarify_decision.py:93`). De zin over de afspraak is een opdracht aan het model; noemt een bronloos antwoord dat de vraag niet beantwoordt de afspraak niet terwijl de knop er hangt, dan zet code de vaste zin erachter (`_judge_composed_answer`, slot; `appointment_offer_sentence` in `klai-libs/chat-prompts`). De knop komt langs drie wegen: het model zet de marker én noemt de afspraak in de tekst (`services/partner_chat.py:2160-2162`), een bronloos antwoord dat de vraag niet beantwoordt (`:2521-2525`), of `partial_answer` uit B18 | B8 | nee | – | `answer_signals.weak_sources` |

### 2.2 Generatie

**B14.** Het model wordt aangeroepen via LiteLLM met de master key; het hele antwoord wordt gebufferd (`_llm_request_body`, `services/partner_chat.py:1571`; niet-streamend `:4002-4014`). Sinds #1661 reist de organisatie van de tenant mee op elke aanroep van de beurt (`api/partner.py:2074-2076`), zodat LiteLLM het maskeerbeleid van de tenant toepast. Als er iets gemaskeerd is, zet LiteLLM een instructie vóór de berichten (`deploy/litellm/klai_pii_enforce.py:716-740`); die instructie noemt `<PERSON_1>` als voorbeeld terwijl PERSON nooit gemaskeerd wordt (`deploy/litellm/klai_pii_restore_eval.py:289-296`). **De router van pad A doet mee, alleen als `klai-primary` is aangevraagd**: een laatste user-bericht boven 300 tokens wordt `klai-large`, een prompt boven 3000 tokens `klai-fast` (`deploy/litellm/custom_router.py:42,46,199`).

### 2.3 Na het model

| # | Stap | Beslist op basis van | LLM | Bij falen | Vastgelegd als |
|---|---|---|---|---|---|
| B15 | Bronnencomposer; zonder ondersteunde bron de vaste weigering, in support met afspraakknop en het aanbod breder te zoeken (`_compose_backend_managed_answer`, `services/partner_chat.py:2083`). Krijgt de band niet mee | antwoord vs bronnen | nee | – | `partner_chat_citation_selection_decision` |
| B16 | Uitvoerveiligheid (`output_safety_violation`, `services/partner_chat.py:168`, aangeroepen `:2899`, `:4072`) | tekst | nee | weigering | `partner_chat_output_blocked` |
| B17 | **Antwoordbeoordelaar en controle per zin parallel** (support en intern Strict; `_judge_composed_answer`, `services/partner_chat.py:2328`, `:2447-2460`) | concept + artikelen + gesprek | beoordelaar `klai-fast` 2,5 s; controle `klai-medium` 4 s, intern 12 s (`services/answer_grounding.py:60-67`) | met bron tonen, zonder bron weigeren | `partner_chat_answer_judge`, `answer_grounding_late` |
| B18 | **`decide_answer`** (`services/answer_judge.py:112`): met bron altijd tonen, niet volledig beantwoord → `partial_answer` met afspraakknop; bij escalatie met bron altijd `answer`; zonder bron en niet-onderbouwd → weigering; zonder bron, vraag onduidelijk en concept eindigt op `?` → verduidelijkingsvraag zonder knoppen; anders tonen | B6, B12, B17 | nee | – | `answer_signals.decision` |
| B19 | **Na de controle per zin.** Op de helpwidget verandert niets een antwoord nadat het geschreven is: de controle legt alleen vast (`answer_signals.unsupported`). Eén vangnet: koos B8b de passages niet (uitgevallen of overgeslagen) en onderbouwt de controle geen enkele uitspraak, dan wordt het de weigering. Intern herschrijft een model bij ≥ 2 niet-onderbouwde uitspraken of 1 tegenspraak (`_repair_unsupported_statements` in `services/partner_chat.py`); blijft daar niets over, dan blijft het antwoord staan (#1715) | controle | intern `klai-medium`, 8 s | antwoord ongewijzigd | `partner_chat_answer_repair` (intern) |
| B20 | Opslag van de assistent-beurt met `answer_signals` (`api/partner.py:2486-2506`, `services/widget_audit.py:261-279`). Sinds 29 september ook of de knop getoond is (`appointment`) en de toon die de vraagbeoordelaar hoorde (`sentiment`), via `_fill_answer_signals` | – | nee | – | `widget_messages.answer_signals` |

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

**Pad B, support** (in volgorde): parafrasen (`klai-medium`, 2,5 s, alleen de eerste vraag) ∥ vraagbeoordelaar (`klai-fast`, 2 s) ∥ retrieval met coreferentie op vervolgvragen (`klai-fast`, 3 s) → doorverwijzing buiten onderwerp (`klai-fast`, 2,5 s, dan stopt de beurt) → de vraag schrijven, alleen als de vaste vraagstap vuurt (`klai-fast`, 2 s) → generatie (primary, router kan fast of large maken) → antwoordbeoordelaar (`klai-fast`, 2,5 s) ∥ grounding-check (`klai-medium`, 4 s); boven de drempel volgt geen modelaanroep meer (B19). **Zonder support: alleen coreferentie en generatie.**

---

## 5. De beslissingen waar het om draait

**Antwoorden, weigeren, verduidelijken of een mens aanbieden**
- *Pad A.* Weigeren gebeurt vóór het model (instellingen onbereikbaar, veiligheid, en in Strict: geen kennisbank, retrieval faalt, nul chunks, lage zekerheid bij geplakte mail) of erna (Strict zonder ondersteunde bron, tenzij de claims-check niets bedenkelijks ziet). Verduidelijken is een instructie aan het antwoordmodel bij A20; of het model daarna echt een vraag stelt, wordt niet vastgelegd. Een mens aanbieden bestaat niet.
- *Pad B.* Verduidelijken via de vaste vraagstap (B12, vóór het schrijven) of als een bronloos concept toevallig op een vraagteken eindigt terwijl de vraag onduidelijk heette (B18). Weigeren als de keuzestap zegt dat het er niet in staat, of bij geen bron plus onbewezen uitspraken. Bij zwakke bronnen eerlijk "staat er niet" (B13). Afspraakknop bij weigering, deelantwoord, escalatie en buiten het onderwerp.

**Wordt de zekerheidsband gebruikt?**
- *Pad A: ja*, voor verduidelijken (A20), de Strict-weigering bij geplakte mail (A21), de upgrade naar `klai-medium` (A24) en een lagere bronlat (A27). De gebruiker ziet de band in de voettekst (A31).
- *Pad B: nee.* Opgeslagen, niet gebruikt. Zwakke resultaten worden herkend met "alle rerankerscores < 0,4" (B8, B13).

**Meerdere vragen in één bericht**
- *Pad A*: deelvragen krijgen een eigen zoekpass, de rest verschijnt als "niet doorzocht"; zet de per-vraag-instructie in de prompt, schakelt verduidelijken uit en geeft in Strict een upgrade naar `klai-medium`.
- *Pad B*: sinds #1663 wordt een bericht met meerdere vragen ook hier in deelvragen gesplitst, elk met een eigen zoekpass; zo'n bericht krijgt geen parafrasen. Het effect op antwoorden van de widget is niet gemeten.

**Modelkeuze**: de router van A24, ook op pad B. Geen van de regels is tegen antwoordkwaliteit gemeten.

**Onbewezen uitspraken**: pad A Strict controleert en repareert en weigert nooit op dit signaal; pad A Open doet niets; pad B support kiest vóór het schrijven wat de schrijver leest, en de controle achteraf meet alleen.

**Wat de eerste vraag extra krijgt**: pad A niets (herschrijven draait altijd); pad B twee parafrasen, vervolgvragen krijgen coreferentie.

---

## 6. Wat er vandaag gemeten wordt

**Logregels van pad A** (`service:litellm` in VictoriaLogs). Alleen **warning en hoger** komt aan (`klai_kb_citation_render.py:1016-1022`). Zichtbaar zijn onder meer `query_rewrite*`, `kb_clarify_decision`, `low_confidence_injection_applied`, `kb_answer_claims`, `kb_answer_grounding`, `kb_answer_repaired`, `kb_citations_*`, `chat_synthesis_complete`, `llm_safety_litellm_*`. **Niet zichtbaar** (info): de routerbeslissing (`custom_router.py:272`), taxonomie, meta-vragen, titelverzoeken en de Strict-weigeringen zonder kennisbank. Welk model een interne beurt kreeg, is daardoor niet terug te vinden.

**Logregels van pad B** (`service:portal-api`, structlog, info komt wel aan): `partner_chat_turn_judge`, `partner_chat_answer_judge`, `partner_chat_query_paraphrase`, `partner_chat_answer_repair`, `clarify_decision`, `partner_chat_turn_timing`, `answer_grounding_late`, `partner_chat_off_topic`.

**Database**
- `widget_messages.answer_signals` (per assistent-beurt van support-widgets): band, top_score (vóór boosts), gat-type, bronnen, broad_mode, taal, model, duidelijkheid, verdict, grounding, beslissing, aantal onbewezen uitspraken, gerepareerd, geplande vraag, zwakke bronnen. Pad A heeft hier geen tegenhanger.
- `portal_retrieval_gaps` voor beide paden; `conversation_quality_judgments` (nachtelijke LLM-beoordeling per widgetgesprek, `klai-judge`); `telemetry.query_shadow` (retrieval-api).
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
