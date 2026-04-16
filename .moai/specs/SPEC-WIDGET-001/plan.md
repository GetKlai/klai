# SPEC-WIDGET-001 — Implementatieplan

## Architectuur

De widget is een 3-laags systeem dat gebruik maakt van bestaande Partner API infrastructuur:

```
┌──────────────────────────────────────────────────────────────┐
│ Laag 1: Widget JS (browser, externe website)                │
│   - FlowiseChatEmbed fork (SolidJS + Vite)                  │
│   - Gehost op cdn.getklai.com/widget/klai-chat.js           │
│   - Leest data-widget-id="wgt_..." van <script> tag         │
│   - Streaming via @microsoft/fetch-event-source             │
└──────────────────┬───────────────────────────────────────────┘
                   │ Stap 1: GET /widget-config?id=wgt_...
                   │   + Origin header (browser automatisch)
                   │   → response: config + session_token (JWT)
                   │
                   │ Stap 2: POST /chat/completions
                   │   Authorization: Bearer <session_token>
                   │   + Origin header gevalideerd
                   ▼
┌──────────────────────────────────────────────────────────────┐
│ Laag 2: Partner API (api.getklai.com/partner/v1/)           │
│   - NIEUW GET /widget-config (publiek, Origin-validated)    │
│     → geeft session token (JWT, TTL 1h) terug               │
│   - Bestaand POST /chat/completions (SPEC-API-001)          │
│     → accepteert session token als Bearer                   │
│   - partner_api_keys tabel: integration_type, widget_id,   │
│     widget_config kolommen (nieuw)                          │
│   - Origin validatie, CORS, rate limiting                   │
└──────────────────────────┬──────────────────────────────────┘
                           │
                           ▼
┌──────────────────────────────────────────────────────────────┐
│ Laag 3: Knowledge Base + LLM (ongewijzigd)                  │
│   - Bestaand retrieval-api + LLM pipeline                   │
│   - KB-scoping via session token claims (geen wijziging)    │
└──────────────────────────────────────────────────────────────┘

Admin configuratie (separate flow):
┌──────────────────────────────────────────────────────────────┐
│ klai-portal admin UI                                         │
│   - /admin/integrations/new: type-keuze (API of Widget)     │
│   - /admin/integrations/{id}: conditionele detail-view      │
│     • API-type: bestaande view (pk_live_... key)            │
│     • Widget-type: WidgetTab (wgt_... ID, config, snippet)  │
│   - PATCH request naar bestaand integrations endpoint       │
│   - i18n: NL + EN (Paraglide)                               │
└──────────────────────────────────────────────────────────────┘
```

### Componenten per laag

**Laag 1 — Widget JS (nieuwe repo)**:

- Repo: `klai-widget/` (separaat van klai-portal)
- Basis: fork van FlowiseChatEmbed op specifieke commit
- Build: Vite → single-file IIFE bundle
- Deployment: CI upload naar Hetzner Object Storage → `https://cdn.getklai.com/widget/klai-chat@{version}.js`

**Laag 2 — Partner API wijzigingen (klai-portal)**:

- `klai-portal/backend/app/models/partner_api_key.py` — twee nieuwe velden
- `klai-portal/backend/app/routes/partner_api.py` — nieuw `/widget-config` endpoint
- `klai-portal/backend/app/schemas/partner_api.py` — Pydantic schema uitbreiding
- Nieuwe Alembic migratie

**Admin UI wijzigingen (klai-portal)**:

- `klai-portal/frontend/app/admin/integrations/[id]/` — widget tab component
- `klai-portal/frontend/app/admin/integrations/page.tsx` — badge-rendering in lijst
- `klai-portal/frontend/messages/nl.json` en `en.json` — Paraglide messages

## Bestanden te wijzigen

### Nieuwe bestanden [NEW]

