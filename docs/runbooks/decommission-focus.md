# Runbook — Focus / research-api decommission cleanup

**SPEC:** SPEC-DECOMM-FOCUS-001
**One-shot, manual.** Run AFTER the PR for SPEC-DECOMM-FOCUS-001 has been
merged into `main` and deployed to core-01.

This runbook executes the destructive operations that the SPEC kept out of
CI/CD: dropping the `klai_focus` Qdrant collection, wiping
`/opt/klai/research-uploads/` and `/opt/klai/research-api-src/`, and
emitting a compliance event.

## Prerequisites

- SSH access to core-01 (`ssh core-01`).
- The SPEC-DECOMM-FOCUS-001 PR is merged into `main` and deployed.
- The klai-infra PR (separate; removes SOPS env vars and updates SERVERS.md)
  is also merged.
- You have read [.moai/specs/SPEC-DECOMM-FOCUS-001/](../../.moai/specs/SPEC-DECOMM-FOCUS-001/).

## Out-of-band pre-checks

Before running any destructive step, confirm that nothing in production
unexpectedly started using the deleted scope:

```bash
ssh core-01 "docker logs --since 24h klai-core-retrieval-api-1 2>&1 \
  | grep -E 'scope.*notebook|scope.*broad|_search_notebook'"
# Expected: empty output

ssh core-01 "docker logs --since 24h klai-core-caddy-1 2>&1 \
  | grep -c '/research/'"
# Expected: 0
```

If either query returns a hit, STOP. A caller is still using the path and
needs investigation before the cleanup proceeds.

## Step 1 — Drop the Qdrant `klai_focus` collection

```bash
ssh core-01 "docker exec klai-core-portal-api-1 sh -c 'API=\$(printenv QDRANT_API_KEY); python -c \"
import urllib.request, json
req = urllib.request.Request(
    \\\"http://qdrant:6333/collections/klai_focus\\\",
    headers={\\\"api-key\\\": \\\"\$API\\\"},
    method=\\\"DELETE\\\"
)
print(urllib.request.urlopen(req).read().decode())
\"'"
```

Expected response: `{"result":true,"status":"ok",...}`. The endpoint is
idempotent — if the collection is already gone, you still get a 2xx with
`result: false` rather than a 404.

## Step 2 — Wipe `/opt/klai/research-uploads/`

Final review before deletion (single tenant directory expected):

```bash
ssh core-01 "find /opt/klai/research-uploads -maxdepth 2 -type d"
ssh core-01 "find /opt/klai/research-uploads -type f | wc -l"
```

If the file count matches the expected residue documented in the SPEC
(2 PDFs as of 2026-05-05), proceed:

```bash
ssh core-01 "rm -rf /opt/klai/research-uploads"
ssh core-01 "ls /opt/klai/research-uploads 2>&1"
# Expected: ls: cannot access '/opt/klai/research-uploads': No such file or directory
```

If the file count is unexpectedly higher, STOP and investigate before
deleting — there may be data from a tenant that wasn't accounted for.

## Step 3 — Wipe `/opt/klai/research-api-src/`

This is sync-residue from when the deploy workflow rsync'd source code
to the server. Safe to remove unconditionally — code is in git.

```bash
ssh core-01 "rm -rf /opt/klai/research-api-src"
ssh core-01 "ls /opt/klai/research-api-src 2>&1"
# Expected: No such file or directory
```

## Step 4 — Emit the compliance retention event

Insert a `focus.legacy_data_purged` row into `product_events` so future
GDPR audits can see the data wipe happened with the SPEC reference:

```bash
ssh core-01 "docker exec klai-core-portal-api-1 sh -c '
python -c \"
import asyncio, asyncpg, os, json
async def main():
    c = await asyncpg.connect(
        host=\\\"postgres\\\",
        user=os.environ[\\\"POSTGRES_USER\\\"],
        password=os.environ[\\\"POSTGRES_PASSWORD\\\"],
        database=\\\"klai\\\",
    )
    await c.execute(
        \\\"INSERT INTO product_events (event_type, org_id, user_id, properties, created_at) \\\"
        \\\"VALUES (\$1, NULL, NULL, \$2, NOW())\\\",
        \\\"focus.legacy_data_purged\\\",
        json.dumps({
            \\\"point_count\\\": 15,
            \\\"pdf_count\\\": 2,
            \\\"tenant_id\\\": \\\"100000000000000001\\\",
            \\\"spec\\\": \\\"SPEC-DECOMM-FOCUS-001\\\",
        }),
    )
    await c.close()
asyncio.run(main())
\"'"
```

