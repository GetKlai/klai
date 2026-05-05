# knowledge-ingest Alembic stamp (one-time, before SPEC-INGEST-ALEMBIC-001 deploy)

> **AI playbook** — execute on prod **before** the first deploy that contains the
> SPEC-INGEST-ALEMBIC-001 entrypoint (PR #337 or its successor).

## Why

knowledge-ingest historically had no Alembic. PR #337 introduces it with a
single baseline migration `0001_baseline` whose `upgrade()` is the full
`knowledge.*` schema as it exists in prod today (idempotent: every CREATE
guarded by `IF NOT EXISTS` or a `pg_class` / `pg_proc` / `pg_trigger`
existence-check).

After this PR merges, the new container's `entrypoint.sh` will run
`alembic upgrade head` on every start. Without a prior stamp, that command
will:

1. Find no row in `knowledge.alembic_version` → assume the schema is empty.
2. Try to create every table / sequence / index / function / trigger.
3. Hit the idempotency guards → all CREATEs become no-ops, no error raised.

That's "functionally fine" but introduces ~13 startup-log lines noise per
container restart. **Stamping** writes `0001_baseline` to
`knowledge.alembic_version` once, so subsequent `upgrade head` is a clean
no-op until a real new migration is added.

## When to run

- **Once**, on `core-01`, **after PR #337 image is built but before** the
  new container actually starts (i.e. after the GHA workflow's `Build and
  push` step completes but before `docker compose up -d` recreates the
  knowledge-ingest container).
- Or at any time AFTER the new container is running. The first ten or so
  startups will be noisy but functional; this command silences them.

## Command

```bash
ssh core-01 "docker exec --workdir /repo/klai-knowledge-ingest klai-core-knowledge-ingest-1 alembic stamp 0001_baseline"
```

Audit 2026-05-05 finding: `--workdir` is REQUIRED. The `alembic.ini`
file lives at `/repo/klai-knowledge-ingest/alembic.ini` and `alembic`
resolves it from the process working directory. Without `--workdir`,
the exec process runs at `/` and fails with `FileNotFoundError:
alembic.ini`. The container's normal CMD entrypoint runs at the right
WORKDIR, but `docker exec` does NOT inherit it — you have to specify
it explicitly.

Expected output:

```
INFO  [alembic.runtime.migration] Context impl PostgresqlImpl.
INFO  [alembic.runtime.migration] Will assume transactional DDL.
INFO  [alembic.runtime.migration] Running stamp_revision  -> 0001_baseline
```

## Verify

```bash
ssh core-01 "docker exec klai-core-knowledge-ingest-1 alembic current"
```

Expected: `0001_baseline (head)`.

## What if the stamp is forgotten?

Symptom: container starts cleanly but `docker logs klai-core-knowledge-ingest-1`
shows lines like:

```
INFO  [alembic.runtime.migration] Will assume transactional DDL.
INFO  [alembic.runtime.migration] Running upgrade  -> 0001_baseline, baseline
... CREATE TABLE IF NOT EXISTS ... (×13)
```

The migration completes successfully thanks to the IF NOT EXISTS guards;
no data loss. To clean up, run the stamp command above and restart the
container — subsequent restarts will be silent.

## What about future migrations?

A new migration `0002_<name>` adds new DDL (e.g. ADD COLUMN). It uses
`down_revision = "0001_baseline"`. After this stamp, the prod chain is:

- `alembic_version = "0001_baseline"` (current)
- `alembic upgrade head` runs migration `0002_<name>` on next start
- `alembic_version = "0002_<name>"` after.

No second stamp is ever needed; only the initial baseline is.

## Refs

- SPEC-INGEST-ALEMBIC-001 — the bootstrap SPEC
- pitfall `alembic-stamped-past-skipped-migration` (HIGH) — the inverse
  failure mode (stamping past a migration that never actually ran)
- pitfall `scribe-deploy-no-alembic` (HIGH) — the pre-existing class this
  fix closes for knowledge-ingest
- klai-portal/backend/CLAUDE.md "Deploy workflow" — same stamp-on-bootstrap
  pattern was used during portal-api's initial Alembic adoption
