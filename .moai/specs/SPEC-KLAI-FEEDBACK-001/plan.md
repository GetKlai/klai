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
6. roadmap en execution tracker worden bijgewerkt zonder dubbel invoerwerk.

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

Geverifieerd:

- Frontend build en deploy groen.
- Portal API quality, Semgrep, Trivy, RLS smoke test en deploy groen.
- Live submit en Platform Feedback-tab werken.
- Productie-incident met detached ORM instances is gefixt met regressietest:
  API response-objecten worden binnen de DB-sessie gematerialiseerd.

Nog niet gebouwd:

- Volledige correctie-flow voor AI-suggesties na eerste live gebruik.
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
  `wont_do`
- `area`
- `priority_score`
- `org_count`, `user_count`
- `external_tracker_type`, `external_tracker_id`, `external_tracker_url`
- `public_feedback_url`: optionele publieke feedback/voting post
- `public_title`, `public_summary`: gecureerde tekst voor roadmap/voting
- `target_window`, `owner`: lichte roadmapplanning zonder nieuw extern systeem
- `created_at`, `updated_at`

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
- Feedback detail drawer toont een compacte "AI voorstel" kaart.
- Staff kan het voorstel accepteren via bestaande flows:
  - link met bestaand item;
  - maak nieuw item;
  - markeer als support;
  - negeer.
- Handmatige correctie blijft beschikbaar onder het voorstel.

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

### Fase 5 - Integraties

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

### Fase 6 - Close the loop

- Bij status `planned`, `in_progress` of `shipped` kan staff gebruikers/orgs
  informeren.
- Begin intern: lijst van affected orgs/users per item.
- Later: opt-in mail of in-app notificatie.

Acceptatie:

- Een shipped item kan alle betrokken feedbackmelders tonen.

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

## Eerstvolgende stap

Verfijn de Fase 3b correctie-flow na eerste live gebruik, zonder nieuwe
handmatige formulierchaos.

Concreet:

1. Observeer echte AI-voorstellen: hoeveel zijn link, nieuw item, support of
   negeer?
2. Maak de primaire knop specifieker waar nodig, bijvoorbeeld "Koppel aan
   Multi-KB chat" of "Maak feature-item".
3. Voeg alleen extra correctie-acties toe als ze in echt gebruik nodig blijken,
   bijvoorbeeld "kies ander bestaand item" of "maak nieuw item met aangepaste
   titel".
4. Houd GitHub Issues en feedback.getklai.com buiten de ruwe inbox.
5. Pas daarna one-click sync toe naar GitHub Issues of feedback.getklai.com,
   altijd vanaf het canonical `feedback_item`.

## Risico's

- Te vroeg een publieke voting portal maken veroorzaakt product-politiek en
  onderhoudswerk.
- Alles direct naar Linear/GitHub sturen veroorzaakt ticket-chaos.
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
