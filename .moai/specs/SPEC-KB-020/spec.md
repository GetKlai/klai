---
id: SPEC-KB-020
version: "1.0"
status: draft
created: "2026-04-04"
updated: "2026-04-04"
author: Mark Vletter
priority: critical
tags: [security, encryption, connectors, credentials]
---

## HISTORY

| Versie | Datum | Auteur | Wijziging |
|---|---|---|---|
| 1.0 | 2026-04-04 | Mark Vletter | Initiele SPEC |

---

# SPEC-KB-020: Secure Connector Credential Storage

## 1. Environment

### Systeemcontext

- **Platform**: Klai portal-api (FastAPI, Python 3.12, SQLAlchemy async, PostgreSQL)
- **Bestaande crypto**: `AESGCMCipher` in `app/core/security.py` (AES-256-GCM, nonce(12b) || ciphertext)
- **Referentie-implementatie**: `PortalSecretsService` in `app/services/secrets.py` met `PORTAL_SECRETS_KEY`
- **Connector service**: klai-connector leest credentials via intern endpoint `GET /internal/connectors/{connector_id}`
- **Logging**: structlog met JSON output naar VictoriaLogs via Alloy
- **Dependencies**: `cryptography>=43.0` (reeds geinstalleerd), Pydantic v2 met `SecretStr`

### Scope

- **In scope**: Encryptie van connector credentials in PostgreSQL, API masking, zero-knowledge logging, migratie van bestaande plaintext data, KEK rotation
- **Buiten scope**: Vault/KMS integratie (toekomstige extensie), encryptie van niet-connector data, wijzigingen aan klai-connector service

---

## 2. Assumptions

| # | Aanname | Vertrouwen | Risico bij onjuistheid |
|---|---|---|---|
| A1 | `ENCRYPTION_KEY` env var wordt veilig beheerd op de server (apart van `PORTAL_SECRETS_KEY`) | Hoog | Key compromise = alle credentials leesbaar |
| A2 | `AESGCMCipher` is correct geimplementeerd en productie-bewezen via `PortalSecretsService` | Hoog | Crypto bugs = data onleesbaar of onveilig |
| A3 | Alle connector types hebben een bekende, eindige set gevoelige velden | Hoog | Onbekende velden worden niet versleuteld |
| A4 | klai-connector verwacht plaintext `config` dict en mag niet gewijzigd worden | Hoog | Breaking change in connector service |
| A5 | Database migraties draaien single-threaded via Alembic | Medium | Race conditions bij concurrent migration |
| A6 | Structlog processors worden uitgevoerd voor alle log output | Hoog | Credentials lekken in logs |

---

## 3. Requirements

### Module 1: Crypto Layer

#### REQ-CRYPTO-001 (Ubiquitous)
Het systeem **SHALL** alle cryptografische operaties voor connector credentials uitvoeren via de bestaande `AESGCMCipher` klasse met AES-256-GCM encryptie.

#### REQ-CRYPTO-002 (Event-driven)
**WHEN** een nieuwe tenant (organisatie) wordt aangemaakt of **WHEN** de eerste connector voor een bestaande tenant wordt gecreeerd, **THEN** genereert het systeem een unieke DEK (Data Encryption Key) van 32 random bytes via `os.urandom(32)`, versleutelt deze met de KEK via `AESGCMCipher`, en slaat het resultaat op als `connector_dek_enc BYTEA` in `portal_orgs`.

#### REQ-CRYPTO-003 (State-driven)
**IF** de `ENCRYPTION_KEY` env var niet geconfigureerd is of geen geldige 64-character hex string is, **THEN** weigert het systeem op te starten en logt een fatale fout met een duidelijke foutmelding.

#### REQ-CRYPTO-004 (Unwanted)
Het systeem **SHALL NOT** de `ENCRYPTION_KEY` (KEK) direct gebruiken om connector credentials te versleutelen. Credentials worden uitsluitend versleuteld met de tenant-specifieke DEK.

#### REQ-CRYPTO-005 (Optional)
**WHERE** een Key Management Service (Vault, AWS KMS) beschikbaar is, **SHALL** het systeem de KEK-opslag kunnen delegeren naar die service in plaats van een env var.

---

### Module 2: Credential Storage

#### REQ-STORE-001 (Ubiquitous)
Het systeem **SHALL** een `SENSITIVE_FIELDS` mapping onderhouden die per connector type definieert welke config-keys credentials zijn:
- `github`: `access_token`, `installation_token`, `app_private_key`
- `notion`: `api_token`
- `google_drive`: `oauth_token`, `refresh_token`, `access_token`
- `ms_docs`: `oauth_token`, `refresh_token`, `access_token`
- `web_crawler`: `auth_headers`

