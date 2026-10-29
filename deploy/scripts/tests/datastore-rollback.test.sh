#!/usr/bin/env bash
# Tests for datastore-rollback.sh (SPEC-INFRA-AUTO-ROLLBACK-001 S8a and S8b):
# snapshot before a data store or self-migrating image change, restore on a red
# gate within 30 minutes, nothing past it, one attempt.
#
# No Docker needed. The fakes in datastore-fakes.sh keep each store as files and
# record every call. compose-up.sh, the env-drift sweep and the Twenty check are
# stubs in each case's scripts/ directory. The driver replays the sync in
# deploy-compose.yml: snapshot and shadow, keep, overwrite, cutover, sweep,
# datastore gates.
#
# Run: bash deploy/scripts/tests/datastore-rollback.test.sh

set -euo pipefail

script_dir=$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)
repo_root=$(CDPATH='' cd -- "$script_dir/../.." && pwd)
helper="$script_dir/compose-rollback.sh"

work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
bin="$work/bin"
mkdir -p "$bin"

failures=0
case_dir=""
rc=0

# --- fakes ------------------------------------------------------------------
# shellcheck source=deploy/scripts/tests/datastore-fakes.sh
source "$script_dir/tests/datastore-fakes.sh"
ds_fakes_install "$bin"

OLD_COMPOSE='services:
  caddy:
    image: caddy:2.9
  umami-postgres:
    image: postgres:18.6-alpine
  umami:
    image: ghcr.io/umami-software/umami:3.0.0
  crm-postgres:
    image: postgres:18.6-alpine
  crm:
    image: twentycrm/twenty:v2.51.0
  crm-worker:
    image: twentycrm/twenty:v2.51.0
  litellm:
    image: ghcr.io/berriai/litellm:v1.106.2
  postgres:
    image: pgvector/pgvector:0.8.7-pg18
  glitchtip-migrate:
    image: glitchtip/glitchtip:6.2.6
  glitchtip-web:
    image: glitchtip/glitchtip:6.2.6
  glitchtip-worker:
    image: glitchtip/glitchtip:6.2.6
  qdrant:
    image: qdrant/qdrant:v1.19.2
  redis:
    image: redis:8.10.2-alpine
  crm-redis:
    image: redis:8.8.0-alpine
  mongodb:
    image: mongo:8.0.34
  firecrawl-rabbitmq:
    image: rabbitmq:4.4.0-alpine
volumes:
  umami-postgres-data-18:
'
UMAMI_BUMP="${OLD_COMPOSE//postgres:18.6-alpine
  umami:/postgres:18.7-alpine
  umami:}"
TWENTY_BUMP="${OLD_COMPOSE//v2.51.0/v2.52.0}"
LITELLM_BUMP="${OLD_COMPOSE//v1.106.2/v1.107.0}"
UMAMI_APP_BUMP="${OLD_COMPOSE//umami:3.0.0/umami:3.1.0}"
GLITCHTIP_BUMP="${OLD_COMPOSE//glitchtip:6.2.6/glitchtip:6.3.0}"
QDRANT_BUMP="${OLD_COMPOSE//qdrant:v1.19.2/qdrant:v1.20.0}"
REDIS_BUMP="${OLD_COMPOSE//redis:8.10.2-alpine/redis:8.10.3-alpine}"
CRM_REDIS_BUMP="${OLD_COMPOSE//redis:8.8.0-alpine/redis:8.8.1-alpine}"
MONGO_BUMP="${OLD_COMPOSE//mongo:8.0.34/mongo:8.0.35}"
RABBIT_BUMP="${OLD_COMPOSE//rabbitmq:4.4.0-alpine/rabbitmq:4.4.1-alpine}"
UMAMI_MAJOR="${OLD_COMPOSE//postgres:18.6-alpine
  umami:/postgres:19.1-alpine
  umami:}"
PG_MAIN_MAJOR="${OLD_COMPOSE//pgvector:0.8.7-pg18/pgvector:0.8.7-pg19}"

