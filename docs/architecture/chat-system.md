# Hoe de chat van Klai werkt

**Stand: 24 september 2026, commit `67e387304` op main; §2 (pad B) herschreven op 13 oktober 2026 naar de code na de sneden O1 tot en met O5 van het plan.** Alleen code en configuratie zijn als bron gebruikt. Specs en oudere architectuurdocumenten zijn behandeld als claims en waar nodig gecorrigeerd. Elke bewering heeft een anker `bestand:regel` (paden relatief aan de map die erbij staat).

Dit is de kaart van het chatpad. Lees hem vóór je een voorstel doet of code verandert in `deploy/litellm/`, `klai-portal/backend/app/services/partner_chat.py`, `retrieval_context.py`, `chat_prompting.py`, `chat_safety.py`, `chat_sources.py`, `chat_sse.py`, `openai_passthrough.py`, `widget_turn.py`, `widget_write.py`, `passage_selection.py`, `answer_*.py`, `turn_judge.py`, `query_paraphrase.py`, `klai-libs/chat-prompts/` of `klai-retrieval-api/retrieval_api/api/retrieve.py`, en werk hem bij in dezelfde PR als je een stap, een LLM-aanroep of een beslisregel verandert. Wat er onderzocht is en waarom het zo gebouwd is, staat in [chat-quality-history-and-plan.md](chat-quality-history-and-plan.md).

---

## 0. In één scherm

Sinds 13 oktober 2026 is er **één chatpad**: `POST /partner/v1/chat/completions` in portal-api, dat de helpwidget, de interne chat (LibreChat-medewerkers) en de partner-API bedient. Sinds 14 oktober loopt elke kennisbeurt die niet van de interne chat is door dezelfde drager, `widget_turn()`: elke widget en elke partner-key op het kennispad, zonder supportschakelaar en zonder generieke generatie ernaast (een partner-key met `general_chat` en een verzoek zonder kennisvelden blijft de algemene doorgeefroute, `canonical_chat_completions`). Het oude pad A (LibreChat → LiteLLM-hook) staat nog in de LiteLLM-container als terugweg, maar geen omgeving gebruikt het (§1).

| | Widget en partner-API | Interne chat (Strict, Open, general) |
|---|---|---|
| Drager | `widget_turn()` (§2.1) | `internal_turn()` op dezelfde stapfuncties (§2.2) |
| Lezen | de zaak per gesprek, opgeslagen bij de beurt (widget); per beurt zonder vorige (partner) | de zaak per beurt, zonder vorige |
| Zoeken | letterlijke zin + parafrasen (eerste beurt) of zaak-zoekvragen (vervolg), acht bronnen | letterlijke zin + zaak-zoekvragen, acht bronnen |
| Kiezen vóór schrijven | ja, met citaten die code terugzoekt | ja; Strict als poort, Open als opdracht |
| Routes in code | contact, buiten onderwerp, niet gevonden, de vraag | niet gevonden (Strict), de vraag |
| Na het schrijven | herkomstpoort, lichte beoordelaar voor de knop, controle per zin erna | Strict als de widget zonder knop; Open live met de controle erna |
| Mens aanbieden | afspraakknop | bestaat niet |
| Wat er per beurt bewaard wordt | `widget_messages.answer_signals` met het hele spoor (widget); alleen het retrievallog (partner, §2.3) | `internal_chat_turns`, alleen signalen |

Modelaliassen (`deploy/litellm/config.yaml`): `klai-primary` en `klai-fast` zijn allebei `mistral-small-2603` (`:7,29`), `klai-medium` is `mistral-medium-3.5` (`:70`), `klai-large` is `mistral-large-2512` (`:47`). `klai-judge` is hetzelfde model als `klai-medium`, maar op de Vibe-key van de Klai-organisatie (hetzelfde Vibe-tegoed als `klai-ingest`, zonder fallback), voor al het achtergrondwerk op Medium van portal-api: de nachtelijke judges, support-case-analyse en het groeperen van gaten (`settings.conversation_judge_model`). Medium-calls binnen een chatbeurt blijven op `klai-medium`. Bij een quotafout vallen primary en fast terug op medium (`:114`). LiteLLM-timeout 120 s met 1 retry (`:122,125`).

---

## 1. Pad A: interne chat (LibreChat → LiteLLM), sinds 13 oktober niet meer in gebruik

