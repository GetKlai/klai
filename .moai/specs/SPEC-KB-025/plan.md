# SPEC-KB-025 Implementation Plan

## Task Decomposition

### Phase 1: Portal Backend — OAuth Routes

**Target files:**
- `klai-portal/backend/app/api/oauth.py` (nieuw)
- `klai-portal/backend/app/api/internal.py` (uitbreiden)
- `klai-portal/backend/app/main.py` (router registreren)

**Tasks:**

1. **OAuth initiation endpoint**
   `GET /api/oauth/{provider}/authorize?kb_slug=...&connector_id=...`
   - `provider` ∈ `{"google-drive", "sharepoint"}`
   - Genereer state (32 bytes, URL-safe base64), sla op in sessie
   - Bouw authorization URL (Google: `accounts.google.com/o/oauth2/v2/auth`, Microsoft: `login.microsoftonline.com/common/oauth2/v2.0/authorize`)
   - Scopes Google Drive: `https://www.googleapis.com/auth/drive.readonly`
   - Scopes SharePoint: `https://graph.microsoft.com/Files.Read.All offline_access`
   - Redirect de gebruiker (HTTP 302)

2. **OAuth callback endpoint**
   `GET /api/oauth/{provider}/callback?code=...&state=...`
   - Valideer state (HTTP 400 bij mismatch)
   - Wissel code uit voor tokens via `httpx.AsyncClient` (POST naar provider token endpoint)
   - Encrypt tokens via `credential_store.encrypt_credentials()`
   - Update `portal_connectors.encrypted_credentials` voor de connector
   - Redirect naar portal UI (terug naar connector setup pagina)

3. **Token writeback endpoint** (internal API)
   `PATCH /internal/connectors/{connector_id}/credentials`
   Body: `{"access_token": "...", "token_expiry": "..."}`
   - Auth: `X-Internal-Secret` header (consistent met alle andere internal endpoints)
   - Decrypt bestaande credentials, update access_token + token_expiry, re-encrypt, sla op

**Reference implementations:**
- State opslag: gebruik bestaande sessie-mechanisme (bekijk hoe OIDC state wordt opgeslagen in auth flow)
- Credential encryptie: `klai-portal/backend/app/services/connector_credentials.py:83` (`encrypt_credentials`)
- Internal secret auth: `klai-portal/backend/app/api/internal.py` (bestaand patroon)

---

### Phase 2: Google Drive Adapter

**Target file:** `klai-connector/app/adapters/google_drive.py` (nieuw)

**Structure:**
```python
class GoogleDriveAdapter(BaseAdapter):
    def __init__(self, settings: Settings) -> None: ...
    
    async def _get_access_token(self, connector: PortalConnectorConfig) -> str:
        """Check expiry, refresh via httpx if needed, writeback to portal."""
    
    async def list_documents(self, connector, cursor_context) -> list[DocumentRef]:
        """First sync: files.list() | Incremental: changes.list(changeToken)"""
    
    async def fetch_document(self, ref: DocumentRef, connector) -> bytes:
        """Export Google-native files or download binary files."""
    
    async def get_cursor_state(self, connector) -> dict[str, Any]:
        """Return {"drive_change_token": self._change_token}"""
```

**Token refresh pattern** (volg `github.py:49-110`):
```python
_token_cache: dict[str, tuple[str, float]] = {}  # connector_id → (token, expires_at)

async def _get_access_token(self, connector):
    now = monotonic()
    cached = self._token_cache.get(connector.connector_id)
    if cached and cached[1] > now + 60:
        return cached[0]
    # POST https://oauth2.googleapis.com/token
    resp = await self._client.post("https://oauth2.googleapis.com/token", data={...})
    new_token = resp.json()["access_token"]
    expires_in = resp.json().get("expires_in", 3600)
    self._token_cache[connector.connector_id] = (new_token, now + expires_in)
    await self._portal_client.update_credentials(connector.connector_id, new_token)
    return new_token
```

