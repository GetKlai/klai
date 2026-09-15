---
id: SPEC-KNOWLEDGE-ACTIVITY-001
version: "0.1.0"
status: plan, nothing built — wacht op Mark's ja voor fase 1 (klasse L)
created: 2026-09-15
updated: 2026-09-15
author: Claude (Fable 5.1), commissioned by Mark Vletter
priority: high
tenant_scope: platform-wide — elke tenant met de `widgets`-unlock; Voys is de eerste die het echt gebruikt
related:
  - SPEC-CHAT-QUALITY-LOOP-001 (de nachtelijke judge waarvan dit plan de output
    zichtbaar, filterbaar en corrigeerbaar maakt; §9.5 daar noemde al dat het
    gatendashboard "verstopt" zit — dit plan is dat opvolgpunt)
  - SPEC-VOYS-HELPBOT-001 (REQ-1 gap-events vanaf het widget-pad, REQ-3
    thumbs, REQ-5 outcome-heuristiek, REQ-7 broad-mode-toestemming)
  - SPEC-RAG-LOW-CONFIDENCE-ABSTAIN-001 / docs/knowledge-retrieval-low-confidence-abstain-2026-05-08.md
    (de `confidence_band`-drempels 0,60 / 0,30 die §4.1 overneemt)
  - SPEC-PRIVACY-QUERY-SHADOW-001 (telemetry-gating op `query_text`, die de
    handmatige gaten uit §4.5 erven)
  - docs/research/help-page-chatbot-voys.md §6.2 (KPI-onderzoek: resolutie is
    de eerlijke noemer, judge over 100% van de gesprekken, mens als ijkpunt)
---

# HISTORY

| Versie | Datum | Wijziging |
|---|---|---|
| 0.1.0 | 2026-09-15 | Eerste plan na onderzoek in de bron en één feedbackronde met Mark. Drie besluiten uit die ronde verwerkt: de bezoeker houdt alleen thumbs (geen druk bij de gebruiker, de interne collega krijgt de beoordelingsroute), de admin-kant blijft bestaan met een expliciete verdeling admin/kennis (§3), en het plan begint bij de externe chat maar is zo ontworpen dat het interne kanaal en meertaligheid er later zonder herbouw bij kunnen (§4.8, §4.9). Niets gebouwd. |

# 0. Aanleiding

De externe chat (webchat-widget op onder meer help.voys.nl) wordt echt
gebruikt, en de kwaliteit van de antwoorden is wisselend. Er draait al een
nachtelijke beoordeling per gesprek, maar de interface laat daar weinig van
zien, en niemand kan zeggen "dit klopt niet, en wél hierom". Mark wil drie
dingen kunnen doen die vandaag niet kunnen:

1. Zien hoe zeker het systeem was van een antwoord, en of die zekerheid
   terecht was. Dat kan een mens veel beter beoordelen dan een model.
2. Als interne collega een antwoord afkeuren met een reden die iets
   oplevert: de kennis ontbreekt, de kennis staat er verkeerd in, of Klai
   gedraagt zich verkeerd (en dat vraagt om prompt- of modelwerk).
3. Dit als kennisbeheer doen, niet als tenant-beheer. Kennisbeheerders
   moeten het kunnen zien en ernaar kunnen handelen, inclusief gap
   detection, zonder dat ze admin hoeven te zijn.

Het merendeel van de slechte antwoorden zal, zo verwacht Mark, aan de
kennisbank liggen en niet aan het model. Dat is precies waarom de
beoordeling per antwoord een oorzaak moet vastleggen en niet alleen een
cijfer: het cijfer zegt hoe vaak het fout gaat, de oorzaak zegt wie er aan
de slag moet.

# 1. Huidige staat (geverifieerd in de bron, 15 sep 2026)

