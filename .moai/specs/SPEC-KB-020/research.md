# SPEC-KB-020 Research: Secure Connector Credential Storage

## Huidige stand van zaken

### Probleem: plaintext credentials in PostgreSQL

Connector configuratie (OAuth tokens, API keys, installation tokens) wordt opgeslagen als plaintext JSONB in `portal_connectors.config`. Dit betekent:

- Een database dump bevat alle tokens in leesbare vorm
- Backups bevatten dezelfde plaintext data
- Log statements die `config` loggen kunnen tokens lekken
- GET responses op de connectors API geven tokens terug aan de frontend

### Betreffende connector types en gevoelige velden

| Connector type | Gevoelige velden |
|---|---|
| `github` | `access_token`, `installation_token`, `app_private_key` |
| `notion` | `api_token` |
| `google_drive` | `oauth_token`, `refresh_token`, `access_token` |
| `ms_docs` | `oauth_token`, `refresh_token`, `access_token` |
| `web_crawler` | `auth_headers` (optioneel dict met secret values) |

### Huidige credential flow

1. `POST /api/app/knowledge-bases/{kb_slug}/connectors` ontvangt `config: dict` plaintext
2. Opgeslagen in `portal_connectors.config` JSONB kolom zonder encryptie
3. `GET /internal/connectors/{connector_id}` retourneert `config` plaintext naar klai-connector
4. `ConnectorOut` schema retourneert `config: dict` -- tokens zichtbaar in GET responses

---

## Referentie-implementaties gevonden

### 1. AESGCMCipher (`app/core/security.py`)

Bestaande AES-256-GCM implementatie:
- Gebruikt `os.urandom(12)` als nonce per encryptie-operatie
- Ciphertext formaat: `nonce(12 bytes) || ciphertext`
- Accepteert 32-byte key als `bytes`
- Methoden: `encrypt(plaintext: str) -> bytes`, `decrypt(ciphertext: bytes) -> str`

### 2. PortalSecretsService (`app/services/secrets.py`)

Wrapper rond `AESGCMCipher`:
- Gebruikt `PORTAL_SECRETS_KEY` env var (64-char hex = 32 bytes)
- Singleton instantie: `portal_secrets`
- Base64 encoding via `encrypt_mcp_secret()` / `decrypt_mcp_secret()` voor JSON-compatible opslag
- Reeds in productie voor MCP secrets

### 3. `_SECRET_MARKERS` patroon

`is_secret_var()` functie gebruikt keyword-matching (`KEY`, `SECRET`, `TOKEN`, `PASSWORD`) om gevoelige variabelen te detecteren. Dit patroon kan als inspiratie dienen voor `SENSITIVE_FIELDS` mapping.

---

## Belangrijkste risico's

### R1: Key loss = permanent dataverlies
Als de `ENCRYPTION_KEY` (KEK) verloren gaat zonder backup, zijn alle connector credentials onherstelbaar verloren. Vereist: key backup procedure + documentatie.

### R2: Migratie van bestaande plaintext data
Bestaande connectors hebben plaintext config. De migratie moet:
- Backward-compatible zijn (dual-read: encrypted_config preferred, config als fallback)
- Rollback ondersteunen (encrypted_config nullable)
- Zero-downtime uitvoerbaar zijn

### R3: Performance overhead
AES-256-GCM encryptie/decryptie per connector sync. Verwachte overhead < 1ms per operatie (referentie: `cryptography` library benchmarks), maar moet gevalideerd worden.

### R4: Key separation
`ENCRYPTION_KEY` (voor connector DEKs) moet strikt gescheiden zijn van `PORTAL_SECRETS_KEY` (voor MCP secrets). Hergebruik zou blast radius vergroten.

### R5: Log leakage
Zonder `SecretStr` en structlog processors kunnen credentials in logs terechtkomen via Pydantic model repr, FastAPI request logging, of SQLAlchemy echo mode.

---

## Architectuuraanbeveling

### KEK-DEK hiearchie (gekozen aanpak)

```
ENCRYPTION_KEY (env var, 32 bytes)
    |
    +-- per-tenant DEK (32 bytes, encrypted met KEK)
            |
            +-- connector credentials (encrypted met DEK)
```

**Voordelen:**
- KEK rotation vereist alleen re-encryptie van DEKs (niet alle credentials)
- Tenant isolatie: compromised DEK raakt alleen 1 tenant
- Hergebruikt bestaande `AESGCMCipher` zonder aanpassingen

**Alternatieven overwogen en afgewezen:**
1. **Enkele key voor alles**: geen tenant isolatie, rotation vereist re-encryptie van alle data
2. **Vault/KMS integratie**: te complex voor huidige fase, optionele toekomstige extensie
3. **Application-level encryption via pgcrypto**: key management in SQL is fragiel en moeilijk te auditen

### Service design

Nieuwe `ConnectorCredentialStore` in `app/services/connector_credentials.py`:
- `SENSITIVE_FIELDS` mapping per connector type
- Encrypt/decrypt met tenant DEK
- Lazy DEK provisioning voor bestaande orgs
- KEK rotation support

### Geen wijzigingen nodig in klai-connector

Het interne endpoint (`GET /internal/connectors/{connector_id}`) decrypt credentials en merged ze in het `config` dict. klai-connector ontvangt hetzelfde plaintext formaat als vandaag.
