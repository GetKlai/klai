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
- Submissions worden op dit moment nog als product events opgeslagen:
  - `klai_assistant.question`
  - `klai_assistant.feedback`
  - `klai_assistant.problem_report`
- `emit_event(...)` schrijft naar `product_events`. Dat is bruikbaar als
  tijdelijke capture/audit, maar niet als goede triage- of roadmapbron.
- De Platform-admin view bestaat al in deze repo:
  - frontend: `klai-portal/frontend/src/routes/admin/platform`
  - backend: `klai-portal/backend/app/api/admin/platform*.py`
- Platform is nu zichtbaar via de admin sidebar met `platformAdminOnly: true`.
  Backend endpoints gebruiken `require_platform_admin()` en lezen cross-tenant.
- De Platform Feedback-tab is live als read-only view op recente assistant
  submissions via `/api/admin/platform/feedback-submissions`.
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

- `6d5e628e` - assistant kleur/UI herstel en planbasis.
- `7b541120` - `Stel een vraag` opent de bestaande Klai Help widget-flow.
- `543239b1` - Platform Feedback-tab toont assistant submissions.
- `6945da1e` - Platform feedback query naar SQLAlchemy `select()` omgezet.
- `515ecdc9` - RLS policy laat Platform `product_events` cross-org lezen.
- `c4c5bed8` - feedback context stript query/hash uit `page_url`.
- `41c4efc9` - feedback context stript query/hash uit `referer`.

Geverifieerd:

- Frontend build en deploy groen.
- Portal API quality, Semgrep, Trivy, RLS smoke test en deploy groen.
- Live submit en Platform Feedback-tab werken.

Nog niet gebouwd:

- Echte feedback-tabellen (`feedback_submissions`, `feedback_items`, links,
  triage suggestions).
- Triage-acties in Platform (`dismiss`, `create item`, `link`, `support`).
- Canonical feedback items / upvote-evidence model.
- AI triage en duplicate detectie.
- Downstream sync naar Linear/GitHub/Plane.

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
- Plane/GitHub Issues: goed als downstream execution tracker, niet als intake.

Beslissing: bouw de primaire feedbacklaag zelf, sync later naar Linear/GitHub
of Plane. Gebruik Fider alleen als er echt een publiek voting board nodig is.

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

Status: gedeeltelijk klaar.

Klaar:

- First-party assistant endpoints bestaan en zijn authenticated.
- Feedback/probleemmeldingen worden tijdelijk betrouwbaar vastgelegd via
  `product_events`.
- Platform kan deze events cross-org lezen via gated admin endpoint.
- Context wordt beperkt opgeslagen zonder URL querystrings/fragments.

Nog te doen:

- Voeg echte feedbacktabellen toe naast `product_events`.
- Laat `/api/app/assistant/feedback` en `/problem-reports` naar
  `feedback_submissions` schrijven.
- Laat `question` alleen in feedback terechtkomen als support-to-product
  classifier dat later suggereert.
- Blijf `product_events` emitten voor analytics/audit.

Acceptatie:

- Nieuwe feedback is zichtbaar in database zonder product-event query.
- Geen cross-tenant leakage: tenant-gebruiker kan alleen eigen submission maken.

### Fase 2 - Platform Feedback tab

Status: read-only basis klaar, triage UI nog niet klaar.

Klaar:

- `Feedback` tab bestaat in `/admin/platform`.
- Read-only endpoint bestaat:
  `/api/admin/platform/feedback-submissions`.
- Search/refresh basis is aanwezig.

Nog te doen:

- Vervang het tijdelijke read-only endpoint door een feedback endpointgroep:
  `/api/admin/platform/feedback/submissions`,
  `/api/admin/platform/feedback/items`,
  `/api/admin/platform/feedback/link`,
  `/api/admin/platform/feedback/dismiss`.
- Alle endpoints gebruiken `require_platform_admin()`.
- UI toont:
  - inbox links;
  - detail drawer rechts;
  - org/user/context;
  - duplicate/item suggestions;
  - acties: `Merge`, `New item`, `Bug/task`, `Support`, `Dismiss`.

Acceptatie:

- Staff kan zonder SQL feedback bekijken en triageren.
- Search/filter op status, org, type, productgebied en datum.

### Fase 3 - AI triage en duplicate detectie

- Background job na elke submission.
- Output:
  - korte samenvatting;
  - type;
  - productgebied;
  - severity/urgency;
  - duplicate candidates;
  - voorgestelde actie.
- Duplicate detectie start simpel:
  - tekst-normalisatie + existing item title/summary vergelijking;
  - later embeddings via bestaande AI/embedding infra of aparte Qdrant collectie.

Acceptatie:

- Elk nieuw item krijgt binnen korte tijd een suggestie.
- Staff kan AI-suggestie accepteren of overschrijven.

### Fase 4 - Roadmap-items en upvotes

- Maak `feedback_items` het canonical product-backlog niveau.
- Een nieuwe submission wordt meestal evidence/upvote op een bestaand item.
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

### Fase 5 - Integraties

- Start met Slack/email digest:
  - nieuwe high-severity bugs;
  - snel stijgende requests;
  - enterprise/high-value org feedback;
  - items zonder triage ouder dan X dagen.
- Daarna execution sync:
  - Linear als Klai intern Linear gebruikt;
  - GitHub Issues voor repo-bound engineering work;
  - Plane alleen als open-source/self-hosted tracker gewenst is.

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
- [ ] Alembic migratie + SQLAlchemy models voor `feedback_submissions`,
  `feedback_items`, `feedback_item_links`, `feedback_triage_suggestions`.
- [ ] Verplaats assistant feedback persistence naar `app/klai_feedback`.
- [ ] Platform Feedback tab uitbreiden met detail drawer.
- [ ] Acties: dismiss, create item, link to item, mark support.
- [ ] Eenvoudige non-AI duplicate search.
- [ ] AI triage job pas daarna.

## Eerstvolgende stap

Maak Fase 1 af met echte, private feedback persistence naast `product_events`:

1. Voeg Alembic migratie en ORM models toe voor:
   - `feedback_submissions`
   - `feedback_items`
   - `feedback_item_links`
   - `feedback_triage_suggestions`
2. Laat `/api/app/assistant/feedback` en `/problem-reports` synchroon naar
   `feedback_submissions` schrijven, zodat een succesvolle submit ook echt
   persisted is.
3. Blijf daarnaast `product_events` emitten voor analytics/audit, maar gebruik
   `feedback_submissions` als bron voor Platform.
4. Verplaats feedback-specifieke backendcode naar
   `klai-portal/backend/app/klai_feedback/`.
5. Pas de Platform Feedback-tab aan om uit de nieuwe tabellen te lezen.

Waarom dit eerst: zolang Platform uit `product_events` leest, hebben we een
werkende tijdelijke inbox, maar nog geen robuuste basis voor status, merge,
triage, assignees, roadmap-items of retention.

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
