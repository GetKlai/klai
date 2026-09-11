---
id: SPEC-CHAT-QUALITY-LOOP-001
version: "0.4.0"
status: built (REQ-1 t/m REQ-5), not yet deployed — gates groen, diffs zelf
  gelezen, post-deploy SQL nog niet tegen een echte database gedraaid. Webchat
  volledig; LibreChat als Voys-only pilot achter een feature-vlag die nog
  niemand heeft aangezet (§9.1's cross-tenant zorg is hiermee vermeden, niet
  opgelost — een tweede tenant vraagt om dezelfde soort afweging opnieuw).
created: 2026-09-11
updated: 2026-09-11
author: Claude (Sonnet 5), commissioned by Mark Vletter
priority: high
tenant_scope: platform-wide — both chat channels, every tenant
related:
  - SPEC-VOYS-HELPBOT-001 (REQ-1 gap events this SPEC reuses, REQ-5 outcome
    heuristic this SPEC replaces/enriches — read that SPEC's §3 REQ-5 and §7
    Open before touching widget_outcome.py)
  - docs/research/help-page-chatbot-voys.md (§6.2 KPI research, §8 gap
    analysis — G-6 "geen eval-harness voor path B" is the gap this SPEC
    closes, G-2 and G-13 are referenced but out of scope here)
  - klai-knowledge-ingest/knowledge_ingest/eval/ (judge_client.py, RAGAS
    harness — sibling system: synthetic query suites, not live conversations;
    this SPEC does not replace it)
  - app/api/app_gaps.py, PortalRetrievalGap (existing knowledge-gap detection
    — already spans both channels; this SPEC's output sits next to it, does
    not rebuild it)
---

# HISTORY

