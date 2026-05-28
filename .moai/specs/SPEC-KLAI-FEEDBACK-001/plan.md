# SPEC-KLAI-FEEDBACK-001 - Klai feedback intake, triage en roadmap workflow

Status: in_progress
Date: 2026-05-27

## Doel

Maak feedback geven laagdrempelig voor Klai-gebruikers, maar houd het werk voor
Klai-staff zo klein mogelijk. De gewenste workflow is:

1. gebruiker geeft in-app feedback of meldt een probleem;
2. Klai slaat de ruwe melding met productcontext op;
3. een triage-job maakt automatisch een voorstel;
4. Platform toont een compacte Feedback-tab voor Klai-staff;
5. staff kiest: merge met bestaand item, nieuw item, bug/task, support, negeren;
6. roadmap en execution tracker worden bijgewerkt zonder dubbel invoerwerk;
7. staff markeert het item als opgelost/gefixt/verzonden;
8. betrokken melders krijgen gecontroleerd een in-app en/of e-mail update.

## Huidige situatie

- De first-party assistant staat in
  `klai-portal/frontend/src/features/klai-assistant/KlaiAssistantLauncher.tsx`.
- De huidige intake-API staat in `klai-portal/backend/app/api/app_assistant.py`.
- De assistant-hub is live met drie opties:
  - `Stel een vraag`
  - `Geef feedback`
  - `Meld een probleem`
- `Stel een vraag` opent weer de bestaande Klai Help webchat via
  `/widget/klai-chat.js`; de tijdelijke custom chatflow in de hub is verwijderd.
- Feedback en probleemmeldingen posten naar authenticated first-party endpoints:
  - `/api/app/assistant/feedback`
  - `/api/app/assistant/problem-reports`
- Feedback en probleemmeldingen worden primair opgeslagen in
  `feedback_submissions`.
- `product_events` blijft alleen secundair analytics/audit-signaal. Ruwe
  feedbacktekst hoort daar niet meer in.
- De Platform-admin view bestaat al in deze repo:
  - frontend: `klai-portal/frontend/src/routes/admin/platform`
  - backend: `klai-portal/backend/app/api/admin/platform*.py`
- Platform is nu zichtbaar via de admin sidebar met `platformAdminOnly: true`.
  Backend endpoints gebruiken `require_platform_admin()` en lezen cross-tenant.
- De Platform Feedback-tab is live als triage view op
  `feedback_submissions`, met acties voor support, negeren, item maken en
  linken.
- RLS op `product_events` staat Platform cross-org reads toe via expliciete
  `app.cross_org_admin=true`, niet via een generieke open policy.
- Security/privacy-hardening die al is toegepast:
  - submit endpoints zijn authenticated via `get_caller`;
  - Platform read endpoint gebruikt `require_platform_admin()`;
  - SQL gebruikt SQLAlchemy `select()` + bindings, geen string-concat query;
  - `page_url` en `referer` worden zonder querystring/hash opgeslagen;
  - Platform reads worden geaudit via `_audit(...)`.

## Huidige implementatiestatus

Live op `main`:

- Echte feedback-tabellen met RLS:
  `feedback_submissions`, `feedback_items`, `feedback_item_links`,
  `feedback_triage_suggestions`.
- Backend module `klai-portal/backend/app/klai_feedback/`.
- `/api/app/assistant/feedback` en `/problem-reports` schrijven synchroon naar
  `feedback_submissions`.
- Platform Feedback-tab leest uit `feedback_submissions` in plaats van
  `product_events`.
- Platform detail drawer voor feedback submissions.
- Platform triage-acties:
  - `dismiss`;
  - `mark support`;
  - `create feedback item`;
  - `link to existing feedback item`.
- Simpele duplicate/item search op bestaande `feedback_items`.
- Item-signaal via `org_count`, `user_count` en `priority_score`.
- Platform item-detail toont gekoppelde submissions en item-signaal.
- `feedback_items` heeft lightweight roadmapvelden voor later gebruik:
  `public_title`, `public_summary`, `public_feedback_url`, `target_window`,
  `owner`, `shipped_at` en external tracker velden.
- De item-detail UI is bewust teruggebracht naar menselijke beslissingen:
  status, titel en korte interne notitie.
- Productgebied, type/classificatie, duplicate candidates, publieke tekst,
  GitHub/Fider-links, owner en target window horen niet als leeg handmatig
  formulier in de eerste workflow. Die moeten door AI/systeem voorgesteld of
  via expliciete acties gezet worden.