| Onderdeel | Waar | Wat het doet | Wat ontbreekt |
|---|---|---|---|
| Outcome-heuristiek | `app/services/widget_outcome.py` | Elke 15 min: `widget_conversations.outcome` = resolved / escalated / abandoned / unknown, puur op regels. | Geen reden. |
| Nachtelijke judge | `app/services/conversation_judge.py`, `librechat_quality_judge.py`, tabel `conversation_quality_judgments` | Tussen 01:00 en 06:00 UTC beoordeelt `klai-medium` elk afgerond gesprek: `outcome` (6 waarden), `failure_category` (retrieval_miss / retrieval_wrong / generation_error / policy_refusal / scope_mismatch / user_confusion / none), `reasoning` met citaat, `confidence` (high/medium/low), `suggested_action`. LibreChat-variant achter unlock `librechat_quality_judge`. | Alleen zichtbaar in een drawer per gesprek; nergens in een lijst, nergens filterbaar; een mens kan het niet corrigeren. |
| Zekerheid per antwoord | `app/services/partner_chat.py` (~2115-2119 en het beslissingsrecord `partner_chat_citation_selection_decision`) | Per antwoord berekent het widget-pad de hoogste reranker-score, `classify_gap()` (hard / soft / geen, drempels 0,40 reranker en 0,35 dense), of de citation-firewall het antwoord verving door de vaste weigering, hoeveel bronnen overbleven, en of het antwoord in broad mode kwam. | Wordt gelogd, niet bewaard. `widget_messages` heeft alleen `content`, `sources`, `rating`, `turn_id`. De retrieval-api's `confidence_band` (0,60 / 0,30) wordt op dit pad niet gelezen. |
| Bezoekersfeedback | `widget_messages.rating` via `POST /partner/v1/widget/feedback` | Thumbs up/down per assistent-beurt, intrekbaar. | Blijft zo (besluit Mark, §2). |
| Activity-tab | `routes/admin/widgets/_components/tabs/ActivityTab.tsx`, `GET /api/admin/widgets/{id}/{stats,conversations}` | Periode 7d/30d/alles, tellingen, outcome-verdeling, uurpatroon, top-10 vragen, lijst van maximaal 50 gesprekken zonder filters, transcript in een drawer met thumbs, bronnen, judge-badge en bezoekersnaam/e-mail. | Geen filters, geen paginering (backend kan wel `cursor`), judge niet in de lijst. De drawer overtreedt `frontend/AGENTS.md` ("geen drawers voor admin-entiteiten"). Het kwaliteitspaneel bestaat twee keer (tenant-admin en `admin/platform/orgs.$orgId.tsx`, gekopieerde code). |
| Rechten | `get_caller_at_least(ProfileRole.ADMIN)` + `require_platform_unlocked("widgets")` | Alleen rol `admin` ziet activiteit. Rollenladder: personal < company < kb_manager < group_manager < admin (`app/core/profiles.py`). | kb_manager ziet niets van de externe chat. |
| Kennisgaten | `app/api/app_gaps.py`, tabel `portal_retrieval_gaps`, `gap_events.py`, `gap_rescorer.py`, `routes/app/gaps/index.tsx` | Eén schrijfpad voor drie bronnen (LibreChat-hook, MCP-clients, widget met `caller_client_id="widget-chat"`). Rescorer stelt open vragen opnieuw na een KB-wijziging en sluit het gat als retrieval weer slaagt. Capability `kb.gaps` (kb_manager en hoger). | Enige ingang is een tegel op de KB-overzichtspagina die alleen admins zien; geen menu-item. Een gat weet niet uit welk gesprek het kwam. Geen handmatig aanmaken of sluiten. |
| Retentie | `widget_messages_retention_days = 7` (`app/core/config.py:384`), sweep in `widget_messages_retention.py` | Gesprekken verdwijnen na 7 dagen; `conversation_quality_judgments.reasoning` wordt dan geleegd, de rest van de rij blijft. | Alles wat dit plan bewaart moet diezelfde purge overleven (§4.2). |
| Taal | `widget_conversations.language_detected` | Per gesprek de gedetecteerde taal (`String(8)`). | Niet op gaten, niet in filters. |
| App-navigatie | `routes/app/-app-tools.ts`, `components/layout/Sidebar.tsx` | Menu-items per product (chat, instructions, transcribe, knowledge, docs). De sidebar ondersteunt al `children` per item, getoond zodra het pad met het item begint (`Sidebar.tsx:133`). | Geen item voor gaten of activiteit. |

# 2. Besluiten uit de feedbackronde (15 sep 2026)