**Op 13 oktober 2026 is elke tenant op het portalpad gezet** (`scripts/switch_internal_chat.py <slug> portal`): het endpoint "Klai AI" van elke LibreChat wijst naar `portal-api:8010/partner/v1`, dus elk LibreChat-gesprek loopt door §2.2. De hook hieronder is de terugweg (`switch_internal_chat.py <slug> litellm`) tot het portalpad zich op echt verkeer heeft bewezen; daarna gaat hij uit de LiteLLM-container. De beschrijving blijft staan tot dan.

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

## 2. Pad B: widget, interne chat en partner-API (portal-api)

**Herschreven op 13 oktober 2026, na de sneden O1 tot en met O5 van het plan (§7 en §8).** Ingang: `POST /partner/v1/chat/completions`, functie `chat_completions` (`klai-portal/backend/app/api/partner.py`). Die functie lost het verzoek op en kiest de drager: `internal_turn()` voor de interne chat (LibreChat-medewerkers via de portal) en `widget_turn()` voor al het andere, dus elke widget en elke partner-key. De twee dragers lopen de stroom van het plan (§5) in dezelfde volgorde op dezelfde stapfuncties en geven een `Reply` terug; één schrijfstap en één uitzender maken daar het antwoord van. De rijen hieronder volgen die volgorde; de kolom "Bij falen" zegt wat een uitgevallen modelaanroep doet.

### 2.1 De widget en de partner-API (`services/widget_turn.py`)

