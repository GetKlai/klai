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
#                         before the sync changes anything: disk check, one
#                         pg_dump -Fc per database, the baseline counts, a
#                         ledger line (kind data). Refuses the run when short.
#   ds_gates              after the env-drift sweep: recreate the consumers of a
#                         changed store, then probe every snapshotted database
#   ds_unsnapshotted <old> <new>   inventory entries changed without a snapshot
#   ds_in_window          0 when every snapshot of this run is at most 30 min old
#   ds_restore_all        a red gate's data half: every snapshot, newest first
#   ds_restore_snapshot <dir>      one snapshot back into the live store
#   ds_restore_into <dir> <container>
#                         restore into a throwaway container from the store's
#                         previous image and compare the counts; never touches
#                         the live store (dry mode now, the monthly rehearsal S8b)
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
# restored state on any busy table. Reading the dump back also proves the
# archive is readable before anything is changed.
#
# Dry mode, for proving the path on production: a file $RB_DIR/.datastore-dry
# holding one inventory name (e.g. umami-postgres). The next sync that changes
# that entry takes the real snapshot, forces the probe red with a table that
# does not exist, and on the rollback restores into a throwaway container
# instead of the live store; the live store only gets its previous image back
# on the same volume. The file is removed when it is used, so it applies once.
#
# Known ceilings
#   - Snapshots under backups/pre-* are not pruned; backup.sh retention only
#     removes the dated nightly sets. The disk check refuses a change when the
#     room is not there. Upgrade path: prune pre-* older than 7 days here.
#   - LiteLLM is recreated (and migrated) by litellm-hook-deploy.yml, which runs
#     beside this sync, so a dump taken here could already be post-migration. It
#     is listed with that workflow as owner; a red gate after a LiteLLM image
#     change restores nothing (rollback_failed, gate self-migrating-image).
#   - The restore holds the host container lock (compose-up.sh's), so no
#     service deploy starts a consumer against a half-restored database. A
#     deploy that does not go through compose-up.sh is not held back.
#
# Tests: deploy/scripts/tests/datastore-rollback.test.sh

DS_BACKUP_ROOT="${KLAI_BACKUP_ROOT:-$RB_DIR/backups}"
DS_LOCK_FILE="${KLAI_CONTAINER_LOCK:-$RB_DIR/.container-deploy.lock}"
DS_DRY_FLAG="$RB_DIR/.datastore-dry"
# R4: an automatic restore only within 30 minutes of the snapshot.
DS_WINDOW_SECONDS=1800
DS_DRY_TABLE="public.__klai_datastore_dry_forced_red__"

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
# changed in the same sync.
DS_APPS='
crm            crm-postgres  twenty     sync                      crm crm-worker
glitchtip-web  postgres      glitchtip  sync                      glitchtip-migrate glitchtip-web glitchtip-worker
zitadel        postgres      zitadel    sync                      zitadel
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
# ---------------------------------------------------------------------------

DS_SNAPSHOTS=()

ds_now_epoch() { date -u +%s; }
ds_compose() { docker compose --project-directory "$RB_DIR" "$@"; }

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

