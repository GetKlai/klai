---
id: SPEC-KB-025
version: 1.0.0
status: draft
created: 2026-04-13
updated: 2026-04-13
author: mark
priority: high
---

# HISTORY

| Version | Date       | Author | Change                          |
|---------|------------|--------|---------------------------------|
| 1.0.0   | 2026-04-13 | mark   | Initial draft                   |

---

# SPEC-KB-025: OAuth Connectors — Google Drive & SharePoint

## Overview

Voeg twee nieuwe kennisbronconnectors toe aan het Klai platform: **Google Drive** en **SharePoint (ms_docs)**. Beide connectors gebruiken een OAuth2 authorization code flow waarbij de gebruiker via een standaard "Connect"-knop zijn account koppelt. Klai registreert één Google Cloud OAuth app en één Azure AD multi-tenant app als platforminfrastructuur. De connectors gebruiken `unstructured-ingest` voor document discovery en download, zodat toekomstige connectors met minimale glue-code kunnen worden toegevoegd uit de community-bibliotheek van 71+ connectors.

---

## Module 1: OAuth Authorization Flow (Portal Backend)

### 1.1 Ubiquitous Language

- **Authorization Code Flow**: OAuth2 flow waarbij de gebruiker via een browser redirect toestemming verleent. De server ontvangt een `code` die wordt uitgewisseld voor tokens.
- **State Parameter**: Cryptografisch willekeurige string die CSRF-aanvallen voorkomt bij de OAuth callback.
- **Token Writeback**: Het opslaan van een vernieuwd access token terug in de portal database nadat klai-connector het heeft ververst.
- **Refresh Token**: Langdurig token waarmee een verlopen access token kan worden vernieuwd zonder opnieuw in te loggen.

### 1.2 Functional Requirements (EARS)

**WHERE** een gebruiker een Google Drive of SharePoint connector aanmaakt,
**THE SYSTEM SHALL** een OAuth2 authorization URL genereren en de gebruiker daarnaar doorsturen.

**WHEN** de OAuth provider een authorization code retourneert via de callback URL,
**THE SYSTEM SHALL** de code uitwisselen voor `access_token` en `refresh_token`, deze versleutelen via `ConnectorCredentialStore`, en opslaan in `portal_connectors.encrypted_credentials`.

**WHEN** een state parameter ontbreekt of niet overeenkomt met de sessiewaarde,
**THE SYSTEM SHALL** de callback afwijzen met HTTP 400 en geen tokens opslaan.

**WHERE** een klai-connector adapter een verlopen access token detecteert,
**THE SYSTEM SHALL** het nieuwe token via `PATCH /internal/connectors/{connector_id}/credentials` terugschrijven naar de portal database.

**WHILE** een OAuth callback wordt verwerkt,
**THE SYSTEM SHALL** de state-waarde opslaan in de gebruikerssessie (via de bestaande Zitadel OIDC sessie) en na afronding verwijderen.

### 1.3 Non-Functional Requirements

- State parameter: 32 bytes cryptografisch willekeurig, URL-safe base64-encoded.
- Token uitwisseling: maximaal 5 seconden timeout.
- Geen OAuth tokens in logs of error responses.

---

## Module 2: Google Drive Adapter

### 2.1 Ubiquitous Language

- **Drive Change Token**: Een opaque token van de Google Drive Changes API dat de positie in de wijzigingengeschiedenis bijhoudt. Wordt gebruikt als cursor voor incrementele sync.
- **Export MIME Type**: Google-native bestanden (Docs, Sheets, Slides) bestaan niet als downloadbaar bestand; ze worden geëxporteerd naar een standaard formaat (Docs → DOCX, Sheets → CSV).

### 2.2 Functional Requirements (EARS)

**WHERE** een Google Drive connector wordt gesynchroniseerd,
**THE SYSTEM SHALL** de opgeslagen `refresh_token` uitwisselen voor een vers `access_token` vóór de eerste API-aanroep, via het Google token endpoint.

**WHEN** het `access_token` succesvol is vernieuwd,
**THE SYSTEM SHALL** het nieuwe token direct terugschrijven naar de portal via het token writeback endpoint.

**WHERE** een Google Drive connector voor het eerst wordt gesynchroniseerd (geen cursor),
**THE SYSTEM SHALL** alle bestanden in de geconfigureerde `drive_id` of gedeelde drive ophalen via `files.list` met mimeType-filter.

**WHEN** een vorige cursor aanwezig is (`drive_change_token`),
**THE SYSTEM SHALL** alleen bestanden ophalen die zijn gewijzigd of toegevoegd via de `changes.list` API.

**WHERE** een bestand een Google-native MIME type heeft (Docs, Sheets, Slides),
**THE SYSTEM SHALL** het bestand exporteren naar het bijbehorende standaard formaat (DOCX, CSV, PPTX) via `files.export`.

**WHEN** `unstructured-ingest` een bestand niet kan parseren,
**THE SYSTEM SHALL** het document overslaan, de fout loggen, en doorgaan met de volgende documenten.

**WHERE** een Google Drive sync is afgerond,
**THE SYSTEM SHALL** de `drive_change_token` opslaan als cursor state via `get_cursor_state`.

### 2.3 Supported File Types