- De Feedback inbox is gecorrigeerd naar het bestaande Platform list/table
  patroon. Roadmap items zijn geen losse card-layout.
- `triage_suggested` is een interne technische status en wordt niet meer als
  aparte productstatus `AI voorstel` aan staff getoond.
- De triage drawer zoekt eerst naar bestaande items en mag niet standaard een
  nieuw item voorstellen als er een bestaande match is.

Geverifieerd:

- Frontend build en deploy groen.
- Portal API quality, Semgrep, Trivy, RLS smoke test en deploy groen.
- Live submit en Platform Feedback-tab werken.
- Productie-incident met detached ORM instances is gefixt met regressietest:
  API response-objecten worden binnen de DB-sessie gematerialiseerd.

Nog niet gebouwd:

- Resolution/close-the-loop flow: item als gefixt/opgelost markeren,
  betrokken gebruikers/orgs tonen, notificatie opstellen en verzenden.
- In-app notificaties voor betrokken melders.
- Transactionele e-mail naar betrokken melders.
- Volledige audit/status tracking voor notificaties.
- Downstream sync naar GitHub Issues of feedback.getklai.com.

## Kritische herijking na huidige progressie

De grootste les uit de huidige implementatie is dat we niet nog meer
handmatige velden moeten toevoegen. Het risico is niet dat we te weinig data
kunnen opslaan; het risico is dat Klai-staff alsnog zelf productmanager,
supportmedewerker en release-manager tegelijk moet spelen.

Daarom gelden vanaf nu deze ontwerpregels:

1. **Mens beslist, systeem vult voor.**
   Staff kiest vooral status/actie en corrigeert titel/notitie waar nodig.
   Type, productgebied, duplicate candidates, publieke tekst, GitHub/Fider
   links, owner en target window zijn suggesties of systeemacties.
2. **AI mag niet automatisch destructief handelen.**
   AI mag voorstellen: link met item, maak nieuw item, support, negeer,
   urgent bug. Staff accepteert of corrigeert.
3. **Eerst betrouwbaarheid, dan automatisering.**
   Elke nieuwe triage-actie moet tests hebben die sessie-lifetime, RLS-gating
   en response-shape afdekken. Productie mag niet opnieuw de eerste plek zijn
   waar een ORM/session of migratieprobleem zichtbaar wordt.
4. **Geen extra bron van waarheid.**
   `feedback_items` blijft canonical. GitHub Issues en feedback.getklai.com
   krijgen pas later links/sync vanaf een item.
5. **Meer velden tonen is geen betere workflow.**
   Velden die staff niet expliciet hoeft te beslissen blijven verborgen of
   alleen-lezen totdat er een concrete actie voor bestaat.
6. **Een feedback-item is pas af als de melder terugkoppeling kan krijgen.**
   Triage eindigt niet bij `linked` of `shipped`. De flow moet kunnen tonen wie
   iets gemeld heeft, welke update zij krijgen, en of die update is verzonden.
7. **AI is geen workflowstatus.**
   `triage_suggested` is een interne queue-toestand. In de UI blijft dit
   gewoon open/nieuw werk; de AI-output verschijnt als voorstel in de drawer.
8. **Standaard bundelen, niet dupliceren.**
   Als er een relevant bestaand item is, is koppelen de primaire actie. Een
   nieuw item aanmaken is een expliciete fallback, niet de default.

## Belangrijke correctie

`Stel een vraag` moet niet automatisch in product-feedback terechtkomen.
Vragen zijn support/knowledge-intent. Alleen als de triage-classifier duidelijk
ziet dat een vraag eigenlijk productfeedback is, mag die als feedbacksuggestie
verschijnen.

## Open-source / private code beslissing

De huidige Platform-code zit in de publieke repo. Dat is technisch verdedigbaar
zolang alle endpoints goed gated zijn en er geen secrets in staan, maar het is
niet ideaal als Klai later echt open-source wordt:

- het lekt interne Klai-ops concepten en workflow;
- het maakt de public repo minder generiek;
- het vergroot de zichtbare attack surface;
- het is verwarrend voor self-hosters die geen Klai platform-org hebben.

Alleen `klai-feedback` private maken is dus niet genoeg als Platform zelf
publiek blijft. Beter:

1. Houd generieke intake-primitives in de open core.
2. Zet Klai-specifieke staff UI, cross-tenant endpoints, prompts en integraties
   in een private module.
3. Mount die module alleen in Klai-deployments via een expliciete build/runtime
   flag.

Pragmatische tussenstap in deze monorepo:

```text
klai-portal/backend/app/klai_feedback/
  models.py
  schemas.py
  intake.py
  triage.py
  duplicate_detection.py
  integrations/
  routers/
    app_intake.py
    platform_admin.py

klai-portal/frontend/src/features/klai-feedback/
  api.ts
  types.ts
  FeedbackInbox.tsx
  FeedbackItemDrawer.tsx
  FeedbackMergeDialog.tsx

klai-portal/frontend/src/routes/admin/platform/-components/FeedbackTab.tsx
```

Latere private extractie:

```text
private/klai-feedback/
  backend/
  frontend/
  prompts/
  integrations/
```

De Platform-route importeert dan alleen een private mount als
`ENABLE_KLAI_INTERNAL_FEEDBACK=true`. Als die flag uit staat, bestaat de tab
niet in de build.

## Onderzoeksconclusie tools

Geen externe OSS-tool moet de primaire bron van waarheid worden voor Klai's
private in-app feedback. De beste workflow is eigen intake + eigen triage,
met optionele sync naar externe tools.

- Fider: volwassenste keuze voor publieke/semi-publieke voting en roadmap.
  Minder geschikt als primaire private, contextuele B2B triage-inbox.
- Formbricks: sterk voor in-app surveys en gerichte prompts. Geen goede
  canonical roadmap/bug triage laag.
- ClearFlask: completer feedback/roadmap systeem, maar zwaarder en minder
  elegant dan een eigen Platform-tab voor Klai-staff.
- Quackback: moderne richting met widget, roadmap en AI-triage, maar voelt nog
  te jong om kerninfra op te baseren.
- GitHub Issues: goede downstream execution tracker voor concrete engineering
  work, niet als ruwe feedback-inbox.
- Plane: inhoudelijk geschikt voor projectmanagement/roadmap, maar voorlopig
  niet toevoegen zolang GitHub Issues al de engineering tracker is. Plane zou
  een extra bron van waarheid worden.

Beslissing: bouw de primaire feedback- en roadmaplaag lightweight zelf in
Platform. Gebruik `feedback_items` als source of truth voor bundeling,
traceability en klantupdates. Sync later optioneel naar GitHub Issues
execution en feedback.getklai.com/Fider voor publieke voting.

## Datamodel

### `feedback_submissions`

Ruwe in-app meldingen.

- `id`
- `source`: `assistant_feedback`, `assistant_problem`, `assistant_question`,
  later `chat_rating`, `manual_import`
- `raw_text`
- `status`: `new`, `triage_suggested`, `linked`, `dismissed`, `support`
- `org_id`, `user_id`
- `page_url`, `route_id`, `locale`, `viewport`, `user_agent`, `referrer`
- `metadata_json`
- `created_at`

### `feedback_items`

Canonical product needs, bugs of roadmap-items.

- `id`
- `kind`: `feature`, `bug`, `ux_confusion`, `docs`, `support_pattern`
- `title`
- `summary`
- `status`: `inbox`, `under_review`, `planned`, `in_progress`, `shipped`,
  `resolved`, `wont_do`
- `area`
- `priority_score`
- `org_count`, `user_count`
- `external_tracker_type`, `external_tracker_id`, `external_tracker_url`
- `public_feedback_url`: optionele publieke feedback/voting post
- `public_title`, `public_summary`: gecureerde tekst voor roadmap/voting
- `target_window`, `owner`: lichte roadmapplanning zonder nieuw extern systeem
- `resolution_summary`: interne/klantvriendelijke samenvatting van de fix
- `resolved_at`: wanneer het item inhoudelijk klaar is
- `resolved_by`: Klai-staff user id
- `notification_state`: `not_needed`, `draft`, `queued`, `partially_sent`,
  `sent`, `failed`
- `created_at`, `updated_at`

Statuslabels in de UI zijn contextafhankelijk:

- `resolved` op een bug toont als `Gefixt`.
- `resolved` op docs/support/UX toont als `Opgelost`.
- `shipped` blijft bruikbaar voor echte feature launches/public roadmap taal.

