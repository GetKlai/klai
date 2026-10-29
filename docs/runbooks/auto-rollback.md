# Automatic rollback

Alerts: `auto_rollback_event`, `rollback_rehearsal_missing`,
`datastore_restore_rehearsal_missing`, `public01_infra_deploy_rollback`,
`public01_infra_deploy_silent` (all `severity=critical`). Spec:
SPEC-INFRA-AUTO-ROLLBACK-001.

`deploy-with-rollback.sh` (one service image) and `compose-rollback.sh` (the
compose and config sync) put the previous state back when a deploy gate goes
red. Each writes a report to `/opt/klai/deploy-reports/<service>.json`
(`compose.json` for the sync), the container log next to it as `.log`, a line
in the ledger `/opt/klai/deploy-ledger.log`, and, for a rollback, an
`auto_rollback` event line that Alloy ships to VictoriaLogs.

## A deploy was rolled back

The mail names the service, the failed gate and the outcome.

- `rolled_back`: the previous version is serving again. Nothing is broken now.
  The bad version is still on `main`, so the next deploy of that service fails
  the same way until it is fixed. Open the issue labelled `auto-rollback`
  (title `CI failure: <workflow>`): it carries the report, the commit and the
  failed run. Fix forward on `main`; there is no revert PR.
- `rollback_failed`: the previous version did not hold either, or there was no
  previous image. Production may be broken. The issue is labelled
  `rollback-failed`. On core-01 read `<service>.json` and `<service>.log` in
  `/opt/klai/deploy-reports/`, then `docker compose ps <service>` and decide:
  restore by hand (`docker tag klai/<service>:previous <compose tag>`, then
  `compose-up.sh --no-pull --force-recreate <service>`) or fix and redeploy.
  Images that migrate their own database (Twenty, LiteLLM, GlitchTip, Zitadel)
  report `rollback_failed` on purpose and are never restored by image alone.
- `shadow_failed` (gate `datastore-shadow`, issue label `shadow-failed`): a
  data store's new image was started on a copy of its latest snapshot and did
  not come up with the same data, so the compose sync stopped before it
  replaced a single file. The live store, its consumers and the files are
  untouched; nothing to restore. The revert PR excludes the version, as after
  `rolled_back`. The run log shows why (the throwaway's last log lines, or
  the facts that differ, `<` snapshot and `>` new image).

In VictoriaLogs:
`_time:30m service:auto-rollback event:auto_rollback | sort by (_time) desc | limit 10`.

## The weekly rehearsal is missing

No `rollback_rehearsal` event with outcome `ok` reached VictoriaLogs for 8
days. The three checks, in order, are in the alert description. A green rehearsal
run whose line is missing means Alloy or VictoriaLogs is the break, not the
rollback.

## A data store rollback failed

An image change of a data store (Postgres, Qdrant, Meilisearch, Mongo, Redis,
FalkorDB, Garage, RabbitMQ) is snapshotted into `/opt/klai/backups/pre-<store>-<time>/`
before the compose sync replaces any file (`datastore-rollback.sh`), and the
new image of a store is proven on a copy first (the shadow, above). Then the
gate `datastore-cutover`, right after the compose file is replaced, stops the
store's consumers, takes a fresh snapshot, starts the new image and probes it
at once; red there restores the fresh snapshot, so only the seconds since it
are lost. The ledger shows `snapshot`, `shadow_green`, `cutover`, then
`deployed` or `restored` per store; the time between `cutover` and the next
line is what a red verdict cost in writes. Three
gates restore nothing on purpose and end `rollback_failed`, leaving the new
version running:

- `restore-window`: the red gate came more than 30 minutes after the snapshot,
  so a restore would throw away too many writes. Decide by hand whether the new
  version can stay.
- `mongo-fcv`: Mongo's featureCompatibilityVersion differs from the one in the
  snapshot, so someone changed it after the upgrade. Restoring would undo that
  and every write since; the snapshot is still there if that is what you want.
- `disk-headroom`: less free disk than twice the store's data; nothing changed.

The snapshot directory holds `meta` (images, consumers), the data
(`volume.tar`, `dump.rdb`, `definitions.json` or one `.dump` per database) and
the baseline the restore is compared against. The ledger line has kind `data`.

## Postgres majors and the Mongo FCV

A Postgres major (18 to 19) needs no person. Its shadow restores the
snapshot's dumps into a throwaway of the new major after checking that it
carries every extension the databases use; red there (`shadow_failed`) moves
nothing. Then, right after the compose file is replaced,
the gate `datastore-major` stops the store's consumers, dumps every database
and the roles (`globals.sql`), renames the old data directory to
`/var/lib/postgresql/<old>/docker.aside` inside the same volume, starts the new
image on an empty directory (its entrypoint runs initdb with the compose
environment and init scripts), restores roles and databases and compares the
probe counts with the dump. The ledger shows `snapshot`, `shadow_green`,
`cutover`, then `migrated`, then the compose line `deployed`. A red gate within 30 minutes renames the old
directory back and starts the previous image on it (ledger `restored`); no
pg_restore runs on the live store then. Past the window the new major is the
state, and the first sync after it removes the `.aside` directory
(`settle: ... removing`); the dump under `backups/pre-*` stays.