new_case() {
  case_dir="$work/$1"
  local f="$case_dir/fake"
  mkdir -p "$case_dir/scripts" "$f/running" "$f/db/umami-postgres/umami" "$f/db/umami-postgres/postgres" "$f/db/crm-postgres/twenty" "$f/db/postgres/glitchtip"
  : > "$f/calls"
  printf '%s' "$OLD_COMPOSE" > "$case_dir/docker-compose.yml"
  awk '/^  [a-z]/ { svc = $1; sub(/:$/, "", svc) } /^    image:/ { print $2 > (dir "/" svc) }' dir="$f/running" "$case_dir/docker-compose.yml"
  echo 91 > "$f/db/umami-postgres/umami/public.website_event"
  echo 52 > "$f/db/umami-postgres/umami/public.session"
  echo 1 > "$f/db/crm-postgres/twenty/core.objectMetadata"
  echo 20 > "$f/db/crm-postgres/twenty/core.fieldMetadata"
  echo 1 > "$f/db/postgres/glitchtip/public.users_user"
  echo 1 > "$f/db/postgres/glitchtip/public.projects_project"
  ds_fakes_engines "$f"
  echo 1000 > "$f/du_bytes"
  echo 1000000 > "$f/df_avail"
  echo aaa111 > "$case_dir/.compose-commit"
  # The sweep starts what the compose file declares; the new version then writes.
  cat > "$case_dir/scripts/compose-env-drift-recreate.sh" <<'STUB'
#!/usr/bin/env bash
echo "sweep" >> "$FAKE/calls"
awk '/^  [a-z]/ { svc = $1; sub(/:$/, "", svc) } /^    image:/ { print $2 > (dir "/" svc) }' dir="$FAKE/running" "$KLAI_COMPOSE_DIR/docker-compose.yml"
for f in "$FAKE/db/umami-postgres/umami/public.website_event" "$FAKE/db/crm-postgres/twenty/core.fieldMetadata"; do
  [[ -e "$FAKE/new_writes" && -e "$f" ]] && echo $(( $(cat "$f") + 5 )) > "$f"
done
# Engines: five more points and keys; RabbitMQ starts as a new, empty node.
if [[ -e "$FAKE/new_writes" ]]; then
  for e in qdrant redis; do
    awk -F= '$1 == "points" || $1 == "keys" { $0 = $1 "=" $2 + 5 } 1' "$FAKE/eng/$e" > "$FAKE/eng/$e.n" && mv "$FAKE/eng/$e.n" "$FAKE/eng/$e"
  done
  echo "queues=" > "$FAKE/eng/firecrawl-rabbitmq"
fi
# Only the first sweep is the new version; the one after a rollback is not.
rm -f "$FAKE/new_writes"
exit 0
STUB
  cat > "$case_dir/scripts/compose-up.sh" <<'STUB'
#!/usr/bin/env bash
echo "compose-up $*" >> "$FAKE/calls"
for svc in "$@"; do
  [[ "$svc" == --* ]] && continue
  awk -v s="$svc" '/^  [a-z]/ { svc = $1; sub(/:$/, "", svc) } /^    image:/ && svc == s { print $2 }' "$KLAI_COMPOSE_DIR/docker-compose.yml" > "$FAKE/running/$svc"
done
STUB
  cat > "$case_dir/scripts/check-twenty-upgrade.sh" <<'STUB'
#!/usr/bin/env bash
echo "twenty-check" >> "$FAKE/calls"
n=$(grep -c '^twenty-check' "$FAKE/calls")
[[ -e "$FAKE/twenty_red_first" && "$n" -eq 1 ]] && exit 1
exit 0
STUB
  chmod +x "$case_dir"/scripts/*.sh
}

# run_driver <new compose> [after-snapshot] [after-cutover] [after-gates]
run_driver() {
  printf '%s' "$1" > "$case_dir/new-compose.yml"
  cat > "$case_dir/driver.sh" <<DRIVER
set -e
source "$helper"
rb_init bbb222
ds_settle
ds_snapshot_changed "\$KLAI_COMPOSE_DIR/docker-compose.yml" "\$KLAI_COMPOSE_DIR/new-compose.yml"
${2:-}
rb_keep "\$KLAI_COMPOSE_DIR/docker-compose.yml"
cp "\$KLAI_COMPOSE_DIR/new-compose.yml" "\$KLAI_COMPOSE_DIR/docker-compose.yml"
red() { echo "red: \$*"; return 1; }
ds_cutover
${3:-}
rb_gate compose compose-up-verification "\$KLAI_COMPOSE_DIR/scripts/compose-env-drift-recreate.sh"
ds_gates
${4:-}
rb_deployed
echo REACHED_END
DRIVER
  set +e
  PATH="$bin:$PATH" FAKE="$case_dir/fake" KLAI_COMPOSE_DIR="$case_dir" bash "$case_dir/driver.sh" > "$case_dir/out" 2>&1
  rc=$?
  set -e
}

fail() {
  echo "  FAIL $1"
  sed 's/^/       /' "$case_dir/out"
  echo "       --- calls ---"
  sed 's/^/       /' "$case_dir/fake/calls"
  failures=$((failures + 1))
}
check() { if [[ "$2" == "$3" ]]; then echo "  ok   $1"; else fail "$1 (expected '$2', got '$3')"; fi; }
calls() { grep -c -- "$1" "$case_dir/fake/calls" || true; }
report() { python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))[sys.argv[2]])' "$case_dir/deploy-reports/compose.json" "$1"; }
data_ledger() { awk -F'\t' '$2 == "data" { print $3, $7 }' "$case_dir/deploy-ledger.log" | paste -sd, -; }
snapdir() { find "$case_dir/backups" -maxdepth 1 -name "pre-$1-*" | head -n1; }
count() { cat "$case_dir/fake/db/$1"; }
running() { cat "$case_dir/fake/running/$1"; }
umami_pg_image() { grep -A1 '^  umami-postgres:' "$case_dir/docker-compose.yml" | awk '/image:/ { print $2 }'; }
order_of() { grep -n -- "$1" "$case_dir/fake/calls" | head -n1 | cut -d: -f1; }
last_of() { grep -n -- "$1" "$case_dir/fake/calls" | tail -n1 | cut -d: -f1; }
# The seconds between a store's cutover line and its deployed or restored line.
window_of() { awk -F'\t' -v s="$1" '$2 == "data" && $3 == s && $7 == "cutover" { c = $1 } $2 == "data" && $3 == s && ($7 == "deployed" || $7 == "restored") { e = $1 } END { print (c != "" && e >= c) ? "measured" : "missing" }' "$case_dir/deploy-ledger.log"; }
untouched() { [[ ! -e "$case_dir/docker-compose.yml.prev" && ! -e "$case_dir/.compose-rollback-failed" ]] && echo yes || echo no; }

echo "datastore-rollback.sh"

# --- inventory against the real compose file ---------------------------------
inventory_check() {
  local compose="$repo_root/deploy/docker-compose.yml" svcs bad=""
  # shellcheck source=deploy/scripts/datastore-rollback.sh
  RB_DIR=/nonexistent source "$script_dir/datastore-rollback.sh"
  svcs=" $(awk '/^services:/ { s = 1; next } /^[^ #]/ { s = 0 } s && /^  [A-Za-z0-9._-]+:[[:space:]]*$/ { sub(/:$/, "", $1); printf "%s ", $1 }' "$compose")"
  local name su rest store db s
  while read -r name su rest; do
    [[ -n "$name" ]] || continue
    [[ "$svcs" == *" $name "* ]] || bad+=" store:$name"
    [[ "$(awk -v s="$name" '/^  [A-Za-z0-9._-]+:[[:space:]]*$/ { cur = $1; sub(/:$/, "", cur) } cur == s && /^      POSTGRES_USER:/ { print $2 }' "$compose")" == "$su" ]] || bad+=" superuser:$name"
    for s in $rest; do [[ "$svcs" == *" $s "* ]] || bad+=" consumer:$s"; done
  done <<< "$DS_STORES"
  while read -r name store db _ rest; do
    [[ -n "$name" ]] || continue
    for s in $name $store $rest; do [[ "$svcs" == *" $s "* ]] || bad+=" app:$s"; done
  done <<< "$DS_APPS"
  # Every Postgres in the compose file is a store row, every self-migrating
  # image has an app row.
  while read -r name s; do
    [[ "$s" =~ (^|/)(postgres|pgvector/pgvector): ]] && [[ -z "$(ds_store_row "$name")" ]] && bad+=" missing-store:$name"
    [[ "$s" =~ twentycrm/twenty|glitchtip/glitchtip|zitadel/zitadel|berriai/litellm|umami-software/umami ]] \
      && [[ -z "$(ds_app_row "$name")" ]] \
      && ! grep -qw "$name" <<< "$DS_APPS" && bad+=" missing-app:$name"
  done < <(ds_images "$compose")
  echo "${bad:-none}"
}
check "inventory names, superusers and consumers exist in deploy/docker-compose.yml" none "$(inventory_check)"

# The other engines: every row a compose service on its engine's image, every
# service on such an image a row, and every service that reaches a store (by
# depends_on, by host name in its environment or command, or by sharing the
# store's private net-* network) one of its consumers.
engine_inventory_check() {
  local compose="$repo_root/deploy/docker-compose.yml" refs svcs bad="" name engine rest s img want nets n
  # shellcheck source=deploy/scripts/datastore-rollback.sh
  RB_DIR=/nonexistent source "$script_dir/datastore-rollback.sh"
  svcs=" $(awk '/^services:/ { s = 1; next } /^[^ #]/ { s = 0 } s && /^  [A-Za-z0-9._-]+:[[:space:]]*$/ { sub(/:$/, "", $1); printf "%s ", $1 }' "$compose")"
  refs="$(awk '
    /^services:/ { s = 1; next }
    /^[^ #]/ { s = 0 }
    !s || /^[[:space:]]*#/ { next }
    { sub(/[[:space:]]+#.*$/, "") }
    /^  [A-Za-z0-9._-]+:[[:space:]]*$/ { svc = $1; sub(/:$/, "", svc); sect = ""; next }
    /^    [A-Za-z_]+:/ { sect = $1; sub(/:.*$/, "", sect) }
    /^      / && (sect == "depends_on" || sect == "networks") {
      v = $0; gsub(/^[[:space:]]*-?[[:space:]]*/, "", v); sub(/:.*$/, "", v)
      if (v != "") print svc, (sect == "depends_on" ? "dep" : "net"), v
      next }
    sect != "image" { t = $0; gsub(/[[:space:]]+/, " ", t); print svc, "text", t }' "$compose")"
  while read -r name engine rest; do
    [[ -n "$name" ]] || continue
    [[ "$svcs" == *" $name "* ]] || { bad+=" store:$name"; continue; }
    for s in $rest; do [[ "$svcs" == *" $s "* ]] || bad+=" consumer:$s"; done
    img="$(ds_image_of "$compose" "$name")"
    case "$engine" in
      qdrant) want='^qdrant/qdrant:' ;; meilisearch) want='^getmeili/meilisearch:' ;; mongo) want='^mongo:' ;;
      redis|redis-ephemeral) want='^redis:' ;; falkordb) want='^falkordb/falkordb:' ;; garage) want='^dxflrs/garage:' ;;
      rabbitmq) want='^rabbitmq:' ;; *) want='^$' ;;
    esac
    [[ "$img" =~ $want ]] || bad+=" engine:$name"
    nets="$(awk -v s="$name" '$1 == s && $2 == "net" && $3 ~ /^net-/ { print $3 }' <<< "$refs")"
    for s in $( { awk -v s="$name" '$2 == "dep" && $3 == s { print $1 }' <<< "$refs"
                  for n in $nets; do awk -v n="$n" '$2 == "net" && $3 == n { print $1 }' <<< "$refs"; done
                  awk -v s="$name" '$2 == "text" { t = substr($0, length($1 " text ") + 1)
                    if (t ~ ("(//|@)" s "(:|/|$)") || t ~ (" " s ":[0-9]") || t ~ ("_HOST: ?\"?" s "\"?$")) print $1 }' <<< "$refs"
                } | sort -u); do
      ds_is_store "$s" && continue
      [[ " $rest " == *" $s "* ]] || bad+=" missing-consumer:$name:$s"
    done
  done <<< "$DS_ENGINE_STORES"
  while read -r name img; do
    [[ "$img" =~ ^(qdrant/qdrant|getmeili/meilisearch|mongo|redis|falkordb/falkordb|dxflrs/garage|rabbitmq): ]] \
      && ! ds_is_store "$name" && bad+=" missing-store:$name"
  done < <(ds_images "$compose")
  echo "${bad:-none}"
}
check "engine stores, images and consumers match deploy/docker-compose.yml" none "$(engine_inventory_check)"

