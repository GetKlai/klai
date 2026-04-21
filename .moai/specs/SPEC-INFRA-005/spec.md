---
id: SPEC-INFRA-005
version: 0.2.0
status: draft
created: 2026-04-19
updated: 2026-04-21
author: Mark Vletter
priority: high
---

# SPEC-INFRA-005: Stateful service persistence, backup, and observability hardening

## HISTORY

### v0.2.0 (2026-04-21)
- Hidden-state sweep of klai-connector, klai-scribe, klai-focus/research-api, klai-retrieval-api, klai-knowledge-mcp, klai-mailer found two additional persistence surfaces not covered by v0.1.0: `scribe-audio-data` (WAV recordings, PII) and `/opt/klai/research-uploads` (user-uploaded documents, PII).
- Discovered stated retention policy for scribe audio ("delete on successful transcription") was not enforced in code — cleanup helper reachable only from manual DELETE endpoint. Fixed separately in commit `032f1c0e` with new `app/services/audio_storage.py` module and 6 regression tests. Orphan WAV from 2026-04-10 removed from production.
- New section: Retention policies. PII-bearing volumes require a time-bound or event-driven cleanup rule, enforceable in code, covered by at least one test.
- Open question added: research-uploads retention rule (none stated yet by product owner).

### v0.1.0 (2026-04-19)
- Initial draft, triggered by FalkorDB graph data loss incident (see `docs/runbooks/post-mortems/2026-04-19-falkordb-graph-loss.md`).
- Scope broadened from the specific falkordb bug to all stateful services after audit revealed: (a) `backup.sh` does not cover FalkorDB, Qdrant, or Garage; (b) no stateful service has a Docker healthcheck; (c) no mechanism exists to detect "container writes go to the wrong place" before it destroys data.
- Hetzner Storage Box upload path is already implemented in `/opt/klai/scripts/backup.sh` (lines 113-131) — this SPEC extends coverage, does not reinvent transport.

---

## Goal

Eliminate the class of bug where a stateful service can silently lose data because its declared persistence configuration does not match reality. "Reality" here covers three concerns that failed together on 2026-04-19:

1. **Mount correctness** — the bind mount target in `docker-compose.yml` matches the image's actual data path.
2. **Backup coverage** — every stateful volume is in the daily backup + Hetzner Storage Box upload.
3. **Observability** — a persistence failure generates an actionable alert without requiring a human to notice a gauge that hit zero.

Success means: the next time an image pin, restart, or volume misconfiguration happens, we catch it before data is lost — either pre-merge in CI, post-deploy via smoke test, or within one hour via alerting.

---

## Why now

- Direct incident: FalkorDB graph lost for all orgs on 2026-04-19 08:01 UTC+3 due to a bind mount that targeted `/data` while the image wrote to `/var/lib/falkordb/data`. Bug was silent for 24 days.
- Audit finding: same class of bug is theoretically possible for any new stateful service added to compose. The version-management playbook (`docs/runbooks/version-management.md`) verifies container health post-deploy, not data persistence.
- Audit finding: `backup.sh` covers PostgreSQL, Gitea, MongoDB, Redis, Meilisearch. It does NOT cover FalkorDB, Qdrant, Garage. Even with correct mounts, these would not have been recoverable.
- Audit finding: no stateful service in compose has a `healthcheck:` block. `docker ps` shows `Up X hours` without `(healthy)`, which prevents Docker from propagating health state to dependent services or external monitors.
- Audit finding: VictoriaLogs has all relevant log lines, but there is no alert that ever fires on their absence (e.g., "Loading RDB" never appeared after 26 March; no one noticed).

---

## Scope

### In scope

- All services in `deploy/docker-compose.yml` with persistent state (list in §Implementation Plan).
- `docker-compose.yml` volume mounts, healthchecks.
- `/opt/klai/scripts/backup.sh` — extension for missing services.
- New CI workflow `audit-compose-volumes` (pre-merge guard).
- New post-deploy smoke test integrated into `deploy/scripts/push-health.sh`.
- VictoriaLogs / VictoriaMetrics alert rules for persistence events.
- Update to `docs/runbooks/version-management.md` reflecting stateful-service upgrade procedure.

### Out of scope

