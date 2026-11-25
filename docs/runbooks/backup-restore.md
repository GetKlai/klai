# Backup restore

> Server paths are relative to the deploy directory of the host named in the step (core-01 or public-01). See [How public runbooks write commands](../contributing/infrastructure-boundary.md#how-public-runbooks-write-commands). The private infrastructure repository holds the host, the credentials and where the off-site copy lives.

The nightly backup (`deploy/scripts/backup.sh`) leaves a day-set in two places: the newest sets unencrypted in the local backup directory, and every set encrypted with `age` on the off-site box. `deploy/scripts/backup-restore.sh` restores a set into throwaway containers and volumes and checks each artifact against a fact. It never writes to a live volume or database: putting data back into production is a decision this runbook leaves to you (step 5).

## 1. Get a day-set

- Last seven days: use the complete day-set directory in the local backup directory (it holds a `.backup-complete` marker).
- Older, or when the local disk is gone: fetch the day-set directory from the off-site box. It holds one `.age` file per artifact, plus `backup-facts.txt.age`. The private infrastructure repository describes how to reach the box.

Fetch into an empty directory on a host with room for about three times the set.

## 2. Decrypt (off-site sets only)

You need an age identity file for one of the two backup recipients. Keep it outside any repository and off the server; pass it by path and do not copy it into the set directory. `backup-restore.sh` decrypts each artifact itself when it finds `.age` files and `--identity <file>`:

```
backup-restore.sh --identity <identity file> <set directory>
```

A wrong identity or a damaged file fails per artifact with `decrypting <file>.age failed`.

## 3. Run the restore

```
backup-restore.sh [--identity <file>] [--keep] <set directory> [artifact ...]
```

Artifacts: `postgres gitea mongodb redis vexa_redis meilisearch qdrant falkordb garage_meta garage_data firecrawl_postgres listmonk_postgres crm_postgres fider_postgres umami_postgres crm_storage listmonk_uploads scribe_audio vaultwarden_db vaultwarden_files research_uploads`. Without names it does all of them. One line per artifact: `ok` with the target it restored into, `FAIL` with the reason, or `SKIP` for an artifact the backup recorded nothing for (its container was not running that night; a name given on the command line is never skipped). The exit code is 1 when any artifact failed; the others are still restored and reported. `--keep` leaves the throwaway containers and volumes (label `klai.adhoc=datastore-restore`) for you to inspect; remove them yourself afterwards.

## 4. Check the facts

A line `ok` means the fact held:

| Artifact | Fact |
|---|---|
| Postgres dumps | rows per table equal the rows in the dump's COPY blocks |
| MongoDB | documents per collection equal what mongorestore reported |
| Redis, vexa Redis, FalkorDB | at least the keys (and graphs) read before the save |
| Meilisearch, Qdrant | at least the documents per index, points per collection read before the snapshot |
| tar artifacts | the member count and name hash recorded at backup time, and as many files after extracting |
| Vaultwarden database | the copy opens in a scratch directory, passes `PRAGMA integrity_check` and holds the server's own tables; no Vaultwarden is started on it |

Every file is also compared with the checksum recorded at backup time, so a cut or damaged copy is named before anything starts. A `FAIL` is a finding: do not promote that artifact; try the previous day-set.

## 5. Promote (production disaster only)

The script restores into throwaways on purpose. To put an artifact back into production, stop the consumers of that store, restore the same file into the store with the engine's own tool (psql for the dumps, mongorestore, the Redis dump placed in the data directory, the Meilisearch and Qdrant snapshot start options, extracting the tar into the volume), start the store and compare the same facts live. The deploy snapshots restore path in `docs/runbooks/auto-rollback.md` is the worked example for a live restore. Garage needs its node key and cluster layout next to the metadata snapshot; the nightly metadata tar holds only the snapshot, so a Garage restore also needs the layout from the private infrastructure repository.

### Vaultwarden

The set holds two artifacts for the password manager: `vaultwarden-db.sqlite3`, a consistent copy of the database made by the server's own `/vaultwarden backup` while it ran, and `vaultwarden-files.tar.gz` with `rsa_key.pem` and, when they exist, `attachments`, `sends` and `config.json`. The icon cache is not backed up; the server fetches the icons again. Both artifacts are as sensitive as the vault itself: decrypt them only on a host you control, and remove the plain copies when you are done.

1. Run `backup-restore.sh <set directory> vaultwarden_db vaultwarden_files` first; promote only when both are `ok`.
2. Stop the service: `docker compose stop vaultwarden`.
3. Keep the current data aside before you touch it, for example with a tar of the volume into the local backup directory, as for any live restore.
4. In the volume (`klai-core_vaultwarden-data`): delete `db.sqlite3-wal` and `db.sqlite3-shm`, put `vaultwarden-db.sqlite3` in place as `db.sqlite3`, and extract `vaultwarden-files.tar.gz` into the volume root. A stale WAL file next to a restored database can corrupt it, which is why it goes first (upstream wiki, "Backing up your vault").
5. Start the service: `docker compose up -d vaultwarden`, and check its log for migration errors.
6. Check: the web vault at the vault host answers, one team member logs in and sees their items, and an item with an attachment opens. Clients that were signed in stay signed in when `rsa_key.pem` came back; without it every session is signed out (upstream wiki), which costs a login, not data.

## The backup restore rehearsal is missing

The rule `backup_restore_rehearsal_missing` fires when no artifact restored in a rehearsal for 30 days. Check, in order:

1. `gh run list --workflow backup-restore-rehearsal.yml --limit 5` (red: read the failing artifact in the log; absent: the schedule stopped).
2. On core-01: `grep backup_restore_rehearsal logs/auto-rollback.log | tail -n 5` (lines present but not in VictoriaLogs: Alloy or VictoriaLogs is the break).
3. A set from before `backup-facts.txt` existed cannot be rehearsed: the first rehearsal after the change needs one new nightly run.
4. Start it by hand: `gh workflow run backup-restore-rehearsal.yml`.

## What the rehearsal does not prove

The rehearsal restores the local unencrypted copy. It does not decrypt the off-site `.age` files, because that needs an identity file that is not on the server. No scheduled run proves yet that last night's off-site copy decrypts and restores; steps 1 to 3 with an identity file are the way to prove it.
