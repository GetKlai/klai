---
id: SPEC-INFRA-002
type: acceptance
version: "1.0.0"
---

# SPEC-INFRA-002: Acceptance Criteria — DB-driven Per-tenant MCP Server Management

---

## Module 1: MCP Catalog (`mcp_catalog.yaml`)

### AC-M1-01: Catalog bevat geldige twenty-crm entry

**Given** het bestand `deploy/librechat/mcp_catalog.yaml` bestaat
**When** de YAML wordt geparsed
**Then** bevat `servers.twenty-crm` de volgende velden:
  - `description` (niet-lege string)
  - `required_env_vars` (lijst met ten minste `TWENTY_API_KEY` en `TWENTY_BASE_URL`)
  - `config_template.type` gelijk aan `streamable-http`
  - `config_template.url` bevat `${TWENTY_BASE_URL}`
  - `config_template.headers.Authorization` bevat `${TWENTY_API_KEY}`

### AC-M1-02: Catalog bevat geen hardcoded secrets

**Given** het bestand `deploy/librechat/mcp_catalog.yaml` bestaat
**When** de inhoud wordt doorzocht op patronen `sk-`, `Bearer [a-zA-Z0-9]`, of plaintext API keys
**Then** worden er nul matches gevonden -- alleen `${VAR}` placeholders zijn toegestaan

---

## Module 2: Secrets + Alembic Migratie

### AC-M2-01: Encrypt/decrypt round-trip

**Given** een plaintext string `"sk-test-api-key-12345"`
**When** `encrypt_mcp_secret(plaintext)` wordt aangeroepen
**And** het resultaat wordt doorgegeven aan `decrypt_mcp_secret(ciphertext)`
**Then** is het geretourneerde resultaat exact gelijk aan de originele plaintext
**And** is de ciphertext een geldige base64-encoded string
**And** is de ciphertext NIET gelijk aan de plaintext

### AC-M2-02: Gedecrypteerde secrets verschijnen niet in logs

**Given** een MCP secret wordt geencrypt en opgeslagen
**When** `decrypt_mcp_secret()` wordt aangeroepen tijdens provisioning
**Then** bevat de structlog output op GEEN enkel log level (DEBUG, INFO, WARNING, ERROR) de gedecrypteerde waarde
**And** bevat de log output geen base64-encoded ciphertext

### AC-M2-03: Alembic migratie corrigeert seed data

**Given** de getklai tenant heeft verouderde stdio seed data in `mcp_servers`
**When** de nieuwe Alembic migratie (upgrade) wordt uitgevoerd
**Then** bevat `PortalOrg.mcp_servers` voor de getklai tenant:
  - Key `twenty-crm` met `enabled: true`
  - `env.TWENTY_API_KEY` als encrypted (niet-plaintext) waarde
  - `env.TWENTY_BASE_URL` als plaintext URL
**And** bevat de data GEEN verwijzingen naar `stdio` transport

### AC-M2-04: Alembic downgrade zet seed data terug

**Given** de migratie is uitgevoerd (upgrade)
**When** de downgrade wordt uitgevoerd
**Then** is `PortalOrg.mcp_servers` voor de getklai tenant teruggezet naar `None`

### AC-M2-05: Lege API key wordt geweigerd

**Given** een lege string `""` wordt aangeboden als MCP secret
**When** `encrypt_mcp_secret("")` wordt aangeroepen
**Then** wordt een `ValueError` geraised met een duidelijke foutmelding

---

## Module 3: Provisioning Updates

### AC-M3-01: YAML generatie gebruikt catalog als whitelist

**Given** de MCP Catalog bevat alleen `twenty-crm`
**And** de tenant DB bevat `mcp_servers` met entries `twenty-crm` (enabled) en `onbekend-server` (enabled)
**When** `_generate_librechat_yaml()` wordt aangeroepen
**Then** bevat de gegenereerde YAML een `mcpServers.twenty-crm` sectie
**And** bevat de YAML GEEN `mcpServers.onbekend-server` sectie
**And** is er een warning gelogd voor `onbekend-server`

