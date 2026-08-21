#!/bin/sh
# Tests for assert-safe-to-prune.sh. No Docker daemon needed: the guard reads
# container mounts from KLAI_MOUNT_PAIRS_FILE when it is set.
#
# Run: sh deploy/scripts/tests/assert-safe-to-prune.test.sh

set -eu

script_dir=$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)
guard="$script_dir/assert-safe-to-prune.sh"

work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT

failures=0

check() {
    label="$1"
    expected="$2"
    actual="$3"
    if [ "$expected" = "$actual" ]; then
        echo "  ok   $label"
    else
        echo "  FAIL $label (expected exit $expected, got $actual)"
        failures=$((failures + 1))
    fi
}

new_case() {
    case_dir="$work/$1"
    rm -rf "$case_dir"
    mkdir -p "$case_dir/src" "$case_dir/dst"
}

run_guard() {
    set +e
    KLAI_MOUNT_PAIRS_FILE="$case_dir/mounts.tsv" \
        sh "$guard" "$case_dir/src" "$case_dir/dst" >"$case_dir/out" 2>&1
    rc=$?
    set -e
}

echo "assert-safe-to-prune.sh"

# 1. A file that disappears from the repo AND is still mounted: block.
new_case blocked
printf 'keep\n' >"$case_dir/src/keep.cjs"
printf 'keep\n' >"$case_dir/dst/keep.cjs"
printf 'gone\n' >"$case_dir/dst/gone.cjs"
printf '/librechat-acme\t%s\n' "$case_dir/dst/gone.cjs" >"$case_dir/mounts.tsv"
run_guard
check "blocks a still-mounted file that the repo no longer has" 1 "$rc"
grep -q 'librechat-acme' "$case_dir/out" || {
    echo "  FAIL blocked case does not name the container"
    failures=$((failures + 1))
}
grep -q 'gone.cjs' "$case_dir/out" || {
    echo "  FAIL blocked case does not name the file"
    failures=$((failures + 1))
}

# 2. Same deletion, but nothing mounts it any more: allow. This is the
#    supported way to retire a patch -- recreate the containers first.
new_case unmounted
printf 'keep\n' >"$case_dir/src/keep.cjs"
printf 'keep\n' >"$case_dir/dst/keep.cjs"
printf 'gone\n' >"$case_dir/dst/gone.cjs"
: >"$case_dir/mounts.tsv"
run_guard
check "allows deleting a file no container mounts" 0 "$rc"

# 3. A mounted file that the repo still ships: allow. The guard must not fire
#    on every ordinary sync -- that is what makes it survivable.
new_case mounted_but_kept
printf 'keep\n' >"$case_dir/src/keep.cjs"
printf 'keep\n' >"$case_dir/dst/keep.cjs"
printf '/librechat-acme\t%s\n' "$case_dir/dst/keep.cjs" >"$case_dir/mounts.tsv"
run_guard
check "allows a mounted file that survives the sync" 0 "$rc"

# 4. Empty host dir: nothing to prune.
new_case empty_dst
printf 'keep\n' >"$case_dir/src/keep.cjs"
: >"$case_dir/mounts.tsv"
run_guard
check "allows an empty host directory" 0 "$rc"

# 5. Host dir absent entirely (first-ever deploy).
new_case missing_dst
printf 'keep\n' >"$case_dir/src/keep.cjs"
rmdir "$case_dir/dst"
: >"$case_dir/mounts.tsv"
run_guard
check "allows a host directory that does not exist yet" 0 "$rc"

# 6. Several blocked files are reported together, not one per run.
new_case multiple
printf 'a\n' >"$case_dir/dst/a.cjs"
printf 'b\n' >"$case_dir/dst/b.cjs"
{
    printf '/librechat-acme\t%s\n' "$case_dir/dst/a.cjs"
    printf '/librechat-voys\t%s\n' "$case_dir/dst/b.cjs"
} >"$case_dir/mounts.tsv"
run_guard
check "blocks on multiple still-mounted files" 1 "$rc"
grep -q 'a.cjs' "$case_dir/out" && grep -q 'b.cjs' "$case_dir/out" || {
    echo "  FAIL multiple case does not list both files"
    failures=$((failures + 1))
}

# 7. A path prefix must not count as a match (dst/gone.cjs.bak != dst/gone.cjs).
new_case prefix_not_a_match
printf 'gone\n' >"$case_dir/dst/gone.cjs"
printf '/librechat-acme\t%s.bak\n' "$case_dir/dst/gone.cjs" >"$case_dir/mounts.tsv"
run_guard
check "does not treat a longer path as a match" 0 "$rc"

# 8. Docker unreachable: refuse, do not assume nothing is mounted.
#    Without KLAI_MOUNT_PAIRS_FILE the guard asks Docker itself. A daemon that
#    does not answer used to yield an empty mount list, which reads exactly like
#    "nothing is mounted" -- so the guard would wave through the delete it exists
#    to block. These two cases pin the difference between "Docker said zero" and
#    "Docker said nothing".
stub_dir="$work/stub"
mkdir -p "$stub_dir"
printf '#!/bin/sh\necho "Cannot connect to the Docker daemon" >&2\nexit 1\n' >"$stub_dir/docker"
chmod +x "$stub_dir/docker"

new_case docker_down
printf 'gone\n' >"$case_dir/dst/gone.cjs"
set +e
PATH="$stub_dir:$PATH" sh "$guard" "$case_dir/src" "$case_dir/dst" >"$case_dir/out" 2>&1
rc=$?
set -e
check "refuses to prune when Docker cannot be reached" 1 "$rc"
grep -q 'REFUSING TO PRUNE' "$case_dir/out" || {
    echo "  FAIL docker-down case does not say why it refused"
    failures=$((failures + 1))
}

# 9. Docker reachable and genuinely idle: that IS a real zero, so allow.
printf '#!/bin/sh\nexit 0\n' >"$stub_dir/docker"
new_case docker_idle
printf 'gone\n' >"$case_dir/dst/gone.cjs"
set +e
PATH="$stub_dir:$PATH" sh "$guard" "$case_dir/src" "$case_dir/dst" >"$case_dir/out" 2>&1
rc=$?
set -e
check "allows the prune when Docker reports no running containers" 0 "$rc"

if [ "$failures" -ne 0 ]; then
    echo "$failures check(s) failed"
    exit 1
fi
echo "all checks passed"
