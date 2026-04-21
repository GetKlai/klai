---
id: SPEC-WIDGET-001
version: 0.2.0
status: draft
created: 2026-04-16
updated: 2026-04-16
author: Mark Vletter
priority: high
issue_number: 0
---

# SPEC-WIDGET-001 — Klai Chat Widget

## HISTORY

- **v0.2.0 (2026-04-16)**: Architectuurwijziging na review. Integratie-entiteit krijgt een `integration_type` (`api` vs `widget`). Widget-type gebruikt een publiek `wgt_...` ID in de embed snippet (nooit een `pk_live_...` API key in de browser). Authenticatie loopt via een short-lived session token: widget-config endpoint geeft token terug, widget gebruikt token voor chat calls. Admin create-flow begint met type-keuze.
- **v0.1.0 (2026-04-16)**: Initiële draft. Widget als optionele extensie op Partner API integratie, FlowiseChatEmbed fork, CDN-hosted bundle, escalatie gedocumenteerd als toekomstige uitbreiding.

## Goal

Een embeddable JavaScript chat widget leveren die klanten met één script tag op hun eigen website kunnen plaatsen, zodat eindgebruikers van die website rechtstreeks kunnen chatten met een Klai knowledge base via streaming AI-antwoorden. De widget is een apart integratie-type (`widget`) naast het bestaande `api`-type — beide werken op dezelfde integratie-entiteit (SPEC-API-001) met dezelfde KB-scoping en rate limits, maar de widget exposeert nooit een `pk_live_...` API key in de browser. Identificatie gaat via een publiek `wgt_...` ID; authenticatie via een short-lived session token dat server-side wordt uitgegeven.

## Success Criteria

- Een admin kan bij aanmaken van een nieuwe integratie kiezen voor type "widget", waarna een `wgt_...` ID wordt gegenereerd en een embed snippet beschikbaar is — zonder dat de `pk_live_...` API key ooit in de browser verschijnt.
- De gegenereerde embed snippet werkt op elke externe website met één `<script data-widget-id="wgt_...">` tag zonder aanvullende JavaScript.
- Widget bundle is kleiner dan 200 kB (gzipped) en laadt async zonder de host-pagina te blokkeren.
- Streaming chat-responses hebben dezelfde p95 TTFT als de Partner API (< 1500 ms) — de widget voegt geen meetbare extra latency toe.
- Origin-validatie blokkeert requests vanaf niet-toegestane domeinen met HTTP 403.
- Widget ondersteunt minimaal NL en EN configuratie-UI in het admin portal (Paraglide i18n).

## Environment

Deze SPEC bouwt voort op het environment van **SPEC-API-001** (Partner API) en erft alle bestaande componenten:

- **Partner API** (`api.getklai.com/partner/v1/`): bestaand, deployed, live. Levert `/chat/completions` streaming endpoint met Bearer API key authenticatie, KB-scoping per key, en rate limiting.
- **Admin Portal** (`klai-portal/`): bestaand Next.js portal met `/admin/integrations` views voor Partner API key beheer.
- **Database**: bestaande `partner_api_keys` tabel met `id`, `org_id`, `key_hash`, `knowledge_base_ids`, `rate_limit` kolommen.

Nieuwe componenten (delta):

- **klai-widget repo** [NEW]: losse repository voor de FlowiseChatEmbed fork (SolidJS + Vite), met CI/CD naar `https://cdn.getklai.com/widget/`.
- **CDN**: Hetzner Object Storage (of equivalent) voor statische bundle hosting, achter cache-enabled HTTPS endpoint.

## Assumptions

- De Partner API (SPEC-API-001) is stabiel, gedocumenteerd en in productie. Het `/chat/completions` endpoint werkt met OpenAI-compatibele streaming SSE.
- `@microsoft/fetch-event-source` in FlowiseChatEmbed ondersteunt de SSE-formaat die de Partner API al levert.
- FlowiseChatEmbed (MIT licentie, commit-pinned) is stabiel genoeg om te forken zonder significante upstream-breaking changes binnen de initiële ontwikkelperiode.
- De `pk_live_...` API key (Partner API type) blijft een intern credential en verschijnt nooit in de browser bij widget-type integraties. Widgets identificeren zichzelf met een publiek `wgt_...` ID; authenticatie loopt via een short-lived session token dat server-side aan de integratie-entiteit gekoppeld is.
- Klanten zijn verantwoordelijk voor het correct invullen van `allowed_origins` voordat ze de widget publiceren. Lege lijst = geen origin werkt (fail-closed).
- CDN hosting (Hetzner Object Storage) is al beschikbaar of wordt als infrastructuurtaak buiten deze SPEC geregeld.

