#!/usr/bin/env bash
# datastore-rollback.sh, sourced by compose-rollback.sh (SPEC-INFRA-AUTO-ROLLBACK-001
# slice S8a, sections 3 R4-R6 and 4.c). Not an entry point: it defines functions
# and runs nothing. It uses RB_DIR, RB_LEDGER, RB_TO and rb_finish from
# compose-rollback.sh.
#
# A Postgres image change, or an image change of an app that migrates its own
# database on start, cannot be undone by putting the old image back: the old
# version may not understand the schema the new one wrote. So before the sync
# replaces any file, the database is dumped; a red gate within 30 minutes of
# that dump restores it together with the previous files and images.
#
# Functions
#   ds_snapshot_changed <old-compose> <new-compose>
#                         before the sync changes anything: disk check, the
#                         snapshot and its baseline per changed store or app,
#                         then the shadow of every store, a ledger line each
#                         (kind data). Ends the run not_started when short or a
#                         snapshot fails, shadow_failed when a shadow is red.
#   ds_shadow <dir> [image]        the snapshot in a throwaway of the new image,
#                         compared exactly with its baseline; never live (below)
#   ds_cutover            right after the compose file is replaced: one gate per
#                         store that switches it to the new image (below)
#   ds_gates              after the env-drift sweep: recreate the consumers of a
#                         self-migrating app's database and probe it
#   ds_unsnapshotted <old> <new>   inventory entries changed without a snapshot
#   ds_in_window          0 when every snapshot of this run is at most 30 min old
#   ds_restore_all        a red gate's data half: every snapshot, newest first
#   ds_restore_snapshot <dir>      one snapshot back into the live store
#   ds_restore_into <dir> <container> [image]
#                         restore into a throwaway container of the store's
#                         previous image (or <image>) and compare the counts;
#                         never touches the live store (ds_shadow,
#                         datastore-restore-rehearsal.sh)
#   ds_fcv_unchanged      0 unless a mongo snapshot's FCV differs from the live one
#   ds_settle             at the start of every sync, before the snapshot: what
#                         waits for a window to close (below)
#
# Shadow, then cutover (2026-10-29). A store's new image is proven on a copy
# before the live store sees it, because a red gate on the live store used to
# cost every write since the snapshot: on 2026-10-29 the mongodb snapshot was
# taken at 16:57:39 UTC and restored at 17:07:19, 580 s of writes to every
# tenant database discarded. After the snapshot and its old-image baseline,
# ds_shadow starts the snapshot in a second throwaway on the NEW image (the
# same mechanics: own volumes, --network none, the limits, the label) and lets
# it migrate the copy: Meilisearch with the MEILI_UPGRADE_DB of the new compose
# file, Mongo on the snapshot's FCV, Qdrant and Garage their storage, Postgres
# by restoring the dumps into the new image, for a major after checking that
# it carries every extension the databases use. Its facts must equal the
# baseline exactly, as in the restore rehearsal, Mongo's fcv line included.
# Red ends the sync before any file changed: outcome shadow_failed, exit 4,
# and renovate-revert-after-rollback.py reverts the version as after a
# rollback, because it is bad for our data. The shadow holds no lock: it never
# touches a live volume, so other deploys run beside it.
#
# Green, ds_cutover runs right after the compose file is replaced, before the
# sync recreates anything else (a `compose up` of librechat-getklai recreates a
# changed mongodb or redis it depends on). Under the host container lock it
# stops the consumers, takes a FRESH snapshot (another cold copy, BGSAVE,
# export or dump: the first one is stale by the shadow's duration), starts the
# store on the new image and probes it at once: ready, and its facts at least
# those of the fresh snapshot (Mongo's fcv line unchanged). Then the consumers
# are recreated. A red step restores the fresh snapshot, so the writes at risk
# are those between the fresh snapshot and the verdict. The ledger has a line
# for each step (snapshot, shadow_green, cutover, deployed or restored), which
# makes that window measurable per store. The first baseline stays the
# shadow's reference; the fresh snapshot is the restore source, and the
# 30-minute window of the later gates (E2E) counts from it.
#
# Postgres majors. A new major cannot open the data directory of the previous
# one, so its data comes over by dump and restore. ds_snapshot reads the major
# and PGDATA of the running container and of the new image (pulled for it). The
# snapshot's dump is the shadow's: it is restored into a throwaway of the new
# major after the extension check. The cutover is ds_major_cutover, which dumps
# again: consumers stop, every database and the roles are
# dumped (the final state, nothing writes any more), the store is recreated on
# the new image, whose entrypoint runs initdb with the compose environment and
# init scripts on an empty directory, and the roles and databases are restored
# and counted. Since postgres:18 the official images keep PGDATA at
# /var/lib/postgresql/<major>/docker inside the volume mounted at
# /var/lib/postgresql (verified on 18.6 and 19rc1, and on the running stores of
# core-01, 2026-10-29), so the new major gets a directory of its own. Its
# entrypoint refuses to initialise while /var/lib/postgresql, .../data or any
# .../*/docker holds a PG_VERSION (seen on 19rc1), so the cutover renames the
# old directory to <old>.aside in the same volume: a rename, no copy. The
# put-back of a red gate within the window: stop, remove the new directory,
# rename the old one back, start the previous image on it, no pg_restore. An
# image whose PGDATA is the old one, or outside the volume, is refused at the
# snapshot. A missing extension (pgvector) in the new image turns the shadow
# red before anything live moves, and is checked again at the cutover before
# anything is restored. Past the window the new major is the state, and
# ds_settle removes the .aside directory at the start of a later sync.
#
# Mongo FCV. An image change within one FCV runs the new binary on the old
# featureCompatibilityVersion, which the previous binary still opens: that is
# what makes a restore possible within the window, and why ds_fcv_unchanged
# refuses a restore over a changed FCV. Once no mongo snapshot is inside its
# window, ds_settle raises the FCV to the running version, so the next version
# starts from it.
#
# Other engines (slice S8b, DS_ENGINE_STORES): the snapshot depends on what the
# engine can read back.
#   volume       qdrant, meilisearch, mongo, garage: none of them opens data a
#                newer version wrote (Meilisearch migrates one way on start,
#                Mongo after an FCV bump, Qdrant and Garage migrate storage), so
#                the store is stopped and every data mount is copied cold with
#                tar, then started again. Seconds on core-01 (largest, Qdrant,
#                786 MB on 2026-10-28). Garage is copied cold as well, not with
#                backup.sh's `garage meta snapshot`: that snapshot holds only
#                db.lmdb, while a restore needs the node key and cluster layout
#                next to it in the same state.
#   rdb          redis, falkordb: BGSAVE, waited for on LASTSAVE as backup.sh
#                does, then the dump.rdb. A store that runs with appendonly
#                gets the dump back as the base file of a fresh AOF manifest,
#                because Redis with appendonly on ignores dump.rdb at start and
#                comes up empty (verified on redis:8.10.2-alpine).
#   definitions  rabbitmq: rabbitmqctl export_definitions. Messages are not
#                kept: firecrawl-rabbitmq has no volume and every recreate
#                already starts a new node.
#   none         crm-redis runs without persistence; a recreate empties it
#                today, so there is nothing to snapshot. The row exists for the
#                probe and the consumer recreate.
# The baseline of an engine snapshot is read from the snapshot itself, as for
# Postgres: it is restored into a throwaway container of the running image
# before any file changes, which also proves it reads back. The facts are lines
# like "collection <name>" and "count <name> <n>". A throwaway must reproduce
# them exactly; the live store after a restore must hold every name and at
# least every count, because LibreChat tenant containers outside compose keep
# writing while it runs.
#
# Throwaway containers run with --network none, label klai.adhoc=datastore-restore,
# limited memory and CPU, and on their own volumes; they are removed afterwards.
#
# Restore method: DROP DATABASE ... WITH (FORCE), then pg_restore --create. Not
# pg_restore --clean: --clean drops only the objects inside the dump, so a table
# a forward migration created stays behind, and the next upgrade of an app like
# Twenty then fails on "relation already exists". Dropping the database brings
# back exactly the dumped state. Roles live outside a per-database dump; they
# are cluster-wide and survive the drop.
#
# Baseline counts are read from the dump itself (the COPY rows of each probe
# table), not from a live count taken after it: the database keeps taking
# writes during and after the dump, and a live count would differ from the
# restored state on any busy table. The count is one full pg_restore pass over
# the archive whose exit status decides: a per-table `pg_restore -t` read a
# truncated dump without error when the table sat before the cut (verified on
# postgres:18.6-alpine), and a dump that does not read back would only fail
# after the live database had been dropped.
#
# The 30-minute window (R4) counts from the end of the last dump of the run,
# just before the first file is replaced, so a long dump does not eat it; for
# a store, from its fresh snapshot at the cutover.
#
# Dry mode, for proving the path on production: a file $RB_DIR/.datastore-dry
# holding one store name (e.g. meilisearch). The next sync that changes that
# store takes the real snapshot, runs the real shadow and then forces the
# shadow red, so the sync ends shadow_failed with the live store, every file
# and every consumer untouched, and the revert PR and the issue follow. Any
# engine, because the shadow never touches live. The file is removed when it
# is used, so it applies once. Stores only: an app row has no shadow, and the
# flag is then removed with a warning.
#
# Known ceilings
#   - Snapshots under backups/pre-* are not pruned; backup.sh retention only
#     removes the dated nightly sets. The disk check refuses a change when the
#     room is not there. Upgrade path: prune pre-* older than 7 days here.
#   - LiteLLM is recreated (and migrated) by litellm-hook-deploy.yml, which runs
#     beside this sync, so a dump taken here could already be post-migration. It
#     is listed with that workflow as owner; a red gate after a LiteLLM image
#     change restores nothing (rollback_failed, gate self-migrating-image).
#     LiteLLM is still a consumer of the main Postgres. A Postgres bump
#     force-recreates it on its unchanged image, which re-runs its Prisma
#     migrate against a schema already at that version: a no-op. When Postgres
#     and LiteLLM change in the same sync, the store row dumps the litellm
#     database too, and a restore puts the previous compose file back, so the
#     old LiteLLM tag returns together with its restored database.
#   - The restore holds the host container lock (compose-up.sh's), so no
#     service deploy starts a consumer against a half-restored database. A
#     deploy that does not go through compose-up.sh is not held back.
#   - The LibreChat tenant containers portal-api starts outside compose
#     (librechat-<tenant>) use mongodb, meilisearch and redis but are not
#     stopped for a restore; they reconnect after it, and their writes during it
#     fail. Their writes are also why a live restore compares counts as "at
#     least" rather than exact.
#   - A Mongo FCV that cannot be read (the new image does not answer) counts as
#     unchanged: a bump needs a running server, and ds_settle only issues it
#     after the window.
#   - A Postgres major is a dump and restore with the store's consumers
#     stopped, so the outage grows with the data: the main Postgres holds
#     5.5 GB on disk (litellm 4.7 GB of it, 2026-10-29). Upgrade path:
#     pg_upgrade --link from an image carrying both majors. Its shadow adds a
#     dump and a restore into a throwaway to the sync, before anything changes
#     and without an outage.
#   - A Postgres minor's cutover dumps again with the consumers stopped, so its
#     outage is that dump, as a major's (the main Postgres: minutes). Upgrade
#     path: a minor reads the same files, so its put-back could be the previous
#     image on them, without the fresh dump.
#   - The cutover's probe compares counts as "at least" the fresh snapshot's.
#     A delete by a writer outside compose (a LibreChat tenant on mongodb,
#     meilisearch or redis) in the seconds between reads red and restores.
#     The restore rehearsal compares a snapshot a cutover refreshed the same
#     way. Upgrade path: read the fresh baseline from the fresh copy in a
#     throwaway, at the cost of that start inside the outage.
#   - Apps that migrate their own database (Twenty, GlitchTip, Zitadel, Umami)
#     have no shadow: proving their migration on a copy needs the app itself
#     against a throwaway database. Their red gate restores their snapshot, as
#     before.
#
# Tests: deploy/scripts/tests/datastore-rollback.test.sh

