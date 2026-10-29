#!/usr/bin/env bash
# The Vexa bot image is an env value, so nothing but vexa-bot-prepull.sh fetches
# it. A bump merged without the pull makes the first meeting fail, which is why
# Vexa could not automerge. These cases pin what lets it: the pull reads the
# image from the compose file of the commit being deployed, a failed pull is
# fatal, a present image is left alone, and the sync runs the helper before it
# replaces the first file on the host.
#
# Driven by a docker stub on PATH, no daemon required.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
SCRIPT="$REPO_ROOT/deploy/scripts/vexa-bot-prepull.sh"
WORKFLOW="$REPO_ROOT/.github/workflows/deploy-compose.yml"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
mkdir -p "$TMP/bin"

# STUB_PRESENT=<image> answers `image inspect`; STUB_PULL_FAILS=1 fails `pull`.
cat >"$TMP/bin/docker" <<'STUB'
#!/bin/sh
echo "$*" >> "$STUB_LOG"
case "$1 $2" in
  "image inspect") [ "$3" = "${STUB_PRESENT:-}" ] && exit 0 || exit 1 ;;
esac
if [ "$1" = pull ]; then
  [ "${STUB_PULL_FAILS:-}" = 1 ] && { echo "Error: manifest for $2 not found" >&2; exit 1; }
  exit 0
fi
exit 0
STUB
chmod +x "$TMP/bin/docker"

FAILURES=0
pass() { echo "ok    $1"; }
fail() { echo "FAIL  $1" >&2; FAILURES=$((FAILURES + 1)); }

compose() {
  printf 'services:\n  vexa12-runtime:\n    image: vexaai/v012-runtime:%s\n    environment:\n      BROWSER_IMAGE: vexaai/vexa-bot:%s\n' "$1" "$1" > "$TMP/compose.yml"
}

# run <label> <expected-exit> — env knobs come from the caller
run() {
  local label="$1" want="$2" rc
  : > "$STUB_LOG"
  set +e
  OUT="$(PATH="$TMP/bin:$PATH" bash "$SCRIPT" "$TMP/compose.yml" 2>&1)"; rc=$?
  set -e
  if [[ "$rc" -eq "$want" ]]; then pass "$label (exit $rc)"; else fail "$label: expected exit $want, got $rc: $OUT"; fi
}
export STUB_LOG="$TMP/docker.log"

# (a) the image comes from the compose file of the new commit
compose v9.9.9
STUB_PRESENT="" run "absent image is pulled" 0
grep -qx "pull vexaai/vexa-bot:v9.9.9" "$STUB_LOG" && pass "pulls the tag named in the compose file" || fail "no pull of vexaai/vexa-bot:v9.9.9: $(cat "$STUB_LOG")"
grep -q "vexaai/vexa-bot:v9.9.9" <<<"$OUT" && pass "prints the image it pulled" || fail "output does not name the image: $OUT"

# the real compose file must be readable by the helper too
set +e
real="$(PATH="$TMP/bin:$PATH" STUB_PRESENT=x bash "$SCRIPT" "$REPO_ROOT/deploy/docker-compose.yml" 2>&1)"; rc=$?
set -e
[[ $rc -eq 0 && "$real" == *"vexaai/vexa-bot:v"* ]] && pass "reads BROWSER_IMAGE from deploy/docker-compose.yml" || fail "real compose file: rc=$rc $real"

# (b) a failed pull is fatal
compose v0.0.0-missing
STUB_PRESENT="" STUB_PULL_FAILS=1 run "failed pull ends red" 1

# (c) a present image is a no-op
compose v9.9.9
STUB_PRESENT="vexaai/vexa-bot:v9.9.9" run "present image succeeds" 0
grep -q "^pull" "$STUB_LOG" && fail "present image was pulled again" || pass "present image is not pulled"

# no readable image name must fail loudly, not pass silently
printf 'services:\n  x:\n    image: busybox\n' > "$TMP/compose.yml"
run "compose without BROWSER_IMAGE fails" 1

# the sync runs the helper before it replaces the first file on the host
helper_line="$(grep -n 'vexa-bot-prepull.sh' "$WORKFLOW" | grep -v '^\s*[0-9]*:\s*#' | head -1 | cut -d: -f1)"
first_replace="$(grep -n 'rb_keep "/opt/klai/\$f"' "$WORKFLOW" | head -1 | cut -d: -f1)"
if [[ -n "$helper_line" && -n "$first_replace" && "$helper_line" -lt "$first_replace" ]]; then
  pass "workflow pulls (line $helper_line) before the first file is replaced (line $first_replace)"
else
  fail "workflow does not run vexa-bot-prepull.sh before the first rb_keep (helper=$helper_line, replace=$first_replace)"
fi

[[ $FAILURES -eq 0 ]] || { echo "$FAILURES failure(s)" >&2; exit 1; }
echo "all vexa-bot-prepull cases passed"