# --- no data store changed: nothing dumped ------------------------------------
new_case unrelated
run_driver "${OLD_COMPOSE//caddy:2.9/caddy:2.10}"
check "caddy-only change -> exit 0" 0 "$rc"
check "  no pg_dump" 0 "$(calls pg_dump)"
check "  no snapshot directory" 0 "$(find "$case_dir" -maxdepth 2 -name 'pre-*' | wc -l | tr -d ' ')"

# --- green store bump: snapshot, shadow on the new minor, cutover -------------
new_case green_store
run_driver "$UMAMI_BUMP"
check "umami-postgres 18.6 -> 18.7, shadow and probe green -> exit 0" 0 "$rc"
snap="$(snapdir umami-postgres)"
check "  snapshot dir pre-umami-postgres-<ts> with one dump per database" yes "$([[ -s "$snap/umami.dump" && ! -e "$snap/postgres.dump" ]] && echo yes || echo no)"
check "  snapshot dir readable for the owner only" 700 "$(stat -c %a "$snap" 2>/dev/null || stat -f %Lp "$snap")"
check "  baseline counts read from the dump" "umami public.website_event 91,umami public.session 52" "$(paste -sd, "$snap/baseline.counts")"
check "  shadow: the dumps restored into a throwaway of 18.7 and counted" "1 91" "$(calls 'run -d --rm --name klai-datastore-shadow-umami-postgres .*postgres:18.7-alpine') $(count klai-datastore-shadow-umami-postgres/umami/public.website_event)"
check "  ledger: snapshot, shadow_green, cutover, deployed" "umami-postgres snapshot,umami-postgres shadow_green,umami-postgres cutover,umami-postgres deployed" "$(data_ledger)"
check "  cutover: consumer stopped, fresh dump, new image, consumer recreated, then the sweep" yes "$( (( $(order_of 'stop umami$') < $(last_of 'pg_dump -U umami') && $(last_of 'pg_dump -U umami') < $(order_of 'up -d --no-deps --force-recreate umami-postgres') && $(order_of 'up -d --no-deps --force-recreate umami-postgres') < $(order_of 'compose-up --no-pull --force-recreate umami$') && $(order_of 'compose-up --no-pull --force-recreate umami$') < $(order_of ^sweep) )) && echo yes || echo no)"
check "  two dumps: the shadow's and the fresh one" 2 "$(calls 'pg_dump -U umami -Fc -d umami')"
check "  store on 18.7, consumer recreated once" "postgres:18.7-alpine 1" "$(running umami-postgres) $(calls 'compose-up --no-pull --force-recreate umami$')"

# --- AT-c1: store bump, cutover probe red -> the fresh dump and image back ----
new_case at_c1_store
: > "$case_dir/fake/probe_red"
# Written after the first snapshot: only the fresh dump holds it.
run_driver "$UMAMI_BUMP" 'echo 93 > "$FAKE/db/umami-postgres/umami/public.website_event"'
check "umami-postgres bump, cutover probe red -> exit 3 (rolled_back)" 3 "$rc"
check "  report gate" datastore-cutover "$(report gate)"
check "  report names the store" umami-postgres "$(report service)"
check "  compose file restored" postgres:18.6-alpine "$(umami_pg_image)"
check "  store runs its previous image" postgres:18.6-alpine "$(running umami-postgres)"
check "  consumer stopped before the restore" yes "$( (( $(order_of 'compose --project-directory .* stop umami') < $(order_of 'exec -T umami-postgres psql .*DROP DATABASE') )) && echo yes || echo no)"
check "  store recreated on its previous image before the restore" yes "$( (( $(last_of 'up -d --no-deps --force-recreate umami-postgres') < $(order_of 'exec -T umami-postgres psql .*DROP DATABASE') )) && echo yes || echo no)"
check "  counts as in the fresh dump, the write after the first snapshot kept" "93 52" "$(count umami-postgres/umami/public.website_event) $(count umami-postgres/umami/public.session)"
check "  consumer force-recreated after the restore" yes "$( (( $(order_of 'exec -T umami-postgres psql .*DROP DATABASE') < $(last_of 'compose-up --no-pull --force-recreate umami$') )) && echo yes || echo no)"
check "  ledger: snapshot, shadow_green, cutover, restored" "umami-postgres snapshot,umami-postgres shadow_green,umami-postgres cutover,umami-postgres restored" "$(data_ledger)"
check "  the window from the fresh dump to the restore is in the ledger" measured "$(window_of umami-postgres)"
check "  compose ledger line" rolled_back "$(tail -n1 "$case_dir/deploy-ledger.log" | cut -f7)"