1. **De bezoeker houdt alleen thumbs.** Geen reden-veld, geen vrije tekst
   voor de bezoeker. De druk ligt niet bij de gebruiker; de interne collega
   krijgt een eigen beoordelingsroute ernaast (§4.2, §4.3).
2. **De admin-interface blijft bestaan.** Niet alles verhuist naar de
   kenniskant. §3 legt vast wat waar hoort, met als regel: de admin beheert
   het kanaal, de kennisbeheerder beoordeelt de antwoorden.
3. **Externe chat eerst, intern voorbereid.** Fase 1 t/m 3 gaan over
   webchat. Het datamodel en de API dragen vanaf dag één een `channel`, zodat
   het interne LibreChat-kanaal er later bij kan zonder tabel- of
   routewijziging (§4.8).
4. **Meertaligheid komt eraan.** De chat wordt op termijn meertalig. Taal is
   daarom vanaf fase 0 een eersteklas veld op signalen, gaten en filters
   (§4.9), zodat "de kennis ontbreekt in het Engels" een ander gat is dan
   "de kennis ontbreekt".

# 3. Verdeling admin-kant en kenniskant

Regel: **de admin beheert het kanaal, de kennisbeheerder beoordeelt de
antwoorden.** Een admin is op de ladder ook kb_manager, dus een admin ziet
beide kanten; een kb_manager ziet alleen de kenniskant. Er is één
transcript-detailpagina, die per rol meer of minder toont, zodat er nooit
twee transcriptweergaven naast elkaar leven.

| | Admin-kant (`/admin/widgets/$id`, tab Activity) | Kenniskant (`/app/knowledge/…`) |
|---|---|---|
| Vraag die het beantwoordt | Werkt de widget, hoe druk is het, wie wil contact? | Klopt het antwoord, wat moet er in de kennisbank, hoe zeker was Klai? |
| Rol | admin + unlock `widgets` | kb_manager en hoger + unlock `widgets` (nieuwe capability `kb.activity`) |
| Blijft / komt | Volume (gesprekken, berichten, uurpatroon, top-vragen), outcome-verdeling van de heuristiek, handoffs, bezoekersnaam en e-mail, retentie-informatie, link "Beoordeel gesprekken onder Kennis" | Werkvoorraad, lijst met filters, judge-oordeel per rij, zekerheidsband, beoordeling per antwoord met oorzaak, koppeling naar gaten en KB-pagina, kalibratiepaneel |
| Transcript | Rij in de lijst linkt naar dezelfde detailroute als de kenniskant; admin ziet daar bovendien bezoekersnaam, e-mail en handoff-status | Detailroute `/app/knowledge/activity/$conversationId` zonder bezoekersgegevens |
| Wat weggaat | De 50-gesprekkenlijst en de drawer (vervangen door de link en de gedeelde detailroute) | – |
| Wat de platform-admin (`/admin/platform/orgs/$orgId`) krijgt | Dezelfde gedeelde transcript- en kwaliteitscomponent, cross-tenant zoals nu | – |

Privacy-argument voor de scheiding van bezoekersgegevens: een kb_manager
beoordeelt kennis, geen klanten. `visitor_name` en `visitor_email` (pre-chat
formulier, commit `bd99641e3`) blijven buiten de kenniskant-respons. Dit is
één veldomissie in het responsschema, geen aparte endpoint.

# 4. Ontwerp

## 4.1 Zekerheid per antwoord: `answer_signals`

Eén nullable JSONB-kolom `answer_signals` op `widget_messages`, gevuld op
het punt waar `partner_chat.py` nu het beslissingsrecord logt, alleen op
assistent-beurten:

```json
{
  "top_score": 0.71,
  "band": "high",
  "gap_type": null,
  "sources_count": 3,
  "refused": false,
  "broad_mode": false,
  "language": "nl",
  "model": "klai-primary"
}
```

- `band` gebruikt dezelfde drempels als de retrieval-api's `confidence_band`
  (high ≥ 0,60, low < 0,30, daartussen medium, `unknown` bij geen score),
  zodat pad A en pad B hetzelfde woord voor hetzelfde ding gebruiken. De
  drempels komen uit settings, niet als literal in de code.
- `refused` is de citation-firewall-weigering; `broad_mode` markeert een
  open antwoord na toestemming van de bezoeker (SPEC-VOYS-HELPBOT-001
  REQ-7). Beide zijn nodig voor §4.7.