| Pad | Doel |
|-----|------|
| `klai-widget/` (nieuwe repo) | FlowiseChatEmbed fork |
| `klai-widget/src/klai-config.ts` | Klai-specifieke config-loading vanuit `/widget-config` endpoint |
| `klai-widget/vite.config.ts` | Build config met bundle-size budget |
| `klai-widget/.github/workflows/release.yml` | CI/CD naar CDN |
| `klai-portal/backend/alembic/versions/{timestamp}_add_widget_fields_to_partner_api_keys.py` | Database migratie |
| `klai-portal/frontend/app/admin/integrations/[id]/_components/WidgetTab.tsx` | Widget configuratie-tab UI |
| `klai-portal/frontend/app/admin/integrations/[id]/_components/EmbedSnippet.tsx` | Copyable snippet component |

### Gewijzigde bestanden [MODIFY]

| Pad | Wijziging |
|-----|-----------|
| `klai-portal/backend/app/models/partner_api_key.py` | `widget_enabled`, `widget_config` velden toegevoegd |
| `klai-portal/backend/app/schemas/partner_api.py` | Pydantic schemas uitgebreid |
| `klai-portal/backend/app/routes/partner_api.py` | Nieuw `/widget-config` endpoint + Origin validatie helper |
| `klai-portal/backend/app/middleware/cors.py` (of equivalent) | Dynamische CORS voor widget-endpoints |
| `klai-portal/frontend/app/admin/integrations/[id]/page.tsx` | Widget tab toegevoegd aan detail-view |
| `klai-portal/frontend/app/admin/integrations/page.tsx` | Badge in lijst-rendering |
| `klai-portal/frontend/messages/nl.json` | Widget i18n keys (NL) |
| `klai-portal/frontend/messages/en.json` | Widget i18n keys (EN) |

## Task decompositie

Volgorde is gebaseerd op afhankelijkheden, niet op tijd. Elke stap is afrondbaar en testbaar voordat de volgende begint.

### Prioriteit: Hoog (blocking path)

**Task 1 — Data model fundament** [MODIFY + NEW migratie]
- Alembic migratie `add_integration_type_and_widget_id` schrijven voor `integration_type`, `widget_id` en `widget_config`.
- SQLAlchemy model `PartnerAPIKey` uitbreiden met de drie nieuwe kolommen.
- Pydantic schemas `PartnerAPIKeyRead` / `PartnerAPIKeyCreate` / `PartnerAPIKeyUpdate` uitbreiden; type is immutable na aanmaken.
- `wgt_...` ID-generatie helper (wgt_ + 40 hex chars, UNIQUE constraint).
- Unit tests voor schema-validatie (o.a. `allowed_origins` format, `integration_type` enum, immutability van type).
- Blokkerend voor: Task 2, Task 4.

**Task 2 — Widget bootstrap endpoint** [NEW]
- `GET /partner/v1/widget-config?id=<wgt_id>` implementeren in `partner_api.py` (geen Auth-header vereist).
- Origin validatie helper schrijven (exact match op scheme+host+port).
- JWT session token generatie (HS256, TTL 1h, claims: wgt_id, org_id, kb_ids, exp).
- Dynamische CORS response configureren.
- `/partner/v1/chat/completions` uitbreiden om session token als geldige Bearer te accepteren naast `pk_live_...`.
- Integration tests: valide wgt_id + valide origin = 200 + session_token; valide wgt_id + ongeldige origin = 403; onbekende wgt_id = 404; api-type integratie als wgt_id = 403.
- Blokkerend voor: Task 3 (widget JS kan niet werken zonder endpoint).