Verify it landed:

```bash
ssh core-01 "docker exec klai-core-portal-api-1 sh -c '
python -c \"
import asyncio, asyncpg, os
async def main():
    c = await asyncpg.connect(
        host=\\\"postgres\\\",
        user=os.environ[\\\"POSTGRES_USER\\\"],
        password=os.environ[\\\"POSTGRES_PASSWORD\\\"],
        database=\\\"klai\\\",
    )
    rows = await c.fetch(
        \\\"SELECT event_type, properties, created_at FROM product_events \\\"
        \\\"WHERE event_type = \$1 ORDER BY created_at DESC LIMIT 1\\\",
        \\\"focus.legacy_data_purged\\\",
    )
    for r in rows:
        print(r)
    await c.close()
asyncio.run(main())
\"'"
```

## Step 5 — Final cleanliness verification

```bash
# Qdrant collections list — only klai_knowledge should remain
ssh core-01 "docker exec klai-core-portal-api-1 sh -c 'API=\$(printenv QDRANT_API_KEY); python -c \"
import urllib.request, json
req = urllib.request.Request(\\\"http://qdrant:6333/collections\\\", headers={\\\"api-key\\\":\\\"\$API\\\"})
print(json.dumps(json.loads(urllib.request.urlopen(req).read())[\\\"result\\\"][\\\"collections\\\"]))
\"'"
# Expected: [{"name":"klai_knowledge"}]

# Filesystem
ssh core-01 "ls /opt/klai/research-uploads /opt/klai/research-api-src 2>&1"
# Expected: both report 'No such file or directory'

# SOPS env (klai-infra cleanup must be merged for this to be empty)
ssh core-01 "SOPS_AGE_KEY_FILE=~/.config/sops/age/keys.txt sops --decrypt /opt/klai/.env.sops 2>/dev/null \
  | grep -E '^KUMA_TOKEN_RESEARCH_API|^RESEARCH_API_ZITADEL_AUDIENCE'"
# Expected: empty

# Container topology unchanged
ssh core-01 "docker ps --format '{{.Names}}' | wc -l"
# Expected: matches your pre-merge baseline (research-api wasn't running anyway,
# so this number should be identical — this catches accidental side-effect deletes)
```

## After this runbook

1. Update `.moai/specs/SPEC-DECOMM-FOCUS-001/spec.md` `status: approved` →
   `status: implemented` and append a HISTORY entry with today's date and
   the merge commit SHAs (one for the main repo, one for klai-infra).
2. Confirm in your next async update / standup that the cleanup has
   landed.
3. If the production retention event from Step 4 is the only one for this
   tenant, you can reasonably consider the residual Focus data closed
   from a compliance standpoint.

## Rollback

There is no rollback. The deletions are intentionally non-reversible:

- **Qdrant collection** — gone. If you need the 15 chunks back, restore
  from a Qdrant snapshot (if one exists for that day) and re-create
  the collection. Practically: the data was orphaned the moment Focus
  was decommissioned in April; recovery is unlikely to be useful.
- **`/opt/klai/research-uploads`** — gone. `core-01` has weekly volume
  backups in `/opt/klai/backups/`; if a tenant raises a question
  within the retention window, the original files can be restored from
  there.
- **SOPS env vars** — re-add them via the standard SOPS roundtrip on
  core-01 (see [credential-rotation.md](../../klai-infra/docs/runbooks/credential-rotation.md)).
- **product_events row** — leave it. It's a compliance trail; even if
  Step 4 ran but the rest didn't, the entry is still accurate
  documentation.

If something feels wrong mid-runbook, STOP and ping `mark.vletter@voys.nl`.