# --- AT-c1 for a self-migrating app: Twenty bump, upgrade check red -----------
new_case at_c1_app
: > "$case_dir/fake/new_writes"
: > "$case_dir/fake/twenty_red_first"
run_driver "$TWENTY_BUMP" "" "" 'rb_gate crm twenty-upgrade --reverify "$KLAI_COMPOSE_DIR/scripts/check-twenty-upgrade.sh"'
check "Twenty v2.51 -> v2.52, upgrade check red -> exit 3" 3 "$rc"
check "  only the twenty database dumped" "twenty.dump" "$(cd "$(snapdir crm)" && ls ./*.dump | sed 's#^\./##')"
check "  crm and crm-worker stopped, the store left running" 1 "$(calls 'stop crm crm-worker$')"
check "  store not recreated" 0 "$(calls 'force-recreate crm-postgres')"
check "  twenty restored to the snapshot" 20 "$(count crm-postgres/twenty/core.fieldMetadata)"
check "  Twenty back on its previous image" twentycrm/twenty:v2.51.0 "$(running crm)"
check "  check ran again on the restored state" 2 "$(calls '^twenty-check')"

# --- AT-c2: not enough disk -> refused before anything changed ----------------
new_case at_c2
echo 1999 > "$case_dir/fake/df_avail"
run_driver "$UMAMI_BUMP"
check "free disk below twice the volume -> exit 4" 4 "$rc"
check "  report gate" disk-headroom "$(report gate)"
check "  report outcome: nothing changed, so not rollback_failed" not_started "$(report outcome)"
check "  event level warning, not error" 1 "$(grep -c '"level":"warning","event":"auto_rollback","outcome":"not_started"' "$case_dir/logs/auto-rollback.log")"
check "  compose file untouched" postgres:18.6-alpine "$(umami_pg_image)"
check "  no .prev made: the run stopped before the first file" no "$([[ -e "$case_dir/docker-compose.yml.prev" ]] && echo yes || echo no)"
check "  no dump, no recreate" "0 0" "$(calls pg_dump) $(calls '^sweep')"
check "  no failure flag: the host is still in its verified state" no "$([[ -e "$case_dir/.compose-rollback-failed" ]] && echo yes || echo no)"

# --- AT-c3: red 31 minutes after the snapshot -> nothing restored -------------
new_case at_c3
: > "$case_dir/fake/new_writes"
run_driver "$UMAMI_BUMP" "" 'sed -i.bak "s/^epoch=.*/epoch=$(( $(date -u +%s) - 1860 ))/" "${DS_SNAPSHOTS[0]}/meta"' 'rb_gate umami-postgres forced red'
check "red gate at +31 min after the fresh dump -> exit 4" 4 "$rc"
check "  report gate" restore-window "$(report gate)"
check "  nothing dropped, only the cutover stopped the consumer" "0 1" "$(calls 'exec -T umami-postgres psql .*DROP DATABASE') $(calls 'compose .* stop ')"
check "  new compose file left in place" postgres:18.7-alpine "$(umami_pg_image)"
check "  the new version's writes kept" 96 "$(count umami-postgres/umami/public.website_event)"

# --- count mismatch after the restore -> rollback_failed, one attempt (R6) -----
new_case mismatch
: > "$case_dir/fake/probe_red"
: > "$case_dir/fake/restore_short"
run_driver "$UMAMI_BUMP"
check "restored counts differ from the snapshot -> exit 4" 4 "$rc"
check "  report gate is the red gate" datastore-cutover "$(report gate)"
check "  one restore attempt" 1 "$(calls 'exec -T umami-postgres psql .*DROP DATABASE')"
check "  consumer not started on unverified data" 0 "$(sed -n "$(order_of 'exec -T umami-postgres psql .*DROP DATABASE'),\$p" "$case_dir/fake/calls" | grep -c 'compose-up --no-pull --force-recreate umami$' || true)"
check "  ledger: restore_failed" "umami-postgres snapshot,umami-postgres shadow_green,umami-postgres cutover,umami-postgres restore_failed" "$(data_ledger)"
check "  failure flag written" yes "$([[ -e "$case_dir/.compose-rollback-failed" ]] && echo yes || echo no)"

# --- red before the cutover started the new image: files back, data untouched -
new_case red_before_cutover
run_driver "$UMAMI_BUMP" 'rb_gate caddy sync-recreate false'
check "red gate before the cutover -> exit 3" 3 "$rc"
check "  no data restore: the new version never ran" 0 "$(calls 'exec -T umami-postgres psql .*DROP DATABASE')"
check "  ledger: snapshot, shadow_green, not_needed" "umami-postgres snapshot,umami-postgres shadow_green,umami-postgres not_needed" "$(data_ledger)"

# --- the shadow is red: the run ends before any file, store or consumer moves -
new_case shadow_red_pg
echo postgres:18.7-alpine > "$case_dir/fake/bad_image"
run_driver "$UMAMI_BUMP"
check "umami-postgres 18.7 fails its shadow -> exit 4, gate datastore-shadow, shadow_failed" "4 datastore-shadow shadow_failed" "$rc $(report gate) $(report outcome)"
check "  live untouched: no stop, no recreate, previous image, compose file as it was" "0 0 postgres:18.6-alpine postgres:18.6-alpine" "$(calls 'compose .* stop ') $(calls 'up -d --no-deps') $(running umami-postgres) $(umami_pg_image)"
check "  no .prev, no failure flag" yes "$(untouched)"

# --- LiteLLM: migrated by its own workflow, never snapshotted here ------------
new_case litellm
run_driver "$LITELLM_BUMP" "" 'rb_gate caddy sync-recreate red caddy'
check "LiteLLM bump, red gate -> exit 4" 4 "$rc"
check "  no dump" 0 "$(calls pg_dump)"
check "  report gate" self-migrating-image "$(report gate)"

# --- a dump that does not read back refuses the sync before anything changed --
new_case corrupt_dump
: > "$case_dir/fake/corrupt_dump"
run_driver "$UMAMI_BUMP"
check "pg_restore cannot read the dump back -> exit 4" 4 "$rc"
check "  report gate" snapshot "$(report gate)"
check "  compose file untouched" postgres:18.6-alpine "$(umami_pg_image)"
check "  no recreate, no failure flag" "0 no" "$(calls '^sweep') $([[ -e "$case_dir/.compose-rollback-failed" ]] && echo yes || echo no)"

# --- Umami migrates its own database (Prisma) -> snapshot and restore ---------
new_case umami_app
: > "$case_dir/fake/new_writes"
: > "$case_dir/fake/probe_red"
run_driver "$UMAMI_APP_BUMP"
check "Umami 3.0.0 -> 3.1.0, probe red -> exit 3" 3 "$rc"
check "  snapshot named after the app, umami database only" "umami.dump" "$(cd "$(find "$case_dir/backups" -maxdepth 1 -name 'pre-umami-2*')" && ls ./*.dump | sed 's#^\./##')"
check "  store not recreated, app stopped" "0 1" "$(calls 'force-recreate umami-postgres') $(calls 'compose .* stop umami$')"
check "  umami restored to the snapshot" 91 "$(count umami-postgres/umami/public.website_event)"
check "  Umami back on its previous image" ghcr.io/umami-software/umami:3.0.0 "$(running umami)"