- FalkorDB graph data re-ingest (tracked separately — requires decision from §Open Questions).
- Introducing a new backup target beyond Hetzner Storage Box.
- Rewriting any stateful service to use a different storage engine.
- Multi-region replication / HA for any service.
- LUKS full-disk encryption (covered in `klai-infra/SERVERS.md` roadmap).
- Kubernetes migration (out of scope indefinitely for this stack).

---

## Success Criteria

1. **Zero mount mismatches** — for every stateful service in compose, the bind mount target matches the image's declared data path (extracted from image env vars or documented allow-list). CI fails the PR otherwise.
2. **Backup coverage 100%** — every stateful volume has a corresponding entry in `backup.sh`, including the two PII-bearing volumes added in v0.2.0 (`scribe-audio-data`, `/opt/klai/research-uploads`). A manual run of `backup.sh` produces encrypted artifacts for each volume, uploaded to the Hetzner Storage Box via the existing rsync path.
3. **Post-deploy persistence smoke test passes** — `deploy/scripts/push-health.sh` writes a canary, triggers persistence, verifies host-side file mtime changed. Exits non-zero on failure.
4. **Healthcheck on every stateful service** — `docker ps` reports `(healthy)` for all of: postgres, mongodb, redis, meilisearch, falkordb, qdrant, gitea, garage, victorialogs, victoriametrics, grafana, scribe-api.
5. **Persistence absence alert** — alerting fires within 60 minutes when a stateful service has been running without writing to its persistent volume on the host. Validated by simulating the 2026-04-19 incident in a dry-run.
6. **Playbook updated** — §3.3 and §3.4 of `version-management.md` carry a stateful-service checklist: verify mount path, run smoke test, confirm backup exists.
7. **Canary-in-a-recreate drill passes** — a scheduled monthly job recreates one stateful service (with data backed up first), verifies data is intact post-restart, and restores from backup if not.
8. **Retention policies enforced in code** — every PII-bearing volume has an explicit retention rule declared in `deploy/volume-mounts.yaml` (e.g. `delete_on_success`, `max_age_days`, `manual`) and at least one regression test proving the rule is honoured in the owning service.

---

## EARS Requirements

### Mount correctness (pre-merge guard)

**REQ-M1** — WHEN a PR modifies `deploy/docker-compose.yml`, the CI system SHALL run `scripts/audit-compose-volumes.sh`.

**REQ-M2** — WHILE `audit-compose-volumes.sh` runs, it SHALL for each bind-mount in compose: pull the image, extract env vars via `docker image inspect`, identify variables ending in `_DATA_PATH`, `_HOME`, `_DIR`, and compare with the mount target.

**REQ-M3** — IF the declared mount target does not match any known data path AND the service is not in a documented allow-list, the CI check SHALL fail the PR with a diagnostic showing the image's env vars and the mount target.

**REQ-M4** — WHILE a stateful service exists without a matching env-derived data path, it SHALL have an explicit entry in `deploy/volume-mounts.yaml` (new file) documenting: image, data path source (env var or upstream docs URL), backup method, restore method.

### Backup coverage

**REQ-B1** — WHEN `backup.sh` runs, it SHALL produce encrypted artifacts for every stateful volume listed in `deploy/volume-mounts.yaml`.

**REQ-B2** — WHILE a stateful volume exists without a backup handler in `backup.sh`, the CI pre-merge check SHALL fail.

**REQ-B3** — WHEN the Storage Box upload step completes, `backup.sh` SHALL emit a structured log line per-artifact with name + size; any absent artifact SHALL produce a non-zero exit + Uptime Kuma "down" push.

**REQ-B4** — WHILE `backup.sh` runs on a daily schedule, a retention policy SHALL keep 30 daily + 12 monthly + 3 yearly backups on the Storage Box, managed via sftp pruning (the TODO in backup.sh L133-135).

### Healthcheck

**REQ-H1** — WHERE a service in compose holds persistent state, it SHALL declare a `healthcheck:` block that verifies both (a) the service responds to its protocol, and (b) a write-then-flush-then-host-file-check round-trip has succeeded in the last 10 minutes.

**REQ-H2** — WHILE Docker reports a stateful service as `unhealthy`, that state SHALL be scraped by Prometheus (`cadvisor` or `docker-exporter`) and surface as a Grafana alert.

### Post-deploy smoke test

