#!/usr/bin/env bash
# Tests for datastore-rollback.sh (SPEC-INFRA-AUTO-ROLLBACK-001 S8a): snapshot
# before a Postgres or self-migrating image change, restore on a red gate within
# 30 minutes, nothing past it, one attempt.
#
# No Docker needed. A fake docker on PATH keeps each store as files
# (fake/db/<store>/<db>/<schema.table> holds a row count) and records every call;
# sudo du, df and flock are fakes too. compose-up.sh, the env-drift sweep and the
# Twenty check are stubs in each case's scripts/ directory. The driver replays the
# sync in deploy-compose.yml: snapshot, keep, overwrite, sweep, datastore gates.
# The pg_dump/pg_restore/psql command shapes were checked against a real
# postgres:18.6-alpine on 2026-10-28; the fake follows what that produced.
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
cat > "$bin/docker" <<'FAKE'
#!/usr/bin/env bash
F="$FAKE"
echo "docker $*" >> "$F/calls"
img_of() { awk -v s="$1" '/^  [a-z]/ { svc = $1; sub(/:$/, "", svc) } /^    image:/ && svc == s { print $2 }' "$KLAI_COMPOSE_DIR/docker-compose.yml"; }
in_store() {
  local t="$1" db="" sql="" a; shift
  local cmd="$1"; shift
  case "$cmd" in
    pg_isready) [[ ! -e "$F/pg_down" ]] ;;
    pg_dump)
      while (( $# )); do [[ "$1" == -d ]] && db="$2"; shift; done
      echo "FAKEDUMP $db"
      for a in "$F/db/$t/$db"/*; do [[ -e "$a" ]] && echo "T $(basename "$a") $(cat "$a")"; done ;;
    pg_restore)
      local list=0 data=0 create=0 n="" tb=""
      while (( $# )); do
        case "$1" in -l) list=1 ;; --data-only) data=1 ;; --create) create=1 ;; -n) n="$2" ;; -t) tb="$2" ;; esac
        shift
      done
      if (( data )); then
        awk '$1 == "T" { print "COPY " $2 " (id) FROM stdin;"; for (i = 0; i < $3; i++) print "row"; print "\\." }'
        # A dump that does not read back: pg_restore stops with exit 1.
        if [[ -e "$F/corrupt_dump" ]]; then exit 1; fi
      elif (( create )); then
        local first=1
        while read -r k name cnt; do
          if [[ "$k" == FAKEDUMP ]]; then db="$name"; mkdir -p "$F/db/$t/$db"; continue; fi
          if [[ -e "$F/restore_short" && "$first" == 1 ]]; then cnt=$((cnt - 1)); fi
          first=0
          echo "$cnt" > "$F/db/$t/$db/$name"
        done
        rm -f "$F/probe_red"
      fi ;;
    psql)
      while (( $# )); do case "$1" in -d) db="$2" ;; -c|-Atc) sql="$2" ;; esac; shift; done
      case "$sql" in
        "select datname"*) for a in "$F/db/$t"/*; do [[ "$(basename "$a")" == postgres ]] || basename "$a"; done ;;
        "DROP DATABASE"*) a="${sql#*\"}"; rm -rf "$F/db/$t/${a%%\"*}" ;;
        "select count(*)"*)
          [[ -e "$F/probe_red" ]] && exit 1
          a="${sql#*from \"}"; a="${a//\"/}"
          cat "$F/db/$t/$db/$a" 2>/dev/null ;;
      esac ;;
  esac
}
if [[ "$1" == compose ]]; then
  shift; [[ "$1" == --project-directory ]] && shift 2
  case "$1" in
    exec) shift; [[ "$1" == -T ]] && shift; t="$1"; shift; in_store "$t" "$@" ;;
    ps) for a in "$@"; do [[ "$a" == -* || "$a" == ps ]] || echo "cid-$a"; done ;;
    stop) : ;;
    up) svc="${!#}"; img_of "$svc" > "$F/running/$svc" ;;
  esac
elif [[ "$1" == exec ]]; then
  shift; [[ "$1" == -i ]] && shift; t="$1"; shift; in_store "$t" "$@"
elif [[ "$1" == inspect ]]; then
  fmt="$3"; shift 3
  for c in "$@"; do
    case "$fmt" in
      *Mounts*) echo "/var/lib/docker/volumes/${c#cid-}" ;;
      *Config.Image*) cat "$F/running/${c#cid-}" ;;
    esac
  done
elif [[ "$1" == run ]]; then
  while (( $# )); do [[ "$1" == --name ]] && mkdir -p "$F/db/$2"; shift; done
  echo ctr-id
fi
FAKE
cat > "$bin/sudo" <<'FAKE'
#!/usr/bin/env bash
# sudo -n du -sb <path>
printf '%s\t%s\n' "$(cat "$FAKE/du_bytes")" "${!#}"
FAKE
cat > "$bin/df" <<'FAKE'
#!/usr/bin/env bash
echo "Filesystem 1-blocks Used Available Capacity Mounted"
# Only the backup root has the fixture's free space; any other path plenty, so
# a check against the wrong filesystem passes and the test notices.
if [[ "${!#}" == */backups ]]; then avail="$(cat "$FAKE/df_avail")"; else avail=999999999999; fi
echo "/dev/fake 1000000000000 1 $avail 1% /"
FAKE
printf '#!/usr/bin/env bash\nexit 0\n' > "$bin/flock"
chmod +x "$bin"/*

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

echo
if [[ "$failures" -eq 0 ]]; then
  echo "datastore-rollback: all cases passed"
else
  echo "datastore-rollback: $failures case(s) failed"
  exit 1
fi
