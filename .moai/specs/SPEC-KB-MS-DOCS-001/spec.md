---
id: SPEC-KB-MS-DOCS-001
version: "1.1.0"
status: draft
created: 2026-04-23
updated: 2026-04-23
author: Mark Vletter
priority: high
supersedes_partial: SPEC-KB-025 (MS/SharePoint-half; Google Drive-half is geland)
---

## HISTORY

| Date | Version | Change |
|------|---------|--------|
| 2026-04-23 | 1.0.0 | Initial draft — Microsoft SharePoint / OneDrive connector (`ms_docs`) als OAuth-backed adapter. Spiegel van `GoogleDriveAdapter` op Microsoft Graph. `ms_docs` staat al in `ConnectorType` Literal + CHECK-constraint + `SENSITIVE_FIELDS` — alleen adapter + OAuth provider-registratie + frontend-wizard ontbreken. |
| 2026-04-23 | 1.1.0 | Open vragen beantwoord na owner-review. (1) App-registratie in klai-eigen M365 tenant (zelfde structuur als social login IDP-registratie). (2) Multi-tenant app — bevestigd. (3) **Één `ms_docs` connector-type met UI-label "Office 365"** — geen split van SharePoint/OneDrive als losse tiles; config-veld `site_url` onderscheidt per-site sync van "mijn OneDrive". (4) Refresh-token rotation support is onderdeel van deze SPEC (R2.2 + PortalClient uitbreiding). (5) **`site_url` tekstveld + optionele `drive_id`, geen picker in v1** — conform SPEC-KB-025 design. D2 toegevoegd: erkenning dat SPEC-KB-025 partial superseded wordt. |

---

# SPEC-KB-MS-DOCS-001: Microsoft SharePoint / OneDrive connector

## Summary

Implementatie van de al-in-de-Literal-staande `ms_docs` connector-type als OAuth-backed adapter in `klai-connector`, met bijbehorende OAuth provider-registratie in portal-api en UI-support in portal-frontend. De adapter is een één-op-één spiegel van `GoogleDriveAdapter` op Microsoft Graph v1.0: zelfde `OAuthAdapterBase`, zelfde `BaseAdapter` interface, zelfde delta-cursor patroon, zelfde credential-writeback flow. Alleen de provider-details (token endpoint, scopes, API shape) verschillen.

**Geen** adapter voor Outlook-mail, Teams-messages, of andere M365-bronnen — dit is uitsluitend document-ingest van OneDrive + SharePoint document libraries, analoog aan hoe `GoogleDriveAdapter` alleen Drive-files behandelt.

### Relatie tot SPEC-KB-025

Dit SPEC vervangt de MS/SharePoint-helft van `SPEC-KB-025` ("OAuth Connectors — Google Drive & SharePoint", april 2026). KB-025 is toen in twee fasen geïmplementeerd: de Google Drive-helft is geland (zie `OAuthAdapterBase` + `GoogleDriveAdapter`), de MS-helft niet. Design-keuzes uit KB-025 die in dit SPEC terugkeren: `site_url` als tekstveld, `delta_link` als cursor, `webUrl` als `source_url`, multi-tenant Azure AD app (`tenant_id=common`). KB-025 stelde `MSAL` + `unstructured-ingest[sharepoint]` voor als dependencies — beide expliciet **afgewezen** in dit SPEC (zie D2).

## Motivation

Drie concrete redenen:

1. **Klanten met M365-tenants** (verreweg de grootste groep Nederlandse SMB's) kunnen hun kennis vandaag niet syncen. Google Drive werkt, SharePoint niet. Dat is een harde GTM-blocker voor elke klant wiens primaire tool SharePoint of OneDrive is.
2. **Scaffolding is al klaar.** `ms_docs` staat in `ConnectorType` Literal, in de `ck_portal_connectors_type` CHECK constraint, in `SENSITIVE_FIELDS`, en in `CONTENT_TYPE_DEFAULTS`. De frontend heeft al een `ms_docs` type-entry met `available: false`. Het is af-bouwen, geen green-field.
3. **De abstracties zijn al gemaakt.** `OAuthAdapterBase` noemt letterlijk in zijn docstring "used by Google Drive (and, later, SharePoint)" — de refresh-token cache, expiry-skew, lock, en writeback-naar-portal zijn adapter-agnostisch.

De opportunity-kost om dit niet te doen: elke inbound-klant met een M365-tenant moet "eerst hun kennis naar Google Drive verhuizen" of afhaken.

## Scope

**In scope:**
- `MsDocsAdapter` in `klai-connector/app/adapters/ms_docs.py` als subclass van `OAuthAdapterBase` + `BaseAdapter`
- Microsoft Graph OAuth-provider in `klai-portal/backend/app/api/oauth.py` (`_SUPPORTED_PROVIDERS`, authorize + callback)
- Conditionele registratie in `klai-connector/app/main.py` bij ontbrekende credentials
- Portal-frontend wizard-step voor `ms_docs`: `available: true` + OAuth-connect button + optionele `site_url` / `drive_id` config
- Settings: `ms_docs_client_id` + `ms_docs_client_secret` + `ms_docs_tenant_id` (default `common` voor multi-tenant)
- Azure AD app-registratie als runbook, niet als code (`docs/runbooks/ms-docs-oauth.md`)
- Unit + integration tests ≥ 85% coverage met gemockte Graph responses (httpx.MockTransport)
- `sender_email` + `mentioned_emails[]` identifier-capture conform `SPEC-KB-CONNECTORS-001` R2.5

**Buiten scope (toekomstig werk):**
- **Outlook / Exchange Online mail** — past bij `SPEC-KB-EMAIL-001` pipeline
- **Teams messages** — communicatie-kanaal, niet knowledge-bron
- **Application permissions / app-only OAuth flow** — v1 gebruikt delegated (per-user OAuth)
- **User-facing connector-type split** (`ms_onedrive`, `ms_sharepoint` als losse entries) — wacht op `AdapterRegistry.register_alias` uit `SPEC-KB-CONNECTORS-001`
- **Real-time sync via Graph webhooks**
- **SharePoint Pages (aspx / modern pages)** — alleen DriveItems in v1
- **SharePoint list items als knowledge** — alleen files in document libraries
- **PDF-conversie van legacy Office-formaten** (.doc, .xls, .ppt, .rtf)

**Niet in scope (expliciet afgewezen):**
- **`msgraph-sdk`** of **`MSAL`** als dependencies (zie D2)
- **`unstructured-ingest[sharepoint]`** (SPEC-KB-025 voorstel, afgewezen in D2)

---

## Design Decisions

### D1: Mirror `GoogleDriveAdapter` exact — niet creatief zijn

De `MsDocsAdapter` volgt `GoogleDriveAdapter` methode-voor-methode:

| `GoogleDriveAdapter` | `MsDocsAdapter` | Graph endpoint |
|---|---|---|
| `_refresh_oauth_token` via `https://oauth2.googleapis.com/token` | idem via `https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token` | OAuth 2.0 refresh_token grant |
| `_list_files` → `files.list` | `_drain_delta` → `/drives/{id}/root/delta` | Drive listing |
| `_list_changes` → `changes.list?pageToken=...` | incremental via persisted `@odata.deltaLink` | Delta |
| `_fetch_start_page_token` (bootstrap) | `get_cursor_state` bootstrap via delta-call | Delta bootstrap |
| `_download_file` (`alt=media`) | `_graph_get_bytes` (`/drive/items/{id}/content`) | Binary download |
| `_export_file` (Google-native → DOCX/XLSX/PPTX) | **n.v.t.** — Office-files zijn al in Office format | — |
| `get_cursor_state` → `{"page_token": ...}` | `get_cursor_state` → `{"delta_link": ...}` | Persist exact deltaLink URL |

### D2: Direct `httpx` — geen `msgraph-sdk`, geen `MSAL`, geen `unstructured-ingest[sharepoint]`

Drie kandidaten voor provider-SDK, alle drie afgewezen:

| Aanpak | Voordeel | Waarom afgewezen |
|---|---|---|
| `msgraph-sdk` (Kiota-based, async-first, MS-aanbevolen) | Type-safe voor alle Graph-endpoints | Grote dep (100+ transitieve imports via Kiota runtime), andere test-ergonomie dan `GoogleDriveAdapter`, overkill voor 6 endpoints |
| `MSAL` (Microsoft Authentication Library) voor token acquisition + `httpx` voor Graph calls | MS-canonical auth library; sync, wrap via `asyncio.to_thread` | `oauth.py` doet Google-tokens al via directe `/oauth2/token` POST zonder SDK. MSAL voor MS maar niet voor Google is inconsistent. **SPEC-KB-025 stelde MSAL voor; we wijken bewust af.** |
| `unstructured-ingest[sharepoint]` voor discovery + download | Gratis lib voor 71+ connectors | Conflicts met onze ingest-pipeline (eigen parsing via knowledge-ingest). Non-additive om te introduceren. **SPEC-KB-025 stelde dit voor; we wijken bewust af.** |
| **Direct `httpx` calls** | 1:1 patroon met `GoogleDriveAdapter`, trivial te mocken met `httpx.MockTransport`, geen extra deps | Zelf paginatie + 429-retry schrijven — maar klein en voorspelbaar |

**Gekozen: direct `httpx`.** Pattern-consistentie met werkende Google Drive adapter weegt zwaarder dan SDK type-safety.

Noot: wijkt bewust af van `SPEC-KB-CONNECTORS-001` R2 ("officiële SDK's zijn verplicht"). Die regel geldt voor Airtable/Confluence/Google-split.

### D3: Delegated permissions, multi-tenant Azure AD app in klai-eigen M365 tenant

OAuth 2.0 authorization code flow met delegated scopes:
```
offline_access
User.Read
Files.Read.All
Sites.Read.All
```

**App-registratie eigenaar:** Klai-team M365 tenant, zelfde structuur als `ZITADEL_IDP_MICROSOFT_ID` (social login). Credentials SOPS-versleuteld.

**Multi-tenant:** `tenant_id = common` — elke M365-tenant van een klant kan connecten.

**Admin-consent:** `Files.Read.All` en `Sites.Read.All` vereisen tenant-wide admin-consent in de meeste klant-tenants. UX-risico: gewone medewerker kan niet zelf activeren zonder IT-admin van klant. Gedocumenteerd in wizard-copy en runbook.

**App-only flow buiten scope v1** — wordt v2 als delegated te hoge drempel blijkt.

### D4: Één connector-type "Office 365", filter via config

Eén `ms_docs` connector-type met UI-label **"Office 365"**. Geen split van SharePoint/OneDrive als losse tiles. Filtering via config:

| Config-veld | Betekenis | Graph-endpoint |
|---|---|---|
| (beide leeg) | "Mijn OneDrive" — persoonlijke OneDrive | `/me/drive/root/delta` |
| `site_url` (optional) | Specifieke SharePoint site | `/sites/{hostname}:/{path}:/drive/root/delta` |
| `drive_id` (optional) | Specifieke Drive-GUID | `/drives/{drive_id}/root/delta` |

Resolutie-volgorde: `drive_id` > `site_url` > `/me/drive`.

### D5: `site_url` tekstveld, geen site-picker in v1

De wizard vraagt om een `site_url` tekstveld (format `https://{tenant}.sharepoint.com/sites/{site}`) — geen picker.

**Waarom geen picker:** kip/ei probleem — picker vereist Graph-calls vóór de OAuth-flow rond is. Copy/paste van een URL uit de browser is triviaal.

**Server-side resolutie** van `site_url` naar Graph-site-object via `GET /sites/{hostname}:/{path}` → response.id, gecached per connector.

### D6: Delta-sync via `driveItem: delta` — één call, niet per-item

Graph's `/drives/{id}/root/delta` retourneert alle wijzigingen (add/update/delete) in één call, gepaginaliseerd via `@odata.nextLink`, afsluitend met `@odata.deltaLink` voor volgende run.

**Persistentie:** de exacte deltaLink URL opgeslagen als cursor — geen param-reconstructie.

**410 Gone handling:** bij expired deltaLink (>30d inactief) → cursor reset → full re-sync volgende run.

### D7: Office-binaries direct doorzetten, geen PDF-conversie in v1

`.docx`/`.xlsx`/`.pptx`/`.pdf` → `fetch_document` doet `GET /drive/items/{id}/content` → bytes. Geen conversie, geen `?format=pdf`, geen export-mime-mapping. Legacy formats (`.doc`, etc.) gewoon downloaden; conversie pas toevoegen als knowledge-ingest er in productie op faalt.

### D8: `source_url` is `webUrl` uit de DriveItem response

`DocumentRef.source_url = driveItem.webUrl` — klikbaar, user-visible, geauthoriseerd in klant-browser via hun M365-sessie.

### D9: Identifier capture — `sender_email` uit DriveItem's `createdBy`/`lastModifiedBy`

- `sender_email = driveItem.lastModifiedBy.user.email` (fallback `createdBy.user.email`)
- `mentioned_emails[] = {createdBy.email, lastModifiedBy.email}` gededupliceerd, lege strings eruit

### D10: `FRONTEND_URL` is het redirect-URI anchor

Azure AD app-registratie moet `https://my.getklai.com/api/oauth/ms_docs/callback` als toegestane redirect-URI hebben. Bij domain-wijziging moet Azure AD mee — staat in runbook.

---

## Requirements

### Module 1: Portal OAuth-provider registratie

**R1:** `klai-portal/backend/app/api/oauth.py` voegt `"ms_docs"` toe aan `_SUPPORTED_PROVIDERS`.

**R1.1:** `_provider_enabled("ms_docs")` retourneert `True` wanneer `settings.ms_docs_client_id` niet-leeg is.

**R1.2:** `GET /api/oauth/ms_docs/authorize` zet een signed state-cookie en retourneert een `authorize_url` naar `https://login.microsoftonline.com/{tenant}/oauth2/v2.0/authorize` met:
- `scope = "offline_access User.Read Files.Read.All Sites.Read.All"`
- `response_type = code`, `response_mode = query`, `prompt = consent`
- `redirect_uri = {FRONTEND_URL}/api/oauth/ms_docs/callback`

**R1.3:** `GET /api/oauth/ms_docs/callback` verifieert state, wisselt `code` in op `https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token`, encrypt tokens via `ConnectorCredentialStore`, redirect naar `/app/knowledge/{kb_slug}/connectors?oauth=connected`.

**R1.4:** Tokens worden nooit gelogd (alleen status codes + connector_id).

**R1.5:** `GET /api/oauth/providers` retourneert `ms_docs: {enabled, scopes}`.

### Module 2: klai-connector adapter implementatie

**R2:** `klai-connector/app/adapters/ms_docs.py` bevat een `MsDocsAdapter(OAuthAdapterBase, BaseAdapter)`.

**R2.1:** `_refresh_oauth_token` roept `login.microsoftonline.com/{tenant}/oauth2/v2.0/token` aan met `grant_type=refresh_token` en retourneert raw JSON.

**R2.2:** Wanneer de response een nieuwe `refresh_token` bevat, wordt die via `portal_client.update_credentials(refresh_token=...)` teruggeschreven. Vereist PortalClient uitbreiding (Module 9).

**R2.3:** `list_documents` delta-root-URL resolutie:
1. `config.drive_id` → `/drives/{drive_id}/root/delta`
2. `config.site_url` → eerst `GET /sites/{hostname}:/{path}` → `/sites/{site-id}/drive/root/delta`
3. Beide leeg → `/me/drive/root/delta`

**R2.4:** `list_documents` persisteert `@odata.deltaLink` in `_latest_delta_link[connector_id]`.

**R2.5:** Elke `DocumentRef` bevat:
- `source_ref = driveItem.id`
- `source_url = driveItem.webUrl`
- `last_edited = driveItem.lastModifiedDateTime`
- `content_type` gemapped via R2.6
- `size = driveItem.size`

**R2.6:** MIME → `content_type` mapping:
- `application/vnd.openxmlformats-officedocument.wordprocessingml.document` → `word_document`
- `application/vnd.openxmlformats-officedocument.spreadsheetml.sheet` → `excel_document`
- `application/vnd.openxmlformats-officedocument.presentationml.presentation` → `powerpoint_document`
- `application/pdf` → `pdf_document`
- overige → `kb_article`

**R2.7:** `fetch_document` roept `GET /drive/items/{ref.ref}/content` aan met Bearer-header, volgt 302-redirects naar preauthenticated URLs.

**R2.8:** `get_cursor_state` retourneert `{"delta_link": <url>}` uit `_latest_delta_link`, of bootstrap via enkele delta-call.

**R2.9:** `aclose()` no-op.

**R2.10:** `sender_email` en `mentioned_emails[]` gevuld conform D9.

**R2.11:** 429/503 response → één retry met `Retry-After` header (cap 30s); tweede failure propageert.

**R2.12:** Nooit access_token / refresh_token / Bearer headers loggen.

### Module 3: klai-connector registratie

**R3:** `main.py` registreert `MsDocsAdapter` in `AdapterRegistry` onder key `"ms_docs"`, conditioneel op `settings.ms_docs_client_id`.

**R3.1:** Bij ontbrekende `ms_docs_client_id` logt `main.py` een `warning` zonder te crashen.

### Module 4: Portal-frontend wizard

**R4:** `add-connector.tsx` zet `ms_docs` op `available: true`, label "Office 365", icon `FileText` (fallback voor MS-icon).

**R4.1:** Wizard volgt voor `ms_docs` dezelfde OAuth-dans als `google_drive` (create connector → fetch authorize_url → redirect).

**R4.2:** Optionele config-velden:
- `site_url` — placeholder `https://contoso.sharepoint.com/sites/marketing`, helper-text "laat leeg voor persoonlijke OneDrive"
- `drive_id` — advanced, helper-link naar Graph Explorer

**R4.3:** Client-side `site_url` validatie via regex `^https://[a-z0-9-]+\.sharepoint\.com/sites/[^/]+/?$`.

**R4.4:** Edit-connector pagina toont "Opnieuw verbinden" knop voor verlopen refresh_tokens.

**R4.5:** Alle i18n-strings via Paraglide (`admin_connectors_ms_docs_*` en `admin_connectors_type_ms_docs = "Office 365"`) in NL + EN.

### Module 5: Configuratie

**R5:** Portal `config.py` krijgt:
- `ms_docs_client_id: str = ""`
- `ms_docs_client_secret: str = ""`
- `ms_docs_tenant_id: str = "common"`

**R5.1:** Connector `config.py` idem.

**R5.2:** `deploy/docker-compose.yml` mount `MS_DOCS_CLIENT_ID`, `MS_DOCS_CLIENT_SECRET`, `MS_DOCS_TENANT_ID` op `portal-api` + `klai-connector`.

**R5.3:** `klai-infra/core-01/.env.sops` bevat versleutelde waarden na Azure AD app-registratie.

### Module 6: Azure AD registratie — runbook

**R6:** `docs/runbooks/ms-docs-oauth.md` documenteert stap-voor-stap:
1. App registrations → New (multi-tenant)
2. Redirect URI: `https://my.getklai.com/api/oauth/ms_docs/callback`
3. Delegated permissions: `offline_access`, `User.Read`, `Files.Read.All`, `Sites.Read.All`
4. Client secret → SOPS
5. Grant tenant-wide admin consent voor dev-tenant
6. Verify via `/oauth2/v2.0/authorize` curl

### Module 7: Tests

**R7:** `tests/adapters/test_ms_docs.py` coverage ≥ 85%.

**R7.1:** Tests via `httpx.MockTransport` + `pytest-asyncio` + `unittest.mock.patch`.

**R7.2:** Verplichte scenarios: first-sync, incremental-sync, paginatie, token-refresh, refresh-rotation (R9), 429 retry, content-download, MIME mapping, `drive_id`/`site_url` varianten.

**R7.3:** `tests/test_oauth_routes.py` krijgt `ms_docs` variant parallel aan `google_drive`.

**R7.4:** Integration test runt volledige sync-flow via `AdapterRegistry` met mocked Graph responses.

### Module 8: Quality & Observability

**R8:** `ruff check` + `pyright` clean op alle nieuwe bestanden.

**R8.1:** `@MX:ANCHOR` + `@MX:REASON` op methoden met `fan_in >= 3`.

**R8.2:** Structured logs met `connector_id`, `org_id`, `item_count`, `duration_ms`, `status_code` — nooit tokens of bodies.

**R8.3:** Cross-service trace-test via `request_id:<uuid>` in VictoriaLogs.

### Module 9: Refresh-token rotation support (PortalClient uitbreiding)

**R9:** `PortalClient.update_credentials` krijgt optionele `refresh_token: str | None = None` parameter:
```python
async def update_credentials(
    self,
    connector_id: str,
    access_token: str,
    token_expiry: str | None = None,
    refresh_token: str | None = None,
) -> None
```

**R9.1:** `PATCH /internal/connectors/{id}/credentials` accepteert optioneel `refresh_token` in de JSON body.

**R9.2:** `OAuthAdapterBase.ensure_token` parsed nieuwe `refresh_token` uit refresh-response; indien aanwezig én verschillend van input → writeback via `update_credentials(refresh_token=...)` + in-memory mutatie van `connector.config["refresh_token"]`.

**R9.3:** Tests dekken beide paden: (a) geen rotatie → RT writeback skipped, (b) rotatie → beide tokens versleuteld opgeslagen.

**R9.4:** Additief en backward-compatible — Google Drive blijft werken.

---

## Architecture — samenvatting

```
┌──────────────────────────────────────────────────────────────────┐
│ portal-frontend: /app/knowledge/{kb}/add-connector               │
│   Office 365 tile (was: ms_docs available:false → true)          │
│     ↓ "Connect with Microsoft"                                   │
│     POST /api/connectors (with optional site_url / drive_id)     │
│     GET  /api/oauth/ms_docs/authorize?kb_slug=&connector_id=     │
│     → navigate to login.microsoftonline.com                      │
└──────────────────────────┬───────────────────────────────────────┘
                           │ consent
                           ▼
┌──────────────────────────────────────────────────────────────────┐
│ portal-api: /api/oauth/ms_docs/callback                          │
│   verify state cookie → exchange code → encrypt tokens           │
│   via ConnectorCredentialStore (SPEC-KB-020)                     │
│   → redirect /app/knowledge/{kb}/connectors?oauth=connected      │
└──────────────────────────┬───────────────────────────────────────┘
                           │ scheduled sync
                           ▼
┌──────────────────────────────────────────────────────────────────┐
│ klai-connector: MsDocsAdapter (extends OAuthAdapterBase)         │
│   ensure_token → Graph token endpoint (refresh if expired)       │
│     ↑ rotates refresh_token if Microsoft issues a new one (R9)   │
│   list_documents → /drive/root/delta (or /sites/{id}/ or         │
│                    /drives/{id}/) + paginate                     │
│   fetch_document → GET /drive/items/{id}/content                 │
│   get_cursor_state → {"delta_link": <url>}                       │
│                                                                   │
│ Chunk metadata:                                                  │
│   source_ref = item.id                                           │
│   source_url = item.webUrl                                       │
│   last_edited = item.lastModifiedDateTime                        │
│   sender_email = item.lastModifiedBy.user.email                  │
│   mentioned_emails = [createdBy.email, lastModifiedBy.email]     │
└──────────────────────────────────────────────────────────────────┘
```

---

## Verification

1. **Adapter:**
   - `pytest klai-connector/tests/adapters/test_ms_docs.py -v --cov=app.adapters.ms_docs` → coverage ≥ 85%
   - `uv run ruff check klai-connector/app/adapters/ms_docs.py` → clean
   - `uv run --with pyright pyright klai-connector/app/adapters/ms_docs.py` → clean

2. **Portal OAuth routes:**
   - `pytest klai-portal/backend/tests/test_oauth_routes.py -v -k ms_docs` → alle cases groen

3. **End-to-end (staging, na Azure AD app-registratie):**
   - Configureer ms_docs connector in portal → OAuth redirect → consent → connected
   - Trigger sync → VictoriaLogs `service:klai-connector AND connector_type:ms_docs` toont item counts
   - Qdrant: chunks hebben `source_url` met `webUrl` en `sender_email` gevuld
   - Chat-citatie klikt door naar SharePoint/OneDrive web-view

4. **Incremental sync:**
   - Na eerste sync: `connector.sync_runs.cursor_state` bevat `{"delta_link": "https://graph.microsoft.com/..."}`
   - Bewerk één document → volgende sync ingesereert alleen dat document

5. **Credential safety:**
   - `grep -r "Bearer\|access_token\|refresh_token" klai-connector/app/adapters/ms_docs.py` — alleen metadata-logs
   - Encrypted-at-rest: `SELECT config FROM portal_connectors WHERE connector_type='ms_docs'` toont `***`

6. **Token refresh + rotation:**
   - Handmatig `connector.config.token_expiry` op verleden zetten → sync → log `Refreshing OAuth token (provider=MsDocsAdapter)` → sync completes
   - Na tweede refresh: verifieer in `portal_connectors` dat `encrypted_credentials` is gewijzigd (new refresh_token opgeslagen)

---

## Risks

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Admin-consent vereist voor `Files.Read.All` / `Sites.Read.All` — gewone gebruiker kan connector niet zelf activeren | High | High | Documenteer in wizard-copy en runbook. Na eerste tenant-wide consent werkt elke user in die tenant. |
| Graph rate-limits bij grote SharePoint-tenants | Medium | Medium | R2.11 implementeert retry met `Retry-After`. v2: checkpointing binnen een run. |
| deltaLink expiry (>30d inactief) → 410 Gone | Low | Medium | Catch 410 → reset `cursor_state` → volgende run doet full re-sync. |
| Refresh-token rotation — oude RT invalid na grace window | High | High | R9 verplicht writeback van nieuwe RT. `update_credentials` uitgebreid met optionele parameter. |
| `site_url` invoer UX — user plakt verkeerde URL (document i.p.v. site-root) | Medium | Low | R4.3 client-side regex validatie + server-side 404 via Graph resolutie-call. |
| Wijking van `SPEC-KB-CONNECTORS-001` R2 (SDK verplicht) + `SPEC-KB-025` (MSAL + unstructured-ingest) naar direct httpx | N/A | N/A | Expliciet gedocumenteerd in D2. Consistentie met `GoogleDriveAdapter` + minimale deps. |
| Azure AD app-registratie blokkeert bij ontbrekende DPIA/AVG-review voor `Files.Read.All` | Low | High | Pre-flight in test-tenant. Runbook documenteert. |
| `site_url` → `site_id` resolutie 403 (admin-consent ontbreekt) | Medium | Medium | Catch 403 specifiek → helder error-message in portal-frontend. |
| PortalClient API-signature wijziging (R9) breekt bestaande tests | Low | Low | Optionele parameter met `None` default — bestaande call-sites blijven werken. |

---

## Open vragen

Alle open vragen uit v1.0.0 beantwoord in v1.1.0 na owner-review:

1. ~~**App-registratie eigenaar.**~~ ✅ Klai-team M365 tenant, zelfde structuur als `ZITADEL_IDP_MICROSOFT_ID`. Credentials klai-team-beheerd, SOPS-versleuteld. Zie D3.
2. ~~**Multi-tenant vs single-tenant.**~~ ✅ Multi-tenant (`tenant_id=common`).
3. ~~**Naming in UI.**~~ ✅ Eén tile, label **"Office 365"**. Consistent met Google Drive als één tile. Zie D4.
4. ~~**Refresh-token rotation.**~~ ✅ Onderdeel van dit SPEC — Module 9 (R9 t/m R9.4). Additief, backward-compatible, baat ook Google Drive.
5. ~~**Site-picker vs tekstveld.**~~ ✅ Tekstveld met `site_url` + optionele `drive_id`. Geen picker in v1. Server-side resolutie via `/sites/{hostname}:/{path}`. Zie D5.

---

## Referenties

### Klai intern
- [SPEC-KB-025](../../docs/specs/SPEC-KB-025.md) — OAuth adapter framework (`OAuthAdapterBase`), partial predecessor
- [SPEC-KB-020](../SPEC-KB-020/spec.md) — Connector credential encryption (`ConnectorCredentialStore`)
- [SPEC-KB-CONNECTORS-001](../SPEC-KB-CONNECTORS-001/spec.md) — Standaard connectors framework
- [SPEC-AUTH-001](../../docs/specs/SPEC-AUTH-001.md) — Social signup flow (reference)
- [docs/runbooks/ms-docs-oauth.md](../../docs/runbooks/ms-docs-oauth.md) — Azure AD app-registratie runbook

### Microsoft documentatie
- [Microsoft Graph permissions reference](https://learn.microsoft.com/en-us/graph/permissions-reference)
- [Overview of permissions and consent](https://learn.microsoft.com/en-us/entra/identity-platform/permissions-consent-overview)
- [driveItem: delta](https://learn.microsoft.com/en-us/graph/api/driveitem-delta?view=graph-rest-1.0)
- [Use delta query to track changes](https://learn.microsoft.com/en-us/graph/delta-query-overview)
- [Best practices for discovering files at scale](https://learn.microsoft.com/en-us/onedrive/developer/rest-api/concepts/scan-guidance?view=odsp-graph-online)
- [Get access on behalf of a user (OAuth 2.0)](https://learn.microsoft.com/en-us/graph/auth-v2-user)
- [Refresh tokens in Microsoft identity platform](https://learn.microsoft.com/en-us/entra/identity-platform/refresh-tokens)
- [msgraph-sdk-python](https://github.com/microsoftgraph/msgraph-sdk-python) — overwogen en afgewezen in D2