**Google Drive Change Token flow:**
- Eerste sync: `GET https://www.googleapis.com/drive/v3/files` → sla `startPageToken` op als change token
- Volgende sync: `GET https://www.googleapis.com/drive/v3/changes?pageToken={change_token}`

**Export MIME mapping:**
```python
EXPORT_MIME_MAP = {
    "application/vnd.google-apps.document":     ("application/vnd.openxmlformats-officedocument.wordprocessingml.document", ".docx"),
    "application/vnd.google-apps.spreadsheet":  ("text/csv", ".csv"),
    "application/vnd.google-apps.presentation": ("application/vnd.openxmlformats-officedocument.presentationml.presentation", ".pptx"),
}
SUPPORTED_NATIVE = {"application/pdf", "text/plain", "text/markdown"}
```

**Reference:** `klai-connector/app/adapters/github.py:49-110` (token cache), `notion.py:223-280` (list_documents pattern)

---

### Phase 3: SharePoint Adapter

**Target file:** `klai-connector/app/adapters/ms_docs.py` (nieuw)

**Structure:**
```python
class SharePointAdapter(BaseAdapter):
    def __init__(self, settings: Settings) -> None: ...
    
    async def _get_access_token(self, connector: PortalConnectorConfig) -> str:
        """MSAL acquire_token_by_refresh_token via asyncio.to_thread."""
    
    async def list_documents(self, connector, cursor_context) -> list[DocumentRef]:
        """First sync: recursive /drive/root/children | Delta: follow delta_link"""
    
    async def fetch_document(self, ref: DocumentRef, connector) -> bytes:
        """GET @microsoft.graph.downloadUrl (pre-authenticated, no extra auth needed)"""
    
    async def get_cursor_state(self, connector) -> dict[str, Any]:
        """Return {"delta_link": self._delta_link}"""
```

**MSAL token refresh pattern** (MSAL is sync → `asyncio.to_thread`):
```python
async def _get_access_token(self, connector):
    now = monotonic()
    cached = self._token_cache.get(connector.connector_id)
    if cached and cached[1] > now + 60:
        return cached[0]
    
    def _refresh_sync():
        app = msal.ConfidentialClientApplication(
            settings.sharepoint_client_id,
            authority=settings.sharepoint_tenant_authority,
            client_credential=settings.sharepoint_client_secret,
        )
        return app.acquire_token_by_refresh_token(
            connector.config["refresh_token"],
            scopes=["https://graph.microsoft.com/Files.Read.All"],
        )
    
    result = await asyncio.to_thread(_refresh_sync)
    new_token = result["access_token"]
    # cache + writeback
    ...
```

**Microsoft Graph delta flow:**
- Eerste sync: `GET /drives/{driveId}/root/delta` → pagineer t/m laatste pagina → sla `@odata.deltaLink` op
- Volgende sync: `GET {delta_link}` (absolute URL)

**Reference:** `klai-connector/app/adapters/notion.py` (asyncio.to_thread patroon), `github.py` (token cache)

---

### Phase 4: klai-connector Config & Registry

**Target files:**
- `klai-connector/app/core/config.py` — 5 nieuwe settings fields
- `klai-connector/app/main.py` — 2 nieuwe registry.register() aanroepen
- `klai-connector/pyproject.toml` — 3 nieuwe dependencies

**Settings fields:**
```python
google_drive_client_id: str = ""
google_drive_client_secret: str = ""
sharepoint_client_id: str = ""
sharepoint_client_secret: str = ""
sharepoint_tenant_authority: str = "https://login.microsoftonline.com/common"
```

Leeg string = feature disabled (bestaand patroon, zie `garage_s3_endpoint`).

**Registry:**
```python
if settings.google_drive_client_id:
    registry.register("google_drive", GoogleDriveAdapter(settings, portal_client))
if settings.sharepoint_client_id:
    registry.register("ms_docs", SharePointAdapter(settings, portal_client))
```