- `datastore-shadow` red with `extension <x> of the old cluster is not in
  <image>`: the new image lacks an extension (pgvector) a database uses.
  Nothing moved; pick an image that carries it. The same check runs again in
  `datastore-major`.
- `<store> no longer runs <image>: something recreated it before the cutover`:
  another deploy started the new major before the cutover. Put back, nothing
  lost; find what recreated it.
- The consumers are down from the dump to the end of the restore. The main
  Postgres holds 5.5 GB on disk (litellm 4.7 GB of it, 2026-10-29), so expect
  minutes.

Mongo: a new MongoDB version runs on the previous featureCompatibilityVersion,
and each version only starts on the FCV of the one before it. At the start of
every compose sync, once no `pre-mongodb-*` snapshot is inside its window,
the sync raises the FCV to the running version (ledger `fcv_raised`), so the
next minor can start. A failed `setFeatureCompatibilityVersion` stops that sync
before it changes anything, with the server's answer in the log.

## Proving the shadow on production (dry flag)

`echo <store> > /opt/klai/.datastore-dry` on core-01, then merge any patch
bump of that store. The sync takes the real snapshot, runs the real shadow
and then forces it red: the run ends `shadow_failed`, the live store, its
consumers and the files stay as they were, a revert PR and an issue follow.
The flag is removed when it is used. It works for every store except the
self-migrating apps, which have no shadow.

## The datastore restore rehearsal is missing

No `datastore_restore_rehearsal` event with outcome `ok` reached VictoriaLogs
for 30 days (the retention of VictoriaLogs). The rehearsal runs on the 1st and
the 15th and restores every store's newest snapshot into a throwaway container
labelled `klai.adhoc=datastore-restore`; a store without a snapshot gets a fresh
one first, which stops Qdrant, Meilisearch, Mongo and Garage for the seconds of
a cold copy. The three checks, in order, are in the alert description. A red
run names the store whose restore failed; its event line carries the snapshot
path, and the restore output is in the run log. Leftover throwaways from an
interrupted run: `docker ps -a --filter label=klai.adhoc=datastore-restore` and
`docker volume ls --filter label=klai.adhoc=datastore-restore`. To prove a
newer image before Renovate offers it, dispatch the rehearsal with the input
`shadow` (`<store>=<image>`, space-separated): each named store also gets a
shadow on that image, reported in its event (`shadow_image`, `shadow`); a red
shadow there is a fact about that version and does not fail the run.

## A public-01 infra deploy was rolled back

public-01 is not deployed by this repo's CI: its `deploy-infra.sh` (root cron,
every 10 minutes, script in the private klai-infra repo) applies klai-infra
`public-01/` from main. Before installing it copies every file to `<file>.prev`;
when the post-apply checks fail (Kuma healthy, Alloy running, getklai.com and
status.getklai.com answering) it puts them back, brings the stack up, checks
again and writes the revision to `/opt/klai/.infra-bad-rev`. No later run
reapplies that revision; a newer one on main is tried normally. The mail names
the revision, the failed gate and the outcome. The line is
`public01_infra_deploy_rollback`, from `/opt/klai/logs/infra-deploy.log` via
public-01's Alloy (`service:infra-deploy`).

- `rolled_back`: the previous files serve again. Nothing is broken now. Fix the
  revision forward in klai-infra; the next revision deploys by itself.
- `rollback_failed`: the previous files failed the checks too. Treat the site,
  status page and Uptime Kuma as possibly down: `ssh` to public-01,
  `docker compose ps` in `/opt/klai/public` and `/opt/klai/alloy`, read
  `tail -n 20 /opt/klai/logs/infra-deploy.log` and `journalctl -t infra-deploy`.
  The `.prev` files sit next to the installed ones.
- Not rolled back: Uptime Kuma's own database after a Kuma image migration. The
  nightly backup (`docs/runbooks/uptime-kuma.md#backup-and-restore` in
  klai-infra) is the restore source, used by hand.

In VictoriaLogs:
`_time:1h service:infra-deploy | sort by (_time) desc | limit 10`.

## Public-01 infra deploy is silent

No heartbeat line (`event:infra_deploy` with outcome `deployed`, `up_to_date`
or `skipped_bad_rev`) reached VictoriaLogs for 2 hours
(`public01_infra_deploy_silent`); lines with outcome `failed` or
`validation_failed` are not counted. `deploy-infra.sh`
writes one every run, so silence is one of three things: the cron stopped, Alloy
stopped shipping `/opt/klai/logs/infra-deploy.log` (the Alloy container must
mount `/opt/klai/logs`), or every run fails before it finishes, which logs
`failed` or `validation_failed` with the failing `gate`. The three checks, in
order, are in the alert description.
