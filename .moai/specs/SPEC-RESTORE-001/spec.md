---
id: SPEC-RESTORE-001
version: "0.1.0"
status: draft
created: 2026-05-05
updated: 2026-05-05
author: Mark Vletter
priority: high
related:
  - SPEC-INFRA-005 (backup pipeline parent)
  - SPEC-CODEBASE-AUDIT-001 (parent, Cluster M)
---

# SPEC-RESTORE-001: End-to-end restore runbook + maandelijks test-restore in CI

## Summary

Sluit Schrödinger's-backup risico: backup-pipeline produceert daily encrypted artifacts naar Storage Box, maar **er is geen restore runbook en geen verificatie-test**. Bij een echte outage moet operator backup.sh reverse-engineeren onder druk. Deze SPEC voegt staging-restore-script + cron-driven verificatie + canonical runbook toe.

## Motivation

Per `reports/audit-2026-05-04/i18n-a11y-backup.md` (8.19):
- **HIGH gap**: Geen end-to-end restore runbook (`docs/runbooks/restore.md` ontbreekt)
- **HIGH gap**: Geen periodieke test-restore in CI/cron — backup-set kan corrupt zijn zonder dat iemand het merkt
- **MED**: Storage Box retention "TODO" — onbeperkt opbouwen

## Scope

### In scope

1. **`scripts/test-restore.sh`** — staging-restore-script dat:
   - Pulls latest backup van Storage Box
   - Decrypt met age recipient (mac of test-host)
   - Restore naar isolated test-postgres + test-mongo + test-redis containers
   - Run smoke-queries (count rows, verify schemas)
   - Tear down containers
   - Output: pass/fail + report

2. **`docs/runbooks/restore.md`** — end-to-end runbook met:
   - Disaster scenarios (full host loss, single-DB corruption, single-blob corruption)
   - Per scenario: specifieke restore-commands
   - Pre-flight verificatie (age key access, Storage Box connectivity)
   - Per-store restore-volgorde (Postgres eerst voor metadata, dan blob-stores)
   - Post-restore validation checklist

3. **Maandelijkse test-restore in CI** — GitHub Action `test-restore.yml`:
   - `schedule: cron: '0 4 1 * *'` (1e van maand 04:00 UTC)
   - Pulls backup van afgelopen 24u, draait `test-restore.sh`, alarmeert bij failure (push naar Uptime Kuma + Grafana)
   - Failure-report naar `reports/restore-tests/<date>.md`

4. **Storage Box retention policy**:
   - Local 30d (huidige)
   - Remote tier-rotation: dagelijks laatste 30 + wekelijks laatste 12 + maandelijks laatste 12 = ~64 backups
   - Auto-cleanup script `scripts/storagebox-prune.sh` met dry-run flag

### Out of scope

- LUKS migratie van core-01 (apart traject prod-01 migration)
- VictoriaLogs offsite backup (apart SPEC)

## Acceptance criteria

1. `scripts/test-restore.sh --dry-run` werkt zonder echte restore
2. `scripts/test-restore.sh` op test-host: alle 7 stores restoreren + smoke-queries pass
3. `docs/runbooks/restore.md` reviewed door 2e operator (peer-review)
4. Eerste cron-run slaagt (handmatige trigger via `gh workflow run test-restore.yml`)
5. Storage Box retention: na 1 maand bevat 30+12+12 = 54 artifacts (verify via SSH)

## Implementation notes

- Test-host: dedicated machine (kan dev-host of klai-private VM zijn) met SSH-key in CI secrets
- Postgres restore: `pg_restore` op temp container; verify schemas matchen `pg_dump --schema-only` van source
- Mongo restore: `mongorestore --archive` op temp container
- Redis restore: stop test-redis, copy `dump.rdb`, start test-redis
- FalkorDB restore: idem als Redis (RDB-format)
- Qdrant restore: snapshot API per collection
- Garage restore: rsync blobs naar test-Garage
- Retention prune: respect age + tier-class via filename-pattern

## Risks

| Risk | Mitigatie |
|---|---|
| Test-host disk vult (backups stack op) | Per-run cleanup; max 1 active test-restore tegelijk |
| Schema-mismatch tussen prod en test-restore tooling | Use latest production image-tags voor test-Postgres/Mongo |
| age key compromise op test-host | Read-only key (recipient-only voor decrypt) |

## References

- `reports/audit-2026-05-04/i18n-a11y-backup.md` (8.19)
- `deploy/scripts/backup.sh` — bestaande backup pipeline
- `deploy/volume-mounts.yaml` — SPEC-INFRA-005 inventory
- `klai-infra/SERVERS.md` — Disaster recovery sectie
