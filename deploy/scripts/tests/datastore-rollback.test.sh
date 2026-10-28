#!/usr/bin/env bash
# Tests for datastore-rollback.sh (SPEC-INFRA-AUTO-ROLLBACK-001 S8a and S8b):
# snapshot before a data store or self-migrating image change, restore on a red
# gate within 30 minutes, nothing past it, one attempt.
#
# No Docker needed. The fakes in datastore-fakes.sh keep each store as files and
# record every call. compose-up.sh, the env-drift sweep and the Twenty check are
# stubs in each case's scripts/ directory. The driver replays the sync in
# deploy-compose.yml: snapshot, keep, overwrite, sweep, datastore gates.
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

# run_driver <new compose> [after-snapshot] [before-sweep] [after-gates]
run_driver() {
  printf '%s' "$1" > "$case_dir/new-compose.yml"
  cat > "$case_dir/driver.sh" <<DRIVER
set -e
source "$helper"
rb_init bbb222
ds_snapshot_changed "\$KLAI_COMPOSE_DIR/docker-compose.yml" "\$KLAI_COMPOSE_DIR/new-compose.yml"
${2:-}
rb_keep "\$KLAI_COMPOSE_DIR/docker-compose.yml"
cp "\$KLAI_COMPOSE_DIR/new-compose.yml" "\$KLAI_COMPOSE_DIR/docker-compose.yml"
red() { echo "red: \$*"; return 1; }
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

# --- green store bump: snapshot, consumers recreated after the sweep ----------
new_case green_store
run_driver "$UMAMI_BUMP"
check "umami-postgres 18.6 -> 18.7, probe green -> exit 0" 0 "$rc"
snap="$(snapdir umami-postgres)"
check "  snapshot dir pre-umami-postgres-<ts> with one dump per database" yes "$([[ -s "$snap/umami.dump" && ! -e "$snap/postgres.dump" ]] && echo yes || echo no)"
check "  snapshot dir readable for the owner only" 700 "$(stat -c %a "$snap" 2>/dev/null || stat -f %Lp "$snap")"
check "  baseline counts read from the dump" "umami public.website_event 91,umami public.session 52" "$(paste -sd, "$snap/baseline.counts")"
check "  ledger: data snapshot line" "umami-postgres snapshot" "$(data_ledger)"
check "  dump taken before the sweep recreated anything" yes "$( (( $(order_of pg_dump) < $(order_of ^sweep) )) && echo yes || echo no)"
check "  consumer umami force-recreated after the sweep" 1 "$(calls 'compose-up --no-pull --force-recreate umami$')"

# --- AT-c1: store bump, probe red within the window -> data and image back ----
new_case at_c1_store
: > "$case_dir/fake/new_writes"
: > "$case_dir/fake/probe_red"
run_driver "$UMAMI_BUMP"
check "umami-postgres bump, probe red -> exit 3 (rolled_back)" 3 "$rc"
check "  report gate" datastore-probe "$(report gate)"
check "  report names the store" umami-postgres "$(report service)"
check "  compose file restored" postgres:18.6-alpine "$(umami_pg_image)"
check "  store runs its previous image" postgres:18.6-alpine "$(running umami-postgres)"
check "  consumer stopped before the restore" yes "$( (( $(order_of 'compose --project-directory .* stop umami') < $(order_of 'DROP DATABASE') )) && echo yes || echo no)"
check "  store recreated on its previous image before the restore" yes "$( (( $(order_of 'up -d --no-deps --force-recreate umami-postgres') < $(order_of 'DROP DATABASE') )) && echo yes || echo no)"
check "  writes of the new version gone: counts as in the snapshot" "91 52" "$(count umami-postgres/umami/public.website_event) $(count umami-postgres/umami/public.session)"
check "  consumer force-recreated after the restore" yes "$( (( $(order_of 'DROP DATABASE') < $(grep -n 'compose-up --no-pull --force-recreate umami$' "$case_dir/fake/calls" | tail -n1 | cut -d: -f1) )) && echo yes || echo no)"
check "  ledger: snapshot, then restored" "umami-postgres snapshot,umami-postgres restored" "$(data_ledger)"
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
check "  report outcome" rollback_failed "$(report outcome)"
check "  compose file untouched" postgres:18.6-alpine "$(umami_pg_image)"
check "  no .prev made: the run stopped before the first file" no "$([[ -e "$case_dir/docker-compose.yml.prev" ]] && echo yes || echo no)"
check "  no dump, no recreate" "0 0" "$(calls pg_dump) $(calls '^sweep')"
check "  no failure flag: the host is still in its verified state" no "$([[ -e "$case_dir/.compose-rollback-failed" ]] && echo yes || echo no)"

# --- AT-c3: red 31 minutes after the snapshot -> nothing restored -------------
new_case at_c3
: > "$case_dir/fake/new_writes"
: > "$case_dir/fake/probe_red"
run_driver "$UMAMI_BUMP" 'sed -i.bak "s/^epoch=.*/epoch=$(( $(date -u +%s) - 1860 ))/" "${DS_SNAPSHOTS[0]}/meta"'
check "red gate at +31 min -> exit 4" 4 "$rc"
check "  report gate" restore-window "$(report gate)"
check "  nothing dropped or stopped" "0 0" "$(calls 'DROP DATABASE') $(calls 'compose .* stop ')"
check "  new compose file left in place" postgres:18.7-alpine "$(umami_pg_image)"
check "  the new version's writes kept" 96 "$(count umami-postgres/umami/public.website_event)"

# --- count mismatch after the restore -> rollback_failed, one attempt (R6) -----
new_case mismatch
: > "$case_dir/fake/probe_red"
: > "$case_dir/fake/restore_short"
run_driver "$UMAMI_BUMP"
check "restored counts differ from the snapshot -> exit 4" 4 "$rc"
check "  report gate is the red gate" datastore-probe "$(report gate)"
check "  one restore attempt" 1 "$(calls 'DROP DATABASE')"
check "  consumer not started on unverified data" 0 "$(sed -n "$(order_of 'DROP DATABASE'),\$p" "$case_dir/fake/calls" | grep -c 'compose-up --no-pull --force-recreate umami$' || true)"
check "  ledger: restore_failed" "umami-postgres snapshot,umami-postgres restore_failed" "$(data_ledger)"
check "  failure flag written" yes "$([[ -e "$case_dir/.compose-rollback-failed" ]] && echo yes || echo no)"

# --- red before the new image started: files back, data untouched -------------
new_case red_before_sweep
run_driver "$UMAMI_BUMP" "" 'rb_gate caddy sync-recreate red caddy'
check "red gate before the sweep -> exit 3" 3 "$rc"
check "  no data restore: the new version never ran" 0 "$(calls 'DROP DATABASE')"
check "  ledger: snapshot, not_needed" "umami-postgres snapshot,umami-postgres not_needed" "$(data_ledger)"

# --- dry mode: real snapshot, forced red, restore into a throwaway -------------
new_case dry
echo umami-postgres > "$case_dir/.datastore-dry"
: > "$case_dir/fake/new_writes"
run_driver "$UMAMI_BUMP"
check "dry mode -> forced red, exit 3" 3 "$rc"
check "  flag file used once" no "$([[ -e "$case_dir/.datastore-dry" ]] && echo yes || echo no)"
check "  restore went to the throwaway container" 1 "$(calls 'exec -i klai-datastore-dry-umami-postgres pg_restore')"
check "  live store never dropped or stopped" "0 0" "$(calls 'exec -T umami-postgres psql .*DROP') $(calls 'compose .* stop ')"
check "  live data keeps the new version's writes" 96 "$(count umami-postgres/umami/public.website_event)"
check "  throwaway holds the snapshot counts" 91 "$(count klai-datastore-dry-umami-postgres/umami/public.website_event)"
check "  live store back on its previous image" postgres:18.6-alpine "$(running umami-postgres)"
check "  ledger: dry_restored" "umami-postgres snapshot,umami-postgres dry_restored" "$(data_ledger)"

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

# AT-c1, volume kind: Qdrant cold copy, red probe, data and image back.
new_case at_c1_volume
: > "$case_dir/fake/new_writes"
: > "$case_dir/fake/probe_red"
run_driver "$QDRANT_BUMP"
check "qdrant v1.19.2 -> v1.20.0, probe red -> exit 3" 3 "$rc"
snap="$(snapdir qdrant)"
check "  report gate" datastore-probe "$(report gate)"
check "  cold copy: stopped, copied, started, before the sweep" yes "$( (( $(order_of 'stop -t 120 qdrant') < $(order_of 'tar -cf') && $(order_of 'tar -cf') < $(order_of 'compose .* start qdrant') && $(order_of 'compose .* start qdrant') < $(order_of ^sweep) )) && echo yes || echo no)"
check "  baseline read back from a throwaway of the old image" "collection klai_knowledge,count klai_knowledge 120" "$(paste -sd, "$snap/baseline.facts")"
check "  throwaway confined and removed" "1 runs, 0 unconfined, 1 removed" "$(throwaways_ok)"
check "  the API key travels in the environment, not in argv" "0 yes" "$(calls fake-qdrant-key) $( (( $(calls 'run --rm --network container:cid-qdrant -e T -e H') > 0 )) && echo yes || echo no)"
check "  consumers stopped before the volume is put back" yes "$( (( $(order_of 'stop knowledge-ingest retrieval-api portal-api') < $(order_of 'volumes-from cid-qdrant -v') )) && echo yes || echo no)"
check "  points as in the snapshot, the new version's gone" 120 "$(eng qdrant points)"
check "  store on its previous image" qdrant/qdrant:v1.19.2 "$(running qdrant)"
check "  consumers recreated after the restore" yes "$( (( $(order_of 'volumes-from cid-qdrant -v') < $(grep -n 'compose-up --no-pull --force-recreate knowledge-ingest retrieval-api portal-api' "$case_dir/fake/calls" | tail -n1 | cut -d: -f1) )) && echo yes || echo no)"
check "  ledger: snapshot, restored" "qdrant snapshot,qdrant restored" "$(data_ledger)"

# AT-c1, rdb kind: Redis BGSAVE, red probe, keys back.
new_case at_c1_rdb
: > "$case_dir/fake/new_writes"
: > "$case_dir/fake/probe_red"
run_driver "$REDIS_BUMP"
check "redis 8.10.2 -> 8.10.3, probe red -> exit 3" 3 "$rc"
check "  BGSAVE waited for, dump copied out" "1 1" "$(calls 'redis-cli BGSAVE') $(calls 'docker cp cid-redis:/data/dump.rdb')"
check "  baseline counts keys without a TTL" "count keys 57" "$(cat "$(snapdir redis)/baseline.facts")"
check "  the password travels in the environment, not in argv" "0" "$(calls fake-redis-pw)"
check "  dump put back through the AOF-aware path" 1 "$(calls 'volumes-from cid-redis -v .*sh -c')"
check "  keys as in the snapshot" 57 "$(eng redis keys)"
check "  store on its previous image" redis:8.10.2-alpine "$(running redis)"
check "  ledger: snapshot, restored" "redis snapshot,redis restored" "$(data_ledger)"

# AT-c1, definitions kind: RabbitMQ, the new node starts empty, red gate.
new_case at_c1_definitions
: > "$case_dir/fake/new_writes"
run_driver "$RABBIT_BUMP" "" "" 'rb_gate firecrawl-rabbitmq forced red'
check "rabbitmq 4.4.0 -> 4.4.1, red gate -> exit 3" 3 "$rc"
check "  definitions exported as the rabbitmq user" 1 "$(calls 'exec -u rabbitmq cid-firecrawl-rabbitmq rabbitmqctl -q export_definitions')"
check "  queues imported into the previous image" "nuq_scrape nuq_crawl" "$(eng firecrawl-rabbitmq queues)"
check "  store on its previous image" rabbitmq:4.4.0-alpine "$(running firecrawl-rabbitmq)"
check "  ledger: snapshot, restored" "firecrawl-rabbitmq snapshot,firecrawl-rabbitmq restored" "$(data_ledger)"

# AT-c3 for an engine: red 31 minutes after the snapshot -> nothing restored.
new_case at_c3_engine
: > "$case_dir/fake/new_writes"
: > "$case_dir/fake/probe_red"
run_driver "$REDIS_BUMP" 'sed -i.bak "s/^epoch=.*/epoch=$(( $(date -u +%s) - 1860 ))/" "${DS_SNAPSHOTS[0]}/meta"'
check "redis red at +31 min -> exit 4" 4 "$rc"
check "  report gate" restore-window "$(report gate)"
check "  nothing put back, nothing stopped" "0 0" "$(calls 'volumes-from cid-redis -v') $(calls 'compose .* stop ')"
check "  the new version's keys kept, new image in place" "62 redis:8.10.3-alpine" "$(eng redis keys) $(image_of_case redis)"

# Mongo: an FCV changed after the upgrade is never restored over.
new_case mongo_fcv
: > "$case_dir/fake/new_writes"
run_driver "$MONGO_BUMP" "" 'sed -i.bak "s/^fcv=.*/fcv=8.2/" "$FAKE/eng/mongodb"'
check "mongo 8.0.34 -> 8.0.35, FCV raised to 8.2 -> exit 4" 4 "$rc"
check "  report gate" mongo-fcv "$(report gate)"
check "  report outcome" rollback_failed "$(report outcome)"
check "  nothing restored, new compose file left in place" "0 mongo:8.0.35" "$(calls 'volumes-from cid-mongodb -v') $(image_of_case mongodb)"
check "  failure flag written" yes "$([[ -e "$case_dir/.compose-rollback-failed" ]] && echo yes || echo no)"

# A snapshot that does not read back refuses the sync before anything changed.
new_case engine_snapshot_unreadable
: > "$case_dir/fake/corrupt_snapshot"
run_driver "$QDRANT_BUMP"
check "qdrant snapshot does not read back -> exit 4" 4 "$rc"
check "  report gate" snapshot "$(report gate)"
check "  compose file untouched, no sweep" "qdrant/qdrant:v1.19.2 0" "$(image_of_case qdrant) $(calls '^sweep')"

# Dry mode on Redis: forced red, restore into a throwaway, live data kept.
new_case dry_redis
echo redis > "$case_dir/.datastore-dry"
: > "$case_dir/fake/new_writes"
run_driver "$REDIS_BUMP"
check "dry mode on redis -> forced red, exit 3" 3 "$rc"
check "  restore went to a throwaway" 1 "$(calls 'docker run -d --name klai-datastore-dry-redis')"
check "  live store never put back or stopped" "0 0" "$(calls 'volumes-from cid-redis -v') $(calls 'compose .* stop ')"
check "  live keeps the new version's keys" 62 "$(eng redis keys)"
check "  live store back on its previous image" redis:8.10.2-alpine "$(running redis)"
check "  throwaways confined and removed" "2 runs, 0 unconfined, 2 removed" "$(throwaways_ok)"
check "  ledger: dry_restored" "redis snapshot,redis dry_restored" "$(data_ledger)"

# Dry mode is refused for a volume engine.
new_case dry_qdrant_refused
echo qdrant > "$case_dir/.datastore-dry"
run_driver "$QDRANT_BUMP"
check "dry flag on qdrant -> refused, normal green run" 0 "$rc"
check "  refusal printed, flag removed" "1 no" "$(grep -c 'dry mode is refused for qdrant' "$case_dir/out") $([[ -e "$case_dir/.datastore-dry" ]] && echo yes || echo no)"

# crm-redis keeps no state: probed and its consumers recreated, nothing saved.
new_case ephemeral
run_driver "$CRM_REDIS_BUMP"
check "crm-redis bump, green -> exit 0" 0 "$rc"
check "  no BGSAVE, no throwaway" "0 0" "$(calls BGSAVE) $(calls 'docker run -d')"
check "  consumers crm crm-worker recreated" 1 "$(calls 'compose-up --no-pull --force-recreate crm crm-worker$')"

echo
if [[ "$failures" -eq 0 ]]; then
  echo "datastore-rollback: all cases passed"
else
  echo "datastore-rollback: $failures case(s) failed"
  exit 1
fi