**REQ-S1** — WHEN `deploy/scripts/push-health.sh` runs after a deploy, it SHALL execute a per-service canary: write a sentinel key, trigger persistence (SAVE, fsync, etc.), and verify the host-side data file mtime is within 60 seconds.

**REQ-S2** — IF any canary fails, `push-health.sh` SHALL exit non-zero and push a "critical" event to Uptime Kuma (`KUMA_TOKEN_PERSISTENCE_SMOKE`, new token).

**REQ-S3** — WHILE the deploy pipeline runs, CI SHALL fail the deploy job if `push-health.sh` exits non-zero, preventing "green deploy with broken persistence" outcomes.

### Alerting on absence

**REQ-A1** — WHEN a stateful service starts, an observability rule SHALL verify that the expected persistence-load marker appears in logs within 30 seconds. For FalkorDB: `"Loading RDB"`. For Postgres: `"database system is ready to accept connections"`. For MongoDB: `"Waiting for connections"`. For Redis: `"Loading RDB"` or `"Ready to accept connections"` preceded by AOF replay.

**REQ-A2** — IF the expected marker is absent for more than 60 seconds after container start, an alert SHALL fire to the on-call channel (Uptime Kuma + Grafana).

**REQ-A3** — WHILE a stateful service is running, a periodic check (every 10 minutes) SHALL `stat` the canonical data file on the host and raise an alert if mtime has not advanced for longer than the service's expected idle-save interval (falkordb: 1h, postgres: derived from checkpoint_timeout, redis: derived from save rules).

### Retention policies (new in v0.2.0)

**REQ-R1** — WHERE a volume holds PII (audio recordings, uploaded documents, extracted text that quotes user input), it SHALL have a `retention` block in `deploy/volume-mounts.yaml` with one of:
- `delete_on_success` — file is removed as soon as the consuming workflow completes successfully (example: scribe-audio-data when transcription succeeds).
- `max_age_days: N` — file is removed N days after creation regardless of downstream state.
- `manual` — only the user can delete; requires an explicit product-level justification in the entry.

**REQ-R2** — WHILE a service owns a `delete_on_success` volume, the success code path in the owning service SHALL call the cleanup helper. At least one regression test SHALL fail if the cleanup call is removed, renamed, or bypassed.

**REQ-R3** — WHILE a service owns a `max_age_days` volume, a scheduled sweeper (systemd timer, cron, or in-app background task) SHALL walk the volume daily and `unlink` files older than the configured age. The sweeper SHALL emit a structured log line per deletion and a summary (files deleted, bytes freed).

**REQ-R4** — WHEN a PR adds a new bind-mount or named volume to `deploy/docker-compose.yml` AND the service's purpose mentions user-facing content, the CI check SHALL block the PR until the mount has a `retention` entry in `volume-mounts.yaml`.

### Upgrade playbook integration

**REQ-P1** — WHILE `docs/runbooks/version-management.md` exists, §3.3 (minor/patch upgrade) SHALL list a stateful-services pre-flight: run `scripts/audit-compose-volumes.sh` locally; verify last backup is ≤ 24h old on Storage Box.

**REQ-P2** — WHILE `version-management.md` §3.4 (major upgrade) exists, it SHALL require a manual one-shot `backup.sh` run immediately before the upgrade, with the resulting backup name captured in the PR description.

**REQ-P3** — WHERE an image pin changes an existing `:latest` to a specific version, the PR SHALL be labelled `stateful-risk` and require approval from a reviewer (no self-merge, no rule bypass on `main`).

---

## Implementation plan (phased)

### Phase 1 — inventory + allow-list (foundation)

Deliverables:
- `deploy/volume-mounts.yaml` — machine-readable inventory:
  ```yaml
  falkordb:
    image: falkordb/falkordb:v4.18.1
    host_path: /opt/klai/falkordb-data
    container_path: /var/lib/falkordb/data
    data_path_source: env FALKORDB_DATA_PATH
    backup: rdb
    backup_method: copy
    expected_idle_save_interval: 3600
    retention: manual
  postgres:
    image: postgres:17.9
    host_path: (named volume postgres-data)
    container_path: /var/lib/postgresql
    data_path_source: docs PGDATA default
    backup: sql
    backup_method: pg_dumpall
    expected_idle_save_interval: 900
    retention: manual
  scribe-audio-data:
    image: ghcr.io/getklai/scribe-api:latest
    host_path: (named volume)
    container_path: /data/audio
    data_path_source: env AUDIO_STORAGE_DIR (scribe-api app config)
    backup: tar
    backup_method: docker run tar
    pii: true
    retention: delete_on_success
    retention_enforced_by: app/services/audio_storage.py::finalize_success
    retention_test: tests/test_audio_retention.py
  research-uploads:
    image: ghcr.io/getklai/research-api:latest
    host_path: /opt/klai/research-uploads
    container_path: /opt/klai/research-uploads
    data_path_source: literal (app/api/sources.py _UPLOAD_BASE)
    backup: tar
    backup_method: rsync
    pii: true
    retention: TBD                    # OPEN QUESTION — product owner decision needed
  # … etc for: mongodb, redis, meilisearch, qdrant, gitea, garage-meta, garage-data, victorialogs, victoriametrics, grafana
  ```
