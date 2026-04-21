# SPEC-WIDGET-001 — Compact (v0.2.0)

## Requirements

### REQ-1: Data model — integratie-type en widget-identiteit [MODIFY]

- Het systeem **shall** `partner_api_keys` tabel uitbreiden met `integration_type VARCHAR(10) DEFAULT 'api'`, `widget_id VARCHAR(64) UNIQUE NULL`, en `widget_config JSONB NULL`.
- WHEN admin nieuwe integratie aanmaakt THEN shall als eerste stap gekozen worden tussen type `api` (geeft `pk_live_...` Bearer token) of `widget` (geeft `wgt_...` publiek ID + embed snippet). Type is na aanmaken onveranderlijk.
- WHILE `integration_type = 'api'`, THE SYSTEM shall bestaande Partner API flow hanteren (geen widget-endpoints).
- WHILE `integration_type = 'widget'`, THE SYSTEM shall `pk_live_...` key uitsluitend intern gebruiken — nooit tonen in UI na aanmaken.
- IF request op widget-endpoint binnenkomt voor `integration_type = 'api'` integratie THEN shall HTTP 403 returnen.
- `widget_id` wordt gegenereerd als `wgt_` + 40 hex chars (UNIQUE).
- Default `widget_config.allowed_origins = []` betekent géén origins toegestaan (fail-closed).

### REQ-2: Widget bootstrap endpoint [NEW]

- Het systeem **shall** publiek endpoint `GET /partner/v1/widget-config?id=<wgt_id>` aanbieden (geen Auth header vereist).
- WHEN widget JS laadt THEN shall GET naar `/widget-config?id=<wgt_id>` + Origin header (browser automatisch).
- IF `wgt_id` geldig AND Origin exact matcht `allowed_origins` THEN shall HTTP 200 returnen met `{ title, welcome_message, css_variables, chat_endpoint, session_token, session_expires_at }`.
- Het session token is een JWT (HS256, TTL 1h) met claims: `wgt_id`, `org_id`, `kb_ids`, `exp`.
- IF Origin niet matcht THEN shall HTTP 403 + `{ "detail": "Origin not allowed" }` + géén info-lekkende details.
- IF `wgt_id` onbekend of `integration_type != 'widget'` THEN shall HTTP 404 returnen.
- IF Origin header ontbreekt THEN shall HTTP 403 returnen (fail-closed).
- Origin validatie: exact-match scheme + host + port.

### REQ-3: Klai Chat Widget JS build [NEW]

- Het systeem **shall** JS bundle leveren op `https://cdn.getklai.com/widget/klai-chat.js`.
- WHEN `<script>` tag laadt THEN shall widget `data-widget-id` uitlezen EN `/widget-config?id=<wgt_id>` request doen EN — bij succes — chat-bubble renderen.
- WHEN gebruiker bericht verstuurt THEN shall streaming POST naar `chat_endpoint` met `Authorization: Bearer <session_token>` EN tokens real-time renderen via SSE. De `pk_live_...` key verschijnt nooit in de browser.
- WHILE streaming actief, THE SYSTEM shall typing-indicator tonen EN input disable'n EN stop-knop tonen.
- WHERE `data-title` of `data-welcome` aanwezig op `<script>` tag THEN shall die waardes server-side defaults overriden.
- IF session token verlopen tijdens sessie (401 op chat endpoint) THEN shall widget automatisch nieuw token ophalen (max 1 keer), daarna foutmelding.
- IF `widget-config` request faalt (403/404/netwerkfout) THEN shall géén chat-bubble renderen EN `console.error` met foutcode (`KLAI_WIDGET_ORIGIN_NOT_ALLOWED`, `KLAI_WIDGET_NOT_FOUND`) — géén silent failure, géén infinite spinner.
- Session token opgeslagen in memory (niet localStorage) — nieuw token bij page reload.
- Repo `klai-widget/` (fork FlowiseChatEmbed MIT, commit-pinned, SolidJS + Vite).
- Streaming via `@microsoft/fetch-event-source`.
- Bundle < 200 kB gzipped (CI-enforced).
- Versioned filenames `klai-chat@{version}.js`.

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

- WHEN admin `/admin/integrations/new` opent THEN shall type-selectie als eerste stap getoond worden (API of Widget).
- WHILE `integration_type = 'widget'`, THE SYSTEM shall detail-view conditioneel tonen: `wgt_...` ID (read-only), config-velden, embed snippet. `pk_live_...` key nooit zichtbaar.
- WHILE `integration_type = 'api'`, THE SYSTEM shall bestaande view tonen (geen widget-tab).
- WHEN admin widget-config opslaat THEN shall PATCH naar integrations endpoint met `widget_config = { allowed_origins: [...], title, welcome_message, css_variables }`.
- WHEN admin "Kopieer embed snippet" klikt THEN shall `<script>` tag met `data-widget-id` (niet `data-api-key`) op klembord + bevestigingsbericht.
- WHILE `integration_type = 'widget'`, THE SYSTEM shall "Widget" badge tonen in lijst-view.
- WHERE admin `allowed_origins` leeg laat bij opslaan, THE SYSTEM shall waarschuwing tonen.
- i18n: NL + EN via Paraglide.
- `css_variables` editor: dropdown met 4 keys (`--klai-primary-color`, `--klai-text-color`, `--klai-background-color`, `--klai-border-radius`) + vrij tekstveld voor waarde.
- `allowed_origins`: multiline textarea, één origin per regel, client-side URL-validatie.

