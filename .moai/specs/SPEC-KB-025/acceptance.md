# SPEC-KB-025 Acceptance Criteria

## Scenario 1: Google Drive OAuth koppeling — happy path

**Given** een ingelogde gebruiker die KB-eigenaar is,
  en de klai-portal heeft `GOOGLE_DRIVE_CLIENT_ID` en `GOOGLE_DRIVE_CLIENT_SECRET` geconfigureerd,

**When** de gebruiker op de "Connect Google Drive" tegel klikt in de connector-selectiepagina,

**Then** wordt de gebruiker doorgestuurd naar `accounts.google.com/o/oauth2/v2/auth`
  met scope `https://www.googleapis.com/auth/drive.readonly`,
  en keert na autorisatie terug naar de connector setup pagina,
  en zijn `access_token` en `refresh_token` versleuteld opgeslagen in `portal_connectors.encrypted_credentials`.

---

## Scenario 2: Google Drive OAuth — CSRF bescherming

**Given** een OAuth callback request zonder geldig state-parameter,

**When** `GET /api/oauth/google-drive/callback?code=xxx&state=invalid` wordt aangeroepen,

**Then** retourneert de server HTTP 400,
  en worden er geen tokens opgeslagen,
  en bevat de response body geen OAuth code of token.

---

## Scenario 3: Google Drive eerste synchronisatie

**Given** een Google Drive connector met geldige OAuth tokens,
  en er is geen vorige sync cursor,

**When** een sync wordt getriggerd,

**Then** roept de adapter `files.list` aan voor alle ondersteunde bestandstypen,
  en worden Google Docs geëxporteerd als `.docx`,
  en worden Google Sheets geëxporteerd als `.csv`,
  en worden alle bestanden ingediend bij `klai-knowledge-ingest`,
  en slaat de adapter een `drive_change_token` op als cursor state.

---

## Scenario 4: Google Drive incrementele synchronisatie

**Given** een Google Drive connector met een bestaande `drive_change_token` cursor,
  en één bestand is gewijzigd en twee bestanden zijn ongewijzigd,

**When** een sync wordt getriggerd,

**Then** roept de adapter `changes.list` aan met de bestaande `drive_change_token`,
  en worden alleen de gewijzigde bestanden opnieuw ingediend (niet de ongewijzigde),
  en wordt de `drive_change_token` bijgewerkt naar de nieuwe cursor waarde.

---

## Scenario 5: Google Drive token verloop tijdens sync

**Given** een Google Drive connector waarvan het `access_token` is verlopen,
  en een geldig `refresh_token` is aanwezig,

**When** een sync wordt gestart,

**Then** wisselt de adapter het `refresh_token` uit voor een nieuw `access_token` vóór de eerste API-aanroep,
  en schrijft het nieuwe `access_token` terug naar de portal via `PATCH /internal/connectors/{id}/credentials`,
  en verloopt de sync zonder `AUTH_ERROR`.

---

## Scenario 6: SharePoint OAuth koppeling — happy path

**Given** een ingelogde gebruiker die KB-eigenaar is,
  en de klai-portal heeft `SHAREPOINT_CLIENT_ID` en `SHAREPOINT_CLIENT_SECRET` geconfigureerd,

**When** de gebruiker op de "Connect SharePoint" tegel klikt,

**Then** wordt de gebruiker doorgestuurd naar `login.microsoftonline.com/common/oauth2/v2.0/authorize`
  met scopes `https://graph.microsoft.com/Files.Read.All offline_access`,
  en keert na autorisatie terug naar de connector setup pagina,
  en zijn `access_token` en `refresh_token` versleuteld opgeslagen.

---

## Scenario 7: SharePoint incrementele synchronisatie via delta link

**Given** een SharePoint connector met een bestaande `delta_link` cursor,
  en één document is gewijzigd in SharePoint,

**When** een sync wordt getriggerd,

**Then** roept de adapter de `delta_link` URL aan via Microsoft Graph,
  en worden alleen gewijzigde documenten verwerkt,
  en wordt de nieuwe `delta_link` opgeslagen als bijgewerkte cursor.

---

## Scenario 8: SharePoint delta link verlopen (410 Gone)

**Given** een SharePoint connector waarvan de `delta_link` is verlopen (meer dan 30 dagen inactief),

**When** een sync wordt getriggerd,

**Then** detecteert de adapter HTTP 410 van de delta API,
  en voert een volledige re-sync uit (alsof er geen cursor is),
  en logt een waarschuwing `delta_link_expired, performing full resync`,
  en slaat een nieuwe `delta_link` op als cursor na afronding.

---

## Scenario 9: Frontend — beschikbaarheid connector tegels

**Given** de portal UI is geladen voor een KB-eigenaar,

**When** de gebruiker naar de "Voeg connector toe" pagina navigeert,

**Then** zijn de Google Drive en SharePoint tegels klikbaar (niet disabled / geen "Coming Soon" badge),
  en starten beide een OAuth redirect flow bij klikken.

---

## Scenario 10: Feature flag — uitgeschakeld zonder credentials

**Given** `GOOGLE_DRIVE_CLIENT_ID` is een lege string in de omgeving,

**When** klai-connector opstart,

**Then** wordt de `google_drive` adapter niet geregistreerd in de AdapterRegistry,
  en retourneert een sync-aanroep voor een `google_drive` connector HTTP 500 met `adapter_not_found`,
  en wordt er geen fout gegooid bij de startup van de service.

---

## Quality Gates

- [ ] `ruff check` en `pyright` slagen zonder fouten in gewijzigde bestanden
- [ ] Alle nieuwe adapter methoden hebben `asyncio`-compatibele implementaties (geen blocking calls buiten `asyncio.to_thread`)
- [ ] OAuth callback logt geen tokens (coverage via log output verificatie)
- [ ] Token writeback endpoint vereist `X-Internal-Secret` header (consistent met andere internal endpoints)
- [ ] `unstructured-ingest` dependency gepind op major version in `pyproject.toml`
- [ ] i18n strings aanwezig in zowel `en.*` als `nl.*` message files