| # | Stap | Beslist op basis van | LLM | Bij falen | Vastgelegd als |
|---|---|---|---|---|---|
| W0 | **Ingang** (`chat_completions`): rechten, model en berichtvorm, invoerveiligheid, KB-scope fail-closed (`_partner_kb_scope`), widgetinstellingen (eigen instructie, paginacontext, onderwerpen buiten de widget, toon), `tools`, `tool_choice` en `web_search` geweigerd met een 400, de user-beurt naar `widget_messages`, de vorige zaak (`last_case` en `stored_case_for`, alleen op een vervolgbeurt en alleen als de opgeslagen beurt het laatste antwoord van dit gesprek is) | key/widget, instellingen | nee | fail-closed op scope; zonder zaak loopt de beurt | `widget_messages` (user-beurt) |
| W1 | **Lezen: de zaak** (`case_file.update_case`): probleem, product, variant, geprobeerd, wat de bezoeker nu wil, of het onderwerp buiten de widget valt, toon, twee zoekvragen. Op een vervolgbeurt vóór het zoeken (de zoekvragen gaan mee), op een eerste beurt ernaast | gesprek + vorige zaak + ingestelde onderwerpen | `klai-fast`, 3 s | beurt loopt als duidelijke kennisvraag zonder escalatie (`judge_failed: ["case"]`) | `answer_signals.case`, `partner_chat_turn_judge` |
| W2 | **Route in code: contact.** Wil de bezoeker een mens, contact of een afspraak, dan is `contact_reply` de hele reactie, met de knop en zonder schrijver (en met het e-mailadres en telefoonnummer dat de widget heeft ingesteld, als die er zijn); op een vervolgbeurt vóór het zoeken, op een eerste beurt erna. Wint van de doorverwijzing (W5) | zaak | nee | – | `partner_chat_contact_request`, `decision=human_request` |
| W3 | **Zoeken** (`retrieve_context` in `services/retrieval_context.py` → `/retrieve`, §3): de letterlijke zin als hoofdvraag, `top_k=8`; op een eerste beurt twee parafrasen als extra zoekgangen (`query_paraphrase`), op een vervolgbeurt de twee zoekvragen van de zaak; een bron per passage (`max_sources`, sinds 13 oktober, stap J: de zoekdienst levert één passage per sectie en het bewijspakket houdt tot acht bronnen in plaats van drie). Chunk-veiligheid; de zekerheidsband gaat alleen naar de opslag; gat-klasse *soft* (alle scores < 0,4) of *hard* (nul chunks) | vraag, zaak | parafrasen `klai-medium`, 2,5 s | zonder parafrasen | `answer_signals.search` (zoekvraag, zoekgangen, gevonden passages met titel, url, score), `band`, `gap_type` |
| W4 | **Kiezen** (`passage_selection.select_passages`, in `retrieve_context`): het model schrijft de behoefte, per passage de letterlijke zin die antwoordt, dan het oordeel (beantwoordt, hangt af van een variant, staat er niet in) en bij een variant de ene vraag. Code zoekt elk citaat terug (80% van de woorden): eerst in de aangewezen passage, anders in de best passende sectie van hetzelfde artikel (zelfde bron-url), nooit in een ander artikel; een citaat van minstens vijf woorden dat als één zin in een passage staat weegt zwaarder dan het oordeelwoord "staat er niet in". Alleen passages met een teruggevonden zin gaan naar de schrijver, zonder dubbele tekst, met de opdracht die de behoefte en die zinnen noemt (`writer_brief`). Niet bij brede modus of een vraag met meerdere delen | gesprek + passages | `klai-medium`, 4 s | beurt loopt op alle passages (de controle in W6 loopt dan op het pad) | `answer_signals.selection` (oordeel, citaten, vraag), `passage_selection` |
| W5 | **Routes in code** (`_route`), in deze volgorde: escalatie (regex `escalation_intent`; zaak: mens/contact/afspraak → `HUMAN_REQUEST`, negatieve toon → `FRUSTRATION`); gespreksklasse (`wants == "chat"` bij een gat → gespreksaddendum voor de schrijver); **buiten het onderwerp** (`out_of_scope` van de zaak en geen contactverzoek → `off_topic_referral`, een zin van ons met het onderwerp of de ingestelde tekst, met knop, zonder schrijver); **contact** op een eerste beurt (W2); **staat er niet in** (`turn.not_in_passages`, niet als om een mens is gevraagd → vaste "niet gevonden" met knop, zonder schrijver); **de vraag** (`question_reply`: oordeel "hangt af van", gekozen passages, één regel op een vraagteken, uitvoerveiligheid, niet eerder gevraagd in dit gesprek → de vraag van de keuzestap is de hele reactie, zonder knop). Brede modus alleen met toestemming van de bezoeker en bij nul chunks: antwoord uit algemene kennis, gelabeld | zaak, keuze, regex | doorverwijzing `klai-fast`, 2,5 s | doorverwijzing: ingestelde tekst | `decision` (`off_topic`, `refusal`, `clarifying_question`), `partner_chat_not_in_passages`, `clarify_decision`, `planned_question` |
| W6 | **Schrijven** (`widget_write.write_reply`): één modelaanroep met het profiel, de gekozen passages, de opdracht en de addenda (escalatie, gesprek); de bronnencomposer (bronnen op documentniveau, link- en markerfilter met de urls uit de passages als toegestaan); de **herkomstpoort** (`without_unsourced_contact_details` in `services/chat_safety.py`: een zin met een telefoonnummer of e-mailadres dat niet in de gekozen passages staat vervalt); uitvoerveiligheid; de **lichte beoordelaar** (`judge_answer` → `decide_answer`: met bron altijd tonen, niet volledig beantwoord → deelantwoord met knop; zonder bron beslissen de uitspraken); de **controle per zin** (`check_grounding`): zijn de passages gekozen en geciteerd, dan ná het antwoord (`_check_answer_later`), anders op het pad; het ene vangnet: niets onderbouwd en de passages niet gekozen → de weigering; de afspraakzin in code waar de knop hangt; de tijd per stap | concept + passages + gesprek | schrijver `klai-primary`, 120 s; beoordelaar `klai-fast`, 2,5 s; controle `klai-medium`, 4 s (achteraf 12 s) | schrijver uitgevallen: 502 of foutframes; beoordelaar uitgevallen: met bron tonen, zonder bron weigeren; controle uitgevallen: antwoord ongewijzigd | `partner_chat_answer_judge`, `partner_chat_turn_timing`, `answer_signals.decision`, `.grounding`, `.unsupported`, `.unsupported_statements`, `.appointment` |
| W7 | **Uitzenden** (`widget_reply.reply_body` of `reply_frames`): dezelfde `Reply` als JSON-body of als frames (taal, brede modus, escalatie, activiteit, bronnen, tekst, `[DONE]`); een vaste reactie en een geschreven reactie gaan door dezelfde uitzender; het hele antwoord is af vóór de eerste byte | `Reply` | nee | – | – |
| W8 | **Opslag** (`_write_widget_record`): de assistent-beurt direct uit de `Reply` zelf, met tekst, bronnen en `answer_signals`; de schrijftaak start vóór het eerste frame en loopt naast het verzenden (de frames worden niet meer teruggelezen, dus een antwoord dat de bezoeker niet of half ontving staat toch volledig in het transcript, want het is wel gegeven), daarna `patch_answer_signals` op dezelfde rij-id zodra de controle erna klaar is; het zoeklogboek (`log_retrieval`) wordt vóór de routes geschreven, zodat feedback op een vraag of weigering deze beurt vindt | – | nee | record zonder de late signalen | `widget_messages.answer_signals` |