- Dit is **retrieval-zekerheid**, geen zelfinschatting van het model. Bewust:
  het lage-confidence-onderzoek van mei liet zien dat de reranker-score de
  bruikbare voorspeller is, en een taalmodel dat zichzelf een cijfer geeft is
  slecht gekalibreerd en zou de streaming-uitvoer raken. Of de band
  daadwerkelijk klopt, meet §4.6.
- De kolom leeft op het bericht en verdwijnt dus met de purge na 7 dagen.
  Wat voor kalibratie bewaard moet blijven, wordt bij beoordeling
  gekopieerd naar de beoordelingsrij (§4.2).

## 4.2 Beoordeling door een collega: `answer_reviews`

Nieuwe tabel, Cat-D RLS, gebouwd zoals `conversation_quality_judgments`
(no-op alembic-marker plus post-deploy SQL, omdat portal_api geen
REFERENCES-recht heeft op klai-owned tabellen).

| Kolom | Type | Toelichting |
|---|---|---|
| `id` | bigint pk | |
| `org_id` | int, FK portal_orgs, CASCADE | |
| `channel` | text CHECK ('webchat','librechat') | Vanaf dag één, zie §4.8 |
| `conversation_id` | bigint, FK widget_conversations, **SET NULL** | Overleeft de purge |
| `message_id` | bigint, FK widget_messages, **SET NULL** | Overleeft de purge |
| `external_message_id` | text, nullable | LibreChat later; nu altijd NULL |
| `turn_sequence` | int | `widget_messages.sequence`, zodat de rij na purge nog zegt om welke beurt het ging |
| `reviewer_user_id` | int, FK portal_users | |
| `verdict` | text CHECK ('correct','incomplete','wrong','not_a_fault') | |
| `cause` | text CHECK ('knowledge_missing','knowledge_wrong','behaviour','none') | Verplicht bij incomplete/wrong, anders 'none' |
| `note` | text, nullable | Eigen woorden van de beoordelaar |
| `kb_slug` | text, nullable | Waar de kennis hoort (bij knowledge_*) |
| `band_at_review` | text | Snapshot uit `answer_signals.band` |
| `judge_outcome_at_review` | text, nullable | Snapshot uit de judgment-rij |
| `judge_failure_category_at_review` | text, nullable | Snapshot |
| `language` | text, nullable | Snapshot uit het gesprek |
| `gap_id` | bigint, FK portal_retrieval_gaps, SET NULL | Het gat dat deze beoordeling aanmaakte (§4.5) |
| `reviewed_at`, `updated_at` | timestamptz | |

UNIQUE op (`message_id`) zolang er één beoordeling per beurt is; een tweede
beoordelaar overschrijft (UPSERT), de laatste telt. Beoordelen per
**assistent-beurt**, niet per gesprek: één gesprek kan een goed en een fout
antwoord bevatten, de thumbs zijn ook per beurt, en de oorzaak hoort bij
het antwoord. Het gespreksoordeel is afgeleid (slechtste beurt).

De vier oorzaken mappen op de judge-categorieën, zodat mens en judge tegen
elkaar gemeten kunnen worden (§4.6):

| Oorzaak (mens) | Judge `failure_category` | Wie moet aan de slag |
|---|---|---|
| kennis ontbreekt | retrieval_miss | KB-auteur: schrijven |
| kennis klopt niet / verouderd | retrieval_wrong | KB-auteur: corrigeren |
| Klai gedraagt zich verkeerd | generation_error | Klai-team: prompt, retrieval, model |
| geen fout van Klai | policy_refusal, scope_mismatch, user_confusion | niemand |

## 4.3 De beoordelingsroute in de UI

- **Werkvoorraad als standaardweergave** op `/app/knowledge/activity`:
  onbeoordeelde gesprekken waarvan de judge niet "resolved" zei, óf de
  bezoeker een duim omlaag gaf, óf een assistent-beurt band `low`/`unknown`
  had, óf de citation-firewall weigerde. Dit is wat een kennisbeheerder
  afwerkt; het aantal staat als teller bij het menu-item. Omdat gesprekken
  na 7 dagen verdwijnen, is de voorraad per definitie nooit ouder dan een
  week, en dat is de bedoeling: beoordelen gebeurt terwijl het gesprek er
  nog is.