#### REQ-STORE-002 (Event-driven)
**WHEN** connector credentials worden opgeslagen, **THEN** extraheert het systeem gevoelige velden uit `config` op basis van `SENSITIVE_FIELDS`, serialiseert ze als JSON, versleutelt met de tenant DEK via `AESGCMCipher`, en slaat de ruwe binaire blob (nonce + ciphertext) op in `encrypted_credentials BYTEA` op `portal_connectors`. Er vindt geen base64-encoding plaats — de blob wordt direct als bytes opgeslagen.

#### REQ-STORE-003 (Event-driven)
**WHEN** connector credentials worden opgehaald voor intern gebruik, **THEN** leest het systeem de `encrypted_credentials BYTEA` blob, decrypteert met de tenant DEK, deserialiseert de JSON, en merged het resultaat terug in het `config` dict.

#### REQ-STORE-004 (State-driven)
**IF** een connector geen `encrypted_credentials` heeft (legacy data, `IS NULL`), **THEN** leest het systeem credentials uit het plaintext `config` veld als fallback.

#### REQ-STORE-005 (Unwanted)
Het systeem **SHALL NOT** gevoelige velden uit `SENSITIVE_FIELDS` opslaan in het plaintext `config` veld nadat encryptie is uitgevoerd. Gevoelige velden worden verwijderd uit `config` en uitsluitend opgeslagen in `encrypted_credentials`.

---

### Module 3: API Behavior

#### REQ-API-001 (Event-driven)
**WHEN** een connector wordt aangemaakt via `POST /api/app/knowledge-bases/{kb_slug}/connectors`, **THEN** extraheert het systeem gevoelige velden, versleutelt ze, slaat ze op in `encrypted_credentials`, en verwijdert ze uit `config`.

#### REQ-API-002 (Event-driven)
**WHEN** een connector wordt gewijzigd via `PATCH`, **THEN** versleutelt het systeem eventueel bijgewerkte gevoelige velden en schrijft de nieuwe blob naar `encrypted_credentials`.

#### REQ-API-003 (Event-driven)
**WHEN** een publiek GET-endpoint connector data retourneert, **THEN** maskeert het systeem alle gevoelige velden met `"***"` in de response. Plaintext credentials worden nooit aan de frontend geretourneerd.

#### REQ-API-004 (Event-driven)
**WHEN** het interne endpoint `GET /internal/connectors/{connector_id}` wordt aangeroepen, **THEN** decrypteert het systeem de credentials en merged ze in het `config` dict, zodat klai-connector hetzelfde plaintext formaat ontvangt als voor de encryptie.

#### REQ-API-005 (Unwanted)
Het systeem **SHALL NOT** plaintext credentials retourneren in enig publiek API-endpoint, inclusief list endpoints, detail endpoints, en error responses.

---

### Module 4: Zero-Knowledge Logging

#### REQ-LOG-001 (Ubiquitous)
Het systeem **SHALL** Pydantic `SecretStr` gebruiken voor alle credential-velden in request- en response-schemas. `SecretStr` voorkomt dat waarden verschijnen in `repr()`, `str()`, en JSON serialisatie.

#### REQ-LOG-002 (Ubiquitous)
Het systeem **SHALL** een structlog processor registreren die `SecretStr` waarden maskeert naar `"***"` voordat log output wordt geschreven.

#### REQ-LOG-003 (Unwanted)
Het systeem **SHALL NOT** credential-waarden opnemen als kwargs in log statements. Alleen metadata (connector_id, org_id, connector_type) is toegestaan in log kwargs.

#### REQ-LOG-004 (State-driven)
**IF** SQLAlchemy echo mode actief is (development), **THEN** worden credential-waarden in query parameters gemaskeerd via een custom log filter.

---

### Module 5: Migration & Rotation

#### REQ-MIG-001 (Event-driven)
**WHEN** de Alembic schema-migratie draait, **THEN** voegt het systeem `encrypted_credentials BYTEA` toe aan `portal_connectors` en `connector_dek_enc BYTEA` toe aan `portal_orgs`, zonder bestaande data te wijzigen.