### 2.2 De interne chat (`services/internal_turn.py`)

Dezelfde stappen op dezelfde functies, met deze verschillen. **W1:** de zaak leest elke beurt zonder vorige zaak (LibreChat stuurt het hele gesprek mee) en zonder ingestelde onderwerpen; in de general-modus geen zaak. **W2 en W5:** geen contactroute en geen doorverwijzing (er is geen knop); **staat er niet in** geldt alleen in Strict en geeft de vaste Strict-weigering met de suggestie van de open modus; de vraag van de keuzestap is de reactie in Strict én Open. **W3:** geen parafrasen; een vervolgbeurt zoekt op de letterlijke zin plus de zaak-zoekvragen (de herschrijving `query_rewrite` is weg); een geplakte mail op een eerste beurt laat de zaak vóór het zoeken lopen. **W4:** de keuzestap draait op elke beurt met passages; in Open en general kiest ze de passages en schrijft de opdracht maar weigert nooit (zonder keuze krijgt de schrijver alle passages); niet bij meegestuurde inhoud of geplakte correspondentie. **W6:** Strict schrijft via `write_reply` (vastgehouden tot geschreven en beoordeeld, zonder knop, met de Strict-weigeringstekst, de voettekst met bronnen en activiteit, webzoektools gestript); Open en general streamen live (`chat_completion_streaming`) met de controle erna. **W8:** `record_internal_turn` bewaart alleen signalen, nooit tekst, geen citaten en geen zaaktekst.

### 2.3 De partner-key

Een partner-key loopt dezelfde stappen als de widget in §2.1, met deze verschillen, die er allemaal uit volgen dat er geen widget is. **W0:** geen widgetinstellingen (geen eigen instructie, paginacontext, onderwerpen buiten de widget of toonkeuze, dus het ingetogen supportprofiel, tenzij de partner een eigen systeembericht meestuurt, dat het profiel vervangt), geen user-beurt naar `widget_messages` en geen vorige zaak, zodat de zaak elke beurt zonder vorige leest; een pdf in de laatste beurt wordt wel tekst, omdat de key een geauthenticeerde integratie is en geen anonieme bezoeker. **W8:** geen assistent-beurt in `widget_messages`; alleen het retrievallog voor feedback blijft, net als bij de widget. `knowledge.query`, `knowledge.top_k`, `knowledge.include_sources`, `knowledge_base_ids` en `broad_mode` werken zoals bij de widget; `model` en `temperature` gaan naar de schrijver zoals bij de widget; `web_search` en `tools` geven een 400, omdat de beurt alleen de helpartikelen leest. De generieke generatie die partner-keys en widgets zonder supportschakelaar tot 14 oktober kregen, is weg (plan §8, "Eén pad voor partners"); `chat_completion_streaming` en `chat_completion_non_streaming` bedienen alleen nog de live leg van de interne chat (§2.2).

## 3. De zoekdienst `/retrieve` (klai-retrieval-api)