- **Lijst** (`ListFrame`/`DataTable` volgens `ui-standards.md`, zoeken en
  paginering boven de 10 rijen): per rij de eerste vraag, taal, band-badge
  van de slechtste beurt, judge-badge, thumbs, beoordelingsstatus, en of er
  een open gat aan hangt. Filters, gesynchroniseerd naar de URL zoals
  `/app/gaps` dat al doet: periode, widget, taal, judge-outcome,
  failure_category, beoordelingsstatus (open / beoordeeld / per oorzaak),
  band, thumbs. Sortering: nieuwste of slechtste eerst.
- **Detailroute** `/app/knowledge/activity/$conversationId`: transcript
  met per assistent-beurt de bronnen (bestaande allowlist op http/https),
  de thumbs, de band en de signalen, het judge-oordeel met reasoning en
  suggested_action, en het beoordelingsformulier. Geen drawer.
- **Formulier in twee klikken**: verdict (4 knoppen, toetsen 1 t/m 4), dan
  bij incomplete/wrong de oorzaak (3 knoppen), optioneel een notitie en een
  KB-keuze. De judge-categorie staat vooringevuld als suggestie, zodat de
  mens vooral corrigeert. Bij een kennis-oorzaak verschijnt de knop "Open
  in kennisbank" (naar `/app/docs/$kbSlug`, zoals de gaten-pagina nu al
  doet) en wordt het gat aangemaakt (§4.5).
- **Gedeelde component** `features/chat-activity/` voor transcript,
  kwaliteitspaneel en beoordelingsformulier, gebruikt door de kenniskant,
  de admin-tab-link en de platform-admin. Het bestaande dubbele
  `QualityPanel` verdwijnt daarin.

## 4.4 Plek en rechten

- Menu: "Kennis" krijgt drie `children` in de sidebar: **Kennisbanken**
  (`/app/knowledge`, huidige lijst), **Gesprekken**
  (`/app/knowledge/activity`), **Kennisgaten** (`/app/knowledge/gaps`,
  verhuisd; `/app/gaps` blijft als redirect). Geen nieuwe navigatie-
  primitief: `Sidebar.tsx` toont kinderen al zodra het pad met het
  ouder-item begint.
- Backend: nieuwe router `app/api/app_activity.py`, prefix `/api/app/activity`,
  router-breed `require_capability(Capability.KB_ACTIVITY)` én
  `require_platform_unlocked("widgets")`. `KB_ACTIVITY = "kb.activity"`
  wordt toegevoegd aan `_KB_FULL_CAPS` in `app/core/profiles.py`, dus
  kb_manager, group_manager en admin.
- Endpoints: `GET /conversations` (filters uit §4.3, cursor-paginering die
  de backend al kent), `GET /conversations/{id}` (transcript + signalen +
  judgment + reviews; bezoekersvelden alleen als de aanroeper admin is),
  `PUT /messages/{id}/review` (UPSERT), `DELETE /messages/{id}/review`,
  `GET /summary` (§4.6), `GET /queue-count` (teller).
- De admin-endpoints `GET /api/admin/widgets/{id}/conversations[/{conv_id}]`
  en `/quality` vervallen in dezelfde wijziging; de admin-tab houdt
  `GET /stats` en linkt voor gesprekken naar de kenniskant. Geen oud en
  nieuw naast elkaar.
- Tenant-scoping: alles via `perms.org_id` en `tenant_scoped_session`, zoals
  `app_gaps.py`. Widget-filter alleen op widgets van de eigen org.

## 4.5 Gap detection integreren via de bestaande tabel

- Een beoordeling met oorzaak `knowledge_missing` of `knowledge_wrong`
  schrijft via het bestaande `record_gap_event()` een rij in
  `portal_retrieval_gaps` met `caller_client_id="human-review"`,
  `gap_type` hard (ontbreekt) of soft (klopt niet), `query_text` = de
  bezoekersvraag van die beurt, `nearest_kb_slug` = de gekozen KB. De
  telemetry-gating van SPEC-PRIVACY-QUERY-SHADOW-001 geldt automatisch mee.
  Het `gap_id` komt op de beoordelingsrij.