DS_BACKUP_ROOT="${KLAI_BACKUP_ROOT:-$RB_DIR/backups}"
DS_LOCK_FILE="${KLAI_CONTAINER_LOCK:-$RB_DIR/.container-deploy.lock}"
DS_DRY_FLAG="$RB_DIR/.datastore-dry"
# R4: an automatic restore only within 30 minutes of the snapshot.
DS_WINDOW_SECONDS=1800

# ---------------------------------------------------------------------------
# Inventory. Names are compose services in deploy/docker-compose.yml; the test
# suite checks every name, superuser and consumer against that file.
#
# Postgres stores: service, superuser (its POSTGRES_USER), then the services
# that hold a connection to it. Those are stopped before a restore and
# force-recreated after it, and after a green image change of the store too: a
# consumer keeps a pool to the old container and still looks healthy
# (.claude/rules/klai/lang/docker.md, 2026-09-18).
DS_STORES='
postgres            klai       zitadel litellm klai-slack portal-api glitchtip-web glitchtip-worker retrieval-api gitea docs-app scribe-api vexa12-admin-api vexa12-meeting-api knowledge-ingest klai-connector
crm-postgres        twenty     crm crm-worker
firecrawl-postgres  firecrawl  firecrawl-api
listmonk-db         listmonk   listmonk
umami-postgres      umami      umami
fider-postgres      fider      fider
'
# Apps that migrate their own database on start: service whose image change
# triggers the snapshot, store, database, who recreates it, then the services
# stopped before and recreated after a restore. A store change already dumps
# every database of that store, so an app row is skipped when its store
# changed in the same sync, and so is a second row on the same database
# (glitchtip-migrate runs the migration, glitchtip-web shares its image).
DS_APPS='
crm            crm-postgres  twenty     sync                      crm crm-worker
glitchtip-migrate postgres   glitchtip  sync                      glitchtip-migrate glitchtip-web glitchtip-worker
glitchtip-web  postgres      glitchtip  sync                      glitchtip-migrate glitchtip-web glitchtip-worker
zitadel        postgres      zitadel    sync                      zitadel
umami          umami-postgres umami     sync                      umami
litellm        postgres      litellm    litellm-hook-deploy.yml   litellm
'
# Probe tables per database: store, database, schema.table... Content tables
# that exist in every version; a table missing from a dump refuses the change,
# so a rename upstream shows up as a red sync rather than a blind probe.
DS_PROBES='
postgres            klai        public.portal_orgs public.portal_users public.portal_knowledge_bases
postgres            zitadel     eventstore.events2 eventstore.unique_constraints
postgres            litellm     public.LiteLLM_TeamTable public.LiteLLM_VerificationToken
postgres            glitchtip   public.users_user public.projects_project
postgres            gitea       public.repository public.user
postgres            klai_slack  public.slack_installations public.workspace_links
postgres            vexa_v012   public.meetings public.transcriptions
crm-postgres        twenty      core.objectMetadata core.fieldMetadata
firecrawl-postgres  firecrawl   nuq.queue_scrape nuq.group_crawl
listmonk-db         listmonk    public.subscribers public.lists
umami-postgres      umami       public.website_event public.session
fider-postgres      fider       public.users public.system_settings
'
# The other stores (S8b): service, engine, then the services that hold a
# connection to it, with the same role as the consumers in DS_STORES. The test
# suite checks every name, and that every service naming the store in its
# environment or depends_on, or sharing its private network, is listed.
# redis-ephemeral is a Redis without persistence: probed, never snapshotted.
DS_ENGINE_STORES='
qdrant              qdrant           knowledge-ingest retrieval-api portal-api
meilisearch         meilisearch      librechat-getklai portal-api
mongodb             mongo            librechat-getklai portal-api
redis               redis            litellm librechat-getklai glitchtip-web glitchtip-worker retrieval-api firecrawl-api klai-mailer portal-api
vexa12-redis        redis            vexa12-runtime vexa12-meeting-api
crm-redis           redis-ephemeral  crm crm-worker
falkordb            falkordb         knowledge-ingest retrieval-api
garage              garage           knowledge-ingest klai-connector portal-api vexa12-meeting-api
firecrawl-rabbitmq  rabbitmq         firecrawl-api
'
# ---------------------------------------------------------------------------

# Images for the helper containers: tar and cp into volumes, and curl into the
# network namespace of a store whose image has no HTTP client (Qdrant, Garage).
# Both are pulled on core-01; the curl tag is the one backup.sh uses.
DS_TOOL_IMAGE="${KLAI_DS_TOOL_IMAGE:-alpine:3.22}"
DS_CURL_IMAGE="${KLAI_CURL_IMAGE:-curlimages/curl:8.11.1}"
# A throwaway holds one store's snapshot. The largest, FalkorDB, used 2.9 GiB
# live on 2026-10-28 on a host with 62 GiB; one throwaway runs at a time.
# nofile: Docker's default of 1024 open files is what the live mongodb raises to
# 64000 in the compose file, and the throwaway needs the same: counting the
# 2107 collections of all tenants opened one WiredTiger file each and the
# throwaway died with EMFILE on 2026-10-29 16:04 UTC, which read as "the
# snapshot does not read back" and stopped the sync.
DS_THROWAWAY_LIMITS=(--memory 6g --cpus 2 --ulimit nofile=64000:64000)

DS_SNAPSHOTS=()

ds_now_epoch() { date -u +%s; }
ds_compose() { docker compose --project-directory "$RB_DIR" "$@"; }
ds_set_meta() { { grep -v "^$2=" "$1/meta"; echo "$2=$3"; } > "$1/meta.tmp" && mv "$1/meta.tmp" "$1/meta"; }
ds_image_env() {
  docker image inspect --format '{{range .Config.Env}}{{println .}}{{end}}' "$1" 2>/dev/null | sed -n "s/^$2=//p" | head -n1
}

# ds_x <target> <cmd...>: run a command in a store. Target "ctr:<name>" is a
# plain container (the throwaway restore), anything else a compose service.
ds_x() {
  local t="$1"; shift
  case "$t" in
    ctr:*) docker exec -i "${t#ctr:}" "$@" ;;
    *) ds_compose exec -T "$t" "$@" ;;
  esac
}

ds_store_row() { awk -v s="$1" '$1 == s' <<< "$DS_STORES"; }
ds_app_row() { awk -v s="$1" '$1 == s' <<< "$DS_APPS"; }
ds_probe_tables() { awk -v s="$1" -v d="$2" '$1 == s && $2 == d { $1 = ""; $2 = ""; print }' <<< "$DS_PROBES"; }
ds_meta() { sed -n "s/^$2=//p" "$1/meta"; }

# ds_engine <service>: postgres, the engine of a DS_ENGINE_STORES row, or nothing.
ds_engine() {
  if [[ -n "$(ds_store_row "$1")" ]]; then echo postgres; return; fi
  awk -v s="$1" '$1 == s { print $2 }' <<< "$DS_ENGINE_STORES"
}
ds_is_store() { [[ -n "$(ds_engine "$1")" ]]; }
ds_store_names() { awk 'NF { print $1 }' <<< "$DS_STORES$DS_ENGINE_STORES"; }
# A snapshot written before S8b has no engine line; those were all Postgres.
ds_dir_engine() { local e; e="$(ds_meta "$1" engine)"; echo "${e:-postgres}"; }
ds_kind() {
  case "$1" in
    postgres) echo dump ;;
    qdrant|meilisearch|mongo|garage) echo volume ;;
    redis|falkordb) echo rdb ;;
    rabbitmq) echo definitions ;;
    *) echo none ;;
  esac
}
# Data mounts (container paths) per engine, as deploy/volume-mounts.yaml lists them.
ds_paths() {
  case "$1" in
    postgres) echo /var/lib/postgresql ;;
    qdrant) echo /qdrant/storage ;;
    meilisearch) echo /meili_data ;;
    mongo) echo /data/db ;;
    garage) echo /var/lib/garage/meta /var/lib/garage/data ;;
    redis) echo /data ;;
    falkordb) echo /var/lib/falkordb/data ;;
  esac
}
# The container behind a target: "ctr:<name>", or a compose service's container,
# running or not (a stopped one still carries the volumes).
ds_ctr() {
  case "$1" in
    ctr:*) echo "${1#ctr:}" ;;
    *) ds_compose ps -a -q "$1" 2>/dev/null | head -n1 ;;
  esac
}