### AC-M3-02: Env generatie decrypteert secrets correct

**Given** een tenant met `mcp_servers` containing `twenty-crm` (enabled) met encrypted `TWENTY_API_KEY`
**When** `_generate_librechat_env()` wordt aangeroepen
**Then** bevat de gegenereerde `.env` inhoud:
  - `TWENTY_API_KEY=<gedecrypteerde-waarde>` (de echte API key)
  - `TWENTY_BASE_URL=https://crm.getklai.com` (plaintext)
**And** bevat de `.env` GEEN base64 ciphertext of encrypted waarden

### AC-M3-03: Redis flush + container restart

**Given** een draaiende LibreChat container `librechat-getklai`
**When** `_flush_redis_and_restart_librechat("getklai")` wordt aangeroepen
**Then** wordt Redis FLUSHALL uitgevoerd
**And** wordt de container herstart
**And** is de container binnen 30 seconden weer healthy
**And** bevat de log output GEEN gedecrypteerde secrets

### AC-M3-04: Selectieve Redis flush (optioneel)

**Given** REQ-O-002 is geimplementeerd
**When** een MCP-configuratiewijziging wordt doorgevoerd
**Then** worden alleen de `CacheKeys.APP_CONFIG` gerelateerde keys geflushed
**And** blijven andere Redis keys (sessies, chat history) intact

### AC-M3-05: Disabled MCP server wordt niet opgenomen

**Given** een tenant met `mcp_servers` containing `twenty-crm` met `enabled: false`
**When** `_generate_librechat_yaml()` wordt aangeroepen
**Then** bevat de gegenereerde YAML GEEN `mcpServers.twenty-crm` sectie
**And** bevat `modelSpecs.list[].mcpServers` NIET de waarde `twenty-crm`

### AC-M3-06: Onbekend catalog ID wordt genegeerd

**Given** een tenant met `mcp_servers` containing een entry met ID `niet-bestaand`
**When** `_generate_librechat_yaml()` wordt aangeroepen
**Then** wordt de entry overgeslagen zonder crash
**And** wordt een warning gelogd met het onbekende ID
**And** worden alle andere geldige entries normaal verwerkt

### AC-M3-07: Container restart bij lege mcp_servers

**Given** een tenant met `mcp_servers` gelijk aan `None` of een lege dict
**When** `_generate_librechat_yaml()` wordt aangeroepen
**Then** wordt de base YAML ongewijzigd geretourneerd (alleen klai-knowledge)
**And** wordt er GEEN crash of error gelogd

---

## Module 4: Portal API

### AC-M4-01: GET retourneert catalog + tenant status

**Given** een geauthenticeerde admin van de getklai tenant
**When** `GET /api/orgs/{org_id}/mcp-servers` wordt aangeroepen
**Then** bevat de response een lijst met ten minste de `twenty-crm` entry
**And** bevat elke entry: `id`, `description`, `enabled`, `required_env_vars`, `configured_env_vars`
**And** bevat `configured_env_vars` GEEN secret waarden, alleen de keys als lijst van strings
**And** is de response status 200

### AC-M4-02: PUT slaat encrypted secrets op

**Given** een geauthenticeerde admin van de getklai tenant
**When** `PUT /api/orgs/{org_id}/mcp-servers/twenty-crm` wordt aangeroepen met:
  ```json
  {"enabled": true, "env": {"TWENTY_API_KEY": "sk-test-123", "TWENTY_BASE_URL": "https://crm.example.com"}}
  ```
**Then** wordt `TWENTY_API_KEY` encrypted opgeslagen in de database
**And** wordt `TWENTY_BASE_URL` plaintext opgeslagen
**And** is de response status 200
**And** bevat de response `restart_required: true`

### AC-M4-03: POST test valideert connectiviteit

**Given** een tenant met geconfigureerde `twenty-crm` MCP server
**When** `POST /api/orgs/{org_id}/mcp-servers/twenty-crm/test` wordt aangeroepen
**Then** stuurt het systeem een JSON-RPC `initialize` request naar de MCP server URL
**And** retourneert `status: "ok"` met `response_time_ms` bij succesvolle verbinding
**And** retourneert `status: "error"` met foutmelding bij falen