- **Coreferentie** draait alleen als de aanroeper het niet zelf deed (`klai-retrieval-api/retrieval_api/api/retrieve.py:138-154`), met `klai-fast` en 3 s (`retrieval_api/config.py:38-39`). Pad A doet het zelf in A11; pad B niet, dus elke widget-vervolgvraag met geschiedenis krijgt deze aanroep.
- **Zoeken**: dense + sparse embeddings, hybride RRF in Qdrant met een letterlijke leg als de vraag herschreven is (`retrieve.py:700-712`), graph-search (Graphiti, fail-open) en link-expansie; dan herrangschikken (Infinity, fail-open naar Qdrant-volgorde), kwaliteitsvloer, maximaal 2 chunks per bron. Varianten (pad B) krijgen elk een eigen pass en worden na het herrangschikken samengevoegd; deelvragen (pad A) krijgen elk een eigen retrieve.
- **Zekerheidsband** (`retrieval_api/api/ranking.py:20-64`): de hoogste `final_rank_score` over de geserveerde chunks, alleen als er een rerankerscore is. ≥ 0,60 `high`, < 0,30 `low`, daartussen `medium`, anders `unknown` (`retrieval_api/config.py:84-91`). Eén berekening voor beide paden (`retrieve.py:1250`). `answer_signals.top_score` op pad B is de score vóór de boosts en dus níet de score waarop de band beslist.
- **Evidence pack**: maximaal 3 bronnen, of 5 bij meerdere kennisbanken (`retrieve.py:1264-1268`); beide paden bouwen hun context daaruit.
- **Telemetrie**: bij niveau `shadow`/`full` een rij in `telemetry.query_shadow` met band, chunk-ids en rerankerscore, zonder vraagtekst (`retrieve.py:1338-1354`).

---

## 4. Alle LLM-aanroepen per beurt

**Pad A** (in volgorde): herschrijven (`klai-fast`, 1,5 s, A11) → coreferentie in `/retrieve` alleen als A11 om infrastructuurredenen oversloeg (`klai-fast`, 3 s) → generatie (primary, via de router naar large, medium of fast) → claims-check (`klai-fast`, 4 s, alleen Strict bij dreigende weigering) → grounding-check (`klai-medium`, 12 s, alleen Strict) → reparatie (`klai-medium`, 8 s, alleen boven de drempel). **Open-modus: alleen herschrijven en generatie.**

**Pad B, widget en partner-API** (in volgorde): de zaak (`klai-fast`, 3 s; op een eerste beurt ∥ het zoeken) → parafrasen op een eerste beurt (`klai-medium`, 2,5 s) → de keuzestap (`klai-medium`, 4 s) → de schrijver (`klai-primary`) → de lichte beoordelaar (`klai-fast`, 2,5 s); de controle per zin (`klai-medium`) loopt ná het antwoord als de passages gekozen en geciteerd zijn, anders op het pad; een doorverwijzing buiten het onderwerp kost één aanroep extra (`klai-fast`, 2,5 s) en geen schrijver. Vier aanroepen op het pad van een geschreven vervolgbeurt, vijf op een eerste beurt, plus één meting erna. **Intern:** hetzelfde zonder parafrasen; Strict wordt vastgehouden, Open en general streamen live met de controle erna.

---

## 5. De beslissingen waar het om draait

**Antwoorden, weigeren, verduidelijken of een mens aanbieden**
- *Pad A.* Weigeren gebeurt vóór het model (instellingen onbereikbaar, veiligheid, en in Strict: geen kennisbank, retrieval faalt, nul chunks, lage zekerheid bij geplakte mail) of erna (Strict zonder ondersteunde bron, tenzij de claims-check niets bedenkelijks ziet). Verduidelijken is een instructie aan het antwoordmodel bij A20; of het model daarna echt een vraag stelt, wordt niet vastgelegd. Een mens aanbieden bestaat niet.
- *Pad B.* Verduidelijken via de vraag van de keuzestap (W5, vóór het schrijven), op de widget en intern. Weigeren als de keuzestap zegt dat het er niet in staat (widget en intern Strict), of bij geen bron plus onbewezen uitspraken. Intern bij zwakke bronnen eerlijk "staat er niet" (B13). Afspraakknop bij weigering, deelantwoord, escalatie en buiten het onderwerp.

**Wordt de zekerheidsband gebruikt?**
- *Pad A: ja*, voor verduidelijken (A20), de Strict-weigering bij geplakte mail (A21), de upgrade naar `klai-medium` (A24) en een lagere bronlat (A27). De gebruiker ziet de band in de voettekst (A31).
- *Pad B: nee.* Opgeslagen, niet gebruikt. Zwakke resultaten worden herkend met "alle rerankerscores < 0,4" (W3); sinds oktober beslist de keuzestap (W4) of een passage antwoordt, niet de score.