# "<service> <image>" per service in a compose file. The file keeps services at
# two spaces and their image at four.
ds_images() {
  awk '/^services:/ { s = 1; next }
       /^[^ #]/ { s = 0 }
       s && /^  [A-Za-z0-9._-]+:[[:space:]]*$/ { svc = $1; sub(/:$/, "", svc) }
       s && /^    image:/ { img = $2; gsub(/["'\'']/, "", img); print svc, img }' "$1"
}
ds_image_of() { ds_images "$1" | awk -v s="$2" '$1 == s { print $2 }'; }
# ds_service_env <compose> <service> <key>: a literal environment value.
ds_service_env() {
  awk -v s="$2" -v k="$3:" '/^  [A-Za-z0-9._-]+:[[:space:]]*$/ { svc = $1; sub(/:$/, "", svc) }
    svc == s && $1 == k { v = $2; gsub(/["'\'']/, "", v); print v; exit }' "$1"
}

# Inventory names whose image differs between two compose files, store rows
# first; an app row is dropped when its store is in the list as well.
ds_changed() {
  local old="$1" new="$2" name rest store db stores=() dbs=()
  [[ -f "$old" && -f "$new" ]] || return 0
  while read -r name rest; do
    [[ -n "$name" ]] || continue
    if [[ "$(ds_image_of "$old" "$name")" != "$(ds_image_of "$new" "$name")" ]]; then
      stores+=("$name"); echo "$name"
    fi
  done <<< "$DS_STORES$DS_ENGINE_STORES"
  while read -r name store db rest; do
    [[ -n "$name" ]] || continue
    [[ " ${stores[*]} " == *" $store "* || " ${dbs[*]} " == *" $store/$db "* ]] && continue
    if [[ "$(ds_image_of "$old" "$name")" != "$(ds_image_of "$new" "$name")" ]]; then
      dbs+=("$store/$db"); echo "$name"
    fi
  done <<< "$DS_APPS"
  return 0
}

# Inventory entries this sync does not snapshot (their owner is another
# workflow) that changed between two compose files.
ds_unsnapshotted() {
  local name
  while read -r name; do
    [[ "$(ds_app_row "$name" | awk '{ print $4 }')" == sync ]] || ds_is_store "$name" || echo "$name"
  done < <(ds_changed "$1" "$2")
}

# Size in bytes of the data mounts behind a store's running container; 0 for
# an engine without any (rabbitmq, redis-ephemeral).
ds_volume_bytes() {
  local cid p src n total=0 paths
  paths="$(ds_paths "$(ds_engine "$1")")"
  [[ -n "$paths" ]] || { echo 0; return 0; }
  cid="$(ds_compose ps -q "$1")" && [[ -n "$cid" ]] || return 1
  for p in $paths; do
    src="$(docker inspect --format "{{range .Mounts}}{{if eq .Destination \"$p\"}}{{.Source}}{{end}}{{end}}" "$cid")" \
      && [[ -n "$src" ]] || return 1
    # A named volume lives under /var/lib/docker, readable for root only.
    n="$(sudo -n du -sb "$src" | awk '{ print $1 }')" && [[ "$n" =~ ^[0-9]+$ ]] || return 1
    total=$(( total + n ))
  done
  echo "$total"
}

# The image of every container, running or exited, of the given services.
ds_container_images() {
  local cids
  cids="$(ds_compose ps -a -q "$@" 2>/dev/null)" && [[ -n "$cids" ]] || return 0
  # shellcheck disable=SC2086 # one word per container id
  docker inspect --format '{{.Config.Image}}' $cids 2>/dev/null || true
}

# ds_baseline <target> <db> <dump> <schema.table>...: one full pass over the
# archive, "<db> <table> <rows>" per probe table, "MISSING" for a table without
# a COPY block (an empty table still has one). COPY text escapes newlines in
# values, so one line is one row; a header only counts outside a block. Fails
# when pg_restore cannot read the whole archive.
ds_baseline() {
  local t="$1" db="$2" dump="$3"
  shift 3
  ds_x "$t" pg_restore --data-only -f - < "$dump" \
    | awk -v db="$db" -v tables="$*" '
        !inb && /^COPY / { inb = 1; name = $2; gsub(/"/, "", name)
                           c = index(" " tables " ", " " name " ") > 0
                           if (c) { seen[name] = 1; n[name] = 0 }
                           next }
        inb && /^\\\.$/ { inb = 0; c = 0; next }
        c { n[name]++ }
        END { k = split(tables, w, " ")
              for (i = 1; i <= k; i++) print db, w[i], (w[i] in seen) ? n[w[i]] : "MISSING" }'
  return "${PIPESTATUS[0]}"
}

# stdin from /dev/null: exec forwards stdin, and inside ds_compare's read loop
# it would swallow the remaining baseline lines.
ds_count_live() {
  local t="$1" su="$2" db="$3" table="$4"
  ds_x "$t" psql -U "$su" -d "$db" -v ON_ERROR_STOP=1 -Atc \
    "select count(*) from \"${table%%.*}\".\"${table#*.}\"" < /dev/null
}

ds_ledger() {
  printf '%s\tdata\t%s\t%s\t%s\t%s\t%s\n' "$(rb_now)" "$1" "$2" "$3" "$RB_TO" "$4" >> "$RB_LEDGER"
}

# --- other engines (S8b) ----------------------------------------------------

ds_env_of() {
  docker inspect --format '{{range .Config.Env}}{{println .}}{{end}}' "$1" 2>/dev/null | sed -n "s/^$2=//p" | head -n1
}

# ds_rcli <target> <command...>: redis-cli in a Redis or FalkorDB container.
# redis and vexa12-redis take their password as --requirepass on the command
# line (backup.sh reads it from the same place); it is handed over in the
# environment, never as an argument.
ds_rcli() {
  local cid pw
  cid="$(ds_ctr "$1")" && [[ -n "$cid" ]] || return 1
  shift
  pw="$(docker inspect --format '{{range .Config.Cmd}}{{println .}}{{end}}' "$cid" 2>/dev/null \
    | grep -A1 -x -- --requirepass | tail -n1 || true)"
  if [[ -n "$pw" ]]; then
    REDISCLI_AUTH="$pw" docker exec -e REDISCLI_AUTH "$cid" redis-cli "$@" < /dev/null
  else
    docker exec "$cid" redis-cli "$@" < /dev/null
  fi
}

# ds_curl <target> <port> <auth-header-prefix> <token-env> <method> <path> [body]:
# HTTP from a curl container in the store's network namespace, so it works for
# a throwaway on --network none and for images without an HTTP client. The
# token comes from the store's own environment and travels in the environment.
ds_curl() {
  local cid
  cid="$(ds_ctr "$1")" && [[ -n "$cid" ]] || return 1
  T="$(ds_env_of "$cid" "$4")" H="$3" docker run --rm --network "container:$cid" -e T -e H \
    --entrypoint sh "$DS_CURL_IMAGE" -c \
    'curl -fsS --max-time 60 -X "$1" -H "$H$T" -H "Content-Type: application/json" -d "$3" "http://127.0.0.1:$0$2"' \
    "$2" "$5" "$6" "${7:-}"
}
ds_qdrant() { ds_curl "$1" 6333 "api-key: " QDRANT__SERVICE__API_KEY "$2" "$3" "${4:-}"; }
ds_garage() { ds_curl "$1" 3903 "Authorization: Bearer " GARAGE_ADMIN_TOKEN GET "$2"; }
# Meilisearch has wget and its key in its own environment. A throwaway has no
# key, and then any Authorization header is refused with 403 (v1.55.0).
ds_meili() {
  local cid
  cid="$(ds_ctr "$1")" && [[ -n "$cid" ]] || return 1
  docker exec "$cid" sh -c 'if [ -n "${MEILI_MASTER_KEY:-}" ]; then
      exec wget -qO- --header="Authorization: Bearer $MEILI_MASTER_KEY" "http://127.0.0.1:7700$1"; fi
    exec wget -qO- "http://127.0.0.1:7700$1"' sh "$2" < /dev/null
}
# ds_mongo <target> <js>: the root user from the container's own environment;
# a throwaway runs without auth.
ds_mongo() {
  local cid
  cid="$(ds_ctr "$1")" && [[ -n "$cid" ]] || return 1
  JS="$2" docker exec -e JS "$cid" sh -c 'if [ -n "${MONGO_INITDB_ROOT_USERNAME:-}" ]; then
      set -- -u "$MONGO_INITDB_ROOT_USERNAME" -p "$MONGO_INITDB_ROOT_PASSWORD" --authenticationDatabase admin; fi
    exec mongosh --quiet "$@" --eval "$JS"' < /dev/null
}
DS_MONGO_FCV='print(db.adminCommand({getParameter: 1, featureCompatibilityVersion: 1}).featureCompatibilityVersion.version)'
# The CLI as the rabbitmq user: as root it can write .erlang.cookie before the
# server does, and the server then dies on eacces (seen on rabbitmq:4.4.0-alpine).
ds_rmq() {
  local cid
  cid="$(ds_ctr "$1")" && [[ -n "$cid" ]] || return 1
  shift
  docker exec -u rabbitmq "$cid" "$@" < /dev/null
}
# import_definitions does not read stdin, so the file goes in first.
ds_rmq_import() {
  local cid
  cid="$(ds_ctr "$1")" && [[ -n "$cid" ]] || return 1
  docker exec -i -u rabbitmq "$cid" sh -c 'cat > /tmp/definitions.json && rabbitmqctl -q import_definitions /tmp/definitions.json' < "$2"
}

ds_ready() {
  local t="$1"
  case "$2" in
    qdrant) ds_qdrant "$t" GET /readyz > /dev/null 2>&1 ;;
    meilisearch) ds_meili "$t" /health 2>/dev/null | grep -q '"available"' ;;
    mongo) [[ "$(ds_mongo "$t" 'print(db.adminCommand({ping: 1}).ok)' 2>/dev/null)" == 1 ]] ;;
    redis|redis-ephemeral|falkordb) [[ "$(ds_rcli "$t" PING 2>/dev/null)" == PONG ]] ;;
    garage) ds_garage "$t" /health > /dev/null 2>&1 ;;
    rabbitmq) ds_rmq "$t" rabbitmq-diagnostics -q check_running > /dev/null 2>&1 ;;
    *) return 1 ;;
  esac
}
# Up to 10 minutes: FalkorDB took 104 s to load its 1.1 GB dump on core-01
# (2026-10-23) and answers LOADING until then. A throwaway that exited never
# answers (it has no restart policy), so that ends the wait: Meilisearch 1.56
# exits 1 on a 1.55 database without MEILI_UPGRADE_DB, which kept a shadow
# waiting the full 616 s in the local proof of 2026-10-29.
ds_wait_ready() {
  local i
  for (( i = 0; i < 300; i++ )); do
    ds_ready "$1" "$2" && return 0
    [[ "$1" != ctr:* || "$(docker inspect --format '{{.State.Running}}' "${1#ctr:}" 2>/dev/null)" == true ]] || return 1
    sleep 2
  done
  return 1
}

# ds_facts <target> <engine>: sorted lines "<thing> <name>" and "count <name> <n>".
# Every call is checked: a failing call inside a pipeline or a for-list would
# otherwise read as a store with nothing in it.
ds_facts() {
  local t="$1" out n c i
  case "$2" in
    qdrant)
      n="$(ds_qdrant "$t" GET /collections \
        | python3 -c 'import json, sys; [print(c["name"]) for c in json.load(sys.stdin)["result"]["collections"]]')" || return 1
      for n in $n; do
        c="$(ds_qdrant "$t" POST "/collections/$n/points/count" '{"exact": true}' \
          | python3 -c 'import json, sys; print(json.load(sys.stdin)["result"]["count"])')" || return 1
        out+="collection $n"$'\n'"count $n $c"$'\n'
      done ;;
    meilisearch)
      # Tasks left in the copied queue resume on start; count once they are done.
      for (( i = 0; i < 150; i++ )); do
        ds_meili "$t" '/tasks?statuses=enqueued,processing&limit=1' 2>/dev/null \
          | python3 -c 'import json, sys; sys.exit(json.load(sys.stdin)["total"] != 0)' 2>/dev/null && break
        sleep 2
      done
      (( i < 150 )) || { echo "::error::meilisearch tasks still processing after 300s" >&2; return 1; }
      out="$(ds_meili "$t" /stats | python3 -c '
import json, sys
for uid, s in json.load(sys.stdin)["indexes"].items():
    print("index", uid)
    print("count", uid, s["numberOfDocuments"])')" || return 1 ;;
    mongo)
      # Document counts per collection, so empty collections fail the probe.
      # config holds session and transaction bookkeeping that changes on its own.
      out="$(ds_mongo "$t" 'print("fcv " + db.adminCommand({getParameter: 1, featureCompatibilityVersion: 1}).featureCompatibilityVersion.version);
        db.adminCommand({listDatabases: 1, nameOnly: true}).databases.forEach(function (d) {
          if (d.name === "local") return;
          print("db " + d.name);
          if (d.name === "config") return;
          var s = db.getSiblingDB(d.name);
          s.getCollectionInfos({type: "collection"}, {nameOnly: true}).forEach(function (c) {
            if (c.name.indexOf("system.") === 0) return;
            print("collection " + d.name + "." + c.name);
            print("count " + d.name + "." + c.name + " " + s.getCollection(c.name).countDocuments({}));
          });
        });')" || return 1 ;;
    redis|falkordb)
      n="$(ds_rcli "$t" INFO keyspace)" && [[ "$n" == *Keyspace* ]] || return 1
      # Keys without a TTL only: an expiring key can lapse between snapshot and count.
      out="$(tr -d '\r' <<< "$n" | awk -F'[:=,]' '/^db[0-9]+:/ { k += $3 - $5 } END { print "count keys", k + 0 }')"
      if [[ "$2" == falkordb ]]; then
        n="$(ds_rcli "$t" GRAPH.LIST)" || return 1
        out+=$'\n'"$(tr -d '\r' <<< "$n" | sed '/^$/d; s/^/graph /')"
      fi ;;
    garage)
      out="$(ds_garage "$t" /v2/ListBuckets | python3 -c '
import json, sys
for b in json.load(sys.stdin):
    print("bucket", b["id"])
    for a in b.get("globalAliases", []):
        print("alias", a)')" || return 1
      for n in $(awk '$1 == "bucket" { print $2 }' <<< "$out"); do
        c="$(ds_garage "$t" "/v2/GetBucketInfo?id=$n" | python3 -c 'import json, sys; print(json.load(sys.stdin)["objects"])')" || return 1
        out+=$'\n'"count $n $c"
      done ;;
    rabbitmq)
      n="$(ds_rmq "$t" rabbitmqctl -q list_queues --no-table-headers name)" || return 1
      out="$(sed '/^$/d; s/^/queue /' <<< "$n")" ;;
    *) return 1 ;;
  esac
  sed '/^$/d' <<< "$out" | LC_ALL=C sort
}