### AC-M4-04: PUT met onbekend server_id retourneert 404

**Given** een geauthenticeerde admin
**When** `PUT /api/orgs/{org_id}/mcp-servers/onbekend-server` wordt aangeroepen
**Then** is de response status 404
**And** bevat de body een foutmelding dat het server ID niet in de catalog voorkomt

### AC-M4-05: PUT met ontbrekende required env vars retourneert 422

**Given** de catalog vereist `TWENTY_API_KEY` en `TWENTY_BASE_URL` voor `twenty-crm`
**When** `PUT /api/orgs/{org_id}/mcp-servers/twenty-crm` wordt aangeroepen met:
  ```json
  {"enabled": true, "env": {"TWENTY_BASE_URL": "https://crm.example.com"}}
  ```
**Then** is de response status 422
**And** bevat de body een lijst van ontbrekende variabelen: `["TWENTY_API_KEY"]`

### AC-M4-06: IDOR bescherming -- cross-tenant toegang geblokkeerd

**Given** een geauthenticeerde admin van tenant A
**When** `GET /api/orgs/{tenant_b_org_id}/mcp-servers` wordt aangeroepen
**Then** is de response status 403 of 404
**And** worden GEEN gegevens van tenant B geretourneerd

---

## Module 5: Portal UI + i18n

### AC-M5-01: Integratiepagina toont MCP servers

**Given** een ingelogde admin navigeert naar `/admin/integrations`
**When** de pagina is geladen
**Then** worden alle MCP servers uit de catalog getoond als kaarten
**And** toont elke kaart: naam, beschrijving, activatiestatus (toggle), configuratievelden
**And** zijn secret velden (keys die `KEY`, `SECRET`, `TOKEN` bevatten) gemaskeerd (password type)

### AC-M5-02: Sidebar bevat Integraties link

**Given** een ingelogde admin
**When** de sidebar wordt gerenderd
**Then** bevat de admin sectie een menu-item "Integraties" (NL) of "Integrations" (EN)
**And** navigeert een klik naar `/admin/integrations`

### AC-M5-03: Formulier validatie voorkomt incompleet opslaan

**Given** een MCP server kaart met required velden `TWENTY_API_KEY` en `TWENTY_BASE_URL`
**When** de admin alleen `TWENTY_BASE_URL` invult en op opslaan klikt
**Then** is de opslaan-knop disabled
**And** toont het formulier een validatiefout bij het lege `TWENTY_API_KEY` veld

### AC-M5-04: Test verbinding knop toont resultaat

**Given** een volledig geconfigureerde `twenty-crm` MCP server
**When** de admin op "Test verbinding" klikt
**Then** toont de knop een loading state
**And** toont na voltooiing een success (groen) of error (rood) resultaat inline
**And** toont bij succes de response tijd in milliseconden

### AC-M5-05: i18n vertalingen beschikbaar

**Given** de gebruiker heeft de taal op Nederlands ingesteld
**When** de integratiepagina wordt geladen
**Then** zijn alle labels, knoppen en foutmeldingen in het Nederlands
**And** zijn dezelfde elementen beschikbaar in het Engels wanneer de taal wordt gewisseld

---

## Edge Cases

### EC-01: Lege API key bij activatie

**Given** een admin probeert een MCP server te activeren
**When** het `TWENTY_API_KEY` veld is leeg of bevat alleen whitespace
**Then** weigert de API de request met status 422
**And** toont de UI een validatiefout

### EC-02: Catalog ID niet gevonden in database

**Given** de MCP Catalog wordt bijgewerkt en een server ID wordt verwijderd
**And** een tenant heeft dit ID nog in `mcp_servers`
**When** provisioning draait voor die tenant
**Then** wordt het verwijderde ID genegeerd met een warning log
**And** worden alle andere servers normaal geconfigureerd
**And** crasht het systeem NIET