# "<service> <image>" per service in a compose file. The file keeps services at
# two spaces and their image at four.
ds_images() {
  awk '/^services:/ { s = 1; next }
       /^[^ #]/ { s = 0 }
       s && /^  [A-Za-z0-9._-]+:[[:space:]]*$/ { svc = $1; sub(/:$/, "", svc) }
       s && /^    image:/ { img = $2; gsub(/["'\'']/, "", img); print svc, img }' "$1"
}
ds_image_of() { ds_images "$1" | awk -v s="$2" '$1 == s { print $2 }'; }

# Inventory names whose image differs between two compose files, store rows
# first; an app row is dropped when its store is in the list as well.
ds_changed() {
  local old="$1" new="$2" name rest store stores=()
  [[ -f "$old" && -f "$new" ]] || return 0
  while read -r name rest; do
    [[ -n "$name" ]] || continue
    if [[ "$(ds_image_of "$old" "$name")" != "$(ds_image_of "$new" "$name")" ]]; then
      stores+=("$name"); echo "$name"
    fi
  done <<< "$DS_STORES"
  while read -r name store rest; do
    [[ -n "$name" ]] || continue
    [[ " ${stores[*]} " == *" $store "* ]] && continue
    [[ "$(ds_image_of "$old" "$name")" != "$(ds_image_of "$new" "$name")" ]] && echo "$name"
  done <<< "$DS_APPS"
  return 0
}

# Inventory entries this sync does not snapshot (their owner is another
# workflow) that changed between two compose files.
ds_unsnapshotted() {
  local name
  while read -r name; do
    [[ "$(ds_app_row "$name" | awk '{ print $4 }')" == sync || -n "$(ds_store_row "$name")" ]] || echo "$name"
  done < <(ds_changed "$1" "$2")
}

# Size in bytes of the data volume behind a store's running container.
ds_volume_bytes() {
  local cid src
  cid="$(ds_compose ps -q "$1")" && [[ -n "$cid" ]] || return 1
  src="$(docker inspect --format '{{range .Mounts}}{{if eq .Destination "/var/lib/postgresql"}}{{.Source}}{{end}}{{end}}' "$cid")" \
    && [[ -n "$src" ]] || return 1
  # The volume lives under /var/lib/docker, readable for root only.
  sudo -n du -sb "$src" | awk '{ print $1 }'
}

ds_running_image() {
  local cid
  cid="$(ds_compose ps -q "$1" 2>/dev/null)" && [[ -n "$cid" ]] || return 0
  docker inspect --format '{{.Config.Image}}' "$cid" 2>/dev/null || true
}

# Rows of the COPY block of one table in a dump read on stdin. COPY text escapes
# newlines inside values, so one line is one row.
ds_count_in_dump() {
  local t="$1" su="$2" table="$3"
  ds_x "$t" pg_restore -U "$su" --data-only -n "${table%%.*}" -t "${table#*.}" -f - \
    | awk '/^COPY / { c = 1; next } c && /^\\\.$/ { c = 0; next } c { n++ } END { print n + 0 }'
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

# ds_snapshot <name> <old-compose> <new-compose>: dump, baseline, ledger.
ds_snapshot() {
  local name="$1" old="$2" new="$3" store su dbs consumers storecons dir db table n app
  app="$(ds_app_row "$name")"
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
  dir="$DS_BACKUP_ROOT/pre-$name-$(date -u +%Y%m%dT%H%M%SZ)"
  # A dump holds customer data: readable for the deploy user only.
  (umask 077 && mkdir -p "$dir") || return 1
  {
    echo "name=$name"
    echo "store=$store"
    echo "superuser=$su"
    echo "databases=$dbs"
    echo "consumers=$consumers"
    echo "old_image=$(ds_image_of "$old" "$name")"
    echo "new_image=$(ds_image_of "$new" "$name")"
    echo "store_image=$(ds_image_of "$old" "$store")"
    echo "epoch=$(ds_now_epoch)"
  } > "$dir/meta"
  : > "$dir/baseline.counts"
  for db in $dbs; do
    echo "snapshot: pg_dump -Fc $store/$db -> $dir/$db.dump"
    ds_x "$store" pg_dump -U "$su" -Fc -d "$db" > "$dir/$db.dump" \
      || { echo "::error::pg_dump of $store/$db failed"; return 1; }
    for table in $(ds_probe_tables "$store" "$db"); do
      ds_x "$store" pg_restore -l < "$dir/$db.dump" | grep -q " TABLE DATA ${table%%.*} ${table#*.} " \
        || { echo "::error::probe table $table is not in the dump of $store/$db; fix DS_PROBES in datastore-rollback.sh"; return 1; }
      n="$(ds_count_in_dump "$store" "$su" "$table" < "$dir/$db.dump")" || return 1
      printf '%s %s %s\n' "$db" "$table" "$n" >> "$dir/baseline.counts"
    done
  done
  ds_ledger "$name" "$dir" "$(ds_image_of "$new" "$name")" snapshot
  DS_SNAPSHOTS+=("$dir")
  echo "snapshot of $name taken: $dir"
}

ds_snapshot_changed() {
  local old="$1" new="$2" names name need=0 free bytes store
  mapfile -t names < <(ds_changed "$old" "$new")
  local own=()
  for name in "${names[@]}"; do
    [[ -n "$(ds_store_row "$name")" || "$(ds_app_row "$name" | awk '{ print $4 }')" == sync ]] && own+=("$name")
  done
  (( ${#own[@]} > 0 )) || return 0
  echo "image change of a data store or a self-migrating app: ${own[*]}"
  # AT-c2: twice the volume size free, or the change does not start.
  for name in "${own[@]}"; do
    store="$name"; [[ -n "$(ds_app_row "$name")" ]] && store="$(ds_app_row "$name" | awk '{ print $2 }')"
    bytes="$(ds_volume_bytes "$store")" && [[ "$bytes" =~ ^[0-9]+$ ]] \
      || { echo "::error::could not size the volume of $store"; rb_finish "$name" disk-headroom rollback_failed; }
    need=$(( need + 2 * bytes ))
  done
  free="$(df -PB1 "$RB_DIR" | awk 'NR == 2 { print $4 }')"
  if ! [[ "$free" =~ ^[0-9]+$ ]] || (( free < need )); then
    echo "::error::not enough disk for a snapshot: ${free:-?} bytes free on $RB_DIR, $need needed (twice the volume size of ${own[*]}); nothing changed"
    rb_finish "${own[0]}" disk-headroom rollback_failed
  fi
  for name in "${own[@]}"; do
    if [[ -f "$DS_DRY_FLAG" && "$(cat "$DS_DRY_FLAG")" == "$name" ]]; then
      echo "::warning::datastore dry mode for $name: the probe is forced red and the restore goes to a throwaway container"
      DS_DRY="$name"
      rm -f "$DS_DRY_FLAG"
    fi
    ds_snapshot "$name" "$old" "$new" || rb_finish "$name" snapshot rollback_failed
  done
}

# Recreate the consumers of a store whose image changed, then check that every
# probe table of every snapshotted database answers. Counts are not compared
# here: the new version is live and taking writes.
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
  if [[ "${DS_DRY:-}" == "$(ds_meta "$dir" name)" ]]; then
    ds_count_live "$store" "$su" postgres "$DS_DRY_TABLE" > /dev/null || { echo "probe: dry mode, forced red"; return 1; }
  fi
  echo "probe: $store answers on every probe table"
}

ds_gates() {
  local dir
  for dir in "${DS_SNAPSHOTS[@]}"; do
    rb_gate "$(ds_meta "$dir" name)" datastore-probe ds_probe "$dir"
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

# 0 when every probe count in <dir>/baseline.counts equals the live count.
ds_compare() {
  local dir="$1" t="$2" su db table want got rc=0
  su="$(ds_meta "$dir" superuser)"
  while read -r db table want; do
    got="$(ds_count_live "$t" "$su" "$db" "$table")" || got="error"
    if [[ "$got" == "$want" ]]; then
      echo "probe: $db $table $got rows, as in the snapshot"
    else
      echo "::error::probe after restore: $db $table has $got rows, the snapshot $want"
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
  local dir="$1" ctr="$2" su image i rc=0
  su="$(ds_meta "$dir" superuser)"; image="$(ds_meta "$dir" store_image)"
  docker run -d --rm --name "$ctr" --network none --label klai.adhoc=datastore-restore \
    -e POSTGRES_USER="$su" -e POSTGRES_HOST_AUTH_METHOD=trust "$image" > /dev/null || return 1
  # Over TCP: the image's init server listens on the socket only, so a socket
  # check would pass before initialisation has finished.
  for i in $(seq 1 60); do
    docker exec "$ctr" pg_isready -h 127.0.0.1 -U "$su" > /dev/null 2>&1 && break
    (( i == 60 )) && { echo "::error::throwaway $ctr did not start"; docker stop "$ctr" > /dev/null; return 1; }
    sleep 2
  done
  # The roles of the live cluster do not exist here.
  ds_restore_dbs "$dir" "ctr:$ctr" --no-owner --no-acl && ds_compare "$dir" "ctr:$ctr" || rc=1
  docker stop "$ctr" > /dev/null || true
  return "$rc"
}

# ds_restore_snapshot <dir>: the previous compose file must be back in place
# (rb_restore) so the store and its consumers start their previous images.
ds_restore_snapshot() {
  local dir="$1" name store su consumers rc
  name="$(ds_meta "$dir" name)"; store="$(ds_meta "$dir" store)"
  su="$(ds_meta "$dir" superuser)"; consumers="$(ds_meta "$dir" consumers)"
  if [[ "$(ds_running_image "$name")" == "$(ds_meta "$dir" old_image)" ]]; then
    echo "restore: $name never started $(ds_meta "$dir" new_image); its database is as it was, nothing to restore"
    ds_ledger "$name" "$dir" "$(ds_meta "$dir" old_image)" not_needed
    return 0
  fi
  if [[ "${DS_DRY:-}" == "$name" ]]; then
    echo "restore (dry): into a throwaway container; live would stop [$consumers], restore [$(ds_meta "$dir" databases)] in $store, recreate [$consumers]"
    ds_restore_into "$dir" "klai-datastore-dry-$name" \
      && { [[ "$name" != "$store" ]] || "$RB_DIR/scripts/compose-up.sh" --no-pull --force-recreate "$store"; } \
      && { # shellcheck disable=SC2086 # one word per service
           "$RB_DIR/scripts/compose-up.sh" --no-pull --force-recreate $consumers; }
    rc=$?
    ds_ledger "$name" "$dir" "$(ds_meta "$dir" old_image)" "$( ((rc == 0)) && echo dry_restored || echo restore_failed)"
    return "$rc"
  fi
  # The host container lock (compose-up.sh's) for the whole stop-restore-probe,
  # so a service deploy cannot start a consumer on a half-restored database.
  # compose-up.sh takes the same lock, so the store is recreated directly.
  (
    exec 8> "$DS_LOCK_FILE"
    flock -w 900 8 || { echo "::error::host container lock $DS_LOCK_FILE busy for 15 minutes"; exit 1; }
    # shellcheck disable=SC2086 # one word per service
    ds_compose stop $consumers || exit 1
    if [[ "$name" == "$store" ]]; then
      ds_compose up -d --no-deps --force-recreate "$store" || exit 1
      for i in $(seq 1 60); do
        ds_x "$store" pg_isready -h 127.0.0.1 -U "$su" > /dev/null 2>&1 && break
        (( i == 60 )) && { echo "::error::$store did not come back on its previous image"; exit 1; }
        sleep 2
      done
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
