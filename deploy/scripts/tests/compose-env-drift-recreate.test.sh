#!/usr/bin/env bash
# Tests for compose-env-drift-recreate.sh, the env-drift sweep that used to live
# inline in deploy-compose.yml.
#
# No Docker daemon or systemd needed: docker, sudo and systemctl are stubs on
# PATH, and compose-up.sh is replaced by a stub in the case's scripts/ directory,
# which is where the script looks for it ($KLAI_COMPOSE_DIR/scripts). compose-up.sh
# has its own suite; this one checks what the sweep hands it and what happens
# around it.
#
# Run: bash deploy/scripts/tests/compose-env-drift-recreate.test.sh

set -euo pipefail

script_dir=$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)
target="$script_dir/compose-env-drift-recreate.sh"

work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT

failures=0
case_dir=""

mkdir -p "$work/bin"

cat > "$work/bin/docker" <<'STUB'
#!/usr/bin/env bash
if [[ "$*" == "compose config --services" ]]; then
  cat "$FIXTURES/services"; exit 0
fi
echo "stub: unexpected docker args: $*" >&2
exit 90
STUB

cat > "$work/bin/sudo" <<'STUB'
#!/usr/bin/env bash
exec "$@"
STUB

cat > "$work/bin/systemctl" <<'STUB'
#!/usr/bin/env bash
echo "systemctl $*" >> "$FIXTURES/calls"
if [[ "$1" == restart && -f "$FIXTURES/systemctl_restart_rc" ]]; then
  exit "$(cat "$FIXTURES/systemctl_restart_rc")"
fi
exit 0
STUB

chmod +x "$work/bin/docker" "$work/bin/sudo" "$work/bin/systemctl"

new_case() {
    case_dir="$work/$1"
    mkdir -p "$case_dir/fixtures" "$case_dir/scripts"
    : > "$case_dir/fixtures/calls"
    echo 0 > "$case_dir/fixtures/compose_up_rc"
    cat > "$case_dir/scripts/compose-up.sh" <<'STUB'
#!/usr/bin/env bash
echo "compose-up $*" >> "$FIXTURES/calls"
exit "$(cat "$FIXTURES/compose_up_rc")"
STUB
    chmod +x "$case_dir/scripts/compose-up.sh"
}

fx() { printf '%s\n' "$2" > "$case_dir/fixtures/$1"; }

run_target() {
    set +e
    PATH="$work/bin:$PATH" \
    KLAI_COMPOSE_DIR="$case_dir" \
    KLAI_DOCKER_BIN="$work/bin/docker" \
    FIXTURES="$case_dir/fixtures" \
        bash "$target" > "$case_dir/out" 2>&1
    rc=$?
    set -e
}

fail() {
    echo "  FAIL $1"
    sed 's/^/       /' "$case_dir/out"
    echo "       --- calls ---"
    sed 's/^/       /' "$case_dir/fixtures/calls"
    failures=$((failures + 1))
}

check() { if [[ "$2" == "$3" ]]; then echo "  ok   $1"; else fail "$1 (expected $2, got $3)"; fi; }
check_output() { if grep -qF -- "$2" "$case_dir/out"; then echo "  ok   $1"; else fail "$1 (output lacks: $2)"; fi; }
count_calls() { grep -c -- "$1" "$case_dir/fixtures/calls" || true; }

echo "compose-env-drift-recreate.sh"

# An empty list would turn compose-up.sh into an all-services `up -d`, litellm
# included, which skips the Prisma baseline gate litellm-hook-deploy.yml runs.
# litellm is the one service the sweep drops, so a list of only litellm resolves
# to nothing.
new_case no_services
fx services litellm
run_target
check "no env-drift services resolved -> 1" 1 "$rc"
check_output "  refuses the all-services compose up" "no env-drift services resolved"
check "  never called compose-up.sh" 0 "$(count_calls '^compose-up ')"
check "  firewall left alone (nothing was recreated)" 0 "$(count_calls '^systemctl ')"

new_case happy
printf 'caddy\nlitellm\nportal-api\n' > "$case_dir/fixtures/services"
run_target
check "services resolved, compose-up green -> 0" 0 "$rc"
check "  one compose-up for every service but litellm, without pulling" \
    "compose-up --no-pull caddy portal-api" "$(grep '^compose-up ' "$case_dir/fixtures/calls")"
check "  firewall re-applied exactly once" 1 "$(count_calls '^systemctl restart klai-harden-firewall.service$')"

# Docker can recreate a network before a later container fails, so the firewall
# is re-applied on the failure path too, and compose-up's exit code survives.
new_case compose_up_fails
printf 'caddy\nportal-api\n' > "$case_dir/fixtures/services"
fx compose_up_rc 3
run_target
check "compose-up fails -> its exit code" 3 "$rc"
check "  firewall still re-applied exactly once" 1 "$(count_calls '^systemctl restart klai-harden-firewall.service$')"

# A green recreate with stale bot-isolation rules is not a green deploy.
new_case firewall_fails
printf 'caddy\nportal-api\n' > "$case_dir/fixtures/services"
fx systemctl_restart_rc 1
run_target
check "compose-up green, firewall restart fails -> 1" 1 "$rc"
check_output "  says the firewall did not restart" "::error::klai-harden-firewall failed to restart"

echo
if [[ "$failures" -eq 0 ]]; then
    echo "compose-env-drift-recreate: all cases passed"
else
    echo "compose-env-drift-recreate: $failures case(s) failed"
    exit 1
fi
