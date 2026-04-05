# SPEC-KB-020 Acceptance Criteria: Secure Connector Credential Storage

---

## Module 1: Crypto Layer

### AC-CRYPTO-001: AESGCMCipher round-trip
```gherkin
Given een AESGCMCipher instantie met een 32-byte key
When ik een plaintext string versleutel en het resultaat ontsleutel
Then is de ontsleutelde string identiek aan de originele plaintext
```

### AC-CRYPTO-002: DEK generatie bij eerste connector
```gherkin
Given een organisatie zonder connector_dek_enc
When de eerste connector voor die organisatie wordt aangemaakt
Then wordt een DEK van 32 bytes gegenereerd
And wordt de DEK versleuteld met de KEK opgeslagen in portal_orgs.connector_dek_enc
And is de opgeslagen DEK niet gelijk aan de plaintext DEK (is versleuteld)
```

### AC-CRYPTO-003: DEK hergebruik bij tweede connector
```gherkin
Given een organisatie met een bestaande connector_dek_enc
When een tweede connector voor die organisatie wordt aangemaakt
Then wordt dezelfde DEK hergebruikt (geen nieuwe DEK gegenereerd)
And worden de credentials van de tweede connector versleuteld met dezelfde DEK
```

### AC-CRYPTO-004: Ongeldige ENCRYPTION_KEY bij startup
```gherkin
Given een ENCRYPTION_KEY env var die geen geldige 64-character hex string is
When de applicatie opstart
Then faalt de startup met een duidelijke foutmelding
And worden geen requests geaccepteerd
```

### AC-CRYPTO-005: Tampered ciphertext detectie
```gherkin
Given een versleutelde credential blob
When een byte in de ciphertext wordt gewijzigd
Then gooit de decrypt operatie een InvalidTag exception
And worden de credentials niet geretourneerd
```

---

## Module 2: Credential Storage

### AC-STORE-001: Gevoelige velden mapping compleet
```gherkin
Given de SENSITIVE_FIELDS mapping
When ik alle gedefinieerde connector types controleer
Then bevat github: access_token, installation_token, app_private_key
And bevat notion: api_token
And bevat google_drive: oauth_token, refresh_token, access_token
And bevat ms_docs: oauth_token, refresh_token, access_token
And bevat web_crawler: auth_headers
```

### AC-STORE-002: Encryptie scheidt gevoelige van niet-gevoelige data
```gherkin
Given een github connector config met access_token en repo_url
When de credentials worden opgeslagen
Then bevat config alleen repo_url (niet access_token)
And bevat encrypted_config de versleutelde access_token
And is encrypted_config niet leesbaar als plaintext
```

### AC-STORE-003: Fallback naar plaintext config
```gherkin
Given een connector zonder encrypted_config (legacy data)
When de credentials worden opgehaald via het interne endpoint
Then worden de credentials gelezen uit het plaintext config veld
And werkt de connector normaal
```

### AC-STORE-004: Decrypt retourneert correcte velden
```gherkin
Given een connector met versleutelde credentials in encrypted_config
When de credentials worden gedecrypt
Then bevat het resultaat exact dezelfde key-value pairs als de originele gevoelige velden
And zijn de waarden byte-voor-byte identiek aan de originele plaintext
```

---

## Module 3: API Behavior

### AC-API-001: POST connector versleutelt credentials
```gherkin
Given een geauthenticeerde gebruiker
When ik POST /api/app/knowledge-bases/{kb_slug}/connectors met config: {access_token: "ghp_abc123", repo_url: "https://github.com/org/repo"}
Then wordt de connector aangemaakt met status 201
And bevat de database portal_connectors.config NIET het access_token
And bevat de database portal_connectors.encrypted_config een versleutelde blob
```