- Audit of all 13+ stateful services including the two added in v0.2.0. Classify: OK, misconfig, missing backup, missing retention.

### Phase 2 — CI pre-merge guard

Deliverables:
- `scripts/audit-compose-volumes.sh` — parses compose, inspects images, compares, fails on mismatch.
- `.github/workflows/audit-compose.yml` — runs on any PR touching `deploy/docker-compose.yml` or `deploy/volume-mounts.yaml`. Required status check.
- Branch protection: `audit-compose` required on `main` for paths `deploy/**`.

### Phase 3 — backup extension

Deliverables:
- `backup.sh` update: add FalkorDB (`redis-cli --rdb` export or `docker run tar` on `/opt/klai/falkordb-data`), Qdrant (`docker run tar` on named volume), Garage (both meta + data volumes).
- `backup.sh` update: Storage Box sftp pruning (close the TODO on L133-135). Retention 30/12/3 per REQ-B4.
- Uptime Kuma monitor: `KUMA_TOKEN_BACKUP_SIZE` — tracks daily backup size; alerts on sudden drop (e.g., backup shrinks > 50% day-over-day = likely an empty volume mount).

### Phase 4 — healthchecks

Deliverables:
- Add `healthcheck:` block to each stateful service in compose:
  ```yaml
  falkordb:
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 30s
      timeout: 5s
      retries: 3
  ```
- For services that support it (postgres, redis), add a deeper check that writes + reads a canary key.
- Grafana dashboard panel: per-service health status + time-since-last-healthy transition.

### Phase 5 — post-deploy smoke test

Deliverables:
- Extend `deploy/scripts/push-health.sh` with a `persistence_smoke` function:
  - For each service in `deploy/volume-mounts.yaml`, run the service-specific canary write + flush + host mtime check.
  - Exit non-zero on any failure.
- Wire into the deploy-compose GitHub Action so a failed smoke test breaks the deploy job.

### Phase 6 — absence alerting

Deliverables:
- VictoriaLogs alert rules (`deploy/vmalert/persistence-rules.yaml`):
  - Rule: "stateful service started but no Loading marker within 30s" — evaluated per service.
  - Rule: "no BGSAVE / checkpoint message in last N hours" — per-service N from volume-mounts.yaml.
- Host-side mtime probe (systemd timer `/etc/systemd/system/persistence-probe.timer`, 10-min interval):
  - Reads `deploy/volume-mounts.yaml` from `/opt/klai/` (synced copy).
  - For each entry, `stat` the data file, emits Prometheus metric `klai_persistence_file_age_seconds{service="..."}`.
- Grafana alert: `klai_persistence_file_age_seconds > 2 * expected_idle_save_interval` → fire.

### Phase 7 — playbook + drill

Deliverables:
- Update `docs/runbooks/version-management.md` §3.3, §3.4, §7 per REQ-P1, P2, P3. Add a §12 "Stateful service change checklist".
- Schedule a monthly "recreate drill" (systemd timer): picks one stateful service, backs it up, recreates the container, verifies data, reports to Uptime Kuma. Rotates through services monthly.

---

## Open questions