- `portal_retrieval_gaps` krijgt twee nullable kolommen: `conversation_id`
  (FK, SET NULL) zodat je van gat naar gesprek kunt, en `language` (§4.9).
  Het widget-pad vult beide voortaan ook bij automatische gaten.
- Het gatenoverzicht krijgt één mutatie erbij: **handmatig sluiten**
  (`PATCH /api/app/gaps/{id}` → `resolved_at`), plus een kolom "bron"
  (automatisch / beoordeling) en een link naar het gesprek als dat er nog
  is. De rescorer blijft de automatische sluiter: zodra de KB is aangepast
  en de vraag weer een antwoord vindt, sluit het gat vanzelf. Dat is de
  gesloten lus.
- Geen tweede gatenlijst, geen aparte "review-gaten".

## 4.6 Kalibratie: hoe zeker was hij, en klopte dat

`GET /api/app/activity/summary` levert voor een periode twee kruistabellen,
berekend uit `answer_reviews` (de snapshots, dus onafhankelijk van de
purge):

1. `band_at_review` × `verdict`: "bij hoge zekerheid was n% van m
   beoordeelde antwoorden correct".
2. `judge_outcome_at_review`/`judge_failure_category_at_review` × menselijk
   `verdict`/`cause`: hoe vaak de judge het met de mens eens is, en welke
   categorie hij structureel mist.

In de UI is dat één compact paneel boven de lijst met de drie zinnen die
ertoe doen, en een uitklap voor de tabellen. Pas als tabel 1 laat zien dat
`high` betrouwbaar is, wordt het verantwoord om de band ooit aan een
bezoeker te tonen (§6).

## 4.7 Open versus strikt

Er is al gratis materiaal: op de publieke help-widget volgt na een
weigering een aanbod, en na toestemming van de bezoeker een open antwoord in
hetzelfde gesprek. Zodra `answer_signals.broad_mode` bestaat, zijn die
beurten herkenbaar en beoordeelt de collega ze met dezelfde vier knoppen.
Het summary-paneel toont dan "open antwoorden: x% correct, y% fout" naast
hetzelfde cijfer voor strikte antwoorden op vragen met een gat.

Als die steekproef te klein blijft (te weinig bezoekers zeggen ja), is de
duurdere variant een aparte, optionele fase: 's nachts voor gesprekken met
`failure_category` retrieval_miss of retrieval_wrong een schaduw-open-antwoord
genereren (non-streaming, `broad_consent=True`, gemaximeerd per nacht) en
naast het strikte antwoord tonen, met een derde beoordelingsvraag "open was
beter / gelijk / slechter". Geen bezoeker ziet dat antwoord.

## 4.8 Intern kanaal (later, geen herbouw)

- `answer_reviews.channel` en `external_message_id` staan er vanaf dag één;
  de judgment-tabel heeft hetzelfde patroon al.
- De lijst-API draagt `channel` als filter met default `webchat`. LibreChat-
  rijen verschijnen alleen voor tenants met de unlock `librechat_quality_judge`,
  en in fase 1 t/m 3 uitsluitend als geaggregeerde judge-uitkomst zonder
  transcript: het zijn gesprekken van medewerkers, en of kb_managers die
  mogen lezen is een aparte beslissing van Mark, niet een bijproduct van
  dit plan.
- Wat er nog mist voor een volwaardig intern kanaal: een transcript-adapter
  van Mongo naar de detailroute (zelfde verbindingspatroon als
  `librechat_chat_context.py`) en `answer_signals` op pad A (de LiteLLM-hook
  heeft `confidence_band` al, alleen wordt hij niet bewaard). Beide zijn
  toevoegingen, geen wijzigingen aan wat fase 1 bouwt.

## 4.9 Meertaligheid (nu al goed inrichten)

- `language` op `answer_signals`, `answer_reviews` en `portal_retrieval_gaps`
  (§4.5), afgeleid van `language_detected` op het gesprek.
- Taalfilter in de lijst en op de gatenpagina. Een gat is per (vraag,
  gap_type) gegroepeerd; met taal erbij wordt dat (vraag, gap_type, taal),
  zodat "ontbreekt in het Engels" een eigen rij is en de rescorer de vraag
  in die taal opnieuw stelt.