# ds_compare_facts <baseline> <live> <exact|atleast>
ds_compare_facts() {
  if [[ "$3" == exact ]]; then
    diff "$1" "$2" > /dev/null && { echo "probe: $(wc -l < "$1" | tr -d ' ') facts, as in the snapshot"; return 0; }
    echo "::error::probe after restore differs from the snapshot (< snapshot, > restored):"
    diff "$1" "$2" | grep '^[<>]'
    return 1
  fi
  awk '
    NR == FNR { if ($1 == "count") have[$1 " " $2] = $3; else have[$0] = 1; next }
    $1 == "count" { k = $1 " " $2
                    if (!(k in have) || have[k] + 0 < $3 + 0) { print "::error::probe: " $2 " has " (k in have ? have[k] : "nothing") ", the snapshot " $3; bad = 1 }
                    else print "probe: " $2 " " have[k] ", the snapshot " $3
                    next }
    !($0 in have) { print "::error::probe: " $0 " is missing"; bad = 1 }
    END { exit bad }' "$2" "$1"
}

# ds_throwaway <dir> <container> [image]: the snapshot in a throwaway container
# of the image that wrote it (or <image>, the shadow), on volumes of its own;
# prints its facts, removes it all.
ds_throwaway() {
  local dir="$1" ctr="$2" engine kind image p i=0 rc=0 vols=() mounts=() run=() args=()
  engine="$(ds_dir_engine "$dir")"; kind="$(ds_kind "$engine")"; image="${3:-$(ds_meta "$dir" store_image)}"
  for p in $(ds_paths "$engine"); do
    docker volume create --label klai.adhoc=datastore-restore "$ctr-$i" > /dev/null || rc=1
    vols+=("$ctr-$i"); mounts+=(-v "$ctr-$i:$p")
    i=$(( i + 1 ))
  done
  case "$kind" in
    volume) (( rc )) || docker run --rm "${mounts[@]}" -v "$dir:/in:ro" "$DS_TOOL_IMAGE" tar -xf /in/volume.tar -C / || rc=1 ;;
    rdb) (( rc )) || docker run --rm "${mounts[@]}" -v "$dir:/in:ro" "$DS_TOOL_IMAGE" cp /in/dump.rdb "$p/dump.rdb" || rc=1 ;;
  esac
  case "$engine" in
    # MEILI_UPGRADE_DB as the new compose file sets it, so a shadow starts the
    # new minor the way the live store will (a no-op on the image that wrote it).
    meilisearch)
      run=(-e MEILI_DB_PATH=/meili_data -e MEILI_ENV=development -e MEILI_NO_ANALYTICS=true)
      [[ -z "$(ds_meta "$dir" meili_upgrade_db)" ]] || run+=(-e "MEILI_UPGRADE_DB=$(ds_meta "$dir" meili_upgrade_db)") ;;
    redis) args=(redis-server --appendonly no) ;;
    falkordb) run=(-e BROWSER=0) ;;
    # Fresh secrets: the node key and layout come from the snapshot, the RPC
    # secret is only shared with the CLI inside the same container.
    garage) run=(-v "$RB_DIR/garage/garage.toml:/etc/garage.toml:ro" -e GARAGE_RPC_SECRET -e GARAGE_ADMIN_TOKEN) ;;
  esac
  if (( rc == 0 )); then
    GARAGE_RPC_SECRET="$(od -An -tx1 -N32 /dev/urandom | tr -d ' \n')" GARAGE_ADMIN_TOKEN="$(od -An -tx1 -N32 /dev/urandom | tr -d ' \n')" \
      docker run -d --name "$ctr" --network none --label klai.adhoc=datastore-restore "${DS_THROWAWAY_LIMITS[@]}" \
      "${mounts[@]}" "${run[@]}" "$image" "${args[@]}" > /dev/null || rc=1
  fi
  if (( rc == 0 )) && ! ds_wait_ready "ctr:$ctr" "$engine"; then
    echo "::error::throwaway $ctr ($image) did not become ready; its last log lines:" >&2
    docker logs --tail 20 "$ctr" >&2 2>&1
    rc=1
  fi
  if (( rc == 0 )) && [[ "$kind" == definitions ]]; then
    ds_rmq_import "ctr:$ctr" "$dir/definitions.json" > /dev/null || rc=1
  fi
  (( rc )) || ds_facts "ctr:$ctr" "$engine" || rc=1
  docker rm -f "$ctr" > /dev/null 2>&1 || true
  (( ${#vols[@]} == 0 )) || docker volume rm "${vols[@]}" > /dev/null 2>&1 || true
  return "$rc"
}

# A cold copy: the store is stopped so the files are consistent, and started
# again on the same image whatever the copy did; with "stopped" a good copy
# leaves it stopped, for the cutover to start the new image on those files.
ds_cold_copy() {
  local name="$1" engine="$2" dir="$3" cid p rc=0 rel=()
  cid="$(ds_ctr "$name")" && [[ -n "$cid" ]] || return 1
  for p in $(ds_paths "$engine"); do rel+=("${p#/}"); done
  echo "snapshot: $name stopped for a cold copy of $(ds_paths "$engine")"
  # A stop that reports an error may still have stopped the store.
  ds_compose stop -t 120 "$name" || { ds_compose start "$name"; return 1; }
  docker run --rm --volumes-from "$cid:ro" -v "$dir:/out" "$DS_TOOL_IMAGE" tar -cf /out/volume.tar -C / "${rel[@]}" || rc=1
  if (( rc )) || [[ "${4:-}" != stopped ]]; then ds_compose start "$name" || rc=1; fi
  return "$rc"
}

# BGSAVE and wait for it on LASTSAVE, as backup.sh does (a fixed sleep copied a
# three-hour-old FalkorDB dump on 2026-10-22).
ds_bgsave() {
  local t="$1" before now="" reply i
  for (( i = 0; i < 900; i++ )); do
    ds_rcli "$t" INFO persistence | grep -q '^rdb_bgsave_in_progress:0' && break
    sleep 1
  done
  before="$(ds_rcli "$t" LASTSAVE)" && [[ "$before" =~ ^[0-9]+$ ]] || return 1
  # LASTSAVE has one-second resolution.
  sleep 1
  reply="$(ds_rcli "$t" BGSAVE)"
  [[ "$reply" == *"Background saving started"* ]] || { echo "::error::$t: BGSAVE not started (${reply:-no reply})"; return 1; }
  for (( i = 0; i < 900; i++ )); do
    now="$(ds_rcli "$t" LASTSAVE)" && [[ "$now" =~ ^[0-9]+$ ]] || return 1
    (( now > before )) && break
    sleep 1
  done
  (( now > before )) || { echo "::error::$t: BGSAVE did not finish within 900s"; return 1; }
  ds_rcli "$t" INFO persistence | grep -q '^rdb_last_bgsave_status:ok'
}

# ds_take <store> <dir> [stopped]: an engine store's data into <dir>.
ds_take() {
  local name="$1" dir="$2" engine kind cid
  engine="$(ds_engine "$name")"; kind="$(ds_kind "$engine")"
  case "$kind" in
    volume) ds_cold_copy "$name" "$engine" "$dir" "${3:-}" ;;
    rdb)
      cid="$(ds_ctr "$name")" && [[ -n "$cid" ]] \
        && ds_bgsave "$name" \
        && docker cp "$cid:$(ds_paths "$engine")/dump.rdb" "$dir/dump.rdb" ;;
    definitions)
      ds_rmq "$name" rabbitmqctl -q export_definitions - > "$dir/definitions.json" \
        && python3 -c 'import json, sys; json.load(open(sys.argv[1]))' "$dir/definitions.json" ;;
  esac || { echo "::error::$kind snapshot of $name failed"; return 1; }
}