Voorlopig mag de implementatie `shipped` intern blijven gebruiken als migreren
te duur is, maar de UI en het plan moeten onderscheid maken tussen "feature is
verzonden" en "klantbug is gefixt".

### `feedback_item_links`

Koppelt submissions als evidence/upvote aan canonical items.

- `item_id`
- `submission_id`
- `link_type`: `upvote`, `evidence`, `bug_repro`, `support_signal`
- `confidence`
- `created_by`: `ai`, `staff`

### `feedback_triage_suggestions`

AI-output die staff kan accepteren of corrigeren.

- `submission_id`
- `classification`
- `summary`
- `suggested_area`
- `suggested_severity`
- `duplicate_candidates_json`
- `suggested_action`
- `model`
- `created_at`

### `feedback_notifications`

Per-recipient close-the-loop records. Dit is bewust een aparte tabel en geen
los e-mail-logje, zodat we later in-app, mail en audit op dezelfde bron kunnen
baseren.

- `id`
- `item_id`
- `submission_id`
- `org_id`
- `user_id`
- `recipient_email`
- `channel`: `in_app`, `email`
- `status`: `draft`, `queued`, `sent`, `failed`, `skipped`
- `subject`
- `body`
- `generated_by`: `ai`, `staff`, `system`
- `sent_at`
- `error`
- `created_at`, `updated_at`

De ontvangers worden afgeleid uit `feedback_item_links -> feedback_submissions`.
Staff typt dus geen ontvangers over.

## Workflow

### Fase 1 - Persistente intake

Status: klaar en live.

Klaar:

- First-party assistant endpoints bestaan en zijn authenticated.
- Feedback/probleemmeldingen worden synchroon vastgelegd in
  `feedback_submissions`.
- `product_events` blijft bestaan als secundaire analytics/audit eventstream.
- Platform kan feedback-submissions cross-org lezen via gated admin endpoint.
- Context wordt beperkt opgeslagen zonder URL querystrings/fragments.
- `questions` worden niet in `feedback_submissions` geschreven; alleen een
  latere support-to-product classifier mag daar feedbacksuggesties van maken.

Acceptatie:

- Nieuwe feedback is zichtbaar in database zonder product-event query.
- Geen cross-tenant leakage: tenant-gebruiker kan alleen eigen submission maken.

### Fase 2 - Platform Feedback tab

Status: klaar en live.

Klaar:

- `Feedback` tab bestaat in `/admin/platform`.
- Read-only endpoint bestaat:
  `/api/admin/platform/feedback-submissions`.
- Read-only endpoint leest uit `feedback_submissions`.
- Search/refresh basis is aanwezig.
- Endpointgroep bestaat onder `/api/admin/platform/feedback/*`:
  - `/feedback/submissions` als nieuwe alias naast de bestaande
    `/feedback-submissions`;
  - `/feedback/items`;
  - `/feedback/submissions/{id}/dismiss`;
  - `/feedback/submissions/{id}/support`;
  - `/feedback/submissions/{id}/items`;
  - `/feedback/submissions/{id}/links`.
- Alle endpoints gebruiken `require_platform_admin()`.
- UI toont inbox, status, detail drawer, org/user/context en acties:
  `Link`, `Maak item`, `Support`, `Negeer`.
- Acties werken op `feedback_submissions`, `feedback_items` en
  `feedback_item_links`.
- Simpele non-AI duplicate search zoekt op item titel, samenvatting en area.

Nog te doen:

- Filters uitbreiden naar status, org, type, productgebied en datum.
- Detail drawer verder verfijnen op basis van echt gebruik, maar geen extra
  lege invoervelden toevoegen zonder duidelijke handmatige beslissing.

Acceptatie:

- Staff kan zonder SQL feedback bekijken en triageren.
- Search/filter op status, org, type, productgebied en datum wordt in Fase 3/4
  afgemaakt zodra er meer items en signalen zijn.

### Fase 3 - AI triage en duplicate detectie

Status: Fase 3a en de eerste Fase 3b zijn gebouwd in code. De eerstvolgende
productstap is de correctie-flow verfijnen op basis van live gebruik.

#### Fase 3a - Suggesties genereren

- Background task wordt ingepland na elke feedback/probleem submission.
- Idempotent: dezelfde submission krijgt maximaal één suggestie per
  model/config versie.
- Output wordt opgeslagen in `feedback_triage_suggestions`; geen automatische
  wijziging aan `feedback_items` of `feedback_submissions` behalve eventueel
  status `triage_suggested`.
