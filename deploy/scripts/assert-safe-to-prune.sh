#!/bin/sh
# Refuse to delete a file that a running container still bind-mounts.
#
# Incident 2026-08-14. A config sync ran `rsync --delete` over
# /opt/klai/librechat/patches/ and removed a patch file that 42 tenant
# containers still declared as a mount. Nothing broke at that moment: a
# running container's mounts are already resolved. The damage landed on the
# NEXT start -- Docker re-resolves the bind mount, finds no source, and
# silently creates an empty DIRECTORY there. LibreChat then booted against a
# directory where it expected a file and 36 of 42 tenants exited 127.
#
# A bind mount has two ends and they must be retired in this order:
#   1. remove the mount from the container definitions and recreate them
#   2. verify no running container declares the path any more
#   3. only then delete the host file
#
# This guard enforces step 3 by blocking the delete while step 1 is undone.
# It fires only when a file is BOTH about to be deleted AND still mounted, so
# it has no false positives on an ordinary sync.
#
# usage: assert-safe-to-prune.sh <repo-src-dir> <host-dst-dir>
#
# KLAI_MOUNT_PAIRS_FILE overrides Docker introspection with a file of
# "container-name<TAB>mount-source" lines. Used by the test suite so the guard
# is verifiable without a Docker daemon.

set -eu

if [ $# -ne 2 ]; then
    echo "usage: $0 <repo-src-dir> <host-dst-dir>" >&2
    exit 2
fi

src_dir="$1"
dst_dir="$2"

# Nothing on the host yet means nothing can be pruned.
[ -d "$dst_dir" ] || exit 0

if [ -n "${KLAI_MOUNT_PAIRS_FILE:-}" ]; then
    mount_pairs=$(cat "$KLAI_MOUNT_PAIRS_FILE")
elif running_ids=$(docker ps -q) && [ -n "$running_ids" ]; then
    # .Name carries a leading slash; the awk below strips it.
    mount_pairs=$(
        echo "$running_ids" | xargs docker inspect \
            --format '{{$name := .Name}}{{range .Mounts}}{{$name}}{{"\t"}}{{.Source}}{{"\n"}}{{end}}'
    )
else
    mount_pairs=""
fi

blocked=""
for host_path in "$dst_dir"/*; do
    # Literal glob when the directory is empty.
    [ -e "$host_path" ] || continue

    base_name=$(basename "$host_path")
    # Present in the incoming tree: survives the sync, not our problem.
    [ -e "$src_dir/$base_name" ] && continue

    users=$(
        printf '%s\n' "$mount_pairs" \
            | awk -F'\t' -v target="$host_path" '$2 == target { print substr($1, 2) }' \
            | sort -u \
            | tr '\n' ' '
    )
    [ -n "$users" ] || continue

    blocked="${blocked}  ${host_path}
      still mounted by: ${users}
"
done

if [ -n "$blocked" ]; then
    cat >&2 <<EOF
REFUSING TO PRUNE.

These files are about to be deleted from
  ${dst_dir}
while running containers still bind-mount them:

${blocked}
Deleting them now breaks nothing today. It arms an outage for the next
container start: Docker replaces the missing source with an empty directory
and the container dies against it (2026-08-14: 36 of 42 tenants, exit 127).

Fix the order instead:
  1. remove the mount from the container definitions, then recreate them
  2. confirm nothing declares the path any more:
       for n in \$(docker ps --format '{{.Names}}'); do
         docker inspect "\$n" --format '{{range .Mounts}}{{.Source}} {{end}}' \\
           | grep -q '<file>' && echo "\$n"
       done
  3. re-run this deploy

If the two steps cannot land together, keep a placeholder file at the path
until every container has been recreated. A placeholder is cheap; an
auto-created empty directory is an outage.
EOF
    exit 1
fi