**Task 3 — klai-widget repo opzetten** [NEW]
- FlowiseChatEmbed forken op specifieke commit, repo-rename, MIT license-attribution behouden.
- Flowise-specifieke endpoint-logic vervangen door Klai bootstrap: lees `data-widget-id`, fetch `GET /widget-config?id=<wgt_id>`, sla session token op in memory, gebruik returned `chat_endpoint`.
- Data attributen parseren: `data-widget-id` (vereist), `data-title`, `data-welcome` (optioneel, overriden server-side defaults).
- Chat calls: `Authorization: Bearer <session_token>` header; auto-refresh bij 401 (één keer, dan foutmelding).
- CSS variabelen injecteren uit `widget_config.css_variables` in shadow DOM.
- Error states: 403 origin / 404 onbekende widget-id / netwerkfout → console.error met foutcode + géén widget-render.
- Bundle-size CI check (< 200 kB gzipped).
- Blokkerend voor: Task 5, Task 6 (acceptatie-testen).

**Task 4 — Admin portal integratie-type + widget UI** [MODIFY]
- `/admin/integrations/new` flow: type-selectie stap als eerste (API of Widget).
- Bij Widget-type: `wgt_...` ID genereren en tonen als read-only veld.
- `WidgetTab.tsx` component: `allowed_origins` textarea, `title`, `welcome_message`, `css_variables` key-value tabel (4 ondersteunde CSS vars), waarschuwing bij lege `allowed_origins`.
- `EmbedSnippet.tsx` component: auto-gegenereerde script-tag met `data-widget-id` (niet `data-api-key`) en copy-button.
- Client-side validatie: `allowed_origins` regels parseren en valideren (URL-format per regel).
- Detail-view conditioneel op `integration_type`: API-type = bestaande view; widget-type = WidgetTab.
- Lijstpagina: type-badge ("API" of "Widget") naast integratie-naam.
- Paraglide messages toevoegen in NL + EN.
- Parallel met Task 3 ontwikkelbaar (geen directe afhankelijkheid).

### Prioriteit: Medium (follow-up)

**Task 5 — Admin portal lijst-view badge** [MODIFY]
- "Widget actief" badge rendering in `/admin/integrations` lijst.
- i18n-label voor badge toegevoegd.
- Afhankelijk van: Task 1 (data model).

**Task 6 — CDN deployment pipeline** [NEW]
- GitHub Actions workflow in klai-widget repo.
- Bouw → upload naar Hetzner Object Storage → cache-bust voor mutable `klai-chat.js`.
- Versioned filenames voor rollback.
- Smoke test: curl cdn URL, HTTP 200, Content-Type application/javascript, juiste cache headers.
- Afhankelijk van: Task 3.

**Task 7 — End-to-end acceptatie test** [NEW]
- Playwright test: embed snippet op testpagina → widget verschijnt → bericht versturen → streaming response → antwoord compleet.
- Origin-mismatch scenario: testpagina op niet-geautoriseerde origin → geen widget, console.error.
- Afhankelijk van: Task 2, Task 3, Task 6.

### Prioriteit: Laag (polish)

**Task 8 — Documentatie**
- README in klai-widget repo met integration guide voor klanten.
- Admin portal help-tekst bij widget-tab (tooltip naar documentatie).
- Update van Partner API docs met `/widget-config` endpoint.

## Dependencies

### Externe dependencies

- **FlowiseChatEmbed** (MIT, `github.com/FlowiseAI/FlowiseChatEmbed`): basis voor widget UI. Commit-gepinned in klai-widget repo.
- **@microsoft/fetch-event-source** (MIT): SSE streaming in browser. Reeds aanwezig in FlowiseChatEmbed dependency tree.
- **SolidJS** (MIT): framework van FlowiseChatEmbed.
- **Vite** (MIT): build tool van FlowiseChatEmbed.

### Interne dependencies

- **SPEC-API-001 (Partner API)**: moet stabiel en live zijn. Widget hangt aan bestaand `/chat/completions` endpoint.
- **Partner API key management**: bestaande UI/flow in `/admin/integrations` blijft onveranderd — widget is extensie.
- **Paraglide i18n**: bestaande infrastructuur voor NL/EN in klai-portal.
- **Hetzner Object Storage** (of equivalent CDN): infra-taak buiten scope. Moet beschikbaar zijn vóór Task 6 kan draaien.