---

### Phase 5: Frontend UI

**Target files:**
- `klai-portal/frontend/src/routes/app/knowledge/$kbSlug_.add-connector.tsx`
- `klai-portal/frontend/src/routes/app/knowledge/$kbSlug_.edit-connector.$connectorId.tsx`
- `klai-portal/frontend/src/paraglide/messages/en.*`
- `klai-portal/frontend/src/paraglide/messages/nl.*`

**Changes in add-connector.tsx:**
1. Zet `available: true` voor `google_drive` en `ms_docs`
2. Voeg OAuth redirect handler toe: bij selectie → `GET /api/oauth/{provider}/authorize?kb_slug=...` → browser redirect
3. Na terugkeer (callback): sla connector name + extra config (drive_id / site_url) op via `POST /api/app/knowledge-bases/{kbSlug}/connectors/`

**Changes in edit-connector.tsx:**
- Voeg branch toe voor `google_drive` en `ms_docs` met "Opnieuw verbinden" knop + drive_id/site_url veld

**i18n strings nodig (nl + en):**
- `connector_google_drive_label`
- `connector_ms_docs_label`
- `connector_reconnect_button`
- `connector_drive_id_label`
- `connector_site_url_label`
- `connector_oauth_connecting`

---

## Technology Stack

| Component | Library | Versie |
|---|---|---|
| Google token refresh | `httpx` (al aanwezig) | — |
| Google Drive API client | `httpx` (REST calls direct) | — |
| SharePoint token refresh | `msal` | `>=1.31` |
| SharePoint file listing | `httpx` (Microsoft Graph REST) | — |
| Document parsing | `unstructured-ingest[google-drive,sharepoint]` | `>=0.5` |
| State parameter | Python `secrets.token_urlsafe(32)` | stdlib |

---

## Risk Analysis

| Risk | Probability | Impact | Mitigation |
|---|---|---|---|
| Google Drive API quota (100 req/s) | Medium | Medium | Backoff bij 429, batch file exports |
| SharePoint delta link expiry (30 dagen inactief) | Low | Medium | Bij 410 Gone: volledige re-sync als fallback |
| `unstructured-ingest` breaking changes | Low | High | Pin major version, test bij upgrade |
| Token writeback race condition (concurrent syncs) | Low | Low | Per-connector `asyncio.Lock` in SyncEngine voorkomt parallelle syncs voor dezelfde connector |
| OAuth state CSRF zonder persistente sessie | Medium | High | State opslaan in signed cookie (niet alleen in-memory) |

---

## Architectural Decisions

### ADR-1: httpx voor Google Drive, MSAL voor SharePoint
Google heeft geen officiële async Python client. Direct `httpx` calls zijn consistent met de bestaande GitHub adapter. Microsoft's MSAL library is de aanbevolen manier voor token management; wrap met `asyncio.to_thread` (bestaand patroon in de codebase voor sync SDKs).

### ADR-2: unstructured-ingest als parsing engine, niet als pipeline runner
`unstructured-ingest` wordt gebruikt voor document parsing (via `partition_*` functies), niet als volledige pipeline runner. De klai-connector `BaseAdapter` interface blijft de orchestratielaag. Dit behoudt incremental sync, cursor state, en de bestaande error handling.

### ADR-3: Token writeback via portal internal API
Klai-connector is stateless — het kan niet direct naar de portal DB schrijven. Een nieuw `PATCH /internal/connectors/{connector_id}/credentials` endpoint in de portal houdt dit schoon gescheiden en consistent met het bestaande two-service model.

### ADR-4: Feature flag via lege string
Google Drive en SharePoint adapters worden alleen geregistreerd als de client credentials zijn geconfigureerd (lege string = disabled). Bestaand patroon uit `garage_s3_endpoint`.