### AC-API-002: GET connector maskeert credentials
```gherkin
Given een connector met versleutelde credentials
When ik GET /api/app/knowledge-bases/{kb_slug}/connectors/{id}
Then bevat de response access_token: "***"
And bevat de response repo_url: "https://github.com/org/repo" (niet gemaskeerd)
And is het access_token NIET zichtbaar in de response body
```

### AC-API-003: Intern endpoint retourneert plaintext
```gherkin
Given een connector met versleutelde credentials
When klai-connector GET /internal/connectors/{connector_id} aanroept
Then bevat de response config.access_token de originele plaintext waarde
And bevat de response config.repo_url de originele waarde
And is het formaat identiek aan het formaat voor encryptie was ingevoerd
```

### AC-API-004: PATCH connector update versleutelde credentials
```gherkin
Given een connector met versleutelde credentials
When ik PATCH met een nieuwe access_token waarde
Then wordt de encrypted_config bijgewerkt met de nieuwe versleutelde waarde
And retourneert het interne endpoint de nieuwe plaintext waarde
```

---

## Module 4: Zero-Knowledge Logging

### AC-LOG-001: SecretStr in request logging
```gherkin
Given een POST request met access_token als SecretStr
When het request wordt gelogd door structlog
Then bevat de log output "***" in plaats van de token waarde
And is de originele token waarde NIET vindbaar in de log output
```

### AC-LOG-002: Structlog processor maskeert SecretStr
```gherkin
Given een log statement met een SecretStr als kwarg
When de structlog processor chain wordt uitgevoerd
Then wordt de SecretStr waarde vervangen door "***"
And blijven niet-gevoelige kwargs ongewijzigd
```

### AC-LOG-003: Pydantic model repr maskeert credentials
```gherkin
Given een ConnectorCreateRequest met access_token als SecretStr
When str() of repr() wordt aangeroepen op het model
Then bevat de output "**********" (Pydantic SecretStr default)
And is de originele token waarde NIET zichtbaar
```

---

## Module 5: Migration & Rotation

### AC-MIG-001: Schema-migratie is additief
```gherkin
Given een bestaande portal_connectors tabel met data
When de Alembic schema-migratie draait
Then wordt encrypted_config JSONB kolom toegevoegd met default '{}'
And wordt connector_dek_enc BYTEA kolom toegevoegd aan portal_orgs
And zijn alle bestaande rijen ongewijzigd
And is de migratie reversibel (downgrade verwijdert de kolommen)
```

### AC-MIG-002: Data-migratie versleutelt bestaande credentials
```gherkin
Given 3 organisaties met totaal 10 connectors met plaintext credentials
When het data-migratiescript draait
Then heeft elke organisatie een connector_dek_enc (niet null)
And heeft elke connector een encrypted_config met versleutelde gevoelige velden
And retourneert decryptie van encrypted_config de originele plaintext waarden
```

### AC-MIG-003: KEK-rotatie re-encrypteert DEKs
```gherkin
Given een systeem met 5 organisaties elk met een versleutelde DEK
When rotate_kek(old_kek, new_kek) wordt uitgevoerd
Then zijn alle connector_dek_enc waarden gewijzigd (re-encrypted met nieuwe KEK)
And retourneert decrypt met de nieuwe KEK dezelfde DEK als decrypt met de oude KEK deed
And zijn connector credentials NIET opnieuw versleuteld (ongewijzigd)
```

### AC-MIG-004: Rollback scenario
```gherkin
Given een systeem met versleutelde credentials
When encrypted_config op null wordt gezet voor alle connectors
Then valt het systeem terug op plaintext config
And werken alle connectors normaal via het interne endpoint
```

---

## Penetratie-testscenario's

### PEN-001: Aanvaller krijgt PostgreSQL dump
```gherkin
Given een aanvaller heeft een volledige PostgreSQL database dump
When de aanvaller portal_connectors.config inspecteert
Then zijn gevoelige velden (access_token, api_token) NIET zichtbaar in config
When de aanvaller portal_connectors.encrypted_config inspecteert
Then ziet de aanvaller een base64-encoded versleutelde blob
And kan de aanvaller de blob NIET ontsleutelen zonder de DEK
When de aanvaller portal_orgs.connector_dek_enc inspecteert
Then ziet de aanvaller een versleutelde DEK blob
And kan de aanvaller de DEK NIET ontsleutelen zonder de ENCRYPTION_KEY env var
```