### REQ-5: Security, session tokens en rate limiting [MODIFY]

- Het systeem **shall** garanderen dat `pk_live_...` API key nooit in browser verschijnt — embed snippet, widget-config response, network requests.
- WHILE widget actief, THE SYSTEM shall per-integratie rate limit hanteren via session token claims (gedeeld met non-widget traffic).
- Session token (JWT): TTL 1h, HS256 signing, SOPS-managed secret, geen refresh endpoint in v1.
- IF Origin header ontbreekt THEN shall HTTP 403 returnen.
- IF `pk_live_...` in logbericht THEN shall masken naar `pk_live_...{last4}`. JWTs: `[JWT redacted]`.
- Het systeem **shall** CORS-headers correct zetten op `/widget-config` en `/chat/completions`: `Access-Control-Allow-Origin` reflecteert matchende origin (niet `*`). Preflight (OPTIONS) correct afgehandeld.
- Origin validatie is server-side authoritative.

### REQ-6: Escalatie ontwerp — FUTURE, NIET IN V1

- Gedocumenteerd in `spec.md` sectie "Toekomstige uitbreidingen".
- V1 implementeert GEEN escalatie UI, GEEN summary-endpoint, GEEN platform-integraties.

## Non-Functional Requirements

- p95 TTFT < 1500 ms (gelijk aan Partner API SLA).
- Widget bundle < 200 kB gzipped.
- First render < 500 ms na script-load.
- Main Thread blocking < 50 ms tijdens init.
- CORS fail-closed: lege `allowed_origins` = geen origin werkt.
- Browser support: evergreen (Chrome, Firefox, Safari, Edge — laatste 2 major versies).
- Observability: `request_id` traceerbaar in VictoriaLogs via `service:partner-api`.
- `pk_live_...` key verschijnt nergens in browser (Security invariant).

## Acceptance Criteria (samenvatting)

### Scenario 1: Widget laadt op externe website

- **Given** embed snippet met `data-widget-id="wgt_..."` op origin in `allowed_origins`.
- **When** eindgebruiker pagina laadt.
- **Then** chat-bubble binnen 500 ms + juiste title/welcome + géén JS errors.
- **And** precies 2 network requests: `klai-chat.js` + `/partner/v1/widget-config?id=wgt_...`.
- **And** `widget-config` response bevat `session_token`, géén `pk_live_...`.

### Scenario 2: Streaming chat response

- **When** gebruiker typt bericht + Enter.
- **Then** POST naar `/chat/completions` met `Bearer <session_token>` (nooit `pk_live_...`).
- **And** eerste token < 1500 ms (p95). Tokens real-time via SSE. Input disabled + stop-knop.

### Scenario 3: Admin maakt widget-integratie aan

- **When** admin type "Widget" selecteert + config invult + opslaat.
- **Then** PATCH met `widget_config` payload. `wgt_...` ID zichtbaar (read-only). `pk_live_...` nooit zichtbaar.
- **And** embed snippet met `data-widget-id`. "Widget" badge in lijst-view.

### Scenario 4: Request vanaf niet-toegestane origin

- **When** browser op ongeldige origin roept `/widget-config` aan.
- **Then** HTTP 403. Géén chat-bubble. `console.error` met `KLAI_WIDGET_ORIGIN_NOT_ALLOWED`.

### Scenario 5: Onbekende widget-ID

- **When** widget vraagt `/widget-config?id=wgt_unknownid999`.
- **Then** HTTP 404. Géén chat-bubble. `console.error` met `KLAI_WIDGET_NOT_FOUND`. Géén retry-loop.

### Scenario 6: Type-keuze onveranderlijk

- **Given** API-type integratie: géén widget-tab, `pk_live_...` zichtbaar.
- **Given** Widget-type integratie: `wgt_...` zichtbaar (read-only), `pk_live_...` nooit zichtbaar. Type niet wijzigbaar.

### Scenario 7: CSS variabele past styling aan

- **When** admin `--klai-primary-color: #ff0000` toevoegt + opslaat.
- **Then** `css_variables` in DB + widget injecteert in Shadow DOM + rood accent + géén CSS-bleed.

### Scenario 8: Widget uitgeschakeld... (zie acceptance.md scenario 8)

- Zie `acceptance.md` voor volledige Given/When/Then.

### Edge cases (samenvatting)

1. Lege `allowed_origins` → HTTP 403 + admin-waarschuwing bij opslaan.
2. Origin met poort: `https://example.com:8080` matcht niet `https://example.com`.
3. Interne key-rotatie: `wgt_...` ID blijft stabiel, embed snippet ongewijzigd, nieuwe session tokens automatisch.
4. Rate limit: HTTP 429 → gebruikersvriendelijke melding in widget.
5. Host-pagina CSS conflict: Shadow DOM beschermt widget.