## Out of Scope

De volgende items zijn expliciet **NIET** onderdeel van v1:

- **Escalatie UI in de widget**: geen "Escaleer naar medewerker" knop, geen live-chat redirect, geen summary-generatie endpoint. Design is wel gedocumenteerd in "Toekomstige uitbreidingen" voor v2.
- **Visuele CSS editor in het portal**: geen color picker, geen preview-pane, geen WYSIWYG. Admin vult raw CSS variabele-waardes in een key-value tabel.
- **Meerdere widgets per integratie**: elke integratie heeft precies één widget-configuratie. Twee verschillende widgets = twee verschillende Partner API keys = twee verschillende integraties.
- **Widget analytics dashboard**: geen aparte metrics UI voor widget conversaties. Verkeer is zichtbaar via bestaande Partner API metrics (product_events).
- **Custom fonts / uitgebreide theming**: alleen de vier gedefinieerde CSS-variabelen in v1. Geen font-loading, geen image-upload, geen custom HTML.
- **Conversatiegeheugen tussen sessies**: geen localStorage-persistentie van chat history tussen page reloads in v1.
- **Widget voor niet-chat use cases**: geen zoekbalk-widget, geen aanbevelingswidget — alleen chat.

## Requirements

### REQ-1: Data model — integratie-type en widget-identiteit [MODIFY]

**Ubiquitous**: Het systeem **shall** de integratie-entiteit uitbreiden met een `integration_type` veld dat bepaalt via welke interface de rechten worden geëxposed.

**Event-Driven**: WHEN een admin een nieuwe integratie aanmaakt THEN shall het systeem vragen welk type: `api` (geeft `pk_live_...` Bearer token) of `widget` (geeft `wgt_...` publiek ID + embed snippet). Het type is na aanmaken onveranderlijk.

**State-Driven**: WHILE `integration_type = 'api'`, THE SYSTEM shall de bestaande Partner API authenticatie-flow hanteren (REQ-2 van SPEC-API-001) en géén widget-endpoints activeren voor deze integratie.

**State-Driven**: WHILE `integration_type = 'widget'`, THE SYSTEM shall de `pk_live_...` key uitsluitend intern gebruiken (nooit tonen in de UI na aanmaken) en géén API key in embed snippets opnemen.

**Unwanted**: IF een request binnenkomt op een widget-endpoint met een integratie-ID waarvan `integration_type = 'api'` THEN shall het systeem HTTP 403 returnen.

Implementatie-eisen:

- [NEW] Alembic migratie `add_integration_type_and_widget_id` met:
  - `integration_type VARCHAR(10) NOT NULL DEFAULT 'api'` — enum-waarden `api`, `widget`
  - `widget_id VARCHAR(64) NULL UNIQUE` — gegenereerd als `wgt_` + 40 hex chars, alleen ingevuld voor `widget`-type
  - `widget_config JSONB NULL` — velden: `allowed_origins` (lijst strings), `title` (string), `welcome_message` (string), `css_variables` (object)
- [MODIFY] SQLAlchemy model `PartnerAPIKey` uitgebreid; bestaande `api`-type records blijven ongewijzigd (default).
- Default `allowed_origins` is lege lijst. Lege lijst = géén origin werkt (fail-closed).

### REQ-2: Widget bootstrap endpoint [NEW]

**Ubiquitous**: Het systeem **shall** een nieuw publiek endpoint `GET /partner/v1/widget-config?id=<wgt_id>` aanbieden dat de widget-configuratie en een short-lived session token retourneert. Dit endpoint vereist géén `Authorization` header — de `wgt_...` ID is het publieke identificatiemiddel.

**Event-Driven**: WHEN de widget JS laadt in de browser THEN shall de widget een request doen naar `GET /partner/v1/widget-config?id=<wgt_id>` met de Origin header die de browser automatisch meestuurt.

**State-Driven**: IF de `wgt_id` een geldige `widget`-type integratie identificeert AND de Origin header exact matcht een entry in `widget_config.allowed_origins` THEN shall het systeem HTTP 200 returnen met body:

```json
{
  "title": "string",
  "welcome_message": "string",
  "css_variables": {},
  "chat_endpoint": "https://api.getklai.com/partner/v1/chat/completions",
  "session_token": "<jwt>",
  "session_expires_at": "<ISO-8601>"
}
```

**Ubiquitous**: Het session token **shall** een short-lived JWT zijn (TTL: 1 uur) dat server-side gegenereerd wordt en de widget-integratie identificeert. De widget gebruikt dit token als `Authorization: Bearer <session_token>` header voor chat calls — nooit de `pk_live_...` API key.

**Unwanted**: IF de Origin header NIET exact matcht een entry in `allowed_origins` THEN shall het systeem HTTP 403 returnen met body `{ "detail": "Origin not allowed" }` EN géén `Access-Control-Allow-Origin` header voor de ongeautoriseerde origin teruggeven.

**Unwanted**: IF de `wgt_id` onbekend is of de bijbehorende integratie `integration_type != 'widget'` heeft THEN shall het systeem HTTP 404 returnen.

**Unwanted**: IF de Origin header ontbreekt THEN shall het systeem HTTP 403 returnen (fail-closed).

Implementatie-eisen:

- [NEW] Nieuwe route toegevoegd aan bestaande `partner_api.py` router. Geen authenticatie-dependency — lookup is via `wgt_id` query-parameter.
- Session token is een signed JWT (HS256 of RS256) met claims: `wgt_id`, `org_id`, `kb_ids`, `exp`.
- JWT signing key is projectintern (SOPS-managed secret), niet de `pk_live_...` key.
- Origin validatie ondersteunt exact-match op scheme + host + port (bijv. `https://example.com` matcht niet `http://example.com` en niet `https://example.com:8080`).
- De `chat_endpoint` in de response is de URL die de widget gebruikt voor streaming chat calls; de Partner API valideert het session token op die endpoint.

### REQ-3: Klai Chat Widget JS build [NEW]

**Ubiquitous**: Het systeem **shall** een zelfstandig gehoste JavaScript bundle leveren op `https://cdn.getklai.com/widget/klai-chat.js` die de chat widget initialiseert.

**Event-Driven**: WHEN het `<script>` tag geladen wordt op een externe pagina THEN shall de widget het `data-widget-id` attribuut uitlezen EN shall een request naar `GET /partner/v1/widget-config?id=<wgt_id>` doen EN — na succesvolle response — de widget-bubble renderen rechtsonder in de viewport (of waar de host-pagina positie bepaalt via CSS). Het `data-widget-id` attribuut is het enige vereiste attribuut.

**Event-Driven**: WHEN een eindgebruiker een bericht typt en verstuurt THEN shall de widget een streaming POST request doen naar `chat_endpoint` (uit widget-config response) met `Authorization: Bearer <session_token>` header EN shall tokens real-time renderen zodra ze via SSE binnenkomen.

**State-Driven**: WHILE een streaming-response actief is, THE SYSTEM shall een typing-indicator tonen EN shall de input-box disable'n EN shall een stop-knop tonen waarmee de gebruiker de stream kan afbreken.

**Optional**: Where `data-title` of `data-welcome` aanwezig is op het `<script>` tag, THE SYSTEM shall die waardes gebruiken in plaats van de server-side defaults uit `widget_config`.

**Unwanted**: IF het `widget-config` request faalt (403 origin, 404 onbekende widget-id, netwerkfout) THEN shall de widget géén chat-bubble tonen EN shall één `console.error` loggen met een duidelijke foutcode (`KLAI_WIDGET_ORIGIN_NOT_ALLOWED`, `KLAI_WIDGET_NOT_FOUND`) — géén silent failure en géén infinite loading spinner.

**Unwanted**: IF het session token verlopen is tijdens een actieve sessie (401 op chat endpoint) THEN shall de widget automatisch een nieuw session token ophalen via `/partner/v1/widget-config?id=<wgt_id>` en het mislukte request herhalen — maximaal één keer, daarna foutmelding.

Implementatie-eisen:

- [NEW] Nieuwe repo `klai-widget/` als fork van `github.com/FlowiseAI/FlowiseChatEmbed` (MIT).
- SolidJS + Vite build pipeline (meegebracht vanuit FlowiseChatEmbed).
- Gepinned op een specifieke FlowiseChatEmbed commit om upstream breaking changes te voorkomen.
- Streaming via `@microsoft/fetch-event-source` (reeds aanwezig in fork).
- De widget slaat het session token op in memory (niet in localStorage) — verdwijnt bij page reload, widget haalt automatisch nieuw token op.
- Bundle output: single ESM/IIFE JS bestand, geen externe runtime dependencies in de browser.
- CI/CD upload naar CDN met versioned filenames `klai-chat@{version}.js` naast mutable `klai-chat.js` pointer.
- Bundle size budget: < 200 kB gzipped (hard limit, CI-check).

