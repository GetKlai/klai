# Runbook — Focus / research-api decommission cleanup

> Commands are given as they run on core-01; paths are relative to the deploy directory. See [How public runbooks write commands](../contributing/infrastructure-boundary.md#how-public-runbooks-write-commands).

**SPEC:** SPEC-DECOMM-FOCUS-001
**One-shot, manual.** Run AFTER the PR for SPEC-DECOMM-FOCUS-001 has been
merged into `main` and deployed to core-01.

This runbook executes the destructive operations that the SPEC kept out of
CI/CD: dropping the `klai_focus` Qdrant collection, wiping
`research-uploads/` and `research-api-src/` in the deploy directory, and
emitting a compliance event.

## Prerequisites

- Operator access to core-01 (see the private infrastructure runbooks). Every command below runs on core-01, from the deploy directory (`cd <deploy-dir>`).
- The SPEC-DECOMM-FOCUS-001 PR is merged into `main` and deployed.
- The klai-infra PR (separate; removes SOPS env vars and updates SERVERS.md)
  is also merged.

## Out-of-band pre-checks

Before running any destructive step, confirm that nothing in production
unexpectedly started using the deleted scope:

```bash
docker logs --since 24h klai-core-retrieval-api-1 2>&1 \
  | grep -E 'scope.*notebook|scope.*broad|_search_notebook'
# Expected: empty output

docker logs --since 24h klai-core-caddy-1 2>&1 \
  | grep -c '/research/'
# Expected: 0
```

If either query returns a hit, STOP. A caller is still using the path and
needs investigation before the cleanup proceeds.

## Step 1 — Drop the Qdrant `klai_focus` collection

```bash
docker exec -i klai-core-portal-api-1 python - <<'PY'
import os, urllib.request
req = urllib.request.Request(
    "http://qdrant:6333/collections/klai_focus",
    headers={"api-key": os.environ["QDRANT_API_KEY"]},
    method="DELETE",
)
print(urllib.request.urlopen(req).read().decode())
PY
```

Expected response: `{"result":true,"status":"ok",...}`. The endpoint is
idempotent — if the collection is already gone, you still get a 2xx with
`result: false` rather than a 404.

## Step 2 — Wipe `research-uploads/`

Final review before deletion (single tenant directory expected):

```bash
find research-uploads -maxdepth 2 -type d
find research-uploads -type f | wc -l
```

If the file count matches the expected residue documented in the SPEC
(2 PDFs as of 2026-05-05), proceed:

```bash
rm -rf research-uploads
ls research-uploads 2>&1
# Expected: ls: cannot access 'research-uploads': No such file or directory
```

If the file count is unexpectedly higher, STOP and investigate before
deleting — there may be data from a tenant that wasn't accounted for.

## Step 3 — Wipe `research-api-src/`

This is sync-residue from when the deploy workflow rsync'd source code
to the server. Safe to remove unconditionally — code is in git.

```bash
rm -rf research-api-src
ls research-api-src 2>&1
# Expected: No such file or directory
```

## Step 4 — Emit the compliance retention event

Insert a `focus.legacy_data_purged` row into `product_events` so future
GDPR audits can see the data wipe happened with the SPEC reference:

```bash
docker exec -i klai-core-portal-api-1 python - <<'PY'
import asyncio, asyncpg, os, json
async def main():
    c = await asyncpg.connect(
        host="postgres",
        user=os.environ["POSTGRES_USER"],
        password=os.environ["POSTGRES_PASSWORD"],
        database="klai",
    )
    await c.execute(
        "INSERT INTO product_events (event_type, org_id, user_id, properties, created_at) "
        "VALUES ($1, NULL, NULL, $2, NOW())",
        "focus.legacy_data_purged",
        json.dumps({
            "point_count": 15,
            "pdf_count": 2,
            "tenant_id": "100000000000000001",
            "spec": "SPEC-DECOMM-FOCUS-001",
        }),
    )
    await c.close()
asyncio.run(main())
PY
```

Verify it landed:

```bash
docker exec -i klai-core-portal-api-1 python - <<'PY'
import asyncio, asyncpg, os
async def main():
    c = await asyncpg.connect(
        host="postgres",
        user=os.environ["POSTGRES_USER"],
        password=os.environ["POSTGRES_PASSWORD"],
        database="klai",
    )
    rows = await c.fetch(
        "SELECT event_type, properties, created_at FROM product_events "
        "WHERE event_type = $1 ORDER BY created_at DESC LIMIT 1",
        "focus.legacy_data_purged",
    )
    for r in rows:
        print(r)
    await c.close()
asyncio.run(main())
PY
```

## Step 5 — Final cleanliness verification

```bash
# Qdrant collections list — only klai_knowledge should remain
docker exec -i klai-core-portal-api-1 python - <<'PY'
import os, json, urllib.request
req = urllib.request.Request("http://qdrant:6333/collections", headers={"api-key": os.environ["QDRANT_API_KEY"]})
print(json.dumps(json.loads(urllib.request.urlopen(req).read())["result"]["collections"]))
PY
# Expected: [{"name":"klai_knowledge"}]

# Filesystem
ls research-uploads research-api-src 2>&1
# Expected: both report 'No such file or directory'

# SOPS env (klai-infra cleanup must be merged for this to be empty)
SOPS_AGE_KEY_FILE=~/.config/sops/age/keys.txt sops --decrypt .env.sops 2>/dev/null \
  | grep -E '^KUMA_TOKEN_RESEARCH_API|^RESEARCH_API_ZITADEL_AUDIENCE'
# Expected: empty

# Container topology unchanged
docker ps --format '{{.Names}}' | wc -l
# Expected: matches your pre-merge baseline (research-api wasn't running anyway,
# so this number should be identical — this catches accidental side-effect deletes)
```

## After this runbook

1. Confirm in your next async update / standup that the cleanup has
   landed.
2. If the production retention event from Step 4 is the only one for this
   tenant, you can reasonably consider the residual Focus data closed
   from a compliance standpoint.

## Rollback

There is no rollback. The deletions are intentionally non-reversible:

- **Qdrant collection** — gone. If you need the 15 chunks back, restore
  from a Qdrant snapshot (if one exists for that day) and re-create
  the collection. Practically: the data was orphaned the moment Focus
  was decommissioned in April; recovery is unlikely to be useful.
- **`research-uploads/`** — gone. `core-01` has weekly volume
  backups in `backups/` of the deploy directory; if a tenant raises a question
  within the retention window, the original files can be restored from
  there.
- **SOPS env vars** — re-add them via the standard SOPS roundtrip on
  core-01 (see [credential-rotation.md](../../klai-infra/docs/runbooks/credential-rotation.md)).
- **product_events row** — leave it. It's a compliance trail; even if
  Step 4 ran but the rest didn't, the entry is still accurate
  documentation.

If something feels wrong mid-runbook, STOP and ping Mark.