| Google MIME Type | Export Format | unstructured Parser |
|---|---|---|
| `application/vnd.google-apps.document` | DOCX | `partition_docx` |
| `application/vnd.google-apps.spreadsheet` | CSV | `partition_csv` |
| `application/vnd.google-apps.presentation` | PPTX | `partition_pptx` |
| `application/pdf` | (native) | `partition_pdf` |
| `text/plain` | (native) | `partition_text` |

### 2.4 Non-Functional Requirements

- Google Drive API rate limit: maximaal 100 requests per seconde (default quota).
- `source_ref`: gebruik Drive `fileId` (stabiel bij hernoemen/verplaatsen).
- `source_url`: `https://drive.google.com/file/d/{fileId}/view`.

---

## Module 3: SharePoint Adapter (ms_docs)

### 3.1 Ubiquitous Language

- **Delta Link**: Een URL die de Microsoft Graph delta API retourneert na een volledige sync. Opvolgende aanroepen via deze URL geven alleen gewijzigde items terug.
- **Drive Item**: Een bestand of map in een SharePoint document library, geïdentificeerd door een `driveItem.id`.
- **Site URL**: De volledig gekwalificeerde URL van een SharePoint site, bijv. `https://contoso.sharepoint.com/sites/marketing`.

### 3.2 Functional Requirements (EARS)

**WHERE** een SharePoint connector wordt gesynchroniseerd,
**THE SYSTEM SHALL** het `refresh_token` uitwisselen voor een vers Microsoft Graph `access_token` via MSAL's `acquire_token_by_refresh_token`, met Klai's Azure AD multi-tenant app credentials.

**WHEN** het `access_token` succesvol is vernieuwd,
**THE SYSTEM SHALL** het nieuwe token terugschrijven naar de portal via het token writeback endpoint.

**WHERE** een SharePoint connector voor het eerst wordt gesynchroniseerd (geen delta link),
**THE SYSTEM SHALL** alle bestanden in de geconfigureerde site en document library ophalen via `GET /drives/{drive-id}/root/children` (recursief).

**WHEN** een vorige `delta_link` aanwezig is als cursor,
**THE SYSTEM SHALL** alleen gewijzigde of nieuwe bestanden ophalen via de delta link.

**WHERE** een bestand wordt gedownload,
**THE SYSTEM SHALL** de Microsoft Graph `@microsoft.graph.downloadUrl` gebruiken voor directe download zonder additionele authenticatie.

**WHERE** een SharePoint sync is afgerond,
**THE SYSTEM SHALL** de nieuwe `delta_link` opslaan als cursor state.

### 3.3 Supported File Types

`.docx`, `.pdf`, `.txt`, `.pptx`, `.xlsx` — alle ondersteund door `unstructured-ingest`.

### 3.4 Non-Functional Requirements

- `source_ref`: gebruik SharePoint `driveItem.id`.
- `source_url`: gebruik `webUrl` van het driveItem.
- MSAL token cache: per-adapter in-memory cache; per connector instance.

---

## Module 4: Frontend OAuth UI

### 4.1 Functional Requirements (EARS)

**WHERE** een gebruiker de connector-selectiepagina opent,
**THE SYSTEM SHALL** de Google Drive en SharePoint tegels als beschikbaar tonen (niet langer "Coming Soon").

**WHEN** een gebruiker "Connect Google Drive" of "Connect SharePoint" selecteert,
**THE SYSTEM SHALL** een OAuth initiatie-aanroep doen naar het portal backend en de gebruiker doorsturen naar de OAuth provider.

**WHEN** de OAuth flow succesvol is afgerond en de gebruiker terugkeert naar het portal,
**THE SYSTEM SHALL** de gebruiker direct naar de configuratiestap leiden (drive ID of site URL invullen).

**WHERE** een connector al gekoppeld is,
**THE SYSTEM SHALL** een "Opnieuw verbinden" knop tonen in de edit-pagina voor het geval de autorisatie is verlopen.

### 4.2 Non-Functional Requirements

- OAuth popup of redirect: gebruik redirect (geen popup) voor maximale browsercompatibiliteit.
- i18n: alle nieuwe strings in `en.*` en `nl.*` paraglide message files.

---

## Module 5: Dependencies & Configuration

### 5.1 Functional Requirements (EARS)

**WHERE** de klai-connector service wordt gestart,
**THE SYSTEM SHALL** de volgende omgevingsvariabelen inlezen via `pydantic-settings`:
- `GOOGLE_DRIVE_CLIENT_ID`
- `GOOGLE_DRIVE_CLIENT_SECRET`
- `SHAREPOINT_CLIENT_ID`
- `SHAREPOINT_CLIENT_SECRET`
- `SHAREPOINT_TENANT_AUTHORITY` (bijv. `https://login.microsoftonline.com/common`)

**WHERE** de klai-portal service een OAuth flow initieert,
**THE SYSTEM SHALL** dezelfde client credentials gebruiken als klai-connector voor token refresh.

### 5.2 New Dependencies

**klai-connector/pyproject.toml:**
```
unstructured-ingest[google-drive]>=0.5
unstructured-ingest[sharepoint]>=0.5
msal>=1.31
```

**klai-portal/backend** (geen nieuwe dependencies — `httpx` al aanwezig voor token uitwisseling).