Embed snippet formaat:

```html
<script
  src="https://cdn.getklai.com/widget/klai-chat.js"
  data-widget-id="wgt_abc123"
  data-title="Klai Kennisbank"
  data-welcome="Hoe kan ik je helpen?"
></script>
```

### REQ-4: Admin portal integratie-type en widget configuratie [MODIFY]

**Event-Driven**: WHEN een admin in `/admin/integrations/new` een nieuwe integratie aanmaakt THEN shall het portal als eerste stap een type-keuze tonen: **API** (geeft een `pk_live_...` Bearer token voor server-to-server gebruik) of **Widget** (geeft een `wgt_...` embed snippet voor browser-gebruik). Het type kan na aanmaken niet meer gewijzigd worden.

**State-Driven**: WHILE `integration_type = 'api'`, THE SYSTEM shall de bestaande detail-view tonen met de `pk_live_...` API key, revoke-knop en usage metrics — geen widget-tab.

**State-Driven**: WHILE `integration_type = 'widget'`, THE SYSTEM shall in de detail-view van de integratie de configuratie-velden tonen (`allowed_origins`, `title`, `welcome_message`, `css_variables`), de `wgt_...` ID zichtbaar maken (read-only, kopieerbaar), en de embed snippet tonen. De `pk_live_...` key wordt nooit getoond na aanmaken.

**Event-Driven**: WHEN een admin de widget-configuratie invult en opslaat THEN shall het portal een PATCH request sturen naar het bestaande integratie-update endpoint met `widget_config = { allowed_origins: [...], title: "...", welcome_message: "...", css_variables: {} }`.

**Event-Driven**: WHEN een admin op "Kopieer embed snippet" klikt THEN shall het portal een `<script>` tag met `src`, `data-widget-id`, `data-title` en `data-welcome` attributen op het klembord zetten EN shall een bevestigingsbericht tonen "Gekopieerd naar klembord".

**State-Driven**: WHILE een `widget`-type integratie bestaat, THE SYSTEM shall in de `/admin/integrations` lijst-view een "Widget" badge tonen naast de integratie-naam.

**Optional**: Where een admin de `allowed_origins` leeg laat bij opslaan, THE SYSTEM shall een waarschuwing tonen: "Zonder toegestane domeinen werkt de widget nergens."

Implementatie-eisen:

- [MODIFY] `/admin/integrations/new` flow uitgebreid met type-selectie stap als eerste.
- [MODIFY] `klai-portal/frontend/app/admin/integrations/[id]/` detail page conditioneel: API-type toont bestaande view; widget-type toont `<WidgetTab>` component met embed snippet en `<EmbedSnippet>` copy-component.
- [MODIFY] `klai-portal/frontend/app/admin/integrations/page.tsx` lijst-view met type-badge rendering ("API" of "Widget").
- i18n: alle widget-configuratie labels vertaald in NL en EN via Paraglide (messages toegevoegd aan `messages/nl.json` en `messages/en.json`).
- `css_variables` editor is een simpele key-value tabel met een dropdown van de 4 ondersteunde keys (`--klai-primary-color`, `--klai-text-color`, `--klai-background-color`, `--klai-border-radius`) en een vrij tekstveld voor de waarde.
- `allowed_origins` editor is een multiline textarea met één origin per regel; client-side validatie op valide URL-format (scheme + host) voor opslaan.
- Embed snippet is auto-gegenereerd op basis van `wgt_id` en `widget_config` — admin kan de inhoud niet bewerken.

### REQ-5: Security, session tokens en rate limiting [MODIFY]

**Ubiquitous**: Het systeem **shall** alle bestaande Partner API security-mechanismes toepassen op widget-requests, inclusief rate limiting en logging.

**Ubiquitous**: Het systeem **shall** garanderen dat de `pk_live_...` API key van een `widget`-type integratie nooit in de browser verschijnt — niet in de embed snippet, niet in de widget-config response, niet in network requests. Enige publieke identifier is de `wgt_...` ID.