# --- GlitchTip: migrate and web change together -> one snapshot ---------------
new_case glitchtip_one_snapshot
run_driver "$GLITCHTIP_BUMP"
check "GlitchTip bump, green -> exit 0" 0 "$rc"
check "  one snapshot of the glitchtip database" "1 1" "$(find "$case_dir/backups" -maxdepth 1 -name 'pre-glitchtip-*' | wc -l | tr -d ' ') $(calls 'pg_dump -U klai -Fc -d glitchtip')"
check "  taken under the migrating service" yes "$([[ -n "$(snapdir glitchtip-migrate)" ]] && echo yes || echo no)"

# --- the migrate job ran the new image, web still shows the old one -> restore -
new_case glitchtip_migrate_ran
: > "$case_dir/fake/probe_red"
run_driver "$GLITCHTIP_BUMP" "" 'echo glitchtip/glitchtip:6.3.0 > "$FAKE/running/glitchtip-migrate"; echo 2 > "$FAKE/db/postgres/glitchtip/public.users_user"; rb_gate caddy sync-recreate red caddy'
check "red gate before the sweep, migrate already ran 6.3.0 -> exit 3" 3 "$rc"
check "  glitchtip restored, not skipped" 1 "$(calls 'DROP DATABASE IF EXISTS "glitchtip"')"
check "  migrated rows gone" 1 "$(count postgres/glitchtip/public.users_user)"
check "  ledger: restored" "glitchtip-migrate snapshot,glitchtip-migrate restored" "$(data_ledger)"

# --- no service of the row ran the new image -> nothing restored -------------
new_case glitchtip_never_ran
run_driver "$GLITCHTIP_BUMP" "" 'rb_gate caddy sync-recreate red caddy'
check "red gate before the sweep, no GlitchTip container on 6.3.0 -> exit 3" 3 "$rc"
check "  no restore" 0 "$(calls 'DROP DATABASE')"
check "  ledger: not_needed" "glitchtip-migrate snapshot,glitchtip-migrate not_needed" "$(data_ledger)"

# --- dry mode is refused for an app row: it would migrate the live database ----
new_case dry_app_refused
echo crm > "$case_dir/.datastore-dry"
: > "$case_dir/fake/twenty_red_first"
run_driver "$TWENTY_BUMP" "" "" 'rb_gate crm twenty-upgrade --reverify "$KLAI_COMPOSE_DIR/scripts/check-twenty-upgrade.sh"'
check "dry flag on Twenty -> refused, normal run, exit 3" 3 "$rc"
check "  refusal printed" 1 "$(grep -c 'dry mode is for stores only' "$case_dir/out")"
check "  flag removed" no "$([[ -e "$case_dir/.datastore-dry" ]] && echo yes || echo no)"
check "  live restore, no throwaway" "1 0" "$(calls 'exec -T crm-postgres psql .*DROP') $(calls 'docker run')"

# --- S6 replay: a red E2E after a green sync restores that sync's snapshot ----
# replay [age-seconds]: a second shell, as rollback-since.sh runs it.
replay() {
  local snap
  snap="$(snapdir crm)"
  [[ -z "${1:-}" ]] || sed -i.bak "s/^epoch=.*/epoch=$(( $(date -u +%s) - $1 ))/" "$snap/meta"
  set +e
  # shellcheck source=deploy/scripts/compose-rollback.sh
  ( source "$helper" && rb_replay e2e ) < /dev/null > "$case_dir/out" 2>&1
  rc=$?
  set -e
}
for age in "" 1860; do
  new_case "replay_${age:-in_window}"
  : > "$case_dir/fake/new_writes"
  run_driver "$TWENTY_BUMP"
  check "green Twenty sync (replay setup) -> exit 0" 0 "$rc"
  export FAKE="$case_dir/fake" KLAI_COMPOSE_DIR="$case_dir"
  PATH="$bin:$PATH" replay "$age"
  unset FAKE KLAI_COMPOSE_DIR
  if [[ -z "$age" ]]; then
    check "  replay within the window -> exit 3" 3 "$rc"
    check "  twenty restored to the snapshot" 20 "$(count crm-postgres/twenty/core.fieldMetadata)"
    check "  Twenty back on its previous image" twentycrm/twenty:v2.51.0 "$(running crm)"
  else
    check "  replay at +31 min -> exit 4, gate restore-window" "4 restore-window" "$rc $(report gate)"
    check "  nothing restored" "0 25" "$(calls 'DROP DATABASE') $(count crm-postgres/twenty/core.fieldMetadata)"
  fi
done

# --- the window counts from the end of the dumps -------------------------------
new_case epoch_after_dumps
run_driver "$UMAMI_BUMP" 'echo "epoch line count: $(grep -c ^epoch= "${DS_SNAPSHOTS[0]}/meta") last: $(tail -n1 "${DS_SNAPSHOTS[0]}/meta" | cut -d= -f1)"'
check "epoch written once, after the dump" "epoch line count: 1 last: epoch" "$(grep '^epoch line count' "$case_dir/out")"

# --- Postgres majors ----------------------------------------------------------
pgdir() { [[ -d "$case_dir/fake/pgdir/$1" ]] && echo yes || echo no; }

# A minor reads its major from the images and stays on the plain path.
check "  18.6 -> 18.7 is no major: no cutover, no roles dumped" "0 no" "$(grep -c pg_dumpall "$work/green_store/fake/calls" || true) $(grep -q '^new_pgdata=' "$(find "$work/green_store/backups" -maxdepth 1 -name 'pre-umami-postgres-*' | head -n1)/meta" && echo yes || echo no)"

new_case major_green
: > "$case_dir/fake/new_writes"
# A write after the snapshot step reaches the new major: the cutover dumps again.
run_driver "$UMAMI_MAJOR" 'echo 95 > "$FAKE/db/umami-postgres/umami/public.website_event"'
snap="$(snapdir umami-postgres)"
check "umami-postgres 18.6 -> 19.1, shadow and probe green -> exit 0" 0 "$rc"
check "  major read from the running container and the pulled image" "pg_major=18 19,new_pgdata=/var/lib/postgresql/19/docker" "$(grep -E '^(pg_major|new_pgdata)=' "$snap/meta" | paste -sd, -)"
check "  shadow: the first dump restored into a throwaway of 19.1, before any consumer stopped" yes "$( (( $(order_of 'run -d --rm --name klai-datastore-shadow-umami-postgres .*postgres:19.1-alpine') < $(order_of 'stop umami$') )) && echo yes || echo no)"
check "  consumer stopped, then dumped, store recreated, then the sweep" yes "$( (( $(order_of 'stop umami$') < $(last_of pg_dump) && $(last_of pg_dump) < $(order_of 'up -d --no-deps --force-recreate umami-postgres') && $(order_of 'up -d --no-deps --force-recreate umami-postgres') < $(order_of ^sweep) )) && echo yes || echo no)"
check "  baseline is the final state, write after the snapshot step included" "umami public.website_event 95,umami public.session 52" "$(paste -sd, "$snap/baseline.counts")"
check "  roles restored, CREATE ROLE tolerating the ones init made" yes "$(grep -q 'DO \$\$ BEGIN CREATE ROLE grafana_reader; EXCEPTION WHEN duplicate_object' "$case_dir/fake/globals_applied_umami-postgres" && echo yes || echo no)"
check "  new major restored from the dump, the new version writes on" "100 52" "$(count umami-postgres/umami/public.website_event) $(count umami-postgres/umami/public.session)"
check "  old directory moved aside as the cutover found it" 95 "$(cat "$case_dir/fake/pgdir/umami-postgres/18_docker.aside/umami/public.website_event")"
check "  store on the new major, consumer recreated before the sweep" "postgres:19.1-alpine 1 yes" "$(running umami-postgres) $(calls 'compose-up --no-pull --force-recreate umami$') $( (( $(order_of 'compose-up --no-pull --force-recreate umami$') < $(order_of ^sweep) )) && echo yes || echo no)"
check "  ledger: snapshot, shadow_green, cutover, migrated" "umami-postgres snapshot,umami-postgres shadow_green,umami-postgres cutover,umami-postgres migrated" "$(data_ledger)"