ds_snapshot_engine() {
  local name="$1" old="$2" new="$3" engine kind dir
  engine="$(ds_engine "$name")"; kind="$(ds_kind "$engine")"
  dir="$DS_BACKUP_ROOT/pre-$name-$(date -u +%Y%m%dT%H%M%SZ)"
  (umask 077 && mkdir -p "$dir") || return 1
  {
    echo "name=$name"
    echo "store=$name"
    echo "engine=$engine"
    echo "consumers=$(awk -v s="$name" '$1 == s { $1 = ""; $2 = ""; print }' <<< "$DS_ENGINE_STORES" | xargs)"
    echo "old_image=$(ds_image_of "$old" "$name")"
    echo "new_image=$(ds_image_of "$new" "$name")"
    echo "store_image=$(ds_image_of "$old" "$name")"
    [[ "$engine" != meilisearch ]] || echo "meili_upgrade_db=$(ds_service_env "$new" "$name" MEILI_UPGRADE_DB)"
  } > "$dir/meta"
  : > "$dir/baseline.facts"
  ds_take "$name" "$dir" || return 1
  # The baseline comes from the snapshot itself, restored into a throwaway of
  # the running image: exact without stopping any writer, and proof that the
  # snapshot reads back before the sync changes anything.
  if [[ "$kind" != none ]]; then
    echo "snapshot: reading the baseline of $name back from a throwaway container"
    ds_throwaway "$dir" "klai-datastore-baseline-$name" > "$dir/baseline.facts" \
      || { echo "::error::the $kind snapshot of $name does not read back"; return 1; }
  fi
  ds_ledger "$name" "$dir" "$(ds_image_of "$new" "$name")" snapshot
  DS_SNAPSHOTS+=("$dir")
  echo "snapshot of $name taken: $dir ($(wc -l < "$dir/baseline.facts" | tr -d ' ') facts)"
}

# The facts a live store is compared with after a cutover or a put-back: those
# read just before the fresh snapshot, or the first baseline before a cutover.
ds_ref_facts() { if [[ -s "$1/cutover.facts" ]]; then echo "$1/cutover.facts"; else echo "$1/baseline.facts"; fi; }

# The live half of an engine restore; consumers are already stopped. The store
# is stopped, its data mounts emptied and filled from the snapshot through the
# stopped container's volumes, and it is started on the previous image (the
# previous compose file is back by now).
ds_put_back() {
  local dir="$1" store engine kind cid live rc
  store="$(ds_meta "$dir" store)"; engine="$(ds_dir_engine "$dir")"; kind="$(ds_kind "$engine")"
  cid="$(ds_ctr "$store")"
  ds_compose stop -t 120 "$store" || return 1
  case "$kind" in
    volume)
      [[ -n "$cid" ]] || return 1
      # shellcheck disable=SC2046 # one word per path
      docker run --rm --volumes-from "$cid" -v "$dir:/in:ro" "$DS_TOOL_IMAGE" sh -c \
        'for p in "$@"; do find "$p" -mindepth 1 -delete || exit 1; done; tar -xf /in/volume.tar -C /' \
        sh $(ds_paths "$engine") || return 1 ;;
    rdb)
      [[ -n "$cid" ]] || return 1
      # With appendonly on, Redis loads the AOF and ignores dump.rdb, so the
      # dump becomes the base of a fresh manifest (Redis 7+ multi-part AOF).
      docker run --rm --volumes-from "$cid" -v "$dir:/in:ro" "$DS_TOOL_IMAGE" sh -c '
        cd "$1" || exit 1
        if [ -d appendonlydir ]; then
          rm -rf appendonlydir && mkdir appendonlydir \
            && cp /in/dump.rdb appendonlydir/appendonly.aof.1.base.rdb \
            && : > appendonlydir/appendonly.aof.1.incr.aof \
            && printf "file appendonly.aof.1.base.rdb seq 1 type b\nfile appendonly.aof.1.incr.aof seq 1 type i\n" \
              > appendonlydir/appendonly.aof.manifest || exit 1
        fi
        cp /in/dump.rdb dump.rdb && chown -R "$(stat -c %u:%g .)" .' sh "$(ds_paths "$engine")" || return 1 ;;
  esac
  ds_compose up -d --no-deps --force-recreate "$store" || return 1
  ds_wait_ready "$store" "$engine" || { echo "::error::$store did not come back on its previous image"; return 1; }
  if [[ "$kind" == definitions ]]; then
    ds_rmq_import "$store" "$dir/definitions.json" || return 1
  fi
  [[ "$kind" != none ]] || return 0
  live="$(mktemp)"
  ds_facts "$store" "$engine" > "$live" && ds_compare_facts "$(ds_ref_facts "$dir")" "$live" atleast
  rc=$?
  rm -f "$live"
  return "$rc"
}

# Restoring a Mongo snapshot after the FCV was raised would silently throw that
# away; ds_settle raises it only after the window, so inside it a changed FCV
# was someone else's step. Called before anything is restored.
ds_fcv_unchanged() {
  local dir want got
  for dir in "${DS_SNAPSHOTS[@]}"; do
    [[ "$(ds_dir_engine "$dir")" == mongo ]] || continue
    want="$(sed -n 's/^fcv //p' "$dir/baseline.facts")"
    got="$(ds_mongo "$(ds_meta "$dir" store)" "$DS_MONGO_FCV" 2>/dev/null)" || got=""
    if [[ -n "$got" && "$got" != "$want" ]]; then
      echo "::error::mongodb runs featureCompatibilityVersion $got, the snapshot $dir has $want: it was changed after the upgrade, and a restore would undo that together with every write since. Nothing restored."
      return 1
    fi
  done
}

# ds_dump_dbs <dir> <store> <superuser> <databases>: one pg_dump -Fc per
# database, and its probe counts read back from the dump into baseline.counts.
ds_dump_dbs() {
  local dir="$1" store="$2" su="$3" db n
  : > "$dir/baseline.counts"
  for db in $4; do
    echo "snapshot: pg_dump -Fc $store/$db -> $dir/$db.dump"
    ds_x "$store" pg_dump -U "$su" -Fc -d "$db" > "$dir/$db.dump" \
      || { echo "::error::pg_dump of $store/$db failed"; return 1; }
    # shellcheck disable=SC2046 # one word per table
    n="$(ds_baseline "$store" "$db" "$dir/$db.dump" $(ds_probe_tables "$store" "$db"))" \
      || { echo "::error::the dump of $store/$db does not read back"; return 1; }
    if grep -q ' MISSING$' <<< "$n"; then
      echo "::error::probe table $(awk '$3 == "MISSING" { print $2 }' <<< "$n" | paste -sd' ' -) not in the dump of $store/$db; fix DS_PROBES in datastore-rollback.sh"
      return 1
    fi
    [[ -z "$n" ]] || printf '%s\n' "$n" >> "$dir/baseline.counts"
  done
}

