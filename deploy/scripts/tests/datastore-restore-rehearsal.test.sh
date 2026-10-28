#!/usr/bin/env bash
# Tests for datastore-restore-rehearsal.sh (SPEC-INFRA-AUTO-ROLLBACK-001 S8b,
# AT-c4): every store in the inventory restored into a throwaway container and
# probed; one store whose restore fails makes the run red while the others are
# still reported.
#
# No Docker needed: the fakes in datastore-fakes.sh. The real path was run
# against Redis 8.10, Qdrant 1.19, Meilisearch 1.55, Mongo 8.0, RabbitMQ 4.4
# and Garage 2.4 containers on 2026-10-28.
#
# Run: bash deploy/scripts/tests/datastore-restore-rehearsal.test.sh

set -euo pipefail

script_dir=$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)
repo_root=$(CDPATH='' cd -- "$script_dir/../.." && pwd)
rehearsal="$script_dir/datastore-restore-rehearsal.sh"

work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
bin="$work/bin"
# shellcheck source=deploy/scripts/tests/datastore-fakes.sh
source "$script_dir/tests/datastore-fakes.sh"
ds_fakes_install "$bin"

failures=0
case_dir=""
rc=0

new_case() {
  case_dir="$work/$1"
  local f="$case_dir/fake"
  mkdir -p "$f/running" "$f/db/umami-postgres/umami" "$case_dir/backups"
  : > "$f/calls"
  # The real compose file: the rehearsal reads every store's image from it.
  cp "$repo_root/deploy/docker-compose.yml" "$case_dir/docker-compose.yml"
  awk '/^  [a-z]/ { svc = $1; sub(/:$/, "", svc) } /^    image:/ { print $2 > (dir "/" svc) }' dir="$f/running" "$case_dir/docker-compose.yml"
  ds_fakes_engines "$f"
  echo 91 > "$f/db/umami-postgres/umami/public.website_event"
  echo 52 > "$f/db/umami-postgres/umami/public.session"
  echo 1000 > "$f/du_bytes"
  echo 1000000 > "$f/df_avail"
}

# A snapshot as a sync leaves it: meta with epoch, the artifact, the baseline.
old_snapshot() {
  local store="$1" engine="$2" file="$3" baseline="$4" dir
  dir="$case_dir/backups/pre-$store-20261001T020000Z"
  mkdir -p "$dir"
  printf 'name=%s\nstore=%s\nengine=%s\nconsumers=\nstore_image=%s\nepoch=1790000000\n' \
    "$store" "$store" "$engine" "$(cat "$case_dir/fake/running/$store")" > "$dir/meta"
  cp "$case_dir/fake/eng/$store" "$dir/$file"
  printf '%s\n' "$baseline" > "$dir/baseline.facts"
  echo "$dir"
}

run_rehearsal() {
  set +e
  PATH="$bin:$PATH" FAKE="$case_dir/fake" KLAI_COMPOSE_DIR="$case_dir" bash "$rehearsal" "$@" > "$case_dir/out" 2>&1
  rc=$?
  set -e
}

fail() {
  echo "  FAIL $1"
  sed 's/^/       /' "$case_dir/out"
  failures=$((failures + 1))
}
check() { if [[ "$2" == "$3" ]]; then echo "  ok   $1"; else fail "$1 (expected '$2', got '$3')"; fi; }
calls() { grep -c -- "$1" "$case_dir/fake/calls" || true; }
events() {
  python3 -c '
import json, sys
for line in open(sys.argv[1]):
    e = json.loads(line)
    print(e["store"], e["outcome"])' "$case_dir/logs/auto-rollback.log" 2>/dev/null | paste -sd, -
}

echo "datastore-restore-rehearsal.sh"

# --- every store, none with a snapshot yet -------------------------------------
new_case all_fresh
run_rehearsal
# shellcheck source=deploy/scripts/datastore-rollback.sh
expected="$(RB_DIR=/nonexistent; source "$script_dir/datastore-rollback.sh"
  for s in $(ds_store_names); do [[ "$(ds_kind "$(ds_engine "$s")")" == none ]] || echo "$s ok"; done | paste -sd, -)"