- Als AI faalt, blijft de handmatige workflow werken.
- Model/config versie wordt opgeslagen als `model:feedback-triage-v1`.
- Er is een unieke index op `submission_id + model` om race conditions te
  voorkomen.

Acceptatie:

- Nieuwe feedback krijgt een suggestie zonder dat staff iets hoeft in te vullen.
- Een AI-fout veroorzaakt geen submit-failure voor de gebruiker.
- Suggestie bevat model/config metadata zodat output later te auditen is.
- Targeted tests dekken idempotentie, AI-failure fallback en duplicate
  candidate filtering.

#### Fase 3b - Suggesties gebruiken in Platform

Gebouwd:

- Platform response bevat de nieuwste AI-suggestie per submission.
- Duplicate candidates worden verrijkt met itemtitel, status, kind en area.
- Feedback detail drawer toont een compacte voorstelkaart. De UI noemt dit
  niet meer `AI voorstel` als status.
- Staff kan het voorstel accepteren via bestaande flows:
  - link met bestaand item;
  - maak nieuw item;
  - markeer als support;
  - negeer.
- Handmatige correctie blijft beschikbaar achter een expliciete
  `Corrigeer`-actie, zodat staff niet eerst door lege formulieren hoeft.
- Technische context zoals route, URL en user-id wordt opgeslagen maar niet als
  primair bewerkveld getoond.
- Probleemmeldingen krijgen expliciet een bug-voorstel van de AI-triage,
  tenzij ze duidelijk support/docs/configuratie zijn.
- De primaire actie moet bestaande matches prefereren. Als de zoekactie of
  duplicate candidates een bestaand item vinden, is `Koppel aan bestaand item`
  de primaire actie. `Maak nieuw item` verschijnt pas als fallback of onder
  `Andere actie`.

- Output:
  - korte samenvatting;
  - type;
  - productgebied;
  - severity/urgency;
  - duplicate candidates;
  - voorgestelde actie.
- UI toont suggesties als voorstel, niet als verplicht formulier:
  - "lijkt op bestaand item X";
  - "maak nieuw item";
  - "support";
  - "negeer";
  - "bug met hoge urgentie".
- Staff accepteert of corrigeert de suggestie; alleen correcties vragen input.
- Duplicate detectie start simpel:
  - tekst-normalisatie + existing item title/summary vergelijking;
  - later embeddings via bestaande AI/embedding infra of aparte Qdrant collectie.

Acceptatie:

- Elke nieuwe submission krijgt binnen korte tijd een suggestie.
- Staff kan AI-suggestie accepteren of overschrijven.
- Accepteren van "link met bestaand item" gebruikt dezelfde bestaande
  `feedback_item_links` flow.
- Accepteren van "nieuw item" gebruikt dezelfde bestaande create-item flow.

### Fase 4 - Roadmap-items en upvotes

Status: Fase 4-light klaar; verdere roadmapautomatisering na Fase 3.

- Maak `feedback_items` het canonical product-backlog niveau.
- Een nieuwe submission wordt meestal evidence/upvote op een bestaand item.
- Roadmap leeft eerst lightweight in Platform, niet in Plane:
  - `feedback_items.status` is de roadmapstatus;
  - itemdetail toont gekoppelde submissions, orgs/users en externe links;
  - GitHub issue en feedback.getklai.com post zijn optionele links vanaf het
    item, geen primaire bron.
- Roadmap item list gebruikt hetzelfde Platform table/list patroon als de
  feedback inbox. Geen losse card-layout voor dezelfde soort beheerdata.
- Prioriteit wordt automatisch berekend op:
  - aantal orgs;
  - aantal users;
  - severity;
  - recency;
  - plan/ARR later optioneel;
  - handmatige staff override.

Acceptatie:

- Een roadmap-item toont alle gekoppelde feedback snippets.
- Nieuwe duplicaten verhogen item-signaal zonder nieuwe ticket-chaos.
- Bij status `shipped` kan Klai alle betrokken orgs/users terugvinden voor
  close-the-loop communicatie.

### Fase 5 - Resolution en klantnotificaties

Status: eerstvolgende productstap.

Doel: een bug/request is niet klaar als alleen de code gefixt is. Staff moet in
Platform kunnen vastleggen dat een item is opgelost en de gekoppelde melders
kunnen informeren zonder handmatig zoeken of overtypen.