**State-Driven**: WHILE een widget actief is, THE SYSTEM shall de rate limit van de onderliggende integratie-entiteit hanteren — widget-requests (via session token) tellen mee in dezelfde per-integratie rate limit als directe API-requests.

**Ubiquitous**: Het session token (JWT) **shall** de volgende security-eisen hebben:

- TTL: 1 uur (`exp` claim in JWT)
- Scope: gebonden aan `wgt_id`, `org_id` en de `kb_ids` van de integratie-entiteit
- Signing: HS256 of RS256 met een projectintern secret (SOPS-managed)
- Geen refresh endpoint in v1 — widget haalt automatisch een nieuw token op via `widget-config` als het huidige verlopen is

**Unwanted**: IF een Origin header ontbreekt op een widget-endpoint request THEN shall het systeem HTTP 403 returnen (fail-closed, geen bypass).

**Unwanted**: IF een `pk_live_...` key of JWT-secret in een logbericht voorkomt THEN shall het systeem die waarde masken voor hij in VictoriaLogs terechtkomt. API keys: `pk_live_...{last4}`. JWTs: `[JWT redacted]`.

**Ubiquitous**: Het systeem **shall** CORS-headers correct zetten op `/partner/v1/widget-config` en `/partner/v1/chat/completions` endpoints zodat de browser cross-origin requests toestaat voor de geconfigureerde origins.

Implementatie-eisen:

- Reuse bestaande rate-limiter dependency uit Partner API; rate limit lookup via de integratie-entiteit achter het session token.
- CORS middleware-configuratie dynamisch per request: `Access-Control-Allow-Origin` header reflecteert de matchende origin uit `allowed_origins`, niet `*`. Preflight (OPTIONS) requests worden correct afgehandeld voor streaming endpoints.
- API key masking in structlog processor (identiek aan bestaande patterns). JWT masking als aanvulling.
- Origin validatie is server-side authoritative — de browser's CORS check is een extra beveiligingslaag, niet de enige.

### REQ-6: Escalatie ontwerp — FUTURE, NIET BOUWEN IN V1

> **Status**: Deze requirement is **gedocumenteerd voor toekomstig gebruik** en maakt GEEN deel uit van de v1 implementatie-scope. Zie ook "Toekomstige uitbreidingen" sectie.

**Ubiquitous (future)**: Het systeem **shall** in een toekomstige versie escalatie naar een menselijke live-chat ondersteunen wanneer de AI geen voldoende antwoord kan geven.

Beoogd gedrag in v2+ (niet implementeren in v1):

- Widget toont "Escaleer naar medewerker" knop onder het chat-venster.
- On click: backend genereert een conversatie-samenvatting via LLM (nieuw endpoint `GET /partner/v1/conversations/{id}/summary`).
- Widget redirect naar customer's live-chat platform met de samenvatting pre-loaded.
- Ondersteunde platforms en integratiepatronen zijn gedocumenteerd in "Toekomstige uitbreidingen".

Reden voor uitstel: escalatie vereist een per-platform integratie-matrix die zinvol pas uitgewerkt wordt nadat v1 daadwerkelijke gebruikersdata oplevert over WANNEER escalatie nodig is.

## Non-Functional Requirements

### Performance

- **p95 TTFT < 1500 ms**: gelijk aan Partner API SLA (SPEC-API-001). Widget voegt geen extra latency toe — streaming-pipeline is identiek aan direct API-gebruik.
- **Widget bundle < 200 kB gzipped**: gemeten op CI, build faalt bij overschrijding.
- **First render < 500 ms** na script-load op een gemiddelde host-pagina (gemeten via Lighthouse op referentie-pagina).
- **Geen Main Thread blocking > 50 ms** tijdens initialisatie (verifieerbaar in Chrome DevTools Performance tab).

### Security

- Origin validatie is server-side authoritative (REQ-5).
- `pk_live_...` API key verschijnt nooit in de browser — enkel `wgt_...` ID en short-lived session token (REQ-5).
- API key masking en JWT masking in alle logs (REQ-5).
- Widget bundle bevat géén hardcoded credentials of secrets.
- CORS fail-closed: lege `allowed_origins` = geen origin werkt.
- Rate limiting gedeeld met de integratie-entiteit — geen escape hatch via widget-path.

### Observability

