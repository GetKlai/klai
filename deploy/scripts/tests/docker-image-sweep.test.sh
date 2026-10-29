#!/usr/bin/env bash
# Tests for docker-image-sweep.sh: a fake docker answers `images`, `ps -aq`,
# `inspect` and records every `image rm`, so the keep rules are checked one by
# one against synthetic image names (no real registry or tenant name).
#
# Run: bash deploy/scripts/tests/docker-image-sweep.test.sh
set -uo pipefail

script_dir=$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)
target="$script_dir/docker-image-sweep.sh"
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
failures=0
check() { local desc="$1"; shift; if "$@"; then echo "ok   - $desc"; else echo "FAIL - $desc"; failures=$((failures + 1)); fi; }

# Ages are written as docker prints them: local time with offset and zone name.
stamp() { date -u -d "@$(( $(date -u +%s) - $1 * 3600 ))" '+%Y-%m-%d %H:%M:%S +0000 UTC' 2>/dev/null \
    || date -u -r "$(( $(date -u +%s) - $1 * 3600 ))" '+%Y-%m-%d %H:%M:%S +0000 UTC'; }
old=$(stamp 72); young=$(stamp 3)

cat > "$work/images" <<IMG
sha256:aaa	ghcr.io/example/portal-api:oldsha	$old
sha256:bbb	ghcr.io/example/portal-api:livesha	$old
sha256:ccc	klai/portal-api:previous	$old
sha256:ddd	klai/rollback-canary:1.0-local	$old
sha256:eee	ghcr.io/getklai/crawl4ai:local	$old
sha256:fff	mongo:8.0.34	$old
sha256:ggg	mongo:8.1.3	$old
sha256:hhh	ghcr.io/example/knowledge-ingest:freshsha	$young
sha256:iii	<none>:<none>	$old
sha256:jjj	redis:8.9.0-alpine	$old
IMG
cat > "$work/docker" <<'STUB'
#!/usr/bin/env bash
case "$1 ${2:-}" in
  "images --no-trunc") awk -F'\t' '{print $1"\t"$2"\t"$3}' "$STATE/images" ;;
  "ps -aq") echo c1 ;;
  "inspect -f") echo sha256:bbb ;;
  "image rm") echo "$3" >> "$STATE/removed"; [[ "$3" == redis:* ]] && exit 1; exit 0 ;;
  *) echo "stub: unexpected docker args: $*" >&2; exit 90 ;;
esac
STUB
chmod +x "$work/docker"
mkdir -p "$work/compose"
printf '  mongodb:\n    image: mongo:8.1.3\n' > "$work/compose/docker-compose.yml"
printf '  mongodb:\n    image: mongo:8.0.34\n' > "$work/compose/docker-compose.yml.prev"

out=$(STATE="$work" KLAI_DOCKER_BIN="$work/docker" KLAI_COMPOSE_DIR="$work/compose" bash "$target" 2>&1); rc=$?
removed=$(cat "$work/removed" 2>/dev/null)

check "exit 0 even though one removal failed" test "$rc" = 0
check "an old unused tag is removed" grep -qx 'ghcr.io/example/portal-api:oldsha' <<< "$removed"
check "the image a container uses stays" bash -c '! grep -q livesha <<< "$1"' _ "$removed"
check "klai/* tags stay (:previous and the canary)" bash -c '! grep -q "^klai/" <<< "$1"' _ "$removed"
check "the locally built crawl4ai stays" bash -c '! grep -q crawl4ai <<< "$1"' _ "$removed"
check "tags in the current and previous compose file stay" bash -c '! grep -q "^mongo:" <<< "$1"' _ "$removed"
check "an image younger than 24h stays" bash -c '! grep -q freshsha <<< "$1"' _ "$removed"
check "untagged images are left to docker image prune" bash -c '! grep -q none <<< "$1"' _ "$removed"
check "a failed removal is reported and counted" grep -q 'could not remove redis:8.9.0-alpine' <<< "$out"
check "summary line" grep -q 'image sweep: removed 1, kept 7, failed 1' <<< "$out"

if (( failures > 0 )); then echo "docker-image-sweep: $failures check(s) failed"; exit 1; fi
echo "docker-image-sweep: all checks passed"
