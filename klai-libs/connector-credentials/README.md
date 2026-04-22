# klai-connector-credentials

Shared AES-256-GCM credential encryption library for Klai connectors.

## Purpose

`klai-portal/backend`, `klai-connector`, and `klai-knowledge-ingest` all need
to encrypt or decrypt per-tenant connector credentials (OAuth tokens, cookies,
app private keys). Before SPEC-CRAWLER-004 the encryption logic lived inside
`klai-portal/backend` and was copied into `klai-connector`. This library
consolidates both into a single path-installed dependency so plaintext secrets
never leave a service boundary.

## Key hierarchy (SPEC-KB-020)

```
ENCRYPTION_KEY env var (64-char hex = 32 bytes)
    = KEK (key encryption key)
        encrypts
    portal_orgs.connector_dek_enc (per-org DEK, AES-256-GCM)
        encrypts
    portal_connectors.encrypted_credentials (AES-256-GCM JSON blob)
```

## Usage

```python
from connector_credentials import ConnectorCredentialStore

store = ConnectorCredentialStore(os.environ["ENCRYPTION_KEY"])

# Encrypt on the write path (portal-api)
encrypted_blob, stripped_config = await store.encrypt_credentials(
    org_id=org_id,
    connector_type="web_crawler",
    config={"url": "https://example.com", "cookies": [...]},
    db=session,
)

# Decrypt on the read path (knowledge-ingest, klai-connector)
sensitive = await store.decrypt_credentials(
    org_id=org_id,
    encrypted_credentials=encrypted_blob,
    db=session,
)
```

## Schema dependency

The library reads and writes `portal_orgs.connector_dek_enc` directly via
parameterized raw SQL. It does not import any service-specific ORM model.
Every service embedding this library must share the `portal_orgs` table
(currently: one Postgres database, `klai` schema).

## Testing

```bash
cd klai-libs/connector-credentials
uv sync --group dev
uv run pytest
```

No live database is required — `ConnectorCredentialStore` accepts any object
quacking like `sqlalchemy.ext.asyncio.AsyncSession`, and the suite uses
`AsyncMock` to verify the SQL interactions.
