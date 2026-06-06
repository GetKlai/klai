# Meilisearch Tenant Search Rollout

Use this runbook before enabling `SEARCH=true` for any LibreChat tenant.

## Preconditions

- Meilisearch runs `getmeili/meilisearch:v1.45.2`.
- Meilisearch has `MEILI_DB_PATH=/meili_data`; otherwise v1.45.2 can start on
  `./data.ms` and ignore the mounted volume.
- The tenant LibreChat env uses tenant indexes:
  - `MEILI_MESSAGES_INDEX={slug}_messages`
  - `MEILI_CONVOS_INDEX={slug}_convos`
- The tenant LibreChat env uses a scoped Meili API key in `MEILI_MASTER_KEY`.
  Do not put the real `MEILI_MASTER_KEY` in a LibreChat container.

## Create or Rotate a Tenant Runtime Key

Run with the real Meili master key from portal/provisioning only:

```bash
curl -fsS -X POST "$MEILI_URL/keys" \
  -H "Authorization: Bearer $MEILI_MASTER_KEY" \
  -H "Content-Type: application/json" \
  --data-binary "{
    \"name\": \"librechat-${slug}-meili\",
    \"description\": \"LibreChat search for tenant ${slug}\",
    \"actions\": [
      \"search\",
      \"documents.*\",
      \"indexes.create\",
      \"indexes.get\",
      \"indexes.update\",
      \"settings.*\",
      \"tasks.get\"
    ],
    \"indexes\": [\"${slug}_messages\", \"${slug}_convos\"],
    \"expiresAt\": null
  }"
```

For the static getklai canary, store the returned `key` as
`GETKLAI_MEILI_API_KEY`. Compose intentionally fails if this value is missing.

## Runtime Image Verification

Before rollout on a new LibreChat image, verify the container filesystem still
matches the entrypoint patch assumptions:

```bash
docker run --rm --entrypoint sh ghcr.io/danny-avila/librechat:v0.8.5-rc1 -lc '
  set -eu
  test -f /app/packages/data-schemas/dist/models/message.cjs
  test -f /app/packages/data-schemas/dist/models/convo.cjs
  test -f /app/packages/data-schemas/dist/models/plugins/mongoMeili.cjs
  test -f /app/api/db/indexSync.js
  grep -R "indexName: ['\''\"]messages['\''\"]" /app/packages/data-schemas/dist/models/message.cjs
  grep -R "indexName: ['\''\"]convos['\''\"]" /app/packages/data-schemas/dist/models/convo.cjs
  grep -R "client.index(['\''\"]messages['\''\"])" /app/packages/data-schemas/dist/models /app/api/db
  grep -R "client.index(['\''\"]convos['\''\"])" /app/packages/data-schemas/dist/models /app/api/db
'
```

Then run a tenant container with `SEARCH=true`,
`MEILI_MESSAGES_INDEX`, and `MEILI_CONVOS_INDEX` set and confirm the entrypoint
logs do not report remaining global Meili references. If any required file is
missing or any global reference remains, do not enable search.

## Migration and Backfill

Do not rely on `MEILI_NO_SYNC=true` as a migration. Existing Mongo documents may
already have `_meiliIndex=true` from the old global `messages`/`convos` indexes,
which can make LibreChat consider sync complete while tenant indexes are empty.

For an existing tenant:

1. Keep `SEARCH=true` off.
2. Create `{slug}_messages` and `{slug}_convos`.
3. Backfill the tenant indexes from the tenant Mongo database, or reset the
   tenant Mongo `_meiliIndex` flags and run a controlled sync.
4. Verify document counts and key parity:
   - tenant Mongo messages vs `{slug}_messages`
   - tenant Mongo convos vs `{slug}_convos`
   - no documents in the tenant indexes whose primary keys are absent from the
     tenant Mongo database
5. Enable `SEARCH=true` only after parity is clean.

## Rollback

To disable tenant search without deleting chat data:

1. Remove or set `SEARCH=false` in the tenant LibreChat env.
2. Restart the tenant LibreChat container.
3. Keep `{slug}_messages` and `{slug}_convos` intact until counts and user
   search behavior have been reviewed.
4. If rotating a key, create the new scoped key first, update the env, restart,
   verify search, then delete the old `librechat-{slug}-meili` key.
