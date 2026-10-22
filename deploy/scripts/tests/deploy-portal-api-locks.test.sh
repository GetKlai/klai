#!/usr/bin/env bash
# deploy-portal-api.sh must hold both host locks before `alembic upgrade head`.
#
# compose-up.sh waits up to 20 minutes for a measurement round and 15 for the
# container lock. When only compose-up.sh took them, the migration ran first and
# the live portal-api served the migrated schema with its old code for that
# whole wait. Migration and recreate belong inside one hold.
#
# Run: bash deploy/scripts/tests/deploy-portal-api-locks.test.sh
set -euo pipefail

script_dir=$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
mkdir -p "$work/bin" "$work/compose"

# Every docker and flock call lands in one file, in order.
cat >"$work/bin/docker" <<'STUB'
#!/usr/bin/env bash
echo "docker $*" >> "$CALLS"
exit 0
STUB

cat >"$work/bin/flock" <<'STUB'
#!/usr/bin/env bash
fd="${*: -1}"
if [[ -e "/proc/$$/fd/$fd" ]]; then
  lock="$(readlink "/proc/$$/fd/$fd")"
else
  lock="$(python3 -c 'import fcntl, sys; print(fcntl.fcntl(int(sys.argv[1]), fcntl.F_GETPATH, bytes(1024)).rstrip(b"\0").decode())' "$fd")"
fi
echo "flock $* lock=$(basename "$lock")" >> "$CALLS"
exit 0
STUB

# Room on every filesystem, so the disk guard lets the deploy start.
cat >"$work/bin/df" <<'STUB'
#!/usr/bin/env bash
echo "Filesystem 1024-blocks Used Available Capacity Mounted on"
for _ in 1 2 3; do echo "/dev/md2 456519844 403000000 53519844 89% /"; done
STUB
chmod +x "$work/bin/docker" "$work/bin/flock" "$work/bin/df"

# The deploy ends at /opt/klai/scripts/compose-up.sh, which does not exist here;
# the order of the calls before it is what this test is about.
set +e
PATH="$work/bin:$PATH" KLAI_COMPOSE_DIR="$work/compose" CALLS="$work/calls" \
    bash "$script_dir/deploy-portal-api.sh" >"$work/out" 2>&1
set -e

order="$(grep -E '^flock |--entrypoint alembic' "$work/calls" | sed -E 's/^docker compose run .*alembic.*/alembic/' | paste -sd '|' -)"
expected="flock -x -n 8 lock=.measurement.lock|flock -w 900 9 lock=.container-deploy.lock|alembic"
if [[ "$order" != "$expected" ]]; then
    echo "FAIL: the migration did not run inside both locks" >&2
    echo "  expected: $expected" >&2
    echo "  got:      $order" >&2
    sed 's/^/  /' "$work/out" >&2
    exit 1
fi

echo "deploy-portal-api locks: OK"
