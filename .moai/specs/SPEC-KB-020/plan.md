# SPEC-KB-020 Plan: Secure Connector Credential Storage

## Overzicht

Implementatie van een KEK-DEK encryptie-hierarchie voor connector credentials, met zero-downtime migratie van bestaande plaintext data en zero-knowledge logging.

---

## Taakdecompositie

### Milestone 1: Schema & Crypto Service (Prioriteit Hoog)

**Doel**: Database schema uitbreiden en `ConnectorCredentialStore` service bouwen.

#### Taak 1.1: Alembic schema-migratie
- **Bestand**: `klai-portal/backend/alembic/versions/xxxx_add_encrypted_config.py`
- **Actie**: Nieuwe migratie die `encrypted_config JSONB DEFAULT '{}'` toevoegt aan `portal_connectors` en `connector_dek_enc BYTEA` toevoegt aan `portal_orgs`
- **Afhankelijkheden**: Geen
- **Risico**: Laag -- additieve kolommen, geen data-wijziging

#### Taak 1.2: SQLAlchemy model updates
- **Bestand**: `klai-portal/backend/app/models/connectors.py`
- **Actie**: `encrypted_config: Mapped[dict | None] = mapped_column(JSONB, nullable=True, default=dict)` toevoegen aan `PortalConnector`
- **Bestand**: `klai-portal/backend/app/models/orgs.py` (of equivalent)
- **Actie**: `connector_dek_enc: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)` toevoegen aan het org model
- **Afhankelijkheden**: Taak 1.1

#### Taak 1.3: ConnectorCredentialStore service
- **Bestand (nieuw)**: `klai-portal/backend/app/services/connector_credentials.py`
- **Actie**: Implementeer `ConnectorCredentialStore` met:
  - `SENSITIVE_FIELDS` mapping (dict per connector type)
  - `encrypt_credentials(org_id, connector_type, config, db) -> dict`
  - `decrypt_credentials(org_id, encrypted_config, db) -> dict`
  - `get_or_create_dek(org_id, db) -> bytes`
  - `rotate_kek(old_kek, new_kek, db) -> None`
- **Referentie**: `PortalSecretsService` in `app/services/secrets.py` als patroon
- **Afhankelijkheden**: Taak 1.2

#### Taak 1.4: ENCRYPTION_KEY configuratie
- **Bestand**: `klai-portal/backend/app/core/config.py`
- **Actie**: `encryption_key: str` toevoegen aan Settings met validatie (64-char hex)
- **Actie**: Startup validatie dat de key aanwezig en geldig is
- **Afhankelijkheden**: Geen

---

### Milestone 2: API-laag aanpassingen (Prioriteit Hoog)

**Doel**: Encryptie integreren in connector CRUD endpoints.

#### Taak 2.1: POST/PATCH connector endpoints
- **Bestand**: `klai-portal/backend/app/api/app/connectors.py` (of equivalent)
- **Actie**: Na validatie, `ConnectorCredentialStore.encrypt_credentials()` aanroepen. Gevoelige velden verwijderen uit `config`, resultaat opslaan in `encrypted_config`.
- **Afhankelijkheden**: Milestone 1

#### Taak 2.2: GET connector endpoints (publiek)
- **Bestand**: `klai-portal/backend/app/api/app/connectors.py`
- **Actie**: Response schema aanpassen zodat gevoelige velden gemaskeerd worden met `"***"`. Nooit plaintext credentials in publieke responses.
- **Afhankelijkheden**: Milestone 1

#### Taak 2.3: GET internal connector endpoint
- **Bestand**: `klai-portal/backend/app/api/internal.py` (regels 120-156)
- **Actie**: `ConnectorCredentialStore.decrypt_credentials()` aanroepen en resultaat mergen in `config` dict voordat het naar klai-connector wordt gestuurd.
- **Afhankelijkheden**: Milestone 1

#### Taak 2.4: Pydantic schema updates
- **Bestand**: `klai-portal/backend/app/schemas/connectors.py`
- **Actie**:
  - `ConnectorCreateRequest`: credential velden als `SecretStr`
  - `ConnectorOut`: gevoelige velden retourneren als gemaskeerde strings
  - `ConnectorInternalOut`: volledig config dict (voor intern gebruik)