# The next syncs: inside the window the old directory stays, past it it goes.
run_driver "${UMAMI_MAJOR//caddy:2.9/caddy:2.10}"
check "  next sync inside the window -> old directory kept" "0 yes" "$rc $(pgdir umami-postgres/18_docker.aside)"
sed -i.bak "s/^epoch=.*/epoch=$(( $(date -u +%s) - 1860 ))/" "$snap/meta"
# Past the window, but without proof the new cluster is the state to keep.
sed -i.bak '/^migrated=/d' "$snap/meta"
run_driver "${UMAMI_MAJOR//caddy:2.9/caddy:2.12}"
check "  past the window without migrated=yes -> .aside kept" "0 yes 1" "$rc $(pgdir umami-postgres/18_docker.aside) $(grep -c 'never finished its restore' "$case_dir/out")"
echo migrated=yes >> "$snap/meta"
echo "commit=old gate=datastore-probe" > "$case_dir/.compose-rollback-failed"
run_driver "${UMAMI_MAJOR//caddy:2.9/caddy:2.13}"
check "  past the window with a rollback_failed flag -> .aside kept" "yes 1" "$(pgdir umami-postgres/18_docker.aside) $(grep -c 'ended rollback_failed' "$case_dir/out")"
run_driver "${UMAMI_MAJOR//caddy:2.9/caddy:2.11}"
check "  next sync past the window -> old directory removed, new one kept" "0 no yes" "$rc $(pgdir umami-postgres/18_docker.aside) $(pgdir umami-postgres/19_docker)"
check "  removed once" 1 "$(calls 'rm -rf /var/lib/postgresql/18/docker.aside')"

# The cutover fails before the old directory moves: store and consumers come back.
new_case major_dump_fails
run_driver "$UMAMI_MAJOR" ': > "$FAKE/pg_dump_fails"'
check "umami-postgres major, pg_dump fails at the cutover -> exit 3, gate datastore-major" "3 datastore-major" "$rc $(report gate)"
check "  consumer started again before the rollback sweep" yes "$( (( $(order_of 'stop umami$') < $(order_of 'compose-up --no-pull --force-recreate umami$') && $(order_of 'compose-up --no-pull --force-recreate umami$') < $(order_of ^sweep) )) && echo yes || echo no)"
check "  store untouched: old image, not recreated, nothing moved" "postgres:18.6-alpine 0 no 91" "$(running umami-postgres) $(calls 'up -d --no-deps --force-recreate umami-postgres') $(pgdir umami-postgres/18_docker.aside) $(count umami-postgres/umami/public.website_event)"

# A red gate inside the window puts the old directory back.
new_case major_red
: > "$case_dir/fake/new_writes"
run_driver "$UMAMI_MAJOR" "" "" 'rb_gate umami-postgres forced red'
check "umami-postgres major, red gate after the probe -> exit 3" 3 "$rc"
check "  compose file and previous image back" "postgres:18.6-alpine postgres:18.6-alpine" "$(umami_pg_image) $(running umami-postgres)"
check "  old directory opened again, the new major's writes gone" "91 52" "$(count umami-postgres/umami/public.website_event) $(count umami-postgres/umami/public.session)"
check "  new major's directory removed, the old one renamed back" "no yes no" "$(pgdir umami-postgres/19_docker) $(pgdir umami-postgres/18_docker) $(pgdir umami-postgres/18_docker.aside)"
check "  no pg_restore on the live store for the put-back" 1 "$(calls 'exec -T umami-postgres pg_restore -U umami --create')"
check "  consumer recreated after the put-back" yes "$( (( $(grep -n 'rm -rf /var/lib/postgresql/19/docker' "$case_dir/fake/calls" | tail -n1 | cut -d: -f1) < $(grep -n 'compose-up --no-pull --force-recreate umami$' "$case_dir/fake/calls" | tail -n1 | cut -d: -f1) )) && echo yes || echo no)"
check "  ledger: snapshot, shadow_green, cutover, migrated, restored" "umami-postgres snapshot,umami-postgres shadow_green,umami-postgres cutover,umami-postgres migrated,umami-postgres restored" "$(data_ledger)"

# The new image lacks an extension the old cluster uses: the shadow is red, so
# nothing moves on the live store.
new_case major_ext_missing
echo vector > "$case_dir/fake/db/postgres/glitchtip/.ext"
echo vector > "$case_dir/fake/missing_ext"
run_driver "$PG_MAIN_MAJOR"
check "postgres pg18 -> pg19 without vector -> exit 4, gate datastore-shadow, shadow_failed" "4 datastore-shadow shadow_failed" "$rc $(report gate) $(report outcome)"
check "  named in the log" 1 "$(grep -c 'extension vector of the old cluster is not in pgvector/pgvector:0.8.7-pg19' "$case_dir/out")"
check "  checked in the shadow, before any restore anywhere" "1 0" "$(calls "exec -i klai-datastore-shadow-postgres psql .*pg_available_extensions where name = 'vector'") $(calls 'pg_restore -U klai --create')"
check "  live untouched: no consumer stopped, no directory moved, no sweep" "0 no no 0" "$(calls 'compose .* stop ') $(pgdir postgres/19_docker) $(pgdir postgres/18_docker.aside) $(calls '^sweep')"
check "  previous image, counts as before, compose file as it was" "pgvector/pgvector:0.8.7-pg18 1 yes" "$(running postgres) $(count postgres/glitchtip/public.users_user) $(untouched)"
check "  ledger: snapshot, shadow_failed" "postgres snapshot,postgres shadow_failed" "$(data_ledger)"

# --- S8b: the other engines --------------------------------------------------
eng() { sed -n "s/^$2=//p" "$case_dir/fake/eng/$1"; }
image_of_case() { grep -A1 "^  $1:" "$case_dir/docker-compose.yml" | awk '/image:/ { print $2 }'; }
throwaways_ok() {
  # Every throwaway: --network none, the adhoc label, a memory limit; and removed.
  local runs bad
  runs="$(grep -c 'docker run -d --name klai-datastore-' "$case_dir/fake/calls" || true)"
  bad="$(grep 'docker run -d --name klai-datastore-' "$case_dir/fake/calls" | grep -vc -- '--network none --label klai.adhoc=datastore-restore --memory 6g --cpus 2' || true)"
  echo "$runs runs, $bad unconfined, $(grep -c 'docker rm -f klai-datastore-' "$case_dir/fake/calls" || true) removed"
}