### PEN-002: Aanvaller krijgt backup zonder ENCRYPTION_KEY
```gherkin
Given een aanvaller heeft een database backup EN applicatie-code maar NIET de ENCRYPTION_KEY env var
When de aanvaller probeert ConnectorCredentialStore.decrypt_credentials() aan te roepen
Then faalt de decryptie omdat de KEK niet beschikbaar is
And zijn alle connector credentials onleesbaar
And zijn de DEKs in portal_orgs onleesbaar
```

### PEN-003: Log stream blootgesteld
```gherkin
Given een aanvaller heeft toegang tot de volledige VictoriaLogs log stream
When de aanvaller zoekt naar access_token, api_token, refresh_token, app_private_key
Then vindt de aanvaller GEEN credential-waarden in log entries
And vindt de aanvaller alleen "***" voor gemaskeerde velden
And vindt de aanvaller alleen metadata (connector_id, org_id, connector_type)
```

### PEN-004: GET /connectors response onderschept
```gherkin
Given een aanvaller onderschept een GET /api/app/knowledge-bases/{kb_slug}/connectors response
When de aanvaller de response body parseert
Then bevatten alle credential-velden de waarde "***"
And is GEEN enkele plaintext token zichtbaar in de response
And zijn niet-gevoelige velden (repo_url, schedule) wel zichtbaar
```

### PEN-005: Brute force op encrypted_config
```gherkin
Given een aanvaller heeft de encrypted_config blob en connector_dek_enc
When de aanvaller een brute force aanval probeert op AES-256-GCM
Then is de zoekruimte 2^256 (computationeel onhaalbaar)
And biedt de 12-byte nonce bescherming tegen replay attacks
And detecteert GCM authenticatie elke bitflip in de ciphertext
```

---

## Performance criteria

### PERF-001: Encryptie overhead
```gherkin
Given een connector config met 5 gevoelige velden van gemiddeld 100 bytes
When encrypt_credentials wordt aangeroepen
Then is de uitvoertijd minder dan 5ms
And is de overhead verwaarloosbaar vergeleken met de totale connector sync duur
```

### PERF-002: Decryptie overhead
```gherkin
Given een encrypted_config blob van een typische connector
When decrypt_credentials wordt aangeroepen
Then is de uitvoertijd minder dan 5ms
And wordt de DEK gecached voor de duur van het request
```

### PERF-003: Migratie doorvoer
```gherkin
Given 1000 bestaande connectors met plaintext credentials
When het data-migratiescript draait
Then is de totale migratietijd minder dan 60 seconden
And wordt de voortgang gelogd per 100 connectors
```

---

## Coverage gate

| Component | Vereiste coverage |
|---|---|
| `ConnectorCredentialStore` | 100% |
| `AESGCMCipher` | 100% |
| `SENSITIVE_FIELDS` mapping | 100% (alle connector types) |
| Connector API endpoints (encrypt/decrypt/mask) | >= 90% |
| Structlog SecretStr processor | 100% |
| Data-migratiescript | >= 85% |

---

## Definition of Done

- [ ] Alle acceptance criteria scenarios slagen
- [ ] Alle penetratie-testscenario's bevestigd
- [ ] Coverage gates gehaald
- [ ] Performance criteria gevalideerd
- [ ] Security code review uitgevoerd door tweede ontwikkelaar
- [ ] Data-migratie succesvol gedraaid op staging
- [ ] Rollback procedure getest op staging
- [ ] `ENCRYPTION_KEY` veilig gedistribueerd naar productie
- [ ] Deploy runbook bijgewerkt met key backup procedure
