# SPEC-WIDGET-001 — Acceptatiecriteria

Alle scenarios gebruiken Given/When/Then formaat. Elk scenario verwijst naar de bijbehorende requirement(s) uit `spec.md`.

## Scenario 1: Widget laadt op externe website

**Dekt**: REQ-3, REQ-2

**Given** een externe website heeft het volgende `<script>` tag in hun HTML:

```html
<script
  src="https://cdn.getklai.com/widget/klai-chat.js"
  data-widget-id="wgt_abc123def456"
  data-title="Klai Kennisbank"
  data-welcome="Hoe kan ik je helpen?"
></script>
```

**And** de integratie met `widget_id = 'wgt_abc123def456'` heeft `integration_type = 'widget'`.

**And** `widget_config.allowed_origins` bevat het domein van deze externe website.

**When** een eindgebruiker de website bezoekt en de pagina laadt.

**Then** verschijnt er binnen 500 ms na script-load een chat-bubble rechtsonder in de viewport.

**And** een klik op de bubble opent het chatvenster met de titel "Klai Kennisbank" en het welkomstbericht "Hoe kan ik je helpen?".

**And** er zijn géén JavaScript errors in de browser console.

**And** er worden precies twee network requests gedaan tijdens initialisatie: één GET voor `klai-chat.js` en één GET voor `/partner/v1/widget-config?id=wgt_abc123def456`.

**And** de `widget-config` response bevat een `session_token` veld — géén `pk_live_...` API key.

**And** de `pk_live_...` API key van deze integratie verschijnt nergens in de browser (niet in network tab, niet in JS heap).

## Scenario 2: Streaming chat response

**Dekt**: REQ-3

**Given** de widget is geopend en geïnitialiseerd (scenario 1 succesvol afgerond).

**When** de eindgebruiker typt "Wat is Klai?" in de input-box en drukt op Enter.

**Then** start onmiddellijk een POST request naar `https://api.getklai.com/partner/v1/chat/completions` met `Authorization: Bearer <session_token>` header (het session token uit de widget-config response — géén `pk_live_...` key).

**And** toont de widget binnen 1500 ms (p95) het eerste token van het antwoord in het chatvenster (streaming TTFT gelijk aan Partner API SLA).

**And** worden vervolg-tokens real-time gerenderd terwijl ze via SSE binnenkomen — geen wachten op volledige response.

**And** is tijdens het streamen de input-box gedisabled en is er een stop-knop zichtbaar.

**And** verschijnt na afloop van de stream een typing-indicator-verwijdering en wordt de input-box weer enabled.

## Scenario 3: Admin maakt widget-integratie aan en kopieert embed snippet

**Dekt**: REQ-1, REQ-4

**Given** een admin is ingelogd in het klai-portal en klikt op "Nieuwe integratie".

**When** de admin het type "Widget" selecteert (in plaats van "API").

**Then** genereert het systeem een `wgt_...` ID voor deze integratie en toont dit als read-only veld.

**And** is de `pk_live_...` API key niet zichtbaar in de UI (nooit getoond voor widget-type integraties).

**When** de admin `allowed_origins` invult met `https://customer-website.com`.

**And** `title = "Klai Kennisbank"` en `welcome_message = "Hoe kan ik je helpen?"` invult.

**And** klikt op "Opslaan".

**Then** stuurt het portal een PATCH request naar het integrations update endpoint met `integration_type = 'widget'` en `widget_config = { allowed_origins: ["https://customer-website.com"], title: "Klai Kennisbank", welcome_message: "Hoe kan ik je helpen?", css_variables: {} }`.

**And** verschijnt de embed snippet:

```html
<script
  src="https://cdn.getklai.com/widget/klai-chat.js"
  data-widget-id="wgt_<gegenereerde-id>"
  data-title="Klai Kennisbank"
  data-welcome="Hoe kan ik je helpen?"
></script>
```

**And** bij een klik op "Kopieer embed snippet" wordt exact deze HTML-inhoud op het klembord gezet.

**And** wordt er een bevestigingsbericht "Gekopieerd naar klembord" getoond.

**And** verschijnt in de lijst-view `/admin/integrations` een "Widget" badge naast deze integratie.

## Scenario 4: Request vanaf niet-toegestane origin

**Dekt**: REQ-2, REQ-5

**Given** de widget-integratie met `widget_id = 'wgt_abc123def456'` heeft `allowed_origins = ["https://customer-website.com"]`.