check "no snapshots anywhere -> exit 0" 0 "$rc"
check "  one ok event per store with state, in inventory order" "$expected" "$(events)"
check "  crm-redis keeps no state and is skipped" 1 "$(grep -c 'crm-redis keeps no state' "$case_dir/out")"
check "  fresh snapshots removed afterwards" "" "$(ls "$case_dir/backups")"
check "  the deploy ledger is not touched" no "$([[ -e "$case_dir/deploy-ledger.log" ]] && echo yes || echo no)"
runs="$(grep -c 'docker run -d .*--name klai-datastore-' "$case_dir/fake/calls")"
check "  every throwaway on --network none, labelled, limited" 0 "$(grep 'docker run -d .*--name klai-datastore-' "$case_dir/fake/calls" | grep -vc -- '--network none --label klai.adhoc=datastore-restore --memory 6g --cpus 2' || true)"
check "  every engine throwaway removed, every Postgres one stopped" yes "$( (( $(calls 'docker rm -f klai-datastore-') + $(calls 'docker stop klai-datastore-') == runs )) && echo yes || echo no)"
check "  no throwaway mounts a live volume" 0 "$(grep 'docker run -d .*--name klai-datastore-' "$case_dir/fake/calls" | grep -c -- '--volumes-from' || true)"

# --- an existing snapshot is restored, not replaced ----------------------------
new_case existing
snap="$(old_snapshot redis redis dump.rdb 'count keys 57')"
run_rehearsal redis
check "redis with a snapshot from a sync -> exit 0" 0 "$rc"
check "  no new BGSAVE" 0 "$(calls BGSAVE)"
check "  restored from that snapshot" 1 "$(grep -c "restoring $snap" "$case_dir/out")"
check "  the snapshot is kept" yes "$([[ -d "$snap" ]] && echo yes || echo no)"
check "  event ok" "redis ok" "$(events)"

# --- a snapshot whose sync never finished is not used -------------------------
new_case incomplete
snap="$(old_snapshot redis redis dump.rdb 'count keys 57')"
sed -i.bak '/^epoch=/d' "$snap/meta"
run_rehearsal redis
check "redis with only an unfinished snapshot -> a fresh one, exit 0" "0 1" "$rc $(calls BGSAVE)"

# --- AT-c4: one store fails, the others are still rehearsed and reported -------
new_case one_fails
old_snapshot qdrant qdrant volume.tar $'collection klai_knowledge\ncount klai_knowledge 120' > /dev/null
# A dump that no longer holds what its baseline says it held.
snap="$(old_snapshot redis redis dump.rdb 'count keys 57')"
echo "keys=40" > "$snap/dump.rdb"
old_snapshot firecrawl-rabbitmq rabbitmq definitions.json 'queue nuq_scrape' > /dev/null
python3 -c 'import json; print(json.dumps({"queues": [{"name": "nuq_scrape"}]}))' > "$case_dir/backups/pre-firecrawl-rabbitmq-20261001T020000Z/definitions.json"
run_rehearsal qdrant redis firecrawl-rabbitmq
check "redis restore differs from its baseline -> exit 1" 1 "$rc"
check "  every store reported, redis failed" "qdrant ok,redis failed,firecrawl-rabbitmq ok" "$(events)"
check "  the difference is printed" 1 "$(grep -c '^< count keys 57' "$case_dir/out")"
check "  the run names the failed store" 1 "$(grep -c 'rehearsal failed for: redis$' "$case_dir/out")"

# --- no room for the throwaway volumes: failed, existing snapshot or not ------
new_case no_disk
old_snapshot redis redis dump.rdb 'count keys 57' > /dev/null
echo 1 > "$case_dir/fake/df_avail"
run_rehearsal redis qdrant
check "no disk -> exit 1, nothing stopped, no throwaway" "1 0 0" "$rc $(calls 'stop -t 120 qdrant') $(calls 'docker run -d')"
check "  both reported failed" "redis failed,qdrant failed" "$(events)"

# --- a name outside the inventory is refused ----------------------------------
new_case unknown
run_rehearsal caddy
check "store not in the inventory -> exit 1, nothing run" "1 0" "$rc $(calls 'docker run')"

echo
if [[ "$failures" -eq 0 ]]; then
  echo "datastore-restore-rehearsal: all cases passed"
else
  echo "datastore-restore-rehearsal: $failures case(s) failed"
  exit 1
fi