### Observability dependencies

- **VictoriaLogs**: bestaand (zie `.claude/rules/klai/infra/observability.md`). Widget-requests erven `request_id` propagatie via Partner API.
- **product_events tabel**: bestaand. Nieuwe event types `widget.chat.*` hergebruiken bestaande SQL-schema.

## Risico's en mitigaties

### Risico 1: FlowiseChatEmbed upstream breaking changes

**Impact**: Medium. Fork raakt achter, security patches niet automatisch beschikbaar.

**Mitigatie**: Commit-pinnen bij fork. Upstream-sync beleid: quarterly review van upstream changelog, selectief cherry-picken van security fixes. Als fork te ver divergeert (> 80% lines changed), behandelen als volledig losse codebase.

### Risico 2: API key exposure in browser

**Impact**: Medium. Key is zichtbaar in HTML source, DevTools, request headers.

**Mitigatie**: Zie SPEC REQ-5 — key is géén secret, wel KB-scoped, rate-limited en Origin-gerestricteerd. Vergelijkbaar met Mapbox/Stripe publishable keys. Documentatie benadrukt dat `allowed_origins` correct ingesteld moet zijn.

### Risico 3: CORS edge cases

**Impact**: Medium. Preflight requests (OPTIONS) voor streaming endpoints kunnen subtiel falen.

**Mitigatie**: Integration tests voor preflight + actual request flow. Handmatige test van multiple browsers (Chrome, Firefox, Safari) tijdens Task 7.

### Risico 4: Bundle size overschrijding

**Impact**: Laag-medium. Als bundle > 200 kB, CI faalt.

**Mitigatie**: Vite tree-shaking + rollup-plugin-visualizer voor size-analyse. Budget-check in CI pipeline. Eventueel dynamic imports voor zelden-gebruikte features.

### Risico 5: Streaming stabiliteit over SSE

**Impact**: Medium. SSE kan instabiel zijn achter bepaalde proxies/firewalls bij klanten.

**Mitigatie**: Gebruik `@microsoft/fetch-event-source` (robuuster dan native EventSource — ondersteunt POST, custom headers, auto-reconnect). Documentatie vermeldt dat corporate firewalls die streaming blokkeren mogelijk issues geven.

### Risico 6: Widget UX breaking host-pagina styles

**Impact**: Laag. CSS-leakage tussen widget en host-pagina.

**Mitigatie**: Shadow DOM isolatie (standaard in FlowiseChatEmbed). CSS-variabelen enkel via `:host` selector, niet global.

## MX tag targets

Deze SPEC is grotendeels greenfield voor de widget-zijde (nieuwe repo) — weinig bestaande invariants om te beschermen. Wel aandacht voor de volgende extensiepunten in `klai-portal`:

- `partner_api.py` — bestaande Partner API router krijgt nieuwe route. **@MX:NOTE** bij de nieuwe `/widget-config` handler om de relatie met SPEC-WIDGET-001 expliciet te maken.
- `PartnerAPIKey` model — bestaand model krijgt nieuwe velden. Geen `@MX:WARN` nodig: fields zijn opt-in (default false / null), geen risico voor bestaande API-consumers. Wel **@MX:NOTE** op het model met link naar SPEC-WIDGET-001 voor context.
- Origin validatie helper — security-relevant. **@MX:ANCHOR** als de helper meerdere callers krijgt, met invariant "elke widget-endpoint MOET door deze helper gaan".

Geen kritieke MX-tagging vereist voor klai-widget repo zelf (greenfield code, geen legacy callers).

## Acceptatie-koppeling

Zie `acceptance.md` voor volledige Given/When/Then scenarios. Samenvatting:

- Embed snippet op test-website → widget rendert.
- User typt bericht → streaming response.
- Admin enable flow → embed snippet gegenereerd met juiste key.
- Origin mismatch → 403 + geen render.
- Invalid key → error state, géén infinite spinner.
- CSS variabele change → visuele update.