# ds_snapshot <name> <old-compose> <new-compose>: dump, baseline, ledger; for
# a Postgres major only the plan, the dump waits for ds_major_cutover.
ds_snapshot() {
  local name="$1" old="$2" new="$3" store su dbs consumers storecons dir app cid img oldm oldd newm="" newd=""
  app="$(ds_app_row "$name")"
  if [[ -z "$app" && "$(ds_engine "$name")" != postgres ]]; then
    ds_snapshot_engine "$@"
    return
  fi
  if [[ -n "$app" ]]; then
    read -r _ store dbs _ consumers <<< "$app"
  else
    store="$name"
  fi
  read -r _ su storecons <<< "$(ds_store_row "$store")"
  if [[ -z "$app" ]]; then
    consumers="$storecons"
    dbs="$(ds_x "$store" psql -U "$su" -d postgres -v ON_ERROR_STOP=1 -Atc \
      "select datname from pg_database where not datistemplate and datname <> 'postgres' order by 1" | tr '\n' ' ')" \
      || { echo "::error::could not list the databases of $store"; return 1; }
  fi
  # The major and data directory of what runs and of what will run, read from
  # the container and the image rather than from the tag. The restore
  # rehearsal snapshots without an image change and skips this.
  img="$(ds_image_of "$new" "$store")"
  if [[ -z "$app" && "$(ds_image_of "$old" "$store")" != "$img" ]]; then
    cid="$(ds_ctr "$store")"
    oldm="$(ds_env_of "$cid" PG_MAJOR)"; oldd="$(ds_env_of "$cid" PGDATA)"
    if docker pull -q "$img" > /dev/null; then
      newm="$(ds_image_env "$img" PG_MAJOR)"; newd="$(ds_image_env "$img" PGDATA)"
    fi
    if [[ -z "$oldm" || -z "$newm" ]]; then
      echo "::error::could not read PG_MAJOR of the running $store or of $img"
      return 1
    fi
    if [[ "$oldm" == "$newm" ]]; then
      newd=""
    elif ! [[ "$oldd" == /var/lib/postgresql/?* && "$newd" == /var/lib/postgresql/?* && "$oldd" != "$newd" ]]; then
      echo "::error::$store $oldm -> $newm: the new major needs a data directory of its own inside the volume at /var/lib/postgresql, beside the old one ($oldd); $img uses $newd"
      return 1
    fi
  fi
  dir="$DS_BACKUP_ROOT/pre-$name-$(date -u +%Y%m%dT%H%M%SZ)"
  # A dump holds customer data: readable for the deploy user only.
  (umask 077 && mkdir -p "$dir") || return 1
  {
    echo "name=$name"
    echo "store=$store"
    echo "engine=postgres"
    echo "superuser=$su"
    echo "databases=$dbs"
    echo "consumers=$consumers"
    echo "old_image=$(ds_image_of "$old" "$name")"
    echo "new_image=$(ds_image_of "$new" "$name")"
    echo "store_image=$(ds_image_of "$old" "$store")"
    [[ -z "$newd" ]] || printf 'pg_major=%s %s\nold_pgdata=%s\nnew_pgdata=%s\n' "$oldm" "$newm" "$oldd" "$newd"
  } > "$dir/meta"
  ds_dump_dbs "$dir" "$store" "$su" "$dbs" || return 1
  if [[ -n "$newd" ]]; then
    echo "snapshot: $store $oldm -> $newm is a major; this dump is the shadow's, restored into a throwaway of $newm, and the cutover dumps again with the consumers stopped"
    ds_pg_extensions "$dir" "$store" "$su" || { echo "::error::could not list the extensions of $store"; return 1; }
  fi
  ds_ledger "$name" "$dir" "$(ds_image_of "$new" "$name")" snapshot
  DS_SNAPSHOTS+=("$dir")
  echo "snapshot of $name taken: $dir"
}

# ds_disk_ok <name>...: 0 when the backup root has twice the data volume of
# every named store (an app row counts its store) free (AT-c2). A Postgres
# major covers the same room: the dump (at most the data, compressed and
# without indexes) and the new major's directory (about the data) beside the
# old one, which stays in the volume for the window.
ds_disk_ok() {
  local name store bytes need=0 free
  for name in "$@"; do
    store="$name"; [[ -n "$(ds_app_row "$name")" ]] && store="$(ds_app_row "$name" | awk '{ print $2 }')"
    bytes="$(ds_volume_bytes "$store")" && [[ "$bytes" =~ ^[0-9]+$ ]] \
      || { echo "::error::could not size the volume of $store"; return 1; }
    need=$(( need + 2 * bytes ))
  done
  free="$(df -PB1 "$DS_BACKUP_ROOT" | awk 'NR == 2 { print $4 }')"
  if ! [[ "$free" =~ ^[0-9]+$ ]] || (( free < need )); then
    echo "::error::not enough disk for a snapshot: ${free:-?} bytes free on $DS_BACKUP_ROOT, $need needed (twice the volume size of $*); nothing changed"
    return 1
  fi
}

ds_snapshot_changed() {
  local old="$1" new="$2" names name
  mapfile -t names < <(ds_changed "$old" "$new")
  local own=()
  for name in "${names[@]}"; do
    { ds_is_store "$name" || [[ "$(ds_app_row "$name" | awk '{ print $4 }')" == sync ]]; } && own+=("$name")
  done
  (( ${#own[@]} > 0 )) || return 0
  echo "image change of a data store or a self-migrating app: ${own[*]}"
  mkdir -p "$DS_BACKUP_ROOT" || rb_finish "${own[0]}" snapshot not_started
  ds_disk_ok "${own[@]}" || rb_finish "${own[0]}" disk-headroom not_started
  for name in "${own[@]}"; do
    if [[ -f "$DS_DRY_FLAG" && "$(cat "$DS_DRY_FLAG")" == "$name" ]]; then
      rm -f "$DS_DRY_FLAG"
      if [[ -n "$(ds_app_row "$name")" ]]; then
        echo "::warning::datastore dry mode is for stores only: $name migrates its own database and has no shadow. Flag removed; this sync runs normally."
      else
        echo "::warning::datastore dry mode for $name: its shadow runs and is then forced red, so this sync ends shadow_failed with the live store untouched"
        DS_DRY="$name"
      fi
    fi
    ds_snapshot "$name" "$old" "$new" || rb_finish "$name" snapshot not_started
  done
  local now dir
  # Stores only: an app's migration needs the app itself, which no throwaway runs.
  for dir in "${DS_SNAPSHOTS[@]}"; do
    [[ "$(ds_meta "$dir" name)" == "$(ds_meta "$dir" store)" ]] || continue
    ds_shadow "$dir" || rb_finish "$(ds_meta "$dir" name)" datastore-shadow shadow_failed
  done
  # The window starts after the last dump, just before the first file changes.
  now="$(ds_now_epoch)"
  for dir in "${DS_SNAPSHOTS[@]}"; do echo "epoch=$now" >> "$dir/meta"; done
}

# ds_shadow <dir> [image]: the snapshot in a throwaway of the new image (or
# <image>), compared exactly with its baseline. See "Shadow, then cutover".
ds_shadow() {
  local dir="$1" image="${2:-$(ds_meta "$1" new_image)}" name t0 rc=0
  name="$(ds_meta "$dir" name)"; t0="$(ds_now_epoch)"
  if [[ "$(ds_kind "$(ds_dir_engine "$dir")")" == none ]]; then
    echo "shadow: $name keeps no state; nothing to prove on a copy"
  else
    echo "shadow: $name on $image, in a throwaway from $dir"
    ds_restore_into "$dir" "klai-datastore-shadow-$name" "$image" || rc=1
  fi
  if (( rc == 0 )) && [[ "${DS_DRY:-}" == "$name" ]]; then
    echo "shadow: dry mode, forced red"
    rc=1
  fi
  echo "shadow: $name $( ((rc == 0)) && echo green || echo red) on $image after $(( $(ds_now_epoch) - t0 ))s"
  ds_ledger "$name" "$dir" "$image" "$( ((rc == 0)) && echo shadow_green || echo shadow_failed)"
  return "$rc"
}

# ds_fresh_snapshot <dir>: the cutover's snapshot, consumers stopped. It
# replaces the first one as the restore source; the facts read just before it
# are the probe's reference (cutover.facts; a dump carries its own counts).
ds_fresh_snapshot() {
  local dir="$1" name store engine kind
  name="$(ds_meta "$dir" name)"; store="$(ds_meta "$dir" store)"
  engine="$(ds_dir_engine "$dir")"; kind="$(ds_kind "$engine")"
  case "$kind" in
    dump) ds_dump_dbs "$dir" "$store" "$(ds_meta "$dir" superuser)" "$(ds_meta "$dir" databases)" || return 1 ;;
    volume|rdb|definitions)
      ds_facts "$store" "$engine" > "$dir/cutover.facts" || { echo "::error::$store does not answer its $engine probe before the cutover"; return 1; }
      ds_take "$store" "$dir" stopped || return 1 ;;
  esac
  ds_set_meta "$dir" epoch "$(ds_now_epoch)"
  ds_ledger "$name" "$dir" "$(ds_meta "$dir" new_image)" cutover
}

# The live store right after the switch: it answers, and its facts are at least
# the fresh snapshot's (Mongo's fcv line included, so an FCV change is red).
# RabbitMQ only answers: its new node starts empty and the consumers declare
# their queues.
ds_probe_cutover() {
  local dir="$1" store engine live rc
  store="$(ds_meta "$dir" store)"; engine="$(ds_dir_engine "$dir")"
  if [[ "$engine" == postgres ]]; then
    ds_pg_wait "$store" "$(ds_meta "$dir" superuser)" && ds_compare "$dir" "$store" atleast
    return
  fi
  ds_wait_ready "$store" "$engine" || { echo "probe: $store does not answer on $(ds_meta "$dir" new_image)"; return 1; }
  case "$(ds_kind "$engine")" in
    none) ;;
    definitions) ds_facts "$store" "$engine" > /dev/null || { echo "probe: $store does not answer its $engine probe"; return 1; } ;;
    *)
      live="$(mktemp)"
      ds_facts "$store" "$engine" > "$live" && ds_compare_facts "$dir/cutover.facts" "$live" atleast
      rc=$?
      rm -f "$live"
      (( rc == 0 )) || return 1 ;;
  esac
  echo "probe: $store answers on $(ds_meta "$dir" new_image)"
}

# ds_store_cutover <dir>: a store other than a Postgres major onto its new
# image. Holds the host container lock from the consumer stop to the probe, as
# a restore does; compose-up.sh takes the same lock, so the consumers are
# recreated after it.
ds_store_cutover() {
  local dir="$1" name store consumers rc
  name="$(ds_meta "$dir" name)"; store="$(ds_meta "$dir" store)"; consumers="$(ds_meta "$dir" consumers)"
  (
    exec 8> "$DS_LOCK_FILE"
    flock -w 900 8 || { echo "::error::host container lock $DS_LOCK_FILE busy for 15 minutes"; exit 1; }
    # shellcheck disable=SC2086 # one word per service
    ds_compose stop $consumers || exit 1
    # Then the new image already ran on live data without a fresh snapshot,
    # and the red gate restores the first one.
    [[ "$(ds_container_images "$store")" == "$(ds_meta "$dir" store_image)" ]] \
      || { echo "::error::$store no longer runs $(ds_meta "$dir" store_image): something recreated it before the cutover"; exit 1; }
    ds_fresh_snapshot "$dir" || exit 1
    echo "cutover: $store starts $(ds_meta "$dir" new_image)"
    ds_compose up -d --no-deps --force-recreate "$store" || exit 1
    ds_probe_cutover "$dir"
  )
  rc=$?
  if (( rc == 0 )); then
    # shellcheck disable=SC2086 # one word per service
    "$RB_DIR/scripts/compose-up.sh" --no-pull --force-recreate $consumers || rc=1
  elif [[ "$(ds_container_images "$store")" == "$(ds_meta "$dir" store_image)" ]]; then
    # Never switched, so the restore finds nothing to restore; the store and
    # its consumers come back here, on the previous image and its own data.
    echo "cutover: $store failed before it switched; starting it and its consumers again"
    ds_compose start "$store" || true
    # shellcheck disable=SC2086 # one word per service
    "$RB_DIR/scripts/compose-up.sh" --no-pull --force-recreate $consumers || true
  fi
  (( rc )) || ds_ledger "$name" "$dir" "$(ds_meta "$dir" new_image)" deployed
  return "$rc"
}