### EC-03: Container restart faalt

**Given** `_flush_redis_and_restart_librechat()` wordt aangeroepen
**When** de Docker daemon onbereikbaar is of de container niet bestaat
**Then** wordt een error gelogd met container naam en foutmelding
**And** wordt de fout doorgestuurd naar de API caller
**And** blijft de database-status consistent (de configuratie is opgeslagen, maar restart is gefaald)

### EC-04: Redis FLUSHALL impact op actieve sessies

**Given** een tenant heeft actieve chat-sessies in LibreChat
**When** `FLUSHALL` wordt uitgevoerd als onderdeel van MCP-configuratiewijziging
**Then** verliezen actieve sessies hun cached state
**And** moeten gebruikers hun pagina herladen
**And** gaat GEEN chatgeschiedenis verloren (opgeslagen in MongoDB, niet Redis)

### EC-05: Gelijktijdige PUT requests

**Given** twee admins van dezelfde tenant sturen tegelijkertijd een PUT request
**When** beide requests de `mcp_servers` JSON willen bijwerken
**Then** wint de laatste schrijver (last-write-wins)
**And** is de database-status consistent (geen partieel overschreven JSON)

### EC-06: Ongeldig MCP server URL formaat

**Given** een admin configureert `TWENTY_BASE_URL` als `not-a-valid-url`
**When** `POST .../test` wordt aangeroepen
**Then** retourneert de API `status: "error"` met een duidelijke foutmelding
**And** crashed het systeem NIET

---

## Performancecriteria

| Criterium | Drempelwaarde |
|-----------|---------------|
| GET `/mcp-servers` response tijd | < 500ms |
| PUT `/mcp-servers/{id}` verwerking (exclusief container restart) | < 2s |
| POST `/mcp-servers/{id}/test` timeout | max 10s |
| Container restart na flush | < 30s tot healthy |
| Encrypt/decrypt round-trip | < 50ms |
| UI integratiepagina laden (inclusief API call) | < 2s |

---

## Beveiligingscriteria

### SEC-01: Secrets nooit in logs

**Verificatie:** Grep alle log output na een volledige flow (activate, test, restart) op bekende test-API-keys. Resultaat moet nul matches zijn.

### SEC-02: Secrets encrypted in database

**Verificatie:** Query `SELECT mcp_servers FROM portal_orgs WHERE slug='getklai'`. De waarde van `TWENTY_API_KEY` moet NIET in plaintext leesbaar zijn.

### SEC-03: API endpoint IDOR bescherming

**Verificatie:** Authenticeer als tenant A, probeer endpoints van tenant B aan te roepen. Alle requests moeten 403 of 404 retourneren.

### SEC-04: Secret velden niet pre-filled in UI

**Verificatie:** Laad de integratiepagina voor een geconfigureerde server. Het `TWENTY_API_KEY` veld moet leeg zijn (niet pre-filled met de opgeslagen waarde). Alleen `configured_env_vars` (als key-lijst) mag getoond worden.

### SEC-05: Geen secrets in git-getrackte bestanden

**Verificatie:** `grep -r "sk-" deploy/librechat/mcp_catalog.yaml` moet nul resultaten opleveren. Alleen `${VAR}` placeholders zijn toegestaan.

---

## Definition of Done

- [ ] Alle AC scenarios hierboven slagen
- [ ] Alle SEC verificaties zijn uitgevoerd en geslaagd
- [ ] Alle performancecriteria zijn gehaald
- [ ] Unit tests voor secrets helpers (encrypt/decrypt) bestaan en slagen
- [ ] Alembic migratie is getest (upgrade + downgrade)
- [ ] Portal API endpoints zijn functioneel getest
- [ ] Portal UI is visueel geverifieerd in browser
- [ ] i18n vertalingen zijn aanwezig voor NL en EN
- [ ] Geen ruff of pyright warnings in gewijzigde bestanden
- [ ] Geen ESLint warnings in gewijzigde frontend bestanden
- [ ] Code review op secret handling door tweede paar ogen