- **Afhankelijkheden**: Taak 2.1

---

### Milestone 3: Zero-Knowledge Logging (Prioriteit Hoog)

**Doel**: Voorkomen dat credentials in log output verschijnen.

#### Taak 3.1: SecretStr in request schemas
- **Bestand**: `klai-portal/backend/app/schemas/connectors.py`
- **Actie**: Alle credential-velden in request models wijzigen naar `SecretStr`
- **Afhankelijkheden**: Geen

#### Taak 3.2: Structlog SecretStr processor
- **Bestand**: `klai-portal/backend/app/logging_setup.py`
- **Actie**: Processor toevoegen die `SecretStr` instanties in log kwargs vervangt door `"***"`
- **Afhankelijkheden**: Geen

#### Taak 3.3: Log audit
- **Actie**: Grep door alle connector-gerelateerde code om te verifier dat geen credential-waarden als kwargs worden gelogd. Alleen metadata (connector_id, org_id, connector_type).
- **Afhankelijkheden**: Taak 3.1, 3.2

---

### Milestone 4: Data-migratie (Prioriteit Hoog)

**Doel**: Bestaande plaintext credentials retroactief versleutelen.

#### Taak 4.1: Data-migratiescript
- **Bestand (nieuw)**: `klai-portal/backend/scripts/migrate_connector_credentials.py`
- **Actie**:
  1. Voor elke organisatie: genereer DEK, versleutel met KEK, sla op in `connector_dek_enc`
  2. Voor elke connector: extraheer gevoelige velden uit `config`, versleutel met DEK, sla op in `encrypted_config`
  3. Verwijder gevoelige velden NIET uit `config` (backward compat tot bevestiging)
- **Afhankelijkheden**: Milestone 1 schema-migratie moet eerst gedraaid zijn

#### Taak 4.2: Migratie verificatie
- **Actie**: Script dat voor elke connector verifieert:
  - `encrypted_config` bevat verwachte velden
  - Decryptie retourneert originele waarden
  - Fallback naar `config` werkt als `encrypted_config` null is
- **Afhankelijkheden**: Taak 4.1

#### Taak 4.3: Cleanup migratie (na bevestiging)
- **Bestand**: Alembic migratie die gevoelige velden uit `config` verwijdert
- **Timing**: Pas uitvoeren nadat alle connectors succesvol gemigreerd zijn
- **Rollback**: Niet automatisch -- vereist handmatige bevestiging
- **Afhankelijkheden**: Taak 4.2 succesvol

---

### Milestone 5: Tests (Prioriteit Hoog)

**Doel**: 100% coverage op crypto en credential store code.

#### Taak 5.1: Unit tests ConnectorCredentialStore
- **Bestand (nieuw)**: `klai-portal/backend/tests/test_connector_credentials.py`
- **Scenarios**:
  - Encrypt/decrypt round-trip per connector type
  - SENSITIVE_FIELDS mapping compleet voor alle types
  - DEK generatie en caching
  - Fallback bij ontbrekende `encrypted_config`
  - Ongeldige key handling

#### Taak 5.2: Unit tests AESGCMCipher
- **Bestand**: `klai-portal/backend/tests/test_security.py` (uitbreiden)
- **Scenarios**:
  - Round-trip encryptie/decryptie
  - Verschillende plaintext lengtes
  - Tampered ciphertext detectie
  - Verkeerde key afwijzing

#### Taak 5.3: API integration tests
- **Bestand (nieuw)**: `klai-portal/backend/tests/test_connector_encryption_api.py`
- **Scenarios**:
  - POST connector: credentials versleuteld in DB
  - GET connector (publiek): credentials gemaskeerd
  - GET connector (intern): credentials gedecrypt
  - PATCH connector: encrypted_config bijgewerkt

#### Taak 5.4: Log masking tests
- **Bestand (nieuw)**: `klai-portal/backend/tests/test_log_masking.py`
- **Scenarios**:
  - SecretStr niet zichtbaar in structlog output
  - SecretStr niet zichtbaar in Pydantic model repr

---

## Technologiekeuzes