# Recreate the consumers of a self-migrating app's database or of a Postgres
# major, then check that every probe table of every snapshotted database
# answers. Counts are not compared here: the new version is live and writing.
ds_probe() {
  local dir="$1" store su db table
  store="$(ds_meta "$dir" store)"; su="$(ds_meta "$dir" superuser)"
  if [[ "$(ds_meta "$dir" name)" == "$store" ]]; then
    # shellcheck disable=SC2046 # one word per service
    "$RB_DIR/scripts/compose-up.sh" --no-pull --force-recreate $(ds_meta "$dir" consumers) || return 1
  fi
  for db in $(ds_meta "$dir" databases); do
    for table in $(ds_probe_tables "$store" "$db"); do
      ds_count_live "$store" "$su" "$db" "$table" > /dev/null || { echo "probe: $store/$db $table does not answer"; return 1; }
    done
  done
  echo "probe: $store answers on every probe table"
}

# Up to 2 minutes, over TCP: the image's init server listens on the socket only,
# so a socket check would pass before initialisation has finished.
ds_pg_wait() {
  local i
  for (( i = 0; i < 60; i++ )); do
    ds_x "$1" pg_isready -h 127.0.0.1 -U "$2" > /dev/null 2>&1 < /dev/null && return 0
    sleep 2
  done
  echo "::error::$1 did not accept connections within 120s"
  return 1
}

# ds_pg_rm <store> <pgdata>: remove a data directory that is not the store's
# live one, through the volumes of its (stopped) container.
ds_pg_rm() {
  local cid
  [[ "$2" == /var/lib/postgresql/?* ]] || { echo "::error::refusing to remove $2: not inside /var/lib/postgresql"; return 1; }
  cid="$(ds_ctr "$1")" && [[ -n "$cid" ]] || return 1
  docker run --rm --volumes-from "$cid" "$DS_TOOL_IMAGE" rm -rf "$2"
}
# ds_pg_mv <store> <from> <to>: rename a data directory of a stopped store;
# fails when <from> is missing or <to> exists, so nothing is ever overwritten.
ds_pg_mv() {
  local cid
  cid="$(ds_ctr "$1")" && [[ -n "$cid" ]] || return 1
  docker run --rm --volumes-from "$cid" "$DS_TOOL_IMAGE" sh -c '[ -d "$1" ] && [ ! -e "$2" ] && mv "$1" "$2"' sh "$2" "$3" \
    || { echo "::error::could not move $2 to $3 in $1"; return 1; }
}

# ds_pg_extensions <dir> <target> <superuser>: every extension the snapshot's
# databases use, into meta.
ds_pg_extensions() {
  local dir="$1" db exts=""
  for db in $(ds_meta "$dir" databases); do
    exts+=" $(ds_x "$2" psql -U "$3" -d "$db" -v ON_ERROR_STOP=1 -Atc "select extname from pg_extension" < /dev/null)" || return 1
  done
  ds_set_meta "$dir" extensions "$(xargs -n1 <<< "$exts" | sort -u | xargs)"
}
# ds_pg_has_extensions <target> <superuser> <image> <extensions>
ds_pg_has_extensions() {
  local ext
  for ext in $4; do
    [[ "$(ds_x "$1" psql -U "$2" -d postgres -Atc "select 1 from pg_available_extensions where name = '$ext'" < /dev/null)" == 1 ]] \
      || { echo "probe: extension $ext of the old cluster is not in $3"; return 1; }
  done
}

# ds_major_cutover <dir>: see "Postgres majors" at the top. Holds the host
# container lock, as a restore does, so no deploy starts a consumer meanwhile.
ds_major_cutover() {
  local dir="$1" name store su consumers newd rc
  name="$(ds_meta "$dir" name)"; store="$(ds_meta "$dir" store)"; su="$(ds_meta "$dir" superuser)"
  consumers="$(ds_meta "$dir" consumers)"; newd="$(ds_meta "$dir" new_pgdata)"
  (
    exec 8> "$DS_LOCK_FILE"
    flock -w 900 8 || { echo "::error::host container lock $DS_LOCK_FILE busy for 15 minutes"; exit 1; }
    # shellcheck disable=SC2086 # one word per service
    ds_compose stop $consumers || exit 1
    # Anything that started the new image before now gave it an empty cluster.
    [[ "$(ds_container_images "$store")" == "$(ds_meta "$dir" store_image)" ]] \
      || { echo "::error::$store no longer runs $(ds_meta "$dir" store_image): something recreated it before the cutover; nothing dumped"; exit 1; }
    ds_dump_dbs "$dir" "$store" "$su" "$(ds_meta "$dir" databases)" || exit 1
    ds_x "$store" pg_dumpall -U "$su" --globals-only < /dev/null > "$dir/globals.sql" \
      || { echo "::error::pg_dumpall --globals-only of $store failed"; exit 1; }
    ds_pg_extensions "$dir" "$store" "$su" || exit 1
    # The window starts here, at the end of the dump.
    ds_set_meta "$dir" epoch "$(ds_now_epoch)"
    ds_ledger "$name" "$dir" "$(ds_meta "$dir" new_image)" cutover
    ds_compose stop -t 120 "$store" || exit 1
    # Left by an earlier attempt that was rolled back; the running image never opens it.
    ds_pg_rm "$store" "$newd" || exit 1
    ds_pg_mv "$store" "$(ds_meta "$dir" old_pgdata)" "$(ds_meta "$dir" old_pgdata).aside" || exit 1
    # From here on a red gate must rename it back, whatever image ran.
    ds_set_meta "$dir" moved_aside yes
    echo "cutover: $(ds_meta "$dir" old_pgdata) moved aside; $store starts $(ds_meta "$dir" new_image) on an empty $newd, whose entrypoint runs initdb with the compose environment"
    ds_compose up -d --no-deps --force-recreate "$store" || exit 1
    ds_pg_wait "$store" "$su" || exit 1
    ds_pg_has_extensions "$store" "$su" "$(ds_meta "$dir" new_image)" "$(ds_meta "$dir" extensions)" || exit 1
    # Roles are cluster-wide, outside every per-database dump. The init
    # scripts (deploy/postgres/init.sql) already created some, with placeholder
    # passwords; the dumped ALTER ROLE then sets the real hash.
    sed -E 's/^CREATE ROLE (.+);$/DO $$ BEGIN CREATE ROLE \1; EXCEPTION WHEN duplicate_object THEN NULL; END $$;/' "$dir/globals.sql" \
      | ds_x "$store" psql -U "$su" -d postgres -v ON_ERROR_STOP=1 -q > /dev/null \
      || { echo "::error::the roles of $store did not restore"; exit 1; }
    ds_restore_dbs "$dir" "$store" && ds_compare "$dir" "$store"
  )
  rc=$?
  if (( rc == 0 )); then
    ds_set_meta "$dir" migrated yes
    ds_ledger "$name" "$dir" "$(ds_meta "$dir" new_image)" migrated
  elif [[ -z "$(ds_meta "$dir" moved_aside)" ]]; then
    # The old directory never moved, so the restore finds the old image still
    # in place and restores nothing; the store and its consumers have to come
    # back here. After the lock: compose-up.sh takes the same one.
    echo "cutover: $store failed before its data directory moved; starting it and its consumers again"
    ds_compose start "$store" || true
    # shellcheck disable=SC2086 # one word per service
    "$RB_DIR/scripts/compose-up.sh" --no-pull --force-recreate $consumers || true
  fi
  return "$rc"
}

# Right after the compose file is replaced: one gate per store of this run. A
# Postgres major is ds_major_cutover, then the probe that recreates its
# consumers; any other store ds_store_cutover. Apps are left to the sweep.
ds_cutover() {
  local dir name
  for dir in "${DS_SNAPSHOTS[@]}"; do
    name="$(ds_meta "$dir" name)"
    [[ "$name" == "$(ds_meta "$dir" store)" ]] || continue
    if [[ -n "$(ds_meta "$dir" new_pgdata)" ]]; then
      rb_gate "$name" datastore-major ds_major_cutover "$dir"
      rb_gate "$name" datastore-probe ds_probe "$dir"
    else
      rb_gate "$name" datastore-cutover ds_store_cutover "$dir"
    fi
  done
}

# After the sweep: the apps that migrate their own database, which the sweep
# started on their new image.
ds_gates() {
  local dir name
  for dir in "${DS_SNAPSHOTS[@]}"; do
    name="$(ds_meta "$dir" name)"
    [[ "$name" != "$(ds_meta "$dir" store)" ]] || continue
    rb_gate "$name" datastore-probe ds_probe "$dir"
  done
}

ds_in_window() {
  local dir age now
  now="$(ds_now_epoch)"
  for dir in "${DS_SNAPSHOTS[@]}"; do
    age=$(( now - $(ds_meta "$dir" epoch) ))
    if (( age > DS_WINDOW_SECONDS )); then
      echo "::error::snapshot $dir is ${age}s old, past the ${DS_WINDOW_SECONDS}s window: restoring it would lose too many writes"
      return 1
    fi
  done
}

# ds_compare <dir> <target> [atleast]: 0 when every probe count in
# <dir>/baseline.counts equals the live count (or is at most it).
ds_compare() {
  local dir="$1" t="$2" su db table want got rc=0
  su="$(ds_meta "$dir" superuser)"
  while read -r db table want; do
    got="$(ds_count_live "$t" "$su" "$db" "$table")" || got="error"
    if [[ "$got" == "$want" ]]; then
      echo "probe: $db $table $got rows, as in the snapshot"
    elif [[ "${3:-}" == atleast && "$got" =~ ^[0-9]+$ ]] && (( got > want )); then
      echo "probe: $db $table $got rows, the snapshot $want"
    else
      echo "::error::probe: $db $table has $got rows, the snapshot $want"
      rc=1
    fi
  done < "$dir/baseline.counts"
  return "$rc"
}