**When** een browser op `https://unauthorized-site.com` het widget-script laadt en probeert `GET /partner/v1/widget-config?id=wgt_abc123def456` aan te roepen met Origin header `https://unauthorized-site.com`.

**Then** returnt het systeem HTTP 403 met response body `{ "detail": "Origin not allowed" }`.

**And** bevat de response géén `Access-Control-Allow-Origin` header voor `unauthorized-site.com`.

**And** logt het systeem de geblokkeerde request in VictoriaLogs zonder gevoelige details die een aanvaller zouden informeren.

**And** rendert de widget géén chat-bubble op `unauthorized-site.com`.

**And** verschijnt er één `console.error` met foutcode `KLAI_WIDGET_ORIGIN_NOT_ALLOWED`.

## Scenario 5: Onbekende widget-ID op externe website

**Dekt**: REQ-3, REQ-5

**Given** een externe website heeft een embed snippet met `data-widget-id="wgt_unknownid999"` op een correct geconfigureerde origin.

**And** de widget-ID `wgt_unknownid999` bestaat niet (of de integratie heeft `integration_type = 'api'`).

**When** de browser laadt de pagina en het widget-script vraagt `GET /partner/v1/widget-config?id=wgt_unknownid999` op.

**Then** returnt het systeem HTTP 404 met body `{ "detail": "Widget not found" }`.

**And** rendert de widget géén chat-bubble.

**And** draait er géén infinite loading spinner — de widget faalt stil in de UI maar logt duidelijk in de console.

**And** verschijnt er precies één `console.error` met foutcode `KLAI_WIDGET_NOT_FOUND`.

**And** worden er géén verdere retry-requests naar het endpoint gedaan (geen retry-loop).

## Scenario 6: Type-keuze bij aanmaken is onveranderlijk

**Dekt**: REQ-1, REQ-4

**Given** een admin maakt een nieuwe integratie aan van type "API".

**Then** is in de detail-view de `pk_live_...` API key zichtbaar en is er géén widget embed snippet.

**And** is er géén optie om het type achteraf te wijzigen naar "Widget" — de integratie blijft van type `api`.

**Given** een admin maakt een nieuwe integratie aan van type "Widget".

**Then** is de `pk_live_...` API key niet zichtbaar in de UI.

**And** is de `wgt_...` ID zichtbaar als read-only kopieerbaar veld.

**And** is er géén optie om het type achteraf te wijzigen naar "API".

**And** verschijnt in de lijstpagina `/admin/integrations` een "Widget" type-badge naast de integratie-naam.

## Scenario 7: CSS variabele past widget styling aan

**Dekt**: REQ-1, REQ-3, REQ-4

**Given** een admin heeft een widget-type integratie in `/admin/integrations/abc-123`.

**When** de admin in de "CSS variabelen" tabel de key `--klai-primary-color` toevoegt met waarde `#ff0000`.

**And** klikt op "Opslaan".

**Then** wordt `widget_config.css_variables = { "--klai-primary-color": "#ff0000" }` opgeslagen in de database.

**When** vervolgens een eindgebruiker op een externe website met deze integratie's widget de pagina laadt.

**Then** returnt `GET /partner/v1/widget-config` in de response body de `css_variables` waarde.

**And** injecteert de widget JS `--klai-primary-color: #ff0000` als CSS custom property in zijn Shadow DOM root.

**And** rendert de chat-bubble (of het verzend-knopje, afhankelijk van waar de primary color gebruikt wordt) in rood in plaats van de default Klai-kleur.

**And** beïnvloedt deze CSS-variabele géén elementen buiten de widget (Shadow DOM isolatie).

## Scenario 7: Widget uitgeschakeld maar configuratie bewaard

**Dekt**: REQ-1, REQ-4

**Given** een integratie heeft `widget_enabled = true` en een volledig ingevuld `widget_config`.

**When** de admin zet de toggle "Widget inschakelen" op uit en klikt Opslaan.

**Then** wordt `widget_enabled = false` opgeslagen.

**And** blijft `widget_config` ongewijzigd in de database bewaard (niet overschreven naar null).

**And** returnt `GET /partner/v1/widget-config` nu HTTP 403 met body `{ "detail": "Widget not enabled for this integration" }` voor valide API key + valide origin combinaties.

**And** verdwijnt de "Widget actief" badge uit de lijst-view.

**When** de admin de toggle later opnieuw aanzet en Opslaan klikt.

**Then** worden de eerder opgeslagen waardes (allowed_origins, title, welcome_message, css_variables) automatisch pre-filled in de velden — de admin hoeft ze niet opnieuw in te voeren.

## Edge cases