| Keuze | Rationale |
|---|---|
| AES-256-GCM via bestaande `AESGCMCipher` | Bewezen in productie, geen nieuwe crypto dependencies |
| KEK-DEK hiearchie | KEK rotation zonder credential re-encryptie, tenant isolatie |
| `ENCRYPTION_KEY` als aparte env var | Blast radius beperking: compromised key raakt niet MCP secrets |
| Base64 encoding in JSONB | JSON-compatible opslag, consistent met `encrypt_mcp_secret` patroon |
| Pydantic `SecretStr` | Voorkomt accidentele logging/serialisatie van credentials |
| Structlog processor | Uniforme masking over alle log pipelines |

---

## Risicoanalyse

### R1: Key verlies
- **Impact**: Alle connector credentials onherstelbaar verloren
- **Mitigatie**: Documenteer key backup procedure in deploy runbook. Overweeg key escrow in een aparte beveiligde locatie.

### R2: Migratie faalt halverwege
- **Impact**: Sommige connectors versleuteld, andere niet
- **Mitigatie**: Dual-read strategie (REQ-STORE-004). Migratiescript is idempotent -- kan opnieuw gedraaid worden. Logging per connector voor voortgang.

### R3: Backward compatibiliteit
- **Impact**: Bestaande connectors stoppen met werken na deploy
- **Mitigatie**: Fallback naar plaintext `config` als `encrypted_config` leeg is. klai-connector ontvangt exact hetzelfde formaat.

### R4: Performance overhead
- **Impact**: Vertraging bij connector sync
- **Mitigatie**: AES-256-GCM overhead < 1ms per operatie. DEK caching in memory per request voorkomt herhaalde DB lookups.

### R5: Rollback scenario
- **Impact**: Encryptie moet ongedaan gemaakt worden
- **Mitigatie**: `encrypted_config` op null zetten activeert fallback. Originele `config` data blijft intact tot cleanup migratie.

---

## Implementatievolgorde

```
Taak 1.4 (config)
    |
Taak 1.1 (schema migratie) -> Taak 1.2 (model updates) -> Taak 1.3 (service)
    |                                                           |
    +--- Taak 3.1, 3.2 (logging, parallel)                     |
                                                                |
                                        Taak 2.1, 2.2, 2.3, 2.4 (API)
                                                                |
                                        Taak 5.1, 5.2, 5.3, 5.4 (tests)
                                                                |
                                        Taak 4.1 (data migratie)
                                                                |
                                        Taak 4.2 (verificatie)
                                                                |
                                        Taak 4.3 (cleanup, later)
```

---

## Bestanden overzicht

### Nieuw te creeren

| Bestand | Beschrijving |
|---|---|
| `app/services/connector_credentials.py` | `ConnectorCredentialStore` service |
| `alembic/versions/xxxx_add_encrypted_config.py` | Schema-migratie |
| `scripts/migrate_connector_credentials.py` | Data-migratiescript |
| `tests/test_connector_credentials.py` | Unit tests credential store |
| `tests/test_connector_encryption_api.py` | API integration tests |
| `tests/test_log_masking.py` | Log masking tests |

### Te wijzigen

| Bestand | Wijziging |
|---|---|
| `app/core/config.py` | `encryption_key` setting toevoegen |
| `app/models/connectors.py` | `encrypted_config` kolom toevoegen |
| `app/models/orgs.py` | `connector_dek_enc` kolom toevoegen |
| `app/schemas/connectors.py` | `SecretStr` velden, masking in responses |
| `app/api/app/connectors.py` | Encrypt bij POST/PATCH, mask bij GET |
| `app/api/internal.py` | Decrypt bij intern GET endpoint |
| `app/logging_setup.py` | SecretStr processor toevoegen |
| `tests/test_security.py` | AESGCMCipher tests uitbreiden |

---

## @MX Tag strategie

Nieuwe code krijgt de volgende tags:

- `@MX:ANCHOR` op `ConnectorCredentialStore` klasse (fan_in >= 3: API endpoints, migratie, tests)
- `@MX:ANCHOR` op `SENSITIVE_FIELDS` mapping (contract: wijzigingen raken alle connector types)
- `@MX:WARN` op `rotate_kek()` (gevaarlijke operatie: verkeerde uitvoering = data loss)
- `@MX:NOTE` op fallback logica in decrypt (tijdelijk: verwijderen na cleanup migratie)