**Meerdere vragen in één bericht**
- *Pad A*: deelvragen krijgen een eigen zoekpass, de rest verschijnt als "niet doorzocht"; zet de per-vraag-instructie in de prompt, schakelt verduidelijken uit en geeft in Strict een upgrade naar `klai-medium`.
- *Pad B*: sinds #1663 wordt een bericht met meerdere vragen ook hier in deelvragen gesplitst, elk met een eigen zoekpass; zo'n bericht krijgt geen parafrasen. Het effect op antwoorden van de widget is niet gemeten.

**Modelkeuze**: de router van A24, ook op pad B. Geen van de regels is tegen antwoordkwaliteit gemeten.

**Onbewezen uitspraken**: pad A Strict controleert en repareert en weigert nooit op dit signaal; pad A Open doet niets; pad B (widget, partner-API en interne chat) kiest vóór het schrijven wat de schrijver leest, en de controle achteraf meet alleen.

**Wat de eerste vraag extra krijgt**: pad A niets (herschrijven draait altijd); pad B twee parafrasen, vervolgvragen krijgen coreferentie.

---

## 6. Wat er vandaag gemeten wordt

**Logregels van pad A** (`service:litellm` in VictoriaLogs). Alleen **warning en hoger** komt aan (`klai_kb_citation_render.py:1016-1022`). Zichtbaar zijn onder meer `query_rewrite*`, `kb_clarify_decision`, `low_confidence_injection_applied`, `kb_answer_claims`, `kb_answer_grounding`, `kb_answer_repaired`, `kb_citations_*`, `chat_synthesis_complete`, `llm_safety_litellm_*`. **Niet zichtbaar** (info): de routerbeslissing (`custom_router.py:272`), taxonomie, meta-vragen, titelverzoeken en de Strict-weigeringen zonder kennisbank. Welk model een interne beurt kreeg, is daardoor niet terug te vinden.

**Logregels van pad B** (`service:portal-api`, structlog, info komt wel aan): `partner_chat_turn_judge` (velden uit de zaak), `partner_chat_answer_judge`, `partner_chat_query_paraphrase`, `case_update_failed`, `passage_selection`, `partner_chat_contact_request`, `partner_chat_not_in_passages`, `clarify_decision`, `partner_chat_turn_timing`, `answer_grounding_late`, `partner_chat_off_topic`.

**Database**
- `widget_messages.answer_signals` (per assistent-beurt van elke widget): de zaak (`case`), de zoekvraag en zoekgangen met de gevonden passages (`search`), het oordeel van de keuzestap met citaten en vraag (`selection`), band, top_score, gat-type, bronnen, broad_mode, taal, model, verdict en grounding van de controle, beslissing, aantal en tekst van onbewezen uitspraken, geplande vraag, afspraakknop, tijd per stap (`timings`). De interne chat bewaart in `internal_chat_turns` dezelfde signalen zonder tekst (geen citaten, geen zaaktekst).
- `portal_retrieval_gaps` voor beide paden; `conversation_quality_judgments` (nachtelijke LLM-beoordeling per widgetgesprek, `klai-judge`); `telemetry.query_shadow` (retrieval-api).
- `knowledge.rag_eval_results`: de nachtelijke RAGAS-run in knowledge-ingest. Die **genereert zijn eigen antwoord met `klai-fast`** en meet dus geen van beide chatpaden (`klai-knowledge-ingest/knowledge_ingest/eval/judge_client.py:4-20`).

**Operatorscripts** in `klai-portal/backend/scripts/`: `grounding_report.py` (widgetgrounding per dag), `calibrate_confidence_bands.py` (band tegen uitkomst), `simulate_conversations.py` (gesimuleerde bezoeker), `export_librechat_messages.py`. De metingen van het logboek (herspelen met een gesimuleerde bezoeker, blind beoordeeld op de volgende beurt) draaien met gereedschap buiten de repo.

---

## 7. Geprobeerd en afgewezen

Voorstellen die al gemeten zijn. Stel ze niet opnieuw voor zonder te zeggen wat er nu anders is. Details en meetmethode: [chat-quality-history-and-plan.md](chat-quality-history-and-plan.md).

| Idee | Wanneer | Waarom afgewezen |
|---|---|---|
| Antwoordmodel in dezelfde generatie laten doorvragen | 17 sep, 18 sep, 22 sep | Het model vraagt dan bijna nooit (2 op 150; 7 op 32; 4 op 12); een aparte stap (W5, de vraag van de keuzestap) werkt wel |
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