#### REQ-MIG-002 (Event-driven)
**WHEN** het data-migratiescript draait, **THEN** genereert het voor elke bestaande organisatie een DEK (versleuteld met KEK) en versleutelt het voor elke bestaande connector de gevoelige velden uit `config` naar `encrypted_credentials`. Het script **SHALL** idempotent zijn: organisaties waarbij `connector_dek_enc IS NOT NULL` worden overgeslagen, connectors waarbij `encrypted_credentials IS NOT NULL` worden overgeslagen. Herhaald uitvoeren heeft geen effect.

#### REQ-MIG-003 (State-driven)
**IF** de applicatie gedeployed is met de nieuwe code maar de data-migratie nog niet is uitgevoerd, **THEN** functioneert het systeem correct via de fallback naar plaintext `config` (REQ-STORE-004).

#### REQ-MIG-004 (Event-driven)
**WHEN** een KEK-rotatie wordt uitgevoerd, **THEN** decrypteert het systeem alle DEKs in `portal_orgs` met de oude KEK en re-encrypteert ze met de nieuwe KEK. Connector credentials hoeven niet opnieuw versleuteld te worden.

#### REQ-MIG-005 (Event-driven)
**WHEN** een rollback nodig is, **THEN** wordt `encrypted_credentials` op null gezet en valt de applicatie terug op plaintext `config` (REQ-STORE-004). De originele plaintext data in `config` wordt pas verwijderd na bevestigde succesvolle migratie.

---

## 4. Specifications

### Datamodel wijzigingen

**`portal_connectors` tabel:**
- Nieuw: `encrypted_credentials BYTEA` (nullable; `IS NULL` = legacy plaintext, `IS NOT NULL` = encrypted)
- Bestaand: `config JSONB` blijft voor niet-gevoelige configuratie (repo URLs, schedule, etc.)

**`portal_orgs` tabel:**
- Nieuw: `connector_dek_enc BYTEA` (nullable, gevuld bij eerste connector of migratie)

### Service architectuur

**Nieuw bestand: `app/services/connector_credentials.py`**
- Klasse: `ConnectorCredentialStore`
- Methoden:
  - `encrypt_credentials(org_id, connector_type, config) -> bytes` -- retourneert encrypted_credentials blob (BYTEA)
  - `decrypt_credentials(org_id, encrypted_credentials) -> dict` -- retourneert plaintext dict
  - `get_or_create_dek(org_id, db) -> bytes` -- lazy DEK provisioning
  - `rotate_kek(old_kek, new_kek, db) -> None` -- re-encrypt alle DEKs
- Constante: `SENSITIVE_FIELDS: dict[str, list[str]]`

### Key hiearchie

```
ENCRYPTION_KEY (env var, 64-char hex = 32 bytes)
    |
    +-- AESGCMCipher(KEK).encrypt(DEK) -> connector_dek_enc (BYTEA, per tenant)
            |
            +-- AESGCMCipher(DEK).encrypt(credentials_json) -> nonce+ciphertext in encrypted_credentials (BYTEA)
```

### Threat model

| Dreigingsscenario | Bescherming | Residueel risico |
|---|---|---|
| Database dump gelekt | Credentials versleuteld met DEK; DEK versleuteld met KEK | Geen (zonder KEK onleesbaar) |
| Backup gelekt zonder `ENCRYPTION_KEY` | Zelfde bescherming als DB dump | Geen |
| Log stream blootgesteld | SecretStr masking + structlog processor | Geen credential-waarden in logs |
| GET response onderschept | Masking met `"***"` op publieke endpoints | Geen tokens zichtbaar |
| App server compromised met memory access | Aanvaller kan `decrypt_credentials` aanroepen | **Buiten scope** -- vereist Vault/KMS |
| Insider met KEK toegang | Kan alle credentials ontsleutelen | **Buiten scope** -- vereist HSM/split keys |

### Traceability

| Requirement | Implementatie | Test |
|---|---|---|
| REQ-CRYPTO-001 | `ConnectorCredentialStore` -> `AESGCMCipher` | `test_credential_store.py` |
| REQ-CRYPTO-002 | `get_or_create_dek()` | `test_dek_lifecycle.py` |
| REQ-STORE-001 | `SENSITIVE_FIELDS` constant | `test_sensitive_fields_mapping.py` |
| REQ-STORE-002 | `encrypt_credentials()` → BYTEA | `test_encrypt_decrypt.py` |
| REQ-API-003 | `ConnectorOut` schema masking | `test_connector_api_masking.py` |
| REQ-LOG-001 | `SecretStr` in schemas | `test_secretstr_masking.py` |
| REQ-MIG-001 | Alembic migration | `test_migration_schema.py` |
| REQ-MIG-004 | `rotate_kek()` | `test_kek_rotation.py` |