- De beoordelingstaxonomie is taalneutraal; de judge schrijft `reasoning`
  al in de taal van het gesprek. Het summary-paneel kan per taal splitsen.
- Wat dit plan niet doet: de KB zelf meertalig maken. Dat is een
  kennisbank-vraagstuk; dit plan zorgt alleen dat de meting per taal
  uiteenvalt zodra dat nodig is.

# 5. Fasering

| Fase | Inhoud | Klasse | Gate |
|---|---|---|---|
| 0 | `answer_signals` (kolom, model, vullen in `partner_chat.py`, één test op het vulpad) | S/M | backend-suite, `pyright` |
| 1 | `answer_reviews` (tabel, RLS, model), router `app_activity.py`, capability `kb.activity`, gedeelde component, lijst met filters en werkvoorraad, detailroute, formulier, sidebar-kinderen, admin-tab wordt stats + link, oude admin-endpoints en drawer weg, platform-admin op de gedeelde component | L | backend-suite, `pyright`, `tests/test_rls_hygiene.py`, frontend `lint`/`typecheck`/`test`, Playwright-klikpad: beoordelen als kb_manager, bezoekersvelden onzichtbaar als kb_manager en zichtbaar als admin |
| 2 | Gaten: `conversation_id` en `language` op gaten, review → gat, handmatig sluiten, kolom bron, `/app/gaps` → `/app/knowledge/gaps` met redirect | M | idem + `tests/test_gap_*` |
| 3 | `GET /summary`, kalibratiepaneel, open-versus-strikt-cijfer, taalsplitsing | M | idem |
| 4 (optioneel) | Schaduw-open-antwoorden; band tonen aan de bezoeker in de widget; intern kanaal met transcript | L, elk apart | apart SPEC-verzoek |

Fase 0 kan direct, want elke dag zonder deze data is een dag zonder
kalibratiemateriaal. Fase 1 is klasse L en wacht op Mark's expliciete ja.
Fase 2 en 3 zijn los in te plannen zodra fase 1 een week data heeft.

Klasse-S-alternatief voor wie fase 1 te groot vindt: alleen de
judge-badge en een taalfilter in de bestaande admin-lijst. Dat lost het
zichtbaarheidsprobleem voor admins op, maar niet de kernvraag: geen
menselijke beoordeling, geen kb_manager-toegang, geen kalibratie.

# 6. Bewust niet

- **Geen zelfgerapporteerde modelzekerheid** in de antwoordprompt (§4.1).
- **Geen band aan de bezoeker tonen** voordat §4.6 laat zien dat `high`
  betrouwbaar is; anders leg je een onbewezen cijfer bij de klant.
- **Geen reden-veld of vrije tekst voor de bezoeker** (besluit Mark, §2).
- **Geen tweede gatenlijst** naast `portal_retrieval_gaps`.
- **Geen transcripten van de interne chat op de kenniskant** zonder apart
  besluit (§4.8).
- **Geen fine-tuning**: de hefboom is kenniscuratie, zoals
  SPEC-CHAT-QUALITY-LOOP-001 §8 al concludeerde.
- **Geen langere retentie** om de werkvoorraad ruimer te maken; de 7 dagen
  zijn een privacybesluit van 11 september en het ontwerp werkt ermee
  (snapshots op de beoordelingsrij).

# 7. Open

1. **Ja voor fase 1 (klasse L)?** Met §3 als verdeling en kb_manager als
   drempel.
2. **Een tweede beoordelaar overschrijft de eerste** (UNIQUE op
   `message_id`). Alternatief is een geschiedenis per beurt; dat is meer
   tabel voor een geval dat bij één of twee kennisbeheerders per tenant
   zelden voorkomt. Advies: overschrijven, `updated_at` bijhouden.
3. **Handmatige gaten en telemetry-gating.** Bij een tenant met telemetry
   op `shadow` wordt ook de handmatig aangemaakte `query_text` geredigeerd
   tot `[REDACTED:shadow]`. Dat is consistent, maar maakt het gat voor de
   KB-auteur onleesbaar; de beoordelingsrij houdt `note` en `kb_slug`, dus
   de auteur weet nog wél waar het hoort. Advies: consistent laten en op de
   gatenpagina bij zo'n rij naar de beoordeling linken.