# Shadow green, cutover green: Qdrant onto its new image with a fresh copy.
new_case shadow_green_cutover
run_driver "$QDRANT_BUMP"
snap="$(snapdir qdrant)"
check "qdrant v1.19.2 -> v1.20.0, shadow and cutover green -> exit 0" 0 "$rc"
check "  cold copy: stopped, copied, started, before the cutover" yes "$( (( $(order_of 'stop -t 120 qdrant') < $(order_of 'tar -cf') && $(order_of 'tar -cf') < $(order_of 'compose .* start qdrant') && $(order_of 'compose .* start qdrant') < $(order_of 'stop knowledge-ingest retrieval-api portal-api') )) && echo yes || echo no)"
check "  baseline read back from a throwaway of the old image" "collection klai_knowledge,count klai_knowledge 120" "$(paste -sd, "$snap/baseline.facts")"
check "  shadow: the snapshot in a throwaway of v1.20.0, before any consumer stopped" yes "$( (( $(order_of 'run -d --name klai-datastore-shadow-qdrant .*qdrant/qdrant:v1.20.0') < $(order_of 'stop knowledge-ingest retrieval-api portal-api') )) && echo yes || echo no)"
check "  throwaways (baseline, shadow) confined and removed" "2 runs, 0 unconfined, 2 removed" "$(throwaways_ok)"
check "  the API key travels in the environment, not in argv" "0 yes" "$(calls fake-qdrant-key) $( (( $(calls 'run --rm --network container:cid-qdrant -e T -e H') > 0 )) && echo yes || echo no)"
check "  cutover: consumers stopped, fresh copy, store started on v1.20.0 without the old image in between, consumers recreated, then the sweep" yes "$( (( $(order_of 'stop knowledge-ingest retrieval-api portal-api') < $(last_of 'tar -cf') && $(last_of 'tar -cf') < $(order_of 'up -d --no-deps --force-recreate qdrant') && $(calls 'compose .* start qdrant') == 1 && $(order_of 'up -d --no-deps --force-recreate qdrant') < $(order_of 'compose-up --no-pull --force-recreate knowledge-ingest retrieval-api portal-api') && $(order_of 'compose-up --no-pull --force-recreate knowledge-ingest retrieval-api portal-api') < $(order_of ^sweep) )) && echo yes || echo no)"
check "  probe reference: the facts read just before the fresh copy" "collection klai_knowledge,count klai_knowledge 120" "$(paste -sd, "$snap/cutover.facts")"
check "  store on v1.20.0, nothing put back" "qdrant/qdrant:v1.20.0 0" "$(running qdrant) $(calls 'volumes-from cid-qdrant -v')"
check "  ledger: snapshot, shadow_green, cutover, deployed" "qdrant snapshot,qdrant shadow_green,qdrant cutover,qdrant deployed" "$(data_ledger)"
check "  the window from the fresh copy to the verdict is in the ledger" measured "$(window_of qdrant)"

# Shadow red: Mongo's new image does not hold the copy's data.
new_case shadow_red
echo mongo:8.0.35 > "$case_dir/fake/bad_image"
run_driver "$MONGO_BUMP"
check "mongo 8.0.35 fails its shadow -> exit 4, gate datastore-shadow, shadow_failed" "4 datastore-shadow shadow_failed" "$rc $(report gate) $(report outcome)"
check "  report names the store" mongodb "$(report service)"
check "  event at warning level, the version named for the revert" 1 "$(grep -c '"level":"warning","event":"auto_rollback","outcome":"shadow_failed","deploy_service":"mongodb","gate":"datastore-shadow"' "$case_dir/logs/auto-rollback.log")"
check "  live untouched: consumers never stopped, store never recreated, previous image" "0 0 mongo:8.0.34" "$(calls 'stop librechat-getklai portal-api') $(calls 'up -d --no-deps') $(running mongodb)"
check "  files untouched: compose file as it was, no .prev, no failure flag, no sweep" "mongo:8.0.34 yes 0" "$(image_of_case mongodb) $(untouched) $(calls '^sweep')"
check "  ledger: snapshot, shadow_failed, then the compose line" "mongodb snapshot,mongodb shadow_failed shadow_failed" "$(data_ledger) $(tail -n1 "$case_dir/deploy-ledger.log" | cut -f7)"

# AT-c1, volume kind: Qdrant cutover probe red, the fresh copy and image back.
new_case at_c1_volume
echo qdrant/qdrant:v1.20.0 > "$case_dir/fake/bad_live_image"
# Written after the first snapshot: only the fresh copy holds it.
run_driver "$QDRANT_BUMP" 'sed -i.bak "s/^points=.*/points=130/" "$FAKE/eng/qdrant"'
check "qdrant v1.19.2 -> v1.20.0, cutover probe red -> exit 3" 3 "$rc"
check "  report gate" datastore-cutover "$(report gate)"
check "  consumers stopped before the volume is put back" yes "$( (( $(order_of 'stop knowledge-ingest retrieval-api portal-api') < $(order_of 'volumes-from cid-qdrant -v') )) && echo yes || echo no)"
check "  points as in the fresh copy, the write after the first snapshot kept" 130 "$(eng qdrant points)"
check "  store on its previous image" qdrant/qdrant:v1.19.2 "$(running qdrant)"
check "  consumers recreated after the restore" yes "$( (( $(order_of 'volumes-from cid-qdrant -v') < $(last_of 'compose-up --no-pull --force-recreate knowledge-ingest retrieval-api portal-api') )) && echo yes || echo no)"
check "  ledger: snapshot, shadow_green, cutover, restored" "qdrant snapshot,qdrant shadow_green,qdrant cutover,qdrant restored" "$(data_ledger)"
check "  the window from the fresh copy to the restore is in the ledger" measured "$(window_of qdrant)"

# AT-c1, rdb kind: Redis BGSAVE, cutover probe red, keys back.
new_case at_c1_rdb
echo redis:8.10.3-alpine > "$case_dir/fake/bad_live_image"
run_driver "$REDIS_BUMP"
check "redis 8.10.2 -> 8.10.3, cutover probe red -> exit 3" 3 "$rc"
check "  BGSAVE waited for and the dump copied out twice: snapshot, fresh" "2 2" "$(calls 'redis-cli BGSAVE') $(calls 'docker cp cid-redis:/data/dump.rdb')"
check "  baseline counts keys without a TTL" "count keys 57" "$(cat "$(snapdir redis)/baseline.facts")"
check "  the password travels in the environment, not in argv" "0" "$(calls fake-redis-pw)"
check "  dump put back through the AOF-aware path" 1 "$(calls 'volumes-from cid-redis -v .*sh -c')"
check "  keys as in the snapshot" 57 "$(eng redis keys)"
check "  store on its previous image" redis:8.10.2-alpine "$(running redis)"
check "  ledger: snapshot, shadow_green, cutover, restored" "redis snapshot,redis shadow_green,redis cutover,redis restored" "$(data_ledger)"