- Widget-requests zijn in VictoriaLogs traceerbaar via `request_id` (Caddy genereert, portal-api propageert — zie `.claude/rules/klai/infra/observability.md`).
- Widget-gerelateerde product_events worden geëmit onder event types zoals `widget.chat.started`, `widget.chat.completed` op bestaande `product_events` tabel.
- Het `service:partner-api` filter in VictoriaLogs toont widget-traffic automatisch (geen nieuwe service nodig).

### Browser compatibility

- Evergreen browsers: Chrome, Firefox, Safari, Edge — laatste 2 major versies.
- Geen IE11 support.
- Geen polyfills in de bundle — moderne ES2020+ syntax.

### Internationalisatie

- Admin portal widget-configuratie UI: NL + EN (Paraglide).
- Widget eindgebruiker-UI taal: volgt de `widget_config.welcome_message` taal — admin is verantwoordelijk voor juiste taalkeuze. Geen runtime-taalwissel in v1.

## Toekomstige uitbreidingen

### Escalatie naar menselijke live-chat (v2+)

Wanneer de AI een vraag niet adequaat kan beantwoorden, wil de klant de gebruiker kunnen doorverwijzen naar een menselijke medewerker via het bestaande live-chat platform van de klant.

**Beoogde flow**:

1. Widget toont "Escaleer naar medewerker" knop (configureerbaar label).
2. On click: widget roept nieuw endpoint `GET /partner/v1/conversations/{conversation_id}/summary` aan.
3. Partner API genereert via LLM een beknopte samenvatting (key points, resterende vraag, bronnen geraadpleegd).
4. Widget redirect of opent embedded het live-chat platform van de klant met de samenvatting als eerste bericht.
5. Link naar volledig gespreklog wordt meegestuurd als attribuut of in de message body.

**Configuratie-uitbreiding** op `widget_config` (future):

- `escalation_enabled: bool`
- `escalation_platform: enum` (livechat, intercom, zendesk, crisp, hubspot, tawk, freshchat, tidio)
- `escalation_url: string` (platform-specifieke endpoint of widget-ID)
- `escalation_button_label: string` (i18n)

**Platform-integratiematrix** (research bevindingen):

| Platform     | Pre-fill methode                                                       | Geschiktheid       |
| ------------ | ---------------------------------------------------------------------- | ------------------ |
| LiveChat     | `LiveChatWidget.call('maximize', { messageDraft: summary })` + session variables | Beste native pre-fill support |
| Intercom     | `window.Intercom('showNewMessage', summary)` + attributes              | Zeer geschikt      |
| Zendesk      | `zE('messenger:ui', 'newConversation', { message: { content: { text: summary } } })` | Geschikt         |
| Crisp        | `$crisp.push(['set', 'message:text', [summary]])` + `session:data`     | Geschikt           |
| HubSpot      | Geen native pre-fill; server-side Conversations API vereist voor notes | Minst geschikt     |
| Tawk.to      | Geen pre-fill, alleen attributes en events                             | Beperkt            |
| Freshchat    | `fcWidget.open({ replyText: summary })`                                | Goed geschikt      |
| Tidio        | `tidioChatApi.messageFromVisitor(summary)`                             | Beperkt            |

**Escalatie-flow concreet**:

- Widget redirect naar customer's platform URL (configurabel).
- Conversatie-samenvatting wordt meegegeven als eerste bericht via platform-specifieke API.
- Link naar Klai's volledige gesprekslog wordt meegestuurd in platform's metadata-veld.
- Tracking via nieuw product_event `widget.escalation.triggered` voor analytics.

**Waarom uitgesteld naar v2**: de platform-matrix is groot en elk platform heeft eigen nuances. Zonder v1-data over WANNEER gebruikers escalatie nodig hebben (hoe vaak, bij welke onderwerpen), is het risico op over-engineering te groot. V1 levert de basis-widget; v2 voegt escalatie toe gebaseerd op echte usage-patterns.

### Widget analytics dashboard (v2+)

Aparte UI in admin portal met widget-specifieke metrics: conversaties per dag, gemiddelde lengte, bounce rate, top-vragen, escalatie-ratio. Gebouwd op bestaande product_events.

### Meerdere widgets per integratie (v2+)

Bijv. één widget voor support, één voor sales, beide op dezelfde KB — met verschillende styling/messaging. Vereist refactor van data model naar `widgets` tabel met FK naar `partner_api_keys`.

### Visuele CSS editor (v2+)

Color picker + live preview pane in admin portal. V1 accepteert de bare-bones key-value approach omdat de doelgroep (developer/integrator) hier prima mee overweg kan.