# ds_restore_dbs <dir> <target> [pg_restore options]: drop and restore every
# database of the snapshot in <target>.
ds_restore_dbs() {
  local dir="$1" t="$2" su db
  shift 2
  su="$(ds_meta "$dir" superuser)"
  for db in $(ds_meta "$dir" databases); do
    echo "restore: $db from $dir/$db.dump"
    ds_x "$t" psql -U "$su" -d postgres -v ON_ERROR_STOP=1 -c "DROP DATABASE IF EXISTS \"$db\" WITH (FORCE)" || return 1
    ds_x "$t" pg_restore -U "$su" --create --exit-on-error -d postgres "$@" < "$dir/$db.dump" || return 1
  done
}

ds_restore_into() {
  local dir="$1" ctr="$2" image="${3:-$(ds_meta "$1" store_image)}" su i rc=0 facts mode=exact
  if [[ "$(ds_dir_engine "$dir")" != postgres ]]; then
    [[ "$(ds_kind "$(ds_dir_engine "$dir")")" != none ]] || { echo "restore: $(ds_meta "$dir" store) keeps no state"; return 0; }
    # After a cutover the data is its fresh copy, whose reference are the facts
    # read just before it; tenants outside compose may have written since.
    [[ "$(ds_ref_facts "$dir")" == */baseline.facts ]] || mode=atleast
    facts="$(mktemp)"
    ds_throwaway "$dir" "$ctr" "$image" > "$facts" && ds_compare_facts "$(ds_ref_facts "$dir")" "$facts" "$mode" || rc=1
    rm -f "$facts"
    return "$rc"
  fi
  su="$(ds_meta "$dir" superuser)"
  docker run -d --rm --name "$ctr" --network none --label klai.adhoc=datastore-restore "${DS_THROWAWAY_LIMITS[@]}" \
    -e POSTGRES_USER="$su" -e POSTGRES_HOST_AUTH_METHOD=trust "$image" > /dev/null || return 1
  # Over TCP: the image's init server listens on the socket only, so a socket
  # check would pass before initialisation has finished.
  for i in $(seq 1 60); do
    docker exec "$ctr" pg_isready -h 127.0.0.1 -U "$su" > /dev/null 2>&1 && break
    (( i == 60 )) && { echo "::error::throwaway $ctr did not start"; docker stop "$ctr" > /dev/null; return 1; }
    sleep 2
  done
  # A new major has to carry every extension the databases use (pgvector)
  # before anything is restored; the list is in meta for a major only.
  ds_pg_has_extensions "ctr:$ctr" "$su" "$image" "$(ds_meta "$dir" extensions)" || rc=1
  # The roles of the live cluster do not exist here.
  (( rc )) || { ds_restore_dbs "$dir" "ctr:$ctr" --no-owner --no-acl && ds_compare "$dir" "ctr:$ctr"; } || rc=1
  docker stop "$ctr" > /dev/null || true
  return "$rc"
}

# ds_restore_snapshot <dir>: the previous compose file must be back in place
# (rb_restore) so the store and its consumers start their previous images.
ds_restore_snapshot() {
  local dir="$1" name store su consumers rc
  name="$(ds_meta "$dir" name)"; store="$(ds_meta "$dir" store)"
  su="$(ds_meta "$dir" superuser)"; consumers="$(ds_meta "$dir" consumers)"
  # A store row looks at the store; an app row at every service in it, because
  # glitchtip-migrate migrates while glitchtip-web may still show the old image.
  # Any container on an image other than the old one counts as having run the
  # new version: restoring when in doubt loses minutes of writes, not restoring
  # leaves the old image on a schema it may not understand.
  local svcs="$store"
  [[ "$name" == "$store" ]] || svcs="$consumers"
  # shellcheck disable=SC2086 # one word per service
  if [[ -z "$(ds_meta "$dir" moved_aside)" ]] && ! ds_container_images $svcs | grep -qvxF "$(ds_meta "$dir" old_image)"; then
    echo "restore: no container of [$svcs] ran anything but $(ds_meta "$dir" old_image); the database is as it was, nothing to restore"
    ds_ledger "$name" "$dir" "$(ds_meta "$dir" old_image)" not_needed
    return 0
  fi
  local newd
  newd="$(ds_meta "$dir" new_pgdata)"
  # The host container lock (compose-up.sh's) for the whole stop-restore-probe,
  # so a service deploy cannot start a consumer on a half-restored database.
  # compose-up.sh takes the same lock, so the store is recreated directly.
  (
    exec 8> "$DS_LOCK_FILE"
    flock -w 900 8 || { echo "::error::host container lock $DS_LOCK_FILE busy for 15 minutes"; exit 1; }
    # shellcheck disable=SC2086 # one word per service
    ds_compose stop $consumers || exit 1
    [[ "$(ds_dir_engine "$dir")" == postgres ]] || { ds_put_back "$dir"; exit; }
    if [[ -n "$newd" ]]; then
      echo "restore: $store back on its previous image and the data directory it left at the cutover; $newd removed"
      ds_compose stop -t 120 "$store" && ds_pg_rm "$store" "$newd" \
        && ds_pg_mv "$store" "$(ds_meta "$dir" old_pgdata).aside" "$(ds_meta "$dir" old_pgdata)" \
        && ds_compose up -d --no-deps --force-recreate "$store" \
        && ds_pg_wait "$store" "$su" && ds_compare "$dir" "$store" \
        && ds_set_meta "$dir" settled put-back
      exit
    fi
    if [[ "$name" == "$store" ]]; then
      ds_compose up -d --no-deps --force-recreate "$store" || exit 1
      ds_pg_wait "$store" "$su" || exit 1
    fi
    ds_restore_dbs "$dir" "$store" && ds_compare "$dir" "$store"
  )
  rc=$?
  if (( rc == 0 )); then
    # shellcheck disable=SC2086 # one word per service
    "$RB_DIR/scripts/compose-up.sh" --no-pull --force-recreate $consumers || rc=1
  fi
  ds_ledger "$name" "$dir" "$(ds_meta "$dir" old_image)" "$( ((rc == 0)) && echo restored || echo restore_failed)"
  return "$rc"
}

# R6: one attempt, newest snapshot first; the first failure ends it.
ds_restore_all() {
  local i
  for (( i = ${#DS_SNAPSHOTS[@]} - 1; i >= 0; i-- )); do
    ds_restore_snapshot "${DS_SNAPSHOTS[i]}" || return 1
  done
}

# ds_settle: at the start of every sync, before ds_snapshot_changed, finish
# what had to wait for a window to close. A store with any snapshot still
# inside its window is left alone.
#   Postgres major: remove the old data directory renamed aside at the cutover,
#   only when the new cluster is proven the state to keep: the cutover's
#   restore and count succeeded (migrated=yes), the store runs the new image,
#   and no sync is left rollback_failed. Otherwise it says why and keeps it.
#   The dump under backups/pre-* stays either way.
#   Mongo: raise featureCompatibilityVersion to the running version.
# Returns 1 when an FCV bump is refused, so the sync stops before it changes
# anything; a mongo that does not answer is a warning, retried next sync.
ds_settle() {
  local meta dir epoch now open=" " olds=() store cid old v fcv out
  now="$(ds_now_epoch)"
  for meta in "$DS_BACKUP_ROOT"/pre-*/meta; do
    [[ -e "$meta" ]] || continue
    dir="${meta%/meta}"; epoch="$(ds_meta "$dir" epoch | tail -n1)"
    [[ -n "$epoch" ]] || continue
    if (( now - epoch <= DS_WINDOW_SECONDS )); then
      open+="$(ds_meta "$dir" store) "
    elif [[ -n "$(ds_meta "$dir" old_pgdata)" && -z "$(ds_meta "$dir" settled)" ]]; then
      olds+=("$dir")
    fi
  done
  for dir in "${olds[@]}"; do
    store="$(ds_meta "$dir" store)"; old="$(ds_meta "$dir" old_pgdata).aside"
    [[ "$open" != *" $store "* ]] || continue
    if [[ "$(ds_meta "$dir" migrated)" != yes ]]; then
      echo "settle: keeping $old of $store: the cutover of $dir never finished its restore"; continue
    fi
    if [[ "$(ds_container_images "$store" | head -n1)" != "$(ds_meta "$dir" new_image)" ]]; then
      echo "settle: keeping $old of $store: it does not run $(ds_meta "$dir" new_image)"; continue
    fi
    if [[ -e "$RB_FAILED_FLAG" ]]; then
      echo "settle: keeping $old of $store: an earlier sync ended rollback_failed ($(cat "$RB_FAILED_FLAG"))"; continue
    fi
    echo "settle: $store is past the window of $dir; removing $old"
    ds_pg_rm "$store" "$old" || { echo "::warning::could not remove $old from $store; retried next sync"; continue; }
    ds_set_meta "$dir" settled "$(ds_now_epoch)"
  done
  for store in $(awk '$2 == "mongo" { print $1 }' <<< "$DS_ENGINE_STORES"); do
    if [[ "$open" == *" $store "* ]]; then
      echo "settle: $store has a snapshot inside its window; its featureCompatibilityVersion stays"
      continue
    fi
    cid="$(ds_ctr "$store")" && [[ -n "$cid" ]] || continue
    out="$(ds_mongo "$store" 'var v = db.version().split("."); print(v[0] + "." + v[1] + " " + db.adminCommand({getParameter: 1, featureCompatibilityVersion: 1}).featureCompatibilityVersion.version)' 2>/dev/null)" \
      || { echo "::warning::$store does not answer; its featureCompatibilityVersion is checked again next sync"; continue; }
    read -r v fcv <<< "$out"
    [[ -n "$fcv" && "$v" != "$fcv" && "$(printf '%s\n' "$fcv" "$v" | sort -V | tail -n1)" == "$v" ]] || continue
    echo "settle: $store runs $v on featureCompatibilityVersion $fcv, past every window; raising it to $v"
    out="$(ds_mongo "$store" "print(db.adminCommand({setFeatureCompatibilityVersion: \"$v\", confirm: true}).ok)" 2>&1)"
    if [[ "$out" != 1 ]]; then
      echo "::error::setFeatureCompatibilityVersion $v on $store failed: $out"
      return 1
    fi
    ds_ledger "$store" "fcv $fcv" "fcv $v" fcv_raised
  done
}