# AT-c1, definitions kind: RabbitMQ, the new node starts empty, red gate.
new_case at_c1_definitions
: > "$case_dir/fake/new_writes"
run_driver "$RABBIT_BUMP" "" "" 'rb_gate firecrawl-rabbitmq forced red'
check "rabbitmq 4.4.0 -> 4.4.1, red gate -> exit 3" 3 "$rc"
check "  definitions exported as the rabbitmq user: snapshot, fresh" 2 "$(calls 'exec -u rabbitmq cid-firecrawl-rabbitmq rabbitmqctl -q export_definitions')"
check "  queues imported into the previous image" "nuq_scrape nuq_crawl" "$(eng firecrawl-rabbitmq queues)"
check "  store on its previous image" rabbitmq:4.4.0-alpine "$(running firecrawl-rabbitmq)"
check "  ledger: snapshot, shadow_green, cutover, deployed, restored" "firecrawl-rabbitmq snapshot,firecrawl-rabbitmq shadow_green,firecrawl-rabbitmq cutover,firecrawl-rabbitmq deployed,firecrawl-rabbitmq restored" "$(data_ledger)"

# AT-c3 for an engine: red 31 minutes after the fresh snapshot -> nothing restored.
new_case at_c3_engine
: > "$case_dir/fake/new_writes"
run_driver "$REDIS_BUMP" "" 'sed -i.bak "s/^epoch=.*/epoch=$(( $(date -u +%s) - 1860 ))/" "${DS_SNAPSHOTS[0]}/meta"' 'rb_gate redis forced red'
check "redis red at +31 min -> exit 4" 4 "$rc"
check "  report gate" restore-window "$(report gate)"
check "  nothing put back" "0 0" "$(calls 'volumes-from cid-redis -v') $(calls 'stop -t 120 redis')"
check "  the new version's keys kept, new image in place" "62 redis:8.10.3-alpine" "$(eng redis keys) $(image_of_case redis)"

# Mongo: an FCV changed after the upgrade is never restored over.
new_case mongo_fcv
: > "$case_dir/fake/new_writes"
run_driver "$MONGO_BUMP" "" 'sed -i.bak "s/^fcv=.*/fcv=8.2/" "$FAKE/eng/mongodb"' 'rb_gate mongodb forced red'
check "mongo 8.0.34 -> 8.0.35, FCV raised to 8.2 -> exit 4" 4 "$rc"
check "  report gate" mongo-fcv "$(report gate)"
check "  report outcome" rollback_failed "$(report outcome)"
check "  nothing restored, new compose file left in place" "0 mongo:8.0.35" "$(calls 'volumes-from cid-mongodb -v') $(image_of_case mongodb)"
check "  failure flag written" yes "$([[ -e "$case_dir/.compose-rollback-failed" ]] && echo yes || echo no)"
check "  baseline holds document counts per collection" "count tenant_a.messages 40,count tenant_b.messages 7" "$(grep '^count ' "$(snapdir mongodb)/baseline.facts" | paste -sd, -)"

# FCV: raised at the start of a sync once no mongo snapshot is inside its window.
fcv_case() {
  new_case "$1"
  sed -i.bak 's/^version=.*/version=8.2.3/' "$case_dir/fake/eng/mongodb"
  mkdir -p "$case_dir/backups/pre-mongodb-20261029T080000Z"
  printf 'name=mongodb\nstore=mongodb\nengine=mongo\nepoch=%s\n' "$(( $(date -u +%s) - $2 ))" > "$case_dir/backups/pre-mongodb-20261029T080000Z/meta"
  [[ -z "${3:-}" ]] || : > "$case_dir/fake/$3"
  run_driver "${OLD_COMPOSE//caddy:2.9/caddy:2.10}"
}
fcv_case fcv_inside_window 600
check "mongo 8.2.3 on FCV 8.0, snapshot 10 min old -> FCV stays" "0 8.0 0" "$rc $(eng mongodb fcv) $(calls 'setFeatureCompatibilityVersion')"
check "  reason printed" 1 "$(grep -c 'mongodb has a snapshot inside its window' "$case_dir/out")"
fcv_case fcv_after_window 1860
check "mongo 8.2.3 on FCV 8.0, snapshot 31 min old -> FCV 8.2" "0 8.2" "$rc $(eng mongodb fcv)"
check "  raised before the snapshot step, ledger line" "mongodb fcv_raised" "$(data_ledger)"
fcv_case fcv_refused 1860 fcv_fails
check "setFeatureCompatibilityVersion fails -> the sync stops before any change" "1 0 caddy:2.9" "$rc $(calls '^sweep') $(image_of_case caddy)"

# A cold copy whose stop reports an error still starts the store again.
new_case stop_fails
: > "$case_dir/fake/stop_fails"
run_driver "$QDRANT_BUMP"
check "qdrant stop fails during the snapshot -> exit 4, gate snapshot, not_started" "4 snapshot not_started" "$rc $(report gate) $(report outcome)"
check "  store started again, nothing copied" "1 0" "$(calls 'compose .* start qdrant') $(calls 'tar -cf')"

# A snapshot that does not read back refuses the sync before anything changed.
new_case engine_snapshot_unreadable
: > "$case_dir/fake/corrupt_snapshot"
run_driver "$QDRANT_BUMP"
check "qdrant snapshot does not read back -> exit 4" 4 "$rc"
check "  report gate" snapshot "$(report gate)"
check "  compose file untouched, no sweep" "qdrant/qdrant:v1.19.2 0" "$(image_of_case qdrant) $(calls '^sweep')"

# Dry mode on a cold-copied engine: the real shadow, forced red, live untouched.
new_case dry_qdrant
echo qdrant > "$case_dir/.datastore-dry"
run_driver "$QDRANT_BUMP"
check "dry flag on qdrant -> shadow forced red, exit 4, shadow_failed" "4 datastore-shadow shadow_failed" "$rc $(report gate) $(report outcome)"
check "  the shadow itself ran on v1.20.0, then was forced red" "1 1" "$(calls 'run -d --name klai-datastore-shadow-qdrant .*qdrant/qdrant:v1.20.0') $(grep -c 'shadow: dry mode, forced red' "$case_dir/out")"
check "  flag file used once" no "$([[ -e "$case_dir/.datastore-dry" ]] && echo yes || echo no)"
check "  live untouched: one stop for the snapshot copy, no consumer stopped, no recreate, previous image" "1 0 0 qdrant/qdrant:v1.19.2" "$(calls 'stop -t 120 qdrant') $(calls 'stop knowledge-ingest') $(calls 'up -d --no-deps') $(running qdrant)"
check "  files untouched, no failure flag" "qdrant/qdrant:v1.19.2 yes" "$(image_of_case qdrant) $(untouched)"
check "  ledger: snapshot, shadow_failed" "qdrant snapshot,qdrant shadow_failed" "$(data_ledger)"

# crm-redis keeps no state: switched and probed, its consumers recreated, nothing saved.
new_case ephemeral
run_driver "$CRM_REDIS_BUMP"
check "crm-redis bump, green -> exit 0" 0 "$rc"
check "  no BGSAVE, no throwaway" "0 0" "$(calls BGSAVE) $(calls 'docker run -d')"
check "  consumers crm crm-worker recreated" 1 "$(calls 'compose-up --no-pull --force-recreate crm crm-worker$')"
check "  ledger: snapshot, shadow_green, cutover, deployed" "crm-redis snapshot,crm-redis shadow_green,crm-redis cutover,crm-redis deployed" "$(data_ledger)"

echo
if [[ "$failures" -eq 0 ]]; then
  echo "datastore-rollback: all cases passed"
else
  echo "datastore-rollback: $failures case(s) failed"
  exit 1
fi