### Edge case 1: Lege `allowed_origins` lijst

**Given** admin slaat widget op met `widget_enabled = true` en `allowed_origins = []` (lege lijst).

**When** een request komt binnen op `/widget-config` met een willekeurige valide Origin.

**Then** returnt het systeem HTTP 403 (fail-closed: lege lijst = geen origin toegestaan).

**And** toont het admin portal bij het opslaan een waarschuwing: "Zonder toegestane domeinen werkt de widget nergens."

### Edge case 2: Origin match met poort

**Given** `allowed_origins = ["https://example.com"]` (zonder poort).

**When** request komt van `https://example.com:8080` (met poort).

**Then** returnt het systeem HTTP 403 — exact-match inclusief poort.

**And** idem voor scheme-mismatch: `http://example.com` matcht niet `https://example.com`.

### Edge case 3: Widget-ID blijft stabiel — onafhankelijk van interne rotaties

**Given** de integratie heeft `widget_id = 'wgt_abc123def456'` en een interne `pk_live_...` API key.

**When** de admin de interne `pk_live_...` key roteert via de bestaande rotate-flow.

**Then** blijft de `wgt_...` ID ongewijzigd — de embed snippet op externe websites hoeft niet bijgewerkt te worden.

**And** genereert het widget-config endpoint automatisch nieuwe session tokens op basis van de nieuwe interne key — transparant voor externe websites.

**And** werken bestaande actieve sessies van eindgebruikers door totdat hun session token verloopt (max 1 uur).

### Edge case 4: Rate limit bereikt door widget-verkeer

**Given** de Partner API key heeft een rate limit van 100 requests/minuut en deze is bereikt door widget-traffic.

**When** de widget een nieuwe chat-request stuurt.

**Then** returnt het Partner API `/chat/completions` endpoint HTTP 429 (Too Many Requests), identiek aan niet-widget requests.

**And** toont de widget een gebruikersvriendelijke foutmelding ("Even wachten a.u.b., te veel verzoeken") in plaats van een rauwe 429.

**And** verschijnt deze rate-limit-hit in VictoriaLogs onder het bestaande Partner API logging-patroon.

### Edge case 5: Widget op host-pagina met conflicterende CSS

**Given** de host-pagina gebruikt `!important` CSS-regels die globally `*` selectors overriden.

**When** de widget laadt.

**Then** blijft de widget visueel intact — Shadow DOM isolatie beschermt tegen CSS-bleeding vanuit de host-pagina.

**And** klopt het omgekeerde ook: widget-CSS bleedt niet naar buiten het shadow DOM.

## Definition of Done

Een SPEC-WIDGET-001 feature is compleet wanneer:

- [ ] Alle 8 kern-scenarios hierboven slagen in een Playwright E2E test.
- [ ] Alle 5 edge cases zijn gedekt met integration tests of handmatige verificatie.
- [ ] Bundle-size check in CI: `klai-chat.js` gzipped < 200 kB.
- [ ] p95 TTFT gemeten en < 1500 ms bij een gemiddelde chat-query.
- [ ] Admin portal widget-configuratie is beschikbaar in NL én EN (Paraglide).
- [ ] Alembic migratie (`add_integration_type_and_widget_id`) draait schoon op dev, staging én productie zonder data-loss.
- [ ] Partner API endpoint `GET /widget-config` is gedocumenteerd in de bestaande API docs.
- [ ] README in `klai-widget` repo beschrijft: install, `data-widget-id` attribuut, voorbeeld-snippet, troubleshooting (corporate firewalls, CSP, session token expiry).
- [ ] Security review: `pk_live_...` API key verschijnt nergens in de browser, session token correct gesigned en TTL gerespecteerd, Origin validatie server-side authoritative, CORS headers correct.
- [ ] Observability: widget-requests traceerbaar via `request_id:<uuid>` in VictoriaLogs.
- [ ] Nieuwe `product_events` (minimaal `widget.chat.started`, `widget.chat.completed`) geëmit en zichtbaar in Grafana.
- [ ] Type-selectie bij aanmaken werkt correct en type is onveranderlijk na aanmaken (scenario 6).
- [ ] Handmatige cross-browser test: Chrome, Firefox, Safari — widget werkt en streaming is stabiel.

## Niet-dekking

Expliciet NIET in scope van deze acceptance (zie `spec.md` Out of Scope):

- Escalatie naar live-chat platforms.
- Visuele CSS editor met color picker.
- Widget analytics dashboard.
- Meerdere widgets per integratie.
- localStorage persistentie tussen page loads.