| Versie | Datum | Wijziging |
|---|---|---|
| 0.4.0 | 2026-09-11 | REQ-5 (LibreChat, Voys-only pilot) gebouwd en geverifieerd. Schema-uitbreiding en feature-vlag zelf gedaan (echte productie-RLS-tabel + eerste keer dat dit een klant's eigen interne chatdata raakt); Mongo-leeslogica en de aparte interne-gebruiker-rubric naar Qwen (klasse L, zelfde reden als REQ-2). Berichtenschema niet gegist maar opgehaald van de echte, gepinde LibreChat-broncode (v0.8.7) op GitHub. Twee eigen fouten gevonden en gefixt bij verificatie: een kopieerfout in de SPEC (overtollige `"""`) en de daarvan afgeleide pyright-fouten in Qwen's testbestand (mock-params zonder None-narrowing) — geen van beide een Qwen-fout, beide mijn eigen nasleep. Volledige backend-suite: 4036 passed, `uv run --with pyright pyright` (de echte CI-gate) 0 errors. |
| 0.3.0 | 2026-09-11 | REQ-2, REQ-3, REQ-4 gebouwd en zelf geverifieerd (diff gelezen, gates herdraaid, niet op Qwen's rapport vertrouwd). REQ-2 moest herclassificeren naar klasse L — Qwen weigerde terecht een eerste M-poging (verbatim-promptregel + volledige widget_outcome-mirror past objectief niet in 200 regels); herissue met Mark's staande "GO" als expliciete L-autorisatie loste dit op. Volledige backend-suite: 4028 passed (was 4012 vóór deze sessie). |
| 0.2.0 | 2026-09-11 | Mark gaf expliciet "GO" om het hele plan te implementeren. Scope vastgezet op webchat-only (LibreChat blijft §9.1's open spike — niet gebouwd). Retentie-besluit (§6, §9.2) en de kennisgat-zichtbaarheidsopmerking (§9.5) waren al verwerkt. REQ-1 t/m REQ-4 hieronder toegevoegd en REQ-1 gebouwd. |
| 0.1.0 | 2026-09-11 | Eerste plan, alleen onderzoek + open vragen, niets gebouwd. |

# 0. Wat mij hiertoe bracht

Mark vroeg om een self-learning loop op de webchat (voorbeeld: gesprek #255,
Voys-tenant), onderzoek naar hoe anderen dat bouwen, en een compleet plan
voordat er iets gebouwd wordt. Twee kleinere, losstaande stukken zijn al apart
belegd bij Qwen build-agents (zie § 10) omdat Mark daar expliciet groen licht
voor gaf. Dit document is het "heel goede en complete plan" voor de rest: de
nachtelijke kwaliteitsbeoordeling die gesprekken goed categoriseert én weet
waarom, over alle chatkanalen, en de stap naar zwaardere Mistral-modellen
die de huidige handmatige heuristiek vervangen. Niets in dit document is
gebouwd. Het is een leesstuk.

# 1. Waarom

`widget_conversations.outcome` bestaat al (`resolved|escalated|abandoned|
unknown`), maar wordt afgeleid door een pure heuristiek
(`app/services/widget_outcome.py`) zonder enige onderbouwing — geen
"waarom". De module zelf, en SPEC-VOYS-HELPBOT-001 REQ-5, zeggen het met
zoveel woorden: het grote aandeel `unknown` *is* het argument voor een
LLM-as-judge pass, niet iets om met een gok te vullen. Die pass bestaat nog
niet. Het onderliggende gat staat al genoteerd in het Voys-onderzoek als
**G-6, hoog prioriteit, nog open**: "Geen eval-harness voor path B (widget) —
alle kwaliteitsmetingen gaan via pad A."

Kennisgat-detectie (retrieval vond niets/te zwak) bestaat wél al, en dekt
beide kanalen: `app_gaps.py` / `PortalRetrievalGap`, gevoed door zowel de
LiteLLM-hook op het interne LibreChat-pad als — sinds REQ-1 — het
widget-pad. Dit plan bouwt dat niet opnieuw; het output van deze nieuwe
judge-pass komt naast de gap-rijen te staan op hetzelfde soort
admin-oppervlak, niet in plaats ervan.

Wat wél ontbreekt, op geen van beide kanalen: een oordeel over een heel
gesprek (niet per losse retrieval-query) met een categorie én een
onderbouwing die een latere Claude Code-sessie (of een mens) kan gebruiken
om gericht door te vragen — "waarom scoorden gesprekken over onderwerp X
deze week slecht?".

# 2. Scope

**In scope:** een nachtelijke LLM-as-judge pass over echte gesprekken op
**beide** live chatoppervlakken:

- **Webchat widget** — `widget_conversations`/`widget_messages` (Postgres),
  anonieme bezoekers, geen kennis van het product.
- **LibreChat** (`/app/chat`, "Klai AI") — `conversations`/`messages`
  (MongoDB, één database per tenant), ingelogde interne Klai-portal-
  gebruikers die weten hoe een kennissysteem werkt.

Dit levert per gesprek een gestructureerde categorie + onderbouwing op,
opgeslagen zodat een dashboard of een latere analyse-pass kan filteren op
"waarom faalde dit". Bewust **twee aparte rubrics/prompts** (§5) — één
pijplijn, want de populaties verschillen wezenlijk (naïeve anonieme bezoeker
vs. kennissysteem-vaardige interne gebruiker), precies het onderscheid dat
Mark zelf noemde.

**Expliciet buiten scope** (benoemd, niet gebouwd):

| Niet nu | Waarom |
|---|---|
| Model fine-tunen/RLHF op deze feedback | Zwaar, past niet bij een RAG-architectuur; zie §8 — de hefboom hier is kennis-curatie, niet gewichten-updates. Onderbouwd met actueel extern onderzoek, zie bronnenlijst onderaan. |
| Automatisch prompts/retrieval bijsturen op basis van judge-output | Toekomstwerk; heeft pas zin als de judge een tijdje meetgeschiedenis heeft opgebouwd. |
| Kennisgat-detectie opnieuw bouwen | Bestaat al (`app_gaps.py`). |
| De losse, kleinere G-6-suggestie uit het Voys-onderzoek ("draai de 41 synthetische chat.yaml-queries ook wekelijks op pad B") | Goedkoop en orthogonaal, mag parallel — maar beantwoordt niet "waarom faalde DIT echte gesprek", dus lost het probleem van dit plan niet op. |
| LibreChat's Mongo vervangen door Postgres als bron van waarheid | De judge-uitkomst leeft in Postgres en verwijst losjes naar het Mongo-gespreks-id, zoals `portal_feedback_events.conversation_id` dat vandaag al doet voor LibreChat-berichten. |
| Cross-tenant Klai-admin gespreksbrowser | Dat is een apart, kleiner stuk (Fase 1), al bij Qwen belegd — zie §10. |

# 3. Huidige staat (geverifieerd in de bron, 11 sep 2026)

| Onderdeel | Bestand | Wat het doet | Beperking |
|---|---|---|---|
| Outcome-heuristiek | `app/services/widget_outcome.py` | Regel-gebaseerd: `escalated` (handoff-rij of letterlijke weigeringstekst), `abandoned` (laatste beurt is user + stil na quiet-period), `resolved` (alléén expliciete thumbs-up), rest `unknown`. 15-minuten achtergrondlus, alleen widget. | Geen vrije-tekst-analyse, geen reden, alleen widget-kanaal. Eigen docstring noemt dit expliciet als tussenstap richting een LLM-judge. |
| Kennisgat-detectie | `app/api/app_gaps.py`, `PortalRetrievalGap` | Registreert per zwakke/mislukte retrieval-query een gap-rij, met taxonomie-classificatie. Voedt al een dashboard. Al actief op beide paden (LibreChat-hook + widget sinds REQ-1). Detectie zelf is een doorlopende lus, geen tijdelijke functie. | Per query, niet per gesprek; zegt niets over generatie-fouten (juist opgehaald, verkeerd antwoord). Een dagelijkse privacy-purge (`app/services/telemetry_purge.py`, SPEC-PRIVACY-QUERY-SHADOW-001) verwijdert na 7 dagen ALLEEN de rijen waarvan `query_text` nog ruwe/legacy tekst bevat; rijen met een `[REDACTED:...]`-sentinel blijven staan tot het gat is opgelost. Niet "gap-detectie werkt 7 dagen" — de ruwe queryvraag verdwijnt na 7 dagen bij tenants zonder volledige telemetry-toestemming, het gat zelf niet per se. |
| Bestaand judge-precedent | `klai-knowledge-ingest/knowledge_ingest/eval/judge_client.py` | RAGAS-gebaseerd, draait op synthetische query/chunk/antwoord-sets uit `eval/suites/*.yaml`, niet op live gesprekken. Faithfulness draait bewust op `klai-medium` omdat `klai-fast` (Small) meerdere-uitspraken-JSON afkapt. | Ander doel (regressie-detectie op vaste suites), geen categorisatie-met-reden op een heel gesprek. |
| Feedback op berichtniveau | `widget_messages.rating` (thumbsUp/thumbsDown) | Bestaat al, wordt in Fase 0 (§10) pas zichtbaar in de admin-UI. | Geen reden-veld, alleen widget. |
| Modeltiers (LiteLLM) | `deploy/litellm/config.yaml` | `klai-fast`/`klai-primary` = Mistral Small 2603, `klai-medium` = Mistral Medium 3.5, `klai-large` = Mistral Large 2512. Naamgevingsregel: tier-benoemd, nooit rol-benoemd (geen `klai-eval-judge`-alias toevoegen). | — |

# 4. Welke data nodig is (de kernvraag van Mark)

**Judge-input per gesprek** — wat de judge te lezen krijgt, zodat hij niet
zelf uit platte tekst hoeft te raden wat al ergens anders vaststaat:

- Volledige transcript, chronologisch (user+assistant-beurten). Voor widget
  bestaat dit al; voor LibreChat is een Mongo→judge-input-adapter nodig
  (zie open vraag §9.1).
- Bestaande signalen als context, niet als iets om te herleiden: rating per
  assistant-beurt, of er al gap-events vuurden voor dit gesprek (join op
  `app_gaps`), of er een handoff/escalatie plaatsvond, welke bronnen
  (`sources`) elk antwoord citeerde — of het ontbreken daarvan. Een
  citation-firewall-weigering (SPEC-VOYS-HELPBOT-001 §7 Open: antwoorden
  zonder bron worden vervangen door een vaste weigeringstekst) is een sterk,
  al bestaand signaal en hoort als input, niet als iets wat de judge zelf
  uit de tekst moet afleiden.
- Kanaal-discriminator (webchat / librechat) — bepaalt welke rubric/prompt
  draait (§5).

**Judge-output** — gestructureerd, geen vrije tekst, ontworpen op het
patroon uit actueel onderzoek naar LLM-as-judge-schema's (categorie waarvan
er precies één past, onderbouwing met een citaat uit het gesprek, een
confidence-band, en het onderscheid retrieval-fout vs. generatie-fout uit de
gangbare root-cause-taxonomie):

| Veld | Waarden | Doel |
|---|---|---|
| `outcome` | `resolved` \| `partially_resolved` \| `unresolved` \| `escalated` \| `out_of_scope` \| `abandoned_early` | Superset van de huidige 4 heuristiek-waarden, blijft uitputtend. Een 5e+ waarde op de bestaande `widget_conversations.outcome`-kolom vraagt een migratie op de CHECK-constraint — zie §6, vandaar een aparte tabel. |
| `failure_category` (alleen bij niet-resolved) | `retrieval_miss` \| `retrieval_wrong` \| `generation_error` \| `policy_refusal` \| `scope_mismatch` \| `user_confusion` \| `none` | Onderscheidt "niets gevonden" van "verkeerd gevonden" van "goed gevonden, fout antwoord" — precies het onderscheid dat de huidige heuristiek en de gap-events niet maken. |
| `reasoning` | 1-3 zinnen, verplicht een citaat/verwijzing naar een specifieke beurt | Voorkomt vage "klinkt slecht"-oordelen; dit is het veld waarmee Claude Code straks kan doorvragen. |
| `confidence` | `high` \| `medium` \| `low` | `low`/`medium` gaat in een steekproef (5-10%) naar menselijke review — vertrouw een judge nooit blind, dat is de expliciete aanbeveling uit het onderzoek. |
| `suggested_action` | vrije tekst, optioneel | Bv. "KB-gat: facturatie-artikel ontbreekt" — het veld dat een rij direct bruikbaar maakt voor een editorial-vervolgstap, kruislinks met de bestaande gap-taxonomie waar van toepassing. |

# 5. Twee rubrics, één pijplijn

| | Webchat | LibreChat |
|---|---|---|
| Publiek | Anoniem, geen productkennis | Ingelogd, kent het kennissysteem |
| "Resolved" betekent | Vraag beantwoord zonder mensinterventie, expliciete of impliciete tevredenheid | Vraag beantwoord tot bruikbaarheidsniveau van een collega die de tool al kent |
| Citation firewall | Actief (pad B) — een weigering zonder bron is een hard signaal | Niet op dezelfde manier actief (pad A) — ander beoordelingskader nodig |
| Escalatiepad | Handoff/booking (SPEC-VOYS-HELPBOT-001 §4) | Geen — interne gebruiker zoekt zelf verder of stelt een vervolgvraag |

Eén pijplijn (zelfde jobvorm, zelfde opslag, zelfde modeltier), twee
promptvarianten geselecteerd op kanaal — zoals `partner_chat.py` nu al
meerdere systeemprompt-profielen kiest op basis van widget-instellingen.

# 6. Pijplijn-ontwerp

- **Vorm:** volg het patroon van de bestaande `widget_outcome_loop()`
  (FastAPI-lifespan achtergrondlus, batches, per-tenant
  `tenant_scoped_session`) voor het widget-deel. Voor LibreChat is dit een
  nieuwe stap, want die data staat niet in Postgres — vermoedelijk beter als
  een aparte nachtelijke job in de stijl van de bestaande
  `klai-knowledge-ingest`-cron dan als een in-process FastAPI-lus, omdat dat
  al de plek is waar dit soort niet-realtime batchwerk draait. **Nog niet
  bevestigd — zie open vraag §9.1.**
- **Model:** bestaande `klai-medium`-tier (Mistral Medium 3.5) — geen nieuwe
  alias, want de naamgevingsregel in dit repo staat rol-benoemde aliases
  ("geen `klai-eval-judge`") expliciet niet toe. Dezelfde tier waar RAGAS'
  faithfulness-metric al naartoe verhuisde omdat `klai-fast` (Small)
  meerdere-uitspraken-JSON afkapt — zelfde risico geldt hier, dus zelfde
  keuze. `klai-large` (Mistral Large 2512) is de volgende stap omhoog als
  een kleine benchmark (§7) laat zien dat medium categorieën mist die een
  menselijke reviewer wel ziet.
- **Opslag:** nieuwe tabel, bv. `conversation_quality_judgments`
  (`channel`, `external_conversation_id`, `org_id`, `outcome`,
  `failure_category`, `reasoning`, `confidence`, `suggested_action`,
  `model_used`, `judged_at`) — een aparte tabel, niet de bestaande
  4-waarden-CHECK-kolom op `widget_conversations.outcome`, zodat de
  goedkope realtime heuristiek (§7) gewoon blijft werken als directe
  fallback en dit een verrijkende nachtelijke laag erbovenop is.
- **Retentie — Mark's keuze (11 sep 2026):** gesprekken zelf maximaal 7 dagen
  bewaren, de metadata over gespreksuccess (het judge-oordeel) langer, maar
  geanonimiseerd. Twee gevolgen, uitgewerkt:
  1. **Ruwe gesprekken korter bewaren is een bestaande, losse knop, geen
     nieuwe bouw.** `widget_messages`/`widget_conversations` hebben al een
     actieve purge-lus (`app/services/widget_messages_retention.py`,
     SPEC-SEC-CROSS-TENANT-FOLLOWUP-001 REQ-8), gestuurd door
     `settings.widget_messages_retention_days` (huidige default: **90**,
     env `PORTAL_API_WIDGET_MESSAGES_RETENTION_DAYS`). Naar 7 dagen zou een
     config-wijziging zijn, geen nieuwe code. **Dit is een aparte,
     productiedata-verwijderende beslissing die niet stilzwijgend in dit
     SPEC hoort** — het verkort nu al hoeveel geschiedenis Fase 1's
     cross-tenant gespreksbrowser (§10) kan tonen, en het Voys-onderzoek
     ging uit van wekelijkse handmatige review van transcripts, wat bij 7
     dagen krapper wordt. Aparte go/no-go vraag, zie §9.2.
  2. **De nieuwe `conversation_quality_judgments`-tabel moet dan
     TWEE trapsgewijze vormen hebben, niet één rij die voor altijd
     hetzelfde blijft:**
     - **Kort (≤7 dagen, gekoppeld aan het nog levende gesprek):** het volle
       schema uit §4, inclusief `reasoning` met een citaat uit het gesprek —
       dit is de vorm die Claude Code direct kan gebruiken om door te
       vragen.
     - **Lang (na het verlopen van de gesprek-retentie, geanonimiseerd):**
       zodra het onderliggende gesprek gepurged is, moet `reasoning` niet
       langer een citaat bevatten — een citaat uit een verwijderd gesprek is
       zelf weer identificeerbare inhoud. Wat overblijft: `outcome`,
       `failure_category`, `confidence`, een gegeneraliseerde (niet-
       citerende) samenvatting, `org_id`, `channel`, `judged_at`. Dit is het
       niveau waarop een trenddashboard ("categorie X neemt toe") nog werkt
       zonder een individueel gesprek te kunnen reconstrueren.
     Een derde optie — de detailrij gewoon nooit verwijderen maar op t=7d
     automatisch de citaten uit `reasoning` strippen (in plaats van twee
     aparte rijen) — is technisch eenvoudiger en waarschijnlijk de betere
     keuze; genoemd als ontwerprichting, niet als besluit.

# 7. Fase 3-visie: zwaardere Mistral-modellen vervangen de handmatige stap

Mark's vraag: kunnen we dit uiteindelijk op zwaardere Mistral-modellen bouwen
en daarmee "onze handmatige code stap" vervangen? Concreet is die
handmatige stap `widget_outcome.py` — de heuristiek noemt zichzelf expliciet
als kandidaat voor vervanging.

**Actuele Mistral-lijn (gecheckt via webonderzoek, sep 2026, niet uit
trainingsdata):** Mistral Large 3 (vlaggenschip voor redeneren/agentwerk,
open-weight MoE, uitgebracht 2 dec 2025, ≈$0,50/$1,50 per miljoen in/uit-
tokens) en Mistral Medium 3.5 (frontier-class, sterk in agent- en codewerk,
≈$1,50/$7,50/M). Klai draait beide al in productie als `klai-large`
(mistral-large-2512) en `klai-medium` (mistral-medium-3.5). "Zwaardere
Mistral-modellen" is dus geen nieuwe integratie — het is deze judge-calls
routeren naar een tier die al bestaat.

**Vervangingspad voor `widget_outcome.py`:** de goedkope heuristiek blijft
bestaan als instant/realtime label (het dashboard heeft een
zelfde-dag-cijfer nodig, en niet elk gesprek hoeft op een LLM te wachten);
de nachtelijke judge-pass overschrijft/verrijkt die met het echte oordeel
zodra beschikbaar — exact het patroon dat `klai-knowledge-ingest` al
gebruikt voor RAGAS' asynchrone naderhand-scoring.

# 8. Wat "self-learning loop" hier concreet betekent

Judge-uitkomst → aggregatie op een dashboard naast `app_gaps` → KB-auteur of
een Claude Code-sessie repareert de content → het volgende soortgelijke
gesprek scoort beter → lus gesloten. Expliciet **geen** model-finetuning
(§2): actueel extern onderzoek (Anthropic/arXiv-bronnen, Braintrust/Langfuse-
platformvergelijkingen, zie eerdere onderzoeksronde in deze sessie) bevestigt
dat producten met deze RAG-vorm meer waarde halen uit een kennis-curatie-
flywheel dan uit gewichten-updates — en dat matcht wat dit repo al doet:
`app_gaps.py` bestaat, een trainingspijplijn niet.

# 9. Open vragen voor Mark (vóór goedkeuring / vóór een klai:auto-run)

1. **LibreChat-toegang.** LibreChat's data staat in MongoDB, één database per
   tenant. Vandaag leest alleen `librechat_chat_context.py` daar iets uit, en
   dat is in-process, per ingelogde gebruiker, portal-geauthenticeerd. Een
   nachtelijke BATCH cross-tenant Mongo-read is een nieuw toegangspatroon dat
   nog niet bewezen veilig/afgeschermd is. Dit verdient een kleine spike
   vóórdat de LibreChat-helft van dit plan scope krijgt — voorstel: eerst
   alleen het webchat-deel bouwen, LibreChat als expliciete fase 2b na die
   spike.
2. **Retentie — deels beantwoord, één vraag terug.** Je koos: gesprekken max
   7 dagen, judge-metadata langer maar geanonimiseerd (uitgewerkt in §6).
   Nog open: mag `widget_messages_retention_days` (nu 90) daadwerkelijk naar
   7? Dat is een productiedata-verwijderende config-wijziging op een
   bestaand systeem, los van dit SPEC — bevestig je dat expliciet, en zo ja,
   geldt 7 dagen voor alle tenants direct, of eerst een pilot-tenant?
3. **Modeltier om mee te starten:** advies `klai-medium`, met een kleine
   benchmark tegen `klai-large` voordat we `klai-medium` als definitief
   aanmerken.
4. **Dit wordt een eigen SPEC** (`SPEC-CHAT-QUALITY-LOOP-001`) in plaats van
   een uitbreiding van `SPEC-VOYS-HELPBOT-001`, omdat de scope nu
   platform-breed + LibreChat is, niet Voys-pilot-specifiek. Akkoord?
5. **Mark's observatie (11 sep 2026):** de bestaande kennisgat-detectie
   (§3, `app_gaps.py`) zit vandaag niet goed genoeg in de workflow — hij
   ervaart het dashboard als verstopt in de UI. Genoteerd, expliciet niet
   nu oppakken ("wellicht voor later", zijn woorden) — geen scope-wijziging
   op dit SPEC. Wel relevant voor de volgorde: een judge-verdict dat naast
   een al-onzichtbaar gap-dashboard komt te staan lost het onderliggende
   "niemand kijkt ernaar"-probleem niet op. Als losse opvolgvraag, niet in
   dit plan: verdient het gap-dashboard eerst een zichtbaarheids-fix
   (bijvoorbeeld een plek in het platform-adminmenu, zie Fase 1 in §10)
   vóórdat dit SPEC een tweede, vergelijkbaar dashboard toevoegt?

Zodra je hierop antwoord geeft, schrijf ik de requirements (REQ-1, REQ-2, …)
met acceptatiecriteria uit en gaat dit pas dan naar een build-brief — precies
zoals SPEC-VOYS-HELPBOT-001 dat deed.

# 10. Wat al loopt (apart belegd, klein genoeg om niet op dit plan te wachten)

- **Fase 0** (klasse S): bestaande `widget_messages.rating` zichtbaar maken
  in de tenant-admin activity-drawer. Bij Qwen `build-s`, brief in
  `.context/briefs/fase0-widget-rating.txt`.
- **Fase 1** (klasse M): Klai-admin cross-tenant gespreksbrowser, als nieuwe
  tab op de bestaande `/admin/platform/orgs/$orgId`-detailpagina (hergebruikt
  het cross-tenant `require_platform_admin()`/`cross_org_session()`-patroon
  uit `app/api/admin/platform.py`). Bij Qwen `build-m`, brief in
  `.context/briefs/fase1-platform-conversations.txt`.

Beide zijn losstaand van dit plan en hoeven niet op goedkeuring hiervan te
wachten — ze maken bestaande data zichtbaar, ze bouwen geen nieuw
oordeel-systeem.

# 11. Requirements (webchat-only, na Mark's GO van 11 sep 2026)

Status legend: **done** = gebouwd, gates gedraaid, diff zelf gelezen;
**wip** = in uitvoering (Qwen build-agent bezig); **open** = nog niet gestart.

## REQ-1 — Tabel + model voor het judge-verdict · done

`conversation_quality_judgments`, Cat-D RLS, FK naar `widget_conversations`
(webchat-only scope, geen losse `channel`-tabel nodig zolang er maar één
kanaal is). Eén rij per gesprek (UNIQUE op `conversation_id`), met
`reasoning` als het veld dat door de anonimiseringssweep (REQ-4) wordt
geleegd zodra het onderliggende gesprek is gepurged.

- `alembic/versions/b7e4f1a9c3d2_conversation_quality_judgments.py` (no-op
  marker, zelfde reden als bij `widget_conversations`: portal_api heeft geen
  REFERENCES-recht op een klai-owned tabel).
- `alembic/versions/post_deploy_b7e4f1a9c3d2_conversation_quality_judgments_rls.sql`
  (echte DDL, Cat-D policy, mirror van
  `post_deploy_a4f72e913c8b_widget_conversations_rls.sql`).
- `app/models/conversation_quality.py` (SQLAlchemy-model).
- `app/core/rls_guard.py` — tabel toegevoegd aan `RLS_DML_TABLES`.
- `app/core/config.py` — nieuwe instelling `conversation_judge_model =
  "klai-medium"` (tier-benoemd, geen rol-alias, zelfde reden als RAGAS
  faithfulness al op klai-medium draait).

Gate: `uv run pytest tests/test_rls_hygiene.py -q` → 23 passed. Model-import
handmatig geverifieerd (`ConversationQualityJudgment.__table__.columns.keys()`
komt exact overeen met de SQL-kolommen).

**Nog niet gedraaid: de post-deploy SQL zelf tegen een echte database.** Dat
gebeurt pas bij deploy (`scripts/apply_post_deploy_sql.sh` of handmatig als
klai-superuser), net als bij elke eerdere klai-owned-tabel-migratie in dit
repo — dat hoort niet in een worktree-sessie te draaien.

## REQ-2 — Judge-service + nachtelijke lus · done

Nieuw bestand `app/services/conversation_judge.py`, structuur mirrort
`app/services/widget_outcome.py` (per-org batch, cross-org discovery,
FastAPI-lifespan-lus) en de LiteLLM-aanroep mirrort exact
`app/klai_feedback/triage.py::_call_triage_llm` (httpx naar
`{settings.litellm_base_url}/v1/chat/completions`, `Authorization: Bearer
{settings.litellm_master_key}`, `get_trace_headers()`).

**Selectie:** gesprekken met een NIET-NULL `widget_conversations.outcome`
(de heuristiek heeft het gesprek al als "afgerond" gelabeld — hergebruikt
`widget_outcome.py`'s quiet-period-logica in plaats van die te dupliceren)
EN nog geen rij in `conversation_quality_judgments` (LEFT JOIN … WHERE
cqj.id IS NULL). `is_preview`-gesprekken worden overgeslagen, zelfde
uitzondering als `widget_outcome.py` en de gap-events.

**Judge-prompt (exact, niet vrij te ontwerpen door de bouwer):**

System:
```
You are a quality judge for a webchat AI assistant conversation. You will be
shown a full conversation transcript (visitor + assistant turns) plus known
signals, and must output a single JSON object evaluating the conversation.

Known signals given to you (trust these, do not re-derive them):
- explicit_rating: the visitor's thumbs rating on the last assistant answer,
  if any ("thumbsUp" / "thumbsDown" / null)
- had_citation_refusal: whether any assistant answer was the fixed
  "no sources found" refusal
- had_handoff: whether the conversation was escalated to a human/booking flow

Output EXACTLY one JSON object, no other text, matching this schema:
{
  "outcome": one of "resolved" | "partially_resolved" | "unresolved" |
    "escalated" | "out_of_scope" | "abandoned_early",
  "failure_category": one of "retrieval_miss" | "retrieval_wrong" |
    "generation_error" | "policy_refusal" | "scope_mismatch" |
    "user_confusion" | "none" (use "none" only when outcome is "resolved"),
  "reasoning": 1-3 sentences in the conversation's own language, MUST quote
    or closely paraphrase a specific turn as evidence. Never invent evidence
    not present in the transcript.
  "confidence": one of "high" | "medium" | "low" — use "low" whenever the
    visitor gave no explicit signal and the transcript alone is ambiguous.
  "suggested_action": a short actionable note for a knowledge-base editor,
    or null if none applies.
}

Category definitions:
- retrieval_miss: the assistant found no relevant source and said so (or
  gave the fixed refusal).
- retrieval_wrong: the assistant cited a source, but it does not actually
  answer the visitor's question.
- generation_error: the right source was cited, but the assistant's answer
  is wrong, incomplete, or hallucinated beyond the source.
- policy_refusal: the assistant correctly declined per policy (e.g.
  broad-mode consent, pricing/contract commitment ban) — not a knowledge gap.
- scope_mismatch: the question is entirely outside what this product/company
  does.
- user_confusion: the visitor's own question was unclear or
  self-contradictory, not a system fault.
- none: only for outcome "resolved".

Trust explicit_rating over your own read of tone when they conflict: an
explicit thumbsUp always yields "resolved" outcome with high confidence; an
explicit thumbsDown never yields "resolved".
```

User content: JSON-encoded transcript (`role`, `content`, `sources` per
turn) + the three known signals, `json.dumps(..., ensure_ascii=False)`
zoals `_build_triage_prompt`.

**Opslag:** `INSERT ... ON CONFLICT (conversation_id) DO UPDATE SET
outcome=…, failure_category=…, reasoning=…, confidence=…,
suggested_action=…, model_used=…, judged_at=NOW()` — idempotent, geen
model-versiegeschiedenis nodig (in tegenstelling tot feedback-triage's
`model_key`-patroon; hier is er maar één actueel verdict per gesprek).

**Fail-open bij parse-fouten:** ongeldige JSON of een schema-mismatch →
`logger.warning(..., exc_info=True)`, gesprek overslaan, volgende cyclus
probeert opnieuw (zelfde patroon als `_safe_ascore()` in
`judge_client.py` — nooit een stille "success", altijd zichtbaar in logs).

Model: `settings.conversation_judge_model` (= `klai-medium`, al toegevoegd
in REQ-1).

**Gate (opgegeven aan de bouwer):**
```
cd klai-portal/backend && uv run pytest tests/test_conversation_judge.py -q
```
Nieuw testbestand, mirror van `tests/test_widget_outcome.py`'s mock-stijl
voor de selectiequery + een gemockte httpx-response voor de LiteLLM-call
(geen echte netwerkaanroep in tests).

## REQ-3 — Judge-verdict zichtbaar in de admin-UI · done

Toon `outcome`/`failure_category`/`reasoning`/`confidence` uit REQ-2 als een
klein paneel/badge bij een gesprek, op zowel de tenant-admin
`ActivityTab.tsx` (Fase 0) als de platform cross-tenant
`ConversationsSection` (Fase 1) — beide renderen al de transcript-drawer,
dit voegt alleen een leesveld toe naast de bestaande rating-badge. Geen
nieuwe route.

## REQ-5 — LibreChat-pilot, alleen Voys, achter een feature-vlag · done

Mark vroeg op 11 sep om ook LibreChat-gesprekken te kunnen monitoren, maar
beperkt tot één tenant (Voys) zodat het risico van een nieuw cross-tenant
Mongo-toegangspatroon (§9.1) niet hoeft te worden opgelost — verbinden met
precies één, al-bekende tenant-database is geen nieuw patroon, het is
`librechat_chat_context.py`'s bestaande aanpak nog een keer.

**Schema (al gebouwd, zelf gedaan, zelfde reden als REQ-1):**
Nieuwe migratie `d3c8b6a5f1e0`: `channel`-CHECK verbreed naar
`('webchat', 'librechat')`, nieuwe nullable kolom
`external_conversation_id TEXT` (uniek) voor LibreChat-rijen — een
LibreChat-gesprek heeft geen Postgres-rij om naar te FK'en (Mongo
ObjectId-string, geen widget_conversations.id), dus een aparte kolom i.p.v.
de bestaande `conversation_id` hergebruiken.

**Feature-vlag (al gebouwd):** nieuwe key `librechat_quality_judge` in
`KNOWN_FEATURES` (`app/core/extensions_registry.py`) — standaard uit voor
elke tenant, alleen aan te zetten via het bestaande platform-unlocks-
mechanisme (`PATCH /api/admin/orgs/{slug}/platform-unlocks`), zelfde
mechanisme als `widgets`/`partner_api`. Geen hardcoded tenant-check —
expliciet de les uit SPEC-VOYS-HELPBOT-001 REQ-10 toegepast.

**Geverifieerd LibreChat-schema (echt Mongoose-schema, gepinde tag v0.8.7,
`packages/data-schemas/src/schema/message.ts` — niet afgeleid, opgehaald
van danny-avila/LibreChat op GitHub):** relevante velden op een
`messages`-document: `conversationId`, `isCreatedByUser` (bool — het echte
rolveld), `text`, `content` (mixed array, alleen voor rijkere content),
`sender`, `createdAt`, `unfinished`/`error` (bool — platformeigen
foutsignalen), `feedback: {rating: 'thumbsUp'|'thumbsDown', tag, text}`.
**Geen citaten/bronnen-veld** — LibreChat's Klai-fork parseert bronnen uit
de gestreamde tekst maar bewaart ze niet gestructureerd in Mongo. De
LibreChat-rubric gebruikt dus GEEN `had_citation_refusal`-signaal (bestaat
niet voor dit kanaal); in plaats daarvan `had_error` op basis van het echte
`error`-veld.

**Tweede rubric (interne, kennissysteem-vaardige gebruiker — het
onderscheid dat Mark vanaf het begin vroeg):**

System (LETTERLIJK over te nemen, niet te herschrijven):
```
You are a quality judge for an internal AI knowledge assistant conversation.
The user is an authenticated employee who already knows how to use this
tool and how to talk to a knowledge system — unlike an anonymous public
visitor, they can rephrase, drill down, and are not put off by a
clarifying question. You will be shown a full conversation transcript
(user + assistant turns) plus known signals, and must output a single JSON
object evaluating the conversation.

Known signals given to you (trust these, do not re-derive them):
- explicit_rating: the user's thumbs rating on the last assistant answer,
  if any ("thumbsUp" / "thumbsDown" / null)
- had_error: whether any assistant turn was flagged as a system/generation
  error by the platform itself

Output EXACTLY one JSON object, no other text, matching this schema:
{
  "outcome": one of "resolved" | "partially_resolved" | "unresolved" |
    "escalated" | "out_of_scope" | "abandoned_early",
  "failure_category": one of "retrieval_miss" | "retrieval_wrong" |
    "generation_error" | "policy_refusal" | "scope_mismatch" |
    "user_confusion" | "none" (use "none" only when outcome is "resolved"),
  "reasoning": 1-3 sentences in the conversation's own language, MUST quote
    or closely paraphrase a specific turn as evidence. Never invent evidence
    not present in the transcript.
  "confidence": one of "high" | "medium" | "low" — use "low" whenever the
    user gave no explicit signal and the transcript alone is ambiguous.
  "suggested_action": a short actionable note for a knowledge-base editor,
    or null if none applies.
}

Category definitions (adjusted for this internal audience):
- retrieval_miss: the assistant found no relevant source and said so, or
  answered generically without grounding in this organisation's own
  knowledge base.
- retrieval_wrong: the assistant referenced material that does not answer
  what the user actually asked.
- generation_error: the right information was available, but the
  assistant's answer is wrong, incomplete, or hallucinated beyond it — or
  had_error is true.
- policy_refusal: not typically applicable to this internal audience; use
  only if the assistant explicitly declined for a stated policy reason.
- scope_mismatch: the question is entirely outside what this
  organisation's knowledge base could ever cover.
- user_confusion: the user's own question was unclear or
  self-contradictory, not a system fault.
- none: only for outcome "resolved".

There is no escalation/handoff concept on this channel and no
citation-firewall — judge the answer on its own merits, not on whether a
source was formally cited. Trust explicit_rating over your own read of
tone when they conflict: an explicit thumbsUp always yields "resolved"
outcome with high confidence; an explicit thumbsDown never yields
"resolved".
```

**Selectie:** per org waarvoor `librechat_quality_judge` unlocked is
(`org.platform_unlocked_features`), resolve `org.slug` →
`provisioning_names_for_slug(slug).mongodb_database` (patroon:
`librechat-{slug}`), verbind met dezelfde root-credentials en timeouts als
`librechat_chat_context.py::_sync_recent_conversations` (host
`settings.mongodb_container_name`, poort 27017, `authSource="admin"`, 2s
timeouts), sync `pymongo.MongoClient` binnen `asyncio.to_thread` — geen
nieuw verbindingspatroon, exacte kopie. Sluit gesprekken uit waarvan
`external_conversation_id` al een rij heeft in
`conversation_quality_judgments` (eerst ophalen uit Postgres, dan filteren
in Python — bij pilotschaal van één tenant geen probleem).

**Opslag:** zelfde tabel, `channel='librechat'`,
`external_conversation_id` gevuld, `conversation_id` NULL. UPSERT op
`external_conversation_id` (aparte unieke constraint, al gebouwd).

**Loop:** geen nieuwe FastAPI-lifespan-taak — de bestaande
`conversation_judge_loop()` roept na de webchat-pas ook de nieuwe
LibreChat-pas aan, zodat er geen tweede achtergrondlus met eigen interval
nodig is.

## REQ-4 — Anonimiseringssweep · done

Nieuwe stap in `widget_messages_retention.py::_retention_run_once`, VÓÓR de
bestaande DELETE op `widget_messages`: voor elke conversation_id die zo
meteen gepurged gaat worden, `UPDATE conversation_quality_judgments SET
reasoning = NULL, anonymized_at = NOW() WHERE conversation_id = ANY(:ids)
AND reasoning IS NOT NULL`. De FK is `ON DELETE SET NULL` (gefixt in REQ-1,
was per ongeluk `CASCADE`), dus de judgment-rij zelf overleeft de latere
purge van `widget_conversations` — alleen `conversation_id` wordt dan NULL.
Resultaat: `outcome`, `failure_category`, `confidence`, `suggested_action`,
`org_id`, `judged_at`, `anonymized_at` blijven bruikbaar voor een
trenddashboard, zonder citaat uit een inmiddels verwijderd gesprek.

---

# Bronnen (webonderzoek, sep 2026)

- [Agent-in-the-Loop: A Data Flywheel for Continuous Improvement in LLM-based Customer Support](https://arxiv.org/pdf/2510.06674)
- [Reinforcement Learning for Optimizing RAG for Domain Chatbots](https://arxiv.org/pdf/2401.06800)
- [8 best human-in-the-loop LLM evaluation platforms in 2026 — Braintrust](https://www.braintrust.dev/articles/best-human-in-the-loop-llm-evaluation-platforms-2026)
- [Top LLM Observability and Evaluation Platforms in 2026 — MarkTechPost](https://www.marktechpost.com/2026/08/09/top-llm-observability-and-evaluation-platforms-in-2026-langfuse-langsmith-braintrust-arize-and-more-compared/)
- [LLM-as-a-Judge — Langfuse](https://langfuse.com/docs/evaluation/evaluation-methods/llm-as-a-judge)
- [Auto-Flag Problematic LLM Conversations Without Classifiers — Latitude](https://latitude.so/blog/auto-flag-problematic-conversations)
- [Every Mistral AI Model Explained and Compared (Sep 2026) — Second Talent](https://www.secondtalent.com/resources/every-mistral-ai-model-explained-compared/)
- [Mistral API Pricing (September 2026) — BenchLM.ai](https://benchlm.ai/mistral/api-pricing)
- Intern: `docs/research/help-page-chatbot-voys.md` §6.2 (Zendesk verifieert "resolved"-labels achteraf met een LLM, juist om opgeblazen cijfers te voorkomen — dezelfde reden als hier), §8 (G-6, G-2, G-13)