#### Fase 5a - Resolve actie

- Voeg op `feedback_items` een expliciete actie toe:
  - `Markeer als gefixt` voor `kind=bug`;
  - `Markeer als opgelost` voor support/docs/UX;
  - `Markeer als verzonden` voor features.
- De actie opent een modal/drawer, niet direct een destructieve statuschange.
- Modal toont:
  - itemtitel;
  - gekoppelde submissions;
  - betrokken orgs/users;
  - beschikbare e-mailadressen;
  - AI-concept voor klantvriendelijke update;
  - kanaalkeuze: `in-app`, `e-mail`, `beide`, `geen notificatie`.
- Staff kan de update aanpassen en bevestigen.

Acceptatie:

- Staff kan een item als opgelost markeren zonder SQL.
- Een opgelost item toont `resolved_at`, `resolved_by` en
  `resolution_summary`.
- Bugs tonen in de UI `Gefixt`, niet `Verzonden`.

#### Fase 5b - Notification records

- Maak `feedback_notifications` records per recipient + kanaal.
- Deduplicate per item/user/channel zodat iemand niet twee keer dezelfde
  update krijgt door meerdere submissions.
- Bewaar status per notification: `draft`, `queued`, `sent`, `failed`,
  `skipped`.
- Audit elke staff-confirmatie en elke verzendpoging.

Acceptatie:

- Platform kan tonen wie wel/niet geïnformeerd is.
- Een mislukte mail blokkeert de itemstatus niet, maar blijft zichtbaar als
  `failed`.
- Staff kan failed notifications later opnieuw proberen.

#### Fase 5c - In-app notificatie

- Start simpel: een notification inbox/toast in de Klai app, gekoppeld aan de
  ingelogde user.
- Toon alleen updates voor eigen `user_id` binnen eigen org.
- Link vanuit de notification naar relevante app-context als die veilig
  beschikbaar is.
- Geen cross-tenant Platform data in tenant-facing payloads.

Acceptatie:

- Een gebruiker die feedback meldde ziet in-app dat het item is opgelost.
- Tenant-users kunnen nooit notificaties van andere orgs lezen.

#### Fase 5d - E-mail

- Gebruik de bestaande transactionele mail-infra.
- Verstuur alleen naar bekende, geverifieerde user-e-mailadressen.
- Mail bevat:
  - korte klantvriendelijke update;
  - product/contextnaam waar relevant;
  - geen interne Platform labels, issue IDs of andere klantdata;
  - preference/unsubscribe-gedrag conform bestaande productmail-regels.
- Bounces/failures worden teruggeschreven naar `feedback_notifications`.

Acceptatie:

- Staff kan een resolved item mailen naar gekoppelde melders.
- Verzonden mails zijn auditable per item/submission/user.
- Geen e-mail naar users zonder geldig adres of met opt-out.

### Fase 6 - Integraties

- Start met Slack/email digest:
  - nieuwe high-severity bugs;
  - snel stijgende requests;
  - enterprise/high-value org feedback;
  - items zonder triage ouder dan X dagen.
- Daarna execution sync:
  - GitHub Issues voor repo-bound engineering work;
  - feedback.getklai.com/Fider voor gecureerde publieke voting;
  - Plane alleen heroverwegen als GitHub Issues niet genoeg blijkt voor
    execution/roadmap planning.

Acceptatie:

- Staff hoeft feedback niet handmatig over te typen naar execution tooling.
- Een external issue link komt terug op het feedback item.

### Fase 7 - Public roadmap/voting

- Alleen gecureerde items worden gepubliceerd naar feedback.getklai.com/Fider
  of een vergelijkbare publieke/semi-publieke laag.
- Public roadmap is downstream van `feedback_items`, niet andersom.
- Private klantmeldingen en ruwe context blijven in Platform.

Acceptatie:

- Een public roadmap post kan teruglinken naar het canonical internal item.
- Staff kan public copy genereren/aanpassen zonder ruwe klantfeedback te lekken.

## Eerste implementatiepakket

- [x] Kleur/UI-correctie van de assistant widget.
- [x] `Stel een vraag` teruggezet naar de bestaande Klai Help widget-flow.
- [x] Feedback/probleem forms werkend gemaakt in de Klai assistant.
- [x] Tijdelijke persistence via `product_events`.
- [x] Platform Feedback tab met read-only event inbox.
- [x] Security fix: Platform read endpoint gated met `require_platform_admin()`.
- [x] RLS fix: `product_events` cross-org read alleen via
  `app.cross_org_admin=true`.