1. ~~**FalkorDB graph rebuild**~~ — RESOLVED 2026-04-21: test data, let it repopulate organically via new ingests on the next tenant.
2. ~~**Qdrant backup method**~~ — RESOLVED 2026-04-21: Snapshot API (`POST /collections/{name}/snapshots`), zero downtime.
3. ~~**Garage backup strategy**~~ — RESOLVED 2026-04-21: `garage meta snapshot` (Garage v0.9.4+, confirmed running v2.3.0 on core-01) for the LMDB; rsync for data blobs (immutable once written). Zero downtime.
4. ~~**Granularity of absence alerting**~~ — RESOLVED 2026-04-21: per-service totals for Phase 6 MVP; per-org later if needed.
5. ~~**Hidden state in klai-connector/scribe/research-api/focus**~~ — RESOLVED 2026-04-21: sweep completed. connector uses `tempfile.TemporaryDirectory()` context manager (clean). scribe has `/data/audio` persistent volume (now added to scope). research-api has `/opt/klai/research-uploads` (now added to scope). retrieval-api, knowledge-mcp, mailer clean.
6. **research-uploads retention policy** — NEW, unresolved. Scribe has `delete_on_success`. Research uploads currently have no retention rule at all. Options:
   - `max_age_days: 90` after last notebook activity
   - `delete_when_source_removed` — when user deletes the source record, also purge the file
   - `manual` — explicit user action only
   Product owner decision required. Default recommendation: `delete_when_source_removed` + optional max_age_days as secondary sweeper.

---

## Risks and mitigations

| Risk | Mitigation |
|---|---|
| Healthcheck commands themselves fail and take down containers | Each healthcheck has `retries: 3` + `start_period: 60s`; only reports unhealthy after sustained failure. Tested before merge. |
| Added post-deploy smoke test slows deploys beyond tolerance | Smoke test runs in parallel per service; budget 60s total; bail if exceeded with explicit "smoke-test-slow" alert rather than deploy failure. |
| Backup of FalkorDB via `SAVE` blocks writes | FalkorDB/Redis `BGSAVE` is non-blocking (fork-based). We use BGSAVE, not SAVE. |
| Qdrant snapshot during live traffic loses writes | Maintenance window: pick a low-traffic hour; accept a ~60s stall acceptable for nightly backup. |
| Alert fatigue from too-sensitive absence rules | Start with per-service thresholds tuned to 2× expected idle-save interval; iterate for 2 weeks, loosen where needed. |
| CI audit script blocks legitimate mount changes | Allow-list mechanism in `volume-mounts.yaml` lets you explicitly declare a non-standard path. Reviewer friction is intentional — it forces a second look. |

---

## Rollback plan

Each phase lands as an independent PR. Rollback = `git revert` of the relevant PR. No phase depends on a prior phase in a way that prevents isolated revert:

- Phase 2 rollback: disable the GitHub Action; CI stops blocking PRs.
- Phase 4 rollback: remove healthcheck blocks; containers revert to previous health-less state.
- Phase 6 rollback: disable the vmalert rules; alerts stop firing.

The only phase that changes production-critical files (backup.sh) is Phase 3. That one carries its own pre-flight: run the new backup.sh with dry-run flag first, verify artifacts produced, then enable the cron.

---

## Definition of Done

- All seven phases landed, each with its own test evidence in PR.
- Drill executed: the 2026-04-19 incident re-created in a sandbox, and the new stack detects + alerts within 60 min.
- `version-management.md` updated and cross-referenced from this SPEC.
- Post-mortem `docs/runbooks/post-mortems/2026-04-19-falkordb-graph-loss.md` updated with a link to this SPEC and a "Status: mitigated" line.
- Monthly recreate drill running for at least one cycle without false positives.

---

## References

- `docs/runbooks/post-mortems/2026-04-19-falkordb-graph-loss.md` — the triggering incident.
- `docs/runbooks/version-management.md` — the playbook this SPEC extends.
- `/opt/klai/scripts/backup.sh` — existing backup infrastructure, extended here.
- `klai-infra/SERVERS.md` — Hetzner Storage Box config, disaster recovery context.
- `.claude/rules/klai/infra/deploy.md` — existing deploy rules, some will graduate to this SPEC.
- `.claude/rules/klai/lang/docker.md` § "Non-root USER and host volume ownership" — related pitfall.
- Commit `3c5673ea` — the falkordb mount fix already landed.
- Commit `5d12587e` — the pinning commit that triggered exposure.
- Commit `fe9a4239` — where the falkordb mount misconfig was introduced (2026-03-26).
- Commit `032f1c0e` — audio retention fix (scribe); extracted `audio_storage.py` helper module and added regression tests; reference implementation of REQ-R2.