- [x] Privacy fix: `page_url` en `referer` zonder querystring/hash.
- [x] Alembic migratie + SQLAlchemy models voor `feedback_submissions`,
  `feedback_items`, `feedback_item_links`, `feedback_triage_suggestions`.
- [x] Verplaats assistant feedback persistence naar `app/klai_feedback`.
- [x] Platform Feedback-tab leest uit `feedback_submissions`.
- [x] Platform Feedback tab uitbreiden met detail drawer.
- [x] Acties: dismiss, create item, link to item, mark support.
- [x] Eenvoudige non-AI duplicate search.
- [x] AI triage job met idempotente suggestie-opslag.
- [x] AI voorstelkaart in Platform met acceptactie via bestaande flows.
- [x] Correctie-flow versimpeld: AI eerst, handmatige forms pas na
  `Corrigeer`, context ingeklapt.
- [x] `AI voorstel` verwijderd als zichtbare status/filter; het is alleen een
  interne technische toestand.
- [x] Roadmap item view teruggebracht naar het standaard Platform table/list
  patroon.
- [x] Triage default gecorrigeerd: eerst zoeken/koppelen aan bestaand item,
  nieuw item alleen als fallback of expliciete andere actie.

## Eerstvolgende stap

Build Fase 5a/5b: resolution + notification records. Zonder dit blijft de flow
stuk zodra een echte klantbug is opgelost, omdat Klai-staff niet kan vastleggen
dat het gefixt is en de melder niet betrouwbaar kan informeren.

Concreet:

1. Voeg datamodel/migratie toe voor `feedback_notifications` en resolution
   velden op `feedback_items`.
2. Voeg backend endpoints toe:
   - `POST /api/admin/platform/feedback/items/{id}/resolve-draft`
   - `POST /api/admin/platform/feedback/items/{id}/resolve`
   - `POST /api/admin/platform/feedback/notifications/{id}/retry`
3. Voeg Platform UI toe in item-detail:
   - knop `Markeer als gefixt/opgelost/verzonden`;
   - modal met affected users/orgs;
   - AI/system conceptbericht;
   - kanaalkeuze in-app/e-mail/beide/geen notificatie.
4. Implementeer in-app notification persistence en tenant-facing read endpoint.
5. Implementeer e-mail enqueue via bestaande transactionele mail-infra.
6. Voeg tests toe voor:
   - recipient dedupe;
   - cross-tenant RLS/auth;
   - no-email/no-opt-in handling;
   - failed notification retry;
   - bug label `Gefixt` versus feature label `Verzonden`.
7. Pas daarna pas GitHub Issues of feedback.getklai.com sync verder aan.

## Risico's

- Te vroeg een publieke voting portal maken veroorzaakt product-politiek en
  onderhoudswerk.
- Alles direct naar Linear/GitHub sturen veroorzaakt ticket-chaos.
- Een item als `shipped` markeren zonder close-the-loop veroorzaakt verloren
  klantvertrouwen: de melder hoort niets terwijl Klai de fix wel weet.
- Notificaties zonder per-recipient audit veroorzaken support-onduidelijkheid:
  "wie heeft deze update gehad?" moet altijd beantwoordbaar zijn.
- E-mail zonder preference/opt-out check kan privacy/compliance problemen
  veroorzaken.
- `question` en `feedback` mengen maakt roadmap data rommelig.
- Alleen de frontend tab verbergen is geen security boundary; backend moet
  altijd `require_platform_admin()` gebruiken.
- Een private `klai-feedback` module is nuttig, maar Platform zelf moet ook
  private/flagged worden als de repo open-source wordt.

## Bronnen

- Fider: https://docs.fider.io/ en https://github.com/getfider/fider
- Formbricks: https://formbricks.com/in-app-survey en https://github.com/formbricks/formbricks
- ClearFlask: https://clearflask.com/ en https://github.com/clearflask/clearflask
- Quackback: https://www.quackback.io/ en https://github.com/QuackbackIO/quackback
- Plane: https://github.com/makeplane/plane
- GitHub issue forms: https://docs.github.com/communities/using-templates-to-encourage-useful-issues-and-pull-requests/syntax-for-issue-forms
- GitHub Discussions: https://docs.github.com/en/discussions
